"""
URL-based lead search pipeline.

Flow (runs in a background thread; status lives in `search_history`):

    POST /api/url/search
      → detect + canonicalize the social URL (UrlError on bad input)
      → fetch page details (apify actor for the platform)
      → save/merge into `facebook_pages`  (dedupe by facebook_url+run_id)
      → fetch posts → `facebook_posts` (dedupe by post_url per page)
      → fetch comments → `facebook_comments` (dedupe by comment_url per post)

Overloads the existing collections so every existing page/post/comment
endpoint (list, posts, comments, CSV export, report page) works for URL
search results too — the docs carry a `platform` field for display.
"""
import logging
import threading
from typing import Any, Dict, List

from pymongo.errors import DuplicateKeyError

from app.config import get_settings
from app.db.mongo import get_sync_db
from app.db.models import utcnow
from app.social.url_detector import UrlError, detect_social_url
from app.social.scrapers import get_scraper

logger = logging.getLogger(__name__)


from bson import ObjectId  # noqa: E402


def _url_derived_page(platform: str, url: str, run_id: str,
                      page_error: Dict[str, Any]) -> Dict[str, Any]:
    """Minimal page doc built from the canonical URL alone — used when the
    details actor is unavailable (access/credits/blocked). The pipeline can
    still collect posts/comments and the report page stays meaningful."""
    from urllib.parse import unquote
    handle = None
    for seg in reversed(url.rstrip("/").split("/")):
        seg = unquote(seg).strip()
        if seg and seg not in ("@", "c", "user", "channel", "company", "pages"):
            handle = seg
            break
    name = (handle or url)
    name = name.replace("-", " ").replace("_", " ").title()
    return {
        "page_id": None,
        "page_name": (name[:80] if name else None) or url,
        "facebook_url": url,
        "platform": platform,
        "category": None,
        "about": None,
        "followers": None,
        "likes": None,
        "verified": None,
        "phone": None,
        "email": None,
        "whatsapp": None,
        "website": None,
        "address": None,
        "city": None,
        "state": None,
        "country": None,
        "profile_picture": None,
        "cover_image": None,
        "source_type": "page_url",
        "source_page_url": url,
        "search_run_id": run_id,
        "search_keyword": url,
        "source": "apify_url_search",
        "posts_status": "not_started",
        "comments_status": "not_started",
        "details_error": page_error.get("message"),
        "details_error_type": page_error.get("errorType"),
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }


