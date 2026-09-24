"""Admin sign-in + signed session cookie helpers.

Flow: the login page posts email/password to ``POST /api/auth/login``; the
backend verifies the password using bcrypt (with automatic migration from
legacy SHA-256 hashes) and sets an httpOnly session cookie. Failed attempts
are throttled per IP to slow brute-force attacks.
"""
import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Request, Response

from app.admin.envvars import get_envvar_bool, get_envvar_int, get_envvar_str
from app.config import get_settings
from app.db.mongo import get_sync_db
from app.db.models import utcnow

logger = logging.getLogger(__name__)
settings = get_settings()

COOKIE_NAME = "leadai_session"
_HEADER = {"v": 1}
_FALLBACK_SECRET: Optional[str] = None

# ── Brute-force throttling (per IP, MongoDB backed — app/auth/rate_limit.py) ──
from app.auth.rate_limit import RateLimiter  # noqa: E402

_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_SEC = 300  # 5 minutes (was 60s — increased for better protection)
# Durable + shared across processes; ``_login_attempts.clear()`` is the
# test reset hook (clears the in-memory fallback).
_login_attempts = RateLimiter("login_ip", _LOGIN_MAX_ATTEMPTS, _LOGIN_WINDOW_SEC)


def _get_client_ip(request: Request) -> str:
    """Client IP. X-Forwarded-For / X-Real-IP are honoured only when
    TRUST_PROXY_HEADERS=true (the app sits behind a reverse proxy);
    otherwise a client could spoof them to dodge per-IP throttling."""
    if not get_envvar_bool("TRUST_PROXY_HEADERS", False):
        return request.client.host if request.client else "unknown"
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        # "client-supplied…, client, proxy-appended": the leftmost entries are
        # whatever the caller sent (spoofable); the rightmost one is the
        # address our proxy (e.g. Render's load balancer) actually saw.
        ips = [ip.strip() for ip in forwarded_for.split(",") if ip.strip()]
        if ips:
            return ips[-1]
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


def _login_protection_enabled() -> bool:
    try:
        from app.admin.settings import get_setting
        return bool(get_setting("security.login_protection"))
    except Exception:
        return True


def login_allowed(ip: str) -> bool:
    """True when this IP may still try to log in within the rate window.

    The Security page can disable brute-force protection entirely
    (``security.login_protection``); when disabled every attempt passes."""
    if not _login_protection_enabled():
        return True
    return _login_attempts.allowed(ip)


def login_denied_seconds(ip: str) -> int:
    """Seconds until the IP may try again (0 = allowed)."""
    return _login_attempts.retry_after(ip)


def record_login_failure(ip: str) -> None:
    if not _login_protection_enabled():
        return
    _login_attempts.hit(ip)


def reset_login_attempts(ip: str) -> None:
    _login_attempts.reset(ip)


# ── Per-account lockout (DB backed) ───────────────────────────────────────
LOCKOUT_COLLECTION = "login_lockouts"


def _lockout_policy() -> tuple[int, int]:
    """(failed attempts before lock, lock minutes) from Security settings."""
    threshold, minutes = 5, 15
    try:
        from app.admin.settings import get_setting
        threshold = int(get_setting("security.lockout_threshold") or threshold)
        minutes = int(get_setting("security.lockout_minutes") or minutes)
    except Exception:
        pass
    return max(1, threshold), max(1, minutes)


def account_locked_seconds(email: str) -> int:
    """Seconds the account stays locked (0 = not locked)."""
    try:
        db = get_sync_db()
        if db is None:
            return 0
        doc = db[LOCKOUT_COLLECTION].find_one({"email": email.strip().lower()})
        until = (doc or {}).get("locked_until")
        if not until:
            return 0
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        remaining = (until - datetime.now(timezone.utc)).total_seconds()
        return int(remaining) + 1 if remaining > 0 else 0
    except Exception:
        return 0


def record_account_failure(email: str) -> bool:
    """Count a failed login for an account; True when this locked it."""
    email = (email or "").strip().lower()
    if not email:
        return False
    try:
        db = get_sync_db()
        if db is None:
            return False
        threshold, minutes = _lockout_policy()
        doc = db[LOCKOUT_COLLECTION].find_one_and_update(
            {"email": email},
            {"$inc": {"failures": 1}, "$set": {"updated_at": utcnow()}},
            upsert=True, return_document=True)
        if doc and doc.get("failures", 0) >= threshold:
            db[LOCKOUT_COLLECTION].update_one({"email": email}, {"$set": {
                "locked_until": datetime.fromtimestamp(time.time() + minutes * 60,
                                                       tz=timezone.utc),
                "failures": 0}})
            return True
    except Exception as e:
        logger.warning("lockout bookkeeping failed for %s: %s", email, e)
    return False


