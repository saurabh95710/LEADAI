"""
AI Comment Analysis — the agent's only AI job on the collected data.

For every comment of a selected post:

  Stage 1 (rules, free, offline):
    - drops gracious filler ("nice", "awesome", "thank you"…), emoji-only,
      link-only and spam comments BEFORE any AI call
  Stage 2 (Gemini, only for comments that pass Stage 1):
    - extracts: phone, email, whatsapp, website, budget, requirement,
      location, intent, urgency, priority, lead_quality, confidence
    - Gemini NEVER fabricates — unknown values stay null, and comments
      with no lead signal are marked is_lead=false (hidden from the UI)

Persisted to the `ai_comments` collection (one doc per comment, unique on
comment_ref — re-runs upsert, never duplicate).
"""
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

import httpx

from app.config import get_settings
from app.db.models import utcnow

logger = logging.getLogger(__name__)
settings = get_settings()


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 1 — Rule-based filter (never calls the network)
# ─────────────────────────────────────────────────────────────────────────────

GRATITUDE_WORDS = {
    "nice", "awesome", "beautiful", "good", "wow", "thank", "thanks", "thnks",
    "thanx", "thanku", "thankyou", "amazing", "great", "excellent",
    "congratulations", "congrats", "congratulation", "congrtz", "cool", "superb",
    "super", "cute", "lovely", "love", "loved", "like", "liked", "likes", "lol",
    "hehe", "haha", "hmm", "ok", "okay", "kk", "yes", "no", "best", "fantastic",
    "gorgeous", "impressive", "wonderful", "sweet", "brilliant", "perfect",
    "stunning", "pretty", "terrific", "outstanding", "adorable",
    "well", "done", "job", "work", "post", "photo", "pic", "pics", "picture",
    "pictures", "video", "videos", "place", "keep", "sharing", "more", "such",
    "hats", "off", "big", "fan", "bro", "bhai", "ji", "mast", "mastt",
    "badhiya", "wah", "waah", "kya", "baat", "namaste", "namaskar",
    "dhanyawad", "shukriya", "goodmorning", "goodafternoon", "goodevening",
    "goodnight", "morning", "evening", "night", "jai", "mata", "di", "shree",
    "shri", "ram", "om", "sri", "akal", "sat", "waheguru", "guruji",
    "subhanallah", "mashaallah", "mashallah", "allah", "hu", "akbar",
    "barakallahu", "nicee", "greeat", "woww", "wowww", "superbb", "greatt",
    "so", "much", "very", "really", "this", "that", "it", "its", "them", "us",
    "is", "are", "was", "were", "to", "be", "for", "and", "the", "a", "an",
    "too", "also", "just", "please", "you", "your", "yours", "u", "me", "my",
}

SPAM_PATTERNS = [
    re.compile(r"\b(free money|earn (money|₹|rs|rupees)|cash prize|lottery|jackpot|"
               r"click (here|this)|subscribe( to)? (my )?(channel|page)|follow (me|@\w+)|"
               r"dm (me|@\w+)|hurry( ?up)? offer|risk ?free|guaranteed (income|return|profit)|"
               r"bitcoin|crypto giveaway|casino|betting|100% ?(profit|return)|"
               r"check (my )?(bio|profile|link)|visit (my )?(bio|profile|link)|"
               r"earn (in|up to))", re.IGNORECASE),
]

_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF"
    "\U0000FE0F\U00002700-\U000027BF\u2B50\u2764\u26A0\u26A1\u2705\u261D\u26C4]"
)
_ALNUM_RE = re.compile(r"[a-zA-Z0-9]")
_URL_ONLY_RE = re.compile(r"^\s*((?:https?://|www\.)\S+|\s+)*$")
_TOKEN_SPLIT_RE = re.compile(r"[^\w\u0900-\u097F]+", re.UNICODE)

_PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s\-.]?)?\d{5}[\s\-.]?\d{5}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# email providers written WITHOUT the @ — e.g. "abc.gmail.com", "mail me gmail.com"
_BARE_EMAIL_DOMAIN_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:gmail|yahoo|hotmail|outlook|rediffmail|aol|live|"
    r"ymail|icloud|protonmail|zoho|msn|mail)\s*\.\s*(?:com|in|co\.in|org|"
    r"net|co|me|uk)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_WHATSAPP_RE = re.compile(r"whatsapp|whats ?app|wa\.me", re.IGNORECASE)
