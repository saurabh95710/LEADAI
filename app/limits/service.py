"""Run the limit checks, store the results, raise alerts; live error hooks.

``run_checks(scope)``
  * ``"platform"``  LeadAI's own Apify / Gemini / email / SMS / payments
  * ``<org id>``    one organization: own Apify / Gemini keys (when it brings
                    its own), LeadAI tokens and plan meters
  * ``"all"``       both, for every working organization (own Apify keys are
                    checked with bounded concurrency; organizations checked
                    recently are skipped unless ``force``)

``run_scheduled()`` is what the background sweeper calls every pass: it does
nothing while ``api_limits.enabled`` is off or the last run is younger than
``api_limits.check_minutes``.

Hooks (``record_*``) are called where LeadAI already sees a provider refuse
work for a limit reason (Apify 402 / limit error types, Gemini 429 / 402 /
FAILED_PRECONDITION, SMTP "sending limit" replies, Fast2SMS 416 / Twilio
20005 / 30002). They update the status of that scope at once and evaluate
the alerts; they never raise and are throttled (one write per state per
30 seconds).
"""
import asyncio
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any, Dict, List, Optional

from app.db.mongo import get_sync_db
from app.limits import alerts, checkers, store
from app.limits.model import (
    ALERT_LEVELS,
    ALERT_RANK,
    PLATFORM,
    PLATFORM_PROVIDERS,
    aware,
    make_status,
    now_utc,
    org_scope,
    pacific_day,
    worst,
)

logger = logging.getLogger(__name__)

APIFY_CONCURRENCY = 4
ACTIVE_ORG_STATUSES = ("active", "demo", "trial")


def check_minutes() -> int:
    try:
        from app.admin.settings import get_setting
        v = int(get_setting("api_limits.check_minutes") or 15)
    except Exception:
        v = 15
    return min(max(v, 5), 1440)


def enabled() -> bool:
    try:
        from app.admin.settings import get_setting
        v = get_setting("api_limits.enabled")
        return True if v is None else bool(v)
    except Exception:
        return True


def limit_settings() -> Dict[str, Any]:
    """The monitor's settings as the Super Admin page shows them."""
    from app.limits.model import thresholds
    warn, crit = thresholds()
    cfg = alerts.alert_settings()

    def gi(key: str) -> int:
        try:
            from app.admin.settings import get_setting
            return int(get_setting(key) or 0)
        except Exception:
            return 0
    return {"enabled": cfg["enabled"], "check_minutes": check_minutes(), "warn_percent": warn,
            "critical_percent": crit, "notify_org_admins": cfg["notify_org_admins"],
            "notify_users": cfg["notify_users"], "email_admins": cfg["email_admins"],
            "gemini_limits": {"requests_per_minute": gi("ai.gemini.requests_per_minute"),
                              "requests_per_day": gi("ai.gemini.requests_per_day"),
                              "input_per_minute": gi("ai.gemini.input_per_minute")},
            "email_daily_limit": gi("email.daily_limit"),
            "sms_min_balance": checkers._min_balance(),
            "settings_link": "/superadmin#/settings?tab=notifications"}


def _finish(status: Optional[Dict[str, Any]], *, alert: bool = True) -> Optional[Dict[str, Any]]:
    if not status:
        return None
    prev = store.public(store.get_status(status["scope"], status["provider"]))
    if status["provider"] == "apify":
        merge_live_event(status, prev)
    else:
        carry_last_event(status, prev)
    track_streak(status, prev)
    status["next_check_at"] = (now_utc() + timedelta(minutes=check_minutes())).isoformat()
    store.save_status(status)
    if alert:
        alerts.evaluate(status)
    return status


def track_streak(status: Dict[str, Any], prev: Optional[Dict[str, Any]]) -> None:
    """Recovery hysteresis inputs: ``ok_streak`` (ok checks in a row) and
    ``last_bad_at`` (last time the level was warning or worse)."""
    lv = status.get("level")
    if lv in ALERT_LEVELS:
        status["ok_streak"], status["last_bad_at"] = 0, now_utc().isoformat()
    elif lv == "ok":
        status["ok_streak"] = int((prev or {}).get("ok_streak") or 0) + 1
        status["last_bad_at"] = (prev or {}).get("last_bad_at")
    else:   # unknown / not configured: no evidence either way
        status["ok_streak"] = int((prev or {}).get("ok_streak") or 0)
        status["last_bad_at"] = (prev or {}).get("last_bad_at")


