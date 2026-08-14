"""
LeadAI Agent API â€” the whole product surface.

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

Real-time: every collect runs in a background task; the GET endpoints
expose live status fields (running / completed / error) to poll.
"""
import asyncio
import csv
import io
import logging
import re
from datetime import datetime
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app.db.mongo import get_async_db
from app.db.models import utcnow
from app.agent.search import _parse_iso, current_min_comments

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["agent"])

_tasks: dict = {}


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
        return doc.isoformat()
    return doc


def _oid(value: str):
    try:
        return ObjectId(value)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid id: {value}")


def _resolve_platform(doc: dict, parent: Optional[dict] = None) -> str:
    """Platform for any doc â€” never guessed, never defaulted to Facebook.

    Order: explicit `platform` field â†’ the doc's own URL â†’ parent doc's
    platform/URL â†’ "unknown". A mismatch between an explicit platform and
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
                    "[platform] mismatch: doc says %r but %s=%s â€” correcting to %r",
                    p, url, derived, derived)
            return derived
    if p:
        return p
    if parent:
        return _resolve_platform(parent)
    return "unknown"


async def _doc_or_404(db, collection: str, oid: ObjectId):
    doc = await db[collection].find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail=f"{collection} document not found")
    return doc


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


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# SEARCH RUNS â€” status, history, cancellation (URL search writes these docs)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.post("/search/{run_id}/cancel")
async def cancel_search_run(run_id: str):
    """Request cancellation of a running URL-based search.
    The background worker picks up `cancel_requested` at its next checkpoint
    and aborts the in-flight Apify run. Poll GET /api/search/{run_id} to see
    the final status (`cancelled`)."""
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    run = await db.search_history.find_one({"run_id": run_id})
    if not run:
        raise HTTPException(status_code=404, detail="Search run not found")
    if run.get("status") != "running":
        return {"run_id": run_id, "status": run.get("status"),
                "message": "This search is no longer running"}
    await db.search_history.update_one({"run_id": run_id}, {"$set": {
        "cancel_requested": True, "message": "Cancelling searchâ€¦",
        "updated_at": utcnow()}})
    return {"run_id": run_id, "status": "cancelling",
            "message": "Cancellation requested â€” stopping soon"}


@router.get("/search/history")
async def search_history(limit: int = Query(20, ge=1, le=100)):
    db = get_async_db()
    if db is None:
        return {"searches": [], "count": 0}
    cursor = db.search_history.find({}).sort("created_at", -1).limit(limit)
    docs = [_serialize(doc) async for doc in cursor]
    return {"searches": docs, "count": len(docs)}


@router.delete("/search/{run_id}")
async def delete_search_run(run_id: str):
    """Delete a search run and everything it produced: the run doc, its
    pages, their posts, and all raw + AI-analyzed comments (leads)."""
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    run = await db.search_history.find_one({"run_id": run_id})
    if not run:
        raise HTTPException(status_code=404, detail="Search run not found")
    if run.get("status") == "running":
        # best-effort: the background worker aborts at its next checkpoint
        await db.search_history.update_one({"run_id": run_id}, {"$set": {
            "cancel_requested": True}})

    page_ids = [str(p["_id"]) async for p in
                db.facebook_pages.find({"search_run_id": run_id}, {"_id": 1})]
    post_ids = []
    if page_ids:
        post_ids = [str(p["_id"]) async for p in
                    db.facebook_posts.find({"page_ref": {"$in": page_ids}}, {"_id": 1})]

    counts = {"pages": len(page_ids), "posts": len(post_ids),
              "comments": 0, "leads": 0}
    if post_ids:
        counts["comments"] = (await db.facebook_comments.delete_many(
            {"post_ref": {"$in": post_ids}})).deleted_count
        counts["leads"] = (await db.ai_comments.delete_many(
            {"$or": [{"post_ref": {"$in": post_ids}},
                     {"page_ref": {"$in": page_ids}}]})).deleted_count
        await db.facebook_posts.delete_many({"page_ref": {"$in": page_ids}})
    if page_ids:
        await db.facebook_pages.delete_many({"search_run_id": run_id})
    await db.search_history.delete_one({"run_id": run_id})

    return {"run_id": run_id, "deleted": counts,
            "message": "Search deleted"}


@router.get("/search/{run_id}")
async def get_search_run(run_id: str):
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    doc = await db.search_history.find_one({"run_id": run_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Search run not found")
    pages = []
    async for p in db.facebook_pages.find({"search_run_id": run_id}).sort("followers", -1).limit(200):
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


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# URL-BASED SEARCH â€” paste a Facebook/Instagram/YouTube/LinkedIn URL
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.post("/url/search")
async def start_url_search(
    url: str = Query(..., min_length=4, max_length=300,
                     description="Facebook page, Instagram profile, YouTube channel or LinkedIn company URL"),
    max_posts: Optional[int] = Query(None, ge=1,
                                     description="Posts to scrape (default/cap from admin limits)"),
    max_comments_per_post: Optional[int] = Query(None, ge=1,
                                                 description="Comments to scrape per post (admin-capped)"),
):
    """Start a URL-based social lead search. Poll GET /api/search/{run_id}."""
    from app.social.url_detector import detect_social_url, UrlError
    from app.social.url_search import UrlSearchThread
    from app.admin.settings import effective_limits, is_platform_enabled, get_bool

    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

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

    # admin-controlled defaults + hard caps â€” enforcement lives server-side
    lim = effective_limits()
    max_posts = min(max_posts or lim["max_posts_default"], lim["max_posts_cap"])
    max_comments_per_post = min(
        max_comments_per_post or lim["max_comments_per_post_default"],
        lim["max_comments_per_post_cap"], lim["global_max_comments"])

    run_id = f"URL{datetime.now().strftime('%Y%m%d%H%M%S')}{abs(hash(url)) % 1000:03d}"
    await db.search_history.insert_one({
        "run_id": run_id, "query": url, "intent": {
            "keyword": url, "type": "url", "platform": platform,
            "canonical_url": canonical, "limit": max_posts,
            "max_comments_per_post": max_comments_per_post},
        "limit": max_posts, "provider": "apify", "url_search": True,
        "status": "running", "phase": "queued", "message": "Starting URL search...",
        "pages_found": 0, "pages_stored": 0,
        "created_at": utcnow(), "completed_at": None,
    })
    _start(f"url_search:{run_id}",
           UrlSearchThread(run_id, canonical, max_posts,
                           max_comments_per_post).run)
    return {
        "run_id": run_id, "status": "running", "platform": platform,
        "canonical_url": canonical,
        "message": f"{platform} search started â€” the page appears in the results below "
                   "when it is fetched",
    }


@router.get("/url/search/{run_id}/report")
async def url_search_report(run_id: str):
    """Full report bundle for a URL search run: page + posts + comments."""
    from app.agent.search import _activity_status

    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    run = await db.search_history.find_one({"run_id": run_id})
    if not run:
        raise HTTPException(status_code=404, detail="Search run not found")

    pages = []
    async for p in db.facebook_pages.find({"search_run_id": run_id}):
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
    async for p in db.facebook_posts.find({"page_ref": page_id}).sort("published_date", -1).limit(30):
        doc = _serialize(p)
        doc["total_comment_count"] = doc.get("total_comment_count") or doc.get("comments_count")
        doc.setdefault("scraped_comment_count", None)
        doc["platform"] = _resolve_platform(doc, page)
        posts.append(doc)

    post_ids = [p["id"] for p in posts]
    comments = []
    if post_ids:
        async for c in db.facebook_comments.find({"post_ref": {"$in": post_ids}}).sort("published_date", -1).limit(100):
            comments.append(_serialize(c))
    for c in comments:
        c["platform"] = _resolve_platform(c)
        c["commenter_name"] = c.get("author_name")
        c["comment_text"] = c.get("text")
        # enrich raw comments with AI analysis (phone, email, intent, lead scoreâ€¦)
        analysis = await db.ai_comments.find_one({"comment_ref": c["id"]})
        if analysis:
            a = _serialize(analysis)
            for key in ("is_lead", "lead_score", "priority", "lead_quality",
                        "confidence", "intent", "urgency", "budget", "requirement",
                        "location", "phone", "email", "whatsapp", "website",
                        "reason", "analyzed_by"):
                if a.get(key) is not None:
                    c[key] = a[key]

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
        "generated_at": utcnow().isoformat(),
    }

@router.get("/pages")
async def list_pages(
    run_id: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="filter by page name"),
    category: Optional[str] = Query(None),
    city: Optional[str] = Query(None),
    contact: bool = Query(False, description="only pages with phone or email"),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
):
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    # Every scraped page of the run is a result and must stay visible â€” the
    # qualifying/lead info is displayed per-row (sorted so qualifying pages
    # rank first) instead of hiding pages that did not qualify.
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
        query["$and"] = [{"$or": [{"email": {"$ne": None}}, {"phone": {"$ne": None}}]}]
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
async def get_page(page_id: str):
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return _serialize(await _doc_or_404(db, "facebook_pages", _oid(page_id)))


@router.post("/pages/{page_id}/posts")
async def collect_posts(page_id: str, max_posts: Optional[int] = Query(None, ge=1)):
    """Collect posts of the selected page via the platform's Apify actor."""
    from app.agent.search import collect_page_posts
    from app.admin.settings import effective_limits

    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    page = await db.facebook_pages.find_one({"_id": _oid(page_id)})
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    lim = effective_limits()
    max_posts = min(max_posts or lim["max_posts_default"], lim["max_posts_cap"])
    started = _start(f"posts:{page_id}", collect_page_posts, page_id, max_posts)
    if not started:
        return {"status": "running", "message": "Posts collection already in progress"}
    return {"status": "running", "message": "Posts collection started"}


