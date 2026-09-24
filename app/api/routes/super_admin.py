"""
Super Admin Portal API Routes.

Platform-level management endpoints for the Super Admin:
  - Dashboard metrics (orgs, admins, users, demo, subscriptions, payments,
    tokens, searches, Apify jobs, leads, errors, recent activity, health)
  - Organization management (list, detail drill-down, status lifecycle incl.
    soft-delete/archive)
  - Platform user management (list, detail, status)
  - Subscription list / plan catalog CRUD (shared validation in
    app.billing.plan_admin)
  - Audit logs, impersonation

System health, integrations, analytics, security center, feature flags,
reports and support tooling live in super_admin_platform.py; demo,
subscription queue, payments, tokens, notifications and the role matrix live
in super_admin_lifecycle.py.

All endpoints require the super_admin platform role; every mutation is audited.
"""
import logging
import re
from datetime import timedelta
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

from app.auth.tenant import get_tenant_context, TenantContext, require_platform_role
from app.db.models import utcnow
from app.db.mongo import get_async_db, get_sync_db
from app.admin import audit as _audit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/super-admin", tags=["super-admin"])


def _clean(d: Any) -> Any:
    """Recursively convert ObjectId/datetime for JSON serialization and drop
    secret fields."""
    if d is None:
        return None
    if isinstance(d, ObjectId):
        return str(d)
    if isinstance(d, dict):
        return {k: _clean(v) for k, v in d.items()
                if k not in ("password_hash", "token_hash", "session_id")}
    if isinstance(d, list):
        return [_clean(i) for i in d]
    return d


def _db():
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return db


def _oid(value: Any) -> Optional[ObjectId]:
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _rx(q: Optional[str]) -> Optional[Dict[str, Any]]:
    q = (q or "").strip()
    return {"$regex": re.escape(q[:100]), "$options": "i"} if q else None


async def _c(coll, query: Dict[str, Any]) -> int:
    """count_documents that never takes the dashboard down."""
    try:
        return int(await coll.count_documents(query))
    except Exception:
        return 0


