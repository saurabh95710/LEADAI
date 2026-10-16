"""One checker per provider. Each returns a status (see app.limits.model).

Real provider data (``source: "provider_api"``):
  * Apify     GET https://api.apify.com/v2/users/me/limits (Bearer token)
  * Twilio    GET /2010-04-01/Accounts/{SID}/Balance.json (basic auth)
  * Fast2SMS  POST https://www.fast2sms.com/dev/wallet (authorization header)
Counted by LeadAI (``source: "counted"``) against limits an admin typed in
(``limit_source: "configured"``):
  * Gemini    requests / minute, requests / day (Pacific day), input tokens /
              minute from ``ai_requests`` (Google has no API that tells a key
              its quota) + the "blocked until" state parsed from Google's
              429 / 402 / 400 answers
  * Email     emails sent in the last 24 hours (``email_outbox``) vs
              ``email.daily_limit`` + recent "sending limit" SMTP refusals
  * SMS       texts sent in the last 24 hours vs SMS_DAILY_LIMIT
  * LeadAI    token balance and plan meters of each organization
Configuration only (no network): payments (Razorpay / Stripe have no quota API).

Checkers never raise and never store or log a key or a provider password.
"""
import logging
import re
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.db.models import utcnow
from app.db.mongo import get_sync_db
from app.limits.model import (
    PLATFORM,
    aware,
    describe_metric,
    fmt_number,
    level_for_percent,
    level_from_metrics,
    make_status,
    metric,
    metric_level,
    now_utc,
    org_scope,
    pacific_day,
    scope_org_id,
    thresholds,
    worst,
)

logger = logging.getLogger(__name__)

TIMEOUT = 10.0
APIFY_LIMITS_URL = "https://api.apify.com/v2/users/me/limits"
TWILIO_BALANCE_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Balance.json"
FAST2SMS_WALLET_URL = "https://www.fast2sms.com/dev/wallet"

# Apify error.type values that mean "out of credit / usage limit reached"
APIFY_EXHAUSTED_TYPES = frozenset({"limit-reached", "not-enough-usage-to-run-paid-actor",
                                   "monthly-usage-limit-exceeded", "platform-usage-limit-exceeded"})
# momentary: new runs wait or fail until running ones finish
APIFY_BUSY_TYPES = frozenset({"actor-memory-limit-exceeded", "concurrent-runs-limit-exceeded"})
APIFY_BILLING_HINTS = ("exceed your remaining usage", "insufficient credits", "monthly usage limit",
                       "not enough usage")
# the actor itself needs a paid Apify plan / rental: that actor can't run, the
# account is not out of credit
_APIFY_PAID_PLAN_RE = re.compile(r"upgrade to a paid plan|paid (apify )?(plan|subscription)|\brent(ed|al)?\b|"
                                 r"actor-is-not-rented|trial (has )?expired", re.IGNORECASE)
APIFY_PAID_PLAN_TYPES = frozenset({"actor-is-not-rented", "actor-rental-expired", "paid-actor-requires-plan"})


def apify_needs_paid_plan(error_type: Optional[str], message: str = "") -> bool:
    et = (error_type or "").strip().lower()
    return et in APIFY_PAID_PLAN_TYPES or bool(_APIFY_PAID_PLAN_RE.search(message or ""))


def _setting(key: str, default: Any = None) -> Any:
    try:
        from app.admin.settings import get_setting
        v = get_setting(key)
        return default if v is None else v
    except Exception:
        return default


def _int_setting(key: str, default: int = 0) -> int:
    try:
        return max(0, int(_setting(key, default) or 0))
    except (TypeError, ValueError):
        return default


def _date_words(value: Any) -> str:
    d = aware(value)
    return d.strftime("%d %b %Y") if d else "the next cycle"


