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
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.admin.settings import SETTING_DEFAULTS

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_URL_RE = re.compile(r"^(https?://|/static/)[^\s]+$")
_JS_DANGER_RE = re.compile(r"<\s*script|on[a-z]+\s*=|javascript\s*:", re.IGNORECASE)

FONT_OPTIONS = ["inter", "roboto", "manrope", "plus-jakarta-sans", "system"]
THEME_OPTIONS = ["light", "dark", "system"]
BILLING_PROVIDER_OPTIONS = ["auto", "razorpay", "stripe"]
LEAD_STATUS_OPTIONS = ["New", "Contacted", "Qualified", "Follow-up", "Converted", "Lost",
                       "Disqualified", "Archived"]
CURRENCY_OPTIONS = ["USD", "EUR", "GBP", "INR", "AUD", "CAD", "JPY", "AED", "SGD"]


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
# Categories mirror the tabs of Super Admin → Global Settings, in the order
# below. Every registered key must take effect somewhere: a setting that does
# nothing is retired (RETIRED_KEYS), not left on the page. Labels and
# descriptions are what the Super Admin reads, so they say what really
# happens. Secrets (apify.token) and internal keys (actor.*,
# security.session_epoch, apify.last_test_*, lifecycle.preview_notified) are
# deliberately NOT registered here.

