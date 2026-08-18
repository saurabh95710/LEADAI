"""
Comment Scraping & Keyword Intelligence — the first filtering layer of the
LeadAI pipeline.

Pipeline position (keywords are the FIRST layer; AI remains responsible for
deeper understanding):

    Apify / Platform
        -> Comment Scraper
        -> Raw Comments
        -> Keyword / Category Filter   <-- this module
        -> AI Comment Intelligence
        -> Intent Detection -> Lead Scoring -> Lead Creation -> Analytics

Modes
-----
* NO FILTER  — no keywords, no categories, no rules: every comment is
  processed (status ``NO_FILTER``). This is the default and preserves the
  legacy behavior of the application exactly.
* KEYWORD / CATEGORY / ADVANCED RULES — only comments matching the configured
  rules are forwarded to AI analysis (status ``MATCHED``); the rest stay
  stored untouched (status ``NOT_MATCHED``) so rules can be changed later and
  comments reprocessed without scraping again.

The original scraped comments are NEVER deleted by the filter. Matching only
decides whether a comment enters the lead-processing pipeline.

Matching semantics
------------------
* case-insensitive (both sides normalized to lowercase)
* Unicode normalization (NFKC) + whitespace/punctuation normalization
* phrase matching (multi-word keywords = normalized substring)
* word-boundary matching for short keywords (<4 chars) so "car" never
  matches "career"
* prefix matching for longer keywords so "price" matches "prices"
* Devanagari (Hindi) keywords match natively after normalization
"""
import logging
import re
import time
import unicodedata
from typing import Any, Dict, List, Optional

from app.db.models import utcnow

logger = logging.getLogger(__name__)

# Status values stored on every processed comment (and in comment_filter_results)
STATUS_MATCHED = "MATCHED"
STATUS_NOT_MATCHED = "NOT_MATCHED"
STATUS_NO_FILTER = "NO_FILTER"

MATCH_MODES = ("any", "all", "category", "advanced")

# ─────────────────────────────────────────────────────────────────────────────
# Predefined business categories — keywords curated per the product spec
# ─────────────────────────────────────────────────────────────────────────────

