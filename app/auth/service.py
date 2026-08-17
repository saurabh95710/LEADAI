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

from app.admin.envvars import get_envvar, get_envvar_bool, get_envvar_int, get_envvar_str
from app.config import get_settings
from app.db.mongo import get_sync_db

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
    """True when this IP may still try to log in within the rate window.

    The Security page can disable brute-force protection entirely
    (``security.login_protection``); when disabled every attempt passes."""
    try:
        from app.admin.settings import get_setting
        if not bool(get_setting("security.login_protection")):
            return True
    except Exception:
        pass
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
    try:
        from app.admin.settings import get_setting
        if not bool(get_setting("security.login_protection")):
            return
    except Exception:
        pass
    _prune_login_attempts(time.time())
    _login_attempts.setdefault(ip, []).append(time.time())


def reset_login_attempts(ip: str) -> None:
    _login_attempts.pop(ip, None)


# ── Admin credential verification ─────────────────────────────────────────

def verify_admin_login(email: str, password: str) -> Optional[Dict[str, Any]]:
    """Constant-time check of email + sha256(password).

    Accounts: the .env ``admin_email`` is always a recovery super-admin;
    additional managers/viewers live in the ``admin_users`` collection
    (managed from the admin Security page). A DB record with the same email
    as the env admin takes precedence so a password set in the panel works
    while the env password stays valid as a fallback.

    Returns the user dict on success, None otherwise. All comparisons use
    hmac.compare_digest so timing cannot leak how close a guess was.
    """
    email_clean = email.strip().lower()
    guess = hashlib.sha256(password.encode("utf-8")).hexdigest().lower()
    now = time.time()

    # 1) Managed account (admin_users) — takes precedence for the env email
    record = _admin_user_record(email_clean)
    if record is not None:
        expected_hash = (record.get("password_hash") or "").strip().lower()
        if not record.get("enabled", True) or not expected_hash or not password:
            return None
        if not hmac.compare_digest(guess, expected_hash):
            return None
        _touch_last_login(email_clean, now)
        return {
            "email": email_clean,
            "name": record.get("name") or email_clean.split("@")[0],
            "role": record.get("role") or "viewer",
        }

    # 2) Env admin (recovery super-admin)
    expected_email = get_envvar_str("ADMIN_EMAIL", settings.admin_email).strip().lower()
    if not expected_email or email_clean != expected_email:
        return None
    expected_hash = (get_envvar_str("ADMIN_PASSWORD_HASH",
                                    settings.admin_password_hash) or "").strip().lower()
    if not expected_hash or not password:
        return None
    if not hmac.compare_digest(guess, expected_hash):
        return None
    return {
        "email": expected_email,
        "name": "Admin",
        "role": "super_admin",
    }


def _admin_user_record(email: str) -> Optional[Dict[str, Any]]:
    try:
        db = get_sync_db()
        if db is None:
            return None
        return db["admin_users"].find_one({"email": email})
    except Exception:
        return None


def _touch_last_login(email: str, when: float) -> None:
    try:
        db = get_sync_db()
        if db is None:
            return
        db["admin_users"].update_one(
            {"email": email}, {"$set": {"last_login": when}})
    except Exception:
        pass


# ── Signed session cookie ─────────────────────────────────────────────────

def _secret() -> str:
    global _FALLBACK_SECRET
    override = get_envvar_str("SESSION_SECRET", "")
    if override:
        return override
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
        "iat": int(time.time()),
        "exp": int(time.time()) + get_envvar_int("SESSION_TTL_DAYS", settings.session_ttl_days) * 86400,
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
        max_age=get_envvar_int("SESSION_TTL_DAYS", settings.session_ttl_days) * 86400,
        httponly=True,
        samesite="lax",
        secure=get_envvar_bool("SESSION_COOKIE_SECURE", settings.session_cookie_secure),
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def session_user(request: Request) -> Optional[Dict[str, Any]]:
    """Current user from the session cookie, or None when signed out."""
    return parse_session_value(request.cookies.get(COOKIE_NAME))


def session_issued_at(value: Optional[str]) -> Optional[int]:
    """Issue timestamp (iat) of a session cookie, or None when unavailable
    (legacy cookie without the claim). Used for revocation + idle timeout."""
    if not value or "." not in value:
        return None
    payload_b64 = value.rsplit(".", 1)[0]
    try:
        data = json.loads(_b64d(payload_b64))
    except Exception:
        return None
    iat = data.get("iat")
    if isinstance(iat, (int, float)):
        return int(iat)
    return None


def public_user(user: Dict[str, Any]) -> Dict[str, Any]:
    """Shape of the user object exposed to the frontend."""
    return {
        "email": user.get("email", ""),
        "name": user.get("name", ""),
        "role": user.get("role", ""),
    }


# ── Environment panel unlock cookie ─────────────────────────────────────────
# A short-lived signed cookie that gates every /api/admin/env* endpoint on
# top of the normal admin session. Set by POST /api/admin/env/unlock after
# the guard password verifies; path-scoped so it only travels with env API
# calls.

ENV_UNLOCK_COOKIE = "leadai_env_unlock"
ENV_UNLOCK_TTL_SEC = 15 * 60
_ENV_UNLOCK_HEADER = {"v": 1}


def build_env_unlock_value(ttl_sec: int = ENV_UNLOCK_TTL_SEC) -> str:
    payload = {
        **_ENV_UNLOCK_HEADER,
        "iat": int(time.time()),
        "exp": int(time.time()) + ttl_sec,
    }
    payload_b64 = _b64e(
        json.dumps(payload, separators=(",", ":")).encode())
    return f"{payload_b64}.{_sign(payload_b64)}"


def parse_env_unlock_value(value: Optional[str]) -> bool:
    if not value or "." not in value:
        return False
    payload_b64, sig = value.rsplit(".", 1)
    if not hmac.compare_digest(_sign(payload_b64), sig):
        return False
    try:
        data = json.loads(_b64d(payload_b64))
    except Exception:
        return False
    return (data.get("v") == _ENV_UNLOCK_HEADER["v"]
            and data.get("exp", 0) > time.time())


def set_env_unlock_cookie(response: Response) -> None:
    response.set_cookie(
        ENV_UNLOCK_COOKIE,
        build_env_unlock_value(),
        max_age=ENV_UNLOCK_TTL_SEC,
        httponly=True,
        samesite="lax",
        secure=get_envvar_bool("SESSION_COOKIE_SECURE", settings.session_cookie_secure),
        path="/api/admin/env",
    )


def clear_env_unlock_cookie(response: Response) -> None:
    response.delete_cookie(ENV_UNLOCK_COOKIE, path="/api/admin/env")