SETTINGS: List[SettingSpec] = [
    # ══ Brand & website ══════════════════════════════════════════════════
    # ── General ──────────────────────────────────────────────────────────
    _s("general.app.name", "general", "Name & tagline", "str",
       label="Product name", is_public=True,
       description="Your product's name: browser tabs, website header, sign-in page, "
                   "apps and emails.",
       placeholder="LeadAI"),
    _s("general.app.tagline", "general", "Name & tagline", "str",
       label="Tagline", is_public=True,
       description="The short line under the name on the website, sign-in page and apps.",
       placeholder="AI Lead Intelligence"),
    _s("general.company.name", "general", "Company & support", "str",
       label="Company name", is_public=True,
       description="Your company's name, shown in the sign-in page and app footers."),
    _s("general.contact.support_email", "general", "Company & support", "email",
       label="Support email", is_public=True,
       description="Where customers can reach you; shown in the sign-in page and app "
                   "footers. Empty = hidden."),
    _s("general.contact.support_phone", "general", "Company & support", "str",
       label="Support phone", is_public=True,
       description="Shown next to the support email. Empty = hidden."),
    _s("general.contact.support_url", "general", "Company & support", "url",
       label="Support page", is_public=True,
       description="Link to your help desk or contact page. Empty = hidden."),
    _s("general.links.privacy_url", "general", "Help & legal links", "url",
       label="Privacy policy", is_public=True,
       description="Link in the sign-in page and app footers. Empty = hidden.",
       placeholder="/privacy"),
    _s("general.links.terms_url", "general", "Help & legal links", "url",
       label="Terms of service", is_public=True,
       description="Link in the sign-in page and app footers. Empty = hidden.",
       placeholder="/terms"),
    _s("general.links.docs_url", "general", "Help & legal links", "url",
       label="Documentation", is_public=True,
       description="Link in the sign-in page and app footers. Empty = hidden."),
    _s("general.links.help_url", "general", "Help & legal links", "url",
       label="Help / FAQ", is_public=True,
       description="Link in the sign-in page and app footers. Empty = hidden."),
    _s("localization.timezone", "general", "Regional defaults", "select",
       label="Default timezone", is_public=True,
       options=["auto", "UTC", "America/New_York", "America/Chicago",
                "America/Los_Angeles", "Europe/London", "Europe/Paris",
                "Europe/Berlin", "Asia/Dubai", "Asia/Kolkata",
                "Asia/Singapore", "Asia/Tokyo", "Asia/Shanghai",
                "Australia/Sydney", "Africa/Lagos", "Africa/Johannesburg",
                "America/Sao_Paulo", "America/Mexico_City"],
       description="Timezone given to new organizations, from the website or created "
                   "here ('auto' = UTC). Existing organizations keep theirs."),
    _s("localization.currency", "general", "Regional defaults", "select",
       label="Default currency", is_public=True, options=CURRENCY_OPTIONS,
       description="Currency given to new organizations. Existing organizations keep theirs."),

    # ── Branding ─────────────────────────────────────────────────────────
    _s("branding.logo_primary", "branding", "Logos & icons", "path",
       label="Main logo", is_public=True,
       description="Logo in the apps and on the sign-in page. Empty = the built-in mark."),
    _s("branding.logo_light", "branding", "Logos & icons", "path",
       label="Website logo", is_public=True,
       description="Logo in the public website header. Empty = the main logo."),
    _s("branding.logo_login", "branding", "Logos & icons", "path",
       label="Sign-in logo", is_public=True,
       description="Logo on the sign-in card only. Empty = the main logo."),
    _s("branding.favicon", "branding", "Logos & icons", "path",
       label="Favicon", is_public=True,
       description="The small icon in the browser tab, on every page. PNG, SVG or ICO."),
    _s("branding.apple_touch_icon", "branding", "Logos & icons", "path",
       label="Home-screen icon", is_public=True,
       description="Icon used when someone adds the site to a phone home screen "
                   "(180×180 PNG works best)."),
    _s("branding.colors.primary", "branding", "Colours", "color",
       label="Brand colour", is_public=True,
       description="Buttons, links and highlights on the website, sign-in page and apps.",
       placeholder="#2e9573"),
    _s("branding.colors.accent", "branding", "Colours", "color",
       label="Accent colour", is_public=True,
       description="Second colour for highlights such as lead values and badges.",
       placeholder="#d9a441"),
    _s("branding.colors.success", "branding", "Colours", "color",
       label="Success colour", is_public=True,
       description="Positive states: saved, connected, completed.", placeholder="#1fae6a"),
    _s("branding.colors.warning", "branding", "Colours", "color",
       label="Warning colour", is_public=True,
       description="Needs-attention states: limits close, trial ending.", placeholder="#f0a531"),
    _s("branding.colors.danger", "branding", "Colours", "color",
       label="Danger colour", is_public=True,
       description="Errors and destructive actions: failed, delete.", placeholder="#e5484d"),
    _s("branding.colors.info", "branding", "Colours", "color",
       label="Info colour", is_public=True,
       description="Neutral notices and tips.", placeholder="#3b82f6"),
    _s("branding.theme", "branding", "Look & feel", "select",
       label="Default theme", is_public=True, options=THEME_OPTIONS,
       description="Light or dark for visitors who haven't picked one themselves "
                   "('system' follows their device)."),
    _s("branding.font", "branding", "Look & feel", "select",
       label="Font", is_public=True, options=FONT_OPTIONS,
       description="Typeface of the website, sign-in page and apps."),
    _s("branding.login_heading", "branding", "Sign-in page", "str",
       label="Heading", is_public=True, placeholder="Welcome Back",
       description="The big heading on the customer sign-in card."),
    _s("branding.login_subtext", "branding", "Sign-in page", "textarea",
       label="Text under the heading", is_public=True,
       description="One or two sentences under the heading."),
    _s("branding.footer_text", "branding", "Sign-in page", "str",
       label="Footer line", is_public=True,
       description="Small line at the bottom of the sign-in card.",
       placeholder="Protected by secure authentication"),

    # ── SEO & sharing ────────────────────────────────────────────────────
    _s("seo.meta_description", "seo", "Search & social sharing", "textarea",
       label="Meta description", is_public=True,
       description="Search-engine summary of the app and sign-in pages. The public "
                   "website's pages are set in Website / CMS → SEO."),
    _s("seo.og_title", "seo", "Search & social sharing", "str",
       label="Share title", is_public=True,
       description="Title shown when an app link is shared on social media or chat. "
                   "Empty = the product name."),
    _s("seo.og_image", "seo", "Search & social sharing", "path",
       label="Share image", is_public=True,
       description="Picture shown with shared links (1200×630 PNG or JPG works best)."),

    # ══ Leads engine ═════════════════════════════════════════════════════
    # ── AI engine ────────────────────────────────────────────────────────
    _s("ai.enabled", "ai", "Gemini analysis", "bool",
       label="Analyse comments with AI",
       description="Read every collected comment with Gemini to find leads. Off = only "
                   "the keyword rules run (free, less accurate)."),
    _s("ai.rule_fallback", "ai", "Gemini analysis", "bool",
       label="Use rules when AI fails",
       description="If a Gemini call fails, score that comment with the keyword rules "
                   "instead of leaving it unanalysed."),
    _s("ai.max_calls_per_job", "ai", "Gemini analysis", "int",
       label="AI calls per search", min=1, max=10000,
       description="Most Gemini calls one search may make; the rest of its comments "
                   "are scored by the rules. Caps the AI cost of a single search."),
    _s("ai.model", "ai", "Gemini analysis", "str",
       label="Gemini model", placeholder="gemini-2.5-flash",
       description="Model name. An active prompt in AI Management that names its own "
                   "model takes priority."),
    _s("ai.temperature", "ai", "Gemini analysis", "float",
       label="Creativity (temperature)", min=0, max=2,
       description="Lower = more consistent answers. 0.1 is recommended for lead scoring."),
    _s("ai.gemini.requests_per_minute", "ai", "Gemini analysis", "int",
       label="Requests per minute limit", min=0, max=1000000,
       description="Your Gemini key's requests-per-minute limit, from Google AI Studio → Rate "
                   "limits. Google has no way to ask for it, so LeadAI counts its own requests "
                   "and warns before this runs out. 0 = not watched."),
    _s("ai.gemini.requests_per_day", "ai", "Gemini analysis", "int",
       label="Requests per day limit", min=0, max=100000000,
       description="Your Gemini key's requests-per-day limit (Google AI Studio → Rate limits). "
                   "The day resets at midnight Pacific time. 0 = not watched."),
    _s("ai.gemini.input_per_minute", "ai", "Gemini analysis", "int",
       label="Input tokens per minute limit", min=0, max=1000000000,
       description="Your Gemini key's input-tokens-per-minute limit (Google AI Studio → Rate "
                   "limits). 0 = not watched."),
    _s("business.default_industry", "ai", "Business context", "str",
       label="Default industry",
       description="Industry key used for organizations that haven't chosen one "
                   "(see Industries; unknown keys fall back to 'general').",
       placeholder="general"),

    # ── Lead scoring ─────────────────────────────────────────────────────
    _s("scoring.confidence_weight", "scoring", "Lead score", "int",
       label="AI confidence", min=0, max=100,
       description="Points for how sure the AI is (confidence 0–1 × this)."),
    _s("scoring.priority_weight", "scoring", "Lead score", "int",
       label="High priority", min=0, max=100,
       description="Points when the AI rates the comment high priority (half for medium)."),
    _s("scoring.quality_weight", "scoring", "Lead score", "int",
       label="Hot lead", min=0, max=100,
       description="Points when the AI rates the lead hot (half for warm)."),
    _s("scoring.contact_phone", "scoring", "Lead score", "int",
       label="Phone found", min=0, max=100,
       description="Points when a phone, mobile or WhatsApp number is found."),
    _s("scoring.contact_email", "scoring", "Lead score", "int",
       label="Email found", min=0, max=100,
       description="Points when an email, Telegram or website is found."),
    _s("scoring.spam_penalty", "scoring", "Lead score", "int",
       label="Spam penalty", min=0, max=100,
       description="Points taken off for spam (spam likelihood 0–1 × this)."),
    _s("ci.min_lead_score", "scoring", "Lead score", "int",
       label="Minimum lead score", min=0, max=100,
       description="Comments scoring below this are never leads. An organization's own "
                   "minimum applies when it is higher."),
    _s("scoring.phone", "scoring", "Signal score", "int",
       label="Mentions a phone number", min=0, max=100),
    _s("scoring.email", "scoring", "Signal score", "int",
       label="Mentions an email", min=0, max=100),
    _s("scoring.budget", "scoring", "Signal score", "int",
       label="Mentions a budget", min=0, max=100),
    _s("scoring.urgency", "scoring", "Signal score", "int",
       label="Sounds urgent", min=0, max=100),
    _s("scoring.location", "scoring", "Signal score", "int",
       label="Mentions a location", min=0, max=100),
    _s("scoring.buying_intent", "scoring", "Signal score", "int",
       label="Wants to buy", min=0, max=100),
    _s("scoring.derive_quality", "scoring", "Hot / warm from the signal score", "bool",
       label="Label leads from the signal score",
       description="When the AI gives no hot/warm/cold label, set one from the signal "
                   "score using the two thresholds below."),
    _s("scoring.hot_min", "scoring", "Hot / warm from the signal score", "int",
       label="Hot from", min=0, max=100,
       description="Signal score at or above this = hot."),
    _s("scoring.warm_min", "scoring", "Hot / warm from the signal score", "int",
       label="Warm from", min=0, max=100,
       description="Signal score at or above this (and below hot) = warm."),

    # ── Comment detection ────────────────────────────────────────────────
    _s("ci.detect_phone", "detection", "What to look for", "bool",
       label="Phone numbers", description="Find phone, mobile and WhatsApp numbers."),
    _s("ci.detect_email", "detection", "What to look for", "bool",
       label="Email addresses", description="Find email addresses."),
    _s("ci.detect_budget", "detection", "What to look for", "bool",
       label="Budgets", description="Spot amounts and price ranges (\"under 50 lakh\")."),
    _s("ci.detect_location", "detection", "What to look for", "bool",
       label="Locations", description="Spot places and areas the person mentions."),
    _s("ci.detect_urgency", "detection", "What to look for", "bool",
       label="Urgency", description="Spot words like \"urgent\", \"asap\", \"today\"."),
    _s("ci.detect_buying_intent", "detection", "What to look for", "bool",
       label="Buying intent", description="Spot people who want to buy or ask for prices."),
    _s("ci.detect_selling_intent", "detection", "What to look for", "bool",
       label="Sellers and brokers",
       description="Spot people selling or promoting, so they aren't counted as buyers."),
    _s("ci.ignore_emoji_only", "detection", "What to skip", "bool",
       label="Emoji-only comments",
       description="Mark comments with only emoji as not useful, without an AI call."),
    _s("ci.ignore_spam", "detection", "What to skip", "bool",
       label="Link-only comments",
       description="Mark comments that are just a link as spam, without an AI call."),
    _s("ci.ignore_low_value", "detection", "What to skip", "bool",
       label="Very short or thank-you comments",
       description="Skip comments under three words or only thanks (\"nice\", \"thank you\"), "
                   "without an AI call."),

    # ── Search limits ────────────────────────────────────────────────────
    _s("limits.max_posts_default", "limits", "Posts per search", "int",
       label="Default", min=1, max=100000,
       description="Posts collected when the user doesn't choose a number."),
    _s("limits.max_posts_cap", "limits", "Posts per search", "int",
       label="Maximum", min=1, max=100000,
       description="Most posts any one search may collect, whatever the plan or request."),
    _s("limits.max_comments_per_post_default", "limits", "Comments per post", "int",
       label="Default", min=1, max=100000,
       description="Comments collected from each post when the user doesn't choose."),
    _s("limits.max_comments_per_post_cap", "limits", "Comments per post", "int",
       label="Maximum", min=1, max=100000,
       description="Most comments collected from any one post."),
    _s("limits.global_max_comments", "limits", "Comments per post", "int",
       label="Platform-wide ceiling", min=1, max=1000000,
       description="An extra ceiling on comments per post for every search (the lower "
                   "of this and the maximum applies). A link search also stops once its "
                   "total reaches this, or 500 if higher."),
    _s("limits.min_comments", "limits", "Comments per post", "int",
       label="Facebook posts worth collecting", min=1, max=100000,
       description="In Facebook page searches, a post needs at least this many comments "
                   "to count as worth collecting; smaller posts are marked as not "
                   "qualifying."),
    _s("cost.stop_on_limit", "limits", "Cost protection", "bool",
       label="Limit searches running at once",
       description="Each organization can run at most 5 searches at the same time; "
                   "more are refused until one finishes."),
    _s("cost.warn_before_expensive", "limits", "Cost protection", "bool",
       label="Warn before big searches",
       description="Ask users to confirm before starting a search much bigger than "
                   "the defaults above."),

    # ── Keyword search (app/discovery) ───────────────────────────────────
    _s("features.keyword_search.enabled", "limits", "Keyword search", "bool",
       label="Keyword search", is_public=True,
       description="Let users find new profiles by keyword (needs the Keyword search plan "
                   "feature too). Off = keyword searches are refused."),
    _s("keyword.platform.instagram.enabled", "limits", "Keyword search", "bool",
       label="Instagram discovery", is_public=True,
       description="Let keyword searches look for Instagram profiles."),
    _s("keyword.platform.facebook.enabled", "limits", "Keyword search", "bool",
       label="Facebook discovery", is_public=True,
       description="Let keyword searches look for Facebook Pages."),
    _s("keyword.platform.youtube.enabled", "limits", "Keyword search", "bool",
       label="YouTube discovery", is_public=True,
       description="Let keyword searches look for YouTube channels."),
    _s("keyword.platform.linkedin.enabled", "limits", "Keyword search", "bool",
       label="LinkedIn discovery", is_public=True,
       description="Let keyword searches look for LinkedIn companies. Uses a third-party "
                   "actor, and LinkedIn's terms forbid scraping: turn on only if you accept "
                   "that risk."),
    _s("keyword.max_keywords", "limits", "Keyword search", "int",
       label="Most keywords per search", min=1, max=100, is_public=True,
       description="Plans can allow fewer, never more."),
    _s("keyword.max_candidates", "limits", "Keyword search", "int",
       label="Most profiles per search", min=1, max=10000, is_public=True,
       description="Profiles one keyword search may discover across all its platforms. "
                   "Plans can allow fewer, never more."),
    _s("keyword.max_recent_posts", "limits", "Keyword search", "int",
       label="Most recent posts analyzed", min=1, max=200, is_public=True,
       description="Recent posts checked per profile for comments and engagement. Plans can "
                   "allow fewer, never more."),
    _s("keyword.cache_ttl_hours", "limits", "Keyword search", "int",
       label="Reuse profile data for (hours)", min=0, max=720,
       description="A profile fetched by any search is reused for this long without a new "
                   "Apify call (and without charging). 0 = never reuse."),
    _s("keyword.default.min_followers", "limits", "Keyword search", "int",
       label="Suggested minimum followers", min=0, max=1000000000, is_public=True,
       description="Pre-filled in the search form; users can change it."),
    _s("keyword.default.min_posts", "limits", "Keyword search", "int",
       label="Suggested minimum posts", min=0, max=10000000, is_public=True,
       description="Pre-filled in the search form; users can change it."),
    _s("keyword.default.recent_posts", "limits", "Keyword search", "int",
       label="Suggested recent posts to analyze", min=1, max=200, is_public=True,
       description="Pre-filled in the search form, and used when a search doesn't say."),
    _s("keyword.default.min_avg_comments", "limits", "Keyword search", "int",
       label="Suggested minimum average comments", min=0, max=1000000, is_public=True,
       description="Pre-filled in the search form; users can change it."),
    _s("keyword.default.active_within_days", "limits", "Keyword search", "int",
       label="Suggested posted within (days)", min=1, max=3650, is_public=True,
       description="Pre-filled in the search form; users can change it."),
    _s("discovery.actor.instagram.search", "limits", "Keyword search", "str",
       label="Instagram search actor",
       description="Apify actor that finds Instagram profiles by keyword.",
       placeholder="apify/instagram-search-scraper"),
    _s("discovery.actor.instagram.posts", "limits", "Keyword search", "str",
       label="Instagram posts actor",
       description="Apify actor that reads more recent Instagram posts than the search returns.",
       placeholder="apify/instagram-post-scraper"),
    _s("discovery.actor.facebook.search", "limits", "Keyword search", "str",
       label="Facebook search actor",
       description="Apify actor that finds Facebook Pages by keyword.",
       placeholder="apify/facebook-search-scraper"),
    _s("discovery.actor.youtube.channel_search", "limits", "Keyword search", "str",
       label="YouTube channel search actor",
       description="Optional Apify actor that finds YouTube channels by keyword. Empty = "
                   "search videos with the main YouTube actor and keep their channels."),
    _s("discovery.actor.youtube.channel", "limits", "Keyword search", "str",
       label="YouTube channel details actor",
       description="Apify actor that reads a channel's subscribers, video count and "
                   "verified badge.",
       placeholder="streamers/youtube-channel-scraper"),
    _s("discovery.actor.linkedin.search", "limits", "Keyword search", "str",
       label="LinkedIn company search actor",
       description="Apify actor that finds LinkedIn companies by keyword.",
       placeholder="harvestapi/linkedin-company-search"),

    # ── Platforms & features ─────────────────────────────────────────────
    _s("platform.facebook.enabled", "platforms", "Platforms", "bool",
       label="Facebook", description="Let users search Facebook. Off = Facebook links are refused."),
    _s("platform.instagram.enabled", "platforms", "Platforms", "bool",
       label="Instagram", description="Let users search Instagram. Off = Instagram links are refused."),
    _s("platform.youtube.enabled", "platforms", "Platforms", "bool",
       label="YouTube", description="Let users search YouTube. Off = YouTube links are refused."),
    _s("platform.linkedin.enabled", "platforms", "Platforms", "bool",
       label="LinkedIn", description="Let users search LinkedIn. Off = LinkedIn links are refused."),
    _s("features.url_search.enabled", "platforms", "Features", "bool",
       label="Search by link", is_public=True,
       description="Let users find leads by pasting a post or page link."),
    _s("features.exports.enabled", "platforms", "Features", "bool",
       label="Lead downloads", is_public=True,
       description="Let users download their leads (CSV / Excel). Off = downloads are refused."),
    _s("features.demo_registration.enabled", "platforms", "Features", "bool",
       label="Website sign-ups", is_public=True,
       description="Let new customers sign up for the free trial on the website. "
                   "Off = sign-up is closed (existing customers can still sign in)."),

    # ── Leads & exports ──────────────────────────────────────────────────
    _s("leads.require_phone_or_email", "leads", "New leads", "bool",
       label="Require a phone or email",
       description="A comment becomes a lead only when it includes a phone, WhatsApp "
                   "number or email. Applies to comments analysed from now on."),
    _s("leads.default_status", "leads", "New leads", "select",
       label="Starting status", options=LEAD_STATUS_OPTIONS,
       description="Status given to leads created from now on."),
    _s("exports.max_records", "leads", "Downloads", "int",
       label="Most rows per download", min=1, max=1000000,
       description="The most rows one download can contain (user app, Admin portal, "
                   "platform console, Excel). Bigger downloads are cut and say so."),
    _s("exports.include_leads_only", "leads", "Downloads", "bool",
       label="Leads only by default",
       description="When a download could hold every analysed comment and nobody chose, "
                   "include only leads."),
    _s("exports.include_ai_summary", "leads", "Downloads", "bool",
       label="Include the AI reason",
       description="Add the AI's reason for each lead as a column in the user app's "
                   "\"My leads\" download."),

    # ══ Communication ════════════════════════════════════════════════════
    _s("notifications.alerts_enabled", "notifications", "Admin alerts", "bool",
       label="Live operations alerts",
       description="Show live alerts (failed searches, quota warnings) in the admin "
                   "alert bell. Off = no live alerts; stored notices still show."),
    _s("notifications.job_failed_email", "notifications", "Automatic emails", "bool",
       label="Search failed",
       description="Email an organization when one of its searches fails. Off = nobody "
                   "gets it; the in-app notice still shows. On = each organization's own "
                   "settings decide."),
    _s("notifications.new_lead_email", "notifications", "Automatic emails", "bool",
       label="New leads found",
       description="Email an organization when a search finds new leads. Off = nobody "
                   "gets it; the in-app notice still shows. On = each organization's own "
                   "settings decide."),
    _s("email.sender_name", "notifications", "Email sender", "str",
       label="Sender name",
       description="Name shown as the sender of every email. Empty = the name in the "
                   "mail server settings, otherwise the product name."),
    _s("email.reply_to", "notifications", "Email sender", "email",
       label="Reply-to address",
       description="Where replies to platform emails go. Empty = the sending address."),
    _s("email.footer_text", "notifications", "Email sender", "str",
       label="Footer line",
       description="Added as the last paragraph of every email the platform sends "
                   "(e.g. your address)."),

    _s("api_limits.enabled", "notifications", "API limit alerts", "bool",
       label="Watch API usage and limits",
       description="Check the usage and limits of Apify, Gemini, email, SMS and every "
                   "organization's tokens and plan in the background, and tell the people "
                   "affected before something runs out. Off = no background checks or alerts."),
    _s("api_limits.check_minutes", "notifications", "API limit alerts", "int",
       label="Check every (minutes)", min=5, max=1440,
       description="How often the limits are checked (the background pass runs every 15 "
                   "minutes, so smaller values act like 15). Apify recommends 5–15 minutes."),
    _s("api_limits.warn_percent", "notifications", "API limit alerts", "int",
       label="Warn at (%)", min=1, max=99,
       description="A first warning is sent when a limit is this full."),
    _s("api_limits.critical_percent", "notifications", "API limit alerts", "int",
       label="Urgent at (%)", min=1, max=100,
       description="An urgent alert (with email) is sent when a limit is this full. At 100% "
                   "everyone affected is told the limit is reached."),
    _s("api_limits.notify_org_admins", "notifications", "API limit alerts", "bool",
       label="Tell organization admins",
       description="When one of LeadAI's own services is almost used up, tell the admins of "
                   "the organizations that use it (no numbers, just what it means for them)."),
    _s("api_limits.notify_users", "notifications", "API limit alerts", "bool",
       label="Tell users when something stops",
       description="When a limit is reached, show the users of the affected organizations an "
                   "in-app notice (never an email). Their own notification choices apply."),
    _s("api_limits.email_admins", "notifications", "API limit alerts", "bool",
       label="Email urgent alerts",
       description="Email the Super Admin and organization admins when a limit is almost or "
                   "fully used up. Off = in-app notices only."),
    _s("sms.min_balance", "notifications", "API limit alerts", "float",
       label="Warn when the SMS balance is below", min=0, max=100000000,
       description="In the SMS account's own currency: Twilio USD, Fast2SMS INR. Below this a "
                   "warning is sent, below half of it an urgent alert; at zero texts stop. "
                   "0 = off (only an empty balance alerts)."),
    _s("email.daily_limit", "notifications", "API limit alerts", "int",
       label="Mailbox daily sending limit", min=0, max=10000000,
       description="How many emails your mail server lets you send in 24 hours, so LeadAI can "
                   "warn before it refuses more. Typical: Gmail 500, Google Workspace 2,000, "
                   "Hostinger 1,000 or 3,000 (hPanel → Emails → your mailbox → View limits). "
                   "0 = not watched."),

    # ══ Operations ═══════════════════════════════════════════════════════
    _s("security.login_protection", "security", "Sign-in protection", "bool",
       label="Limit sign-in attempts",
       description="Slow down password guessing by limiting how often one network "
                   "address can try to sign in. Off = no limit."),
    _s("security.lockout_threshold", "security", "Sign-in protection", "int",
       label="Wrong passwords before lock", min=1, max=50,
       description="Wrong passwords in a row that lock that account for a while."),
    _s("security.lockout_minutes", "security", "Sign-in protection", "int",
       label="Lock lasts (minutes)", min=1, max=1440),
    _s("security.session_timeout_hours", "security", "Sessions", "int",
       label="Admin idle sign-out (hours)", min=1, max=8760,
       description="Admin staff are signed out after this long without activity."),
    _s("security.impersonation_minutes", "security", "Sessions", "int",
       label="Support view time limit (minutes)", min=5, max=240,
       description="When staff open a customer's account to help them, the session "
                   "ends after this time."),

    _s("billing.provider", "payments", "Payment provider", "select",
       label="Take payments with", options=BILLING_PROVIDER_OPTIONS,
       description="'auto' = Razorpay when its keys are set, else Stripe, else the test-only "
                   "mock page. A provider whose keys are missing is never used."),

    _s("maintenance.enabled", "maintenance", "Maintenance mode", "bool",
       label="Maintenance mode",
       description="Show a maintenance page instead of the user app. Admin portals "
                   "and signed-in admins keep working."),
    _s("maintenance.message", "maintenance", "Maintenance mode", "textarea",
       label="Message", is_public=True,
       description="What users see on the maintenance page."),
    _s("lifecycle.auto_apply", "maintenance", "Automation", "bool",
       label="Apply plan and trial changes automatically",
       description="End finished billing periods and expired trials on schedule. "
                   "Off = they are only listed for review (Subscriptions → Lifecycle "
                   "automation)."),
    _s("appearance.dashboard_widgets", "maintenance", "Operations overview layout", "json",
       label="Overview cards", sensitive=True,
       json_shape={"show": "bool", "order": "int"},
       description="Show, hide and order the cards on Operations overview. "
                   "JSON: {\"kpis\": {\"show\": true, \"order\": 1}}. Cards: hero, kpis, "
                   "activity, leads_by_platform, alerts, system_status, keyword_filter, "
                   "recent_jobs."),
]

