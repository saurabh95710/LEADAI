"""
SaaS Plan Management and Configuration Engine.

Manages plan tiers (Free, Starter, Professional, Business, Enterprise),
feature flags, limit definitions, and plan caching.
"""
import logging
import time
from typing import Any, Dict, List, Optional
from bson import ObjectId

from app.db.models import utcnow
from app.db.mongo import get_async_db, get_sync_db

logger = logging.getLogger(__name__)

# In-memory cache for plans: {slug_or_id: (plan_dict, timestamp)}
_PLAN_CACHE: Dict[str, Any] = {}
_ALL_PLANS_CACHE: Optional[tuple[List[Dict[str, Any]], float]] = None
_CACHE_TTL_SECONDS = 60.0

DEFAULT_PLANS: List[Dict[str, Any]] = [
    {
        "name": "Free",
        "slug": "free",
        "description": "Explore social discovery with basic features.",
        "status": "active",
        "price_monthly": 0.0,
        "price_yearly": 0.0,
        "allows_byok": False,
        "currency": "USD",
        "trial_days": 0,
        "display_order": 1,
        "is_public": True,
        "is_default": False,
        "is_trial": False,
        "features": [
            "url_search",
            "facebook",
        ],
        "limits": {
            "monthly_searches": 5,
            "monthly_posts": 50,
            "monthly_comments": 100,
            "monthly_ai_analyses": 25,
            "monthly_exports": 2,
            "team_members": 1,
        },
    },
    {
        "name": "Starter",
        "slug": "starter",
        "description": "Essential lead intelligence for solo founders and consultants.",
        "status": "active",
        "price_monthly": 49.0,
        "price_yearly": 490.0,
        "allows_byok": True,
        # taken off the price when the customer brings that API key (the plan price includes both)
        "api_addons": {"apify": {"monthly": 12.0, "yearly": 120.0},
                       "gemini": {"monthly": 8.0, "yearly": 80.0}},
        "currency": "USD",
        "trial_days": 14,
        "display_order": 2,
        "is_public": True,
        "is_default": False,
        "is_trial": False,
        "features": [
            "url_search",
            "facebook",
            "instagram",
            "ai_analysis",
            "lead_scoring",
            "csv_export",
            "keyword_search",
        ],
        "limits": {
            "monthly_searches": 50,
            "monthly_posts": 500,
            "monthly_comments": 1500,
            "monthly_ai_analyses": 500,
            "monthly_exports": 20,
            "team_members": 3,
        },
    },
    {
        "name": "Professional",
        "slug": "pro",
        "description": "Complete multi-platform social discovery & AI lead qualifying for growing teams.",
        "status": "active",
        "price_monthly": 149.0,
        "price_yearly": 1490.0,
        "allows_byok": True,
        # taken off the price when the customer brings that API key (the plan price includes both)
        "api_addons": {"apify": {"monthly": 36.0, "yearly": 360.0},
                       "gemini": {"monthly": 24.0, "yearly": 240.0}},
        "currency": "USD",
        "trial_days": 14,
        "display_order": 3,
        "is_public": True,
        "is_default": True,
        "is_trial": True,
        "features": [
            "url_search",
            "facebook",
            "instagram",
            "youtube",
            "linkedin",
            "ai_analysis",
            "lead_scoring",
            "advanced_filters",
            "csv_export",
            "keyword_search",
            "team_management",
        ],
        "limits": {
            "monthly_searches": 500,
            "monthly_posts": 5000,
            "monthly_comments": 15000,
            "monthly_ai_analyses": 5000,
            "monthly_exports": 100,
            "team_members": 10,
        },
    },
    {
        "name": "Business",
        "slug": "business",
        "description": "Advanced volume, automation, webhooks, and team collaboration.",
        "status": "active",
        "price_monthly": 399.0,
        "price_yearly": 3990.0,
        "allows_byok": True,
        # taken off the price when the customer brings that API key (the plan price includes both)
        "api_addons": {"apify": {"monthly": 96.0, "yearly": 960.0},
                       "gemini": {"monthly": 64.0, "yearly": 640.0}},
        "currency": "USD",
        "trial_days": 14,
        "display_order": 4,
        "is_public": True,
        "is_default": False,
        "is_trial": False,
        "features": [
            "url_search",
            "facebook",
            "instagram",
            "youtube",
            "linkedin",
            "ai_analysis",
            "lead_scoring",
            "advanced_filters",
            "csv_export",
            "keyword_search",
            "team_management",
            "api_access",
            "webhooks",
        ],
        "limits": {
            "monthly_searches": 2000,
            "monthly_posts": 20000,
            "monthly_comments": 60000,
            "monthly_ai_analyses": 20000,
            "monthly_exports": 500,
            "team_members": 25,
        },
    },
    {
        "name": "Enterprise",
        "slug": "enterprise",
        "description": "Maximum scale, custom quotas, custom branding, and priority SLA.",
        "status": "active",
        "price_monthly": 999.0,
        "price_yearly": 9990.0,
        "allows_byok": True,
        # taken off the price when the customer brings that API key (the plan price includes both)
        "api_addons": {"apify": {"monthly": 240.0, "yearly": 2400.0},
                       "gemini": {"monthly": 160.0, "yearly": 1600.0}},
        "currency": "USD",
        "trial_days": 30,
        "display_order": 5,
        "is_public": True,
        "is_default": False,
        "is_trial": False,
        "features": [
            "url_search",
            "facebook",
            "instagram",
            "youtube",
            "linkedin",
            "ai_analysis",
            "lead_scoring",
            "advanced_filters",
            "csv_export",
            "keyword_search",
            "team_management",
            "api_access",
            "webhooks",
            "custom_branding",
            "priority_support",
        ],
        "limits": {
            "monthly_searches": 10000,
            "monthly_posts": 100000,
            "monthly_comments": 300000,
            "monthly_ai_analyses": 100000,
            "monthly_exports": 2500,
            "team_members": 100,
        },
    },
]


