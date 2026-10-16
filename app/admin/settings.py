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
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from app.config import get_settings
from app.db.mongo import get_async_db, get_sync_db
from app.db.models import utcnow

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
    "actor.youtube.comments": "streamers/youtube-comments-scraper",
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
    # LeadAI's Gemini key limits (Google AI Studio → Rate limits); 0 = not set
    "ai.gemini.requests_per_minute": 0,
    "ai.gemini.requests_per_day": 0,
    "ai.gemini.input_per_minute": 0,

    # Business context: industry used for organizations that chose none
    # (a key of app/pipeline/business_context.py; "general" = any business)
    "business.default_industry": "general",

    # Lifecycle automation (app/lifecycle/maintenance.py): preview-only until
    # the Super Admin reviews the pending changes and turns it on
    "lifecycle.auto_apply": False,
    "lifecycle.preview_notified": False,
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
    "security.audit_logging": True,  # informational only: audit logging cannot be disabled
    "security.session_epoch": 0,
    "security.lockout_threshold": 5,
    "security.lockout_minutes": 15,
    "security.impersonation_minutes": 30,

    # Payments: which configured provider takes payments (keys stay in .env)
    "billing.provider": "auto",

    # Maintenance mode — gates the user app, never the admin panel
    "maintenance.enabled": False,
    "maintenance.message": "LeadAI is undergoing scheduled maintenance. "
                           "Please check back shortly.",

    # Feature flags
    "features.url_search.enabled": True,
    "features.exports.enabled": True,
    "features.demo_registration.enabled": True,
    "features.ai_analysis.enabled": True,

    # Keyword / discovery search (app/discovery): switches, hard caps (plans
    # can allow less, never more), suggested filter values and actor ids
    "features.keyword_search.enabled": True,
    "keyword.platform.instagram.enabled": True,
    "keyword.platform.facebook.enabled": True,
    "keyword.platform.youtube.enabled": True,
    "keyword.platform.linkedin.enabled": False,
    "keyword.max_keywords": 5,
    "keyword.max_candidates": 200,
    "keyword.max_recent_posts": 30,
    "keyword.cache_ttl_hours": 24,
    "keyword.default.min_followers": 1000,
    "keyword.default.min_posts": 10,
    "keyword.default.recent_posts": 10,
    "keyword.default.min_avg_comments": 2,
    "keyword.default.active_within_days": 90,
    "discovery.actor.instagram.search": "apify/instagram-search-scraper",
    "discovery.actor.instagram.posts": "apify/instagram-post-scraper",
    "discovery.actor.facebook.search": "apify/facebook-search-scraper",
    "discovery.actor.youtube.channel_search": "",
    "discovery.actor.youtube.channel": "streamers/youtube-channel-scraper",
    "discovery.actor.linkedin.search": "harvestapi/linkedin-company-search",

    # ── White-label / Global Settings (managed from the Global Settings UI) ──
    "general.app.name": "LeadAI",
    "general.app.short_name": "LeadAI",
    "general.app.tagline": "AI Lead Intelligence",
    "general.app.description": (
        "AI-powered social lead intelligence — paste a Facebook, Instagram, "
        "YouTube or LinkedIn URL and uncover high-intent prospects through "
        "real Apify data."),
    "general.company.name": "",
    "general.company.website": "",
    "general.contact.support_email": "",
    "general.contact.support_phone": "",
    "general.contact.support_url": "",
    "general.links.privacy_url": "",
    "general.links.terms_url": "",
    "general.links.docs_url": "",
    "general.links.help_url": "",

    "branding.logo_primary": "",
    "branding.logo_dark": "",
    "branding.logo_light": "",
    "branding.logo_compact": "",
    "branding.logo_login": "",
    "branding.logo_email": "",
    "branding.favicon": "",
    "branding.apple_touch_icon": "",
    "branding.colors.primary": "#2e9573",
    "branding.colors.accent": "#d9a441",
    "branding.colors.success": "#1fae6a",
    "branding.colors.warning": "#f0a531",
    "branding.colors.danger": "#e5484d",
    "branding.colors.info": "#3b82f6",
    "branding.theme": "light",
    "branding.font": "inter",
    "branding.white_label": False,
    "branding.login_heading": "Welcome Back",
    "branding.login_subtext": (
        "Sign in to continue to your AI-powered lead intelligence dashboard."),
    "branding.footer_text": (
        "Protected by secure authentication · LeadAI © 2026"),

    "appearance.sidebar_title": "",
    "appearance.sidebar_subtitle": "Admin Control Center",
    "appearance.sidebar_collapsed_default": False,
    "appearance.show_icons": True,
    "appearance.show_section_labels": True,
    "appearance.show_footer_links": True,
    "appearance.nav_overrides": {},
    "appearance.dashboard_widgets": {},

    # User-app defaults (search form, pagination, theme)
    "defaults.comment_filter_mode": "all",
    "defaults.keyword_preset": "",
    "defaults.theme": "system",
    "defaults.date_range": 30,
    "defaults.page_size": 20,

    # Notifications & email: platform-wide switches over each organization's
    # own notification choices (app/events/notifications.py, app/events/email.py)
    "notifications.alerts_enabled": True,
    "notifications.job_failed_email": True,
    "notifications.new_lead_email": True,
    "email.sender_name": "",
    "email.reply_to": "",
    "email.company_name": "",
    "email.logo": "",
    "email.footer_text": "",
    # API usage & limits monitor (app/limits): background checks, alert
    # thresholds and who is told; limits of providers that have no quota API
    "api_limits.enabled": True,
    "api_limits.check_minutes": 15,
    "api_limits.warn_percent": 80,
    "api_limits.critical_percent": 95,
    "api_limits.notify_org_admins": True,
    "api_limits.notify_users": True,
    "api_limits.email_admins": True,
    "email.daily_limit": 0,
    "sms.min_balance": 0.0,

    # Leads & exports
    "leads.duplicate_detection": True,
    "leads.retention_days": 365,
    "leads.default_status": "New",
    "leads.default_temperature": "Warm",
    "leads.require_phone_or_email": False,
    "exports.default_format": "CSV",
    "exports.max_records": 5000,
    "exports.include_leads_only": True,
    "exports.include_ai_summary": True,

    # SEO & localization
    "seo.meta_description": "",
    "seo.og_title": "",
    "seo.og_image": "",
    "localization.timezone": "auto",
    "localization.date_format": "YYYY-MM-DD",
    "localization.time_format": "24h",
    "localization.currency": "USD",
    "localization.language": "en",

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
_CACHE_TTL = 5.0
_CACHE: Dict[str, Tuple[float, Any]] = {}          # key -> (time.time(), value)


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