EVENT_TRANSIENT_MINUTES = 30


def apify_event_active(event: Optional[Dict[str, Any]], scope: str) -> bool:
    """A live Apify error stays in force until an actor run of the same token
    succeeds after it (momentary memory / parallel-run events also expire
    after EVENT_TRANSIENT_MINUTES)."""
    if not isinstance(event, dict) or event.get("level") not in ALERT_LEVELS:
        return False
    expires = aware(event.get("expires_at"))
    if expires is not None and expires <= now_utc():
        return False
    at = aware(event.get("at"))
    db = get_sync_db()
    if db is None or at is None:
        return True
    q: Dict[str, Any] = {"status": "succeeded", "created_at": {"$gt": at}}
    org_id = scope[4:] if scope.startswith("org:") else None
    if org_id:
        q.update(organization_id=org_id, token_source="organization")
    else:
        q["token_source"] = "platform"
    try:
        return db.apify_jobs.find_one(q) is None
    except Exception:
        return True


def merge_live_event(status: Dict[str, Any], prev: Optional[Dict[str, Any]]) -> None:
    """Keep a live Apify error (402 / paid plan needed / limit) in force over a
    scheduled read that looks fine, until a successful run proves it is over."""
    event = (prev or {}).get("event")
    if status.get("level") == "not_configured" or not apify_event_active(event, status["scope"]):
        return
    status["event"] = event
    lv = event.get("level")
    if ALERT_RANK.get(lv, 0) > ALERT_RANK.get(status.get("level"), 0) or status.get("level") == "unknown":
        status["level"], status["message"] = lv, event.get("message") or status.get("message")


LAST_EVENT_HOURS = 24


def carry_last_event(status: Dict[str, Any], prev: Optional[Dict[str, Any]]) -> None:
    """Keep showing the last live error ("Last live error" on the page) after a
    scheduled check rebuilt the status: until it expires, while the service is
    still not ok, or for LAST_EVENT_HOURS after it happened. Display only — the
    level comes from the check."""
    event = (prev or {}).get("event")
    if status.get("event") or not isinstance(event, dict):
        return
    now = now_utc()
    expires = aware(event.get("expires_at"))
    if expires is not None and expires <= now:
        return
    at = aware(event.get("at"))
    recent = at is not None and now - at <= timedelta(hours=LAST_EVENT_HOURS)
    if status.get("level") != "ok" or recent:
        status["event"] = event


