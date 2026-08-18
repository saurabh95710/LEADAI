"""Global Settings — setting schema registry.

Every admin-manageable setting (the existing ``SETTING_DEFAULTS`` keys plus
the new white-label / branding / localization keys) is described here with the
schema the Settings API and the admin "Global Settings" UI use for
validation, rendering, public exposure and import/export.

The registry never stores values — it only describes them. Effective values
live in the ``system_settings`` collection (see :mod:`app.admin.settings`),
so a change made here takes effect at runtime with no restart.
"""
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.admin.settings import SETTING_DEFAULTS

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_URL_RE = re.compile(r"^(https?://|/static/)[^\s]+$")
_JS_DANGER_RE = re.compile(r"<\s*script|on[a-z]+\s*=|javascript\s*:", re.IGNORECASE)

# Admin views that may be re-labelled / re-ordered / hidden via nav overrides.
KNOWN_VIEWS = {
    "dashboard", "jobs", "failed", "leads", "analytics", "platforms", "apify",
    "actors", "usage", "environment", "ai", "scoring", "ci", "keyword-rules",
    "limits", "database", "logs", "health", "exports", "users", "security",
    "features", "maintenance", "audit", "pages", "posts", "settings",
}

# Dashboard widgets that can be hidden / re-ordered from the dashboard.
KNOWN_WIDGETS = {
    "hero", "kpis", "activity", "leads_by_platform", "alerts",
    "system_status", "keyword_filter", "recent_jobs",
}

FONT_OPTIONS = ["inter", "roboto", "manrope", "plus-jakarta-sans", "system"]
THEME_OPTIONS = ["light", "dark", "system"]
LEAD_STATUS_OPTIONS = ["New", "Contacted", "Qualified", "Converted", "Rejected", "Archived"]
TEMPERATURE_OPTIONS = ["Hot", "Warm", "Cold"]
EXPORT_FORMAT_OPTIONS = ["CSV", "XLSX", "JSON"]
FILTER_MODE_OPTIONS = ["all", "preset", "custom"]
DATE_FORMAT_OPTIONS = ["YYYY-MM-DD", "DD-MM-YYYY", "MM-DD-YYYY"]
TIME_FORMAT_OPTIONS = ["24h", "12h"]
CURRENCY_OPTIONS = ["USD", "EUR", "GBP", "INR", "AUD", "CAD", "JPY", "AED", "SGD"]
LANGUAGE_OPTIONS = ["en", "hi", "es", "fr", "de", "ar", "pt", "id"]


@dataclass
class SettingSpec:
    """Schema of a single setting key."""
    key: str
    category: str
    group: str
    type: str = "str"
    label: str = ""
    description: str = ""
    options: Optional[List[str]] = None
    min: Optional[float] = None
    max: Optional[float] = None
    is_public: bool = False
    sensitive: bool = False
    placeholder: str = ""
    default: Any = field(init=False)
    json_shape: Optional[Dict[str, str]] = None  # allowed keys -> value type

    def __post_init__(self):
        if not self.label:
            self.label = self.key.split(".")[-1].replace("_", " ").title()
        self.default = SETTING_DEFAULTS.get(self.key)


def _s(key: str, category: str, group: str, type: str = "str",
       label: str = "", description: str = "", options: Optional[List[str]] = None,
       min: Optional[float] = None, max: Optional[float] = None,
       is_public: bool = False, sensitive: bool = False,
       placeholder: str = "", json_shape: Optional[Dict[str, str]] = None) -> SettingSpec:
    return SettingSpec(key=key, category=category, group=group, type=type,
                       label=label, description=description, options=options,
                       min=min, max=max, is_public=is_public,
                       sensitive=sensitive, placeholder=placeholder,
                       json_shape=json_shape)


# ── The full registry ───────────────────────────────────────────────────────
# Categories mirror the tabs of the admin "Global Settings" page. Existing
# runtime keys (limits.*, ai.*, scoring.*, ci.*, features.*, maintenance.*,
# security.*, platform.*.enabled) are folded in so the panel is the single
# place where every non-secret setting can be edited, reset and versioned.
# Secrets (apify.token) and internal keys (actor.*, security.session_epoch,
# apify.last_test_*) are deliberately NOT registered here.