_WHATSAPP_WITH_NUM_RE = re.compile(r"(?i)whatsapp[^\d]{0,12}(?:\+?91[\s\-.]?)?\d{5}[\s\-.]?\d{5}")
_BUDGET_RE = re.compile(
    r"(?:₹|rs\.?|rupees|inr|lakh|lac|crore|cr\.?)\s*\d+|\d+\s*(?:lakh|lac|crore|cr\.?)\b",
    re.IGNORECASE,
)
_REQUIREMENT_WORDS = [
    "bhk", "flat", "apartment", "villa", "plot", "land", "shop", "office",
    "showroom", "property", "house", "ghar", "bungalow", "banglow",
    "builder floor", "penthouse", "commercial", "retail", "warehouse",
    "factory", "farmhouse", "studio", "need", "want", "looking", "chahiye",
    "required", "require", "searching", "interested in",
]
_CITY_WORDS = [
    "noida", "gurgaon", "gurugram", "delhi", "new delhi", "mumbai",
    "jaipur", "pune", "bangalore", "bengaluru", "hyderabad", "chennai",
    "kolkata", "indore", "lucknow", "chandigarh", "ahmedabad", "nagpur",
    "ghaziabad", "faridabad", "dehradun", "ranchi", "patna", "bhopal",
    "surat", "kochi", "goa", "agra", "kanpur", "varanasi", "thane",
    "jodhpur", "udaipur", "kota", "bikaner", "ajmer", "alwar", "bhiwadi",
    "vadodara", "rajkot", "mysore", "coimbatore", "madurai", "vizag",
    "visakhapatnam", "bhubaneswar", "panvel",
]
_INQUIRY_WORDS = [
    "price", "rate", "cost", "quote", "available", "availability",
    "kitna", "kya", "kaise", "kiti", "how much", "interested",
    "interest", "want to know", "details", "brochure", "is this",
    "is it", "emi", "offer", "discount", "deal",
]
_URGENCY_WORDS = [
    "urgent", "asap", "immediately", "as soon as", "soon", "quickly",
    "jaldi", "jldi", "early", "hurry", "today", "this week",
    "right away", "immediate", "fast",
]
_CONTACT_REQUEST_RE = re.compile(
    r"call me|call at|call on|call: |contact me|contact at|contact on|"
    r"whatsapp me|whatsapp at|wa ?me|dm me|message me|text me|reach me|"
    r"reach out|share details|send details|please call|please contact|"
    r"get in touch|my number|my whatsapp|my email|mail me|ping me|"
    r"contact number|phone number",
    re.IGNORECASE,
)


def strip_emoji(text: str) -> str:
    if not text:
        return ""
    return _EMOJI_RE.sub("", text)


def has_contact_info(text: Optional[str]) -> bool:
    """True when the text contains a 10-digit phone number OR an email
    address (incl. bare provider domains like gmail.com). Each signal can
    be switched off by the admin (Comment Intelligence settings)."""
    if not text:
        return False
    from app.admin.settings import get_bool_cached
    phone = get_bool_cached("ci.detect_phone") and bool(_PHONE_RE.search(text))
    email = get_bool_cached("ci.detect_email") and bool(
        _EMAIL_RE.search(text) or _BARE_EMAIL_DOMAIN_RE.search(text))
    return bool(phone or email)


def extract_contact_quick(text: Optional[str]) -> Dict[str, Optional[str]]:
    """Fast regex extraction of phone/email/whatsapp for display without AI."""
    text = text or ""
    phone_m = _PHONE_RE.search(text)
    email_m = _EMAIL_RE.search(text)
    wa_m = _WHATSAPP_WITH_NUM_RE.search(text)
    bare_m = _BARE_EMAIL_DOMAIN_RE.search(text)
    return {
        "phone": phone_m.group(0).strip() if phone_m else None,
        "email": (email_m.group(0).strip() if email_m
                  else (bare_m.group(0).strip() if bare_m else None)),
        "whatsapp": wa_m.group(0).strip() if wa_m else None,
    }


def is_emoji_only(text: str) -> bool:
    if not text:
        return False
    stripped = strip_emoji(text)
    return not _ALNUM_RE.search(stripped) and stripped.strip() == ""


