"""
Apify Connector — Facebook data source (3 actors).

  1. apify/facebook-pages-scraper       → page details by URL
  2. apify/facebook-posts-scraper       → posts of a page
  3. apify/facebook-comments-scraper    → comments of a post

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
import contextvars
import json
import logging
import re
import time
import uuid
from datetime import timedelta
from typing import Any, Callable, Dict, List, Optional

from apify_client.errors import ApifyApiError, ApifyClientError

from app.config import get_settings

# Active Apify run IDs (for graceful shutdown abort)
_active_apify_runs: set = set()

# Tenant context of the pipeline step that is calling Apify (set by the
# URL search / post / comment collectors) so every actor run is recorded in
# ``apify_jobs`` for the organization's "Actor runs" view.
_RUN_CONTEXT: "contextvars.ContextVar[Optional[Dict[str, Any]]]" = contextvars.ContextVar(
    "apify_run_context", default=None)


def set_run_context(**ctx: Any) -> None:
    """Tag the Apify runs of the current thread / task with tenant context
    (organization_id, user_id, search_run_id, platform)."""
    _RUN_CONTEXT.set({k: (str(v) if v is not None else None) for k, v in ctx.items()})


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def usage_total_usd(run: Any) -> Optional[float]:
    """The run's total cost in USD as a plain float (BSON-safe).

    apify_client 3.x: ``Run.usage_total_usd`` (alias usageTotalUsd) is the
    total; ``Run.usage_usd`` is a per-resource breakdown object (RunUsageUsd)
    that must never be stored as-is. A numeric usageUsd (older clients / raw
    dicts) is accepted; a breakdown is summed as a last resort."""
    if run is None:
        return None
    total = _number(_rget(run, "usageTotalUsd"))
    if total is not None:
        return total
    raw = _rget(run, "usageUsd")
    n = _number(raw)
    if n is not None:
        return n
    if raw is not None:
        try:
            parts = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw)
            vals = [_number(v) for v in parts.values()]
            nums = [v for v in vals if v is not None]
            return round(sum(nums), 6) if nums else None
        except Exception:
            return None
    return None


def _record_actor_run(actor_id: str, label: str, started: float, run: Any = None,
                      error: Optional["ScrapeError"] = None, token_source: Optional[str] = None) -> None:
    """Best-effort ``apify_jobs`` record of one actor run (never raises)."""
    ctx = _RUN_CONTEXT.get()
    if not ctx or not ctx.get("organization_id"):
        return
    try:
        from app.db.models import utcnow
        from app.db.mongo import get_sync_db
        db = get_sync_db()
        if db is None:
            return
        finished = time.time()
        run_id = _rget(run, "id") if run is not None else None
        db.apify_jobs.insert_one({
            "job_id": run_id or f"local-{uuid.uuid4().hex}",
            "organization_id": ctx.get("organization_id"), "user_id": ctx.get("user_id"),
            "search_run_id": ctx.get("search_run_id"), "platform": ctx.get("platform"),
            "actor_id": actor_id, "label": label, "run_id": run_id,
            "dataset_id": _rget(run, "defaultDatasetId") if run is not None else None,
            "status": ("failed" if error is not None
                       else str(_rget(run, "status") or "succeeded").lower()),
            "error": str(error)[:500] if error is not None else None,
            "error_type": getattr(error, "error_type", None),
            # plain floats only (the Run model's usageUsd is a breakdown object)
            "usage_usd": usage_total_usd(run),
            "usage_total_usd": usage_total_usd(run),
            "token_source": token_source,
            "duration_seconds": round(finished - started, 1),
            "created_at": utcnow(), "finished_at": utcnow()})
    except Exception as e:  # recording must never break scraping
        logger.debug("apify job record failed: %s", e)

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
    def __init__(self, token: Optional[str] = None, organization_id: Optional[str] = None):
        self.organization_id = organization_id
        if token:
            self.token = token
            self.token_source = "explicit"
        else:
            # Check tenant-specific BYOK token or platform default
            effective_org_id = organization_id
            if not effective_org_id:
                ctx = _RUN_CONTEXT.get()
                if ctx and ctx.get("organization_id"):
                    effective_org_id = ctx.get("organization_id")

            from app.services.tenant_api_keys import get_tenant_apify_token_sync
            resolved_token, source = get_tenant_apify_token_sync(effective_org_id)
            self.token = resolved_token
            self.token_source = source

        self._client: Optional[Any] = None
        # metadata of the most recent actor call (for API diagnostics):
        # {"actorId", "runId", "datasetId", "status", "usageUsd", "itemsReturned"}
        self.last_call: Dict[str, Any] = {}

    def has_token(self) -> bool:
        return bool(self.token)

    def _own_key_failure(self, status: Optional[int], billing: bool) -> str:
        from app.services.tenant_api_keys import record_key_failure
        org_id = self.organization_id or ((_RUN_CONTEXT.get() or {}).get("organization_id"))
        if billing or status == 402:
            code, msg = ("OWN_APIFY_QUOTA_EXCEEDED",
                         "Your Apify account is out of credit, so this search stopped. Add credit at "
                         "console.apify.com/billing, or switch Apify to LeadAI-provided in Admin portal → API keys & plan.")
        else:
            code, msg = ("OWN_APIFY_KEY_REJECTED",
                         "Apify rejected your organization's token, so this search stopped. Update it in "
                         "Admin portal → API keys & plan, or switch Apify to LeadAI-provided.")
        record_key_failure(org_id, "apify", code, msg)
        return msg

    def _note_limit(self, exc: Exception, status: Optional[int], admins_notified: bool = False) -> None:
        """Out of credit / usage limit / token rejected: tell the API limits
        monitor at once (LeadAI's token → platform scope, an organization's
        own token → that organization). Never raises."""
        try:
            from app.limits.service import record_apify_error
            org_id = self.organization_id or ((_RUN_CONTEXT.get() or {}).get("organization_id"))
            record_apify_error(org_id, getattr(self, "token_source", ""), status,
                               getattr(exc, "type", None), str(exc)[:500], admins_notified=admins_notified)
        except Exception as err:  # pragma: no cover - monitoring is best effort
            logger.debug("apify limit note failed: %s", err)

    def _get_client(self):
        if self._client is None:
            if not self.token:
                if getattr(self, "token_source", "") == "organization_missing":
                    from app.services.tenant_api_keys import own_key_problem, record_key_failure
                    org_id = self.organization_id or ((_RUN_CONTEXT.get() or {}).get("organization_id"))
                    problem = own_key_problem(org_id, "apify") or {}
                    record_key_failure(org_id, "apify", problem.get("code", "OWN_APIFY_KEY_MISSING"),
                                       problem.get("message", "Your Apify key is missing."))
                    raise ApifyError(problem.get("message") or "Your organization's own Apify key is missing")
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
                    attempts: int = 1,
                    should_abort: Optional[Callable[[], bool]] = None) -> Any:
        """Run an actor; classify every failure. Logs request + response.

        When `should_abort` is given (callable → bool), the run is started
        asynchronously and polled; a True return aborts the Apify run and
        raises a CANCELLED ScrapeError, so cancellation is responsive even
        while an actor is mid-flight.
        """
        client = self._get_client()
        last_error: Optional[ScrapeError] = None
        started = time.time()
        for attempt in range(attempts):
            _log_request(label, actor_id, run_input)
            try:
                if should_abort is None:
                    run = client.actor(actor_id).call(
                        run_input=run_input, content_type="application/json",
                        **_run_wait_and_timeout())
                else:
                    run = self._call_actor_polling(
                        client, actor_id, run_input, label, should_abort)
            except Exception as e:
                if isinstance(e, ScrapeError) and e.error_type == "CANCELLED":
                    _record_actor_run(actor_id, label, started, error=e)
                    raise
                billing = _explain_error(e)
                status = getattr(e, "status_code", None)
                own_failure = getattr(self, "token_source", "") == "organization" and (
                    bool(billing) or (isinstance(e, ApifyApiError) and status in (401, 402, 403)))
                # own_failure -> _own_key_failure tells the organization's admins below
                self._note_limit(e, status, admins_notified=own_failure)
                if own_failure:
                    # the organization's OWN Apify key: stop now (no retry, never
                    # LeadAI's key) and tell its admins what to fix
                    msg = self._own_key_failure(status, bool(billing))
                    _record_actor_run(actor_id, label, started, error=ScrapeError("API_ERROR", msg, actor_id=actor_id))
                    raise ApifyError(msg)
                if billing:
                    _record_actor_run(actor_id, label, started,
                                      error=ScrapeError("BILLING", billing, actor_id=actor_id))
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
                # plain floats only: usageTotalUsd = total cost; usageUsd kept
                # only when the client gave a number (it is a breakdown object
                # in apify_client 3.x and can't be stored)
                "usageTotalUsd": usage_total_usd(run),
            }
            raw_usage = _number(_rget(run, "usageUsd"))
            if raw_usage is not None:
                self.last_call["usageUsd"] = raw_usage
            _record_actor_run(actor_id, label, started, run=run,
                              token_source=getattr(self, "token_source", None))
            return run
        assert last_error is not None
        _record_actor_run(actor_id, label, started, error=last_error)
        raise last_error

    def _call_actor_polling(self, client: Any, actor_id: str,
                            run_input: Dict[str, Any], label: str,
                            should_abort: Callable[[], bool]) -> Any:
        """Start the actor run, then poll its status — aborting it as soon as
        `should_abort()` returns True (user clicked Cancel).

        Keeps the same server-side run timeout as the blocking `.call()` path
        (`run_timeout`) and adds a local watchdog so a hung/blocked run can
        never keep the pipeline looping forever.
        """
        started = client.actor(actor_id).start(
            run_input=run_input, content_type="application/json",
            run_timeout=timedelta(minutes=_RUN_TIMEOUT_MIN))
        run_id = _rget(started, "id")
        if not run_id:
            raise ScrapeError("API_ERROR", "Apify run started without an id",
                              actor_id=actor_id, details=str(started)[:500])
        _active_apify_runs.add(run_id)
        logger.info(f"[Apify] polling run {run_id} ({label}) for cancellation")
        run_client = client.run(run_id)
        deadline = time.monotonic() + _RUN_TIMEOUT_MIN * 60 + 120  # server timeout + margin
        while True:
            if should_abort():
                try:
                    run_client.abort()
                    logger.info(f"[Apify] aborted run {run_id} — user cancelled")
                except Exception as e:
                    logger.warning(f"[Apify] abort of run {run_id} failed: {e}")
                _active_apify_runs.discard(run_id)
                raise ScrapeError(
                    "CANCELLED", "Search cancelled by user",
                    actor_id=actor_id, run_id=run_id)
            if time.monotonic() > deadline:
                logger.warning(f"[Apify] run {run_id} ({label}) exceeded "
                               f"{_RUN_TIMEOUT_MIN} min — aborting locally")
                try:
                    run_client.abort()
                except Exception:
                    pass
                _active_apify_runs.discard(run_id)
                raise ScrapeError(
                    "ACTOR_TIMED_OUT",
                    f"Apify actor run timed out after {_RUN_TIMEOUT_MIN} minutes — retry",
                    actor_id=actor_id, run_id=run_id)
            try:
                run = run_client.get()
            except Exception as e:
                raise ScrapeError(
                    "API_ERROR", f"Apify run status check failed: {e}",
                    actor_id=actor_id, run_id=run_id, details=str(e)[:2000])
            status = str(_rget(run, "status") or "").upper()
            if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
                _active_apify_runs.discard(run_id)
                return run
            time.sleep(5)

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
                     label: str, keyword: Optional[str] = None,
                     should_abort: Optional[Callable[[], bool]] = None) -> List[Dict[str, Any]]:
        """Run an arbitrary Apify actor and return its dataset items.

        Failures are classified exactly like the Facebook actors
        (BLOCKED / ACTOR_FAILED / NO_RESULTS / …) and raised as ScrapeError;
        the caller decides how to surface them.
        """
        self._get_client()
        logger.info(f"[Apify] Running actor {actor_id} ({label})")
        run = self._call_actor(actor_id, run_input, label, attempts=2,
                               should_abort=should_abort)
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
    # 1. FACEBOOK PAGES SCRAPER — details by URL
    # ─────────────────────────────────────────────────────────────────────────

    def scrape_facebook_pages_by_urls(self, page_urls: List[str],
                                      should_abort: Optional[Callable[[], bool]] = None) -> List[Dict[str, Any]]:
        if not page_urls:
            return []
        from app.admin.settings import get_actor_id
        actor_id = get_actor_id("facebook", "pages")
        self._get_client()
        logger.info(f"[Apify] Scraping details for {len(page_urls)} page URLs")
        run = self._call_actor(
            actor_id,
            {
                "startUrls": [{"url": u} for u in page_urls],
            },
            "pages-details",
            attempts=2,
            should_abort=should_abort,
        )
        hint = _blocking_hint(run)
        if hint:
            raise ScrapeError(
                "BLOCKED", f"Facebook may have blocked the request ({hint}).",
                actor_id=actor_id,
                run_id=_rget(run, "id"), dataset_id=_rget(run, "defaultDatasetId"),
                details=hint)
        classified = _classify_run_status(run, actor_id=actor_id)
        if classified:
            raise classified
        items = self._read_items(run, actor_id=actor_id)
        logger.info(f"[Apify] pages-scraper → {len(items)} detail items")
        return items

    # ─────────────────────────────────────────────────────────────────────────
    # 2. FACEBOOK POSTS SCRAPER
    # ─────────────────────────────────────────────────────────────────────────

    def scrape_facebook_posts(self, page_urls: List[str], posts_per_page: int = 20,
                              should_abort: Optional[Callable[[], bool]] = None) -> List[Dict[str, Any]]:
        if not page_urls:
            return []
        from app.admin.settings import get_actor_id
        actor_id = get_actor_id("facebook", "posts")
        self._get_client()
        logger.info(f"[Apify] Scraping posts from {len(page_urls)} pages (max {posts_per_page} each)")
        run = self._call_actor(
            actor_id,
            {
                "startUrls": [{"url": u} for u in page_urls],
                "resultsLimit": max(posts_per_page, 1),
                "captionText": True,
            },
            "posts",
            attempts=2,
            should_abort=should_abort,
        )
        hint = _blocking_hint(run)
        if hint:
            raise ScrapeError(
                "BLOCKED", f"Facebook may have blocked the request ({hint}).",
                actor_id=actor_id,
                run_id=_rget(run, "id"), dataset_id=_rget(run, "defaultDatasetId"),
                details=hint)
        classified = _classify_run_status(run, actor_id=actor_id)
        if classified:
            raise classified
        items = self._read_items(run, actor_id=actor_id)
        logger.info(f"[Apify] posts-scraper → {len(items)} posts")
        return items

    # ─────────────────────────────────────────────────────────────────────────
    # 3. FACEBOOK COMMENTS SCRAPER
    # ─────────────────────────────────────────────────────────────────────────

    def scrape_facebook_comments(self, post_urls: List[str], comments_per_post: int = 50,
                                 should_abort: Optional[Callable[[], bool]] = None) -> List[Dict[str, Any]]:
        if not post_urls:
            return []
        from app.admin.settings import get_actor_id
        actor_id = get_actor_id("facebook", "comments")
        self._get_client()
        logger.info(f"[Apify] Scraping comments from {len(post_urls)} posts (max {comments_per_post} each)")
        run = self._call_actor(
            actor_id,
            {
                "startUrls": [{"url": u} for u in post_urls],
                "resultsLimit": max(comments_per_post, 1),
                "includeNestedComments": True,
                "viewOption": "RANKED_UNFILTERED",
            },
            "comments",
            attempts=2,
            should_abort=should_abort,
        )
        hint = _blocking_hint(run)
        if hint:
            raise ScrapeError(
                "BLOCKED", f"Facebook may have blocked the request ({hint}).",
                actor_id=actor_id,
                run_id=_rget(run, "id"), dataset_id=_rget(run, "defaultDatasetId"),
                details=hint)
        classified = _classify_run_status(run, actor_id=actor_id)
        if classified:
            raise classified
        items = self._read_items(run, actor_id=actor_id)
        logger.info(f"[Apify] comments-scraper → {len(items)} comments")
        return items