def clear_account_failures(email: str) -> None:
    try:
        db = get_sync_db()
        if db is not None:
            db[LOCKOUT_COLLECTION].delete_one({"email": (email or "").strip().lower()})
    except Exception:
        pass


# ── Password verification with bcrypt + legacy migration ─────────────────

def _verify_and_migrate_password(plain: str, stored_hash: str) -> tuple[bool, Optional[str]]:
    """Verify password and return (valid, new_bcrypt_hash_if_migrated).

    If the stored hash is a legacy SHA-256 and the password matches,
    returns the new bcrypt hash so the caller can persist the upgrade.
    """
    from app.auth.crypto import verify_password
    res = verify_password(plain, stored_hash)
    if isinstance(res, tuple):
        return res
    return (bool(res), None)


def _migrate_password_hash(email: str, new_hash: str, scope: str) -> None:
    """Persist a migrated bcrypt hash. Best-effort; failures are logged."""
    try:
        db = get_sync_db()
        if db is None:
            return
        if scope == "admin":
            record = db["admin_users"].find_one({"email": email})
            if record:
                db["admin_users"].update_one(
                    {"_id": record["_id"]},
                    {"$set": {"password_hash": new_hash, "updated_at": utcnow()}})
                logger.info("Migrated admin password hash to bcrypt for %s", email)
        elif scope == "panel":
            from app.admin.envvars import set_envvar_override
            set_envvar_override("PANEL_ADMIN_PASSWORD_HASH", new_hash, by="system")
            logger.info("Migrated panel admin password hash to bcrypt")
    except Exception as e:
        logger.warning("Failed to migrate password hash for %s: %s", email, e)


# ── Credential verification (two scopes) ─────────────────────────────────

def _panel_scope_user(email: str, name: str, role: str) -> Dict[str, Any]:
    is_super = role == "super_admin"
    return {
        "email": email,
        "name": name,
        "role": role,
        "scope": "admin",
        "is_platform_admin": is_super,
        "platform_role": role if is_super else None,
    }


def verify_admin_login(email: str, password: str) -> Optional[Dict[str, Any]]:
    """Constant-time check for the ADMIN PORTAL login (scope "admin").

    Accounts: the .env ``panel_admin_email`` is always a recovery
    super-admin; additional managers/viewers live in the ``admin_users``
    collection (managed from the admin Security page). A DB record with the
    same email as the env admin takes precedence so a password set in the
    panel works while the env password stays valid as a fallback.

    Returns the user dict on success, None otherwise. All comparisons use
    secure verification. Supports automatic migration from legacy SHA-256.
    """
    email_clean = email.strip().lower()
    now = time.time()

    # 0) The permanent Super Admin (SUPERADMIN_EMAIL / SUPERADMIN_PASSWORD):
    #    for that email the environment is the only source of truth.
    from app.auth.superadmin import managed_by_env, verify_env_superadmin
    env_result = verify_env_superadmin(email_clean, password)
    if env_result is not None:
        if not env_result:
            return None
        _touch_last_login(email_clean, now)
        return _panel_scope_user(email_clean, "Super Admin", "super_admin")

    # 1) Managed account (admin_users) — takes precedence for the env email
    record = _admin_user_record(email_clean)
    if record is not None:
        expected_hash = (record.get("password_hash") or "").strip()
        if not record.get("enabled", True) or not expected_hash or not password:
            return None
        valid, new_hash = _verify_and_migrate_password(password, expected_hash)
        if not valid:
            return None
        # Migrate legacy hash in DB
        if new_hash:
            try:
                db = get_sync_db()
                if db is not None:
                    db["admin_users"].update_one(
                        {"_id": record["_id"]},
                        {"$set": {"password_hash": new_hash, "updated_at": now}})
                    logger.info("Migrated admin password hash to bcrypt for %s", email_clean)
            except Exception as e:
                logger.warning("Failed to migrate admin hash: %s", e)
        _touch_last_login(email_clean, now)
        return _panel_scope_user(
            email_clean,
            record.get("name") or email_clean.split("@")[0],
            record.get("role") or "viewer",
        )

    # 2) Legacy env super admin (PANEL_ADMIN_*), only without SUPERADMIN_*
    expected_email = "" if managed_by_env() else get_envvar_str(
        "PANEL_ADMIN_EMAIL", settings.panel_admin_email).strip().lower()
    if expected_email and email_clean == expected_email:
        expected_hash = (get_envvar_str("PANEL_ADMIN_PASSWORD_HASH",
                                        settings.panel_admin_password_hash)
                         or "").strip()
        if expected_hash and password:
            valid, new_hash = _verify_and_migrate_password(password, expected_hash)
            if valid:
                if new_hash:
                    _migrate_password_hash(email_clean, new_hash, "panel")
                return _panel_scope_user(expected_email, "Super Admin", "super_admin")

    return None