def rule_based_classify(text: Optional[str], author_name: str = "") -> Dict[str, Any]:
    """Stage 1 — deterministic filter returning the canonical analysis shape."""
    empty = {
        "is_useful": True,
        "reason": None,
        "lead_type": None,
        "confidence_score": 0.0,
        "priority": "low",
        "lead_quality": None,
        "sentiment": "neutral",
        "spam_score": 0.0,
        "duplicate_score": 0.0,
        "contact": {k: None for k in ("phone", "mobile", "whatsapp", "email",
                                      "telegram", "website", "instagram", "facebook_profile")},
        "person": {k: None for k in ("commenter_name", "city", "state", "country",
                                     "language", "occupation")},
        "buyer": {"budget": None, "requirement": None, "product": None,
                  "service_needed": None, "property_type": None, "vehicle_type": None,
                  "business_type": None, "preferred_location": None, "timeline": None,
                  "urgency": None, "intent": "other"},
    }
    if not text or not text.strip():
        return {**empty, "is_useful": False, "reason": "Empty comment"}

    raw = text.strip()
    lower = raw.lower()

    from app.admin.settings import get_bool_cached
    ignore_emoji = get_bool_cached("ci.ignore_emoji_only")
    ignore_spam = get_bool_cached("ci.ignore_spam")
    ignore_low_value = get_bool_cached("ci.ignore_low_value")

    if ignore_emoji and is_emoji_only(raw):
        return {**empty, "is_useful": False, "reason": "Emoji-only comment",
                "sentiment": "positive", "spam_score": 0.4}
    if ignore_spam and _URL_ONLY_RE.match(raw) and len(raw) > 8:
        return {**empty, "is_useful": False, "reason": "Link-only comment", "spam_score": 0.9}
    if ignore_spam:
        for pattern in SPAM_PATTERNS:
            if pattern.search(lower):
                return {**empty, "is_useful": False, "reason": "Spam pattern detected", "spam_score": 1.0}
    if ignore_low_value and len(_ALNUM_RE.findall(raw)) < 3:
        return {**empty, "is_useful": False, "reason": "Too short to be meaningful", "spam_score": 0.3}
    tokens = [t for t in _TOKEN_SPLIT_RE.split(lower) if t]
    if ignore_low_value and tokens and all(t in GRATITUDE_WORDS for t in tokens):
        return {**empty, "is_useful": False, "reason": "Gracious filler comment",
                "sentiment": "positive"}

    analysis = {**empty, "is_useful": True,
                "reason": "Comment may contain lead information",
                "confidence_score": 0.55, "priority": "medium"}
    return _rule_extraction(analysis, raw, lower)


def _rule_extraction(analysis: Dict[str, Any], text: str, lower: str) -> Dict[str, Any]:
    """
    Deterministic regex extraction applied on the rule path so contact and
    requirement fields are still surfaced without an AI call. Never invents —
    only values literally present in the comment text are filled.
    """
    phone_m = _PHONE_RE.search(text)
    email_m = _EMAIL_RE.search(text)
    wa_m = _WHATSAPP_WITH_NUM_RE.search(lower) or (_WHATSAPP_RE.search(lower) and phone_m)
    url_m = _URL_RE.search(text)
    budget_m = _BUDGET_RE.search(lower)
    city_m = next((c for c in _CITY_WORDS if c in lower), None)
    req_words = [w for w in _REQUIREMENT_WORDS if w in lower]
    urg_words = [w for w in _URGENCY_WORDS if w in lower]

    if phone_m:
        analysis["contact"]["phone"] = phone_m.group(0).strip()
        analysis["contact"]["mobile"] = phone_m.group(0).strip()
    if email_m:
        analysis["contact"]["email"] = email_m.group(0).strip()
    if wa_m:
        analysis["contact"]["whatsapp"] = (phone_m or _WHATSAPP_WITH_NUM_RE.search(lower)).group(0).strip()
    if url_m:
        analysis["contact"]["website"] = url_m.group(0).strip()
    if budget_m:
        analysis["buyer"]["budget"] = budget_m.group(0).strip()
    if req_words:
        analysis["buyer"]["requirement"] = req_words[0]
    if city_m:
        analysis["buyer"]["preferred_location"] = city_m.title()
    if urg_words:
        analysis["buyer"]["urgency"] = urg_words[0]
    if req_words or urg_words or budget_m or city_m:
        analysis["buyer"]["intent"] = "buying"
    if phone_m or email_m or url_m:
        analysis["lead_type"] = "buyer"
        analysis["priority"] = "high"
        analysis["confidence_score"] = 0.7
    return analysis


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 2 — Gemini extraction
# ─────────────────────────────────────────────────────────────────────────────

