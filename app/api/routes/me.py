"""User self-service API — the private, per-user side of the User Portal.

  GET   /api/me/summary           dashboard: own counts, recent searches/leads,
                                  alerts, org usage + search caps + token costs
  GET   /api/me/profile           own profile (email read-only) + notification prefs
  PATCH /api/me/profile           update name / phone / notification prefs ONLY
  GET   /api/me/usage             own searches / leads / exports / tokens consumed
                                  (token_ledger rows with user_id == me) + org meters
  GET   /api/me/usage/ledger      own token ledger entries (paginated)
  GET   /api/me/searches          own search history (paginated, filter, sort)
  GET   /api/me/leads             own leads + leads assigned to me
                                  (view=all|mine|assigned, filters, paginated)
  GET   /api/me/exports           own export history (paginated, filter, sort)

Every endpoint resolves the caller through ``get_org_context`` /
``require_org_permission`` and ANDs ``scope_query(..., force_own=True)`` onto
every query: even an organization owner/admin only sees their OWN records
here (the org-wide view lives in the Admin portal). Mutations are audited.
"""
import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from app.admin.audit import aaudit, request_meta
from app.auth import permissions as P
from app.auth.tenant import (
    TenantContext,
    get_org_context,
    org_match,
    require_org_permission,
    scope_query,
)
from app.db.models import utcnow
from app.db.mongo import get_async_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/me", tags=["me"])

# Ownership scoping (always the caller's OWN records in this router).
_OWN = {"owner_field": "user_id", "legacy_email_field": "created_by", "force_own": True}
# Leads: own + assigned to me.
_LEAD = {**_OWN, "assigned_field": "assigned_user_id"}

# Fields a user can never change through the self-service profile endpoint.
PROTECTED_PROFILE_FIELDS = frozenset({
    "_id", "id", "user_id", "email", "role", "org_role", "roles", "organization_id",
    "default_organization_id", "organizations", "status", "platform_role",
    "is_platform_admin", "is_super_admin", "permissions", "password", "password_hash",
    "created_at", "updated_at", "last_login_at", "email_verified", "mfa", "plan_id",
})

# Personal notification preferences — ONE source of truth shared with the
# Admin portal (app/api/routes/org_admin.py GET/PATCH /api/org-admin/profile):
# ``users.notification_preferences`` = flat {key: bool}. Keys/defaults below
# must stay identical to org_admin._PREF_KEYS (all on, product_updates off).
# Security alerts are always delivered and are not configurable.
NOTIFICATION_PREFERENCES = {
    "email_notifications": {"label": "Email me notifications", "default": True,
                            "hint": "Master switch for email; in-app notifications always appear"},
    "search_completed": {"label": "Search completed or failed", "default": True,
                         "types": ("search_completed", "search_failed")},
    "lead_assigned": {"label": "A lead is assigned to me", "default": True,
                      "types": ("lead_assigned",)},
    "usage_warnings": {"label": "Token, usage & demo warnings", "default": True,
                       "types": ("high_token_usage", "tokens_low", "demo_expiring",
                                 "demo_expired", "quota_warning")},
    "weekly_summary": {"label": "Weekly summary email", "default": True, "types": ("weekly_summary",)},
    "product_updates": {"label": "Product news & tips", "default": False,
                        "types": ("product_update",)},
}
PREF_FIELD = "notification_preferences"

