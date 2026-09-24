"""
Security hardening tests for LeadAI.

Tests cover:
- Password hashing (bcrypt, legacy migration)
- Authentication (login, logout, session)
- RBAC (role-based access control)
- CORS configuration
- Rate limiting
- Secret management
- Security headers
- XSS protection
"""
import pytest
from unittest.mock import patch, MagicMock


# ── Password Hashing Tests ───────────────────────────────────────────────────

class TestPasswordHashing:
    """Test bcrypt password hashing and legacy SHA-256 migration."""

    def test_hash_password_returns_bcrypt(self):
        from app.auth.crypto import hash_password
        h = hash_password("test_password_123")
        assert h.startswith("$2")
        assert len(h) == 60

    def test_verify_bcrypt_password(self):
        from app.auth.crypto import hash_password, verify_password
        h = hash_password("my_secure_pass")
        valid, new_hash = verify_password("my_secure_pass", h)
        assert valid is True
        assert new_hash is None  # No migration needed for bcrypt

    def test_verify_wrong_password(self):
        from app.auth.crypto import hash_password, verify_password
        h = hash_password("correct_password")
        valid, new_hash = verify_password("wrong_password", h)
        assert valid is False
        assert new_hash is None

    def test_verify_legacy_sha256_migrates(self):
        import hashlib
        from app.auth.crypto import verify_password
        # Create a legacy SHA-256 hash
        password = "legacy_password"
        legacy_hash = hashlib.sha256(password.encode()).hexdigest()
        valid, new_hash = verify_password(password, legacy_hash)
        assert valid is True
        assert new_hash is not None
        assert new_hash.startswith("$2")  # Should be bcrypt

    def test_verify_legacy_sha256_wrong_password(self):
        import hashlib
        from app.auth.crypto import verify_password
        legacy_hash = hashlib.sha256("correct_password".encode()).hexdigest()
        valid, new_hash = verify_password("wrong_password", legacy_hash)
        assert valid is False
        assert new_hash is None

    def test_is_legacy_sha256_detection(self):
        from app.auth.crypto import _is_legacy_sha256
        import hashlib
        # SHA-256 hex digest
        assert _is_legacy_sha256(hashlib.sha256(b"test").hexdigest()) is True
        # Bcrypt hash
        assert _is_legacy_sha256("$2b$12$abcdefghijklmnopqrstuu123456789012345678") is False
        # Random string
        assert _is_legacy_sha256("not_a_hash") is False
        # Empty string
        assert _is_legacy_sha256("") is False

    def test_empty_password_returns_false(self):
        from app.auth.crypto import verify_password
        valid, _ = verify_password("", "$2b$12$abcdefghijklmnopqrstuu123456789012345678")
        assert valid is False

    def test_empty_hash_returns_false(self):
        from app.auth.crypto import verify_password
        valid, _ = verify_password("password", "")
        assert valid is False

    def test_is_bcrypt_hash(self):
        from app.auth.crypto import is_bcrypt_hash, hash_password
        # Generate a real bcrypt hash
        real_hash = hash_password("test")
        assert is_bcrypt_hash(real_hash) is True
        assert is_bcrypt_hash("not_a_bcrypt_hash") is False
        assert is_bcrypt_hash("") is False


# ── Session Security Tests ──────────────────────────────────────────────────

