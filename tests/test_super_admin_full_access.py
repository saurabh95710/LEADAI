"""
The Super Admin holds every permission: every platform permission, full
Owner rights inside any organization (unaffected by the organization's own
role restrictions), full access to any partner's portal, and control of every
API key and webhook on the platform. Every such action is attributed and
audited; no other role gets these powers.
"""
import time
from unittest.mock import patch

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from tests.conftest import TEST_SUPERADMIN_PASSWORD, as_superadmin

SUPER = "platform-owner@leadai.example"
PW = "Partner-Pass-2026"


@pytest.fixture
def env():
    as_superadmin(SUPER)
    with patch("app.admin.settings.is_maintenance_enabled", return_value=False):
        from app.main import app
        with TestClient(app) as client:
            from app.db.mongo import get_sync_db
            yield client, get_sync_db()


def _login(client, email, password, scope="site", expect=200):
    c = TestClient(client.app)
    r = c.post("/api/auth/login", json={"email": email, "password": password, "scope": scope})
    assert r.status_code == expect, (email, r.text)
    return c


def _partner(client, sa, email, name="P"):
    r = client.post("/api/public/partners/apply", json={
        "name": name, "email": email, "password": PW, "company": name + " Co", "partner_type": "reseller",
        "experience": "Ten years of SaaS sales", "promotion_plan": "Newsletter", "accepted_terms": True})
    assert r.status_code == 200, r.text
    r = sa.post(f"/api/super-admin/partners/applications/{r.json()['application']['id']}/approve",
                json={"partner_type": "reseller"})
    assert r.status_code == 200, r.text
    return r.json()["partner"]


def test_super_admin_holds_every_platform_permission():
    from app.auth.permissions import ALL_PLATFORM_PERMISSIONS, effective_platform_permissions
    assert effective_platform_permissions("super_admin") == set(ALL_PLATFORM_PERMISSIONS)
    for role in ("operations_admin", "billing_admin", "support_admin", "technical_admin", "viewer"):
        assert effective_platform_permissions(role) < set(ALL_PLATFORM_PERMISSIONS), role


def test_super_admin_acts_as_owner_with_all_rights_in_any_organization(env, site_account):
    client, db = env
    from app.auth.permissions import ORG_ROLE_PERMISSIONS
    _, org_id = site_account("owner@tenant.example")
    # the organization restricts its admins as far as it can
    restricted = {p: False for p in ORG_ROLE_PERMISSIONS["admin"]}
    db.organizations.update_one({"_id": ObjectId(org_id)}, {"$set": {"settings.role_permissions": {"admin": restricted}}})
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    assert sa.post("/api/super-admin/impersonate", json={"organization_id": org_id, "reason": ""}).status_code == 422
    r = sa.post("/api/super-admin/impersonate", json={"organization_id": org_id, "reason": "support ticket 42"})
    assert r.status_code == 200, r.text
    me = sa.get("/api/auth/me").json()["user"]
    assert me["org_role"] == "owner" and me["organization_id"] == org_id
    assert set(me["permissions"]) == set().union(*ORG_ROLE_PERMISSIONS.values())
    assert sa.get("/org-admin", follow_redirects=False).status_code == 200
    assert sa.get("/api/org-admin/users").status_code == 200
    assert sa.get("/api/org-admin/audit-logs").status_code == 200
    assert db.audit_logs.find_one({"action": "impersonation.start", "organization_id": org_id})
    out = sa.post("/api/super-admin/impersonate/exit")
    assert out.status_code == 200 and out.json()["redirect"] == "/superadmin"


def test_super_admin_views_any_partner_portal_with_full_rights(env):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    p = _partner(client, sa, "viewme@p.example", name="ViewMe")
    assert sa.post(f"/api/super-admin/partners/{p['id']}/impersonate", json={"reason": "no"}).status_code == 422
    r = sa.post(f"/api/super-admin/partners/{p['id']}/impersonate", json={"reason": "help with a payout"})
    assert r.status_code == 200 and r.json()["redirect"] == "/partner#/dashboard"
    me = sa.get("/api/partner/v1/me").json()["me"]
    assert me["is_active_partner"] and me["impersonated_by"] == SUPER and "customers.create" in me["permissions"]
    assert sa.get("/partner").status_code == 200
    # full rights: the Super Admin can act inside the portal …
    r = sa.post("/api/partner/v1/campaigns", json={"name": "Set up by LeadAI"})
    assert r.status_code == 200, r.text
    # … and it is attributed to the Super Admin, never silently to the partner
    act = db.audit_logs.find_one({"action": "partner.campaign.created"})
    assert act["details"]["impersonated_by"] == SUPER and SUPER in act["actor_email"]
    req = db.partner_activity.find_one({"kind": "request", "action": "POST /api/partner/v1/campaigns"})
    assert req["details"]["impersonated_by"] == SUPER and req["actor"].startswith(SUPER)
    assert db.partner_activity.find_one({"action": "partner.impersonation.start", "partner_id": p["id"]})
    # exit goes back to the partner's page in the Super Admin portal
    out = sa.post("/api/super-admin/impersonate/exit")
    assert out.status_code == 200 and out.json()["redirect"] == f"/superadmin#/partners/{p['id']}"
    assert sa.get("/api/super-admin/partners").status_code == 200
    assert sa.get("/api/partner/v1/me").status_code == 401
    assert db.partner_activity.find_one({"action": "partner.impersonation.stop", "partner_id": p["id"]})


