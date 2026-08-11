"""
Bright Data Connector — alternative Facebook data source (Facebook Scraper API).

Same interface as ApifyConnector, so app/agent/search.py can switch provider
per search run. Every method returns items in the SAME shape the Apify actors
produce; mapping to the data model stays in app/agent/search.py.

Methods:
  1. scrape_facebook_pages            → Bright Data Discover API (AI-ranked
                                        Google search for `site:facebook.com`
                                        pages — Facebook has no public page
                                        keyword search) → page URLs
  2. scrape_facebook_pages_by_urls    → /scrape (sync, <=20 URLs) on the
                                        pages/profiles dataset → full details
  3. scrape_facebook_posts            → /trigger (async) posts dataset
  4. scrape_facebook_comments         → /trigger (async) comments dataset

Reference (verified 2026):
  - async trigger:   POST /datasets/v3/trigger?dataset_id=...&format=json
  - sync scrape:     POST /datasets/v3/scrape?dataset_id=...&format=json
  - progress:        GET  /datasets/v3/progress/{snapshot_id} → {"status": ...}
  - download:        GET  /datasets/v3/snapshot/{snapshot_id}?format=json
"""
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

API_BASE = "https://api.brightdata.com"
_SCRAPE_URL = f"{API_BASE}/datasets/v3/scrape"
_TRIGGER_URL = f"{API_BASE}/datasets/v3/trigger"
_PROGRESS_URL = f"{API_BASE}/datasets/v3/progress/{{sid}}"
_SNAPSHOT_URL = f"{API_BASE}/datasets/v3/snapshot/{{sid}}"
_DISCOVER_URL = f"{API_BASE}/discover"

_POLL_INTERVAL = 10          # seconds between progress checks
_COLLECTION_TIMEOUT = 900    # give up after 15 minutes
_SYNC_TIMEOUT = 120          # httpx timeout for the sync /scrape call

# Facebook page/profile URL shapes we accept as lead targets.
# Exclude groups, marketplace, events, posts, videos, reels, watch, shares.
_FB_PAGE_RE = re.compile(
    r"^(?:https?://)?(?:www\.|m\.|web\.)?facebook\.com/"
    r"(?!(?:groups|marketplace|events|people/profile|watch|reel|share|photo|video|posts|story|messages)/?)"
    r"([^/?#]+)",
    re.IGNORECASE,
)


class BrightDataError(RuntimeError):
    """Raised with a user-friendly message when Bright Data cannot deliver data."""


def _explain_http_status(status: int, body: str) -> Optional[str]:
    if status == 401:
        return ("Bright Data rejected the API key — check BRIGHTDATA_API_KEY in .env "
                "(https://brightdata.com/cp/setting/users)")
    if status == 402 or status == 403:
        return ("Bright Data account has no credit / no access to the Facebook datasets — "
                "top up at https://brightdata.com/cp and retry.")
    if status == 429:
        return ("Bright Data rate limit reached — wait a few minutes and retry.")
    return None


# ─────────────────────────────────────────────────────────────────────────
# NORMALIZATION — Bright Data rows → Apify-shaped items (so app/agent/search.py
# mappers work unchanged)
# ─────────────────────────────────────────────────────────────────────────