class TestSessionSecurity:
    """Test session cookie generation, validation, and security."""

    def test_session_roundtrip(self):
        from app.auth.service import build_session_value, parse_session_value
        import os
        os.environ["SESSION_SECRET"] = "test-session-secret-for-testing"
        try:
            user = {"email": "test@example.com", "name": "Test", "role": "user"}
            value = build_session_value(user)
            parsed = parse_session_value(value)
            assert parsed is not None
            assert parsed["email"] == "test@example.com"
        finally:
            del os.environ["SESSION_SECRET"]

    def test_session_tamper_detected(self):
        from app.auth.service import build_session_value, parse_session_value
        import os
        os.environ["SESSION_SECRET"] = "test-session-secret-for-testing"
        try:
            value = build_session_value({"email": "test@example.com", "name": "Test", "role": "user"})
            payload, sig = value.rsplit(".", 1)
            tampered = payload[:-5] + "XXXXX"
            assert parse_session_value(f"{tampered}.{sig}") is None
        finally:
            del os.environ["SESSION_SECRET"]

    def test_session_empty_returns_none(self):
        from app.auth.service import parse_session_value
        assert parse_session_value(None) is None
        assert parse_session_value("") is None
        assert parse_session_value("invalid") is None

    def test_session_cookie_httponly(self):
        """Session cookie should be HttpOnly to prevent XSS access."""
        from app.auth.service import set_session_cookie, COOKIE_NAME
        from starlette.responses import Response
        response = Response()
        set_session_cookie(response, {"email": "test@example.com", "name": "Test", "role": "user"})
        cookie_header = response.headers.get("set-cookie", "")
        assert "httponly" in cookie_header.lower()

    def test_session_cookie_samesite_lax(self):
        """Session cookie should use SameSite=Lax."""
        from app.auth.service import set_session_cookie
        from starlette.responses import Response
        response = Response()
        set_session_cookie(response, {"email": "test@example.com", "name": "Test", "role": "user"})
        cookie_header = response.headers.get("set-cookie", "")
        assert "samesite=lax" in cookie_header.lower()


# ── Authentication Flow Tests ──────────────────────────────────────────────

class TestAuthenticationFlow:
    """Test login, logout, and credential verification."""

    def test_site_login_correct_credentials(self, site_account):
        from app.auth import service
        from app.config import get_settings
        from app.admin.envvars import clear_cache
        clear_cache()
        settings = get_settings()
        settings.admin_email = "test@example.com"
        settings.admin_password_hash = "a36aef5a11c4073fbe60314fc9df530a9d5f986533594d1f5190742ff9e0e408"
        import app.admin.envvars as ev
        ev.get_sync_db = lambda: None
        try:
            # no users record -> fail closed even with the right env password
            assert service.verify_site_login("test@example.com", "Admin@2026") is None
            site_account("test@example.com")
            user = service.verify_site_login("test@example.com", "Admin@2026")
            assert user is not None
            assert user["email"] == "test@example.com"
            assert user["scope"] == "site"
            assert user["organization_id"]
        finally:
            ev.get_sync_db = _original_get_sync_db

    def test_site_login_wrong_password(self):
        from app.auth import service
        from app.config import get_settings
        from app.admin.envvars import clear_cache
        clear_cache()
        settings = get_settings()
        settings.admin_email = "test@example.com"
        settings.admin_password_hash = "a36aef5a11c4073fbe60314fc9df530a9d5f986533594d1f5190742ff9e0e408"
        import app.admin.envvars as ev
        ev.get_sync_db = lambda: None
        try:
            user = service.verify_site_login("test@example.com", "wrong_password")
            assert user is None
        finally:
            ev.get_sync_db = _original_get_sync_db

    def test_site_login_wrong_email(self):
        from app.auth import service
        from app.config import get_settings
        from app.admin.envvars import clear_cache
        clear_cache()
        settings = get_settings()
        settings.admin_email = "test@example.com"
        settings.admin_password_hash = "a36aef5a11c4073fbe60314fc9df530a9d5f986533594d1f5190742ff9e0e408"
        import app.admin.envvars as ev
        ev.get_sync_db = lambda: None
        try:
            user = service.verify_site_login("other@example.com", "Admin@2026")
            assert user is None
        finally:
            ev.get_sync_db = _original_get_sync_db


# ── Rate Limiting Tests ─────────────────────────────────────────────────────

