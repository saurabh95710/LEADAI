"""Partner Portal API — /api/partner/v1

Session (``scope="partner"``) for the portal; read-only partner API keys
(``X-API-Key: lap_live_...``) for GET endpoints when the partner holds
``api.access``. Every query is filtered by the caller's own ``partner_id``;
IDs from the URL are always matched together with it, so another partner's
records answer 404 (and log a security event).

  GET   /me                         partner or applicant status
  GET   /application | PUT          applicant: view / resubmit (changes requested)
  GET   /dashboard                  KPIs + recent activity (?from&to)
  GET   /profile | PATCH            profile (payout/tax masked)
  PUT   /profile/payout             payout + tax details (audited, notified)
  GET   /referral-links             main link + campaign links
  GET   /referrals                  attributed signups (paged, stage/q/from/to)
  GET   /customers | POST           customers (resellers onboard via the demo flow)
  GET   /customers/{org_id}         reseller customer detail
  POST  /customers/{org_id}/resend-setup
  GET   /commissions                commissions (paged, status/from/to)
  GET   /wallet                     balances per currency + ledger
  GET   /payouts | POST             payouts / request payout of available balance
  POST  /payouts/{id}/cancel
  GET   /analytics                  time series, campaigns, sources, funnel
  GET   /campaigns | POST | PATCH /campaigns/{id}
  GET   /coupons | POST | PATCH /coupons/{id}
  GET   /marketing-assets
  GET   /notifications | POST /notifications/read
  GET   /api-keys | POST | DELETE /api-keys/{key_id}
"""
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.admin.audit import request_meta
from app.partners import constants as K
from app.partners import commissions as C
from app.partners import coupons as CP
from app.partners import referrals as R
from app.partners import service as S
from app.partners import stats as ST
from app.partners.auth import (
    PartnerContext, create_partner_api_key, get_partner_session, list_partner_api_keys,
    load_partner_doc, require_partner_permission, revoke_partner_api_key,
)

router = APIRouter(prefix="/api/partner/v1", tags=["partner-portal"])


def _ip(request: Request) -> Optional[str]:
    return request_meta(request).get("ip")


