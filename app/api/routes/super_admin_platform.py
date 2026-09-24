"""
Super Admin — platform control-center API (/api/super-admin/*).

Complements super_admin.py (dashboard, organizations, users, plans, audit,
impersonation) and super_admin_lifecycle.py (demo, subscriptions queue,
payments, tokens, notifications, security events, role matrix, outbox).
Nothing here duplicates a path of those two routers.

  Search & people
    GET  /search                                   global search (orgs, users, runs, demos)
    GET  /admins                                   org owners/admins across all orgs
    POST /users/{user_id}/reset-access             email a one-time reset link (never a password)
    POST /users/{user_id}/revoke-sessions
    PATCH /organizations/{org_id}                  edit profile fields (audited before/after)
  Subscriptions
    GET  /subscriptions/{sub_id}/detail            history + payments + payment events
    POST /subscriptions/{sub_id}/change-plan       upgrade / downgrade (active subs only)
    POST /subscriptions/{sub_id}/extend            extend the current period
  Tokens
    GET  /tokens/summary                           totals, >=80% warnings, 24h anomalies
    GET  /tokens/{org_id}/users                    per-user consumption
    POST /tokens/{org_id}/expiry                   set / clear balance expiry
  LeadAI operations
    GET  /leadai/summary                           searches / jobs / pages / posts / comments / leads
    GET  /searches                                 global search runs (filters, paging)
    GET  /searches/{run_id}/chain                  Org -> Admin -> User -> Search -> Apify job(s) -> counts -> error
    GET  /leads                                    global leads (contact data masked)
  Analytics
    GET  /analytics                                business + product series for a date range
  Security
    GET  /security/overview | /security/logins | /security/lockouts
    POST /security/lockouts/clear
    GET  /sessions                                 active sessions (session secrets never returned)
    POST /sessions/{session_ref}/revoke
  Platform
    GET|PUT /feature-flags                         flags, platform toggles, maintenance, notification config
    GET  /integrations                             configured? booleans only — never secret values
    GET  /health                                   system health (API, DB, Apify, AI, payments, jobs, 5xx)
  AI management
    GET  /ai/overview | /ai/prompts | /ai/models
    POST /ai/prompts | /ai/prompts/{id}/activate | /ai/prompts/{id}/rollback
    PATCH /ai/models/{id}
  Reports
    GET  /reports                                  catalog + recent exports
    GET  /reports/{kind}.csv                       redacted CSV, every export audited
  Support
    GET  /support/tickets | /support/tickets/{id}  staff inbox for org-admin tickets
    POST /support/tickets/{id}/messages            staff reply (from_staff: true) -> notifies org admins
    POST /support/tickets/{id}/status              open | waiting | resolved | closed -> notifies org admins

Every endpoint requires the ``super_admin`` platform role (the auth gate also
blocks every other role from /api/super-admin/*). Every mutation writes an
audit event. No endpoint returns passwords, hashes, tokens, API keys or
session identifiers.
"""
import csv
import io
import json
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel

from app.admin.audit import aaudit, redact, request_meta
from app.auth.tenant import TenantContext, require_platform_role
from app.db.models import utcnow
from app.db.mongo import get_async_db

router = APIRouter(prefix="/api/super-admin", tags=["super-admin-platform"])
SUPER = require_platform_role("super_admin")

MAX_EXPORT_ROWS = 50_000
_PLATFORMS = ("facebook", "instagram", "linkedin", "youtube")


# ── helpers ────────────────────────────────────────────────────────────────

def _db():
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return db


def _clean(d: Any) -> Any:
    if isinstance(d, ObjectId):
        return str(d)
    if isinstance(d, dict):
        return {("id" if k == "_id" else k): _clean(v) for k, v in d.items()
                if k not in ("password_hash", "token_hash", "session_id")}
    if isinstance(d, list):
        return [_clean(v) for v in d]
    if isinstance(d, datetime):
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.isoformat()
    return d


def _oid(value: Any) -> Optional[ObjectId]:
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _rx(q: Optional[str]) -> Optional[Dict[str, Any]]:
    q = (q or "").strip()
    if not q:
        return None
    return {"$regex": re.escape(q[:100]), "$options": "i"}


async def _count(coll, query: Dict[str, Any]) -> int:
    try:
        return int(await coll.count_documents(query))
    except Exception:
        return 0


async def _agg(coll, pipeline: List[Dict[str, Any]], limit: int = 5000) -> List[Dict[str, Any]]:
    try:
        return [d async for d in coll.aggregate(pipeline)][:limit]
    except Exception:
        return []


def _sort(sort: str, allowed: Iterable[str], default: str) -> Tuple[str, int]:
    field = (sort or default).lstrip("-")
    if field not in allowed:
        field, sort = default.lstrip("-"), default
    return field, (-1 if (sort or default).startswith("-") else 1)


