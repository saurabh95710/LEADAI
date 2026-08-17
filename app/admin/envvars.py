"""Admin-managed environment variables.

Three-layer model: a DB override (``env_overrides`` collection) wins;
otherwise the real environment value is used — the .env file as loaded by
:mod:`app.config` (``Settings``), or a value injected into ``os.environ``
(e.g. Docker/Kubernetes); finally a documented default.

Values whose consumers already read through these accessors apply
immediately (no restart). Values marked ``restart=True`` are read once at
import time by :mod:`app.config` or the settings registry, so they take
effect after the process restarts — the admin panel says so explicitly.

Secrets are never returned by the API — only masked hints.
"""
import logging
import os
import time
from typing import Any, Dict, Optional

from app.config import Settings, get_settings
from app.db.mongo import get_async_db, get_sync_db

logger = logging.getLogger(__name__)
settings = get_settings()

COLLECTION = "env_overrides"

# ── Registry ─────────────────────────────────────────────────────────────────
# name: the .env variable name (uppercase)
# kind: str | int | bool
# secret: masked in every API response and never editable except by super_admin
# restart: takes effect only after the process restarts
# settings_attr: Settings field that carries the .env value (None = os.environ only)
# env_only: value exists only in os.environ / overrides, never in Settings
# managed_elsewhere: value lives in another admin surface (Apify view)

EnvVarDef = Dict[str, Any]

ENVVAR_REGISTRY: list[EnvVarDef] = [
    # AI
    {"name": "GEMINI_API_KEY", "kind": "str", "secret": True, "restart": False,
     "settings_attr": "gemini_api_key", "default": "",
     "group": "AI", "description": "Google Gemini API key for AI comment analysis."},
    {"name": "GEMINI_MODEL", "kind": "str", "secret": False, "restart": True,
     "settings_attr": "gemini_model", "default": "gemini-2.5-flash",
     "group": "AI", "description": "Gemini model used for comment analysis "
                                   "(default of the ai.model setting)."},
    # Apify
    {"name": "APIFY_API_TOKEN", "kind": "str", "secret": True, "restart": False,
     "settings_attr": "apify_api_token", "default": "", "managed_elsewhere": True,
     "group": "Apify", "description": "Apify API token. Managed in the Apify view "
                                     "(DB override vs .env fallback)."},
    # Actors
    {"name": "INSTAGRAM_ACTOR_ID", "kind": "str", "secret": False, "restart": True,
     "settings_attr": "instagram_actor_id", "default": "apify/instagram-scraper",
     "group": "Actors", "description": "Instagram actor used for URL search "
                                      "(default of actor.instagram.main)."},
    {"name": "YOUTUBE_ACTOR_ID", "kind": "str", "secret": False, "restart": True,
     "settings_attr": "youtube_actor_id", "default": "streamers/youtube-scraper",
     "group": "Actors", "description": "YouTube actor used for URL search "
                                      "(default of actor.youtube.main)."},
    {"name": "LINKEDIN_ACTOR_ID", "kind": "str", "secret": False, "restart": True,
     "settings_attr": "linkedin_actor_id", "default": "harvestapi/linkedin-company",
     "group": "Actors", "description": "LinkedIn actor used for URL search "
                                      "(default of actor.linkedin.company)."},
    {"name": "LINKEDIN_POSTS_ACTOR_ID", "kind": "str", "secret": False, "restart": True,
     "settings_attr": "linkedin_posts_actor_id",
     "default": "harvestapi/linkedin-company-posts",
     "group": "Actors", "description": "LinkedIn posts actor (default of "
                                      "actor.linkedin.posts)."},
    # Scraping
    {"name": "MIN_COMMENTS", "kind": "int", "secret": False, "restart": True,
     "settings_attr": "min_comments", "default": 10,
     "group": "Scraping", "description": "Min comments for a post to qualify as "
                                        "lead material (0 disables)."},
    {"name": "MAX_COMMENTS_TO_COLLECT", "kind": "int", "secret": False, "restart": True,
     "settings_attr": "max_comments_to_collect", "default": 100,
     "group": "Scraping", "description": "Per-platform cap on comments per "
                                        "URL-search run."},
    # Security
    {"name": "ADMIN_EMAIL", "kind": "str", "secret": False, "restart": False,
     "settings_attr": "admin_email", "default": "admin@gmail.com",
     "group": "Security", "description": "Recovery super-admin account email. "
                                        "Read at login time — applies immediately."},
    {"name": "ADMIN_PASSWORD_HASH", "kind": "str", "secret": True, "restart": False,
     "settings_attr": "admin_password_hash", "default": "",
     "group": "Security", "description": "sha256 hash of the recovery admin "
                                        "password. Read at login time. Use "
                                        "'Change admin password' instead of "
                                        "pasting hashes."},
    {"name": "SESSION_SECRET", "kind": "str", "secret": True, "restart": False,
     "settings_attr": "session_secret", "default": "",
     "group": "Security", "description": "Secret signing session cookies. "
                                        "Changing it invalidates all active "
                                        "sessions immediately."},
    {"name": "SESSION_TTL_DAYS", "kind": "int", "secret": False, "restart": False,
     "settings_attr": "session_ttl_days", "default": 7,
     "group": "Security", "description": "Session lifetime in days (applied to "
                                        "new logins)."},
    {"name": "SESSION_COOKIE_SECURE", "kind": "bool", "secret": False, "restart": False,
     "settings_attr": "session_cookie_secure", "default": False,
     "group": "Security", "description": "Send the session cookie only over "
                                        "HTTPS. Enable behind a TLS proxy."},
    # Database
    {"name": "MONGO_URI", "kind": "str", "secret": False, "restart": True,
     "settings_attr": "mongo_uri", "default": "mongodb://localhost:27017",
     "group": "Database", "description": "MongoDB connection string. Changing "
                                        "it requires a restart (connections are "
                                        "opened at startup)."},
    {"name": "MONGO_DB_NAME", "kind": "str", "secret": False, "restart": True,
     "settings_attr": "mongo_db_name", "default": "LeadAI",
     "group": "Database", "description": "MongoDB database name. Requires a "
                                        "restart."},
    {"name": "DNS_SERVERS", "kind": "str", "secret": False, "restart": True,
     "settings_attr": "dns_servers", "default": "8.8.8.8,1.1.1.1",
     "group": "Database", "description": "Comma-separated DNS servers for "
                                        "mongodb+srv:// SRV resolution. "
                                        "Requires a restart."},
    # Server
    {"name": "API_PORT", "kind": "int", "secret": False, "restart": True,
     "settings_attr": None, "env_only": True, "default": 8000,
     "group": "Server", "description": "HTTP port the server listens on. Read "
                                      "by the launcher only."},
    {"name": "BUSINESS_DOMAIN", "kind": "str", "secret": False, "restart": False,
     "settings_attr": None, "env_only": True, "default": "",
     "group": "Server", "description": "Your business domain, stored for "
                                      "reference (not consumed by the app)."},
]