# Short read cache for _sync_get. Settings are read on nearly every request
# (maintenance flag, limits, feature switches), often many times per request;
# against a remote MongoDB each read is a network round trip. Writes through
# this module clear the cache at once; another worker process sees a change
# within _CACHE_TTL seconds. Entries are tied to the database client they were
# read from, so a different database (e.g. per-test) never sees stale values.
_READ_CACHE: Dict[str, Tuple[float, Any, Any]] = {}   # key -> (monotonic expiry, db client, value)


def clear_settings_cache() -> None:
    """Drop both caches (called on every write through this module)."""
    _READ_CACHE.clear()
    _CACHE.clear()


def _sync_get(key: str) -> Optional[Any]:
    try:
        db = get_sync_db()
        if db is None:
            return None
        owner = getattr(db, "client", db)
        now = time.monotonic()
        hit = _READ_CACHE.get(key)
        if hit is not None and hit[0] > now and hit[1] is owner:
            return hit[2]
        doc = db[COLLECTION].find_one({"_id": key}, {"value": 1})
        value = doc.get("value") if doc else None
        _READ_CACHE[key] = (now + _CACHE_TTL, owner, value)
        return value
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


_MOJIBAKE = ("â€", "Ã", "Â")


def fix_mojibake(text: str) -> str:
    """Text that was UTF-8 but got decoded as Windows-1252 and saved again
    ("AI-powered â€” …" -> "AI-powered — …"). Unchanged when it isn't."""
    if not isinstance(text, str) or not any(m in text for m in _MOJIBAKE):
        return text
    for _ in range(3):                      # it can be wrapped more than once
        try:
            fixed = text.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
        if fixed == text:
            break
        text = fixed
        if not any(m in text for m in _MOJIBAKE):
            break
    return text


def repair_mojibake_settings(db) -> int:
    """Once per start: fix garbled text in stored settings. Returns how many."""
    if db is None:
        return 0
    n = 0
    for doc in db[COLLECTION].find({"value": {"$type": "string"}}):
        fixed = fix_mojibake(doc.get("value"))
        if fixed != doc.get("value"):
            db[COLLECTION].update_one({"_id": doc["_id"]}, {"$set": {"value": fixed}})
            n += 1
    if n:
        clear_settings_cache()
    return n