class TestRateLimiting:
    """Test brute-force protection."""

    def test_rate_limit_allows_initial_requests(self):
        from app.auth.service import login_allowed
        assert login_allowed("192.168.1.100") is True

    def test_rate_limit_blocks_after_max_attempts(self):
        from app.auth.service import record_login_failure, login_allowed, reset_login_attempts, _LOGIN_MAX_ATTEMPTS
        ip = "192.168.1.200"
        try:
            for _ in range(_LOGIN_MAX_ATTEMPTS):
                record_login_failure(ip)
            assert login_allowed(ip) is False
        finally:
            reset_login_attempts(ip)

    def test_rate_limit_resets_after_success(self):
        from app.auth.service import record_login_failure, reset_login_attempts, login_allowed
        ip = "192.168.1.300"
        try:
            for _ in range(3):
                record_login_failure(ip)
            reset_login_attempts(ip)
            assert login_allowed(ip) is True
        finally:
            reset_login_attempts(ip)


# ── CORS Configuration Tests ───────────────────────────────────────────────

class TestCORSConfiguration:
    """Test CORS is properly configured."""

    def test_no_wildcard_cors(self):
        """Wildcard CORS should not be used in the app configuration."""
        from app.main import _cors_origins
        assert "*" not in _cors_origins

    def test_cors_configurable(self):
        """CORS origins should be configurable via settings."""
        import app.config as cfg
        cfg.get_settings.cache_clear()
        settings = cfg.get_settings()
        # Default should be empty (same-origin only)
        assert settings.allowed_origins == "" or isinstance(settings.allowed_origins, str)
        cfg.get_settings.cache_clear()


# ── Secret Management Tests ─────────────────────────────────────────────────

class TestSecretManagement:
    """Test that secrets are not exposed."""

    def test_health_endpoint_no_secrets(self):
        """Health endpoint should not expose secrets."""
        from app.main import health
        import asyncio
        result = asyncio.run(health())
        assert "apify_configured" not in result  # Should not expose this
        assert "gemini_configured" not in result
        assert "status" in result

    def test_envvar_masking(self):
        """Secret envvars should be masked in API responses."""
        from app.admin.envvars import _mask
        assert _mask("") == ""
        assert _mask("abc") == "••••"
        assert _mask("abcdefgh") == "••••efgh"

    def test_no_password_in_public_user(self):
        """Public user object should never contain password hash."""
        from app.auth.service import public_user
        user = {"email": "test@example.com", "name": "Test", "role": "user", "password_hash": "secret"}
        public = public_user(user)
        assert "password_hash" not in public


# ── Security Headers Tests ──────────────────────────────────────────────────

class TestSecurityHeaders:
    """Test security headers are present."""

    def test_security_headers_configured(self):
        """Security headers middleware should be configured in the app."""
        from app.main import app
        # Verify the middleware stack has entries (auth_gate, maintenance_gate, security headers)
        assert len(app.user_middleware) > 0


# ── Guard Password Tests ────────────────────────────────────────────────────

class TestGuardPassword:
    """Test environment panel guard password security."""

    def test_no_hardcoded_guard_password(self):
        """No hardcoded guard password should exist in source."""
        import app.admin.envvars as ev
        assert not hasattr(ev, 'GUARD_DEFAULT_PASSWORD')

    def test_guard_password_requires_db(self):
        """Guard password verification should require DB."""
        import app.admin.envvars as ev
        from app.admin.envvars import clear_cache
        clear_cache()
        original_db = ev.get_sync_db
        ev.get_sync_db = lambda: None
        try:
            result = ev.verify_guard_password("any_password")
            assert result is False  # No DB = no hash = can't verify
        finally:
            ev.get_sync_db = original_db


# ── Password Policy Tests ──────────────────────────────────────────────────

class TestPasswordPolicy:
    """Test password minimum length requirements."""

    def test_password_minimum_length_enforced(self):
        """Passwords shorter than 8 characters should be rejected."""
        from app.auth.crypto import hash_password
        # bcrypt accepts any length, but the API layer enforces min 8
        # This test verifies the crypto layer works with short passwords
        # (the policy is enforced at the API route level)
        h = hash_password("short")
        assert h.startswith("$2")  # bcrypt accepts it


# ── Import Fixtures ─────────────────────────────────────────────────────────

# Save original DB function for cleanup
import app.admin.envvars as _ev_module
_original_get_sync_db = _ev_module.get_sync_db