# Limit keys added for the unified plan system (Step 1.6). Seeded onto the
# defaults above and BACKFILLED onto existing plan documents only where the
# key is missing — an operator's edited values are never overwritten.
PLAN_LIMIT_SEED: Dict[str, Dict[str, int]] = {
    "free":       {"monthly_tokens": 100,    "posts_per_search": 10,  "comments_per_post": 20,
                   "max_leads": 100,     "storage_mb": 100},
    "starter":    {"monthly_tokens": 2000,   "posts_per_search": 25,  "comments_per_post": 50,
                   "max_leads": 2000,    "storage_mb": 1024},
    "pro":        {"monthly_tokens": 10000,  "posts_per_search": 50,  "comments_per_post": 100,
                   "max_leads": 20000,   "storage_mb": 5120},
    "business":   {"monthly_tokens": 40000,  "posts_per_search": 100, "comments_per_post": 200,
                   "max_leads": 100000,  "storage_mb": 20480},
    "enterprise": {"monthly_tokens": 200000, "posts_per_search": 200, "comments_per_post": 500,
                   "max_leads": 1000000, "storage_mb": 102400},
}
# Keyword / discovery search limits per paid tier (keywords per search,
# profiles discovered per search across platforms, recent posts analyzed per
# profile). The Global Settings caps (keyword.max_*) still apply on top.
KEYWORD_LIMIT_SEED: Dict[str, Dict[str, int]] = {
    "starter":    {"keyword_max_keywords": 3,  "keyword_max_candidates": 100,  "keyword_recent_posts": 20},
    "pro":        {"keyword_max_keywords": 5,  "keyword_max_candidates": 250,  "keyword_recent_posts": 30},
    "business":   {"keyword_max_keywords": 10, "keyword_max_candidates": 500,  "keyword_recent_posts": 50},
    "enterprise": {"keyword_max_keywords": 10, "keyword_max_candidates": 500,  "keyword_recent_posts": 50},
}
# a paid plan created by an operator (not one of the defaults) gets the entry tier
KEYWORD_LIMIT_FALLBACK: Dict[str, int] = dict(KEYWORD_LIMIT_SEED["starter"])
for _slug, _kw in KEYWORD_LIMIT_SEED.items():
    PLAN_LIMIT_SEED[_slug] = {**PLAN_LIMIT_SEED.get(_slug, {}), **_kw}

for _p in DEFAULT_PLANS:
    _p["limits"] = {**PLAN_LIMIT_SEED.get(_p["slug"], {}), **_p.get("limits", {})}

# Every limit key a plan can carry (editors show these; missing = 0 / not included)
PLAN_LIMIT_KEYS = (
    "monthly_tokens", "monthly_searches", "posts_per_search", "comments_per_post",
    "monthly_posts", "monthly_comments", "monthly_ai_analyses", "monthly_exports",
    "team_members", "max_leads", "storage_mb",
    "keyword_max_keywords", "keyword_max_candidates", "keyword_recent_posts",
)