def verify_saas_user_login(email: str, password: str, scope: str = "site") -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Authenticate a user against the unified SaaS `users` collection.

    Returns (user_dict, error_message). On success, error_message is None.
    On authentication failure, returns (None, reason).
    """
    email_clean = email.strip().lower()
    now = time.time()
    db = get_sync_db()
    if db is None:
        return None, "Database unavailable"

    user_record = db["users"].find_one({"email": email_clean})
    if not user_record:
        return None, "Invalid email or password"

    if user_record.get("status") == "pending_approval":
        # still verify the password so the pending state is not an oracle
        ok, _ = _verify_and_migrate_password(password or "", (user_record.get("password_hash") or "").strip())
        return None, ("Demo pending approval" if ok else "Invalid email or password")
    if user_record.get("status") == "rejected":
        return None, "Invalid email or password"
    if user_record.get("status") in ("suspended", "disabled", "deactivated"):
        return None, "Account is suspended"

    expected_hash = (user_record.get("password_hash") or "").strip()
    if not expected_hash or not password:
        return None, "Invalid email or password"

    valid, new_hash = _verify_and_migrate_password(password, expected_hash)
    if not valid:
        return None, "Invalid email or password"

    user_id = str(user_record["_id"])

    # Update password hash if migrated
    if new_hash:
        try:
            db["users"].update_one(
                {"_id": user_record["_id"]},
                {"$set": {"password_hash": new_hash, "updated_at": utcnow()}},
            )
        except Exception as e:
            logger.warning("Failed to update migrated password hash for %s: %s", email_clean, e)

    # Touch last login
    try:
        db["users"].update_one(
            {"_id": user_record["_id"]},
            {"$set": {"last_login": utcnow()}},
        )
    except Exception:
        pass

    return build_user_claims(db, user_record, scope=scope)


def build_user_claims(db, user_record: Dict[str, Any], scope: str = "site") -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Session claims for a ``users`` record, resolved from the database.

    Platform admins get an admin-scope session. Everyone else needs an
    ACTIVE membership in a usable organization — there is no fallback
    organization and no default "owner" role.
    """
    email_clean = (user_record.get("email") or "").strip().lower()
    user_id = str(user_record["_id"])
    name = user_record.get("name") or email_clean.split("@")[0]
    if scope == "admin":
        if not user_record.get("is_platform_admin"):
            return None, "Not a platform account"
        return {
            "user_id": user_id, "email": email_clean, "name": name,
            "role": user_record.get("platform_role"), "scope": "admin",
            "organization_id": "", "organization_name": "", "organization_slug": "",
            "org_role": "", "is_platform_admin": True,
            "platform_role": user_record.get("platform_role"),
        }, None

    default_org_id = user_record.get("default_organization_id")
    membership = None
    if default_org_id:
        membership = db["organization_members"].find_one({
            "user_id": user_id, "organization_id": str(default_org_id), "status": "active"})
    if not membership:
        membership = db["organization_members"].find_one({"user_id": user_id, "status": "active"})
    if not membership:
        return None, "No active organization membership"

    org_id = str(membership.get("organization_id", ""))
    org_role = membership.get("role", "member")
    try:
        from bson import ObjectId
        org_doc = db["organizations"].find_one({"_id": ObjectId(org_id)})
    except Exception:
        org_doc = None
    if not org_doc:
        return None, "No active organization membership"
    status = org_doc.get("status", "active")
    if status == "pending":
        return None, "Demo pending approval"
    if status == "rejected":
        return None, "Demo request was not approved"
    if status in ("suspended", "disabled", "archived", "cancelled"):
        return None, "Organization is suspended"

    return {
        "user_id": user_id,
        "email": email_clean,
        "name": name,
        "role": org_role,
        "scope": "site",
        "organization_id": org_id,
        "organization_name": org_doc.get("name", ""),
        "organization_slug": org_doc.get("slug", ""),
        "org_role": org_role,
        "is_platform_admin": False,
        "platform_role": None,
    }, None