SETTINGS: List[SettingSpec] = [
    # ── General ──────────────────────────────────────────────────────────
    _s("general.app.name", "general", "Application", "str",
       label="Application name", is_public=True,
       description="Shown in the browser tab, sidebar and login page.",
       placeholder="LeadAI"),
    _s("general.app.short_name", "general", "Application", "str",
       label="Short name", is_public=True,
       description="Compact name for tight spaces (mobile, collapsed sidebar).",
       placeholder="LeadAI"),
    _s("general.app.tagline", "general", "Application", "str",
       label="Tagline", is_public=True,
       description="The one-line description under the brand mark.",
       placeholder="AI Lead Intelligence"),
    _s("general.app.description", "general", "Application", "textarea",
       label="Description", is_public=True,
       description="Long-form description (meta description, about text).",
       placeholder="AI-powered social lead intelligence platform…"),
    _s("general.company.name", "general", "Company", "str",
       label="Company name", is_public=True,
       description="The organization this deployment belongs to."),
    _s("general.company.website", "general", "Company", "url",
       label="Company website", is_public=True),
    _s("general.contact.support_email", "general", "Contact", "email",
       label="Support email", is_public=True),
    _s("general.contact.support_phone", "general", "Contact", "str",
       label="Support phone", is_public=True),
    _s("general.contact.support_url", "general", "Contact", "url",
       label="Support URL", is_public=True),
    _s("general.links.privacy_url", "general", "Legal links", "url",
       label="Privacy policy URL", is_public=True),
    _s("general.links.terms_url", "general", "Legal links", "url",
       label="Terms of service URL", is_public=True),
    _s("general.links.docs_url", "general", "Legal links", "url",
       label="Documentation URL", is_public=True),
    _s("general.links.help_url", "general", "Legal links", "url",
       label="Help / FAQ URL", is_public=True),

    # ── Branding ─────────────────────────────────────────────────────────
    _s("branding.logo_primary", "branding", "Logos", "path",
       label="Primary logo", is_public=True,
       description="Upload a logo or paste a /static/ path. Shown in the header, "
                   "sidebar and login card. Leave empty to keep the built-in mark."),
    _s("branding.logo_dark", "branding", "Logos", "path",
       label="Logo (dark background)", is_public=True,
       description="Used on dark surfaces (login page, dark theme)."),
    _s("branding.logo_light", "branding", "Logos", "path",
       label="Logo (light background)", is_public=True),
    _s("branding.logo_compact", "branding", "Logos", "path",
       label="Compact logo", is_public=True,
       description="Small icon used when the sidebar is collapsed."),
    _s("branding.logo_login", "branding", "Logos", "path",
       label="Login logo", is_public=True,
       description="Overrides the login-card logo when set."),
    _s("branding.logo_email", "branding", "Logos", "path",
       label="Email logo", is_public=True,
       description="Used in email branding (forward-looking)."),
    _s("branding.favicon", "branding", "Logos", "path",
       label="Favicon", is_public=True,
       description="Browser tab icon. PNG, SVG or ICO."),
    _s("branding.apple_touch_icon", "branding", "Logos", "path",
       label="Apple touch icon", is_public=True),
    _s("branding.colors.primary", "branding", "Colors", "color",
       label="Primary color", is_public=True,
       description="Replaces the violet accent everywhere (brand, AI highlights).",
       placeholder="#7c5cff"),
    _s("branding.colors.accent", "branding", "Colors", "color",
       label="Accent color", is_public=True,
       description="Replaces the amber accent (value, lead highlights).",
       placeholder="#f0a531"),
    _s("branding.colors.success", "branding", "Colors", "color",
       label="Success color", is_public=True, placeholder="#1fae6a"),
    _s("branding.colors.warning", "branding", "Colors", "color",
       label="Warning color", is_public=True, placeholder="#f0a531"),
    _s("branding.colors.danger", "branding", "Colors", "color",
       label="Danger color", is_public=True, placeholder="#e5484d"),
    _s("branding.colors.info", "branding", "Colors", "color",
       label="Info color", is_public=True, placeholder="#3b82f6"),
    _s("branding.theme", "branding", "Theme", "select",
       label="Default theme", is_public=True, options=THEME_OPTIONS,
       description="Preferred theme for signed-out pages and default for the apps."),
    _s("branding.font", "branding", "Theme", "select",
       label="UI font", is_public=True, options=FONT_OPTIONS,
       description="Applied to both the user app and the admin panel."),
    _s("branding.white_label", "branding", "Theme", "bool",
       label="White-label mode", is_public=True,
       description="Suppresses 'Powered by' / product mentions so the platform "
                   "can be sold as your own."),
    _s("branding.login_heading", "branding", "Login card", "str",
       label="Login heading", is_public=True, placeholder="Welcome Back"),
    _s("branding.login_subtext", "branding", "Login card", "textarea",
       label="Login subtext", is_public=True),
    _s("branding.footer_text", "branding", "Login card", "str",
       label="Footer text", is_public=True,
       description="Shown at the bottom of the login card and user-app footer.",
       placeholder="Protected by secure authentication · LeadAI © 2026"),

    # ── Appearance (admin panel) ─────────────────────────────────────────
    _s("appearance.sidebar_title", "appearance", "Sidebar", "str",
       label="Sidebar title", is_public=True, placeholder="LeadAI"),
    _s("appearance.sidebar_subtitle", "appearance", "Sidebar", "str",
       label="Sidebar subtitle", is_public=True, placeholder="Admin Control Center"),
    _s("appearance.sidebar_collapsed_default", "appearance", "Sidebar", "bool",
       label="Collapse sidebar by default", is_public=True,
       description="New visitors see the sidebar collapsed until they toggle it."),
    _s("appearance.show_icons", "appearance", "Sidebar", "bool",
       label="Show nav icons", is_public=True),
    _s("appearance.show_section_labels", "appearance", "Sidebar", "bool",
       label="Show nav section labels", is_public=True),
    _s("appearance.show_footer_links", "appearance", "Sidebar", "bool",
       label="Show footer links (app / docs)", is_public=True),
    _s("appearance.nav_overrides", "appearance", "Navigation", "json",
       label="Nav overrides", sensitive=True,
       json_shape={"label": "str", "order": "int", "hidden": "bool"},
       description="JSON: {\"view\": {\"label\": \"…\", \"order\": 2, \"hidden\": true}}. "
                   "Re-labels, re-orders or hides admin nav items. Example: "
                   "{\"usage\": {\"label\": \"Billing\", \"order\": 1}}"),
    _s("appearance.dashboard_widgets", "appearance", "Dashboard", "json",
       label="Dashboard widgets", sensitive=True,
       json_shape={"show": "bool", "order": "int"},
       description="JSON: {\"widget\": {\"show\": true, \"order\": 1}}. Widgets: "
                   "hero, kpis, activity, leads_by_platform, alerts, system_status, "
                   "keyword_filter, recent_jobs."),
    _s("defaults.comment_filter_mode", "appearance", "User app defaults", "select",
       label="Comment filter mode", is_public=True, options=FILTER_MODE_OPTIONS,
       description="Default mode of the comment-filter panel on the search form."),
    _s("defaults.keyword_preset", "appearance", "User app defaults", "str",
       label="Keyword preset", is_public=True,
       description="Preset id preselected when the filter mode is 'preset'."),
    _s("defaults.theme", "appearance", "User app defaults", "select",
       label="User app theme", is_public=True, options=THEME_OPTIONS),
    _s("defaults.date_range", "appearance", "User app defaults", "int",
       label="Default date range (days)", is_public=True, min=1, max=365),
    _s("defaults.page_size", "appearance", "User app defaults", "int",
       label="Default page size", is_public=True, min=5, max=200),

    # ── AI & Intelligence ────────────────────────────────────────────────
    _s("ai.enabled", "ai", "Gemini engine", "bool",
       label="AI analysis enabled",
       description="When off, comments are stored but never analyzed by the LLM."),
    _s("ai.rule_fallback", "ai", "Gemini engine", "bool",
       label="Rule fallback when AI fails",
       description="Fall back to keyword rules when the Gemini call errors."),
    _s("ai.max_calls_per_job", "ai", "Gemini engine", "int",
       label="Max AI calls per job", min=1, max=10000),
    _s("ai.model", "ai", "Gemini engine", "str",
       label="Gemini model", placeholder="gemini-2.5-flash"),
    _s("ai.temperature", "ai", "Gemini engine", "float",
       label="Temperature", min=0, max=2),
    _s("scoring.confidence_weight", "ai", "Lead scoring", "int",
       label="Confidence weight", min=0, max=100),
    _s("scoring.priority_weight", "ai", "Lead scoring", "int",
       label="Priority weight", min=0, max=100),
    _s("scoring.quality_weight", "ai", "Lead scoring", "int",
       label="Quality weight", min=0, max=100),
    _s("scoring.contact_phone", "ai", "Lead scoring", "int",
       label="Contact signal: phone", min=0, max=100),
    _s("scoring.contact_email", "ai", "Lead scoring", "int",
       label="Contact signal: email", min=0, max=100),
    _s("scoring.spam_penalty", "ai", "Lead scoring", "int",
       label="Spam penalty", min=0, max=100),
    _s("scoring.phone", "ai", "Lead scoring", "int",
       label="Signal weight: phone", min=0, max=100),
    _s("scoring.email", "ai", "Lead scoring", "int",
       label="Signal weight: email", min=0, max=100),
    _s("scoring.budget", "ai", "Lead scoring", "int",
       label="Signal weight: budget", min=0, max=100),
    _s("scoring.urgency", "ai", "Lead scoring", "int",
       label="Signal weight: urgency", min=0, max=100),
    _s("scoring.location", "ai", "Lead scoring", "int",
       label="Signal weight: location", min=0, max=100),
    _s("scoring.buying_intent", "ai", "Lead scoring", "int",
       label="Signal weight: buying intent", min=0, max=100),
    _s("scoring.hot_min", "ai", "Lead scoring", "int",
       label="Hot threshold (min score)", min=0, max=100),
    _s("scoring.warm_min", "ai", "Lead scoring", "int",
       label="Warm threshold (min score)", min=0, max=100),
    _s("scoring.derive_quality", "ai", "Lead scoring", "bool",
       label="Derive quality from score"),
    _s("ci.detect_phone", "ai", "Comment intelligence", "bool",
       label="Detect phone numbers"),
    _s("ci.detect_email", "ai", "Comment intelligence", "bool",
       label="Detect email addresses"),
    _s("ci.detect_budget", "ai", "Comment intelligence", "bool",
       label="Detect budget mentions"),
    _s("ci.detect_location", "ai", "Comment intelligence", "bool",
       label="Detect locations"),
    _s("ci.detect_urgency", "ai", "Comment intelligence", "bool",
       label="Detect urgency"),
    _s("ci.detect_buying_intent", "ai", "Comment intelligence", "bool",
       label="Detect buying intent"),
    _s("ci.detect_selling_intent", "ai", "Comment intelligence", "bool",
       label="Detect selling intent"),
    _s("ci.ignore_emoji_only", "ai", "Comment intelligence", "bool",
       label="Ignore emoji-only comments"),
    _s("ci.ignore_spam", "ai", "Comment intelligence", "bool",
       label="Ignore spam comments"),
    _s("ci.ignore_low_value", "ai", "Comment intelligence", "bool",
       label="Ignore low-value comments"),
    _s("ci.min_lead_score", "ai", "Comment intelligence", "int",
       label="Minimum lead score", min=0, max=100),

    # ── Scraping & Limits ────────────────────────────────────────────────
    _s("limits.min_comments", "limits", "Limits", "int",
       label="Minimum comments", min=1, max=100000,
       description="Below this, a scrape is treated as low-value."),
    _s("limits.max_posts_default", "limits", "Limits", "int",
       label="Max posts (default)", min=1, max=100000),
    _s("limits.max_posts_cap", "limits", "Limits", "int",
       label="Max posts (hard cap)", min=1, max=100000),
    _s("limits.max_comments_per_post_default", "limits", "Limits", "int",
       label="Max comments per post (default)", min=1, max=100000),
    _s("limits.max_comments_per_post_cap", "limits", "Limits", "int",
       label="Max comments per post (hard cap)", min=1, max=100000),
    _s("limits.global_max_comments", "limits", "Limits", "int",
       label="Global max comments", min=1, max=1000000),
    _s("cost.stop_on_limit", "limits", "Cost protection", "bool",
       label="Stop on limit",
       description="Abort a scrape when the global comment limit is reached."),
    _s("cost.warn_before_expensive", "limits", "Cost protection", "bool",
       label="Warn before expensive scrapes"),

    # ── Platforms ────────────────────────────────────────────────────────
    _s("platform.facebook.enabled", "platforms", "Platforms", "bool",
       label="Facebook enabled"),
    _s("platform.instagram.enabled", "platforms", "Platforms", "bool",
       label="Instagram enabled"),
    _s("platform.youtube.enabled", "platforms", "Platforms", "bool",
       label="YouTube enabled"),
    _s("platform.linkedin.enabled", "platforms", "Platforms", "bool",
       label="LinkedIn enabled"),

    # ── Features ─────────────────────────────────────────────────────────
    _s("features.url_search.enabled", "features", "Features", "bool",
       label="URL search enabled", is_public=True),
    _s("features.exports.enabled", "features", "Features", "bool",
       label="Exports enabled", is_public=True),

    # ── Notifications & Email ────────────────────────────────────────────
    # Forward-looking: no mail backend exists yet — registered so the panel
    # is the single source of truth when one ships.
    _s("notifications.alerts_enabled", "notifications", "Notifications", "bool",
       label="In-app alerts enabled",
       description="Master switch for the admin alert bell."),
    _s("notifications.job_failed_email", "notifications", "Email", "bool",
       label="Email on job failure",
       description="Requires the email sender below (not wired yet — see report)."),
    _s("notifications.new_lead_email", "notifications", "Email", "bool",
       label="Email on new hot lead",
       description="Requires the email sender below (not wired yet — see report)."),
    _s("email.sender_name", "notifications", "Email", "str",
       label="Sender name"),
    _s("email.reply_to", "notifications", "Email", "email",
       label="Reply-to address"),
    _s("email.company_name", "notifications", "Email", "str",
       label="Company name in emails"),
    _s("email.logo", "notifications", "Email", "path",
       label="Email logo (path)"),
    _s("email.footer_text", "notifications", "Email", "str",
       label="Email footer text"),

    # ── Leads & Exports ──────────────────────────────────────────────────
    _s("leads.duplicate_detection", "leads", "Leads", "bool",
       label="Duplicate detection",
       description="Merge/dedupe leads that share a phone or email."),
    _s("leads.retention_days", "leads", "Leads", "int",
       label="Lead retention (days)", min=1, max=3650),
    _s("leads.default_status", "leads", "Leads", "select",
       label="Default lead status", options=LEAD_STATUS_OPTIONS),
    _s("leads.default_temperature", "leads", "Leads", "select",
       label="Default temperature", options=TEMPERATURE_OPTIONS),
    _s("leads.require_phone_or_email", "leads", "Leads", "bool",
       label="Require phone or email",
       description="Only store leads that carry a reachable contact."),
    _s("exports.default_format", "leads", "Exports", "select",
       label="Default export format", options=EXPORT_FORMAT_OPTIONS),
    _s("exports.max_records", "leads", "Exports", "int",
       label="Max records per export", min=1, max=1000000),
    _s("exports.include_leads_only", "leads", "Exports", "bool",
       label="Export leads only by default"),
    _s("exports.include_ai_summary", "leads", "Exports", "bool",
       label="Include AI summary column"),

    # ── SEO & Localization ───────────────────────────────────────────────
    _s("seo.meta_description", "seo", "SEO", "textarea",
       label="Meta description", is_public=True),
    _s("seo.og_title", "seo", "SEO", "str",
       label="Social share title", is_public=True),
    _s("seo.og_image", "seo", "SEO", "path",
       label="Social share image", is_public=True),
    _s("localization.timezone", "seo", "Localization", "select",
       label="Timezone", is_public=True,
       options=["auto", "UTC", "America/New_York", "America/Chicago",
                "America/Los_Angeles", "Europe/London", "Europe/Paris",
                "Europe/Berlin", "Asia/Dubai", "Asia/Kolkata",
                "Asia/Singapore", "Asia/Tokyo", "Asia/Shanghai",
                "Australia/Sydney", "Africa/Lagos", "Africa/Johannesburg",
                "America/Sao_Paulo", "America/Mexico_City"],
       description="Display timezone for timestamps ('auto' = browser local)."),
    _s("localization.date_format", "seo", "Localization", "select",
       label="Date format", is_public=True, options=DATE_FORMAT_OPTIONS),
    _s("localization.time_format", "seo", "Localization", "select",
       label="Time format", is_public=True, options=TIME_FORMAT_OPTIONS),
    _s("localization.currency", "seo", "Localization", "select",
       label="Currency", is_public=True, options=CURRENCY_OPTIONS),
    _s("localization.language", "seo", "Localization", "select",
       label="Language", is_public=True, options=LANGUAGE_OPTIONS),

    # ── Maintenance ──────────────────────────────────────────────────────
    _s("maintenance.enabled", "maintenance", "Maintenance mode", "bool",
       label="Maintenance mode",
       description="Blocks the user app with a maintenance page; the admin "
                   "panel and signed-in admins stay reachable."),
    _s("maintenance.message", "maintenance", "Maintenance mode", "textarea",
       label="Maintenance message", is_public=True),

    # ── Security ─────────────────────────────────────────────────────────
    _s("security.session_timeout_hours", "security", "Sessions", "int",
       label="Session timeout (hours)", min=1, max=8760,
       description="Hard idle timeout on top of the cookie lifetime."),
    _s("security.login_protection", "security", "Sessions", "bool",
       label="Login protection (rate limiting)"),
    _s("security.audit_logging", "security", "Sessions", "bool",
       label="Audit logging",
       description="When off, admin actions are not written to the audit log."),
]

