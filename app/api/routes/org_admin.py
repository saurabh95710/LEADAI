"""
Organization Admin Portal API (one portal per organization, strictly own-org).

Every endpoint:
  * requires an org owner/admin (``require_org_admin``) or a specific org
    permission, AND an organization whose Admin portal is enabled
    (``admin_portal_enabled`` or status == "active"; otherwise 403
    ``{code: "admin_portal_disabled"}``);
  * scopes every query by ``ctx.organization_id`` (never by an id from the
    request) — ids of other organizations answer 404 and are logged;
  * audits every mutation / export;
  * returns paginated lists as ``{items, total, page, limit, pages}``.

Existing endpoints are reused by the portal instead of being duplicated here:
organization profile/settings/roles/team (/api/organizations/current*),
lead detail/update/notes (/api/leads/*), URL search (/api/url/search),
search detail/cancel (/api/search/*), pages CSV (/api/export/pages.csv),
billing (/api/billing/*), notifications (/api/notifications),
sessions/password (/api/auth/*).

  GET  /context                       org, caller, caps, permission catalog
  GET  /overview                      dashboard
  GET  /users                         members (+usage), search/filter/sort/paginate
  GET  /users/{user_id}               member detail: usage, searches, leads, activity
  POST /users/{user_id}/reset-access  email a one-time password reset link
  GET  /invitations                   invitations by status
  GET  /roles                         configurable roles + delegable permission catalog
  GET  /searches                      all org searches (filters)
  GET  /searches/{run_id}             one run with result counts
  GET  /leads                         all / assigned / unassigned leads (filters)
  GET  /leads/pipeline                lifecycle counts + owners
  GET/PUT /lead-rules, POST /lead-rules/test   org lead keywords
  GET  /data/{pages|posts|comments}   org-wide scraped data
  GET  /apify/summary, /apify/jobs, /apify/runs   Apify activity (view only)
  GET  /analytics                     charts data for a date range
  GET  /billing/history               subscriptions + payments
  GET  /exports                       export history
  GET  /exports/{kind}.csv            users|activity|usage|leads|searches|posts|comments
  GET  /audit-logs, /audit-logs/facets, /audit-logs.csv
  GET/POST /support/tickets, GET /support/tickets/{id},
  POST /support/tickets/{id}/messages, POST /support/tickets/{id}/status
  GET/PATCH /profile                  own name + notification preferences
"""
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.admin.audit import aaudit, redact, request_meta
from app.auth import permissions as P
from app.auth.tenant import (
    TenantContext,
    _permission_denied,
    assert_can_manage_member,
    org_match,
    report_out_of_scope,
    require_org_admin,
    scope_query,
    stamp,
)
from app.db.models import utcnow
from app.db.mongo import get_async_db, get_sync_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/org-admin", tags=["org-admin"])

MAX_LIMIT = 100
_OWN = {"owner_field": "user_id", "legacy_email_field": "created_by"}
_LEAD = {**_OWN, "assigned_field": "assigned_user_id"}

# Human labels for the permissions an Admin may delegate (UI catalog). Only
# keys that are in DELEGABLE_ORG_PERMISSIONS are ever returned.
PERMISSION_CATALOG = [
    ("Searches", [
        (P.SEARCH_CREATE, "Run searches", "Start URL searches and collect posts/comments"),
        (P.SEARCH_VIEW, "View searches", "See search runs, pages, posts and comments"),
        (P.SEARCH_CANCEL, "Cancel & delete searches", "Stop running searches or delete results"),
        (P.SEARCH_EXPORT, "Export search results", "Download search results"),
    ]),
    ("Leads", [
        (P.LEADS_VIEW, "View leads", "See leads they own or are assigned"),
        (P.LEADS_MANAGE, "Manage leads", "Change status, priority, notes and follow-ups"),
        (P.LEADS_ASSIGN, "Assign leads", "Assign leads to team members"),
        (P.LEADS_EXPORT, "Export leads", "Download leads as CSV"),
    ]),
    ("Exports", [
        (P.EXPORTS_VIEW, "View exports", "See the export history"),
        (P.EXPORTS_CREATE, "Create exports", "Download CSV exports"),
    ]),
    ("Team", [
        (P.MEMBERS_VIEW, "View team", "See the member list"),
        (P.MEMBERS_INVITE, "Invite users", "Send, resend and revoke invitations"),
        (P.MEMBERS_UPDATE, "Edit users", "Change roles and reset access"),
        (P.MEMBERS_SUSPEND, "Suspend users", "Deactivate, suspend and restore users"),
        (P.MEMBERS_DELETE, "Remove users", "Remove users from the organization"),
        (P.MEMBERS_MANAGE, "Manage users", "General team management"),
    ]),
    ("Organization", [
        (P.WORKSPACE_VIEW, "Access workspace", "Sign in to the organization workspace"),
        (P.SETTINGS_VIEW, "View settings", "See organization settings"),
        (P.SETTINGS_MANAGE, "Org settings", "Change organization profile and settings"),
        (P.ORG_BILLING_VIEW, "View billing", "See plan, usage and invoices"),
        (P.ORG_AUDIT_VIEW, "View audit logs", "See the organization audit trail"),
        (P.DATA_VIEW_ALL, "See all org data", "See every member's searches and leads"),
    ]),
]

LEAD_STATUSES = ("new", "contacted", "qualified", "follow_up",
                 "converted", "lost", "disqualified", "archived")
TICKET_CATEGORIES = ("question", "bug", "billing", "feature", "account", "other")
TICKET_PRIORITIES = ("low", "normal", "high", "urgent")
TICKET_STATUSES = ("open", "in_progress", "waiting", "resolved", "closed")


# ═══════════════════════════════════════════════════════════════════════════
# Guards
# ═══════════════════════════════════════════════════════════════════════════

def admin_portal_enabled(org: Optional[Dict[str, Any]]) -> bool:
    """Same rule as GET /api/auth/me: confirmed subscription or explicit flag."""
    org = org or {}
    return bool(org.get("admin_portal_enabled")) or org.get("status") == "active"


def _assert_portal(ctx: TenantContext) -> None:
    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    try:
        org = db.organizations.find_one({"_id": ObjectId(ctx.organization_id)},
                                        {"admin_portal_enabled": 1, "status": 1})
    except Exception:
        org = None
    if not admin_portal_enabled(org):
        raise HTTPException(status_code=403, detail={
            "code": "admin_portal_disabled",
            "message": "The Admin portal unlocks after your subscription is confirmed."})


def require_portal(perm: Optional[str] = None):
    """Org owner/admin of an organization whose Admin portal is enabled
    (+ an optional specific org permission)."""
    admin_dep = require_org_admin()

    def _dep(request: Request, ctx: TenantContext = Depends(admin_dep)) -> TenantContext:
        if perm and perm not in ctx.permissions:
            _permission_denied(request, ctx, perm)
        _assert_portal(ctx)
        return ctx
    return _dep


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _db():
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return db


def _ser(doc: Any) -> Any:
    if isinstance(doc, dict):
        return {("id" if k == "_id" else k): _ser(v) for k, v in doc.items()
                if k not in ("token_hash", "password_hash")}
    if isinstance(doc, list):
        return [_ser(v) for v in doc]
    if isinstance(doc, ObjectId):
        return str(doc)
    if isinstance(doc, datetime):
        if doc.tzinfo is None:
            doc = doc.replace(tzinfo=timezone.utc)
        return doc.isoformat()
    return doc


def _oid(value: Any) -> Optional[ObjectId]:
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _paged(items: List[Any], total: int, page: int, limit: int, **extra) -> Dict[str, Any]:
    return {"success": True, "items": items, "total": total, "page": page, "limit": limit,
            "pages": (total + limit - 1) // limit if limit else 0, **extra}


def _parse_date(value: Optional[str], *, end: bool = False) -> Optional[datetime]:
    """ISO date/datetime -> naive UTC (Mongo stores naive UTC)."""
    if not value:
        return None
    raw = value.strip()
    try:
        d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid date '{value}' (use YYYY-MM-DD)")
    if d.tzinfo is not None:
        d = d.astimezone(timezone.utc).replace(tzinfo=None)
    if end and len(raw) <= 10:
        d = d.replace(hour=23, minute=59, second=59, microsecond=999000)
    return d


def _date_filter(field: str, date_from: Optional[str], date_to: Optional[str]) -> Dict[str, Any]:
    rng: Dict[str, Any] = {}
    start, end = _parse_date(date_from), _parse_date(date_to, end=True)
    if start:
        rng["$gte"] = start
    if end:
        rng["$lte"] = end
    return {field: rng} if rng else {}


def _as_naive(v: Any) -> Optional[datetime]:
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).replace(tzinfo=None) if v.tzinfo else v
    if isinstance(v, (int, float)):
        try:
            return datetime.utcfromtimestamp(v)
        except Exception:
            return None
    if isinstance(v, str):
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return d.astimezone(timezone.utc).replace(tzinfo=None) if d.tzinfo else d
        except ValueError:
            return None
    return None


def _regex(q: str) -> Dict[str, Any]:
    return {"$regex": re.escape(q.strip()[:120]), "$options": "i"}


def _and(*clauses: Dict[str, Any]) -> Dict[str, Any]:
    parts = [c for c in clauses if c]
    if not parts:
        return {}
    return parts[0] if len(parts) == 1 else {"$and": parts}


def _org_q(ctx: TenantContext) -> Dict[str, Any]:
    return {"organization_id": org_match(ctx.organization_id)}


async def _org_doc(db, ctx: TenantContext) -> Dict[str, Any]:
    org = await db.organizations.find_one({"_id": ObjectId(ctx.organization_id)})
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


async def _user_map(db, ids) -> Dict[str, Dict[str, Any]]:
    oids = [o for o in (_oid(i) for i in set(i for i in ids if i)) if o is not None]
    out: Dict[str, Dict[str, Any]] = {}
    if not oids:
        return out
    async for u in db.users.find({"_id": {"$in": oids}}, {"name": 1, "email": 1}):
        out[str(u["_id"])] = {"name": u.get("name"), "email": u.get("email")}
    return out


async def _member_ids(db, ctx: TenantContext) -> List[str]:
    return [m["user_id"] async for m in db.organization_members.find(
        {"organization_id": ctx.organization_id, "status": {"$ne": "removed"}}, {"user_id": 1})]