async def _a(coll, pipeline: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    try:
        return [d async for d in coll.aggregate(pipeline)]
    except Exception:
        return []


async def _recent(coll, query: Dict[str, Any], sort_field: str, limit: int,
                  projection: Optional[Dict[str, int]] = None) -> List[Dict[str, Any]]:
    try:
        return [_clean(d) async for d in coll.find(query, projection).sort(sort_field, -1).limit(limit)]
    except Exception:
        return []


async def _with_org_names(db, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Add organization_name to feed rows that only carry organization_id."""
    ids = {str(r["organization_id"]) for r in rows if r.get("organization_id")}
    oids = [ObjectId(i) for i in ids if ObjectId.is_valid(i)]
    if not oids:
        return rows
    names = {str(o["_id"]): o.get("name", "")
             async for o in db.organizations.find({"_id": {"$in": oids}}, {"name": 1})}
    for r in rows:
        if r.get("organization_id"):
            r["organization_name"] = names.get(str(r["organization_id"]), "")
    return rows


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


class OrgStatusUpdate(BaseModel):
    status: str  # active | suspended | disabled | archived | cancelled | demo
    reason: str = ""


class UserStatusUpdate(BaseModel):
    status: str  # active | suspended | disabled
    reason: str = ""


class ImpersonateRequest(BaseModel):
    organization_id: str
    reason: str = ""


# ── Dashboard ───────────────────────────────────────────────────────────────

@router.get("/dashboard")
async def super_admin_dashboard(
    ctx: TenantContext = Depends(require_platform_role("super_admin")),
):
    """Platform-wide control-center metrics for the Super Admin."""
    import time
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    now = utcnow()
    d1, d7, d30 = now - timedelta(days=1), now - timedelta(days=7), now - timedelta(days=30)

    org_stats: Dict[str, int] = {}
    for doc in await _a(db.organizations, [{"$group": {"_id": "$status", "count": {"$sum": 1}}}]):
        org_stats[str(doc["_id"])] = int(doc.get("count") or 0)
    total_orgs = sum(org_stats.values())

    # Money is never added across currencies: every amount is grouped by
    # currency first; the historic single-number fields are only filled
    # when exactly one currency is involved (None otherwise).
    from app.api.routes.super_admin_platform import (currency_totals, norm_currency,
                                                     single_amount, single_currency)
    sub_rows = await _a(db.subscriptions, [
        {"$group": {"_id": {"s": "$status", "c": "$currency"}, "count": {"$sum": 1},
                    "total_amount": {"$sum": "$amount"}}}])
    sub_stats: Dict[str, Dict[str, Any]] = {}
    for doc in sub_rows:
        st = str((doc.get("_id") or {}).get("s"))
        entry = sub_stats.setdefault(st, {"count": 0, "amount_by_currency": {}})
        entry["count"] += int(doc.get("count") or 0)
        cur = norm_currency((doc.get("_id") or {}).get("c"))
        entry["amount_by_currency"][cur] = round(entry["amount_by_currency"].get(cur, 0.0)
                                                 + _num(doc.get("total_amount")), 2)
    for entry in sub_stats.values():
        entry["total_amount"] = single_amount(entry["amount_by_currency"])

    mrr_rows = []
    for doc in await _a(db.subscriptions, [
            {"$match": {"status": "active"}},
            {"$group": {"_id": {"b": "$billing_cycle", "c": "$currency"}, "total": {"$sum": "$amount"}}}]):
        key = doc.get("_id") or {}
        mrr_rows.append({"currency": key.get("c"),
                         "amount": _num(doc.get("total")) / (12 if key.get("b") == "yearly" else 1)})
    mrr_by_currency = currency_totals(mrr_rows)
    mrr = single_amount(mrr_by_currency)

    pay: Dict[str, Dict[str, Any]] = {}
    for doc in await _a(db.payments, [
            {"$group": {"_id": {"s": "$status", "c": "$currency"}, "n": {"$sum": 1},
                        "amount": {"$sum": "$amount"}}}]):
        st = str((doc.get("_id") or {}).get("s"))
        entry = pay.setdefault(st, {"count": 0, "amount_by_currency": {}})
        entry["count"] += int(doc.get("n") or 0)
        cur = norm_currency((doc.get("_id") or {}).get("c"))
        entry["amount_by_currency"][cur] = round(entry["amount_by_currency"].get(cur, 0.0)
                                                 + _num(doc.get("amount")), 2)
    for entry in pay.values():
        entry["amount"] = single_amount(entry["amount_by_currency"])
    rev30 = await _a(db.payments, [{"$match": {"status": "succeeded", "created_at": {"$gte": d30}}},
                                   {"$group": {"_id": "$currency", "amount": {"$sum": "$amount"}}}])
    rev30_by_currency = currency_totals([{"currency": r.get("_id"), "amount": r.get("amount")}
                                         for r in rev30])
    succeeded_by_currency = dict(sorted((pay.get("succeeded") or {}).get("amount_by_currency", {}).items()))
    all_currencies = sorted(set(mrr_by_currency) | set(rev30_by_currency) | set(succeeded_by_currency))
    currency = single_currency({c: 0 for c in all_currencies})

    tok = await _a(db.token_balances, [{"$group": {"_id": None, "allocated": {"$sum": "$allocated"},
                                                   "used": {"$sum": "$used"},
                                                   "remaining": {"$sum": "$remaining"}}}])
    tok = tok[0] if tok else {}
    consumed = {}
    for label, since in (("24h", d1), ("30d", d30)):
        rows = await _a(db.token_ledger, [{"$match": {"type": "consume", "created_at": {"$gte": since}}},
                                          {"$group": {"_id": None, "n": {"$sum": "$amount"}}}])
        consumed[label] = int(rows[0].get("n") or 0) if rows else 0

    sh = db.search_history
    failed_q = {"status": {"$in": ["error", "failed"]}}
    apify_q = {"$or": [{"scrape_info": {"$exists": True}}, {"apify_run_id": {"$exists": True}},
                       {"provider": "apify"}]}
    total_searches_30d = await _c(sh, {"created_at": {"$gte": d30}})
    total_leads = await _c(db.ai_comments, {"is_lead": True})
    hot_leads = await _c(db.ai_comments, {"is_lead": True, "lead_quality": "hot"})

    db_ok, latency = False, None
    try:
        started = time.perf_counter()
        await db.command("ping")
        latency = round((time.perf_counter() - started) * 1000, 1)
        db_ok = True
    except Exception:
        pass

    awaiting = await _c(db.subscriptions, {"status": "pending_admin_confirmation"})
    return {
        "success": True,
        "dashboard": {
            "organizations": {
                "total": total_orgs,
                "active": org_stats.get("active", 0),
                "demo": org_stats.get("demo", 0),
                "trial": org_stats.get("trial", 0),
                "pending": org_stats.get("pending", 0),
                "suspended": org_stats.get("suspended", 0),
                "disabled": org_stats.get("disabled", 0),
                "cancelled": org_stats.get("cancelled", 0),
                "archived": org_stats.get("archived", 0),
                "by_status": org_stats,
            },
            "admins": {
                "total": await _c(db.organization_members, {"role": {"$in": ["owner", "admin"]},
                                                            "status": "active"}),
            },
            "users": {
                "total": await _c(db.users, {}),
                "active": await _c(db.users, {"status": "active"}),
                "suspended": await _c(db.users, {"status": "suspended"}),
                "platform_admins": await _c(db.users, {"is_platform_admin": True}),
                "new_7d": await _c(db.users, {"created_at": {"$gte": d7}}),
            },
            "demo": {
                "accounts": org_stats.get("demo", 0),
                "pending_requests": await _c(db.demo_requests, {"status": "pending"}),
                "converted": await _c(db.demo_requests, {"status": "converted"}),
                "total_requests": await _c(db.demo_requests, {}),
            },
            "subscriptions": {
                "total": sum(s.get("count", 0) for s in sub_stats.values()),
                "by_status": sub_stats,
                "active": sub_stats.get("active", {}).get("count", 0),
                "pending": sum(sub_stats.get(s, {}).get("count", 0)
                               for s in ("pending_payment", "payment_received")),
                "awaiting_confirmation": awaiting,
                "expired": sub_stats.get("expired", {}).get("count", 0),
                "cancelled": sub_stats.get("cancelled", {}).get("count", 0),
                "suspended": sub_stats.get("suspended", {}).get("count", 0),
                "monthly_revenue": mrr,
                "mrr": mrr,
                "mrr_by_currency": mrr_by_currency,
                "multi_currency": len(mrr_by_currency) > 1,
            },
            "payments": {
                "by_status": pay,
                "succeeded_amount": single_amount(succeeded_by_currency),
                "succeeded_by_currency": succeeded_by_currency,
                "revenue_30d": single_amount(rev30_by_currency),
                "revenue_30d_by_currency": rev30_by_currency,
                "pending": pay.get("pending", {}).get("count", 0),
                "failed": pay.get("failed", {}).get("count", 0),
                "refund_required": await _c(db.payments, {"refund_required": True}),
                "currency": currency,
                "currencies": all_currencies,
                "multi_currency": len(all_currencies) > 1,
            },
            "tokens": {
                "allocated": int(tok.get("allocated") or 0), "used": int(tok.get("used") or 0),
                "remaining": int(tok.get("remaining") or 0),
                "consumed_24h": consumed["24h"], "consumed_30d": consumed["30d"],
            },
            "searches": {
                "total": await _c(sh, {}),
                "running": await _c(sh, {"status": "running"}),
                "completed": await _c(sh, {"status": {"$in": ["completed", "done", "success"]}}),
                "failed": await _c(sh, failed_q),
                "last_30d": total_searches_30d,
            },
            "apify": {
                "total": await _c(sh, apify_q),
                "failed": await _c(sh, {"$and": [apify_q, failed_q]}),
                "failed_24h": await _c(sh, {"$and": [apify_q, failed_q, {"created_at": {"$gte": d1}}]}),
            },
            "leads": {"total": total_leads, "hot": hot_leads,
                      "last_30d": await _c(db.ai_comments, {"is_lead": True, "created_at": {"$gte": d30}})},
            "errors": {
                "security_high_7d": await _c(db.security_events, {"severity": {"$in": ["high", "critical"]},
                                                                  "at": {"$gte": d7}}),
                "system_errors_7d": await _c(db.notifications, {"type": "system_error",
                                                                "created_at": {"$gte": d7}}),
            },
            "activity": {
                "searches_30d": total_searches_30d,
                "total_leads": total_leads,
                "hot_leads": hot_leads,
            },
            "platform": {
                "plans_count": await _c(db.plans, {}),
                "recent_audit_logs": await _c(db.audit_logs, {"at": {"$gte": d7}}),
            },
            "recent": {
                "registrations": await _recent(db.organizations, {}, "created_at", 6,
                                               {"name": 1, "status": 1, "created_at": 1, "plan_id": 1}),
                "demo_requests": await _recent(db.demo_requests, {}, "created_at", 6,
                                               {"company": 1, "name": 1, "status": 1, "created_at": 1}),
                "payments": await _with_org_names(db, await _recent(
                    db.payments, {}, "created_at", 6,
                    {"organization_id": 1, "amount": 1, "currency": 1, "status": 1,
                     "created_at": 1, "refund_required": 1})),
                "subscription_requests": await _with_org_names(db, await _recent(
                    db.subscriptions, {"status": "pending_admin_confirmation"}, "created_at", 6,
                    {"organization_id": 1, "plan_id": 1, "amount": 1, "currency": 1, "created_at": 1})),
                "activity": await _with_org_names(db, await _recent(
                    db.audit_logs, {}, "at", 12,
                    {"action": 1, "category": 1, "actor_email": 1, "at": 1,
                     "organization_id": 1, "status": 1, "resource_type": 1,
                     "resource_id": 1})),
            },
            "health": {"database": db_ok, "latency_ms": latency},
            "generated_at": now.isoformat(),
        },
    }


# ── Organization Management ─────────────────────────────────────────────────

ORG_STATUSES = {"active", "suspended", "demo", "cancelled", "disabled", "archived"}


@router.get("/organizations")
async def list_organizations(
    status: Optional[str] = Query(None, description="Filter by status"),
    search: Optional[str] = Query(None, description="Search by name or slug"),
    plan: Optional[str] = Query(None),
    include_archived: bool = False,
    sort: str = "-created_at",
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    ctx: TenantContext = Depends(require_platform_role("super_admin")),
):
    """List organizations with filtering, sorting and pagination (archived
    organizations are hidden unless requested)."""
    db = _db()
    query: Dict[str, Any] = {}
    if status:
        query["status"] = status
    elif not include_archived:
        query["status"] = {"$ne": "archived"}
    if plan:
        query["plan_id"] = plan
    rx = _rx(search)
    if rx:
        query["$or"] = [{"name": rx}, {"slug": rx}]
    field = sort.lstrip("-") if sort.lstrip("-") in ("created_at", "name", "status", "updated_at") else "created_at"
    direction = -1 if sort.startswith("-") else 1

    skip = (page - 1) * limit
    total = await db.organizations.count_documents(query)
    cursor = db.organizations.find(query).sort(field, direction).skip(skip).limit(limit)

    orgs = []
    async for org in cursor:
        org_doc = _clean(org)
        org_doc["id"] = str(org["_id"])
        org_id = str(org["_id"])
        org_doc["member_count"] = await db.organization_members.count_documents(
            {"organization_id": org_id, "status": "active"})
        sub = await db.subscriptions.find_one({"organization_id": org_id}, sort=[("created_at", -1)])
        if sub:
            org_doc["current_plan"] = sub.get("plan_id", "free")
            org_doc["subscription_status"] = sub.get("status", "inactive")
        owner = await db.organization_members.find_one({"organization_id": org_id, "role": "owner"})
        if owner and _oid(owner.get("user_id")):
            u = await db.users.find_one({"_id": _oid(owner["user_id"])}, {"email": 1})
            org_doc["owner_email"] = (u or {}).get("email")
        orgs.append(org_doc)

    return {
        "success": True,
        "organizations": orgs,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": max(1, (total + limit - 1) // limit),
    }


@router.get("/organizations/{org_id}")
async def get_organization_detail(
    org_id: str,
    ctx: TenantContext = Depends(require_platform_role("super_admin")),
):
    """Organization drill-down: members/admins, subscription(s), tokens,
    demo, usage, recent searches and audit activity."""
    db = _db()
    try:
        org = await db.organizations.find_one({"_id": ObjectId(org_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid organization ID")
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    org_doc = _clean(org)
    org_doc["id"] = org_id

    members = []
    async for m in db.organization_members.find({"organization_id": org_id, "status": {"$ne": "removed"}}):
        m_doc = _clean(m)
        u = await db.users.find_one({"_id": _oid(m.get("user_id"))}) if _oid(m.get("user_id")) else None
        if u:
            m_doc["email"] = u.get("email")
            m_doc["name"] = u.get("name")
            m_doc["last_login"] = u.get("last_login")
            m_doc["user_status"] = u.get("status", "active")
        members.append(m_doc)
    org_doc["members"] = members
    org_doc["member_count"] = len(members)
    org_doc["admins"] = [m for m in members if m.get("role") in ("owner", "admin")]

    sub = await db.subscriptions.find_one({"organization_id": org_id}, sort=[("created_at", -1)])
    org_doc["subscription"] = _clean(sub) if sub else None
    org_doc["subscriptions"] = [_clean(s) async for s in db.subscriptions.find(
        {"organization_id": org_id}, {"status_history": 0}).sort("created_at", -1).limit(20)]

    try:
        from app.billing.tokens import get_balance
        org_doc["tokens"] = get_balance(org_id)
    except Exception:
        org_doc["tokens"] = None
    org_doc["demo_request"] = _clean(await db.demo_requests.find_one(
        {"organization_id": org_id}, {"ip": 0}, sort=[("created_at", -1)]))

    org_doc["usage_stats"] = {
        "total_searches": await _c(db.search_history, {"organization_id": org_id}),
        "running_searches": await _c(db.search_history, {"organization_id": org_id, "status": "running"}),
        "failed_searches": await _c(db.search_history, {"organization_id": org_id,
                                                        "status": {"$in": ["error", "failed"]}}),
        "total_leads": await _c(db.ai_comments, {"organization_id": org_id, "is_lead": True}),
        "hot_leads": await _c(db.ai_comments, {"organization_id": org_id, "is_lead": True,
                                               "lead_quality": "hot"}),
        "pages": await _c(db.facebook_pages, {"organization_id": org_id}),
        "ai_calls": await _c(db.ai_requests, {"organization_id": org_id}),
        "active_sessions": await _c(db.user_sessions, {"organization_id": org_id, "revoked_at": None}),
    }
    org_doc["recent_searches"] = await _recent(
        db.search_history, {"organization_id": org_id}, "created_at", 8,
        {"run_id": 1, "query": 1, "status": 1, "created_at": 1, "created_by": 1, "error": 1})
    org_doc["recent_activity"] = await _recent(db.audit_logs, {"organization_id": org_id}, "at", 15)
    return {"success": True, "organization": org_doc}


@router.patch("/organizations/{org_id}/status")
async def update_organization_status(
    org_id: str,
    body: OrgStatusUpdate,
    request: Request,
    ctx: TenantContext = Depends(require_platform_role("super_admin")),
):
    """Activate, suspend, deactivate (disabled), cancel or archive (soft
    delete) an organization. Tenant data is never deleted here."""
    db = _db()
    if body.status not in ORG_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {', '.join(sorted(ORG_STATUSES))}")
    if body.status in ("archived", "disabled") and not body.reason.strip():
        raise HTTPException(status_code=422, detail="A reason is required to archive or deactivate")
    oid = _oid(org_id)
    org = await db.organizations.find_one({"_id": oid}) if oid else None
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    before = org.get("status")
    updates: Dict[str, Any] = {"status": body.status, "updated_at": utcnow()}
    if body.status == "archived":
        updates.update({"archived_at": utcnow(), "archived_by": ctx.email,
                        "archive_reason": body.reason.strip()[:300]})
    if body.status in ("suspended", "disabled"):
        updates["suspended_reason"] = body.reason.strip()[:300]
    await db.organizations.update_one({"_id": oid}, {"$set": updates})

    revoked = 0
    if body.status in ("suspended", "disabled", "archived", "cancelled"):
        from app.auth.service import revoke_user_sessions
        members = await db.organization_members.find(
            {"organization_id": org_id, "status": "active"}).to_list(length=1000)
        for m in members:
            revoked += revoke_user_sessions(m["user_id"], revoked_by=f"super_admin:{ctx.email}") or 0

    await _audit.aaudit(f"organization.{body.status}", "platform", user=ctx.audit_user(),
                        organization_id=org_id, resource_type="organization",
                        resource_id=org_id, details={"before": before, "after": body.status,
                                                     "new_status": body.status,
                                                     "reason": body.reason[:300],
                                                     "sessions_revoked": revoked},
                        **_audit.request_meta(request))
    if body.status == "suspended":
        from app.events.notifications import notify_super_admins
        notify_super_admins("organization_suspended", "Organization suspended",
                            f"{org.get('name') or org_id} suspended by {ctx.email}",
                            severity="warning", link="/superadmin#/organizations/" + org_id)

    return {"success": True, "message": f"Organization status updated to {body.status}"}


# ── User Management ─────────────────────────────────────────────────────────

@router.get("/users")
async def list_users(
    status: Optional[str] = Query(None, description="Filter by status"),
    org_id: Optional[str] = Query(None, description="Filter by organization"),
    search: Optional[str] = Query(None, description="Search by email or name"),
    role: Optional[str] = Query(None, description="Organization role"),
    platform: Optional[bool] = Query(None, description="Only platform staff"),
    sort: str = "-created_at",
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    ctx: TenantContext = Depends(require_platform_role("super_admin")),
):
    """List all platform users with filtering, sorting and pagination."""
    db = _db()
    query: Dict[str, Any] = {}
    if status:
        query["status"] = status
    if org_id or role:
        mq: Dict[str, Any] = {"status": {"$ne": "removed"}}
        if org_id:
            mq["organization_id"] = org_id
        if role:
            mq["role"] = role
        member_user_ids = [m["user_id"] async for m in db.organization_members.find(mq, {"user_id": 1})]
        query["_id"] = {"$in": [o for o in (_oid(u) for u in member_user_ids) if o is not None]}
    if platform is not None:
        query["is_platform_admin"] = True if platform else {"$ne": True}
    rx = _rx(search)
    if rx:
        query["$or"] = [{"email": rx}, {"name": rx}]
    field = sort.lstrip("-") if sort.lstrip("-") in ("created_at", "email", "name", "last_login", "status") else "created_at"
    direction = -1 if sort.startswith("-") else 1

    skip = (page - 1) * limit
    total = await db.users.count_documents(query)
    cursor = db.users.find(query, {"password_hash": 0}).sort(field, direction).skip(skip).limit(limit)
    raw = [u async for u in cursor]

    uids = [str(u["_id"]) for u in raw]
    mems: Dict[str, List[Dict[str, Any]]] = {}
    org_ids = set()
    async for m in db.organization_members.find({"user_id": {"$in": uids}, "status": {"$ne": "removed"}}):
        mems.setdefault(m["user_id"], []).append(m)
        org_ids.add(m.get("organization_id"))
    names = {}
    oids = [o for o in (_oid(i) for i in org_ids) if o is not None]
    async for o in db.organizations.find({"_id": {"$in": oids}}, {"name": 1}):
        names[str(o["_id"])] = o.get("name")

    users = []
    for user in raw:
        user_doc = _clean(user)
        user_doc["id"] = str(user["_id"])
        user_doc["memberships"] = [{
            "organization_id": m.get("organization_id"),
            "organization_name": names.get(str(m.get("organization_id"))),
            "role": m.get("role"),
            "status": m.get("status"),
        } for m in mems.get(str(user["_id"]), [])]
        users.append(user_doc)

    return {
        "success": True,
        "users": users,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": max(1, (total + limit - 1) // limit),
    }


@router.get("/users/{user_id}")
async def get_user_detail(
    user_id: str,
    ctx: TenantContext = Depends(require_platform_role("super_admin")),
):
    """User profile, memberships, sessions, usage and recent activity."""
    db = _db()
    try:
        user = await db.users.find_one({"_id": ObjectId(user_id)}, {"password_hash": 0})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user ID")
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user_doc = _clean(user)
    user_doc["id"] = user_id

    memberships = []
    async for m in db.organization_members.find({"user_id": user_id, "status": {"$ne": "removed"}}):
        m_doc = _clean(m)
        org = await db.organizations.find_one({"_id": _oid(m.get("organization_id"))}) \
            if _oid(m.get("organization_id")) else None
        m_doc["organization_name"] = org.get("name") if org else "Unknown"
        m_doc["organization_status"] = org.get("status") if org else None
        memberships.append(m_doc)
    user_doc["memberships"] = memberships

    user_doc["active_sessions"] = await _c(db.user_sessions, {"user_id": user_id, "revoked_at": None})
    consumed = await _a(db.token_ledger, [{"$match": {"user_id": user_id, "type": "consume"}},
                                          {"$group": {"_id": None, "n": {"$sum": "$amount"}}}])
    user_doc["usage"] = {
        "searches": await _c(db.search_history, {"user_id": user_id}),
        "running": await _c(db.search_history, {"user_id": user_id, "status": "running"}),
        "failed": await _c(db.search_history, {"user_id": user_id, "status": {"$in": ["error", "failed"]}}),
        "leads": await _c(db.ai_comments, {"user_id": user_id, "is_lead": True}),
        "exports": await _c(db.exports, {"user_id": user_id}),
        "tokens_consumed": int(consumed[0].get("n") or 0) if consumed else 0,
    }
    user_doc["recent_searches"] = await _recent(
        db.search_history, {"user_id": user_id}, "created_at", 8,
        {"run_id": 1, "query": 1, "status": 1, "created_at": 1, "organization_id": 1, "error": 1})
    user_doc["recent_activity"] = await _recent(
        db.audit_logs, {"$or": [{"actor_user_id": user_id}, {"actor_email": user.get("email")}]}, "at", 15)
    return {"success": True, "user": user_doc}


USER_STATUSES = {"active", "suspended", "disabled"}


@router.patch("/users/{user_id}/status")
async def update_user_status(
    user_id: str,
    body: UserStatusUpdate,
    request: Request,
    ctx: TenantContext = Depends(require_platform_role("super_admin")),
):
    """Activate, suspend or deactivate (disabled) a user."""
    db = _db()
    if body.status not in USER_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be: {', '.join(sorted(USER_STATUSES))}")
    if user_id == ctx.user_id and body.status != "active":
        raise HTTPException(status_code=400, detail="Cannot suspend your own account")
    oid = _oid(user_id)
    if oid is None:
        raise HTTPException(status_code=400, detail="Invalid user ID")
    user = await db.users.find_one({"_id": oid}, {"status": 1, "email": 1, "is_platform_admin": 1,
                                                  "platform_role": 1})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.get("is_platform_admin") and user.get("platform_role") == "super_admin":
        raise HTTPException(status_code=403, detail="Super Admin accounts are managed from the platform "
                                                    "staff console, not here")
    before = user.get("status", "active")
    await db.users.update_one({"_id": oid}, {"$set": {"status": body.status, "updated_at": utcnow()}})

    revoked = 0
    if body.status != "active":
        from app.auth.service import revoke_user_sessions
        revoked = revoke_user_sessions(user_id, revoked_by=f"super_admin:{ctx.email}") or 0

    await _audit.aaudit(f"user.{body.status}", "platform", user=ctx.audit_user(),
                        resource_type="user", resource_id=user_id,
                        details={"before": before, "after": body.status, "new_status": body.status,
                                 "email": user.get("email"), "reason": body.reason[:300],
                                 "sessions_revoked": revoked},
                        **_audit.request_meta(request))
    return {"success": True, "message": f"User status updated to {body.status}"}


# ── Subscription Management ─────────────────────────────────────────────────

@router.get("/subscriptions")
async def list_all_subscriptions(
    status: Optional[str] = Query(None),
    organization_id: Optional[str] = Query(None),
    plan: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="Organization name"),
    sort: str = "-created_at",
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    ctx: TenantContext = Depends(require_platform_role("super_admin")),
):
    """List subscriptions across all organizations (with status history)."""
    db = _db()
    query: Dict[str, Any] = {}
    if status == "awaiting":
        query["status"] = "pending_admin_confirmation"
    elif status == "pending":
        query["status"] = {"$in": ["pending_payment", "payment_received"]}
    elif status:
        query["status"] = status
    if organization_id:
        query["organization_id"] = organization_id
    if plan:
        query["plan_id"] = plan
    rx = _rx(q)
    if rx:
        ids = [str(o["_id"]) async for o in db.organizations.find({"name": rx}, {"_id": 1}).limit(500)]
        query["organization_id"] = {"$in": ids}
    field = sort.lstrip("-") if sort.lstrip("-") in ("created_at", "updated_at", "amount", "status") else "created_at"
    direction = -1 if sort.startswith("-") else 1

    skip = (page - 1) * limit
    total = await db.subscriptions.count_documents(query)
    cursor = db.subscriptions.find(query).sort(field, direction).skip(skip).limit(limit)

    subs = []
    async for sub in cursor:
        sub_doc = _clean(sub)
        sub_doc["id"] = str(sub["_id"])
        sub_doc.pop("checkout_session_id", None)
        org = await db.organizations.find_one({"_id": _oid(sub.get("organization_id"))}, {"name": 1}) \
            if _oid(sub.get("organization_id")) else None
        sub_doc["organization_name"] = org.get("name") if org else "Unknown"
        subs.append(sub_doc)

    return {
        "success": True,
        "subscriptions": subs,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": max(1, (total + limit - 1) // limit),
    }


@router.get("/plans")
async def list_plans(
    ctx: TenantContext = Depends(require_platform_role("super_admin")),
):
    """List all available plans."""
    db = _db()
    cursor = db.plans.find({}).sort("display_order", 1)
    plans = []
    async for plan in cursor:
        p = _clean(plan)
        p["id"] = p.get("_id")
        p["live_subscriptions"] = await _c(db.subscriptions, {
            "plan_id": {"$in": [plan.get("slug"), str(plan["_id"])]},
            "status": {"$in": ["active", "trialing", "suspended", "pending_payment",
                               "payment_received", "pending_admin_confirmation"]}})
        plans.append(p)
    return {"success": True, "plans": plans}


@router.get("/plans/schema")
async def plan_editor_schema(ctx: TenantContext = Depends(require_platform_role("super_admin"))):
    """Every limit key and feature a plan can carry (for the plan editor)."""
    from app.billing.plan_admin import plan_schema
    return {"success": True, **plan_schema()}


@router.post("/plans")
async def create_plan(
    body: dict,
    ctx: TenantContext = Depends(require_platform_role("super_admin")),
):
    """Create a plan (shared validation in app.billing.plan_admin)."""
    from app.billing.plan_admin import create_plan as _create
    db = _db()
    plan = await _create(db, body, actor=ctx.audit_user())
    return {"success": True, "plan": _clean(plan)}


@router.patch("/plans/{plan_id}")
async def update_plan(
    plan_id: str,
    body: dict,
    ctx: TenantContext = Depends(require_platform_role("super_admin")),
):
    """Update a plan; limits are merged, never wiped."""
    from app.billing.plan_admin import update_plan as _update
    db = _db()
    plan = await _update(db, plan_id, body, actor=ctx.audit_user())
    return {"success": True, "plan": _clean(plan)}


@router.delete("/plans/{plan_id}")
async def archive_plan(
    plan_id: str,
    ctx: TenantContext = Depends(require_platform_role("super_admin")),
):
    """Soft-archive a plan (refused while subscriptions use it)."""
    from app.billing.plan_admin import archive_plan as _archive
    db = _db()
    await _archive(db, plan_id, actor=ctx.audit_user())
    return {"success": True}


# ── Audit Logs ──────────────────────────────────────────────────────────────

@router.get("/audit-logs")
async def list_audit_logs(
    category: Optional[str] = Query(None),
    actor_email: Optional[str] = Query(None, description="Actor email (partial match)"),
    organization_id: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None, description="Actor user id"),
    action: Optional[str] = Query(None, description="Action prefix, e.g. 'subscription.'"),
    status: Optional[str] = Query(None, description="success | failure"),
    resource_type: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="Resource id / action text"),
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    ctx: TenantContext = Depends(require_platform_role("super_admin")),
):
    """Platform-wide audit trail: who / what / when / org / resource /
    before-after / result. Read-only — no API edits or deletes audit rows."""
    from datetime import datetime, timezone
    db = _db()
    query: Dict[str, Any] = {}
    if category:
        query["category"] = category
    rx = _rx(actor_email)
    if rx:
        query["actor_email"] = rx
    if organization_id:
        query["organization_id"] = organization_id
    if user_id:
        query["actor_user_id"] = user_id
    if action:
        query["action"] = {"$regex": "^" + re.escape(action.strip()[:80])}
    if status in ("success", "failure"):
        query["status"] = status
    if resource_type:
        query["resource_type"] = resource_type
    rq = _rx(q)
    if rq:
        query["$or"] = [{"resource_id": rq}, {"action": rq}]
    rng: Dict[str, Any] = {}
    for key, value, shift in (("$gte", from_, 0), ("$lt", to, 1)):
        if value:
            try:
                rng[key] = datetime.strptime(value[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc) \
                    + timedelta(days=shift)
            except ValueError:
                raise HTTPException(status_code=422, detail="Dates must be YYYY-MM-DD")
    if rng:
        query["at"] = rng

    skip = (page - 1) * limit
    total = await db.audit_logs.count_documents(query)
    cursor = db.audit_logs.find(query).sort("at", -1).skip(skip).limit(limit)
    logs = []
    async for log in cursor:
        d = _clean(log)
        d["id"] = d.pop("_id", None)
        d["details"] = _audit.redact(d.get("details") or {})
        logs.append(d)
    categories = []
    try:
        categories = sorted(c for c in await db.audit_logs.distinct("category") if c)
    except Exception:
        pass

    return {
        "success": True,
        "audit_logs": logs,
        "items": logs,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": max(1, (total + limit - 1) // limit),
        "categories": categories,
    }


# ── Impersonation ───────────────────────────────────────────────────────────

@router.post("/impersonate")
async def impersonate_organization(
    body: ImpersonateRequest,
    request: Request,
    response: Response,
    ctx: TenantContext = Depends(require_platform_role("super_admin")),
):
    """Time-limited, audited support view of an organization (acts as its
    Admin). A reason is mandatory."""
    from app.auth.service import set_session_cookie, create_tracked_session
    from app.auth.tenant import impersonation_expiry

    reason = (body.reason or "").strip()
    if len(reason) < 5:
        raise HTTPException(status_code=422, detail="A reason (at least 5 characters) is required to impersonate")
    db = _db()
    try:
        org = await db.organizations.find_one({"_id": ObjectId(body.organization_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid organization ID")
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    if org.get("status") == "archived":
        raise HTTPException(status_code=409, detail="Archived organizations cannot be impersonated")

    impersonated_user = {
        "user_id": ctx.user_id,
        "email": ctx.email,
        "name": ctx.name,
        "role": "owner",
        "scope": "site",
        "organization_id": str(org["_id"]),
        "organization_name": org.get("name", "Organization"),
        "organization_slug": org.get("slug", "org"),
        "org_role": "owner",
        "is_platform_admin": True,
        "platform_role": "super_admin",
        "impersonated_by": ctx.email,
        "impersonation_reason": reason[:300],
        "impersonation_expires_at": impersonation_expiry(),
    }
    ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    tracked = create_tracked_session(impersonated_user, ip=ip, user_agent=user_agent,
                                     impersonated_by=ctx.email, impersonation_reason=reason[:300])
    set_session_cookie(response, tracked)

    await _audit.aaudit("impersonation.start", "security", user=ctx.audit_user(),
                        organization_id=str(org["_id"]), resource_type="organization",
                        resource_id=str(org["_id"]),
                        details={"reason": reason[:300],
                                 "expires_at": impersonated_user["impersonation_expires_at"]},
                        **_audit.request_meta(request))

    return {
        "success": True,
        "message": f"Now impersonating organization: {org.get('name')}",
        "expires_at": impersonated_user["impersonation_expires_at"],
        "redirect": "/",
        "organization": {
            "id": str(org["_id"]),
            "name": org.get("name"),
            "slug": org.get("slug"),
        },
    }


@router.post("/impersonate/exit")
async def exit_impersonation(
    request: Request,
    response: Response,
):
    """Exit impersonation and return to super admin session."""
    from app.auth.service import session_user, set_session_cookie, clear_session_cookie, create_tracked_session

    user = session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not signed in")

    impersonated_by = user.get("impersonated_by")
    if not impersonated_by:
        raise HTTPException(status_code=400, detail="Not currently impersonating")
    from app.auth.roles import effective_role
    if effective_role(impersonated_by) != "super_admin":
        clear_session_cookie(response)
        raise HTTPException(status_code=403, detail="Impersonator is no longer a Super Admin")
    if user.get("session_id"):
        from app.auth.service import revoke_session
        revoke_session(user["session_id"], revoked_by="impersonation_exit")

    original_user = {
        "user_id": user["user_id"],
        "email": impersonated_by,
        "name": "Admin",
        "role": "super_admin",
        "scope": "admin",
        "organization_id": "",
        "organization_name": "",
        "organization_slug": "",
        "org_role": "owner",
        "is_platform_admin": True,
        "platform_role": "super_admin",
    }

    ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    tracked = create_tracked_session(original_user, ip=ip, user_agent=user_agent)
    set_session_cookie(response, tracked)
    await _audit.aaudit("impersonation.stop", "security", user=impersonated_by,
                        organization_id=user.get("organization_id") or None,
                        details={"organization_id": user.get("organization_id")},
                        ip=ip, user_agent=user_agent)

    return {"success": True, "message": "Exited impersonation", "redirect": "/superadmin"}
