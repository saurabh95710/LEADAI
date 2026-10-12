"""
Email delivery over SMTP: SSL (465) and STARTTLS (587), readable errors, and
the Super Admin setup status, which never reveals the password.

Run:  python -m pytest tests/test_email_smtp.py -q -p no:cacheprovider
"""
import smtplib
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import TEST_SUPERADMIN_PASSWORD, as_superadmin

SUPER = "platform-owner@leadai.example"


def _env(**values):
    from app.admin.envvars import _CACHE
    _CACHE.clear()
    return patch.dict("os.environ", {k: str(v) for k, v in values.items()})


@pytest.mark.parametrize("port,mode,expected", [
    (465, "auto", "ssl"), (587, "auto", "starttls"), (25, "none", "none"),
    (2525, "ssl", "ssl"), (587, "bogus", "starttls"),
])
def test_security_follows_the_port_unless_set(port, mode, expected):
    from app.events.email import smtp_config
    with _env(SMTP_HOST="smtp.example.com", SMTP_PORT=port, SMTP_SECURITY=mode):
        assert smtp_config()["security"] == expected


def test_port_465_uses_implicit_ssl_and_logs_in():
    from app.events import email
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    with _env(SMTP_HOST="smtp.hostinger.com", SMTP_PORT=465, SMTP_USER="hi@acme.in",
              SMTP_PASSWORD="pw", SMTP_FROM="hi@acme.in", SMTP_SECURITY="auto"), \
            patch.object(email.smtplib, "SMTP_SSL", return_value=smtp) as ssl_cls, \
            patch.object(email.smtplib, "SMTP") as plain_cls:
        assert email.send_email("buyer@example.com", "Hello", "Body", kind="test") == "sent"
    ssl_cls.assert_called_once()
    plain_cls.assert_not_called()
    smtp.starttls.assert_not_called()
    smtp.login.assert_called_once_with("hi@acme.in", "pw")
    smtp.send_message.assert_called_once()


def test_port_587_upgrades_with_starttls():
    from app.events import email
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    with _env(SMTP_HOST="smtp.gmail.com", SMTP_PORT=587, SMTP_USER="", SMTP_FROM="a@b.co", SMTP_SECURITY="auto"), \
            patch.object(email.smtplib, "SMTP", return_value=smtp), \
            patch.object(email.smtplib, "SMTP_SSL") as ssl_cls:
        assert email.send_email("x@example.com", "Hi", "Body") == "sent"
    ssl_cls.assert_not_called()
    smtp.starttls.assert_called_once()
    smtp.login.assert_not_called()


def test_a_rejected_password_is_explained():
    from app.db.mongo import get_sync_db
    from app.events import email
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad credentials")
    with _env(SMTP_HOST="smtp.gmail.com", SMTP_PORT=587, SMTP_USER="u", SMTP_PASSWORD="p", SMTP_FROM="a@b.co"), \
            patch.object(email.smtplib, "SMTP", return_value=smtp):
        assert email.send_email("x@example.com", "Hi", "Body", kind="authfail") == "failed"
    doc = get_sync_db()[email.COLLECTION].find_one({"kind": "authfail"}, sort=[("created_at", -1)])
    assert "app password" in doc["error"] and "SMTP_PASSWORD" in doc["error"]


def test_super_admin_sees_setup_status_without_the_password():
    from fastapi.testclient import TestClient
    as_superadmin(SUPER)
    with _env(SMTP_HOST="smtp.hostinger.com", SMTP_PORT=465, SMTP_USER="hi@acme.in",
              SMTP_PASSWORD="Top-Secret-Mail-Pw", SMTP_FROM="", PUBLIC_BASE_URL=""), \
            patch("app.admin.settings.is_maintenance_enabled", return_value=False):
        from app.main import app
        with TestClient(app) as client:
            r = client.post("/api/auth/login", json={"email": SUPER, "password": TEST_SUPERADMIN_PASSWORD,
                                                     "scope": "admin"})
            assert r.status_code == 200, r.text
            res = client.get("/api/super-admin/messaging")
    assert res.status_code == 200, res.text
    assert "Top-Secret-Mail-Pw" not in res.text
    em = res.json()["email"]
    assert em["configured"] and em["security"] == "ssl" and em["port"] == 465 and em["password_set"]
    assert set(em["missing"]) == {"SMTP_FROM", "PUBLIC_BASE_URL"}
    assert {"queued", "sent", "failed"} <= set(em["outbox"])


