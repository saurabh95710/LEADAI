"""
AI Agent orchestrator — the only brain between the user and Facebook.

Flow (each step is real Apify data, never mocked or fabricated):
    POST /api/search
      → parse intent (keyword / city / state / category / limit)
      → apify/facebook-search-scraper (+ pages-scraper graph fallback)
      → normalize + store into `facebook_pages` (upsert, dedupe by URL)
      → best-effort detail enrichment via apify/facebook-pages-scraper
      → update `search_history` (real-time status)

    POST /api/pages/{id}/posts
      → apify/facebook-posts-scraper for THAT page only → `facebook_posts`

    POST /api/posts/{id}/comments
      → apify/facebook-comments-scraper for THAT post only
      → `facebook_comments` → AI analysis → `ai_comments`

All functions are synchronous — they run in background threads from the
FastAPI routes (asyncio.to_thread) so the UI can poll status live.
"""
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.connectors.apify_connector import ApifyConnector, ApifyError, ScrapeError
from app.connectors.brightdata_connector import BrightDataConnector, BrightDataError
from app.config import get_settings
from app.db.mongo import get_sync_db
from app.db.models import utcnow
from app.agent.intent import parse_query

logger = logging.getLogger(__name__)

_PROVIDERS = ("apify", "brightdata")

# A post qualifies as lead material when it is RELEVANT (mentions a query
# token) AND its Facebook-reported total comment count is >= MIN_COMMENTS.
# Configurable via MIN_COMMENTS in .env (0 disables the requirement).
_MIN_COMMENTS = get_settings().min_comments


def get_connector(provider: Optional[str] = None):
    """Provider factory — one shared interface, two backends."""
    provider = (provider or "apify").strip().lower()
    if provider == "brightdata":
        return BrightDataConnector()
    return ApifyConnector()


class NotFoundError(Exception):
    pass


# A "running" status left by a crashed/restarted server is stale after 30
# minutes — allow retry instead of blocking the page forever.
_STALE_TIMEOUT = timedelta(minutes=30)


def _stale(doc: Dict[str, Any], started_key: str) -> bool:
    started = doc.get(started_key)
    if not started:
        return True
    try:
        return (utcnow() - started) > _STALE_TIMEOUT
    except TypeError:
        return True


def is_run_cancelled(run_id: str, db=None) -> bool:
    """True when the user requested cancellation of this run
    (POST /api/search/{run_id}/cancel sets `cancel_requested`)."""
    if db is None:
        db = get_sync_db()
    if db is None:
        return False
    doc = db.search_history.find_one({"run_id": run_id}, {"cancel_requested": 1})
    return bool(doc and doc.get("cancel_requested"))


def mark_run_cancelled(run_id: str, db=None, message: str = "Search cancelled by user") -> None:
    if db is None:
        db = get_sync_db()
    if db is None:
        return
    db.search_history.update_one({"run_id": run_id}, {"$set": {
        "status": "cancelled", "phase": "cancelled", "message": message,
        "cancel_requested": True, "completed_at": utcnow(), "updated_at": utcnow()}})


# ─────────────────────────────────────────────────────────────────────────────
# Normalization — raw actor items → data-model docs (never fabricate)
# ─────────────────────────────────────────────────────────────────────────────

def _first(*values: Any) -> Any:
    for v in values:
        if v not in (None, "", [], {}):
            return v
    return None


def _as_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    m = re.match(r"([\d.,]+\s*[KMBkmb]?)", text)
    if not m:
        return None
    token = m.group(1).replace(",", "").strip()
    mult = 1
    if token[-1:].upper() == "K":
        mult, token = 1000, token[:-1]
    elif token[-1:].upper() == "M":
        mult, token = 1000000, token[:-1]
    elif token[-1:].upper() == "B":
        mult, token = 1000000000, token[:-1]
    try:
        return int(float(token) * mult)
    except (TypeError, ValueError):
        return None


def _page_name(item: Dict[str, Any]) -> Optional[str]:
    """Best-effort page name. The search-scraper puts the real name in
    `title` ("Shyam property dealer | Jaipur") and leaves `pageName` as
    literally "p" for profile-style URLs."""
    title = str(item.get("title") or "").strip()
    if title:
        title = re.split(r"\s*\|\s*", title, maxsplit=1)[0].strip()
        if len(title) > 1:
            return title
    for key in ("pageName", "name"):
        val = str(item.get(key) or "").strip()
        if val and val.lower() != "p":
            return val
    return None


def _item_url(item: Dict[str, Any]) -> str:
    return str(_first(item.get("facebookUrl"), item.get("pageUrl"),
                      item.get("url"), item.get("link")) or "").strip()


def _item_photo(item: Dict[str, Any]) -> Optional[str]:
    url = _first(item.get("profilePictureUrl"), item.get("profilePhoto"),
                 item.get("profileImageUrl"), item.get("photoUrl"))
    return str(url).strip() if url else None