COMMENT_SYSTEM_PROMPT = """You are a lead-intelligence analyst for Facebook business pages. \
You are given ONE public comment and the caption of the post it appeared on. Decide whether \
the comment contains meaningful lead information and extract it.

A comment is MEANINGFUL when the person expresses actual interest or intent or gives \
contactable information — e.g. asking for price/availability, wanting to buy, sell, rent, \
book, visit, invest, offering a service, sharing a phone/WhatsApp/email, or a specific \
requirement (budget, location, size, timeline).

IGNORE (mark is_useful false) comments that are:
- gracious filler: nice, awesome, beautiful, good, wow, thank you, amazing, great, \
excellent, congratulations, love it, like it, and similar
- emoji-only comments (hearts, thumbs up, fire, etc.)
- spam (links, contests, "follow me", "dm me", lottery, etc.)
- meaningless chatter / random words / single letters

NEVER invent or guess any value. Unknown values must be null. Numbers must be JSON numbers \
(scores between 0 and 1).

Respond with STRICT JSON only — no markdown, no commentary:
{
  "is_useful": true,
  "reason": "one short sentence why",
  "lead_type": "buyer" | "seller" | "broker" | "other" | "none",
  "confidence_score": 0.0 to 1.0,
  "priority": "high" | "medium" | "low",
  "lead_quality": "hot" | "warm" | "cold" | "none",
  "sentiment": "excited" | "positive" | "neutral" | "negative",
  "spam_score": 0.0 to 1.0,
  "duplicate_score": 0.0 to 1.0,
  "contact": {"phone": string|null, "mobile": string|null, "whatsapp": string|null,
    "email": string|null, "telegram": string|null, "website": string|null,
    "instagram": string|null, "facebook_profile": string|null},
  "person": {"commenter_name": string|null, "city": string|null, "state": string|null,
    "country": string|null, "language": string|null, "occupation": string|null},
  "buyer": {"budget": string|null, "requirement": string|null, "product": string|null,
    "service_needed": string|null, "property_type": string|null, "vehicle_type": string|null,
    "business_type": string|null, "preferred_location": string|null, "timeline": string|null,
    "urgency": string|null, "intent": "buying" | "selling" | "rent" | "investment" | "other"}
}"""

_INTENT_VALUES = {"buying", "selling", "rent", "investment", "other"}
_LEAD_TYPE_VALUES = {"buyer", "seller", "broker", "other", "none"}
_PRIORITY_VALUES = {"high", "medium", "low"}
_QUALITY_VALUES = {"hot", "warm", "cold", "none"}
_SENTIMENT_VALUES = {"excited", "positive", "neutral", "negative"}


_GEMINI_DISABLED_UNTIL = 0.0  # circuit breaker: skip Gemini while rate-limited


def _call_gemini(system_prompt: str, user_content: str, temperature: float = 0.1,
                 model: Optional[str] = None, retries: int = 1) -> dict:
    """Gemini API call with 429 backoff + circuit breaker. Raises when it
    finally fails — callers fall back to rule-based analysis."""
    global _GEMINI_DISABLED_UNTIL
    if time.time() < _GEMINI_DISABLED_UNTIL:
        raise RuntimeError("Gemini rate-limited — circuit open, using rules")
    model = model or settings.gemini_model or "gemini-2.5-flash"
    from app.admin.envvars import get_envvar_str
    gemini_key = get_envvar_str("GEMINI_API_KEY", settings.gemini_api_key)
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={gemini_key}"
    )
    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_content}]}],
        "generationConfig": {"response_mime_type": "application/json", "temperature": temperature},
    }
    backoff = [5]
    for attempt in range(retries + 1):
        try:
            with httpx.Client(timeout=60) as client:
                resp = client.post(url, json=payload)
                if resp.status_code == 429:
                    # open the circuit for 10 minutes so the whole comment
                    # batch isn't slowed by endless retries
                    _GEMINI_DISABLED_UNTIL = time.time() + 600
                    logger.warning(f"[Gemini] 429 rate limit, circuit open until "
                                   f"{time.strftime('%H:%M:%S', time.localtime(_GEMINI_DISABLED_UNTIL))}")
                    if attempt < retries:
                        wait = backoff[min(attempt, len(backoff) - 1)]
                        time.sleep(wait)
                        continue
                    raise RuntimeError("Gemini rate-limited (429)")
                resp.raise_for_status()
                data = resp.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    raise RuntimeError("Gemini returned no candidates")
                parts = candidates[0].get("content", {}).get("parts", [])
                if not parts:
                    raise RuntimeError("Gemini returned no parts")
                raw = parts[0].get("text", "").strip()
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                return json.loads(raw.strip())
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                _GEMINI_DISABLED_UNTIL = time.time() + 600
                logger.warning(f"[Gemini] 429 rate limit, circuit open until "
                               f"{time.strftime('%H:%M:%S', time.localtime(_GEMINI_DISABLED_UNTIL))}")
                if attempt < retries:
                    wait = backoff[min(attempt, len(backoff) - 1)]
                    logger.warning(f"[Gemini] retrying in {wait}s...")
                    time.sleep(wait)
                    continue
            raise
    raise RuntimeError("Gemini API failed after all retries")


