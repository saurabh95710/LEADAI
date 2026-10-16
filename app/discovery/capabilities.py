"""What each platform can measure for a keyword search.

Every filter is ``supported`` (the actor output carries the value),
``partial`` (only sometimes, or only from free text such as a bio), or
``unsupported`` (no actor we use returns it). ``stage`` names the pipeline
stage whose actor provides the value:

  discovery   the keyword search actor itself
  enrichment  a per-profile details actor (only where discovery lacks it)
  sampling    the recent-posts actor (post-level metrics)

Facts come from the actors' published input/output schemas (verified
2026-10-15). Values an actor *may* return but that were not verified are
``partial`` and are parsed defensively: a missing value is "unknown", never 0.
"""
from typing import Any, Dict, List, Optional

PLATFORMS = ("instagram", "facebook", "youtube", "linkedin")

SUPPORTED = "supported"
PARTIAL = "partial"
UNSUPPORTED = "unsupported"

# filter key -> description used by the API / UI. ``level``: profile filters
# run right after discovery/enrichment (cheap); post filters need sampled
# posts; ``parameter`` is not a filter but sets how many posts are sampled;
# ``option`` modifies another filter (location_match -> location_keyword).
# ``hidden``: still accepted by the API (compatibility) but not offered by the
# UI and left out of the estimate notes (no platform can measure it).
FILTERS: Dict[str, Dict[str, Any]] = {
    "min_followers": {"label": "Minimum followers", "type": "int", "level": "profile",
                      "metric": "followers"},
    "max_followers": {"label": "Maximum followers", "type": "int", "level": "profile",
                      "metric": "followers"},
    "min_posts": {"label": "Minimum total posts", "type": "int", "level": "profile",
                  "metric": "posts_count"},
    "recent_posts_to_analyze": {"label": "Recent posts to analyze", "type": "int",
                                "level": "parameter", "metric": None},
    "min_avg_comments": {"label": "Minimum average comments per post", "type": "float",
                         "level": "posts", "metric": "avg_comments"},
    "min_total_comments": {"label": "Minimum comments across analyzed posts", "type": "int",
                           "level": "posts", "metric": "total_comments"},
    "min_engagement_rate": {"label": "Minimum engagement rate (%)", "type": "float",
                            "level": "posts", "metric": "engagement_rate", "unit": "percent"},
    "active_within_days": {"label": "Posted within the last N days", "type": "int",
                           "level": "posts", "metric": "last_post_at"},
    "business_account_only": {"label": "Business accounts only", "type": "bool",
                              "level": "profile", "metric": "is_business"},
    "has_contact_info": {"label": "Has contact info", "type": "bool", "level": "profile",
                         "metric": "contacts"},
    "location_keyword": {"label": "Location contains", "type": "str", "level": "profile",
                         "metric": "location"},
    "location_match": {"label": "Location must mention", "type": "choice", "level": "option",
                       "metric": None, "choices": ["all", "any"], "default": "all",
                       "applies_to": "location_keyword"},
    "category_keyword": {"label": "Business type contains", "type": "str", "level": "profile",
                         "metric": "category"},
    "language": {"label": "Language", "type": "str", "level": "profile", "metric": "language",
                 "hidden": True},
    "exclude_keywords": {"label": "Exclude names / bios containing", "type": "list",
                         "level": "profile", "metric": "name_bio"},
    "verified_only": {"label": "Verified accounts only", "type": "bool", "level": "profile",
                      "metric": "verified"},
}

PROFILE_FILTERS = tuple(k for k, v in FILTERS.items() if v["level"] == "profile")
POST_FILTERS = tuple(k for k, v in FILTERS.items() if v["level"] == "posts")


def _c(support: str, stage: Optional[str] = None, note: str = "") -> Dict[str, Any]:
    return {"support": support, "stage": stage, "note": note}


_ENGAGEMENT_NOTE = "average (likes + comments{shares}) per analyzed post / followers x 100"