SPEC_BY_KEY: Dict[str, SettingSpec] = {spec.key: spec for spec in SETTINGS}

# Registry excludes secrets (apify.token) and internal keys (actor.*,
# security.session_epoch, apify.last_test_*) — they stay in their own views.
REGISTERED_KEYS: set = set(SPEC_BY_KEY)


# ── Categories (tab order for the admin UI) ─────────────────────────────────

CATEGORIES: List[Dict[str, Any]] = [
    {"id": "general", "label": "General", "icon": "🏷"},
    {"id": "branding", "label": "Branding", "icon": "🎨"},
    {"id": "appearance", "label": "Appearance", "icon": "🧭"},
    {"id": "ai", "label": "AI & Intelligence", "icon": "🧠"},
    {"id": "limits", "label": "Scraping & Limits", "icon": "⏱"},
    {"id": "platforms", "label": "Platforms", "icon": "🌐"},
    {"id": "features", "label": "Features", "icon": "🚩"},
    {"id": "notifications", "label": "Notifications & Email", "icon": "📨"},
    {"id": "leads", "label": "Leads & Exports", "icon": "💼"},
    {"id": "seo", "label": "SEO & Localization", "icon": "🌍"},
    {"id": "maintenance", "label": "Maintenance", "icon": "🛠"},
    {"id": "security", "label": "Security", "icon": "🔐"},
]


