"""Transactional email with a durable outbox.

Every email is first written to the ``email_outbox`` collection (status
``queued``). When SMTP is configured (SMTP_HOST / SMTP_PORT / SMTP_USER /
SMTP_PASSWORD / SMTP_FROM / SMTP_SECURITY in the server's .env — the
credentials are locked there so nobody can redirect password-reset email
from the app) it is sent right away and marked ``sent`` / ``failed``;
otherwise it stays ``queued`` so a Super Admin can see exactly what would
have been delivered. Queued emails are never re-sent later: their one-time
links are not stored.

SMTP_SECURITY: ``ssl`` (implicit TLS, port 465 — Hostinger, Zoho, most
hosts), ``starttls`` (port 587 — Gmail, Outlook, SendGrid, Brevo), ``none``
(local relay only), ``auto`` (default: ssl on 465, starttls otherwise).

Never put a plaintext password in an email — only one-time links.

Global Settings applied to every email (read at send time, no restart):

* ``general.app.name`` — the product name: "LeadAI" in a subject or body is
  replaced by the configured name (:func:`brand_name`);
* ``email.sender_name`` — display name in From (else the name already in
  SMTP_FROM, else the product name);
* ``email.reply_to`` — Reply-To header when set;
* ``email.footer_text`` — appended as the last line of every body when set.
"""
import logging
import re
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from typing import Any, Optional

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


DEFAULT_BRAND = "LeadAI"
_BRAND_RE = re.compile(r"\bLeadAI\b")
_EMAIL_RE = re.compile(r"^[^\s@<>,;]+@[^\s@<>,;]+\.[^\s@<>,;]+$")


def _setting(key: str) -> Any:
    try:
        from app.admin.settings import get_setting
        return get_setting(key)
    except Exception:
        return None


def _one_line(value: Any, limit: int = 200) -> str:
    """Setting text safe for a mail header: one line, trimmed."""
    return re.sub(r"[\r\n\t]+", " ", str(value or "")).strip()[:limit]


def brand_name() -> str:
    """The product name customers see (Global Settings → Application name);
    "LeadAI" when it isn't set."""
    return _one_line(_setting("general.app.name"), 80) or DEFAULT_BRAND


def apply_brand(text: str) -> str:
    """``text`` with the word "LeadAI" replaced by the configured product name."""
    brand = brand_name()
    if not text or brand == DEFAULT_BRAND:
        return text or ""
    return _BRAND_RE.sub(lambda _m: brand, text)


def sender_header(smtp_from: Optional[str] = None) -> str:
    """From header: ``email.sender_name`` (else the name already in SMTP_FROM,
    else the product name) with the SMTP_FROM address."""
    raw = smtp_from if smtp_from is not None else _env("SMTP_FROM", "no-reply@leadai.local")
    name, addr = parseaddr(raw or "")
    addr = addr or "no-reply@leadai.local"
    display = _one_line(_setting("email.sender_name"), 120) or _one_line(name, 120) or brand_name()
    return formataddr((display, addr))


def reply_to_address() -> str:
    """``email.reply_to`` when it is a usable address, else ""."""
    value = _one_line(_setting("email.reply_to"), 254)
    return value if _EMAIL_RE.match(value) else ""


def email_footer() -> str:
    """``email.footer_text`` (may be several lines), "" when not set."""
    return str(_setting("email.footer_text") or "").strip()[:2000]


def compose(subject: str, body: str) -> tuple:
    """(subject, body) exactly as sent: product name applied, footer appended."""
    subject = _one_line(apply_brand(subject), 300)
    body = apply_brand(body or "")
    footer = email_footer()
    if footer:
        body = f"{body.rstrip()}\n\n{footer}"
    return subject, body


def build_message(to: str, subject: str, body: str) -> EmailMessage:
    """The SMTP message for an already composed subject / body."""
    msg = EmailMessage()
    msg["From"] = sender_header()
    msg["To"], msg["Subject"] = to, subject
    reply_to = reply_to_address()
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    return msg


def public_base_url() -> str:
    """Absolute origin used in emailed links (PUBLIC_BASE_URL env var)."""
    return _env("PUBLIC_BASE_URL", "").rstrip("/")


def absolute_url(path: str) -> str:
    return f"{public_base_url()}{path}"


_SECRET_LINK_RE = re.compile(r"(token=|/invite/)[A-Za-z0-9_\-]{8,}")


def redact_links(body: str) -> str:
    """Body with single-use tokens (reset / invitation links) removed: the
    outbox is a log, never a way to obtain a live link."""
    return _SECRET_LINK_RE.sub(lambda m: m.group(1) + "[redacted]", body or "")


SECURITY_MODES = ("auto", "ssl", "starttls", "none")


