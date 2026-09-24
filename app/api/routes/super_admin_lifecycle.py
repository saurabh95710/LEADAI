"""
Super Admin — customer lifecycle & platform control endpoints.

  Demo management
    GET  /api/super-admin/demo-requests                 inbox (search/filter/sort/paginate)
    GET  /api/super-admin/demo-requests/{id}            detail + demo usage/tokens
    POST /api/super-admin/demo-requests/{id}/approve    {overrides?}
    POST /api/super-admin/demo-requests/{id}/reject     {reason}
    POST /api/super-admin/demo-requests/{id}/extend     {days, tokens}
    POST /api/super-admin/demo-requests/{id}/cancel     {reason}
    GET|PUT /api/super-admin/demo-config
    GET|PUT /api/super-admin/token-costs
  Subscriptions & payments
    GET  /api/super-admin/subscriptions/queue           PENDING_ADMIN_CONFIRMATION
    POST /api/super-admin/subscriptions/{id}/confirm    -> ACTIVE (only path)
    POST /api/super-admin/subscriptions/{id}/status     {status, reason}
    GET  /api/super-admin/payments                      payments + events
  Tokens
    GET  /api/super-admin/tokens                        balances
    GET  /api/super-admin/tokens/{org_id}/ledger
    POST /api/super-admin/tokens/{org_id}/adjust        {delta, reason}
  Platform
    GET  /api/super-admin/notifications  | POST /api/super-admin/notifications/read
    GET  /api/super-admin/security-events
    GET|PUT /api/super-admin/role-permissions
    GET  /api/super-admin/email-outbox

Every endpoint requires the super_admin platform role; every mutation is audited.
"""
import re
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.admin.audit import aaudit, request_meta
from app.auth.tenant import TenantContext, require_platform_role
from app.db.mongo import get_async_db

router = APIRouter(prefix="/api/super-admin", tags=["super-admin-lifecycle"])
SUPER = require_platform_role("super_admin")


def _clean(d: Any) -> Any:
    if isinstance(d, ObjectId):
        return str(d)
    if isinstance(d, dict):
        return {("id" if k == "_id" else k): _clean(v) for k, v in d.items()}
    if isinstance(d, list):
        return [_clean(v) for v in d]
    if hasattr(d, "isoformat"):
        return d.isoformat()
    return d


def _db():
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return db