# Human labels for feature keys (website, billing and admin screens share these)
FEATURE_LABELS: Dict[str, str] = {
    "url_search": "URL Search Agent",
    "keyword_search": "Keyword discovery search",
    "facebook": "Facebook",
    "instagram": "Instagram",
    "youtube": "YouTube",
    "linkedin": "LinkedIn",
    "ai_analysis": "AI comment analysis",
    "lead_scoring": "Lead scoring",
    "advanced_filters": "Advanced lead filters",
    "csv_export": "CSV exports",
    "team_management": "Team management",
    "api_access": "API access",
    "webhooks": "Webhooks",
    "custom_branding": "Custom branding",
    "priority_support": "Priority support",
}


def plan_highlights(plan: Dict[str, Any]) -> List[str]:
    """Customer-facing bullet list derived from a plan's limits + features."""
    lim = plan.get("limits") or {}
    out: List[str] = []

    def num(v: int) -> str:
        return f"{v:,}"
    if lim.get("monthly_tokens"):
        out.append(f"{num(lim['monthly_tokens'])} tokens / month")
    if lim.get("monthly_searches"):
        out.append(f"{num(lim['monthly_searches'])} searches / month")
    if lim.get("posts_per_search"):
        out.append(f"Up to {num(lim['posts_per_search'])} posts per search")
    if lim.get("comments_per_post"):
        out.append(f"Up to {num(lim['comments_per_post'])} comments per post")
    if lim.get("team_members"):
        n = lim["team_members"]
        out.append(f"{num(n)} team member{'s' if n != 1 else ''}")
    platforms = [FEATURE_LABELS[f] for f in ("facebook", "instagram", "youtube", "linkedin")
                 if f in (plan.get("features") or [])]
    if platforms:
        out.append(", ".join(platforms))
    for f in plan.get("features") or []:
        if f in ("url_search", "facebook", "instagram", "youtube", "linkedin"):
            continue
        out.append(FEATURE_LABELS.get(f, f.replace("_", " ").capitalize()))
    return out


def invalidate_plan_cache() -> None:
    """Clear in-memory cached plans so configuration updates apply immediately."""
    global _PLAN_CACHE, _ALL_PLANS_CACHE
    _PLAN_CACHE.clear()
    _ALL_PLANS_CACHE = None


def _clean_plan(doc: Dict[str, Any]) -> Dict[str, Any]:
    if not doc:
        return {}
    out: Dict[str, Any] = {}
    for k, v in doc.items():
        if k == "_id":
            out["id"] = str(v)
        elif isinstance(v, ObjectId):
            out[k] = str(v)
        elif isinstance(v, dict):
            out[k] = _clean_plan(v)
        elif isinstance(v, list):
            out[k] = [_clean_plan(item) if isinstance(item, dict) else (str(item) if isinstance(item, ObjectId) else item) for item in v]
        else:
            out[k] = v
    return out


# ── Who provides each external API, and what it costs ─────────────────────────
# A plan's price_monthly / price_yearly is the ALL-INCLUDED price (LeadAI
# provides Apify and Gemini). ``api_addons[api][cycle]`` is taken off when the
# customer brings that API's key instead. Plans without api_addons fall back
# to the older single "bring both" price (price_*_byok), split 60/40.
API_PROVIDERS = ("apify", "gemini")
API_LABELS = {"apify": "Apify (scraping)", "gemini": "Google Gemini (AI analysis)"}
COVERAGE_VALUES = ("leadai", "own")
ALL_INCLUDED = {"apify": "leadai", "gemini": "leadai"}
_LEGACY_SPLIT = {"apify": 0.6, "gemini": 0.4}


def normalize_coverage(value: Any = None, *, api_mode: Optional[str] = None) -> Dict[str, str]:
    """{"apify": "leadai"|"own", "gemini": ...}; ``api_mode="byok"`` (old
    all-or-nothing switch) means "own" for both."""
    out = dict(ALL_INCLUDED)
    if isinstance(value, dict):
        for api in API_PROVIDERS:
            v = str(value.get(api) or "").strip().lower()
            if v in ("own", "byok", "customer"):
                out[api] = "own"
    elif api_mode and str(api_mode).strip().lower() == "byok":
        out = {api: "own" for api in API_PROVIDERS}
    return out


def _money2(v: Any) -> float:
    try:
        return round(max(0.0, float(v or 0)), 2)
    except (TypeError, ValueError):
        return 0.0


