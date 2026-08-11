"""
Agent intent extraction — `POST /api/search` query → structured params.

The AI agent NEVER searches Facebook itself. It only understands the
user's intent (what, where, how many) and hands the real fetching to
Apify actors. Extraction is deterministic (offline dictionaries) with a
Gemini fallback when a city/state/category is not recognised locally.

Returns:
    {keyword, city, state, category, limit, raw_query}
"""
import json
import logging
import re
from typing import Any, Dict, Optional

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

STATE_NAMES = {
    "rajasthan", "maharashtra", "mh", "karnataka", "ka", "tamil nadu",
    "tamilnadu", "tn", "telangana", "ts", "haryana", "hr", "uttar pradesh",
    "up", "west bengal", "wb", "gujarat", "gj", "madhya pradesh", "mp",
    "bihar", "kerala", "andhra pradesh", "ap", "odisha", "orissa",
    "punjab", "chandigarh", "assam", "chhattisgarh", "uttarakhand",
    "delhi", "goa", "jharkhand", "himachal pradesh", "ncr",
}

INDIAN_CITIES = {
    "jaipur": "Rajasthan", "jodhpur": "Rajasthan", "udaipur": "Rajasthan",
    "kota": "Rajasthan", "bikaner": "Rajasthan", "ajmer": "Rajasthan",
    "alwar": "Rajasthan", "sikar": "Rajasthan", "bharatpur": "Rajasthan",
    "bhiwadi": "Rajasthan", "pushkar": "Rajasthan", "kishangarh": "Rajasthan",
    "mumbai": "Maharashtra", "pune": "Maharashtra", "nagpur": "Maharashtra",
    "thane": "Maharashtra", "nashik": "Maharashtra", "aurangabad": "Maharashtra",
    "delhi": "Delhi", "new delhi": "Delhi",
    "gurgaon": "Haryana", "gurugram": "Haryana", "faridabad": "Haryana",
    "noida": "Uttar Pradesh", "lucknow": "Uttar Pradesh", "agra": "Uttar Pradesh",
    "kanpur": "Uttar Pradesh", "varanasi": "Uttar Pradesh", "prayagraj": "Uttar Pradesh",
    "meerut": "Uttar Pradesh", "ghaziabad": "Uttar Pradesh", "greater noida": "Uttar Pradesh",
    "kolkata": "West Bengal",
    "ahmedabad": "Gujarat", "surat": "Gujarat", "vadodara": "Gujarat", "rajkot": "Gujarat",
    "bengaluru": "Karnataka", "bangalore": "Karnataka", "mysore": "Karnataka", "mangalore": "Karnataka",
    "hyderabad": "Telangana",
    "chennai": "Tamil Nadu", "coimbatore": "Tamil Nadu", "madurai": "Tamil Nadu",
    "indore": "Madhya Pradesh", "bhopal": "Madhya Pradesh", "gwalior": "Madhya Pradesh",
    "patna": "Bihar",
    "chandigarh": "Chandigarh",
    "kochi": "Kerala", "cochin": "Kerala",
    "vizag": "Andhra Pradesh", "visakhapatnam": "Andhra Pradesh", "vijayawada": "Andhra Pradesh",
    "ranchi": "Jharkhand", "goa": "Goa", "panaji": "Goa",
    "dehradun": "Uttarakhand", "haridwar": "Uttarakhand", "rishikesh": "Uttarakhand",
    "guwahati": "Assam", "amritsar": "Punjab", "ludhiana": "Punjab",
    "bhubaneswar": "Odisha", "jaipur": "Rajasthan", "panvel": "Maharashtra",
    "srinagar": "Jammu and Kashmir", "shimla": "Himachal Pradesh",
}