def _item_cover(item: Dict[str, Any]) -> Optional[str]:
    url = _first(item.get("coverPhotoUrl"), item.get("coverImageUrl"), item.get("coverPhoto"))
    return str(url).strip() if url else None


def _item_category(item: Dict[str, Any]) -> Optional[str]:
    cat = item.get("category")
    if cat:
        return str(cat).strip()
    cats = item.get("categories") or []
    if cats and isinstance(cats, list):
        for c in cats:
            c = str(c).strip()
            if c.lower() not in ("page", "musician/band", "organisation", "organization"):
                return c
    return None


def _item_location(item: Dict[str, Any]) -> Dict[str, Optional[str]]:
    loc = item.get("location")
    if isinstance(loc, dict):
        return {
            "city": str(loc.get("city") or "").strip() or None,
            "state": str(loc.get("state") or "").strip() or None,
            "country": str(loc.get("country") or "").strip() or None,
        }
    return {"city": None, "state": None, "country": None}


def _item_websites(item: Dict[str, Any]) -> Optional[str]:
    ws = item.get("website")
    if ws:
        return str(ws).strip()
    websites = item.get("websites") or []
    if websites and isinstance(websites, list):
        for w in websites:
            if isinstance(w, dict):
                w = w.get("url") or w.get("website") or w.get("link") or ""
            w = str(w or "").strip()
            if w and not _junk_url(w):
                return w
    return None


def _junk_url(url: str) -> bool:
    """Facebook search-scraper fills `websites` with maps/instagram links."""
    return any(host in url.lower() for host in (
        "google.com/maps", "bing.com/maps", "facebook.com",
        "instagram.com", "whatsapp.com", "fb.watch", "maps.app.goo.gl",
    ))


def _item_about(item: Dict[str, Any]) -> Optional[str]:
    intro = item.get("intro") or item.get("about") or item.get("introText")
    if intro:
        return str(intro).strip()
    info = item.get("info") or []
    if info and isinstance(info, list):
        return " ".join(str(x).strip() for x in info if str(x).strip())
    return None


def _extract_page_id(url: str) -> Optional[str]:
    if not url:
        return None
    m = re.search(r"profile\.php\?id=(\d+)", url)
    if m:
        return m.group(1)
    m = re.search(r"facebook\.com/pages/[^/]+/(\d+)", url)
    if m:
        return m.group(1)
    m = re.search(r"facebook\.com/(\d{5,})(?:/|$)", url)
    if m:
        return m.group(1)
    return None


# Different Facebook actors name the comment-count field differently.
_COMMENT_COUNT_KEYS = ("commentsCount", "commentCount", "comments", "comments_count")


def _count_value(value: Any) -> Optional[int]:
    """Parse a count that may be a plain int, '1.2K' string, a nested
    {count/total} wrapper, or a preview-comments list."""
    parsed = _as_int(value)
    if parsed is not None:
        return parsed
    if isinstance(value, dict):
        for key in ("count", "totalCount", "total", "value"):
            parsed = _as_int(value.get(key))
            if parsed is not None:
                return parsed
    if isinstance(value, list):
        return len(value)
    return None


def _comment_count(item: Dict[str, Any]) -> Optional[int]:
    """Detect the comment-count field regardless of the actor's naming or
    shape. Returns None when NO comment-count field exists on the item."""
    if not isinstance(item, dict):
        return None
    for key in _COMMENT_COUNT_KEYS:
        value = item.get(key)
        if value is not None:
            parsed = _count_value(value)
            if parsed is not None:
                return parsed
    return None


# ── Relevance & engagement ranking (real data only, nothing fabricated) ─────

_STOPWORDS = {
    "a", "an", "the", "in", "of", "to", "for", "on", "at", "by", "near",
    "with", "and", "or", "is", "are", "our", "you", "your", "we", "me",
    "us", "it", "this", "that", "be", "as", "from",
}


def _relevance_tokens(text: Optional[str]) -> set:
    """Meaningful tokens of a query / location — the relevance yardstick."""
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if len(w) > 1 and w not in _STOPWORDS}


def _build_query_tokens(page_doc: Dict[str, Any]) -> set:
    """Tokens from the search keyword + the page's own city/state."""
    tokens: set = set()
    for part in (page_doc.get("search_keyword"),
                 page_doc.get("city"), page_doc.get("state")):
        if part:
            tokens |= _relevance_tokens(part)
    return tokens


def _post_relevant(caption: Optional[str], tokens: set) -> Optional[bool]:
    """True when the caption mentions any query token, False when it clearly
    doesn't, None when there is nothing to judge against."""
    if not tokens:
        return None
    if not caption:
        return False
    text = str(caption).lower()
    for token in tokens:
        if token in text:
            return True
    return False