def category_specs(category: str) -> List[SettingSpec]:
    return [spec for spec in SETTINGS if spec.category == category]


def group_specs(category: str, group: str) -> List[SettingSpec]:
    return [spec for spec in SETTINGS
            if spec.category == category and spec.group == group]


def groups_of(category: str) -> List[str]:
    seen: List[str] = []
    for spec in SETTINGS:
        if spec.category == category and spec.group not in seen:
            seen.append(spec.group)
    return seen


def schema_categories() -> List[Dict[str, Any]]:
    """Categories with their groups and specs — what the admin UI renders."""
    out = []
    for cat in CATEGORIES:
        groups = []
        for group in groups_of(cat["id"]):
            groups.append({
                "id": group,
                "label": group,
                "specs": [
                    {"key": spec.key, "type": spec.type, "label": spec.label,
                     "description": spec.description, "options": spec.options,
                     "min": spec.min, "max": spec.max, "default": spec.default,
                     "placeholder": spec.placeholder, "sensitive": spec.sensitive}
                    for spec in group_specs(cat["id"], group)
                ],
            })
        out.append({**cat, "groups": groups})
    return out


# ── Validation & coercion ───────────────────────────────────────────────────

def _validate_map(spec: SettingSpec, value: Any) -> Tuple[Any, Optional[str]]:
    shape = spec.json_shape or {}
    if not isinstance(value, dict):
        return value, "Expected a JSON object"
    clean: Dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(item, dict):
            return value, f"Entry '{key}' must be an object"
        allowed = set(shape)
        unknown = set(item) - allowed
        if unknown:
            return value, f"Entry '{key}' has unknown keys: {', '.join(sorted(unknown))}"
        for sub_key, sub_value in item.items():
            if shape[sub_key] == "bool" and not isinstance(sub_value, bool):
                return value, f"'{key}.{sub_key}' must be true or false"
            if shape[sub_key] == "int" and not isinstance(sub_value, int):
                return value, f"'{key}.{sub_key}' must be a number"
            if shape[sub_key] == "str" and not isinstance(sub_value, str):
                return value, f"'{key}.{sub_key}' must be text"
        clean[key] = dict(item)
    return clean, None


