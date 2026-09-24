"""The permanent Super Admin configured through the environment.

``SUPERADMIN_EMAIL`` + ``SUPERADMIN_PASSWORD`` (Render environment variables
or .env) define the platform owner. They are the only credentials the
deployment needs: organization admins and users are database accounts
created through signup and invitations.

Rules:
  * read from the process environment / .env only — never from the admin
    panel's DB overrides, so nobody can change them from inside the app;
  * ``SUPERADMIN_PASSWORD`` may be the plain password or a bcrypt hash
    (``$2b$...``); plain values are compared in constant time;
  * when ``SUPERADMIN_EMAIL`` is set, only this password signs that email in
    (a same-email ``admin_users`` record cannot shadow it);
  * the legacy ``PANEL_ADMIN_EMAIL`` / ``PANEL_ADMIN_PASSWORD_HASH`` pair is
    still honoured when ``SUPERADMIN_EMAIL`` is not set, so existing
    deployments keep working.
"""
import hashlib
import hmac
import logging
import re
from typing import Any, Dict, Optional

from app.config import get_settings

logger = logging.getLogger(__name__)

MIN_PASSWORD_LENGTH = 12
_BCRYPT = re.compile(r"^\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _norm(email: Optional[str]) -> str:
    return (email or "").strip().lower()


def env_superadmin_email() -> str:
    """SUPERADMIN_EMAIL, normalised ("" when unset)."""
    return _norm(get_settings().superadmin_email)


def managed_by_env() -> bool:
    """True when the SUPERADMIN_* variables define the Super Admin."""
    s = get_settings()
    return bool(env_superadmin_email() and (s.superadmin_password or "").strip())


def _legacy_email() -> str:
    from app.admin.envvars import get_envvar_str
    return _norm(get_envvar_str("PANEL_ADMIN_EMAIL", get_settings().panel_admin_email))


def superadmin_email() -> str:
    """The environment-defined Super Admin email: SUPERADMIN_EMAIL, else the
    legacy PANEL_ADMIN_EMAIL ("" when neither is set)."""
    return env_superadmin_email() or _legacy_email()


def is_superadmin_email(email: Optional[str]) -> bool:
    target = superadmin_email()
    return bool(target) and _norm(email) == target


def _verify_secret(password: str, secret: str) -> bool:
    if not password or not secret:
        return False
    if _BCRYPT.match(secret):
        import bcrypt
        try:
            return bcrypt.checkpw(password.encode("utf-8"), secret.encode("utf-8"))
        except ValueError:
            return False
    # plain value: constant-time comparison of fixed-length digests
    return hmac.compare_digest(hashlib.sha256(password.encode("utf-8")).digest(),
                               hashlib.sha256(secret.encode("utf-8")).digest())


def verify_env_superadmin(email: str, password: str) -> Optional[bool]:
    """Check SUPERADMIN_EMAIL / SUPERADMIN_PASSWORD.

    Returns True/False when ``email`` is the SUPERADMIN_EMAIL account (the
    answer is final for that email), or None when the variables are not set
    or the email is a different account."""
    if not managed_by_env() or _norm(email) != env_superadmin_email():
        return None
    return _verify_secret(password, get_settings().superadmin_password.strip())


def config_status() -> Dict[str, Any]:
    """Non-secret summary of the Super Admin configuration."""
    s = get_settings()
    if managed_by_env():
        pw = s.superadmin_password.strip()
        is_hash = bool(_BCRYPT.match(pw))
        return {"configured": True, "source": "SUPERADMIN_EMAIL",
                "email_valid": bool(_EMAIL.match(env_superadmin_email())),
                "password_is_hash": is_hash,
                "password_too_short": (not is_hash) and len(pw) < MIN_PASSWORD_LENGTH}
    if env_superadmin_email() and not (s.superadmin_password or "").strip():
        return {"configured": False, "source": "SUPERADMIN_EMAIL",
                "problem": "SUPERADMIN_PASSWORD is empty"}
    if (s.superadmin_password or "").strip() and not env_superadmin_email():
        return {"configured": False, "source": "SUPERADMIN_PASSWORD",
                "problem": "SUPERADMIN_EMAIL is empty"}
    if s.panel_admin_email and s.panel_admin_password_hash:
        return {"configured": True, "source": "PANEL_ADMIN_EMAIL (deprecated)"}
    return {"configured": False, "source": None,
            "problem": "SUPERADMIN_EMAIL and SUPERADMIN_PASSWORD are not set"}


def _mask(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


def validate_superadmin_config() -> Dict[str, Any]:
    """Log the Super Admin configuration at startup (never the password).
    Misconfiguration is reported loudly but does not stop the app, so the
    health check and database-backed accounts keep working."""
    status = config_status()
    if status["configured"] and status["source"] == "SUPERADMIN_EMAIL":
        logger.info("Super Admin configured from SUPERADMIN_EMAIL (%s)",
                    _mask(env_superadmin_email()))
        if not status["email_valid"]:
            logger.error("SUPERADMIN_EMAIL does not look like an email address.")
        if status["password_too_short"]:
            logger.warning("SUPERADMIN_PASSWORD is shorter than %d characters — "
                           "use a long, unique password.", MIN_PASSWORD_LENGTH)
    elif status["configured"]:
        logger.warning("Super Admin configured from the deprecated PANEL_ADMIN_EMAIL / "
                       "PANEL_ADMIN_PASSWORD_HASH — set SUPERADMIN_EMAIL and "
                       "SUPERADMIN_PASSWORD instead.")
    else:
        logger.error("No Super Admin configured: %s. Set SUPERADMIN_EMAIL and "
                     "SUPERADMIN_PASSWORD in the environment (e.g. Render → "
                     "Environment) to sign in to /superadmin.", status.get("problem"))
    return status
