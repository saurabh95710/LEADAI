"""Super Admin → Messaging: the email / SMS gateway, the automatic messages and
messages to anyone (users, organization admins, partners).

  GET  /api/super-admin/messaging               gateway status (no secrets) + automatic messages
  PUT  /api/super-admin/messaging/sms           SMS provider settings (secrets stored encrypted)
  PUT  /api/super-admin/messaging/automatic     welcome / login-alert switches
  POST /api/super-admin/messaging/test          send a test email or SMS
  GET  /api/super-admin/messaging/people        recipient search (phones masked)
  GET  /api/super-admin/messaging/sms-outbox    recent texts (codes and phones masked)
  POST /api/super-admin/messages                send a message
  GET  /api/super-admin/messages                sent messages
  GET  /api/super-admin/messages/{id}           one message with its deliveries
"""
import asyncio
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.admin.audit import aaudit, request_meta
from app.auth.tenant import TenantContext, require_platform_role
from app.db.mongo import get_sync_db

router = APIRouter(prefix="/api/super-admin", tags=["super-admin-messaging"])
SUPER = require_platform_role("super_admin")

_SMS_FIELDS = {  # body field -> environment variable
    "provider": "SMS_PROVIDER", "twilio_account_sid": "TWILIO_ACCOUNT_SID", "twilio_from": "TWILIO_FROM",
    "webhook_url": "SMS_WEBHOOK_URL", "default_country_code": "SMS_DEFAULT_COUNTRY_CODE",
    "daily_limit": "SMS_DAILY_LIMIT", "per_number_daily_limit": "SMS_PER_NUMBER_DAILY_LIMIT",
}
_SMS_SECRETS = {"twilio_auth_token": "TWILIO_AUTH_TOKEN", "fast2sms_api_key": "FAST2SMS_API_KEY",
                "webhook_token": "SMS_WEBHOOK_TOKEN"}


def _db():
    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return db


def _email_status() -> Dict[str, Any]:
    """Mail-server setup as the Super Admin sees it: what is set, what is
    missing, and how the outbox is doing. Never the password."""
    from app.events.email import COLLECTION, public_base_url, smtp_config
    cfg = smtp_config()
    out: Dict[str, Any] = {"queued": 0, "sent": 0, "failed": 0, "last_error": None,
                           "last_error_at": None, "last_sent_at": None}
    db = get_sync_db()
    if db is not None:
        for st in ("queued", "sent", "failed"):
            out[st] = db[COLLECTION].count_documents({"status": st})
        bad = db[COLLECTION].find_one({"status": "failed"}, {"error": 1, "created_at": 1},
                                      sort=[("created_at", -1)])
        if bad:
            out["last_error"], out["last_error_at"] = bad.get("error"), bad.get("created_at")
        ok = db[COLLECTION].find_one({"status": "sent"}, {"sent_at": 1}, sort=[("sent_at", -1)])
        if ok:
            out["last_sent_at"] = ok.get("sent_at")
    missing = [name for name, ok in (("SMTP_HOST", cfg["host"]), ("SMTP_FROM", cfg["from"]),
                                     ("PUBLIC_BASE_URL", public_base_url())) if not ok]
    if cfg["host"] and cfg["user"] and not cfg["password_set"]:
        missing.append("SMTP_PASSWORD")
    return {"configured": bool(cfg["host"]), "host": cfg["host"], "port": cfg["port"],
            "security": cfg["security"], "auth": bool(cfg["user"]), "user": cfg["user"],
            "password_set": cfg["password_set"],
            "from": cfg["from"] or "no-reply@leadai.local", "from_set": bool(cfg["from"]),
            "public_base_url": public_base_url(), "missing": missing, "outbox": out,
            "note": "Email server settings can be updated here (stored encrypted) or configured in the server's .env file."}


_EMAIL_FIELDS = {
    "smtp_host": "SMTP_HOST",
    "smtp_port": "SMTP_PORT",
    "smtp_security": "SMTP_SECURITY",
    "smtp_user": "SMTP_USER",
    "smtp_from": "SMTP_FROM",
    "public_base_url": "PUBLIC_BASE_URL",
}
_EMAIL_SECRETS = {"smtp_password": "SMTP_PASSWORD"}