_PHONE_RE = re.compile(r"^[+0-9 ()\-.]{5,25}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


# ── helpers ─────────────────────────────────────────────────────────────────

def _db():
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return db


def _iso(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return value


def _clean(doc: Any) -> Any:
    """JSON-safe copy of a Mongo document (``_id`` -> ``id``)."""
    if isinstance(doc, dict):
        return {("id" if k == "_id" else k): _clean(v) for k, v in doc.items()
                if k not in ("password_hash",)}
    if isinstance(doc, list):
        return [_clean(v) for v in doc]
    if isinstance(doc, ObjectId):
        return str(doc)
    return _iso(doc)


def _page_meta(total: int, page: int, page_size: int) -> Dict[str, int]:
    return {"total": total, "page": page, "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size if page_size else 0}


def _user_oid(ctx: TenantContext) -> Optional[ObjectId]:
    try:
        return ObjectId(str(ctx.user_id))
    except Exception:
        return None


def _as_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            d = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def merge_notification_prefs(stored: Optional[Dict[str, Any]]) -> Dict[str, bool]:
    """Stored ``users.notification_preferences`` over the defaults (unknown
    keys dropped)."""
    out = {k: bool(meta["default"]) for k, meta in NOTIFICATION_PREFERENCES.items()}
    for k, v in (stored or {}).items():
        if k in out and isinstance(v, bool):
            out[k] = v
    return out


def notification_allowed(user_doc: Optional[Dict[str, Any]], ntype: str,
                         channel: str = "in_app") -> bool:
    """Whether ``user_doc``'s preferences allow a ``ntype`` notification on
    ``channel`` ("in_app" | "email"). Unknown types (security, system) are
    always allowed; email additionally needs ``email_notifications``.
    Intended to be called by app/events/notifications.notify_user / emailers."""
    prefs = merge_notification_prefs((user_doc or {}).get(PREF_FIELD))
    if channel == "email" and not prefs["email_notifications"]:
        return ntype in ("security_event",)
    for key, meta in NOTIFICATION_PREFERENCES.items():
        if ntype in meta.get("types", ()):
            return bool(prefs.get(key, True))
    return True


def _role_label(role: str) -> str:
    try:
        return P.SPEC_ROLE_LABELS.get(role, role.title() if role else "")
    except Exception:
        return role


# ── profile ─────────────────────────────────────────────────────────────────

class ProfileUpdate(BaseModel):
    """The ONLY fields a user may change about themselves."""
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = None
    phone: Optional[str] = None
    notification_preferences: Optional[Dict[str, bool]] = None

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        if v is None:
            return v
        v = " ".join(str(v).split())
        if not v:
            raise ValueError("Name cannot be empty")
        if len(v) > 100:
            raise ValueError("Name is too long (max 100 characters)")
        if _CONTROL_RE.search(v) or "<" in v or ">" in v:
            raise ValueError("Name contains characters that are not allowed")
        return v

    @field_validator("phone")
    @classmethod
    def _phone(cls, v):
        if v is None:
            return v
        v = str(v).strip()
        if v and not _PHONE_RE.match(v):
            raise ValueError("Enter a valid phone number (digits, spaces, + ( ) - .)")
        return v

    @field_validator("notification_preferences")
    @classmethod
    def _prefs(cls, v):
        if v is None:
            return v
        unknown = [k for k in v if k not in NOTIFICATION_PREFERENCES]
        if unknown:
            raise ValueError(f"Unknown notification preference: {', '.join(sorted(unknown))}")
        return v


async def _profile_payload(db, ctx: TenantContext) -> Dict[str, Any]:
    oid = _user_oid(ctx)
    user = await db.users.find_one({"_id": oid}) if oid is not None else None
    if user is None:
        user = await db.users.find_one({"email": ctx.email}) or {}
    return {
        "user_id": ctx.user_id,
        "email": ctx.email,
        "name": user.get("name") or ctx.name,
        "phone": user.get("phone") or "",
        "role": ctx.org_role,
        "role_label": _role_label(ctx.org_role),
        "organization": {"id": ctx.organization_id, "name": ctx.organization_name,
                         "status": ctx.organization_status},
        "created_at": _iso(user.get("created_at")) if not isinstance(user.get("created_at"), (int, float)) else None,
        "last_login_at": _iso(user.get("last_login_at")),
        "password_changed_at": _iso(user.get("password_changed_at")),
        "notification_preferences": merge_notification_prefs(user.get(PREF_FIELD)),
        "notification_options": [{"key": k, "label": m["label"], "hint": m.get("hint", "")}
                                 for k, m in NOTIFICATION_PREFERENCES.items()],
        "editable_fields": ["name", "phone", "notification_preferences"],
        "impersonated": bool(ctx.impersonated_by),
    }


@router.get("/profile")
async def get_my_profile(ctx: TenantContext = Depends(get_org_context)):
    db = _db()
    return {"success": True, "profile": await _profile_payload(db, ctx)}


@router.patch("/profile")
async def update_my_profile(request: Request, ctx: TenantContext = Depends(get_org_context)):
    """Update own name / phone / notification preferences.

    Mass-assignment protection: the body is validated against an allow-list
    model (``extra='forbid'``); any attempt to set a protected field (email,
    role, organization, status, ...) is rejected with 400 and logged as a
    security event."""
    db = _db()
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail={"code": "INVALID_BODY",
                                                     "message": "Send a JSON object."})
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail={"code": "INVALID_BODY",
                                                     "message": "Send a JSON object."})
    protected = sorted(k for k in body if k in PROTECTED_PROFILE_FIELDS)
    if protected:
        try:
            from app.events.security import security_event_from_request
            security_event_from_request(request, "mass_assignment_attempt", "medium", ctx=ctx,
                                        details={"endpoint": "PATCH /api/me/profile",
                                                 "fields": protected})
        except Exception:
            pass
        raise HTTPException(status_code=400, detail={
            "code": "FIELD_NOT_EDITABLE", "fields": protected,
            "message": f"These fields cannot be changed here: {', '.join(protected)}."})
    if ctx.impersonated_by:
        raise HTTPException(status_code=403, detail={
            "code": "IMPERSONATION_READ_ONLY",
            "message": "Profiles cannot be edited during a support session."})
    try:
        upd = ProfileUpdate(**body)
    except ValidationError as e:
        first = e.errors()[0] if e.errors() else {}
        loc = ".".join(str(x) for x in first.get("loc", []))
        msg = str(first.get("msg", "Invalid value")).replace("Value error, ", "")
        if first.get("type") == "extra_forbidden":
            msg = f"'{loc}' cannot be changed here"
        raise HTTPException(status_code=422, detail={"code": "VALIDATION_ERROR",
                                                     "field": loc, "message": msg})

    oid = _user_oid(ctx)
    user = await db.users.find_one({"_id": oid}) if oid is not None else None
    if user is None:
        raise HTTPException(status_code=404, detail="Account not found")

    sets: Dict[str, Any] = {}
    changed: List[str] = []
    if upd.name is not None and upd.name != user.get("name"):
        sets["name"] = upd.name
        changed.append("name")
    if upd.phone is not None and upd.phone != (user.get("phone") or ""):
        sets["phone"] = upd.phone or None
        changed.append("phone")
    if upd.notification_preferences is not None:
        current = merge_notification_prefs(user.get(PREF_FIELD))
        diff = {k: bool(v) for k, v in upd.notification_preferences.items()
                if bool(v) != current.get(k) or k not in (user.get(PREF_FIELD) or {})}
        # dotted $set = partial merge, same write shape as /api/org-admin/profile
        for k, v in diff.items():
            sets[f"{PREF_FIELD}.{k}"] = v
        if diff:
            changed.append(PREF_FIELD)
    if sets:
        sets["updated_at"] = utcnow()
        # the filter pins the caller's own record — nothing else can match
        await db.users.update_one({"_id": oid, "email": user.get("email")}, {"$set": sets})
        if "name" in sets:
            await db.organization_members.update_many(
                {"user_id": ctx.user_id, "organization_id": org_match(ctx.organization_id)},
                {"$set": {"name": sets["name"]}})
        meta = request_meta(request)
        await aaudit("profile.updated", "users", user=ctx.audit_user(), ip=meta["ip"],
                     user_agent=meta["user_agent"], organization_id=ctx.organization_id,
                     resource_type="user", resource_id=ctx.user_id,
                     details={"fields": changed})
    return {"success": True, "changed": changed,
            "profile": await _profile_payload(db, ctx)}


# ── usage / caps ────────────────────────────────────────────────────────────

async def _org_usage(ctx: TenantContext, db) -> Dict[str, Any]:
    """Organization meters (plan, tokens, demo) — the same data as
    GET /api/billing/usage, which every member may read."""
    try:
        from app.billing.entitlements import EntitlementService
        summary = await EntitlementService.get_usage_summary(ctx.organization_id, db=db)
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("usage summary failed: %s", e)
        summary = {"plan": {}, "tokens": None, "metrics": {}, "period": {}}
    try:
        from app.lifecycle.demo import demo_status
        summary["demo"] = await asyncio.to_thread(demo_status, ctx.organization_id)
    except Exception:
        summary["demo"] = None
    return _clean(summary)


def _search_caps(usage: Dict[str, Any]) -> Dict[str, Any]:
    """Effective per-search caps = min(plan/demo cap, admin hard cap)."""
    limits = ((usage.get("plan") or {}).get("limits") or {})
    try:
        from app.admin.settings import effective_limits
        lim = effective_limits()
    except Exception:
        lim = {"max_posts_default": 20, "max_posts_cap": 100,
               "max_comments_per_post_default": 30, "max_comments_per_post_cap": 500,
               "global_max_comments": 100}

    def plan_cap(key):
        try:
            v = int(limits.get(key))
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None

    posts_plan = plan_cap("posts_per_search")
    comments_plan = plan_cap("comments_per_post")
    posts_max = min(x for x in (posts_plan, lim.get("max_posts_cap")) if x) \
        if (posts_plan or lim.get("max_posts_cap")) else None
    comments_max = min(x for x in (comments_plan, lim.get("max_comments_per_post_cap")) if x) \
        if (comments_plan or lim.get("max_comments_per_post_cap")) else None
    posts_default = min(lim.get("max_posts_default") or 20, posts_max or 10 ** 6)
    comments_default = min(lim.get("max_comments_per_post_default") or 30,
                           comments_max or 10 ** 6)
    return {
        "posts_per_search": posts_max, "comments_per_post": comments_max,
        "posts_default": posts_default, "comments_default": comments_default,
        "plan_posts_per_search": posts_plan, "plan_comments_per_post": comments_plan,
        "global_max_comments": lim.get("global_max_comments"),
        "max_leads": plan_cap("max_leads"),
    }


def _token_costs() -> Dict[str, int]:
    try:
        from app.lifecycle.config import get_token_costs
        return {k: int(v) for k, v in (get_token_costs() or {}).items()
                if isinstance(v, (int, float))}
    except Exception:
        return {}


def _blockers(usage: Dict[str, Any], costs: Dict[str, int],
              org_status: str) -> List[Dict[str, Any]]:
    """Reasons a new search would be refused right now (checked client-side
    BEFORE running; the server enforces them regardless)."""
    out: List[Dict[str, Any]] = []
    demo = usage.get("demo") or None
    tokens = usage.get("tokens") or None
    if org_status not in ("active", "demo", "trial"):
        out.append({"code": "ORGANIZATION_INACTIVE",
                    "message": f"Your workspace is {org_status}."})
    if demo and demo.get("expired"):
        out.append({"code": "DEMO_EXPIRED", "message": "Your demo has ended."})
    if tokens:
        if tokens.get("expired"):
            out.append({"code": "TOKENS_EXPIRED", "message": "Your tokens have expired."})
        elif costs.get("search") and int(tokens.get("remaining") or 0) < int(costs["search"]):
            out.append({"code": "TOKENS_EXHAUSTED",
                        "needed": costs["search"], "remaining": tokens.get("remaining"),
                        "message": (f"A search needs {costs['search']} tokens and you have "
                                    f"{tokens.get('remaining') or 0} left.")})
    m = ((usage.get("metrics") or {}).get("monthly_searches") or {})
    if m.get("limit") and m.get("used", 0) >= m.get("limit"):
        out.append({"code": "QUOTA_EXCEEDED", "metric": "monthly_searches",
                    "used": m.get("used"), "limit": m.get("limit"),
                    "message": f"Monthly search limit reached ({m.get('used')}/{m.get('limit')})."})
    return out


async def _ledger_totals(db, ctx: TenantContext, since: Optional[datetime]) -> Dict[str, Any]:
    base = {"organization_id": str(ctx.organization_id), "user_id": ctx.user_id,
            "type": "consume"}
    total = 0
    period = 0
    by_reason: Dict[str, int] = {}
    async for row in db.token_ledger.find(base, {"amount": 1, "reason": 1, "created_at": 1}):
        amt = int(row.get("amount") or 0)
        total += amt
        reason = row.get("reason") or "other"
        by_reason[reason] = by_reason.get(reason, 0) + amt
        created = _as_dt(row.get("created_at"))
        if since is None or (created and created >= since):
            period += amt
    return {"total": total, "period": period, "by_reason": by_reason}


async def _own_counts(db, ctx: TenantContext, since: Optional[datetime]) -> Dict[str, Any]:
    def own(q=None, **kw):
        return scope_query(ctx, q or {}, **(kw or _OWN))

    period_q = {"created_at": {"$gte": since}} if since else {}
    searches = await db.search_history.count_documents(own())
    searches_period = await db.search_history.count_documents(own(period_q)) if since else searches
    leads_q = {"is_lead": True}
    return {
        "searches": searches,
        "searches_period": searches_period,
        "searches_running": await db.search_history.count_documents(own({"status": "running"})),
        "searches_completed": await db.search_history.count_documents(own({"status": "completed"})),
        "searches_failed": await db.search_history.count_documents(own({"status": "error"})),
        "leads": await db.ai_comments.count_documents(own(leads_q, **_LEAD)),
        "leads_created": await db.ai_comments.count_documents(own(leads_q)),
        "hot_leads": await db.ai_comments.count_documents(own(
            {"is_lead": True, "$or": [{"lead_quality": "hot"}, {"lead_score": {"$gte": 80}}]},
            **_LEAD)),
        "new_leads": await db.ai_comments.count_documents(own(
            {"is_lead": True, "lead_status": {"$in": [None, "new"]}}, **_LEAD)),
        "assigned_to_me": await db.ai_comments.count_documents(scope_query(
            ctx, {"is_lead": True, "assigned_user_id": ctx.user_id}, **_LEAD)),
        "exports": await db.exports.count_documents(own()),
        "exports_period": await db.exports.count_documents(own(period_q)) if since else None,
    }


@router.get("/usage")
async def my_usage(ctx: TenantContext = Depends(get_org_context)):
    """Own consumption (searches, leads, exports, tokens) + org meters."""
    db = _db()
    usage = await _org_usage(ctx, db)
    since = _as_dt((usage.get("period") or {}).get("start"))
    counts = await _own_counts(db, ctx, since)
    tokens = await _ledger_totals(db, ctx, since)
    costs = _token_costs()
    return {"success": True, "me": {**counts, "tokens_consumed": tokens["total"],
                                    "tokens_consumed_period": tokens["period"],
                                    "tokens_by_reason": tokens["by_reason"]},
            "organization": usage, "caps": _search_caps(usage), "token_costs": costs,
            "blockers": _blockers(usage, costs, ctx.organization_status)}


@router.get("/usage/ledger")
async def my_token_ledger(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                          reason: Optional[str] = Query(None, max_length=40),
                          ctx: TenantContext = Depends(get_org_context)):
    """Own token ledger rows (``user_id`` == me within my organization)."""
    db = _db()
    q: Dict[str, Any] = {"organization_id": str(ctx.organization_id), "user_id": ctx.user_id}
    if reason:
        q["reason"] = reason
    total = await db.token_ledger.count_documents(q)
    items = []
    cursor = db.token_ledger.find(q).sort("created_at", -1) \
        .skip((page - 1) * page_size).limit(page_size)
    async for row in cursor:
        items.append({"id": str(row["_id"]), "type": row.get("type"),
                      "amount": row.get("amount"), "reason": row.get("reason"),
                      "reference": row.get("reference"),
                      "balance_after": row.get("balance_after"),
                      "created_at": _iso(row.get("created_at"))})
    return {"success": True, "items": items, **_page_meta(total, page, page_size)}


# ── dashboard summary ───────────────────────────────────────────────────────

def _lead_view(doc: Dict[str, Any], ctx: TenantContext) -> Dict[str, Any]:
    d = _clean(doc)
    return {k: d.get(k) for k in (
        "id", "commenter_name", "comment_text", "platform", "lead_score", "lead_quality",
        "lead_status", "lead_priority", "priority", "intent", "phone", "email", "whatsapp",
        "location", "budget", "requirement", "confidence", "reason", "assigned_to",
        "assigned_user_id", "lead_created_at", "created_at", "search_run_id",
        "comment_ref", "post_ref", "page_ref")} | {
        "assigned_to_me": doc.get("assigned_user_id") == ctx.user_id,
        "owned_by_me": doc.get("user_id") == ctx.user_id or (
            not doc.get("user_id") and doc.get("created_by") == ctx.email),
    }


def _search_view(doc: Dict[str, Any]) -> Dict[str, Any]:
    d = _clean(doc)
    intent = d.get("intent") or {}
    return {
        "id": d.get("id"), "run_id": d.get("run_id"), "query": d.get("query"),
        "platform": d.get("platform") or intent.get("platform"),
        "canonical_url": intent.get("canonical_url"),
        "status": d.get("status"), "phase": d.get("phase"), "message": d.get("message"),
        "error": d.get("error"), "pages_stored": d.get("pages_stored"),
        "limit": d.get("limit"), "max_comments_per_post": intent.get("max_comments_per_post"),
        "created_at": d.get("created_at"), "completed_at": d.get("completed_at"),
    }


async def _subscription_state(ctx: TenantContext, db) -> Optional[Dict[str, Any]]:
    try:
        from app.billing.subscriptions import get_organization_subscription
        sub = await get_organization_subscription(ctx.organization_id, db=db)
    except Exception:
        return None
    pending = (sub or {}).get("pending")
    out = {"status": (sub or {}).get("status"),
           "plan_name": ((sub or {}).get("plan") or {}).get("name"),
           "is_demo": bool((sub or {}).get("is_demo")),
           "cancel_at_period_end": bool((sub or {}).get("cancel_at_period_end")),
           "pending": None}
    if pending:
        out["pending"] = {"status": pending.get("status"),
                          "plan_name": (pending.get("plan") or {}).get("name")
                          or pending.get("plan_id"),
                          "created_at": _iso(pending.get("created_at"))}
    return out


def _alerts(usage: Dict[str, Any], sub: Optional[Dict[str, Any]], counts: Dict[str, Any],
            blockers: List[Dict[str, Any]], failed_recent: int) -> List[Dict[str, Any]]:
    alerts: List[Dict[str, Any]] = []
    demo = usage.get("demo") or None
    tokens = usage.get("tokens") or None
    codes = {b["code"] for b in blockers}
    for b in blockers:
        alerts.append({"level": "danger", "code": b["code"], "message": b["message"],
                       "action": "upgrade"})
    if demo and not demo.get("expired"):
        days = int(demo.get("days_remaining") or 0)
        alerts.append({"level": "warning" if days <= 2 else "info", "code": "DEMO_ACTIVE",
                       "message": (f"Demo ends in {days} day{'s' if days != 1 else ''}."
                                   if days else "Your demo ends today."),
                       "expires_at": demo.get("expires_at"), "action": "upgrade"})
    if tokens and not tokens.get("expired") and "TOKENS_EXHAUSTED" not in codes:
        allocated = int(tokens.get("allocated") or 0)
        remaining = int(tokens.get("remaining") or 0)
        if allocated and remaining * 100 / allocated <= 20:
            alerts.append({"level": "warning", "code": "TOKENS_LOW",
                           "message": f"Only {remaining} of {allocated} tokens left.",
                           "action": "upgrade"})
    if sub and sub.get("pending"):
        alerts.append({"level": "info", "code": "SUBSCRIPTION_PENDING",
                       "message": (f"{sub['pending'].get('plan_name') or 'Your new plan'} is "
                                   "pending confirmation by our team."),
                       "action": "billing"})
    if counts.get("assigned_to_me"):
        alerts.append({"level": "info", "code": "LEADS_ASSIGNED",
                       "message": f"{counts['assigned_to_me']} lead(s) are assigned to you.",
                       "action": "assigned"})
    if failed_recent:
        alerts.append({"level": "warning", "code": "SEARCHES_FAILED",
                       "message": f"{failed_recent} search(es) failed in the last 24 hours.",
                       "action": "history"})
    return alerts


@router.get("/summary")
async def my_dashboard(ctx: TenantContext = Depends(get_org_context)):
    """Everything the dashboard needs in one call — own data only."""
    db = _db()
    usage = await _org_usage(ctx, db)
    since = _as_dt((usage.get("period") or {}).get("start"))
    counts = await _own_counts(db, ctx, since)
    costs = _token_costs()
    blockers = _blockers(usage, costs, ctx.organization_status)

    recent_searches = []
    async for d in db.search_history.find(scope_query(ctx, {}, **_OWN)) \
            .sort("created_at", -1).limit(5):
        recent_searches.append(_search_view(d))
    recent_leads = []
    async for d in db.ai_comments.find(scope_query(ctx, {"is_lead": True}, **_LEAD)) \
            .sort([("lead_created_at", -1), ("created_at", -1)]).limit(5):
        recent_leads.append(_lead_view(d, ctx))

    from datetime import timedelta
    failed_recent = await db.search_history.count_documents(scope_query(
        ctx, {"status": "error", "created_at": {"$gte": utcnow() - timedelta(days=1)}}, **_OWN))
    sub = await _subscription_state(ctx, db)
    tokens = await _ledger_totals(db, ctx, since)
    return {
        "success": True,
        "user": {"name": ctx.name, "email": ctx.email, "role": ctx.org_role,
                 "role_label": _role_label(ctx.org_role)},
        "organization": {"id": ctx.organization_id, "name": ctx.organization_name,
                         "status": ctx.organization_status},
        "counts": {**counts, "tokens_consumed": tokens["total"],
                   "tokens_consumed_period": tokens["period"]},
        "recent_searches": recent_searches,
        "recent_leads": recent_leads,
        "usage": usage,
        "caps": _search_caps(usage),
        "token_costs": costs,
        "blockers": blockers,
        "subscription": sub,
        "alerts": _alerts(usage, sub, counts, blockers, failed_recent),
    }


# ── lists ───────────────────────────────────────────────────────────────────

_SEARCH_SORTS = {"newest": [("created_at", -1)], "oldest": [("created_at", 1)],
                 "status": [("status", 1), ("created_at", -1)]}


@router.get("/searches")
async def my_searches(
    q: Optional[str] = Query(None, max_length=200),
    status: Optional[str] = Query(None, pattern="^(running|completed|error|cancelled)$"),
    platform: Optional[str] = Query(None, pattern="^(facebook|instagram|youtube|linkedin)$"),
    sort: str = Query("newest"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    ctx: TenantContext = Depends(require_org_permission(P.SEARCH_VIEW)),
):
    """Own search history with search / filter / sort / pagination."""
    db = _db()
    query: Dict[str, Any] = {}
    if q:
        query["query"] = {"$regex": re.escape(q.strip()), "$options": "i"}
    if status:
        query["status"] = status
    if platform:
        query["$or"] = [{"platform": platform}, {"intent.platform": platform}]
    query = scope_query(ctx, query, **_OWN)
    total = await db.search_history.count_documents(query)
    items = []
    async for d in db.search_history.find(query) \
            .sort(_SEARCH_SORTS.get(sort, _SEARCH_SORTS["newest"])) \
            .skip((page - 1) * page_size).limit(page_size):
        items.append(_search_view(d))
    return {"success": True, "items": items, **_page_meta(total, page, page_size)}


_LEAD_SORTS = {
    "score": [("lead_score", -1), ("lead_created_at", -1)],
    "newest": [("lead_created_at", -1), ("created_at", -1)],
    "oldest": [("lead_created_at", 1), ("created_at", 1)],
    "updated": [("lead_updated_at", -1)],
}


@router.get("/leads")
async def my_leads(
    view: str = Query("all", pattern="^(all|mine|assigned)$"),
    status: Optional[str] = Query(None, max_length=30),
    priority: Optional[str] = Query(None, pattern="^(high|medium|low)$"),
    quality: Optional[str] = Query(None, pattern="^(hot|warm|cold)$"),
    platform: Optional[str] = Query(None, pattern="^(facebook|instagram|youtube|linkedin)$"),
    min_score: Optional[int] = Query(None, ge=0, le=100),
    q: Optional[str] = Query(None, max_length=200),
    run_id: Optional[str] = Query(None, max_length=80),
    comment_ref: Optional[str] = Query(None, max_length=40,
                                       description="AI analysis of one raw comment "
                                                   "(also non-leads)"),
    sort: str = Query("score"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    ctx: TenantContext = Depends(require_org_permission(P.LEADS_VIEW)),
):
    """My leads: ``view=mine`` (found by my searches), ``view=assigned``
    (assigned to me by an Admin) or ``all`` (both). Always inside my tenant
    and never another member's unassigned leads."""
    db = _db()
    # comment_ref resolves the AI analysis of one comment (lead or not) for
    # the lead-intelligence dossier opened from the comments view
    base: Dict[str, Any] = {"comment_ref": comment_ref} if comment_ref else {"is_lead": True}
    if status:
        base["lead_status"] = {"$in": [None, "new"]} if status == "new" else status
    if priority:
        base["$or"] = [{"lead_priority": priority},
                       {"lead_priority": {"$in": [None, ""]}, "priority": priority}]
    if quality:
        base["lead_quality"] = quality
    if platform:
        base["platform"] = platform
    if min_score is not None:
        base["lead_score"] = {"$gte": min_score}
    if run_id:
        base["search_run_id"] = run_id
    clauses: List[Dict[str, Any]] = [base]
    if q:
        rx = {"$regex": re.escape(q.strip()), "$options": "i"}
        clauses.append({"$or": [{"commenter_name": rx}, {"comment_text": rx},
                                {"reason": rx}, {"requirement": rx}, {"location": rx}]})
    if view == "assigned":
        clauses.append({"assigned_user_id": ctx.user_id})
    elif view == "mine":
        clauses.append(scope_query(ctx, {}, **_OWN))
    query = scope_query(ctx, {"$and": clauses} if len(clauses) > 1 else base, **_LEAD)

    total = await db.ai_comments.count_documents(query)
    items = []
    async for d in db.ai_comments.find(query) \
            .sort(_LEAD_SORTS.get(sort, _LEAD_SORTS["score"])) \
            .skip((page - 1) * page_size).limit(page_size):
        items.append(_lead_view(d, ctx))

    view_counts = {
        "all": await db.ai_comments.count_documents(scope_query(ctx, {"is_lead": True}, **_LEAD)),
        "mine": await db.ai_comments.count_documents(scope_query(
            ctx, {"$and": [{"is_lead": True}, scope_query(ctx, {}, **_OWN)]}, **_LEAD)),
        "assigned": await db.ai_comments.count_documents(scope_query(
            ctx, {"is_lead": True, "assigned_user_id": ctx.user_id}, **_LEAD)),
    }
    return {"success": True, "items": items, "view_counts": view_counts,
            **_page_meta(total, page, page_size)}


_EXPORT_SORTS = {"newest": [("created_at", -1)], "oldest": [("created_at", 1)]}


@router.get("/exports")
async def my_exports(
    scope: Optional[str] = Query(None, pattern="^(pages|posts|comments)$"),
    sort: str = Query("newest"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    ctx: TenantContext = Depends(require_org_permission(P.EXPORTS_VIEW)),
):
    """My own export history (the ``exports`` collection, force-own scoped)."""
    db = _db()
    query: Dict[str, Any] = {}
    if scope:
        query["scope"] = scope
    query = scope_query(ctx, query, **_OWN)
    total = await db.exports.count_documents(query)
    items = []
    async for d in db.exports.find(query) \
            .sort(_EXPORT_SORTS.get(sort, _EXPORT_SORTS["newest"])) \
            .skip((page - 1) * page_size).limit(page_size):
        c = _clean(d)
        items.append({k: c.get(k) for k in (
            "id", "scope", "run_id", "page_id", "post_id", "only_leads", "format",
            "status", "created_at")})
    return {"success": True, "items": items, **_page_meta(total, page, page_size)}