SPEC_BY_KEY: Dict[str, SettingSpec] = {spec.key: spec for spec in SETTINGS}

# Registry excludes secrets (apify.token) and internal keys (actor.*,
# security.session_epoch, apify.last_test_*) — they stay in their own views.
REGISTERED_KEYS: set = set(SPEC_BY_KEY)


# ── Categories (tab order for the admin UI) ─────────────────────────────────

CATEGORIES: List[Dict[str, Any]] = [
    {"id": "general", "label": "General", "icon": "🏷", "section": "Brand & website",
     "description": "Your product name, company, support contacts and the links shown in "
                    "footers.",
     "links": [["Website / CMS", "#/website"]]},
    {"id": "branding", "label": "Branding", "icon": "🎨", "section": "Brand & website",
     "description": "Logos, colours, font and the sign-in page. Changes show on the website, "
                    "the sign-in page and every app on the next page load.",
     "links": [["Website / CMS", "#/website"]]},
    {"id": "seo", "label": "SEO & sharing", "icon": "🌍", "section": "Brand & website",
     "description": "How the app and sign-in pages look in search results and when a link "
                    "is shared.",
     "links": [["Website SEO", "#/website"]]},
    {"id": "ai", "label": "AI engine", "icon": "🧠", "section": "Leads engine",
     "description": "How comments are read: Gemini analysis, its cost limit and the "
                    "fallback rules.",
     "links": [["AI Management", "#/ai"], ["Industries", "#/industries"],
               ["API keys", "#/provider-keys"]]},
    {"id": "scoring", "label": "Lead scoring", "icon": "🎯", "section": "Leads engine",
     "description": "Which comments become leads and how hot they are. Every analysed "
                    "comment gets a 0–100 lead score: with a phone or email it needs 20+, "
                    "without one 35+ and real buying intent.",
     "links": [["Keyword rules", "#/keyword-rules"]]},
    {"id": "detection", "label": "Comment detection", "icon": "🔎", "section": "Leads engine",
     "description": "What is picked out of each comment, and which comments are skipped "
                    "before any AI cost.",
     "links": [["Keyword rules", "#/keyword-rules"]]},
    {"id": "limits", "label": "Search limits", "icon": "⏱", "section": "Leads engine",
     "description": "How much one search collects. Plans can allow less, never more than "
                    "the maximums here.",
     "links": [["Plans", "#/plans"], ["Usage & cost", "#/usage"]]},
    {"id": "platforms", "label": "Platforms & features", "icon": "🌐",
     "section": "Leads engine",
     "description": "Switch social platforms and product features on or off for everyone.",
     "links": [["Feature Flags", "#/flags"], ["Free trials", "#/demo"]]},
    {"id": "leads", "label": "Leads & downloads", "icon": "💼", "section": "Leads engine",
     "description": "How new leads start out and what lead downloads contain.",
     "links": [["Reports / Exports", "#/reports"]]},
    {"id": "notifications", "label": "Email & alerts", "icon": "📨",
     "section": "Communication",
     "description": "The mail server every email goes out through, the alert bell, automatic "
                    "emails and how emails are signed. SMS and welcome / sign-in messages are on "
                    "the Messaging page.",
     "links": [["Messaging", "#/messaging"], ["Environment", "#/environment"]]},
    {"id": "payments", "label": "Payments", "icon": "💳", "section": "Operations",
     "description": "Which provider customers pay through, and whether its keys and webhook "
                    "are set up. Keys live in the server's .env file, never in the app.",
     "links": [["Payments", "#/payments"], ["Subscriptions", "#/subscriptions"]]},
    {"id": "security", "label": "Security", "icon": "🔐", "section": "Operations",
     "description": "Sign-in protection and session limits.",
     "links": [["Security Center", "#/security"], ["Admins", "#/admins"]]},
    {"id": "maintenance", "label": "Operations", "icon": "🛠", "section": "Operations",
     "description": "Maintenance mode, scheduled plan and trial changes, and the Operations "
                    "overview layout.",
     "links": [["Subscriptions", "#/subscriptions"], ["Operations overview", "#/operations"]]},
]

