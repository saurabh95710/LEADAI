"""
Messaging gateway: SMS (+ the existing email outbox), automatic welcome /
login-alert messages, password reset by SMS code, and messages from the Super
Admin and organization admins.

Run:  python -m pytest tests/test_messaging.py -q -p no:cacheprovider
"""
import re
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.auth.crypto import hash_password
from tests.conftest import TEST_SUPERADMIN_PASSWORD, as_superadmin

SUPER = "platform-owner@leadai.example"
PW = "Member-Pass-2026"
SENT = []          # bodies the fake SMS provider received


def _fake_post(url, *a, **k):
    SENT.append({"url": url, **(k.get("json") or {})})
    r = MagicMock(status_code=200)
    r.headers = {"content-type": "application/json"}
    r.json.return_value = {}
    return r


@pytest.fixture
def env():
    as_superadmin(SUPER)
    SENT.clear()
    with patch("app.admin.settings.is_maintenance_enabled", return_value=False), \
            patch("app.messaging.service._in_background", lambda fn, *a, **k: fn(*a, **k)), \
            patch("app.messaging.sms.httpx.post", side_effect=_fake_post), \
            patch.dict("os.environ", {"SMS_PROVIDER": "webhook", "SMS_WEBHOOK_URL": "https://sms.example/send"}):
        from app.admin.envvars import _CACHE
        _CACHE.clear()
        from app.main import app
        with TestClient(app) as client:
            from app.db.mongo import get_sync_db
            yield client, get_sync_db()
        _CACHE.clear()


def _org(db, name="Acme"):
    return str(db.organizations.insert_one({"name": name, "slug": name.lower(), "status": "active",
                                            "admin_portal_enabled": True, "settings": {},
                                            "created_at": datetime.utcnow()}).inserted_id)


def _user(db, email, org_id, role="owner", phone=None):
    from app.messaging.phone import normalize_phone
    uid = str(db.users.insert_one({"email": email, "name": email.split("@")[0], "status": "active",
                                   "password_hash": hash_password(PW), "default_organization_id": org_id,
                                   "phone": phone, "phone_e164": normalize_phone(phone),
                                   "created_at": datetime.utcnow()}).inserted_id)
    db.organization_members.insert_one({"organization_id": org_id, "user_id": uid, "role": role,
                                        "status": "active", "permissions_override": {}})
    return uid


def _login(client, email, password, scope="site"):
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"email": email, "password": password, "scope": scope})
    assert r.status_code == 200, r.text
    return client


# ── phone numbers ──────────────────────────────────────────────────────────

def test_phone_numbers_become_e164():
    from app.messaging.phone import mask_phone, normalize_phone
    assert normalize_phone("+91 98765 43210") == "+919876543210"
    assert normalize_phone("098765 43210", "+91") == "+919876543210"
    assert normalize_phone("9876543210", "+91") == "+919876543210"
    assert normalize_phone("919876543210", "+91") == "+919876543210"
    assert normalize_phone("0091 98765 43210") == "+919876543210"
    assert normalize_phone("+1 (415) 555-0100") == "+14155550100"
    assert normalize_phone("hello") is None and normalize_phone("12") is None and normalize_phone("") is None
    assert mask_phone("+919876543210").endswith("43210") and "98765" not in mask_phone("+919876543210")


# ── the SMS gateway ────────────────────────────────────────────────────────

def test_sms_outbox_limits_and_masking(env):
    from app.messaging.sms import send_sms
    client, db = env
    assert send_sms("+91 98765 43210", "hello", kind="generic") == "sent"
    assert SENT[-1]["to"] == "+919876543210" and SENT[-1]["message"] == "hello"
    assert send_sms("not a phone", "x") == "invalid"
    # codes are masked in the stored copy, never in the SMS itself
    send_sms("+919876543211", "Your code is 123456.", kind="password_reset_code")
    assert "123456" in SENT[-1]["message"]
    stored = db.sms_outbox.find_one({"to": "+919876543211"})
    assert "123456" not in stored["body"] and stored["status"] == "sent"
    # per-number daily limit stops abuse
    with patch.dict("os.environ", {"SMS_PER_NUMBER_DAILY_LIMIT": "2"}):
        from app.admin.envvars import _CACHE
        _CACHE.clear()
        assert send_sms("+919800000001", "a") == "sent"
        assert send_sms("+919800000001", "b") == "sent"
        assert send_sms("+919800000001", "c") == "limited"
        _CACHE.clear()


