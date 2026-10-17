"""
LeadAI Compliance & Privacy API Routes (Phase 4).

Endpoints:
  GET    /api/public/compliance-notice     Public Data Processing Notice
  POST   /api/compliance/opt-out           Public / prospect opt-out request (sends a check)
  GET    /api/compliance/opt-out/confirm   Confirm an email opt-out (emailed link)
  POST   /api/compliance/opt-out/confirm   Confirm a phone opt-out (SMS code)
  GET    /api/compliance/blocklist         List organization blocklist
  POST   /api/compliance/blocklist         Add contact to blocklist
  DELETE /api/compliance/blocklist/{id}    Remove contact from blocklist
  POST   /api/compliance/delete-request    Right to be Forgotten deletion
  POST   /api/compliance/purge-pii         Trigger PII retention purge
  GET    /api/compliance/settings          Get PII retention settings
  PUT    /api/compliance/settings          Update PII retention settings
"""
import asyncio
import hashlib
import html
import logging
import secrets
from datetime import timedelta

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.admin.audit import request_meta
from app.auth import permissions as P
from app.auth.rate_limit import RateLimiter
from app.auth.tenant import TenantContext, require_org_permission
from app.compliance import service as cs
from app.db.models import utcnow
from app.db.mongo import get_sync_db

logger = logging.getLogger(__name__)
router = APIRouter(tags=["compliance"])

_opt_out_limiter = RateLimiter("compliance_opt_out", 10, 300)
_opt_out_confirm_limiter = RateLimiter("compliance_opt_out_confirm", 20, 300)

OPT_OUT_REQUESTS = "compliance_optout_requests"
_LINK_TTL = timedelta(hours=24)
_CODE_TTL = timedelta(minutes=15)
_CODE_MAX_TRIES = 5


class OptOutRequest(BaseModel):
    contact_type: str = Field(..., description="phone or email")
    contact_value: str = Field(..., description="The contact value to opt out")
    reason: str | None = Field("User requested opt-out", description="Optional reason")


class OptOutConfirm(BaseModel):
    contact_value: str = Field(..., description="The phone number the code was sent to")
    code: str = Field(..., min_length=6, max_length=6)


class BlocklistAddRequest(BaseModel):
    contact_type: str = Field(..., description="phone, email, or username")
    contact_value: str = Field(..., description="Contact value to block")
    reason: str | None = Field("Do not contact", description="Reason for block")


class DeleteProspectRequest(BaseModel):
    identifier: str = Field(..., description="Phone, email, or username of prospect")
    identifier_type: str = Field("phone", description="phone, email, or username")
    reason: str | None = Field("Right to be Forgotten", description="Deletion reason")


class RetentionSettingsUpdate(BaseModel):
    pii_retention_days: int = Field(..., ge=0, le=365, description="PII retention in days (0=disabled)")


# ── Public Endpoints ─────────────────────────────────────────────────────────

@router.get("/api/public/compliance-notice")
async def get_public_compliance_notice():
    """Return platform Data Processing Notice and Privacy Notice."""
    return cs.get_compliance_notice()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@router.post("/api/compliance/opt-out")