class EmailSettingsBody(BaseModel):
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_security: Optional[str] = None
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from: Optional[str] = None
    public_base_url: Optional[str] = None
    clear_secrets: List[str] = []


@router.put("/messaging/email")
async def update_email_settings(body: EmailSettingsBody, request: Request, ctx: TenantContext = Depends(SUPER)):
    """Update SMTP email gateway settings. Secrets (SMTP_PASSWORD) are stored encrypted."""
    from app.admin import envvars
    from app.events.email import SECURITY_MODES
    data = body.model_dump(exclude_none=True)
    clear = data.pop("clear_secrets", []) or []
    by_user = ctx.email or "super_admin"

    if data.get("smtp_security") and data["smtp_security"].strip().lower() not in SECURITY_MODES:
        raise HTTPException(status_code=422, detail=f"smtp_security must be one of: {', '.join(SECURITY_MODES)}")
    if "smtp_port" in data and data["smtp_port"] is not None:
        try:
            port = int(data["smtp_port"])
            if not 1 <= port <= 65535:
                raise ValueError()
            data["smtp_port"] = port
        except (ValueError, TypeError):
            raise HTTPException(status_code=422, detail="smtp_port must be between 1 and 65535")

    changed: List[str] = []
    for field, env in list(_EMAIL_FIELDS.items()) + list(_EMAIL_SECRETS.items()):
        if field not in data:
            continue
        val = data[field]
        if field in _EMAIL_SECRETS and not str(val).strip():
            continue  # keep existing secret
        val_to_save = str(val).strip() if isinstance(val, str) else val
        ok = await envvars.aset_envvar_override(env, val_to_save, by=by_user, allow_locked=True)
        if not ok:
            raise HTTPException(status_code=503, detail=f"Couldn't save {field}. Try again.")
        envvars._CACHE.pop(env, None)
        changed.append(field)

    for field in clear:
        if field in _EMAIL_SECRETS:
            env = _EMAIL_SECRETS[field]
            envvars.delete_envvar_override(env, allow_locked=True)
            envvars._CACHE.pop(env, None)
            changed.append(f"{field} (cleared)")

    envvars._CACHE.clear()
    meta = request_meta(request)
    await aaudit("messaging.email_settings_changed", "settings", user=ctx.audit_user(), ip=meta["ip"],
                 user_agent=meta["user_agent"], details={"changed": changed})
    return {"success": True, "email": await asyncio.to_thread(_email_status)}


@router.get("/messaging")
async def messaging_overview(ctx: TenantContext = Depends(SUPER)):
    from app.messaging.service import auto_settings
    from app.messaging.sms import provider_status
    db = _db()
    return {"success": True, "email": await asyncio.to_thread(_email_status), "sms": await asyncio.to_thread(provider_status),
            "automatic": await asyncio.to_thread(auto_settings, db)}


class SmsSettingsBody(BaseModel):
    provider: Optional[str] = None
    twilio_account_sid: Optional[str] = None
    twilio_auth_token: Optional[str] = None
    twilio_from: Optional[str] = None
    fast2sms_api_key: Optional[str] = None
    webhook_url: Optional[str] = None
    webhook_token: Optional[str] = None
    default_country_code: Optional[str] = None
    daily_limit: Optional[int] = None
    per_number_daily_limit: Optional[int] = None
    clear_secrets: List[str] = []       # e.g. ["webhook_token"]: remove a stored secret