def _clean_prev(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return store.public(doc) if doc else None


# ── platform ────────────────────────────────────────────────────────────────

def _check_platform_provider(provider: str) -> Optional[Dict[str, Any]]:
    if provider == "apify":
        from app.services.tenant_api_keys import platform_key
        return checkers.check_apify(platform_key("apify"), PLATFORM, store.get_status(PLATFORM, "apify"))
    if provider == "gemini":
        return checkers.check_gemini(PLATFORM, _clean_prev(store.get_status(PLATFORM, "gemini")))
    if provider == "email":
        return checkers.check_email()
    if provider == "sms":
        return checkers.check_sms()
    return checkers.check_payments()


def check_platform(force: bool = False) -> List[Dict[str, Any]]:
    out = []
    minutes = check_minutes()
    for provider in PLATFORM_PROVIDERS:
        prev = store.get_status(PLATFORM, provider)
        if not force and store.is_fresh(prev, minutes):
            out.append(store.public(prev))
            continue
        try:
            st = _finish(_check_platform_provider(provider))
        except Exception as e:
            logger.warning("api limit check of %s failed: %s", provider, e)
            st = None
        if st:
            out.append(st)
    return [s for s in out if s]


# ── organizations ───────────────────────────────────────────────────────────

def _org_doc(organization_id: str) -> Optional[Dict[str, Any]]:
    from app.services.tenant_api_keys import _org_sync
    return _org_sync(organization_id)


def check_org(org: Dict[str, Any], *, force: bool = False, network: bool = True,
              providers: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Statuses of one organization (stored + alerted). ``network=False``
    skips the Apify call (the stored Apify status is kept)."""
    org_id = str(org["_id"])
    scope = org_scope(org_id)
    minutes = check_minutes()
    out: List[Dict[str, Any]] = []
    for provider in providers or ["tokens", "plan", "gemini", "apify"]:
        prev = store.get_status(scope, provider)
        if provider == "apify" and not network:
            from app.services.tenant_api_keys import coverage_of
            if prev and coverage_of(org).get("apify") == "own":
                out.append(store.public(prev))
            elif prev:
                store.delete_status(scope, provider)
            continue
        if not force and store.is_fresh(prev, minutes):
            out.append(store.public(prev))
            continue
        try:
            if provider == "tokens":
                st = checkers.check_tokens(org_id, org)
            elif provider == "plan":
                st = checkers.check_plan(org_id, org)
            elif provider == "gemini":
                st = checkers.check_gemini(scope, _clean_prev(prev))
            else:
                st = checkers.check_org_apify(org, prev)
        except Exception as e:
            logger.warning("api limit check of %s for %s failed: %s", provider, org_id, e)
            continue
        if st is None:
            if prev:
                store.delete_status(scope, provider)
            continue
        out.append(_finish(st))
    return [s for s in out if s]


def _working_orgs() -> List[Dict[str, Any]]:
    db = get_sync_db()
    if db is None:
        return []
    return list(db.organizations.find({"status": {"$in": list(ACTIVE_ORG_STATUSES)}}))


def check_all_orgs(force: bool = False) -> int:
    orgs = _working_orgs()
    for org in orgs:
        check_org(org, force=force, network=False)
    # own Apify accounts: a network call each, a few at a time
    from app.services.tenant_api_keys import coverage_of
    own = [o for o in orgs if coverage_of(o).get("apify") == "own"]
    if own:
        with ThreadPoolExecutor(max_workers=APIFY_CONCURRENCY, thread_name_prefix="api-limits") as pool:
            list(pool.map(lambda o: check_org(o, force=force, providers=["apify"]), own))
    return len(orgs)


class ChecksBusy(RuntimeError):
    """Another "all" pass is running (this process or another worker)."""


LEASE_SECONDS = 15 * 60
_run_lock = threading.Lock()


def run_checks(scope: str = "all", force: bool = False) -> Dict[str, Any]:
    """Check, store and alert. Returns {scope, platform: [...], organizations: n | statuses}.
    An "all" pass holds a process lock and a database lease (``_meta.lease_until``)
    so only one worker runs it; raises ChecksBusy otherwise."""
    result: Dict[str, Any] = {"scope": scope, "checked_at": now_utc().isoformat()}
    if scope == "all":
        if not _run_lock.acquire(blocking=False):
            raise ChecksBusy("a check is already running")
        try:
            if not store.acquire_lease(LEASE_SECONDS):
                raise ChecksBusy("a check is already running on another worker")
            try:
                result["platform"] = check_platform(force=force)
                result["organizations"] = check_all_orgs(force=False)
                store.mark_run(scope, {"organizations": result["organizations"]})
            finally:
                store.release_lease()
        finally:
            _run_lock.release()
        return result
    if scope == PLATFORM:
        result["platform"] = check_platform(force=force)
    else:
        org = _org_doc(scope)
        if not org:
            raise ValueError("Organization not found")
        result["statuses"] = check_org(org, force=force)
    return result


def run_scheduled() -> Optional[Dict[str, Any]]:
    """One scheduled pass (skipped while off, not yet due, or already running).
    The pass is claimed atomically in ``api_limit_status._meta`` (last_run_at
    older than the interval), so with several workers only one runs it."""
    if not enabled():
        return None
    if not store.claim_scheduled_run(check_minutes()):
        return None
    try:
        return run_checks("all")
    except ChecksBusy:
        return None


FIRST_RUN_DELAY_SECONDS = 60


def loop_sleep_seconds() -> int:
    """Pause between two passes of the limits loop: max(5, check_minutes) minutes."""
    return max(5, check_minutes()) * 60


async def _limits_loop() -> None:
    """The API limits monitor's own background loop (started by app.main's
    lifespan outside tests, cancelled at shutdown)."""
    await asyncio.sleep(FIRST_RUN_DELAY_SECONDS)   # let startup finish first
    while True:
        try:
            await asyncio.to_thread(run_scheduled)
        except Exception as e:  # never let the loop die
            logger.warning("[limits] api limit check failed: %s", e)
        await asyncio.sleep(loop_sleep_seconds())


def start_limits_loop() -> "asyncio.Task":
    return asyncio.create_task(_limits_loop(), name="api-limits-monitor")


# ── live hooks ──────────────────────────────────────────────────────────────

_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = threading.Lock()
_pending = threading.BoundedSemaphore(200)


def _run_in_background(fn: Any, *args: Any, **kwargs: Any) -> None:
    """Alert evaluation of a live hook runs off the scraping / AI thread
    (bounded: 2 workers, at most 200 waiting; errors are logged). Under
    pytest it runs inline so tests stay deterministic."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        fn(*args, **kwargs)
        return
    global _executor
    if not _pending.acquire(blocking=False):
        logger.warning("api limit alert queue full: dropped one evaluation")
        return

    def job() -> None:
        try:
            fn(*args, **kwargs)
        except Exception as e:
            logger.warning("api limit alert evaluation failed: %s", e)
        finally:
            _pending.release()
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="api-limit-alerts")
    _executor.submit(job)