def _norm_page(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    url = str(row.get("url") or "").strip()
    if not url:
        return None
    name = str(row.get("page_name") or row.get("name") or "").strip()
    info = str(row.get("summary_text") or row.get("description") or "").strip() or None
    contact = row.get("contact_and_basic_info") or {}
    if isinstance(contact, dict):
        cinfo = contact.get("contact_info") or {}
        if isinstance(cinfo, dict):
            info = info or str(cinfo.get("summary") or "").strip() or None
    addr = row.get("address")
    if isinstance(addr, dict):
        addr = addr.get("formatted") or addr.get("address") or None
    phones = row.get("phones") or []
    emails = row.get("emails") or []
    websites = row.get("websites") or []
    if not phones and isinstance(contact, dict):
        phones = contact.get("phones") or phones
    if not emails and isinstance(contact, dict):
        emails = contact.get("emails") or emails
    if not websites and isinstance(contact, dict):
        websites = (contact.get("websites") or websites)
    categories = [row.get("primary_category")] if row.get("primary_category") else None
    return {
        "facebookUrl": url,
        "pageName": name or None,
        "title": name or None,
        "pageId": str(row.get("id") or "").strip() or None,
        "categories": categories,
        "followers": row.get("followers"),
        "likes": row.get("likes"),
        "phone": str(_first(phones) or "").strip() or None,
        "email": str(_first(emails) or "").strip() or None,
        "address": str(addr or "").strip() or None,
        "website": str(_first(websites) or "").strip() or None,
        "profilePictureUrl": str(row.get("logo") or "").strip() or None,
        "verified": row.get("is_verified") if isinstance(row.get("is_verified"), bool) else None,
        "info": [info] if info else None,
    }


def _norm_post(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    post_url = str(row.get("url") or row.get("post_url") or "").strip()
    if not post_url:
        return None
    likes = row.get("num_likes_type")
    if isinstance(likes, dict):
        likes = likes.get("num")
    images = []
    for att in row.get("attachments") or []:
        if not isinstance(att, dict):
            continue
        url = str(att.get("attachment_url") or att.get("url") or "").strip()
        thumb = str(att.get("thumbnail_url") or "").strip()
        typ = str(att.get("type") or "").lower()
        if url and "video" not in typ and url not in images:
            images.append(url)
        elif thumb and "video" not in typ and thumb not in images:
            images.append(thumb)
    return {
        "id": str(row.get("post_id") or "").strip() or None,
        "url": post_url,
        "pageUrl": str(row.get("page_url") or row.get("user_url") or "").strip(),
        "text": str(row.get("content") or row.get("text") or "").strip() or None,
        "date": str(row.get("date_posted") or row.get("date") or "").strip() or None,
        "likesCount": likes if isinstance(likes, (int, float)) else row.get("likes"),
        "commentsCount": row.get("num_comments"),
        "sharesCount": row.get("num_shares"),
        "imageUrls": images,
    }


def _norm_comment(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    user = row.get("user")
    if isinstance(user, dict):
        author = str(user.get("name") or user.get("url") or "").strip()
        author_url = str(user.get("url") or "").strip()
    else:
        author = str(row.get("user_name") or row.get("username") or row.get("author_name") or "").strip()
        author_url = str(row.get("user_url") or row.get("author_url") or "").strip()
    comment_url = str(row.get("url") or row.get("comment_url") or "").strip()
    post_url = str(row.get("post_url") or row.get("postUrl") or "").strip()
    likes = row.get("num_likes")
    if not isinstance(likes, (int, float)):
        likes = row.get("num_reactions") or row.get("reactions") or None
    return {
        "id": str(row.get("comment_id") or row.get("id") or "").strip() or None,
        "postUrl": post_url,
        "url": comment_url,
        "authorName": author or None,
        "authorUrl": author_url or None,
        "text": str(row.get("comment_text") or row.get("text") or row.get("content") or "").strip() or None,
        "date": str(row.get("date_posted") or row.get("date") or row.get("created_at") or "").strip() or None,
        "likesCount": likes if isinstance(likes, (int, float)) else None,
    }


def _first(*values: Any) -> Any:
    for v in values:
        if v not in (None, "", [], {}):
            return v
    return None


class BrightDataConnector:
    def __init__(self):
        self.api_key = (
            os.environ.get("BRIGHTDATA_API_KEY", "").strip()
            or settings.brightdata_api_key
        )
        self.dataset_pages = settings.brightdata_dataset_pages
        self.dataset_posts = settings.brightdata_dataset_posts
        self.dataset_comments = settings.brightdata_dataset_comments
        self._http: Optional[httpx.Client] = None

    def has_token(self) -> bool:
        return bool(self.api_key)

    # ─────────────────────────────────────────────────────────────────────────
    # INTERNAL — http client + snapshot lifecycle
    # ─────────────────────────────────────────────────────────────────────────

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(
                timeout=_SYNC_TIMEOUT,
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"},
            )
        return self._http

    def _require_key(self):
        if not self.api_key:
            raise BrightDataError(
                "BRIGHTDATA_API_KEY is not set in .env — "
                "add it from https://brightdata.com/cp/setting/users"
            )

    def _trigger(self, dataset_id: str, inputs: List[Dict[str, Any]],
                 limit_per_input: Optional[int] = None) -> str:
        """Start an async collection; returns snapshot_id."""
        params = {"dataset_id": dataset_id, "format": "json"}
        if limit_per_input:
            params["limit_per_input"] = limit_per_input
        try:
            resp = self._client().post(_TRIGGER_URL, params=params, json=inputs)
        except httpx.HTTPError as e:
            raise BrightDataError(f"Bright Data unreachable: {e}")
        if resp.status_code != 200:
            raise BrightDataError(_explain_http_status(resp.status_code, resp.text)
                                  or f"Bright Data trigger failed ({resp.status_code}): {resp.text[:300]}")
        return resp.json()["snapshot_id"]

    def _wait_and_download(self, snapshot_id: str) -> List[Dict[str, Any]]:
        """Poll until ready/failed, then download the rows."""
        started = time.time()
        while True:
            if time.time() - started > _COLLECTION_TIMEOUT:
                raise BrightDataError("Bright Data collection timed out — retry in a few minutes.")
            try:
                resp = self._client().get(_PROGRESS_URL.format(sid=snapshot_id))
            except httpx.HTTPError as e:
                raise BrightDataError(f"Bright Data progress check failed: {e}")
            if resp.status_code != 200:
                raise BrightDataError(_explain_http_status(resp.status_code, resp.text)
                                      or f"Bright Data progress failed ({resp.status_code})")
            body = resp.json()
            status = body.get("status")
            if status == "ready":
                break
            if status == "failed":
                raise BrightDataError("Bright Data collection failed — check the input URLs "
                                      "and your account at https://brightdata.com/cp")
            time.sleep(_POLL_INTERVAL)

        try:
            down = self._client().get(_SNAPSHOT_URL.format(sid=snapshot_id), params={"format": "json"})
        except httpx.HTTPError as e:
            raise BrightDataError(f"Bright Data download failed: {e}")
        if down.status_code != 200:
            raise BrightDataError(_explain_http_status(down.status_code, down.text)
                                  or f"Bright Data download failed ({down.status_code})")
        rows = down.json()
        if not isinstance(rows, list):
            rows = [rows] if isinstance(rows, dict) else []
        return [r for r in rows if isinstance(r, dict)]

    # ─────────────────────────────────────────────────────────────────────────
    # 1. PAGE SEARCH — Discover API (site:facebook.com) → page URLs
    # ─────────────────────────────────────────────────────────────────────────

    def scrape_facebook_pages(self, keyword: str, limit: int = 10,
                              locations: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Bright Data has no Facebook page keyword-search endpoint, so we use
        the Discover API (AI-ranked Google search): `site:facebook.com
        <keyword> <city>` → keep real page/profile URLs → lightweight items.
        Full details (phone/email/…) come from scrape_facebook_pages_by_urls
        during enrichment.
        """
        self._require_key()
        city = (locations or [""])[0].strip() if locations else ""
        query = " ".join(x for x in ["site:facebook.com", keyword, city] if x)
        logger.info(f"[BrightData] Discover pages: '{query}' (limit={limit})")
        try:
            resp = self._client().post(_DISCOVER_URL, json={
                "query": query,
                "num_results": max(limit * 3, 10),
                "intent": "Facebook business pages or profiles offering this service, "
                          "excluding marketplace, groups, events, jobs and personal memes",
                "format": "json",
            })
        except httpx.HTTPError as e:
            raise BrightDataError(f"Bright Data unreachable: {e}")
        if resp.status_code != 200:
            raise BrightDataError(_explain_http_status(resp.status_code, resp.text)
                                  or f"Bright Data Discover failed ({resp.status_code}): {resp.text[:300]}")

        items: List[Dict[str, Any]] = []
        for row in resp.json() or []:
            if not isinstance(row, dict):
                continue
            link = str(row.get("link") or row.get("url") or "").strip()
            m = _FB_PAGE_RE.match(link)
            if not m:
                continue
            name = (str(row.get("title") or "").strip() or m.group(1)).replace("- Facebook", "").strip()
            items.append({
                "facebookUrl": link,
                "pageName": name,
                "title": f"{name} | {city}" if city else name,
                "pageId": m.group(1),
                "info": [str(row.get("description") or "")] if row.get("description") else None,
                "categories": ["Page"],
                "followers": None, "likes": None,
                "phone": None, "email": None, "address": None, "website": None,
                "profilePictureUrl": None,
            })
            if len(items) >= limit:
                break
        logger.info(f"[BrightData] Discover → {len(items)} page URL(s)")
        return items

    # ─────────────────────────────────────────────────────────────────────────
    # 2. PAGES/PROFILES BY URL — sync /scrape (max 20 URLs per call)
    # ─────────────────────────────────────────────────────────────────────────

    def scrape_facebook_pages_by_urls(self, page_urls: List[str]) -> List[Dict[str, Any]]:
        if not page_urls:
            return []
        self._require_key()
        out: List[Dict[str, Any]] = []
        for chunk in [page_urls[i:i + 20] for i in range(0, len(page_urls), 20)]:
            logger.info(f"[BrightData] Page details for {len(chunk)} URL(s)")
            try:
                resp = self._client().post(
                    _SCRAPE_URL,
                    params={"dataset_id": self.dataset_pages, "format": "json"},
                    json={"input": [{"url": u} for u in chunk]},
                )
            except httpx.HTTPError as e:
                raise BrightDataError(f"Bright Data unreachable: {e}")
            if resp.status_code not in (200, 201, 202):
                raise BrightDataError(_explain_http_status(resp.status_code, resp.text)
                                      or f"Bright Data pages scrape failed ({resp.status_code}): {resp.text[:300]}")
            if resp.status_code == 202:
                # still processing — wait then re-download via snapshot id
                sid = resp.json().get("snapshot_id")
                if sid:
                    out.extend(_norm_page(r) for r in self._wait_and_download(sid) if _norm_page(r))
                continue
            rows = resp.json()
            if not isinstance(rows, list):
                rows = [rows] if isinstance(rows, dict) else []
            out.extend(r for r in (_norm_page(x) for x in rows) if r)
        return out

    # ─────────────────────────────────────────────────────────────────────────
    # 3. PAGE POSTS — async /trigger on the posts dataset
    # ─────────────────────────────────────────────────────────────────────────

    def scrape_facebook_posts(self, page_urls: List[str], posts_per_page: int = 20) -> List[Dict[str, Any]]:
        if not page_urls:
            return []
        self._require_key()
        logger.info(f"[BrightData] Posts for {len(page_urls)} page(s) (max {posts_per_page} each)")
        sid = self._trigger(self.dataset_posts, [{"url": u} for u in page_urls],
                            limit_per_input=max(posts_per_page, 1))
        return [p for p in (_norm_post(r) for r in self._wait_and_download(sid)) if p]

    # ─────────────────────────────────────────────────────────────────────────
    # 4. POST COMMENTS — async /trigger on the comments dataset
    # ─────────────────────────────────────────────────────────────────────────

    def scrape_facebook_comments(self, post_urls: List[str], comments_per_post: int = 50) -> List[Dict[str, Any]]:
        if not post_urls:
            return []
        self._require_key()
        logger.info(f"[BrightData] Comments for {len(post_urls)} post(s) (max {comments_per_post} each)")
        sid = self._trigger(self.dataset_comments, [{"url": u} for u in post_urls],
                            limit_per_input=max(comments_per_post, 1))
        return [c for c in (_norm_comment(r) for r in self._wait_and_download(sid)) if c]