def _pages(total: int, limit: int) -> int:
    return max(1, -(-total // limit))


def _as_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str) and value:
        try:
            v = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _parse_day(value: Optional[str], field: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{field} must be YYYY-MM-DD")


def _date_range(range_: str, from_: Optional[str], to: Optional[str]) -> Tuple[datetime, datetime]:
    now = utcnow()
    start, end = _parse_day(from_, "from"), _parse_day(to, "to")
    if start or end:
        end = (end + timedelta(days=1)) if end else now
        start = start or (end - timedelta(days=30))
        if start >= end:
            raise HTTPException(status_code=422, detail="'from' must be before 'to'")
        if (end - start).days > 731:
            raise HTTPException(status_code=422, detail="Date range is limited to two years")
        return start, end
    days = {"24h": 1, "7d": 7, "30d": 30, "90d": 90, "365d": 365}.get(range_ or "30d")
    if days is None:
        raise HTTPException(status_code=422, detail="range must be 24h, 7d, 30d, 90d or 365d")
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return today - timedelta(days=days - 1), now


def mask_email(value: Any) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    if "@" not in s:
        return s[:1] + "•••"
    local, _, domain = s.partition("@")
    return (local[:2] if len(local) > 2 else local[:1]) + "•••@" + domain


def mask_phone(value: Any) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    digits = re.sub(r"\D", "", s)
    if len(digits) <= 4:
        return "•" * len(digits)
    return "•" * (len(digits) - 4) + digits[-4:]


async def _org_names(db, ids: Iterable[Any]) -> Dict[str, str]:
    oids = [o for o in (_oid(i) for i in {str(x) for x in ids if x}) if o is not None]
    if not oids:
        return {}
    return {str(o["_id"]): o.get("name", "") async for o in
            db.organizations.find({"_id": {"$in": oids}}, {"name": 1})}


async def _user_map(db, ids: Iterable[Any]) -> Dict[str, Dict[str, Any]]:
    oids = [o for o in (_oid(i) for i in {str(x) for x in ids if x}) if o is not None]
    if not oids:
        return {}
    return {str(u["_id"]): {"email": u.get("email"), "name": u.get("name"),
                            "status": u.get("status", "active"), "last_login": u.get("last_login")}
            async for u in db.users.find({"_id": {"$in": oids}},
                                         {"email": 1, "name": 1, "status": 1, "last_login": 1})}


def _meta(request: Request) -> Dict[str, Any]:
    return request_meta(request)


class ReasonBody(BaseModel):
    reason: str = ""


# ── Global search ──────────────────────────────────────────────────────────

@router.get("/search")
async def global_search(q: str = Query("", max_length=100), ctx: TenantContext = Depends(SUPER)):
    db = _db()
    rx = _rx(q)
    if not rx or len(q.strip()) < 2:
        return {"success": True, "q": q, "groups": {}}
    groups: Dict[str, List[Dict[str, Any]]] = {}
    groups["organizations"] = [
        {"id": str(o["_id"]), "label": o.get("name"), "sub": o.get("slug"), "status": o.get("status")}
        async for o in db.organizations.find({"$or": [{"name": rx}, {"slug": rx}]},
                                             {"name": 1, "slug": 1, "status": 1}).limit(6)]
    groups["users"] = [
        {"id": str(u["_id"]), "label": u.get("email"), "sub": u.get("name"), "status": u.get("status", "active")}
        async for u in db.users.find({"$or": [{"email": rx}, {"name": rx}]},
                                     {"email": 1, "name": 1, "status": 1}).limit(6)]
    groups["searches"] = [
        {"id": s.get("run_id"), "label": s.get("query"), "sub": s.get("run_id"), "status": s.get("status")}
        async for s in db.search_history.find({"$or": [{"run_id": rx}, {"query": rx}]},
                                              {"run_id": 1, "query": 1, "status": 1}).limit(6)]
    groups["demo_requests"] = [
        {"id": str(d["_id"]), "label": d.get("company"), "sub": d.get("email"), "status": d.get("status")}
        async for d in db.demo_requests.find({"$or": [{"company": rx}, {"email": rx}, {"name": rx}]},
                                             {"company": 1, "email": 1, "status": 1}).limit(6)]
    return {"success": True, "q": q, "groups": {k: v for k, v in groups.items() if v}}


# ── Admins (organization owners / admins across all orgs) ──────────────────

@router.get("/admins")
async def list_admins(q: Optional[str] = None, organization_id: Optional[str] = None,
                      role: Optional[str] = None, status: Optional[str] = None,
                      page: int = Query(1, ge=1), limit: int = Query(25, ge=1, le=200),
                      sort: str = "-joined_at", ctx: TenantContext = Depends(SUPER)):
    db = _db()
    roles = [role] if role in ("owner", "admin") else ["owner", "admin"]
    query: Dict[str, Any] = {"role": {"$in": roles}, "status": {"$ne": "removed"}}
    if organization_id:
        query["organization_id"] = organization_id
    rx = _rx(q)
    if rx:
        uids = [str(u["_id"]) async for u in db.users.find({"$or": [{"email": rx}, {"name": rx}]}, {"_id": 1}).limit(500)]
        oids = [str(o["_id"]) async for o in db.organizations.find({"name": rx}, {"_id": 1}).limit(500)]
        query["$or"] = [{"user_id": {"$in": uids}}, {"organization_id": {"$in": oids}}]
    if status in ("active", "suspended", "disabled"):
        uids = [str(u["_id"]) async for u in db.users.find(
            {"status": status} if status != "active" else {"status": {"$in": ["active", None]}}, {"_id": 1})]
        query["user_id"] = {"$in": uids}
    field, direction = _sort(sort, ("joined_at", "created_at", "role"), "-joined_at")
    total = await _count(db.organization_members, query)
    rows = [m async for m in db.organization_members.find(query).sort(field, direction)
            .skip((page - 1) * limit).limit(limit)]
    users = await _user_map(db, [m.get("user_id") for m in rows])
    orgs: Dict[str, Dict[str, Any]] = {}
    oids = [o for o in (_oid(m.get("organization_id")) for m in rows) if o is not None]
    async for o in db.organizations.find({"_id": {"$in": oids}}, {"name": 1, "status": 1, "plan_id": 1}):
        orgs[str(o["_id"])] = o
    items = []
    for m in rows:
        u = users.get(str(m.get("user_id")), {})
        o = orgs.get(str(m.get("organization_id")), {})
        sub = await db.subscriptions.find_one({"organization_id": str(m.get("organization_id"))},
                                              {"status": 1, "plan_id": 1}, sort=[("created_at", -1)])
        items.append({
            "user_id": m.get("user_id"), "email": u.get("email") or m.get("email"),
            "name": u.get("name"), "user_status": u.get("status", "unknown"),
            "last_login": _clean(u.get("last_login")), "role": m.get("role"),
            "member_status": m.get("status"), "joined_at": _clean(m.get("joined_at") or m.get("created_at")),
            "organization_id": m.get("organization_id"), "organization_name": o.get("name"),
            "organization_status": o.get("status"),
            "subscription_status": (sub or {}).get("status"), "plan_id": (sub or {}).get("plan_id") or o.get("plan_id"),
        })
    return {"success": True, "items": items, "total": total, "page": page, "limit": limit,
            "pages": _pages(total, limit)}


# ── Users: reset access / revoke sessions ──────────────────────────────────

class ResetAccessBody(BaseModel):
    reason: str = ""
    revoke_sessions: bool = True


@router.post("/users/{user_id}/reset-access")
async def reset_user_access(user_id: str, body: ResetAccessBody, request: Request,
                            ctx: TenantContext = Depends(SUPER)):
    """Email the user a one-time, expiring password-reset link (the same
    ``password_resets`` mechanism as /api/auth/password/forgot). A password
    is never generated, shown or emailed."""
    import hashlib
    from app.api.routes.auth import _RESET_TOKEN_TTL_MIN
    from app.events.email import absolute_url, send_email
    db = _db()
    oid = _oid(user_id)
    user = await db.users.find_one({"_id": oid}) if oid else None
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.get("status") in ("archived", "deleted"):
        raise HTTPException(status_code=409, detail="This account is archived")
    if not user.get("email"):
        raise HTTPException(status_code=422, detail="User has no email address")
    now = utcnow()
    token = secrets.token_urlsafe(32)
    await db.password_resets.update_many({"user_id": str(user["_id"]), "used_at": None},
                                         {"$set": {"used_at": now, "invalidated": True}})
    await db.password_resets.insert_one({
        "user_id": str(user["_id"]), "token_hash": hashlib.sha256(token.encode()).hexdigest(),
        "expires_at": now + timedelta(minutes=_RESET_TOKEN_TTL_MIN), "used_at": None,
        "purpose": "admin_reset", "requested_by": ctx.email, "created_at": now})
    send_email(user["email"], "Reset your LeadAI password",
               f"Hi {user.get('name') or ''},\n\nOur support team issued a password reset for your "
               f"account. Use this link to choose a new password (valid for {_RESET_TOKEN_TTL_MIN} "
               f"minutes, single use):\n{absolute_url('/reset-password?token=' + token)}\n\n"
               "If you did not expect this, contact support.", kind="password_reset")
    revoked = 0
    if body.revoke_sessions:
        from app.auth.service import revoke_user_sessions
        revoked = revoke_user_sessions(str(user["_id"]), revoked_by=f"super_admin:{ctx.email}")
    await aaudit("user.access_reset", "security", user=ctx.audit_user(), resource_type="user",
                 resource_id=str(user["_id"]),
                 details={"email": user["email"], "reason": body.reason[:300],
                          "sessions_revoked": revoked}, **_meta(request))
    return {"success": True, "message": f"A one-time reset link was emailed to {user['email']}.",
            "sessions_revoked": revoked}


@router.post("/users/{user_id}/revoke-sessions")
async def revoke_user_sessions_route(user_id: str, body: ReasonBody, request: Request,
                                     ctx: TenantContext = Depends(SUPER)):
    from app.auth.service import revoke_user_sessions
    db = _db()
    oid = _oid(user_id)
    user = await db.users.find_one({"_id": oid}, {"email": 1}) if oid else None
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    n = revoke_user_sessions(user_id, revoked_by=f"super_admin:{ctx.email}")
    await aaudit("user.sessions_revoked", "security", user=ctx.audit_user(), resource_type="user",
                 resource_id=user_id, details={"revoked": n, "reason": body.reason[:300]},
                 **_meta(request))
    return {"success": True, "revoked": n}


# ── Organizations: edit profile ────────────────────────────────────────────

class OrgEditBody(BaseModel):
    name: Optional[str] = None
    timezone: Optional[str] = None
    currency: Optional[str] = None
    notes: Optional[str] = None
    admin_portal_enabled: Optional[bool] = None


@router.patch("/organizations/{org_id}")
async def edit_organization(org_id: str, body: OrgEditBody, request: Request,
                            ctx: TenantContext = Depends(SUPER)):
    db = _db()
    oid = _oid(org_id)
    org = await db.organizations.find_one({"_id": oid}) if oid else None
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    updates: Dict[str, Any] = {}
    if body.name is not None:
        name = body.name.strip()
        if not name or len(name) > 120:
            raise HTTPException(status_code=422, detail="Name must be 1-120 characters")
        updates["name"] = name
    if body.timezone is not None:
        updates["timezone"] = body.timezone.strip()[:64] or "UTC"
    if body.currency is not None:
        cur = body.currency.strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", cur):
            raise HTTPException(status_code=422, detail="Currency must be a 3-letter code")
        updates["currency"] = cur
    if body.notes is not None:
        updates["admin_notes"] = body.notes.strip()[:2000]
    if body.admin_portal_enabled is not None:
        updates["admin_portal_enabled"] = bool(body.admin_portal_enabled)
    if not updates:
        return {"success": True, "organization": _clean(org)}
    before = {k: org.get(k) for k in updates}
    updates["updated_at"] = utcnow()
    await db.organizations.update_one({"_id": oid}, {"$set": updates})
    await aaudit("organization.updated", "organizations", user=ctx.audit_user(),
                 organization_id=org_id, resource_type="organization", resource_id=org_id,
                 details={"before": _clean(before),
                          "after": _clean({k: v for k, v in updates.items() if k != "updated_at"})},
                 **_meta(request))
    return {"success": True, "organization": _clean(await db.organizations.find_one({"_id": oid}))}


# ── Subscriptions: detail / change plan / extend ───────────────────────────

async def _sub_or_404(db, sub_id: str) -> Dict[str, Any]:
    oid = _oid(sub_id)
    sub = await db.subscriptions.find_one({"_id": oid}) if oid else None
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return sub


@router.get("/subscriptions/{sub_id}/detail")
async def subscription_detail(sub_id: str, ctx: TenantContext = Depends(SUPER)):
    db = _db()
    sub = await _sub_or_404(db, sub_id)
    sid = str(sub["_id"])
    payments = [_clean(p) async for p in db.payments.find({"subscription_id": sid}).sort("created_at", -1).limit(50)]
    for p in payments:
        p.pop("provider_payment_id", None)
    events = [_clean(e) async for e in db.payment_events.find({"subscription_id": sid}).sort("created_at", 1).limit(200)]
    org = await db.organizations.find_one({"_id": _oid(sub.get("organization_id"))}, {"name": 1, "status": 1}) \
        if _oid(sub.get("organization_id")) else None
    others = [_clean(s) async for s in db.subscriptions.find(
        {"organization_id": sub.get("organization_id"), "_id": {"$ne": sub["_id"]}},
        {"plan_id": 1, "status": 1, "created_at": 1, "amount": 1, "currency": 1}).sort("created_at", -1).limit(20)]
    out = _clean(sub)
    out.pop("checkout_session_id", None)
    return {"success": True, "subscription": out, "organization": _clean(org), "payments": payments,
            "events": events, "other_subscriptions": others}


class ChangePlanBody(BaseModel):
    plan: str
    billing_cycle: str = "monthly"
    reason: str = ""


@router.post("/subscriptions/{sub_id}/change-plan")
async def change_plan(sub_id: str, body: ChangePlanBody, request: Request,
                      ctx: TenantContext = Depends(SUPER)):
    """Upgrade / downgrade a LIVE subscription. Pending subscriptions must go
    through the confirmation queue instead (never activated from here)."""
    from app.billing.plans import get_plan_by_slug_or_id
    from app.billing.subscriptions import change_subscription_plan
    db = _db()
    sub = await _sub_or_404(db, sub_id)
    if sub.get("status") not in ("active", "trialing"):
        raise HTTPException(status_code=409, detail="Only an active subscription can change plan. "
                                                    "Confirm or re-activate it first.")
    latest = await db.subscriptions.find_one({"organization_id": sub.get("organization_id")},
                                             sort=[("created_at", -1)])
    if latest and latest["_id"] != sub["_id"]:
        raise HTTPException(status_code=409, detail="This organization has a newer subscription; "
                                                    "change the plan there.")
    if body.billing_cycle not in ("monthly", "yearly"):
        raise HTTPException(status_code=422, detail="billing_cycle must be monthly or yearly")
    plan = await get_plan_by_slug_or_id(body.plan, db=db)
    if not plan or plan.get("status") == "archived":
        raise HTTPException(status_code=404, detail="Plan not found")
    before = {"plan_id": sub.get("plan_id"), "billing_cycle": sub.get("billing_cycle"),
              "amount": sub.get("amount")}
    await change_subscription_plan(sub["organization_id"], plan["slug"], body.billing_cycle, db=db)
    now = utcnow()
    await db.subscriptions.update_one({"_id": sub["_id"]}, {"$push": {"status_history": {
        "from": sub.get("status"), "to": "active", "at": now, "by": ctx.email,
        "note": f"plan {before['plan_id']} -> {plan['slug']} ({body.billing_cycle}) {body.reason}".strip()}}})
    await aaudit("subscription.plan_changed", "billing", user=ctx.audit_user(),
                 organization_id=sub.get("organization_id"), resource_type="subscription",
                 resource_id=str(sub["_id"]),
                 details={"before": before, "after": {"plan_id": plan["slug"],
                                                      "billing_cycle": body.billing_cycle},
                          "reason": body.reason[:300]}, **_meta(request))
    return {"success": True, "subscription": _clean(await db.subscriptions.find_one({"_id": sub["_id"]}))}


class ExtendBody(BaseModel):
    days: int
    reason: str = ""


@router.post("/subscriptions/{sub_id}/extend")
async def extend_subscription(sub_id: str, body: ExtendBody, request: Request,
                              ctx: TenantContext = Depends(SUPER)):
    """Push the current period end (and trial end) out by N days. Never
    changes the status."""
    db = _db()
    if not 1 <= body.days <= 365:
        raise HTTPException(status_code=422, detail="days must be between 1 and 365")
    if not body.reason.strip():
        raise HTTPException(status_code=422, detail="A reason is required")
    sub = await _sub_or_404(db, sub_id)
    if sub.get("status") not in ("active", "trialing", "suspended", "past_due"):
        raise HTTPException(status_code=409, detail="Only a live subscription can be extended")
    now = utcnow()
    base = _as_dt(sub.get("current_period_end")) or now
    new_end = max(base, now) + timedelta(days=body.days)
    updates: Dict[str, Any] = {"current_period_end": new_end, "updated_at": now}
    if sub.get("status") == "trialing" or sub.get("trial_end"):
        t = _as_dt(sub.get("trial_end")) or now
        updates["trial_end"] = max(t, now) + timedelta(days=body.days)
    await db.subscriptions.update_one({"_id": sub["_id"]}, {"$set": updates, "$push": {"status_history": {
        "from": sub.get("status"), "to": sub.get("status"), "at": now, "by": ctx.email,
        "note": f"extended {body.days}d: {body.reason.strip()[:200]}"}}})
    await aaudit("subscription.extended", "billing", user=ctx.audit_user(),
                 organization_id=sub.get("organization_id"), resource_type="subscription",
                 resource_id=str(sub["_id"]),
                 details={"days": body.days, "before": _clean(sub.get("current_period_end")),
                          "after": new_end.isoformat(), "reason": body.reason[:300]}, **_meta(request))
    return {"success": True, "subscription": _clean(await db.subscriptions.find_one({"_id": sub["_id"]}))}


# ── Tokens: summary / per-user / expiry ────────────────────────────────────

@router.get("/tokens/summary")
async def tokens_summary(ctx: TenantContext = Depends(SUPER)):
    db = _db()
    totals = {"allocated": 0, "used": 0, "remaining": 0, "organizations": 0}
    warnings, expired = [], []
    now = utcnow()
    async for b in db.token_balances.find({}):
        alloc, used = int(b.get("allocated") or 0), int(b.get("used") or 0)
        totals["allocated"] += alloc
        totals["used"] += used
        totals["remaining"] += int(b.get("remaining") or 0)
        totals["organizations"] += 1
        pct = round(used * 100 / alloc, 1) if alloc else (100.0 if used else 0.0)
        exp = _as_dt(b.get("expires_at"))
        row = {"organization_id": b.get("organization_id"), "allocated": alloc, "used": used,
               "remaining": int(b.get("remaining") or 0), "percentage": pct,
               "expires_at": _clean(exp)}
        if exp and exp < now:
            expired.append(row)
        elif pct >= 80:
            warnings.append(row)
    since24 = now - timedelta(hours=24)
    since30 = now - timedelta(days=31)
    recent = {r["_id"]: r["n"] for r in await _agg(db.token_ledger, [
        {"$match": {"type": "consume", "created_at": {"$gte": since24}}},
        {"$group": {"_id": "$organization_id", "n": {"$sum": "$amount"}}}])}
    history = {r["_id"]: r["n"] for r in await _agg(db.token_ledger, [
        {"$match": {"type": "consume", "created_at": {"$gte": since30, "$lt": since24}}},
        {"$group": {"_id": "$organization_id", "n": {"$sum": "$amount"}}}])}
    anomalies = []
    for org_id, n24 in recent.items():
        daily_avg = (history.get(org_id, 0) or 0) / 30.0
        if n24 > 3 * daily_avg and n24 >= 10:
            anomalies.append({"organization_id": org_id, "last_24h": n24,
                              "daily_average": round(daily_avg, 1),
                              "factor": round(n24 / daily_avg, 1) if daily_avg else None})
    names = await _org_names(db, [r["organization_id"] for r in warnings + expired + anomalies])
    for r in warnings + expired + anomalies:
        r["organization_name"] = names.get(str(r["organization_id"]))
    consumed_24h = sum(recent.values())
    return {"success": True, "totals": totals, "consumed_24h": consumed_24h,
            "warnings": sorted(warnings, key=lambda r: -r["percentage"])[:100],
            "expired": expired[:100],
            "anomalies": sorted(anomalies, key=lambda r: -r["last_24h"])[:100],
            "over_limit_behaviour": (
                "When an organization's balance cannot cover an action, the action is refused "
                "with HTTP 402 (TOKENS_EXHAUSTED) before any work starts — nothing is charged "
                "partially and existing data is untouched. Expired balances behave the same. "
                "Owners/admins are notified at 80% and 100% usage.")}


@router.get("/tokens/{org_id}/users")
async def tokens_by_user(org_id: str, range: str = "30d", from_: Optional[str] = Query(None, alias="from"),
                         to: Optional[str] = None, ctx: TenantContext = Depends(SUPER)):
    db = _db()
    start, end = _date_range(range, from_, to)
    rows = await _agg(db.token_ledger, [
        {"$match": {"organization_id": org_id, "type": "consume",
                    "created_at": {"$gte": start, "$lt": end}}},
        {"$group": {"_id": "$user_id", "tokens": {"$sum": "$amount"}, "actions": {"$sum": 1}}}])
    users = await _user_map(db, [r["_id"] for r in rows])
    items = [{"user_id": r["_id"], "email": users.get(str(r["_id"]), {}).get("email"),
              "name": users.get(str(r["_id"]), {}).get("name"),
              "tokens": r["tokens"], "actions": r["actions"]} for r in rows]
    items.sort(key=lambda r: -r["tokens"])
    return {"success": True, "items": items, "from": start.isoformat(), "to": end.isoformat()}


class ExpiryBody(BaseModel):
    expires_at: Optional[str] = None   # YYYY-MM-DD, or null to clear
    reason: str


@router.post("/tokens/{org_id}/expiry")
async def token_expiry(org_id: str, body: ExpiryBody, request: Request,
                       ctx: TenantContext = Depends(SUPER)):
    db = _db()
    if not body.reason.strip():
        raise HTTPException(status_code=422, detail="A reason is required")
    bal = await db.token_balances.find_one({"organization_id": org_id})
    if not bal:
        raise HTTPException(status_code=404, detail="Organization has no token balance")
    new_exp = _parse_day(body.expires_at, "expires_at") if body.expires_at else None
    if new_exp is not None:
        new_exp = new_exp + timedelta(hours=23, minutes=59, seconds=59)
    await db.token_balances.update_one({"_id": bal["_id"]},
                                       {"$set": {"expires_at": new_exp, "updated_at": utcnow()}})
    await aaudit("tokens.expiry_changed", "billing", user=ctx.audit_user(), organization_id=org_id,
                 resource_type="token_balance", resource_id=org_id,
                 details={"before": _clean(bal.get("expires_at")), "after": _clean(new_exp),
                          "reason": body.reason[:300]}, **_meta(request))
    from app.billing.tokens import get_balance
    return {"success": True, "balance": get_balance(org_id)}


# ── LeadAI operations ──────────────────────────────────────────────────────

_FAILED = ("error", "failed")


@router.get("/leadai/summary")
async def leadai_summary(organization_id: Optional[str] = None, ctx: TenantContext = Depends(SUPER)):
    db = _db()
    base = {"organization_id": organization_id} if organization_id else {}
    since = utcnow() - timedelta(hours=24)
    s = db.search_history
    apify_q = {**base, "$or": [{"scrape_info": {"$exists": True}}, {"apify_run_id": {"$exists": True}},
                               {"provider": "apify"}]}
    return {"success": True, "summary": {
        "searches": {"total": await _count(s, base),
                     "running": await _count(s, {**base, "status": "running"}),
                     "completed": await _count(s, {**base, "status": {"$in": ["completed", "done", "success"]}}),
                     "failed": await _count(s, {**base, "status": {"$in": list(_FAILED)}}),
                     "cancelled": await _count(s, {**base, "status": "cancelled"}),
                     "last_24h": await _count(s, {**base, "created_at": {"$gte": since}})},
        "apify_jobs": {"total": await _count(s, apify_q),
                       "failed": await _count(s, {**apify_q, "status": {"$in": list(_FAILED)}}),
                       "failed_24h": await _count(s, {**apify_q, "status": {"$in": list(_FAILED)},
                                                      "created_at": {"$gte": since}})},
        "pages": await _count(db.facebook_pages, base),
        "posts": await _count(db.facebook_posts, base),
        "comments": await _count(db.facebook_comments, base),
        "analyzed": await _count(db.ai_comments, base),
        "leads": await _count(db.ai_comments, {**base, "is_lead": True}),
        "hot_leads": await _count(db.ai_comments, {**base, "is_lead": True, "lead_quality": "hot"}),
    }}


def _search_row(doc: Dict[str, Any], orgs: Dict[str, str]) -> Dict[str, Any]:
    info = doc.get("scrape_info") or {}
    intent = doc.get("intent") or {}
    return {
        "run_id": doc.get("run_id"), "query": doc.get("query"),
        "platform": intent.get("platform") or doc.get("platform"),
        "status": doc.get("status"), "phase": doc.get("phase"),
        "message": (doc.get("message") or "")[:300], "error": (str(doc.get("error") or ""))[:500] or None,
        "organization_id": doc.get("organization_id"),
        "organization_name": orgs.get(str(doc.get("organization_id"))),
        "user_id": doc.get("user_id"), "created_by": doc.get("created_by"),
        "pages_found": doc.get("pages_found"), "created_at": _clean(doc.get("created_at")),
        "completed_at": _clean(doc.get("completed_at")),
        "apify_run_id": doc.get("apify_run_id") or info.get("runId") or info.get("run_id"),
        "actor_id": info.get("actorId") or info.get("actor_id") or info.get("actor"),
        "usage_usd": info.get("usageTotalUsd") or info.get("usage_usd"),
    }


def _search_query(organization_id, user_id, status, platform, q, start, end, apify_only) -> Dict[str, Any]:
    clauses: List[Dict[str, Any]] = []
    if organization_id:
        clauses.append({"organization_id": organization_id})
    if user_id:
        clauses.append({"user_id": user_id})
    if status == "failed":
        clauses.append({"status": {"$in": list(_FAILED)}})
    elif status == "completed":
        clauses.append({"status": {"$in": ["completed", "done", "success"]}})
    elif status:
        clauses.append({"status": status})
    if platform:
        clauses.append({"$or": [{"intent.platform": platform}, {"platform": platform}]})
    rx = _rx(q)
    if rx:
        clauses.append({"$or": [{"query": rx}, {"run_id": rx}, {"created_by": rx}]})
    if start or end:
        rng: Dict[str, Any] = {}
        if start:
            rng["$gte"] = start
        if end:
            rng["$lt"] = end
        clauses.append({"created_at": rng})
    if apify_only:
        clauses.append({"$or": [{"scrape_info": {"$exists": True}}, {"apify_run_id": {"$exists": True}},
                                {"provider": "apify"}]})
    return {"$and": clauses} if clauses else {}


@router.get("/searches")
async def list_searches(organization_id: Optional[str] = None, user_id: Optional[str] = None,
                        status: Optional[str] = None, platform: Optional[str] = None,
                        q: Optional[str] = None, from_: Optional[str] = Query(None, alias="from"),
                        to: Optional[str] = None, apify_only: bool = False,
                        page: int = Query(1, ge=1), limit: int = Query(25, ge=1, le=200),
                        sort: str = "-created_at", ctx: TenantContext = Depends(SUPER)):
    db = _db()
    start = _parse_day(from_, "from")
    end = _parse_day(to, "to")
    query = _search_query(organization_id, user_id, status, platform, q, start,
                          end + timedelta(days=1) if end else None, apify_only)
    field, direction = _sort(sort, ("created_at", "completed_at", "status"), "-created_at")
    total = await _count(db.search_history, query)
    docs = [d async for d in db.search_history.find(query, {"comment_filter": 0})
            .sort(field, direction).skip((page - 1) * limit).limit(limit)]
    orgs = await _org_names(db, [d.get("organization_id") for d in docs])
    return {"success": True, "items": [_search_row(d, orgs) for d in docs], "total": total,
            "page": page, "limit": limit, "pages": _pages(total, limit)}


@router.get("/searches/{run_id}/chain")
async def search_chain(run_id: str, ctx: TenantContext = Depends(SUPER)):
    """Failure-investigation chain for one search run:
    Organization -> Admin(s) -> User -> Search -> Apify job(s) -> Pages -> Posts -> Comments -> Leads."""
    db = _db()
    doc = await db.search_history.find_one({"run_id": run_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Search run not found")
    org_id = str(doc.get("organization_id") or "")
    org = await db.organizations.find_one({"_id": _oid(org_id)}, {"name": 1, "status": 1, "plan_id": 1, "slug": 1}) \
        if _oid(org_id) else None
    admins = []
    if org_id:
        members = [m async for m in db.organization_members.find(
            {"organization_id": org_id, "role": {"$in": ["owner", "admin"]}, "status": "active"})]
        umap = await _user_map(db, [m.get("user_id") for m in members])
        admins = [{"user_id": m.get("user_id"), "role": m.get("role"),
                   "email": umap.get(str(m.get("user_id")), {}).get("email")} for m in members]
    user = None
    if doc.get("user_id") and _oid(doc.get("user_id")):
        u = await db.users.find_one({"_id": _oid(doc["user_id"])}, {"email": 1, "name": 1, "status": 1})
        if u:
            user = {"id": str(u["_id"]), "email": u.get("email"), "name": u.get("name"),
                    "status": u.get("status", "active")}
    if user is None and doc.get("created_by"):
        user = {"id": None, "email": doc.get("created_by"), "name": None, "status": None}
    # child counts (same linking as the /admin job detail)
    page_ids = [str(p["_id"]) async for p in db.facebook_pages.find({"search_run_id": run_id}, {"_id": 1})]
    post_q: Dict[str, Any] = {"$or": [{"search_run_id": run_id}, {"collection_run_id": run_id}]
                              + ([{"page_ref": {"$in": page_ids}}] if page_ids else [])}
    post_ids = [str(p["_id"]) async for p in db.facebook_posts.find(post_q, {"_id": 1})]
    comment_q: Dict[str, Any] = {"$or": [{"search_run_id": run_id}]
                                 + ([{"post_ref": {"$in": post_ids}}] if post_ids else [])}
    comment_ids = [str(c["_id"]) async for c in db.facebook_comments.find(comment_q, {"_id": 1})]
    lead_q: Dict[str, Any] = {"$or": [{"search_run_id": run_id}]
                              + ([{"comment_ref": {"$in": comment_ids}}] if comment_ids else [])}
    counts = {"pages": len(page_ids), "posts": len(post_ids), "comments": len(comment_ids),
              "analyzed": await _count(db.ai_comments, lead_q),
              "leads": await _count(db.ai_comments, {"$and": [lead_q, {"is_lead": True}]})}
    info = doc.get("scrape_info") or {}
    jobs = []
    if info or doc.get("apify_run_id"):
        jobs.append({"apify_run_id": doc.get("apify_run_id") or info.get("runId") or info.get("run_id"),
                     "actor_id": info.get("actorId") or info.get("actor_id") or info.get("actor"),
                     "dataset_id": info.get("datasetId") or info.get("dataset_id"),
                     "status": info.get("status") or doc.get("status"),
                     "usage_usd": info.get("usageTotalUsd") or info.get("usage_usd"),
                     "items": info.get("items") or info.get("item_count")})
    failures = [_clean(n) async for n in db.notifications.find(
        {"type": {"$in": ["apify_failure", "system_error"]}, "data.search_run_id": run_id},
        {"read_by": 0}).sort("created_at", -1).limit(10)]
    for f in failures:
        if f.get("data", {}).get("apify_run_id") and not any(j.get("apify_run_id") == f["data"]["apify_run_id"] for j in jobs):
            jobs.append({"apify_run_id": f["data"]["apify_run_id"], "status": "failed",
                         "error": f.get("message")})
    audit = [_clean(a) async for a in db.audit_logs.find({"resource_id": run_id}).sort("at", 1).limit(50)]
    orgs = {org_id: (org or {}).get("name", "")}
    error = doc.get("error") or (doc.get("message") if doc.get("status") in _FAILED else None)
    return {"success": True, "chain": {
        "organization": _clean(org), "admins": admins, "user": user,
        "search": _search_row(doc, orgs), "apify_jobs": jobs, "counts": counts,
        "error": (str(error)[:1000] if error else None), "failures": failures, "audit": audit,
        "links": {"pages": f"/admin#/pages?run={run_id}", "posts": f"/admin#/posts?run={run_id}",
                  "comments": "/admin#/ci", "leads": "/admin#/leads",
                  "job": f"/admin#/jobs/details:{run_id}"},
    }}


@router.get("/leads")
async def list_leads(organization_id: Optional[str] = None, run_id: Optional[str] = None,
                     quality: Optional[str] = None, platform: Optional[str] = None,
                     q: Optional[str] = None, page: int = Query(1, ge=1),
                     limit: int = Query(25, ge=1, le=200), sort: str = "-created_at",
                     ctx: TenantContext = Depends(SUPER)):
    """Global lead monitor. Contact data is always masked here; item-level
    work happens in the /admin lead browser."""
    db = _db()
    query: Dict[str, Any] = {"is_lead": True}
    if organization_id:
        query["organization_id"] = organization_id
    if run_id:
        query["search_run_id"] = run_id
    if quality:
        query["lead_quality"] = quality
    if platform:
        query["platform"] = platform
    rx = _rx(q)
    if rx:
        query["$or"] = [{"commenter_name": rx}, {"comment_text": rx}]
    field, direction = _sort(sort, ("created_at", "lead_score"), "-created_at")
    total = await _count(db.ai_comments, query)
    docs = [d async for d in db.ai_comments.find(query).sort(field, direction)
            .skip((page - 1) * limit).limit(limit)]
    orgs = await _org_names(db, [d.get("organization_id") for d in docs])
    items = [{"id": str(d["_id"]), "organization_id": d.get("organization_id"),
              "organization_name": orgs.get(str(d.get("organization_id"))),
              "platform": d.get("platform"), "commenter_name": d.get("commenter_name"),
              "comment_text": (d.get("comment_text") or "")[:240],
              "phone": mask_phone(d.get("phone")), "email": mask_email(d.get("email")),
              "lead_quality": d.get("lead_quality"), "lead_score": d.get("lead_score"),
              "search_run_id": d.get("search_run_id"), "created_at": _clean(d.get("created_at"))}
             for d in docs]
    return {"success": True, "items": items, "total": total, "page": page, "limit": limit,
            "pages": _pages(total, limit)}


# ── Analytics ──────────────────────────────────────────────────────────────

GRANULARITIES = ("day", "week", "month")


def _days(start: datetime, end: datetime) -> List[str]:
    out, d = [], start.replace(hour=0, minute=0, second=0, microsecond=0)
    while d < end and len(out) < 800:
        out.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return out


def bucket_key(day: str, granularity: str) -> str:
    """Bucket label of a UTC day ``YYYY-MM-DD``: the day itself, the ISO
    week's Monday (``YYYY-MM-DD``) or the month (``YYYY-MM``)."""
    if granularity == "month":
        return day[:7]
    if granularity == "week":
        d = datetime.strptime(day, "%Y-%m-%d")
        return (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")
    return day


def bucket_labels(days: List[str], granularity: str) -> List[str]:
    """Dense, ordered bucket labels covering every day of the range (a
    partially covered first/last week or month still gets its bucket)."""
    out: List[str] = []
    for day in days:
        k = bucket_key(day, granularity)
        if not out or out[-1] != k:
            out.append(k)
    return out


def rollup(by_day: Dict[str, Any], days: List[str], granularity: str) -> Dict[str, Any]:
    """Sum daily values into buckets; buckets with no data are 0. Days
    outside ``days`` are ignored."""
    out: Dict[str, Any] = {k: 0 for k in bucket_labels(days, granularity)}
    for day in days:
        v = by_day.get(day) or 0
        out[bucket_key(day, granularity)] += v
    return out


def norm_currency(value: Any) -> str:
    cur = str(value or "").strip().upper()
    return cur if re.fullmatch(r"[A-Z]{3}", cur) else "USD"


def currency_totals(rows: Iterable[Dict[str, Any]], amount_field: str = "amount",
                    currency_field: str = "currency") -> Dict[str, float]:
    """Group amounts by (normalised) currency — never adds different
    currencies together. ``rows`` may be raw docs or ``$group`` output."""
    totals: Dict[str, float] = {}
    for r in rows:
        cur = norm_currency(r.get(currency_field))
        try:
            amt = float(r.get(amount_field) or 0)
        except (TypeError, ValueError):
            amt = 0.0
        totals[cur] = round(totals.get(cur, 0.0) + amt, 2)
    return dict(sorted(totals.items()))


def single_amount(totals: Dict[str, float]) -> Optional[float]:
    """The one total when at most one currency is involved (0.0 for none);
    None when several currencies would otherwise be added together."""
    if not totals:
        return 0.0
    if len(totals) == 1:
        return round(next(iter(totals.values())), 2)
    return None


def single_currency(totals: Dict[str, float], default: str = "USD") -> Optional[str]:
    if not totals:
        return default
    return next(iter(totals)) if len(totals) == 1 else None


async def _series(coll, field: str, start: datetime, end: datetime, days: List[str],
                  match: Optional[Dict[str, Any]] = None, sum_field: Optional[str] = None,
                  distinct: Optional[str] = None, unwind: Optional[str] = None,
                  granularity: str = "day", by_currency: bool = False) -> Dict[str, Any]:
    """Buckets (day / ISO week / month, UTC) of a collection — dense and
    zero-filled — plus the total. ``by_currency`` (money series) never adds
    currencies together: one series per currency under ``by_currency``;
    the flat ``values``/``total`` are kept only when there is one currency."""
    pipeline: List[Dict[str, Any]] = []
    if unwind:
        pipeline += [{"$match": {field: {"$gte": start, "$lt": end}}}, {"$unwind": f"${unwind}"}]
    pipeline.append({"$match": {field: {"$gte": start, "$lt": end}, **(match or {})}})
    day_expr = {"$dateToString": {"format": "%Y-%m-%d", "date": f"${field}"}}
    group: Dict[str, Any] = {"_id": {"d": day_expr, "c": "$currency"} if by_currency else day_expr,
                             "n": {"$sum": f"${sum_field}" if sum_field else 1}}
    if distinct:
        group["d"] = {"$addToSet": f"${distinct}"}
    pipeline.append({"$group": group})
    rows = await _agg(coll, pipeline)
    labels = bucket_labels(days, granularity)

    def _fmt(n: Any) -> Any:
        return round(n, 2) if isinstance(n, float) else n

    if by_currency:
        per_cur: Dict[str, Dict[str, float]] = {}
        for r in rows:
            key = r.get("_id") or {}
            day = key.get("d")
            cur = norm_currency(key.get("c"))
            bucket = per_cur.setdefault(cur, {})
            bucket[day] = bucket.get(day, 0) + (r.get("n") or 0)
        series: Dict[str, Dict[str, Any]] = {}
        for cur in sorted(per_cur):
            sums = rollup(per_cur[cur], days, granularity)
            values = [_fmt(sums[k]) for k in labels]
            series[cur] = {"values": values, "total": round(sum(values), 2)}
        out: Dict[str, Any] = {"by_currency": series, "currencies": list(series)}
        if len(series) <= 1:
            only = next(iter(series.values()), None)
            out.update({"values": only["values"] if only else [0 for _ in labels],
                        "total": only["total"] if only else 0,
                        "currency": next(iter(series), None) or "USD"})
        else:
            out.update({"values": None, "total": None, "currency": None, "multi_currency": True})
        return out

    by_day = {r["_id"]: r for r in rows}
    if distinct:
        sets: Dict[str, set] = {k: set() for k in labels}
        for day in days:
            vals = [v for v in ((by_day.get(day) or {}).get("d") or []) if v]
            sets[bucket_key(day, granularity)].update(vals)
        values = [len(sets[k]) for k in labels]
        total = len(set().union(*sets.values())) if sets else 0
        return {"values": values, "total": total}
    sums = rollup({d: (r.get("n") or 0) for d, r in by_day.items()}, days, granularity)
    values = [_fmt(sums[k]) for k in labels]
    return {"values": values, "total": round(sum(values), 2)}


@router.get("/analytics")
async def analytics(range: str = "30d", from_: Optional[str] = Query(None, alias="from"),
                    to: Optional[str] = None, organization_id: Optional[str] = None,
                    granularity: str = "day", ctx: TenantContext = Depends(SUPER)):
    if granularity not in GRANULARITIES:
        raise HTTPException(status_code=422, detail="granularity must be day, week or month")
    db = _db()
    start, end = _date_range(range, from_, to)
    days = _days(start, end)
    labels = bucket_labels(days, granularity)
    org = {"organization_id": organization_id} if organization_id else {}
    org_self = {"_id": _oid(organization_id)} if organization_id and _oid(organization_id) else {}

    async def s(coll, field, match=None, **kw):
        return await _series(coll, field, start, end, days, match, granularity=granularity, **kw)

    business = {
        "registrations": await s(db.organizations, "created_at", org_self),
        "demo_requests": await s(db.demo_requests, "created_at", org),
        "demo_conversions": await s(db.demo_requests, "history.at",
                                    {**org, "history.status": "converted"}, unwind="history"),
        "subscriptions_activated": await s(db.subscriptions, "status_history.at",
                                           {**org, "status_history.to": "active"}, unwind="status_history"),
        "churn": await s(db.subscriptions, "status_history.at",
                         {**org, "status_history.to": {"$in": ["cancelled", "expired"]},
                          "status_history.from": {"$in": ["active", "suspended", "trialing", "past_due"]}},
                         unwind="status_history"),
        "revenue": await s(db.payments, "created_at", {**org, "status": "succeeded"},
                           sum_field="amount", by_currency=True),
    }
    product = {
        "searches": await s(db.search_history, "created_at", org),
        "failed_searches": await s(db.search_history, "created_at", {**org, "status": {"$in": list(_FAILED)}}),
        "active_users": await s(db.search_history, "created_at", org, distinct="user_id"),
        "apify_jobs": await s(db.search_history, "created_at", {**org, "provider": "apify"}),
        "leads": await s(db.ai_comments, "created_at", {**org, "is_lead": True}),
        "ai_calls": await s(db.ai_requests, "created_at", org),
        "tokens_consumed": await s(db.token_ledger, "created_at", {**org, "type": "consume"},
                                   sum_field="amount"),
        "errors": await s(db.security_events, "at", {**org, "severity": {"$in": ["high", "critical"]}}),
        "new_users": await s(db.users, "created_at"),
    }
    # current-state snapshots
    plan_dist = {r["_id"] or "none": r["n"] for r in await _agg(db.subscriptions, [
        {"$match": {**org, "status": {"$in": ["active", "trialing"]}}},
        {"$group": {"_id": "$plan_id", "n": {"$sum": 1}}}])}
    snapshot = {
        "active_customers": await _count(db.organizations, {**org_self, "status": "active"}),
        "demo_accounts": await _count(db.organizations, {**org_self, "status": "demo"}),
        "total_users": await _count(db.users, {}),
        "plan_distribution": plan_dist,
    }
    # ``days`` holds the bucket labels (kept under its historic name for
    # older clients); ``buckets`` is the same list.
    return {"success": True, "from": start.isoformat(), "to": end.isoformat(),
            "granularity": granularity, "days": labels, "buckets": labels,
            "business": business, "product": product, "snapshot": snapshot}


# ── Security center ────────────────────────────────────────────────────────

_RATE_LIMIT_TYPES = ["login_rate_limited", "signup_rate_limited", "password_reset_rate_limited",
                     "rate_limited"]
_PERMISSION_TYPES = ["permission_denied", "escalation_attempt"]
_CROSS_TENANT_TYPES = ["cross_tenant_access", "cross_user_access"]


@router.get("/security/overview")
async def security_overview(ctx: TenantContext = Depends(SUPER)):
    db = _db()
    now = utcnow()
    d1, d7 = now - timedelta(days=1), now - timedelta(days=7)
    se = db.security_events

    async def c(types, since):
        return await _count(se, {"type": {"$in": types}, "at": {"$gte": since}})
    return {"success": True, "overview": {
        "failed_logins_24h": await c(["login_failed", "account_locked"], d1),
        "failed_logins_7d": await c(["login_failed", "account_locked"], d7),
        "active_lockouts": await _count(db.login_lockouts, {"locked_until": {"$gt": now}}),
        "rate_limit_events_7d": await c(_RATE_LIMIT_TYPES, d7),
        "permission_violations_7d": await c(_PERMISSION_TYPES, d7),
        "cross_tenant_attempts_7d": await c(_CROSS_TENANT_TYPES, d7),
        "high_severity_7d": await _count(se, {"severity": {"$in": ["high", "critical"]}, "at": {"$gte": d7}}),
        "active_sessions": await _count(db.user_sessions, {"revoked_at": None, "expires_at": {"$gt": now}}),
        "successful_logins_24h": await _count(db.audit_logs, {"action": "auth.login", "success": True,
                                                              "at": {"$gte": d1}}),
    }, "groups": {"rate_limit": _RATE_LIMIT_TYPES, "permission": _PERMISSION_TYPES,
                  "cross_tenant": _CROSS_TENANT_TYPES}}


@router.get("/security/logins")
async def login_activity(success: Optional[bool] = None, q: Optional[str] = None,
                         page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=200),
                         ctx: TenantContext = Depends(SUPER)):
    db = _db()
    query: Dict[str, Any] = {"action": "auth.login"}
    if success is not None:
        query["success"] = success
    rx = _rx(q)
    if rx:
        query["$or"] = [{"actor_email": rx}, {"ip": rx}]
    total = await _count(db.audit_logs, query)
    items = [_clean(d) async for d in db.audit_logs.find(query).sort("at", -1)
             .skip((page - 1) * limit).limit(limit)]
    return {"success": True, "items": items, "total": total, "page": page, "limit": limit,
            "pages": _pages(total, limit)}


@router.get("/security/lockouts")
async def lockouts(active_only: bool = False, page: int = Query(1, ge=1),
                   limit: int = Query(50, ge=1, le=200), ctx: TenantContext = Depends(SUPER)):
    db = _db()
    now = utcnow()
    query: Dict[str, Any] = {"locked_until": {"$gt": now}} if active_only else {}
    total = await _count(db.login_lockouts, query)
    items = []
    async for d in db.login_lockouts.find(query).sort("updated_at", -1).skip((page - 1) * limit).limit(limit):
        until = _as_dt(d.get("locked_until"))
        items.append({"email": d.get("email"), "failures": d.get("failures", 0),
                      "locked_until": _clean(until), "locked": bool(until and until > now),
                      "updated_at": _clean(d.get("updated_at"))})
    return {"success": True, "items": items, "total": total, "page": page, "limit": limit,
            "pages": _pages(total, limit)}


class LockoutClearBody(BaseModel):
    email: str
    reason: str = ""


@router.post("/security/lockouts/clear")
async def clear_lockout(body: LockoutClearBody, request: Request, ctx: TenantContext = Depends(SUPER)):
    from app.auth.service import clear_account_failures
    email = body.email.strip().lower()
    if not email:
        raise HTTPException(status_code=422, detail="email is required")
    clear_account_failures(email)
    await aaudit("security.lockout_cleared", "security", user=ctx.audit_user(), resource_type="user",
                 resource_id=email, details={"email": email, "reason": body.reason[:300]}, **_meta(request))
    return {"success": True}


@router.get("/sessions")
async def active_sessions(q: Optional[str] = None, scope: Optional[str] = None,
                          organization_id: Optional[str] = None, user_id: Optional[str] = None,
                          page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=200),
                          ctx: TenantContext = Depends(SUPER)):
    db = _db()
    now = utcnow()
    query: Dict[str, Any] = {"revoked_at": None, "expires_at": {"$gt": now}}
    if scope in ("site", "admin"):
        query["scope"] = scope
    if organization_id:
        query["organization_id"] = organization_id
    if user_id:
        query["user_id"] = user_id
    rx = _rx(q)
    if rx:
        query["email"] = rx
    total = await _count(db.user_sessions, query)
    docs = [d async for d in db.user_sessions.find(query).sort("last_activity_at", -1)
            .skip((page - 1) * limit).limit(limit)]
    orgs = await _org_names(db, [d.get("organization_id") for d in docs])
    items = [{"ref": str(d["_id"]), "user_id": d.get("user_id"), "email": d.get("email"),
              "scope": d.get("scope"), "role": d.get("role"),
              "organization_id": d.get("organization_id"),
              "organization_name": orgs.get(str(d.get("organization_id"))),
              "ip_hash": d.get("ip_hash"), "user_agent": d.get("user_agent"),
              "impersonated_by": d.get("impersonated_by"),
              "created_at": _clean(d.get("created_at")),
              "last_activity_at": _clean(d.get("last_activity_at")),
              "expires_at": _clean(d.get("expires_at")),
              "current": bool(ctx.session_id and d.get("session_id") == ctx.session_id)}
             for d in docs]
    return {"success": True, "items": items, "total": total, "page": page, "limit": limit,
            "pages": _pages(total, limit)}


@router.post("/sessions/{session_ref}/revoke")
async def revoke_session_route(session_ref: str, body: ReasonBody, request: Request,
                               ctx: TenantContext = Depends(SUPER)):
    from app.auth.service import revoke_session
    db = _db()
    oid = _oid(session_ref)
    doc = await db.user_sessions.find_one({"_id": oid}) if oid else None
    if not doc:
        raise HTTPException(status_code=404, detail="Session not found")
    if doc.get("revoked_at") is not None:
        return {"success": True, "revoked": False, "message": "Session was already revoked"}
    ok = revoke_session(doc["session_id"], revoked_by=f"super_admin:{ctx.email}")
    await aaudit("session.revoked", "security", user=ctx.audit_user(),
                 organization_id=doc.get("organization_id") or None, resource_type="session",
                 resource_id=session_ref,
                 details={"email": doc.get("email"), "scope": doc.get("scope"),
                          "reason": body.reason[:300]}, **_meta(request))
    return {"success": True, "revoked": ok}


# ── Feature flags / maintenance / notification config ──────────────────────

FLAG_REGISTRY: List[Dict[str, Any]] = [
    {"key": "features.url_search.enabled", "group": "features", "label": "URL Search Agent",
     "help": "Users can start new URL searches. Existing results stay readable when off."},
    {"key": "features.ai_analysis.enabled", "group": "features", "label": "AI Analysis",
     "help": "New comments are analysed by AI. Off = rule-based fallback only."},
    {"key": "features.exports.enabled", "group": "features", "label": "CSV Export",
     "help": "Users can create new CSV exports. Past exports are untouched."},
    {"key": "features.demo_registration.enabled", "group": "features", "label": "Demo Registration",
     "help": "The public 'Request a demo' signup accepts new requests."},
    *[{"key": f"platform.{p}.enabled", "group": "platforms", "label": p.capitalize(),
       "help": f"New {p.capitalize()} scrapes are allowed."} for p in _PLATFORMS],
    {"key": "maintenance.enabled", "group": "maintenance", "label": "Maintenance Mode",
     "help": "Blocks the customer app with a 503 page. Admin portals stay reachable."},
    {"key": "maintenance.message", "group": "maintenance", "label": "Maintenance message",
     "type": "text", "help": "Shown to customers while maintenance mode is on."},
    {"key": "notifications.alerts_enabled", "group": "notifications", "label": "In-app alerts",
     "help": "Live alerts in the admin consoles."},
    {"key": "notifications.job_failed_email", "group": "notifications", "label": "Email on failed job",
     "help": "Email owners when a search job fails."},
    {"key": "notifications.new_lead_email", "group": "notifications", "label": "Email on new lead",
     "help": "Email owners when a new hot lead is found."},
]
_FLAG_KEYS = {f["key"]: f for f in FLAG_REGISTRY}


def _flag_values() -> List[Dict[str, Any]]:
    from app.admin.settings import get_setting
    out = []
    for f in FLAG_REGISTRY:
        v = get_setting(f["key"])
        out.append({**f, "type": f.get("type", "bool"),
                    "value": (str(v or "") if f.get("type") == "text" else bool(v))})
    return out


@router.get("/feature-flags")
async def get_flags(ctx: TenantContext = Depends(SUPER)):
    return {"success": True, "flags": _flag_values(),
            "note": "Flags only gate NEW actions; turning one off never deletes or alters data."}


class FlagsBody(BaseModel):
    changes: Dict[str, Any]
    reason: str = ""


@router.put("/feature-flags")
async def put_flags(body: FlagsBody, request: Request, ctx: TenantContext = Depends(SUPER)):
    from app.admin.settings import get_setting, set_setting
    if not body.changes:
        raise HTTPException(status_code=422, detail="No changes")
    unknown = [k for k in body.changes if k not in _FLAG_KEYS]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown flag(s): {', '.join(unknown)}")
    clean: Dict[str, Any] = {}
    for key, value in body.changes.items():
        if _FLAG_KEYS[key].get("type") == "text":
            text = str(value or "").strip()
            if len(text) > 500:
                raise HTTPException(status_code=422, detail="Message is limited to 500 characters")
            clean[key] = text
        else:
            if not isinstance(value, bool):
                raise HTTPException(status_code=422, detail=f"{key} must be true or false")
            clean[key] = value
    changed = []
    for key, value in clean.items():
        before = get_setting(key)
        if before == value:
            continue
        if not set_setting(key, value, by=ctx.email):
            raise HTTPException(status_code=503, detail="Could not save setting")
        changed.append(key)
        await aaudit("feature_flag.updated", "settings", user=ctx.audit_user(),
                     resource_type="setting", resource_id=key,
                     details={"key": key, "before": before, "after": value,
                              "reason": body.reason[:300]}, **_meta(request))
    try:
        from app.admin import settings as s
        for key in changed:
            s._CACHE.pop(key, None)
    except Exception:
        pass
    return {"success": True, "changed": changed, "flags": _flag_values()}


# ── Integrations & system health ───────────────────────────────────────────

def _env_set(name: str, default: str = "") -> bool:
    try:
        from app.admin.envvars import get_envvar_str
        return bool((get_envvar_str(name, default) or "").strip())
    except Exception:
        return bool(default)


async def _db_ping(db) -> Tuple[bool, Optional[float]]:
    try:
        started = time.perf_counter()
        await db.command("ping")
        return True, round((time.perf_counter() - started) * 1000, 1)
    except Exception:
        return False, None


async def _integrations(db) -> Dict[str, Any]:
    from app.admin.settings import get_actor_id, get_apify_token, get_setting
    from app.config import get_settings
    cfg = get_settings()
    since = utcnow() - timedelta(hours=24)
    apify_q = {"$or": [{"scrape_info": {"$exists": True}}, {"provider": "apify"}]}
    usage = await _agg(db.search_history, [
        {"$match": {"created_at": {"$gte": utcnow() - timedelta(days=30)}, "scrape_info": {"$exists": True}}},
        {"$group": {"_id": None, "usd": {"$sum": "$scrape_info.usageTotalUsd"}, "n": {"$sum": 1}}}])
    try:
        from app.billing.provider import get_billing_provider
        provider = get_billing_provider().name
    except Exception:
        provider = "unknown"
    ping_ok, latency = await _db_ping(db)
    outbox = {s: await _count(db.email_outbox, {"status": s}) for s in ("queued", "sent", "failed")}
    ai_failed = await _count(db.ai_requests, {"status": {"$nin": ["success"]}, "created_at": {"$gte": since}})
    return {
        "apify": {
            "configured": bool(get_apify_token()),
            "last_test_ok": get_setting("apify.last_test_ok"),
            "last_test_at": _clean(get_setting("apify.last_test_at")),
            "actors": {p: get_actor_id(p) for p in _PLATFORMS},
            "platforms_enabled": {p: bool(get_setting(f"platform.{p}.enabled")) for p in _PLATFORMS},
            "jobs_24h": await _count(db.search_history, {**apify_q, "created_at": {"$gte": since}}),
            "failures_24h": await _count(db.search_history, {**apify_q, "status": {"$in": list(_FAILED)},
                                                             "created_at": {"$gte": since}}),
            "usage_usd_30d": round(float((usage[0].get("usd") if usage else 0) or 0), 4),
            "manage_link": "/admin#/apify",
        },
        "ai": {
            "provider": "gemini",
            "configured": _env_set("GEMINI_API_KEY", cfg.gemini_api_key or ""),
            "enabled": bool(get_setting("ai.enabled")),
            "model": get_setting("ai.model"),
            "requests_24h": await _count(db.ai_requests, {"created_at": {"$gte": since}}),
            "failures_24h": ai_failed,
            "manage_link": "/admin#/ai",
        },
        "payments": {
            "provider": provider,
            "stripe_key_configured": _env_set("STRIPE_SECRET_KEY", cfg.stripe_secret_key or ""),
            "stripe_webhook_secret_configured": _env_set("STRIPE_WEBHOOK_SECRET", cfg.stripe_webhook_secret or ""),
            "billing_webhook_secret_configured": _env_set("BILLING_WEBHOOK_SECRET"),
            "failed_payments_7d": await _count(db.payments, {"status": "failed",
                                                             "created_at": {"$gte": utcnow() - timedelta(days=7)}}),
        },
        "email": {
            "smtp_configured": _env_set("SMTP_HOST"),
            "smtp_auth_configured": _env_set("SMTP_USER"),
            "public_base_url_configured": _env_set("PUBLIC_BASE_URL"),
            "outbox": outbox,
        },
        "storage": {
            "exports": await _count(db.exports, {}),
            "media_files": await _count(db.website_media, {}),
        },
        "database": {"connected": ping_ok, "latency_ms": latency},
    }


@router.get("/integrations")
async def integrations(ctx: TenantContext = Depends(SUPER)):
    return {"success": True, "integrations": await _integrations(_db())}


@router.get("/health")
async def platform_health(ctx: TenantContext = Depends(SUPER)):
    """System health: API, DB ping + latency, Apify, AI provider, payment
    provider, background jobs, failed jobs (24h) and recent 5xx errors."""
    db = get_async_db()
    if db is None:
        return {"success": True, "health": {"status": "down", "overall": "down", "components": [
            {"key": "database", "label": "Database", "status": "down", "detail": "Unavailable"}]}}
    integ = await _integrations(db)
    now = utcnow()
    since = now - timedelta(hours=24)
    running = await _count(db.search_history, {"status": "running"})
    stale = await _count(db.search_history, {"status": "running", "created_at": {"$lt": now - timedelta(minutes=30)}})
    failed_24h = await _count(db.search_history, {"status": {"$in": list(_FAILED)}, "created_at": {"$gte": since}})
    errors = [_clean(n) async for n in db.notifications.find(
        {"type": "system_error", "created_at": {"$gte": now - timedelta(days=7)}},
        {"read_by": 0}).sort("created_at", -1).limit(10)]
    errors_24h = await _count(db.notifications, {"type": "system_error", "created_at": {"$gte": since}})
    dbi = integ["database"]
    components = [
        {"key": "api", "label": "API", "status": "ok", "detail": "Responding"},
        {"key": "database", "label": "Database", "status": "ok" if dbi["connected"] else "down",
         "detail": f"{dbi['latency_ms']} ms" if dbi["connected"] else "Ping failed"},
        {"key": "apify", "label": "Apify", "status": ("warn" if not integ["apify"]["configured"]
                                                      or integ["apify"]["failures_24h"] else "ok"),
         "detail": ("Token not configured" if not integ["apify"]["configured"]
                    else f"{integ['apify']['failures_24h']} failed / {integ['apify']['jobs_24h']} jobs (24h)")},
        {"key": "ai", "label": "AI provider", "status": ("warn" if not integ["ai"]["configured"]
                                                         or integ["ai"]["failures_24h"] else "ok"),
         "detail": ("API key not configured (rule fallback)" if not integ["ai"]["configured"]
                    else f"{integ['ai']['failures_24h']} failed / {integ['ai']['requests_24h']} calls (24h)")},
        {"key": "payments", "label": "Payment provider",
         "status": "ok" if (integ["payments"]["provider"] == "mock"
                            and integ["payments"]["billing_webhook_secret_configured"]) or
                           (integ["payments"]["provider"] == "stripe"
                            and integ["payments"]["stripe_webhook_secret_configured"]) else "warn",
         "detail": f"{integ['payments']['provider']} provider"},
        {"key": "email", "label": "Email", "status": "ok" if integ["email"]["smtp_configured"] else "warn",
         "detail": "SMTP configured" if integ["email"]["smtp_configured"] else "SMTP not configured — emails queue only"},
        {"key": "jobs", "label": "Background jobs", "status": "warn" if stale else "ok",
         "detail": f"{running} running" + (f", {stale} stale (>30 min)" if stale else "")},
        {"key": "failed_jobs", "label": "Failed jobs (24h)", "status": "warn" if failed_24h else "ok",
         "detail": str(failed_24h)},
        {"key": "errors", "label": "Server errors (24h)", "status": "err" if errors_24h else "ok",
         "detail": str(errors_24h)},
    ]
    worst = "ok"
    for c in components:
        if c["status"] in ("down", "err"):
            worst = "down" if c["key"] == "database" else "degraded"
            if worst == "down":
                break
        elif c["status"] == "warn" and worst == "ok":
            worst = "warn"
    collection_counts = {}
    for name in ("organizations", "users", "organization_members", "search_history", "facebook_pages",
                 "facebook_posts", "facebook_comments", "ai_comments", "subscriptions", "plans", "audit_logs"):
        collection_counts[name] = await _count(db[name], {})
    return {"success": True, "health": {
        "status": "ok" if worst in ("ok", "warn") else "degraded",
        "overall": worst, "components": components, "recent_errors": errors,
        "mongodb": {"connected": dbi["connected"], "latency_ms": dbi["latency_ms"]},
        "collection_counts": collection_counts, "checked_at": now.isoformat()}}


# ── AI management ──────────────────────────────────────────────────────────

@router.get("/ai/overview")
async def ai_overview(range: str = "30d", organization_id: Optional[str] = None,
                      ctx: TenantContext = Depends(SUPER)):
    from app.pipeline.ai_usage_service import get_ai_overview_metrics
    if range not in ("24h", "7d", "30d", "90d"):
        raise HTTPException(status_code=422, detail="range must be 24h, 7d, 30d or 90d")
    return {"success": True, "metrics": _clean(get_ai_overview_metrics(range, organization_id))}


@router.get("/ai/prompts")
async def ai_prompts(ctx: TenantContext = Depends(SUPER)):
    from app.pipeline.ai_prompt_service import list_prompts
    items = _clean(list_prompts())
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for p in items:
        groups.setdefault(p.get("prompt_key") or "unknown", []).append(p)
    return {"success": True, "groups": groups}


class PromptBody(BaseModel):
    prompt_key: str
    name: str
    purpose: str = ""
    system_instructions: str
    user_template: str
    model: str = "gemini-2.5-flash"
    variables: Optional[List[str]] = None
    change_reason: str
    make_active: bool = False


@router.post("/ai/prompts")
async def ai_prompt_create(body: PromptBody, request: Request, ctx: TenantContext = Depends(SUPER)):
    from app.pipeline.ai_prompt_service import create_prompt_version
    if not re.fullmatch(r"[a-z0-9_]{2,60}", body.prompt_key):
        raise HTTPException(status_code=422, detail="prompt_key must be lowercase letters, digits or _")
    if not body.change_reason.strip():
        raise HTTPException(status_code=422, detail="A change reason is required")
    if not body.system_instructions.strip() or not body.user_template.strip():
        raise HTTPException(status_code=422, detail="Instructions and template are required")
    doc = create_prompt_version(body.prompt_key, body.name.strip()[:120], body.purpose.strip()[:300],
                                body.system_instructions, body.user_template, model=body.model,
                                variables=body.variables, created_by=ctx.email,
                                change_reason=body.change_reason.strip()[:300],
                                make_active=body.make_active)
    await aaudit("ai.prompt_version_created", "ai", user=ctx.audit_user(), resource_type="ai_prompt",
                 resource_id=str(doc.get("_id")),
                 details={"prompt_key": body.prompt_key, "version": doc.get("version"),
                          "active": body.make_active, "reason": body.change_reason[:300]}, **_meta(request))
    return {"success": True, "prompt": _clean(doc)}


@router.post("/ai/prompts/{prompt_id}/activate")
async def ai_prompt_activate(prompt_id: str, request: Request, ctx: TenantContext = Depends(SUPER)):
    from app.pipeline.ai_prompt_service import activate_prompt_version
    if not _oid(prompt_id):
        raise HTTPException(status_code=404, detail="Prompt not found")
    try:
        doc = activate_prompt_version(prompt_id, actor_id=ctx.email)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    await aaudit("ai.prompt_activated", "ai", user=ctx.audit_user(), resource_type="ai_prompt",
                 resource_id=prompt_id, details={"prompt_key": doc.get("prompt_key"),
                                                 "version": doc.get("version")}, **_meta(request))
    return {"success": True, "prompt": _clean(doc)}


@router.post("/ai/prompts/{prompt_id}/rollback")
async def ai_prompt_rollback(prompt_id: str, request: Request, ctx: TenantContext = Depends(SUPER)):
    from app.pipeline.ai_prompt_service import rollback_prompt
    if not _oid(prompt_id):
        raise HTTPException(status_code=404, detail="Prompt not found")
    try:
        doc = rollback_prompt(prompt_id, actor_id=ctx.email)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    await aaudit("ai.prompt_rolled_back", "ai", user=ctx.audit_user(), resource_type="ai_prompt",
                 resource_id=prompt_id, details={"prompt_key": doc.get("prompt_key"),
                                                 "new_version": doc.get("version")}, **_meta(request))
    return {"success": True, "prompt": _clean(doc)}


@router.get("/ai/models")
async def ai_models(ctx: TenantContext = Depends(SUPER)):
    from app.pipeline.ai_models_service import list_models
    return {"success": True, "models": _clean(list_models())}


class ModelPatch(BaseModel):
    display_name: Optional[str] = None
    purpose: Optional[str] = None
    is_enabled: Optional[bool] = None
    is_default: Optional[bool] = None
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    cost_input_per_1k: Optional[float] = None
    cost_output_per_1k: Optional[float] = None
    context_limit: Optional[int] = None


@router.patch("/ai/models/{model_id}")
async def ai_model_update(model_id: str, body: ModelPatch, request: Request,
                          ctx: TenantContext = Depends(SUPER)):
    from app.pipeline.ai_models_service import update_model
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=422, detail="No changes")
    if "temperature" in updates and not 0 <= updates["temperature"] <= 2:
        raise HTTPException(status_code=422, detail="temperature must be 0-2")
    for k in ("max_tokens", "context_limit", "cost_input_per_1k", "cost_output_per_1k"):
        if k in updates and updates[k] < 0:
            raise HTTPException(status_code=422, detail=f"{k} cannot be negative")
    if updates.get("is_default") and updates.get("is_enabled") is False:
        raise HTTPException(status_code=422, detail="The default model must be enabled")
    db = _db()
    oid = _oid(model_id)
    before = await db.ai_models.find_one({"_id": oid}) if oid else None
    if not before:
        raise HTTPException(status_code=404, detail="Model not found")
    doc = update_model(model_id, updates)
    await aaudit("ai.model_updated", "ai", user=ctx.audit_user(), resource_type="ai_model",
                 resource_id=model_id,
                 details={"before": _clean({k: before.get(k) for k in updates}), "after": updates},
                 **_meta(request))
    return {"success": True, "model": _clean(doc)}


# ── Reports / exports ──────────────────────────────────────────────────────

_CSV_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return _clean(value)
    if isinstance(value, (dict, list)):
        value = json.dumps(_clean(redact(value) if isinstance(value, dict) else value),
                           default=str)[:2000]
    s = str(value)
    if s.startswith(_CSV_DANGEROUS):
        s = "'" + s
    return s


def _csv(columns: List[str], rows: Iterable[List[Any]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(columns)
    for r in rows:
        w.writerow([_cell(v) for v in r])
    return buf.getvalue()


REPORT_KINDS = {
    "organizations": "Organizations", "users": "Users", "admins": "Admins",
    "subscriptions": "Subscriptions", "payments": "Payments", "usage": "Token usage",
    "searches": "Searches", "leads": "Leads (contact data masked)", "apify-jobs": "Apify jobs",
    "ai-usage": "AI usage", "audit-logs": "Audit logs",
}


@router.get("/reports")
async def reports_catalog(ctx: TenantContext = Depends(SUPER)):
    db = _db()
    recent = [_clean(a) async for a in db.audit_logs.find(
        {"action": {"$regex": r"^report\.exported"}}).sort("at", -1).limit(25)]
    return {"success": True, "kinds": [{"key": k, "label": v} for k, v in REPORT_KINDS.items()],
            "recent": recent, "max_rows": MAX_EXPORT_ROWS}


def _range_q(field: str, start: Optional[datetime], end: Optional[datetime]) -> Dict[str, Any]:
    rng: Dict[str, Any] = {}
    if start:
        rng["$gte"] = start
    if end:
        rng["$lt"] = end
    return {field: rng} if rng else {}


@router.get("/reports/{kind}.csv")
async def export_report(kind: str, request: Request, organization_id: Optional[str] = None,
                        from_: Optional[str] = Query(None, alias="from"), to: Optional[str] = None,
                        status: Optional[str] = None, category: Optional[str] = None,
                        action: Optional[str] = None, ctx: TenantContext = Depends(SUPER)):
    if kind not in REPORT_KINDS:
        raise HTTPException(status_code=404, detail="Unknown report")
    db = _db()
    start = _parse_day(from_, "from")
    end = _parse_day(to, "to")
    end = end + timedelta(days=1) if end else None
    org = {"organization_id": organization_id} if organization_id else {}
    cap = MAX_EXPORT_ROWS
    columns: List[str]
    rows: List[List[Any]] = []

    if kind == "organizations":
        q: Dict[str, Any] = {**_range_q("created_at", start, end)}
        if organization_id and _oid(organization_id):
            q["_id"] = _oid(organization_id)
        if status:
            q["status"] = status
        columns = ["id", "name", "slug", "status", "plan_id", "active_members", "created_at"]
        async for o in db.organizations.find(q).sort("created_at", -1).limit(cap):
            n = await _count(db.organization_members, {"organization_id": str(o["_id"]), "status": "active"})
            rows.append([str(o["_id"]), o.get("name"), o.get("slug"), o.get("status"), o.get("plan_id"),
                         n, o.get("created_at")])
    elif kind in ("users", "admins"):
        if kind == "admins":
            columns = ["user_id", "email", "name", "role", "member_status", "user_status",
                       "organization_id", "organization_name", "last_login"]
            mq: Dict[str, Any] = {"role": {"$in": ["owner", "admin"]}, "status": {"$ne": "removed"}, **org}
            members = [m async for m in db.organization_members.find(mq).limit(cap)]
            users = await _user_map(db, [m.get("user_id") for m in members])
            names = await _org_names(db, [m.get("organization_id") for m in members])
            for m in members:
                u = users.get(str(m.get("user_id")), {})
                rows.append([m.get("user_id"), u.get("email"), u.get("name"), m.get("role"), m.get("status"),
                             u.get("status"), m.get("organization_id"),
                             names.get(str(m.get("organization_id"))), u.get("last_login")])
        else:
            columns = ["id", "email", "name", "status", "platform_role", "organizations", "last_login", "created_at"]
            uq: Dict[str, Any] = {**_range_q("created_at", start, end)}
            if status:
                uq["status"] = status
            if organization_id:
                ids = [_oid(m["user_id"]) async for m in db.organization_members.find(
                    {"organization_id": organization_id}, {"user_id": 1})]
                uq["_id"] = {"$in": [i for i in ids if i]}
            async for u in db.users.find(uq, {"password_hash": 0}).sort("created_at", -1).limit(cap):
                mems = [m async for m in db.organization_members.find(
                    {"user_id": str(u["_id"]), "status": {"$ne": "removed"}}, {"organization_id": 1, "role": 1})]
                names = await _org_names(db, [m.get("organization_id") for m in mems])
                orgs_txt = "; ".join(f"{names.get(str(m.get('organization_id')), m.get('organization_id'))} "
                                     f"({m.get('role')})" for m in mems)
                rows.append([str(u["_id"]), u.get("email"), u.get("name"), u.get("status", "active"),
                             u.get("platform_role") if u.get("is_platform_admin") else "",
                             orgs_txt, u.get("last_login"), u.get("created_at")])
    elif kind == "subscriptions":
        columns = ["id", "organization_id", "organization_name", "plan_id", "status", "billing_cycle",
                   "amount", "currency", "started_at", "current_period_end", "created_at"]
        q = {**org, **_range_q("created_at", start, end)}
        if status:
            q["status"] = status
        docs = [d async for d in db.subscriptions.find(q).sort("created_at", -1).limit(cap)]
        names = await _org_names(db, [d.get("organization_id") for d in docs])
        for d in docs:
            rows.append([str(d["_id"]), d.get("organization_id"), names.get(str(d.get("organization_id"))),
                         d.get("plan_id"), d.get("status"), d.get("billing_cycle"), d.get("amount"),
                         d.get("currency"), d.get("started_at"), d.get("current_period_end"), d.get("created_at")])
    elif kind == "payments":
        columns = ["id", "organization_id", "organization_name", "subscription_id", "amount", "amount_paid",
                   "currency", "status", "provider", "verified_via", "refund_required", "created_at"]
        q = {**org, **_range_q("created_at", start, end)}
        if status:
            q["status"] = status
        docs = [d async for d in db.payments.find(q).sort("created_at", -1).limit(cap)]
        names = await _org_names(db, [d.get("organization_id") for d in docs])
        for d in docs:
            rows.append([str(d["_id"]), d.get("organization_id"), names.get(str(d.get("organization_id"))),
                         d.get("subscription_id"), d.get("amount"), d.get("amount_paid"), d.get("currency"),
                         d.get("status"), d.get("provider"), d.get("verified_via"),
                         bool(d.get("refund_required")), d.get("created_at")])
    elif kind == "usage":
        columns = ["organization_id", "organization_name", "allocated", "used", "remaining",
                   "percentage_used", "source", "expires_at"]
        docs = [d async for d in db.token_balances.find(org).limit(cap)]
        names = await _org_names(db, [d.get("organization_id") for d in docs])
        for d in docs:
            alloc, used = int(d.get("allocated") or 0), int(d.get("used") or 0)
            rows.append([d.get("organization_id"), names.get(str(d.get("organization_id"))), alloc, used,
                         d.get("remaining"), round(used * 100 / alloc, 1) if alloc else "", d.get("source"),
                         d.get("expires_at")])
    elif kind in ("searches", "apify-jobs"):
        columns = ["run_id", "organization_id", "organization_name", "user_email", "query", "platform",
                   "status", "phase", "error", "apify_run_id", "actor_id", "usage_usd", "created_at",
                   "completed_at"]
        q = _search_query(organization_id, None, status, None, None, start, end, kind == "apify-jobs")
        docs = [d async for d in db.search_history.find(q).sort("created_at", -1).limit(cap)]
        names = await _org_names(db, [d.get("organization_id") for d in docs])
        for d in docs:
            r = _search_row(d, names)
            rows.append([r["run_id"], r["organization_id"], r["organization_name"], d.get("created_by"),
                         r["query"], r["platform"], r["status"], r["phase"], r["error"], r["apify_run_id"],
                         r["actor_id"], r["usage_usd"], d.get("created_at"), d.get("completed_at")])
    elif kind == "leads":
        columns = ["id", "organization_id", "organization_name", "platform", "commenter_name", "phone_masked",
                   "email_masked", "lead_quality", "lead_score", "search_run_id", "comment_excerpt", "created_at"]
        q = {"is_lead": True, **org, **_range_q("created_at", start, end)}
        docs = [d async for d in db.ai_comments.find(q).sort("created_at", -1).limit(cap)]
        names = await _org_names(db, [d.get("organization_id") for d in docs])
        for d in docs:
            rows.append([str(d["_id"]), d.get("organization_id"), names.get(str(d.get("organization_id"))),
                         d.get("platform"), d.get("commenter_name"), mask_phone(d.get("phone")),
                         mask_email(d.get("email")), d.get("lead_quality"), d.get("lead_score"),
                         d.get("search_run_id"), (d.get("comment_text") or "")[:200], d.get("created_at")])
    elif kind == "ai-usage":
        columns = ["created_at", "organization_id", "user_id", "provider", "model", "prompt_key",
                   "prompt_version", "status", "tokens_in", "tokens_out", "latency_ms", "estimated_cost",
                   "error_type"]
        q = {**org, **_range_q("created_at", start, end)}
        async for d in db.ai_requests.find(q).sort("created_at", -1).limit(cap):
            rows.append([d.get("created_at"), d.get("organization_id"), d.get("user_id"), d.get("provider"),
                         d.get("model"), d.get("prompt_key"), d.get("prompt_version"), d.get("status"),
                         d.get("tokens_in"), d.get("tokens_out"), d.get("latency_ms"), d.get("estimated_cost"),
                         d.get("error_type")])
    else:  # audit-logs
        columns = ["at", "actor_email", "actor_role", "organization_id", "action", "category",
                   "resource_type", "resource_id", "status", "ip", "details"]
        q = {**org, **_range_q("at", start, end)}
        if category:
            q["category"] = category
        if action:
            q["action"] = {"$regex": "^" + re.escape(action)}
        if status in ("success", "failure"):
            q["status"] = status
        async for d in db.audit_logs.find(q).sort("at", -1).limit(cap):
            rows.append([d.get("at"), d.get("actor_email") or d.get("user"), d.get("actor_role") or d.get("role"),
                         d.get("organization_id"), d.get("action"), d.get("category"), d.get("resource_type"),
                         d.get("resource_id"), d.get("status"), d.get("ip"), redact(d.get("details") or {})])

    body = _csv(columns, rows)
    await aaudit(f"report.exported.{kind}", "exports", user=ctx.audit_user(),
                 organization_id=organization_id, resource_type="report", resource_id=kind,
                 details={"rows": len(rows), "filters": {"organization_id": organization_id, "from": from_,
                                                         "to": to, "status": status, "category": category,
                                                         "action": action}}, **_meta(request))
    stamp = utcnow().strftime("%Y%m%d-%H%M")
    return Response(content=body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="leadai-{kind}-{stamp}.csv"',
                             "Cache-Control": "no-store"})


class ClientExportLog(BaseModel):
    table: str
    rows: int = 0
    columns: List[str] = []
    filters: Dict[str, Any] = {}


def _short(v: Any, n: int = 200) -> Any:
    if isinstance(v, (int, float, bool)) or v is None:
        return v
    return str(v)[:n]


@router.post("/exports/client-log")
async def log_client_export(body: ClientExportLog, request: Request, ctx: TenantContext = Depends(SUPER)):
    """Audit a "Current view" CSV that the browser built itself (the rows
    never pass through the server, so the download is recorded here)."""
    table = re.sub(r"[^A-Za-z0-9_.:/ -]", "", (body.table or "").strip())[:80]
    if not table:
        raise HTTPException(status_code=422, detail="table is required")
    if body.rows < 0 or body.rows > 1_000_000:
        raise HTTPException(status_code=422, detail="rows out of range")
    if len(body.columns) > 200 or len(body.filters) > 50:
        raise HTTPException(status_code=422, detail="Too many columns or filters")
    columns = [_short(c, 80) for c in body.columns]
    filters = {str(k)[:50]: _short(v) for k, v in body.filters.items()
               if isinstance(v, (str, int, float, bool)) or v is None}
    await aaudit("export.client", "exports", user=ctx.audit_user(), resource_type="client_export",
                 resource_id=table, details={"table": table, "rows": body.rows, "columns": columns,
                                             "filters": redact(filters), "source": "current_view"},
                 **_meta(request))
    return {"success": True}


# ── Support tickets (created by org admins in /org-admin) ──────────────────

TICKET_STATUSES = ("open", "waiting", "resolved", "closed")


def _ticket(t: Dict[str, Any], full: bool = False) -> Dict[str, Any]:
    msgs = t.get("messages") or []
    row = {"id": str(t["_id"]), "number": t.get("number"), "subject": t.get("subject"),
           "category": t.get("category"), "priority": t.get("priority"), "status": t.get("status"),
           "organization_id": t.get("organization_id"), "organization_name": t.get("organization_name"),
           "created_by": t.get("created_by"), "created_at": _clean(t.get("created_at")),
           "updated_at": _clean(t.get("updated_at")), "messages_count": len(msgs),
           "last_reply_from_staff": bool(msgs and msgs[-1].get("from_staff")),
           "awaiting_staff": bool(msgs and not msgs[-1].get("from_staff")
                                  and t.get("status") in ("open", "waiting"))}
    if full:
        row["messages"] = [{"author": m.get("author_name") or m.get("author_email"),
                            "author_email": m.get("author_email"), "from_staff": bool(m.get("from_staff")),
                            "body": m.get("body"), "at": _clean(m.get("at"))} for m in msgs]
        row["status_history"] = _clean(t.get("status_history") or [])
    return row


async def _ticket_or_404(db, ticket_id: str) -> Dict[str, Any]:
    oid = _oid(ticket_id)
    t = await db.support_tickets.find_one({"_id": oid}) if oid else None
    if not t:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return t


@router.get("/support/tickets")
async def support_tickets(organization_id: Optional[str] = None, status: Optional[str] = None,
                          priority: Optional[str] = None, q: Optional[str] = None,
                          page: int = Query(1, ge=1), limit: int = Query(25, ge=1, le=200),
                          sort: str = "-updated_at", ctx: TenantContext = Depends(SUPER)):
    db = _db()
    query: Dict[str, Any] = {}
    if organization_id:
        query["organization_id"] = organization_id
    if status == "active":
        query["status"] = {"$in": ["open", "waiting"]}
    elif status:
        query["status"] = status
    if priority:
        query["priority"] = priority
    rx = _rx(q)
    if rx:
        ors: List[Dict[str, Any]] = [{"subject": rx}, {"organization_name": rx}, {"created_by": rx}]
        num = (q or "").strip().lstrip("#")
        if num.isdigit():
            ors.append({"number": int(num)})
        query["$or"] = ors
    field, direction = _sort(sort, ("updated_at", "created_at", "number", "priority"), "-updated_at")
    total = await _count(db.support_tickets, query)
    items = [_ticket(t) async for t in db.support_tickets.find(query)
             .sort(field, direction).skip((page - 1) * limit).limit(limit)]
    counts = {}
    for row in await _agg(db.support_tickets, [{"$group": {"_id": "$status", "n": {"$sum": 1}}}]):
        counts[str(row["_id"])] = row["n"]
    return {"success": True, "items": items, "total": total, "page": page, "limit": limit,
            "pages": _pages(total, limit), "counts": counts}


@router.get("/support/tickets/{ticket_id}")
async def support_ticket_detail(ticket_id: str, ctx: TenantContext = Depends(SUPER)):
    db = _db()
    return {"success": True, "ticket": _ticket(await _ticket_or_404(db, ticket_id), full=True)}


class TicketReplyBody(BaseModel):
    message: str
    status: Optional[str] = None   # optionally move the ticket at the same time


@router.post("/support/tickets/{ticket_id}/messages")
async def support_ticket_reply(ticket_id: str, body: TicketReplyBody, request: Request,
                               ctx: TenantContext = Depends(SUPER)):
    """Staff reply (from_staff: true). Defaults the ticket to 'waiting'
    (awaiting the customer); org owners/admins are notified."""
    db = _db()
    t = await _ticket_or_404(db, ticket_id)
    text = (body.message or "").strip()[:5000]
    if not text:
        raise HTTPException(status_code=422, detail="Message is required")
    new_status = body.status or ("waiting" if t.get("status") in ("open", "waiting", None) else t.get("status"))
    if new_status not in TICKET_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {', '.join(TICKET_STATUSES)}")
    now = utcnow()
    push: Dict[str, Any] = {"messages": {"author_id": ctx.user_id, "author_email": ctx.email,
                                         "author_name": "LeadAI Support", "from_staff": True,
                                         "body": text, "at": now}}
    if new_status != t.get("status"):
        push["status_history"] = {"from": t.get("status"), "to": new_status, "at": now, "by": ctx.email}
    await db.support_tickets.update_one({"_id": t["_id"]}, {
        "$push": push,
        "$set": {"updated_at": now, "status": new_status, "last_staff_reply_at": now}})
    from app.events.notifications import notify_org_admins
    notify_org_admins(t.get("organization_id"), "support_reply",
                      f"Support replied to ticket #{t.get('number')}", (t.get("subject") or "")[:200],
                      severity="info", link="/org-admin#support", data={"ticket_id": str(t["_id"])})
    await aaudit("support.staff_replied", "support", user=ctx.audit_user(),
                 organization_id=t.get("organization_id"), resource_type="support_ticket",
                 resource_id=str(t["_id"]),
                 details={"number": t.get("number"), "status_before": t.get("status"),
                          "status_after": new_status, "length": len(text)}, **_meta(request))
    return {"success": True, "ticket": _ticket(await db.support_tickets.find_one({"_id": t["_id"]}), full=True)}


class TicketStatusBody(BaseModel):
    status: str
    reason: str = ""


@router.post("/support/tickets/{ticket_id}/status")
async def support_ticket_status(ticket_id: str, body: TicketStatusBody, request: Request,
                                ctx: TenantContext = Depends(SUPER)):
    if body.status not in TICKET_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {', '.join(TICKET_STATUSES)}")
    db = _db()
    t = await _ticket_or_404(db, ticket_id)
    if t.get("status") == body.status:
        return {"success": True, "ticket": _ticket(t, full=True)}
    now = utcnow()
    await db.support_tickets.update_one({"_id": t["_id"]}, {
        "$set": {"status": body.status, "updated_at": now},
        "$push": {"status_history": {"from": t.get("status"), "to": body.status, "at": now,
                                     "by": ctx.email, "note": body.reason[:300]}}})
    from app.events.notifications import notify_org_admins
    notify_org_admins(t.get("organization_id"), "support_status",
                      f"Ticket #{t.get('number')} is now {body.status}", (t.get("subject") or "")[:200],
                      severity="success" if body.status in ("resolved", "closed") else "info",
                      link="/org-admin#support", data={"ticket_id": str(t["_id"])})
    await aaudit("support.status_changed", "support", user=ctx.audit_user(),
                 organization_id=t.get("organization_id"), resource_type="support_ticket",
                 resource_id=str(t["_id"]),
                 details={"before": t.get("status"), "after": body.status, "reason": body.reason[:300]},
                 **_meta(request))
    return {"success": True, "ticket": _ticket(await db.support_tickets.find_one({"_id": t["_id"]}), full=True)}