async def _audit(ctx: TenantContext, request: Request, action: str, category: str, *,
                 resource_type: Optional[str] = None, resource_id: Optional[str] = None,
                 details: Optional[Dict[str, Any]] = None, success: bool = True) -> None:
    await aaudit(action, category, user=ctx.audit_user(), organization_id=ctx.organization_id,
                 resource_type=resource_type, resource_id=resource_id, details=details or {},
                 success=success, **request_meta(request))


async def _get_member_or_404(db, ctx: TenantContext, user_id: str,
                             request: Request) -> Dict[str, Any]:
    membership = await db.organization_members.find_one(
        {"organization_id": ctx.organization_id, "user_id": str(user_id),
         "status": {"$ne": "removed"}})
    if not membership:
        other = await db.organization_members.find_one(
            {"user_id": str(user_id), "organization_id": {"$ne": ctx.organization_id}},
            {"_id": 1, "organization_id": 1})
        if other:
            report_out_of_scope(request, ctx, "organization_members", other)
        raise HTTPException(status_code=404, detail="Team member not found")
    return membership


async def _count_by(db, coll: str, match: Dict[str, Any], field: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    async for row in db[coll].aggregate([{"$match": match},
                                         {"$group": {"_id": f"${field}", "n": {"$sum": 1}}}]):
        out[str(row["_id"]) if row["_id"] is not None else ""] = row["n"]
    return out


def _run_platform(run: Dict[str, Any]) -> str:
    return ((run.get("intent") or {}).get("platform") or run.get("platform") or "unknown")


def _run_row(run: Dict[str, Any], users: Dict[str, Dict[str, Any]],
             leads: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    uid = run.get("user_id") or ""
    u = users.get(uid) or {}
    started, finished = _as_naive(run.get("created_at")), _as_naive(run.get("completed_at"))
    info = redact(run.get("scrape_info") or {}) if isinstance(run.get("scrape_info"), dict) else {}
    return {
        "run_id": run.get("run_id"),
        "query": run.get("query"),
        "url": (run.get("intent") or {}).get("canonical_url") or run.get("query"),
        "platform": _run_platform(run),
        "status": run.get("status"),
        "phase": run.get("phase"),
        "message": run.get("message"),
        "error": run.get("error"),
        "pages_found": run.get("pages_found", 0),
        "pages_stored": run.get("pages_stored", 0),
        "leads": (leads or {}).get(run.get("run_id") or "", 0),
        "user_id": uid,
        "user_name": u.get("name"),
        "user_email": u.get("email") or run.get("created_by"),
        "created_at": _ser(run.get("created_at")),
        "completed_at": _ser(run.get("completed_at")),
        "duration_seconds": int((finished - started).total_seconds())
        if started and finished else None,
        "apify_run_id": info.get("runId"),
        "dataset_id": info.get("datasetId"),
        "actor": info.get("actorId") or info.get("actor"),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Context & dashboard
# ═══════════════════════════════════════════════════════════════════════════

def _permission_catalog() -> List[Dict[str, Any]]:
    out = []
    for group, perms in PERMISSION_CATALOG:
        items = [{"key": k, "label": label, "description": desc}
                 for k, label, desc in perms if k in P.DELEGABLE_ORG_PERMISSIONS]
        if items:
            out.append({"group": group, "permissions": items})
    return out


def _caps(org_id: str) -> Dict[str, Any]:
    caps: Dict[str, Any] = {}
    try:
        from app.billing.entitlements import EntitlementService
        caps = dict(EntitlementService.get_run_caps(org_id) or {})
    except Exception:
        pass
    try:
        from app.admin.settings import effective_limits
        lim = effective_limits()
        caps["platform_max_posts"] = lim.get("max_posts_cap")
        caps["platform_max_comments_per_post"] = lim.get("max_comments_per_post_cap")
        caps["default_posts"] = lim.get("max_posts_default")
        caps["default_comments_per_post"] = lim.get("max_comments_per_post_default")
    except Exception:
        pass
    return caps


@router.get("/context")
async def portal_context(ctx: TenantContext = Depends(require_portal())):
    db = _db()
    org = await _org_doc(db, ctx)
    return {"success": True,
            "organization": {"id": ctx.organization_id, "name": org.get("name"),
                             "slug": org.get("slug"), "status": org.get("status"),
                             "logo_url": org.get("logo_url"),
                             "branding": _ser(org.get("branding") or {})},
            "me": {"user_id": ctx.user_id, "email": ctx.email, "name": ctx.name,
                   "role": ctx.org_role, "role_label": P.SPEC_ROLE_LABELS.get(ctx.org_role),
                   "permissions": ctx.permissions,
                   "impersonated_by": ctx.impersonated_by},
            "caps": _caps(ctx.organization_id),
            "lead_statuses": list(LEAD_STATUSES),
            "role_labels": {r: P.SPEC_ROLE_LABELS[r] for r in ("admin", "manager", "member", "viewer")}}


@router.get("/overview")
async def overview(ctx: TenantContext = Depends(require_portal())):
    db = _db()
    org = await _org_doc(db, ctx)
    oq = _org_q(ctx)
    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    week_ago = now - timedelta(days=7)

    member_status = await _count_by(db, "organization_members",
                                    {"organization_id": ctx.organization_id,
                                     "status": {"$ne": "removed"}}, "status")
    pending_invites = await db.organization_invitations.count_documents(
        {"organization_id": ctx.organization_id, "status": "pending"})
    search_status = await _count_by(db, "search_history", oq, "status")
    searches_month = await db.search_history.count_documents(
        _and(oq, {"created_at": {"$gte": month_start}}))
    lead_q = _and(oq, {"is_lead": True})
    leads_total = await db.ai_comments.count_documents(lead_q)
    leads_month = await db.ai_comments.count_documents(
        _and(lead_q, {"analyzed_at": {"$gte": month_start}}))
    unassigned = await db.ai_comments.count_documents(
        _and(lead_q, {"$or": [{"assigned_user_id": None}, {"assigned_user_id": ""},
                              {"assigned_user_id": {"$exists": False}}]}))

    from app.billing.entitlements import EntitlementService
    from app.billing.subscriptions import get_organization_subscription
    usage = await EntitlementService.get_usage_summary(ctx.organization_id, db=db)
    sub = await get_organization_subscription(ctx.organization_id, db=db)

    # recent lists
    recent_runs = [r async for r in db.search_history.find(oq).sort("created_at", -1).limit(6)]
    failed_runs = [r async for r in db.search_history.find(
        _and(oq, {"status": {"$in": ["error", "failed"]}})).sort("created_at", -1).limit(5)]
    recent_leads = [lead async for lead in db.ai_comments.find(lead_q).sort("analyzed_at", -1).limit(6)]
    recent_members = [m async for m in db.organization_members.find(
        {"organization_id": ctx.organization_id, "status": {"$ne": "removed"}}
    ).sort("_id", -1).limit(6)]
    uids = [r.get("user_id") for r in recent_runs + failed_runs] + \
        [m.get("user_id") for m in recent_members] + \
        [lead.get("assigned_user_id") for lead in recent_leads]
    users = await _user_map(db, uids)
    activity = []
    if P.ORG_AUDIT_VIEW in ctx.permissions:
        async for a in db.audit_logs.find({"organization_id": ctx.organization_id}) \
                .sort("at", -1).limit(8):
            activity.append({"action": a.get("action"), "category": a.get("category"),
                             "actor": a.get("actor_email"), "status": a.get("status"),
                             "resource_type": a.get("resource_type"), "at": _ser(a.get("at"))})

    # alerts
    settings = org.get("settings") or {}
    warn_at = int(settings.get("usage_warning_percent") or 80)
    alerts: List[Dict[str, Any]] = []
    for metric, m in (usage.get("metrics") or {}).items():
        if m.get("limit") and m.get("percentage", 0) >= warn_at:
            alerts.append({"severity": "danger" if m["percentage"] >= 100 else "warning",
                           "metric": metric,
                           "message": f"{metric.replace('_', ' ').title()}: {m['used']} of {m['limit']} used ({m['percentage']}%)."})
    tokens = usage.get("tokens") or {}
    token_pct = None
    if tokens and tokens.get("allocated"):
        token_pct = round(tokens.get("used", 0) / tokens["allocated"] * 100, 1)
        if token_pct >= warn_at:
            alerts.append({"severity": "danger" if tokens.get("remaining", 0) <= 0 else "warning",
                           "metric": "tokens",
                           "message": f"Tokens: {tokens.get('remaining', 0)} remaining ({token_pct}% used)."})
    if tokens and tokens.get("expired"):
        alerts.append({"severity": "danger", "metric": "tokens", "message": "Your token allowance has expired."})
    recent_failed = await db.search_history.count_documents(
        _and(oq, {"status": {"$in": ["error", "failed"]}, "created_at": {"$gte": week_ago}}))
    if recent_failed:
        alerts.append({"severity": "warning", "metric": "searches",
                       "message": f"{recent_failed} search(es) failed in the last 7 days."})
    if (sub or {}).get("pending"):
        alerts.append({"severity": "info", "metric": "subscription",
                       "message": "A plan change is awaiting payment or confirmation."})
    if (sub or {}).get("cancel_at_period_end"):
        alerts.append({"severity": "warning", "metric": "subscription",
                       "message": "Your subscription is scheduled to cancel at the end of the period."})

    inactive = sum(v for k, v in member_status.items() if k in ("inactive", "suspended"))
    return {
        "success": True,
        "organization": {"name": org.get("name"), "status": org.get("status")},
        "users": {"total": sum(member_status.values()), "active": member_status.get("active", 0),
                  "inactive": inactive, "pending_invitations": pending_invites},
        "searches": {"total": sum(search_status.values()), "this_month": searches_month,
                     "failed": search_status.get("error", 0) + search_status.get("failed", 0),
                     "running": search_status.get("running", 0),
                     "cancelled": search_status.get("cancelled", 0),
                     "completed": search_status.get("completed", 0)},
        "leads": {"total": leads_total, "this_month": leads_month, "unassigned": unassigned},
        "apify_jobs": search_status,
        "tokens": {**(tokens or {}), "percentage": token_pct} if tokens else None,
        "usage": usage,
        "subscription": _ser({k: (sub or {}).get(k) for k in (
            "status", "plan_id", "billing_cycle", "current_period_start", "current_period_end",
            "cancel_at_period_end", "has_subscription", "is_demo")}),
        "plan": {"name": (usage.get("plan") or {}).get("name"),
                 "slug": (usage.get("plan") or {}).get("slug"),
                 "is_demo": (usage.get("plan") or {}).get("is_demo")},
        "pending_subscription": bool((sub or {}).get("pending")),
        "recent_searches": [_run_row(r, users) for r in recent_runs],
        "failed_searches": [_run_row(r, users) for r in failed_runs],
        "recent_leads": [_lead_row(lead, users) for lead in recent_leads],
        "recent_users": [{"user_id": m.get("user_id"), "role": m.get("role"),
                          "status": m.get("status"),
                          **(users.get(m.get("user_id")) or {}),
                          "joined_at": _ser(m.get("joined_at") or m.get("created_at"))}
                         for m in recent_members],
        "activity": activity,
        "alerts": alerts,
    }


def _lead_row(lead: Dict[str, Any], users: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    assignee = users.get(lead.get("assigned_user_id") or "") or {}
    owner = users.get(lead.get("user_id") or "") or {}
    return {
        "id": str(lead["_id"]),
        "name": lead.get("commenter_name") or "Unknown",
        "text": (lead.get("comment_text") or "")[:280],
        "platform": lead.get("platform"),
        "page_name": lead.get("page_name"),
        "post_url": lead.get("post_url"),
        "score": lead.get("lead_score"),
        "quality": lead.get("lead_quality"),
        "priority": lead.get("lead_priority") or lead.get("priority"),
        "status": lead.get("lead_status") or "new",
        "intent": lead.get("intent"),
        "phone": lead.get("phone"),
        "email": lead.get("email"),
        "assigned_user_id": lead.get("assigned_user_id"),
        "assigned_name": assignee.get("name"),
        "assigned_email": assignee.get("email") or lead.get("assigned_to"),
        "owner_user_id": lead.get("user_id"),
        "owner_email": owner.get("email") or lead.get("created_by"),
        "search_run_id": lead.get("search_run_id"),
        "created_at": _ser(lead.get("lead_created_at") or lead.get("analyzed_at")),
        "updated_at": _ser(lead.get("lead_updated_at")),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Team
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/users")
async def list_users(
    q: Optional[str] = None, role: Optional[str] = None, status: Optional[str] = None,
    sort: str = Query("name", pattern="^(name|email|role|status|last_login|searches|leads|joined)$"),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=MAX_LIMIT),
    ctx: TenantContext = Depends(require_portal(P.MEMBERS_VIEW)),
):
    db = _db()
    members = [m async for m in db.organization_members.find(
        {"organization_id": ctx.organization_id, "status": {"$ne": "removed"}})]
    uids = [m["user_id"] for m in members]
    oids = [o for o in (_oid(u) for u in uids) if o is not None]
    users = {str(u["_id"]): u async for u in db.users.find(
        {"_id": {"$in": oids}}, {"name": 1, "email": 1, "last_login": 1, "status": 1})}
    oq = _org_q(ctx)
    searches = await _count_by(db, "search_history", oq, "user_id")
    leads = await _count_by(db, "ai_comments", _and(oq, {"is_lead": True}), "user_id")
    assigned = await _count_by(db, "ai_comments", _and(oq, {"is_lead": True}), "assigned_user_id")
    rows = []
    for m in members:
        u = users.get(m["user_id"]) or {}
        rows.append({
            "user_id": m["user_id"], "name": u.get("name") or "", "email": u.get("email") or m.get("email") or "",
            "role": m.get("role"), "role_label": P.SPEC_ROLE_LABELS.get(m.get("role"), m.get("role")),
            "status": m.get("status"), "account_status": u.get("status"),
            "last_login": _ser(u.get("last_login")),
            "joined_at": _ser(m.get("joined_at") or m.get("created_at")),
            "searches": searches.get(m["user_id"], 0), "leads": leads.get(m["user_id"], 0),
            "assigned_leads": assigned.get(m["user_id"], 0),
            "has_overrides": bool(m.get("permissions_override")),
            "is_me": m["user_id"] == ctx.user_id,
        })
    if q:
        needle = q.strip().lower()
        rows = [r for r in rows if needle in r["name"].lower() or needle in r["email"].lower()]
    if role:
        rows = [r for r in rows if r["role"] == role]
    if status:
        rows = [r for r in rows if r["status"] == status]
    field = {"joined": "joined_at"}.get(sort, sort)

    def _key(r):
        v = r.get(field)
        if isinstance(v, str):
            v = v.lower()
        return (v is None, v if v is not None else "")
    rows.sort(key=_key, reverse=order == "desc")
    total = len(rows)
    return _paged(rows[(page - 1) * limit: page * limit], total, page, limit)


@router.get("/users/{user_id}")
async def user_detail(user_id: str, request: Request,
                      ctx: TenantContext = Depends(require_portal(P.MEMBERS_VIEW))):
    db = _db()
    membership = await _get_member_or_404(db, ctx, user_id, request)
    org = await _org_doc(db, ctx)
    u = await db.users.find_one({"_id": _oid(user_id)}, {"name": 1, "email": 1, "last_login": 1,
                                                        "status": 1, "created_at": 1}) or {}
    oq = _org_q(ctx)
    own = _and(oq, {"user_id": str(user_id)})
    runs = [r async for r in db.search_history.find(own).sort("created_at", -1).limit(10)]
    lead_q = _and(oq, {"is_lead": True, "$or": [{"user_id": str(user_id)},
                                                 {"assigned_user_id": str(user_id)}]})
    leads = [lead async for lead in db.ai_comments.find(lead_q).sort("analyzed_at", -1).limit(10)]
    users = await _user_map(db, [user_id] + [lead.get("assigned_user_id") for lead in leads])
    activity = []
    async for a in db.audit_logs.find({"organization_id": ctx.organization_id,
                                       "actor_user_id": str(user_id)}).sort("at", -1).limit(20):
        activity.append({"action": a.get("action"), "category": a.get("category"),
                         "status": a.get("status"), "resource_type": a.get("resource_type"),
                         "resource_id": a.get("resource_id"), "ip": a.get("ip"),
                         "at": _ser(a.get("at"))})
    tokens_used = 0
    async for row in db.token_ledger.aggregate([
            {"$match": {"organization_id": ctx.organization_id, "user_id": str(user_id),
                        "type": "consume"}},
            {"$group": {"_id": None, "n": {"$sum": "$amount"}}}]):
        tokens_used = row.get("n") or 0
    role = membership.get("role", "member")
    return {"success": True, "user": {
        "user_id": str(user_id), "name": u.get("name"), "email": u.get("email"),
        "role": role, "role_label": P.SPEC_ROLE_LABELS.get(role, role),
        "status": membership.get("status"), "account_status": u.get("status"),
        "last_login": _ser(u.get("last_login")),
        "joined_at": _ser(membership.get("joined_at") or membership.get("created_at")),
        "permissions": sorted(P.resolve_org_permissions(role, org, membership)),
        "permissions_override": membership.get("permissions_override") or {},
        "configurable": role in P.CONFIGURABLE_ORG_ROLES,
        "is_me": str(user_id) == ctx.user_id,
    }, "usage": {
        "searches": await db.search_history.count_documents(own),
        "failed_searches": await db.search_history.count_documents(
            _and(own, {"status": {"$in": ["error", "failed"]}})),
        "leads": await db.ai_comments.count_documents(_and(own, {"is_lead": True})),
        "assigned_leads": await db.ai_comments.count_documents(
            _and(oq, {"is_lead": True, "assigned_user_id": str(user_id)})),
        "exports": await db.exports.count_documents(own),
        "tokens_used": tokens_used,
    }, "searches": [_run_row(r, users) for r in runs],
        "leads": [_lead_row(lead, users) for lead in leads],
        "activity": activity}


class ResetAccessBody(BaseModel):
    revoke_sessions: bool = True


@router.post("/users/{user_id}/reset-access")
async def reset_user_access(user_id: str, request: Request, body: Optional[ResetAccessBody] = None,
                            ctx: TenantContext = Depends(require_portal(P.MEMBERS_UPDATE))):
    """Email the member a single-use password reset link (never a password).
    Optionally signs them out everywhere."""
    from app.api.routes.auth import _RESET_TOKEN_TTL_MIN, _hash_token
    from app.auth.service import revoke_user_sessions
    from app.events.email import absolute_url, send_email
    from app.events.security import security_event_from_request
    body = body or ResetAccessBody()
    db = _db()
    membership = await _get_member_or_404(db, ctx, user_id, request)
    assert_can_manage_member(ctx, target_role=membership.get("role"),
                             target_user_id=user_id, request=request)
    record = await db.users.find_one({"_id": _oid(user_id)})
    if not record or not record.get("email"):
        raise HTTPException(status_code=404, detail="Team member not found")
    if record.get("status", "active") != "active":
        raise HTTPException(status_code=409, detail="This account is not active")
    token = secrets.token_urlsafe(32)
    now = utcnow()
    await db.password_resets.update_many({"user_id": str(user_id), "used_at": None},
                                         {"$set": {"used_at": now, "invalidated": True}})
    await db.password_resets.insert_one({
        "user_id": str(user_id), "token_hash": _hash_token(token),
        "expires_at": now + timedelta(minutes=_RESET_TOKEN_TTL_MIN), "used_at": None,
        "requested_ip": request_meta(request)["ip"], "requested_by": ctx.email,
        "organization_id": ctx.organization_id, "created_at": now})
    link = absolute_url(f"/reset-password?token={token}")
    send_email(record["email"], f"Reset your LeadAI access — {ctx.organization_name}",
               f"Hi {record.get('name') or ''},\n\nAn administrator of {ctx.organization_name} "
               f"asked us to reset your access. Use this link to choose a new password "
               f"(valid for {_RESET_TOKEN_TTL_MIN} minutes, single use):\n{link}\n\n"
               "If you didn't expect this, contact your organization admin.",
               kind="password_reset", organization_id=ctx.organization_id)
    revoked = 0
    if body.revoke_sessions:
        revoked = revoke_user_sessions(str(user_id), revoked_by=f"org_admin_reset:{ctx.email}")
    security_event_from_request(request, "password_reset_by_admin", "low", ctx=ctx,
                                details={"target_user_id": str(user_id)})
    await _audit(ctx, request, "member.access_reset", "team", resource_type="member",
                 resource_id=str(user_id),
                 details={"email": record["email"], "sessions_revoked": revoked})
    return {"success": True, "sessions_revoked": revoked,
            "message": f"A password reset link was emailed to {record['email']}."}


@router.get("/invitations")
async def list_invitations(
    status: str = Query("pending", pattern="^(pending|accepted|cancelled|expired|all)$"),
    q: Optional[str] = None,
    page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=MAX_LIMIT),
    ctx: TenantContext = Depends(require_portal(P.MEMBERS_INVITE)),
):
    db = _db()
    now = datetime.utcnow()
    query: Dict[str, Any] = {"organization_id": ctx.organization_id}
    if status == "pending":
        query.update({"status": "pending", "expires_at": {"$gt": now}})
    elif status == "expired":
        query["$or"] = [{"status": "expired"}, {"status": "pending", "expires_at": {"$lte": now}}]
    elif status != "all":
        query["status"] = status
    if q:
        query["email"] = _regex(q)
    total = await db.organization_invitations.count_documents(query)
    items = []
    inviter_ids = []
    docs = [d async for d in db.organization_invitations.find(query).sort("created_at", -1)
            .skip((page - 1) * limit).limit(limit)]
    inviter_ids = [d.get("invited_by") for d in docs]
    users = await _user_map(db, inviter_ids)
    for d in docs:
        exp = _as_naive(d.get("expires_at"))
        st = d.get("status")
        if st == "pending" and exp and exp <= now:
            st = "expired"
        items.append({"id": str(d["_id"]), "email": d.get("email"), "role": d.get("role"),
                      "status": st, "invited_by": (users.get(d.get("invited_by")) or {}).get("email"),
                      "created_at": _ser(d.get("created_at")), "expires_at": _ser(d.get("expires_at"))})
    return _paged(items, total, page, limit)


@router.get("/roles")
async def roles(ctx: TenantContext = Depends(require_portal(P.MEMBERS_VIEW))):
    db = _db()
    org = await _org_doc(db, ctx)
    cfg = ((org.get("settings") or {}).get("role_permissions") or {})
    counts = await _count_by(db, "organization_members",
                             {"organization_id": ctx.organization_id,
                              "status": {"$ne": "removed"}}, "role")
    out = []
    for role in P.CONFIGURABLE_ORG_ROLES:
        base = P.global_org_role_permissions(role)
        effective = P.resolve_org_permissions(role, org, None)
        out.append({"role": role, "label": P.SPEC_ROLE_LABELS.get(role, role),
                    "members": counts.get(role, 0),
                    "defaults": sorted(p for p in base if p in P.DELEGABLE_ORG_PERMISSIONS),
                    "overrides": {k: bool(v) for k, v in (cfg.get(role) or {}).items()
                                  if k in P.DELEGABLE_ORG_PERMISSIONS},
                    "effective": sorted(p for p in effective if p in P.DELEGABLE_ORG_PERMISSIONS)})
    return {"success": True, "roles": out, "catalog": _permission_catalog(),
            "can_manage": P.ROLES_MANAGE in ctx.permissions,
            "fixed_roles": [{"role": r, "label": P.SPEC_ROLE_LABELS[r], "members": counts.get(r, 0)}
                            for r in ("owner", "admin")],
            "shared_workspace": bool((org.get("settings") or {}).get("shared_workspace"))}


# ═══════════════════════════════════════════════════════════════════════════
# Searches / LeadAI data
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/searches")
async def list_searches(
    q: Optional[str] = None, user_id: Optional[str] = None, platform: Optional[str] = None,
    status: Optional[str] = None, date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    sort: str = Query("newest", pattern="^(newest|oldest|results|status)$"),
    page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=MAX_LIMIT),
    ctx: TenantContext = Depends(require_portal(P.SEARCH_VIEW)),
):
    db = _db()
    clauses = [scope_query(ctx, {}, **_OWN)]
    if q:
        clauses.append({"$or": [{"query": _regex(q)}, {"run_id": _regex(q)}]})
    if user_id:
        clauses.append({"user_id": str(user_id)})
    if platform:
        clauses.append({"intent.platform": platform})
    if status:
        clauses.append({"status": {"$in": ["error", "failed"]}} if status in ("error", "failed")
                       else {"status": status})
    clauses.append(_date_filter("created_at", date_from, date_to))
    query = _and(*clauses)
    sort_key = {"newest": [("created_at", -1)], "oldest": [("created_at", 1)],
                "results": [("pages_found", -1), ("created_at", -1)],
                "status": [("status", 1), ("created_at", -1)]}[sort]
    total = await db.search_history.count_documents(query)
    runs = [r async for r in db.search_history.find(query).sort(sort_key)
            .skip((page - 1) * limit).limit(limit)]
    run_ids = [r.get("run_id") for r in runs if r.get("run_id")]
    leads = await _count_by(db, "ai_comments",
                            _and(_org_q(ctx), {"is_lead": True, "search_run_id": {"$in": run_ids}}),
                            "search_run_id") if run_ids else {}
    users = await _user_map(db, [r.get("user_id") for r in runs])
    return _paged([_run_row(r, users, leads) for r in runs], total, page, limit)


@router.get("/searches/{run_id}")
async def search_detail(run_id: str, request: Request,
                        ctx: TenantContext = Depends(require_portal(P.SEARCH_VIEW))):
    db = _db()
    run = await db.search_history.find_one(scope_query(ctx, {"run_id": run_id}, **_OWN))
    if not run:
        other = await db.search_history.find_one({"run_id": run_id}, {"_id": 1, "organization_id": 1})
        if other:
            report_out_of_scope(request, ctx, "search_history", other)
        raise HTTPException(status_code=404, detail="Search run not found")
    oq = _org_q(ctx)
    page_ids = [str(p["_id"]) async for p in db.facebook_pages.find(
        _and(oq, {"search_run_id": run_id}), {"_id": 1})]
    post_ids = [str(p["_id"]) async for p in db.facebook_posts.find(
        _and(oq, {"page_ref": {"$in": page_ids}}), {"_id": 1})] if page_ids else []
    users = await _user_map(db, [run.get("user_id")])
    row = _run_row(run, users)
    row.update({
        "pages": len(page_ids), "posts": len(post_ids),
        "comments": await db.facebook_comments.count_documents(
            _and(oq, {"post_ref": {"$in": post_ids}})) if post_ids else 0,
        "leads": await db.ai_comments.count_documents(
            _and(oq, {"is_lead": True, "search_run_id": run_id})),
        "max_posts": run.get("limit"),
        "max_comments_per_post": (run.get("intent") or {}).get("max_comments_per_post"),
        "comment_filter": _ser(run.get("comment_filter")),
        "cancel_requested": bool(run.get("cancel_requested")),
    })
    return {"success": True, "search": row}


def _lead_filters(ctx: TenantContext, *, q=None, status=None, priority=None, platform=None,
                  quality=None, assignee=None, owner=None, min_score=None, max_score=None,
                  date_from=None, date_to=None, run_id=None) -> Dict[str, Any]:
    clauses: List[Dict[str, Any]] = [scope_query(ctx, {"is_lead": True}, **_LEAD)]
    if q:
        rx = _regex(q)
        clauses.append({"$or": [{"commenter_name": rx}, {"comment_text": rx}, {"page_name": rx},
                                {"phone": rx}, {"email": rx}, {"requirement": rx}]})
    if status:
        clauses.append({"lead_status": {"$in": ["new", None]}} if status == "new"
                       else {"lead_status": status})
    if priority:
        clauses.append({"$or": [{"lead_priority": priority}, {"priority": priority}]})
    if platform:
        clauses.append({"platform": platform})
    if quality:
        clauses.append({"lead_quality": quality})
    if assignee == "unassigned":
        clauses.append({"$or": [{"assigned_user_id": None}, {"assigned_user_id": ""},
                                {"assigned_user_id": {"$exists": False}}]})
    elif assignee == "assigned":
        clauses.append({"assigned_user_id": {"$nin": [None, ""], "$exists": True}})
    elif assignee:
        clauses.append({"assigned_user_id": str(assignee)})
    if owner:
        clauses.append({"user_id": str(owner)})
    if run_id:
        clauses.append({"search_run_id": run_id})
    score: Dict[str, Any] = {}
    if min_score is not None:
        score["$gte"] = min_score
    if max_score is not None:
        score["$lte"] = max_score
    if score:
        clauses.append({"lead_score": score})
    clauses.append(_date_filter("analyzed_at", date_from, date_to))
    return _and(*clauses)


@router.get("/leads")
async def list_leads(
    q: Optional[str] = None, status: Optional[str] = None, priority: Optional[str] = None,
    platform: Optional[str] = None, quality: Optional[str] = None,
    assignee: Optional[str] = None, owner: Optional[str] = None, run_id: Optional[str] = None,
    min_score: Optional[int] = Query(None, ge=0, le=100), max_score: Optional[int] = Query(None, ge=0, le=100),
    date_from: Optional[str] = Query(None, alias="from"), date_to: Optional[str] = Query(None, alias="to"),
    sort: str = Query("newest", pattern="^(score|newest|oldest|updated|name)$"),
    page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=MAX_LIMIT),
    ctx: TenantContext = Depends(require_portal(P.LEADS_VIEW)),
):
    db = _db()
    query = _lead_filters(ctx, q=q, status=status, priority=priority, platform=platform,
                          quality=quality, assignee=assignee, owner=owner, min_score=min_score,
                          max_score=max_score, date_from=date_from, date_to=date_to, run_id=run_id)
    sort_key = {"score": [("lead_score", -1), ("analyzed_at", -1)],
                "newest": [("analyzed_at", -1)], "oldest": [("analyzed_at", 1)],
                "updated": [("lead_updated_at", -1), ("analyzed_at", -1)],
                "name": [("commenter_name", 1)]}[sort]
    total = await db.ai_comments.count_documents(query)
    docs = [d async for d in db.ai_comments.find(query).sort(sort_key)
            .skip((page - 1) * limit).limit(limit)]
    users = await _user_map(db, [d.get("assigned_user_id") for d in docs] + [d.get("user_id") for d in docs])
    return _paged([_lead_row(d, users) for d in docs], total, page, limit)


@router.get("/leads/pipeline")
async def lead_pipeline(ctx: TenantContext = Depends(require_portal(P.LEADS_VIEW))):
    from app.api.routes.search import VALID_TRANSITIONS
    db = _db()
    base = scope_query(ctx, {"is_lead": True}, **_LEAD)
    by_status = await _count_by(db, "ai_comments", base, "lead_status")
    statuses = {s: by_status.get(s, 0) for s in LEAD_STATUSES}
    statuses["new"] += by_status.get("", 0) + by_status.get("None", 0)
    by_owner = await _count_by(db, "ai_comments", base, "assigned_user_id")
    users = await _user_map(db, list(by_owner.keys()))
    owners = [{"user_id": k or None, "name": (users.get(k) or {}).get("name") or ("Unassigned" if not k else None),
               "email": (users.get(k) or {}).get("email"), "leads": v}
              for k, v in sorted(by_owner.items(), key=lambda kv: -kv[1])
              if k not in ("None",)]
    unassigned = by_owner.get("", 0) + by_owner.get("None", 0)
    owners = [o for o in owners if o["user_id"]] + ([{"user_id": None, "name": "Unassigned",
                                                      "email": None, "leads": unassigned}] if unassigned else [])
    return {"success": True, "statuses": statuses, "owners": owners,
            "transitions": {k: list(v) for k, v in VALID_TRANSITIONS.items()},
            "total": sum(statuses.values())}


# ── Lead rules (org keywords) ───────────────────────────────────────────────

class LeadRulesBody(BaseModel):
    keywords: List[str] = []
    exclude_keywords: List[str] = []


class LeadRulesTest(BaseModel):
    text: str


def _global_rule_summary() -> Optional[Dict[str, Any]]:
    try:
        from app.pipeline.comment_filter import load_active_rule
        db = get_sync_db()
        rule = load_active_rule(db) if db is not None else None
    except Exception:
        rule = None
    if not rule:
        return None
    return {"name": rule.get("name"), "include_keywords": list(rule.get("include_keywords") or [])[:60],
            "exclude_keywords": list(rule.get("exclude_keywords") or [])[:60],
            "categories": list(rule.get("categories") or [])}


@router.get("/lead-rules")
async def get_lead_rules(ctx: TenantContext = Depends(require_portal(P.SETTINGS_VIEW))):
    db = _db()
    settings = (await _org_doc(db, ctx)).get("settings") or {}
    kws = settings.get("lead_keywords") or []
    return {"success": True, "keywords": kws,
            "exclude_keywords": settings.get("lead_exclude_keywords") or [],
            "using_defaults": not kws, "global_rule": _global_rule_summary(),
            "can_edit": P.SETTINGS_MANAGE in ctx.permissions}


@router.put("/lead-rules")
async def put_lead_rules(body: LeadRulesBody, request: Request,
                         ctx: TenantContext = Depends(require_portal(P.SETTINGS_MANAGE))):
    from app.pipeline.comment_filter import normalize_keyword_list
    db = _db()
    kws = normalize_keyword_list(body.keywords)[:200]
    exclude = normalize_keyword_list(body.exclude_keywords)[:200]
    before = (await _org_doc(db, ctx)).get("settings") or {}
    await db.organizations.update_one({"_id": ObjectId(ctx.organization_id)}, {"$set": {
        "settings.lead_keywords": kws, "settings.lead_exclude_keywords": exclude,
        "updated_at": utcnow()}})
    await _audit(ctx, request, "lead_rules.updated", "leads", resource_type="organization",
                 resource_id=ctx.organization_id,
                 details={"before": {"keywords": before.get("lead_keywords") or [],
                                     "exclude": before.get("lead_exclude_keywords") or []},
                          "after": {"keywords": kws, "exclude": exclude}})
    return {"success": True, "keywords": kws, "exclude_keywords": exclude,
            "using_defaults": not kws,
            "message": "Lead rules saved" if kws else "Lead rules cleared — global defaults apply"}


@router.post("/lead-rules/test")
async def test_lead_rules(body: LeadRulesTest, ctx: TenantContext = Depends(require_portal(P.SETTINGS_VIEW))):
    from app.pipeline import org_lead_rules as R
    from app.pipeline.comment_filter import evaluate_rule
    text = (body.text or "")[:2000]
    kws = R.org_keywords(ctx.organization_id)
    if kws:
        exclude = R.org_exclude_keywords(ctx.organization_id)
        rule = R.org_rule(ctx.organization_id)
        res = evaluate_rule(text, rule) if rule else {}
        return {"success": True, "source": "organization", "matched": R.matches(text, kws, exclude),
                "matched_keywords": res.get("matched_keywords", []),
                "excluded_keywords": res.get("excluded_keywords", [])}
    g = _global_rule_summary()
    if not g:
        return {"success": True, "source": "none", "matched": True, "matched_keywords": [],
                "excluded_keywords": [], "message": "No rules configured — every comment is analysed."}
    from app.pipeline.comment_filter import load_active_rule
    res = evaluate_rule(text, load_active_rule(get_sync_db()))
    return {"success": True, "source": "global", "matched": res.get("status") != "NOT_MATCHED",
            "matched_keywords": res.get("matched_keywords", []),
            "excluded_keywords": res.get("excluded_keywords", [])}


# ── Org-wide scraped data (pages / posts / comments) ────────────────────────

_DATA = {
    "pages": ("facebook_pages", ["page_name", "category", "city"],
              {"newest": [("_id", -1)], "oldest": [("_id", 1)],
               "score": [("lead_score", -1)], "followers": [("followers", -1)],
               "name": [("page_name", 1)]}),
    "posts": ("facebook_posts", ["caption", "page_name", "post_url"],
              {"newest": [("_id", -1)], "oldest": [("_id", 1)],
               "comments": [("total_comment_count", -1)], "likes": [("likes_count", -1)],
               "published": [("published_date", -1)]}),
    "comments": ("facebook_comments", ["text", "author_name"],
                 {"newest": [("_id", -1)], "oldest": [("_id", 1)],
                  "published": [("published_date", -1)], "reactions": [("reactions_count", -1)]}),
}


@router.get("/data/{kind}")
async def browse_data(
    kind: str, q: Optional[str] = None, platform: Optional[str] = None,
    run_id: Optional[str] = None, page_id: Optional[str] = None, post_id: Optional[str] = None,
    user_id: Optional[str] = None, sort: str = "newest",
    page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=MAX_LIMIT),
    ctx: TenantContext = Depends(require_portal(P.SEARCH_VIEW)),
):
    if kind not in _DATA:
        raise HTTPException(status_code=404, detail="Unknown data type")
    coll, text_fields, sorts = _DATA[kind]
    db = _db()
    clauses = [scope_query(ctx, {}, **_OWN)]
    if q:
        rx = _regex(q)
        clauses.append({"$or": [{f: rx} for f in text_fields]})
    if platform:
        clauses.append({"platform": platform})
    if run_id:
        clauses.append({"search_run_id": run_id})
    if page_id and kind == "posts":
        clauses.append({"page_ref": page_id})
    if post_id and kind == "comments":
        clauses.append({"post_ref": post_id})
    if user_id:
        clauses.append({"user_id": str(user_id)})
    query = _and(*clauses)
    total = await db[coll].count_documents(query)
    docs = [d async for d in db[coll].find(query).sort(sorts.get(sort, sorts["newest"]))
            .skip((page - 1) * limit).limit(limit)]
    items = []
    if kind == "pages":
        for d in docs:
            items.append({"id": str(d["_id"]), "name": d.get("page_name"), "platform": d.get("platform"),
                          "url": d.get("facebook_url") or d.get("url"), "category": d.get("category"),
                          "followers": d.get("followers"), "phone": d.get("phone"), "email": d.get("email"),
                          "city": d.get("city"), "lead_score": d.get("lead_score"),
                          "posts": d.get("posts_count") or d.get("total_posts_found"),
                          "posts_status": d.get("posts_status"), "search_run_id": d.get("search_run_id"),
                          "created_at": _ser(d.get("created_at") or d.get("scraped_at"))})
    elif kind == "posts":
        for d in docs:
            items.append({"id": str(d["_id"]), "caption": (d.get("caption") or "")[:280],
                          "platform": d.get("platform"), "url": d.get("post_url"),
                          "page_ref": d.get("page_ref"), "page_name": d.get("page_name"),
                          "published": d.get("published_date"), "likes": d.get("likes_count"),
                          "comments": d.get("total_comment_count") or d.get("comments_count"),
                          "scraped_comments": d.get("scraped_comment_count"),
                          "comments_status": d.get("comments_status"),
                          "search_run_id": d.get("search_run_id")})
    else:
        refs = [str(d["_id"]) for d in docs]
        ai = {}
        if refs:
            async for a in db.ai_comments.find(_and(_org_q(ctx), {"comment_ref": {"$in": refs}}),
                                               {"comment_ref": 1, "is_lead": 1, "lead_score": 1,
                                                "lead_quality": 1}):
                ai[a.get("comment_ref")] = a
        for d in docs:
            a = ai.get(str(d["_id"])) or {}
            items.append({"id": str(d["_id"]), "author": d.get("author_name"),
                          "text": (d.get("text") or "")[:400], "platform": d.get("platform"),
                          "published": d.get("published_date"), "post_ref": d.get("post_ref"),
                          "url": d.get("comment_url"), "is_lead": bool(a.get("is_lead")),
                          "lead_id": str(a["_id"]) if a.get("_id") else None,
                          "lead_score": a.get("lead_score"), "quality": a.get("lead_quality"),
                          "analysed": bool(a)})
    return _paged(items, total, page, limit)