def test_values_only_in_env_file_reach_the_app(tmp_path, monkeypatch):
    """SMTP_* / RAZORPAY_* / PUBLIC_BASE_URL aren't Settings fields: they must
    still be read from .env, and a real environment variable wins."""
    from app.config import load_env_file
    f = tmp_path / ".env"
    f.write_text("SMTP_HOST=smtp.from-file.example\nPUBLIC_BASE_URL=https://file.example\n", encoding="utf-8")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://real-env.example")
    try:
        assert load_env_file(str(f))
        import os
        assert os.environ["SMTP_HOST"] == "smtp.from-file.example"
        assert os.environ["PUBLIC_BASE_URL"] == "https://real-env.example"
    finally:
        import os
        os.environ.pop("SMTP_HOST", None)



def test_super_admin_update_email_settings():
    from fastapi.testclient import TestClient
    from app.events.email import smtp_config, public_base_url
    from app.db.mongo import get_sync_db
    as_superadmin(SUPER)
    with _env(SMTP_HOST="", SMTP_PORT=587, SMTP_USER="", SMTP_PASSWORD=""), \
            patch("app.admin.settings.is_maintenance_enabled", return_value=False):
        from app.main import app
        with TestClient(app) as client:
            r = client.post("/api/auth/login", json={"email": SUPER, "password": TEST_SUPERADMIN_PASSWORD,
                                                     "scope": "admin"})
            assert r.status_code == 200, r.text

            # Test validation errors
            bad_sec = client.put("/api/super-admin/messaging/email", json={"smtp_security": "invalid_mode"})
            assert bad_sec.status_code == 422

            bad_port = client.put("/api/super-admin/messaging/email", json={"smtp_port": 99999})
            assert bad_port.status_code == 422

            # Valid update
            payload = {
                "smtp_host": "smtp.gmail.com",
                "smtp_port": 587,
                "smtp_security": "starttls",
                "smtp_user": "admin@mycompany.com",
                "smtp_password": "MySuperSecret16CharPassword",
                "smtp_from": "MyCompany <no-reply@mycompany.com>",
                "public_base_url": "https://portal.mycompany.com",
            }
            res = client.put("/api/super-admin/messaging/email", json=payload)
            assert res.status_code == 200, res.text
            data = res.json()
            assert data["success"] is True
            em = data["email"]
            assert em["configured"] is True
            assert em["host"] == "smtp.gmail.com"
            assert em["port"] == 587
            assert em["security"] == "starttls"
            assert em["user"] == "admin@mycompany.com"
            assert em["password_set"] is True
            assert em["from"] == "MyCompany <no-reply@mycompany.com>"
            assert em["public_base_url"] == "https://portal.mycompany.com"
            # Password never in response
            assert "MySuperSecret16CharPassword" not in res.text

    try:
        # Runtime consumer reads transparently decrypted password
        from app.admin.envvars import get_envvar_str
        from app.services import secret_box
        cfg = smtp_config()
        assert cfg["host"] == "smtp.gmail.com"
        assert cfg["password_set"] is True
        assert get_envvar_str("SMTP_PASSWORD") == "MySuperSecret16CharPassword"
        assert public_base_url() == "https://portal.mycompany.com"

        # Encrypted sealed doc in db
        db = get_sync_db()
        doc = db.env_overrides.find_one({"_id": "SMTP_PASSWORD"})
        assert doc is not None
        assert doc["value"] != "MySuperSecret16CharPassword"
        assert secret_box.is_encrypted(doc["value"]) is True
    finally:
        db = get_sync_db()
        if db is not None:
            for k in ("SMTP_HOST", "SMTP_PORT", "SMTP_SECURITY", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "PUBLIC_BASE_URL"):
                db.env_overrides.delete_one({"_id": k})
        from app.admin.envvars import _CACHE
        _CACHE.clear()

