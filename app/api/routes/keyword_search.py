"""
Keyword / discovery search API (customer app, session auth).

  GET    /api/keyword-search/capabilities           what each platform can measure + defaults + caps
  POST   /api/keyword-search/estimate               worst-case tokens + rough Apify cost
  POST   /api/keyword-search                        start (needs confirm: true)
  GET    /api/keyword-search/presets                saved keyword presets
  POST   /api/keyword-search/presets                save one
  DELETE /api/keyword-search/presets/{preset_id}    delete one (creator or admin)
  GET    /api/keyword-search/{run_id}               status, stages, counts, tokens
  GET    /api/keyword-search/{run_id}/candidates    paginated candidates with reasons + metrics
  POST   /api/keyword-search/{run_id}/reapply       recompute statuses with new filters (no scraping)
  POST   /api/keyword-search/{run_id}/continue      sample / deep-scrape what is left (charges tokens)
  POST   /api/keyword-search/{run_id}/cancel        cancel a running search

Tenant isolation: every read is scoped like the URL-search routes (members see
their own runs, org owners/admins the organization's); another tenant's run
answers 404 and is logged as a security event.
"""
import asyncio
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.routes.search import _audit, _db_or_503, _find_or_404, _oid, _scoped, _serialize
from app.auth import permissions as P
from app.auth.rate_limit import RateLimiter
from app.auth.tenant import (
    TenantContext,
    org_match,
    report_out_of_scope,
    require_org_permission,
    stamp,
)
from app.db.models import utcnow
from app.discovery import service as S
from app.discovery.pipeline import CANDIDATES
from app.discovery.schemas import (
    CandidatesPage,
    ContinueRequest,
    ContinueResponse,
    Estimate,
    KeywordPresetCreate,
    KeywordSearchRequest,
    ReapplyRequest,
    ReapplyResponse,
    RunStatus,
    StartResponse,
)

router = APIRouter(prefix="/api/keyword-search", tags=["keyword_search"])

START_LIMIT = (10, 60)        # starts / continues per organization per minute
ESTIMATE_LIMIT = (60, 60)     # estimates / re-applies per organization per minute

_ERRORS: dict[int | str, dict[str, Any]] = {
    402: {"description": "Plan limit (PLAN_LIMIT), quota (QUOTA_EXCEEDED), tokens (TOKENS_EXHAUSTED / "
                         "TOKENS_EXPIRED) or trial expired"},
    403: {"description": "feature_disabled / platform_disabled / FEATURE_NOT_AVAILABLE / no permission"},
    409: {"description": "CONFIRM_REQUIRED (detail.estimate holds the cost) or run state conflict"},
    422: {"description": "invalid_request / invalid_filters (detail.errors maps field -> message)"},
    429: {"description": "Rate limited (Retry-After header) or too many concurrent searches"},
}


def _rate(bucket: str, limit: tuple, ctx: TenantContext) -> None:
    limiter = RateLimiter(bucket, limit[0], limit[1])
    key = str(ctx.organization_id or ctx.user_id)
    if not limiter.consume(key):
        raise HTTPException(status_code=429, detail={
            "success": False, "errorType": "rate_limit", "message": "Too many requests. Please wait a moment."},
            headers={"Retry-After": str(max(1, limiter.retry_after(key)))})


async def _keyword_run_or_404(db, run_id: str, ctx: TenantContext, request: Request) -> dict:
    run = await _find_or_404(db, "search_history", {"run_id": run_id}, ctx, request,
                             detail="Keyword search run not found")
    if run.get("search_type") != "keyword":
        raise HTTPException(status_code=404, detail="Keyword search run not found")
    return run


@router.get("/capabilities", summary="Platform capability matrix, suggested filters and your caps")
async def get_capabilities(ctx: TenantContext = Depends(require_org_permission(P.SEARCH_VIEW))):
    """For every platform and filter: ``supported`` | ``partial`` | ``unsupported``
    and the stage (discovery / enrichment / sampling) whose actor provides it;
    the Global-Settings switches; suggested filter values; this organization's
    effective caps (min of plan and Global Settings) and per-unit token costs."""
    return await asyncio.to_thread(S.capabilities_for, ctx.organization_id)


@router.post("/estimate", response_model=Estimate, responses=_ERRORS,
             summary="Worst-case cost of a keyword search (nothing is charged)")
async def estimate_keyword_search(body: KeywordSearchRequest, request: Request,
                                  ctx: TenantContext = Depends(require_org_permission(P.SEARCH_CREATE))):
    """Same validation as start. ``tokens`` = worst case where every candidate
    passes (discover per keyword x platform, enrich / sample per profile,
    collect per deep-scraped profile). ``confirm`` is ignored here."""
    _rate("keyword_estimate", ESTIMATE_LIMIT, ctx)
    return await S.estimate_request(ctx, body, db=_db_or_503())