@router.get("/pages/{page_id}/posts")
async def list_page_posts(page_id: str, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    oid = _oid(page_id)
    page = await db.facebook_pages.find_one({"_id": oid})
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    docs = []
    async for p in db.facebook_posts.find({"page_ref": page_id}):
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


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# POSTS
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get("/posts/{post_id}")
async def get_post(post_id: str):
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return _serialize(await _doc_or_404(db, "facebook_posts", _oid(post_id)))


@router.post("/posts/{post_id}/comments")
async def collect_comments(post_id: str, max_comments: Optional[int] = Query(None, ge=1)):
    """Collect comments of the selected post + run AI analysis (ai_comments)."""
    from app.agent.search import collect_post_comments
    from app.admin.settings import effective_limits

    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    post = await db.facebook_posts.find_one({"_id": _oid(post_id)})
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    lim = effective_limits()
    max_comments = min(max_comments or lim["max_comments_per_post_default"],
                       lim["max_comments_per_post_cap"])
    started = _start(f"comments:{post_id}", collect_post_comments, post_id, max_comments)
    if not started:
        return {"status": "running", "message": "Comments collection already in progress"}
    return {"status": "running", "message": "Comments collection + AI analysis started"}


@router.get("/posts/{post_id}/comments")
async def list_post_comments(
    post_id: str,
    only_leads: bool = Query(False, description="show only valuable comments (is_lead)"),
    contact_only: bool = Query(True, description="only comments with a 10-digit phone number or email"),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    oid = _oid(post_id)
    post = await db.facebook_posts.find_one({"_id": oid})
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    query = {"post_ref": post_id}
    docs = []
    if only_leads:
        # AI-analyzed comments that scored as valuable leads
        query["is_lead"] = True
        async for c in (db.ai_comments.find(query).sort("lead_score", -1)
                        .skip(offset).limit(limit)):
            docs.append(_serialize(c))

        # attach raw comment details (profile url, date, reactions)
        refs = [c["comment_ref"] for c in docs if c.get("comment_ref")]
        raw_by_id = {}
        if refs:
            async for raw in db.facebook_comments.find({"_id": {"$in": [ObjectId(r) for r in refs]}}):
                raw_by_id[str(raw["_id"])] = _serialize(raw)
        for c in docs:
            raw = raw_by_id.get(c.get("comment_ref"), {})
            c["author_profile_url"] = raw.get("author_profile_url")
            c["published_date"] = c.get("published_date") or raw.get("published_date")
            c["comment_url"] = raw.get("comment_url")
            c["reactions_count"] = raw.get("reactions_count")
            c["platform"] = _resolve_platform(c)
        total = await db.ai_comments.count_documents(query)
    else:
        # ALL raw comments for the post, contact-bearing ones first; by
        # default only comments with a 10-digit phone number or email are
        # shown (contact_only) â€” unchecking reveals every comment
        from app.pipeline.comment_ai import extract_contact_quick
        all_docs = []
        async for raw in db.facebook_comments.find(query):
            c = _serialize(raw)
            c["platform"] = _resolve_platform(c)
            c["commenter_name"] = raw.get("author_name")
            c["comment_text"] = raw.get("text")
            quick = extract_contact_quick(raw.get("text"))
            has_contact = raw.get("has_contact")
            if has_contact is None:
                has_contact = bool(quick["phone"] or quick["email"])
            c["has_contact"] = bool(has_contact)
            for k, v in quick.items():
                if v:
                    c[k] = v
            analysis = await db.ai_comments.find_one({"comment_ref": c["id"]})
            if analysis:
                a = _serialize(analysis)
                for key in ("is_lead", "lead_score", "priority", "lead_quality",
                            "confidence", "intent", "urgency", "budget", "requirement",
                            "location", "phone", "email", "whatsapp", "website",
                            "reason", "analyzed_by"):
                    if a.get(key) is not None:
                        c[key] = a[key]
            all_docs.append(c)
        contact_count = sum(1 for d in all_docs if d.get("has_contact"))
        all_docs_len = len(all_docs)
        if contact_only:
            all_docs = [d for d in all_docs if d.get("has_contact")]
        # newest first, then stable-sorted so contact comments stay on top
        all_docs.sort(key=lambda d: d.get("published_date") or "", reverse=True)
        all_docs.sort(key=lambda d: d.get("has_contact") is not True)
        total = len(all_docs)
        docs = all_docs[offset:offset + limit]
    return {
        "post": _serialize(post),
        "platform": _resolve_platform(post),
        "comments": docs,
        "total": total,
        "all_count": all_docs_len if not only_leads else total,
        "contact_count": contact_count if not only_leads else
            await db.ai_comments.count_documents(
                {"post_ref": post_id, "is_lead": True}),
        "comments_status": post.get("comments_status"),
        "total_comment_count": post.get("total_comment_count") or post.get("comments_count") or 0,
        "scraped_comment_count": post.get("scraped_comment_count") or 0,
        "comments_count": post.get("scraped_comment_count") or 0,
        "minComments": current_min_comments(),
        "comments_error": post.get("comments_error"),
        "comments_error_meta": post.get("comments_error_meta"),
    }


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# LEAD DETAIL
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get("/comments/{comment_id}")
async def get_lead_detail(comment_id: str):
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    oid = _oid(comment_id)
    analysis = await db.ai_comments.find_one({"_id": oid})
    if analysis:
        result = _serialize(analysis)
        raw = None
        if analysis.get("comment_ref"):
            raw = await db.facebook_comments.find_one({"_id": ObjectId(analysis["comment_ref"])})
        post = await db.facebook_posts.find_one({"_id": ObjectId(analysis.get("post_ref"))}) if analysis.get("post_ref") else None
        page = await db.facebook_pages.find_one({"_id": ObjectId(analysis.get("page_ref"))}) if analysis.get("page_ref") else None
        result["platform"] = _resolve_platform(result, post)
        result["comment"] = _serialize(raw) if raw else None
        result["post"] = _serialize(post) if post else None
        result["page"] = _serialize(page) if page else None
        return result

    # fall back to the raw comment itself (e.g. a non-lead comment from the
    # "all comments" view that was never AI-analyzed)
    raw = await db.facebook_comments.find_one({"_id": oid})
    if not raw:
        raise HTTPException(status_code=404, detail="Comment not found")
    post = await db.facebook_posts.find_one({"_id": ObjectId(raw.get("post_ref"))}) if raw.get("post_ref") else None
    page = await db.facebook_pages.find_one({"_id": ObjectId(post.get("page_ref"))}) if post and post.get("page_ref") else None
    result = _serialize(raw)
    result["platform"] = _resolve_platform(result, post)
    result["commenter_name"] = raw.get("author_name")
    result["comment_text"] = raw.get("text")
    result["comment"] = result
    result["post"] = _serialize(post) if post else None
    result["page"] = _serialize(page) if page else None
    result["lead_quality"] = "none"
    result["priority"] = None
    result["is_lead"] = False
    return result


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# EXPORT â€” CSV
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
                "lead_quality", "confidence", "lead_score"]


def _split_date_time(value) -> tuple:
    """Tolerant actor date string â†’ (comment_date, comment_time) in LOCAL time.

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


def _csv_response(rows: list, columns: list, filename: str) -> Response:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([row.get(c, "") if row.get(c) is not None else "" for c in columns])
    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export/{scope}.csv")
async def export_csv(
    scope: str,
    run_id: Optional[str] = Query(None),
    page_id: Optional[str] = Query(None),
    post_id: Optional[str] = Query(None),
    only_leads: bool = Query(True),
):
    from app.admin.settings import get_bool
    if not get_bool("features.exports.enabled"):
        raise HTTPException(
            status_code=403,
            detail="CSV exports are currently disabled by the administrator.")
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    if scope == "pages":
        query = {"search_run_id": run_id} if run_id else {}
        rows = [doc async for doc in db.facebook_pages.find(query).sort("followers", -1)]
        for row in rows:
            row["platform"] = _resolve_platform(row)
        return _csv_response(rows, PAGES_CSV, f"pages_{datetime.now().strftime('%Y%m%d')}.csv")

    if scope == "posts":
        if not page_id:
            raise HTTPException(status_code=400, detail="page_id is required for posts export")
        rows = [doc async for doc in db.facebook_posts.find({"page_ref": page_id}).sort("published_date", -1)]
        for row in rows:
            if row.get("total_comment_count") is None:
                row["total_comment_count"] = row.get("comments_count")
            row["platform"] = _resolve_platform(row)
        return _csv_response(rows, POSTS_CSV, f"posts_{datetime.now().strftime('%Y%m%d')}.csv")

    if scope == "comments":
        if not post_id:
            raise HTTPException(status_code=400, detail="post_id is required for comments export")
        query = {"post_ref": post_id}
        if only_leads:
            query["is_lead"] = True
        rows = [doc async for doc in db.ai_comments.find(query).sort("lead_score", -1)]
        # ai_comments does not store the scrape date or the commenter's
        # profile URL â€” back-fill both from the raw comment doc so the CSV
        # always carries date/time and a clickable profile link
        if rows:
            refs = [r["comment_ref"] for r in rows if r.get("comment_ref")]
            raw_fields = {}
            if refs:
                async for raw in db.facebook_comments.find(
                        {"_id": {"$in": [ObjectId(r) for r in refs]}},
                        {"published_date": 1, "author_profile_url": 1}):
                    raw_fields[str(raw["_id"])] = raw
            for r in rows:
                raw = raw_fields.get(r.get("comment_ref"), {})
                published = r.get("published_date") or raw.get("published_date")
                r["comment_date"], r["comment_time"] = _split_date_time(published)
                url = raw.get("author_profile_url") or ""
                r["commenter_url"] = (
                    f'=HYPERLINK("{url}","Open profile")' if url.strip() else "")
        return _csv_response(rows, COMMENTS_CSV, f"leads_{datetime.now().strftime('%Y%m%d')}.csv")

    raise HTTPException(status_code=404, detail="scope must be pages, posts or comments")