def create_tracked_session(user: Dict[str, Any], ip: str = "unknown",
                           user_agent: str = "unknown",
                           impersonated_by: Optional[str] = None,
                           impersonation_reason: Optional[str] = None) -> Dict[str, Any]:
    """Persist a session in MongoDB `user_sessions` and attach session_id to user claims."""
    session_id = secrets.token_hex(24)
    ttl_days = get_envvar_int("SESSION_TTL_DAYS", settings.session_ttl_days)
    expires_at = datetime.fromtimestamp(time.time() + ttl_days * 86400, tz=timezone.utc)

    db = get_sync_db()
    if db is not None:
        try:
            ip_hash = hashlib.sha256(ip.encode("utf-8")).hexdigest()[:16]
            session_doc = {
                "session_id": session_id,
                "user_id": str(user.get("user_id") or f"env:{user.get('email', '')}"),
                "email": user.get("email"),
                "scope": user.get("scope"),
                "organization_id": user.get("organization_id"),
                "role": user.get("role"),
                "ip_hash": ip_hash,
                "user_agent": user_agent[:200] if user_agent else "unknown",
                "impersonated_by": impersonated_by,
                "impersonation_reason": impersonation_reason,
                "created_at": utcnow(),
                "expires_at": expires_at,
                "last_activity_at": utcnow(),
                "revoked_at": None,
                "revoked_by": None,
            }
            db["user_sessions"].insert_one(session_doc)
        except Exception as e:
            logger.warning("Could not persist session in user_sessions: %s", e)

    enriched_user = dict(user)
    enriched_user["session_id"] = session_id
    if impersonated_by:
        enriched_user["impersonated_by"] = impersonated_by
        enriched_user["impersonation_reason"] = impersonation_reason
    return enriched_user


def revoke_user_sessions(user_id: str, revoked_by: str = "system") -> int:
    """Revoke all active sessions for a user."""
    db = get_sync_db()
    if db is None:
        return 0
    try:
        res = db["user_sessions"].update_many(
            {"user_id": str(user_id), "revoked_at": None},
            {"$set": {"revoked_at": utcnow(), "revoked_by": revoked_by}},
        )
        return res.modified_count
    except Exception as e:
        logger.warning("Error revoking sessions for user %s: %s", user_id, e)
        return 0


def revoke_session(session_id: str, revoked_by: str = "system") -> bool:
    """Revoke a single session by session_id."""
    db = get_sync_db()
    if db is None:
        return False
    try:
        res = db["user_sessions"].update_one(
            {"session_id": session_id, "revoked_at": None},
            {"$set": {"revoked_at": utcnow(), "revoked_by": revoked_by}},
        )
        return res.modified_count > 0
    except Exception as e:
        logger.warning("Error revoking session %s: %s", session_id, e)
        return False


def _admin_user_record(email: str) -> Optional[Dict[str, Any]]:
    try:
        db = get_sync_db()
        if db is None:
            return None
        return db["admin_users"].find_one({"email": email})
    except Exception:
        return None


def _touch_last_login(email: str, when: float) -> None:
    try:
        db = get_sync_db()
        if db is None:
            return
        db["admin_users"].update_one(
            {"email": email}, {"$set": {"last_login": when}})
    except Exception:
        pass


# ── Signed session cookie ─────────────────────────────────────────────────

def _secret() -> str:
    global _FALLBACK_SECRET
    override = get_envvar_str("SESSION_SECRET", "")
    if override:
        return override
    if settings.session_secret:
        return settings.session_secret
    if _FALLBACK_SECRET is None:
        _FALLBACK_SECRET = secrets.token_urlsafe(48)
        logger.critical(
            "SESSION_SECRET is not set — using a per-process random secret. "
            "Sessions will be invalidated on every restart. "
            "Set SESSION_SECRET in .env for production deployments."
        )
    return _FALLBACK_SECRET


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _sign(payload_b64: str) -> str:
    return hmac.new(_secret().encode(), payload_b64.encode(), hashlib.sha256).hexdigest()


def build_session_value(user: Dict[str, Any]) -> str:
    """Signed, timestamped cookie value carrying the logged-in user."""
    payload = {
        "user": user,
        "iat": int(time.time()),
        "exp": int(time.time()) + get_envvar_int("SESSION_TTL_DAYS", settings.session_ttl_days) * 86400,
    }
    payload_b64 = _b64e(
        json.dumps({**_HEADER, **payload}, separators=(",", ":")).encode())
    return f"{payload_b64}.{_sign(payload_b64)}"