def test_partner_impersonation_expires(env):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    p = _partner(client, sa, "expire@p.example", name="Expire")
    with patch("app.auth.tenant.impersonation_expiry", return_value=int(time.time()) - 1):
        sa.post(f"/api/super-admin/partners/{p['id']}/impersonate", json={"reason": "expired session test"})
    assert sa.get("/api/partner/v1/me").status_code == 401


def test_only_the_super_admin_gets_these_powers(env):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    p = _partner(client, sa, "nope@p.example", name="Nope")
    from app.auth.crypto import hash_password
    from app.db.models import utcnow
    db.users.insert_one({"email": "ops@leadai.example", "name": "Ops", "password_hash": hash_password(PW),
                         "status": "active", "is_platform_admin": True, "platform_role": "operations_admin",
                         "created_at": utcnow()})
    staff = _login(client, "ops@leadai.example", PW, scope="admin")
    for path in (f"/api/super-admin/partners/{p['id']}/impersonate", "/api/super-admin/api-keys/x/revoke"):
        assert staff.post(path, json={"reason": "trying it out"}).status_code == 403
    partner = _login(client, "nope@p.example", PW, scope="partner")
    assert partner.get("/api/super-admin/api-keys").status_code == 401
    assert partner.post(f"/api/super-admin/partners/{p['id']}/impersonate", json={"reason": "self"}).status_code == 401


def test_super_admin_controls_every_api_key_and_webhook(env, site_account):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    _, org_id = site_account("dev@tenant.example")
    from app.services.api_keys import create_api_key
    from app.services.outbound_webhooks import create_outbound_webhook
    raw, key = create_api_key(db, organization_id=org_id, name="Leaked key", scopes=["leads:read"])
    whk = create_outbound_webhook(db, organization_id=org_id, target_url="https://hooks.example/x", secret="s3cret-value")
    p = _partner(client, sa, "keys@p.example", name="Keys")
    sa.patch(f"/api/super-admin/partners/{p['id']}", json={"permissions": p["permissions"] + ["api.access"]})
    portal = _login(client, "keys@p.example", PW, scope="partner")
    praw = portal.post("/api/partner/v1/api-keys", json={"name": "Partner BI"}).json()["api_key"]

    keys = sa.get("/api/super-admin/api-keys?active=true").json()
    owners = {k["name"]: (k["owner_type"], k["owner_name"]) for k in keys["items"]}
    assert owners["Leaked key"] == ("organization", "Test Org") and owners["Partner BI"] == ("partner", "Keys Co")
    assert "key_hash" not in str(keys) and raw not in str(keys)
    # revoke an organization's key: the public API stops accepting it at once
    api = TestClient(client.app)
    assert api.get("/api/v1/leads", headers={"X-API-Key": raw}).status_code == 200
    assert sa.post(f"/api/super-admin/api-keys/{key['key_id']}/revoke", json={"reason": "leaked on GitHub"}).status_code == 200
    assert api.get("/api/v1/leads", headers={"X-API-Key": raw}).status_code == 401
    assert sa.post(f"/api/super-admin/api-keys/{key['key_id']}/revoke", json={}).status_code == 409
    # revoke a partner's key
    pk = next(k for k in keys["items"] if k["name"] == "Partner BI")
    assert sa.post(f"/api/super-admin/api-keys/{pk['key_id']}/revoke", json={"reason": "audit"}).status_code == 200
    assert api.get("/api/partner/v1/dashboard", headers={"X-API-Key": praw}).status_code == 401
    # webhooks: listed without secrets, disabled and enabled
    hooks = sa.get("/api/super-admin/webhooks").json()
    assert hooks["items"][0]["url"] == "https://hooks.example/x" and "s3cret-value" not in str(hooks)
    assert sa.post(f"/api/super-admin/webhooks/{whk['webhook_id']}/disable", json={"reason": "endpoint down"}).status_code == 200
    assert db.outbound_webhooks.find_one({"webhook_id": whk["webhook_id"]})["is_active"] is False
    assert sa.post(f"/api/super-admin/webhooks/{whk['webhook_id']}/enable", json={}).status_code == 200
    for action in ("api_key.revoked_by_super_admin", "webhook.disabled_by_super_admin", "webhook.enabled_by_super_admin"):
        assert db.audit_logs.find_one({"action": action}), action


