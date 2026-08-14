"""
Acceptance tests for admin sign-in: credential verification (constant-time,
hash-based), the signed session cookie (tamper + expiry protection) and the
per-IP brute-force throttle.
"""
from app.auth import service
from app.config import get_settings


def _patch():
    settings = get_settings()
    settings.admin_email = "admin@gmail.com"
    settings.admin_password_hash = (
        "a36aef5a11c4073fbe60314fc9df530a9d5f986533594d1f5190742ff9e0e408")
    settings.session_secret = "test-secret"
    settings.session_ttl_days = 7


def test_correct_credentials():
    _patch()
    user = service.verify_admin_login("admin@gmail.com", "Admin@2026")
    assert user is not None
    assert user["email"] == "admin@gmail.com"
    assert user["role"] == "super_admin"  # env account is the recovery super-admin


def test_email_case_insensitive():
    _patch()
    assert service.verify_admin_login(" ADMIN@GMAIL.COM ", "Admin@2026") is not None


def test_wrong_password():
    _patch()
    assert service.verify_admin_login("admin@gmail.com", "wrong") is None
    assert service.verify_admin_login("admin@gmail.com", "") is None


def test_wrong_email():
    _patch()
    assert service.verify_admin_login("hacker@example.com", "Admin@2026") is None


def test_cookie_roundtrip():
    _patch()
    value = service.build_session_value({"email": "admin@gmail.com", "name": "Admin", "role": "admin"})
    user = service.parse_session_value(value)
    assert user is not None
    assert user["email"] == "admin@gmail.com"


def test_cookie_tamper_detected():
    _patch()
    value = service.build_session_value({"email": "admin@gmail.com", "name": "Admin", "role": "admin"})
    payload, sig = value.rsplit(".", 1)
    forged = service._b64e(
        service._b64d(payload).replace(b"admin@gmail.com", b"evil@example.com"))
    assert service.parse_session_value(f"{forged}.{sig}") is None


def test_cookie_garbage_rejected():
    _patch()
    assert service.parse_session_value("not-a-cookie") is None
    assert service.parse_session_value("") is None
    assert service.parse_session_value(None) is None


def test_cookie_expiry():
    _patch()
    service.get_settings().session_ttl_days = 0
    value = service.build_session_value({"email": "admin@gmail.com", "name": "Admin", "role": "admin"})
    assert service.parse_session_value(value) is None


def test_login_throttle():
    _patch()
    service.reset_login_attempts("1.2.3.4")
    for _ in range(5):
        assert service.login_allowed("1.2.3.4")
        service.record_login_failure("1.2.3.4")
    assert not service.login_allowed("1.2.3.4")
    assert service.login_denied_seconds("1.2.3.4") > 0
    service.reset_login_attempts("1.2.3.4")
    assert service.login_allowed("1.2.3.4")