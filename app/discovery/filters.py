"""Keyword-search filters — PURE functions (no database, no network).

Validation
    ``validate_filters(raw, max_recent_posts=...)`` -> (clean, notes); raises
    ``FilterValidationError`` with per-field messages for negative,
    non-numeric or absurd values. ``recent_posts_to_analyze`` is clamped to
    the caller's cap (a note says so).

Evaluation
    Every enabled filter is ANDed. Each yields an outcome dict
    ``{filter, outcome, message, value, threshold}`` where outcome is

      pass         measured and satisfied
      fail         measured and not satisfied ("followers 800 < min 5000")
      unsupported  the platform can't measure it
      unknown      measurable on this platform but this profile lacks the value

    ``unsupported`` / ``unknown`` never pass or fail silently: they are listed
    on the candidate, and with ``strict=True`` they reject it.

    Profile filters run first (``evaluate_profile``); post filters
    (average / total comments, engagement, and activity when the last post
    date is not known yet) run on sampled posts (``evaluate_posts``).

Location / business type
    ``location_keyword`` is a list of places separated by commas or
    semicolons, most specific first ("Kota, Rajasthan"). ``location_match``
    "all" (default, also for runs stored before it existed) needs every place,
    "any" one of them. ``category_keyword`` holds alternatives ("software, IT
    services"); one is enough. Both match case-insensitively on WORD
    boundaries after punctuation is normalised, so "kota" never matches
    "Dakota".
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.discovery.capabilities import FILTERS, POST_FILTERS, UNSUPPORTED, capability

PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"
UNSUPPORTED_OUTCOME = UNSUPPORTED

DEFAULT_RECENT_POSTS = 10

# (min, max) — anything outside is rejected as invalid / absurd
_NUMERIC_BOUNDS: Dict[str, Tuple[float, float]] = {
    "min_followers": (0, 10_000_000_000),
    "max_followers": (0, 10_000_000_000),
    "min_posts": (0, 10_000_000),
    "recent_posts_to_analyze": (1, 1000),
    "min_avg_comments": (0, 10_000_000),
    "min_total_comments": (0, 1_000_000_000),
    "min_engagement_rate": (0, 1000),
    "active_within_days": (1, 36_500),
}
_BOOL_FILTERS = ("business_account_only", "has_contact_info", "verified_only")
_STR_FILTERS = {"location_keyword": 100, "category_keyword": 200, "language": 35}
LOCATION_MATCH = ("all", "any")
_MAX_EXCLUDE = 50
_MAX_EXCLUDE_LEN = 100

_UNIT_SECONDS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 7 * 86400,
                 "month": 30 * 86400, "year": 365 * 86400}
_RELATIVE_RE = re.compile(r"^(?:[a-z ]*?)(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago$")


class FilterValidationError(ValueError):
    def __init__(self, errors: Dict[str, str]):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


def _number(key: str, value: Any, errors: Dict[str, str]) -> Optional[float]:
    if isinstance(value, bool):
        errors[key] = "must be a number"
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            errors[key] = "must be a number"
            return None
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        errors[key] = "must be a number"
        return None
    lo, hi = _NUMERIC_BOUNDS[key]
    if value < 0:
        errors[key] = "must not be negative"
        return None
    if value < lo or value > hi:
        errors[key] = f"must be between {lo:g} and {hi:g}"
        return None
    if FILTERS[key]["type"] == "int":
        if float(value) != int(value):
            errors[key] = "must be a whole number"
            return None
        return int(value)
    return float(value)


def validate_filters(raw: Optional[Dict[str, Any]], *,
                     max_recent_posts: Optional[int] = None,
                     default_recent_posts: int = DEFAULT_RECENT_POSTS) -> Tuple[Dict[str, Any], List[str]]:
    """Clean + validate a filters object. Disabled filters (None / False /
    empty) are dropped. ``recent_posts_to_analyze`` is always present."""
    raw = dict(raw or {})
    errors: Dict[str, str] = {}
    notes: List[str] = []
    clean: Dict[str, Any] = {}
    for key, value in raw.items():
        if key not in FILTERS:
            errors[key] = "unknown filter"
            continue
        if value is None or value == "" or value == []:
            continue
        if key in _NUMERIC_BOUNDS:
            num = _number(key, value, errors)
            if num is not None:
                clean[key] = num
        elif key in _BOOL_FILTERS:
            if isinstance(value, bool):
                if value:
                    clean[key] = True
            elif isinstance(value, str) and value.strip().lower() in ("true", "false"):
                if value.strip().lower() == "true":
                    clean[key] = True
            else:
                errors[key] = "must be true or false"
        elif key in _STR_FILTERS:
            if not isinstance(value, str):
                errors[key] = "must be text"
            elif len(value.strip()) > _STR_FILTERS[key]:
                errors[key] = f"must be at most {_STR_FILTERS[key]} characters"
            elif value.strip():
                clean[key] = value.strip()
        elif key == "location_match":
            v = value.strip().lower() if isinstance(value, str) else value
            if v not in LOCATION_MATCH:
                errors[key] = "must be 'all' or 'any'"
            else:
                clean[key] = v
        elif key == "exclude_keywords":
            items = value.split(",") if isinstance(value, str) else value
            if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
                errors[key] = "must be a list of words"
                continue
            words: List[str] = []
            for w in items:
                w = w.strip()
                if not w:
                    continue
                if len(w) > _MAX_EXCLUDE_LEN:
                    errors[key] = f"each word must be at most {_MAX_EXCLUDE_LEN} characters"
                    break
                if w.lower() not in (x.lower() for x in words):
                    words.append(w)
            if key not in errors:
                if len(words) > _MAX_EXCLUDE:
                    errors[key] = f"at most {_MAX_EXCLUDE} words"
                elif words:
                    clean[key] = words
    if "location_match" in clean and "location_keyword" not in clean:
        clean.pop("location_match")      # nothing to apply it to
    if "location_keyword" in clean and not parse_places(clean["location_keyword"]):
        errors["location_keyword"] = "must name at least one place"
    if "category_keyword" in clean and not parse_terms(clean["category_keyword"]):
        errors["category_keyword"] = "must name at least one business type"
    if ("min_followers" in clean and "max_followers" in clean
            and clean["min_followers"] > clean["max_followers"]):
        errors["max_followers"] = "must be greater than or equal to min_followers"
    if errors:
        raise FilterValidationError(errors)
    n = clean.get("recent_posts_to_analyze") or default_recent_posts
    if max_recent_posts and n > max_recent_posts:
        notes.append(f"recent_posts_to_analyze lowered from {n} to {max_recent_posts} (your limit).")
        n = max_recent_posts
    clean["recent_posts_to_analyze"] = int(max(1, n))
    return clean, notes


# ── text matching (location / business type) ───────────────────────────────

_SPLIT_RE = re.compile(r"[,;]")
_NON_WORD_RE = re.compile(r"[\W_]+")


def parse_places(text: Any) -> List[str]:
    """"Kota, Rajasthan" -> ["Kota", "Rajasthan"] (most specific first)."""
    if not isinstance(text, str):
        return []
    out: List[str] = []
    for part in _SPLIT_RE.split(text):
        part = " ".join(part.split())
        if part and normalize_text(part) and part.lower() not in (p.lower() for p in out):
            out.append(part)
    return out


def parse_terms(text: Any) -> List[str]:
    """"software, IT services" -> ["software", "IT services"] (alternatives)."""
    return parse_places(text)


def normalize_text(text: Any) -> str:
    """Lowercase, punctuation -> spaces, whitespace collapsed."""
    return " ".join(_NON_WORD_RE.sub(" ", str(text or "").lower()).split())


def contains_phrase(normalized: str, phrase: str) -> bool:
    """Whole-word (word-boundary) match of ``phrase`` in normalized text."""
    p = normalize_text(phrase)
    return bool(p) and f" {p} " in f" {normalized} "


def _texts(profile: Dict[str, Any], keys: Tuple[str, ...]) -> List[str]:
    out: List[str] = []
    for k in keys:
        v = profile.get(k)
        for item in (v if isinstance(v, (list, tuple)) else [v]):
            if isinstance(item, (str, int, float)) and not isinstance(item, bool) and str(item).strip():
                out.append(str(item))
    return out


# where a place may appear (a location field, or the name / bio / category);
# "location data" = the fields whose absence makes the location unknown
LOCATION_FIELDS = ("name", "bio", "address", "city", "state", "country", "location", "locations",
                   "category", "categories")
LOCATION_DATA_FIELDS = ("address", "city", "state", "country", "location", "locations", "bio")
CATEGORY_FIELDS = ("category", "categories", "bio", "name")


def _quoted(items: List[str]) -> str:
    return ", ".join(f"'{i}'" for i in items)


def _location_outcome(profile: Dict[str, Any], filters: Dict[str, Any], platform: str) -> Dict[str, Any]:
    raw = filters["location_keyword"]
    places = parse_places(raw) or [str(raw)]
    mode = filters.get("location_match") or "all"
    if not _texts(profile, LOCATION_DATA_FIELDS):
        return _missing(platform, "location_keyword", "location", raw)
    text = normalize_text("\n".join(_texts(profile, LOCATION_FIELDS)))
    found = [p for p in places if contains_phrase(text, p)]
    missing = [p for p in places if p not in found]
    value = {"matched": found, "missing": missing, "match": mode}
    if (not missing) if mode == "all" else bool(found):
        return _o("location_keyword", PASS, f"location matches {', '.join(found)}", value, raw)
    if mode == "any" and len(places) > 1:
        return _o("location_keyword", FAIL, f"location: none of {_quoted(places)} found", value, raw)
    return _o("location_keyword", FAIL, f"location: {_quoted(missing)} not found", value, raw)


def _category_outcome(profile: Dict[str, Any], filters: Dict[str, Any], platform: str) -> Dict[str, Any]:
    raw = filters["category_keyword"]
    terms = parse_terms(raw) or [str(raw)]
    texts = _texts(profile, CATEGORY_FIELDS)
    if not texts:
        return _missing(platform, "category_keyword", "business type", raw)
    text = normalize_text("\n".join(texts))
    found = [t for t in terms if contains_phrase(text, t)]
    if found:
        return _o("category_keyword", PASS, f"business type matches {_quoted(found)}", found, raw)
    if len(terms) > 1:
        return _o("category_keyword", FAIL, f"business type: none of {_quoted(terms)} found", None, raw)
    return _o("category_keyword", FAIL, f"business type: {_quoted(terms)} not found", None, raw)


# ── value helpers ────────────────────────────────────────────────────────────

def to_datetime(value: Any) -> Optional[datetime]:
    """ISO string / datetime / epoch seconds or milliseconds -> aware datetime."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, int) and 10_000_000 <= value <= 99_999_999:
        return to_datetime(str(value))        # an 8-digit number is YYYYMMDD, not an epoch
    if isinstance(value, (int, float)):
        v = float(value)
        if v > 1e12:          # milliseconds
            v /= 1000.0
        try:
            return datetime.fromtimestamp(v, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():      # YYYYMMDD (e.g. YouTube uploadDate)
        try:
            return datetime.strptime(text, "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    if text.isdigit():
        return to_datetime(int(text))
    rel = _RELATIVE_RE.match(text.lower())
    if rel:  # "3 days ago", "Streamed 2 weeks ago"
        n, unit = int(rel.group(1)), rel.group(2)
        return datetime.now(timezone.utc) - timedelta(seconds=n * _UNIT_SECONDS[unit])
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.2f}".rstrip("0").rstrip(".") if not v.is_integer() else str(int(v))
    return str(v)


def _o(key: str, outcome: str, message: str, value: Any = None, threshold: Any = None) -> Dict[str, Any]:
    return {"filter": key, "outcome": outcome, "message": message,
            "value": value, "threshold": threshold}


def _missing(platform: str, key: str, what: str, threshold: Any = None) -> Dict[str, Any]:
    if capability(platform, key) == UNSUPPORTED:
        return _o(key, UNSUPPORTED_OUTCOME, f"{what} not available on {platform}", None, threshold)
    return _o(key, UNKNOWN, f"{what} unknown", None, threshold)


def _min(key: str, platform: str, what: str, value: Any, threshold: Any) -> Dict[str, Any]:
    if value is None:
        return _missing(platform, key, what, threshold)
    if value >= threshold:
        return _o(key, PASS, f"{what} {_fmt(value)} >= min {_fmt(threshold)}", value, threshold)
    return _o(key, FAIL, f"{what} {_fmt(value)} < min {_fmt(threshold)}", value, threshold)


def _contacts_found(profile: Dict[str, Any]) -> bool:
    c = profile.get("contacts") or {}
    return any(c.get(k) for k in ("emails", "phones", "websites"))


def _activity(key: str, platform: str, last: Any, days: int, now: datetime) -> Dict[str, Any]:
    dt = to_datetime(last)
    if dt is None:
        return _missing(platform, key, "last post date", days)
    age = max(0, (now - dt).days)
    iso = dt.isoformat()
    if age <= days:
        return _o(key, PASS, f"last post {_days(age)} ago <= {days}", iso, days)
    return _o(key, FAIL, f"last post {_days(age)} ago > {days}", iso, days)


def _days(n: int) -> str:
    return "1 day" if n == 1 else f"{n} days"


def evaluate_profile(profile: Dict[str, Any], filters: Dict[str, Any],
                     now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Profile-level outcomes (in a stable order). ``active_within_days`` is
    judged here only when the last post date is already known."""
    now = now or datetime.now(timezone.utc)
    platform = profile.get("platform") or ""
    out: List[Dict[str, Any]] = []
    if profile.get("private") is True:
        out.append(_o("private_account", FAIL, "private account", True, None))
    followers = profile.get("followers")
    if "min_followers" in filters:
        out.append(_min("min_followers", platform, "followers", followers, filters["min_followers"]))
    if "max_followers" in filters:
        t = filters["max_followers"]
        if followers is None:
            out.append(_missing(platform, "max_followers", "followers", t))
        elif followers <= t:
            out.append(_o("max_followers", PASS, f"followers {_fmt(followers)} <= max {_fmt(t)}", followers, t))
        else:
            out.append(_o("max_followers", FAIL, f"followers {_fmt(followers)} > max {_fmt(t)}", followers, t))
    if "min_posts" in filters:
        out.append(_min("min_posts", platform, "posts", profile.get("posts_count"), filters["min_posts"]))
    if filters.get("business_account_only"):
        v = profile.get("is_business")
        if v is None:
            out.append(_missing(platform, "business_account_only", "business account flag", True))
        else:
            out.append(_o("business_account_only", PASS if v else FAIL,
                          "business account" if v else "not a business account", v, True))
    if filters.get("verified_only"):
        v = profile.get("verified")
        if v is None:
            out.append(_missing(platform, "verified_only", "verified badge", True))
        else:
            out.append(_o("verified_only", PASS if v else FAIL, "verified" if v else "not verified", v, True))
    if filters.get("has_contact_info"):
        found = _contacts_found(profile)
        out.append(_o("has_contact_info", PASS if found else FAIL,
                      "contact info found" if found else "no contact info found", found, True))
    if filters.get("location_keyword"):
        out.append(_location_outcome(profile, filters, platform))
    if filters.get("category_keyword"):
        out.append(_category_outcome(profile, filters, platform))
    if filters.get("language"):
        lang = profile.get("language")
        want = str(filters["language"]).strip().lower()
        if not lang:
            out.append(_o("language", UNSUPPORTED_OUTCOME, f"language not available on {platform}",
                           None, filters["language"]))
        elif str(lang).strip().lower().startswith(want) or want.startswith(str(lang).strip().lower()):
            out.append(_o("language", PASS, f"language {lang}", lang, filters["language"]))
        else:
            out.append(_o("language", FAIL, f"language {lang} is not {filters['language']}",
                          lang, filters["language"]))
    if filters.get("exclude_keywords"):
        name, bio = str(profile.get("name") or ""), str(profile.get("bio") or "")
        if not name and not bio:
            out.append(_missing(platform, "exclude_keywords", "name and bio", filters["exclude_keywords"]))
        else:
            text = f"{name}\n{bio}".lower()
            hit = next((w for w in filters["exclude_keywords"] if w.lower() in text), None)
            if hit:
                out.append(_o("exclude_keywords", FAIL, f"name/bio contains excluded keyword '{hit}'",
                              hit, filters["exclude_keywords"]))
            else:
                out.append(_o("exclude_keywords", PASS, "no excluded keyword", None,
                              filters["exclude_keywords"]))
    if "active_within_days" in filters and to_datetime(profile.get("last_post_at")) is not None:
        out.append(_activity("active_within_days", platform, profile.get("last_post_at"),
                             filters["active_within_days"], now))
    return out


def post_metrics(profile: Dict[str, Any], posts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Metrics of the analyzed posts. Unknown counts stay unknown (None)."""
    comments = [p.get("comments_count") for p in posts if isinstance(p.get("comments_count"), (int, float))]
    likes = [p.get("likes_count") for p in posts if isinstance(p.get("likes_count"), (int, float))]
    dates = [d for d in (to_datetime(p.get("posted_at")) for p in posts) if d is not None]
    interactions: List[float] = []
    for p in posts:
        parts = [p.get(k) for k in ("likes_count", "comments_count")]
        if all(not isinstance(v, (int, float)) for v in parts):
            continue
        total = sum(v for v in parts + [p.get("shares_count")] if isinstance(v, (int, float)))
        interactions.append(float(total))
    followers = profile.get("followers")
    engagement = None
    if interactions and isinstance(followers, (int, float)) and followers > 0:
        engagement = round(sum(interactions) / len(interactions) / followers * 100, 4)
    last = max(dates) if dates else to_datetime(profile.get("last_post_at"))
    return {
        "posts_sampled": len(posts),
        "avg_comments": round(sum(comments) / len(comments), 2) if comments else None,
        "total_comments": int(sum(comments)) if comments else None,
        "avg_likes": round(sum(likes) / len(likes), 2) if likes else None,
        "engagement_rate": engagement,
        "last_post_at": last.isoformat() if last else None,
    }


def evaluate_posts(profile: Dict[str, Any], posts: List[Dict[str, Any]], filters: Dict[str, Any],
                   now: Optional[datetime] = None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Post-level outcomes + metrics for the sampled posts."""
    now = now or datetime.now(timezone.utc)
    platform = profile.get("platform") or ""
    m = post_metrics(profile, posts)
    out: List[Dict[str, Any]] = []
    if "min_avg_comments" in filters:
        out.append(_min("min_avg_comments", platform, "average comments", m["avg_comments"],
                        filters["min_avg_comments"]))
    if "min_total_comments" in filters:
        out.append(_min("min_total_comments", platform, "total comments", m["total_comments"],
                        filters["min_total_comments"]))
    if "min_engagement_rate" in filters:
        t = filters["min_engagement_rate"]
        v = m["engagement_rate"]
        if v is None:
            out.append(_missing(platform, "min_engagement_rate", "engagement rate", t))
        elif v >= t:
            out.append(_o("min_engagement_rate", PASS, f"engagement rate {_fmt(v)}% >= min {_fmt(t)}%", v, t))
        else:
            out.append(_o("min_engagement_rate", FAIL, f"engagement rate {_fmt(v)}% < min {_fmt(t)}%", v, t))
    if "active_within_days" in filters:
        out.append(_activity("active_within_days", platform, m["last_post_at"],
                             filters["active_within_days"], now))
    return out, m


def needs_post_filters(filters: Dict[str, Any], profile_outcomes: List[Dict[str, Any]]) -> bool:
    """True when some enabled filter still needs sampled posts."""
    done = {o["filter"] for o in profile_outcomes}
    return any(k in filters and k not in done for k in POST_FILTERS)


def decide(outcomes: List[Dict[str, Any]], strict: bool) -> Dict[str, Any]:
    """AND every outcome: any fail rejects; with ``strict`` so does any
    unknown / unsupported. Returns {passed, unsupported[], unknown[], failed[]}."""
    failed = [o["filter"] for o in outcomes if o["outcome"] == FAIL]
    unsupported = [o["filter"] for o in outcomes if o["outcome"] == UNSUPPORTED_OUTCOME]
    unknown = [o["filter"] for o in outcomes if o["outcome"] == UNKNOWN]
    passed = not failed and not (strict and (unsupported or unknown))
    return {"passed": passed, "failed": failed, "unsupported": unsupported, "unknown": unknown}


def merge_outcomes(*groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Later groups replace earlier outcomes of the same filter."""
    by_key: Dict[str, Dict[str, Any]] = {}
    for group in groups:
        for o in group:
            by_key[o["filter"]] = o
    return list(by_key.values())
