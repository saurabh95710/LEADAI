"""
LeadAI Agent API - the whole product surface.

  POST /api/url/search                  start a URL-based search (Apify)
  GET  /api/url/search/{run_id}/report  full report bundle for a URL run
  GET  /api/search/history              recent runs
  GET  /api/search/{run_id}             run status + pages
  POST /api/search/{run_id}/cancel      cancel a running search
  GET  /api/pages                       list pages (filter/paginate)
  GET  /api/pages/{id}                  one page
  POST /api/pages/{id}/posts            collect posts of THAT page (Apify)
  GET  /api/pages/{id}/posts            cached posts + collection status
  GET  /api/posts/{id}                  one post
  POST /api/posts/{id}/comments         collect comments of THAT post + AI analysis
  GET  /api/posts/{id}/comments         analyzed comments (leads only by default)
  GET  /api/comments/{id}               lead detail (comment + AI + context)
  GET  /api/export/{pages|posts|comments}.csv

Tenant isolation: every route resolves a ``TenantContext`` (fail closed - no
session / no active membership -> 401/403) and EVERY query, including child
lookups, updates and deletes, is ANDed with ``scope_query``:
  * org owner/admin (data.view_all) -> the whole organization
  * everyone else                   -> own records (+ leads assigned to them)
Out-of-scope ids answer 404 and are logged as security events.

Real-time: every collect runs in a background task; the GET endpoints
expose live status fields (running / completed / error) to poll.
"""
import asyncio
import csv
import io
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from app.auth import permissions as P
from app.auth.tenant import (
    TenantContext,
    find_scoped_or_404,
    org_match,
    report_out_of_scope,
    require_org_permission,
    scope_query,
    stamp,
)
from app.admin.audit import aaudit
from app.db.mongo import get_async_db
from app.db.models import utcnow
from app.agent.search import _parse_iso, current_min_comments

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["agent"])

_tasks: dict = {}

# Ownership scoping per collection. search_history / pages / posts / comments
# are owned by user_id (legacy: created_by email); leads (ai_comments) are
# additionally visible to the member they are assigned to.
_OWN = {"owner_field": "user_id", "legacy_email_field": "created_by"}
_LEAD = {**_OWN, "assigned_field": "assigned_user_id"}

# ── Per-user API rate limiting (in-memory) ─────────────────────────────────
_api_rate_limits: dict = {}
_API_RATE_WINDOW = 60  # seconds
_API_RATE_MAX = 30  # requests per window per user
_EXPORT_INFLIGHT = 0
_EXPORT_MAX_CONCURRENT = 3
_MAX_CONCURRENT_RUNS_PER_ORG = 5


def _check_api_rate(user_key: str) -> bool:
    """Return True if the user is within rate limits."""
    import time as _t
    now = _t.time()
    key = user_key or "anonymous"
    stamps = _api_rate_limits.get(key, [])
    stamps = [s for s in stamps if now - s < _API_RATE_WINDOW]
    if len(stamps) >= _API_RATE_MAX:
        _api_rate_limits[key] = stamps
        return False
    stamps.append(now)
    _api_rate_limits[key] = stamps
    return True


def new_url_run_id() -> str:
    """Unguessable run id: timestamp prefix (sortable, human readable) plus
    a random uuid4 fragment."""
    return f"URL{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:12]}"


def _serialize(doc):
    if isinstance(doc, dict):
        out = {}
        for k, v in doc.items():
            key = "id" if k == "_id" else k
            out[key] = _serialize(v)
        return out
    if isinstance(doc, list):
        return [_serialize(v) for v in doc]
    if isinstance(doc, ObjectId):
        return str(doc)
    if isinstance(doc, datetime):
        if doc.tzinfo is None:
            doc = doc.replace(tzinfo=timezone.utc)
        return doc.isoformat()
    return doc


def _oid(value: str):
    try:
        return ObjectId(value)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid id: {value}")


def _safe_oid(value: Any) -> Optional[ObjectId]:
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _resolve_platform(doc: dict, parent: Optional[dict] = None) -> str:
    """Platform for any doc - never guessed, never defaulted to Facebook.

    Order: explicit `platform` field -> the doc's own URL -> parent doc's
    platform/URL -> "unknown". A mismatch between an explicit platform and
    the doc's URL is logged and corrected from the URL (the original source).
    """
    from app.social.url_detector import platform_from_url

    def url_of(d: dict) -> Optional[str]:
        for key in ("post_url", "comment_url", "facebook_url", "url"):
            v = d.get(key)
            if v:
                return v
        return None

    p = doc.get("platform")
    url = url_of(doc)
    if url:
        derived = platform_from_url(url)
        if derived:
            if p and derived != p:
                logger.warning(
                    "[platform] mismatch: doc says %r but %s=%s - correcting to %r",
                    p, url, derived, derived)
            return derived
    if p:
        return p
    if parent:
        return _resolve_platform(parent)
    return "unknown"


# ── Tenant scoping helpers ──────────────────────────────────────────────────

def _scope_kw(collection: str) -> Dict[str, Any]:
    return _LEAD if collection == "ai_comments" else _OWN