def plan_api_pricing(plan: Dict[str, Any]) -> Dict[str, Any]:
    """{"allows_own": bool, "addons": {api: {"monthly": x, "yearly": y}}}."""
    allows = bool(plan.get("allows_byok", True)) and _money2(plan.get("price_monthly")) > 0
    stored = plan.get("api_addons") if isinstance(plan.get("api_addons"), dict) else None
    addons: Dict[str, Dict[str, float]] = {}
    for api in API_PROVIDERS:
        addons[api] = {}
        for cycle in ("monthly", "yearly"):
            full = _money2(plan.get(f"price_{cycle}"))
            if stored is not None:
                val = _money2((stored.get(api) or {}).get(cycle))
            else:
                legacy = plan.get(f"price_{cycle}_byok")
                gap = max(0.0, full - _money2(legacy)) if legacy not in (None, "", 0, 0.0) else 0.0
                val = round(gap * _LEGACY_SPLIT[api], 2)
            addons[api][cycle] = min(val, full)
    return {"allows_own": allows, "addons": addons}


def price_for(plan: Dict[str, Any], coverage: Optional[Dict[str, str]], cycle: str) -> float:
    """What the customer pays for ``plan`` per ``cycle`` with this API coverage.
    The one place the price is computed (checkout, renewal, pricing pages)."""
    cycle = "yearly" if cycle == "yearly" else "monthly"
    full = _money2(plan.get(f"price_{cycle}"))
    cov = normalize_coverage(coverage)
    pricing = plan_api_pricing(plan)
    if not pricing["allows_own"]:
        return full
    off = sum(pricing["addons"][api][cycle] for api in API_PROVIDERS if cov[api] == "own")
    return round(max(0.0, full - off), 2)


