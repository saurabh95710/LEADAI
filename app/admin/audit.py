"""Audit logging — one tamper-resistant trail for every important action.

Every security-relevant, lifecycle or destructive action is written to the
``audit_logs`` collection with a unified schema:

    actor_user_id, actor_email, actor_role   who
    organization_id                          tenant the action belongs to
    action, category                         what (e.g. "search.started")
    resource_type, resource_id               on which resource
    status ("success" | "failure")           result
    ip, session_id, user_agent               from where
    details                                  metadata (secrets redacted)
    at                                       when

Audit logging can NOT be disabled by any role: the legacy
``security.audit_logging`` toggle is ignored. No API deletes or edits audit
rows. Secrets are redacted recursively (dict keys and nested lists) before
anything reaches the log.

``user`` may be a dict (session claims / admin record / TenantContext dump)
or a plain email string — both are accepted, so a caller can never silently
drop a write by passing the "wrong" shape.
"""
import logging
from contextvars import ContextVar
from typing import Any, Dict, Optional, Union

from app.db.mongo import get_async_db, get_sync_db
from app.db.models import utcnow

logger = logging.getLogger(__name__)

COLLECTION = "audit_logs"

_REDACT_EXACT = {"token", "password", "password_hash", "apify.token", "session_secret",
                 "admin_password_hash", "new_password", "old_password", "secret",
                 "authorization", "cookie", "set-cookie", "api_key", "apikey",
                 "access_token", "refresh_token", "client_secret", "webhook_secret",
                 "card_number", "cvv", "cvc", "token_hash", "raw_token"}
_REDACT_FRAGMENTS = ("password", "secret", "api_key", "apikey", "_token",
                     "token_", "private_key", "credential")
_MASK = "••••"


def _is_sensitive_key(key: str) -> bool:
    low = str(key).lower()
    if low in _REDACT_EXACT:
        return True
    return any(frag in low for frag in _REDACT_FRAGMENTS)


def _redact(details: Any, _top: bool = True) -> Any:
    """Recursively mask secret fields (dict keys, nested dicts and lists).

    A bare string passed as the whole ``details`` payload has no key to judge
    it by, so it is masked entirely."""
    if _top and isinstance(details, str):
        return {"value": "<redacted>"}
    if isinstance(details, dict):
        clean: Dict[str, Any] = {}
        for key, value in details.items():
            if _is_sensitive_key(key):
                clean[key] = _MASK
            else:
                clean[key] = _redact(value, _top=False)
        return clean
    if isinstance(details, list):
        return [_redact(v, _top=False) for v in details]
    return details


def redact(details: Any) -> Any:
    """Public redaction helper (exports, security events, notifications)."""
    return _redact(details)


# The signed-in user of the current HTTP request (set by main.auth_gate).
# build_record falls back to it when a caller passes no ``user``, so no
# audit entry written while serving a request is ever actor-less.
_request_actor: ContextVar = ContextVar("audit_request_actor", default=None)


def set_request_actor(user: Optional[Dict[str, Any]], ip: Optional[str] = None,
                      user_agent: Optional[str] = None):
    """Bind the request's actor; returns a token for reset_request_actor."""
    return _request_actor.set({"user": user, "ip": ip, "user_agent": user_agent})


def reset_request_actor(token) -> None:
    try:
        _request_actor.reset(token)
    except Exception:
        pass


def _actor(user: Union[Dict[str, Any], str, None]) -> Dict[str, Any]:
    if user is None:
        return {"actor_user_id": None, "actor_email": "", "actor_role": ""}
    if isinstance(user, str):
        return {"actor_user_id": None, "actor_email": user, "actor_role": ""}
    if hasattr(user, "model_dump"):
        user = user.model_dump()
    if not isinstance(user, dict):
        return {"actor_user_id": None, "actor_email": str(user), "actor_role": ""}
    role = (user.get("platform_role") if user.get("is_super_admin") else None) \
        or user.get("role") or user.get("org_role") or ""
    return {
        "actor_user_id": str(user.get("user_id") or "") or None,
        "actor_email": user.get("email", "") or "",
        "actor_role": role,
    }


def build_record(action: str, category: str, *, user=None, ip: Optional[str] = None,
                 success: bool = True, details: Optional[Dict[str, Any]] = None,
                 organization_id: Optional[str] = None,
                 resource_type: Optional[str] = None,
                 resource_id: Optional[str] = None,
                 session_id: Optional[str] = None,
                 user_agent: Optional[str] = None) -> Dict[str, Any]:
    bound = _request_actor.get()
    if bound:
        if user is None or user == "system":
            user = bound.get("user") or user
        ip = ip or bound.get("ip")
        user_agent = user_agent or bound.get("user_agent")
    actor = _actor(user)
    if organization_id is None and isinstance(user, dict):
        organization_id = user.get("organization_id") or None
    if session_id is None and isinstance(user, dict):
        session_id = user.get("session_id")
    return {
        "action": action,
        "category": category,
        **actor,
        # legacy fields kept so existing admin views keep rendering
        "user": actor["actor_email"],
        "role": actor["actor_role"],
        "organization_id": str(organization_id) if organization_id else None,
        "resource_type": resource_type,
        "resource_id": str(resource_id) if resource_id is not None else None,
        "status": "success" if success else "failure",
        "success": success,
        "ip": ip or "",
        "session_id": (session_id[:12] + "…") if session_id else None,
        "user_agent": (user_agent or "")[:200] or None,
        "details": _redact(details or {}),
        # actions taken while a Super Admin impersonates a customer stay attributable
        "impersonated_by": (user.get("impersonated_by") if isinstance(user, dict) else None) or None,
        "at": utcnow(),
    }


def audit(action: str, category: str, *, user=None,
          ip: Optional[str] = None, success: bool = True,
          details: Optional[Dict[str, Any]] = None, **extra) -> None:
    """Sync audit write (background threads and sync contexts)."""
    try:
        db = get_sync_db()
        if db is None:
            return
        db[COLLECTION].insert_one(build_record(
            action, category, user=user, ip=ip, success=success,
            details=details, **extra))
    except Exception as e:
        logger.warning(f"Audit write failed: {e}")


async def aaudit(action: str, category: str, *, user=None,
                 ip: Optional[str] = None, success: bool = True,
                 details: Optional[Dict[str, Any]] = None, **extra) -> None:
    """Async audit write (FastAPI handlers)."""
    try:
        db = get_async_db()
        if db is None:
            return
        await db[COLLECTION].insert_one(build_record(
            action, category, user=user, ip=ip, success=success,
            details=details, **extra))
    except Exception as e:
        logger.warning(f"Audit write failed: {e}")


def request_meta(request) -> Dict[str, Optional[str]]:
    """ip + user_agent of a request, for audit/security records."""
    if request is None:
        return {"ip": None, "user_agent": None}
    try:
        from app.auth.service import _get_client_ip
        ip = _get_client_ip(request)
    except Exception:
        ip = request.client.host if getattr(request, "client", None) else None
    return {"ip": ip, "user_agent": request.headers.get("user-agent")}