_REGISTRY: Dict[str, EnvVarDef] = {e["name"]: e for e in ENVVAR_REGISTRY}

_SECRET_NAMES = {e["name"] for e in ENVVAR_REGISTRY if e.get("secret")}
_MANAGED_ELSEWHERE = {e["name"] for e in ENVVAR_REGISTRY if e.get("managed_elsewhere")}

# TTL cache for hot paths (login / per-request secret reads). DB lookups are
# one read per key per TTL window at most; changes apply within 5 seconds.
_CACHE: Dict[str, tuple] = {}
_CACHE_TTL = 5.0


def _coerce(name: str, value: Any) -> Any:
    kind = _REGISTRY[name]["kind"]
    if kind == "int":
        try:
            return int(value)
        except (TypeError, ValueError):
            return _default(name)
    if kind == "bool":
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    return str(value)


def _default(name: str) -> Any:
    return _REGISTRY[name].get("default")


def _settings_value(name: str) -> Optional[Any]:
    attr = _REGISTRY[name].get("settings_attr")
    if not attr:
        return None
    return getattr(settings, attr, None)


def _is_env_source(name: str) -> bool:
    """True when the .env / process environment actually defines the var
    (i.e. the Settings value differs from its declared default)."""
    attr = _REGISTRY[name].get("settings_attr")
    if attr:
        field = Settings.model_fields.get(attr)
        declared = field.default if field else None
        return getattr(settings, attr, None) != declared
    return os.environ.get(name) is not None


# ── Low-level storage ────────────────────────────────────────────────────────

def _sync_override(name: str) -> Optional[Dict[str, Any]]:
    try:
        db = get_sync_db()
        if db is None:
            return None
        return db[COLLECTION].find_one({"_id": name})
    except Exception:
        return None


async def _async_override(name: str) -> Optional[Dict[str, Any]]:
    try:
        db = get_async_db()
        if db is None:
            return None
        return await db[COLLECTION].find_one({"_id": name})
    except Exception:
        return None


# ── Public accessors (runtime consumers) ─────────────────────────────────────

