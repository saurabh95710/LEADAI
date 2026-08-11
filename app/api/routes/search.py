"""
LeadAI Agent API — the whole product surface.

  POST /api/search?query=&limit=          start an agent search (Apify)
  GET  /api/search/history                recent searches
  GET  /api/search/{run_id}               search run status + pages
  GET  /api/pages                         list pages (filter/paginate)
  GET  /api/pages/{id}                    one page
  POST /api/pages/{id}/posts              collect posts of THAT page (Apify)
  GET  /api/pages/{id}/posts              cached posts + collection status
  GET  /api/posts/{id}                    one post
  POST /api/posts/{id}/comments           collect comments of THAT post + AI analysis
  GET  /api/posts/{id}/comments           analyzed comments (leads only by default)
  GET  /api/comments/{id}                 lead detail (comment + AI + context)
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
from app.agent.search import _MIN_COMMENTS

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


# ─────────────────────────────────────────────────────────────────────────────
# SEARCH
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/search")
async def start_search(
    query: str = Query(..., min_length=1, max_length=200, description="e.g. 'property dealers in jaipur'"),
    limit: int = Query(10, ge=1, le=100, description="max pages to find"),
    provider: str = Query("apify", pattern="^(apify|brightdata)$",
                          description="data provider: apify or brightdata"),
):
    """Start an agent search run. Returns immediately; poll GET /api/search/{run_id}."""
    from app.agent.intent import parse_query
    from app.agent.search import run_search
    from app.config import get_settings
    from app.db.models import utcnow

    if provider == "brightdata" and not get_settings().brightdata_api_key:
        raise HTTPException(status_code=400, detail=(
            "Bright Data is not configured — add BRIGHTDATA_API_KEY to .env "
            "(https://brightdata.com/cp/setting/users) and restart"))

    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    intent = parse_query(query, limit)
    run_id = f"{datetime.now().strftime('%Y%m%d%H%M%S')}{len(query) % 97:02d}{len(_tasks) % 89:02d}"
    await db.search_history.insert_one({
        "run_id": run_id, "query": query, "intent": intent, "limit": limit,
        "provider": provider,
        "status": "running", "phase": "queued", "message": "Starting...",
        "pages_found": 0, "pages_stored": 0,
        "created_at": utcnow(), "completed_at": None,
    })
    _start(f"search:{run_id}", run_search, run_id, query, limit, provider)
    return {"run_id": run_id, "status": "running", "intent": intent, "provider": provider}


@router.post("/search/{run_id}/collect")
async def auto_collect_run(
    run_id: str,
    max_posts: int = Query(20, ge=1, le=100),
    auto_comments: int = Query(3, ge=0, le=10),
):
    """Automatically collect posts for every page of the run, then comments
    (with AI analysis) for the top posts of each page. No clicks needed."""
    from app.agent.search import collect_run_posts

    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    run = await db.search_history.find_one({"run_id": run_id})
    if not run:
        raise HTTPException(status_code=404, detail="Search run not found")
    started = _start(f"collect:{run_id}", collect_run_posts, run_id, max_posts, auto_comments)
    if not started:
        return {"status": "running", "message": "Auto-collection already in progress"}
    return {"status": "running", "message": "Auto-collection started"}


@router.get("/search/history")
async def search_history(limit: int = Query(20, ge=1, le=100)):
    db = get_async_db()
    if db is None:
        return {"searches": [], "count": 0}
    cursor = db.search_history.find({}).sort("created_at", -1).limit(limit)
    docs = [_serialize(doc) async for doc in cursor]
    return {"searches": docs, "count": len(docs)}


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


# ─────────────────────────────────────────────────────────────────────────────
# URL-BASED SEARCH — paste a Facebook/Instagram/YouTube/LinkedIn URL
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/url/search")
async def start_url_search(
    url: str = Query(..., min_length=4, max_length=300,
                     description="Facebook page, Instagram profile, YouTube channel or LinkedIn company URL"),
    max_posts: int = Query(20, ge=1, le=100),
):
    """Start a URL-based social lead search. Poll GET /api/search/{run_id}."""
    from app.social.url_detector import detect_social_url, UrlError
    from app.social.url_search import UrlSearchThread

    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        platform, canonical = detect_social_url(url)
    except UrlError as e:
        raise HTTPException(status_code=422, detail={
            "success": False, "errorType": e.kind, "message": e.message})
    except Exception as e:
        raise HTTPException(status_code=422, detail={
            "success": False, "errorType": "invalid", "message": f"Invalid URL: {e}"})

    run_id = f"URL{datetime.now().strftime('%Y%m%d%H%M%S')}{abs(hash(url)) % 1000:03d}"
    await db.search_history.insert_one({
        "run_id": run_id, "query": url, "intent": {
            "keyword": url, "type": "url", "platform": platform,
            "canonical_url": canonical, "limit": max_posts},
        "limit": max_posts, "provider": "apify", "url_search": True,
        "status": "running", "phase": "queued", "message": "Starting URL search...",
        "pages_found": 0, "pages_stored": 0,
        "created_at": utcnow(), "completed_at": None,
    })
    _start(f"url_search:{run_id}", UrlSearchThread(run_id, canonical, max_posts).run)
    return {
        "run_id": run_id, "status": "running", "platform": platform,
        "canonical_url": canonical,
        "message": f"{platform} search started — the page appears in the results below "
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
    posts = []
    async for p in db.facebook_posts.find({"page_ref": page_id}).sort("published_date", -1).limit(30):
        doc = _serialize(p)
        doc["total_comment_count"] = doc.get("total_comment_count") or doc.get("comments_count")
        doc.setdefault("scraped_comment_count", None)
        posts.append(doc)

    post_ids = [p["id"] for p in posts]
    comments = []
    if post_ids:
        async for c in db.facebook_comments.find({"post_ref": {"$in": post_ids}}).sort("published_date", -1).limit(100):
            comments.append(_serialize(c))
    for c in comments:
        c["commenter_name"] = c.get("author_name")
        c["comment_text"] = c.get("text")
        # enrich raw comments with AI analysis (phone, email, intent, lead score…)
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
        "platform": page.get("platform") or "facebook",
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
    # Only pages with at least one qualifying post are results. Pages that are
    # still being analyzed (or failed) stay visible so the UI can show progress
    # and offer "Analyze Posts". Legacy pages analyzed before this feature are
    # kept visible too.
    query = {"$or": [
        {"posts_status": {"$in": ["not_started", "pending", "running", "error"]}},
        {"qualifying_posts_count": {"$gt": 0}},
        {"posts_status": "completed", "qualifying_posts_count": {"$exists": False}},
    ]}
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
        docs.append(_serialize(p))
    total = await db.facebook_pages.count_documents(query)
    return {"pages": docs, "total": total, "offset": offset, "limit": limit}


@router.get("/pages/{page_id}")
async def get_page(page_id: str):
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return _serialize(await _doc_or_404(db, "facebook_pages", _oid(page_id)))


@router.post("/pages/{page_id}/posts")
async def collect_posts(page_id: str, max_posts: int = Query(20, ge=1, le=100)):
    """Collect posts of the selected page via apify/facebook-posts-scraper."""
    from app.agent.search import collect_page_posts

    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    page = await db.facebook_pages.find_one({"_id": _oid(page_id)})
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
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
        "minComments": _MIN_COMMENTS,
        "posts_status": page.get("posts_status"),
        "posts_count": page.get("posts_count", 0),
        "posts_error": page.get("posts_error"),
        "posts_error_meta": page.get("posts_error_meta"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# POSTS
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/posts/{post_id}")
async def get_post(post_id: str):
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return _serialize(await _doc_or_404(db, "facebook_posts", _oid(post_id)))


@router.post("/posts/{post_id}/comments")
async def collect_comments(post_id: str, max_comments: int = Query(200, ge=1, le=500)):
    """Collect comments of the selected post + run AI analysis (ai_comments)."""
    from app.agent.search import collect_post_comments

    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    post = await db.facebook_posts.find_one({"_id": _oid(post_id)})
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    started = _start(f"comments:{post_id}", collect_post_comments, post_id, max_comments)
    if not started:
        return {"status": "running", "message": "Comments collection already in progress"}
    return {"status": "running", "message": "Comments collection + AI analysis started"}


@router.get("/posts/{post_id}/comments")
async def list_post_comments(
    post_id: str,
    only_leads: bool = Query(True, description="show only valuable comments (is_lead)"),
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
        total = await db.ai_comments.count_documents(query)
    else:
        # ALL raw comments, enriched with AI analysis where it exists
        total = await db.facebook_comments.count_documents(query)
        async for raw in (db.facebook_comments.find(query)
                          .sort("published_date", -1).skip(offset).limit(limit)):
            c = _serialize(raw)
            c["commenter_name"] = raw.get("author_name")
            c["comment_text"] = raw.get("text")
            analysis = await db.ai_comments.find_one({"comment_ref": c["id"]})
            if analysis:
                a = _serialize(analysis)
                for key in ("is_lead", "lead_score", "priority", "lead_quality",
                            "confidence", "intent", "urgency", "budget", "requirement",
                            "location", "phone", "email", "whatsapp", "website",
                            "reason", "analyzed_by"):
                    if a.get(key) is not None:
                        c[key] = a[key]
            docs.append(c)
    return {
        "post": _serialize(post),
        "comments": docs,
        "total": total,
        "comments_status": post.get("comments_status"),
        "total_comment_count": post.get("total_comment_count") or post.get("comments_count") or 0,
        "scraped_comment_count": post.get("scraped_comment_count") or 0,
        "comments_count": post.get("scraped_comment_count") or 0,
        "minComments": _MIN_COMMENTS,
        "comments_error": post.get("comments_error"),
        "comments_error_meta": post.get("comments_error_meta"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# LEAD DETAIL
# ─────────────────────────────────────────────────────────────────────────────

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
    result["commenter_name"] = raw.get("author_name")
    result["comment_text"] = raw.get("text")
    result["comment"] = result
    result["post"] = _serialize(post) if post else None
    result["page"] = _serialize(page) if page else None
    result["lead_quality"] = "none"
    result["priority"] = None
    result["is_lead"] = False
    return result


# ─────────────────────────────────────────────────────────────────────────────
# EXPORT — CSV
# ─────────────────────────────────────────────────────────────────────────────

PAGES_CSV = ["page_name", "facebook_url", "page_id", "category", "source_type",
             "followers", "likes", "phone", "email", "whatsapp", "website",
             "address", "city", "state", "verified", "about",
             "total_posts_found", "relevant_posts_count", "qualifying_posts_count",
             "total_comments_on_qualifying_posts", "latest_post_date",
             "activity_status", "lead_score"]
POSTS_CSV = ["post_id", "page_name", "post_url", "caption", "published_date",
             "likes_count", "total_comment_count", "scraped_comment_count",
             "shares_count", "is_relevant", "is_qualifying", "images"]
COMMENTS_CSV = ["commenter_name", "comment_text", "phone", "email", "whatsapp", "website",
                "budget", "requirement", "location", "intent", "urgency", "priority",
                "lead_quality", "confidence", "lead_score"]


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
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    if scope == "pages":
        query = {"search_run_id": run_id} if run_id else {}
        rows = [doc async for doc in db.facebook_pages.find(query).sort("followers", -1)]
        return _csv_response(rows, PAGES_CSV, f"pages_{datetime.now().strftime('%Y%m%d')}.csv")

    if scope == "posts":
        if not page_id:
            raise HTTPException(status_code=400, detail="page_id is required for posts export")
        rows = [doc async for doc in db.facebook_posts.find({"page_ref": page_id}).sort("published_date", -1)]
        for row in rows:
            if row.get("total_comment_count") is None:
                row["total_comment_count"] = row.get("comments_count")
        return _csv_response(rows, POSTS_CSV, f"posts_{datetime.now().strftime('%Y%m%d')}.csv")

    if scope == "comments":
        if not post_id:
            raise HTTPException(status_code=400, detail="post_id is required for comments export")
        query = {"post_ref": post_id}
        if only_leads:
            query["is_lead"] = True
        rows = [doc async for doc in db.ai_comments.find(query).sort("lead_score", -1)]
        return _csv_response(rows, COMMENTS_CSV, f"leads_{datetime.now().strftime('%Y%m%d')}.csv")

    raise HTTPException(status_code=404, detail="scope must be pages, posts or comments")
