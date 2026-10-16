"""Short-lived cache of discovered profiles: ``discovery_profile_cache``.

One document per (platform, profile_id) holding the normalized (enriched)
profile, its recent posts (+ the raw actor items for the deep scrape) and
``fetched_at``. Each part (profile, posts) expires on its own after the Global Setting
``keyword.cache_ttl_hours`` (default 24) and a TTL index deletes stale rows;
reads also check it, so an expired row is never used. A cache hit skips the
Apify call and is not charged.

The cache holds public profile data only (what the actors return for
anyone); it is shared by every organization.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

from app.db.models import utcnow

logger = logging.getLogger(__name__)

COLLECTION = "discovery_profile_cache"


def ttl_hours() -> int:
    from app.admin.settings import get_int
    return max(0, get_int("keyword.cache_ttl_hours", 24))


def _aware(dt: Any) -> Any:
    from datetime import timezone
    if dt is not None and getattr(dt, "tzinfo", None) is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def get(db, platform: str, profile_id: str, part: str = "profile") -> Optional[Dict[str, Any]]:
    """The cache row when its ``part`` (profile | posts) is still fresh, else None.
    Each part has its own expiry, so caching new posts never extends an old
    profile (and the other way round)."""
    if db is None or ttl_hours() <= 0:
        return None
    try:
        doc = db[COLLECTION].find_one({"platform": platform, "profile_id": profile_id})
    except Exception as e:  # cache is best effort
        logger.debug("profile cache read failed: %s", e)
        return None
    exp = (doc or {}).get(f"{part}_expires_at")
    if not doc or not exp or _aware(exp) <= utcnow():
        return None
    return doc


def get_profile(db, platform: str, profile_id: str) -> Optional[Dict[str, Any]]:
    doc = get(db, platform, profile_id, "profile")
    if doc and doc.get("enriched") and isinstance(doc.get("profile"), dict):
        return doc["profile"]
    return None


def get_posts(db, platform: str, profile_id: str, n: int) -> Optional[Dict[str, Any]]:
    """{"posts": [...], "raw": [...]} when the cache holds at least ``n``
    recent posts (or every post the profile had when fewer were returned)."""
    doc = get(db, platform, profile_id, "posts")
    if not doc or doc.get("posts") is None:
        return None
    posts: List[Dict[str, Any]] = doc.get("posts") or []
    requested = int(doc.get("posts_requested") or 0)
    if len(posts) >= n or requested >= n:
        raws = doc.get("posts_raw") or [None] * len(posts)
        return {"posts": posts[:n], "raw": raws[:n]}
    return None


def _write(db, platform: str, profile_id: str, part: str, fields: Dict[str, Any]) -> None:
    hours = ttl_hours()
    if db is None or hours <= 0:
        return
    now = utcnow()
    exp = now + timedelta(hours=hours)
    try:
        db[COLLECTION].update_one(
            {"platform": platform, "profile_id": profile_id},
            {"$set": {**fields, f"{part}_fetched_at": now, f"{part}_expires_at": exp, "fetched_at": now},
             # the TTL index removes the row once its newest part has expired
             "$max": {"expires_at": exp},
             "$setOnInsert": {"platform": platform, "profile_id": profile_id, "created_at": now}},
            upsert=True)
    except Exception as e:
        logger.debug("profile cache write failed: %s", e)


def put_profile(db, profile: Dict[str, Any]) -> None:
    prof = {k: v for k, v in profile.items() if k != "recent_posts"}
    _write(db, profile["platform"], profile["profile_id"], "profile", {"profile": prof, "enriched": True})


def put_posts(db, platform: str, profile_id: str, posts: List[Dict[str, Any]],
              raws: List[Any], requested: int) -> None:
    _write(db, platform, profile_id, "posts",
           {"posts": posts, "posts_raw": raws, "posts_requested": int(requested)})