def get_envvar(name: str) -> Any:
    """Effective value: DB override -> .env/process value -> default."""
    if name not in _REGISTRY:
        return None
    now = time.time()
    hit = _CACHE.get(name)
    if hit is not None and now - hit[0] < _CACHE_TTL:
        return hit[1]
    doc = _sync_override(name)
    if doc is not None and doc.get("value") is not None:
        value = _coerce(name, doc["value"])
    else:
        env_value = _settings_value(name)
        if env_value is None or (isinstance(env_value, str) and not env_value):
            env_value = os.environ.get(name)
        value = _coerce(name, env_value) if env_value is not None else _default(name)
    _CACHE[name] = (now, value)
    return value


def get_envvar_str(name: str, fallback: str = "") -> str:
    value = get_envvar(name)
    return str(value) if value is not None else fallback


def get_envvar_int(name: str, fallback: int = 0) -> int:
    try:
        return int(get_envvar(name) or fallback)
    except (TypeError, ValueError):
        return fallback


def get_envvar_bool(name: str, fallback: bool = False) -> bool:
    value = get_envvar(name)
    return bool(value) if value is not None else fallback


def set_envvar_override(name: str, value: Any, by: str = "admin") -> bool:
    """Persist a coerced override. Returns False when Mongo is down."""
    if name not in _REGISTRY:
        return False
    coerced = _coerce(name, value)
    try:
        db = get_sync_db()
        if db is None:
            return False
        db[COLLECTION].update_one(
            {"_id": name},
            {"$set": {"value": coerced,
                      "updated_at": time.time(),
                      "updated_by": by}},
            upsert=True)
        _CACHE.pop(name, None)
        return True
    except Exception as e:
        logger.warning(f"Failed to persist env override {name}: {e}")
        return False


async def aset_envvar_override(name: str, value: Any, by: str = "admin") -> bool:
    coerced = _coerce(name, value)
    try:
        db = get_async_db()
        if db is None:
            return False
        await db[COLLECTION].update_one(
            {"_id": name},
            {"$set": {"value": coerced,
                      "updated_at": time.time(),
                      "updated_by": by}},
            upsert=True)
        _CACHE.pop(name, None)
        return True
    except Exception as e:
        logger.warning(f"Failed to persist env override {name}: {e}")
        return False


def delete_envvar_override(name: str) -> bool:
    try:
        db = get_sync_db()
        if db is None:
            return False
        db[COLLECTION].delete_one({"_id": name})
        _CACHE.pop(name, None)
        return True
    except Exception:
        return False


def _mask(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 4:
        return "••••"
    return "••••" + text[-4:]


# ── Reporting (admin panel) ──────────────────────────────────────────────────

def _source(name: str, override_doc: Optional[Dict[str, Any]]) -> str:
    if override_doc is not None:
        return "override"
    if _is_env_source(name):
        return "env"
    return "default"


def envvar_info(name: str) -> Dict[str, Any]:
    """One registry entry with effective value, source and override metadata."""
    definition = _REGISTRY[name]
    doc = _sync_override(name)
    source = _source(name, doc)

    if definition.get("managed_elsewhere"):
        value = _settings_value(name) or ""
        from app.admin.settings import get_apify_token, get_apify_token_hint
        if get_apify_token():
            value = get_apify_token_hint()
        info = {
            "value": value,
            "set": bool(value),
            "masked": _mask(value),
        }
    else:
        value = get_envvar(name)
        info = {
            "value": "" if definition.get("secret") else value,
            "set": value not in (None, "", False),
            "masked": _mask(value) if definition.get("secret") else None,
        }

    return {
        "name": name,
        "group": definition.get("group", ""),
        "description": definition.get("description", ""),
        "kind": definition.get("kind", "str"),
        "secret": bool(definition.get("secret")),
        "restart": bool(definition.get("restart")),
        "managed_elsewhere": bool(definition.get("managed_elsewhere")),
        "source": source,
        "overridden": doc is not None,
        "updated_at": (doc or {}).get("updated_at"),
        "updated_by": (doc or {}).get("updated_by"),
        **info,
    }


def all_envvar_info() -> list[Dict[str, Any]]:
    return [envvar_info(name) for name in _REGISTRY]


def is_secret(name: str) -> bool:
    return name in _SECRET_NAMES


def is_managed_elsewhere(name: str) -> bool:
    return name in _MANAGED_ELSEWHERE


def known_name(name: str) -> bool:
    return name in _REGISTRY


def clear_cache() -> None:
    _CACHE.clear()