# Old tab ids (bookmarks, guides) → where those settings live now.
CATEGORY_ALIASES: Dict[str, str] = {"appearance": "maintenance", "features": "platforms"}

# One line under a group's title.
GROUP_INFO: Dict[Tuple[str, str], str] = {
    ("general", "Regional defaults"): "Starting values for new organizations.",
    ("branding", "Logos & icons"): "Upload a file or paste an https:// or /static/ address, "
                                   "then save. An empty field uses the default.",
    ("scoring", "Lead score"): "Points that add up to the 0–100 lead score, which decides "
                               "who is a lead.",
    ("scoring", "Signal score"): "A second 0–100 score shown next to each lead: points for "
                                 "what the comment mentions.",
    ("scoring", "Hot / warm from the signal score"): "Optional: label leads the AI left "
                                                     "unlabelled.",
    ("detection", "What to skip"): "Skipped comments cost nothing: no AI call is made.",
    ("limits", "Posts per search"): "Users can ask for less, never more than the maximum.",
    ("limits", "Comments per post"): "Users can ask for less, never more than the maximum.",
    ("limits", "Keyword search"): "Find new profiles by keyword, then collect their comments "
                                  "like a link search. Plans can allow less, never more than "
                                  "these maximums.",
    ("notifications", "Automatic emails"): "Platform-wide switches over each organization's "
                                           "own notification settings.",
    ("notifications", "Email sender"): "Applies to every email: sign-up, password reset, "
                                       "alerts and messages. The product name replaces "
                                       "\"LeadAI\" in all of them.",
    ("notifications", "API limit alerts"): "Warnings before Apify, Gemini, email, SMS, tokens "
                                           "or a plan run out. See them in API usage & limits.",
    ("maintenance", "Automation"): "Review what would change first in Subscriptions → "
                                   "Lifecycle automation.",
}

