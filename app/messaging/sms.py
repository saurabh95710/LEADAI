"""SMS gateway with a durable outbox (``sms_outbox``), like the email outbox.

Providers (SMS_PROVIDER):
- ``twilio``   — TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM (a number, or a
                 Messaging Service SID starting with "MG"). Worldwide.
- ``fast2sms`` — FAST2SMS_API_KEY (India, "Quick SMS" route).
- ``webhook``  — SMS_WEBHOOK_URL (+ optional SMS_WEBHOOK_TOKEN): LeadAI POSTs
                 {"to", "message", "kind"} as JSON, for any other provider (MSG91,
                 Textlocal, an in-house gateway…).
- empty        — nothing is sent; messages stay ``queued`` in the outbox so a Super
                 Admin can see what would have gone out.

Spending guards: SMS_DAILY_LIMIT messages a day in total, SMS_PER_NUMBER_DAILY_LIMIT
to any one number (stops SMS-pumping abuse of public forms). Codes in the stored
copy are masked; the SMS itself carries them.
"""
import logging
import re
from datetime import timedelta
from typing import Any, Dict, Optional

import httpx

from app.db.models import utcnow
from app.db.mongo import get_sync_db
from app.messaging.phone import mask_phone, normalize_phone

logger = logging.getLogger(__name__)

COLLECTION = "sms_outbox"
PROVIDERS = ("twilio", "fast2sms", "webhook")
_CODE_RE = re.compile(r"\b\d{4,8}\b")


def _env(name: str, default: str = "") -> str:
    try:
        from app.admin.envvars import get_envvar_str
        return (get_envvar_str(name, default) or default).strip()
    except Exception:
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(_env(name, str(default)) or default))
    except (TypeError, ValueError):
        return default


def provider_name() -> str:
    p = _env("SMS_PROVIDER").lower()
    return p if p in PROVIDERS else ""


def provider_status() -> Dict[str, Any]:
    """What the gateway is set to — never a secret, only whether it is set."""
    p = provider_name()
    need = {"twilio": ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM"),
            "fast2sms": ("FAST2SMS_API_KEY",), "webhook": ("SMS_WEBHOOK_URL",)}.get(p, ())
    missing = [n for n in need if not _env(n)]
    return {
        "provider": p, "configured": bool(p) and not missing, "missing": missing,
        # every provider's saved (non-secret) settings, so switching back shows them
        "from": _env("TWILIO_FROM"),
        "webhook_url": _env("SMS_WEBHOOK_URL"),
        "twilio_account_sid": _env("TWILIO_ACCOUNT_SID"),
        "secrets_set": {n: bool(_env(n)) for n in ("TWILIO_AUTH_TOKEN", "FAST2SMS_API_KEY", "SMS_WEBHOOK_TOKEN")},
        "default_country_code": _env("SMS_DEFAULT_COUNTRY_CODE", "+91") or "+91",
        "daily_limit": _int_env("SMS_DAILY_LIMIT", 500),
        "per_number_daily_limit": _int_env("SMS_PER_NUMBER_DAILY_LIMIT", 10),
        "sent_today": sent_today(),
    }


# texts that let someone back into their account are limited separately, so
# sign-in alerts and welcome texts can never use up a person's reset codes
CODE_KINDS = ("password_reset_code", "password_reset_info", "verification_code")


def sent_today(to: Optional[str] = None, *, codes: Optional[bool] = None) -> int:
    db = get_sync_db()
    if db is None:
        return 0
    q: Dict[str, Any] = {"created_at": {"$gte": utcnow() - timedelta(days=1)},
                         "status": {"$in": ["sent", "queued"]}}
    q["kind"] = {"$in": list(CODE_KINDS)} if codes else ({"$nin": list(CODE_KINDS) + ["test"]}
                                                         if codes is False else {"$ne": "test"})
    if to:
        q["to"] = to
    try:
        return db[COLLECTION].count_documents(q)
    except Exception:
        return 0


def _send_twilio(to: str, body: str) -> str:
    sid, token, sender = _env("TWILIO_ACCOUNT_SID"), _env("TWILIO_AUTH_TOKEN"), _env("TWILIO_FROM")
    data = {"To": to, "Body": body}
    data["MessagingServiceSid" if sender.startswith("MG") else "From"] = sender
    r = httpx.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                   data=data, auth=(sid, token), timeout=15)
    if r.status_code >= 300:
        if r.headers.get("content-type", "").startswith("application/json"):
            data = r.json() or {}
            code = f" (code {data['code']})" if data.get("code") else ""
            raise RuntimeError(f"Twilio HTTP {r.status_code}{code}: {str(data.get('message', ''))[:160]}")
        raise RuntimeError(f"Twilio HTTP {r.status_code}")
    return str((r.json() or {}).get("sid") or "")