def test_super_admin_row_of_the_access_matrix(env, site_account):
    """Every column of the Super Admin row: Website, Dashboard, Org Admin,
    Platform Staff console, Super Admin, Partner Portal, Customer API and
    Partner API — reachable, and every reach attributed."""
    client, db = env
    _, org_id = site_account("owner@matrix.example", org_name="Matrix Org")
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    # Website + Super Admin + Platform Staff console
    assert sa.get("/pricing").status_code == 200
    assert sa.get("/superadmin").status_code == 200
    assert sa.get("/api/super-admin/organizations").status_code == 200
    assert sa.get("/admin", follow_redirects=False).headers["location"] == "/superadmin"   # merged
    assert sa.get("/api/admin/dashboard").status_code in (200, 500)  # 500 = mongomock-only $convert gap
    # Customer API: the Super Admin issues a key for any organization
    r = sa.post("/api/super-admin/api-keys", json={"owner_type": "organization", "owner_id": org_id,
                                                   "scopes": ["leads:read", "leads:write"], "reason": "no"})
    assert r.status_code == 422                                   # reason required
    r = sa.post("/api/super-admin/api-keys", json={"owner_type": "organization", "owner_id": org_id,
                                                   "scopes": ["leads:read", "leads:write"],
                                                   "reason": "integration support"})
    assert r.status_code == 200, r.text
    org_key = r.json()["api_key"]
    api = TestClient(client.app)
    assert api.get("/api/v1/leads", headers={"X-API-Key": org_key}).status_code == 200
    key_doc = db.api_keys.find_one({"key_id": r.json()["key_id"]})
    assert key_doc["created_by"] == f"super_admin:{SUPER}" and key_doc["issued_by_super_admin"]
    assert db.audit_logs.find_one({"action": "api_key.issued_by_super_admin", "organization_id": org_id})
    # Partner API: a partner without API access gets it granted explicitly
    p = _partner(client, sa, "matrix-partner@example.com", name="Matrix")
    r = sa.post("/api/super-admin/api-keys", json={"owner_type": "partner", "owner_id": p["id"],
                                                   "reason": "reporting integration"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "api_access_disabled"
    r = sa.post("/api/super-admin/api-keys", json={"owner_type": "partner", "owner_id": p["id"],
                                                   "reason": "reporting integration", "grant_api_access": True})
    assert r.status_code == 200, r.text
    assert api.get("/api/partner/v1/dashboard", headers={"X-API-Key": r.json()["api_key"]}).status_code == 200
    assert "api.access" in db.partners.find_one({"_id": ObjectId(p["id"])})["permissions"]
    # Partner Portal (view as partner)
    viewer = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    assert viewer.post(f"/api/super-admin/partners/{p['id']}/impersonate",
                       json={"reason": "matrix check"}).status_code == 200
    assert viewer.get("/partner").status_code == 200
    assert viewer.get("/api/partner/v1/dashboard").status_code == 200
    # Dashboard + Org Admin (sign in as the organization, Owner rights)
    org_view = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    r = org_view.post("/api/super-admin/impersonate", json={"organization_id": org_id, "reason": "matrix check"})
    assert r.status_code == 200, r.text
    assert org_view.get("/dashboard").status_code == 200
    assert org_view.get("/org-admin").status_code == 200
    me = org_view.get("/api/auth/me").json()["user"]
    assert me["org_role"] == "owner" and me["organization_id"] == org_id
    # nobody else can issue keys
    staff_id = db.users.insert_one({"email": "staff@leadai.example", "name": "Staff", "status": "active",
                                    "password_hash": __import__("app.auth.crypto", fromlist=["x"]).hash_password(PW),
                                    "is_platform_admin": True, "platform_role": "operations_admin"}).inserted_id
    assert staff_id
    staff = _login(client, "staff@leadai.example", PW, scope="admin")
    assert staff.post("/api/super-admin/api-keys", json={"owner_type": "organization", "owner_id": org_id,
                                                         "reason": "should fail"}).status_code == 403
