"""Audit logging for admin actions.

Every security-relevant or destructive action (logins, settings changes,
user management, deletions, retries, maintenance toggles) is written to the
``audit_logs`` collection with who, what, when and from where. Secrets are
redacted before they ever reach the log. The whole system can be toggled
from the Security page (``security.audit_logging``) — when off, nothing is
written, so the toggle itself is the only thing that isn't audited.
"""
import logging
import time
from typing import Any, Dict, Optional

from app.db.mongo import get_async_db, get_sync_db

logger = logging.getLogger(__name__)

COLLECTION = "audit_logs"

_REDACT_KEYS = {"token", "password", "password_hash", "apify.token", "session_secret",
                "admin_password_hash", "new_password", "old_password", "secret"}


def _redact(details: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively mask known secret fields before logging."""
    if not isinstance(details, dict):
        return {"value": "<redacted>"} if isinstance(details, str) else details
    clean: Dict[str, Any] = {}
    for key, value in details.items():
        low = str(key).lower()
        if low in _REDACT_KEYS or "password" in low or low == "token":
            clean[key] = "••••"
        elif isinstance(value, dict):
            clean[key] = _redact(value)
        else:
            clean[key] = value
    return clean


def audit(action: str, category: str, *, user: Optional[Dict[str, Any]] = None,
          ip: Optional[str] = None, success: bool = True,
          details: Optional[Dict[str, Any]] = None) -> None:
    """Sync audit write (used from background threads and sync contexts)."""
    try:
        db = get_sync_db()
        if db is None:
            return
        db[COLLECTION].insert_one({
            "action": action,
            "category": category,
            "user": (user or {}).get("email", ""),
            "role": (user or {}).get("role", ""),
            "ip": ip or "",
            "success": success,
            "details": _redact(details or {}),
            "at": time.time(),
        })
    except Exception as e:
        logger.warning(f"Audit write failed: {e}")


async def aaudit(action: str, category: str, *, user: Optional[Dict[str, Any]] = None,
                 ip: Optional[str] = None, success: bool = True,
                 details: Optional[Dict[str, Any]] = None) -> None:
    """Async audit write (FastAPI handlers)."""
    try:
        db = get_async_db()
        if db is None:
            return
        await db[COLLECTION].insert_one({
            "action": action,
            "category": category,
            "user": (user or {}).get("email", ""),
            "role": (user or {}).get("role", ""),
            "ip": ip or "",
            "success": success,
            "details": _redact(details or {}),
            "at": time.time(),
        })
    except Exception as e:
        logger.warning(f"Audit write failed: {e}")