def _send_fast2sms(to: str, body: str) -> str:
    if not to.startswith("+91"):
        raise RuntimeError("Fast2SMS only delivers to Indian (+91) numbers")
    r = httpx.post("https://www.fast2sms.com/dev/bulkV2",
                   headers={"authorization": _env("FAST2SMS_API_KEY")},
                   json={"route": "q", "message": body, "numbers": to[3:], "flash": 0}, timeout=15)
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code >= 300 or not data.get("return"):
        msg = data.get("message")
        code = f" (code {data['status_code']})" if data.get("status_code") else ""
        raise RuntimeError(f"Fast2SMS HTTP {r.status_code}{code}: {msg if isinstance(msg, str) else ''}"[:200])
    return str(data.get("request_id") or "")


def _send_webhook(to: str, body: str, kind: str) -> str:
    headers = {"Content-Type": "application/json"}
    if _env("SMS_WEBHOOK_TOKEN"):
        headers["Authorization"] = "Bearer " + _env("SMS_WEBHOOK_TOKEN")
    r = httpx.post(_env("SMS_WEBHOOK_URL"), json={"to": to, "message": body, "kind": kind},
                   headers=headers, timeout=15)
    if r.status_code >= 300:
        raise RuntimeError(f"SMS webhook HTTP {r.status_code}")
    return ""


def send_sms(to: Optional[str], body: str, *, kind: str = "generic", organization_id: Optional[str] = None,
             user_id: Optional[str] = None, campaign_id: Optional[str] = None,
             bypass_limits: bool = False) -> str:
    """Send one SMS. Returns the status: sent | queued (no provider) | failed |
    invalid (not a phone number) | limited (a daily limit was reached)."""
    e164 = normalize_phone(to)
    body = (body or "").strip()[:640]
    doc: Dict[str, Any] = {
        "to": e164 or (str(to or "")[:40]), "body": _CODE_RE.sub("••••••", body) if kind in (
            "password_reset_code", "verification_code") else body,
        "kind": kind, "organization_id": organization_id, "user_id": user_id, "campaign_id": campaign_id,
        "provider": provider_name() or None, "status": "queued", "error": None,
        "created_at": utcnow(), "sent_at": None, "provider_id": None,
    }
    if not e164:
        doc.update(status="invalid", error="Not a valid phone number")
    elif not bypass_limits and sent_today() >= _int_env("SMS_DAILY_LIMIT", 500):
        doc.update(status="limited", error="Daily SMS limit reached (SMS_DAILY_LIMIT)")
    elif not bypass_limits and sent_today(e164, codes=kind in CODE_KINDS) >= _int_env("SMS_PER_NUMBER_DAILY_LIMIT", 10):
        doc.update(status="limited", error="Daily limit for this number reached (SMS_PER_NUMBER_DAILY_LIMIT)")
    db = get_sync_db()
    oid = None
    try:
        if db is not None:
            oid = db[COLLECTION].insert_one(doc).inserted_id
    except Exception as e:
        logger.warning("sms outbox write failed: %s", e)
    if doc["status"] != "queued":
        logger.info("[sms] %s (%s) to %s: %s", doc["status"], kind, mask_phone(e164), doc["error"])
        return doc["status"]
    provider = provider_name()
    if not provider or not provider_status()["configured"]:
        logger.info("[sms] queued (%s) to %s — no SMS provider configured", kind, mask_phone(e164))
        return "queued"
    status, error, pid = "sent", None, None
    try:
        if provider == "twilio":
            pid = _send_twilio(e164, body)
        elif provider == "fast2sms":
            pid = _send_fast2sms(e164, body)
        else:
            pid = _send_webhook(e164, body, kind)
    except Exception as e:
        status, error = "failed", str(e)[:300]
        logger.warning("[sms] send failed to %s via %s: %s", mask_phone(e164), provider, e)
    limit_hit = status == "failed" and _is_balance_error(error or "")
    if db is not None and oid is not None:
        try:
            sets: Dict[str, Any] = {"status": status, "error": error, "provider_id": pid or None,
                                    "sent_at": utcnow() if status == "sent" else None}
            if limit_hit:
                sets["limit_hit"] = True
            db[COLLECTION].update_one({"_id": oid}, {"$set": sets})
        except Exception:
            pass
    if limit_hit:
        try:
            from app.limits.service import record_sms_error
            record_sms_error(provider, error or "")
        except Exception as e:  # the monitor never breaks sending
            logger.debug("sms limit note failed: %s", e)
    return status


def _is_balance_error(text: str) -> bool:
    """Fast2SMS 416 (insufficient balance) / Twilio 20005, 30002 (suspended)."""
    try:
        from app.limits.checkers import is_sms_limit_error
        return is_sms_limit_error(text)
    except Exception:
        return False