@router.post("", response_model=StartResponse, responses=_ERRORS, summary="Start a keyword search")
async def start_keyword_search(body: KeywordSearchRequest, request: Request,
                               ctx: TenantContext = Depends(require_org_permission(P.SEARCH_CREATE))):
    """Discover profiles for each keyword on each platform, filter them, then
    collect comments of the ones that pass. Without ``confirm: true`` answers
    409 ``CONFIRM_REQUIRED`` with ``detail.estimate``. Poll
    ``GET /api/keyword-search/{run_id}``."""
    _rate("keyword_start", START_LIMIT, ctx)
    return await S.admit_keyword_search(ctx, request, body, db=_db_or_503(), source="app")


# ── presets (declared before /{run_id}) ─────────────────────────────────────

@router.get("/presets", summary="Saved keyword-search presets")
async def list_keyword_presets(ctx: TenantContext = Depends(require_org_permission(P.SEARCH_CREATE))):
    db = _db_or_503()
    query: dict[str, Any] = {"organization_id": org_match(ctx.organization_id), "kind": "keyword"}
    if not ctx.can_view_all_org_data:
        query["$or"] = [{"user_id": ctx.user_id}, {"is_shared": True}]
    presets = [_serialize(d) async for d in db.search_presets.find(query).sort("created_at", -1).limit(200)]
    return {"success": True, "presets": presets}


@router.post("/presets", summary="Save a keyword-search preset")
async def create_keyword_preset(body: KeywordPresetCreate, request: Request,
                                ctx: TenantContext = Depends(require_org_permission(P.SEARCH_CREATE))):
    """Keywords, platforms, filters (validated like a search), strict and limits."""
    db = _db_or_503()
    caps = await asyncio.to_thread(S.effective_caps, ctx.organization_id)
    filters, _ = S.validate_filters_or_422(body.filters.model_dump(exclude_none=True), caps)
    keywords = [" ".join(k.split())[:100] for k in body.keywords if k and k.strip()][:100]
    doc = stamp(ctx, {
        "kind": "keyword", "name": body.name.strip(), "keywords": keywords,
        "platforms": list(dict.fromkeys(body.platforms)), "filters": filters, "strict": body.strict,
        "limits": body.limits.model_dump(exclude_none=True), "is_shared": bool(body.is_shared),
        "created_at": utcnow(),
    })
    res = await db.search_presets.insert_one(doc)
    doc["_id"] = res.inserted_id
    await _audit(ctx, request, "search.keyword_preset_created", "search", resource_type="search_preset",
                 resource_id=str(res.inserted_id), details={"name": doc["name"]})
    return {"success": True, "preset": _serialize(doc)}


@router.delete("/presets/{preset_id}", summary="Delete a keyword-search preset")
async def delete_keyword_preset(preset_id: str, request: Request,
                                ctx: TenantContext = Depends(require_org_permission(P.SEARCH_CREATE))):
    db = _db_or_503()
    oid = _oid(preset_id)
    preset = await _find_or_404(db, "search_presets", {"_id": oid}, ctx, request, detail="Preset not found")
    if preset.get("kind") != "keyword":
        raise HTTPException(status_code=404, detail="Preset not found")
    if preset.get("user_id") != ctx.user_id and not ctx.has(P.SETTINGS_MANAGE):
        report_out_of_scope(request, ctx, "search_presets", preset)
        raise HTTPException(status_code=403, detail="Cannot delete presets created by other members")
    await db.search_presets.delete_one({"_id": oid})
    await _audit(ctx, request, "search.keyword_preset_deleted", "search", resource_type="search_preset",
                 resource_id=preset_id, details={"name": preset.get("name")})
    return {"success": True, "deleted_id": preset_id}


# ── one run ─────────────────────────────────────────────────────────────────

@router.get("/{run_id}", response_model=RunStatus, summary="Keyword search status")
async def get_keyword_search(run_id: str, request: Request,
                             ctx: TenantContext = Depends(require_org_permission(P.SEARCH_VIEW))):
    """``status`` running | completed | error | cancelled; ``phase`` queued |
    discovery | enrichment | sampling | deep_scrape | completed | failed |
    cancelled | stopped_tokens | stopped_budget. ``stages`` per stage
    pending | running | done | failed | stopped; ``stage_counts``;
    ``tokens_charged`` per stage; ``platform_errors``; ``discovery_queries``
    (what each keyword search sent to Apify: search text, locations and other
    actor inputs the filters were turned into); ``passed_count`` and
    ``rejection_summary`` (the most common reasons profiles were rejected:
    {filter, label, count, outcome, message})."""
    db = _db_or_503()
    run = await _keyword_run_or_404(db, run_id, ctx, request)
    return await S.run_status_full(db, run, _scoped(ctx, CANDIDATES, {"search_run_id": run["run_id"]}))


