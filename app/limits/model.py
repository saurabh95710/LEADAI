"""Provider usage / limit status model shared by checkers, storage, alerts and APIs.

A *status* describes one third-party API (or LeadAI's own meter) for one
*scope*:

    {provider, scope ("platform" | "org:<id>"), organization_id | None, label,
     configured, level, message, metrics: [metric, ...], checked_at,
     next_check_at, help_link, period, event?, block?}

``level`` is one of LEVELS. It comes from the worst metric percentage
against the admin thresholds (warn % / critical %; 100 % = exhausted) or
from an error state (HTTP 402 / quota error = exhausted, rejected key =
error, could not check = unknown).

A *metric* is ``{key, label, used, limit, remaining, unit, percent,
resets_at, source, informational}`` where ``source`` says where the number
comes from: ``provider_api`` (read from the provider), ``counted`` (LeadAI
counted its own calls) or ``configured`` (a limit an admin typed in).
``informational`` metrics (momentary meters such as Apify memory in use, or
team seats) are shown but never raise the level or an alert.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

PLATFORM = "platform"

LEVELS = ("ok", "warning", "critical", "exhausted", "error", "unknown", "not_configured")
# display order (worst first when sorted descending)
DISPLAY_RANK = {"not_configured": 0, "unknown": 1, "ok": 2, "warning": 3, "critical": 4,
                "error": 5, "exhausted": 6}
# alert escalation: a notice is sent again only when this rank goes up
ALERT_RANK = {"not_configured": 0, "unknown": 0, "ok": 1, "warning": 2, "critical": 3,
              "error": 3, "exhausted": 4}
ALERT_LEVELS = ("warning", "critical", "error", "exhausted")
SOURCES = ("provider_api", "counted", "configured")

PROVIDER_LABELS = {
    "apify": "Apify (scraping)",
    "gemini": "Google Gemini (AI analysis)",
    "email": "Email (SMTP)",
    "sms": "SMS",
    "payments": "Payments",
    "tokens": "LeadAI tokens",
    "plan": "Plan limits",
}
PLATFORM_PROVIDERS = ("apify", "gemini", "email", "sms", "payments")
ORG_PROVIDERS = ("apify", "gemini", "tokens", "plan")

# where each problem is fixed (portal routes)
HELP_LINKS = {
    ("platform", "apify"): "/superadmin#/provider-keys",
    ("platform", "gemini"): "/superadmin#/provider-keys",
    ("platform", "email"): "/superadmin#/settings?tab=notifications",
    ("platform", "sms"): "/superadmin#/messaging",
    ("platform", "payments"): "/superadmin#/settings?tab=payments",
    ("org", "apify"): "/org-admin#integrations",
    ("org", "gemini"): "/org-admin#integrations",
    ("org", "tokens"): "/org-admin#subscription",
    ("org", "plan"): "/org-admin#subscription",
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: Any) -> Optional[datetime]:
    """A timezone-aware UTC datetime from a datetime / ISO string, else None."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            d = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def iso(value: Any) -> Optional[str]:
    d = aware(value)
    return d.isoformat() if d else None


def org_scope(organization_id: Any) -> str:
    return f"org:{organization_id}"


def scope_org_id(scope: str) -> Optional[str]:
    return scope[4:] if scope.startswith("org:") else None


def status_id(scope: str, provider: str) -> str:
    return f"{scope}:{provider}"


def help_link(scope: str, provider: str) -> Optional[str]:
    return HELP_LINKS.get(("platform" if scope == PLATFORM else "org", provider))


# ── thresholds ──────────────────────────────────────────────────────────────

def thresholds(warn: Optional[int] = None) -> Tuple[int, int]:
    """(warn %, critical %) from the API limit settings (sane bounds)."""
    try:
        from app.admin.settings import get_setting
        w = int(get_setting("api_limits.warn_percent") or 80)
        c = int(get_setting("api_limits.critical_percent") or 95)
    except Exception:
        w, c = 80, 95
    if warn is not None:
        w = warn
    w = min(max(w, 1), 99)
    c = min(max(c, w), 100)
    return w, c


def level_for_percent(percent: Optional[float], warn: int, critical: int) -> str:
    if percent is None:
        return "ok"
    if percent >= 100:
        return "exhausted"
    if percent >= critical:
        return "critical"
    if percent >= warn:
        return "warning"
    return "ok"


# ── metrics ─────────────────────────────────────────────────────────────────

def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_number(value: Optional[float]) -> Any:
    if value is None:
        return None
    return int(value) if float(value).is_integer() else round(value, 4)


def metric(key: str, label: str, *, used: Any = None, limit: Any = None, remaining: Any = None,
           unit: str = "", resets_at: Any = None, source: str = "counted",
           informational: bool = False) -> Dict[str, Any]:
    """One meter. ``percent`` = used / limit (when both are known and limit > 0);
    ``remaining`` defaults to limit - used."""
    u, lim, rem = _num(used), _num(limit), _num(remaining)
    if lim is not None and lim <= 0:
        lim = None
    if rem is None and u is not None and lim is not None:
        rem = max(0.0, lim - u)
    percent = round(u * 100.0 / lim, 1) if (u is not None and lim) else None
    return {"key": key, "label": label, "used": _clean_number(u), "limit": _clean_number(lim),
            "remaining": _clean_number(rem), "unit": unit, "percent": percent,
            "resets_at": iso(resets_at), "source": source if source in SOURCES else "counted",
            "informational": bool(informational)}


