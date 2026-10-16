"""Shared adapter contract + helpers.

Adapters are the ONLY code that knows actor input/output field names. Every
adapter returns profiles in one normalized shape (``empty_profile``); a value
the actor did not return stays None ("unknown"), never 0.

  discover(keyword, limit, filters) -> [profile]    the keyword search actor
  needs_enrichment(profile) -> bool                  discovery lacked key fields
  enrich(profiles)          -> {profile_id: profile|None}   None = not returned
  recent_posts(profile, n)  -> [post]                the recent-posts actor

Posts are ``{id, url, comments_count, likes_count, shares_count, posted_at,
raw}`` — ``raw`` is the untouched actor item so the deep scrape can store it
through the platform scraper's ``normalize_post`` (same docs as URL search).

Filters pushed into the search (``discovery_request``): only inputs an
actor's published schema documents are sent — the location as the actor's
``locations[]`` (Facebook, LinkedIn) or appended to the search text
(Instagram, YouTube: at most one place + one business-type term, query kept
<= ``MAX_QUERY_LEN`` characters), and the YouTube channel-search actor's own
subscriber / video / verified limits. Everything is still checked afterwards
by the filters. ``last_query`` records what was sent
(``{platform, keyword, query_text, locations, extra_inputs}``).
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from app.social.url_detector import UrlError, detect_social_url

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")
URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)


class AdapterError(RuntimeError):
    """A discovery adapter could not produce results (actor misconfigured…)."""


MAX_QUERY_LEN = 60


def filter_places(filters: Optional[Dict[str, Any]]) -> List[str]:
    from app.discovery.filters import parse_places
    return parse_places((filters or {}).get("location_keyword"))


def filter_terms(filters: Optional[Dict[str, Any]]) -> List[str]:
    from app.discovery.filters import parse_terms
    return parse_terms((filters or {}).get("category_keyword"))


def location_text(filters: Optional[Dict[str, Any]]) -> Optional[str]:
    """The location exactly as typed (whitespace tidied) — for actors that
    take a free-text location (``locations[]``)."""
    raw = (filters or {}).get("location_keyword")
    text = " ".join(raw.split()) if isinstance(raw, str) else ""
    return text or None


def search_text(keyword: str, *extras: Optional[str], max_len: int = MAX_QUERY_LEN) -> str:
    """``keyword`` + each extra term that isn't in it already, while the query
    stays within ``max_len`` characters (an extra that doesn't fit is skipped)."""
    from app.discovery.filters import contains_phrase, normalize_text
    query = " ".join(str(keyword or "").split())
    for extra in extras:
        extra = " ".join(str(extra or "").split())
        if not extra or contains_phrase(normalize_text(query), extra):
            continue
        if len(query) + 1 + len(extra) <= max_len:
            query = f"{query} {extra}"
    return query


def query_record(platform: str, keyword: str, query_text: str, locations: Optional[List[str]] = None,
                 extra_inputs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"platform": platform, "keyword": keyword, "query_text": query_text,
            "locations": list(locations or []), "extra_inputs": dict(extra_inputs or {})}


def empty_profile(platform: str) -> Dict[str, Any]:
    return {
        "platform": platform, "profile_id": None, "url": None, "handle": None, "name": None,
        "followers": None, "posts_count": None, "bio": None,
        "contacts": {"emails": [], "phones": [], "websites": []},
        "verified": None, "is_business": None, "category": None, "country": None,
        "address": None, "last_post_at": None, "private": None, "profile_picture": None,
        "recent_posts": [],
    }


def pick(item: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        v = item.get(key) if isinstance(item, dict) else None
        if v not in (None, "", [], {}):
            return v
    return None


def as_list(value: Any) -> List[Any]:
    """An actor field that should be a list: a list/tuple as-is, a single
    string/dict as a one-item list, anything else (None, numbers) as []."""
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, (str, dict)):
        return [value] if value else []
    return []


def as_count(value: Any) -> Optional[int]:
    """A non-negative count, or None when absent / not a number. Negative
    values (Instagram reports hidden likes as -1) are unknown."""
    if value is None or isinstance(value, (bool, list, dict)):
        return None
    if isinstance(value, (int, float)):
        return int(value) if value >= 0 else None
    from app.social.scrapers import _as_int
    parsed = _as_int(value)
    return parsed if parsed is not None and parsed >= 0 else None


def as_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return None


def canonical(raw: Any, platform: str) -> Optional[str]:
    """Canonical profile URL via the URL-search validator (personal profiles,
    groups, posts, reels… are rejected -> None)."""
    if not raw or not isinstance(raw, str):
        return None
    try:
        detected, url = detect_social_url(raw)
    except UrlError:
        return None
    except Exception:
        return None
    return url if detected == platform else None


def _dedupe(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    for v in values:
        v = str(v).strip().rstrip(".,;")
        if v and v.lower() not in (x.lower() for x in out):
            out.append(v)
    return out


def extract_contacts(*texts: Any, emails: Iterable[Any] = (), phones: Iterable[Any] = (),
                     websites: Iterable[Any] = ()) -> Dict[str, List[str]]:
    """Explicit contact fields plus anything found in free text (bios)."""
    found_emails = [str(e) for e in emails if e]
    found_phones = [str(p) for p in phones if p]
    found_sites = [str(w) for w in websites if w]
    for text in texts:
        if not text or not isinstance(text, str):
            continue
        found_emails += EMAIL_RE.findall(text)
        found_sites += URL_RE.findall(text)
        for p in PHONE_RE.findall(text):
            if sum(ch.isdigit() for ch in p) >= 8:
                found_phones.append(p.strip())
    return {"emails": _dedupe(found_emails), "phones": _dedupe(found_phones),
            "websites": _dedupe(found_sites)}


def post_entry(*, id: Any = None, url: Any = None, comments: Any = None, likes: Any = None,
               shares: Any = None, posted_at: Any = None, raw: Any = None) -> Dict[str, Any]:
    from app.discovery.filters import to_datetime
    dt = to_datetime(posted_at)
    return {"id": str(id) if id not in (None, "") else None,
            "url": str(url) if url else None,
            "comments_count": as_count(comments), "likes_count": as_count(likes),
            "shares_count": as_count(shares),
            "posted_at": dt.isoformat() if dt else None,
            "raw": raw}


def latest_post_at(posts: List[Dict[str, Any]]) -> Optional[str]:
    from app.discovery.filters import to_datetime
    dates = [d for d in (to_datetime(p.get("posted_at")) for p in posts) if d]
    return max(dates).isoformat() if dates else None


def split_raw(posts: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Any]]:
    """(posts without ``raw``, raw items) — raws are stored apart (large)."""
    clean, raws = [], []
    for p in posts:
        p = dict(p)
        raws.append(p.pop("raw", None))
        clean.append(p)
    return clean, raws


class DiscoveryAdapter:
    platform: str = ""

    def __init__(self, connector: Any, should_abort: Optional[Callable[[], bool]] = None):
        self.connector = connector
        self.should_abort = should_abort
        self.last_query: Optional[Dict[str, Any]] = None

    # ── contract ────────────────────────────────────────────────────────
    def discovery_request(self, keyword: str, limit: int,
                          filters: Optional[Dict[str, Any]] = None) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
        """(actor id, run_input, query record) of the keyword search — pure
        apart from reading which actor is configured."""
        raise NotImplementedError

    def discover(self, keyword: str, limit: int,
                 filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def needs_enrichment(self, profile: Dict[str, Any]) -> bool:
        return False

    def enrich(self, profiles: List[Dict[str, Any]]) -> Dict[str, Optional[Dict[str, Any]]]:
        return {p["profile_id"]: p for p in profiles}

    def recent_posts(self, profile: Dict[str, Any], n: int) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def posts_available(self, profile: Dict[str, Any], n: int) -> bool:
        """True when the profile already carries enough recent posts."""
        have = len(profile.get("recent_posts") or [])
        if have >= n:
            return True
        total = profile.get("posts_count")
        return isinstance(total, int) and have > 0 and have >= total

    # ── helpers ─────────────────────────────────────────────────────────
    def _run(self, actor_id: str, run_input: Dict[str, Any], label: str,
             keyword: Optional[str] = None) -> List[Dict[str, Any]]:
        if not actor_id:
            raise AdapterError(f"No Apify actor is configured for {self.platform} ({label}).")
        items = self.connector.scrape_actor(actor_id, run_input, label, keyword=keyword,
                                            should_abort=self.should_abort)
        return [i for i in items or [] if isinstance(i, dict) and not i.get("error")]

    @staticmethod
    def _chunks(seq: List[Any], size: int) -> Iterable[List[Any]]:
        for i in range(0, len(seq), size):
            yield seq[i:i + size]
