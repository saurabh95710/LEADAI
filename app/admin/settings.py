"""Admin-managed system settings.

Storage: the ``system_settings`` collection (``key`` -> ``value``) layered on
top of the .env defaults from :mod:`app.config`. A row in the DB always wins;
a missing row falls back to the environment default. Because only the keys
below are ever stored, the two sources can never drift apart.

Sync accessors (``get_setting``, ``set_setting``) are used by the background
scrapers/agents (threads); async ones (``aget_setting`` …) by the FastAPI
handlers. Every accessor degrades to the environment default when Mongo is
unreachable, so the app never dies because a settings read failed.

The value of every setting is stored as a JSON-native type (bool/int/float/
str/list/dict) so it round-trips through Motor/PyMongo unchanged.
"""
import json
import logging
import time
from typing import Any, Dict, Optional

from app.config import get_settings
from app.db.mongo import get_async_db, get_sync_db

logger = logging.getLogger(__name__)
settings = get_settings()

COLLECTION = "system_settings"

# ── Settings registry ──────────────────────────────────────────────────────
# Every admin-manageable key with its environment default. The runtime code
# consults these values at the exact moment they matter, so a change in the
# admin panel takes effect on the next operation — no restart required.

_PLATFORM_KEYS = {
    "facebook": "actor.facebook.pages",
    "instagram": "actor.instagram.main",
    "youtube": "actor.youtube.main",
    "linkedin": "actor.linkedin.company",
}

SETTING_DEFAULTS: Dict[str, Any] = {
    # Platform enable/disable — enforced server-side on every entry point
    "platform.facebook.enabled": True,
    "platform.instagram.enabled": bool(settings.instagram_actor_id),
    "platform.linkedin.enabled": bool(settings.linkedin_actor_id),
    "platform.youtube.enabled": bool(settings.youtube_actor_id),

    # Apify actor IDs actually used by the scrapers
    "actor.facebook.pages": "apify/facebook-pages-scraper",
    "actor.facebook.posts": "apify/facebook-posts-scraper",
    "actor.facebook.comments": "apify/facebook-comments-scraper",
    "actor.instagram.main": settings.instagram_actor_id,
    "actor.youtube.main": settings.youtube_actor_id,
    "actor.linkedin.company": settings.linkedin_actor_id,
    "actor.linkedin.posts": settings.linkedin_posts_actor_id,

    # Scraping limits
    "limits.min_comments": settings.min_comments,
    "limits.max_posts_default": 20,
    "limits.max_posts_cap": 100,
    "limits.max_comments_per_post_default": 30,
    "limits.max_comments_per_post_cap": 500,
    "limits.global_max_comments": settings.max_comments_to_collect,

    # Cost protection
    "cost.stop_on_limit": True,
    "cost.warn_before_expensive": True,

    # AI / Gemini
    "ai.enabled": True,
    "ai.rule_fallback": True,
    "ai.max_calls_per_job": 500,
    "ai.temperature": 0.1,
    "ai.model": settings.gemini_model or "gemini-2.5-flash",

    # Lead scoring — components of the deterministic comment_lead_score
    # formula (score 0-100) and the additive signal score + thresholds
    "scoring.confidence_weight": 50,
    "scoring.priority_weight": 20,
    "scoring.quality_weight": 20,
    "scoring.contact_phone": 6,
    "scoring.contact_email": 4,
    "scoring.spam_penalty": 20,
    "scoring.phone": 20,
    "scoring.email": 15,
    "scoring.budget": 20,
    "scoring.urgency": 15,
    "scoring.location": 10,
    "scoring.buying_intent": 20,
    "scoring.hot_min": 80,
    "scoring.warm_min": 50,
    "scoring.derive_quality": False,

    # Comment intelligence signals
    "ci.detect_phone": True,
    "ci.detect_email": True,
    "ci.detect_budget": True,
    "ci.detect_location": True,
    "ci.detect_urgency": True,
    "ci.detect_buying_intent": True,
    "ci.detect_selling_intent": False,  # off by default: preserves current lead filtering
    "ci.ignore_emoji_only": True,
    "ci.ignore_spam": True,
    "ci.ignore_low_value": True,
    "ci.min_lead_score": 0,

    # Security
    "security.session_timeout_hours": settings.session_ttl_days * 24,
    "security.login_protection": True,
    "security.audit_logging": True,
    "security.session_epoch": 0,

    # Maintenance mode — gates the user app, never the admin panel
    "maintenance.enabled": False,
    "maintenance.message": "LeadAI is undergoing scheduled maintenance. "
                           "Please check back shortly.",

    # Feature flags
    "features.url_search.enabled": True,
    "features.exports.enabled": True,

    # Apify token override (env APIFY_API_TOKEN is the default source).
    # Never returned by any API — only a masked hint is exposed.
    "apify.token": "",
    "apify.last_test_ok": None,
    "apify.last_test_at": None,
}