def _parse_iso(value: Any) -> Optional[datetime]:
    """Actor date strings (ISO with/without time/tz, or plain date) → datetime."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(value))
        if m:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                            tzinfo=timezone.utc)
        return None


_ACTIVE_DAYS = timedelta(days=90)
_RECENT_DAYS = timedelta(days=365)


def _activity_status(latest_date: Any) -> str:
    """active | recent | inactive | unknown — from the latest real post date."""
    latest = _parse_iso(latest_date)
    if latest is None:
        return "unknown"
    age = utcnow() - latest
    if age <= _ACTIVE_DAYS:
        return "active"
    if age <= _RECENT_DAYS:
        return "recent"
    return "inactive"


def _lead_score(qualifying_count: int, total_comments: int, activity: str) -> int:
    """Deterministic page ranking: qualifying posts dominate, then total
    comments on them, then posting recency. 0 when nothing qualifies."""
    if qualifying_count <= 0:
        return 0
    score = qualifying_count * 5 + total_comments
    if activity == "active":
        score += 100
    elif activity == "recent":
        score += 50
    return score


def _is_qualifying_post(doc: Dict[str, Any]) -> bool:
    """Relevant post AND Facebook-reported total comments >= _MIN_COMMENTS."""
    if doc.get("is_relevant") is not True:
        return False
    total = doc.get("total_comment_count")
    if total is None:
        total = doc.get("comments_count")
    return bool(total is not None and total >= _MIN_COMMENTS)


def _compute_page_stats(posts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Post statistics for a page — from the real stored post docs only."""
    qualifying = [p for p in posts if _is_qualifying_post(p)]
    relevant = [p for p in posts if p.get("is_relevant") is True]
    comments = sum((p.get("total_comment_count") or 0) for p in qualifying)
    latest = None
    for p in posts:
        parsed = _parse_iso(p.get("published_date"))
        if parsed and (latest is None or parsed > latest):
            latest = parsed
    latest_value = latest.isoformat() if latest else None
    activity = _activity_status(latest_value)
    qualifying_count = len(qualifying)
    return {
        "total_posts_found": len(posts),
        "relevant_posts_count": len(relevant),
        "qualifying_posts_count": qualifying_count,
        "total_comments_on_qualifying_posts": comments,
        "latest_post_date": latest_value,
        "has_qualifying_posts": qualifying_count > 0,
        "activity_status": activity,
        "lead_score": _lead_score(qualifying_count, comments, activity),
    }


def map_page_item(item: Dict[str, Any], run_id: str, keyword: str) -> Optional[Dict[str, Any]]:
    """Raw search-scraper / pages-scraper item → facebook_pages doc."""
    if not isinstance(item, dict) or item.get("error"):
        return None
    url = _item_url(item)
    if not url or "facebook.com" not in url.lower():
        return None
    loc = _item_location(item)
    name = _page_name(item)
    return {
        "page_id": str(item.get("pageId") or "").strip() or _extract_page_id(url),
        "page_name": name or None,
        "facebook_url": url,
        "category": _item_category(item),
        "about": _item_about(item),
        "followers": _as_int(item.get("followers")) or _as_int(item.get("followerCount")),
        "likes": _as_int(item.get("likes")) or _as_int(item.get("likeCount")),
        "verified": item.get("verified") if isinstance(item.get("verified"), bool) else None,
        "phone": str(_first(item.get("phone"), item.get("phoneNumber")) or "").strip() or None,
        "email": str(_first(item.get("email"), item.get("contactEmail")) or "").strip() or None,
        "whatsapp": str(_first(item.get("whatsapp_number"), item.get("wa_number"),
                               item.get("whatsappNumber")) or "").strip() or None,
        "website": _item_websites(item),
        "address": str(item.get("address") or "").strip() or None,
        "city": loc["city"],
        "state": loc["state"],
        "country": loc["country"],
        "profile_picture": _item_photo(item),
        "cover_image": _item_cover(item),
        "source_type": "group" if re.search(r"facebook\.com/groups?/", url.lower()) else "page",
        "search_run_id": run_id,
        "search_keyword": keyword,
        "source": "apify_search",
        "posts_status": "not_started",
        "comments_status": "not_started",
    }