MATRIX: Dict[str, Dict[str, Dict[str, Any]]] = {
    "instagram": {
        "min_followers": _c(SUPPORTED, "discovery", "followersCount"),
        "max_followers": _c(SUPPORTED, "discovery", "followersCount"),
        "min_posts": _c(SUPPORTED, "discovery", "postsCount"),
        "recent_posts_to_analyze": _c(SUPPORTED, "sampling",
                                      "up to 12 come with discovery; more need the post scraper"),
        "min_avg_comments": _c(SUPPORTED, "sampling", "commentsCount per post"),
        "min_total_comments": _c(SUPPORTED, "sampling", "commentsCount per post"),
        "min_engagement_rate": _c(SUPPORTED, "sampling",
                                  _ENGAGEMENT_NOTE.format(shares="") + "; hidden likes are left out"),
        "active_within_days": _c(SUPPORTED, "sampling", "latest post timestamp"),
        "business_account_only": _c(SUPPORTED, "discovery", "isBusinessAccount"),
        "has_contact_info": _c(PARTIAL, "discovery",
                               "bio text and bio links only; Instagram's contact button is not exposed"),
        "location_keyword": _c(PARTIAL, "discovery", "bio / business address text; no country field"),
        "category_keyword": _c(PARTIAL, "discovery", "business category (business accounts only) and bio"),
        "language": _c(UNSUPPORTED, None, "no language field"),
        "exclude_keywords": _c(SUPPORTED, "discovery", "name and bio"),
        "verified_only": _c(SUPPORTED, "discovery", "verified"),
    },
    "facebook": {
        "min_followers": _c(SUPPORTED, "enrichment",
                            "page followers (from search when present, else the pages scraper)"),
        "max_followers": _c(SUPPORTED, "enrichment",
                            "page followers (from search when present, else the pages scraper)"),
        "min_posts": _c(UNSUPPORTED, None, "Facebook doesn't expose a page's total post count"),
        "recent_posts_to_analyze": _c(SUPPORTED, "sampling", "posts scraper"),
        "min_avg_comments": _c(SUPPORTED, "sampling", "comments per post"),
        "min_total_comments": _c(SUPPORTED, "sampling", "comments per post"),
        "min_engagement_rate": _c(SUPPORTED, "sampling", _ENGAGEMENT_NOTE.format(shares=" + shares")),
        "active_within_days": _c(SUPPORTED, "sampling", "latest post time"),
        "business_account_only": _c(SUPPORTED, "discovery", "every Facebook Page is a business/public page"),
        "has_contact_info": _c(SUPPORTED, "enrichment", "email, phone and website of the page"),
        "location_keyword": _c(PARTIAL, "enrichment", "address text only"),
        "category_keyword": _c(SUPPORTED, "discovery", "page categories"),
        "language": _c(UNSUPPORTED, None, "no language field"),
        "exclude_keywords": _c(SUPPORTED, "discovery", "name and intro"),
        "verified_only": _c(PARTIAL, "discovery", "only some search results carry the verified badge"),
    },
    "youtube": {
        "min_followers": _c(SUPPORTED, "enrichment", "subscribers"),
        "max_followers": _c(SUPPORTED, "enrichment", "subscribers"),
        "min_posts": _c(SUPPORTED, "enrichment", "total videos"),
        "recent_posts_to_analyze": _c(SUPPORTED, "sampling", "newest videos"),
        "min_avg_comments": _c(SUPPORTED, "sampling", "comments per video"),
        "min_total_comments": _c(SUPPORTED, "sampling", "comments per video"),
        "min_engagement_rate": _c(SUPPORTED, "sampling",
                                  _ENGAGEMENT_NOTE.format(shares="") + " (subscribers; views not used)"),
        "active_within_days": _c(SUPPORTED, "sampling", "newest video date"),
        "business_account_only": _c(UNSUPPORTED, None, "YouTube has no business-account flag"),
        "has_contact_info": _c(PARTIAL, "enrichment", "links in the channel description; no email"),
        "location_keyword": _c(PARTIAL, "enrichment", "channel country / description"),
        "category_keyword": _c(PARTIAL, "enrichment", "channel description only; YouTube has no category"),
        "language": _c(UNSUPPORTED, None, "no language field"),
        "exclude_keywords": _c(SUPPORTED, "discovery", "channel name and description"),
        "verified_only": _c(SUPPORTED, "enrichment", "verified badge"),
    },
    "linkedin": {
        "min_followers": _c(SUPPORTED, "discovery", "followerCount"),
        "max_followers": _c(SUPPORTED, "discovery", "followerCount"),
        "min_posts": _c(UNSUPPORTED, None, "LinkedIn doesn't expose a company's total post count"),
        "recent_posts_to_analyze": _c(SUPPORTED, "sampling", "company posts scraper"),
        "min_avg_comments": _c(SUPPORTED, "sampling", "engagement.comments"),
        "min_total_comments": _c(SUPPORTED, "sampling", "engagement.comments"),
        "min_engagement_rate": _c(SUPPORTED, "sampling", _ENGAGEMENT_NOTE.format(shares=" + shares")),
        "active_within_days": _c(SUPPORTED, "sampling", "postedAt"),
        "business_account_only": _c(SUPPORTED, "discovery", "company pages only"),
        "has_contact_info": _c(PARTIAL, "discovery", "website and phone; no email"),
        "location_keyword": _c(SUPPORTED, "discovery", "headquarters city / country"),
        "category_keyword": _c(SUPPORTED, "discovery", "industries"),
        "language": _c(UNSUPPORTED, None, "no language field"),
        "exclude_keywords": _c(SUPPORTED, "discovery", "name and description"),
        "verified_only": _c(PARTIAL, "discovery", "pageVerified when present"),
    },
}

