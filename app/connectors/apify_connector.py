"""
Apify Connector — Facebook data source (4 actors).

  1. apify/facebook-search-scraper      → keyword search → pages
  2. apify/facebook-pages-scraper       → page details by URL
  3. apify/facebook-posts-scraper       → posts of a page
  4. apify/facebook-comments-scraper    → comments of a post

Every actor call is wrapped so failures are CLASSIFIED, not guessed:
  - every request + response is logged (debug = raw body, info = summary)
  - an empty result is reported as NO_RESULTS — never assumed to be a block
  - "Facebook may have blocked" is only said when the run status, statusMessage
    or an HTTP 403/429 error actually indicates blocking / rate limiting

`ScrapeError` carries a structured `.error` object for the API:
    {"success": false, "errorType": "...", "message": "...", "keyword": ...,
     "actorId": ..., "runId": ..., "datasetId": ..., "itemsReturned": ...}

Raw actor items are returned untouched; mapping happens in app/agent/search.py.
"""
import json
import logging
import os
import re
import time
from datetime import timedelta
from typing import Any, Dict, List, Optional

from apify_client.errors import ApifyApiError, ApifyClientError

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Minutes an Apify actor run may take before the client stops waiting and
# the retry chain kicks in. Without this, a blocked Facebook actor can hang
# for hours. run_timeout also aborts the run server-side so it does not
# keep consuming compute.
_RUN_TIMEOUT_MIN = 8


def _run_wait_and_timeout() -> Dict[str, timedelta]:
    return {
        "run_timeout": timedelta(minutes=_RUN_TIMEOUT_MIN),
        "wait_duration": timedelta(minutes=_RUN_TIMEOUT_MIN),
    }


_BILLING_HINTS = (
    "exceed your remaining usage",
    "upgrade to a paid plan",
    "insufficient credits",
    "billing",
    "payment",
)

# Evidence that Facebook (or Apify's proxy) blocked/limited the request.
# Only these trigger the "may have blocked" wording — anything else gets a
# factual NO_RESULTS / ACTOR_FAILED message.
_BLOCK_HINTS = (
    "block", "blocked", "captcha", "rate limit", "too many requests",
    "access restriction", "restricted", "forbidden", "banned", "429",
)


class ApifyError(RuntimeError):
    """Plain user-facing message (missing token / billing)."""


