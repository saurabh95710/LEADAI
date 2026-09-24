"""
LeadAI CMS Data Models

MongoDB document schemas for the public website CMS.
All collections are platform-global (one website — no organization_id).

Page document
-------------
    {slug, title, status: "draft"|"published", seo{title,description,og_image,robots},
     sections: [Section],            # the DRAFT (what the editor works on)
     live: {title, seo, sections},   # the PUBLISHED snapshot the website serves
     has_draft_changes: bool, version, versions[], created_at, updated_at,
     published_at, created_by, updated_by}

Editing a page never takes it offline: edits change the draft fields and set
``has_draft_changes``; the public site keeps serving ``live`` until the
operator publishes again. ``unpublish`` takes the page offline.

Section (all text is plain text — rendered with textContent on the website)
    {key, type, enabled, eyebrow, title, highlight, subtitle, body, note,
     card_title, cta_primary{label,url}, cta_secondary{label,url},
     items: [{icon, title, description, label, value, tone, url}]}

``body`` supports a tiny plain-text convention (no HTML): blank line = new
paragraph, a line starting with "## " = heading, "- " = bullet.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Collection names ────────────────────────────────────────────────────────

COLL_PAGES        = "website_pages"
COLL_SECTIONS     = "website_sections"      # reserved (sections live inside pages)
COLL_NAVIGATION   = "website_navigation"
COLL_FAQ          = "website_faq"
COLL_TESTIMONIALS = "website_testimonials"
COLL_MEDIA        = "website_media"
COLL_SETTINGS     = "website_settings"
COLL_CONTACT      = "contact_submissions"
COLL_SEED_STATE   = "website_seed_state"    # what the seed already created once

# ── Index definitions (applied in lifespan) ─────────────────────────────────

CMS_INDEXES = [
    (COLL_PAGES,        [("slug", 1)],       {"unique": True,  "name": "idx_slug"}),
    (COLL_PAGES,        [("status", 1)],     {"name": "idx_status"}),
    (COLL_SECTIONS,     [("page_id", 1), ("order", 1)], {"name": "idx_page_order"}),
    (COLL_NAVIGATION,   [("location", 1), ("order", 1)], {"name": "idx_nav_order"}),
    (COLL_FAQ,          [("order", 1)],      {"name": "idx_faq_order"}),
    (COLL_TESTIMONIALS, [("order", 1)],      {"name": "idx_test_order"}),
    (COLL_MEDIA,        [("uploaded_at", -1)], {"name": "idx_media_date"}),
    (COLL_CONTACT,      [("created_at", -1)], {"name": "idx_contact_date"}),
    (COLL_CONTACT,      [("read", 1), ("created_at", -1)], {"name": "idx_contact_read"}),
    (COLL_SETTINGS,     [("key", 1)],        {"unique": True,  "name": "idx_settings_key"}),
]


# ── Helper ──────────────────────────────────────────────────────────────────

def _clean(doc: Optional[Dict]) -> Optional[Dict]:
    """Stringify _id and datetime fields for JSON serialisation."""
    if doc is None:
        return None
    from bson import ObjectId
    result: Dict[str, Any] = {}
    for k, v in doc.items():
        if isinstance(v, ObjectId):
            result[k] = str(v)
        elif isinstance(v, datetime):
            result[k] = v.isoformat()
        elif isinstance(v, list):
            result[k] = [_clean(i) if isinstance(i, dict) else i for i in v]
        elif isinstance(v, dict):
            result[k] = _clean(v)
        else:
            result[k] = v
    return result


def clean_list(docs: List[Dict]) -> List[Dict]:
    return [_clean(d) for d in docs if d]


# ── Section vocabulary (the editor schema) ──────────────────────────────────

SECTION_TYPES = {
    "hero":          "Hero (title, subtitle, CTAs, example card)",
    "logos":         "Platform / logo strip",
    "features":      "Feature grid",
    "steps":         "Numbered steps (how it works)",
    "pricing":       "Pricing header + how-to-buy steps (plans come from billing)",
    "testimonials":  "Testimonials header (items come from Testimonials)",
    "faq":           "FAQ header (items come from FAQ)",
    "cta":           "Call-to-action banner",
    "page_header":   "Page header (eyebrow, title, subtitle)",
    "rich_text":     "Text body (## heading, - bullet, blank line = paragraph)",
    "cards":         "Card grid (values, stats)",
    "contact_form":  "Contact form copy + contact details",
}

# Named icons the website renders as SVG (anything else is shown as text, e.g. an emoji)
ICON_NAMES = ["search", "brain", "trophy", "phone", "refresh", "filter", "chart",
              "download", "users", "shield", "zap", "globe", "mail", "clock",
              "target", "check", "sparkles", "map", "facebook", "instagram",
              "youtube", "linkedin", "x", "lock", "file", "cookie"]


def _cta(label: str, url: str) -> Dict[str, str]:
    return {"label": label, "url": url}


def _sec(key: str, type_: str, **kw) -> Dict[str, Any]:
    base = {"key": key, "type": type_, "enabled": True, "eyebrow": "", "title": "",
            "highlight": "", "subtitle": "", "body": "", "note": "", "card_title": "",
            "cta_primary": {"label": "", "url": ""}, "cta_secondary": {"label": "", "url": ""},
            "items": []}
    base.update(kw)
    return base


def _item(**kw) -> Dict[str, str]:
    base = {"icon": "", "title": "", "description": "", "label": "", "value": "",
            "tone": "", "url": ""}
    base.update(kw)
    return base


_DEMO = _cta("Request a demo", "/request-demo")
_SIGNIN = _cta("Sign in", "/login")

HOME_SECTIONS = [
    _sec("hero", "hero",
         eyebrow="AI-powered lead intelligence",
         title="Turn social conversations into high-intent leads with AI",
         highlight="high-intent leads",
         subtitle=("LeadAI reads the posts and comments around your market on Facebook, "
                   "Instagram, YouTube and LinkedIn, detects buying intent, extracts contact "
                   "details and scores every lead automatically."),
         cta_primary=_DEMO, cta_secondary=_cta("See how it works", "/how-it-works"),
         card_title="Example lead profile",
         note="Example data — for illustration only",
         items=[_item(label="Lead score", value="94/100", tone="primary"),
                _item(label="Intent", value="Ready to buy", tone=""),
                _item(label="Quality", value="High", tone="success"),
                _item(label="Email", value="Detected", tone="success"),
                _item(label="Phone / WhatsApp", value="Detected", tone="success"),
                _item(label="Urgency", value="High", tone="warning"),
                _item(label="Next step", value="Contact today", tone="primary")]),
    _sec("platforms", "logos", title="Works with the platforms your buyers use",
         items=[_item(icon="facebook", title="Facebook"), _item(icon="instagram", title="Instagram"),
                _item(icon="youtube", title="YouTube"), _item(icon="linkedin", title="LinkedIn")]),
    _sec("features", "features",
         eyebrow="Features", title="Everything you need to find buyers, not just followers",
         highlight="buyers",
         subtitle="Real AI analysis of social conversations — not keyword matching.",
         items=[
             _item(icon="search", title="Paste a link, get leads",
                   description="Drop in any Facebook, Instagram, YouTube or LinkedIn URL. LeadAI detects the platform and collects posts and comments for you."),
             _item(icon="brain", title="Buying-intent detection",
                   description="AI reads each comment in context to separate genuine purchase signals from noise and small talk."),
             _item(icon="trophy", title="0–100 lead scoring",
                   description="Every lead gets a transparent score based on intent strength, urgency and contact availability."),
             _item(icon="phone", title="Contact extraction",
                   description="Emails, phone numbers and WhatsApp numbers mentioned in comments are pulled out automatically."),
             _item(icon="refresh", title="Lead lifecycle",
                   description="Move leads from discovered to contacted, qualified and won — nothing falls through the cracks."),
             _item(icon="filter", title="Smart filters",
                   description="Keyword, category and AI filters keep your team focused on the conversations that matter."),
             _item(icon="chart", title="Analytics",
                   description="See where leads come from, how they score and how your pipeline converts over time."),
             _item(icon="download", title="CSV export",
                   description="Export leads, posts and comments to your CRM or spreadsheet in one click."),
             _item(icon="users", title="Built for teams",
                   description="Invite teammates, assign roles and work leads together in a private workspace."),
         ]),
    _sec("how-it-works", "steps",
         eyebrow="How it works", title="From a social link to a qualified lead in minutes",
         highlight="qualified lead",
         subtitle="Fully automated — you only step in when it's time to sell.",
         items=[
             _item(title="Add a social URL", description="A page, profile, channel, company page or a single post."),
             _item(title="We collect the conversation", description="Posts and comments are gathered from the right platform automatically."),
             _item(title="AI qualifies every comment", description="Intent, urgency, sentiment and quality are analysed comment by comment."),
             _item(title="Leads are scored", description="Each lead receives a 0–100 score and a recommended next action."),
             _item(title="Contacts are extracted", description="Emails, phone and WhatsApp numbers are captured where people shared them."),
             _item(title="Your team takes action", description="Work the pipeline, track status and export to your CRM."),
         ]),
    _sec("pricing", "pricing",
         eyebrow="Pricing", title="Simple, transparent pricing", highlight="transparent",
         subtitle="Pick the plan that fits your team. Every account starts with a short demo so we can set you up properly.",
         card_title="How to get started",
         items=[
             _item(title="Request a demo", description="Tell us about your team and create your login.",
                   label="Request a demo", url="/request-demo"),
             _item(title="Get approved", description="We review your request and open your workspace — you'll get an email.",
                   label="Check request status", url="/demo-pending"),
             _item(title="Choose your plan", description="Sign in and pick a plan in your workspace billing page.",
                   label="Sign in", url="/login"),
         ],
         cta_primary=_DEMO, cta_secondary=_cta("Already approved? Sign in", "/login"),
         note="Prices shown exclude applicable taxes."),
    _sec("testimonials", "testimonials",
         eyebrow="Customers", title="Trusted by lead generation teams", highlight="lead generation teams"),
    _sec("faq", "faq", eyebrow="FAQ", title="Frequently asked questions", highlight="questions",
         subtitle="Can't find what you're looking for?",
         cta_primary=_cta("Contact us", "/contact")),
    _sec("cta", "cta", eyebrow="Ready when you are",
         title="Start discovering high-intent leads today",
         subtitle="Request a demo and we'll get your workspace ready.",
         cta_primary=_DEMO, cta_secondary=_cta("Talk to us", "/contact")),
]

ABOUT_SECTIONS = [
    _sec("header", "page_header", eyebrow="About LeadAI",
         title="We help teams hear buyers in the noise",
         subtitle="LeadAI turns public social conversations into qualified, contactable leads."),
    _sec("story", "rich_text", title="Our story", body=(
        "Every day, people ask for recommendations, compare options and say they are ready "
        "to buy — in the comments of posts across social media. Most of those signals are "
        "never seen by the businesses that could help.\n\n"
        "LeadAI was built to change that. We combine reliable data collection with AI that "
        "understands context, so sales and marketing teams can focus on real buyers instead "
        "of scrolling through thousands of comments.\n\n"
        "## What we believe\n"
        "- Leads should be earned from genuine intent, not guessed from keywords.\n"
        "- Your data belongs to you and stays inside your private workspace.\n"
        "- Great tools are simple: paste a link, get qualified leads.")),
    _sec("values", "cards", eyebrow="Principles", title="How we work", items=[
        _item(icon="target", title="Intent first", description="We surface people who are actually looking to buy."),
        _item(icon="shield", title="Private by design", description="Strict workspace isolation and role-based access."),
        _item(icon="zap", title="Fast to value", description="From a social link to qualified leads in minutes."),
    ]),
    _sec("cta", "cta", title="See LeadAI on your own market",
         subtitle="Request a demo and we'll walk you through it.",
         cta_primary=_DEMO, cta_secondary=_cta("Contact us", "/contact")),
]

CONTACT_SECTIONS = [
    _sec("header", "page_header", eyebrow="Get in touch", title="Let's talk", highlight="talk",
         subtitle="Questions about LeadAI, pricing or a partnership? Send us a message and we'll get back to you."),
    _sec("form", "contact_form", title="Send us a message",
         note="We respond within one business day.",
         body="Thanks for reaching out — your message is on its way to our team. We'll reply to the email you provided.",
         card_title="Message sent",
         cta_primary=_cta("Back to the website", "/website"),
         cta_secondary=_DEMO,
         items=[_item(icon="clock", title="Replies within one business day"),
                _item(icon="globe", title="Serving teams worldwide")]),
]


def _legal(title: str, subtitle: str, body: str) -> List[Dict[str, Any]]:
    return [_sec("header", "page_header", eyebrow="Legal", title=title, subtitle=subtitle),
            _sec("body", "rich_text", body=body)]


PRIVACY_BODY = (
    "This Privacy Policy explains how LeadAI (\"we\", \"us\") collects, uses and protects "
    "personal information when you visit our website or use the LeadAI service.\n\n"
    "## Information we collect\n"
    "- Account information you give us, such as your name, work email, company and password (stored only as a secure hash).\n"
    "- Messages and demo requests you send us through our forms.\n"
    "- Usage information such as log data, device and browser type, and pages visited.\n"
    "- Content your workspace processes, such as public social media posts and comments collected at your request.\n\n"
    "## How we use information\n"
    "- To provide, secure and improve the service.\n"
    "- To review demo requests, open accounts and provide support.\n"
    "- To send service and account notifications.\n"
    "- To comply with legal obligations.\n\n"
    "## Sharing\n"
    "We do not sell personal information. We share information only with service providers "
    "that help us run LeadAI (for example hosting, email delivery, payment processing and data "
    "collection providers), under contracts that protect it, or when required by law.\n\n"
    "## Data retention and security\n"
    "We keep information for as long as your account is active or as needed to provide the "
    "service, and we use technical and organisational measures — including tenant isolation, "
    "encryption in transit and access controls — to protect it.\n\n"
    "## Your rights\n"
    "Depending on where you live, you may have the right to access, correct, delete or export "
    "your personal information, and to object to certain processing. Contact us to exercise "
    "these rights.\n\n"
    "## Contact\n"
    "Questions about this policy? Reach us through the contact page.")

TERMS_BODY = (
    "These Terms of Service govern your access to and use of the LeadAI website and service. "
    "By creating an account or using the service you agree to these terms.\n\n"
    "## Accounts\n"
    "- Accounts are opened after a demo request has been reviewed and approved.\n"
    "- You are responsible for keeping your login credentials confidential and for activity in your workspace.\n"
    "- You must provide accurate information and keep it up to date.\n\n"
    "## Subscriptions and payment\n"
    "Paid plans are billed in advance for the selected billing period. Plan features, limits "
    "and prices are shown on our pricing page and in your workspace. Subscriptions may be "
    "suspended if payment fails.\n\n"
    "## Acceptable use\n"
    "- Use LeadAI only for lawful purposes and in line with the terms of the platforms you analyse.\n"
    "- Respect the privacy of the people whose public content you process, and comply with applicable data protection and anti-spam laws when contacting leads.\n"
    "- Do not attempt to disrupt, reverse engineer or gain unauthorised access to the service.\n\n"
    "## Your content\n"
    "You keep all rights to the data in your workspace. You grant us the limited rights needed "
    "to operate the service for you.\n\n"
    "## Disclaimers and liability\n"
    "The service is provided \"as is\". AI analysis may be inaccurate and should be reviewed "
    "before you act on it. To the extent permitted by law, our liability is limited to the "
    "amount you paid for the service in the twelve months before the claim.\n\n"
    "## Changes and termination\n"
    "We may update these terms and will notify you of material changes. You may stop using "
    "the service at any time; we may suspend accounts that breach these terms.\n\n"
    "## Contact\n"
    "Questions about these terms? Reach us through the contact page.")

COOKIES_BODY = (
    "This Cookie Policy explains how LeadAI uses cookies and similar technologies.\n\n"
    "## What are cookies?\n"
    "Cookies are small text files stored by your browser. They help websites remember "
    "information about your visit.\n\n"
    "## Cookies we use\n"
    "- Strictly necessary: a secure session cookie that keeps you signed in and protects your account. The service cannot work without it.\n"
    "- Preferences: your light or dark theme choice, stored in your browser.\n"
    "- Analytics (only if enabled): aggregated statistics that help us improve the website.\n\n"
    "## Managing cookies\n"
    "You can block or delete cookies in your browser settings. If you block strictly "
    "necessary cookies you will not be able to sign in.\n\n"
    "## Contact\n"
    "Questions about cookies? Reach us through the contact page.")


def _page(slug: str, title: str, seo_title: str, seo_desc: str,
          sections: Optional[List[Dict[str, Any]]] = None, robots: str = "index,follow") -> Dict[str, Any]:
    return {"slug": slug, "title": title,
            "seo": {"title": seo_title, "description": seo_desc, "og_image": "", "robots": robots},
            "sections": sections or []}


# ── Default pages seeded on first run (per slug, once) ──────────────────────

DEFAULT_PAGES = [
    _page("home", "Home", "LeadAI — AI-Powered Social Lead Intelligence",
          "Turn social conversations into high-intent leads with AI. LeadAI analyses Facebook, Instagram, YouTube & LinkedIn for qualified buyer signals.",
          HOME_SECTIONS),
    _page("features", "Features", "LeadAI Features — AI Lead Intelligence Platform",
          "Explore LeadAI features: social URL search, AI intent detection, lead scoring, contact extraction, analytics and team collaboration."),
    _page("how-it-works", "How it works", "How LeadAI Works — From Social Link to Qualified Lead",
          "See how LeadAI turns a social media link into scored, contactable leads in minutes."),
    _page("pricing", "Pricing", "LeadAI Pricing — Choose Your Plan",
          "Simple, transparent pricing for teams of every size. Request a demo to get started."),
    _page("faq", "FAQ", "LeadAI FAQ — Frequently Asked Questions",
          "Answers to common questions about LeadAI, supported platforms, data security and pricing."),
    _page("about", "About", "About LeadAI",
          "Learn about LeadAI, the AI-powered social intelligence platform for discovering high-intent leads.",
          ABOUT_SECTIONS),
    _page("contact", "Contact", "Contact LeadAI",
          "Get in touch with the LeadAI team. Ask a question, talk about pricing or request a demo.",
          CONTACT_SECTIONS),
    _page("privacy", "Privacy Policy", "Privacy Policy — LeadAI",
          "How LeadAI collects, uses and protects personal information.",
          _legal("Privacy Policy", "Please review this policy with your legal adviser before relying on it.", PRIVACY_BODY)),
    _page("terms", "Terms of Service", "Terms of Service — LeadAI",
          "The terms that govern your use of the LeadAI website and service.",
          _legal("Terms of Service", "Please review these terms with your legal adviser before relying on them.", TERMS_BODY)),
    _page("cookies", "Cookie Policy", "Cookie Policy — LeadAI",
          "How LeadAI uses cookies and similar technologies.",
          _legal("Cookie Policy", "How and why we use cookies.", COOKIES_BODY)),
]

# Public path of each built-in page (sitemap, canonical URLs, SSR meta)
PAGE_PATHS = {"home": "/website", "features": "/features", "how-it-works": "/how-it-works",
              "pricing": "/pricing", "faq": "/faq", "about": "/about", "contact": "/contact",
              "privacy": "/privacy", "terms": "/terms", "cookies": "/cookies"}

DEFAULT_FAQ = [
    {"question": "What is LeadAI?", "answer": "LeadAI is an AI-powered social intelligence platform that analyses posts and comments across Facebook, Instagram, YouTube and LinkedIn to identify people with genuine buying intent.", "category": "general", "order": 1, "enabled": True},
    {"question": "Which social platforms are supported?", "answer": "Facebook, Instagram, YouTube and LinkedIn. Which platforms are included depends on your plan.", "category": "general", "order": 2, "enabled": True},
    {"question": "How does AI lead scoring work?", "answer": "LeadAI analyses each comment's text, intent signals, urgency and whether contact details were shared, then assigns a 0–100 score with a recommended next action.", "category": "features", "order": 3, "enabled": True},
    {"question": "How do I buy a subscription?", "answer": "Request a demo and create your login. Once our team approves your request you can sign in and choose a plan from the billing page in your workspace.", "category": "billing", "order": 4, "enabled": True},
    {"question": "Is my data secure?", "answer": "Yes. Your data is stored in your private workspace with strict tenant isolation and role-based access — it is never shared with other organisations.", "category": "security", "order": 5, "enabled": True},
    {"question": "Can I export leads?", "answer": "Yes. You can export leads, pages, posts and comments as CSV files from your workspace.", "category": "features", "order": 6, "enabled": True},
]

DEFAULT_WEBSITE_SETTINGS = [
    {"key": "brand_name",       "value": "LeadAI",          "label": "Brand Name"},
    {"key": "tagline",          "value": "Turn Social Conversations Into High-Intent Leads With AI", "label": "Tagline"},
    {"key": "footer_tagline",   "value": "AI-powered social lead intelligence. Turn conversations into customers.", "label": "Footer Tagline"},
    {"key": "site_url",         "value": "",                 "label": "Public Site URL (canonical, sitemap)"},
    {"key": "contact_email",    "value": "",                 "label": "Contact Email"},
    {"key": "contact_phone",    "value": "",                 "label": "Contact Phone"},
    {"key": "contact_address",  "value": "",                 "label": "Contact Address"},
    {"key": "footer_copyright", "value": "© {year} LeadAI. All rights reserved.", "label": "Footer Copyright ({year} = current year)"},
    {"key": "social_twitter",   "value": "",                 "label": "X / Twitter URL"},
    {"key": "social_linkedin",  "value": "",                 "label": "LinkedIn URL"},
    {"key": "social_facebook",  "value": "",                 "label": "Facebook URL"},
    {"key": "social_instagram", "value": "",                 "label": "Instagram URL"},
    {"key": "social_youtube",   "value": "",                 "label": "YouTube URL"},
    {"key": "logo_url",         "value": "",                 "label": "Logo URL"},
    {"key": "favicon_url",      "value": "",                 "label": "Favicon URL"},
    {"key": "og_image",         "value": "",                 "label": "Default OG Image"},
    {"key": "primary_color",    "value": "#8b5cf6",          "label": "Primary Color"},
    {"key": "accent_color",     "value": "#a78bfa",          "label": "Accent Color"},
    {"key": "cookie_notice",    "value": "We use cookies to keep you signed in and to improve your experience.", "label": "Cookie Notice"},
    {"key": "announcement_enabled",    "value": False, "label": "Announcement Bar Enabled"},
    {"key": "announcement_text",       "value": "New: AI lead scoring now covers LinkedIn company pages.", "label": "Announcement Text"},
    {"key": "announcement_link_label", "value": "Learn more", "label": "Announcement Link Label"},
    {"key": "announcement_link_url",   "value": "/features",  "label": "Announcement Link URL"},
    {"key": "ga_id",            "value": "",                 "label": "Google Analytics ID"},
]

# Setting value kinds (validation in the service)
SETTING_KINDS = {
    "announcement_enabled": "bool",
    "site_url": "url", "social_twitter": "url", "social_linkedin": "url",
    "social_facebook": "url", "social_instagram": "url", "social_youtube": "url",
    "logo_url": "url", "favicon_url": "url", "og_image": "url",
    "announcement_link_url": "url",
    "contact_email": "email",
    "primary_color": "color", "accent_color": "color",
}

# Header + footer navigation. Footer items carry a ``group`` (footer column).
DEFAULT_NAVIGATION = {
    "header": [
        {"label": "Features", "url": "/features"},
        {"label": "How it works", "url": "/how-it-works"},
        {"label": "Pricing", "url": "/pricing"},
        {"label": "FAQ", "url": "/faq"},
        {"label": "About", "url": "/about"},
        {"label": "Contact", "url": "/contact"},
    ],
    "footer": [
        {"label": "Features", "url": "/features", "group": "Product"},
        {"label": "How it works", "url": "/how-it-works", "group": "Product"},
        {"label": "Pricing", "url": "/pricing", "group": "Product"},
        {"label": "FAQ", "url": "/faq", "group": "Product"},
        {"label": "About", "url": "/about", "group": "Company"},
        {"label": "Contact", "url": "/contact", "group": "Company"},
        {"label": "Sign in", "url": "/login", "group": "Account"},
        {"label": "Request a demo", "url": "/request-demo", "group": "Account"},
        {"label": "Demo request status", "url": "/demo-pending", "group": "Account"},
        {"label": "Forgot password", "url": "/forgot-password", "group": "Account"},
        {"label": "Privacy Policy", "url": "/privacy", "group": "Legal"},
        {"label": "Terms of Service", "url": "/terms", "group": "Legal"},
        {"label": "Cookie Policy", "url": "/cookies", "group": "Legal"},
    ],
}


async def ensure_cms_indexes(db) -> None:
    """Create MongoDB indexes for CMS collections. Called from lifespan."""
    import logging
    log = logging.getLogger(__name__)
    for coll_name, keys, kwargs in CMS_INDEXES:
        try:
            await db[coll_name].create_index(keys, **kwargs)
        except Exception as e:
            log.warning("CMS index on %s failed: %s", coll_name, e)


async def _seed_state(db, key: str) -> List[str]:
    doc = await db[COLL_SEED_STATE].find_one({"_id": key})
    return list((doc or {}).get("done", []))


async def _mark_seeded(db, key: str, names: List[str]) -> None:
    if names:
        await db[COLL_SEED_STATE].update_one(
            {"_id": key}, {"$addToSet": {"done": {"$each": names}}}, upsert=True)


def _is_untouched_seed(doc: Dict[str, Any]) -> bool:
    """A page still exactly as an older seed left it (never edited/published)."""
    return (not doc.get("sections") and doc.get("created_by") == "system"
            and not doc.get("updated_by") and int(doc.get("version") or 1) <= 1
            and not doc.get("live"))


async def seed_cms_defaults(db) -> None:
    """Seed default pages, FAQ, navigation and settings.

    Each default is created ONCE: a ledger in ``website_seed_state`` records
    what was seeded, so an operator who edits or deletes a default page or
    menu never sees it come back or get overwritten."""
    import copy
    import logging
    log = logging.getLogger(__name__)
    now = utcnow()

    # Pages — per slug, once
    try:
        done = set(await _seed_state(db, "pages"))
        seeded: List[str] = []
        for spec in DEFAULT_PAGES:
            slug = spec["slug"]
            existing = await db[COLL_PAGES].find_one({"slug": slug})
            if existing is None:
                if slug in done:
                    continue            # operator deleted it — respect that
                page = copy.deepcopy(spec)
                page.update({"status": "published", "has_draft_changes": False,
                             "version": 1, "versions": [], "created_at": now,
                             "updated_at": now, "published_at": now, "created_by": "system",
                             "live": {"title": page["title"], "seo": copy.deepcopy(page["seo"]),
                                      "sections": copy.deepcopy(page["sections"])}})
                await db[COLL_PAGES].insert_one(page)
                seeded.append(slug)
            elif slug not in done:
                # Page from an older seed without content: backfill sections
                # only while it is still untouched.
                if spec["sections"] and _is_untouched_seed(existing):
                    secs = copy.deepcopy(spec["sections"])
                    live = {"title": existing.get("title") or spec["title"],
                            "seo": existing.get("seo") or copy.deepcopy(spec["seo"]),
                            "sections": copy.deepcopy(secs)}
                    upd: Dict[str, Any] = {"sections": secs, "has_draft_changes": False}
                    if existing.get("status") == "published":
                        upd["live"] = live
                    await db[COLL_PAGES].update_one(
                        {"_id": existing["_id"], "sections": existing.get("sections", [])},
                        {"$set": upd})
                seeded.append(slug)
        await _mark_seeded(db, "pages", seeded)
        if seeded:
            log.info("CMS: seeded pages %s", seeded)
    except Exception as e:
        log.warning("CMS: page seed failed: %s", e)

    # FAQ — only into an empty collection, once
    try:
        if "faq" not in await _seed_state(db, "collections"):
            if await db[COLL_FAQ].count_documents({}) == 0:
                await db[COLL_FAQ].insert_many(
                    [{**f, "created_at": now, "updated_at": now} for f in DEFAULT_FAQ])
                log.info("CMS: seeded %d default FAQ items", len(DEFAULT_FAQ))
            await _mark_seeded(db, "collections", ["faq"])
    except Exception as e:
        log.warning("CMS: FAQ seed failed: %s", e)

    # Navigation — per location, only when that menu is empty, once
    try:
        done = set(await _seed_state(db, "navigation"))
        marked: List[str] = []
        for location, items in DEFAULT_NAVIGATION.items():
            if location in done:
                continue
            if await db[COLL_NAVIGATION].count_documents({"location": location}) == 0:
                await db[COLL_NAVIGATION].insert_many([
                    {"location": location, "label": it["label"], "url": it["url"],
                     "group": it.get("group", ""), "target": "_self", "order": i,
                     "enabled": True, "created_at": now}
                    for i, it in enumerate(items)])
            marked.append(location)
        await _mark_seeded(db, "navigation", marked)
    except Exception as e:
        log.warning("CMS: navigation seed failed: %s", e)

    # Website settings — $setOnInsert never touches an existing value
    for setting in DEFAULT_WEBSITE_SETTINGS:
        try:
            await db[COLL_SETTINGS].update_one(
                {"key": setting["key"]},
                {"$setOnInsert": {**setting, "created_at": now, "updated_at": now}},
                upsert=True,
            )
        except Exception as e:
            log.warning("CMS: settings seed failed for %s: %s", setting["key"], e)