_THROTTLE_SECONDS = 30.0
_last_hook: Dict[str, float] = {}
_hook_lock = threading.Lock()


def _throttled(key: str) -> bool:
    now = time.monotonic()
    with _hook_lock:
        last = _last_hook.get(key)
        if last is not None and now - last < _THROTTLE_SECONDS:
            return True
        _last_hook[key] = now
        if len(_last_hook) > 5000:
            _last_hook.clear()
    return False


def reset_hook_throttle() -> None:
    _last_hook.clear()


def _record_event(scope: str, provider: str, level: str, message: str, *, code: str,
                  block: Optional[Dict[str, Any]] = None, period: Optional[str] = None,
                  configured: bool = True, transient: bool = False,
                  admins_notified: bool = False) -> Optional[Dict[str, Any]]:
    if _throttled(f"{scope}:{provider}:{level}:{(block or {}).get('kind')}"):
        return None
    prev = store.public(store.get_status(scope, provider))
    now = now_utc()
    if prev:
        st = dict(prev)
        prev_level = prev.get("level") or "unknown"
        if ALERT_RANK.get(level, 0) >= ALERT_RANK.get(prev_level, 0) or prev_level in ("unknown", "not_configured"):
            st["level"], st["message"] = level, message
        else:
            st["level"] = worst([prev_level, level])
    else:
        st = make_status(provider, scope, configured=configured, level=level, message=message)
    if period:
        st["period"] = period
    st["event"] = {"code": code, "level": level, "message": message, "at": now.isoformat(),
                   "expires_at": (now + timedelta(minutes=EVENT_TRANSIENT_MINUTES)).isoformat() if transient else None,
                   "admins_notified": bool(admins_notified)}
    if block is not None:
        st["block"] = block
    if st.get("level") in ALERT_LEVELS:
        st["ok_streak"], st["last_bad_at"] = 0, now.isoformat()
    store.save_status(st)
    _run_in_background(alerts.evaluate, st, from_event=True, admins_notified=admins_notified)
    return st


