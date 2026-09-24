"""
Organization-level lead qualification rules.

An organization Admin can set its own lead keywords (e.g. "property, buy,
rent, house, apartment, price, interested") in the Admin Portal. They are
stored on the organization document:

    organizations.settings.lead_keywords          include keywords
    organizations.settings.lead_exclude_keywords  veto keywords (optional)

When an organization has no keywords the pipeline falls back to the global
defaults (the Super Admin's active comment-filter rule, or no filter).

Public API
  org_keywords(org_id)            -> list[str] | None   (None = use global defaults)
  org_exclude_keywords(org_id)    -> list[str]
  matches(text, keywords, exclude=None) -> bool
  org_rule(org_id)                -> comment-filter rule dict | None

``org_rule`` returns a rule in exactly the shape ``comment_filter`` evaluates
(the same shape as ``build_inline_rule``), so the pipeline hook is a one-line
fallback in ``comment_filter.resolve_effective_rule``.
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _org_settings(org_id: Any, db=None) -> Dict[str, Any]:
    if not org_id:
        return {}
    try:
        from bson import ObjectId
        if db is None:
            from app.db.mongo import get_sync_db
            db = get_sync_db()
        if db is None:
            return {}
        org = db.organizations.find_one({"_id": ObjectId(str(org_id))}, {"settings": 1})
        return (org or {}).get("settings") or {}
    except Exception as e:  # never break the pipeline over a lookup
        logger.debug("org lead rules lookup failed for %s: %s", org_id, e)
        return {}


def _normalize(value: Any) -> List[str]:
    from app.pipeline.comment_filter import normalize_keyword_list
    return normalize_keyword_list(value or [])


def org_keywords(org_id: Any, db=None) -> Optional[List[str]]:
    """The organization's lead keywords, or None when it has none (the
    caller then applies the global defaults)."""
    kws = _normalize(_org_settings(org_id, db).get("lead_keywords"))
    return kws or None


def org_exclude_keywords(org_id: Any, db=None) -> List[str]:
    return _normalize(_org_settings(org_id, db).get("lead_exclude_keywords"))


def matches(text: Optional[str], keywords: Optional[List[str]],
            exclude: Optional[List[str]] = None) -> bool:
    """True when ``text`` contains any keyword (same word-boundary matching
    as the global comment filter) and no exclude keyword. An empty keyword
    list matches everything (no filter)."""
    from app.pipeline.comment_filter import keyword_matches, normalize_text
    normalized = normalize_text(text or "")
    if exclude and any(keyword_matches(normalized, kw) for kw in exclude):
        return False
    if not keywords:
        return True
    return any(keyword_matches(normalized, kw) for kw in keywords)


def org_rule(org_id: Any, db=None) -> Optional[Dict[str, Any]]:
    """A comment-filter rule built from the organization's keywords, or None
    when the organization uses the global defaults."""
    settings = _org_settings(org_id, db)
    include = _normalize(settings.get("lead_keywords"))
    if not include:
        return None
    return {
        "_id": f"org:{org_id}",
        "name": "Organization lead rules",
        "description": "Lead keywords configured by the organization Admin",
        "platform": "all",
        "business_category": "",
        "categories": [],
        "include_keywords": include,
        "exclude_keywords": _normalize(settings.get("lead_exclude_keywords")),
        "match_mode": "any",
        "group_operator": "and",
        "groups": [],
        "language": "all",
        "detect_contacts": True,
        "intent_type": "",
        "organization_id": str(org_id),
    }