def test_without_a_provider_texts_stay_queued(env):
    from app.admin.envvars import _CACHE
    from app.messaging.sms import send_sms
    client, db = env
    with patch.dict("os.environ", {"SMS_PROVIDER": ""}):
        _CACHE.clear()
        n = len(SENT)
        assert send_sms("+919876500000", "hi") == "queued" and len(SENT) == n
    _CACHE.clear()


# ── password reset by SMS code ─────────────────────────────────────────────

def _code_from_last_sms():
    m = re.search(r"\b(\d{6})\b", SENT[-1]["message"])
    assert m, SENT[-1]
    return m.group(1)


def test_password_reset_by_phone(env):
    client, db = env
    org = _org(db)
    _user(db, "riya@shop.test", org, phone="+91 98765 43210")
    generic = client.post("/api/auth/password/forgot", json={"phone": "+91 90000 00000"}).json()   # unknown number
    assert generic["success"] is True and "code" in generic["message"]
    n = len(SENT)
    r = client.post("/api/auth/password/forgot", json={"phone": "98765 43210"})                   # local format
    assert r.status_code == 200 and len(SENT) == n + 1
    code = _code_from_last_sms()
    bad = "000000" if code != "000000" else "111111"
    r = client.post("/api/auth/password/reset", json={"phone": "+919876543210", "code": bad,
                                                      "new_password": "Brand-New-Pass-2026"})
    assert r.status_code == 400 and "tries left" in r.json()["detail"]
    r = client.post("/api/auth/password/reset", json={"phone": "+919876543210", "code": code,
                                                      "new_password": "Brand-New-Pass-2026"})
    assert r.status_code == 200, r.text
    _login(client, "riya@shop.test", "Brand-New-Pass-2026")
    # the code works once only
    r = client.post("/api/auth/password/reset", json={"phone": "+919876543210", "code": code,
                                                      "new_password": "Another-Pass-2026"})
    assert r.status_code == 400


def test_too_many_wrong_codes_kill_the_code(env):
    client, db = env
    _user(db, "amit@shop.test", _org(db), phone="+919811111111")
    client.post("/api/auth/password/forgot", json={"phone": "+919811111111"})
    code = _code_from_last_sms()
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        client.post("/api/auth/password/reset", json={"phone": "+919811111111", "code": wrong,
                                                      "new_password": "Brand-New-Pass-2026"})
    r = client.post("/api/auth/password/reset", json={"phone": "+919811111111", "code": code,
                                                      "new_password": "Brand-New-Pass-2026"})
    assert r.status_code == 400


def test_shared_phone_number_gets_no_code(env):
    client, db = env
    org = _org(db)
    _user(db, "a@shop.test", org, phone="+919822222222")
    _user(db, "b@shop.test", org, phone="+919822222222")
    client.post("/api/auth/password/forgot", json={"phone": "+919822222222"})
    assert "several accounts" in SENT[-1]["message"]
    assert not db.password_resets.find_one({"phone_e164": "+919822222222"})


# ── automatic messages ─────────────────────────────────────────────────────

def test_sign_up_texts_a_welcome(env):
    client, db = env
    from app.lifecycle.config import update_demo_config
    update_demo_config({"auto_approve": True}, "test")
    r = client.post("/api/auth/signup", json={"name": "Neha", "email": "neha@shop.test", "company": "Neha Homes",
                                              "password": "Trial-Pass-2026!", "phone": "98765 00001",
                                              "accepted_terms": True})
    assert r.status_code == 200, r.text
    assert db.users.find_one({"email": "neha@shop.test"})["phone_e164"] == "+919876500001"
    welcome = db.sms_outbox.find_one({"kind": "welcome", "to": "+919876500001"})
    assert welcome and "free trial" in welcome["body"]


def test_welcome_text_can_be_switched_off(env):
    client, db = env
    from app.lifecycle.config import update_demo_config
    from app.messaging.service import update_auto_settings
    update_demo_config({"auto_approve": True}, "test")
    update_auto_settings({"welcome_sms": False}, "test", db)
    client.post("/api/auth/signup", json={"name": "Om", "email": "om@shop.test", "company": "Om Co",
                                          "password": "Trial-Pass-2026!", "phone": "98765 00002",
                                          "accepted_terms": True})
    assert not db.sms_outbox.find_one({"kind": "welcome", "to": "+919876500002"})