def record_apify_error(organization_id: Optional[str], token_source: str, status_code: Optional[int],
                       error_type: Optional[str] = None, message: str = "", *,
                       admins_notified: bool = False) -> Optional[Dict[str, Any]]:
    """An Apify call failed. Platform token → platform scope; an organization's
    own token → that organization's scope. ``admins_notified``: the connector
    already told the organization's admins (record_key_failure). Never raises."""
    try:
        if token_source == "platform":
            scope = PLATFORM
        elif token_source == "organization" and organization_id:
            scope = org_scope(organization_id)
        else:
            return None
        level = checkers.classify_apify_error(status_code, error_type, message)
        if level not in ("exhausted", "error", "warning"):
            return None
        prev = store.get_status(scope, "apify") or {}
        period = prev.get("period") or checkers.apify_period()
        paid = checkers.apify_needs_paid_plan(error_type, message)
        code = "APIFY_ACTOR_NEEDS_PAID_PLAN" if paid and level == "error" else \
            f"APIFY_{(error_type or status_code or 'ERROR')}".upper().replace("-", "_")
        return _record_event(scope, "apify", level,
                             checkers.apify_error_message(scope, level, status_code, error_type, message),
                             code=code, period=period,
                             transient=(error_type or "").lower() in checkers.APIFY_BUSY_TYPES,
                             admins_notified=admins_notified)
    except Exception as e:
        logger.debug("apify limit hook failed: %s", e)
        return None


def record_gemini_error(organization_id: Optional[str], key_source: str, status_code: int,
                        body: Any, *, admins_notified: bool = False) -> Optional[Dict[str, Any]]:
    """A Gemini call was refused. Returns the stored status (None when the
    answer is no quota / billing problem). Never raises."""
    try:
        parsed = checkers.parse_gemini_error(int(status_code), body)
        block = checkers.gemini_block(parsed)
        if not block:
            return None
        if key_source == "platform":
            scope = PLATFORM
        elif organization_id:
            scope = org_scope(organization_id)
        else:
            return None
        level = checkers.GEMINI_BLOCK_LEVEL.get(block["kind"], "warning")
        # a longer block already in force (e.g. the daily quota) is never
        # shortened by a later per-minute answer
        prev = store.public(store.get_status(scope, "gemini")) or {}
        active = checkers._block_active(prev.get("block"), scope)
        if active and active.get("kind") != block["kind"] and ALERT_RANK.get(
                checkers.GEMINI_BLOCK_LEVEL.get(active["kind"], "warning"), 0) >= ALERT_RANK.get(level, 0):
            return {**prev, "block": active}
        st = _record_event(scope, "gemini", level, checkers.gemini_block_message(scope, block),
                           code=f"GEMINI_{block['kind'].upper()}", block=block,
                           period=f"pt-{pacific_day()[0]}", admins_notified=admins_notified)
        return st or {"block": block, "level": level}
    except Exception as e:
        logger.debug("gemini limit hook failed: %s", e)
        return None


def record_email_error(error_text: str, code: Optional[int] = None) -> bool:
    """The mail server refused a send. True when it was a sending-limit reply
    (the outbox row should be flagged ``limit_hit``)."""
    try:
        if not checkers.is_smtp_limit_error(error_text, code):
            return False
        if not alerts.in_alert():
            _record_event(PLATFORM, "email", "exhausted",
                          "The mail server refused emails because its sending limit was reached "
                          f"({str(error_text)[:120]}). Emails fail until the limit resets (usually within "
                          "24 hours) or the mailbox plan is raised.",
                          code="SMTP_LIMIT", period=now_utc().strftime("%Y-%m-%d"))
        return True
    except Exception as e:
        logger.debug("email limit hook failed: %s", e)
        return False


def record_sms_error(provider: str, error_text: str) -> bool:
    """The SMS provider refused a text. True for a balance / suspension error."""
    try:
        if not checkers.is_sms_limit_error(error_text):
            return False
        if not alerts.in_alert():
            _record_event(PLATFORM, "sms", "exhausted",
                          f"The SMS provider ({provider}) refused texts for lack of balance "
                          f"({str(error_text)[:120]}). Recharge the account to send texts again.",
                          code="SMS_BALANCE", period=now_utc().strftime("%Y-%m-%d"))
        return True
    except Exception as e:
        logger.debug("sms limit hook failed: %s", e)
        return False