_OBJECT_ID_RE = re.compile(r"^[0-9a-f]{24}$")


def clean_stored_settings(db) -> int:
    """Once per start: drop stored settings the Settings page can't use.

    * an image setting holding a page address (``…/superadmin#/settings``),
      which shows as a broken logo; the built-in default is used instead
    * stray documents keyed by a bare id, which no setting is named after

    Returns how many were removed."""
    if db is None:
        return 0
    from app.settings.registry import SPEC_BY_KEY
    gone = []
    for doc in db[COLLECTION].find({}, {"value": 1}):
        key = str(doc["_id"])
        spec = SPEC_BY_KEY.get(key)
        if spec is None:
            if _OBJECT_ID_RE.match(key):
                gone.append(doc["_id"])
        elif spec.type == "path" and isinstance(doc.get("value"), str) and "#" in doc["value"]:
            gone.append(doc["_id"])
    if gone:
        db[COLLECTION].delete_many({"_id": {"$in": gone}})
        clear_settings_cache()
        logger.info("Removed %d unusable stored setting(s): %s", len(gone), ", ".join(map(str, gone)))
    return len(gone)


def _seal(key: str, value: Any) -> Any:
    """Secrets (the platform Apify token) are stored encrypted at rest."""
    if key in _SECRET_KEYS and isinstance(value, str) and value:
        from app.services import secret_box
        if not secret_box.is_encrypted(value):
            return secret_box.encrypt(value)
    return value


def _reveal(value: Any) -> Optional[str]:
    """A stored secret in plain text: decrypts sealed values, passes older
    plain values through, None when it can no longer be decrypted."""
    if not value:
        return ""
    from app.services import secret_box
    if secret_box.is_encrypted(str(value)):
        return secret_box.decrypt(str(value))
    return str(value)


def set_setting(key: str, value: Any, by: str = "admin") -> bool:
    """Persist a coerced value. Returns True on success, False if Mongo is
    down (the change is lost, but the app keeps running on env defaults)."""
    coerced = _seal(key, _coerce(key, value))
    try:
        db = get_sync_db()
        if db is None:
            return False
        db[COLLECTION].update_one(
            {"_id": key},
            {"$set": {"value": coerced,
                      "updated_at": utcnow(),
                      "updated_by": by}},
            upsert=True)
        clear_settings_cache()
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
        clear_settings_cache()
        return True
    except Exception:
        return False


# ── Async variants for FastAPI handlers ────────────────────────────────────

async def aget_setting(key: str) -> Any:
    value = await _async_get(key)
    if value is None:
        return _default(key)
    return value


async def adelete_setting(key: str) -> bool:
    """Remove the override so the env default is used again (async)."""
    try:
        db = get_async_db()
        if db is None:
            return False
        await db[COLLECTION].delete_one({"_id": key})
        clear_settings_cache()
        try:
            from app.settings.registry import invalidate_public_config_cache
            invalidate_public_config_cache()
        except Exception:
            pass
        return True
    except Exception:
        return False


async def aget_all_settings() -> Dict[str, Any]:
    """Fetch all setting overrides from MongoDB in a single batch query and merge with defaults."""
    res = dict(SETTING_DEFAULTS)
    try:
        db = get_async_db()
        if db is not None:
            cursor = db[COLLECTION].find({}, {"_id": 1, "value": 1})
            async for doc in cursor:
                res[doc["_id"]] = doc.get("value")
    except Exception as e:
        logger.warning(f"Failed to fetch all settings in batch: {e}")
    return res


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
    coerced = _seal(key, _coerce(key, value))
    try:
        db = get_async_db()
        if db is None:
            return False
        await db[COLLECTION].update_one(
            {"_id": key},
            {"$set": {"value": coerced,
                      "updated_at": utcnow(),
                      "updated_by": by}},
            upsert=True)
        clear_settings_cache()
        try:
            from app.settings.registry import invalidate_public_config_cache
            invalidate_public_config_cache()
        except Exception:
            pass
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
            result[f"{key}.masked"] = _mask(_reveal(value) or "")
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
        return {"main": "actor.youtube.main",
                "video": "actor.youtube.main",
                "comments": "actor.youtube.comments"}.get(
                    kind, "actor.youtube.main")
    return "actor.facebook.pages"