# ═══════════════════════════════════════════════════════════════════════════
# Apify (view only)
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/apify/summary")
async def apify_summary(ctx: TenantContext = Depends(require_portal(P.SEARCH_VIEW))):
    db = _db()
    oq = _org_q(ctx)
    month_ago = datetime.utcnow() - timedelta(days=30)
    by_status = await _count_by(db, "search_history", oq, "status")
    by_platform = await _count_by(db, "search_history", oq, "intent.platform")
    return {"success": True, "jobs": by_status, "platforms": by_platform,
            "last_30_days": await db.search_history.count_documents(
                _and(oq, {"created_at": {"$gte": month_ago}})),
            "pages": await db.facebook_pages.count_documents(oq),
            "posts": await db.facebook_posts.count_documents(oq),
            "comments": await db.facebook_comments.count_documents(oq),
            "actor_runs": await db.apify_jobs.count_documents(oq)}


@router.get("/apify/jobs")
async def apify_jobs(
    status: Optional[str] = None, platform: Optional[str] = None, q: Optional[str] = None,
    date_from: Optional[str] = Query(None, alias="from"), date_to: Optional[str] = Query(None, alias="to"),
    page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=MAX_LIMIT),
    ctx: TenantContext = Depends(require_portal(P.SEARCH_VIEW)),
):
    """Scrape jobs of this organization (the search runs that drive Apify)."""
    return await list_searches(q=q, user_id=None, platform=platform, status=status,
                               date_from=date_from, date_to=date_to, sort="newest",
                               page=page, limit=limit, ctx=ctx)