def _page(coll, query: Dict[str, Any], *, page: int, limit: int, sort: str, out) -> Dict[str, Any]:
    field = sort.lstrip("-") or "created_at"
    direction = -1 if sort.startswith("-") else 1
    total = coll.count_documents(query)
    items = [out(d) for d in coll.find(query).sort(field, direction).skip((page - 1) * limit).limit(limit)]
    return {"items": items, "total": total, "page": page, "limit": limit,
            "pages": max(1, -(-total // limit))}


def _date_filter(field: str, date_from: Optional[str], date_to: Optional[str]) -> Dict[str, Any]:
    if not date_from and not date_to:
        return {}
    start, end = ST.parse_range(date_from, date_to, default_days=3650)
    return {field: {"$gte": start, "$lte": end}}


def _sort(value: str, allowed: tuple, default: str) -> str:
    return value if value.lstrip("-") in allowed else default


def _probe(request: Request, ctx: PartnerContext, coll: str, query: Dict[str, Any]) -> None:
    """A record that exists for ANOTHER partner = cross-partner probe."""
    db = S.db_or_503()
    other = db[coll].find_one(query, {"_id": 1, "partner_id": 1})
    if other and str(other.get("partner_id")) != ctx.partner_id:
        from app.events.security import security_event_from_request
        security_event_from_request(request, "cross_partner_access", "high",
                                    details={"collection": coll, "resource_id": str(other["_id"]),
                                             "partner_id": ctx.partner_id, "actor": ctx.email})
        from app.partners.fraud import raise_flag
        raise_flag("cross_partner_access", partner_id=ctx.partner_id, severity="high", subject_type="partner",
                   subject_id=ctx.partner_id, details={"collection": coll, "resource_id": str(other["_id"]),
                                                       "owner_partner_id": str(other.get("partner_id"))})


@router.get("")
def api_index(ctx: PartnerContext = Depends(require_partner_permission(None))):
    """What this caller can use (session: everything permitted; API key: read-only)."""
    endpoints = {
        "GET /me": None, "GET /dashboard": K.DASHBOARD_VIEW, "GET /profile": K.PROFILE_MANAGE,
        "GET /referral-links": K.LINKS_CREATE, "GET /referrals": K.REFERRALS_VIEW,
        "GET /referrals/{id}": K.REFERRALS_VIEW, "GET /customers": K.CUSTOMERS_VIEW,
        "GET /customers/{organization_id}": K.CUSTOMERS_VIEW, "GET /commissions": K.COMMISSIONS_VIEW,
        "GET /wallet": K.WALLET_VIEW, "GET /payouts": K.WALLET_VIEW, "GET /analytics": K.ANALYTICS_VIEW,
        "GET /analytics.csv": K.ANALYTICS_VIEW, "GET /campaigns": K.CAMPAIGNS_MANAGE,
        "GET /coupons": K.COUPONS_MANAGE, "GET /marketing-assets": K.MARKETING_ACCESS,
        "GET /sales": K.SALES_VIEW, "GET /deals": K.DEALS_MANAGE,
        "GET /tasks": None, "GET /tasks/{id}": None, "GET /notifications": None,
    }
    return {"success": True, "base": "/api/partner/v1", "auth": "api_key" if ctx.via_api_key else "session",
            "read_only": ctx.via_api_key, "rate_limit_per_minute": 60 if ctx.via_api_key else None,
            "endpoints": sorted(e for e, perm in endpoints.items() if perm is None or perm in ctx.permissions)}


# ── identity / application ───────────────────────────────────────────────────

@router.get("/me")
def me(ctx: PartnerContext = Depends(get_partner_session)):
    db = S.db_or_503()
    out: Dict[str, Any] = {"user_id": ctx.user_id, "email": ctx.email, "name": ctx.name,
                           "application_status": ctx.application_status,
                           "partner_status": ctx.status, "partner_type": ctx.partner_type,
                           "permissions": ctx.permissions, "is_active_partner": ctx.is_active_partner,
                           "impersonated_by": ctx.impersonated_by,
                           "permission_labels": K.PERMISSION_LABELS}
    if ctx.partner_id:
        p = db[K.PARTNERS].find_one({"_id": S.oid(ctx.partner_id)})
        out["partner"] = S.public_partner(p)
        tier = db[K.TIERS].find_one({"_id": S.oid(p.get("tier_id"))}) if p.get("tier_id") else None
        out["tier"] = S.clean(tier) if tier else None
        # the next tier the partner can earn, with their progress (auto tiers only)
        from app.partners.tiers import partner_metrics
        nxt = None
        for t in db[K.TIERS].find({"status": "active", "auto_assign": {"$ne": False},
                                   "order": {"$gt": int((tier or {}).get("order") or 0)}}).sort("order", 1):
            req = t.get("requirements") or {}
            if any(float(req.get(k) or 0) for k in ("min_customers", "min_referrals", "min_revenue")):
                nxt = {"name": t["name"], "requirements": req, "benefits": t.get("benefits") or [],
                       "progress": partner_metrics(db, ctx.partner_id, int(req.get("period_days") or 0))}
                break
        out["next_tier"] = nxt
        from app.partners.tasks import open_count
        out["open_tasks"] = open_count(db, ctx.partner_id)
    settings = S.get_program_settings(db)
    out["program"] = {k: settings[k] for k in ("min_payout", "payout_methods", "attribution_model",
                                               "attribution_window_days", "commission_hold_days",
                                               "partner_coupons_enabled", "payout_schedule", "payout_day",
                                               "max_partner_coupon_percent")}
    return {"success": True, "me": out}


@router.get("/application")
def my_application(ctx: PartnerContext = Depends(get_partner_session)):
    db = S.db_or_503()
    doc = db[K.APPLICATIONS].find_one({"user_id": ctx.user_id}, sort=[("created_at", -1)])
    if not doc:
        raise HTTPException(status_code=404, detail="Application not found")
    return {"success": True, "application": S.public_application(doc)}


class ApplicationUpdate(BaseModel):
    name: Optional[str] = None
    company: Optional[str] = None
    phone: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    website: Optional[str] = None
    business_type: Optional[str] = None
    partner_type: Optional[str] = None
    experience: Optional[str] = None
    promotion_plan: Optional[str] = None
    social_profiles: Optional[Dict[str, str]] = None
    tax_info: Optional[Dict[str, Any]] = None
    payout_info: Optional[Dict[str, Any]] = None


@router.put("/application")
def resubmit_application(body: ApplicationUpdate, request: Request,
                         ctx: PartnerContext = Depends(get_partner_session)):
    if ctx.partner_id:
        raise HTTPException(status_code=409, detail="You are already an approved partner")
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    return {"success": True, "application": S.update_own_application(ctx.user_id, data, ip=_ip(request))}


# ── dashboard / profile ──────────────────────────────────────────────────────

@router.get("/dashboard")
def dashboard(date_from: Optional[str] = Query(None, alias="from"),
              date_to: Optional[str] = Query(None, alias="to"),
              ctx: PartnerContext = Depends(require_partner_permission(K.DASHBOARD_VIEW))):
    db = S.db_or_503()
    start, end = ST.parse_range(date_from, date_to)
    partner = load_partner_doc(ctx)
    data = ST.dashboard(db, partner, start, end)
    if ctx.via_api_key:
        data["recent_notifications"] = []
    else:
        from app.events.notifications import audience_query
        q = audience_query(is_super_admin=False, user_id=ctx.user_id, organization_id=None, org_role=None)
        data["recent_notifications"] = [S.clean(n) for n in db.notifications.find(q)
                                        .sort("created_at", -1).limit(5)]
        for n in data["recent_notifications"]:
            n["read"] = ctx.user_id in (n.pop("read_by", None) or [])
    return {"success": True, "dashboard": data}


@router.get("/profile")
def get_profile(ctx: PartnerContext = Depends(require_partner_permission(K.PROFILE_MANAGE))):
    return {"success": True, "partner": S.public_partner(load_partner_doc(ctx))}


class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    company: Optional[str] = None
    phone: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    website: Optional[str] = None
    business_type: Optional[str] = None
    social_profiles: Optional[Dict[str, str]] = None
    settings: Optional[Dict[str, Any]] = None


@router.patch("/profile")
def update_profile(body: ProfileUpdate, request: Request,
                   ctx: PartnerContext = Depends(require_partner_permission(K.PROFILE_MANAGE, write=True))):
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    return {"success": True, "partner": S.update_own_profile(load_partner_doc(ctx), data, ip=_ip(request))}


class PayoutUpdate(BaseModel):
    payout_info: Dict[str, Any]
    tax_info: Optional[Dict[str, Any]] = None


@router.put("/profile/payout")
def update_payout(body: PayoutUpdate, request: Request,
                  ctx: PartnerContext = Depends(require_partner_permission(K.PROFILE_MANAGE, write=True))):
    return {"success": True, "partner": S.update_own_payout(load_partner_doc(ctx), body.payout_info,
                                                            body.tax_info, ip=_ip(request))}


# ── referral links / referrals / customers ───────────────────────────────────

@router.get("/referral-links")
def referral_links(ctx: PartnerContext = Depends(require_partner_permission(K.LINKS_CREATE))):
    db = S.db_or_503()
    p = load_partner_doc(ctx)
    campaigns = [R.campaign_out(c, p) for c in db[K.CAMPAIGNS].find({"partner_id": ctx.partner_id,
                                                                     "status": "active"})
                 .sort("created_at", -1)]
    from app.events.email import absolute_url
    return {"success": True, "links": {
        "partner_code": p["partner_code"], "referral_code": p["referral_code"],
        "referral_url": S.referral_url(p["referral_code"]),
        "signup_url": absolute_url(f"/request-demo?ref={p['referral_code']}"),
        "campaigns": campaigns}}


@router.get("/referrals")
def list_referrals(page: int = Query(1, ge=1), limit: int = Query(25, ge=1, le=100),
                   stage: Optional[str] = None, q: Optional[str] = None,
                   campaign_id: Optional[str] = None, sort: str = "-signed_up_at",
                   date_from: Optional[str] = Query(None, alias="from"),
                   date_to: Optional[str] = Query(None, alias="to"),
                   ctx: PartnerContext = Depends(require_partner_permission(K.REFERRALS_VIEW))):
    db = S.db_or_503()
    query: Dict[str, Any] = {"partner_id": ctx.partner_id, **_date_filter("signed_up_at", date_from, date_to)}
    if stage:
        if stage not in K.STAGES:
            raise HTTPException(status_code=422, detail="Unknown stage")
        query["stage"] = stage
    if campaign_id:
        query["campaign_id"] = campaign_id
    if q:
        query["company"] = {"$regex": re.escape(q.strip()[:80]), "$options": "i"}
    res = _page(db[K.REFERRALS], query, page=page, limit=limit,
                sort=_sort(sort, ("signed_up_at", "converted_at", "company", "revenue_total"), "-signed_up_at"),
                out=ST.referral_out)
    counts = {s: db[K.REFERRALS].count_documents({"partner_id": ctx.partner_id, "stage": s}) for s in K.STAGES}
    return {"success": True, **res, "counts": counts}


@router.get("/referrals/{referral_id}")
def referral_detail(referral_id: str, request: Request,
                    ctx: PartnerContext = Depends(require_partner_permission(K.REFERRALS_VIEW))):
    """One referral with its conversion history and the commissions it earned."""
    db = S.db_or_503()
    ref = db[K.REFERRALS].find_one({"_id": S.oid(referral_id), "partner_id": ctx.partner_id}) if S.oid(referral_id) else None
    if not ref:
        _probe(request, ctx, K.REFERRALS, {"_id": S.oid(referral_id)})
        raise HTTPException(status_code=404, detail="Referral not found")
    out = ST.referral_out(ref)
    out["events"] = [S.clean({k: v for k, v in e.items() if k in ("stage", "at", "amount", "reason")})
                     for e in ref.get("events") or []]
    if K.COMMISSIONS_VIEW in ctx.permissions:
        out["commissions"] = [ST.commission_out(c) for c in db[K.COMMISSIONS].find(
            {"partner_id": ctx.partner_id, "referral_id": str(ref["_id"])}).sort("created_at", -1).limit(100)]
    return {"success": True, "referral": out}


def _customer_detail(db, ref: Dict[str, Any]) -> Dict[str, Any]:
    org = db.organizations.find_one({"_id": S.oid(ref["organization_id"])}) or {}
    sub = db.subscriptions.find_one({"organization_id": ref["organization_id"]}, sort=[("created_at", -1)]) or {}
    out = ST.referral_out(ref)
    out.update({
        "organization": {"name": org.get("name"), "status": org.get("status"),
                         "plan_id": org.get("plan_id"),
                         "demo_expires_at": S.clean((org.get("demo") or {}).get("expires_at")),
                         "created_at": S.clean(org.get("created_at"))},
        "subscription": {"status": sub.get("status"), "plan_id": sub.get("plan_id"),
                         "billing_cycle": sub.get("billing_cycle"),
                         "current_period_end": S.clean(sub.get("current_period_end"))} if sub else None,
        "members": db.organization_members.count_documents({"organization_id": ref["organization_id"],
                                                            "status": "active"}),
    })
    return out


@router.get("/customers")
def list_customers(page: int = Query(1, ge=1), limit: int = Query(25, ge=1, le=100),
                   q: Optional[str] = None, managed: Optional[bool] = None,
                   stage: Optional[str] = None, sort: str = "-signed_up_at",
                   ctx: PartnerContext = Depends(require_partner_permission(K.CUSTOMERS_VIEW))):
    db = S.db_or_503()
    query: Dict[str, Any] = {"partner_id": ctx.partner_id}
    if stage in K.STAGES:
        query["stage"] = stage
    if managed is not None:
        query["managed"] = managed
    if q:
        query["company"] = {"$regex": re.escape(q.strip()[:80]), "$options": "i"}
    res = _page(db[K.REFERRALS], query, page=page, limit=limit,
                sort=_sort(sort, ("signed_up_at", "converted_at", "company", "revenue_total"), "-signed_up_at"),
                out=lambda r: _customer_detail(db, r))
    return {"success": True, **res}


class CustomerCreate(BaseModel):
    company: str
    name: str
    email: str
    phone: Optional[str] = None
    industry: Optional[str] = None
    plan: Optional[str] = None
    notes: Optional[str] = None


@router.post("/customers")
def create_customer(body: CustomerCreate, request: Request,
                    ctx: PartnerContext = Depends(require_partner_permission(K.CUSTOMERS_CREATE, write=True))):
    partner = load_partner_doc(ctx)
    res = R.create_reseller_customer(partner, body.model_dump(), ip=_ip(request))
    return {"success": True, "customer": res,
            "message": ("Customer created and their free trial has started. They'll get an email to set "
                        "their password and sign in." if res.get("status") == "approved" else
                        "Customer created. They'll get an email to set their password; "
                        "the workspace opens once LeadAI approves the demo.")}


@router.get("/customers/{organization_id}")
def get_customer(organization_id: str, request: Request,
                 ctx: PartnerContext = Depends(require_partner_permission(K.CUSTOMERS_VIEW))):
    db = S.db_or_503()
    ref = db[K.REFERRALS].find_one({"organization_id": organization_id, "partner_id": ctx.partner_id})
    if not ref:
        _probe(request, ctx, K.REFERRALS, {"organization_id": organization_id})
        raise HTTPException(status_code=404, detail="Customer not found")
    out = _customer_detail(db, ref)
    if ref.get("managed") and K.RESELLER_MANAGE in ctx.permissions:
        from app.billing import tokens
        try:
            out["tokens"] = tokens.get_balance(organization_id)
        except Exception:
            out["tokens"] = None
        owner = db.users.find_one({"_id": S.oid(ref.get("user_id"))}) if ref.get("user_id") else None
        out["owner_activated"] = bool(owner and owner.get("last_login"))
    out["commissions"] = [ST.commission_out(c) for c in db[K.COMMISSIONS].find(
        {"partner_id": ctx.partner_id, "organization_id": organization_id}).sort("created_at", -1).limit(50)]
    return {"success": True, "customer": out}


@router.post("/customers/{organization_id}/resend-setup")
def resend_setup(organization_id: str,
                 ctx: PartnerContext = Depends(require_partner_permission(K.RESELLER_MANAGE, write=True))):
    R.resend_setup_link(load_partner_doc(ctx), organization_id)
    return {"success": True, "message": "Setup email sent."}


# ── sell LeadAI: sales kit + deal registration ───────────────────────────────

@router.get("/sales")
def sales_kit(ctx: PartnerContext = Depends(require_partner_permission(K.SALES_VIEW))):
    """Live plan catalog with this partner's customer prices, commission per
    plan, plan-specific share links, active coupons and the product pitch."""
    from app.partners.sales import catalog
    return {"success": True, "sales": catalog(S.db_or_503(), load_partner_doc(ctx))}


@router.get("/deals")
def list_deals(status: Optional[str] = None, q: Optional[str] = None, page: int = Query(1, ge=1),
               limit: int = Query(25, ge=1, le=100),
               ctx: PartnerContext = Depends(require_partner_permission(K.DEALS_MANAGE))):
    from app.partners import sales as SL
    db = S.db_or_503()
    query = {**SL.list_query(status, q), "partner_id": ctx.partner_id}

    def out(d):
        row = SL.deal_out(d)
        row["referral_stage"] = SL.deal_stage(db, d)
        return row
    res = _page(db[SL.DEALS], query, page=page, limit=limit, sort="-created_at", out=out)
    res["counts"] = {s_: db[SL.DEALS].count_documents({"partner_id": ctx.partner_id, "status": s_})
                     for s_ in SL.DEAL_STATUSES}
    return {"success": True, **res}


class DealBody(BaseModel):
    company: Optional[str] = None
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    website: Optional[str] = None
    expected_plan: Optional[str] = None
    expected_value: Optional[float] = None
    currency: Optional[str] = None
    notes: Optional[str] = None
    close_as_lost: bool = False


@router.post("/deals")
def create_deal(body: DealBody, request: Request,
                ctx: PartnerContext = Depends(require_partner_permission(K.DEALS_MANAGE, write=True))):
    from app.partners.sales import register_deal
    return {"success": True, "deal": register_deal(load_partner_doc(ctx), body.model_dump(), ip=_ip(request)),
            "message": "Deal registered. LeadAI reviews it; once approved your claim on this prospect is protected."}


@router.patch("/deals/{deal_id}")
def update_deal(deal_id: str, body: DealBody, request: Request,
                ctx: PartnerContext = Depends(require_partner_permission(K.DEALS_MANAGE, write=True))):
    from app.partners.sales import DEALS, update_own_deal
    data = {k: v for k, v in body.model_dump(exclude_unset=True).items()}
    try:
        return {"success": True, "deal": update_own_deal(load_partner_doc(ctx), deal_id, data, ip=_ip(request))}
    except HTTPException as e:
        if e.status_code == 404:
            _probe(request, ctx, DEALS, {"_id": S.oid(deal_id)})
        raise


@router.post("/deals/{deal_id}/convert")
def convert_deal(deal_id: str, request: Request,
                 ctx: PartnerContext = Depends(require_partner_permission(K.CUSTOMERS_CREATE, write=True))):
    """Reseller: onboard a registered prospect as a customer (normal demo flow)."""
    from app.partners.sales import DEALS
    db = S.db_or_503()
    d = db[DEALS].find_one({"_id": S.oid(deal_id), "partner_id": ctx.partner_id}) if S.oid(deal_id) else None
    if not d:
        _probe(request, ctx, DEALS, {"_id": S.oid(deal_id)})
        raise HTTPException(status_code=404, detail="Deal not found")
    if d["status"] not in ("registered", "approved") or d.get("organization_id"):
        raise HTTPException(status_code=409, detail="This deal is closed or already a customer")
    if not d.get("contact_name"):
        raise HTTPException(status_code=422, detail="Add the contact's name to the deal first")
    res = R.create_reseller_customer(load_partner_doc(ctx), {
        "company": d["company"], "name": d["contact_name"], "email": d["contact_email"],
        "phone": d.get("contact_phone"), "plan": d.get("expected_plan"), "notes": d.get("notes")}, ip=_ip(request))
    from app.db.models import utcnow
    db[DEALS].update_one({"_id": d["_id"]}, {"$set": {"organization_id": res["organization_id"],
                                                      "referral_id": res.get("referral_id"), "updated_at": utcnow()},
                                             "$push": {"history": {"status": d["status"], "at": utcnow(),
                                                                   "by": ctx.email, "note": "converted to customer"}}})
    return {"success": True, "customer": res}


# ── tasks assigned by LeadAI (every active partner) ──────────────────────────

@router.get("/tasks")
def list_tasks(status: Optional[str] = None, q: Optional[str] = None, page: int = Query(1, ge=1),
               limit: int = Query(25, ge=1, le=100),
               ctx: PartnerContext = Depends(require_partner_permission(None))):
    from app.partners import tasks as T
    db = S.db_or_503()
    query = {**T.list_query(status, q), "partner_id": ctx.partner_id}
    res = _page(db[T.TASKS], query, page=page, limit=limit, sort="-created_at", out=T.task_out)
    res["counts"] = {s_: db[T.TASKS].count_documents({"partner_id": ctx.partner_id, "status": s_})
                     for s_ in T.TASK_STATUSES}
    return {"success": True, **res}


@router.get("/tasks/{task_id}")
def get_task(task_id: str, request: Request, ctx: PartnerContext = Depends(require_partner_permission(None))):
    from app.partners import tasks as T
    db = S.db_or_503()
    t = db[T.TASKS].find_one({"_id": S.oid(task_id), "partner_id": ctx.partner_id}) if S.oid(task_id) else None
    if not t:
        if S.oid(task_id):
            _probe(request, ctx, T.TASKS, {"_id": S.oid(task_id)})
        raise HTTPException(status_code=404, detail="Task not found")
    return {"success": True, "task": T.task_out(t)}


class TaskAction(BaseModel):
    note: str = ""


@router.post("/tasks/{task_id}/{action}")
def task_action(task_id: str, action: str, body: TaskAction, request: Request,
                ctx: PartnerContext = Depends(require_partner_permission(None, write=True))):
    from app.partners import tasks as T
    if action not in ("start", "submit"):
        raise HTTPException(status_code=404, detail="Unknown action")
    try:
        task = T.partner_action(load_partner_doc(ctx), task_id, action, body.note, ip=_ip(request))
    except HTTPException as e:
        if e.status_code == 404 and S.oid(task_id):
            _probe(request, ctx, T.TASKS, {"_id": S.oid(task_id)})
        raise
    return {"success": True, "task": task,
            "message": "Started" if action == "start" else "Sent to LeadAI for review"}


# ── commissions / wallet / payouts ───────────────────────────────────────────

@router.get("/commissions")
def list_commissions(page: int = Query(1, ge=1), limit: int = Query(25, ge=1, le=100),
                     status: Optional[str] = None, sort: str = "-created_at",
                     date_from: Optional[str] = Query(None, alias="from"),
                     date_to: Optional[str] = Query(None, alias="to"),
                     ctx: PartnerContext = Depends(require_partner_permission(K.COMMISSIONS_VIEW))):
    db = S.db_or_503()
    query: Dict[str, Any] = {"partner_id": ctx.partner_id, **_date_filter("created_at", date_from, date_to)}
    if status:
        if status not in K.COMMISSION_STATUSES:
            raise HTTPException(status_code=422, detail="Unknown status")
        query["status"] = status
    res = _page(db[K.COMMISSIONS], query, page=page, limit=limit,
                sort=_sort(sort, ("created_at", "amount", "status"), "-created_at"), out=ST.commission_out)
    return {"success": True, **res, "balances": C.compute_balances(ctx.partner_id, db)}


@router.get("/wallet")
def wallet(ctx: PartnerContext = Depends(require_partner_permission(K.WALLET_VIEW))):
    db = S.db_or_503()
    settings = S.get_program_settings(db)
    return {"success": True, "wallet": {
        "balances": C.recompute_wallet(ctx.partner_id, db),
        "min_payout": settings.get("min_payout"),
        "transactions": C.recent_ledger(ctx.partner_id, 50, db)}}


@router.get("/payouts")
def list_payouts(page: int = Query(1, ge=1), limit: int = Query(25, ge=1, le=100),
                 status: Optional[str] = None,
                 ctx: PartnerContext = Depends(require_partner_permission(K.WALLET_VIEW))):
    db = S.db_or_503()
    query: Dict[str, Any] = {"partner_id": ctx.partner_id}
    if status:
        query["status"] = status
    return {"success": True, **_page(db[K.PAYOUTS], query, page=page, limit=limit,
                                     sort="-created_at", out=C.payout_out)}


class PayoutRequest(BaseModel):
    currency: Optional[str] = None


@router.post("/payouts")
def request_payout(body: PayoutRequest, request: Request,
                   ctx: PartnerContext = Depends(require_partner_permission(K.PAYOUTS_REQUEST, write=True))):
    return {"success": True, "payout": C.request_payout(load_partner_doc(ctx), currency=body.currency,
                                                        ip=_ip(request))}


@router.post("/payouts/{payout_id}/cancel")
def cancel_payout(payout_id: str, request: Request,
                  ctx: PartnerContext = Depends(require_partner_permission(K.PAYOUTS_REQUEST, write=True))):
    try:
        return {"success": True, "payout": C.cancel_own_payout(load_partner_doc(ctx), payout_id,
                                                               ip=_ip(request))}
    except HTTPException as e:
        if e.status_code == 404:
            _probe(request, ctx, K.PAYOUTS, {"_id": S.oid(payout_id)})
        raise


# ── analytics / campaigns ────────────────────────────────────────────────────

@router.get("/analytics")
def analytics(date_from: Optional[str] = Query(None, alias="from"),
              date_to: Optional[str] = Query(None, alias="to"), unit: str = "day",
              campaign_id: Optional[str] = None,
              ctx: PartnerContext = Depends(require_partner_permission(K.ANALYTICS_VIEW))):
    start, end = ST.parse_range(date_from, date_to)
    db = S.db_or_503()
    if campaign_id and not db[K.CAMPAIGNS].find_one({"_id": S.oid(campaign_id), "partner_id": ctx.partner_id}):
        raise HTTPException(status_code=404, detail="Campaign not found")
    return {"success": True, "analytics": ST.analytics(db, load_partner_doc(ctx), start, end, unit,
                                                       campaign_id=campaign_id)}


@router.get("/analytics.csv")
def analytics_csv(date_from: Optional[str] = Query(None, alias="from"),
                  date_to: Optional[str] = Query(None, alias="to"), unit: str = "day",
                  campaign_id: Optional[str] = None,
                  ctx: PartnerContext = Depends(require_partner_permission(K.ANALYTICS_VIEW))):
    from fastapi.responses import Response
    start, end = ST.parse_range(date_from, date_to)
    db = S.db_or_503()
    if campaign_id and not db[K.CAMPAIGNS].find_one({"_id": S.oid(campaign_id), "partner_id": ctx.partner_id}):
        raise HTTPException(status_code=404, detail="Campaign not found")
    data = ST.analytics(db, load_partner_doc(ctx), start, end, unit, campaign_id=campaign_id)
    return Response(ST.analytics_csv(data), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="analytics-{start:%Y%m%d}-{end:%Y%m%d}.csv"'})


@router.get("/campaigns")
def list_campaigns(ctx: PartnerContext = Depends(require_partner_permission(K.CAMPAIGNS_MANAGE))):
    db = S.db_or_503()
    p = load_partner_doc(ctx)
    return {"success": True, "items": [R.campaign_out(c, p) for c in
                                       db[K.CAMPAIGNS].find({"partner_id": ctx.partner_id}).sort("created_at", -1)]}


class CampaignBody(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    landing_path: Optional[str] = None
    kind: Optional[str] = None          # referral | onboarding (resellers)
    status: str = "active"


@router.post("/campaigns")
def create_campaign(body: CampaignBody, request: Request,
                    ctx: PartnerContext = Depends(require_partner_permission(K.CAMPAIGNS_MANAGE, write=True))):
    return {"success": True, "campaign": R.save_campaign(load_partner_doc(ctx), body.model_dump(),
                                                         ip=_ip(request))}


@router.patch("/campaigns/{campaign_id}")
def update_campaign(campaign_id: str, body: CampaignBody, request: Request,
                    ctx: PartnerContext = Depends(require_partner_permission(K.CAMPAIGNS_MANAGE, write=True))):
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        return {"success": True, "campaign": R.save_campaign(load_partner_doc(ctx), data,
                                                             campaign_id=campaign_id, ip=_ip(request))}
    except HTTPException as e:
        if e.status_code == 404:
            _probe(request, ctx, K.CAMPAIGNS, {"_id": S.oid(campaign_id)})
        raise


# ── coupons / marketing ──────────────────────────────────────────────────────

def _coupon_out(c: Dict[str, Any]) -> Dict[str, Any]:
    return S.clean(c)


@router.get("/coupons")
def list_coupons(ctx: PartnerContext = Depends(require_partner_permission(K.COUPONS_MANAGE))):
    db = S.db_or_503()
    items = [_coupon_out(c) for c in db[K.COUPONS].find({"partner_id": ctx.partner_id}).sort("created_at", -1)]
    settings = S.get_program_settings(db)
    return {"success": True, "items": items,
            "can_create": bool(settings.get("partner_coupons_enabled")),
            "max_percent": settings.get("max_partner_coupon_percent")}


class CouponBody(BaseModel):
    code: Optional[str] = None
    discount_type: str = "percentage"
    discount_value: float = 0
    max_redemptions: Optional[int] = None
    expires_at: Optional[str] = None
    plan_slugs: Optional[List[str]] = None
    status: str = "active"


@router.post("/coupons")
def create_coupon(body: CouponBody, request: Request,
                  ctx: PartnerContext = Depends(require_partner_permission(K.COUPONS_MANAGE, write=True))):
    return {"success": True, "coupon": CP.save_coupon(body.model_dump(), partner_id=ctx.partner_id,
                                                      actor=ctx.audit_user(), by_partner=True, ip=_ip(request))}


@router.patch("/coupons/{coupon_id}")
def update_coupon(coupon_id: str, body: CouponBody, request: Request,
                  ctx: PartnerContext = Depends(require_partner_permission(K.COUPONS_MANAGE, write=True))):
    try:
        return {"success": True, "coupon": CP.save_coupon(body.model_dump(), partner_id=ctx.partner_id,
                                                          actor=ctx.audit_user(), by_partner=True,
                                                          coupon_id=coupon_id, ip=_ip(request))}
    except HTTPException as e:
        if e.status_code == 404:
            _probe(request, ctx, K.COUPONS, {"_id": S.oid(coupon_id)})
        raise


@router.get("/marketing-assets")
def marketing_assets(category: Optional[str] = None, kind: Optional[str] = None, q: Optional[str] = None,
                     ctx: PartnerContext = Depends(require_partner_permission(K.MARKETING_ACCESS))):
    """Only assets this partner is permitted to see (type, tier, named partners)."""
    from app.partners import marketing as M
    db = S.db_or_503()
    p = load_partner_doc(ctx)
    items = []
    for a in db[K.ASSETS].find({"status": "published"}).sort("created_at", -1):
        if not M.can_see(a, p):
            continue
        out = M.asset_out(a, p)
        if (category or kind) and out["category"] not in (category, M._KIND_TO_CATEGORY.get(kind or "")):
            continue
        if q and q.lower() not in (out.get("title") or "").lower() + " " + (out.get("description") or "").lower():
            continue
        items.append(out)
    return {"success": True, "items": items, "categories": M.CATEGORIES}


@router.get("/marketing-assets/{asset_id}/file")
def marketing_asset_file(asset_id: str, request: Request, download: bool = False,
                         ctx: PartnerContext = Depends(require_partner_permission(K.MARKETING_ACCESS))):
    from app.partners import marketing as M
    from app.api.routes.super_admin_partners import _serve_asset_file
    db = S.db_or_503()
    a = db[K.ASSETS].find_one({"_id": S.oid(asset_id)}) if S.oid(asset_id) else None
    if not a or not M.can_see(a, load_partner_doc(ctx)):
        raise HTTPException(status_code=404, detail="Asset not found")
    if download:
        db[K.ASSETS].update_one({"_id": a["_id"]}, {"$inc": {"download_count": 1}})
        S.paudit("partner.asset.downloaded", ctx.partner_id, actor=ctx.audit_user(), ip=_ip(request),
                 details={"asset": a.get("title")}, resource_type="partner_asset", resource_id=asset_id)
    return _serve_asset_file(asset_id, download=download)


# ── notifications ────────────────────────────────────────────────────────────

def _notif_query(ctx: PartnerContext) -> Dict[str, Any]:
    from app.events.notifications import audience_query
    return audience_query(is_super_admin=False, user_id=ctx.user_id, organization_id=None, org_role=None)


def _session_or_key(request: Request) -> PartnerContext:
    """Notifications: applicants (session) and partners (session or API key)."""
    from app.partners.auth import _api_key_from, get_partner_context
    return get_partner_context(request) if _api_key_from(request) else get_partner_session(request)


@router.get("/notifications")
async def notifications(unread: bool = False, page: int = Query(1, ge=1),
                        limit: int = Query(30, ge=1, le=100),
                        ctx: PartnerContext = Depends(_session_or_key)):
    from app.events.notifications import list_for
    res = await list_for(_notif_query(ctx), ctx.user_id, unread_only=unread, limit=limit,
                         skip=(page - 1) * limit)
    return {"success": True, **res}


class ReadBody(BaseModel):
    id: Optional[str] = None


@router.post("/notifications/read")
async def read_notifications(body: ReadBody, ctx: PartnerContext = Depends(get_partner_session)):
    from app.events.notifications import mark_read
    return {"success": True, "updated": await mark_read(_notif_query(ctx), ctx.user_id, body.id)}


# ── API keys ─────────────────────────────────────────────────────────────────

@router.get("/api-keys")
def api_keys(ctx: PartnerContext = Depends(require_partner_permission(K.API_ACCESS, write=True))):
    return {"success": True, "items": list_partner_api_keys(ctx.partner_id)}


class KeyBody(BaseModel):
    name: str = "API key"


@router.post("/api-keys")
def create_api_key(body: KeyBody, request: Request,
                   ctx: PartnerContext = Depends(require_partner_permission(K.API_ACCESS, write=True))):
    raw, doc = create_partner_api_key(load_partner_doc(ctx), body.name)
    S.paudit("partner.api_key.created", ctx.partner_id, actor=ctx.audit_user(), ip=_ip(request),
             details={"key_id": doc["key_id"], "name": doc["name"]}, resource_type="api_key",
             resource_id=doc["key_id"])
    return {"success": True, "api_key": raw, "key": S.clean({k: v for k, v in doc.items() if k != "key_hash"}),
            "message": "Copy this key now — it is shown only once."}


@router.delete("/api-keys/{key_id}")
def delete_api_key(key_id: str, request: Request,
                   ctx: PartnerContext = Depends(require_partner_permission(K.API_ACCESS, write=True))):
    if not revoke_partner_api_key(ctx.partner_id, key_id):
        raise HTTPException(status_code=404, detail="API key not found")
    S.paudit("partner.api_key.revoked", ctx.partner_id, actor=ctx.audit_user(), ip=_ip(request),
             details={"key_id": key_id}, resource_type="api_key", resource_id=key_id)
    return {"success": True}