def coverage_options(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every coverage choice this plan offers, with its monthly / yearly price."""
    combos = [("all_included", "All included", {"apify": "leadai", "gemini": "leadai"}),
              ("own_apify", "Own Apify key", {"apify": "own", "gemini": "leadai"}),
              ("own_gemini", "Own Gemini key", {"apify": "leadai", "gemini": "own"}),
              ("own_both", "Bring both keys", {"apify": "own", "gemini": "own"})]
    allows = plan_api_pricing(plan)["allows_own"]
    return [{"key": k, "label": label, "coverage": cov,
             "price_monthly": price_for(plan, cov, "monthly"), "price_yearly": price_for(plan, cov, "yearly")}
            for k, label, cov in combos if allows or k == "all_included"]


def coverage_label(coverage: Optional[Dict[str, str]]) -> str:
    cov = normalize_coverage(coverage)
    own = [api for api in API_PROVIDERS if cov[api] == "own"]
    if not own:
        return "All included"
    if len(own) == 2:
        return "Bring both keys"
    return f"Own {own[0].capitalize()} key"


async def ensure_default_plans(db=None) -> None:
    """Seed initial default plans if the collection is unpopulated or missing standard plans."""
    if db is None:
        db = get_async_db()
    if db is None:
        return

    try:
        for plan_spec in DEFAULT_PLANS:
            existing = await db.plans.find_one({"slug": plan_spec["slug"]})
            if not existing:
                to_insert = dict(plan_spec)
                to_insert["created_at"] = utcnow()
                to_insert["updated_at"] = utcnow()
                await db.plans.insert_one(to_insert)
                logger.info(f"Seeded SaaS plan: {plan_spec['name']} ({plan_spec['slug']})")
        # Backfill new limit keys and per-API pricing onto existing plans (never overwrite)
        defaults_by_slug = {p["slug"]: p for p in DEFAULT_PLANS}
        async for plan in db.plans.find({}):
            slug = plan.get("slug")
            seed = PLAN_LIMIT_SEED.get(slug, {})
            limits = plan.get("limits") or {}
            missing = {f"limits.{k}": v for k, v in seed.items() if k not in limits}
            def_spec = defaults_by_slug.get(slug)
            if def_spec:
                if "allows_byok" not in plan:
                    missing["allows_byok"] = def_spec.get("allows_byok", True)
                if "api_addons" not in plan and def_spec.get("api_addons"):
                    missing["api_addons"] = def_spec["api_addons"]
            if missing:
                await db.plans.update_one({"_id": plan["_id"]}, {"$set": missing})
        invalidate_plan_cache()
    except Exception as e:
        logger.warning(f"Error ensuring default plans: {e}")


KEYWORD_PLANS_MIGRATION = "keyword_search_plans_v1"


def migrate_keyword_search_plans(db) -> int:
    """Once per database: give every active PAID plan the "keyword_search"
    feature and its keyword limits where missing (an operator's own values are
    kept), and give the stored free-trial settings the new keyword keys.
    Later Super Admin changes stick: a marker in ``migrations`` stops it
    running again. Returns the number of plans changed."""
    if db is None or db.migrations.find_one({"_id": KEYWORD_PLANS_MIGRATION}):
        return 0
    changed = 0
    for plan in db.plans.find({"status": "active"}):
        try:
            paid = float(plan.get("price_monthly") or 0) > 0 or float(plan.get("price_yearly") or 0) > 0
        except (TypeError, ValueError):
            paid = False
        if not paid:
            continue
        limits = plan.get("limits") or {}
        seed = KEYWORD_LIMIT_SEED.get(plan.get("slug") or "", KEYWORD_LIMIT_FALLBACK)
        sets: Dict[str, Any] = {f"limits.{k}": v for k, v in seed.items() if k not in limits}
        update: Dict[str, Any] = {}
        if sets:
            update["$set"] = {**sets, "updated_at": utcnow()}
        if "keyword_search" not in (plan.get("features") or []):
            update["$addToSet"] = {"features": "keyword_search"}
        if update:
            db.plans.update_one({"_id": plan["_id"]}, update)
            changed += 1
    try:  # stored free-trial settings: add the new keyword keys (values kept)
        from app.lifecycle.config import COLLECTION, DEMO_CONFIG_SEED, clear_cache
        doc = db[COLLECTION].find_one({"_id": "demo"})
        if doc is not None:
            stored = doc.get("value") or {}
            missing = {f"value.{k}": v for k, v in DEMO_CONFIG_SEED.items()
                       if k.startswith("keyword_") and k not in stored}
            if missing:
                db[COLLECTION].update_one({"_id": "demo"}, {"$set": missing})
                clear_cache("demo")
    except Exception as e:
        logger.warning("keyword trial settings migration skipped: %s", e)
    db.migrations.update_one({"_id": KEYWORD_PLANS_MIGRATION},
                             {"$set": {"done_at": utcnow(), "plans_changed": changed}}, upsert=True)
    invalidate_plan_cache()
    return changed


async def get_all_plans(active_only: bool = True, db=None) -> List[Dict[str, Any]]:
    """Retrieve all plans ordered by display_order."""
    global _ALL_PLANS_CACHE
    now = time.time()
    if _ALL_PLANS_CACHE and (now - _ALL_PLANS_CACHE[1] < _CACHE_TTL_SECONDS):
        cached = _ALL_PLANS_CACHE[0]
        if active_only:
            return [p for p in cached if p.get("status") == "active"]
        return cached

    if db is None:
        db = get_async_db()
    if db is None:
        return []

    query = {"status": "active"} if active_only else {}
    cursor = db.plans.find(query).sort("display_order", 1)
    results = []
    async for doc in cursor:
        results.append(_clean_plan(doc))

    _ALL_PLANS_CACHE = (results, now)
    return results


async def get_plan_by_slug_or_id(identifier: str, db=None) -> Optional[Dict[str, Any]]:
    """Retrieve a plan by its slug or MongoDB ObjectId, with caching."""
    if not identifier:
        return None
    now = time.time()
    cache_key = str(identifier).strip().lower()

    if cache_key in _PLAN_CACHE:
        entry, stamp = _PLAN_CACHE[cache_key]
        if now - stamp < _CACHE_TTL_SECONDS:
            return entry

    if db is None:
        db = get_async_db()
    if db is None:
        return None

    # Try by slug first
    doc = await db.plans.find_one({"slug": cache_key})
    if not doc:
        try:
            doc = await db.plans.find_one({"_id": ObjectId(identifier)})
        except Exception:
            pass

    if doc:
        cleaned = _clean_plan(doc)
        _PLAN_CACHE[cache_key] = (cleaned, now)
        if cleaned.get("slug"):
            _PLAN_CACHE[cleaned["slug"]] = (cleaned, now)
        if cleaned.get("id"):
            _PLAN_CACHE[cleaned["id"]] = (cleaned, now)
        return cleaned

    return None


def get_plan_sync(identifier: str) -> Optional[Dict[str, Any]]:
    """Synchronous plan lookup for background thread agents."""
    if not identifier:
        return None
    cache_key = str(identifier).strip().lower()
    now = time.time()
    if cache_key in _PLAN_CACHE:
        entry, stamp = _PLAN_CACHE[cache_key]
        if now - stamp < _CACHE_TTL_SECONDS:
            return entry

    db = get_sync_db()
    if db is None:
        return None

    doc = db.plans.find_one({"slug": cache_key})
    if not doc:
        try:
            doc = db.plans.find_one({"_id": ObjectId(identifier)})
        except Exception:
            pass

    if doc:
        cleaned = _clean_plan(doc)
        _PLAN_CACHE[cache_key] = (cleaned, now)
        return cleaned

    return None