# Settings keys of the actor used per platform and stage (resolved at run
# time so an admin change applies to the next run). None = stage not needed.
STAGE_ACTOR_KEYS: Dict[str, Dict[str, Optional[str]]] = {
    "instagram": {"discovery": "discovery.actor.instagram.search", "enrichment": None,
                  "sampling": "discovery.actor.instagram.posts"},
    "facebook": {"discovery": "discovery.actor.facebook.search", "enrichment": "actor.facebook.pages",
                 "sampling": "actor.facebook.posts"},
    "youtube": {"discovery": "discovery.actor.youtube.channel_search", "enrichment": "discovery.actor.youtube.channel",
                "sampling": "actor.youtube.main"},
    "linkedin": {"discovery": "discovery.actor.linkedin.search", "enrichment": None,
                 "sampling": "actor.linkedin.posts"},
}

# Rough Apify prices (USD per item, free tier, pay per event) — for the cost
# estimate only. Comments are a generic rough figure.
PRICING_USD: Dict[str, Dict[str, float]] = {
    "instagram": {"discovery": 0.0027, "enrichment": 0.0, "post": 0.0017, "comment": 0.0023},
    "facebook": {"discovery": 0.012, "enrichment": 0.01, "post": 0.005, "comment": 0.0025},
    "youtube": {"discovery": 0.004, "enrichment": 0.0013, "post": 0.004, "comment": 0.0025},
    "linkedin": {"discovery": 0.004, "enrichment": 0.0, "post": 0.002, "comment": 0.002},
}

# How many recent posts the discovery output already includes (no extra call)
DISCOVERY_POSTS = {"instagram": 12, "facebook": 0, "youtube": 0, "linkedin": 0}


def capability(platform: str, filter_key: str) -> str:
    return (MATRIX.get(platform, {}).get(filter_key) or {}).get("support", UNSUPPORTED)


def resolve_actor(key: Optional[str]) -> str:
    """Actor id stored under a settings key ("" when unset)."""
    if not key:
        return ""
    from app.admin.settings import get_str
    return get_str(key, "")


def stage_actors(platform: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for stage, key in STAGE_ACTOR_KEYS.get(platform, {}).items():
        actor = resolve_actor(key)
        if platform == "youtube" and stage == "discovery" and not actor:
            actor = resolve_actor("actor.youtube.main")  # video search, channels deduped
        out[stage] = actor
    return out


def matrix_payload() -> Dict[str, Any]:
    """The matrix for the API: filters, and per platform each filter's support."""
    platforms: Dict[str, Any] = {}
    for platform in PLATFORMS:
        actors = stage_actors(platform)
        platforms[platform] = {
            "filters": {k: dict(v) for k, v in MATRIX[platform].items()},
            "actors": actors,
            "discovery_posts": DISCOVERY_POSTS[platform],
        }
    return {"filters": {k: dict(v) for k, v in FILTERS.items()}, "platforms": platforms}


def is_hidden(filter_key: str) -> bool:
    return bool((FILTERS.get(filter_key) or {}).get("hidden"))


def unsupported_filters(platform: str, filters: Dict[str, Any]) -> List[str]:
    """Enabled filters this platform can't measure (hidden filters left out)."""
    return [k for k in filters if k in MATRIX.get(platform, {}) and not is_hidden(k)
            and capability(platform, k) == UNSUPPORTED]