def test_login_alert_by_email_and_sms_once_per_window(env):
    client, db = env
    _user(db, "kiran@shop.test", _org(db), phone="+919833333333")
    _login(client, "kiran@shop.test", PW)
    assert db.email_outbox.count_documents({"kind": "login_alert", "to": "kiran@shop.test"}) == 1
    assert db.sms_outbox.count_documents({"kind": "login_alert", "to": "+919833333333"}) == 1
    _login(client, "kiran@shop.test", PW)                                       # within 10 minutes
    assert db.email_outbox.count_documents({"kind": "login_alert", "to": "kiran@shop.test"}) == 1


# ── Super Admin ────────────────────────────────────────────────────────────

def test_super_admin_gateway_settings_keep_secrets_encrypted(env):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    r = sa.put("/api/super-admin/messaging/sms", json={"provider": "twilio", "twilio_account_sid": "AC123",
                                                        "twilio_auth_token": "tw-secret-token-999",
                                                        "twilio_from": "+14155550100"})
    assert r.status_code == 200, r.text
    assert "tw-secret-token-999" not in r.text and r.json()["sms"]["secrets_set"]["TWILIO_AUTH_TOKEN"] is True
    assert db.env_overrides.find_one({"_id": "TWILIO_AUTH_TOKEN"})["value"].startswith("fernet:")
    overview = sa.get("/api/super-admin/messaging").json()
    assert "tw-secret-token-999" not in str(overview) and overview["sms"]["provider"] == "twilio"
    # empty secret = keep the stored one
    sa.put("/api/super-admin/messaging/sms", json={"twilio_auth_token": ""})
    assert db.env_overrides.find_one({"_id": "TWILIO_AUTH_TOKEN"})["value"].startswith("fernet:")
    assert sa.put("/api/super-admin/messaging/sms", json={"provider": "carrier-pigeon"}).status_code == 422
    assert sa.put("/api/super-admin/messaging/sms", json={"webhook_url": "http://x.test"}).status_code == 422
    for k in ("SMS_PROVIDER", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM"):
        db.env_overrides.delete_one({"_id": k})


def test_super_admin_messages_people(env):
    client, db = env
    org_a, org_b = _org(db, "Alpha"), _org(db, "Beta")
    _user(db, "owner@alpha.test", org_a, "owner", phone="+919844444441")
    _user(db, "member@alpha.test", org_a, "member", phone="+919844444442")
    _user(db, "owner@beta.test", org_b, "owner")
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    people = sa.get("/api/super-admin/messaging/people?q=alpha").json()["people"]
    assert {p["email"] for p in people} == {"owner@alpha.test", "member@alpha.test"}
    assert all("44444" not in p["phone"][:-5] for p in people)                           # masked
    r = sa.post("/api/super-admin/messages", json={"audience": "org_admins", "channels": ["email", "sms"],
                                                    "subject": "Maintenance tonight", "body": "We'll be down 10 min."})
    assert r.status_code == 200, r.text
    msg = sa.get(f"/api/super-admin/messages/{r.json()['message']['id']}").json()["message"]
    assert msg["recipient_count"] == 2 and msg["status"] == "done"
    assert msg["counts"]["email"]["sent"] + msg["counts"]["email"]["queued"] == 2
    assert msg["counts"]["sms"]["sent"] == 1 and msg["counts"]["sms"]["skipped"] == 1   # beta owner has no phone
    assert {d["to"] for d in msg["deliveries"] if d["channel"] == "email"} == {"owner@alpha.test", "owner@beta.test"}
    assert sa.post("/api/super-admin/messages", json={"audience": "org_admins", "channels": ["email"],
                                                      "body": "no subject"}).status_code == 422
    assert db.audit_logs.find_one({"action": "messaging.message_sent"})


def test_super_admin_test_send(env):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    r = sa.post("/api/super-admin/messaging/test", json={"channel": "sms", "to": "+919855555555"})
    assert r.status_code == 200 and r.json()["status"] == "sent"
    assert sa.post("/api/super-admin/messaging/test", json={"channel": "sms", "to": "nope"}).status_code == 422


# ── organization admins ────────────────────────────────────────────────────

def test_org_admin_messages_only_their_own_team(env):
    client, db = env
    org_a, org_b = _org(db, "Alpha"), _org(db, "Beta")
    _user(db, "owner@alpha.test", org_a, "owner", phone="+919866666661")
    mate = _user(db, "mate@alpha.test", org_a, "member", phone="+919866666662")
    stranger = _user(db, "x@beta.test", org_b, "owner", phone="+919866666663")
    c = _login(client, "owner@alpha.test", PW)
    r = c.post("/api/org-admin/messages", json={"user_ids": [mate, stranger], "channels": ["sms"],
                                                "body": "Team meeting at 5"})
    assert r.status_code == 200, r.text
    assert r.json()["message"]["recipient_count"] == 1                                  # the stranger is dropped
    assert db.sms_outbox.find_one({"to": "+919866666662", "kind": "admin_message"})
    assert not db.sms_outbox.find_one({"to": "+919866666663", "kind": "admin_message"})
    r = c.post("/api/org-admin/messages", json={"all_members": True, "channels": ["email"], "subject": "Hi",
                                                "body": "Hello team"})
    assert r.json()["message"]["recipient_count"] == 2
    assert len(c.get("/api/org-admin/messages").json()["messages"]) == 2
    people = c.get("/api/org-admin/messaging/people").json()["people"]
    assert {p["email"] for p in people} == {"owner@alpha.test", "mate@alpha.test"}
    assert ObjectId.is_valid(mate)


def test_reset_codes_have_their_own_daily_allowance(env):
    """Sign-in alerts and welcome texts can never use up a person's reset codes."""
    from app.admin.envvars import _CACHE
    from app.messaging.sms import send_sms
    client, db = env
    with patch.dict("os.environ", {"SMS_PER_NUMBER_DAILY_LIMIT": "2"}):
        _CACHE.clear()
        assert send_sms("+919877777777", "alert 1", kind="login_alert") == "sent"
        assert send_sms("+919877777777", "alert 2", kind="login_alert") == "sent"
        assert send_sms("+919877777777", "alert 3", kind="login_alert") == "limited"
        assert send_sms("+919877777777", "code 123456", kind="password_reset_code") == "sent"
    _CACHE.clear()


def test_reset_rate_limit_is_reported_honestly(env):
    from app.api.routes import auth as auth_routes
    client, db = env
    for _ in range(auth_routes._MAX_RESET_ATTEMPTS):
        assert client.post("/api/auth/password/forgot", json={"phone": "+919888888888"}).status_code == 200
    r = client.post("/api/auth/password/forgot", json={"phone": "+919888888888"})
    assert r.status_code == 429 and "Too many" in r.json()["detail"]
    assert client.post("/api/auth/password/forgot", json={"email": "x@y.test"}).status_code == 429


def test_long_message_without_sms_text_is_kept_to_two_texts():
    from app.messaging.service import _sms_from
    text = _sms_from("Update", "word " * 200)
    assert len(text) <= 306 and text.endswith("…") and text.startswith("Update: word")


def test_secrets_can_be_cleared_and_other_provider_settings_stay_visible(env):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    sa.put("/api/super-admin/messaging/sms", json={"provider": "webhook", "webhook_url": "https://gw.example/sms",
                                                    "webhook_token": "wh-secret-1"})
    r = sa.put("/api/super-admin/messaging/sms", json={"provider": "twilio"})
    assert r.json()["sms"]["webhook_url"] == "https://gw.example/sms"        # kept and still shown
    r = sa.put("/api/super-admin/messaging/sms", json={"clear_secrets": ["webhook_token"]})
    assert r.status_code == 200 and r.json()["sms"]["secrets_set"]["SMS_WEBHOOK_TOKEN"] is False
    assert sa.put("/api/super-admin/messaging/sms", json={"clear_secrets": ["nope"]}).status_code == 422
    assert sa.post("/api/super-admin/messages", json={"channels": [], "audience": "org_admins",
                                                      "body": "x"}).json()["detail"] == "Choose email and/or SMS"
    for k in ("SMS_PROVIDER", "SMS_WEBHOOK_URL", "SMS_WEBHOOK_TOKEN"):
        db.env_overrides.delete_one({"_id": k})