@router.get("/apify/runs")
async def apify_runs(
    status: Optional[str] = None,
    page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=MAX_LIMIT),
    ctx: TenantContext = Depends(require_portal(P.SEARCH_VIEW)),
):
    """Low-level Apify actor runs recorded for this organization."""
    db = _db()
    query = _and(_org_q(ctx), {"status": status} if status else {})
    total = await db.apify_jobs.count_documents(query)
    items = []
    async for d in db.apify_jobs.find(query).sort("created_at", -1).skip((page - 1) * limit).limit(limit):
        clean = redact(_ser(d))
        items.append({k: clean.get(k) for k in (
            "id", "job_id", "actor_id", "platform", "status", "run_id", "search_run_id",
            "dataset_id", "items", "items_count", "error", "created_at", "finished_at",
            "completed_at", "duration_seconds")})
    return _paged(items, total, page, limit)


# ═══════════════════════════════════════════════════════════════════════════
# Analytics
# ═══════════════════════════════════════════════════════════════════════════

async def _daily(db, coll: str, match: Dict[str, Any], field: str, days: List[str],
                 value_field: Optional[str] = None, cap: int = 200000) -> Dict[str, int]:
    out = {d: 0 for d in days}
    proj = {field: 1}
    if value_field:
        proj[value_field] = 1
    async for d in db[coll].find(match, proj).limit(cap):
        dt = _as_naive(d.get(field))
        if not dt:
            continue
        key = dt.strftime("%Y-%m-%d")
        if key in out:
            out[key] += int(d.get(value_field) or 0) if value_field else 1
    return out