@router.get("/{run_id}/candidates", response_model=CandidatesPage, summary="Candidates of a keyword search")
async def list_keyword_candidates(
    run_id: str, request: Request,
    status: Optional[Literal["pending", "passed", "rejected", "needs_sampling", "error"]] = Query(None),
    platform: Optional[Literal["instagram", "facebook", "youtube", "linkedin"]] = Query(None),
    sort: Literal["followers", "engagement", "avg_comments", "newest"] = Query("followers"),
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(25, ge=1, le=100),
    ctx: TenantContext = Depends(require_org_permission(P.SEARCH_VIEW)),
):
    """Each candidate carries ``reasons`` (one outcome per filter: pass / fail /
    unsupported / unknown with message, value and threshold), ``metrics``,
    ``unsupported`` / ``unknown`` filter lists, the normalized ``profile``,
    the analyzed ``recent_posts`` and ``page_ref`` (the stored page, once
    deep-scraped; browse it with /api/pages/{id})."""
    db = _db_or_503()
    run = await _keyword_run_or_404(db, run_id, ctx, request)
    return await candidates_page(db, run_id, _scoped(ctx, CANDIDATES, {"search_run_id": run["run_id"]}),
                                 status=status, platform=platform, sort=sort, page=page, page_size=page_size)


_SORTS = {"followers": [("metrics.followers", -1), ("_id", 1)],
          "engagement": [("metrics.engagement_rate", -1), ("_id", 1)],
          "avg_comments": [("metrics.avg_comments", -1), ("_id", 1)],
          "newest": [("_id", -1)]}


async def candidates_page(db, run_id: str, base: dict, *, status: Optional[str], platform: Optional[str],
                          sort: str, page: int, page_size: int) -> dict:
    query = dict(base)
    extra: dict[str, Any] = {}
    if status:
        extra["status"] = status
    if platform:
        extra["platform"] = platform
    if extra:
        query = {"$and": [base, extra]}
    total = await db[CANDIDATES].count_documents(query)
    cursor = (db[CANDIDATES].find(query, {"posts_raw": 0}).sort(_SORTS.get(sort, _SORTS["followers"]))
              .skip((page - 1) * page_size).limit(page_size))
    items = [S.candidate_out(d) async for d in cursor]
    counts = {}
    for s in ("pending", "passed", "rejected", "needs_sampling", "error"):
        counts[s] = await db[CANDIDATES].count_documents({"$and": [base, {"status": s}]})
    return {"run_id": run_id, "items": items, "total": total, "page": page, "page_size": page_size,
            "counts": counts}


@router.post("/{run_id}/reapply", response_model=ReapplyResponse, responses=_ERRORS,
             summary="Re-apply filters to stored results (no scraping, no tokens)")
async def reapply_keyword_filters(run_id: str, body: ReapplyRequest, request: Request,
                                  ctx: TenantContext = Depends(require_org_permission(P.SEARCH_CREATE))):
    """Recomputes every candidate's status from stored metrics. A candidate
    rejected earlier that now passes the profile filters but has no analyzed
    posts becomes ``needs_sampling``; ``continue_cost`` is what
    ``/continue`` would charge (worst case)."""
    db = _db_or_503()
    run = await _keyword_run_or_404(db, run_id, ctx, request)
    _rate("keyword_estimate", ESTIMATE_LIMIT, ctx)
    await asyncio.to_thread(S.ensure_finished, _sync_db(), run)
    caps = await asyncio.to_thread(S.effective_caps, ctx.organization_id)
    filters, notes = S.validate_filters_or_422(body.filters.model_dump(exclude_none=True), caps,
                                               recent_override=S.run_recent_posts(run))
    res = await asyncio.to_thread(S.reapply_run, _sync_db(), run, filters, body.strict, ctx.email)
    res["notes"] = notes + res["notes"]
    await _audit(ctx, request, "search.keyword_reapplied", "search", resource_type="search_run",
                 resource_id=run_id, details={"filters": filters, "strict": body.strict, "counts": res["counts"]})
    return res


def _sync_db():
    from app.db.mongo import get_sync_db
    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return db


@router.post("/{run_id}/continue", response_model=ContinueResponse, responses=_ERRORS,
             summary="Continue a finished search (sample / deep-scrape the rest)")
async def continue_keyword_search(run_id: str, body: ContinueRequest, request: Request,
                                  ctx: TenantContext = Depends(require_org_permission(P.SEARCH_CREATE))):
    """Processes ``needs_sampling`` candidates, passed candidates not yet
    deep-scraped and anything a stop (tokens, budget, cancel) left behind,
    charging tokens per stage. Without ``confirm: true`` answers 409
    ``CONFIRM_REQUIRED`` with ``detail.estimate``."""
    db = _db_or_503()
    run = await _keyword_run_or_404(db, run_id, ctx, request)
    _rate("keyword_start", START_LIMIT, ctx)
    return await S.continue_run(ctx, request, run, body, db=db)


@router.post("/{run_id}/cancel", summary="Cancel a running keyword search")
async def cancel_keyword_search(run_id: str, request: Request,
                                ctx: TenantContext = Depends(require_org_permission(P.SEARCH_CANCEL))):
    """The run stops at its next checkpoint; results found so far are kept and
    it can be continued later."""
    db = _db_or_503()
    await _keyword_run_or_404(db, run_id, ctx, request)
    from app.api.routes.search import cancel_search_run
    return await cancel_search_run(run_id, request, ctx)