def validate_value(spec: SettingSpec, value: Any) -> Tuple[Any, Optional[str]]:
    """Coerce + validate one value. Returns (coerced, error)."""
    t = spec.type
    if t == "bool":
        if isinstance(value, bool):
            return value, None
        if isinstance(value, (int, float)):
            return bool(value), None
        if isinstance(value, str):
            low = value.strip().lower()
            if low in ("1", "true", "yes", "on"):
                return True, None
            if low in ("0", "false", "no", "off", ""):
                return False, None
            return value, "Expected true or false"
        return value, "Expected true or false"
    if t == "int":
        try:
            n = int(value)
        except (TypeError, ValueError):
            return value, "Expected a whole number"
        if spec.min is not None and n < spec.min:
            return value, f"Minimum is {int(spec.min)}"
        if spec.max is not None and n > spec.max:
            return value, f"Maximum is {int(spec.max)}"
        return n, None
    if t == "float":
        try:
            n = float(value)
        except (TypeError, ValueError):
            return value, "Expected a number"
        if spec.min is not None and n < spec.min:
            return value, f"Minimum is {spec.min}"
        if spec.max is not None and n > spec.max:
            return value, f"Maximum is {spec.max}"
        return n, None
    if t == "select":
        text = str(value)
        if spec.options and text not in spec.options:
            return value, f"Must be one of: {', '.join(spec.options)}"
        return text, None
    if t == "color":
        text = str(value).strip()
        if text and not _HEX_RE.match(text):
            return value, "Must be a hex color like #7c5cff"
        return text, None
    if t == "url":
        text = str(value).strip()
        if text and not _URL_RE.match(text):
            return value, "Must be an https:// URL or a /static/ path"
        return text, None
    if t == "path":
        text = str(value).strip()
        if text and not _URL_RE.match(text):
            return value, "Must be an https:// URL or a /static/ path"
        return text, None
    if t == "email":
        text = str(value).strip()
        if text and not _EMAIL_RE.match(text):
            return value, "Must be a valid email address"
        return text, None
    if t == "json":
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return value, "Invalid JSON"
        return _validate_map(spec, value)
    if t == "textarea":
        text = str(value)
        if len(text) > 4000:
            return value, "Too long (max 4000 characters)"
        return text, None
    # str
    text = str(value)
    if len(text) > 500:
        return value, "Too long (max 500 characters)"
    return text, None


