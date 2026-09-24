"""
Step 2 cross-portal integration fixes — in-memory MongoDB only (conftest).

Proves:
  * every audit entry written while serving a request names the signed-in
    actor, even when the handler passes no ``user`` (legacy /api/admin calls);
  * change_subscription_plan / extend_trial never activate or override a
    pending / paid subscription (no bypass of checkout + confirmation), on
    the legacy /api/admin routes as well;
  * /api/admin/ai/prompts works (no infinite recursion) and resolves
    ObjectId prompt ids;
  * support-ticket notifications deep-link to the Super Admin ticket view.
"""
from unittest.mock import patch

import pytest
from bson import ObjectId
from fastapi import HTTPException
from fastapi.testclient import TestClient

from tests.test_super_admin_portal2 import NOW, _cookie, _org, _super, _user


@pytest.fixture
def env():
    with patch("app.admin.settings.is_maintenance_enabled", return_value=False):
        from app.main import app
        with TestClient(app) as client:
            from app.db.mongo import get_sync_db
            yield client, get_sync_db()


# ── audit actor ──────────────────────────────────────────────────────────────

def test_build_record_uses_bound_request_actor():
    from app.admin.audit import build_record, reset_request_actor, set_request_actor
    token = set_request_actor({"user_id": "u1", "email": "root@platform.test", "role": "super_admin",
                               "impersonated_by": None}, "10.0.0.1", "pytest")
    try:
        rec = build_record("x.y", "test")
        assert rec["actor_email"] == "root@platform.test" and rec["actor_user_id"] == "u1"
        assert rec["ip"] == "10.0.0.1"
        # an explicit actor still wins
        assert build_record("x.y", "test", user="other@example.com")["actor_email"] == "other@example.com"
    finally:
        reset_request_actor(token)
    assert build_record("x.y", "test")["actor_email"] == ""


def test_legacy_admin_route_audit_names_actor(env):
    client, db = env
    cookie = _super(db)
    org = _org(db, "Audit Org")
    r = client.post(f"/api/admin/organizations/{org}/suspend", cookies=cookie, json={"reason": "abuse test"})
    assert r.status_code == 200, r.text
    row = db.audit_logs.find_one({"action": "organization.suspend"})
    assert row and row["actor_email"] == "root@platform.test"


# ── subscription bypass guards ───────────────────────────────────────────────

def _sub(db, org, status):
    return db.subscriptions.insert_one({"organization_id": org, "plan_id": "pro", "status": status,
                                        "created_at": NOW, "updated_at": NOW}).inserted_id


@pytest.mark.parametrize("status", ["pending_payment", "payment_received",
                                    "pending_admin_confirmation", "suspended", "cancelled", "expired"])
async def test_change_plan_refuses_non_live_subscription(status):
    from app.billing.plan_admin import create_plan
    from app.billing.subscriptions import change_subscription_plan
    from app.db.mongo import get_async_db, get_sync_db
    db = get_sync_db()
    await create_plan(get_async_db(), {"name": "Pro", "slug": "pro", "price_monthly": 10,
                                       "price_yearly": 100, "currency": "USD",
                                       "limits": {"team_members": 10}}, actor="test")
    org = _org(db, "Pending Org", status="pending")
    sid = _sub(db, org, status)
    with pytest.raises(HTTPException) as exc:
        await change_subscription_plan(org, "pro")
    assert exc.value.status_code == 409
    assert db.subscriptions.find_one({"_id": sid})["status"] == status
    assert db.organizations.find_one({"_id": ObjectId(org)})["status"] == "pending"


@pytest.mark.parametrize("status", ["pending_admin_confirmation", "active", "suspended"])
async def test_extend_trial_refuses_paid_or_pending(status):
    from app.billing.subscriptions import extend_trial
    from app.db.mongo import get_sync_db
    db = get_sync_db()
    org = _org(db, "Trial Org")
    sid = _sub(db, org, status)
    with pytest.raises(HTTPException) as exc:
        await extend_trial(org, 7)
    assert exc.value.status_code == 409
    assert db.subscriptions.find_one({"_id": sid})["status"] == status


def test_legacy_admin_change_plan_cannot_activate_pending(env):
    client, db = env
    cookie = _super(db)
    org = _org(db, "Legacy Org", status="pending")
    sid = _sub(db, org, "pending_admin_confirmation")
    r = client.post(f"/api/admin/subscriptions/{sid}/change-plan", cookies=cookie,
                    json={"plan_slug": "pro", "billing_cycle": "monthly"})
    assert r.status_code in (404, 409), r.text
    assert db.subscriptions.find_one({"_id": sid})["status"] == "pending_admin_confirmation"


# ── admin AI prompts ─────────────────────────────────────────────────────────

def test_admin_ai_prompts_crud(env):
    client, db = env
    cookie = _super(db)
    r = client.post("/api/admin/ai/prompts", cookies=cookie, json={"name": "lead", "template": "Hi {x}"})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    assert client.get("/api/admin/ai/prompts", cookies=cookie).json()["total"] == 1
    got = client.get(f"/api/admin/ai/prompts/{pid}", cookies=cookie)
    assert got.status_code == 200 and got.json()["_id"] == pid
    assert client.put(f"/api/admin/ai/prompts/{pid}", cookies=cookie,
                      json={"template": "v2"}).status_code == 200
    assert db.ai_prompts.find_one({"_id": ObjectId(pid)})["template"] == "v2"
    assert client.get(f"/api/admin/ai/prompts/{ObjectId()}", cookies=cookie).status_code == 404


# ── support ticket deep links ────────────────────────────────────────────────

def test_support_ticket_notification_links_to_super_admin(env):
    client, db = env
    org = _org(db, "Ticket Org", admin_portal_enabled=True)
    uid = _user(db, "owner@example.com", org, role="owner")
    cookie = _cookie({"user_id": uid, "email": "owner@example.com", "name": "owner", "scope": "site",
                      "organization_id": org, "org_role": "owner"})
    r = client.post("/api/org-admin/support/tickets", cookies=cookie,
                    json={"subject": "Exports failing", "message": "The CSV export returns an error.",
                          "category": "bug", "priority": "normal"})
    assert r.status_code in (200, 201), r.text
    n = db.notifications.find_one({"type": "support_ticket"})
    assert n and n["link"].startswith("/superadmin#/support/") and n["data"]["ticket_id"] in n["link"]


# ── maintenance mode through the real admin API ─────────────────────────────

def test_maintenance_toggle_via_api_gates_customers_not_admins():
    from app.main import app
    from app.db.mongo import get_sync_db
    with TestClient(app) as client:
        db = get_sync_db()
        cookie = _super(db)
        org = _org(db, "Maint Org")
        uid = _user(db, "maint@example.com", org, role="owner")
        site = _cookie({"user_id": uid, "email": "maint@example.com", "name": "m", "scope": "site",
                        "organization_id": org, "org_role": "owner"})
        try:
            r = client.post("/api/admin/maintenance", cookies=cookie,
                            json={"enabled": True, "message": "Back soon"})
            assert r.status_code == 200 and r.json()["enabled"] is True
            blocked = client.get("/api/me/summary", cookies=site)
            assert blocked.status_code == 503 and blocked.json()["message"] == "Back soon"
            assert client.get("/api/super-admin/dashboard", cookies=cookie).status_code == 200
            assert client.get("/health").status_code == 200
            assert client.get("/login").status_code == 200
        finally:
            client.post("/api/admin/maintenance", cookies=cookie, json={"enabled": False})
        assert client.get("/api/me/summary", cookies=site).status_code == 200
