"""Super-Admin-managed platform configuration documents.

Stored in the ``platform_config`` collection, one document per key:

  * ``demo``         — DemoConfig: what an approved demo gets
  * ``token_costs``  — how many tokens each metered action consumes

The dictionaries below are only the FIRST-RUN SEED: on first read they are
written to the database, and from then on the database is the single source
of truth (editable from the Super Admin portal). Nothing reads these
constants directly.
"""
import copy
import logging
import time
from typing import Any, Dict, Optional

from app.db.models import utcnow
from app.db.mongo import get_sync_db

logger = logging.getLogger(__name__)

COLLECTION = "platform_config"

DEMO_CONFIG_SEED: Dict[str, Any] = {
    "duration_days": 7,
    "tokens": 500,
    "max_searches": 10,
    "posts_per_search": 20,
    "comments_per_post": 30,
    "allowed_platforms": ["facebook", "instagram", "youtube", "linkedin"],
    "ai_enabled": True,
    "exports_enabled": False,
    "max_leads": 200,
    "max_users": 1,
    "auto_approve": False,
}

TOKEN_COSTS_SEED: Dict[str, int] = {
    "search": 10,      # starting one URL search
    "collect": 5,      # collecting more posts / comments for a page / post
    "ai_call": 1,      # one AI analysis of a comment
    "export": 5,       # one CSV export
}

_SEEDS = {"demo": DEMO_CONFIG_SEED, "token_costs": TOKEN_COSTS_SEED}

# Validation: key -> (type, min, max) for numeric fields
_DEMO_NUMERIC = {
    "duration_days": (1, 365), "tokens": (0, 10_000_000), "max_searches": (0, 100_000),
    "posts_per_search": (1, 1000), "comments_per_post": (1, 5000),
    "max_leads": (0, 10_000_000), "max_users": (1, 1000),
}
_DEMO_BOOL = {"ai_enabled", "exports_enabled", "auto_approve"}
KNOWN_PLATFORMS = ("facebook", "instagram", "youtube", "linkedin")

_cache: Dict[str, Any] = {}
_TTL = 15.0


def _get(key: str) -> Dict[str, Any]:
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _TTL:
        return copy.deepcopy(hit[1])
    value = copy.deepcopy(_SEEDS[key])
    db = get_sync_db()
    if db is not None:
        try:
            doc = db[COLLECTION].find_one({"_id": key})
            if doc is None:
                db[COLLECTION].update_one(
                    {"_id": key},
                    {"$setOnInsert": {"value": value, "updated_at": utcnow(),
                                      "updated_by": "system-seed"}},
                    upsert=True)
            else:
                # merge so newly added keys get their seed value
                value.update(doc.get("value") or {})
        except Exception as e:
            logger.warning("platform_config read failed (%s): %s", key, e)
    _cache[key] = (time.time(), value)
    return copy.deepcopy(value)


def _set(key: str, value: Dict[str, Any], actor: str) -> Dict[str, Any]:
    db = get_sync_db()
    if db is None:
        raise RuntimeError("Database unavailable")
    db[COLLECTION].update_one({"_id": key}, {"$set": {
        "value": value, "updated_at": utcnow(), "updated_by": actor}}, upsert=True)
    _cache.pop(key, None)
    return copy.deepcopy(value)


def get_demo_config() -> Dict[str, Any]:
    return _get("demo")


def get_token_costs() -> Dict[str, int]:
    return _get("token_costs")


def token_cost(action: str) -> int:
    try:
        return max(0, int(get_token_costs().get(action, 0)))
    except (TypeError, ValueError):
        return 0


def validate_demo_config(patch: Dict[str, Any]) -> Dict[str, Any]:
    clean: Dict[str, Any] = {}
    for k, v in patch.items():
        if k in _DEMO_NUMERIC:
            lo, hi = _DEMO_NUMERIC[k]
            try:
                iv = int(v)
            except (TypeError, ValueError):
                raise ValueError(f"{k} must be a number")
            if not lo <= iv <= hi:
                raise ValueError(f"{k} must be between {lo} and {hi}")
            clean[k] = iv
        elif k in _DEMO_BOOL:
            clean[k] = bool(v)
        elif k == "allowed_platforms":
            if not isinstance(v, list) or not v:
                raise ValueError("allowed_platforms must be a non-empty list")
            bad = [p for p in v if p not in KNOWN_PLATFORMS]
            if bad:
                raise ValueError(f"Unknown platforms: {', '.join(bad)}")
            clean[k] = list(dict.fromkeys(v))
        else:
            raise ValueError(f"Unknown demo setting: {k}")
    return clean


def update_demo_config(patch: Dict[str, Any], actor: str) -> Dict[str, Any]:
    value = get_demo_config()
    value.update(validate_demo_config(patch))
    return _set("demo", value, actor)


def update_token_costs(patch: Dict[str, Any], actor: str) -> Dict[str, int]:
    value = get_token_costs()
    for k, v in patch.items():
        try:
            iv = int(v)
        except (TypeError, ValueError):
            raise ValueError(f"{k} must be a whole number")
        if iv < 0 or iv > 100_000:
            raise ValueError(f"{k} must be between 0 and 100000")
        value[str(k)] = iv
    return _set("token_costs", value, actor)


def clear_cache(key: Optional[str] = None) -> None:
    if key:
        _cache.pop(key, None)
    else:
        _cache.clear()
