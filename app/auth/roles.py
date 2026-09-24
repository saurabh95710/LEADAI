"""Role-based authorization for the admin panel.

Three roles, enforced server-side on every /api/admin endpoint:

  * ``viewer``      — read-only access to the admin panel
  * ``manager``     — viewer + can change settings, run tests, retry jobs,
                      export data
  * ``super_admin`` — manager + user management, security, destructive ops

Accounts live in the ``admin_users`` collection (managed from the Security
page). The environment's SUPERADMIN_EMAIL account always exists as the
permanent super-admin, so the panel can never be locked out entirely.

The session cookie is signed, so a forged role in the cookie is impossible;
role changes are re-read from the DB on every request.
"""
import logging
import time
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request

from app.auth.service import COOKIE_NAME, session_user, session_issued_at
from app.config import get_settings
from app.db.mongo import get_sync_db
from app.admin import settings as s

logger = logging.getLogger(__name__)
settings = get_settings()

ADMIN_USERS_COLLECTION = "admin_users"

ROLE_RANK = {"viewer": 1, "manager": 2, "super_admin": 3}
ROLE_LABELS = {
    "viewer": "Viewer",
    "manager": "Manager",
    "super_admin": "Super Admin",
}


def role_rank(role: str) -> int:
    return ROLE_RANK.get(role, 0)


def is_env_admin_email(email: str) -> bool:
    """The environment-defined Super Admin (SUPERADMIN_EMAIL, or the legacy
    PANEL_ADMIN_EMAIL when SUPERADMIN_EMAIL is not set)."""
    from app.auth.superadmin import is_superadmin_email
    return is_superadmin_email(email)


def get_admin_record(email: str) -> Optional[Dict[str, Any]]:
    """User record from admin_users (or None). Sync DB — safe in threads."""
    try:
        db = get_sync_db()
        if db is None:
            return None
        doc = db[ADMIN_USERS_COLLECTION].find_one(
            {"email": email.strip().lower()}, {"password_hash": 0})
        if doc:
            doc["_id"] = str(doc["_id"])
        return doc
    except Exception:
        return None


def effective_role(email: str) -> str:
    """The platform role that governs an account, or "" when the account is
    not platform staff.

    super_admin for the env recovery account, otherwise an enabled
    ``admin_users`` record's role, otherwise ``users.platform_role`` when the
    user is flagged ``is_platform_admin``. There is deliberately NO fallback:
    an admin-scope session for an unknown account gets no access at all.
    """
    if not email:
        return ""
    if is_env_admin_email(email):
        return "super_admin"
    record = get_admin_record(email)
    if record and record.get("enabled", True) and record.get("role") in ROLE_RANK:
        return record["role"]
    try:
        db = get_sync_db()
        if db is not None:
            user_doc = db["users"].find_one({"email": email.strip().lower()})
            if (user_doc and user_doc.get("is_platform_admin")
                    and user_doc.get("status", "active") == "active"
                    and user_doc.get("platform_role")):
                role = user_doc["platform_role"]
                if role in ROLE_RANK:
                    return role
                # SaaS platform roles map onto the admin panel's rank scale
                from app.auth.permissions import PLATFORM_ROLE_RANK
                if role in PLATFORM_ROLE_RANK:
                    return PLATFORM_TO_PANEL_ROLE.get(role, "viewer")
    except Exception:
        pass
    return ""


# SaaS platform role -> legacy admin panel role (ROLE_RANK scale)
PLATFORM_TO_PANEL_ROLE = {
    "super_admin": "super_admin",
    "operations_admin": "manager",
    "technical_admin": "manager",
    "billing_admin": "viewer",
    "support_admin": "viewer",
    "viewer": "viewer",
}
# legacy admin panel role -> SaaS platform role (TenantContext)
PANEL_TO_PLATFORM_ROLE = {
    "super_admin": "super_admin",
    "manager": "operations_admin",
    "viewer": "viewer",
}


def current_admin(request: Request) -> Optional[Dict[str, Any]]:
    """The signed-in admin with up-to-date role, or None.

    Only ``scope="admin"`` sessions count (site sessions from the main
    website login cannot reach the panel). Beyond cookie validity
    (signature + expiry), the admin session is additionally governed by
    two Security-page settings:
      * ``security.session_timeout_hours`` — hard idle timeout on top of the
        cookie lifetime (env default equals the cookie lifetime, so nothing
        changes out of the box);
      * ``security.session_epoch`` — a bumped epoch revokes every session
        issued before it ("revoke all sessions").
    """
    user = session_user(request)
    if user is None or user.get("scope") != "admin":
        return None
    issued_at = session_issued_at(request.cookies.get(COOKIE_NAME))
    try:
        timeout_hours = int(s.get_setting("security.session_timeout_hours")
                            or 0)
    except (TypeError, ValueError):
        timeout_hours = 0
    if timeout_hours > 0 and issued_at is not None:
        if time.time() - issued_at > timeout_hours * 3600:
            return None
    epoch = s.sessions_epoch()
    if epoch > 0 and (issued_at is None or issued_at < epoch):
        return None
    role = effective_role(user.get("email", ""))
    if not role:
        return None
    return {
        "email": user.get("email", ""),
        "name": user.get("name", "Admin"),
        "role": role,
    }


def require_admin(min_role: str = "viewer"):
    """FastAPI dependency: signed-in admin with at least `min_role`.
    Returns a plain callable so it can be used both in `dependencies=[...]`
    and as `Depends(require_admin("manager"))`."""
    def _dep(request: Request):
        admin = current_admin(request)
        if admin is None:
            raise HTTPException(status_code=401, detail="Sign in required")
        if role_rank(admin["role"]) < role_rank(min_role):
            raise HTTPException(
                status_code=403,
                detail=f"Requires role '{min_role}' or higher")
        return admin
    return _dep


# Convenience dependencies (plain callables — wrap with Depends() at use sites)
require_viewer = require_admin("viewer")
require_manager = require_admin("manager")
require_super = require_admin("super_admin")


def require_env_unlocked(request: Request):
    """Additional gate for the /api/admin/env* endpoints: the caller needs a
    valid short-lived unlock cookie on top of the normal admin session."""
    from app.auth.service import ENV_UNLOCK_COOKIE, parse_env_unlock_value
    if not parse_env_unlock_value(request.cookies.get(ENV_UNLOCK_COOKIE)):
        raise HTTPException(
            status_code=403,
            detail="Environment panel is locked — unlock it first")