def _clean_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value or value.lower() in ("none", "null", "n/a", "na", "unknown", "-"):
            return None
        return value
    return str(value)


def _clean_number(value: Any) -> Optional[float]:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None


def _pick(value: Any, allowed: set, default: str) -> str:
    clean = _clean_str(value)
    if clean:
        norm = clean.lower().strip()
        for candidate in allowed:
            if candidate in norm or norm in candidate:
                return candidate
    return default


def _parse_gemini_result(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    contact_raw = raw.get("contact") if isinstance(raw.get("contact"), dict) else {}
    person_raw = raw.get("person") if isinstance(raw.get("person"), dict) else {}
    buyer_raw = raw.get("buyer") if isinstance(raw.get("buyer"), dict) else {}

    is_useful = bool(raw.get("is_useful", False))
    return {
        "is_useful": is_useful,
        "reason": _clean_str(raw.get("reason")) or (
            "No meaningful lead information" if not is_useful else "Comment may contain lead information"
        ),
        "lead_type": _pick(raw.get("lead_type"), _LEAD_TYPE_VALUES, "none") if is_useful else "none",
        "confidence_score": _clean_number(raw.get("confidence_score")) or (0.0 if not is_useful else 0.7),
        "priority": _pick(raw.get("priority"), _PRIORITY_VALUES, "medium") if is_useful else "low",
        "lead_quality": _pick(raw.get("lead_quality"), _QUALITY_VALUES, "none") if is_useful else "none",
        "sentiment": _pick(raw.get("sentiment"), _SENTIMENT_VALUES, "neutral"),
        "spam_score": _clean_number(raw.get("spam_score")) or 0.0,
        "duplicate_score": _clean_number(raw.get("duplicate_score")) or 0.0,
        "contact": {k: _clean_str(contact_raw.get(k)) for k in (
            "phone", "mobile", "whatsapp", "email", "telegram", "website",
            "instagram", "facebook_profile")},
        "person": {k: _clean_str(person_raw.get(k)) for k in (
            "commenter_name", "city", "state", "country", "language", "occupation")},
        "buyer": {"budget": _clean_str(buyer_raw.get("budget")),
                  "requirement": _clean_str(buyer_raw.get("requirement")),
                  "product": _clean_str(buyer_raw.get("product")),
                  "service_needed": _clean_str(buyer_raw.get("service_needed")),
                  "property_type": _clean_str(buyer_raw.get("property_type")),
                  "vehicle_type": _clean_str(buyer_raw.get("vehicle_type")),
                  "business_type": _clean_str(buyer_raw.get("business_type")),
                  "preferred_location": _clean_str(buyer_raw.get("preferred_location")),
                  "timeline": _clean_str(buyer_raw.get("timeline")),
                  "urgency": _clean_str(buyer_raw.get("urgency")),
                  "intent": _pick(buyer_raw.get("intent"), _INTENT_VALUES, "other")},
    }


def analyze_comment_ai(comment_text: Optional[str], author_name: str = "",
                       post_caption: str = "") -> Dict[str, Any]:
    """Two-stage analysis for one comment. Never raises.

    Behavior is admin-configurable: ``ai.enabled`` turns the Gemini stage on
    or off, ``ai.rule_fallback`` decides whether rule analysis is used when
    Gemini fails, and ``ai.temperature``/``ai.model`` tune the call."""
    from app.admin.settings import get_bool_cached, get_int_cached, get_setting_cached
    ai_enabled = get_bool_cached("ai.enabled")
    rule_fallback = get_bool_cached("ai.rule_fallback")
    rule = rule_based_classify(comment_text, author_name)
    if not rule["is_useful"]:
        return {**rule, "analyzed_by": "rules"}
    if not ai_enabled:
        return {**rule, "analyzed_by": "rules",
                "reason": rule["reason"] + " (AI disabled — rule-based pass)"}
    if not get_envvar_str("GEMINI_API_KEY", settings.gemini_api_key):
        return {**rule, "analyzed_by": "rules",
                "reason": rule["reason"] + " (no AI key — rule-based pass)"}
    try:
        user_content = json.dumps({
            "author": author_name or "unknown",
            "post_caption": post_caption or "",
            "comment_text": comment_text or "",
        }, ensure_ascii=False)
        model = get_setting_cached("ai.model") or settings.gemini_model
        temperature = float(get_setting_cached("ai.temperature") or 0.1)
        raw = _call_gemini(COMMENT_SYSTEM_PROMPT, user_content,
                           temperature=temperature, model=model)
        parsed = _parse_gemini_result(raw)
        return {**parsed, "analyzed_by": "gemini"}
    except Exception as e:
        logger.warning(f"[CommentAI] Gemini analysis failed: {e}")
        if not rule_fallback:
            return {**rule, "analyzed_by": "rules",
                    "reason": rule["reason"] + " (AI failed)"}
        return {**rule, "analyzed_by": "rules",
                "reason": rule["reason"] + " (AI failed, rule-based pass)"}


def comment_lead_score(ai_analysis: Optional[Dict[str, Any]] = None) -> int:
    """Deterministic 0-100 lead score: confidence (×weight) + priority
    (≤weight) + lead quality (≤weight) + contact completeness (≤weight) −
    spam penalty (≤weight). The weights are admin-configurable (Scoring
    page) with the original values as defaults."""
    from app.admin.settings import get_int_cached
    w_confidence = get_int_cached("scoring.confidence_weight", 50)
    w_priority = get_int_cached("scoring.priority_weight", 20)
    w_quality = get_int_cached("scoring.quality_weight", 20)
    w_phone = get_int_cached("scoring.contact_phone", 6)
    w_email = get_int_cached("scoring.contact_email", 4)
    w_spam = get_int_cached("scoring.spam_penalty", 20)
    ai = ai_analysis or {}
    if not ai.get("is_useful"):
        return 0
    score = 0.0
    try:
        score += float(ai.get("confidence_score") or 0) * w_confidence
    except (TypeError, ValueError):
        pass
    priority = str(ai.get("priority") or "").lower()
    quality = str(ai.get("lead_quality") or "").lower()
    score += {"high": w_priority, "medium": w_priority // 2, "low": 0}.get(priority, 0)
    score += {"hot": w_quality, "warm": w_quality // 2, "cold": 0}.get(quality, 0)
    contact = ai.get("contact") if isinstance(ai.get("contact"), dict) else {}
    if contact.get("phone") or contact.get("mobile") or contact.get("whatsapp"):
        score += w_phone
    if contact.get("email") or contact.get("telegram") or contact.get("website"):
        score += w_email
    try:
        score -= float(ai.get("spam_score") or 0) * w_spam
    except (TypeError, ValueError):
        pass
    return max(0, min(100, int(round(score))))


def signal_lead_score(ai_analysis: Optional[Dict[str, Any]] = None,
                      text: Optional[str] = None) -> int:
    """Additive 0-100 signal score from the admin-weighted signals: phone,
    email, budget, urgency, location and buying intent. Only signals that
    actually appear in the comment/AI extraction count."""
    from app.admin.settings import get_int_cached
    ai = ai_analysis or {}
    if not ai.get("is_useful"):
        return 0
    signals = set(extract_display_signals(text, ai))
    score = 0
    if "phone" in signals or "whatsapp" in signals:
        score += get_int_cached("scoring.phone", 20)
    if "email" in signals:
        score += get_int_cached("scoring.email", 15)
    if "budget" in signals:
        score += get_int_cached("scoring.budget", 20)
    if "urgency" in signals:
        score += get_int_cached("scoring.urgency", 15)
    if "location" in signals:
        score += get_int_cached("scoring.location", 10)
    if "buying_intent" in signals:
        score += get_int_cached("scoring.buying_intent", 20)
    return max(0, min(100, score))


def derive_quality_from_score(score: int) -> Optional[str]:
    """Map a signal score to hot/warm/cold using the admin thresholds.
    Returns None when the thresholds don't apply (score below warm)."""
    from app.admin.settings import get_int_cached
    hot_min = get_int_cached("scoring.hot_min", 80)
    warm_min = get_int_cached("scoring.warm_min", 50)
    if score >= hot_min:
        return "hot"
    if score >= warm_min:
        return "warm"
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Display filter — show a comment only when it carries at least one lead signal
# ─────────────────────────────────────────────────────────────────────────────

def extract_display_signals(text: Optional[str], ai_analysis: Optional[Dict[str, Any]] = None) -> List[str]:
    """Lead signals present in the comment. Each signal can be toggled from
    the admin Comment Intelligence settings (default: all on)."""
    from app.admin.settings import get_bool_cached
    detect = {
        "phone": get_bool_cached("ci.detect_phone"),
        "email": get_bool_cached("ci.detect_email"),
        "budget": get_bool_cached("ci.detect_budget"),
        "location": get_bool_cached("ci.detect_location"),
        "urgency": get_bool_cached("ci.detect_urgency"),
        "buying_intent": get_bool_cached("ci.detect_buying_intent"),
    }
    text = text or ""
    lower = text.lower()
    ai = ai_analysis or {}
    contact = ai.get("contact") if isinstance(ai.get("contact"), dict) else {}
    buyer = ai.get("buyer") if isinstance(ai.get("buyer"), dict) else {}
    person = ai.get("person") if isinstance(ai.get("person"), dict) else {}
    intent = str(buyer.get("intent") or "").lower()
    lead_type = str(ai.get("lead_type") or "").lower()

    signals: List[str] = []

    def add(name: str) -> None:
        if name not in signals:
            signals.append(name)

    if detect["phone"] and (contact.get("phone") or contact.get("mobile")
                            or _PHONE_RE.search(text)):
        add("phone")
    if detect["email"] and (contact.get("email") or _EMAIL_RE.search(text)):
        add("email")
    if detect["phone"] and (contact.get("whatsapp") or _WHATSAPP_WITH_NUM_RE.search(lower) or (
        _WHATSAPP_RE.search(lower) and _PHONE_RE.search(text)
    )):
        add("whatsapp")
    if detect["email"] and (contact.get("website") or _URL_RE.search(text)):
        add("website")
    if detect["buying_intent"] and (intent == "buying" or lead_type == "buyer"):
        add("buying_intent")
    if get_bool_cached("ci.detect_selling_intent") and (
            intent == "selling" or lead_type == "seller"):
        add("selling_intent")
    if detect["budget"] and (buyer.get("budget") or _BUDGET_RE.search(lower)):
        add("budget")
    if detect["budget"] and (buyer.get("requirement") or any(w in lower for w in _REQUIREMENT_WORDS)):
        add("requirement")
    if detect["location"] and (buyer.get("preferred_location") or person.get("city")
                               or any(c in lower for c in _CITY_WORDS)):
        add("location")
    if detect["urgency"] and (buyer.get("urgency") or any(w in lower for w in _URGENCY_WORDS)):
        add("urgency")
    if "?" in text or any(w in lower for w in _INQUIRY_WORDS):
        add("inquiry")
    if _CONTACT_REQUEST_RE.search(lower):
        add("contact_request")

    return signals


def should_display_comment(text: Optional[str], ai_analysis: Optional[Dict[str, Any]] = None) -> bool:
    return bool(extract_display_signals(text, ai_analysis))


# ─────────────────────────────────────────────────────────────────────────────
# Persist — analyze all comments of one post → `ai_comments`
# ─────────────────────────────────────────────────────────────────────────────

def _flat_extract(analysis: Dict[str, Any], text: Optional[str] = None) -> Dict[str, Any]:
    """Nested AI extraction → flat, display-ready fields."""
    from app.admin.settings import get_bool_cached, get_int_cached
    contact = analysis.get("contact") if isinstance(analysis.get("contact"), dict) else {}
    person = analysis.get("person") if isinstance(analysis.get("person"), dict) else {}
    buyer = analysis.get("buyer") if isinstance(analysis.get("buyer"), dict) else {}
    phone = contact.get("phone") or contact.get("mobile")
    signals = extract_display_signals(text, analysis)
    score = comment_lead_score(analysis)
    signal_score = signal_lead_score(analysis, text)
    quality = analysis.get("lead_quality")
    if get_bool_cached("scoring.derive_quality") and not quality and signal_score > 0:
        quality = derive_quality_from_score(signal_score) or quality
    min_lead_score = get_int_cached("ci.min_lead_score", 0)
    return {
        "phone": phone,
        "email": contact.get("email"),
        "whatsapp": contact.get("whatsapp"),
        "website": contact.get("website"),
        "budget": buyer.get("budget"),
        "requirement": (buyer.get("requirement") or buyer.get("property_type")
                        or buyer.get("service_needed") or buyer.get("product")),
        "location": person.get("city") or buyer.get("preferred_location") or person.get("state"),
        "intent": buyer.get("intent"),
        "urgency": buyer.get("urgency"),
        "priority": analysis.get("priority") or "low",
        "lead_quality": quality,
        "confidence": analysis.get("confidence_score") or 0.0,
        "lead_score": score,
        "signal_score": signal_score,
        "is_lead": bool(signals) and score >= min_lead_score,
        "reason": analysis.get("reason"),
    }


def analyze_comments_for_post(post_ref: str, max_comments: int = 500) -> Dict[str, Any]:
    """
    Analyze stored comments of one post and upsert results into `ai_comments`.
    Returns a summary; never raises.
    """
    from app.db.mongo import get_sync_db

    db = get_sync_db()
    if db is None:
        return {"status": "error", "error": "Database unavailable"}

    from bson import ObjectId
    try:
        post_doc = db.facebook_posts.find_one({"_id": ObjectId(post_ref)})
    except Exception:
        post_doc = None
    if not post_doc:
        return {"status": "error", "error": f"Post not found: {post_ref}"}

    page_doc = None
    if post_doc.get("page_ref"):
        try:
            page_doc = db.facebook_pages.find_one({"_id": ObjectId(post_doc["page_ref"])})
        except Exception:
            page_doc = None

    caption = post_doc.get("caption") or ""
    comments = list(
        db.facebook_comments.find({"post_ref": post_ref})
        .sort("published_date", 1)
        .limit(max_comments)
    )

    summary = {
        "status": "completed",
        "post_id": post_doc.get("post_id") or str(post_doc["_id"]),
        "post_ref": post_ref,
        "analyzed": 0, "useful": 0, "meaningless": 0, "displayed": 0,
        "analyzed_by_rules": 0, "analyzed_by_gemini": 0, "errors": 0,
        "by_priority": {}, "by_intent": {},
    }

    for doc in comments:
        analysis = analyze_comment_ai(doc.get("text"), doc.get("author_name") or "", caption)
        flat = _flat_extract(analysis, doc.get("text"))
        update = {
            "comment_ref": str(doc["_id"]),
            "comment_id": doc.get("comment_id"),
            "comment_text": doc.get("text"),
            "commenter_name": doc.get("author_name"),
            "platform": post_doc.get("platform") or "unknown",
            "post_ref": post_ref,
            "post_id": post_doc.get("post_id"),
            "post_url": post_doc.get("post_url"),
            "page_ref": str(page_doc["_id"]) if page_doc else None,
            "page_name": (page_doc or {}).get("page_name"),
            "details": analysis,
            "analyzed_by": analysis.get("analyzed_by"),
            "analyzed_at": utcnow(),
            **flat,
        }
        try:
            db.ai_comments.update_one({"comment_ref": str(doc["_id"])}, {"$set": update}, upsert=True)
        except Exception as e:
            logger.warning(f"[CommentAI] Failed to persist analysis: {e}")
            summary["errors"] += 1
            continue

        summary["analyzed"] += 1
        if flat["is_lead"]:
            summary["useful"] += 1
            summary["displayed"] += 1
            priority = flat["priority"]
            intent = flat["intent"] or "other"
            summary["by_priority"][priority] = summary["by_priority"].get(priority, 0) + 1
            summary["by_intent"][intent] = summary["by_intent"].get(intent, 0) + 1
        else:
            summary["meaningless"] += 1
        if analysis.get("analyzed_by") == "gemini":
            summary["analyzed_by_gemini"] += 1
        else:
            summary["analyzed_by_rules"] += 1

    if not comments:
        summary["status"] = "empty"
        summary["message"] = "No comments stored for this post"
    return summary