def metric_level(m: Dict[str, Any], warn: int, critical: int) -> str:
    if m.get("level"):
        return str(m["level"])
    return level_for_percent(m.get("percent"), warn, critical)


def worst(levels: Iterable[str]) -> str:
    out = None
    for lv in levels:
        if out is None or DISPLAY_RANK.get(lv, 0) > DISPLAY_RANK.get(out, 0):
            out = lv
    return out or "ok"


def level_from_metrics(metrics: List[Dict[str, Any]], warn: int, critical: int) -> str:
    """Worst level of the metrics that count (informational meters never do)."""
    levels = [metric_level(m, warn, critical) for m in metrics if not m.get("informational")]
    return worst(levels) if levels else "ok"


def make_status(provider: str, scope: str, *, configured: bool, level: str, message: str,
                metrics: Optional[List[Dict[str, Any]]] = None, label: Optional[str] = None,
                period: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
    now = now_utc()
    out = {
        "provider": provider, "scope": scope, "organization_id": scope_org_id(scope),
        "label": label or PROVIDER_LABELS.get(provider, provider),
        "configured": bool(configured), "level": level if level in LEVELS else "unknown",
        "message": message, "metrics": metrics or [], "checked_at": now.isoformat(),
        "next_check_at": None, "help_link": help_link(scope, provider),
        "period": period or now.strftime("%Y-%m-%d"),
    }
    out.update(extra)
    return out


def fmt_number(value: Any) -> str:
    if value is None:
        return "?"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f.is_integer():
        return f"{int(f):,}"
    return f"{f:,.2f}"


def describe_metric(m: Dict[str, Any]) -> str:
    """"Monthly usage: 42.10 of 49 USD used (86%)" in plain words."""
    unit = f" {m['unit']}" if m.get("unit") else ""
    if m.get("limit") is not None and m.get("used") is not None:
        pct = f" ({m['percent']:g}%)" if m.get("percent") is not None else ""
        return f"{m['label']}: {fmt_number(m['used'])} of {fmt_number(m['limit'])}{unit} used{pct}"
    if m.get("remaining") is not None:
        return f"{m['label']}: {fmt_number(m['remaining'])}{unit} left"
    if m.get("used") is not None:
        return f"{m['label']}: {fmt_number(m['used'])}{unit}"
    return str(m.get("label") or m.get("key"))


# ── Pacific time (Gemini's daily quota resets at 00:00 America/Los_Angeles) ──

def _nth_sunday(year: int, month: int, n: int) -> datetime:
    d = datetime(year, month, 1)
    first = d + timedelta(days=(6 - d.weekday()) % 7)
    return first + timedelta(days=7 * (n - 1))


_PST, _PDT = timedelta(hours=-8), timedelta(hours=-7)


def pacific_offset_rule(at_utc: datetime) -> timedelta:
    """US Pacific UTC offset by the US DST rule (no tzdata needed): daylight
    time from the second Sunday of March 02:00 local (PST → 10:00 UTC) to the
    first Sunday of November 02:00 local (PDT → 09:00 UTC)."""
    at_utc = at_utc if at_utc.tzinfo else at_utc.replace(tzinfo=timezone.utc)
    y = at_utc.astimezone(timezone.utc).year
    start = _nth_sunday(y, 3, 2).replace(hour=10, tzinfo=timezone.utc)
    end = _nth_sunday(y, 11, 1).replace(hour=9, tzinfo=timezone.utc)
    return _PDT if start <= at_utc < end else _PST


def pacific_offset(at_utc: datetime) -> timedelta:
    """UTC offset of America/Los_Angeles (tzdata when installed, else the rule)."""
    try:
        from zoneinfo import ZoneInfo
        off = at_utc.astimezone(ZoneInfo("America/Los_Angeles")).utcoffset()
        if off is not None:
            return off
    except Exception:
        pass
    return pacific_offset_rule(at_utc)


def _pacific_local_to_utc(local: datetime) -> datetime:
    """UTC instant of a naive Pacific wall-clock time (midnight is never in a
    DST gap / overlap: changes happen at 02:00)."""
    for off in (_PST, _PDT):
        cand = (local - off).replace(tzinfo=timezone.utc)
        if pacific_offset(cand) == off:
            return cand
    return (local - _PST).replace(tzinfo=timezone.utc)


def pacific_day(at_utc: Optional[datetime] = None) -> Tuple[str, datetime, datetime]:
    """(YYYY-MM-DD in Pacific time, start of that day in UTC, next reset in UTC),
    correct on the DST change days too."""
    at_utc = at_utc or now_utc()
    local = (at_utc + pacific_offset(at_utc)).replace(tzinfo=None)
    day_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = _pacific_local_to_utc(day_local)
    nxt_utc = _pacific_local_to_utc(day_local + timedelta(days=1))
    return day_local.strftime("%Y-%m-%d"), start_utc, nxt_utc
