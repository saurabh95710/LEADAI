"""Admin sign-in + signed session cookie helpers.

Flow: the login page posts email/password to ``POST /api/auth/login``; the
backend verifies the password against its sha256 hash (constant-time
comparison, never stored as plaintext) and sets an httpOnly session cookie.
Failed attempts are throttled per IP to slow brute-force attacks.
"""
import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Any, Dict, List, Optional

from fastapi import Request, Response

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

COOKIE_NAME = "leadai_session"
_HEADER = {"v": 1}
_FALLBACK_SECRET: Optional[str] = None

# ── Brute-force throttling (in-memory, per IP) ────────────────────────────
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_SEC = 60
_login_attempts: Dict[str, List[float]] = {}


def _prune_login_attempts(now: float) -> None:
    for ip in [ip for ip, stamps in _login_attempts.items() if stamps]:
        _login_attempts[ip] = [t for t in _login_attempts[ip]
                               if now - t < _LOGIN_WINDOW_SEC]
        if not _login_attempts[ip]:
            del _login_attempts[ip]


def login_allowed(ip: str) -> bool:
    """True when this IP may still try to log in within the rate window."""
    now = time.time()
    _prune_login_attempts(now)
    return len(_login_attempts.get(ip, [])) < _LOGIN_MAX_ATTEMPTS


def login_denied_seconds(ip: str) -> int:
    """Seconds until the IP may try again (0 = allowed)."""
    now = time.time()
    _prune_login_attempts(now)
    stamps = _login_attempts.get(ip, [])
    if not stamps:
        return 0
    remaining = len(stamps) - _LOGIN_MAX_ATTEMPTS + 1
    if remaining <= 0:
        return 0
    return max(1, int(_LOGIN_WINDOW_SEC - (now - stamps[-1])) + 1)


def record_login_failure(ip: str) -> None:
    _prune_login_attempts(time.time())
    _login_attempts.setdefault(ip, []).append(time.time())


def reset_login_attempts(ip: str) -> None:
    _login_attempts.pop(ip, None)


# ── Admin credential verification ─────────────────────────────────────────

def verify_admin_login(email: str, password: str) -> Optional[Dict[str, Any]]:
    """Constant-time check of email + sha256(password) against config.

    Returns the admin user dict on success, None otherwise. The comparison
    uses hmac.compare_digest so timing cannot leak how close a guess was.
    """
    expected_email = settings.admin_email.strip().lower()
    if not expected_email or email.strip().lower() != expected_email:
        return None
    expected_hash = (settings.admin_password_hash or "").strip().lower()
    if not expected_hash or not password:
        return None
    guess = hashlib.sha256(password.encode("utf-8")).hexdigest().lower()
    if not hmac.compare_digest(guess, expected_hash):
        return None
    return {
        "email": expected_email,
        "name": "Admin",
        "role": "admin",
    }


# ── Signed session cookie ─────────────────────────────────────────────────

def _secret() -> str:
    global _FALLBACK_SECRET
    if settings.session_secret:
        return settings.session_secret
    if _FALLBACK_SECRET is None:
        _FALLBACK_SECRET = secrets.token_urlsafe(48)
        logger.warning(
            "SESSION_SECRET is not set in .env — using a per-process random "
            "secret; sessions will be invalidated on restart."
        )
    return _FALLBACK_SECRET


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _sign(payload_b64: str) -> str:
    return hmac.new(_secret().encode(), payload_b64.encode(), hashlib.sha256).hexdigest()


def build_session_value(user: Dict[str, Any]) -> str:
    """Signed, timestamped cookie value carrying the logged-in user."""
    payload = {
        "user": user,
        "exp": int(time.time()) + settings.session_ttl_days * 86400,
    }
    payload_b64 = _b64e(
        json.dumps({**_HEADER, **payload}, separators=(",", ":")).encode())
    return f"{payload_b64}.{_sign(payload_b64)}"


def parse_session_value(value: Optional[str]) -> Optional[Dict[str, Any]]:
    """Validate signature + expiry and return the user claims (or None)."""
    if not value or "." not in value:
        return None
    payload_b64, sig = value.rsplit(".", 1)
    if not hmac.compare_digest(_sign(payload_b64), sig):
        logger.warning("Session cookie signature mismatch")
        return None
    try:
        data = json.loads(_b64d(payload_b64))
    except Exception:
        logger.warning("Session cookie payload is not valid JSON")
        return None
    if data.get("v") != _HEADER["v"] or data.get("exp", 0) < time.time():
        return None
    user = data.get("user")
    return user if isinstance(user, dict) and user.get("email") else None


def set_session_cookie(response: Response, user: Dict[str, Any]) -> None:
    response.set_cookie(
        COOKIE_NAME,
        build_session_value(user),
        max_age=settings.session_ttl_days * 86400,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def session_user(request: Request) -> Optional[Dict[str, Any]]:
    """Current user from the session cookie, or None when signed out."""
    return parse_session_value(request.cookies.get(COOKIE_NAME))


def public_user(user: Dict[str, Any]) -> Dict[str, Any]:
    """Shape of the user object exposed to the frontend."""
    return {
        "email": user.get("email", ""),
        "name": user.get("name", ""),
        "role": user.get("role", ""),
    }