async def _page(coll, query: Dict[str, Any], *, page: int, limit: int, sort: str,
                projection: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    direction = -1 if sort.startswith("-") else 1
    field = sort.lstrip("-") or "created_at"
    total = await coll.count_documents(query)
    cursor = coll.find(query, projection).sort(field, direction).skip((page - 1) * limit).limit(limit)
    items = [_clean(d) async for d in cursor]
    return {"items": items, "total": total, "page": page, "limit": limit,
            "pages": max(1, -(-total // limit))}


def _search(q: Optional[str], fields: List[str]) -> Dict[str, Any]:
    if not q:
        return {}
    rx = {"$regex": re.escape(q.strip()), "$options": "i"}
    return {"$or": [{f: rx} for f in fields]}


def _actor(ctx: TenantContext) -> str:
    return ctx.email


# ── Demo management ────────────────────────────────────────────────────────

class ApproveBody(BaseModel):
    overrides: Optional[Dict[str, Any]] = None


class ReasonBody(BaseModel):
    reason: str = ""


class ExtendBody(BaseModel):
    days: int = 0
    tokens: int = 0


@router.get("/demo-requests")
async def list_demo_requests(status: Optional[str] = None, q: Optional[str] = None,
                             page: int = Query(1, ge=1), limit: int = Query(25, ge=1, le=200),
                             sort: str = "-created_at", ctx: TenantContext = Depends(SUPER)):
    db = _db()
    query: Dict[str, Any] = _search(q, ["name", "email", "company", "phone"])
    if status:
        query["status"] = status
    res = await _page(db.demo_requests, query, page=page, limit=limit, sort=sort,
                      projection={"ip": 0})
    counts = {}
    async for row in db.demo_requests.aggregate([{"$group": {"_id": "$status", "n": {"$sum": 1}}}]):
        counts[row["_id"]] = row["n"]
    res["counts"] = counts
    return {"success": True, **res}


@router.get("/demo-requests/{req_id}")
async def get_demo_request(req_id: str, ctx: TenantContext = Depends(SUPER)):
    from app.lifecycle.demo import demo_status, get_request
    req = get_request(req_id)
    req["demo"] = demo_status(req["organization_id"])
    db = _db()
    req["usage"] = {
        "searches": await db.search_history.count_documents({"organization_id": req["organization_id"]}),
        "leads": await db.ai_comments.count_documents({"organization_id": req["organization_id"],
                                                       "is_lead": True}),
    }
    return {"success": True, "request": req}


@router.post("/demo-requests/{req_id}/approve")
async def approve_demo(req_id: str, body: ApproveBody, request: Request,
                       ctx: TenantContext = Depends(SUPER)):
    from app.lifecycle.demo import approve
    try:
        return {"success": True, "request": approve(req_id, actor=_actor(ctx), overrides=body.overrides,
                                                    ip=request_meta(request)["ip"])}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/demo-requests/{req_id}/reject")
async def reject_demo(req_id: str, body: ReasonBody, request: Request,
                      ctx: TenantContext = Depends(SUPER)):
    from app.lifecycle.demo import reject
    return {"success": True, "request": reject(req_id, actor=_actor(ctx), reason=body.reason,
                                               ip=request_meta(request)["ip"])}


@router.post("/demo-requests/{req_id}/extend")
async def extend_demo(req_id: str, body: ExtendBody, request: Request,
                      ctx: TenantContext = Depends(SUPER)):
    from app.lifecycle.demo import extend
    return {"success": True, "request": extend(req_id, actor=_actor(ctx), days=body.days,
                                               extra_tokens=body.tokens,
                                               ip=request_meta(request)["ip"])}


@router.post("/demo-requests/{req_id}/cancel")
async def cancel_demo(req_id: str, body: ReasonBody, request: Request,
                      ctx: TenantContext = Depends(SUPER)):
    from app.lifecycle.demo import cancel
    return {"success": True, "request": cancel(req_id, actor=_actor(ctx), reason=body.reason,
                                               ip=request_meta(request)["ip"])}


@router.get("/demo-config")
async def read_demo_config(ctx: TenantContext = Depends(SUPER)):
    from app.lifecycle.config import KNOWN_PLATFORMS, get_demo_config
    return {"success": True, "config": get_demo_config(), "platforms": list(KNOWN_PLATFORMS)}


@router.put("/demo-config")
async def write_demo_config(body: Dict[str, Any], request: Request,
                            ctx: TenantContext = Depends(SUPER)):
    from app.lifecycle.config import get_demo_config, update_demo_config
    before = get_demo_config()
    try:
        cfg = update_demo_config(body, actor=ctx.email)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    await aaudit("demo_config.updated", "settings", user=ctx.audit_user(),
                 resource_type="platform_config", resource_id="demo",
                 details={"before": before, "after": cfg}, **request_meta(request))
    return {"success": True, "config": cfg}


@router.get("/token-costs")
async def read_token_costs(ctx: TenantContext = Depends(SUPER)):
    from app.lifecycle.config import get_token_costs
    return {"success": True, "costs": get_token_costs()}


@router.put("/token-costs")
async def write_token_costs(body: Dict[str, Any], request: Request,
                            ctx: TenantContext = Depends(SUPER)):
    from app.lifecycle.config import get_token_costs, update_token_costs
    before = get_token_costs()
    try:
        costs = update_token_costs(body, actor=ctx.email)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    await aaudit("token_costs.updated", "settings", user=ctx.audit_user(),
                 resource_type="platform_config", resource_id="token_costs",
                 details={"before": before, "after": costs}, **request_meta(request))
    return {"success": True, "costs": costs}


# ── Subscriptions & payments ───────────────────────────────────────────────

class StatusBody(BaseModel):
    status: str
    reason: str = ""


async def _with_org(db, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ids = {i.get("organization_id") for i in items if i.get("organization_id")}
    oids = [ObjectId(x) for x in ids if ObjectId.is_valid(str(x))]
    names = {str(o["_id"]): o.get("name") async for o in db.organizations.find({"_id": {"$in": oids}}, {"name": 1})}
    for i in items:
        i["organization_name"] = names.get(str(i.get("organization_id")))
    return items


@router.get("/subscriptions/queue")
async def confirmation_queue(page: int = Query(1, ge=1), limit: int = Query(25, ge=1, le=200),
                             ctx: TenantContext = Depends(SUPER)):
    db = _db()
    res = await _page(db.subscriptions, {"status": "pending_admin_confirmation"},
                      page=page, limit=limit, sort="created_at")
    res["items"] = await _with_org(db, res["items"])
    return {"success": True, **res}


@router.post("/subscriptions/{sub_id}/confirm")
async def confirm(sub_id: str, request: Request, ctx: TenantContext = Depends(SUPER)):
    from app.billing.subscriptions import confirm_subscription
    sub = await confirm_subscription(sub_id, actor={**ctx.audit_user(), **request_meta(request)})
    return {"success": True, "subscription": _clean(sub)}


@router.post("/subscriptions/{sub_id}/status")
async def change_status(sub_id: str, body: StatusBody, request: Request,
                        ctx: TenantContext = Depends(SUPER)):
    from app.billing.subscriptions import set_subscription_status
    sub = await set_subscription_status(sub_id, body.status, actor=ctx.audit_user(),
                                        reason=body.reason)
    return {"success": True, "subscription": _clean(sub)}


@router.get("/payments")
async def list_payments(status: Optional[str] = None, organization_id: Optional[str] = None,
                        refund_required: Optional[bool] = None, q: Optional[str] = None,
                        page: int = Query(1, ge=1), limit: int = Query(25, ge=1, le=200),
                        sort: str = "-created_at", ctx: TenantContext = Depends(SUPER)):
    db = _db()
    query: Dict[str, Any] = {}
    if status == "refunded":
        query["$or"] = [{"status": "refunded"}, {"refund_required": True}]
    elif status:
        query["status"] = status
    if organization_id:
        query["organization_id"] = organization_id
    if refund_required is not None:
        query["refund_required"] = True if refund_required else {"$ne": True}
    if q:
        rx = {"$regex": re.escape(q.strip()[:100]), "$options": "i"}
        ids = [str(o["_id"]) async for o in db.organizations.find({"name": rx}, {"_id": 1}).limit(500)]
        query["organization_id"] = {"$in": ids}
    if sort.lstrip("-") not in ("created_at", "updated_at", "amount", "status"):
        sort = "-created_at"
    res = await _page(db.payments, query, page=page, limit=limit, sort=sort,
                      projection={"provider_payment_id": 0})
    res["items"] = await _with_org(db, res["items"])
    for p in res["items"]:
        p["events"] = [_clean(e) async for e in db.payment_events.find(
            {"subscription_id": p.get("subscription_id")}).sort("created_at", 1).limit(20)]
        sub = await db.subscriptions.find_one({"_id": ObjectId(p["subscription_id"])},
                                              {"status": 1, "plan_id": 1}) \
            if ObjectId.is_valid(str(p.get("subscription_id") or "")) else None
        p["subscription_status"] = (sub or {}).get("status")
        p["plan_id"] = (sub or {}).get("plan_id")
        p["invoice_status"] = ("paid" if p.get("status") == "succeeded" and not p.get("refund_required")
                               else "refund_due" if p.get("refund_required")
                               else "void" if p.get("status") == "failed" else "open")
    # per status, amounts grouped by currency (never summed across
    # currencies); "amount" only when a single currency is involved
    from app.api.routes.super_admin_platform import norm_currency, single_amount
    summary: Dict[str, Any] = {}
    async for row in db.payments.aggregate([{"$group": {"_id": {"s": "$status", "c": "$currency"},
                                                        "n": {"$sum": 1},
                                                        "amount": {"$sum": "$amount"}}}]):
        key = row.get("_id") or {}
        entry = summary.setdefault(str(key.get("s")), {"count": 0, "amount_by_currency": {}})
        entry["count"] += int(row.get("n") or 0)
        cur = norm_currency(key.get("c"))
        entry["amount_by_currency"][cur] = round(entry["amount_by_currency"].get(cur, 0.0)
                                                 + float(row.get("amount") or 0), 2)
    currencies: set = set()
    for entry in summary.values():
        entry["amount_by_currency"] = dict(sorted(entry["amount_by_currency"].items()))
        entry["amount"] = single_amount(entry["amount_by_currency"])
        currencies.update(entry["amount_by_currency"])
    summary_currencies = sorted(currencies)
    summary["refund_required"] = {"count": await db.payments.count_documents({"refund_required": True})}
    res["summary"] = summary
    res["currencies"] = summary_currencies
    return {"success": True, **res}


# ── Tokens ─────────────────────────────────────────────────────────────────

class AdjustBody(BaseModel):
    delta: int
    reason: str


@router.get("/tokens")
async def token_balances(q: Optional[str] = None, page: int = Query(1, ge=1),
                         limit: int = Query(25, ge=1, le=200), sort: str = "-used",
                         ctx: TenantContext = Depends(SUPER)):
    db = _db()
    query: Dict[str, Any] = {}
    if q:
        # filter BEFORE paginating (organization name -> ids)
        rx = {"$regex": re.escape(q.strip()[:100]), "$options": "i"}
        ids = [str(o["_id"]) async for o in db.organizations.find({"name": rx}, {"_id": 1}).limit(1000)]
        query["organization_id"] = {"$in": ids}
    if sort.lstrip("-") not in ("used", "remaining", "allocated", "updated_at", "expires_at"):
        sort = "-used"
    res = await _page(db.token_balances, query, page=page, limit=limit, sort=sort)
    res["items"] = await _with_org(db, res["items"])
    for i in res["items"]:
        alloc = int(i.get("allocated") or 0)
        i["percentage"] = round(int(i.get("used") or 0) * 100 / alloc, 1) if alloc else 0
    return {"success": True, **res}


@router.get("/tokens/{org_id}/ledger")
async def token_ledger(org_id: str, page: int = Query(1, ge=1),
                       limit: int = Query(50, ge=1, le=500), ctx: TenantContext = Depends(SUPER)):
    db = _db()
    res = await _page(db.token_ledger, {"organization_id": org_id}, page=page, limit=limit,
                      sort="-created_at")
    from app.billing.tokens import get_balance
    res["balance"] = get_balance(org_id)
    return {"success": True, **res}


@router.post("/tokens/{org_id}/adjust")
async def token_adjust(org_id: str, body: AdjustBody, request: Request,
                       ctx: TenantContext = Depends(SUPER)):
    from app.billing.tokens import adjust, allocate, get_balance
    if not body.reason.strip():
        raise HTTPException(status_code=422, detail="A reason is required")
    if get_balance(org_id) is None:
        if body.delta <= 0:
            raise HTTPException(status_code=400, detail="Organization has no token balance")
        bal = allocate(org_id, body.delta, source="manual", actor=ctx.email, reason=body.reason)
    else:
        bal = adjust(org_id, body.delta, actor=ctx.email, reason=body.reason)
    await aaudit("tokens.adjusted", "billing", user=ctx.audit_user(), organization_id=org_id,
                 resource_type="token_balance", resource_id=org_id,
                 details={"delta": body.delta, "reason": body.reason}, **request_meta(request))
    return {"success": True, "balance": bal}


# ── Notifications / security / roles / outbox ─────────────────────────────

class ReadBody(BaseModel):
    id: Optional[str] = None


@router.get("/notifications")
async def super_notifications(unread: bool = False, page: int = Query(1, ge=1),
                              limit: int = Query(30, ge=1, le=200),
                              ctx: TenantContext = Depends(SUPER)):
    from app.events.notifications import list_for
    res = await list_for({"audience": "super_admin"}, ctx.email, unread_only=unread,
                         limit=limit, skip=(page - 1) * limit)
    return {"success": True, **res}


@router.post("/notifications/read")
async def super_notifications_read(body: ReadBody, ctx: TenantContext = Depends(SUPER)):
    from app.events.notifications import mark_read
    n = await mark_read({"audience": "super_admin"}, ctx.email, body.id)
    return {"success": True, "updated": n}


@router.get("/security-events")
async def security_events(type: Optional[str] = None, severity: Optional[str] = None,
                          q: Optional[str] = None, page: int = Query(1, ge=1),
                          limit: int = Query(50, ge=1, le=500), sort: str = "-at",
                          ctx: TenantContext = Depends(SUPER)):
    db = _db()
    query: Dict[str, Any] = _search(q, ["actor_email", "ip", "path"])
    if type:
        query["type"] = type
    if severity:
        query["severity"] = severity
    res = await _page(db.security_events, query, page=page, limit=limit, sort=sort)
    counts = {}
    async for row in db.security_events.aggregate([{"$group": {"_id": "$type", "n": {"$sum": 1}}}]):
        counts[row["_id"]] = row["n"]
    res["counts"] = counts
    return {"success": True, **res}


@router.get("/role-permissions")
async def role_permissions(ctx: TenantContext = Depends(SUPER)):
    from app.auth import permissions as P
    matrix_org = {r: sorted(P.global_org_role_permissions(r)) for r in P.ORG_ROLE_PERMISSIONS}
    matrix_platform = {r: sorted(P.effective_platform_permissions(r)) for r in P.PLATFORM_ROLE_PERMISSIONS}
    return {"success": True, "org": matrix_org, "platform": matrix_platform,
            "all_org_permissions": sorted(P.ALL_ORG_PERMISSIONS),
            "all_platform_permissions": sorted(P.ALL_PLATFORM_PERMISSIONS),
            "labels": P.SPEC_ROLE_LABELS,
            "locked_roles": ["owner", "super_admin"]}


class MatrixBody(BaseModel):
    kind: str          # "org" | "platform"
    role: str
    permissions: List[str]


@router.put("/role-permissions")
async def set_role_permissions(body: MatrixBody, request: Request,
                               ctx: TenantContext = Depends(SUPER)):
    from app.auth import permissions as P
    if body.kind == "org":
        if body.role not in P.ORG_ROLE_PERMISSIONS or body.role == "owner":
            raise HTTPException(status_code=422, detail="This organization role cannot be edited")
        allowed = P.ALL_ORG_PERMISSIONS
    elif body.kind == "platform":
        if body.role not in P.PLATFORM_ROLE_PERMISSIONS or body.role == "super_admin":
            raise HTTPException(status_code=422, detail="The Super Admin role is immutable")
        allowed = P.ALL_PLATFORM_PERMISSIONS
    else:
        raise HTTPException(status_code=422, detail="kind must be org or platform")
    unknown = [p for p in body.permissions if p not in allowed]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown permissions: {', '.join(unknown)}")
    db = _db()
    before = sorted(P.global_org_role_permissions(body.role) if body.kind == "org"
                    else P.effective_platform_permissions(body.role))
    await db[P.ROLE_MATRIX_COLLECTION].update_one(
        {"kind": body.kind, "role": body.role},
        {"$set": {"permissions": sorted(set(body.permissions)), "updated_by": ctx.email}},
        upsert=True)
    P.invalidate_permission_cache()
    await aaudit("role_permissions.updated", "security", user=ctx.audit_user(),
                 resource_type="role", resource_id=f"{body.kind}:{body.role}",
                 details={"before": before, "after": sorted(set(body.permissions))},
                 **request_meta(request))
    return {"success": True}


_LINK_TOKEN_RE = re.compile(r"(token=)[A-Za-z0-9_\-\.%]+")


def redact_email_body(body: Any) -> str:
    """One-time links (password reset / account setup / invitations) are
    bearer secrets: never show them, even to a Super Admin."""
    return _LINK_TOKEN_RE.sub(r"\1••••", str(body or ""))[:4000]


@router.get("/email-outbox")
async def email_outbox(status: Optional[str] = None, kind: Optional[str] = None,
                       q: Optional[str] = None, page: int = Query(1, ge=1),
                       limit: int = Query(25, ge=1, le=200), ctx: TenantContext = Depends(SUPER)):
    db = _db()
    query: Dict[str, Any] = {}
    if status:
        query["status"] = status
    if kind:
        query["kind"] = kind
    query.update(_search(q, ["to", "subject"]))
    res = await _page(db.email_outbox, query, page=page, limit=limit, sort="-created_at")
    for item in res["items"]:
        item["body"] = redact_email_body(item.get("body"))
    return {"success": True, **res}