def _scoped(ctx: TenantContext, collection: str,
            query: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """``query`` restricted to what ``ctx`` may see in ``collection``."""
    return scope_query(ctx, query or {}, **_scope_kw(collection))


async def _find_or_404(db, collection: str, query: Dict[str, Any],
                       ctx: TenantContext, request: Request,
                       detail: Optional[str] = None) -> Dict[str, Any]:
    """Scoped find_one; 404 (+ security event when the doc exists outside the
    caller's scope) otherwise."""
    try:
        return await find_scoped_or_404(db, collection, query, ctx, request,
                                        **_scope_kw(collection))
    except HTTPException as e:
        if e.status_code == 404 and detail:
            raise HTTPException(status_code=404, detail=detail)
        raise


async def _find_scoped(db, collection: str, query: Dict[str, Any],
                       ctx: TenantContext) -> Optional[Dict[str, Any]]:
    """Scoped find_one for child lookups (None when out of scope)."""
    try:
        return await db[collection].find_one(_scoped(ctx, collection, query))
    except Exception:
        return None


def _db_or_503():
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return db


async def _background(key: str, fn, *args):
    try:
        await asyncio.to_thread(fn, *args)
    except Exception as e:
        logger.error(f"[Agent] Background job {key} failed: {e}")
    finally:
        _tasks.pop(key, None)


def _start(key: str, fn, *args) -> bool:
    """True when a new task was started; False when one is already running."""
    existing = _tasks.get(key)
    if existing and not existing.done():
        return False
    _tasks[key] = asyncio.create_task(_background(key, fn, *args))
    return True


async def _audit(ctx: TenantContext, request: Request, action: str, category: str,
                 *, resource_type: Optional[str] = None,
                 resource_id: Optional[str] = None, success: bool = True,
                 details: Optional[Dict[str, Any]] = None) -> None:
    """Best-effort tenant audit record for a user action."""
    try:
        from app.admin.audit import aaudit, request_meta
        meta = request_meta(request)
        await aaudit(action, category, user=ctx.audit_user(), ip=meta["ip"],
                     user_agent=meta["user_agent"], success=success,
                     details=details or {}, organization_id=ctx.organization_id,
                     resource_type=resource_type, resource_id=resource_id)
    except Exception as e:  # pragma: no cover - audit is best effort
        logger.warning(f"[Agent] audit {action} failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# SEARCH RUNS - status, history, cancellation (URL search writes these docs)
# ─────────────────────────────────────────────────────────────────────────────

async def mark_cancel_requested(run_id: str) -> None:
    """Platform (admin panel) cancellation of a run by id. Tenant routes use
    the scoped ``cancel_search_run`` instead."""
    db = _db_or_503()
    await db.search_history.update_one({"run_id": run_id}, {"$set": {
        "cancel_requested": True, "message": "Cancelling search…",
        "updated_at": utcnow()}})


async def _apply_plan_caps(ctx, max_posts, max_comments, requested_posts, requested_comments):
    """Clamp to the plan/demo per-search caps. An EXPLICIT request above the
    cap is rejected (402) instead of silently doing less than asked."""
    from app.billing.entitlements import EntitlementService
    caps = await asyncio.to_thread(EntitlementService.get_run_caps, ctx.organization_id)
    for value, requested, key, label in (
            (max_posts, requested_posts, "posts_per_search", "posts per search"),
            (max_comments, requested_comments, "comments_per_post", "comments per post")):
        cap = caps.get(key)
        if cap and requested and requested > cap:
            raise HTTPException(status_code=402, detail={
                "success": False, "code": "PLAN_LIMIT", "error": "PLAN_LIMIT", "metric": key,
                "limit": cap, "requested": requested, "upgrade_available": True,
                "message": f"Your plan allows up to {cap} {label}."})
    if caps.get("posts_per_search") and max_posts:
        max_posts = min(max_posts, caps["posts_per_search"])
    if caps.get("comments_per_post") and max_comments:
        max_comments = min(max_comments, caps["comments_per_post"])
    max_leads = caps.get("max_leads")
    if max_leads:
        db = _db_or_503()
        leads = await db.ai_comments.count_documents(
            {"organization_id": org_match(ctx.organization_id), "is_lead": True})
        if leads >= max_leads:
            raise HTTPException(status_code=402, detail={
                "success": False, "code": "PLAN_LIMIT", "error": "PLAN_LIMIT",
                "metric": "max_leads", "limit": max_leads, "used": leads,
                "upgrade_available": True,
                "message": f"Lead limit reached ({leads}/{max_leads}). Upgrade to keep finding leads."})
    return max_posts, max_comments


async def _charge_tokens(ctx, action: str, reference: str) -> None:
    """Spend the configured token cost of ``action`` (402 when short)."""
    from app.lifecycle.config import token_cost
    cost = token_cost(action)
    if not cost:
        return
    from app.billing.tokens import consume
    bal = await asyncio.to_thread(consume, ctx.organization_id, cost, user_id=ctx.user_id,
                                  reason=action, reference=reference)
    if bal is not None:
        await aaudit("tokens.consumed", "billing", user=ctx.audit_user(),
                     organization_id=ctx.organization_id, resource_type="token_balance",
                     resource_id=ctx.organization_id,
                     details={"action": action, "amount": cost, "reference": reference,
                              "remaining": bal.get("remaining")})


async def _refund_tokens(ctx, action: str, reference: str, why: str) -> None:
    """Give back the cost of ``action`` charged by _charge_tokens."""
    from app.lifecycle.config import token_cost
    cost = token_cost(action)
    if not cost:
        return
    from app.billing.tokens import refund
    await asyncio.to_thread(refund, ctx.organization_id, cost, reason=f"{action}: {why}",
                            reference=reference)


@router.post("/search/{run_id}/cancel")
async def cancel_search_run(run_id: str, request: Request,
                            ctx: TenantContext = Depends(require_org_permission(P.SEARCH_CANCEL))):
    """Request cancellation of a running URL-based search.
    The background worker picks up `cancel_requested` at its next checkpoint
    and aborts the in-flight Apify run. Poll GET /api/search/{run_id} to see
    the final status (`cancelled`)."""
    db = _db_or_503()
    run = await _find_or_404(db, "search_history", {"run_id": run_id}, ctx, request,
                             detail="Search run not found")
    if run.get("status") != "running":
        return {"run_id": run_id, "status": run.get("status"),
                "message": "This search is no longer running"}
    await db.search_history.update_one(
        _scoped(ctx, "search_history", {"_id": run["_id"]}), {"$set": {
            "cancel_requested": True, "message": "Cancelling search…",
            "updated_at": utcnow()}})
    await _audit(ctx, request, "search.cancel_requested", "search",
                 resource_type="search_run", resource_id=run_id)
    return {"run_id": run_id, "status": "cancelling",
            "message": "Cancellation requested — stopping soon"}


@router.get("/search/history")
async def search_history(request: Request, limit: int = Query(20, ge=1, le=100),
                         ctx: TenantContext = Depends(require_org_permission(P.SEARCH_VIEW))):
    db = get_async_db()
    if db is None:
        return {"searches": [], "count": 0}
    cursor = db.search_history.find(_scoped(ctx, "search_history")) \
        .sort("created_at", -1).limit(limit)
    docs = [_serialize(doc) async for doc in cursor]
    return {"searches": docs, "count": len(docs)}


@router.delete("/search/{run_id}")
async def delete_search_run(run_id: str, request: Request,
                            ctx: TenantContext = Depends(require_org_permission(P.SEARCH_CANCEL))):
    """Delete a search run and everything it produced: the run doc, its
    pages, their posts, and all raw + AI-analyzed comments (leads).
    Members may only delete their own runs; org admins any run of the org."""
    db = _db_or_503()
    run = await _find_or_404(db, "search_history", {"run_id": run_id}, ctx, request,
                             detail="Search run not found")
    run_filter = _scoped(ctx, "search_history", {"_id": run["_id"]})
    if run.get("status") == "running":
        # best-effort: the background worker aborts at its next checkpoint
        await db.search_history.update_one(run_filter, {"$set": {
            "cancel_requested": True}})

    page_filter = _scoped(ctx, "facebook_pages", {"search_run_id": run_id})
    page_ids = [str(p["_id"]) async for p in db.facebook_pages.find(page_filter, {"_id": 1})]
    post_ids = []
    if page_ids:
        post_ids = [str(p["_id"]) async for p in db.facebook_posts.find(
            _scoped(ctx, "facebook_posts", {"page_ref": {"$in": page_ids}}), {"_id": 1})]

    counts = {"pages": len(page_ids), "posts": len(post_ids),
              "comments": 0, "leads": 0}
    if post_ids:
        comment_filter = _scoped(ctx, "facebook_comments", {"post_ref": {"$in": post_ids}})
        comment_ids = [str(c["_id"]) async for c in
                       db.facebook_comments.find(comment_filter, {"_id": 1})]
        counts["comments"] = (await db.facebook_comments.delete_many(
            comment_filter)).deleted_count
        # deletes require ownership - an assignment alone is not enough
        counts["leads"] = (await db.ai_comments.delete_many(scope_query(
            ctx, {"$or": [{"post_ref": {"$in": post_ids}},
                          {"page_ref": {"$in": page_ids}}]}, **_OWN))).deleted_count
        # Clean up comment filter results of exactly the deleted comments
        if comment_ids:
            await db.comment_filter_results.delete_many(
                {"comment_id": {"$in": comment_ids}})
        await db.facebook_posts.delete_many(
            _scoped(ctx, "facebook_posts", {"page_ref": {"$in": page_ids}}))
    if page_ids:
        await db.facebook_pages.delete_many(page_filter)
    await db.search_history.delete_one(run_filter)
    await _audit(ctx, request, "search.deleted", "search",
                 resource_type="search_run", resource_id=run_id, details=counts)

    return {"run_id": run_id, "deleted": counts,
            "message": "Search deleted"}


@router.get("/search/{run_id}")
async def get_search_run(run_id: str, request: Request,
                         ctx: TenantContext = Depends(require_org_permission(P.SEARCH_VIEW))):
    db = _db_or_503()
    doc = await _find_or_404(db, "search_history", {"run_id": run_id}, ctx, request,
                             detail="Search run not found")
    pages = []
    async for p in db.facebook_pages.find(
            _scoped(ctx, "facebook_pages", {"search_run_id": run_id})) \
            .sort("followers", -1).limit(200):
        pages.append(_serialize(p))
    scrape_info = doc.get("scrape_info") or {}
    return {
        "success": doc.get("status") != "error",
        "items": pages,
        "count": len(pages),
        "datasetId": scrape_info.get("datasetId"),
        "runId": scrape_info.get("runId"),
        "scrape_info": scrape_info,
        "search": _serialize(doc),
        "pages": pages,
    }


# ─────────────────────────────────────────────────────────────────────────────
# URL-BASED SEARCH - paste a Facebook/Instagram/YouTube/LinkedIn URL
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/url/search")
async def start_url_search(
    request: Request,
    url: str = Query(..., min_length=4, max_length=300,
                     description="Facebook page, Instagram profile, YouTube channel or LinkedIn company URL"),
    max_posts: Optional[int] = Query(None, ge=1,
                                     description="Posts to scrape (default/cap from admin limits)"),
    max_comments_per_post: Optional[int] = Query(None, ge=1,
                                                 description="Comments to scrape per post (admin-capped)"),
    filter_mode: str = Query("all", description="all | preset | custom"),
    preset: Optional[str] = Query(None,
                                  description="Preset key or saved rule id (filter_mode=preset)"),
    include_keywords: Optional[str] = Query(None,
                                            description="Comma-separated keywords (filter_mode=custom)"),
    exclude_keywords: Optional[str] = Query(None,
                                            description="Comma-separated keywords to veto"),
    categories: Optional[str] = Query(None,
                                      description="Comma-separated category keys (filter_mode=custom)"),
    match_mode: str = Query("any", description="any | all | category | advanced"),
    ctx: TenantContext = Depends(require_org_permission(P.SEARCH_CREATE)),
):
    """Start a URL-based social lead search. Poll GET /api/search/{run_id}.

    Comment filtering: ``filter_mode=preset`` uses a preset key (contact
    signals / high intent / info request / noise & spam) or a saved admin
    rule id; ``filter_mode=custom`` uses inline keywords/categories;
    ``filter_mode=all`` (default) sets no per-run filter, so the admin's
    active rule applies when one is configured (otherwise every comment is
    processed)."""
    from app.social.url_detector import detect_social_url, UrlError
    from app.social.url_search import UrlSearchThread
    from app.admin.settings import effective_limits, is_platform_enabled, get_bool

    db = _db_or_503()

    # Per-user rate limit
    if not _check_api_rate(ctx.user_id):
        raise HTTPException(
            status_code=429,
            detail={"success": False, "errorType": "rate_limit",
                    "message": "Too many requests. Please wait a moment."})

    if not get_bool("features.url_search.enabled"):
        raise HTTPException(
            status_code=403,
            detail={"success": False, "errorType": "feature_disabled",
                    "message": "New searches are currently disabled by the administrator."})

    try:
        platform, canonical = detect_social_url(url)
    except UrlError as e:
        raise HTTPException(status_code=422, detail={
            "success": False, "errorType": e.kind, "message": e.message})
    except Exception as e:
        raise HTTPException(status_code=422, detail={
            "success": False, "errorType": "invalid", "message": f"Invalid URL: {e}"})

    if not is_platform_enabled(platform):
        raise HTTPException(
            status_code=403,
            detail={"success": False, "errorType": "platform_disabled",
                    "message": f"Searching {platform} is currently disabled by the administrator."})

    run_id = new_url_run_id()
    await _audit(ctx, request, "search.url_submitted", "search",
                 resource_type="search_run", resource_id=run_id,
                 details={"url": canonical, "platform": platform})

    # admin-controlled defaults + hard caps — enforcement lives server-side
    lim = effective_limits()
    if lim.get("stop_on_limit"):
        try:
            running = await db.search_history.count_documents(
                {"status": "running",
                 "organization_id": org_match(ctx.organization_id)})
            if running >= _MAX_CONCURRENT_RUNS_PER_ORG:
                raise HTTPException(
                    status_code=429,
                    detail={"success": False, "errorType": "cost_limit",
                            "message": "Too many concurrent jobs. Wait for running jobs to finish."})
        except HTTPException:
            raise
        except Exception:
            pass
    requested_posts, requested_comments = max_posts, max_comments_per_post
    max_posts = min(max_posts or lim["max_posts_default"], lim["max_posts_cap"])
    max_comments_per_post = min(
        max_comments_per_post or lim["max_comments_per_post_default"],
        lim["max_comments_per_post_cap"], lim["global_max_comments"])
    max_posts, max_comments_per_post = await _apply_plan_caps(
        ctx, max_posts, max_comments_per_post, requested_posts, requested_comments)

    # Comment Filter layer: explicit per-run config stored on the run doc.
    # filter_mode=all stores nothing extra — the run then follows the admin's
    # active rule (or processes all comments when no rule is active).
    comment_filter: dict = {}
    if filter_mode == "preset" and preset:
        try:
            ObjectId(preset)
            comment_filter = {"mode": "preset", "rule_id": preset}
        except Exception:
            comment_filter = {"mode": "preset", "preset": preset}
    elif filter_mode == "custom":
        comment_filter = {
            "mode": "custom",
            "include_keywords": [k.strip() for k in
                                 (include_keywords or "").split(",") if k.strip()],
            "exclude_keywords": [k.strip() for k in
                                 (exclude_keywords or "").split(",") if k.strip()],
            "categories": [c.strip() for c in
                           (categories or "").split(",") if c.strip()],
            "match_mode": match_mode if match_mode in
            ("any", "all", "category", "advanced") else "any",
        }

    # ── SaaS Entitlement & Quota Enforcement ──────────────────────────────
    from app.billing.entitlements import EntitlementService
    await EntitlementService.enforce_quota_and_consume(
        organization_id=ctx.organization_id,
        feature_key=platform,
        metric=None,
        user_id=ctx.user_id,
        db=db,
    )
    # Tokens first: a TOKENS_EXHAUSTED refusal must not use up a monthly
    # search. If the search quota then refuses, the tokens are refunded.
    await _charge_tokens(ctx, "search", run_id)
    try:
        await EntitlementService.enforce_quota_and_consume(
            organization_id=ctx.organization_id,
            feature_key="url_search",
            metric="monthly_searches",
            quantity=1,
            user_id=ctx.user_id,
            source=f"url_search_{platform}",
            db=db,
        )
    except HTTPException:
        await _refund_tokens(ctx, "search", run_id, "search quota refused")
        raise

    await db.search_history.insert_one(stamp(ctx, {
        "run_id": run_id, "query": url, "intent": {
            "keyword": url, "type": "url", "platform": platform,
            "canonical_url": canonical, "limit": max_posts,
            "max_comments_per_post": max_comments_per_post},
        "comment_filter": comment_filter or None,
        "limit": max_posts, "provider": "apify", "url_search": True,
        "status": "running", "phase": "queued", "message": "Starting URL search...",
        "pages_found": 0, "pages_stored": 0,
        "created_at": utcnow(), "completed_at": None,
    }))
    _start(f"url_search:{run_id}",
           UrlSearchThread(run_id, canonical, max_posts,
                           max_comments_per_post,
                           organization_id=ctx.organization_id,
                           created_by=ctx.email,
                           user_id=ctx.user_id).run)
    await _audit(ctx, request, "search.started", "search",
                 resource_type="search_run", resource_id=run_id,
                 details={"url": canonical, "platform": platform,
                          "max_posts": max_posts,
                          "max_comments_per_post": max_comments_per_post,
                          "comment_filter": (comment_filter or {}).get("mode")})
    return {
        "run_id": run_id, "status": "running", "platform": platform,
        "canonical_url": canonical,
        "message": f"{platform} search started — the page appears in the results below "
                   "when it is fetched",
    }


@router.get("/url/search/{run_id}/report")
async def url_search_report(run_id: str, request: Request,
                            ctx: TenantContext = Depends(require_org_permission(P.SEARCH_VIEW))):
    """Full report bundle for a URL search run: page + posts + comments."""
    from app.agent.search import _activity_status

    db = _db_or_503()
    run = await _find_or_404(db, "search_history", {"run_id": run_id}, ctx, request,
                             detail="Search run not found")

    pages = []
    async for p in db.facebook_pages.find(
            _scoped(ctx, "facebook_pages", {"search_run_id": run_id})):
        pages.append(_serialize(p))
    if not pages:
        return {
            "run_id": run_id, "status": run.get("status"), "error": run.get("error"),
            "message": run.get("message"), "page": None, "posts": [], "comments": [],
        }

    page = pages[0]
    page_id = page["id"]
    page["platform"] = _resolve_platform(page)
    posts = []
    async for p in db.facebook_posts.find(
            _scoped(ctx, "facebook_posts", {"page_ref": page_id})) \
            .sort("published_date", -1).limit(30):
        doc = _serialize(p)
        doc["total_comment_count"] = doc.get("total_comment_count") or doc.get("comments_count")
        doc.setdefault("scraped_comment_count", None)
        doc["platform"] = _resolve_platform(doc, page)
        posts.append(doc)

    post_ids = [p["id"] for p in posts]
    comments = []
    if post_ids:
        async for c in db.facebook_comments.find(
                _scoped(ctx, "facebook_comments", {"post_ref": {"$in": post_ids}})) \
                .sort("published_date", -1).limit(100):
            comments.append(_serialize(c))
    for c in comments:
        c["platform"] = _resolve_platform(c)
        c["commenter_name"] = c.get("author_name")
        c["comment_text"] = c.get("text")
        # enrich raw comments with AI analysis (phone, email, intent, lead score…)
        analysis = await _find_scoped(db, "ai_comments", {"comment_ref": c["id"]}, ctx)
        if analysis:
            a = _serialize(analysis)
            for key in ("is_lead", "lead_score", "priority", "lead_quality",
                        "confidence", "intent", "urgency", "budget", "requirement",
                        "location", "phone", "email", "whatsapp", "website",
                        "reason", "analyzed_by"):
                if a.get(key) is not None:
                    c[key] = a[key]

    leads = [c for c in comments if c.get("is_lead")]
    page["activity_status"] = page.get("activity_status") or _activity_status(page.get("latest_post_date"))
    return {
        "run_id": run_id,
        "status": run.get("status"),
        "message": run.get("message"),
        "platform": page.get("platform") or _resolve_platform(page),
        "search_url": (run.get("intent") or {}).get("canonical_url") or run.get("query"),
        "page": page,
        "posts": posts,
        "comments": comments,
        "leads": leads,
        "generated_at": utcnow().isoformat(),
    }


@router.get("/pages")
async def list_pages(
    request: Request,
    run_id: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="filter by page name"),
    category: Optional[str] = Query(None),
    city: Optional[str] = Query(None),
    contact: bool = Query(False, description="only pages with phone or email"),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
    ctx: TenantContext = Depends(require_org_permission(P.SEARCH_VIEW)),
):
    db = _db_or_503()
    query: dict = {}
    if run_id:
        query["search_run_id"] = run_id
    if q:
        query["page_name"] = {"$regex": re.escape(q), "$options": "i"}
    if category:
        query["category"] = category
    if city:
        query["city"] = city
    if contact:
        query["$or"] = [{"email": {"$ne": None}}, {"phone": {"$ne": None}}]
    query = _scoped(ctx, "facebook_pages", query)
    docs = []
    async for p in (db.facebook_pages.find(query)
                    .sort([("lead_score", -1), ("followers", -1)])
                    .skip(offset).limit(limit)):
        doc = _serialize(p)
        doc["platform"] = _resolve_platform(doc)
        docs.append(doc)
    total = await db.facebook_pages.count_documents(query)
    return {"pages": docs, "total": total, "offset": offset, "limit": limit}


@router.get("/pages/{page_id}")
async def get_page(page_id: str, request: Request,
                   ctx: TenantContext = Depends(require_org_permission(P.SEARCH_VIEW))):
    db = _db_or_503()
    return _serialize(await _find_or_404(db, "facebook_pages", {"_id": _oid(page_id)},
                                         ctx, request))


@router.post("/pages/{page_id}/posts")
async def collect_posts(page_id: str, request: Request,
                        max_posts: Optional[int] = Query(None, ge=1),
                        ctx: TenantContext = Depends(require_org_permission(P.SEARCH_CREATE))):
    """Collect posts of the selected page via the platform's Apify actor."""
    from app.agent.search import collect_page_posts
    from app.admin.settings import effective_limits

    db = _db_or_503()
    await _find_or_404(db, "facebook_pages", {"_id": _oid(page_id)}, ctx, request)
    lim = effective_limits()
    requested = max_posts
    max_posts = min(max_posts or lim["max_posts_default"], lim["max_posts_cap"])
    max_posts, _ = await _apply_plan_caps(ctx, max_posts, None, requested, None)
    await _charge_tokens(ctx, "collect", f"posts:{page_id}")
    started = _start(f"posts:{page_id}", collect_page_posts, page_id, max_posts)
    if not started:
        return {"status": "running", "message": "Posts collection already in progress"}
    await _audit(ctx, request, "search.posts_requested", "search",
                 resource_type="page", resource_id=page_id,
                 details={"max_posts": max_posts})
    return {"status": "running", "message": "Posts collection started"}


@router.get("/pages/{page_id}/posts")
async def list_page_posts(page_id: str, request: Request,
                          offset: int = Query(0, ge=0),
                          limit: int = Query(50, ge=1, le=200),
                          ctx: TenantContext = Depends(require_org_permission(P.SEARCH_VIEW))):
    db = _db_or_503()
    page = await _find_or_404(db, "facebook_pages", {"_id": _oid(page_id)}, ctx, request)
    docs = []
    async for p in db.facebook_posts.find(
            _scoped(ctx, "facebook_posts", {"page_ref": page_id})):
        doc = _serialize(p)
        # legacy fallback: older posts stored the Facebook total in comments_count
        if doc.get("total_comment_count") is None:
            doc["total_comment_count"] = doc.get("comments_count")
        doc.setdefault("scraped_comment_count", None)
        doc.setdefault("is_relevant", None)
        doc.setdefault("is_qualifying", False)
        doc["platform"] = _resolve_platform(doc, page)
        docs.append(doc)
    # qualifying posts first, then by Facebook-reported comment count
    docs.sort(key=lambda p: (
        1 if p.get("is_qualifying") else 0,
        p.get("total_comment_count") or 0), reverse=True)
    total = len(docs)
    posts = docs[offset:offset + limit]
    for p in posts:
        p["postId"] = p.get("id")
        p["postUrl"] = p.get("post_url")
        p["postText"] = p.get("caption")
        p["commentCount"] = p.get("total_comment_count")
        p["reactionsCount"] = p.get("likes_count")
        p["sharesCount"] = p.get("shares_count")
        p["createdTime"] = p.get("published_date")
        p["thumbnail"] = (p.get("images") or [None])[0]
    return {
        "page": _serialize(page),
        "platform": _resolve_platform(page),
        "posts": posts,
        "total": total,
        "totalPosts": page.get("total_posts_found", page.get("posts_count", 0)),
        "relevantPosts": page.get("relevant_posts_count", 0),
        "qualifyingPosts": page.get("qualifying_posts_count", 0),
        "commentsOnQualifying": page.get("total_comments_on_qualifying_posts", 0),
        "latestPostDate": page.get("latest_post_date"),
        "hasQualifying": page.get("has_qualifying_posts", False),
        "activityStatus": page.get("activity_status"),
        "leadScore": page.get("lead_score", 0),
        "sourceType": page.get("source_type"),
        "minComments": current_min_comments(),
        "posts_status": page.get("posts_status"),
        "posts_count": page.get("posts_count", 0),
        "posts_error": page.get("posts_error"),
        "posts_error_meta": page.get("posts_error_meta"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# POSTS
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/posts/{post_id}")
async def get_post(post_id: str, request: Request,
                   ctx: TenantContext = Depends(require_org_permission(P.SEARCH_VIEW))):
    db = _db_or_503()
    return _serialize(await _find_or_404(db, "facebook_posts", {"_id": _oid(post_id)},
                                         ctx, request))


@router.post("/posts/{post_id}/comments")
async def collect_comments(post_id: str, request: Request,
                           max_comments: Optional[int] = Query(None, ge=1),
                           ctx: TenantContext = Depends(require_org_permission(P.SEARCH_CREATE))):
    """Collect comments of the selected post + run AI analysis (ai_comments)."""
    from app.agent.search import collect_post_comments
    from app.admin.settings import effective_limits

    db = _db_or_503()
    await _find_or_404(db, "facebook_posts", {"_id": _oid(post_id)}, ctx, request)
    lim = effective_limits()
    requested = max_comments
    max_comments = min(max_comments or lim["max_comments_per_post_default"],
                       lim["max_comments_per_post_cap"])
    _, max_comments = await _apply_plan_caps(ctx, None, max_comments, None, requested)
    await _charge_tokens(ctx, "collect", f"comments:{post_id}")
    started = _start(f"comments:{post_id}", collect_post_comments, post_id, max_comments)
    if not started:
        return {"status": "running", "message": "Comments collection already in progress"}
    await _audit(ctx, request, "search.comments_requested", "search",
                 resource_type="post", resource_id=post_id,
                 details={"max_comments": max_comments})
    return {"status": "running", "message": "Comments collection + AI analysis started"}


@router.get("/posts/{post_id}/comments")
async def list_post_comments(
    post_id: str,
    request: Request,
    filter_type: str = Query("all", description="all | leads | contact | hot | warm | pricing | inquiry"),
    only_leads: bool = Query(False, description="show only valuable comments (is_lead)"),
    contact_only: bool = Query(False, description="only comments with phone or email"),
    q: Optional[str] = Query(None, description="Search text in comment, name, phone, email"),
    quality: Optional[str] = Query(None, description="hot | warm | cold"),
    sort_by: str = Query("score", description="score | newest | reactions | oldest"),
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    ctx: TenantContext = Depends(require_org_permission(P.SEARCH_VIEW)),
):
    db = _db_or_503()
    post = await _find_or_404(db, "facebook_posts", {"_id": _oid(post_id)}, ctx, request,
                              detail="Post not found")

    from app.pipeline.comment_ai import extract_contact_quick

    # Load ALL raw comments for this post (within the caller's scope)
    all_raw = []
    async for raw in db.facebook_comments.find(
            _scoped(ctx, "facebook_comments", {"post_ref": post_id})):
        all_raw.append(raw)

    # Load all existing AI analyses for this post's comments in one batch
    comment_ids = [str(r["_id"]) for r in all_raw]
    ai_map = {}
    if comment_ids:
        async for a in db.ai_comments.find(
                _scoped(ctx, "ai_comments", {"comment_ref": {"$in": comment_ids}})):
            ai_map[a.get("comment_ref")] = _serialize(a)

    docs = []
    for raw in all_raw:
        c = _serialize(raw)
        c["platform"] = _resolve_platform(c)
        c["commenter_name"] = raw.get("author_name") or "Unknown"
        c["comment_text"] = raw.get("text") or ""
        quick = extract_contact_quick(raw.get("text"))
        has_contact = raw.get("has_contact")
        if has_contact is None:
            has_contact = bool(quick["phone"] or quick["email"])
        c["has_contact"] = bool(has_contact)
        for k, v in quick.items():
            if v:
                c[k] = v

        # attach AI fields if analyzed
        analysis = ai_map.get(c["id"])
        if analysis:
            for key in ("is_lead", "lead_score", "priority", "lead_quality",
                        "confidence", "intent", "urgency", "budget", "requirement",
                        "location", "phone", "email", "whatsapp", "website",
                        "reason", "analyzed_by"):
                if analysis.get(key) is not None:
                    c[key] = analysis[key]
        else:
            # fallback score calculation if not yet passed through AI batch
            c["lead_score"] = 50 if c["has_contact"] else 10
            c["priority"] = "high" if c["has_contact"] else "low"
            c["lead_quality"] = "warm" if c["has_contact"] else "none"
            c["is_lead"] = c["has_contact"]

        docs.append(c)

    # Compute category counts across the full unfiltered collection
    total_all = len(docs)
    total_leads = sum(1 for d in docs if d.get("is_lead") or (d.get("lead_score") or 0) >= 40)
    total_contact = sum(1 for d in docs if d.get("has_contact") or d.get("phone") or d.get("email"))
    total_hot = sum(1 for d in docs if (d.get("lead_quality") == "hot") or (d.get("lead_score") or 0) >= 80 or d.get("priority") == "high")
    total_pricing = sum(1 for d in docs if d.get("budget") or any(w in (d.get("comment_text") or "").lower() for w in ["price", "cost", "rate", "kitna", "how much", "quote", "charges", "fees", "fee", "budget"]))
    total_inquiry = sum(1 for d in docs if "?" in (d.get("comment_text") or "") or any(w in (d.get("comment_text") or "").lower() for w in ["details", "info", "interested", "available", "where", "how", "share", "send", "call", "dm", "location", "address", "contact"]))

    # Apply active filters
    filtered = docs

    if q and q.strip():
        q_lower = q.strip().lower()
        filtered = [
            d for d in filtered
            if q_lower in (d.get("comment_text") or "").lower()
            or q_lower in (d.get("commenter_name") or "").lower()
            or q_lower in (d.get("phone") or "").lower()
            or q_lower in (d.get("email") or "").lower()
            or q_lower in (d.get("requirement") or "").lower()
            or q_lower in (d.get("location") or "").lower()
            or q_lower in (d.get("reason") or "").lower()
        ]

    # Type / tab filtering
    if filter_type == "leads" or only_leads:
        filtered = [d for d in filtered if d.get("is_lead") or (d.get("lead_score") or 0) >= 40]
    elif filter_type == "contact" or contact_only:
        filtered = [d for d in filtered if d.get("has_contact") or d.get("phone") or d.get("email")]
    elif filter_type == "hot":
        filtered = [d for d in filtered if (d.get("lead_quality") == "hot") or (d.get("lead_score") or 0) >= 80 or d.get("priority") == "high"]
    elif filter_type == "warm":
        filtered = [d for d in filtered if (d.get("lead_quality") == "warm") or (50 <= (d.get("lead_score") or 0) < 80) or d.get("priority") == "medium"]
    elif filter_type == "pricing":
        filtered = [d for d in filtered if d.get("budget") or any(w in (d.get("comment_text") or "").lower() for w in ["price", "cost", "rate", "kitna", "how much", "quote", "charges", "fees", "fee", "budget"])]
    elif filter_type in ("inquiry", "questions"):
        filtered = [d for d in filtered if "?" in (d.get("comment_text") or "") or any(w in (d.get("comment_text") or "").lower() for w in ["details", "info", "interested", "available", "where", "how", "share", "send", "call", "dm", "location", "address", "contact"])]

    if quality:
        filtered = [d for d in filtered if (d.get("lead_quality") or "").lower() == quality.lower()]

    # Sorting
    if sort_by == "newest":
        filtered.sort(key=lambda d: d.get("published_date") or "", reverse=True)
    elif sort_by == "oldest":
        filtered.sort(key=lambda d: d.get("published_date") or "")
    elif sort_by == "reactions":
        filtered.sort(key=lambda d: d.get("reactions_count") or 0, reverse=True)
    else:  # default "score"
        # highest score first, then contact on top, then newest
        filtered.sort(key=lambda d: (
            d.get("lead_score") or 0,
            1 if d.get("has_contact") else 0,
            d.get("published_date") or ""
        ), reverse=True)

    paginated = filtered[offset:offset + limit]

    return {
        "post": _serialize(post),
        "platform": _resolve_platform(post),
        "comments": paginated,
        "total": len(filtered),
        "all_count": total_all,
        "contact_count": total_contact,
        "counts": {
            "all": total_all,
            "leads": total_leads,
            "contact": total_contact,
            "hot": total_hot,
            "pricing": total_pricing,
            "inquiry": total_inquiry,
        },
        "comments_status": post.get("comments_status"),
        "total_comment_count": post.get("total_comment_count") or post.get("comments_count") or total_all,
        "scraped_comment_count": post.get("scraped_comment_count") or total_all,
        "comments_count": post.get("scraped_comment_count") or total_all,
        "minComments": current_min_comments(),
        "comments_error": post.get("comments_error"),
        "comments_error_meta": post.get("comments_error_meta"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# LEAD DETAIL
# ─────────────────────────────────────────────────────────────────────────────

async def _find_by_ref(db, collection: str, ref: Any, ctx: TenantContext):
    oid = _safe_oid(ref)
    if oid is None:
        return None
    return await _find_scoped(db, collection, {"_id": oid}, ctx)


@router.get("/comments/{comment_id}")
async def get_lead_detail(comment_id: str, request: Request,
                          ctx: TenantContext = Depends(require_org_permission(P.SEARCH_VIEW))):
    db = _db_or_503()
    oid = _oid(comment_id)
    # ai_comments are upserted by comment_ref, so a raw comment id resolves
    # to its analysis through that reference.
    analysis = await _find_scoped(db, "ai_comments", {"_id": oid}, ctx) \
        or await _find_scoped(db, "ai_comments", {"comment_ref": comment_id}, ctx)
    if analysis:
        result = _serialize(analysis)
        raw = await _find_by_ref(db, "facebook_comments", analysis.get("comment_ref"), ctx) \
            if analysis.get("comment_ref") else None
        post = await _find_by_ref(db, "facebook_posts", analysis.get("post_ref"), ctx) \
            if analysis.get("post_ref") else None
        page = await _find_by_ref(db, "facebook_pages", analysis.get("page_ref"), ctx) \
            if analysis.get("page_ref") else None
        result["platform"] = _resolve_platform(result, post)
        result["comment"] = _serialize(raw) if raw else None
        result["post"] = _serialize(post) if post else None
        result["page"] = _serialize(page) if page else None
        return result

    # fall back to the raw comment itself
    raw = await _find_scoped(db, "facebook_comments", {"_id": oid}, ctx)
    if not raw:
        # log probes of ids that exist outside the caller's scope
        for coll in ("ai_comments", "facebook_comments"):
            other = await db[coll].find_one({"_id": oid}, {"_id": 1, "organization_id": 1})
            if other:
                report_out_of_scope(request, ctx, coll, other)
                break
        raise HTTPException(status_code=404, detail="Comment not found")
    post = await _find_by_ref(db, "facebook_posts", raw.get("post_ref"), ctx) \
        if raw.get("post_ref") else None
    page = await _find_by_ref(db, "facebook_pages", post.get("page_ref"), ctx) \
        if post and post.get("page_ref") else None
    result = _serialize(raw)
    result["platform"] = _resolve_platform(result, post)
    result["commenter_name"] = raw.get("author_name")
    result["comment_text"] = raw.get("text")
    result["comment"] = _serialize(raw)
    result["post"] = _serialize(post) if post else None
    result["page"] = _serialize(page) if page else None
    result["lead_quality"] = "none"
    result["priority"] = None
    result["is_lead"] = False
    return result


# ─────────────────────────────────────────────────────────────────────────────
# EXPORT - CSV
# ─────────────────────────────────────────────────────────────────────────────

PAGES_CSV = ["platform", "page_name", "facebook_url", "page_id", "category", "source_type",
             "followers", "likes", "phone", "email", "whatsapp", "website",
             "address", "city", "state", "verified", "about",
             "total_posts_found", "relevant_posts_count", "qualifying_posts_count",
             "total_comments_on_qualifying_posts", "latest_post_date",
             "activity_status", "lead_score"]
POSTS_CSV = ["platform", "post_id", "page_name", "post_url", "caption", "published_date",
             "likes_count", "total_comment_count", "scraped_comment_count",
             "shares_count", "is_relevant", "is_qualifying", "images"]
COMMENTS_CSV = ["platform", "commenter_name", "commenter_url", "comment_text",
                "comment_date", "comment_time", "phone", "email", "whatsapp", "website",
                "budget", "requirement", "location", "intent", "urgency", "priority",
                "lead_quality", "confidence", "lead_score",
                "lead_status", "lead_priority", "assigned_to"]


def _split_date_time(value) -> tuple:
    """Tolerant actor date string -> (comment_date, comment_time) in LOCAL time.

    The scrapers store dates exactly as the actor returns them (ISO with a T
    and Z, or a bare date), so the CSV splits them into separate, human
    readable date and time columns. Empty strings when the source has no date
    or no time component.
    """
    if not value:
        return "", ""
    if re.match(r"^\d{4}-\d{2}-\d{2}$", str(value).strip()):
        return str(value).strip(), ""
    parsed = _parse_iso(value)
    if parsed is None:
        return str(value).strip(), ""
    local = parsed.astimezone()
    return local.strftime("%Y-%m-%d"), local.strftime("%H:%M:%S")


def _sanitize_csv_value(val: Any) -> str:
    """Convert a value to a safe CSV string.

    Prevents formula injection by prefixing dangerous leading characters
    with a single quote so spreadsheet applications treat them as literal
    text rather than executable formulas.
    """
    if val is None:
        return ""
    # bool check MUST come before int check (isinstance(True, int) is True)
    if isinstance(val, bool):
        val = "Yes" if val else "No"
    elif isinstance(val, (int, float)):
        val = str(val)
    elif isinstance(val, list):
        val = ", ".join(str(x) for x in val)
    else:
        val = str(val).replace("\r\n", " ").replace("\n", " ").replace("\r", " ").strip()
    # Formula-injection defense: prefix cells that start with spreadsheet formula chars
    if val and val[0] in ("=", "+", "-", "@", "\t", "\r"):
        val = "'" + val
    return val


def _csv_response(rows: list, columns: list, filename: str,
                  max_rows: int = 50000) -> Response:
    global _EXPORT_INFLIGHT
    if _EXPORT_INFLIGHT >= _EXPORT_MAX_CONCURRENT:
        raise HTTPException(
            status_code=429,
            detail={"success": False, "errorType": "export_limit",
                    "message": "Too many concurrent exports. Please try again."})
    _EXPORT_INFLIGHT += 1
    try:
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\r\n")
        writer.writerow(columns)
        truncated = len(rows) > max_rows
        for row in rows[:max_rows]:
            writer.writerow([_sanitize_csv_value(row.get(c, "")) for c in columns])
        # utf-8-sig adds the UTF-8 BOM (\xef\xbb\xbf) so Excel on Windows
        # displays Hindi, regional text, and emojis natively
        csv_bytes = output.getvalue().encode("utf-8-sig")
        headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
        if truncated:
            headers["X-Export-Truncated"] = "true"
            headers["X-Export-Total"] = str(len(rows))
        return Response(
            content=csv_bytes,
            media_type="text/csv; charset=utf-8",
            headers=headers,
        )
    finally:
        _EXPORT_INFLIGHT = max(0, _EXPORT_INFLIGHT - 1)


@router.get("/export/{scope}.csv")
async def export_csv(
    scope: str,
    request: Request,
    run_id: Optional[str] = Query(None),
    page_id: Optional[str] = Query(None),
    post_id: Optional[str] = Query(None),
    only_leads: bool = Query(True),
    ctx: TenantContext = Depends(require_org_permission(P.EXPORTS_CREATE)),
):
    from app.admin.settings import get_bool
    if not get_bool("features.exports.enabled"):
        raise HTTPException(
            status_code=403,
            detail="CSV exports are currently disabled by the administrator.")
    db = _db_or_503()
    if scope not in ("pages", "posts", "comments"):
        raise HTTPException(status_code=404, detail="scope must be pages, posts or comments")

    # validate the parent BEFORE consuming quota
    if scope == "posts":
        if not page_id:
            raise HTTPException(status_code=400, detail="page_id is required for posts export")
        await _find_or_404(db, "facebook_pages", {"_id": _oid(page_id)}, ctx, request)
    if scope == "comments":
        if not post_id:
            raise HTTPException(status_code=400, detail="post_id is required for comments export")
        await _find_or_404(db, "facebook_posts", {"_id": _oid(post_id)}, ctx, request)

    from app.billing.entitlements import EntitlementService
    await EntitlementService.enforce_quota_and_consume(
        organization_id=ctx.organization_id,
        feature_key="csv_export",
        metric="monthly_exports",
        quantity=1,
        user_id=ctx.user_id,
        source=f"export_{scope}",
        db=db,
    )
    await _charge_tokens(ctx, "export", f"export:{scope}")
    try:
        await db.exports.insert_one(stamp(ctx, {
            "scope": scope, "run_id": run_id, "page_id": page_id, "post_id": post_id,
            "only_leads": only_leads, "format": "csv", "status": "completed",
            "created_at": utcnow()}))
    except Exception:
        pass
    await _audit(ctx, request, "export.csv", "exports",
                 resource_type=scope, resource_id=run_id or page_id or post_id,
                 details={"scope": scope, "only_leads": only_leads})

    if scope == "pages":
        query = _scoped(ctx, "facebook_pages", {"search_run_id": run_id} if run_id else {})
        rows = [doc async for doc in db.facebook_pages.find(query).sort("followers", -1)]
        for row in rows:
            row["platform"] = _resolve_platform(row)
        return _csv_response(rows, PAGES_CSV, f"pages_{datetime.now().strftime('%Y%m%d')}.csv")

    if scope == "posts":
        rows = [doc async for doc in db.facebook_posts.find(
            _scoped(ctx, "facebook_posts", {"page_ref": page_id})).sort("published_date", -1)]
        for row in rows:
            if row.get("total_comment_count") is None:
                row["total_comment_count"] = row.get("comments_count")
            row["platform"] = _resolve_platform(row)
        return _csv_response(rows, POSTS_CSV, f"posts_{datetime.now().strftime('%Y%m%d')}.csv")

    # scope == "comments"
    raw_comments = [doc async for doc in db.facebook_comments.find(
        _scoped(ctx, "facebook_comments", {"post_ref": post_id})).sort("published_date", -1)]
    refs = [str(raw["_id"]) for raw in raw_comments]
    ai_by_ref = {}
    if refs:
        async for a in db.ai_comments.find(
                _scoped(ctx, "ai_comments", {"comment_ref": {"$in": refs}})):
            ai_by_ref[a["comment_ref"]] = _serialize(a)

    rows = []
    for raw in raw_comments:
        c_ref = str(raw["_id"])
        ai = ai_by_ref.get(c_ref, {})
        if only_leads and not ai.get("is_lead"):
            continue

        r = _serialize(raw)
        r["platform"] = _resolve_platform(raw)
        r["commenter_name"] = raw.get("author_name") or "User"
        r["comment_text"] = raw.get("text") or ""
        published = raw.get("published_date")
        r["comment_date"], r["comment_time"] = _split_date_time(published)
        url = raw.get("author_profile_url") or raw.get("comment_url") or ""
        r["commenter_url"] = f'=HYPERLINK("{url}","Open profile")' if url.strip() else ""

        for key in ("phone", "email", "whatsapp", "website", "budget", "requirement",
                    "location", "intent", "urgency", "priority", "lead_quality",
                    "confidence", "lead_score",
                    "lead_status", "lead_priority", "assigned_to"):
            if ai.get(key) is not None:
                r[key] = ai[key]
            elif r.get(key) is None:
                r[key] = ""
        rows.append(r)

    rows.sort(key=lambda x: (x.get("lead_score") or 0), reverse=True)
    fname = f"leads_{datetime.now().strftime('%Y%m%d')}.csv" if only_leads else f"comments_{datetime.now().strftime('%Y%m%d')}.csv"
    return _csv_response(rows, COMMENTS_CSV, fname)


# ─────────────────────────────────────────────────────────────────────────────
# Lead Management API (Prompt 7)
# ─────────────────────────────────────────────────────────────────────────────

LEAD_STATUSES = ("new", "contacted", "qualified", "follow_up",
                 "converted", "lost", "disqualified", "archived")
TERMINAL_STATUSES = ("converted", "lost", "disqualified", "archived")
VALID_TRANSITIONS = {
    "new":         ("contacted", "qualified", "follow_up", "disqualified", "lost", "archived"),
    "contacted":   ("qualified", "follow_up", "lost", "archived"),
    "qualified":   ("follow_up", "converted", "lost", "archived"),
    "follow_up":   ("contacted", "qualified", "converted", "lost", "archived"),
    "converted":   ("archived",),
    "lost":        ("archived",),
    "disqualified": ("archived",),
    "archived":    (),
}
LEAD_PRIORITIES = ("high", "medium", "low")


def _lead_oid(lead_id: str) -> ObjectId:
    try:
        return ObjectId(lead_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid lead ID")


async def _get_lead_or_404(db, lead_id: str, ctx: TenantContext,
                           request: Request) -> Dict[str, Any]:
    return await _find_or_404(db, "ai_comments", {"_id": _lead_oid(lead_id)}, ctx, request)


@router.get("/leads")
async def list_leads(
    request: Request,
    status: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    intent: Optional[str] = Query(None),
    min_score: Optional[int] = Query(None),
    max_score: Optional[int] = Query(None),
    search: Optional[str] = Query(None),
    sort: str = Query("score"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    ctx: TenantContext = Depends(require_org_permission(P.LEADS_VIEW)),
):
    """List leads with filtering, sorting, and pagination."""
    db = _db_or_503()

    query: dict = {"is_lead": True}
    if status:
        query["lead_status"] = status
    if priority:
        query["lead_priority"] = priority
    if platform:
        query["platform"] = platform
    if intent:
        query["intent"] = intent
    if min_score is not None:
        query.setdefault("lead_score", {})["$gte"] = min_score
    if max_score is not None:
        query.setdefault("lead_score", {})["$lte"] = max_score
    if search:
        safe_search = re.escape(search)
        query["$or"] = [
            {"commenter_name": {"$regex": safe_search, "$options": "i"}},
            {"comment_text": {"$regex": safe_search, "$options": "i"}},
            {"reason": {"$regex": safe_search, "$options": "i"}},
        ]
    query = _scoped(ctx, "ai_comments", query)

    sort_key = {
        "score": [("lead_score", -1), ("lead_created_at", -1)],
        "newest": [("lead_created_at", -1)],
        "oldest": [("lead_created_at", 1)],
        "priority": [("lead_priority", -1), ("lead_score", -1)],
        "updated": [("lead_updated_at", -1)],
    }.get(sort, [("lead_score", -1), ("lead_created_at", -1)])

    total = await db.ai_comments.count_documents(query)
    skip = (page - 1) * page_size
    cursor = db.ai_comments.find(query).sort(sort_key).skip(skip).limit(page_size)
    leads = [_serialize(doc) async for doc in cursor]

    return {
        "leads": leads,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
    }


@router.get("/leads/{lead_id}")
async def get_lead(lead_id: str, request: Request,
                   ctx: TenantContext = Depends(require_org_permission(P.LEADS_VIEW))):
    """Get a single lead with full context."""
    db = _db_or_503()
    lead = await _get_lead_or_404(db, lead_id, ctx, request)
    result = _serialize(lead)

    # Enrich with source context (each child lookup is scoped as well)
    for ref_key, collection, out_key in (("post_ref", "facebook_posts", "source_post"),
                                         ("page_ref", "facebook_pages", "source_page"),
                                         ("comment_ref", "facebook_comments", "source_comment")):
        if lead.get(ref_key):
            try:
                child = await _find_by_ref(db, collection, lead[ref_key], ctx)
                if child:
                    result[out_key] = _serialize(child)
            except Exception:
                pass

    return result


async def _resolve_assignee(db, ctx: TenantContext, body: Dict[str, Any]
                            ) -> Optional[Dict[str, Any]]:
    """Resolve the requested assignee to an ACTIVE member of the caller's
    organization. Returns None for "unassign"; raises 400 otherwise."""
    raw = body.get("assigned_user_id") if "assigned_user_id" in body else body.get("assigned_to")
    if raw in (None, "") or (isinstance(raw, str) and not raw.strip()):
        return None
    raw = str(raw).strip()
    user = None
    oid = _safe_oid(raw)
    if oid is not None:
        user = await db.users.find_one({"_id": oid})
    if user is None and "@" in raw:
        user = await db.users.find_one({"email": raw.lower()})
    member = None
    if user is not None and user.get("status", "active") == "active":
        member = await db.organization_members.find_one({
            "user_id": str(user["_id"]),
            "organization_id": org_match(ctx.organization_id),
            "status": "active"})
    if not member:
        raise HTTPException(
            status_code=400,
            detail="Assignee must be an active member of this organization")
    return {"user_id": str(user["_id"]), "email": user.get("email"),
            "name": user.get("name")}


@router.patch("/leads/{lead_id}")
async def update_lead(lead_id: str, body: dict, request: Request,
                      ctx: TenantContext = Depends(require_org_permission(P.LEADS_MANAGE))):
    """Update lead status, priority, assignment, or notes."""
    db = _db_or_503()
    lead = await _get_lead_or_404(db, lead_id, ctx, request)
    lead_filter = _scoped(ctx, "ai_comments", {"_id": lead["_id"]})

    updates = {}
    current_status = lead.get("lead_status", "new")

    # Assignment — needs leads.assign and an active member of this org
    assignment_requested = "assigned_to" in body or "assigned_user_id" in body
    assignee = None
    if assignment_requested:
        if not ctx.has(P.LEADS_ASSIGN):
            from app.auth.tenant import _permission_denied
            _permission_denied(request, ctx, P.LEADS_ASSIGN)
        assignee = await _resolve_assignee(db, ctx, body)

    # Status update with transition validation
    new_status = body.get("lead_status")
    history_entry = None
    if new_status and new_status != current_status:
        allowed = VALID_TRANSITIONS.get(current_status, ())
        if new_status not in LEAD_STATUSES:
            raise HTTPException(status_code=400, detail=f"Invalid status: {new_status}")
        if new_status not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot transition from {current_status!r} to {new_status!r}"
            )
        updates["lead_status"] = new_status
        history_entry = {
            "from_status": current_status,
            "to_status": new_status,
            "changed_at": utcnow(),
            "changed_by": ctx.email,
            "changed_by_user_id": ctx.user_id,
            "reason": body.get("reason", ""),
        }

    # Priority update
    new_priority = body.get("lead_priority")
    if new_priority:
        if new_priority not in LEAD_PRIORITIES:
            raise HTTPException(status_code=400, detail=f"Invalid priority: {new_priority}")
        updates["lead_priority"] = new_priority

    if assignment_requested:
        updates["assigned_user_id"] = assignee["user_id"] if assignee else None
        updates["assigned_to"] = assignee["email"] if assignee else None

    if history_entry:
        await db.ai_comments.update_one(lead_filter, {"$push": {"status_history": history_entry}})
    if updates:
        updates["lead_updated_at"] = utcnow()
        await db.ai_comments.update_one(lead_filter, {"$set": updates})
        await _audit(ctx, request,
                     "leads.assigned" if assignment_requested else "leads.updated", "leads",
                     resource_type="lead", resource_id=lead_id,
                     details={k: v for k, v in updates.items() if k != "lead_updated_at"})

    return {"ok": True, "lead_id": lead_id}


@router.post("/leads/{lead_id}/notes")
async def add_lead_note(lead_id: str, body: dict, request: Request,
                        ctx: TenantContext = Depends(require_org_permission(P.LEADS_MANAGE))):
    """Add a note to a lead."""
    db = _db_or_503()
    lead = await _get_lead_or_404(db, lead_id, ctx, request)
    text = (body.get("text") or body.get("note") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Note text is required")
    if len(text) > 2000:
        raise HTTPException(status_code=400, detail="Note text too long (max 2000 chars)")

    note = {
        "text": text,
        "author": body.get("author") or ctx.name or ctx.email,
        "author_user_id": ctx.user_id,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }

    await db.ai_comments.update_one(
        _scoped(ctx, "ai_comments", {"_id": lead["_id"]}),
        {
            "$push": {"notes": note},
            "$set": {"lead_updated_at": utcnow()},
        }
    )

    return {"ok": True, "note": note}


@router.delete("/leads/{lead_id}/notes/{note_index}")
async def delete_lead_note(lead_id: str, note_index: int, request: Request,
                           ctx: TenantContext = Depends(require_org_permission(P.LEADS_MANAGE))):
    """Remove a note from a lead by index."""
    db = _db_or_503()
    lead = await _get_lead_or_404(db, lead_id, ctx, request)

    notes = lead.get("notes", [])
    if note_index < 0 or note_index >= len(notes):
        raise HTTPException(status_code=400, detail="Invalid note index")

    lead_filter = _scoped(ctx, "ai_comments", {"_id": lead["_id"]})
    await db.ai_comments.update_one(
        lead_filter,
        {
            "$unset": {f"notes.{note_index}": ""},
            "$set": {"lead_updated_at": utcnow()},
        }
    )
    await db.ai_comments.update_one(lead_filter, {"$pull": {"notes": None}})

    return {"ok": True}


@router.post("/leads/{lead_id}/follow-ups")
async def add_lead_follow_up(lead_id: str, body: dict, request: Request,
                             ctx: TenantContext = Depends(require_org_permission(P.LEADS_MANAGE))):
    """Add a follow-up to a lead."""
    db = _db_or_503()
    lead = await _get_lead_or_404(db, lead_id, ctx, request)

    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Follow-up title is required")

    due_at = body.get("due_at")
    if due_at and isinstance(due_at, str):
        try:
            due_at = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid due_at format")

    follow_up = {
        "title": title,
        "due_at": due_at,
        "status": "pending",
        "notes": (body.get("notes") or "").strip(),
        "created_by": body.get("created_by") or ctx.email,
        "created_by_user_id": ctx.user_id,
        "created_at": utcnow(),
        "completed_at": None,
    }

    await db.ai_comments.update_one(
        _scoped(ctx, "ai_comments", {"_id": lead["_id"]}),
        {
            "$push": {"follow_ups": follow_up},
            "$set": {"lead_updated_at": utcnow()},
        }
    )

    return {"ok": True, "follow_up": follow_up}


@router.patch("/leads/{lead_id}/follow-ups/{fu_index}")
async def update_lead_follow_up(lead_id: str, fu_index: int, body: dict, request: Request,
                                ctx: TenantContext = Depends(require_org_permission(P.LEADS_MANAGE))):
    """Update a follow-up status (complete/cancel)."""
    db = _db_or_503()
    try:
        lead = await _get_lead_or_404(db, lead_id, ctx, request)
    except HTTPException as e:
        if e.status_code == 404:
            raise HTTPException(status_code=404, detail="Lead not found")
        raise

    follow_ups = lead.get("follow_ups", [])
    if fu_index < 0 or fu_index >= len(follow_ups):
        raise HTTPException(status_code=400, detail="Invalid follow-up index")

    new_status = body.get("status")
    if new_status not in ("completed", "cancelled", "pending"):
        raise HTTPException(status_code=400, detail="Invalid follow-up status")

    update_fields = {f"follow_ups.{fu_index}.status": new_status}
    if new_status in ("completed", "cancelled"):
        update_fields[f"follow_ups.{fu_index}.completed_at"] = utcnow()
    if new_status == "pending":
        update_fields[f"follow_ups.{fu_index}.completed_at"] = None

    await db.ai_comments.update_one(
        _scoped(ctx, "ai_comments", {"_id": lead["_id"]}),
        {"$set": {**update_fields, "lead_updated_at": utcnow()}}
    )

    return {"ok": True}


@router.get("/leads/stats/summary")
async def lead_stats_summary(
    request: Request,
    platform: Optional[str] = Query(None),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    ctx: TenantContext = Depends(require_org_permission(P.LEADS_VIEW)),
):
    """Get lead statistics summary with conversion rates and score metrics."""
    db = _db_or_503()

    match: dict = {"is_lead": True}
    if platform:
        match["platform"] = platform

    # Date filtering
    from app.api.routes.admin import _iso
    date_match = {}
    if from_date:
        s = _iso(from_date)
        if s:
            date_match["$gte"] = s
    if to_date:
        e = _iso(to_date)
        if e:
            date_match["$lte"] = e.replace(hour=23, minute=59, second=59, microsecond=999999)
    if date_match:
        match["lead_created_at"] = date_match
    match = _scoped(ctx, "ai_comments", match)

    # Single aggregation pipeline for all stats
    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": None,
            "total": {"$sum": 1},
            "avg_score": {"$avg": "$lead_score"},
            "max_score": {"$max": "$lead_score"},
            "min_score": {"$min": "$lead_score"},
            "avg_confidence": {"$avg": "$confidence"},
            "new": {"$sum": {"$cond": [{"$eq": ["$lead_status", "new"]}, 1, 0]}},
            "contacted": {"$sum": {"$cond": [{"$eq": ["$lead_status", "contacted"]}, 1, 0]}},
            "qualified": {"$sum": {"$cond": [{"$eq": ["$lead_status", "qualified"]}, 1, 0]}},
            "follow_up": {"$sum": {"$cond": [{"$eq": ["$lead_status", "follow_up"]}, 1, 0]}},
            "converted": {"$sum": {"$cond": [{"$eq": ["$lead_status", "converted"]}, 1, 0]}},
            "lost": {"$sum": {"$cond": [{"$eq": ["$lead_status", "lost"]}, 1, 0]}},
            "disqualified": {"$sum": {"$cond": [{"$eq": ["$lead_status", "disqualified"]}, 1, 0]}},
            "archived": {"$sum": {"$cond": [{"$eq": ["$lead_status", "archived"]}, 1, 0]}},
            "high_priority": {"$sum": {"$cond": [{"$eq": ["$lead_priority", "high"]}, 1, 0]}},
            "medium_priority": {"$sum": {"$cond": [{"$eq": ["$lead_priority", "medium"]}, 1, 0]}},
            "low_priority": {"$sum": {"$cond": [{"$eq": ["$lead_priority", "low"]}, 1, 0]}},
            "hot_quality": {"$sum": {"$cond": [{"$eq": ["$lead_quality", "hot"]}, 1, 0]}},
            "warm_quality": {"$sum": {"$cond": [{"$eq": ["$lead_quality", "warm"]}, 1, 0]}},
            "cold_quality": {"$sum": {"$cond": [{"$eq": ["$lead_quality", "cold"]}, 1, 0]}},
            "score_80_plus": {"$sum": {"$cond": [{"$gte": ["$lead_score", 80]}, 1, 0]}},
            "score_60_79": {"$sum": {"$cond": [{"$and": [{"$gte": ["$lead_score", 60]}, {"$lt": ["$lead_score", 80]}]}, 1, 0]}},
            "score_40_59": {"$sum": {"$cond": [{"$and": [{"$gte": ["$lead_score", 40]}, {"$lt": ["$lead_score", 60]}]}, 1, 0]}},
            "score_below_40": {"$sum": {"$cond": [{"$lt": ["$lead_score", 40]}, 1, 0]}},
        }},
    ]

    result = [doc async for doc in db.ai_comments.aggregate(pipeline)]
    if not result:
        return {
            "total": 0, "by_status": {}, "by_priority": {}, "by_platform": {},
            "by_quality": {}, "score_distribution": {},
            "conversion": {"rate": 0, "qualified_to_converted": 0,
                           "contacted_to_qualified": 0, "lead_to_converted": 0},
            "avg_score": 0, "max_score": 0, "min_score": 0, "avg_confidence": 0,
        }

    doc = result[0]
    total = doc["total"]

    # Conversion rates (safe division)
    def safe_rate(num, den):
        return round(num / den * 100, 1) if den else 0

    # By platform (separate pipeline, lighter)
    platform_counts = {}
    async for p in db.ai_comments.aggregate([
        {"$match": match},
        {"$group": {"_id": "$platform", "count": {"$sum": 1}}},
    ]):
        platform_counts[p["_id"] or "other"] = p["count"]

    return {
        "total": total,
        "by_status": {
            "new": doc["new"], "contacted": doc["contacted"],
            "qualified": doc["qualified"], "follow_up": doc["follow_up"],
            "converted": doc["converted"], "lost": doc["lost"],
            "disqualified": doc["disqualified"], "archived": doc["archived"],
        },
        "by_priority": {
            "high": doc["high_priority"], "medium": doc["medium_priority"],
            "low": doc["low_priority"],
        },
        "by_platform": platform_counts,
        "by_quality": {
            "hot": doc["hot_quality"], "warm": doc["warm_quality"],
            "cold": doc["cold_quality"],
        },
        "score_distribution": {
            "80-100": doc["score_80_plus"], "60-79": doc["score_60_79"],
            "40-59": doc["score_40_59"], "0-39": doc["score_below_40"],
        },
        "conversion": {
            "rate": safe_rate(doc["converted"], total),
            "qualified_to_converted": safe_rate(doc["converted"], doc["qualified"]),
            "contacted_to_qualified": safe_rate(doc["qualified"], doc["contacted"]),
            "lead_to_converted": safe_rate(doc["converted"], total),
        },
        "avg_score": round(doc["avg_score"] or 0, 1),
        "max_score": doc["max_score"] or 0,
        "min_score": doc["min_score"] or 0,
        "avg_confidence": round((doc["avg_confidence"] or 0) * 100, 1),
    }