@router.get("/analytics")
async def analytics(
    date_from: Optional[str] = Query(None, alias="from"), date_to: Optional[str] = Query(None, alias="to"),
    ctx: TenantContext = Depends(require_portal()),
):
    db = _db()
    end = _parse_date(date_to, end=True) or datetime.utcnow()
    start = _parse_date(date_from) or (end - timedelta(days=29)).replace(hour=0, minute=0, second=0,
                                                                        microsecond=0)
    if start > end:
        raise HTTPException(status_code=422, detail="'from' must be before 'to'")
    if (end - start).days > 366:
        raise HTTPException(status_code=422, detail="Date range is limited to one year")
    days = []
    cur = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while cur <= end:
        days.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    oq = _org_q(ctx)
    s_org = {"organization_id": ctx.organization_id}

    def rng(field):
        return {field: {"$gte": start, "$lte": end}}

    run_q = _and(oq, rng("created_at"))
    lead_q = _and(oq, {"is_lead": True}, rng("analyzed_at"))
    series = {
        "searches": await _daily(db, "search_history", run_q, "created_at", days),
        "leads": await _daily(db, "ai_comments", lead_q, "analyzed_at", days),
        "tokens": await _daily(db, "token_ledger", _and(s_org, {"type": "consume"}, rng("created_at")),
                               "created_at", days, value_field="amount"),
        "ai_requests": await _daily(db, "ai_requests", _and(oq, rng("created_at")), "created_at", days),
    }
    members = [m async for m in db.organization_members.find(
        {"organization_id": ctx.organization_id, "status": {"$ne": "removed"}})]
    joined = {d: 0 for d in days}
    for m in members:
        dt = _as_naive(m.get("joined_at") or m.get("created_at"))
        if dt and dt.strftime("%Y-%m-%d") in joined:
            joined[dt.strftime("%Y-%m-%d")] += 1
    series["new_users"] = joined

    leads_by_owner = await _count_by(db, "ai_comments", lead_q, "assigned_user_id")
    search_by_user = await _count_by(db, "search_history", run_q, "user_id")
    lead_by_user = await _count_by(db, "ai_comments", lead_q, "user_id")
    users = await _user_map(db, list(leads_by_owner) + list(search_by_user) + list(lead_by_user))

    def label(uid):
        u = users.get(uid) or {}
        return u.get("name") or u.get("email") or ("Unassigned" if uid in ("", "None") else "Unknown")

    top_users = sorted(({"user_id": uid, "name": label(uid), "searches": search_by_user.get(uid, 0),
                         "leads": lead_by_user.get(uid, 0)}
                        for uid in set(search_by_user) | set(lead_by_user) if uid not in ("", "None")),
                       key=lambda r: -(r["searches"] + r["leads"]))[:10]
    ai_tokens = 0
    async for row in db.ai_requests.aggregate([{"$match": _and(oq, rng("created_at"))},
                                               {"$group": {"_id": None, "n": {"$sum": "$total_tokens"}}}]):
        ai_tokens = row.get("n") or 0
    return {
        "success": True, "from": start.strftime("%Y-%m-%d"), "to": end.strftime("%Y-%m-%d"),
        "days": days, "series": series,
        "totals": {k: sum(v.values()) for k, v in series.items()},
        "users": {"total": len(members),
                  "active": sum(1 for m in members if m.get("status") == "active"),
                  "by_role": {r: sum(1 for m in members if m.get("role") == r)
                              for r in ("owner", "admin", "manager", "member", "viewer")}},
        "searches": {"by_status": await _count_by(db, "search_history", run_q, "status"),
                     "by_platform": await _count_by(db, "search_history", run_q, "intent.platform")},
        "leads": {"by_platform": await _count_by(db, "ai_comments", lead_q, "platform"),
                  "by_status": await _count_by(db, "ai_comments", lead_q, "lead_status"),
                  "by_quality": await _count_by(db, "ai_comments", lead_q, "lead_quality"),
                  "by_owner": {label(k): v for k, v in leads_by_owner.items()}},
        "usage": {"posts": await db.facebook_posts.count_documents(_and(oq, rng("created_at"))),
                  "comments": await db.facebook_comments.count_documents(_and(oq, rng("created_at"))),
                  "ai_analyses": await db.ai_comments.count_documents(_and(oq, rng("analyzed_at"))),
                  "ai_tokens": ai_tokens,
                  "tokens_consumed": sum(series["tokens"].values())},
        "top_users": top_users,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Billing history
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/billing/history")
async def billing_history(page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=MAX_LIMIT),
                          ctx: TenantContext = Depends(require_portal(P.ORG_BILLING_VIEW))):
    db = _db()
    q = {"organization_id": ctx.organization_id}
    total = await db.subscriptions.count_documents(q)
    subs = []
    async for s in db.subscriptions.find(q).sort("created_at", -1).skip((page - 1) * limit).limit(limit):
        subs.append({"id": str(s["_id"]), "plan_id": s.get("plan_id"), "status": s.get("status"),
                     "billing_cycle": s.get("billing_cycle"), "amount": s.get("amount"),
                     "currency": s.get("currency"), "created_at": _ser(s.get("created_at")),
                     "started_at": _ser(s.get("started_at")),
                     "current_period_end": _ser(s.get("current_period_end")),
                     "cancel_at_period_end": bool(s.get("cancel_at_period_end")),
                     "history": _ser(s.get("status_history") or [])[-10:]})
    payments = []
    async for p in db.payments.find(q).sort("created_at", -1).limit(50):
        payments.append({"id": str(p["_id"]), "amount": p.get("amount"), "currency": p.get("currency"),
                         "status": p.get("status"), "subscription_id": p.get("subscription_id"),
                         "invoice_id": p.get("invoice_id"), "created_at": _ser(p.get("created_at"))})
    return _paged(subs, total, page, limit, payments=payments)


