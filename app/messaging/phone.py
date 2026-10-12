"""Phone numbers in one shape: E.164 ("+919876543210")."""
import re
from typing import Optional


def default_country_code() -> str:
    """Country code for numbers typed without one (SMS_DEFAULT_COUNTRY_CODE, default +91)."""
    try:
        from app.admin.envvars import get_envvar_str
        cc = get_envvar_str("SMS_DEFAULT_COUNTRY_CODE", "+91") or "+91"
    except Exception:
        cc = "+91"
    cc = "+" + re.sub(r"\D", "", cc)
    return cc if len(cc) > 1 else "+91"


def normalize_phone(raw: Optional[str], country_code: Optional[str] = None) -> Optional[str]:
    """E.164 or None when it can't be a phone number.
    "+91 98765 43210", "098765 43210" and "9876543210" (with +91 as the
    default country) all become "+919876543210"; "0091…" counts as "+91…"."""
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    if text.startswith("+"):
        e164 = "+" + digits
    elif digits.startswith("00"):
        e164 = "+" + digits[2:]
    else:
        cc = (country_code or default_country_code()).lstrip("+")
        local = digits.lstrip("0")
        if digits.startswith(cc) and len(digits) > len(cc) + 8:
            e164 = "+" + digits                         # already has the country code
        else:
            e164 = "+" + cc + local
    if not re.fullmatch(r"\+[1-9]\d{7,14}", e164):
        return None
    return e164


def mask_phone(e164: Optional[str]) -> str:
    """"+91•••••43210" — enough to recognise, not to dial."""
    if not e164:
        return ""
    return e164[:3] + "•" * max(0, len(e164) - 8) + e164[-5:]