def run_url_search(run_id: str, initial_url: str, max_posts: int = 20) -> Dict[str, Any]:
    """Execute one URL search. Returns a bundle with page/posts/comments counts."""
    db = get_sync_db()
    if db is None:
        return {"status": "error", "error": "Database unavailable"}
    settings = get_settings()

    from app.agent.search import is_run_cancelled, mark_run_cancelled

    def progress(message: str = "", **fields):
        db.search_history.update_one(
            {"run_id": run_id},
            {"$set": {"message": message, "updated_at": utcnow(), **fields}},
        )

    def should_abort():
        return is_run_cancelled(run_id, db)

    def cancelled():
        mark_run_cancelled(run_id, db)
        return {"status": "cancelled", "success": False,
                "message": "Search cancelled by user",
                "posts": 0, "comments": 0, "items": []}

    # ── 1. validate the URL ────────────────────────────────────────────────
    try:
        platform, canonical_url = detect_social_url(initial_url)
    except UrlError as e:
        progress(status="error", error=e.message, phase="url_invalid")
        return {"status": "error", "error": e.message,
                "success": False, "items": [], "page_id": None}

    if should_abort():
        progress(status="cancelled", phase="cancelled",
                 message="Search cancelled by user", completed_at=utcnow())
        return cancelled()

    if not settings.apify_api_token:
        msg = ("APIFY_API_TOKEN is not set in .env — add it and restart. "
               "Get a free token at https://apify.com/account/integrations")
        progress(status="error", error=msg)
        return {"status": "error", "error": msg, "success": False,
                "page_id": None}

    progress(phase="page", platform=platform,
             message=f"Fetching {platform} page details…")
    scraper = get_scraper(platform)

    # ── 2. fetch + store the page ──────────────────────────────────────────
    page_error: Dict[str, Any] = {}
    try:
        page_items = scraper.fetch_page_details(canonical_url,
                                                should_abort=should_abort)
    except Exception as e:
        if should_abort():
            progress(status="cancelled", phase="cancelled",
                     message="Search cancelled by user", completed_at=utcnow())
            return cancelled()
        # keep the run alive: page details may be unavailable (actor access /
        # credits / private page) while posts are still scrapeable
        logger.warning("[URL SEARCH] page details for %s failed: %s", platform, e)
        page_error = {
            "errorType": getattr(e, "error_type", "API_ERROR"),
            "message": str(e),
        }
        page_items = []

    page_doc = None
    for item in page_items:
        page_json = scraper.normalize_page(item, run_id, canonical_url)
        if not page_json:
            continue
        page_json.setdefault("created_at", utcnow())
        page_json["updated_at"] = utcnow()
        # the pages collection indexes on (facebook_url, search_run_id) — dedupe
        existing = db.facebook_pages.find_one(
            {"facebook_url": page_json["facebook_url"], "search_run_id": run_id})
        if existing:
            # augment (never lose original run data we keep) then refresh stats
            merged = {k: v for k, v in page_json.items() if v is not None}
            merged["search_run_id"] = run_id
            db.facebook_pages.update_one({"_id": existing["_id"]},
                                         {"$set": {**merged, "updated_at": utcnow()}})
            page_doc = {**existing, **merged}
        else:
            page_json["_id"] = ObjectId()
            db.facebook_pages.insert_one(page_json)
            page_doc = page_json
        break

    if page_doc is None:
        if page_error:
            # graceful fallback — a URL-derived page doc keeps the pipeline
            # (posts + comments) usable even when the details actor is blocked
            page_doc = _url_derived_page(platform, canonical_url, run_id, page_error)
            try:
                page_doc["_id"] = ObjectId()
                db.facebook_pages.insert_one(page_doc)
            except Exception:
                logger.warning("[URL SEARCH] fallback page insert failed", exc_info=True)
        else:
            msg = ("Could not read page details — the page/profile may be private "
                   "or the link may be wrong.")
            progress(status="error", error=msg, phase="no_page")
            return {"status": "error", "error": msg, "success": False,
                    "page_id": None}

    page_id = str(page_doc["_id"])
    logger.info(f"[URL SEARCH] page stored: {page_id} "
                f"({page_doc.get('platform', 'facebook')})")

    # ── 3. posts ───────────────────────────────────────────────────────────
    progress(phase="posts", page_id=page_id, message="Collecting posts…")
    post_docs: List[Dict[str, Any]] = []
    try:
        post_items = scraper.fetch_posts(canonical_url, max_posts,
                                         should_abort=should_abort)
    except Exception as e:
        if should_abort():
            progress(status="cancelled", phase="cancelled",
                     message="Search cancelled by user", completed_at=utcnow())
            return cancelled()
        logger.exception("[URL SEARCH] posts scrape failed")
        post_items = []
        progress(message=f"Posts collection failed: {e}", posts_error=str(e))
    for item in post_items:
        if should_abort():
            progress(status="cancelled", phase="cancelled",
                     message="Search cancelled by user", completed_at=utcnow())
            return cancelled()
        post_json = scraper.normalize_post(item, page_doc)
        if not post_json or not post_json.get("post_url"):
            continue
        post_json["_id"] = ObjectId()
        post_json["page_ref"] = page_id
        post_json["search_run_id"] = run_id
        post_json["provider"] = page_doc.get("provider") or "apify"
        post_json["created_at"] = utcnow()
        post_json["updated_at"] = utcnow()
        existing = db.facebook_posts.find_one(
            {"post_url": post_json["post_url"], "page_ref": page_id})
        if existing:
            db.facebook_posts.update_one(
                {"_id": existing["_id"]},
                {"$set": {k: v for k, v in post_json.items() if v is not None}})
            post_docs.append({**existing, **post_json})
        else:
            db.facebook_posts.insert_one(post_json)
            post_docs.append(post_json)

    db.facebook_pages.update_one({"_id": page_doc["_id"]}, {"$set": {
        "posts_status": "completed" if post_docs else "empty",
        "posts_count": len(post_docs),
        "posts_collected_at": utcnow(), "updated_at": utcnow()}})
    logger.info(f"[URL SEARCH] stored {len(post_docs)} posts for page {page_id}")

    # post statistics for this page — identical to the keyword-search flow
    try:
        from app.agent.search import _compute_page_stats
        stats = _compute_page_stats(post_docs)
        page_stats = {
            "total_posts_found": stats["total_posts_found"],
            "latest_post_date": stats["latest_post_date"],
            "activity_status": stats["activity_status"],
            "total_comments_on_qualifying_posts": stats["total_comments_on_qualifying_posts"],
            "lead_score": stats["lead_score"],
            "has_qualifying_posts": stats["has_qualifying_posts"],
        }
        # only set counts when > 0 — the pages list keeps URL-search pages
        # visible via source="apify_url_search" regardless
        if stats["relevant_posts_count"]:
            page_stats["relevant_posts_count"] = stats["relevant_posts_count"]
        if stats["qualifying_posts_count"]:
            page_stats["qualifying_posts_count"] = stats["qualifying_posts_count"]
        db.facebook_pages.update_one({"_id": page_doc["_id"]},
                                     {"$set": {**page_stats, "updated_at": utcnow()}})
    except Exception:
        logger.warning("[URL SEARCH] page stats computation failed", exc_info=True)

    # ── 4. comments (facebook + instagram only) ────────────────────────────
    progress(phase="comments", message="Collecting comments…")
    comment_docs = 0
    if getattr(scraper, "comments_supported", True) and post_docs:
        from app.pipeline.comment_ai import has_contact_info
        cap = int(getattr(settings, "max_comments_to_collect", 100) or 100)
        per_post = max(1, min(cap, 30))
        for post in post_docs:
            if should_abort():
                progress(status="cancelled", phase="cancelled",
                         message="Search cancelled by user", completed_at=utcnow())
                return cancelled()
            if comment_docs >= cap:
                break
            try:
                raw_comments = scraper.fetch_comments(post["post_url"], per_post,
                                                      should_abort=should_abort)
            except Exception as e:
                if should_abort():
                    progress(status="cancelled", phase="cancelled",
                             message="Search cancelled by user", completed_at=utcnow())
                    return cancelled()
                logger.warning(f"[URL SEARCH] comments failed for {post['post_url']}: {e}")
                raw_comments = []
            stored_for_post = 0
            for item in raw_comments:
                if should_abort():
                    progress(status="cancelled", phase="cancelled",
                             message="Search cancelled by user", completed_at=utcnow())
                    return cancelled()
                comment_json = scraper.normalize_comment(item, post)
                if not comment_json or not comment_json.get("comment_url"):
                    continue
                # every scraped comment is kept; comments carrying a phone
                # number or email are flagged (has_contact) so they surface
                # at the top of the comments view as leads
                comment_json["has_contact"] = bool(
                    has_contact_info(comment_json.get("text")))
                comment_json["_id"] = ObjectId()
                comment_json["post_ref"] = str(post["_id"])
                comment_json["search_run_id"] = run_id
                comment_json["created_at"] = utcnow()
                comment_json["updated_at"] = utcnow()
                try:
                    db.facebook_comments.insert_one(comment_json)
                    comment_docs += 1
                    stored_for_post += 1
                except DuplicateKeyError:
                    continue
                if comment_docs >= cap:
                    break
            # mark the post completed as soon as comments are stored so the
            # UI can show them immediately — AI analysis below only refines
            db.facebook_posts.update_one({"_id": post["_id"]}, {"$set": {
                "comments_status": "completed" if stored_for_post else "empty",
                "scraped_comment_count": stored_for_post,
                "comments_collected_at": utcnow(), "updated_at": utcnow()}})
            # analyze each post's comments through the same AI pipeline used
            # by the keyword search so leads get phone/email/intent/score etc.
            if stored_for_post:
                try:
                    from app.pipeline.comment_ai import analyze_comments_for_post
                    analyze_comments_for_post(str(post["_id"]))
                except Exception as e:
                    logger.warning(f"[URL SEARCH] AI comment analysis failed for "
                                   f"{post['post_url']}: {e}")

    # ── 5. finalize ────────────────────────────────────────────────────────
    # a run that could not fetch details AND got no posts is a failure, not
    # a success — surface the real reason (actor access/credits/block/private)
    error = None
    if not post_docs:
        if page_error:
            error = (f"Page details and posts could not be fetched for this "
                     f"{platform} link. {page_error.get('message', '')}".strip())
        elif comment_docs == 0:
            error = None  # page-only runs are still meaningful
    if error:
        progress(status="error", error=error, phase="failed",
                 message=error, completed_at=utcnow())
        logger.info(f"[URL SEARCH] run {run_id} failed: {error}")
        return {
            "status": "error", "error": error, "success": False,
            "platform": platform, "url": canonical_url, "page_id": page_id,
            "posts": 0, "comments": 0, "items": [],
        }

    progress(status="completed", phase="completed",
             message=f"Completed — {len(post_docs)} posts, {comment_docs} comments",
             completed_at=utcnow())
    logger.info(f"[URL SEARCH] run {run_id} completed: platform={platform} "
                f"posts={len(post_docs)} comments={comment_docs}")
    return {
        "status": "completed",
        "success": True,
        "platform": platform,
        "url": canonical_url,
        "page_id": page_id,
        "posts": len(post_docs),
        "comments": comment_docs,
        "items": [],  # pages are browsable through GET /api/pages?run_id=
    }


class UrlSearchThread(threading.Thread):
    """Background worker for the URL search route."""

    def __init__(self, run_id: str, url: str, max_posts: int):
        super().__init__(daemon=True)
        self.run_id = run_id
        self.url = url
        self.max_posts = max_posts

    def run(self):
        try:
            run_url_search(self.run_id, self.url, self.max_posts)
        except Exception as e:
            logger.exception("[URL SEARCH] background run crashed")
            db = get_sync_db()
            if db is not None:
                db.search_history.update_one({"run_id": self.run_id}, {"$set": {
                    "status": "error", "error": f"Internal error: {e}",
                    "updated_at": utcnow()}})