def merge_page_details(page_doc: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
    """Fill missing page fields from a pages-scraper detail item (never overwrite)."""
    updates: Dict[str, Any] = {}
    detail = map_page_item(item, page_doc.get("search_run_id") or "", page_doc.get("search_keyword") or "")
    if not detail:
        return updates
    for key in ("verified", "email", "phone", "whatsapp", "website", "address",
                "about", "profile_picture", "cover_image", "category", "followers",
                "likes", "page_name"):
        if not page_doc.get(key) and detail.get(key):
            updates[key] = detail[key]
    return updates


def map_post_item(item: Dict[str, Any], page_doc: Dict[str, Any],
                  query_tokens: Optional[set] = None) -> Optional[Dict[str, Any]]:
    """Raw posts-scraper item → facebook_posts doc."""
    if not isinstance(item, dict) or item.get("error"):
        return None
    post_url = str(_first(item.get("url"), item.get("postUrl"), item.get("link")) or "").strip()
    if not post_url:
        return None
    images = item.get("imageUrls") or item.get("images") or []
    if isinstance(images, str):
        images = [images]
    # this actor returns media as a list of {thumbnail/thumbnailImage/image}
    media = item.get("media") or []
    if isinstance(media, list):
        for m in media:
            if not isinstance(m, dict):
                continue
            thumb = m.get("thumbnail")
            if not thumb and isinstance(m.get("thumbnailImage"), dict):
                thumb = m["thumbnailImage"].get("uri")
            if not thumb and isinstance(m.get("photo_image"), dict):
                thumb = m["photo_image"].get("uri")
            if not thumb and isinstance(m.get("image"), dict):
                thumb = m["image"].get("uri")
            if thumb and thumb not in images:
                images.append(thumb)
    videos = item.get("videoUrls") or []
    if isinstance(videos, str):
        videos = [videos]
    elif item.get("videoUrl") and str(item["videoUrl"]).strip():
        videos = [str(item["videoUrl"]).strip()] + ([str(v) for v in videos if v] if isinstance(videos, list) else [])
    links = item.get("externalLinkUrls") or item.get("links") or []
    if isinstance(links, str):
        links = [links]
    caption = str(_first(item.get("text"), item.get("caption"), item.get("postText")) or "").strip() or None
    tokens = query_tokens if query_tokens is not None else _build_query_tokens(page_doc)
    relevant = _post_relevant(caption, tokens)
    total = _comment_count(item)
    return {
        "post_id": str(item.get("id") or item.get("postId") or "").strip() or None,
        "post_url": post_url,
        "page_id": page_doc.get("page_id"),
        "page_name": page_doc.get("page_name"),
        "caption": caption,
        "images": [str(x) for x in images if x],
        "videos": [str(x) for x in videos if x],
        "external_links": [str(x) for x in links if x],
        "published_date": str(_first(item.get("date"), item.get("publishedAt"), item.get("time")) or "").strip() or None,
        "likes_count": _as_int(item.get("likesCount")) or _as_int(item.get("likes")),
        "total_comment_count": total,
        "scraped_comment_count": None,
        "comments_count": total,            # legacy alias of the Facebook-reported total
        "shares_count": _as_int(item.get("sharesCount")) or _as_int(item.get("shares")),
        "is_relevant": relevant,
        "is_qualifying": bool(total is not None and total >= _MIN_COMMENTS and relevant is True),
        "page_ref": str(page_doc["_id"]),
        "search_run_id": page_doc.get("search_run_id"),
        "provider": page_doc.get("provider") or "apify",
    }


def map_comment_item(item: Dict[str, Any], post_doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Raw comments-scraper item → facebook_comments doc."""
    if not isinstance(item, dict) or item.get("error"):
        return None
    comment_id = str(item.get("id") or item.get("commentId") or "").strip() or None
    post_url = str(item.get("postUrl") or post_doc.get("post_url") or "").strip()
    comment_url = str(item.get("url") or item.get("commentUrl") or "").strip()
    if not comment_url and comment_id:
        comment_url = post_url.split("?")[0] + f"?comment_id={comment_id}"
    if not comment_url:
        return None
    # the facebook-comments-scraper actor names these differently per run
    author = item.get("author")
    if isinstance(author, str):
        author = {}
    if not isinstance(author, dict):
        author = {}
    return {
        "comment_id": comment_id,
        "comment_url": comment_url,
        "author_name": str(_first(item.get("profileName"),
                                  item.get("authorName"), author.get("name")) or "").strip() or None,
        "author_profile_url": str(_first(item.get("profileUrl"),
                                         item.get("authorUrl"),
                                         item.get("authorProfileUrl"),
                                         author.get("url"),
                                         author.get("profileUrl")) or "").strip() or None,
        "text": str(_first(item.get("text"), item.get("comment"),
                           item.get("body")) or "").strip() or None,
        "published_date": str(_first(item.get("date"), item.get("publishedAt")) or "").strip() or None,
        "reactions_count": _as_int(item.get("likesCount")) or _as_int(item.get("reactionsCount")),
        "post_id": post_doc.get("post_id") or str(post_doc.get("_id") or ""),
        "post_url": post_url,
        "page_id": post_doc.get("page_id"),
        "post_ref": str(post_doc["_id"]),
        "search_run_id": post_doc.get("search_run_id"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Search — POST /api/search
# ─────────────────────────────────────────────────────────────────────────────

def run_search(run_id: str, query: str, limit: int = 10, provider: str = "apify") -> Dict[str, Any]:
    """Execute one agent search run. Updates `search_history` live."""
    db = get_sync_db()
    if db is None:
        return {"status": "error", "error": "Database unavailable"}

    provider = provider if provider in _PROVIDERS else "apify"

    def update_history(**fields):
        db.search_history.update_one({"run_id": run_id}, {"$set": {**fields, "updated_at": utcnow()}})

    def should_abort():
        return is_run_cancelled(run_id, db)

    def cancelled():
        mark_run_cancelled(run_id, db)
        return {"status": "cancelled", "message": "Search cancelled by user",
                "pages_found": 0, "pages_stored": 0}

    update_history(phase="searching", message="Searching Facebook Pages...")
    intent = parse_query(query, limit)
    update_history(intent=intent)

    if not intent["keyword"]:
        update_history(status="error", error="The search query is empty — type e.g. 'property dealers in jaipur'")
        return {"status": "error", "error": "Empty search query"}

    connector = get_connector(provider)
    if not connector.has_token():
        if provider == "brightdata":
            msg = ("BRIGHTDATA_API_KEY is not set in .env — "
                   "add it from https://brightdata.com/cp/setting/users and restart")
        else:
            msg = "APIFY_API_TOKEN is not set in .env — add it and restart"
        update_history(status="error", error=msg)
        return {"status": "error", "error": msg}

    try:
        locations = [intent["city"]] if intent.get("city") else None
        items = connector.scrape_facebook_pages(intent["keyword"], intent["limit"],
                                                locations=locations,
                                                should_abort=should_abort)
    except (ScrapeError, ApifyError, BrightDataError) as e:
        if isinstance(e, ScrapeError) and e.error_type == "CANCELLED":
            logger.info(f"[Agent] Search '{query}' cancelled by user")
            update_history(status="cancelled", phase="cancelled",
                           message="Search cancelled by user", completed_at=utcnow())
            return cancelled()
        if isinstance(e, ScrapeError):
            # classified failure → persist the structured error object
            logger.info(f"[Agent] Search failed for '{query}' errorType={e.error_type} "
                        f"runId={e.error.get('runId')} datasetId={e.error.get('datasetId')}")
            update_history(status="error", error=e.error["message"], error_meta=e.error)
            return {"status": "error", **e.error}
        logger.info(f"[Agent] Search failed for '{query}': {e}")
        update_history(status="error", error=str(e))
        return {"status": "error", "error": str(e)}
    except Exception as e:
        logger.error(f"[Agent] Search failed for '{query}': {e}")
        update_history(status="error", error=f"Search failed: {e}")
        return {"status": "error", "error": f"Search failed: {e}"}

    if should_abort():
        update_history(status="cancelled", phase="cancelled",
                       message="Search cancelled by user", completed_at=utcnow())
        return cancelled()

    pages_found = len(items)
    scrape_info = getattr(connector, "last_call", None) or {}
    logger.info(f"[Agent] Search OK '{query}' raw_items={pages_found} "
                f"last_call={json.dumps(scrape_info, default=str)}")
    update_history(scrape_info=scrape_info)
    if pages_found == 0:
        message = "No Facebook pages were found for this search keyword."
        logger.info(f"[Agent] Search '{query}' returned 0 items — status=partial")
        update_history(
            status="partial",
            pages_found=0,
            pages_stored=0,
            message=message,
            completed_at=utcnow(),
        )
        return {"status": "partial", "pages_found": 0, "pages_stored": 0, "message": message}

    # normalize + store (dedupe within THIS run only — a page found again in
    # a later search is a fresh copy, never re-assigned to the new run)
    stored = 0
    for item in items:
        if should_abort():
            update_history(status="cancelled", phase="cancelled",
                           message="Search cancelled by user", completed_at=utcnow())
            return cancelled()
        doc = map_page_item(item, run_id, intent["keyword"])
        if not doc:
            continue
        doc["provider"] = provider
        page_doc = db.facebook_pages.find_one(
            {"facebook_url": doc["facebook_url"], "search_run_id": run_id})
        if page_doc:
            merged = {k: v for k, v in doc.items() if v is not None}
            merged["search_run_id"] = run_id
            db.facebook_pages.update_one({"_id": page_doc["_id"]},
                                         {"$set": {**merged, "updated_at": utcnow()}})
        else:
            db.facebook_pages.insert_one({**doc, "created_at": utcnow(), "updated_at": utcnow()})
        stored += 1

    update_history(status="running", phase="stored", pages_found=pages_found,
                   pages_stored=stored, message=f"Pages Found: {stored}", provider=provider)

    # best-effort detail enrichment (verified / intro / missing contacts)
    urls = [p["facebook_url"] for p in db.facebook_pages.find({"search_run_id": run_id}).limit(30)]
    if urls:
        update_history(phase="enriching", message="Enriching page details...")
        try:
            detail_items = connector.scrape_facebook_pages_by_urls(urls,
                                                                   should_abort=should_abort)
            enriched = 0
            for item in detail_items:
                if not isinstance(item, dict) or item.get("error"):
                    continue
                item_url = _item_url(item)
                if not item_url:
                    continue
                page_doc = db.facebook_pages.find_one(
                    {"facebook_url": item_url, "search_run_id": run_id})
                if not page_doc:
                    continue
                updates = merge_page_details(page_doc, item)
                if updates:
                    db.facebook_pages.update_one({"_id": page_doc["_id"]},
                                                 {"$set": {**updates, "updated_at": utcnow()}})
                    enriched += 1
            logger.info(f"[Agent] Enriched {enriched}/{len(urls)} pages")
        except ScrapeError as e:
            if e.error_type == "CANCELLED":
                update_history(status="cancelled", phase="cancelled",
                               message="Search cancelled by user", completed_at=utcnow())
                return cancelled()
            logger.warning(f"[Agent] Page enrichment skipped: {e}")
        except Exception as e:
            logger.warning(f"[Agent] Page enrichment skipped: {e}")

    if should_abort():
        update_history(status="cancelled", phase="cancelled",
                       message="Search cancelled by user", completed_at=utcnow())
        return cancelled()

    update_history(status="completed", phase="completed", message="Completed",
                   completed_at=utcnow())
    logger.info(f"[Agent] Search '{query}' completed — stored {stored}/{pages_found} pages")

    # No clicks needed: automatically scrape posts (and top comments) for every
    # page of this run, so the UI shows live post/comment counts by itself.
    try:
        collect_run_posts(run_id, max_posts=20, auto_comments=3)
    except Exception as e:
        logger.warning(f"[Agent] Auto-collection for run {run_id} failed: {e}")

    return {"status": "completed", "pages_found": pages_found, "pages_stored": stored}


# ─────────────────────────────────────────────────────────────────────────────
# Posts — POST /api/pages/{id}/posts (one selected page only)
# ─────────────────────────────────────────────────────────────────────────────

def collect_page_posts(page_id: str, max_posts: int = 20,
                       run_id: Optional[str] = None) -> Dict[str, Any]:
    db = get_sync_db()
    if db is None:
        return {"status": "error", "error": "Database unavailable"}
    from bson import ObjectId
    page = db.facebook_pages.find_one({"_id": ObjectId(page_id)})
    if not page:
        raise NotFoundError("Page not found")
    if page.get("posts_status") == "running" and not _stale(page, "posts_started_at"):
        return {"status": "running", "message": "Posts collection already in progress"}

    run_id = run_id or page.get("search_run_id")
    if run_id and is_run_cancelled(run_id, db):
        mark_run_cancelled(run_id, db)
        return {"status": "cancelled", "message": "Search cancelled by user"}

    def should_abort():
        return bool(run_id and is_run_cancelled(run_id, db))

    db.facebook_pages.update_one({"_id": page["_id"]}, {"$set": {
        "posts_status": "running", "posts_error": None,
        "posts_started_at": utcnow(), "updated_at": utcnow()}})

    connector = get_connector(page.get("provider") or "apify")
    try:
        items = connector.scrape_facebook_posts([page["facebook_url"]],
                                                posts_per_page=max_posts,
                                                should_abort=should_abort)
    except (ScrapeError, ApifyError, BrightDataError) as e:
        if isinstance(e, ScrapeError) and e.error_type == "CANCELLED":
            if run_id:
                mark_run_cancelled(run_id, db)
            db.facebook_pages.update_one({"_id": page["_id"]}, {"$set": {
                "posts_status": "cancelled", "posts_error": "Search cancelled by user",
                "updated_at": utcnow()}})
            return {"status": "cancelled", "message": "Search cancelled by user"}
        if isinstance(e, ScrapeError):
            logger.info(f"[Agent] Posts failed for page {page_id} errorType={e.error_type} "
                        f"runId={e.error.get('runId')} datasetId={e.error.get('datasetId')}")
            db.facebook_pages.update_one({"_id": page["_id"]}, {"$set": {
                "posts_status": "error", "posts_error": e.error["message"],
                "posts_error_meta": e.error, "updated_at": utcnow()}})
            return {"status": "error", **e.error}
        logger.info(f"[Agent] Posts failed for page {page_id}: {e}")
        db.facebook_pages.update_one({"_id": page["_id"]}, {"$set": {
            "posts_status": "error", "posts_error": str(e), "updated_at": utcnow()}})
        return {"status": "error", "error": str(e)}

    received = len(items)
    stored = 0
    for item in items:
        doc = map_post_item(item, page)
        if not doc:
            continue
        # per-run dedup: the same post on a page found again in a later run
        # belongs to THAT run's page doc, not the older one
        existing = db.facebook_posts.find_one({"post_url": doc["post_url"], "page_ref": page_id})
        if existing:
            db.facebook_posts.update_one({"_id": existing["_id"]}, {"$set": {
                **{k: v for k, v in doc.items() if v is not None},
                "comments_status": existing.get("comments_status") or "not_started",
                "updated_at": utcnow(),
            }})
        else:
            db.facebook_posts.insert_one({**doc, "created_at": utcnow(), "updated_at": utcnow()})
        stored += 1
        # keep the page's live post count current while collecting
        db.facebook_pages.update_one({"_id": page["_id"]}, {
            "$set": {"posts_count": stored, "updated_at": utcnow()}})

    logger.info(f"[Agent] Posts received: {received} | stored: {stored}")

    # post statistics for this page — computed from the stored posts only
    posts = list(db.facebook_posts.find({"page_ref": page_id}))
    stats = _compute_page_stats(posts)

    if stored == 0:
        status = "empty"
        message = "No posts returned for this page"
    else:
        status = "completed"
        message = f"{stored} posts analyzed · {stats['qualifying_posts_count']} qualifying"
    db.facebook_pages.update_one({"_id": page["_id"]}, {"$set": {
        "posts_status": status, "posts_count": stored, "posts_skipped": 0,
        **stats,
        "posts_error": None if stored else message,
        "posts_collected_at": utcnow(), "updated_at": utcnow()}})
    logger.info(f"[Agent] Posts done for page {page_id}: status={status} stored={stored} "
                f"stats={json.dumps(stats, default=str)}")
    return {"status": status, "posts_count": stored, **stats, "message": message}


# ─────────────────────────────────────────────────────────────────────────────
# Comments + AI analysis — POST /api/posts/{id}/comments
# ─────────────────────────────────────────────────────────────────────────────

def collect_post_comments(post_id: str, max_comments: int = 200,
                          run_id: Optional[str] = None) -> Dict[str, Any]:
    db = get_sync_db()
    if db is None:
        return {"status": "error", "error": "Database unavailable"}
    from bson import ObjectId
    post = db.facebook_posts.find_one({"_id": ObjectId(post_id)})
    if not post:
        raise NotFoundError("Post not found")
    if post.get("comments_status") == "running" and not _stale(post, "comments_started_at"):
        return {"status": "running", "message": "Comments collection already in progress"}

    run_id = run_id or post.get("search_run_id")
    if run_id and is_run_cancelled(run_id, db):
        mark_run_cancelled(run_id, db)
        return {"status": "cancelled", "message": "Search cancelled by user"}

    def should_abort():
        return bool(run_id and is_run_cancelled(run_id, db))

    # only posts with at least MIN_COMMENTS (Facebook-reported total) are
    # worth the expensive comment scrape — everything else is skipped
    total = post.get("total_comment_count")
    if total is None:
        total = post.get("comments_count")
    if total is not None and total < _MIN_COMMENTS:
        message = (f"Not scraped — post has {total} comments (needs ≥ {_MIN_COMMENTS})")
        db.facebook_posts.update_one({"_id": post["_id"]}, {"$set": {
            "comments_status": "skipped", "comments_error": message,
            "updated_at": utcnow()}})
        return {"status": "skipped", "message": message}

    db.facebook_posts.update_one({"_id": post["_id"]}, {"$set": {
        "comments_status": "running", "comments_error": None,
        "comments_started_at": utcnow(), "updated_at": utcnow()}})

    connector = get_connector(post.get("provider") or "apify")
    try:
        items = connector.scrape_facebook_comments([post["post_url"]],
                                                   comments_per_post=max_comments,
                                                   should_abort=should_abort)
    except (ScrapeError, ApifyError, BrightDataError) as e:
        if isinstance(e, ScrapeError) and e.error_type == "CANCELLED":
            if run_id:
                mark_run_cancelled(run_id, db)
            db.facebook_posts.update_one({"_id": post["_id"]}, {"$set": {
                "comments_status": "cancelled",
                "comments_error": "Search cancelled by user", "updated_at": utcnow()}})
            return {"status": "cancelled", "message": "Search cancelled by user"}
        if isinstance(e, ScrapeError):
            logger.info(f"[Agent] Comments failed for post {post_id} errorType={e.error_type} "
                        f"runId={e.error.get('runId')} datasetId={e.error.get('datasetId')}")
            db.facebook_posts.update_one({"_id": post["_id"]}, {"$set": {
                "comments_status": "error", "comments_error": e.error["message"],
                "comments_error_meta": e.error, "updated_at": utcnow()}})
            return {"status": "error", **e.error}
        logger.info(f"[Agent] Comments failed for post {post_id}: {e}")
        db.facebook_posts.update_one({"_id": post["_id"]}, {"$set": {
            "comments_status": "error", "comments_error": str(e), "updated_at": utcnow()}})
        return {"status": "error", "error": str(e)}

    stored = 0
    contact_stored = 0
    from app.pipeline.comment_ai import has_contact_info
    for item in items:
        doc = map_comment_item(item, post)
        if not doc:
            continue
        # every scraped comment is kept; comments carrying a phone number or
        # email are flagged (has_contact) so they surface at the top of the
        # comments view and can be AI-analyzed as leads
        doc["has_contact"] = bool(has_contact_info(doc.get("text")))
        if doc["has_contact"]:
            contact_stored += 1
        # per-run dedup: a comment on a post collected again in a later run
        # belongs to THAT run's post doc
        existing = db.facebook_comments.find_one({"comment_url": doc["comment_url"], "post_ref": post_id})
        if existing:
            db.facebook_comments.update_one({"_id": existing["_id"]}, {"$set": {
                **{k: v for k, v in doc.items() if v is not None}, "updated_at": utcnow()}})
        else:
            db.facebook_comments.insert_one({**doc, "created_at": utcnow()})
        stored += 1
        # keep the post's live scraped-comment count current while collecting
        # (total_comment_count stays as Facebook reported it — never overwritten)
        db.facebook_posts.update_one({"_id": post["_id"]}, {
            "$set": {"scraped_comment_count": stored, "updated_at": utcnow()}})

    if stored == 0:
        status = "empty"
        message = "No comments were returned for this post"
    else:
        status = "completed"
        message = (f"{stored} comments collected "
                   f"({contact_stored} with phone/email)")

    analysis_error = None
    # mark completed as soon as comments are stored so the UI can show them
    # immediately — AI analysis below only refines the data further
    db.facebook_posts.update_one({"_id": post["_id"]}, {"$set": {
        "comments_status": status, "scraped_comment_count": stored,
        "comments_error": None if stored else message,
        "comments_collected_at": utcnow(), "updated_at": utcnow()}})

    if stored:
        try:
            from app.pipeline.comment_ai import analyze_comments_for_post
            analyze_comments_for_post(str(post["_id"]))
        except Exception as e:
            analysis_error = str(e)
            logger.warning(f"[Agent] AI comment analysis failed: {e}")
            db.facebook_posts.update_one({"_id": post["_id"]}, {"$set": {
                "comments_error": str(e), "updated_at": utcnow()}})

    logger.info(f"[Agent] Comments done for post {post_id}: status={status} stored={stored} "
                f"analysis_error={analysis_error}")
    return {"status": status, "comments_count": stored, "message": message}


# ─────────────────────────────────────────────────────────────────────────────
# Bulk auto-pipeline — one background job for a whole search run
# ─────────────────────────────────────────────────────────────────────────────

def collect_run_posts(run_id: str, max_posts: int = 20, auto_comments: int = 3) -> Dict[str, Any]:
    """Automatically collect posts for EVERY page of a run (no clicks), then
    automatically collect + analyze comments for the top `auto_comments`
    posts of each page (by comment count). Progress is written live to
    search_history."""
    db = get_sync_db()
    if db is None:
        return {"status": "error", "error": "Database unavailable"}

    if is_run_cancelled(run_id, db):
        mark_run_cancelled(run_id, db)
        return {"status": "cancelled", "message": "Search cancelled by user"}

    pages = list(db.facebook_pages.find({"search_run_id": run_id}))
    if not pages:
        return {"status": "empty", "message": "No pages in this run"}

    def progress(message: str):
        db.search_history.update_one({"run_id": run_id}, {
            "$set": {"message": message, "phase": "collecting", "updated_at": utcnow()}})

    total = len(pages)
    progress(f"Auto-collection started — {total} page(s) to process")
    for idx, page in enumerate(pages, start=1):
        if is_run_cancelled(run_id, db):
            mark_run_cancelled(run_id, db)
            progress("Search cancelled by user")
            return {"status": "cancelled", "message": "Search cancelled by user"}
        page_id = str(page["_id"])
        progress(f"Collecting posts — page {idx}/{total}")
        collect_page_posts(page_id, max_posts, run_id=run_id)

        if auto_comments > 0:
            # only qualifying posts get the expensive comment scrape
            top = list(db.facebook_posts.find(
                {"page_ref": page_id, "is_qualifying": True})
                .sort("total_comment_count", -1).limit(auto_comments))
            for jdx, post in enumerate(top, start=1):
                if is_run_cancelled(run_id, db):
                    mark_run_cancelled(run_id, db)
                    progress("Search cancelled by user")
                    return {"status": "cancelled", "message": "Search cancelled by user"}
                if post.get("comments_status") not in ("completed", "running"):
                    progress(f"Collecting comments — post {jdx}/{len(top)} on page {idx}/{total}")
                    collect_post_comments(str(post["_id"]), 200, run_id=run_id)

    db.search_history.update_one({"run_id": run_id}, {
        "$set": {"message": "Auto-collection finished", "phase": "completed",
                 "updated_at": utcnow()}})
    return {"status": "completed", "pages": total}