def _json(resp: Any) -> Dict[str, Any]:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _org_doc(organization_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not organization_id:
        return None
    try:
        from app.services.tenant_api_keys import _org_sync
        return _org_sync(organization_id)
    except Exception:
        return None


def _org_warn(org: Optional[Dict[str, Any]]) -> Optional[int]:
    """The organization's own warning % (Org Admin settings), when set."""
    try:
        v = int(((org or {}).get("settings") or {}).get("usage_warning_percent") or 0)
        return v if 1 <= v < 100 else None
    except (TypeError, ValueError):
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Apify
# ═══════════════════════════════════════════════════════════════════════════

def _apify_who(scope: str) -> str:
    return "LeadAI's Apify account" if scope == PLATFORM else "Your organization's Apify account"


def classify_apify_error(status_code: Optional[int], error_type: Optional[str] = None,
                         message: str = "") -> Optional[str]:
    """Level an Apify error means: exhausted (out of credit / usage limit),
    error (token rejected), warning (memory / concurrency momentarily full),
    unknown (rate-limited or could not check) — None when it is no limit problem."""
    et = (error_type or "").strip().lower()
    msg = (message or "").lower()
    if et in APIFY_EXHAUSTED_TYPES or any(h in msg for h in APIFY_BILLING_HINTS):
        return "exhausted"
    if apify_needs_paid_plan(et, msg):
        return "error"
    if status_code == 402:
        return "exhausted"
    if et in APIFY_BUSY_TYPES:
        return "warning"
    if status_code == 401:
        return "error"
    if status_code == 429 or et == "rate-limit-exceeded":
        return "unknown"
    return None


def apify_error_message(scope: str, level: str, status_code: Optional[int], error_type: Optional[str],
                        message: str = "") -> str:
    who = _apify_who(scope)
    if level == "error" and apify_needs_paid_plan(error_type, message):
        return (f"{who} can't run one of the scraping actors: that actor needs a paid Apify plan or "
                "rental. Upgrade the Apify plan (console.apify.com/billing) or choose another actor; "
                "the account itself still has credit.")
    fix = ("Add credit or raise the limit in Apify Console → Billing, or replace the token in "
           "Super Admin → API keys." if scope == PLATFORM else
           "Add credit at console.apify.com/billing, or switch Apify to LeadAI-provided in "
           "Admin portal → API keys & plan.")
    if level == "exhausted":
        return f"{who} is out of credit or reached its monthly usage limit, so scraping is paused. {fix}"
    if level == "warning":
        what = "memory" if (error_type or "").startswith("actor-memory") else "parallel runs"
        return (f"{who} hit its limit of {what} running at the same time. New scraping runs wait or fail "
                "until running ones finish.")
    if level == "error":
        return (f"Apify rejected {'LeadAI' if scope == PLATFORM else 'your organization'}'s token "
                f"(HTTP {status_code}). " + ("Replace it in Super Admin → API keys." if scope == PLATFORM
                                              else "Update it in Admin portal → API keys & plan."))
    return f"Couldn't check {who} right now (HTTP {status_code or '?'}). It is checked again later."


def apify_period(cycle_start: Any = None) -> str:
    """Alert period of Apify: the calendar month of the usage-cycle start (the
    live hooks and the scheduled check use the same scheme)."""
    start = str(cycle_start or "")[:7]
    return f"apify-cycle-{start if len(start) == 7 else now_utc().strftime('%Y-%m')}"


def parse_apify_limits(payload: Dict[str, Any], scope: str) -> Dict[str, Any]:
    """Status from the body of GET /v2/users/me/limits."""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    cycle = data.get("monthlyUsageCycle") or {}
    lim = data.get("limits") or {}
    cur = data.get("current") or {}
    resets = cycle.get("endAt")
    metrics = [
        metric("monthly_usage_usd", "Monthly usage", used=cur.get("monthlyUsageUsd"),
               limit=lim.get("maxMonthlyUsageUsd"), unit="USD", resets_at=resets, source="provider_api"),
        metric("compute_units", "Compute units this month", used=cur.get("monthlyActorComputeUnits"),
               limit=lim.get("maxMonthlyActorComputeUnits"), unit="CU", resets_at=resets,
               source="provider_api"),
        metric("memory_gb", "Memory in use now", used=cur.get("actorMemoryGbytes"),
               limit=lim.get("maxActorMemoryGbytes"), unit="GB", source="provider_api", informational=True),
        metric("concurrent_runs", "Runs at the same time", used=cur.get("activeActorJobCount"),
               limit=lim.get("maxConcurrentActorJobs"), unit="runs", source="provider_api",
               informational=True),
    ]
    warn, crit = thresholds()
    level = level_from_metrics(metrics, warn, crit)
    who = _apify_who(scope)
    main = next((m for m in metrics if m["key"] == "monthly_usage_usd" and m["percent"] is not None),
                next((m for m in metrics if m["percent"] is not None and not m["informational"]), None))
    pct = f"{main['percent']:g}%" if main else "?"
    when = _date_words(resets)
    if level == "exhausted":
        msg = (f"{who} reached its monthly usage limit, so scraping is paused until {when} or until "
               "the limit is raised in Apify Console → Billing.")
    elif level == "critical":
        msg = f"{who} has used {pct} of its monthly limit. Scraping stops at 100%. It resets on {when}."
    elif level == "warning":
        msg = f"{who} has used {pct} of its monthly limit. It resets on {when}."
    else:
        msg = f"Working. {describe_metric(main)}." if main else "Working. Apify reports no usage limit."
    return make_status("apify", scope, configured=True, level=level, message=msg, metrics=metrics,
                       period=apify_period(cycle.get("startAt")), cycle={"start_at": cycle.get("startAt"),
                                                           "end_at": cycle.get("endAt")})


def check_apify(token: Optional[str], scope: str, previous: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Read Apify's own usage and limits for ``token`` (never stored). Error
    states keep the alert period of the last good read (the usage cycle), so
    a lasting problem is not announced again every day."""
    period = apify_period()
    last = aware((previous or {}).get("checked_at"))
    if (previous or {}).get("period") and last and now_utc() - last < timedelta(days=35):
        period = str(previous["period"])
    if not token:
        if scope == PLATFORM:
            return make_status("apify", scope, configured=False, level="not_configured", period=period,
                               message="No Apify token is set, so scraping cannot run. Add it in "
                                       "Super Admin → API keys.")
        return make_status("apify", scope, configured=False, level="error", period=period,
                           message="Your organization uses its own Apify account, but no working token "
                                   "is saved. Add it in Admin portal → API keys & plan, or switch Apify "
                                   "to LeadAI-provided.")
    try:
        resp = httpx.get(APIFY_LIMITS_URL, headers={"Authorization": f"Bearer {token.strip()}"},
                         timeout=TIMEOUT)
    except Exception as e:
        logger.info("Apify limits check failed (%s)", type(e).__name__)
        return make_status("apify", scope, configured=True, level="unknown", period=period,
                           message=f"Couldn't reach Apify to read the limits of {_apify_who(scope)[0].lower()}"
                                   f"{_apify_who(scope)[1:]}. It is checked again later.",
                           error={"code": "NETWORK", "message": type(e).__name__})
    code = int(getattr(resp, "status_code", 0) or 0)
    if code == 200:
        return parse_apify_limits(_json(resp), scope)
    err = _json(resp).get("error") or {}
    etype = err.get("type") if isinstance(err, dict) else None
    level = classify_apify_error(code, etype, str((err or {}).get("message") or "") if isinstance(err, dict) else "")
    if level is None:
        level = "error" if code == 403 else "unknown"
    return make_status("apify", scope, configured=True, level=level, period=period,
                       message=apify_error_message(scope, level, code, etype, str(err.get("message") or "")
                                                   if isinstance(err, dict) else ""),
                       error={"code": f"HTTP_{code}", "type": etype})


def check_org_apify(org: Dict[str, Any], previous: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """The organization's own Apify account (None when LeadAI provides Apify)."""
    from app.services.tenant_api_keys import _plain_key, coverage_of
    if coverage_of(org).get("apify") != "own":
        return None
    try:
        key = _plain_key(org, "apify")
    except Exception:
        key = None
    return check_apify(key, org_scope(org["_id"]), previous)


# ═══════════════════════════════════════════════════════════════════════════
# Google Gemini
# ═══════════════════════════════════════════════════════════════════════════

_DELAY_RE = re.compile(r"^\s*([\d.]+)\s*s\s*$")


def _retry_seconds(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    m = _DELAY_RE.match(str(value or ""))
    return float(m.group(1)) if m else None


def parse_gemini_error(status_code: int, body: Any) -> Dict[str, Any]:
    """What a Gemini error answer means for the key's quota.

    kind: "daily" (a per-day quota is used up — blocked until 00:00 Pacific),
    "minute" (a per-minute quota — short wait), "rate" (429 without details),
    "credit" (402: prepaid credit at $0), "billing" (400 FAILED_PRECONDITION:
    billing disabled), "auth" (401/403), None (not a limit problem)."""
    err = (body or {}).get("error") if isinstance(body, dict) else None
    err = err if isinstance(err, dict) else {}
    details = err.get("details") if isinstance(err.get("details"), list) else []
    quota_ids: List[str] = []
    metrics: List[str] = []
    retry: Optional[float] = None
    for d in details:
        if not isinstance(d, dict):
            continue
        t = str(d.get("@type") or "")
        if t.endswith("QuotaFailure"):
            for v in d.get("violations") or []:
                if isinstance(v, dict):
                    quota_ids.append(str(v.get("quotaId") or ""))
                    metrics.append(str(v.get("quotaMetric") or ""))
        elif t.endswith("RetryInfo"):
            retry = _retry_seconds(d.get("retryDelay"))
    out: Dict[str, Any] = {"kind": None, "retry_seconds": retry, "quota_ids": [q for q in quota_ids if q],
                           "quota_metrics": [m for m in metrics if m],
                           "message": str(err.get("message") or "")[:300]}
    ids = [q.lower() for q in quota_ids if q]
    if status_code == 429:
        # daily only when EVERY violated quota is a per-day one; any per-minute
        # violation means waiting a moment is enough
        if ids and all("perday" in q for q in ids):
            out["kind"] = "daily"
        elif any("perminute" in q for q in ids):
            out["kind"] = "minute"
        else:
            out["kind"] = "rate"
    elif status_code == 402:
        out["kind"] = "credit"
    elif status_code == 400 and str(err.get("status") or "").upper() == "FAILED_PRECONDITION":
        text = out["message"].lower()
        if re.search(r"\blocation\b|\bregion\b|\bcountry\b|not supported", text):
            out["kind"] = "region"
        elif re.search(r"billing|payment|prepay|credit", text) or not text:
            out["kind"] = "billing"
    elif status_code in (401, 403):
        out["kind"] = "auth"
    return out


GEMINI_BLOCK_LEVEL = {"daily": "exhausted", "credit": "exhausted", "billing": "exhausted",
                      "auth": "error", "region": "error", "minute": "warning", "rate": "warning"}


def gemini_block(parsed: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A stored "blocked until" state from parse_gemini_error (None = no limit problem)."""
    kind = parsed.get("kind")
    if not kind:
        return None
    now = now_utc()
    until = None
    if kind == "daily":
        until = pacific_day(now)[2]
    elif kind in ("minute", "rate"):
        until = now + timedelta(seconds=min(max(parsed.get("retry_seconds") or 60.0, 1.0), 3600.0))
    return {"kind": kind, "until": until.isoformat() if until else None, "at": now.isoformat(),
            "quota_ids": parsed.get("quota_ids") or [], "message": parsed.get("message") or ""}


def gemini_block_message(scope: str, block: Dict[str, Any]) -> str:
    who = "LeadAI's Gemini key" if scope == PLATFORM else "Your organization's Gemini key"
    kind = block.get("kind")
    until = aware(block.get("until"))
    if kind == "daily":
        when = until.strftime("%d %b %H:%M UTC") if until else "midnight Pacific time"
        return (f"{who} used up its daily request quota. Comments are scored by the keyword rules "
                f"until it resets at {when} (00:00 Pacific time). Raise the quota in Google AI Studio "
                "→ Rate limits.")
    if kind == "credit":
        return (f"{who} is out of prepaid credit (Google answered 402). AI analysis is paused until "
                "credit is added in Google AI Studio → Billing.")
    if kind == "billing":
        return (f"Billing is not enabled for the Google project of {who[0].lower()}{who[1:]}, so Google "
                "refuses requests. Turn billing on in Google AI Studio / Cloud Console.")
    if kind == "region":
        return (f"Google doesn't offer the Gemini API in the region this server runs in, so {who[0].lower()}"
                f"{who[1:]} is refused (\"User location is not supported\"). Run the server in a supported "
                "region or use a Vertex AI endpoint.")
    if kind == "auth":
        return f"Google rejected {who.lower()}. Replace it" + (
            " in Super Admin → API keys." if scope == PLATFORM else " in Admin portal → API keys & plan.")
    return (f"{who} hit Google's per-minute rate limit. Some comments are scored by the keyword rules "
            "for a short while.")


def _gemini_query(scope: str) -> Dict[str, Any]:
    org_id = scope_org_id(scope)
    if org_id:
        return {"organization_id": org_id, "key_source": "organization"}
    return {"$or": [{"key_source": "platform"}, {"key_source": {"$exists": False}}]}


def _block_active(block: Optional[Dict[str, Any]], scope: str) -> Optional[Dict[str, Any]]:
    """The stored block while it still applies: a successful call after it
    ends any block; otherwise daily / minute blocks last until their time,
    credit / billing / auth / region blocks until that success."""
    if not isinstance(block, dict) or not block.get("kind"):
        return None
    until = aware(block.get("until"))
    if until is not None and until <= now_utc():
        return None
    at = aware(block.get("at"))
    db = get_sync_db()
    if db is not None and at is not None:
        try:
            if db.ai_requests.find_one({**_gemini_query(scope), "status": "success",
                                        "created_at": {"$gt": at}}):
                return None
        except Exception:
            pass
    return block


def check_gemini(scope: str, previous: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Requests / tokens counted by LeadAI against the limits typed in Global
    Settings (LeadAI's key) + Google's last quota answer. None when the
    organization uses LeadAI's Gemini."""
    org_id = scope_org_id(scope)
    if org_id:
        from app.services.tenant_api_keys import _plain_key, coverage_of
        org = _org_doc(org_id)
        if not org or coverage_of(org).get("gemini") != "own":
            return None
        try:
            configured = bool(_plain_key(org, "gemini"))
        except Exception:
            configured = False
        if not configured:
            return make_status("gemini", scope, configured=False, level="error", period=f"month-{now_utc():%Y-%m}",
                               message="Your organization uses its own Gemini key, but no working key is "
                                       "saved. Comments are scored by the keyword rules until you add it in "
                                       "Admin portal → API keys & plan.")
        rpm_lim = rpd_lim = tpm_lim = 0
    else:
        from app.services.tenant_api_keys import platform_key
        if not platform_key("gemini"):
            return make_status("gemini", scope, configured=False, level="not_configured",
                               message="No Gemini key is set, so comments are scored by the keyword rules "
                                       "only. Add it in Super Admin → API keys.")
        rpm_lim = _int_setting("ai.gemini.requests_per_minute")
        rpd_lim = _int_setting("ai.gemini.requests_per_day")
        tpm_lim = _int_setting("ai.gemini.input_per_minute")

    now = now_utc()
    day_label, day_start, reset_at = pacific_day(now)
    minute_ago = utcnow() - timedelta(seconds=60)
    base = {**_gemini_query(scope), "status": "success"}
    rpm = rpd = tpm = 0
    db = get_sync_db()
    if db is not None:
        try:
            rpm = db.ai_requests.count_documents({**base, "created_at": {"$gte": minute_ago}})
            rpd = db.ai_requests.count_documents({**base, "created_at": {"$gte": day_start}})
            agg = list(db.ai_requests.aggregate([
                {"$match": {**base, "created_at": {"$gte": minute_ago}}},
                {"$group": {"_id": None, "t": {"$sum": "$tokens_in"}}}]))
            tpm = int((agg[0].get("t") if agg else 0) or 0)
        except Exception as e:
            logger.debug("gemini usage count failed: %s", e)
    metrics = [
        # per-minute meters are momentary (a burst during a check is normal):
        # shown, never alerted — Google's own 429 answer marks a real block
        metric("requests_per_minute", "Requests in the last minute", used=rpm, limit=rpm_lim or None,
               unit="requests", resets_at=now + timedelta(seconds=60), informational=True),
        metric("requests_per_day", "Requests today (Pacific time)", used=rpd, limit=rpd_lim or None,
               unit="requests", resets_at=reset_at),
        metric("input_per_minute", "Input tokens in the last minute", used=tpm, limit=tpm_lim or None,
               unit="tokens", resets_at=now + timedelta(seconds=60), informational=True),
    ]
    for m in metrics:
        m["limit_source"] = "configured" if m["limit"] is not None else None
    warn, crit = thresholds()
    block = _block_active((previous or {}).get("block"), scope)
    level = level_from_metrics(metrics, warn, crit)
    if block:
        level = worst([level, GEMINI_BLOCK_LEVEL.get(block["kind"], "warning")])
        msg = gemini_block_message(scope, block)
    elif level != "ok":
        hot = max((m for m in metrics if m["percent"] is not None and not m["informational"]),
                  key=lambda m: m["percent"])
        msg = (f"Gemini is at {hot['percent']:g}% of a rate limit ({describe_metric(hot)}). At 100% Google "
               "refuses requests and comments are scored by the keyword rules.")
    elif scope == PLATFORM and not (rpm_lim or rpd_lim or tpm_lim):
        msg = (f"Working. {rpd} requests today. Google has no API for quota, so enter your key's limits "
               "(Google AI Studio → Rate limits) in Settings → AI engine to get warnings before they run out.")
    elif scope != PLATFORM:
        msg = f"Working. {rpd} requests today. Google's own errors are watched for quota problems."
    else:
        msg = f"Working. {rpd} requests today."
    if scope == PLATFORM and not _setting("ai.enabled", True):
        msg = "AI analysis is switched off in Settings → AI engine. " + msg
    out = make_status("gemini", scope, configured=True, level=level, message=msg, metrics=metrics,
                      period=f"pt-{day_label}")
    out["block"] = block
    return out


# ═══════════════════════════════════════════════════════════════════════════
# Email (SMTP)
# ═══════════════════════════════════════════════════════════════════════════

_SMTP_LIMIT_ALWAYS = re.compile(r"\b(5\.4\.5|4\.5\.3)\b")
_SMTP_LIMIT_IF_HINT = re.compile(r"\b(5\.7\.1|4\.7\.\d{1,3}|421|452|550|554)\b")
_LIMIT_WORDS = re.compile(
    r"\brate[ -]limit(ed|ing|s)?\b|\bsending (limit|quota)s?\b|\bquotas?\b|"
    r"\btoo many (messages|emails|mails|recipients|connections)\b|\bdaily\b.{0,40}\blimits?\b|"
    r"\blimits? (exceeded|reached)\b|\bexceeded (the |your )?(limit|quota)s?\b", re.IGNORECASE)
# refusals about reputation, content or sign-in are not sending limits
_NOT_A_LIMIT = re.compile(r"\breputation\b|\bspam\b|\bcontent\b|\bauthenticat\w*|\bcredentials?\b|"
                          r"\bblock ?list(ed)?\b|\bblacklist(ed)?\b", re.IGNORECASE)


def is_smtp_limit_error(text: str, code: Optional[int] = None) -> bool:
    """A mail server reply that means "sending limit reached": 550 5.4.5
    (daily quota, Gmail), 452 4.5.3 (too many recipients), and 550 5.7.1 /
    421 4.7.x / 452 / 554 when the reply talks about a limit / quota / rate."""
    t = f"{code or ''} {text or ''}"
    if _SMTP_LIMIT_ALWAYS.search(t):
        return True
    if _NOT_A_LIMIT.search(text or ""):
        return False
    return bool(_SMTP_LIMIT_IF_HINT.search(t) and _LIMIT_WORDS.search(text or ""))


def check_email() -> Dict[str, Any]:
    from app.events.email import smtp_config
    cfg = smtp_config()
    cap = _int_setting("email.daily_limit")
    db = get_sync_db()
    since = utcnow() - timedelta(hours=24)
    sent, resets_at, last_limit, sent_after = 0, None, None, False
    if db is not None:
        try:
            sent = db.email_outbox.count_documents({"status": "sent", "created_at": {"$gte": since}})
            first = db.email_outbox.find_one({"status": "sent", "created_at": {"$gte": since}},
                                             sort=[("created_at", 1)])
            if first and aware(first.get("created_at")):
                resets_at = aware(first["created_at"]) + timedelta(hours=24)
            last_limit = db.email_outbox.find_one({"limit_hit": True, "created_at": {"$gte": since}},
                                                  sort=[("created_at", -1)])
            if last_limit:
                sent_after = bool(db.email_outbox.find_one({"status": "sent",
                                                            "created_at": {"$gt": last_limit["created_at"]}}))
        except Exception as e:
            logger.debug("email usage count failed: %s", e)
    m = metric("emails_24h", "Emails sent in the last 24 hours", used=sent, limit=cap or None,
               unit="emails", resets_at=resets_at)
    m["limit_source"] = "configured" if cap else None
    period = now_utc().strftime("%Y-%m-%d")
    if not cfg["host"]:
        return make_status("email", PLATFORM, configured=False, level="not_configured",
                           message="No mail server is set, so emails are only queued in the outbox. Set "
                                   "SMTP_* in Settings → Email & alerts.", metrics=[m], period=period)
    warn, crit = thresholds()
    level = metric_level(m, warn, crit)
    if last_limit and not sent_after:
        level = "exhausted"
        msg = ("The mail server refused emails because its sending limit was reached "
               f"({str(last_limit.get('error') or '')[:120]}). Emails fail until the limit resets "
               "(usually within 24 hours) or the mailbox plan is raised.")
    elif last_limit:
        level = worst([level, "warning"])
        msg = ("The mail server's sending limit was hit in the last 24 hours; emails go out again now. "
               "Consider a higher sending limit.")
    elif level != "ok":
        msg = (f"{describe_metric(m)} — close to the mailbox's daily sending limit. More emails may be "
               "refused until the oldest ones are 24 hours old.")
    elif cap:
        msg = f"Working. {describe_metric(m)}."
    else:
        msg = (f"Working. {sent} emails sent in the last 24 hours. Enter your mailbox's daily sending limit "
               "in Settings → Email & alerts to get warnings.")
    return make_status("email", PLATFORM, configured=True, level=level, message=msg, metrics=[m],
                       period=period)


# ═══════════════════════════════════════════════════════════════════════════
# SMS
# ═══════════════════════════════════════════════════════════════════════════

_SMS_LIMIT_RE = re.compile(r"\b(code|HTTP|status_code)\W{0,3}(416|20005|30002)\b|insufficient (wallet )?"
                           r"(balance|credit|fund)|low balance|account (is )?suspended", re.IGNORECASE)


def is_sms_limit_error(text: str) -> bool:
    """Fast2SMS 416 (insufficient balance), Twilio 20005 / 30002 (account
    suspended — balance at zero)."""
    return bool(_SMS_LIMIT_RE.search(text or ""))


def _min_balance() -> float:
    """``sms.min_balance`` (account currency; 0 = no low-balance warning)."""
    try:
        return max(0.0, float(_setting("sms.min_balance", 0) or 0))
    except (TypeError, ValueError):
        return 0.0


def balance_level(balance: float, min_balance: float) -> Optional[str]:
    """exhausted at <= 0; with a minimum set: critical below half of it,
    warning below it; None = fine."""
    if balance <= 0:
        return "exhausted"
    if min_balance > 0:
        if balance < min_balance / 2:
            return "critical"
        if balance < min_balance:
            return "warning"
    return None


def _balance_metric(key: str, label: str, balance: float, unit: str, min_balance: float) -> Dict[str, Any]:
    m = metric(key, label, remaining=balance, unit=unit, source="provider_api")
    m["warn_below"] = min_balance or None          # sms.min_balance (configured)
    return m


def _twilio_balance() -> Tuple[List[Dict[str, Any]], Optional[str], Optional[str]]:
    from app.messaging.sms import _env
    sid, token = _env("TWILIO_ACCOUNT_SID"), _env("TWILIO_AUTH_TOKEN")
    try:
        r = httpx.get(TWILIO_BALANCE_URL.format(sid=sid), auth=(sid, token), timeout=TIMEOUT)
    except Exception as e:
        return [], "unknown", f"Couldn't reach Twilio to read the balance ({type(e).__name__})."
    data = _json(r)
    code = int(getattr(r, "status_code", 0) or 0)
    if code == 200 and data.get("balance") is not None:
        try:
            bal = float(data["balance"])
        except (TypeError, ValueError):
            return [], "unknown", "Twilio returned a balance LeadAI could not read."
        cur = str(data.get("currency") or "USD")
        min_bal = _min_balance()
        m = _balance_metric("balance", "Twilio balance", bal, cur, min_bal)
        lv = balance_level(bal, min_bal)
        if lv == "exhausted":
            return [m], lv, f"The Twilio balance is {fmt_number(bal)} {cur}: texts are not sent until you add funds."
        if lv:
            return [m], lv, (f"The Twilio balance is {fmt_number(bal)} {cur}, below the "
                             f"{fmt_number(min_bal)} {cur} minimum. Add funds in the Twilio Console soon.")
        return [m], None, None
    tcode = data.get("code")
    if tcode in (20005, 30002, "20005", "30002"):
        return [], "exhausted", "Twilio suspended the account (balance at zero). Add funds in the Twilio Console."
    if code in (401, 403) or tcode in (20003, "20003"):
        return [], "error", "Twilio rejected TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN."
    return [], "unknown", f"Couldn't read the Twilio balance (HTTP {code})."


def _fast2sms_wallet(daily_limit: int) -> Tuple[List[Dict[str, Any]], Optional[str], Optional[str]]:
    from app.messaging.sms import _env
    try:
        r = httpx.post(FAST2SMS_WALLET_URL, headers={"authorization": _env("FAST2SMS_API_KEY")}, timeout=TIMEOUT)
    except Exception as e:
        return [], "unknown", f"Couldn't reach Fast2SMS to read the wallet ({type(e).__name__})."
    data = _json(r)
    code = int(getattr(r, "status_code", 0) or 0)
    if data.get("return") is True:
        try:
            wallet = float(data.get("wallet") or 0)
            count = int(float(data.get("sms_count") or 0))
        except (TypeError, ValueError):
            return [], "unknown", "Fast2SMS returned a wallet LeadAI could not read."
        min_bal = _min_balance()
        ms = [_balance_metric("wallet", "Fast2SMS wallet", wallet, "INR", min_bal),
              metric("sms_count", "Texts the wallet can still send", remaining=count, unit="SMS",
                     source="provider_api")]
        if wallet <= 0 or count <= 0:
            return ms, "exhausted", "The Fast2SMS wallet is empty: texts are not sent until you recharge it."
        lv = balance_level(wallet, min_bal)
        if lv:
            return ms, lv, (f"The Fast2SMS wallet is at {fmt_number(wallet)} INR ({count:,} texts), below the "
                            f"{fmt_number(min_bal)} INR minimum. Recharge it soon.")
        if daily_limit and count < daily_limit:
            return ms, "warning", (f"The Fast2SMS wallet has {count:,} texts left, less than one day's allowance "
                                   f"({daily_limit:,}). Recharge it soon.")
        return ms, None, None
    scode = str(data.get("status_code") or code)
    if scode == "416":
        return [], "exhausted", "Fast2SMS reports insufficient balance (416): recharge the wallet."
    if scode in ("401", "412") or code in (401, 403):
        return [], "error", "Fast2SMS rejected FAST2SMS_API_KEY."
    return [], "unknown", f"Couldn't read the Fast2SMS wallet (HTTP {code})."


def check_sms() -> Dict[str, Any]:
    from app.messaging.sms import provider_status
    st = provider_status()
    p = st.get("provider") or ""
    period = now_utc().strftime("%Y-%m-%d")
    daily = metric("sms_24h", "Texts sent in the last 24 hours", used=st.get("sent_today") or 0,
                   limit=st.get("daily_limit") or None, unit="SMS")
    daily["limit_source"] = "configured" if daily["limit"] is not None else None
    label = {"twilio": "SMS (Twilio)", "fast2sms": "SMS (Fast2SMS)", "webhook": "SMS (webhook)"}.get(p, "SMS")
    if not p:
        return make_status("sms", PLATFORM, configured=False, level="not_configured", label=label,
                           message="No SMS provider is set, so texts are only queued. Set one up on the "
                                   "Messaging page.", metrics=[daily], period=period)
    if not st.get("configured"):
        return make_status("sms", PLATFORM, configured=False, level="error", label=label,
                           message=f"The SMS provider ({p}) is missing: {', '.join(st.get('missing') or [])}.",
                           metrics=[daily], period=period)
    extra: List[Dict[str, Any]] = []
    p_level: Optional[str] = None
    p_msg: Optional[str] = None
    if p == "twilio":
        extra, p_level, p_msg = _twilio_balance()
    elif p == "fast2sms":
        extra, p_level, p_msg = _fast2sms_wallet(int(st.get("daily_limit") or 0))
    metrics = [daily] + extra
    warn, crit = thresholds()
    level = metric_level(daily, warn, crit)
    msgs = []
    if p_level:
        level = worst([level, p_level]) if p_level != "unknown" else level
        msgs.append(p_msg or "")
    db = get_sync_db()
    if db is not None:
        try:
            since = utcnow() - timedelta(hours=24)
            last = db.sms_outbox.find_one({"limit_hit": True, "created_at": {"$gte": since}},
                                          sort=[("created_at", -1)])
            if last and not db.sms_outbox.find_one({"status": "sent", "created_at": {"$gt": last["created_at"]}}):
                level = worst([level, "exhausted"])
                msgs.append(f"The SMS provider refused texts for lack of balance ({str(last.get('error') or '')[:100]}).")
        except Exception:
            pass
    if level != "ok" and daily["percent"] is not None and metric_level(daily, warn, crit) != "ok":
        msgs.append(f"{describe_metric(daily)} — LeadAI stops sending at SMS_DAILY_LIMIT.")
    if not msgs:
        bal = next((m for m in extra if m["key"] in ("balance", "wallet")), None)
        msgs.append(f"Working. {describe_metric(daily)}" + (f"; {describe_metric(bal)}." if bal else "."))
    if p_level == "unknown" and level == "ok":
        msgs = [f"Working. {describe_metric(daily)}. " + (p_msg or "")]
    return make_status("sms", PLATFORM, configured=True, level=level, label=label,
                       message=" ".join(x for x in msgs if x).strip(), metrics=metrics, period=period)


# ═══════════════════════════════════════════════════════════════════════════
# Payments (configuration only — no quota API, no polling)
# ═══════════════════════════════════════════════════════════════════════════

def check_payments() -> Dict[str, Any]:
    try:
        from app.billing.provider import payments_status
        pay = payments_status()
    except Exception:
        pay = {"active": "unknown", "providers": {}}
    active = str(pay.get("active") or "unknown")
    prov = (pay.get("providers") or {}).get(active) or {}
    label = {"razorpay": "Payments (Razorpay)", "stripe": "Payments (Stripe)"}.get(active, "Payments")
    metrics: List[Dict[str, Any]] = []
    db = get_sync_db()
    if db is not None:
        try:
            since = utcnow() - timedelta(hours=24)
            n = db.payment_events.count_documents({"created_at": {"$gte": since},
                                                   "$or": [{"http_status": 429}, {"status_code": 429}]})
            if n:
                metrics.append(metric("rate_limited_24h", "Requests the gateway rate-limited (24 h)", used=n,
                                      unit="requests", informational=True))
        except Exception:
            pass
    if active in ("razorpay", "stripe") and prov.get("configured"):
        msg = f"{prov.get('label') or active} is set up ({prov.get('mode') or 'unknown'} mode)."
        if not prov.get("webhook_secret_configured"):
            msg += " The webhook secret is missing, so payments are not confirmed automatically."
        return make_status("payments", PLATFORM, configured=True, level="ok", label=label, message=msg,
                           metrics=metrics)
    return make_status("payments", PLATFORM, configured=False, level="not_configured", label=label,
                       message="No live payment gateway is set up (test payments only). Set it up in "
                               "Settings → Payments.", metrics=metrics)


# ═══════════════════════════════════════════════════════════════════════════
# LeadAI's own meters: tokens and plan limits of an organization
# ═══════════════════════════════════════════════════════════════════════════

PLAN_METRICS = {
    "monthly_searches": ("Searches this month", "searches"),
    "monthly_posts": ("Posts collected this month", "posts"),
    "monthly_comments": ("Comments collected this month", "comments"),
    "monthly_ai_analyses": ("AI analyses this month", "analyses"),
    "monthly_exports": ("Exports this month", "exports"),
    "api_requests": ("API requests this month", "requests"),
}


def check_tokens(organization_id: str, org: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """None when the organization is not metered by tokens."""
    from app.billing.tokens import get_balance
    bal = get_balance(str(organization_id))
    if not bal:
        return None
    scope = org_scope(organization_id)
    org = org if org is not None else _org_doc(str(organization_id))
    warn, crit = thresholds(_org_warn(org))
    m = metric("tokens", "Tokens", used=bal.get("used"), limit=bal.get("allocated") or None,
               remaining=bal.get("remaining"), unit="tokens", resets_at=bal.get("expires_at"))
    try:
        from app.lifecycle.config import token_cost
        search_cost = token_cost("search")
    except Exception:
        search_cost = 0
    level = metric_level(m, warn, crit)
    remaining = int(bal.get("remaining") or 0)
    if bal.get("expired"):
        level, msg = "exhausted", "Your token allowance has expired. Choose a plan to get more."
    elif search_cost and remaining < search_cost:
        level = "exhausted"
        msg = (f"Only {remaining:,} tokens left — a search needs {search_cost:,}. Top up or upgrade to "
               "keep searching.")
    elif level == "exhausted":
        msg = "All tokens are used. Top up or upgrade to continue."
    elif level != "ok":
        msg = f"{describe_metric(m)}. {remaining:,} tokens left."
    else:
        msg = f"{remaining:,} of {fmt_number(bal.get('allocated'))} tokens left."
    m["level"] = level
    period = f"{bal.get('source') or 'tokens'}-{bal.get('allocated')}-{str(bal.get('expires_at') or 'open')[:10]}"
    return make_status("tokens", scope, configured=True, level=level, message=msg, metrics=[m],
                       period=period)


def plan_metrics_sync(organization_id: str) -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]]:
    """(plan, usage document, metrics) without the event loop (worker threads)."""
    from app.billing.entitlements import EntitlementService
    from app.billing.usage import METRIC_TO_COUNTER_FIELD, get_current_usage_doc_sync
    s_org = str(organization_id)
    plan = EntitlementService.get_effective_plan_sync(s_org) or {}
    usage = get_current_usage_doc_sync(s_org) or {}
    limits = plan.get("limits") or {}
    bonuses: Dict[str, int] = {}
    members = 0
    db = get_sync_db()
    if db is not None:
        try:
            for g in db.temporary_entitlements.find({
                    "organization_id": s_org, "entitlement_type": "credit",
                    "key": {"$in": list(METRIC_TO_COUNTER_FIELD)},
                    "$or": [{"expires_at": None}, {"expires_at": {"$gt": utcnow()}}]}):
                try:
                    bonuses[g["key"]] = bonuses.get(g["key"], 0) + int(g.get("value", 0))
                except Exception:
                    pass
            members = db.organization_members.count_documents({"organization_id": s_org, "status": "active"})
        except Exception:
            pass
    resets = usage.get("period_end")
    out = []
    for key, field in METRIC_TO_COUNTER_FIELD.items():
        try:
            limit = int(limits.get(key, 0) or 0) + bonuses.get(key, 0)
        except (TypeError, ValueError):
            limit = 0
        if limit <= 0:
            continue
        label, unit = PLAN_METRICS.get(key, (key.replace("_", " ").capitalize(), ""))
        out.append(metric(key, label, used=usage.get(field, 0) or 0, limit=limit, unit=unit,
                          resets_at=resets))
    try:
        team = int(limits.get("team_members", 0) or 0)
    except (TypeError, ValueError):
        team = 0
    if team > 0:
        out.append(metric("team_members", "Team members", used=members, limit=team, unit="members",
                          informational=True))
    return plan, usage, out


def check_plan(organization_id: str, org: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    try:
        plan, usage, metrics = plan_metrics_sync(str(organization_id))
    except Exception as e:
        logger.debug("plan meters failed for %s: %s", organization_id, e)
        return None
    org = org if org is not None else _org_doc(str(organization_id))
    warn, crit = thresholds(_org_warn(org))
    for m in metrics:
        if not m.get("informational"):
            m["level"] = level_for_percent(m.get("percent"), warn, crit)
    level = level_from_metrics(metrics, warn, crit)
    hot = [m for m in metrics if not m.get("informational") and m.get("level") not in (None, "ok")]
    hot.sort(key=lambda m: m.get("percent") or 0, reverse=True)
    name = plan.get("name") or "your plan"
    if not metrics:
        msg = f"{name} has no monthly limits."
    elif hot and hot[0]["level"] == "exhausted":
        msg = f"{hot[0]['label']} limit of {name} is reached ({describe_metric(hot[0])}). Upgrade or wait for the next period."
    elif hot:
        msg = f"{describe_metric(hot[0])} on {name}."
    else:
        msg = f"Within the limits of {name}."
    start = aware(usage.get("period_start"))
    period = f"plan-{start.strftime('%Y-%m-%d') if start else now_utc().strftime('%Y-%m')}"
    return make_status("plan", org_scope(organization_id), configured=True, level=level, message=msg,
                       metrics=metrics, period=period, plan_name=plan.get("name"))
