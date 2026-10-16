"""Readable names for Apify actors.

Runs record the actor's internal id (e.g. ``shu8hvrXbJbY3Eb9W``). For people
we show the actor's store name (``apify/facebook-posts-scraper``), its title
and what LeadAI uses it for. Ids are looked up once on Apify
(``GET /v2/acts/{id}``) and remembered in ``apify_actor_names``; a failed
lookup is retried after a day, and the id is shown meanwhile.
"""
import logging
from datetime import timedelta
from typing import Any, Dict, Iterable, Optional

from app.db.models import utcnow

logger = logging.getLogger(__name__)

COLLECTION = "apify_actor_names"
RETRY_AFTER = timedelta(days=1)
LOOKUPS_PER_CALL = 25

# What LeadAI uses each actor for (store name -> plain words)
PURPOSE: Dict[str, str] = {
    "apify/facebook-pages-scraper": "Facebook: page details",
    "apify/facebook-posts-scraper": "Facebook: page posts",
    "apify/facebook-comments-scraper": "Facebook: post comments",
    "apify/facebook-search-scraper": "Facebook: find pages by keyword",
    "apify/instagram-scraper": "Instagram: profiles and posts",
    "apify/instagram-search-scraper": "Instagram: find profiles by keyword",
    "apify/instagram-post-scraper": "Instagram: recent posts",
    "apify/instagram-profile-scraper": "Instagram: profile details",
    "apify/instagram-comment-scraper": "Instagram: post comments",
    "streamers/youtube-scraper": "YouTube: videos and search",
    "streamers/youtube-comments-scraper": "YouTube: video comments",
    "streamers/youtube-channel-scraper": "YouTube: channel details",
    "harvestapi/linkedin-company": "LinkedIn: company details",
    "harvestapi/linkedin-company-posts": "LinkedIn: company posts",
    "harvestapi/linkedin-company-search": "LinkedIn: find companies by keyword",
}


def _store_name(actor: str) -> Optional[str]:
    """``apify/x`` or ``apify~x`` → ``apify/x``; a bare id → None."""
    text = str(actor or "").strip()
    if "/" in text or "~" in text:
        return text.replace("~", "/")
    return None


def _configured_purpose(name_or_id: str) -> Optional[str]:
    """Purpose from the Actors settings (``actor.facebook.posts`` = this actor)."""
    try:
        from app.admin import settings as s
        for key in s.SETTING_DEFAULTS:
            if not key.startswith("actor."):
                continue
            value = str(s.get_setting(key) or "").strip().replace("~", "/")
            if value and value == name_or_id:
                _, platform, kind = (key.split(".") + ["", ""])[:3]
                return f"{platform.capitalize()}: {kind.replace('_', ' ')}"
    except Exception:
        pass
    return None


def _fetch(actor_id: str) -> Optional[Dict[str, Any]]:
    """Ask Apify who this actor is. Public store actors can be read with any token."""
    import httpx
    headers = {}
    try:
        from app.admin.settings import get_apify_token
        token = get_apify_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
    except Exception:
        pass
    try:
        resp = httpx.get(f"https://api.apify.com/v2/acts/{actor_id}", headers=headers, timeout=6)
        if resp.status_code != 200:
            return None
        data = (resp.json() or {}).get("data") or {}
        username, name = data.get("username"), data.get("name")
        if not (username and name):
            return None
        return {"name": f"{username}/{name}", "title": str(data.get("title") or "")[:120]}
    except Exception as e:  # network trouble: show the id for now
        logger.debug("Apify actor lookup failed for %s: %s", actor_id, type(e).__name__)
        return None


def _describe(actor: str, name: Optional[str], title: str = "") -> Dict[str, Any]:
    purpose = PURPOSE.get(name or "") or _configured_purpose(name or actor) or ""
    label = purpose or title or name or actor
    return {"actor_id": actor, "actor_name": name, "actor_title": title or None,
            "actor_label": label, "purpose": purpose or None}


def describe_actors(db, actors: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    """{actor id or name: {actor_id, actor_name, actor_title, actor_label, purpose}}.
    Synchronous (does network lookups for unknown ids) — call it in a thread."""
    out: Dict[str, Dict[str, Any]] = {}
    lookups = 0
    for actor in dict.fromkeys(a for a in actors if a):
        name = _store_name(actor)
        if name or actor == "unknown":
            out[actor] = _describe(actor, name)
            continue
        cached = None
        try:
            cached = db[COLLECTION].find_one({"_id": actor}) if db is not None else None
        except Exception:
            cached = None
        if cached and cached.get("name"):
            out[actor] = _describe(actor, cached["name"], cached.get("title") or "")
            continue
        recent_miss = cached and cached.get("checked_at") and utcnow() - cached["checked_at"] < RETRY_AFTER
        found = None
        if not recent_miss and lookups < LOOKUPS_PER_CALL:
            lookups += 1
            found = _fetch(actor)
            if db is not None:
                try:
                    db[COLLECTION].update_one({"_id": actor}, {"$set": {
                        "name": (found or {}).get("name"), "title": (found or {}).get("title"),
                        "checked_at": utcnow()}}, upsert=True)
                except Exception:
                    pass
        out[actor] = _describe(actor, (found or {}).get("name"), (found or {}).get("title") or "")
    return out