# ═══════════════════════════════════════════════════════════════════════════
# Exports
# ═══════════════════════════════════════════════════════════════════════════

EXPORT_KINDS = {
    "users": ["name", "email", "role", "status", "last_login", "joined_at", "searches", "leads",
              "assigned_leads"],
    "activity": ["at", "actor_email", "actor_role", "action", "category", "status",
                 "resource_type", "resource_id", "ip"],
    "usage": ["metric", "used", "limit", "remaining", "percentage"],
    "leads": ["name", "text", "platform", "page_name", "score", "quality", "priority", "status",
              "intent", "phone", "email", "assigned_email", "owner_email", "post_url", "created_at"],
    "searches": ["created_at", "user_email", "url", "platform", "status", "phase", "pages_found",
                 "leads", "error", "completed_at"],
    "posts": ["platform", "page_name", "post_url", "caption", "published_date", "likes_count",
              "total_comment_count", "scraped_comment_count", "search_run_id"],
    "comments": ["platform", "author_name", "text", "published_date", "comment_url", "is_lead",
                 "lead_score", "post_ref"],
}
_DATA_EXPORTS = ("leads", "searches", "posts", "comments")
_EXPORT_PERM = {"users": P.MEMBERS_VIEW, "activity": P.ORG_AUDIT_VIEW, "usage": P.ORG_BILLING_VIEW,
                "leads": P.LEADS_EXPORT, "searches": P.SEARCH_EXPORT, "posts": P.SEARCH_EXPORT,
                "comments": P.SEARCH_EXPORT}
_EXPORT_MAX = 50000


@router.get("/exports")
async def export_history(scope: Optional[str] = None, page: int = Query(1, ge=1),
                         limit: int = Query(20, ge=1, le=MAX_LIMIT),
                         ctx: TenantContext = Depends(require_portal(P.EXPORTS_VIEW))):
    db = _db()
    query = _and(_org_q(ctx), {"scope": scope} if scope else {})
    total = await db.exports.count_documents(query)
    docs = [d async for d in db.exports.find(query).sort("created_at", -1)
            .skip((page - 1) * limit).limit(limit)]
    users = await _user_map(db, [d.get("user_id") for d in docs])
    items = [{"id": str(d["_id"]), "scope": d.get("scope"), "format": d.get("format", "csv"),
              "status": d.get("status"), "rows": d.get("rows"), "filters": _ser(d.get("filters") or {}),
              "source": d.get("source") or "user_portal",
              "user_email": (users.get(d.get("user_id")) or {}).get("email") or d.get("created_by"),
              "created_at": _ser(d.get("created_at"))} for d in docs]
    return _paged(items, total, page, limit)


