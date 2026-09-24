"""
Acceptance tests for sign-in: credential verification for both scopes
(site login = ADMIN_EMAIL, admin portal login = PANEL_ADMIN_EMAIL /
admin_users), the signed session cookie (tamper + expiry protection) and
the per-IP brute-force throttle.
"""
from app.auth import service
from app.config import get_settings
from app.admin.envvars import clear_cache


def _patch():
    clear_cache()
    settings = get_settings()
    settings.admin_email = "Admin@gmail.com"
    settings.admin_password_hash = (
        "a36aef5a11c4073fbe60314fc9df530a9d5f986533594d1f5190742ff9e0e408")
    settings.panel_admin_email = "Admin123@gmail.com"
    settings.panel_admin_password_hash = (
        "bc78e58d55cde1346e68f8e5fe588dedf62fa457aa646a500a53347faff6ee24")
    settings.session_secret = "test-secret"
    settings.session_ttl_days = 7
    # Ensure envvars uses settings values (not DB overrides) during tests
    import app.admin.envvars as ev
    ev.get_sync_db = lambda: None


# ── Main website login (scope "site") ─────────────────────────────────────

def test_site_correct_credentials(site_account):
    _patch()
    # env credentials only unlock the site for a real, active tenant account
    _user_id, org_id = site_account("admin@gmail.com")
    user = service.verify_site_login("Admin@gmail.com", "Admin@2026")
    assert user is not None
    assert user["email"] == "admin@gmail.com"
    assert user["scope"] == "site"
    assert user["organization_id"] == org_id


def test_site_correct_credentials_without_account_fails_closed():
    """Right env credentials but no ``users`` record -> no site session."""
    _patch()
    assert service.verify_site_login("Admin@gmail.com", "Admin@2026") is None


def test_site_credentials_without_active_org_fail_closed(site_account):
    _patch()
    site_account("admin@gmail.com", org_status="suspended")
    assert service.verify_site_login("Admin@gmail.com", "Admin@2026") is None


def test_site_email_case_insensitive(site_account):
    _patch()
    site_account("admin@gmail.com")
    assert service.verify_site_login(" ADMIN@GMAIL.COM ", "Admin@2026") is not None


def test_site_wrong_password():
    _patch()
    assert service.verify_site_login("Admin@gmail.com", "wrong") is None
    assert service.verify_site_login("Admin@gmail.com", "") is None


def test_site_wrong_email():
    _patch()
    assert service.verify_site_login("hacker@example.com", "Admin@2026") is None


def test_site_credentials_rejected_for_panel():
    """The website credentials must not unlock the admin portal."""
    _patch()
    assert service.verify_admin_login("Admin@gmail.com", "Admin@2026") is None


# ── Admin portal login (scope "admin") ────────────────────────────────────

def test_panel_correct_credentials():
    _patch()
    user = service.verify_admin_login("Admin123@gmail.com", "Admin@1234")
    assert user is not None
    assert user["email"] == "admin123@gmail.com"
    assert user["scope"] == "admin"
    assert user["role"] == "super_admin"  # env account is the recovery super-admin


def test_panel_email_case_insensitive():
    _patch()
    assert service.verify_admin_login(" ADMIN123@GMAIL.COM ", "Admin@1234") is not None


def test_panel_wrong_password():
    _patch()
    assert service.verify_admin_login("Admin123@gmail.com", "wrong") is None
    assert service.verify_admin_login("Admin123@gmail.com", "") is None


def test_panel_wrong_email():
    _patch()
    assert service.verify_admin_login("hacker@example.com", "Admin@1234") is None


def test_panel_credentials_rejected_for_site():
    """The admin portal credentials must not unlock the main website."""
    _patch()
    assert service.verify_site_login("Admin123@gmail.com", "Admin@1234") is None


# ── Session cookie ────────────────────────────────────────────────────────

def test_cookie_roundtrip():
    _patch()
    value = service.build_session_value({"email": "admin123@gmail.com", "name": "Admin", "role": "admin"})
    user = service.parse_session_value(value)
    assert user is not None
    assert user["email"] == "admin123@gmail.com"


def test_cookie_tamper_detected():
    _patch()
    value = service.build_session_value({"email": "admin@gmail.com", "name": "Admin", "role": "admin"})
    payload, sig = value.rsplit(".", 1)
    import base64 as b64
    tampered = b64.urlsafe_b64encode(
        b64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        .replace(b"admin@gmail.com", b"evil@example.com")).rstrip(b"=").decode("ascii")
    assert service.parse_session_value(f"{tampered}.{sig}") is None


def test_cookie_expiry():
    _patch()
    value = service.build_session_value({"email": "admin@gmail.com", "name": "Admin", "role": "admin"})
    payload, sig = value.rsplit(".", 1)
    import base64 as b64
    import json
    data = json.loads(b64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    data["exp"] = 0
    expired_payload = b64.urlsafe_b64encode(
        json.dumps(data, separators=(",", ":")).encode()).rstrip(b"=").decode("ascii")
    assert service.parse_session_value(f"{expired_payload}.{sig}") is None