def parse_session_value(value: Optional[str]) -> Optional[Dict[str, Any]]:
    """Validate signature + expiry and return the user claims (or None)."""
    if not value or "." not in value:
        return None
    payload_b64, sig = value.rsplit(".", 1)
    if not hmac.compare_digest(_sign(payload_b64), sig):
        logger.warning("Session cookie signature mismatch")
        return None
    try:
        data = json.loads(_b64d(payload_b64))
    except Exception:
        logger.warning("Session cookie payload is not valid JSON")
        return None
    if data.get("v") != _HEADER["v"] or data.get("exp", 0) < time.time():
        return None
    user = data.get("user")
    if not isinstance(user, dict) or not user.get("email"):
        return None

    # Server-side revocation check for tracked sessions
    session_id = user.get("session_id")
    if session_id:
        try:
            db = get_sync_db()
            if db is not None:
                record = db["user_sessions"].find_one({"session_id": session_id})
                if record is None or record.get("revoked_at") is not None:
                    return None
                exp = record.get("expires_at")
                if exp is not None:
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=timezone.utc)
                    if exp < datetime.now(timezone.utc):
                        return None
        except Exception:
            pass  # DB outage: signature + exp above still apply

    return user


def set_session_cookie(response: Response, user: Dict[str, Any]) -> None:
    response.set_cookie(
        COOKIE_NAME,
        build_session_value(user),
        max_age=get_envvar_int("SESSION_TTL_DAYS", settings.session_ttl_days) * 86400,
        httponly=True,
        samesite="lax",
        secure=get_envvar_bool("SESSION_COOKIE_SECURE", settings.session_cookie_secure),
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def session_user(request: Request) -> Optional[Dict[str, Any]]:
    """Current user from the session cookie, or None when signed out."""
    return parse_session_value(request.cookies.get(COOKIE_NAME))


def session_issued_at(value: Optional[str]) -> Optional[int]:
    """Issue timestamp (iat) of a session cookie, or None when unavailable
    (legacy cookie without the claim). Used for revocation + idle timeout."""
    if not value or "." not in value:
        return None
    payload_b64 = value.rsplit(".", 1)[0]
    try:
        data = json.loads(_b64d(payload_b64))
    except Exception:
        return None
    iat = data.get("iat")
    if isinstance(iat, (int, float)):
        return int(iat)
    return None


def public_user(user: Dict[str, Any]) -> Dict[str, Any]:
    """Shape of the user object exposed to the frontend."""
    return {
        "user_id": str(user.get("user_id", "")),
        "email": user.get("email", ""),
        "name": user.get("name", ""),
        "role": user.get("role", ""),
        "organization_id": user.get("organization_id", ""),
        "organization_name": user.get("organization_name", "Default Organization"),
        "organization_slug": user.get("organization_slug", "default-org"),
        "org_role": user.get("org_role", "owner"),
        "is_platform_admin": bool(user.get("is_platform_admin")),
        "platform_role": user.get("platform_role"),
        "impersonated_by": user.get("impersonated_by"),
        "impersonation_reason": user.get("impersonation_reason"),
    }


# ── Environment panel unlock cookie ─────────────────────────────────────────
# A short-lived signed cookie that gates every /api/admin/env* endpoint on
# top of the normal admin session. Set by POST /api/admin/env/unlock after
# the guard password verifies; path-scoped so it only travels with env API
# calls.

ENV_UNLOCK_COOKIE = "leadai_env_unlock"
ENV_UNLOCK_TTL_SEC = 15 * 60
_ENV_UNLOCK_HEADER = {"v": 1}


def build_env_unlock_value(ttl_sec: int = ENV_UNLOCK_TTL_SEC) -> str:
    payload = {
        **_ENV_UNLOCK_HEADER,
        "iat": int(time.time()),
        "exp": int(time.time()) + ttl_sec,
    }
    payload_b64 = _b64e(
        json.dumps(payload, separators=(",", ":")).encode())
    return f"{payload_b64}.{_sign(payload_b64)}"


def parse_env_unlock_value(value: Optional[str]) -> bool:
    if not value or "." not in value:
        return False
    payload_b64, sig = value.rsplit(".", 1)
    if not hmac.compare_digest(_sign(payload_b64), sig):
        return False
    try:
        data = json.loads(_b64d(payload_b64))
    except Exception:
        return False
    return (data.get("v") == _ENV_UNLOCK_HEADER["v"]
            and data.get("exp", 0) > time.time())


def set_env_unlock_cookie(response: Response) -> None:
    response.set_cookie(
        ENV_UNLOCK_COOKIE,
        build_env_unlock_value(),
        max_age=ENV_UNLOCK_TTL_SEC,
        httponly=True,
        samesite="lax",
        secure=get_envvar_bool("SESSION_COOKIE_SECURE", settings.session_cookie_secure),
        path="/api/admin/env",
    )


def clear_env_unlock_cookie(response: Response) -> None:
    response.delete_cookie(ENV_UNLOCK_COOKIE, path="/api/admin/env")
