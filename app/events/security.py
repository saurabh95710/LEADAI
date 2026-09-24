"""Security events — a dedicated, append-only store for suspicious activity.

Types (``type`` field):
  login_failed, account_locked, login_rate_limited, cross_tenant_access,
  permission_denied, escalation_attempt, csrf_blocked,
  invalid_webhook_signature, password_reset_requested,
  password_reset_completed

High / critical events also raise a Super Admin notification.
"""
import logging
from typing import Any, Dict, Optional

from app.admin.audit import redact, request_meta
from app.db.models import utcnow
from app.db.mongo import get_async_db, get_sync_db

logger = logging.getLogger(__name__)

COLLECTION = "security_events"
_NOTIFY_SEVERITIES = {"high", "critical"}


def _record(event_type: str, severity: str, *, actor_email: Optional[str] = None,
            actor_user_id: Optional[str] = None, organization_id: Optional[str] = None,
            target_organization_id: Optional[str] = None, ip: Optional[str] = None,
            path: Optional[str] = None, method: Optional[str] = None,
            details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "type": event_type,
        "severity": severity,
        "actor_email": actor_email or "",
        "actor_user_id": actor_user_id,
        "organization_id": organization_id,
        "target_organization_id": target_organization_id,
        "ip": ip or "",
        "path": path,
        "method": method,
        "details": redact(details or {}),
        "at": utcnow(),
    }


def _maybe_notify(rec: Dict[str, Any]) -> None:
    if rec["severity"] not in _NOTIFY_SEVERITIES:
        return
    try:
        from app.events.notifications import notify_super_admins
        notify_super_admins(
            "security_event",
            f"Security event: {rec['type'].replace('_', ' ')}",
            f"{rec['actor_email'] or 'anonymous'} from {rec['ip'] or 'unknown IP'}"
            + (f" on {rec['path']}" if rec.get("path") else ""),
            severity="danger",
            link="/superadmin#security",
            data={"type": rec["type"]},
        )
    except Exception as e:  # pragma: no cover - notification is best effort
        logger.warning("security notification failed: %s", e)


def log_security_event(event_type: str, severity: str = "medium", **kw) -> None:
    """Sync write (safe from threads and sync dependencies)."""
    rec = _record(event_type, severity, **kw)
    logger.warning("[security] %s (%s) actor=%s ip=%s path=%s",
                   event_type, severity, rec["actor_email"], rec["ip"], rec["path"])
    try:
        db = get_sync_db()
        if db is not None:
            db[COLLECTION].insert_one(rec)
    except Exception as e:
        logger.warning("security event write failed: %s", e)
    _maybe_notify(rec)


async def alog_security_event(event_type: str, severity: str = "medium", **kw) -> None:
    rec = _record(event_type, severity, **kw)
    logger.warning("[security] %s (%s) actor=%s ip=%s path=%s",
                   event_type, severity, rec["actor_email"], rec["ip"], rec["path"])
    try:
        db = get_async_db()
        if db is not None:
            await db[COLLECTION].insert_one(rec)
    except Exception as e:
        logger.warning("security event write failed: %s", e)
    _maybe_notify(rec)


def security_event_from_request(request, event_type: str, severity: str = "medium",
                                ctx=None, details: Optional[Dict[str, Any]] = None,
                                target_organization_id: Optional[str] = None) -> None:
    """Convenience wrapper pulling actor/ip/path from a request + TenantContext."""
    meta = request_meta(request)
    log_security_event(
        event_type, severity,
        actor_email=getattr(ctx, "email", None),
        actor_user_id=getattr(ctx, "user_id", None),
        organization_id=getattr(ctx, "organization_id", None),
        target_organization_id=target_organization_id,
        ip=meta["ip"],
        path=str(request.url.path) if request is not None else None,
        method=request.method if request is not None else None,
        details=details,
    )