CATEGORIES: Dict[str, Dict[str, Any]] = {
    "real_estate": {
        "name": "Real Estate",
        "icon": "🏠",
        "description": "Property, housing, rentals, site visits, financing",
        "keywords": [
            "property", "house", "home", "apartment", "flat", "villa", "plot",
            "land", "property price", "house price", "apartment price", "rent",
            "rental", "lease", "booking", "possession", "investment",
            "site visit", "visit", "location", "address", "brochure",
            "floor plan", "amenities", "available", "availability",
            "interested", "details", "price", "cost", "budget",
            "down payment", "emi", "loan", "mortgage", "resale",
            "commercial property", "office", "shop", "warehouse", "builder",
            "developer", "agent", "broker", "dealer",
        ],
    },
    "automotive": {
        "name": "Automotive",
        "icon": "🚗",
        "description": "Cars, bikes, test drives, finance, exchange",
        "keywords": [
            "car", "vehicle", "automobile", "bike", "motorcycle", "scooter",
            "suv", "sedan", "hatchback", "ev", "electric vehicle", "used car",
            "second hand", "new car", "test drive", "price", "cost", "mileage",
            "booking", "available", "availability", "finance", "emi", "loan",
            "down payment", "dealer", "showroom", "service", "insurance",
            "exchange", "trade in", "interested", "details",
        ],
    },
    "education": {
        "name": "Education",
        "icon": "🎓",
        "description": "Admissions, courses, coaching, fees, enrollment",
        "keywords": [
            "admission", "admissions", "course", "courses", "college",
            "university", "school", "coaching", "institute", "class",
            "classes", "fees", "fee", "tuition", "syllabus", "enrollment",
            "registration", "batch", "online class", "offline class",
            "certification", "certificate", "diploma", "degree", "scholarship",
            "placement", "eligibility", "entrance", "exam", "coaching fees",
            "demo", "trial class", "interested", "details",
        ],
    },
    "travel": {
        "name": "Travel & Tourism",
        "icon": "✈️",
        "description": "Trips, packages, hotels, visas, sightseeing",
        "keywords": [
            "trip", "tour", "travel", "package", "holiday", "vacation",
            "destination", "hotel", "resort", "booking", "availability",
            "itinerary", "price", "cost", "budget", "flight", "transport",
            "cab", "pickup", "sightseeing", "honeymoon", "family trip",
            "group trip", "visa", "passport", "accommodation", "room",
            "interested", "details",
        ],
    },
    "healthcare": {
        "name": "Healthcare",
        "icon": "🩺",
        "description": "Doctors, clinics, consultations, treatments",
        "keywords": [
            "doctor", "clinic", "hospital", "treatment", "appointment",
            "consultation", "specialist", "surgery", "therapy", "medicine",
            "test", "diagnosis", "procedure", "treatment cost",
            "consultation fee", "appointment available", "availability",
            "booking", "report", "patient", "interested", "details",
        ],
        "disclaimer": ("This feature only detects potential business inquiries. "
                       "It never makes medical recommendations."),
    },
    "ecommerce": {
        "name": "E-commerce / Retail",
        "icon": "🛒",
        "description": "Products, orders, delivery, discounts, returns",
        "keywords": [
            "price", "cost", "buy", "purchase", "order", "available",
            "availability", "stock", "size", "color", "delivery", "shipping",
            "cod", "cash on delivery", "discount", "offer", "coupon",
            "warranty", "return", "exchange", "product", "catalogue",
            "catalog", "link", "interested", "details",
        ],
    },
    "home_services": {
        "name": "Home Services",
        "icon": "🔧",
        "description": "Plumbing, electrical, repair, renovation, interiors",
        "keywords": [
            "plumber", "plumbing", "electrician", "electrical", "cleaning",
            "repair", "renovation", "interior", "painting", "carpenter",
            "furniture", "ac service", "appliance repair", "installation",
            "maintenance", "quotation", "quote", "estimate", "price", "cost",
            "booking", "availability", "service area", "interested", "details",
        ],
    },
    "financial": {
        "name": "Financial Services",
        "icon": "💳",
        "description": "Loans, EMI, insurance, investments, eligibility",
        "keywords": [
            "loan", "personal loan", "home loan", "business loan", "car loan",
            "mortgage", "emi", "interest rate", "credit", "finance",
            "insurance", "policy", "premium", "investment", "mutual fund",
            "financial planning", "eligibility", "documents", "application",
            "approval", "processing fee", "consultation", "interested",
            "details",
        ],
        "disclaimer": ("This only identifies commercial/business inquiry comments. "
                       "It never generates financial advice."),
    },
    "b2b": {
        "name": "B2B / Business Services",
        "icon": "🏢",
        "description": "Software, SaaS, quotes, partnerships, wholesale",
        "keywords": [
            "business", "company", "service", "solution", "software", "saas",
            "enterprise", "quotation", "quote", "pricing", "demo", "trial",
            "partnership", "reseller", "distributor", "wholesale", "supplier",
            "vendor", "agency", "consultation", "proposal", "contract",
            "bulk order", "requirement", "interested", "contact", "details",
        ],
    },
    "jobs": {
        "name": "Jobs / Recruitment",
        "icon": "💼",
        "description": "Vacancies, hiring, interviews, applications",
        "keywords": [
            "job", "vacancy", "hiring", "hiring now", "recruitment",
            "recruiter", "career", "opening", "position", "employment",
            "salary", "role", "interview", "apply", "resume", "cv",
            "qualification", "experience", "internship", "work from home",
            "remote job", "joining",
        ],
        # Job comments are NOT necessarily sales leads; the admin decides how
        # this category maps: sales_lead | recruitment_lead | ignore
        "intent_type": "ignore",
        "intent_types": ("sales_lead", "recruitment_lead", "ignore"),
    },
    "restaurants": {
        "name": "Restaurant / Food",
        "icon": "🍽️",
        "description": "Menus, reservations, delivery, catering, events",
        "keywords": [
            "menu", "price", "food", "restaurant", "booking", "reservation",
            "table", "available", "delivery", "takeaway", "catering", "order",
            "location", "address", "timing", "opening", "closing", "discount",
            "offer", "party", "event", "birthday", "wedding",
            "catering price", "interested", "details",
        ],
    },
    "events": {
        "name": "Event / Wedding",
        "icon": "🎉",
        "description": "Weddings, venues, decor, photography, planning",
        "keywords": [
            "wedding", "event", "venue", "hall", "booking", "decoration",
            "catering", "photographer", "photography", "videography", "dj",
            "makeup", "planner", "package", "price", "quotation",
            "availability", "date", "budget", "guest", "reservation",
            "interested", "details",
        ],
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# Cross-category presets (available inside any rule)
# ─────────────────────────────────────────────────────────────────────────────

PRESETS: Dict[str, Dict[str, Any]] = {
    "contact_signals": {
        "name": "Contact Intent Signals",
        "icon": "📞",
        "description": "Comments that ask for or provide contact details",
        "keywords": [
            "phone", "mobile", "whatsapp", "email", "dm", "inbox", "contact",
            "call me", "message me", "send number", "number please",
            "whatsapp me", "email me", "contact details",
        ],
        # regex contact extraction is used in addition to the keywords:
        # actual phone numbers / email addresses in the comment text also match
        "detect_contacts": True,
    },
    "high_intent": {
        "name": "High Purchase Intent",
        "icon": "🔥",
        "description": "Strong buying signals — works across every category",
        "keywords": [
            "interested", "very interested", "want to buy", "looking to buy",
            "need this", "available?", "price?", "cost?", "how much", "book",
            "booking", "reserve", "purchase", "buy", "order", "quotation",
            "quote", "demo", "schedule", "appointment", "call me",
            "contact me", "details please", "send details", "dm me",
            "whatsapp me",
        ],
    },
    "info_request": {
        "name": "Information Request",
        "icon": "ℹ️",
        "description": "Information seekers (separate from high-intent buyers)",
        "keywords": [
            "what is", "how does", "details", "information", "explain",
            "tell me", "where", "when", "timing", "location", "address",
            "specifications",
        ],
    },
    "noise_spam": {
        "name": "Noise / Spam",
        "icon": "🚫",
        "description": ("Not excluded globally — use it in your EXCLUDE list "
                        "only when it makes sense for your business"),
        "keywords": [
            "spam", "giveaway", "congratulations", "follow back",
            "follow for follow", "f4f", "promotion", "advertise",
            "advertisement", "dm for promotion", "crypto promotion",
            "betting", "free followers",
        ],
    },
}

CATEGORY_KEYS = tuple(CATEGORIES.keys())
PRESET_KEYS = tuple(PRESETS.keys())

# Hindi / Hinglish suggestions shown when the admin picks a category
MULTILINGUAL_KEYWORDS: Dict[str, Dict[str, List[str]]] = {
    "real_estate": {
        "hi": ["कीमत", "प्रॉपर्टी", "मकान", "जमीन", "किराया", "फ्लैट", "प्लॉट"],
        "hinglish": ["kitne ka", "kitna price", "property chahiye",
                     "ghar chahiye", "plot chahiye", "rent kitna hai",
                     "location kaha hai", "details bhejo", "interested hu"],
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# Normalization + matching
# ─────────────────────────────────────────────────────────────────────────────

_DEVA_RANGE = "\u0900-\u097F"
# any unicode word char OR devanagari (kept explicit even though \w already
# covers them — the intent is documented here for reviewers)
_NON_WORD_RE = re.compile(rf"[^\w{_DEVA_RANGE}\s]+", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Canonical form used for matching: NFKC, lowercase, punctuation and
    stray whitespace collapsed. Phone digits/emails survive (they are word
    chars) — the AI stage still handles contact extraction."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", str(text))
    text = text.lower()
    text = _NON_WORD_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def normalize_keyword(keyword: str) -> str:
    """Canonical keyword form (also used at save time by the API)."""
    return normalize_text(keyword)


def normalize_keyword_list(value: Any) -> List[str]:
    """Sanitize a raw keyword list (str with commas, or a list): normalized,
    deduplicated, capped at 300 entries of max 80 chars each."""
    if not value:
        return []
    if isinstance(value, str):
        value = value.split(",")
    out: List[str] = []
    for raw in value:
        kw = normalize_keyword(str(raw))
        if kw and len(kw) <= 80 and kw not in out:
            out.append(kw)
        if len(out) >= 300:
            break
    return out


def keyword_matches(normalized_text: str, keyword: str) -> bool:
    """
    Robust keyword matching against a normalized comment:
      * phrases (multi-word)  -> normalized substring
      * short single words (<4 chars, e.g. "car", "ev") -> strict word
        boundaries both sides, so "car" never matches "career"
      * longer single words   -> start-boundary prefix match, so "price"
        matches "price", "prices", "pricing" — but never "pricey-fake" inside
        another word (e.g. "resprice" is not matched)
      * Devanagari keywords   -> word boundaries via the word-char class
    """
    kw = normalize_keyword(keyword)
    if not kw or not normalized_text:
        return False
    if " " in kw:
        return kw in normalized_text
    if len(kw) < 4:
        return re.search(rf"(?<!\w){re.escape(kw)}(?!\w)", normalized_text) is not None
    return re.search(rf"(?<!\w){re.escape(kw)}", normalized_text) is not None


# Phone / email regexes reused from comment_ai (contact-signal preset)
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s\-.]?)?\d{5}[\s\-.]?\d{5}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _contains_contact_signal(text: str) -> bool:
    return bool(_PHONE_RE.search(text) or _EMAIL_RE.search(text))


# ─────────────────────────────────────────────────────────────────────────────
# Rule evaluation
# ─────────────────────────────────────────────────────────────────────────────

def _expand_categories(rule: Dict[str, Any],
                       custom_categories: Optional[List[Dict[str, Any]]] = None
                       ) -> Dict[str, List[str]]:
    """Map selected category keys -> their keyword lists. Custom categories
    (from the comment_categories collection) are resolved by their `_id`
    string or `key`; built-in presets expand from the PRESETS table."""
    out: Dict[str, List[str]] = {}
    selected = rule.get("categories") or []
    if isinstance(selected, str):
        selected = [selected]
    for key in selected:
        key = str(key)
        if key in CATEGORIES:
            out[key] = list(CATEGORIES[key]["keywords"])
        elif key in PRESETS:
            out[key] = list(PRESETS[key]["keywords"])
        else:
            for custom in (custom_categories or []):
                if str(custom.get("_id")) == key or custom.get("key") == key:
                    out[key] = list(custom.get("keywords") or [])
    return out


def _rule_keywords(rule: Dict[str, Any],
                   custom_categories: Optional[List[Dict[str, Any]]] = None
                   ) -> List[str]:
    """Full include-keyword pool: explicit keywords + selected categories."""
    keywords = list(rule.get("include_keywords") or [])
    for _, kws in _expand_categories(rule, custom_categories).items():
        for kw in kws:
            if kw not in keywords:
                keywords.append(kw)
    return keywords


def _matched_keywords_in(normalized: str, keywords: List[str]) -> List[str]:
    return [kw for kw in keywords if keyword_matches(normalized, kw)]


def _detect_contacts_flag(rule: Dict[str, Any]) -> bool:
    """True when the rule (or one of its selected presets) requests regex
    contact detection on top of the keyword list."""
    if rule.get("detect_contacts"):
        return True
    for key in (rule.get("categories") or []):
        preset = PRESETS.get(str(key))
        if preset and preset.get("detect_contacts"):
            return True
    return False


def evaluate_rule(text: Optional[str], rule: Dict[str, Any],
                  custom_categories: Optional[List[Dict[str, Any]]] = None
                  ) -> Dict[str, Any]:
    """
    Evaluate one comment against one rule. Returns the canonical result:

        status             MATCHED | NOT_MATCHED | NO_FILTER
        matched_keywords   [..]
        matched_categories [..]
        excluded_keywords  [..]
        filter_score       100 (pass) | 0 (fail)
        filter_timestamp   iso string

    NO_FILTER: the rule is empty (no keywords, no categories, no groups) —
    every comment passes. This is the default configuration.
    """
    empty = {
        "status": STATUS_NO_FILTER,
        "matched_keywords": [],
        "matched_categories": [],
        "excluded_keywords": [],
        "filter_score": 100,
        "filter_timestamp": utcnow().isoformat(),
    }
    if not rule:
        return empty

    include = _rule_keywords(rule, custom_categories)
    exclude = list(rule.get("exclude_keywords") or [])
    groups = rule.get("groups") or []

    if not include and not exclude and not groups:
        return empty

    if not text or not text.strip():
        return {
            **empty,
            "status": STATUS_NOT_MATCHED,
            "filter_score": 0,
            "excluded_keywords": [],
        }

    normalized = normalize_text(text)

    # 1. excludes always veto (include+exclude semantics)
    excluded = [kw for kw in exclude if keyword_matches(normalized, kw)]
    if excluded:
        return {
            "status": STATUS_NOT_MATCHED,
            "matched_keywords": [],
            "matched_categories": [],
            "excluded_keywords": excluded,
            "filter_score": 0,
            "filter_timestamp": utcnow().isoformat(),
        }

    # 2. regex contact signals (optional, from the contact-signals preset)
    if _detect_contacts_flag(rule) and _contains_contact_signal(text):
        return {
            "status": STATUS_MATCHED,
            "matched_keywords": [kw for kw in include
                                 if keyword_matches(normalized, kw)],
            "matched_categories": [],
            "excluded_keywords": [],
            "filter_score": 100,
            "filter_timestamp": utcnow().isoformat(),
            "contact_signal": True,
        }

    # 3. match mode
    mode = rule.get("match_mode") or "any"
    matched: List[str] = []

    if mode == "all":
        matched = [kw for kw in include if keyword_matches(normalized, kw)]
        matched_all = len(matched) == len(include) and len(include) > 0
    elif mode == "category":
        # any keyword from the selected categories (categories contribute to
        # the pool anyway; this mode simply means "category-driven only")
        matched = [kw for kw in include if keyword_matches(normalized, kw)]
        matched_all = bool(matched)
    elif mode == "advanced":
        # groups: each group is OR'd internally; groups combine with the
        # rule's group_operator ("and" default, "or" allowed)
        if not groups:
            matched = [kw for kw in include if keyword_matches(normalized, kw)]
            matched_all = bool(matched)
        else:
            group_hits: List[str] = []
            group_ok = []
            for group in groups:
                kws = [g for g in (group or []) if g]
                hits = [kw for kw in kws if keyword_matches(normalized, kw)]
                group_hits.extend(hits)
                group_ok.append(bool(hits))
            operator = (rule.get("group_operator") or "and").lower()
            all_ok = all(group_ok) if operator != "or" else any(group_ok)
            matched = group_hits
            matched_all = all_ok and bool(groups)
    else:  # any (default)
        matched = [kw for kw in include if keyword_matches(normalized, kw)]
        matched_all = bool(matched)

    # explicit keywords always apply on top of category keywords — a comment
    # matching ANY include keyword (any mode) or ALL (all mode) is relevant
    if not matched_all:
        return {
            "status": STATUS_NOT_MATCHED,
            "matched_keywords": matched,
            "matched_categories": [],
            "excluded_keywords": [],
            "filter_score": 0,
            "filter_timestamp": utcnow().isoformat(),
        }

    # attribution: which selected categories contain the matched keywords
    matched_categories: List[str] = []
    pool = _expand_categories(rule, custom_categories)
    for key, kws in pool.items():
        if any(m in kws for m in matched):
            matched_categories.append(key)
    return {
        "status": STATUS_MATCHED,
        "matched_keywords": matched,
        "matched_categories": matched_categories,
        "excluded_keywords": [],
        "filter_score": 100,
        "filter_timestamp": utcnow().isoformat(),
    }


def rule_is_empty(rule: Dict[str, Any]) -> bool:
    """True when the rule carries no filtering configuration at all (the
    NO_FILTER / "all comments" state)."""
    return not (rule.get("include_keywords") or rule.get("exclude_keywords")
                or rule.get("categories") or rule.get("groups"))


# ─────────────────────────────────────────────────────────────────────────────
# Persistence helpers (used by the scraping pipeline and the API)
# ─────────────────────────────────────────────────────────────────────────────

RULES_COLLECTION = "comment_filter_rules"
PRESETS_COLLECTION = "comment_filter_presets"
RESULTS_COLLECTION = "comment_filter_results"
CATEGORIES_COLLECTION = "comment_categories"


def normalize_rule_payload(body: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize an admin-submitted rule config: keyword lists are normalized,
    deduplicated, capped, and invalid modes are coerced to defaults."""
    def clean_keywords(value: Any) -> List[str]:
        if not value:
            return []
        if isinstance(value, str):
            value = value.split(",")
        out: List[str] = []
        for raw in value:
            kw = normalize_keyword(str(raw))
            if kw and len(kw) <= 80 and kw not in out:
                out.append(kw)
        return out[:300]

    def clean_categories(value: Any) -> List[str]:
        if not value:
            return []
        if isinstance(value, str):
            value = value.split(",")
        out: List[str] = []
        for raw in value:
            key = str(raw).strip()
            if key and key not in out:
                out.append(key)
        return out[:50]

    mode = str(body.get("match_mode") or "any").lower()
    if mode not in MATCH_MODES:
        mode = "any"

    groups = body.get("groups") or []
    clean_groups: List[List[str]] = []
    if isinstance(groups, list):
        for group in groups:
            if isinstance(group, list):
                clean_groups.append(clean_keywords(group))
    clean_groups = [g for g in clean_groups if g]

    return {
        "name": str(body.get("name") or "Untitled rule").strip()[:120],
        "description": str(body.get("description") or "").strip()[:500],
        "platform": str(body.get("platform") or "all").strip().lower(),
        "business_category": str(body.get("business_category") or "").strip(),
        "categories": clean_categories(body.get("categories")),
        "include_keywords": clean_keywords(body.get("include_keywords")),
        "exclude_keywords": clean_keywords(body.get("exclude_keywords")),
        "match_mode": mode,
        "group_operator": "or" if str(body.get("group_operator")
                                      or "and").lower() == "or" else "and",
        "groups": clean_groups,
        "language": str(body.get("language") or "all").strip().lower()[:20],
        "detect_contacts": bool(body.get("detect_contacts")),
        "intent_type": str(body.get("intent_type") or "").strip()[:30],
    }


def rule_config(rule: Dict[str, Any]) -> Dict[str, Any]:
    """Executable config view of a stored rule doc."""
    return {
        "name": rule.get("name", ""),
        "description": rule.get("description", ""),
        "platform": rule.get("platform", "all"),
        "business_category": rule.get("business_category", ""),
        "categories": rule.get("categories") or [],
        "include_keywords": rule.get("include_keywords") or [],
        "exclude_keywords": rule.get("exclude_keywords") or [],
        "match_mode": rule.get("match_mode", "any"),
        "group_operator": rule.get("group_operator", "and"),
        "groups": rule.get("groups") or [],
        "language": rule.get("language", "all"),
        "detect_contacts": bool(rule.get("detect_contacts")),
        "intent_type": rule.get("intent_type", ""),
    }


def load_rule(db, rule_id: str) -> Optional[Dict[str, Any]]:
    """Load one stored rule (or None)."""
    from bson import ObjectId
    try:
        return db[RULES_COLLECTION].find_one({"_id": ObjectId(rule_id)})
    except Exception:
        return None


def load_active_rule(db) -> Optional[Dict[str, Any]]:
    """The single active rule (admin activation), or None."""
    return db[RULES_COLLECTION].find_one({"active": True})


def build_inline_rule(config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Ephemeral rule built from a run's inline comment_filter config (the
    user-app URL search form). Accepts a preset key, custom keywords and/or
    category keys. Returns None when the config is empty (NO_FILTER)."""
    if not config:
        return None
    keywords: List[str] = []
    categories: List[str] = []
    detect_contacts = False

    preset_key = config.get("preset")
    if preset_key and preset_key in PRESETS:
        preset = PRESETS[preset_key]
        keywords.extend(preset["keywords"])
        detect_contacts = bool(preset.get("detect_contacts"))

    for raw in (config.get("categories") or []):
        key = str(raw)
        if key in CATEGORIES:
            categories.append(key)
            keywords.extend(CATEGORIES[key]["keywords"])
        elif key in PRESETS:
            keywords.extend(PRESETS[key]["keywords"])
            detect_contacts = detect_contacts or bool(
                PRESETS[key].get("detect_contacts"))

    include = normalize_keyword_list(config.get("include_keywords"))
    for kw in include:
        if kw not in keywords:
            keywords.append(kw)

    if not keywords and not config.get("exclude_keywords"):
        return None

    mode = str(config.get("match_mode") or "any").lower()
    if mode not in MATCH_MODES:
        mode = "any"

    return {
        "_id": f"inline:{config.get('_run_id') or 'run'}",
        "name": config.get("name") or "Run inline filter",
        "description": "Configured from the URL search form",
        "platform": str(config.get("platform") or "all").strip().lower(),
        "business_category": "",
        "categories": categories,
        "include_keywords": keywords,
        "exclude_keywords": normalize_keyword_list(
            config.get("exclude_keywords")),
        "match_mode": mode,
        "group_operator": "and",
        "groups": [],
        "language": str(config.get("language") or "all").strip().lower()[:20],
        "detect_contacts": detect_contacts,
        "intent_type": "",
    }


def resolve_effective_rule(db, run_doc: Optional[Dict[str, Any]] = None
                           ) -> Optional[Dict[str, Any]]:
    """
    Effective rule for a scrape run:
      1. explicit rule reference stored on the run (preset or rule)
      2. inline run configuration (keywords/categories from the search form)
      3. the globally active rule (activated from the admin panel)
      4. None  -> NO FILTER (all comments), the default

    A run that explicitly references a rule uses it even when the rule is not
    the active one; a run with no configuration follows the active rule.
    """
    if run_doc:
        cf_cfg = run_doc.get("comment_filter") or {}
        rule_id = cf_cfg.get("rule_id")
        if rule_id:
            rule = load_rule(db, str(rule_id))
            if rule:
                return rule
        inline = build_inline_rule(cf_cfg)
        if inline:
            return inline
    return load_active_rule(db)


def store_filter_result(db, comment_id: str, result: Dict[str, Any],
                        rule: Optional[Dict[str, Any]] = None,
                        post_ref: Optional[str] = None,
                        search_run_id: Optional[str] = None,
                        platform: Optional[str] = None) -> None:
    """Persist one comment's filter result (comment_filter_results) and mirror
    the compact fields onto the comment doc for the Comment Intelligence UI."""
    rule_id = str(rule["_id"]) if rule else None
    try:
        db[RESULTS_COLLECTION].update_one(
            {"comment_id": comment_id},
            {"$set": {
                "comment_id": comment_id,
                "rule_id": rule_id,
                "status": result["status"],
                "matched_keywords": result.get("matched_keywords") or [],
                "matched_categories": result.get("matched_categories") or [],
                "excluded_keywords": result.get("excluded_keywords") or [],
                "filter_score": result.get("filter_score", 0),
                "processed_at": time.time(),
                "post_ref": post_ref,
                "search_run_id": search_run_id,
                "platform": platform,
            }},
            upsert=True,
        )
    except Exception as e:
        logger.warning("[CommentFilter] failed to store result: %s", e)

    compact = {
        "keyword_filter_status": result["status"],
        "matched_keywords": result.get("matched_keywords") or [],
        "matched_categories": result.get("matched_categories") or [],
        "excluded_keywords": result.get("excluded_keywords") or [],
        "filter_score": result.get("filter_score", 0),
        "filter_rule_id": rule_id,
        "filter_timestamp": result.get("filter_timestamp"),
    }
    try:
        db.facebook_comments.update_one(
            {"_id": _as_oid(comment_id)}, {"$set": compact})
    except Exception as e:
        logger.warning("[CommentFilter] failed to update comment doc: %s", e)


def _as_oid(value: str):
    from bson import ObjectId
    try:
        return ObjectId(value)
    except Exception:
        return value


def filter_comments_for_post(db, post_ref: str, rule: Dict[str, Any],
                             search_run_id: Optional[str] = None,
                             platform: Optional[str] = None
                             ) -> Dict[str, Any]:
    """
    Apply a rule to every stored comment of one post. Comments are NEVER
    deleted; each one gets a keyword filter status + the result row.

    Returns a summary with the refs of MATCHED comments (the ones the AI
    stage should analyze). With an empty rule every comment is NO_FILTER and
    ALL refs are returned (legacy behavior).
    """
    custom_categories = list(db[CATEGORIES_COLLECTION].find(
        {"active": {"$ne": False}}))

    summary = {
        "status": "completed",
        "rule_id": str(rule["_id"]) if rule.get("_id") else None,
        "total": 0, "matched": 0, "not_matched": 0, "no_filter": 0,
        "matched_refs": [],
    }

    query = {"post_ref": post_ref}
    try:
        comments = list(db.facebook_comments.find(
            query, {"_id": 1, "text": 1}))
    except Exception as e:
        logger.warning("[CommentFilter] load comments failed: %s", e)
        return {**summary, "status": "error", "error": str(e)}

    no_filter = rule_is_empty(rule)
    for doc in comments:
        text = doc.get("text") or ""
        if no_filter:
            result = evaluate_rule(text, {})
        else:
            result = evaluate_rule(text, rule, custom_categories)
        summary["total"] += 1
        if result["status"] == STATUS_MATCHED:
            summary["matched"] += 1
            summary["matched_refs"].append(str(doc["_id"]))
        elif result["status"] == STATUS_NOT_MATCHED:
            summary["not_matched"] += 1
        else:
            summary["no_filter"] += 1
            summary["matched_refs"].append(str(doc["_id"]))
        store_filter_result(db, str(doc["_id"]), result, rule,
                            post_ref=post_ref,
                            search_run_id=search_run_id,
                            platform=platform)
    return summary


def apply_filter_to_comment(db, comment_doc: Dict[str, Any], rule: Dict[str, Any],
                            post_ref: Optional[str] = None,
                            search_run_id: Optional[str] = None,
                            platform: Optional[str] = None) -> Dict[str, Any]:
    """Evaluate a rule for a single comment doc and persist the result."""
    custom_categories = list(db[CATEGORIES_COLLECTION].find(
        {"active": {"$ne": False}}))
    result = evaluate_rule(comment_doc.get("text") or "", rule,
                           custom_categories)
    store_filter_result(db, str(comment_doc["_id"]), result, rule,
                        post_ref=post_ref or comment_doc.get("post_ref"),
                        search_run_id=search_run_id
                        or comment_doc.get("search_run_id"),
                        platform=platform or comment_doc.get("platform"))
    return result