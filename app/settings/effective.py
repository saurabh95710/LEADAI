"""Effective values of Global Settings that change server behaviour.

Small, safe readers used by the code that acts on a setting (exports, lead
creation, notification emails, new organizations). Each one reads the value
fresh (``app.admin.settings`` caches for a few seconds) and falls back to the
built-in behaviour when the value is missing or invalid, so a bad stored value
can never break an export or a scrape.
"""
import logging
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Exports ────────────────────────────────────────────────────────────────

EXPORT_MAX_RECORDS_FALLBACK = 5000


def export_max_records() -> int:
    """``exports.max_records``: the most rows any one export may contain."""
    try:
        from app.admin.settings import get_setting
        value = int(get_setting("exports.max_records") or 0)
    except Exception:
        value = 0
    return value if value > 0 else EXPORT_MAX_RECORDS_FALLBACK


def export_leads_only_default() -> bool:
    """``exports.include_leads_only``: whether an export that can hold every
    analysed comment returns only leads when the request doesn't say."""
    try:
        from app.admin.settings import get_bool
        return get_bool("exports.include_leads_only")
    except Exception:
        return True


def export_include_ai_summary() -> bool:
    """``exports.include_ai_summary``: whether lead exports carry the AI's
    reason / summary column."""
    try:
        from app.admin.settings import get_bool
        return get_bool("exports.include_ai_summary")
    except Exception:
        return True


# ── Leads ──────────────────────────────────────────────────────────────────

# Global Settings labels -> lead_status values (app/pipeline/lead_lifecycle.py)
_STATUS_ALIASES: Dict[str, str] = {
    "new": "new", "contacted": "contacted", "qualified": "qualified",
    "follow_up": "follow_up", "follow-up": "follow_up", "converted": "converted",
    "lost": "lost", "disqualified": "disqualified", "rejected": "disqualified",
    "archived": "archived",
}


def default_lead_status() -> str:
    """``leads.default_status`` as a lead_status value; "new" when unset or
    not a known status."""
    try:
        from app.admin.settings import get_setting
        raw = str(get_setting("leads.default_status") or "").strip().lower()
    except Exception:
        raw = ""
    return _STATUS_ALIASES.get(raw, "new")


def require_contact_for_leads() -> bool:
    """``leads.require_phone_or_email``: a comment becomes a lead only when it
    carries a phone, WhatsApp number or email."""
    try:
        from app.admin.settings import get_bool_cached
        return get_bool_cached("leads.require_phone_or_email")
    except Exception:
        return False


# ── Notification emails (platform-wide master switches) ─────────────────────

GLOBAL_EMAIL_SWITCH: Dict[str, str] = {
    "job_failed": "notifications.job_failed_email",
    "search_failed": "notifications.job_failed_email",
    "leads_found": "notifications.new_lead_email",
    "new_leads": "notifications.new_lead_email",
}


def notification_email_enabled(ntype: str) -> bool:
    """False when the platform switched this kind of notification email off
    for every organization. Types without a switch are always allowed."""
    key = GLOBAL_EMAIL_SWITCH.get(ntype)
    if not key:
        return True
    try:
        from app.admin.settings import get_bool
        return get_bool(key)
    except Exception:
        return True


# ── New organizations ──────────────────────────────────────────────────────

def org_locale_defaults(timezone: Optional[str] = None,
                        currency: Optional[str] = None) -> Tuple[str, str]:
    """(timezone, currency) for a new organization: what it chose, otherwise
    ``localization.timezone`` / ``localization.currency``. "auto" (= the
    browser's zone) is not a zone an organization can store, so it is UTC."""
    tz, cur = (timezone or "").strip(), (currency or "").strip().upper()
    if not tz or not cur:
        try:
            from app.admin.settings import get_setting
            if not tz:
                tz = str(get_setting("localization.timezone") or "").strip()
            if not cur:
                cur = str(get_setting("localization.currency") or "").strip().upper()
        except Exception as e:
            logger.debug("localization defaults unavailable: %s", e)
    if not tz or tz.lower() == "auto":
        tz = "UTC"
    if len(cur) != 3 or not cur.isalpha():
        cur = "USD"
    return tz[:64], cur