@router.get("/exports/{kind}.csv")
async def export_csv(
    kind: str, request: Request,
    q: Optional[str] = None, status: Optional[str] = None, platform: Optional[str] = None,
    assignee: Optional[str] = None, run_id: Optional[str] = None,
    date_from: Optional[str] = Query(None, alias="from"), date_to: Optional[str] = Query(None, alias="to"),
    ctx: TenantContext = Depends(require_portal(P.EXPORTS_CREATE)),
):
    from app.api.routes.search import _csv_response
    if kind not in EXPORT_KINDS:
        raise HTTPException(status_code=404, detail="Unknown export")
    perm = _EXPORT_PERM[kind]
    if perm not in ctx.permissions:
        _permission_denied(request, ctx, perm)
    db = _db()
    if kind in _DATA_EXPORTS:
        from app.admin.settings import get_bool
        if not get_bool("features.exports.enabled"):
            raise HTTPException(status_code=403, detail="CSV exports are currently disabled by the administrator.")
        from app.billing.entitlements import EntitlementService
        await EntitlementService.enforce_quota_and_consume(
            organization_id=ctx.organization_id, feature_key="csv_export",
            metric="monthly_exports", quantity=1, user_id=ctx.user_id,
            source=f"org_admin_export_{kind}", db=db)

    oq = _org_q(ctx)
    rows: List[Dict[str, Any]] = []
    if kind == "users":
        members = [m async for m in db.organization_members.find(
            {"organization_id": ctx.organization_id, "status": {"$ne": "removed"}})]
        users = await _user_map(db, [m["user_id"] for m in members])
        last = {str(u["_id"]): u.get("last_login") async for u in db.users.find(
            {"_id": {"$in": [o for o in (_oid(m["user_id"]) for m in members) if o]}}, {"last_login": 1})}
        searches = await _count_by(db, "search_history", oq, "user_id")
        leads = await _count_by(db, "ai_comments", _and(oq, {"is_lead": True}), "user_id")
        assigned = await _count_by(db, "ai_comments", _and(oq, {"is_lead": True}), "assigned_user_id")
        for m in members:
            u = users.get(m["user_id"]) or {}
            if status and m.get("status") != status:
                continue
            if q and q.lower() not in f"{u.get('name') or ''} {u.get('email') or ''}".lower():
                continue
            rows.append({"name": u.get("name"), "email": u.get("email"),
                         "role": P.SPEC_ROLE_LABELS.get(m.get("role"), m.get("role")),
                         "status": m.get("status"), "last_login": _ser(last.get(m["user_id"])),
                         "joined_at": _ser(m.get("joined_at") or m.get("created_at")),
                         "searches": searches.get(m["user_id"], 0), "leads": leads.get(m["user_id"], 0),
                         "assigned_leads": assigned.get(m["user_id"], 0)})
    elif kind == "activity":
        query = _and({"organization_id": ctx.organization_id},
                     _date_filter("at", date_from, date_to),
                     {"action": _regex(q)} if q else {})
        async for a in db.audit_logs.find(query).sort("at", -1).limit(_EXPORT_MAX):
            rows.append({**{k: a.get(k) for k in EXPORT_KINDS["activity"]}, "at": _ser(a.get("at"))})
    elif kind == "usage":
        from app.billing.entitlements import EntitlementService
        usage = await EntitlementService.get_usage_summary(ctx.organization_id, db=db)
        for metric, m in (usage.get("metrics") or {}).items():
            rows.append({"metric": metric, **m})
        tok = usage.get("tokens") or {}
        if tok:
            rows.append({"metric": "tokens", "used": tok.get("used"), "limit": tok.get("allocated"),
                         "remaining": tok.get("remaining"),
                         "percentage": round(tok.get("used", 0) / tok["allocated"] * 100, 1)
                         if tok.get("allocated") else 0})
    elif kind == "leads":
        query = _lead_filters(ctx, q=q, status=status, platform=platform, assignee=assignee,
                              run_id=run_id, date_from=date_from, date_to=date_to)
        docs = [d async for d in db.ai_comments.find(query).sort("lead_score", -1).limit(_EXPORT_MAX)]
        users = await _user_map(db, [d.get("assigned_user_id") for d in docs] + [d.get("user_id") for d in docs])
        for d in docs:
            r = _lead_row(d, users)
            r["text"] = d.get("comment_text") or ""
            rows.append(r)
    elif kind == "searches":
        query = _and(scope_query(ctx, {}, **_OWN), {"intent.platform": platform} if platform else {},
                     {"status": status} if status else {}, _date_filter("created_at", date_from, date_to))
        runs = [r async for r in db.search_history.find(query).sort("created_at", -1).limit(_EXPORT_MAX)]
        leads = await _count_by(db, "ai_comments", _and(oq, {"is_lead": True}), "search_run_id")
        users = await _user_map(db, [r.get("user_id") for r in runs])
        rows = [_run_row(r, users, leads) for r in runs]
    elif kind == "posts":
        query = _and(scope_query(ctx, {}, **_OWN), {"platform": platform} if platform else {},
                     {"search_run_id": run_id} if run_id else {})
        rows = [d async for d in db.facebook_posts.find(query).sort("_id", -1).limit(_EXPORT_MAX)]
    elif kind == "comments":
        query = _and(scope_query(ctx, {}, **_OWN), {"platform": platform} if platform else {},
                     {"search_run_id": run_id} if run_id else {})
        docs = [d async for d in db.facebook_comments.find(query).sort("_id", -1).limit(_EXPORT_MAX)]
        refs = [str(d["_id"]) for d in docs]
        ai = {}
        if refs:
            async for a in db.ai_comments.find(_and(oq, {"comment_ref": {"$in": refs}}),
                                               {"comment_ref": 1, "is_lead": 1, "lead_score": 1}):
                ai[a.get("comment_ref")] = a
        for d in docs:
            a = ai.get(str(d["_id"])) or {}
            rows.append({**d, "is_lead": bool(a.get("is_lead")), "lead_score": a.get("lead_score")})

    filters = {k: v for k, v in {"q": q, "status": status, "platform": platform, "assignee": assignee,
                                 "run_id": run_id, "from": date_from, "to": date_to}.items() if v}
    try:
        await db.exports.insert_one(stamp(ctx, {
            "scope": kind, "format": "csv", "status": "completed", "rows": len(rows),
            "filters": filters, "source": "admin_portal", "created_at": utcnow()}))
    except Exception:
        pass
    await _audit(ctx, request, "export.csv", "exports", resource_type=kind,
                 details={"scope": kind, "rows": len(rows), "filters": filters, "portal": "org_admin"})
    return _csv_response(rows, EXPORT_KINDS[kind],
                         f"{kind}_{datetime.utcnow().strftime('%Y%m%d')}.csv", max_rows=_EXPORT_MAX)


# ═══════════════════════════════════════════════════════════════════════════
# Audit logs (own organization only)
# ═══════════════════════════════════════════════════════════════════════════

def _audit_query(ctx: TenantContext, action, category, user, status, q, date_from, date_to):
    clauses = [{"organization_id": ctx.organization_id}]
    if action:
        clauses.append({"action": {"$regex": "^" + re.escape(action.strip()[:80]), "$options": "i"}})
    if category:
        clauses.append({"category": category})
    if user:
        clauses.append({"$or": [{"actor_user_id": user.strip()}, {"actor_email": _regex(user)}]})
    if status in ("success", "failure"):
        clauses.append({"status": status})
    if q:
        rx = _regex(q)
        clauses.append({"$or": [{"action": rx}, {"actor_email": rx}, {"resource_id": rx},
                                {"resource_type": rx}]})
    clauses.append(_date_filter("at", date_from, date_to))
    return _and(*clauses)


def _audit_row(a: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": str(a["_id"]), "action": a.get("action"), "category": a.get("category"),
            "actor_email": a.get("actor_email"), "actor_role": a.get("actor_role"),
            "actor_user_id": a.get("actor_user_id"), "status": a.get("status"),
            "resource_type": a.get("resource_type"), "resource_id": a.get("resource_id"),
            "ip": a.get("ip"), "user_agent": a.get("user_agent"),
            "details": _ser(redact(a.get("details") or {})), "at": _ser(a.get("at"))}


@router.get("/audit-logs")
async def audit_logs(
    action: Optional[str] = None, category: Optional[str] = None, user: Optional[str] = None,
    status: Optional[str] = None, q: Optional[str] = None,
    date_from: Optional[str] = Query(None, alias="from"), date_to: Optional[str] = Query(None, alias="to"),
    page: int = Query(1, ge=1), limit: int = Query(25, ge=1, le=MAX_LIMIT),
    ctx: TenantContext = Depends(require_portal(P.ORG_AUDIT_VIEW)),
):
    db = _db()
    query = _audit_query(ctx, action, category, user, status, q, date_from, date_to)
    total = await db.audit_logs.count_documents(query)
    items = [_audit_row(a) async for a in db.audit_logs.find(query).sort("at", -1)
             .skip((page - 1) * limit).limit(limit)]
    return _paged(items, total, page, limit)


@router.get("/audit-logs/facets")
async def audit_facets(ctx: TenantContext = Depends(require_portal(P.ORG_AUDIT_VIEW))):
    db = _db()
    q = {"organization_id": ctx.organization_id}
    return {"success": True,
            "actions": sorted(k for k in (await _count_by(db, "audit_logs", q, "action")) if k)[:300],
            "categories": sorted(k for k in (await _count_by(db, "audit_logs", q, "category")) if k)}


@router.get("/audit-logs.csv")
async def audit_logs_csv(
    request: Request, action: Optional[str] = None, category: Optional[str] = None,
    user: Optional[str] = None, status: Optional[str] = None, q: Optional[str] = None,
    date_from: Optional[str] = Query(None, alias="from"), date_to: Optional[str] = Query(None, alias="to"),
    ctx: TenantContext = Depends(require_portal(P.ORG_AUDIT_VIEW)),
):
    from app.api.routes.search import _csv_response
    if P.EXPORTS_CREATE not in ctx.permissions:
        _permission_denied(request, ctx, P.EXPORTS_CREATE)
    db = _db()
    query = _audit_query(ctx, action, category, user, status, q, date_from, date_to)
    rows = [_audit_row(a) async for a in db.audit_logs.find(query).sort("at", -1).limit(_EXPORT_MAX)]
    filters = {k: v for k, v in {"action": action, "category": category, "user": user,
                                 "status": status, "q": q, "from": date_from, "to": date_to}.items() if v}
    await db.exports.insert_one(stamp(ctx, {"scope": "audit_logs", "format": "csv", "status": "completed",
                                            "rows": len(rows), "filters": filters,
                                            "source": "admin_portal", "created_at": utcnow()}))
    await _audit(ctx, request, "export.csv", "exports", resource_type="audit_logs",
                 details={"scope": "audit_logs", "rows": len(rows), "filters": filters})
    return _csv_response(rows, ["at", "actor_email", "actor_role", "action", "category", "status",
                                "resource_type", "resource_id", "ip"],
                         f"audit_{datetime.utcnow().strftime('%Y%m%d')}.csv", max_rows=_EXPORT_MAX)