_SECRET_KEYS = {"apify.token"}

# ── TTL cache for hot paths (per-comment scoring/detection) ───────────────
# The full Mongo lookup is avoided on every comment in a loop; a short TTL
# (5s) keeps admin changes effectively instant while costing one read per
# key per TTL window at most.
_CACHE: Dict[str, tuple] = {}
_CACHE_TTL = 5.0


def _cached(key: str, ttl: float = _CACHE_TTL):
    now = time.time()
    hit = _CACHE.get(key)
    if hit is not None and now - hit[0] < ttl:
        return hit[1]
    value = _sync_get(key)
    if value is None:
        value = _default(key)
    _CACHE[key] = (now, value)
    return value


def get_setting_cached(key: str) -> Any:
    return _cached(key)


def get_bool_cached(key: str) -> bool:
    return bool(_cached(key))


def get_int_cached(key: str, fallback: int = 0) -> int:
    try:
        return int(_cached(key) or fallback)
    except (TypeError, ValueError):
        return fallback


def get_str_cached(key: str, fallback: str = "") -> str:
    value = _cached(key)
    return str(value) if value is not None else fallback


# ── Low-level accessors ────────────────────────────────────────────────────

def _default(key: str) -> Any:
    return SETTING_DEFAULTS.get(key)


def _coerce(key: str, value: Any) -> Any:
    """Coerce an admin-submitted value to the registered type."""
    default = _default(key)
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if isinstance(default, int):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    if isinstance(default, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    if isinstance(default, list):
        return value if isinstance(value, list) else default
    if isinstance(default, dict):
        return value if isinstance(value, dict) else default
    if default is None:
        return None if value in ("", None, "null") else value
    return str(value)


def _sync_get(key: str) -> Optional[Any]:
    try:
        db = get_sync_db()
        if db is None:
            return None
        doc = db[COLLECTION].find_one({"_id": key}, {"value": 1})
        return doc.get("value") if doc else None
    except Exception:
        return None


def get_setting(key: str) -> Any:
    """Effective value: DB row wins, otherwise the env default."""
    value = _sync_get(key)
    if value is None and _default(key) is not None:
        return _default(key)
    return value if value is not None else _default(key)


def get_bool(key: str) -> bool:
    return bool(get_setting(key))


def get_int(key: str, fallback: int = 0) -> int:
    try:
        return int(get_setting(key) or fallback)
    except (TypeError, ValueError):
        return fallback


def get_str(key: str, fallback: str = "") -> str:
    value = get_setting(key)
    return str(value) if value is not None else fallback


def set_setting(key: str, value: Any, by: str = "admin") -> bool:
    """Persist a coerced value. Returns True on success, False if Mongo is
    down (the change is lost, but the app keeps running on env defaults)."""
    coerced = _coerce(key, value)
    try:
        db = get_sync_db()
        if db is None:
            return False
        db[COLLECTION].update_one(
            {"_id": key},
            {"$set": {"value": coerced,
                      "updated_at": time.time(),
                      "updated_by": by}},
            upsert=True)
        return True
    except Exception as e:
        logger.warning(f"Failed to persist setting {key}: {e}")
        return False


def delete_setting(key: str) -> bool:
    """Remove the override so the env default is used again."""
    try:
        db = get_sync_db()
        if db is None:
            return False
        db[COLLECTION].delete_one({"_id": key})
        return True
    except Exception:
        return False


# ── Async variants for FastAPI handlers ────────────────────────────────────

async def aget_setting(key: str) -> Any:
    value = await _async_get(key)
    if value is None:
        return _default(key)
    return value


async def _async_get(key: str) -> Optional[Any]:
    try:
        db = get_async_db()
        if db is None:
            return None
        doc = await db[COLLECTION].find_one({"_id": key}, {"value": 1})
        return doc.get("value") if doc else None
    except Exception:
        return None


async def aset_setting(key: str, value: Any, by: str = "admin") -> bool:
    coerced = _coerce(key, value)
    try:
        db = get_async_db()
        if db is None:
            return False
        await db[COLLECTION].update_one(
            {"_id": key},
            {"$set": {"value": coerced,
                      "updated_at": time.time(),
                      "updated_by": by}},
            upsert=True)
        return True
    except Exception as e:
        logger.warning(f"Failed to persist setting {key}: {e}")
        return False


async def get_all_settings() -> Dict[str, Any]:
    """All keys with effective values, plus masked hints for secrets."""
    result: Dict[str, Any] = {}
    for key in SETTING_DEFAULTS:
        value = await aget_setting(key)
        if key in _SECRET_KEYS:
            result[key] = ""
            result[f"{key}.masked"] = _mask(value)
        else:
            result[key] = value
        if value is not None:
            result[f"{key}.updated_at"] = await _updated_at(key)
    return result


async def _updated_at(key: str) -> Optional[float]:
    try:
        db = get_async_db()
        if db is None:
            return None
        doc = await db[COLLECTION].find_one({"_id": key}, {"updated_at": 1})
        return doc.get("updated_at") if doc else None
    except Exception:
        return None


# ── Domain helpers ─────────────────────────────────────────────────────────

PLATFORMS = ("facebook", "instagram", "linkedin", "youtube")


def is_platform_enabled(platform: str) -> bool:
    return get_bool(f"platform.{platform}.enabled")


def platform_actor_key(platform: str, kind: str = "main") -> str:
    """Actor setting key for a platform. Facebook has pages/posts/comments
    kinds; everyone else just uses ``main`` (or ``posts`` for LinkedIn)."""
    if platform == "facebook":
        return {"pages": "actor.facebook.pages",
                "posts": "actor.facebook.posts",
                "comments": "actor.facebook.comments"}.get(
                    kind, "actor.facebook.pages")
    if platform == "linkedin":
        return {"company": "actor.linkedin.company",
                "posts": "actor.linkedin.posts"}.get(
                    kind, "actor.linkedin.company")
    if platform == "instagram":
        return "actor.instagram.main"
    if platform == "youtube":
        return "actor.youtube.main"
    return "actor.facebook.pages"


def get_actor_id(platform: str, kind: str = "main") -> str:
    """The actor ID the scrapers actually call — read fresh from settings so
    an admin change takes effect on the next scrape."""
    key = platform_actor_key(platform, kind)
    value = get_str(key, "")
    return value or str(_default(key) or "")


def get_apify_token() -> str:
    """Effective Apify token: admin override first, then the environment."""
    override = get_str("apify.token", "")
    return override or settings.apify_api_token


def _mask(token: str) -> str:
    if not token:
        return ""
    if len(token) <= 4:
        return "••••"
    return "••••" + token[-4:]


def get_apify_token_hint() -> str:
    return _mask(get_apify_token())


def effective_limits() -> Dict[str, int]:
    """Runtime scraping limits used for validation/capping."""
    return {
        "min_comments": get_int("limits.min_comments"),
        "max_posts_default": get_int("limits.max_posts_default", 20),
        "max_posts_cap": get_int("limits.max_posts_cap", 100),
        "max_comments_per_post_default": get_int(
            "limits.max_comments_per_post_default", 30),
        "max_comments_per_post_cap": get_int(
            "limits.max_comments_per_post_cap", 500),
        "global_max_comments": get_int("limits.global_max_comments", 100),
    }


def is_maintenance_enabled() -> bool:
    return get_bool("maintenance.enabled")


def maintenance_message() -> str:
    return get_str("maintenance.message",
                   "LeadAI is undergoing scheduled maintenance.")


def is_audit_enabled() -> bool:
    return get_bool("security.audit_logging")


def audit_enabled_sync() -> bool:
    value = _sync_get("security.audit_logging")
    return bool(value) if value is not None else bool(_default("security.audit_logging"))


def sessions_epoch() -> int:
    try:
        return int(get_setting("security.session_epoch") or 0)
    except (TypeError, ValueError):
        return 0


def json_dumps(value: Any) -> str:
    return json.dumps(value, default=str)
