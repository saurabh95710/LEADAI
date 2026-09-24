"""Transactional email with a durable outbox.

Every email is first written to the ``email_outbox`` collection (status
``queued``). When SMTP is configured (SMTP_HOST / SMTP_PORT / SMTP_USER /
SMTP_PASSWORD / SMTP_FROM, editable from the env panel) it is sent right
away and marked ``sent`` / ``failed``; otherwise it stays ``queued`` so a
Super Admin can see exactly what would have been delivered.

Never put a plaintext password in an email — only one-time links.
"""
import logging
import smtplib
from email.message import EmailMessage
from typing import Optional

from app.db.models import utcnow
from app.db.mongo import get_sync_db

logger = logging.getLogger(__name__)

COLLECTION = "email_outbox"


def _env(name: str, default: str = "") -> str:
    try:
        from app.admin.envvars import get_envvar_str
        return get_envvar_str(name, default) or default
    except Exception:
        return default


def public_base_url() -> str:
    """Absolute origin used in emailed links (PUBLIC_BASE_URL env var)."""
    return _env("PUBLIC_BASE_URL", "").rstrip("/")


def absolute_url(path: str) -> str:
    return f"{public_base_url()}{path}"


def send_email(to: str, subject: str, body: str, *, kind: str = "generic",
               organization_id: Optional[str] = None) -> str:
    """Queue (and send when SMTP is configured). Returns the final status."""
    doc = {
        "to": to.strip().lower(), "subject": subject, "body": body, "kind": kind,
        "organization_id": organization_id, "status": "queued",
        "created_at": utcnow(), "sent_at": None, "error": None,
    }
    db = get_sync_db()
    oid = None
    try:
        if db is not None:
            oid = db[COLLECTION].insert_one(doc).inserted_id
    except Exception as e:
        logger.warning("email outbox write failed: %s", e)

    host = _env("SMTP_HOST")
    if not host:
        logger.info("[email] queued (%s) to %s: %s", kind, to, subject)
        return "queued"
    status, error = "sent", None
    try:
        port = int(_env("SMTP_PORT", "587") or 587)
        msg = EmailMessage()
        msg["From"] = _env("SMTP_FROM", "no-reply@leadai.local")
        msg["To"], msg["Subject"] = to, subject
        msg.set_content(body)
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            smtp.starttls()
            if _env("SMTP_USER"):
                smtp.login(_env("SMTP_USER"), _env("SMTP_PASSWORD"))
            smtp.send_message(msg)
    except Exception as e:
        status, error = "failed", str(e)[:300]
        logger.warning("[email] send failed to %s: %s", to, e)
    if db is not None and oid is not None:
        try:
            db[COLLECTION].update_one({"_id": oid}, {"$set": {
                "status": status, "error": error,
                "sent_at": utcnow() if status == "sent" else None}})
        except Exception:
            pass
    return status