CATEGORY_KEYWORDS = {
    "real estate": ["real estate", "property", "builder", "realty", "realtor",
                    "flat", "villa", "plot", "land", "construction"],
    "automobile": ["car dealer", "car dealership", "auto", "automobile",
                   "vehicle", "motor", "bike dealer", "truck"],
    "healthcare": ["hospital", "clinic", "doctor", "dental", "healthcare",
                   "diagnostic", "physio", "ayurveda", "pharmacy"],
    "interior design": ["interior", "furniture", "furnishing", "decor",
                        "modular kitchen"],
    "wedding": ["wedding", "photography", "catering", "marriage", "event",
                "banquet", "function"],
    "restaurant": ["restaurant", "cafe", "food", "bakery", "dhaba", "hotel"],
    "education": ["school", "college", "coaching", "tuition", "academy",
                  "training", "institute"],
    "textile": ["textile", "garment", "cloth", "fabrics", "saree", "dress"],
    "electronics": ["electronics", "mobile", "appliances", "computer", "laptop"],
    "travel": ["travel", "tourism", "tour", "holiday", "agent"],
    "beauty": ["salon", "spa", "beauty", "parlour", "makeup"],
    "general": [],
}


def _find_state(query_lower: str) -> Optional[str]:
    for state in sorted(STATE_NAMES, key=len, reverse=True):
        if state in query_lower:
            return state.title()
    return None


def _find_city(query_lower: str) -> Optional[str]:
    for city in sorted(INDIAN_CITIES, key=len, reverse=True):
        if city in query_lower:
            return city.title()
    return None


def _find_category(query_lower: str) -> Optional[str]:
    for cat, words in CATEGORY_KEYWORDS.items():
        if cat == "general":
            continue
        if any(w in query_lower for w in words):
            return cat
    return None


def _gemini_intent(query: str) -> Optional[Dict[str, Any]]:
    """Fallback: ask Gemini for city/state/category when local dictionaries miss."""
    if not settings.gemini_api_key:
        return None
    import httpx

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.gemini_model}:generateContent?key={settings.gemini_api_key}"
    )
    prompt = (
        "You parse one Facebook lead-search query into structured JSON. "
        "Return ONLY strict JSON: {\"city\": string|null, \"state\": string|null, "
        "\"category\": string|null, \"keyword\": string}. The keyword is the "
        "business-type phrase with any city/state words removed."
    )
    try:
        resp = httpx.post(
            url,
            timeout=30,
            json={
                "system_instruction": {"parts": [{"text": prompt}]},
                "contents": [{"role": "user", "parts": [{"text": query}]}],
                "generationConfig": {"response_mime_type": "application/json", "temperature": 0.0},
            },
        )
        resp.raise_for_status()
        data = resp.json()
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        if not parts:
            return None
        raw = parts[0].get("text", "").strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw.strip())
        if not isinstance(parsed, dict):
            return None
        return {
            "city": (parsed.get("city") or None),
            "state": (parsed.get("state") or None),
            "category": (parsed.get("category") or None),
            "keyword": (parsed.get("keyword") or "").strip(),
        }
    except Exception as e:
        logger.warning(f"[Intent] Gemini intent extraction failed: {e}")
        return None


def parse_query(query: str, limit: int = 10) -> Dict[str, Any]:
    """
    Turn a free-text search request into {keyword, city, state, category, limit}.

    Deterministic dictionaries first; Gemini fills gaps only for the
    missing city/state/category. `keyword` defaults to the raw query —
    Apify itself tries shorter variants when the full query is too long.
    """
    raw = (query or "").strip()
    if not raw:
        return {"keyword": "", "city": None, "state": None, "category": None,
                "limit": limit, "raw_query": ""}

    lower = " " + raw.lower() + " "
    city = _find_city(lower)
    state = _find_state(lower)
    category = _find_category(lower)

    if city is None or state is None or category is None:
        gem = _gemini_intent(raw)
        if gem:
            city = city or gem.get("city")
            state = state or gem.get("state")
            category = category or gem.get("category")

    # keyword: raw query with city/state words removed (helps Apify search)
    keyword = raw
    if city:
        keyword = re.sub(rf"\b{re.escape(city)}\b", "", keyword, flags=re.IGNORECASE)
    if state and state.lower() != "delhi":
        keyword = re.sub(rf"\b{re.escape(state)}\b", "", keyword, flags=re.IGNORECASE)
    keyword = re.sub(r"\s+", " ", keyword).strip().strip(" ,-")
    keyword = re.sub(r"\b(in|at|of|near|for)\s*$", "", keyword, flags=re.IGNORECASE).strip()
    keyword = keyword or raw

    return {
        "keyword": keyword,
        "city": city,
        "state": state,
        "category": category,
        "limit": max(1, min(int(limit), 100)),
        "raw_query": raw,
    }