class ScrapeError(RuntimeError):
    """A classified scrape failure carrying a structured `.error` object."""

    def __init__(self, error_type: str, message: str, *,
                 keyword: Optional[str] = None, actor_id: Optional[str] = None,
                 run_id: Optional[str] = None, dataset_id: Optional[str] = None,
                 items_returned: Optional[int] = None,
                 details: Any = None):
        super().__init__(message)
        self.error_type = error_type
        self.error = {
            "success": False,
            "errorType": error_type,
            "message": message,
            "keyword": keyword,
            "actorId": actor_id,
            "runId": run_id,
            "datasetId": dataset_id,
            "itemsReturned": items_returned,
            "details": details,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Classification helpers
# ─────────────────────────────────────────────────────────────────────────────

# apify_client 3.x returns Pydantic models with snake_case attributes;
# older versions returned camelCase dicts. Read either shape.
_FIELD_ALIASES = {
    "actorId": ("act_id", "actor_id"),
    "statusMessage": ("status_message",),
    "defaultDatasetId": ("default_dataset_id",),
}


def _camel_to_snake(key: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()


def _rget(run: Any, key: str, default: Any = None) -> Any:
    if isinstance(run, dict):
        return run.get(key, default)
    candidates = list(_FIELD_ALIASES.get(key, ())) + [_camel_to_snake(key), key]
    for name in candidates:
        if name and hasattr(run, name):
            return getattr(run, name)
    return default


def _log_request(label: str, actor_id: str, run_input: Dict[str, Any]):
    logger.debug(f"[Apify][REQ] {label} actor={actor_id} "
                 f"input={json.dumps(run_input, default=str)[:2000]}")


def _log_run(label: str, run: Any, note: str = ""):
    logger.info(
        f"[Apify][RESP] {label} actor={_rget(run, 'actorId')} "
        f"runId={_rget(run, 'id')} status={_rget(run, 'status')} "
        f"datasetId={_rget(run, 'defaultDatasetId')} "
        f"statusMessage={_rget(run, 'statusMessage')} {note}".strip())


def _blocking_hint(run: Any) -> Optional[str]:
    """Return the blocking evidence from a run, or None when there is none."""
    texts = [str(_rget(run, "statusMessage") or "")]
    stats = _rget(run, "stats")
    if stats:
        texts.append(str(_rget(stats, "requestErrors") or ""))
        texts.append(str(_rget(stats, "requestFailures") or ""))
    text = " ".join(t for t in texts if t).lower()
    found = [hint for hint in _BLOCK_HINTS if hint in text]
    # longest match = most specific evidence ("blocked" over "block")
    return max(found, key=len) if found else None


def _classify_run_status(run: Any, *, keyword: Optional[str] = None,
                         actor_id: Optional[str] = None) -> Optional[ScrapeError]:
    """Map the actor run's terminal status to a classified error."""
    status = str(_rget(run, "status") or "").upper()
    run_id = _rget(run, "id")
    dataset_id = _rget(run, "defaultDatasetId")
    if status == "FAILED":
        detail = _rget(run, "statusMessage") or "actor run failed"
        return ScrapeError("ACTOR_FAILED", f"Apify actor failed: {detail}",
                           keyword=keyword, actor_id=actor_id, run_id=run_id,
                           dataset_id=dataset_id, details=detail)
    if status in ("TIMED-OUT", "ABORTED"):
        return ScrapeError("ACTOR_TIMED_OUT",
                           f"Apify actor run {status.lower()} after {_RUN_TIMEOUT_MIN} minutes — retry",
                           keyword=keyword, actor_id=actor_id, run_id=run_id,
                           dataset_id=dataset_id, details=status)
    return None


# Evidence that Apify API refused the call itself (the run never started).
# "Facebook may have blocked" is ONLY said when a run/statusMessage/HTTP 403
# inside the scrape actually indicates Facebook blocking — an API-level 403
# means the account/actor access, credits or the token, NOT Facebook.
_ACCESS_HINTS = (
    "access to this actor", "you do not have access", "no access", "not accessible",
    "actor is private", "only for paid", "paid plan", "subscription",
    "upgrade your plan", "upgrade to", "free plan",
)


def _classify_api_error(exc: Exception, *, keyword: Optional[str] = None,
                        actor_id: Optional[str] = None) -> ScrapeError:
    """Map an apify-client exception (HTTP error, network error) to a class.

    API-level 403/429 are account/API problems (credits, actor access, rate
    limit) — never claimed to be a Facebook block.
    """
    status = getattr(exc, "status_code", None)
    message = getattr(exc, "message", None) or str(exc)
    raw = str(exc)[:2000]
    msg = str(message)
    if status == 400:
        return ScrapeError("INVALID_INPUT",
                           f"Invalid actor input (HTTP 400): {message}",
                           keyword=keyword, actor_id=actor_id, details=raw)
    if status == 401:
        return ScrapeError("API_ERROR",
                           "Apify API rejected the token (HTTP 401) — check APIFY_API_TOKEN",
                           keyword=keyword, actor_id=actor_id, details=raw)
    if status == 403:
        if any(hint in msg.lower() for hint in _ACCESS_HINTS) or not msg.strip():
            return ScrapeError(
                "ACCESS_DENIED",
                "Apify refused to run this actor (HTTP 403) — the account does not "
                "have access to it. This actor needs a paid Apify subscription or "
                "credits; upgrade at https://console.apify.com/billing, then retry.",
                keyword=keyword, actor_id=actor_id, details=raw)
        return ScrapeError(
            "API_ERROR",
            f"Apify API rejected the request (HTTP 403): {message}",
            keyword=keyword, actor_id=actor_id, details=raw)
    if status == 429:
        return ScrapeError("API_ERROR",
                           "Apify API is rate-limiting this account (HTTP 429). "
                           "Wait a minute and retry.",
                           keyword=keyword, actor_id=actor_id, details=raw)
    if status is not None and status >= 500:
        return ScrapeError("API_ERROR",
                           f"Apify API server error (HTTP {status}): {message}",
                           keyword=keyword, actor_id=actor_id, details=raw)
    if status is not None:
        return ScrapeError("API_ERROR",
                           f"Apify API error (HTTP {status}): {message}",
                           keyword=keyword, actor_id=actor_id, details=raw)
    return ScrapeError("NETWORK_ERROR",
                       f"Apify network/API error: {message}",
                       keyword=keyword, actor_id=actor_id, details=raw)


def _explain_error(exc: Exception) -> Optional[str]:
    msg = str(exc)
    if any(hint in msg.lower() for hint in _BILLING_HINTS):
        return ("Apify account credits are exhausted — posts/comments scraping needs "
                "paid actors. Upgrade at https://console.apify.com/billing, then retry.")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Connector
# ─────────────────────────────────────────────────────────────────────────────

class ApifyConnector:
    def __init__(self):
        self.token = (
            os.environ.get("APIFY_API_TOKEN", "").strip()
            or settings.apify_api_token
        )
        self._client: Optional[Any] = None
        # metadata of the most recent actor call (for API diagnostics):
        # {"actorId", "runId", "datasetId", "status", "itemsReturned"}
        self.last_call: Dict[str, Any] = {}

    def has_token(self) -> bool:
        return bool(self.token)

    def _get_client(self):
        if self._client is None:
            if not self.token:
                raise ApifyError(
                    "APIFY_API_TOKEN is not set in .env — "
                    "add it from https://apify.com/account/integrations"
                )
            from apify_client import ApifyClient
            self._client = ApifyClient(self.token)
        return self._client

    # ─────────────────────────────────────────────────────────────────────────
    # CORE — one actor run with full request/response diagnostics
    # ─────────────────────────────────────────────────────────────────────────

    def _call_actor(self, actor_id: str, run_input: Dict[str, Any], label: str,
                    attempts: int = 1) -> Any:
        """Run an actor; classify every failure. Logs request + response."""
        client = self._get_client()
        last_error: Optional[ScrapeError] = None
        for attempt in range(attempts):
            _log_request(label, actor_id, run_input)
            try:
                run = client.actor(actor_id).call(
                    run_input=run_input, content_type="application/json",
                    **_run_wait_and_timeout())
            except Exception as e:
                billing = _explain_error(e)
                if billing:
                    raise ApifyError(billing)
                if isinstance(e, ApifyApiError):
                    last_error = _classify_api_error(e, actor_id=actor_id)
                elif isinstance(e, ApifyClientError):
                    last_error = ScrapeError(
                        "NETWORK_ERROR", f"Apify network/API error: {e}",
                        actor_id=actor_id, details=str(e)[:2000])
                else:
                    last_error = ScrapeError(
                        "API_ERROR", f"Apify client error: {e}",
                        actor_id=actor_id, details=str(e)[:2000])
                logger.warning(f"[Apify][ERR] {label} attempt {attempt+1}/{attempts} "
                               f"errorType={last_error.error_type}: {last_error}")
                if attempt + 1 < attempts:
                    time.sleep(5)
                continue
            _log_run(label, run)
            self.last_call = {
                "actorId": _rget(run, "actorId") or actor_id,
                "runId": _rget(run, "id"),
                "datasetId": _rget(run, "defaultDatasetId"),
                "status": _rget(run, "status"),
            }
            return run
        assert last_error is not None
        raise last_error

    def _read_items(self, run: Any, *, actor_id: str,
                    keyword: Optional[str] = None) -> List[Dict[str, Any]]:
        """Read an actor run's dataset; classify retrieval failures."""
        dataset_id = _rget(run, "defaultDatasetId")
        run_id = _rget(run, "id")
        if not dataset_id:
            raise ScrapeError("DATASET_ERROR",
                              "Actor run produced no dataset — cannot read results",
                              keyword=keyword, actor_id=actor_id, run_id=run_id)
        try:
            items = list(self._get_client().dataset(dataset_id).iterate_items())
        except ApifyApiError as e:
            raise ScrapeError("DATASET_ERROR",
                              f"Dataset retrieval failed (HTTP {getattr(e, 'status_code', '?')}): "
                              f"{getattr(e, 'message', e)}",
                              keyword=keyword, actor_id=actor_id, run_id=run_id,
                              dataset_id=dataset_id, details=str(e)[:2000])
        except ApifyClientError as e:
            raise ScrapeError("DATASET_ERROR",
                              f"Dataset retrieval failed: {e}",
                              keyword=keyword, actor_id=actor_id, run_id=run_id,
                              dataset_id=dataset_id, details=str(e)[:2000])
        self.last_call.update({"datasetId": dataset_id, "itemsReturned": len(items)})
        logger.debug(f"[Apify][RESP] {actor_id} dataset={dataset_id} "
                     f"items={len(items)} raw_body_preview="
                     f"{json.dumps(items[0] if items else None, default=str)[:500]}")
        return items

    def _validated(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [i for i in items if isinstance(i, dict) and not i.get("error")]

    # ─────────────────────────────────────────────────────────────────────────
    # GENERIC ACTOR — for the multi-platform URL search (instagram/youtube/…)
    # Same call/classify/read chain as the Facebook methods above.
    # ─────────────────────────────────────────────────────────────────────────

    def scrape_actor(self, actor_id: str, run_input: Dict[str, Any],
                     label: str, keyword: Optional[str] = None) -> List[Dict[str, Any]]:
        """Run an arbitrary Apify actor and return its dataset items.

        Failures are classified exactly like the Facebook actors
        (BLOCKED / ACTOR_FAILED / NO_RESULTS / …) and raised as ScrapeError;
        the caller decides how to surface them.
        """
        self._get_client()
        logger.info(f"[Apify] Running actor {actor_id} ({label})")
        run = self._call_actor(actor_id, run_input, label, attempts=2)
        hint = _blocking_hint(run)
        if hint:
            raise ScrapeError(
                "BLOCKED", f"The platform may have blocked the request ({hint}).",
                keyword=keyword, actor_id=actor_id,
                run_id=_rget(run, "id"), dataset_id=_rget(run, "defaultDatasetId"),
                details=hint)
        classified = _classify_run_status(run, keyword=keyword, actor_id=actor_id)
        if classified:
            raise classified
        items = self._read_items(run, actor_id=actor_id, keyword=keyword)
        logger.info(f"[Apify] {label} actor → {len(items)} item(s)")
        return items

    # ─────────────────────────────────────────────────────────────────────────
    # 1. FACEBOOK SEARCH SCRAPER — keyword → pages
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _keyword_variants(keyword: str, max_variants: int = 3) -> List[str]:
        kws = [w for w in re.split(r"[,\s]+", (keyword or "").strip()) if w]
        if not kws:
            return [keyword or "facebook"]
        variants: List[str] = []
        for n in (len(kws), 2, 1):
            v = " ".join(kws[-n:])
            if v and v not in variants:
                variants.append(v)
            if len(variants) >= max_variants:
                break
        return variants[:max_variants] or [keyword]

    def scrape_facebook_pages(self, keyword: str, limit: int = 10,
                              locations: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Search Facebook pages by keyword. Tries the full query first, then
        shorter variants. Zero results from successful runs are reported as
        NO_RESULTS — never assumed to be a Facebook block.
        """
        client = self._get_client()
        logger.info(f"[Apify] Searching pages for '{keyword}' (limit={limit})")

        failures: List[ScrapeError] = []
        for variant in self._keyword_variants(keyword):
            run = self._call_actor(
                "apify/facebook-search-scraper",
                {
                    "categories": [variant],
                    "locations": locations or [],
                    "resultsLimit": limit,
                },
                "search",
                attempts=1,
            )
            hint = _blocking_hint(run)
            if hint:
                # only here — hard evidence from the run — is blocking named
                raise ScrapeError(
                    "BLOCKED",
                    f"Facebook may have blocked the request ({hint}). "
                    "Wait a few minutes and retry.",
                    keyword=keyword, actor_id="apify/facebook-search-scraper",
                    run_id=_rget(run, "id"), dataset_id=_rget(run, "defaultDatasetId"),
                    details=hint)
            classified = _classify_run_status(
                run, keyword=keyword, actor_id="apify/facebook-search-scraper")
            if classified:
                failures.append(classified)
                time.sleep(5)
                continue
            items = self._read_items(run, actor_id="apify/facebook-search-scraper",
                                     keyword=keyword)
            valid = self._validated(items)
            if valid:
                logger.info(f"[Apify] search-scraper '{variant}' → "
                            f"{len(valid)} valid / {len(items)} raw")
                return valid
            failures.append(ScrapeError(
                "NO_RESULTS", "No Facebook pages were found for this search keyword.",
                keyword=keyword, actor_id="apify/facebook-search-scraper",
                run_id=_rget(run, "id"), dataset_id=_rget(run, "defaultDatasetId"),
                items_returned=len(items)))
            time.sleep(5)

        # every variant came back empty or failed — raise the most useful one
        no_results = [f for f in failures if f.error_type == "NO_RESULTS"]
        chosen = no_results[0] if no_results else (failures[0] if failures else None)
        if chosen is None:
            chosen = ScrapeError(
                "NO_RESULTS", "No Facebook pages were found for this search keyword.",
                keyword=keyword, actor_id="apify/facebook-search-scraper",
                items_returned=0)
        chosen.error["keyword"] = keyword
        raise chosen

    # ─────────────────────────────────────────────────────────────────────────
    # 2. FACEBOOK PAGES SCRAPER — details by URL
    # ─────────────────────────────────────────────────────────────────────────

    def scrape_facebook_pages_by_urls(self, page_urls: List[str]) -> List[Dict[str, Any]]:
        if not page_urls:
            return []
        self._get_client()
        logger.info(f"[Apify] Scraping details for {len(page_urls)} page URLs")
        run = self._call_actor(
            "apify/facebook-pages-scraper",
            {
                "startUrls": [{"url": u} for u in page_urls],
                "maxResults": len(page_urls),
                "proxyConfiguration": {"useApifyProxy": True},
            },
            "pages-details",
            attempts=2,
        )
        hint = _blocking_hint(run)
        if hint:
            raise ScrapeError(
                "BLOCKED", f"Facebook may have blocked the request ({hint}).",
                actor_id="apify/facebook-pages-scraper",
                run_id=_rget(run, "id"), dataset_id=_rget(run, "defaultDatasetId"),
                details=hint)
        classified = _classify_run_status(run, actor_id="apify/facebook-pages-scraper")
        if classified:
            raise classified
        items = self._read_items(run, actor_id="apify/facebook-pages-scraper")
        logger.info(f"[Apify] pages-scraper → {len(items)} detail items")
        return items

    # ─────────────────────────────────────────────────────────────────────────
    # 3. FACEBOOK POSTS SCRAPER
    # ─────────────────────────────────────────────────────────────────────────

    def scrape_facebook_posts(self, page_urls: List[str], posts_per_page: int = 20) -> List[Dict[str, Any]]:
        if not page_urls:
            return []
        self._get_client()
        logger.info(f"[Apify] Scraping posts from {len(page_urls)} pages (max {posts_per_page} each)")
        run = self._call_actor(
            "apify/facebook-posts-scraper",
            {
                "startUrls": [{"url": u} for u in page_urls],
                "resultsLimit": max(posts_per_page, 1),
                "captionText": True,
            },
            "posts",
            attempts=2,
        )
        hint = _blocking_hint(run)
        if hint:
            raise ScrapeError(
                "BLOCKED", f"Facebook may have blocked the request ({hint}).",
                actor_id="apify/facebook-posts-scraper",
                run_id=_rget(run, "id"), dataset_id=_rget(run, "defaultDatasetId"),
                details=hint)
        classified = _classify_run_status(run, actor_id="apify/facebook-posts-scraper")
        if classified:
            raise classified
        items = self._read_items(run, actor_id="apify/facebook-posts-scraper")
        logger.info(f"[Apify] posts-scraper → {len(items)} posts")
        return items

    # ─────────────────────────────────────────────────────────────────────────
    # 4. FACEBOOK COMMENTS SCRAPER
    # ─────────────────────────────────────────────────────────────────────────

    def scrape_facebook_comments(self, post_urls: List[str], comments_per_post: int = 50) -> List[Dict[str, Any]]:
        if not post_urls:
            return []
        self._get_client()
        logger.info(f"[Apify] Scraping comments from {len(post_urls)} posts (max {comments_per_post} each)")
        run = self._call_actor(
            "apify/facebook-comments-scraper",
            {
                "startUrls": [{"url": u} for u in post_urls],
                "commentSettings": {
                    "maxComments": max(comments_per_post, 1),
                    "includeReplies": True,
                    "maxRepliesPerComment": 0,
                    "maxReplyDepth": 1,
                    "emitRepliesAsSeparateRows": True,
                    "commentsSortOrder": "all",
                },
                "proxyConfiguration": {"useApifyProxy": True},
            },
            "comments",
            attempts=2,
        )
        hint = _blocking_hint(run)
        if hint:
            raise ScrapeError(
                "BLOCKED", f"Facebook may have blocked the request ({hint}).",
                actor_id="apify/facebook-comments-scraper",
                run_id=_rget(run, "id"), dataset_id=_rget(run, "defaultDatasetId"),
                details=hint)
        classified = _classify_run_status(run, actor_id="apify/facebook-comments-scraper")
        if classified:
            raise classified
        items = self._read_items(run, actor_id="apify/facebook-comments-scraper")
        logger.info(f"[Apify] comments-scraper → {len(items)} comments")
        return items
