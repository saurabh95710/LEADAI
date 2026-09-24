"""Demo request lifecycle.

  Public site  -> create_demo_request()          status "pending"
                  (user status "pending_approval", org status "pending":
                   the visitor has NO product access yet)
  Super Admin  -> approve()   "approved"  org -> "demo", tokens granted
               -> reject()    "rejected"  org -> "rejected", user -> "rejected"
               -> extend()    "extended"  more days and/or tokens
               -> cancel()    "cancelled" org -> "cancelled"
  Billing      -> mark_converted()  "converted" once a paid subscription is
                  confirmed by a Super Admin.

Each transition writes an audit entry, a notification and (where relevant)
an email with a login link — never a password.
"""
import logging
import re
from datetime import timedelta
from typing import Any, Dict, Optional

from bson import ObjectId
from fastapi import HTTPException

from app.admin.audit import audit
from app.auth.crypto import hash_password
from app.billing import tokens
from app.db.models import utcnow
from app.db.mongo import get_sync_db
from app.events.email import absolute_url, send_email
from app.events.notifications import notify_super_admins, notify_user
from app.lifecycle.config import get_demo_config

logger = logging.getLogger(__name__)

COLLECTION = "demo_requests"
STATUSES = ("pending", "approved", "rejected", "extended", "cancelled", "converted")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _db():
    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return db


def _slugify(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"[-\s]+", "-", slug) or "workspace"


def validate_password(pw: str) -> None:
    if not pw or len(pw) < 8:
        raise HTTPException(status_code=422, detail="Password must be at least 8 characters long")
    if pw.lower() == pw or not re.search(r"\d", pw):
        raise HTTPException(status_code=422,
                            detail="Password must include an uppercase letter and a number")


def create_demo_request(*, name: str, email: str, password: str, company: str,
                        phone: Optional[str] = None, message: Optional[str] = None,
                        ip: Optional[str] = None, source: str = "website") -> Dict[str, Any]:
    db = _db()
    email = (email or "").strip().lower()
    name = (name or "").strip()
    company = (company or "").strip()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="Invalid email address format")
    if not name:
        raise HTTPException(status_code=422, detail="Name is required")
    if not company:
        raise HTTPException(status_code=422, detail="Company name is required")
    validate_password(password)
    if db.users.find_one({"email": email}):
        raise HTTPException(status_code=400, detail="An account with this email already exists")

    cfg = get_demo_config()
    now = utcnow()
    base_slug = _slugify(company)
    slug, n = base_slug, 1
    while db.organizations.find_one({"slug": slug}):
        slug, n = f"{base_slug}-{n}", n + 1

    org_id = str(db.organizations.insert_one({
        "name": company, "slug": slug, "status": "pending", "plan_id": None,
        "admin_portal_enabled": False,
        "timezone": "UTC", "currency": "USD",
        "settings": {"shared_workspace": False},
        "metadata": {"source": source},
        "created_at": now, "updated_at": now, "last_activity_at": now,
    }).inserted_id)
    user_id = str(db.users.insert_one({
        "email": email, "name": name, "phone": (phone or "").strip() or None,
        "password_hash": hash_password(password), "status": "pending_approval",
        "is_platform_admin": False, "platform_role": None,
        "default_organization_id": org_id,
        "created_at": now, "updated_at": now, "last_login": None,
    }).inserted_id)
    db.organizations.update_one({"_id": ObjectId(org_id)}, {"$set": {"owner_id": user_id}})
    db.organization_members.insert_one({
        "organization_id": org_id, "user_id": user_id, "role": "owner",
        "status": "active", "joined_at": now, "invited_by": None,
        "last_activity_at": now, "permissions_override": {},
        "created_at": now, "updated_at": now,
    })
    req = {
        "organization_id": org_id, "user_id": user_id, "name": name, "email": email,
        "phone": (phone or "").strip() or None, "company": company,
        "message": (message or "").strip()[:2000] or None,
        "status": "pending", "source": source, "ip": ip,
        "requested_config": cfg,
        "history": [{"status": "pending", "at": now, "by": email}],
        "created_at": now, "updated_at": now,
    }
    req_id = str(db[COLLECTION].insert_one(req).inserted_id)
    db.organizations.update_one({"_id": ObjectId(org_id)},
                                {"$set": {"metadata.demo_request_id": req_id}})

    who = {"user_id": user_id, "email": email, "role": "owner", "organization_id": org_id}
    audit("account.registered", "lifecycle", user=who, ip=ip, organization_id=org_id,
          resource_type="user", resource_id=user_id, details={"company": company})
    audit("demo.requested", "lifecycle", user=who, ip=ip, organization_id=org_id,
          resource_type="demo_request", resource_id=req_id)
    notify_super_admins("registration", "New registration", f"{name} <{email}> from {company}",
                        link="/superadmin#demos", data={"demo_request_id": req_id})
    notify_super_admins("demo_requested", "New demo request", f"{company} — {name} <{email}>",
                        severity="warning", link="/superadmin#demos",
                        data={"demo_request_id": req_id})
    send_email(email, "We received your LeadAI demo request",
               f"Hi {name},\n\nThanks for requesting a LeadAI demo for {company}. "
               "Our team will review it shortly — you'll get another email as soon as "
               "your demo is approved.\n\n— The LeadAI team", kind="demo_received",
               organization_id=org_id)
    if cfg.get("auto_approve"):
        approve(req_id, actor="system:auto_approve")
    return {"id": req_id, "status": get_request(req_id)["status"], "organization_id": org_id}