@router.put("/messaging/sms")
async def update_sms_settings(body: SmsSettingsBody, request: Request, ctx: TenantContext = Depends(SUPER)):
    """Secrets are only replaced when a new value is sent (empty = keep)."""
    from app.admin import envvars
    from app.messaging.sms import PROVIDERS, provider_status
    data = body.model_dump(exclude_none=True)
    clear = data.pop("clear_secrets", []) or []
    unknown = [c for c in clear if c not in _SMS_SECRETS]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown secret(s): {', '.join(unknown)}")
    if "provider" in data and data["provider"] not in ("",) + PROVIDERS:
        raise HTTPException(status_code=422, detail="Provider must be twilio, fast2sms, webhook or empty")
    if data.get("webhook_url") and not str(data["webhook_url"]).startswith("https://"):
        raise HTTPException(status_code=422, detail="The webhook URL must start with https://")
    if data.get("default_country_code") and not str(data["default_country_code"]).lstrip("+").isdigit():
        raise HTTPException(status_code=422, detail="Country code looks like +91")
    for k in ("daily_limit", "per_number_daily_limit"):
        if k in data and not 0 <= int(data[k]) <= 1_000_000:
            raise HTTPException(status_code=422, detail=f"{k} must be between 0 and 1000000")
    changed: List[str] = []
    for field, env in list(_SMS_FIELDS.items()) + list(_SMS_SECRETS.items()):
        if field not in data:
            continue
        value = data[field]
        if field in _SMS_SECRETS and not str(value).strip():
            continue                                    # keep the stored secret
        ok = await envvars.aset_envvar_override(env, str(value).strip() if isinstance(value, str) else value,
                                                by=ctx.email or "super_admin", allow_locked=True)
        if not ok:
            raise HTTPException(status_code=503, detail="Couldn't save the settings. Try again.")
        envvars._CACHE.pop(env, None)
        changed.append(field)
    for field in clear:
        envvars.delete_envvar_override(_SMS_SECRETS[field], allow_locked=True)
        envvars._CACHE.pop(_SMS_SECRETS[field], None)
        changed.append(f"{field} (cleared)")
    meta = request_meta(request)
    await aaudit("messaging.sms_settings_changed", "settings", user=ctx.audit_user(), ip=meta["ip"],
                 user_agent=meta["user_agent"], details={"changed": changed})   # names only, never values
    return {"success": True, "sms": await asyncio.to_thread(provider_status)}


class AutomaticBody(BaseModel):
    welcome_sms: Optional[bool] = None
    login_alert_email: Optional[bool] = None
    login_alert_sms: Optional[bool] = None
    login_alert_every_minutes: Optional[int] = None


@router.put("/messaging/automatic")
async def update_automatic(body: AutomaticBody, request: Request, ctx: TenantContext = Depends(SUPER)):
    from app.messaging.service import update_auto_settings
    try:
        value = update_auto_settings(body.model_dump(exclude_none=True), ctx.email or "super_admin", _db())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    meta = request_meta(request)
    await aaudit("messaging.automatic_changed", "settings", user=ctx.audit_user(), ip=meta["ip"],
                 user_agent=meta["user_agent"], details=value)
    return {"success": True, "automatic": value}


class TestBody(BaseModel):
    channel: str
    to: str


@router.post("/messaging/test")
async def send_test(body: TestBody, request: Request, ctx: TenantContext = Depends(SUPER)):
    from app.events.email import send_email
    from app.messaging.phone import normalize_phone
    from app.messaging.sms import send_sms
    if body.channel == "sms":
        if not normalize_phone(body.to):
            raise HTTPException(status_code=422, detail="Enter a phone number like +91 98765 43210")
        status = await asyncio.to_thread(send_sms, body.to, "LeadAI test message: your SMS gateway works.",
                                         kind="test", bypass_limits=True)
    elif body.channel == "email":
        if "@" not in body.to:
            raise HTTPException(status_code=422, detail="Enter an email address")
        status = await asyncio.to_thread(send_email, body.to, "LeadAI test email",
                                         "This is a test from Super Admin → Messaging. Your email gateway works.",
                                         kind="test")
    else:
        raise HTTPException(status_code=422, detail="channel must be email or sms")
    error = None
    if status in ("failed", "invalid", "limited"):
        db = _db()
        coll = "sms_outbox" if body.channel == "sms" else "email_outbox"
        last = db[coll].find_one({"kind": "test"}, sort=[("created_at", -1)]) or {}
        error = last.get("error")
    meta = request_meta(request)
    await aaudit("messaging.test_sent", "settings", user=ctx.audit_user(), ip=meta["ip"],
                 user_agent=meta["user_agent"], details={"channel": body.channel, "status": status})
    return {"success": status in ("sent", "queued"), "status": status, "error": error,
            "message": {"sent": "Sent.", "queued": "Saved in the outbox — no provider is configured, so nothing "
                                                   "was delivered.", }.get(status, error or status)}


