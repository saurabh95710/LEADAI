"""
Signing up on the website starts a free trial at once (default: 3 days, a
limited number of tokens) and signs the new owner in to the user panel — no
demo request waits for the Super Admin. With demo settings → auto-approve off,
the old approval workflow applies (covered in test_step1_security.py).

Run:  python -m pytest tests/test_self_serve_trial.py -q -p no:cacheprovider
"""
from datetime import timedelta
from unittest.mock import patch

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.auth.service import COOKIE_NAME
from tests.conftest import REAL_MIGRATE_SELF_SERVE_TRIAL, SEED_AUTO_APPROVE, SEED_DURATION_DAYS

PASSWORD = "Trial-Pass-2026!"
SIGNUP = {"name": "Riya", "email": "riya@shop.test", "company": "Riya Homes", "password": PASSWORD,
          "phone": "+91 98000 00000", "accepted_terms": True}


@pytest.fixture
def env():
    with patch("app.admin.settings.is_maintenance_enabled", return_value=False):
        from app.main import app
        with TestClient(app) as client:
            from app.db.mongo import get_sync_db
            from app.lifecycle.config import update_demo_config
            update_demo_config({"auto_approve": True, "duration_days": 3, "tokens": 150}, "test")
            yield client, get_sync_db()


def test_defaults_are_a_three_day_self_serve_trial():
    assert SEED_AUTO_APPROVE is True and SEED_DURATION_DAYS == 3


def test_signup_starts_the_trial_and_signs_in(env):
    client, db = env
    r = client.post("/api/auth/signup", json=SIGNUP)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved" and body["signed_in"] is True and body["redirect"] == "/user"
    assert body["trial"] == {"days": 3, "tokens": 150}
    assert COOKIE_NAME in r.cookies
    # straight into the user panel with the same browser session
    me = client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["user"]["email"] == "riya@shop.test"
    assert client.get("/api/billing/usage").status_code == 200
    req = db.demo_requests.find_one({"email": "riya@shop.test"})
    assert req["status"] == "approved" and req["approved_by"] == "system:auto_approve"
    org = db.organizations.find_one({"_id": ObjectId(req["organization_id"])})
    assert org["status"] == "demo"
    length = org["demo"]["expires_at"] - org["demo"]["started_at"]
    assert timedelta(days=3) - timedelta(minutes=1) < length <= timedelta(days=3)
    assert db.users.find_one({"email": "riya@shop.test"})["status"] == "active"
    # limited tokens, expiring with the trial
    from app.billing.tokens import get_balance
    bal = get_balance(req["organization_id"])
    assert bal["allocated"] == 150 and bal["remaining"] == 150 and bal["source"] == "demo" and bal["expires_at"]


def test_super_admin_is_told_but_has_nothing_to_approve(env):
    client, db = env
    client.post("/api/auth/signup", json=SIGNUP)
    assert db.notifications.find_one({"audience": "super_admin", "title": "New free trial"})
    assert not db.notifications.find_one({"type": "demo_requested"})          # no approval queue item
    assert db.notifications.find_one({"type": "demo_approved", "title": "Your free trial has started"})


def test_same_email_cannot_start_a_second_trial(env):
    client, db = env
    assert client.post("/api/auth/signup", json=SIGNUP).status_code == 200
    client.cookies.clear()
    r = client.post("/api/auth/signup", json={**SIGNUP, "company": "Another"})
    assert r.status_code == 400


def test_approval_workflow_when_self_serve_is_off(env):
    client, db = env
    from app.lifecycle.config import update_demo_config
    update_demo_config({"auto_approve": False}, "test")
    r = client.post("/api/auth/signup", json=SIGNUP)
    assert r.json()["status"] == "pending" and r.json()["signed_in"] is False
    assert COOKIE_NAME not in r.cookies
    assert db.notifications.find_one({"type": "demo_requested"})


def test_website_knows_it_is_a_free_trial(env):
    client, db = env
    trial = client.get("/api/public/config").json()["trial"]
    assert trial == {"self_serve": True, "days": 3, "credits": 150}


def test_stored_settings_switch_once_to_the_self_serve_trial(env):
    client, db = env
    from app.lifecycle.config import clear_cache, get_demo_config, update_demo_config
    db.platform_config.update_one({"_id": "demo"}, {"$set": {"value.duration_days": 7, "value.auto_approve": False},
                                                    "$unset": {"migrations": ""}})
    clear_cache()
    assert REAL_MIGRATE_SELF_SERVE_TRIAL(db) is True
    cfg = get_demo_config()
    assert cfg["duration_days"] == 3 and cfg["auto_approve"] is True and cfg["tokens"] == 150   # tokens kept
    # a later Super Admin choice is kept: the switch runs only once
    update_demo_config({"auto_approve": False, "duration_days": 5}, "superadmin")
    assert REAL_MIGRATE_SELF_SERVE_TRIAL(db) is False
    cfg = get_demo_config()
    assert cfg["auto_approve"] is False and cfg["duration_days"] == 5