def _load(req_id: str) -> Dict[str, Any]:
    db = _db()
    try:
        doc = db[COLLECTION].find_one({"_id": ObjectId(req_id)})
    except Exception:
        doc = None
    if not doc:
        raise HTTPException(status_code=404, detail="Demo request not found")
    return doc


def get_request(req_id: str) -> Dict[str, Any]:
    return serialize(_load(req_id))


def serialize(doc: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: v for k, v in doc.items() if k not in ("_id", "ip")}
    out["id"] = str(doc["_id"])
    for k, v in list(out.items()):
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
    out["history"] = [{**h, "at": h["at"].isoformat() if hasattr(h.get("at"), "isoformat") else h.get("at")}
                      for h in doc.get("history", [])]
    return out


def _transition(doc, status: str, actor: str, extra: Optional[Dict[str, Any]] = None,
                note: Optional[str] = None) -> None:
    db = _db()
    now = utcnow()
    update = {"status": status, "updated_at": now, **(extra or {})}
    db[COLLECTION].update_one({"_id": doc["_id"]}, {
        "$set": update,
        "$push": {"history": {"status": status, "at": now, "by": actor, "note": note}},
    })


def _require(doc, allowed) -> None:
    if doc.get("status") not in allowed:
        raise HTTPException(status_code=409,
                            detail=f"Demo request is '{doc.get('status')}' — action not allowed")


def approve(req_id: str, *, actor: str, overrides: Optional[Dict[str, Any]] = None,
            ip: Optional[str] = None) -> Dict[str, Any]:
    db = _db()
    doc = _load(req_id)
    _require(doc, ("pending",))
    cfg = get_demo_config()
    if overrides:
        from app.lifecycle.config import validate_demo_config
        cfg.update(validate_demo_config(overrides))
    now = utcnow()
    expires = now + timedelta(days=int(cfg["duration_days"]))
    org_id, user_id = doc["organization_id"], doc["user_id"]
    db.organizations.update_one({"_id": ObjectId(org_id)}, {"$set": {
        "status": "demo", "demo": {"started_at": now, "expires_at": expires, "config": cfg},
        "updated_at": now}})
    db.users.update_one({"_id": ObjectId(user_id)}, {"$set": {"status": "active", "updated_at": now}})
    tokens.allocate(org_id, int(cfg["tokens"]), source="demo", actor=actor,
                    reason="Demo approved", expires_at=expires, reset=True)
    _transition(doc, "approved", actor, {"approved_at": now, "approved_by": actor,
                                         "demo_expires_at": expires, "granted_config": cfg})
    audit("demo.approved", "lifecycle", user=actor, ip=ip, organization_id=org_id,
          resource_type="demo_request", resource_id=req_id,
          details={"tokens": cfg["tokens"], "expires_at": expires.isoformat()})
    notify_super_admins("demo_approved", "Demo approved",
                        f"{doc['company']} ({doc['email']}) by {actor}", severity="success",
                        link="/superadmin#demos", data={"demo_request_id": req_id})
    notify_user(user_id, "demo_approved", "Your demo is ready",
                f"You have {cfg['tokens']} tokens for {cfg['duration_days']} days.",
                organization_id=org_id, severity="success", link="/dashboard")
    send_email(doc["email"], "Your LeadAI demo is approved",
               f"Hi {doc['name']},\n\nYour LeadAI demo for {doc['company']} is approved. "
               f"You have {cfg['tokens']} tokens for {cfg['duration_days']} days.\n\n"
               f"Sign in here: {absolute_url('/login')}\n\n— The LeadAI team",
               kind="demo_approved", organization_id=org_id)
    return get_request(req_id)


