"""
LeadAI Public REST API v1 (Phase 6 Product Feature).

Endpoints authenticated via `X-API-Key: lai_live_...` or Bearer token:
- GET  /api/v1/leads
- POST /api/v1/leads
- POST /api/v1/search
- GET  /api/v1/search/{run_id}
- POST /api/v1/searches/keyword                       (search:create)
- GET  /api/v1/searches/keyword/{run_id}              (search:read or search:create)
- GET  /api/v1/searches/keyword/{run_id}/candidates   (search:read or search:create)
- POST /api/v1/searches/keyword/{run_id}/reapply      (search:create)
"""
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.auth.rate_limit import RateLimiter
from app.db.mongo import get_async_db, get_sync_db
from app.discovery.schemas import KeywordSearchRequest, ReapplyRequest
from app.pipeline.deduplication import deduplicate_lead
from app.services.api_keys import authenticate_api_key

router = APIRouter(prefix="/api/v1", tags=["public_api_v1"])


async def get_api_auth(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    raw_key = x_api_key
    if not raw_key and authorization and authorization.lower().startswith("bearer "):
        raw_key = authorization.split(" ", 1)[1].strip()

    if not raw_key:
        raise HTTPException(status_code=401, detail="Missing API key. Provide 'X-API-Key' header.")

    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    auth = authenticate_api_key(db, raw_key)
    # per-key limit stored on the key (durable, shared across workers)
    limiter = RateLimiter("api_key", int(auth.get("rate_limit_per_minute") or 60), 60)
    if not limiter.consume(str(auth.get("key_id"))):
        raise HTTPException(status_code=429, detail="API rate limit exceeded",
                            headers={"Retry-After": str(max(1, limiter.retry_after(str(auth.get("key_id")))))})
    return auth


class IngestLeadPayload(BaseModel):
    author_name: str
    phone: str | None = None
    email: str | None = None
    comment_text: str = ""
    platform: str = "social"
    intent: str = "purchase_inquiry"
    priority: str = "warm"
    lead_score: int = 50


class StartSearchPayload(BaseModel):
    url: str
    # empty = the platform default; more than the plan allows is refused (402)
    max_posts: int | None = Field(None, ge=1, le=100)
    max_comments_per_post: int | None = Field(None, ge=1, le=200)


@router.get("/leads")
async def list_leads_v1(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    platform: str | None = None,
    priority: str | None = None,
    auth: dict[str, Any] = Depends(get_api_auth),  # noqa: B008
):
    if "leads:read" not in auth.get("scopes", []) and "*" not in auth.get("scopes", []):
        raise HTTPException(status_code=403, detail="API key lacks 'leads:read' scope")

    db = get_async_db()
    org_id = auth["organization_id"]
    query: dict[str, Any] = {"organization_id": org_id}
    if platform:
        query["platform"] = platform
    if priority:
        query["priority"] = priority

    cursor = db.ai_comments.find(query).sort("created_at", -1).skip(offset).limit(limit)
    items = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        items.append(doc)

    total = await db.ai_comments.count_documents(query)
    return {"items": items, "total": total, "offset": offset, "limit": limit}


@router.post("/leads")
async def ingest_lead_v1(
    payload: IngestLeadPayload,
    auth: dict[str, Any] = Depends(get_api_auth),  # noqa: B008
):
    if "leads:write" not in auth.get("scopes", []) and "*" not in auth.get("scopes", []):
        raise HTTPException(status_code=403, detail="API key lacks 'leads:write' scope")

    org_id = auth["organization_id"]

    lead_doc = payload.model_dump()
    lead_id, is_dup, dup_count = deduplicate_lead(get_sync_db(), lead_doc, org_id)

    return {
        "lead_id": lead_id,
        "is_duplicate": is_dup,
        "duplicate_count": dup_count,
        "status": "ingested",
    }


def api_key_context(auth: dict[str, Any]):
    """The API key as the actor of a request: its organization, no person.
    Runs and charges are recorded against ``api_key:<key_id>``."""
    from app.auth.tenant import TenantContext
    actor = f"api_key:{auth.get('key_id')}"
    return TenantContext(user_id=actor, email=actor, name=auth.get("name") or "API key",
                         organization_id=str(auth["organization_id"]), org_role="api",
                         permissions=list(auth.get("scopes") or []))


def require_scope(auth: dict[str, Any], scope: str) -> None:
    scopes = auth.get("scopes", [])
    if scope not in scopes and "*" not in scopes:
        raise HTTPException(status_code=403, detail=f"API key lacks '{scope}' scope")


@router.post("/search")
async def create_search_v1(
    payload: StartSearchPayload,
    request: Request,
    auth: dict[str, Any] = Depends(get_api_auth),  # noqa: B008
):
    """Start a URL search. The same rules as the app apply: feature and
    platform switches, plan limits, monthly search quota and token charges."""
    require_scope(auth, "search:create")
    import uuid

    from app.api.routes.search import admit_url_search
    res = await admit_url_search(
        api_key_context(auth), request, url=payload.url, max_posts=payload.max_posts,
        max_comments_per_post=payload.max_comments_per_post,
        run_id=f"URL_API_{uuid.uuid4().hex[:12]}", source="api")
    return {"run_id": res["run_id"], "url": res["canonical_url"], "platform": res["platform"],
            "status": res["status"]}


@router.get("/search/{run_id}")
async def get_search_v1(
    run_id: str,
    auth: dict[str, Any] = Depends(get_api_auth),  # noqa: B008
):
    if not ({"search:read", "search:create", "*"} & set(auth.get("scopes", []))):
        raise HTTPException(status_code=403, detail="API key lacks 'search:read' scope")
    db = get_async_db()
    org_id = auth["organization_id"]
    run = await db.search_history.find_one({"run_id": run_id, "organization_id": org_id})
    if not run:
        job = await db.job_queue.find_one({"run_id": run_id, "organization_id": org_id})
        if not job:
            raise HTTPException(status_code=404, detail="Search run not found")
        return {
            "run_id": run_id,
            "status": job.get("status", "queued"),
            "created_at": job.get("created_at"),
        }

    run["id"] = str(run.pop("_id"))
    return run


# ── Keyword / discovery search ──────────────────────────────────────────────
# Same service and rules as the app (feature + platform switches, plan
# limits, monthly quota, tokens per stage). Like the app, a start needs
# ``confirm: true`` — without it the answer is 409 CONFIRM_REQUIRED carrying
# ``detail.estimate`` — and may set ``max_tokens`` as a hard budget.

def _require_read(auth: dict[str, Any]) -> None:
    if not ({"search:read", "search:create", "*"} & set(auth.get("scopes", []))):
        raise HTTPException(status_code=403, detail="API key lacks 'search:read' scope")


async def _keyword_run_for_key(run_id: str, auth: dict[str, Any]) -> dict[str, Any]:
    from app.auth.tenant import org_match
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    run = await db.search_history.find_one({"run_id": run_id, "search_type": "keyword",
                                            "organization_id": org_match(auth["organization_id"])})
    if not run:
        raise HTTPException(status_code=404, detail="Keyword search run not found")
    return run


@router.post("/searches/keyword", summary="Start a keyword search (API key, scope search:create)")
async def create_keyword_search_v1(
    payload: KeywordSearchRequest,
    request: Request,
    auth: dict[str, Any] = Depends(get_api_auth),  # noqa: B008
):
    """Body = the app's KeywordSearchRequest (keywords, platforms, filters,
    strict, limits, confirm, max_tokens, comment_filter, business_context).
    Returns {run_id, status, estimate, message}."""
    require_scope(auth, "search:create")
    from app.discovery import service as kw
    body = payload
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return await kw.admit_keyword_search(api_key_context(auth), request, body, db=db, source="api",
                                         run_id=kw.new_keyword_run_id("KW_API_"))


@router.get("/searches/keyword/{run_id}", summary="Keyword search status (API key)")
async def get_keyword_search_v1(run_id: str, auth: dict[str, Any] = Depends(get_api_auth)):  # noqa: B008
    _require_read(auth)
    from app.auth.tenant import org_match
    from app.discovery import service as kw
    run = await _keyword_run_for_key(run_id, auth)
    base = {"search_run_id": run["run_id"], "organization_id": org_match(auth["organization_id"])}
    return await kw.run_status_full(get_async_db(), run, base)


@router.get("/searches/keyword/{run_id}/candidates", summary="Keyword search candidates (API key)")
async def list_keyword_candidates_v1(
    run_id: str,
    status: str | None = Query(None, pattern="^(pending|passed|rejected|needs_sampling|error)$"),
    platform: str | None = Query(None, pattern="^(instagram|facebook|youtube|linkedin)$"),
    sort: str = Query("followers", pattern="^(followers|engagement|avg_comments|newest)$"),
    page: int = Query(1, ge=1, le=10000),
    page_size: int = Query(25, ge=1, le=100),
    auth: dict[str, Any] = Depends(get_api_auth),  # noqa: B008
):
    _require_read(auth)
    from app.api.routes.keyword_search import candidates_page
    from app.auth.tenant import org_match
    run = await _keyword_run_for_key(run_id, auth)
    base = {"search_run_id": run["run_id"], "organization_id": org_match(auth["organization_id"])}
    return await candidates_page(get_async_db(), run_id, base, status=status, platform=platform,
                                 sort=sort, page=page, page_size=page_size)


@router.post("/searches/keyword/{run_id}/reapply", summary="Re-apply filters (API key, no scraping)")
async def reapply_keyword_search_v1(
    run_id: str,
    payload: ReapplyRequest,
    request: Request,
    auth: dict[str, Any] = Depends(get_api_auth),  # noqa: B008
):
    """Body {filters, strict}. Recomputes statuses from stored metrics; returns
    counts and the token cost of continuing (continue from the app)."""
    require_scope(auth, "search:create")
    import asyncio

    from app.api.routes.search import _audit
    from app.discovery import service as kw
    body = payload
    run = await _keyword_run_for_key(run_id, auth)
    await asyncio.to_thread(kw.ensure_finished, get_sync_db(), run)
    ctx = api_key_context(auth)
    caps = await asyncio.to_thread(kw.effective_caps, ctx.organization_id)
    filters, notes = kw.validate_filters_or_422(body.filters.model_dump(exclude_none=True), caps,
                                                recent_override=kw.run_recent_posts(run))
    res = await asyncio.to_thread(kw.reapply_run, get_sync_db(), run, filters, body.strict, ctx.email)
    res["notes"] = notes + res["notes"]
    await _audit(ctx, request, "search.keyword_reapplied", "search", resource_type="search_run",
                 resource_id=run_id, details={"filters": filters, "strict": body.strict, "source": "api"})
    return res