def get_actor_id(platform: str, kind: str = "main") -> str:
    """The actor ID the scrapers actually call — read fresh from settings so
    an admin change takes effect on the next scrape."""
    key = platform_actor_key(platform, kind)
    value = get_str(key, "")
    return value or str(_default(key) or "")


def get_apify_token() -> str:
    """Effective Apify token: admin override first, then the environment."""
    stored = get_str("apify.token", "")
    override = _reveal(stored)
    if override is None:
        # encrypted with a key this server no longer has: use the environment's
        logger.error("The saved platform Apify token can't be decrypted (API_KEY_ENCRYPTION_KEY changed?) "
                     "— using APIFY_API_TOKEN from the environment. Save the token again.")
        override = ""
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
        "stop_on_limit": get_bool("cost.stop_on_limit"),
        "warn_before_expensive": get_bool("cost.warn_before_expensive"),
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


# ── Settings history / revisions ────────────────────────────────────────────
# Every batch change (save, reset, import, restore) pushes a revision into
# ``settings_history`` with a full snapshot so any state can be rolled back.

HISTORY_COLLECTION = "settings_history"


async def current_revision() -> int:
    """Highest history version so far (0 when the collection is empty)."""
    try:
        db = get_async_db()
        if db is None:
            return 0
        doc = await db[HISTORY_COLLECTION].find_one(
            {}, {"version": 1}, sort=[("version", -1)])
        return int(doc["version"]) if doc else 0
    except Exception:
        return 0


async def push_revision(snapshot: Dict[str, Any], changed: Dict[str, Any],
                        by: str = "admin", ip: str = "",
                        reason: str = "") -> int:
    """Write a new history entry and return its version number."""
    try:
        db = get_async_db()
        if db is None:
            return 0
        version = await current_revision() + 1
        await db[HISTORY_COLLECTION].insert_one({
            "version": version,
            "snapshot": snapshot or {},
            "changed": changed or {},
            "changed_by": by,
            "ip": ip or "",
            "reason": reason or "",
            "created_at": utcnow(),
        })
        return version
    except Exception as e:
        logger.warning(f"Failed to push settings revision: {e}")
        return 0


async def list_history(limit: int = 50) -> List[Dict[str, Any]]:
    try:
        db = get_async_db()
        if db is None:
            return []
        docs = [doc async for doc in
                db[HISTORY_COLLECTION].find()
                .sort("version", -1).limit(max(1, min(limit, 200)))]
        return [{**d, "snapshot": None} for d in docs]  # snapshots stay out of lists
    except Exception:
        return []


async def get_history_version(version: int) -> Optional[Dict[str, Any]]:
    try:
        db = get_async_db()
        if db is None:
            return None
        return await db[HISTORY_COLLECTION].find_one({"version": version})
    except Exception:
        return None


async def snapshot_all() -> Dict[str, Any]:
    """Effective values of every registry key — used for exports and revisions."""
    from app.settings.registry import REGISTERED_KEYS
    out: Dict[str, Any] = {}
    for key in sorted(REGISTERED_KEYS):
        out[key] = await aget_setting(key)
    return out


async def apply_snapshot(snapshot: Dict[str, Any], by: str = "admin",
                         ip: str = "", reason: str = "") -> Tuple[int, List[str]]:
    """Apply a stored snapshot (restore). Returns (version, applied_keys)."""
    from app.settings.registry import is_registered
    applied: List[str] = []
    changed: Dict[str, Dict[str, Any]] = {}
    for key, new_value in snapshot.items():
        if not is_registered(key):
            continue
        old_value = await aget_setting(key)
        if old_value != new_value:
            await aset_setting(key, new_value, by=by)
            changed[key] = {"old": old_value, "new": new_value}
            applied.append(key)
    if applied:
        version = await push_revision(
            await snapshot_all(), changed, by=by, ip=ip,
            reason=reason or "restore")
        return version, applied
    return 0, []


def export_payload() -> Dict[str, Any]:
    """Sync export (safe keys only, no secrets)."""
    from app.settings.registry import REGISTERED_KEYS
    out: Dict[str, Any] = {}
    for key in sorted(REGISTERED_KEYS):
        out[key] = get_setting(key)
    return {
        "version": 1,
        "schema": "leadai.settings.v1",
        "exported_at": utcnow().isoformat(),
        "settings": out,
    }


def json_dumps(value: Any) -> str:
    return json.dumps(value, default=str)