# ═══════════════════════════════════════════════════════════════════════════
# Support tickets
# ═══════════════════════════════════════════════════════════════════════════

class TicketCreate(BaseModel):
    subject: str
    message: str
    category: str = "question"
    priority: str = "normal"


class TicketMessage(BaseModel):
    message: str


class TicketStatus(BaseModel):
    status: str


def _ticket_row(t: Dict[str, Any], full: bool = False) -> Dict[str, Any]:
    msgs = t.get("messages") or []
    row = {"id": str(t["_id"]), "number": t.get("number"), "subject": t.get("subject"),
           "category": t.get("category"), "priority": t.get("priority"), "status": t.get("status"),
           "created_by": t.get("created_by"), "created_at": _ser(t.get("created_at")),
           "updated_at": _ser(t.get("updated_at")), "messages_count": len(msgs),
           "last_reply_from_staff": bool(msgs and msgs[-1].get("from_staff"))}
    if full:
        row["messages"] = [{"author": m.get("author_name") or m.get("author_email"),
                            "author_email": m.get("author_email"), "from_staff": bool(m.get("from_staff")),
                            "body": m.get("body"), "at": _ser(m.get("at"))} for m in msgs]
    return row


async def _ticket_or_404(db, ctx: TenantContext, ticket_id: str, request: Request) -> Dict[str, Any]:
    oid = _oid(ticket_id)
    if oid is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    t = await db.support_tickets.find_one({"_id": oid, "organization_id": ctx.organization_id})
    if not t:
        other = await db.support_tickets.find_one({"_id": oid}, {"_id": 1, "organization_id": 1})
        if other:
            report_out_of_scope(request, ctx, "support_tickets", other)
        raise HTTPException(status_code=404, detail="Ticket not found")
    return t


@router.get("/support/tickets")
async def list_tickets(status: Optional[str] = None, q: Optional[str] = None,
                       page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=MAX_LIMIT),
                       ctx: TenantContext = Depends(require_portal())):
    db = _db()
    query = _and({"organization_id": ctx.organization_id},
                 {"status": status} if status else {},
                 {"subject": _regex(q)} if q else {})
    total = await db.support_tickets.count_documents(query)
    items = [_ticket_row(t) async for t in db.support_tickets.find(query).sort("updated_at", -1)
             .skip((page - 1) * limit).limit(limit)]
    return _paged(items, total, page, limit)


@router.post("/support/tickets")
async def create_ticket(body: TicketCreate, request: Request, ctx: TenantContext = Depends(require_portal())):
    subject, message = body.subject.strip()[:200], body.message.strip()[:5000]
    if len(subject) < 3:
        raise HTTPException(status_code=422, detail="Subject is required")
    if len(message) < 5:
        raise HTTPException(status_code=422, detail="Please describe the issue")
    if body.category not in TICKET_CATEGORIES:
        raise HTTPException(status_code=422, detail="Invalid category")
    if body.priority not in TICKET_PRIORITIES:
        raise HTTPException(status_code=422, detail="Invalid priority")
    db = _db()
    now = utcnow()
    number = await db.support_tickets.count_documents({}) + 1001
    doc = stamp(ctx, {
        "number": number, "subject": subject, "category": body.category, "priority": body.priority,
        "status": "open", "organization_name": ctx.organization_name,
        "messages": [{"author_id": ctx.user_id, "author_email": ctx.email, "author_name": ctx.name,
                      "from_staff": False, "body": message, "at": now}],
        "created_at": now, "updated_at": now})
    doc["_id"] = (await db.support_tickets.insert_one(doc)).inserted_id
    from app.events.notifications import notify_super_admins
    notify_super_admins("support_ticket", f"Support ticket #{number}: {subject}",
                        f"{ctx.organization_name} ({ctx.email}) — {body.category}, {body.priority} priority",
                        organization_id=ctx.organization_id,
                        severity="warning" if body.priority in ("high", "urgent") else "info",
                        link=f"/superadmin#/support/{doc['_id']}",
                        data={"ticket_id": str(doc["_id"])})
    await _audit(ctx, request, "support.ticket_created", "support", resource_type="support_ticket",
                 resource_id=str(doc["_id"]), details={"subject": subject, "category": body.category,
                                                       "priority": body.priority})
    return {"success": True, "ticket": _ticket_row(doc, full=True),
            "message": f"Ticket #{number} created — our team will reply soon."}


@router.get("/support/tickets/{ticket_id}")
async def get_ticket(ticket_id: str, request: Request, ctx: TenantContext = Depends(require_portal())):
    db = _db()
    return {"success": True, "ticket": _ticket_row(await _ticket_or_404(db, ctx, ticket_id, request), full=True)}


@router.post("/support/tickets/{ticket_id}/messages")
async def reply_ticket(ticket_id: str, body: TicketMessage, request: Request,
                       ctx: TenantContext = Depends(require_portal())):
    db = _db()
    t = await _ticket_or_404(db, ctx, ticket_id, request)
    text = body.message.strip()[:5000]
    if not text:
        raise HTTPException(status_code=422, detail="Message is required")
    now = utcnow()
    new_status = "open" if t.get("status") in ("resolved", "closed", "waiting") else t.get("status")
    await db.support_tickets.update_one({"_id": t["_id"], "organization_id": ctx.organization_id}, {
        "$push": {"messages": {"author_id": ctx.user_id, "author_email": ctx.email,
                               "author_name": ctx.name, "from_staff": False, "body": text, "at": now}},
        "$set": {"updated_at": now, "status": new_status}})
    from app.events.notifications import notify_super_admins
    notify_super_admins("support_ticket", f"New reply on ticket #{t.get('number')}",
                        f"{ctx.organization_name} ({ctx.email})", organization_id=ctx.organization_id,
                        link=f"/superadmin#/support/{t['_id']}",
                        data={"ticket_id": str(t["_id"])})
    await _audit(ctx, request, "support.ticket_replied", "support", resource_type="support_ticket",
                 resource_id=str(t["_id"]))
    t = await db.support_tickets.find_one({"_id": t["_id"]})
    return {"success": True, "ticket": _ticket_row(t, full=True)}


@router.post("/support/tickets/{ticket_id}/status")
async def set_ticket_status(ticket_id: str, body: TicketStatus, request: Request,
                            ctx: TenantContext = Depends(require_portal())):
    """The customer may close a ticket or reopen it (staff set other states)."""
    if body.status not in ("closed", "open"):
        raise HTTPException(status_code=422, detail="You can close or reopen a ticket")
    db = _db()
    t = await _ticket_or_404(db, ctx, ticket_id, request)
    await db.support_tickets.update_one({"_id": t["_id"], "organization_id": ctx.organization_id},
                                        {"$set": {"status": body.status, "updated_at": utcnow()}})
    await _audit(ctx, request, f"support.ticket_{'closed' if body.status == 'closed' else 'reopened'}",
                 "support", resource_type="support_ticket", resource_id=str(t["_id"]))
    t = await db.support_tickets.find_one({"_id": t["_id"]})
    return {"success": True, "ticket": _ticket_row(t, full=True)}


# ═══════════════════════════════════════════════════════════════════════════
# Own profile (name + personal notification preferences)
# ═══════════════════════════════════════════════════════════════════════════

_PREF_KEYS = ("email_notifications", "lead_assigned", "search_completed", "usage_warnings",
              "weekly_summary", "product_updates")


class ProfileBody(BaseModel):
    name: Optional[str] = None
    notification_preferences: Optional[Dict[str, bool]] = None


@router.get("/profile")
async def get_profile(ctx: TenantContext = Depends(require_portal())):
    db = _db()
    u = await db.users.find_one({"_id": _oid(ctx.user_id)}, {"name": 1, "email": 1, "last_login": 1,
                                                            "created_at": 1, "notification_preferences": 1,
                                                            "password_changed_at": 1}) or {}
    prefs = {k: True for k in _PREF_KEYS}
    prefs["product_updates"] = False
    prefs.update({k: bool(v) for k, v in (u.get("notification_preferences") or {}).items() if k in _PREF_KEYS})
    return {"success": True, "profile": {
        "name": u.get("name") or ctx.name, "email": u.get("email") or ctx.email,
        "role": ctx.org_role, "role_label": P.SPEC_ROLE_LABELS.get(ctx.org_role),
        "organization": ctx.organization_name, "last_login": _ser(u.get("last_login")),
        "password_changed_at": _ser(u.get("password_changed_at")),
        "notification_preferences": prefs}}


@router.patch("/profile")
async def update_profile(body: ProfileBody, request: Request, ctx: TenantContext = Depends(require_portal())):
    if ctx.impersonated_by:
        raise HTTPException(status_code=403, detail="Profiles cannot be edited while impersonating")
    db = _db()
    updates: Dict[str, Any] = {}
    if body.name is not None:
        name = body.name.strip()[:120]
        if len(name) < 2:
            raise HTTPException(status_code=422, detail="Name must be at least 2 characters")
        updates["name"] = name
    if body.notification_preferences is not None:
        bad = [k for k in body.notification_preferences if k not in _PREF_KEYS]
        if bad:
            raise HTTPException(status_code=422, detail=f"Unknown preference: {', '.join(bad)}")
        for k, v in body.notification_preferences.items():
            updates[f"notification_preferences.{k}"] = bool(v)
    if not updates:
        return {"success": True, "message": "No changes"}
    updates["updated_at"] = utcnow()
    await db.users.update_one({"_id": _oid(ctx.user_id)}, {"$set": updates})
    await _audit(ctx, request, "profile.updated", "auth", resource_type="user", resource_id=ctx.user_id,
                 details={k: v for k, v in updates.items() if k != "updated_at"})
    return {"success": True, "message": "Profile saved"}