def validate_patch(values: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """Validate a batch of {key: value}. Returns (clean, errors)."""
    clean: Dict[str, Any] = {}
    errors: Dict[str, str] = {}
    for key, value in values.items():
        spec = SPEC_BY_KEY.get(key)
        if spec is None:
            errors[key] = "Unknown setting key"
            continue
        coerced, error = validate_value(spec, value)
        if error:
            errors[key] = error
        else:
            clean[key] = coerced
    return clean, errors


def is_registered(key: str) -> bool:
    return key in SPEC_BY_KEY


# ── Public config (safe values only — served without a session) ────────────

async def build_public_config(get: Callable[[str], Any]) -> Dict[str, Any]:
    """Assemble the /api/public/config payload. Never includes secrets,
    tokens, credentials or internal infrastructure details.

    ``get`` may be sync or async (e.g. ``settings.aget_setting``); values are
    read once per registered key, then the payload is assembled synchronously.
    """
    import inspect
    values: Dict[str, Any] = {}
    for key in REGISTERED_KEYS:
        value = get(key)
        if inspect.isawaitable(value):
            value = await value
        values[key] = value

    def g(key: str, fallback: Any = None) -> Any:
        value = values.get(key)
        if value is None:
            return SETTING_DEFAULTS.get(key) if fallback is None else fallback
        return value

    def s(key: str, fallback: str = "") -> str:
        value = g(key)
        return str(value) if value is not None and str(value) != "" else fallback

    return {
        "app": {
            "name": s("general.app.name", "LeadAI"),
            "short_name": s("general.app.short_name", "LeadAI"),
            "tagline": s("general.app.tagline", "AI Lead Intelligence"),
            "description": s("general.app.description",
                             "AI-powered social lead intelligence platform."),
        },
        "company": {
            "name": s("general.company.name"),
            "website": s("general.company.website"),
        },
        "contact": {
            "support_email": s("general.contact.support_email"),
            "support_phone": s("general.contact.support_phone"),
            "support_url": s("general.contact.support_url"),
        },
        "links": {
            "privacy_url": s("general.links.privacy_url"),
            "terms_url": s("general.links.terms_url"),
            "docs_url": s("general.links.docs_url"),
            "help_url": s("general.links.help_url"),
        },
        "branding": {
            "logo_primary": s("branding.logo_primary"),
            "logo_dark": s("branding.logo_dark"),
            "logo_light": s("branding.logo_light"),
            "logo_compact": s("branding.logo_compact"),
            "logo_login": s("branding.logo_login"),
            "logo_email": s("branding.logo_email"),
            "favicon": s("branding.favicon"),
            "apple_touch_icon": s("branding.apple_touch_icon"),
            "colors": {
                "primary": s("branding.colors.primary", "#7c5cff"),
                "accent": s("branding.colors.accent", "#f0a531"),
                "success": s("branding.colors.success", "#1fae6a"),
                "warning": s("branding.colors.warning", "#f0a531"),
                "danger": s("branding.colors.danger", "#e5484d"),
                "info": s("branding.colors.info", "#3b82f6"),
            },
            "theme": s("branding.theme", "light"),
            "font": s("branding.font", "inter"),
            "white_label": bool(g("branding.white_label", False)),
            "login_heading": s("branding.login_heading", "Welcome Back"),
            "login_subtext": s("branding.login_subtext",
                               "Sign in to continue to your AI-powered lead "
                               "intelligence dashboard."),
            "footer_text": s("branding.footer_text",
                             "Protected by secure authentication"),
        },
        "appearance": {
            "sidebar_title": s("appearance.sidebar_title"),
            "sidebar_subtitle": s("appearance.sidebar_subtitle"),
            "sidebar_collapsed_default": bool(
                g("appearance.sidebar_collapsed_default", False)),
            "show_icons": bool(g("appearance.show_icons", True)),
            "show_section_labels": bool(g("appearance.show_section_labels", True)),
            "show_footer_links": bool(g("appearance.show_footer_links", True)),
            "nav_overrides": g("appearance.nav_overrides") or {},
            "dashboard_widgets": g("appearance.dashboard_widgets") or {},
        },
        "seo": {
            "meta_description": s("seo.meta_description"),
            "og_title": s("seo.og_title"),
            "og_image": s("seo.og_image"),
        },
        "features": {
            "url_search": bool(g("features.url_search.enabled", True)),
            "exports": bool(g("features.exports.enabled", True)),
        },
        "maintenance": {
            "enabled": bool(g("maintenance.enabled", False)),
            "message": s("maintenance.message"),
        },
        "localization": {
            "timezone": s("localization.timezone", "auto"),
            "date_format": s("localization.date_format", "YYYY-MM-DD"),
            "time_format": s("localization.time_format", "24h"),
            "currency": s("localization.currency", "USD"),
            "language": s("localization.language", "en"),
        },
        "defaults": {
            "comment_filter_mode": s("defaults.comment_filter_mode", "all"),
            "keyword_preset": s("defaults.keyword_preset", ""),
            "theme": s("defaults.theme", "system"),
            "date_range": int(g("defaults.date_range", 30) or 30),
            "page_size": int(g("defaults.page_size", 20) or 20),
        },
    }