@router.get("/messaging/people")
async def people(q: str = "", organization_id: Optional[str] = None, limit: int = Query(25, ge=1, le=100),
                 ctx: TenantContext = Depends(SUPER)):
    from app.messaging.service import search_people
    return {"success": True, "people": await asyncio.to_thread(search_people, _db(), q,
                                                               organization_id=organization_id, limit=limit)}


@router.get("/messaging/sms-outbox")
async def sms_outbox(limit: int = Query(50, ge=1, le=500), status: Optional[str] = None,
                     ctx: TenantContext = Depends(SUPER)):
    from app.messaging.phone import mask_phone
    from app.messaging.sms import COLLECTION
    from app.services.tenant_api_keys import iso_utc
    q: Dict[str, Any] = {"status": status} if status else {}
    rows = []
    for d in _db()[COLLECTION].find(q).sort("created_at", -1).limit(limit):
        rows.append({"id": str(d["_id"]), "to": mask_phone(d.get("to")) or d.get("to"), "kind": d.get("kind"),
                     "status": d.get("status"), "error": d.get("error"), "provider": d.get("provider"),
                     "body": d.get("body"), "campaign_id": d.get("campaign_id"),
                     "created_at": iso_utc(d.get("created_at"))})
    return {"success": True, "items": rows}


class MessageBody(BaseModel):
    channels: List[str]
    subject: str = ""
    body: str
    sms_body: Optional[str] = None
    user_ids: List[str] = []
    organization_ids: List[str] = []
    audience: Optional[str] = None      # org_admins | all_users | partners


@router.post("/messages")
async def send_message(body: MessageBody, request: Request, ctx: TenantContext = Depends(SUPER)):
    from app.messaging.service import resolve_recipients, start_campaign
    if body.audience not in (None, "", "org_admins", "all_users", "partners"):
        raise HTTPException(status_code=422, detail="Unknown audience")
    from app.messaging.service import check_channels
    db = _db()
    try:
        check_channels(body.channels)
        people = resolve_recipients(db, user_ids=body.user_ids, organization_ids=body.organization_ids,
                                    audience=body.audience or None)
        res = start_campaign(db, people, channels=body.channels, subject=body.subject, body=body.body,
                             sms_body=body.sms_body, scope="platform",
                             sender={"email": ctx.email, "user_id": ctx.user_id, "role": "super_admin"})
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    meta = request_meta(request)
    await aaudit("messaging.message_sent", "messaging", user=ctx.audit_user(), ip=meta["ip"],
                 user_agent=meta["user_agent"], resource_type="message", resource_id=res["id"],
                 details={"recipients": res["recipient_count"], "channels": res["channels"],
                          "audience": body.audience, "subject": body.subject[:120]})
    return {"success": True, "message": res}


@router.get("/messages")
async def list_messages(limit: int = Query(50, ge=1, le=200), ctx: TenantContext = Depends(SUPER)):
    from app.messaging.service import CAMPAIGNS, campaign_view
    db = _db()
    ids = [str(d["_id"]) for d in db[CAMPAIGNS].find({}, {"_id": 1}).sort("created_at", -1).limit(limit)]
    return {"success": True, "messages": [campaign_view(db, i) for i in ids]}


@router.get("/messages/{message_id}")
async def get_message(message_id: str, ctx: TenantContext = Depends(SUPER)):
    from app.messaging.service import campaign_view
    view = campaign_view(_db(), message_id, with_deliveries=True)
    if not view:
        raise HTTPException(status_code=404, detail="Message not found")
    return {"success": True, "message": view}