def reject(req_id: str, *, actor: str, reason: str = "", ip: Optional[str] = None) -> Dict[str, Any]:
    db = _db()
    doc = _load(req_id)
    _require(doc, ("pending",))
    now = utcnow()
    db.organizations.update_one({"_id": ObjectId(doc["organization_id"])},
                                {"$set": {"status": "rejected", "updated_at": now}})
    db.users.update_one({"_id": ObjectId(doc["user_id"])},
                        {"$set": {"status": "rejected", "updated_at": now}})
    _transition(doc, "rejected", actor, {"rejected_at": now, "rejection_reason": reason}, reason)
    audit("demo.rejected", "lifecycle", user=actor, ip=ip,
          organization_id=doc["organization_id"], resource_type="demo_request",
          resource_id=req_id, details={"reason": reason})
    send_email(doc["email"], "Your LeadAI demo request",
               f"Hi {doc['name']},\n\nThank you for your interest in LeadAI. We are unable "
               f"to approve your demo request at this time."
               + (f"\n\nReason: {reason}" if reason else "") + "\n\n— The LeadAI team",
               kind="demo_rejected", organization_id=doc["organization_id"])
    return get_request(req_id)


def extend(req_id: str, *, actor: str, days: int = 0, extra_tokens: int = 0,
           ip: Optional[str] = None) -> Dict[str, Any]:
    db = _db()
    doc = _load(req_id)
    _require(doc, ("approved", "extended"))
    days, extra_tokens = int(days or 0), int(extra_tokens or 0)
    if days <= 0 and extra_tokens <= 0:
        raise HTTPException(status_code=422, detail="Provide extra days and/or tokens")
    org = db.organizations.find_one({"_id": ObjectId(doc["organization_id"])}) or {}
    demo = org.get("demo") or {}
    now = utcnow()
    current = demo.get("expires_at") or now
    if current.tzinfo is None:
        from datetime import timezone
        current = current.replace(tzinfo=timezone.utc)
    new_exp = max(current, now) + timedelta(days=days)
    db.organizations.update_one({"_id": org["_id"]}, {"$set": {
        "demo.expires_at": new_exp, "status": "demo", "updated_at": now}})
    if extra_tokens > 0:
        tokens.allocate(doc["organization_id"], extra_tokens, source="demo", actor=actor,
                        reason="Demo extended", expires_at=new_exp)
    elif days > 0:
        db[tokens.BALANCES].update_one({"organization_id": doc["organization_id"]},
                                       {"$set": {"expires_at": new_exp}})
    _transition(doc, "extended", actor, {"demo_expires_at": new_exp},
                f"+{days} days, +{extra_tokens} tokens")
    audit("demo.extended", "lifecycle", user=actor, ip=ip,
          organization_id=doc["organization_id"], resource_type="demo_request",
          resource_id=req_id, details={"days": days, "tokens": extra_tokens,
                                       "expires_at": new_exp.isoformat()})
    notify_user(doc["user_id"], "demo_extended", "Your demo was extended",
                f"New expiry: {new_exp.date().isoformat()}"
                + (f", +{extra_tokens} tokens" if extra_tokens else ""),
                organization_id=doc["organization_id"], severity="success")
    return get_request(req_id)


def cancel(req_id: str, *, actor: str, reason: str = "", ip: Optional[str] = None) -> Dict[str, Any]:
    db = _db()
    doc = _load(req_id)
    _require(doc, ("pending", "approved", "extended"))
    now = utcnow()
    db.organizations.update_one({"_id": ObjectId(doc["organization_id"])},
                                {"$set": {"status": "cancelled", "updated_at": now}})
    _transition(doc, "cancelled", actor, {"cancelled_at": now}, reason)
    audit("demo.cancelled", "lifecycle", user=actor, ip=ip,
          organization_id=doc["organization_id"], resource_type="demo_request",
          resource_id=req_id, details={"reason": reason})
    return get_request(req_id)


def mark_converted(organization_id: str, *, actor: str, subscription_id: str) -> None:
    """Called when a paid subscription for this org is confirmed."""
    db = _db()
    doc = db[COLLECTION].find_one({"organization_id": str(organization_id),
                                   "status": {"$in": ["pending", "approved", "extended"]}})
    if not doc:
        return
    _transition(doc, "converted", actor, {"converted_at": utcnow(),
                                          "subscription_id": subscription_id})
    audit("demo.converted", "lifecycle", user=actor, organization_id=str(organization_id),
          resource_type="demo_request", resource_id=str(doc["_id"]),
          details={"subscription_id": subscription_id})


def demo_status(organization_id: str) -> Optional[Dict[str, Any]]:
    """Demo summary for the User Portal (tokens, expiry countdown)."""
    db = _db()
    try:
        org = db.organizations.find_one({"_id": ObjectId(str(organization_id))})
    except Exception:
        org = None
    if not org or org.get("status") != "demo":
        return None
    demo = org.get("demo") or {}
    exp = demo.get("expires_at")
    from datetime import timezone
    if exp is not None and exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    now = utcnow()
    seconds_left = max(0, int((exp - now).total_seconds())) if exp else 0
    return {
        "is_demo": True,
        "expires_at": exp.isoformat() if exp else None,
        "seconds_remaining": seconds_left,
        "days_remaining": seconds_left // 86400,
        "expired": bool(exp and exp <= now),
        "config": demo.get("config") or {},
        "tokens": tokens.get_balance(str(organization_id)),
    }