def smtp_config() -> dict:
    """The mail-server settings in use (never the password)."""
    host = _env("SMTP_HOST").strip()
    try:
        port = int(_env("SMTP_PORT", "587") or 587)
    except ValueError:
        port = 587
    mode = (_env("SMTP_SECURITY", "auto") or "auto").strip().lower()
    if mode not in SECURITY_MODES:
        mode = "auto"
    security = ("ssl" if port == 465 else "starttls") if mode == "auto" else mode
    return {"host": host, "port": port, "security": security, "user": _env("SMTP_USER").strip(),
            "password_set": bool(_env("SMTP_PASSWORD")), "from": _env("SMTP_FROM").strip()}


def friendly_smtp_error(exc: Exception) -> str:
    """What went wrong, in words a Super Admin can act on."""
    import socket
    import ssl
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return ("The mail server rejected SMTP_USER / SMTP_PASSWORD. For Gmail use an app password; "
                "for Hostinger the full mailbox address and its password.")
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return "The mail server refused the sender address: SMTP_FROM must be a mailbox you can send from."
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "The mail server refused the recipient address."
    if isinstance(exc, (ssl.SSLError, smtplib.SMTPServerDisconnected)):
        return ("The secure connection failed: port 465 needs SMTP_SECURITY=ssl, port 587 needs "
                "SMTP_SECURITY=starttls.")
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "The mail server did not answer in time: check SMTP_HOST and SMTP_PORT (the firewall may block it)."
    if isinstance(exc, (socket.gaierror, ConnectionRefusedError, OSError)):
        return f"Could not reach the mail server: check SMTP_HOST and SMTP_PORT ({str(exc)[:120]})."
    return str(exc)[:300]


def deliver(msg: EmailMessage, cfg: Optional[dict] = None) -> None:
    """Send one message over SMTP with the configured security. Raises on failure."""
    import ssl
    cfg = cfg or smtp_config()
    context = ssl.create_default_context()
    smtp: smtplib.SMTP
    if cfg["security"] == "ssl":
        smtp = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=15, context=context)
    else:
        smtp = smtplib.SMTP(cfg["host"], cfg["port"], timeout=15)
    with smtp:
        if cfg["security"] == "starttls":
            smtp.starttls(context=context)
        if cfg["user"]:
            smtp.login(cfg["user"], _env("SMTP_PASSWORD"))
        smtp.send_message(msg)


def send_email(to: str, subject: str, body: str, *, kind: str = "generic",
               organization_id: Optional[str] = None, campaign_id: Optional[str] = None) -> str:
    """Queue (and send when SMTP is configured). Returns the final status.
    The stored outbox copy has its one-time links redacted; the email itself
    is sent with the full body."""
    subject, body = compose(subject, body)
    doc = {
        "to": to.strip().lower(), "subject": subject, "body": redact_links(body), "kind": kind,
        "organization_id": organization_id, "campaign_id": campaign_id, "status": "queued",
        "created_at": utcnow(), "sent_at": None, "error": None,
    }
    db = get_sync_db()
    oid = None
    try:
        if db is not None:
            oid = db[COLLECTION].insert_one(doc).inserted_id
    except Exception as e:
        logger.warning("email outbox write failed: %s", e)

    cfg = smtp_config()
    if not cfg["host"]:
        logger.info("[email] queued (%s) to %s: %s", kind, to, subject)
        return "queued"
    status, error = "sent", None
    limit_hit, raw_error, smtp_code = False, "", None
    try:
        deliver(build_message(to, subject, body), cfg)
    except Exception as e:
        status, error = "failed", friendly_smtp_error(e)
        raw_error, smtp_code = str(e)[:300], getattr(e, "smtp_code", None)
        limit_hit = _is_limit_reply(raw_error, smtp_code)
        if limit_hit:
            error = f"The mail server's sending limit was reached: {raw_error[:200]}"
        logger.warning("[email] send failed to %s: %s", to, e)
    if db is not None and oid is not None:
        try:
            sets = {"status": status, "error": error, "sent_at": utcnow() if status == "sent" else None}
            if limit_hit:
                sets["limit_hit"] = True
            db[COLLECTION].update_one({"_id": oid}, {"$set": sets})
        except Exception:
            pass
    if limit_hit:
        try:
            from app.limits.service import record_email_error
            record_email_error(raw_error, smtp_code)
        except Exception as e:  # the monitor never breaks sending
            logger.debug("email limit note failed: %s", e)
    return status


def _is_limit_reply(text: str, code: Any = None) -> bool:
    """A "sending limit reached" reply (550 5.4.5, 452 4.5.3, 421 4.7.x ...)."""
    try:
        from app.limits.checkers import is_smtp_limit_error
        return is_smtp_limit_error(text, code if isinstance(code, int) else None)
    except Exception:
        return False