async def public_opt_out(body: OptOutRequest, request: Request):
    """Public / prospect self-serve opt-out.

    Anyone may ask, but the platform-wide block and purge (every organization's
    matching leads lose their contact details) only happen once the person
    proves the contact is theirs: an email address gets a single-use
    confirmation link (24 h), a phone number a 6-digit SMS code (15 min). A
    username can't be proven, so it is refused here; the business that
    contacted the person removes it (POST /api/compliance/delete-request).
    The answer is the same whether or not LeadAI holds data on the contact.
    """
    ip = request_meta(request)["ip"]
    if not _opt_out_limiter.consume(ip):
        raise HTTPException(status_code=429, detail="Too many opt-out requests. Please try again later.")
    ctype = (body.contact_type or "").strip().lower()
    if ctype not in ("email", "phone"):
        raise HTTPException(status_code=422, detail=(
            "Opt out with your email address or phone number: a username can't be verified. "
            "You can also ask the business that contacted you to remove it."))
    value = (cs._normalize_email(body.contact_value) if ctype == "email"
             else cs._normalize_phone(body.contact_value))
    if not value:
        raise HTTPException(status_code=422, detail=f"Invalid {ctype} format")

    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    now = utcnow()
    secret = secrets.token_urlsafe(32) if ctype == "email" else f"{secrets.randbelow(10 ** 6):06d}"
    # a new request replaces any earlier one for the same contact
    db[OPT_OUT_REQUESTS].update_many({"type": ctype, "value": value, "confirmed_at": None},
                                     {"$set": {"superseded_at": now, "confirmed_at": False}})
    db[OPT_OUT_REQUESTS].insert_one({
        "type": ctype, "value": value, "original_input": body.contact_value.strip(),
        "reason": (body.reason or "").strip()[:500], "secret_hash": _hash(f"{value}:{secret}"),
        "attempts": 0, "expires_at": now + (_LINK_TTL if ctype == "email" else _CODE_TTL),
        "confirmed_at": None, "requested_ip": ip, "created_at": now})
    if ctype == "email":
        from app.events.email import absolute_url, send_email
        # the /opt-out page: opening it changes nothing (mail scanners open
        # links); the person confirms there with a button
        link = absolute_url(f"/opt-out?token={secret}&email={value}")
        await asyncio.to_thread(
            send_email, value, "Confirm your opt-out",
            "Someone asked us to stop processing this email address and to delete it from the leads "
            f"our customers collected from public social media comments.\n\nConfirm here (valid for 24 hours):\n{link}"
            "\n\nIf you did not ask for this, ignore this email: nothing changes.",
            kind="compliance_opt_out")
        message = "If the address is valid, we sent it a confirmation link. The opt-out applies once you open it."
    else:
        from app.messaging.sms import send_sms
        await asyncio.to_thread(send_sms, body.contact_value.strip(),
                                f"Your opt-out code: {secret}. It confirms your request to remove this "
                                "number from collected leads. Valid 15 minutes.", kind="compliance_opt_out")
        message = "If the number is valid, we sent it a 6-digit code. Confirm it to apply the opt-out."
    return {"success": True, "status": "confirmation_sent", "message": message}


def _apply_opt_out(db, req: dict, ip: str | None, method: str) -> dict:
    now = utcnow()
    claimed = db[OPT_OUT_REQUESTS].update_one({"_id": req["_id"], "confirmed_at": None},
                                              {"$set": {"confirmed_at": now, "confirmed_ip": ip}})
    if not claimed.modified_count:  # confirmed concurrently
        raise HTTPException(status_code=400, detail="This opt-out link or code is invalid or has expired.")
    return cs.add_to_blocklist(
        db, contact_type=req["type"], contact_value=req["value"],
        reason=f"Public self-service opt-out (verified by {method}): {req.get('reason') or ''}".strip(),
        organization_id=None, actor_email=f"optout:{req['type']}", ip=ip)


def _open_request(db, ctype: str, value: str | None) -> dict | None:
    if not value:
        return None
    return db[OPT_OUT_REQUESTS].find_one(
        {"type": ctype, "value": value, "confirmed_at": None, "expires_at": {"$gt": utcnow()}},
        sort=[("created_at", -1)])


@router.get("/api/compliance/opt-out/confirm")
async def confirm_email_opt_out(request: Request, token: str = Query(..., min_length=16, max_length=200),
                                email: str = Query(..., max_length=320)):
    """The emailed link: applies the opt-out and shows a short page."""
    ip = request_meta(request)["ip"]
    if not _opt_out_confirm_limiter.consume(ip):
        raise HTTPException(status_code=429, detail="Too many attempts. Please try again later.")
    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    value = cs._normalize_email(email)
    req = _open_request(db, "email", value)
    if not req or not secrets.compare_digest(req["secret_hash"], _hash(f"{value}:{token}")):
        raise HTTPException(status_code=400, detail="This opt-out link is invalid or has expired.")
    _apply_opt_out(db, req, ip, "email")
    return HTMLResponse("<!doctype html><meta charset=utf-8><title>Opt-out confirmed</title>"
                        "<body style='font-family:system-ui;max-width:560px;margin:48px auto;padding:0 16px'>"
                        f"<h1>Opt-out confirmed</h1><p>{html.escape(value or '')} is blocked from future "
                        "processing and its details were removed from collected leads.</p></body>")


@router.post("/api/compliance/opt-out/confirm")
async def confirm_phone_opt_out(body: OptOutConfirm, request: Request):
    """The SMS code: applies the opt-out. Five wrong codes end the request."""
    ip = request_meta(request)["ip"]
    if not _opt_out_confirm_limiter.consume(ip):
        raise HTTPException(status_code=429, detail="Too many attempts. Please try again later.")
    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    value = cs._normalize_phone(body.contact_value)
    req = _open_request(db, "phone", value)
    invalid = HTTPException(status_code=400, detail="This code is invalid or has expired.")
    if not req:
        raise invalid
    if not secrets.compare_digest(req["secret_hash"], _hash(f"{value}:{body.code}")):
        tries = int(req.get("attempts") or 0) + 1
        update: dict = {"attempts": tries}
        if tries >= _CODE_MAX_TRIES:
            update["confirmed_at"] = False  # used up: ask for a new code
        db[OPT_OUT_REQUESTS].update_one({"_id": req["_id"]}, {"$set": update})
        raise invalid
    res = _apply_opt_out(db, req, ip, "sms")
    return {"success": True, "message": "Opt-out confirmed: this number is blocked and removed from leads.",
            "data": res}