# Settings that did nothing and were taken off the page. Stored values and
# defaults are kept (harmless), so nothing breaks for older data.
RETIRED_KEYS = frozenset({
    "general.app.short_name", "general.app.description", "general.company.website",
    "branding.logo_dark", "branding.logo_compact", "branding.logo_email",
    "appearance.sidebar_title", "appearance.sidebar_subtitle",
    "appearance.sidebar_collapsed_default", "appearance.show_icons",
    "appearance.show_section_labels", "appearance.show_footer_links",
    "appearance.nav_overrides",
    "defaults.comment_filter_mode", "defaults.keyword_preset", "defaults.theme",
    "defaults.date_range", "defaults.page_size",
    "email.company_name", "email.logo",
    "localization.date_format", "localization.time_format", "localization.language",
    "features.ai_analysis.enabled",
    # no lead de-duplication, retention job or format choice exists for these;
    # lead temperature always comes from the AI or the score
    "leads.duplicate_detection", "leads.retention_days", "leads.default_temperature",
    "exports.default_format",
    # nothing says "Powered by"; every page already shows your own name and logo
    "branding.white_label",
})
# Internal state kept in system_settings but never edited by hand.
INTERNAL_KEYS = frozenset({"lifecycle.preview_notified"})


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
                "description": GROUP_INFO.get((cat["id"], group), ""),
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
            x = float(value)
        except (TypeError, ValueError):
            return value, "Expected a number"
        if spec.min is not None and x < spec.min:
            return value, f"Minimum is {spec.min}"
        if spec.max is not None and x > spec.max:
            return value, f"Maximum is {spec.max}"
        return x, None
    if t == "select":
        text = str(value)
        if spec.options and text not in spec.options:
            return value, f"Must be one of: {', '.join(spec.options)}"
        return text, None
    if t == "color":
        text = str(value).strip()
        if text and not _HEX_RE.match(text):
            return value, "Must be a hex color like #2e9573"
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
        if "#" in text:   # e.g. a pasted page address like …/superadmin#/settings
            return value, "Must point to an image or file, not to a page — upload the file or paste its address"
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

_PUBLIC_CONFIG_CACHE: Optional[Tuple[float, Dict[str, Any]]] = None
_PUBLIC_CONFIG_TTL: float = 60.0


def _trial_summary() -> Dict[str, Any]:
    """What signing up on the website gives: a self-serve free trial (starts at
    once) or a demo request that waits for approval."""
    try:
        from app.lifecycle.config import get_demo_config
        cfg = get_demo_config()
        # "credits" = the trial's token allowance (named so the public config
        # never carries a key that looks like a secret)
        return {"self_serve": bool(cfg.get("auto_approve")), "days": int(cfg.get("duration_days") or 0),
                "credits": int(cfg.get("tokens") or 0)}
    except Exception:
        return {"self_serve": False, "days": 0, "credits": 0}


def invalidate_public_config_cache() -> None:
    global _PUBLIC_CONFIG_CACHE
    _PUBLIC_CONFIG_CACHE = None


async def build_public_config(get: Optional[Callable[[str], Any]] = None,
                              all_settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Assemble the /api/public/config payload. Never includes secrets,
    tokens, credentials or internal infrastructure details.

    Fast, in-memory cached (60s TTL), and uses single-query batch settings loading
    to eliminate sequential N+1 database roundtrips.
    """
    global _PUBLIC_CONFIG_CACHE
    now = time.time()
    if (
        get is None
        and all_settings is None
        and _PUBLIC_CONFIG_CACHE is not None
        and (now - _PUBLIC_CONFIG_CACHE[0] < _PUBLIC_CONFIG_TTL)
    ):
        return _PUBLIC_CONFIG_CACHE[1]

    values: Dict[str, Any] = {}
    if all_settings is not None:
        values = all_settings
    elif get is None:
        from app.admin.settings import aget_all_settings
        values = await aget_all_settings()
    else:
        import inspect
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

    def img(key: str) -> str:
        """An image address only, never a page link saved by mistake."""
        text = s(key).strip()
        return text if text and "#" not in text and _URL_RE.match(text) else ""

    payload = {
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
            "logo_primary": img("branding.logo_primary"),
            "logo_dark": img("branding.logo_dark"),
            "logo_light": img("branding.logo_light"),
            "logo_compact": img("branding.logo_compact"),
            "logo_login": img("branding.logo_login"),
            "logo_email": img("branding.logo_email"),
            "favicon": img("branding.favicon"),
            "apple_touch_icon": img("branding.apple_touch_icon"),
            "colors": {
                "primary": s("branding.colors.primary", "#2e9573"),
                "accent": s("branding.colors.accent", "#d9a441"),
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
            "og_image": img("seo.og_image"),
        },
        "features": {
            "url_search": bool(g("features.url_search.enabled", True)),
            "keyword_search": bool(g("features.keyword_search.enabled", True)),
            "exports": bool(g("features.exports.enabled", True)),
            "demo_registration": bool(g("features.demo_registration.enabled", True)),
            "ai_analysis": bool(g("ai.enabled", True)),
        },
        # the user app asks before a search much bigger than the defaults
        "cost": {"warn_before_expensive": bool(g("cost.warn_before_expensive", True))},
        # keyword search: switches (each ANDed with the platform switch), caps
        # and the suggested filter values the search form pre-fills
        "keyword_search": {
            "enabled": bool(g("features.keyword_search.enabled", True)),
            "platforms": {p: bool(g(f"keyword.platform.{p}.enabled")) and bool(g(f"platform.{p}.enabled"))
                          for p in ("instagram", "facebook", "youtube", "linkedin")},
            "caps": {"max_keywords": int(g("keyword.max_keywords") or 5),
                     "max_candidates": int(g("keyword.max_candidates") or 200),
                     "max_recent_posts": int(g("keyword.max_recent_posts") or 30)},
            "defaults": {"min_followers": int(g("keyword.default.min_followers") or 0),
                         "min_posts": int(g("keyword.default.min_posts") or 0),
                         "recent_posts_to_analyze": int(g("keyword.default.recent_posts") or 10),
                         "min_avg_comments": int(g("keyword.default.min_avg_comments") or 0),
                         "active_within_days": int(g("keyword.default.active_within_days") or 90)},
        },
        "trial": _trial_summary(),
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
    if get is None and all_settings is None:
        _PUBLIC_CONFIG_CACHE = (now, payload)
    return payload