# ── Authenticated Organization Compliance Endpoints ─────────────────────────

@router.get("/api/compliance/blocklist")
async def get_org_blocklist(
    type: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    ctx: TenantContext = Depends(require_org_permission(P.SETTINGS_MANAGE)),  # noqa: B008
):
    """List blocked contacts for the authenticated organization."""
    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    return cs.list_blocklist(
        db,
        organization_id=ctx.organization_id,
        contact_type=type,
        page=page,
        page_size=page_size,
    )


@router.post("/api/compliance/blocklist")
async def add_org_blocklist_entry(
    body: BlocklistAddRequest,
    request: Request,
    ctx: TenantContext = Depends(require_org_permission(P.SETTINGS_MANAGE)),  # noqa: B008
):
    """Add a contact to the organization's blocklist and purge existing leads."""
    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        res = cs.add_to_blocklist(
            db,
            contact_type=body.contact_type,
            contact_value=body.contact_value,
            reason=body.reason or "Admin blocked",
            organization_id=ctx.organization_id,
            actor_email=ctx.email,
            actor_user_id=ctx.user_id,
            ip=request_meta(request)["ip"],
        )
        return {"success": True, "entry": res}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.delete("/api/compliance/blocklist/{id}")
async def remove_org_blocklist_entry(
    id: str,
    request: Request,
    ctx: TenantContext = Depends(require_org_permission(P.SETTINGS_MANAGE)),  # noqa: B008
):
    """Remove a contact from the organization's blocklist."""
    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    ok = cs.remove_from_blocklist(
        db,
        blocklist_id=id,
        organization_id=ctx.organization_id,
        actor_email=ctx.email,
        ip=request_meta(request)["ip"],
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Blocklist entry not found")
    return {"success": True, "deleted_id": id}


@router.post("/api/compliance/delete-request")
async def execute_data_deletion(
    body: DeleteProspectRequest,
    request: Request,
    ctx: TenantContext = Depends(require_org_permission(P.LEADS_MANAGE)),  # noqa: B008
):
    """Right to be Forgotten: permanently delete all data for a prospect."""
    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        res = cs.delete_prospect_data(
            db,
            organization_id=ctx.organization_id,
            identifier=body.identifier,
            identifier_type=body.identifier_type,
            reason=body.reason or "Right to be Forgotten",
            actor_email=ctx.email,
            ip=request_meta(request)["ip"],
        )
        return res
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/api/compliance/purge-pii")
async def trigger_pii_purge(
    retention_days: int | None = None,
    ctx: TenantContext = Depends(require_org_permission(P.SETTINGS_MANAGE)),  # noqa: B008
):
    """Manually trigger PII retention sweep for this organization."""
    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    res = cs.purge_expired_pii(
        db,
        organization_id=ctx.organization_id,
        retention_days=retention_days,
        actor_email=ctx.email,
    )
    return res


@router.get("/api/compliance/settings")
async def get_compliance_settings(
    ctx: TenantContext = Depends(require_org_permission(P.SETTINGS_MANAGE)),  # noqa: B008
):
    """Get the organization's PII retention configuration."""
    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    org = db["organizations"].find_one({"_id": ObjectId(str(ctx.organization_id))})
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    settings = org.get("settings") or {}
    return {
        "pii_retention_days": int(settings.get("pii_retention_days") or 0),
        "data_processing_notice": cs.get_compliance_notice(),
    }


@router.put("/api/compliance/settings")
async def update_compliance_settings(
    body: RetentionSettingsUpdate,
    ctx: TenantContext = Depends(require_org_permission(P.SETTINGS_MANAGE)),  # noqa: B008
):
    """Update the organization's PII retention window (days)."""
    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    db["organizations"].update_one(
        {"_id": ObjectId(str(ctx.organization_id))},
        {"$set": {"settings.pii_retention_days": body.pii_retention_days, "updated_at": utcnow()}},
    )
    return {
        "success": True,
        "pii_retention_days": body.pii_retention_days,
        "message": f"PII retention policy set to {body.pii_retention_days} days (0 = unlimited).",
    }
