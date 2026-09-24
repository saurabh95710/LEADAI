"""
Step 1 security & lifecycle proof — runs on an in-memory MongoDB (mongomock),
never on a real database.

Proves:
  * Admin A cannot read or modify Organization B (team, members, invitations,
    org settings, billing) — and the attempt is logged as a security event.
  * Inside one organization a User cannot manage other members and cannot
    grant anything outside the organization namespace.
  * An Admin can never create, grant or escalate to Super Admin (org roles,
    invitations, forged admin-scope cookies, super-admin APIs).
  * A user without an active membership gets 403 (no fallback organization).
  * Signup = demo request: no session, no access until a Super Admin approves;
    approval grants demo tokens; token consumption is atomic.
  * Payment "success" can never activate a subscription: checkout ->
    PENDING_PAYMENT, verified payment -> PENDING_ADMIN_CONFIRMATION, only a
    Super Admin confirm -> ACTIVE (+ org activated, Admin portal enabled).
  * Webhooks require a valid signature.
  * Audit + redaction; no hardcoded default credentials.
"""
import hashlib
import hmac
import json
import os
from unittest.mock import patch

import mongomock
import mongomock_motor
import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.auth.crypto import hash_password
from app.auth.service import COOKIE_NAME, build_session_value, create_tracked_session

PASSWORD = "Str0ngPass!"


@pytest.fixture
def env():
    """App wired to a fresh in-memory MongoDB shared by sync + async code."""
    sync_client = mongomock.MongoClient()
    async_client = mongomock_motor.AsyncMongoMockClient(mock_mongo_client=sync_client)
    from app.billing import plans as plans_mod
    from app.billing import provider as provider_mod
    from app.lifecycle import config as cfg_mod
    from app.auth import permissions as perm_mod
    plans_mod.invalidate_plan_cache()
    cfg_mod.clear_cache()
    perm_mod.invalidate_permission_cache()
    provider_mod.reset_billing_provider()
    from app.auth import service as svc_mod
    from app.api.routes import auth as auth_routes
    svc_mod._login_attempts.clear()
    auth_routes._signup_attempts.clear()
    auth_routes._reset_attempts.clear()
    with patch("app.db.mongo.get_sync_client", return_value=sync_client), \
            patch("app.db.mongo.get_async_client", return_value=async_client), \
            patch("app.admin.settings.is_maintenance_enabled", return_value=False), \
            patch.dict(os.environ, {"BILLING_WEBHOOK_SECRET": "whsec_test",
                                    "STRIPE_SECRET_KEY": ""}):
        from app.main import app
        with TestClient(app) as client:
            db = sync_client["LeadAI" if False else __import__("app.config", fromlist=["x"]).get_settings().mongo_db_name]
            yield client, db
    provider_mod.reset_billing_provider()
    plans_mod.invalidate_plan_cache()
    cfg_mod.clear_cache()


# ── helpers ────────────────────────────────────────────────────────────────

def _org(db, name, status="active", **extra):
    return str(db.organizations.insert_one({
        "name": name, "slug": name.lower().replace(" ", "-") + str(ObjectId())[-4:],
        "status": status, "settings": {}, "admin_portal_enabled": status == "active",
        **extra}).inserted_id)


def _user(db, email, org_id=None, role="member", member_status="active",
          status="active", platform_role=None):
    uid = str(db.users.insert_one({
        "email": email, "name": email.split("@")[0], "password_hash": hash_password(PASSWORD),
        "status": status, "is_platform_admin": bool(platform_role),
        "platform_role": platform_role, "default_organization_id": org_id}).inserted_id)
    if org_id:
        db.organization_members.insert_one({
            "organization_id": org_id, "user_id": uid, "role": role,
            "status": member_status, "permissions_override": {}})
    return uid


def _cookie(claims):
    tracked = create_tracked_session(claims)
    return {COOKIE_NAME: build_session_value(tracked)}


def _site(uid, email, org_id, role):
    return _cookie({"user_id": uid, "email": email, "name": email, "scope": "site",
                    "organization_id": org_id, "org_role": role})


def _super_admin(db):
    uid = _user(db, "root@platform.test", platform_role="super_admin")
    return _cookie({"user_id": uid, "email": "root@platform.test", "name": "Root",
                    "scope": "admin", "role": "super_admin"})


@pytest.fixture
def world(env):
    client, db = env
    org_a, org_b = _org(db, "Org A"), _org(db, "Org B")
    ids = {
        "a_owner": _user(db, "owner@a.test", org_a, "owner"),
        "a_admin": _user(db, "admin@a.test", org_a, "admin"),
        "a_admin2": _user(db, "admin2@a.test", org_a, "admin"),
        "a_user1": _user(db, "user1@a.test", org_a, "member"),
        "a_user2": _user(db, "user2@a.test", org_a, "member"),
        "a_viewer": _user(db, "viewer@a.test", org_a, "viewer"),
        "b_owner": _user(db, "owner@b.test", org_b, "owner"),
        "b_user": _user(db, "user@b.test", org_b, "member"),
    }
    emails = {k: f"{k.split('_', 1)[1]}@{k.split('_')[0]}.test" for k in ids}
    emails.update({"a_owner": "owner@a.test", "a_admin": "admin@a.test",
                   "a_admin2": "admin2@a.test", "a_user1": "user1@a.test",
                   "a_user2": "user2@a.test", "a_viewer": "viewer@a.test",
                   "b_owner": "owner@b.test", "b_user": "user@b.test"})
    orgs = {"a": org_a, "b": org_b}

    def cookie(key):
        org = org_a if key.startswith("a_") else org_b
        role = db.organization_members.find_one({"user_id": ids[key]})["role"]
        return _site(ids[key], emails[key], org, role)
    return client, db, ids, orgs, cookie


# ═══════════════════════════════════════════════════════════════════════════
# 1. Admin A cannot access Organization B
# ═══════════════════════════════════════════════════════════════════════════

class TestCrossTenantIsolation:
    def test_admin_a_sees_only_org_a_team(self, world):
        client, db, ids, orgs, cookie = world
        r = client.get("/api/organizations/current/team", cookies=cookie("a_admin"))
        assert r.status_code == 200
        member_ids = {m["user_id"] for m in r.json()["members"]}
        assert ids["b_owner"] not in member_ids and ids["b_user"] not in member_ids
        assert ids["a_user1"] in member_ids

    def test_admin_a_cannot_modify_org_b_member(self, world):
        client, db, ids, orgs, cookie = world
        r = client.patch(f"/api/organizations/current/members/{ids['b_user']}",
                         json={"status": "suspended"}, cookies=cookie("a_admin"))
        assert r.status_code == 404
        assert db.organization_members.find_one({"user_id": ids["b_user"]})["status"] == "active"
        assert db.security_events.find_one({"type": "cross_tenant_access",
                                            "actor_email": "admin@a.test",
                                            "target_organization_id": orgs["b"]})
        r = client.delete(f"/api/organizations/current/members/{ids['b_user']}",
                          cookies=cookie("a_admin"))
        assert r.status_code == 404

    def test_forged_org_claim_is_ignored(self, world):
        """A session claiming Org B's id still resolves to the user's real
        membership — the organization never comes from the request."""
        client, db, ids, orgs, cookie = world
        forged = _site(ids["a_admin"], "admin@a.test", orgs["b"], "owner")
        r = client.get("/api/organizations/current", cookies=forged)
        assert r.status_code == 200
        assert r.json()["organization"]["id"] == orgs["a"]
        assert r.json()["organization"]["user_role"] == "admin"

    def test_switch_to_foreign_org_denied_and_logged(self, world):
        client, db, ids, orgs, cookie = world
        r = client.post("/api/auth/switch-organization", json={"organization_id": orgs["b"]},
                        cookies=cookie("a_admin"))
        assert r.status_code == 403
        assert db.security_events.find_one({"type": "cross_tenant_access"}) is not None

    def test_admin_a_cannot_revoke_org_b_invitation(self, world):
        client, db, ids, orgs, cookie = world
        inv = db.organization_invitations.insert_one({
            "organization_id": orgs["b"], "email": "x@b.test", "role": "member",
            "token_hash": "h", "status": "pending"}).inserted_id
        r = client.delete(f"/api/organizations/current/invitations/{inv}", cookies=cookie("a_admin"))
        assert r.status_code == 404
        assert db.organization_invitations.find_one({"_id": inv})["status"] == "pending"

    def test_org_b_billing_invisible_to_admin_a(self, world):
        client, db, ids, orgs, cookie = world
        db.subscriptions.insert_one({"organization_id": orgs["b"], "plan_id": "pro",
                                     "status": "active", "checkout_session_id": "cs_b"})
        r = client.get("/api/billing/checkout/cs_b", cookies=cookie("a_admin"))
        assert r.status_code == 404

    def test_removed_member_loses_access_immediately(self, world):
        client, db, ids, orgs, cookie = world
        c = cookie("a_user1")
        assert client.get("/api/organizations/current", cookies=c).status_code == 200
        db.organization_members.update_one({"user_id": ids["a_user1"]},
                                           {"$set": {"status": "removed"}})
        r = client.get("/api/organizations/current", cookies=c)
        assert r.status_code == 403

    def test_no_membership_gets_403_not_default_org(self, world):
        client, db, ids, orgs, cookie = world
        uid = _user(db, "orphan@x.test")
        r = client.get("/api/organizations/current",
                       cookies=_site(uid, "orphan@x.test", "", "owner"))
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "no_active_organization"

    def test_suspended_org_blocked(self, world):
        client, db, ids, orgs, cookie = world
        db.organizations.update_one({"_id": ObjectId(orgs["b"])}, {"$set": {"status": "suspended"}})
        r = client.get("/api/organizations/current", cookies=cookie("b_owner"))
        assert r.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════
# 2. User-level separation inside one organization
# ═══════════════════════════════════════════════════════════════════════════

class TestUserLevelSeparation:
    def test_member_cannot_manage_other_members(self, world):
        client, db, ids, orgs, cookie = world
        r = client.patch(f"/api/organizations/current/members/{ids['a_user2']}",
                         json={"status": "suspended"}, cookies=cookie("a_user1"))
        assert r.status_code == 403
        assert client.get("/api/organizations/current/team",
                          cookies=cookie("a_user1")).status_code == 403

    def test_member_cannot_change_org_settings_or_billing(self, world):
        client, db, ids, orgs, cookie = world
        assert client.patch("/api/organizations/current", json={"name": "X"},
                            cookies=cookie("a_user1")).status_code == 403
        assert client.post("/api/billing/checkout", json={"plan_slug": "pro"},
                           cookies=cookie("a_user1")).status_code == 403

    def test_permission_denied_is_logged(self, world):
        client, db, ids, orgs, cookie = world
        client.get("/api/organizations/current/team", cookies=cookie("a_viewer"))
        assert db.security_events.find_one({"type": "permission_denied",
                                            "actor_email": "viewer@a.test"})

    def test_scope_filter_limits_members_to_own_records(self, world):
        from app.auth.tenant import resolve_tenant_context, scope_filter
        client, db, ids, orgs, cookie = world
        claims = {"user_id": ids["a_user1"], "email": "user1@a.test", "scope": "site"}
        ctx = resolve_tenant_context(claims, db)
        db.search_history.insert_many([
            {"run_id": "r1", "organization_id": orgs["a"], "user_id": ids["a_user1"]},
            {"run_id": "r2", "organization_id": orgs["a"], "user_id": ids["a_user2"]},
            {"run_id": "r3", "organization_id": orgs["b"], "user_id": ids["b_user"]},
        ])
        seen = {d["run_id"] for d in db.search_history.find(scope_filter(ctx))}
        assert seen == {"r1"}
        admin_ctx = resolve_tenant_context(
            {"user_id": ids["a_admin"], "email": "admin@a.test", "scope": "site"}, db)
        seen = {d["run_id"] for d in db.search_history.find(scope_filter(admin_ctx))}
        assert seen == {"r1", "r2"}

    def test_shared_workspace_must_be_enabled_explicitly(self, world):
        from app.auth.tenant import resolve_tenant_context
        client, db, ids, orgs, cookie = world
        claims = {"user_id": ids["a_user1"], "email": "user1@a.test", "scope": "site"}
        assert not resolve_tenant_context(claims, db).can_view_all_org_data
        db.organizations.update_one({"_id": ObjectId(orgs["a"])},
                                    {"$set": {"settings.shared_workspace": True}})
        assert resolve_tenant_context(claims, db).can_view_all_org_data


# ═══════════════════════════════════════════════════════════════════════════
# 3. Admin can never become Super Admin
# ═══════════════════════════════════════════════════════════════════════════

class TestNoEscalation:
    @pytest.mark.parametrize("role", ["super_admin", "owner", "platform_admin"])
    def test_admin_cannot_invite_privileged_roles(self, world, role):
        client, db, ids, orgs, cookie = world
        r = client.post("/api/organizations/current/invitations",
                        json={"email": "new@example.com", "role": role}, cookies=cookie("a_admin"))
        assert r.status_code == 403
        assert db.security_events.find_one({"type": "escalation_attempt"})

    @pytest.mark.parametrize("role", ["super_admin", "owner"])
    def test_admin_cannot_promote_to_privileged_roles(self, world, role):
        client, db, ids, orgs, cookie = world
        r = client.patch(f"/api/organizations/current/members/{ids['a_user1']}",
                         json={"role": role}, cookies=cookie("a_admin"))
        assert r.status_code == 403
        assert db.organization_members.find_one({"user_id": ids["a_user1"]})["role"] == "member"

    def test_admin_cannot_grant_admin_or_modify_other_admin(self, world):
        client, db, ids, orgs, cookie = world
        assert client.patch(f"/api/organizations/current/members/{ids['a_user1']}",
                            json={"role": "admin"}, cookies=cookie("a_admin")).status_code == 403
        assert client.patch(f"/api/organizations/current/members/{ids['a_admin2']}",
                            json={"status": "suspended"}, cookies=cookie("a_admin")).status_code == 403

    def test_admin_cannot_modify_self_or_owner(self, world):
        client, db, ids, orgs, cookie = world
        assert client.patch(f"/api/organizations/current/members/{ids['a_admin']}",
                            json={"role": "viewer"}, cookies=cookie("a_admin")).status_code == 403
        assert client.delete(f"/api/organizations/current/members/{ids['a_owner']}",
                             cookies=cookie("a_admin")).status_code == 403

    def test_platform_permissions_cannot_be_granted_from_org(self, world):
        client, db, ids, orgs, cookie = world
        r = client.patch(f"/api/organizations/current/members/{ids['a_user1']}",
                         json={"permissions_override": {"platform.manage": True}},
                         cookies=cookie("a_owner"))
        assert r.status_code == 422
        r = client.patch("/api/organizations/current",
                         json={"role_permissions": {"member": {"impersonate.user": True}}},
                         cookies=cookie("a_owner"))
        assert r.status_code == 422

    def test_org_admin_cannot_reach_super_admin_api(self, world):
        client, db, ids, orgs, cookie = world
        for path in ("/api/super-admin/dashboard", "/api/super-admin/demo-requests",
                     "/api/super-admin/subscriptions/queue"):
            assert client.get(path, cookies=cookie("a_owner")).status_code in (401, 403)

    def test_forged_admin_scope_cookie_gets_nothing(self, world):
        """Even a validly signed admin-scope session for a tenant admin has no
        platform role server-side: the old 'viewer' fallback is gone."""
        client, db, ids, orgs, cookie = world
        forged = _cookie({"user_id": ids["a_owner"], "email": "owner@a.test",
                          "scope": "admin", "role": "super_admin"})
        assert client.get("/api/super-admin/dashboard", cookies=forged).status_code == 403
        assert client.get("/api/admin/dashboard", cookies=forged).status_code == 403
        assert client.get("/api/admin/organizations", cookies=forged).status_code == 403

    def test_super_admin_can_access(self, world):
        client, db, ids, orgs, cookie = world
        r = client.get("/api/super-admin/demo-requests", cookies=_super_admin(db))
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# 4. Demo lifecycle
# ═══════════════════════════════════════════════════════════════════════════

class TestDemoLifecycle:
    def test_signup_creates_pending_demo_without_access(self, env):
        client, db = env
        r = client.post("/api/auth/signup", json={
            "name": "Jane", "email": "jane@corp.test", "company": "Corp",
            "password": PASSWORD, "phone": "+1 555"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "pending"
        assert COOKIE_NAME not in r.cookies
        req = db.demo_requests.find_one({"email": "jane@corp.test"})
        assert req["status"] == "pending"
        assert db.organizations.find_one({"_id": ObjectId(req["organization_id"])})["status"] == "pending"
        # cannot sign in yet
        r = client.post("/api/auth/login", json={"email": "jane@corp.test", "password": PASSWORD})
        assert r.status_code == 403 and r.json()["detail"]["code"] == "demo_pending"
        # super admin was notified, audit chain started
        assert db.notifications.find_one({"type": "demo_requested", "audience": "super_admin"})
        assert db.audit_logs.find_one({"action": "account.registered"})
        assert db.audit_logs.find_one({"action": "demo.requested"})

    def test_weak_password_rejected(self, env):
        client, db = env
        r = client.post("/api/auth/signup", json={"name": "J", "email": "j@x.test",
                                                  "company": "C", "password": "short"})
        assert r.status_code == 422

    def test_approval_grants_demo_and_tokens(self, env):
        client, db = env
        client.post("/api/auth/signup", json={"name": "Jane", "email": "jane@corp.test",
                                              "company": "Corp", "password": PASSWORD})
        req = db.demo_requests.find_one({"email": "jane@corp.test"})
        sa = _super_admin(db)
        r = client.post(f"/api/super-admin/demo-requests/{req['_id']}/approve", json={}, cookies=sa)
        assert r.status_code == 200, r.text
        org = db.organizations.find_one({"_id": ObjectId(req["organization_id"])})
        assert org["status"] == "demo"
        bal = db.token_balances.find_one({"organization_id": req["organization_id"]})
        demo_tokens = db.platform_config.find_one({"_id": "demo"})["value"]["tokens"]
        assert bal["allocated"] == demo_tokens and bal["remaining"] == demo_tokens
        r = client.post("/api/auth/login", json={"email": "jane@corp.test", "password": PASSWORD})
        assert r.status_code == 200
        assert db.audit_logs.find_one({"action": "demo.approved"})
        assert db.audit_logs.find_one({"action": "tokens.granted"})
        # approving twice is refused
        r = client.post(f"/api/super-admin/demo-requests/{req['_id']}/approve", json={}, cookies=sa)
        assert r.status_code == 409

    def test_rejected_demo_cannot_sign_in(self, env):
        client, db = env
        client.post("/api/auth/signup", json={"name": "Bob", "email": "bob@x.test",
                                              "company": "X", "password": PASSWORD})
        req = db.demo_requests.find_one({"email": "bob@x.test"})
        client.post(f"/api/super-admin/demo-requests/{req['_id']}/reject",
                    json={"reason": "spam"}, cookies=_super_admin(db))
        r = client.post("/api/auth/login", json={"email": "bob@x.test", "password": PASSWORD})
        assert r.status_code in (401, 403)

    def test_demo_limits_come_from_config(self, env):
        client, db = env
        from app.billing.entitlements import EntitlementService
        org = _org(db, "Demo Co", status="demo", demo={"config": {
            "max_searches": 3, "posts_per_search": 20, "comments_per_post": 30,
            "allowed_platforms": ["facebook"], "ai_enabled": True, "exports_enabled": False,
            "max_leads": 50, "max_users": 1}})
        plan = EntitlementService.get_effective_plan_sync(org)
        assert plan["is_demo"] and plan["limits"]["monthly_searches"] == 3
        assert "facebook" in plan["features"] and "instagram" not in plan["features"]
        assert "csv_export" not in plan["features"]
        caps = EntitlementService.get_run_caps(org)
        assert caps == {"posts_per_search": 20, "comments_per_post": 30, "max_leads": 50}

    def test_expired_demo_blocks_consumption(self, env):
        client, db = env
        from datetime import datetime, timedelta, timezone
        from app.billing.entitlements import AccessBlockedException, EntitlementService
        org = _org(db, "Old Demo", status="demo", demo={
            "expires_at": datetime.now(timezone.utc) - timedelta(days=1), "config": {}})
        with pytest.raises(AccessBlockedException) as e:
            EntitlementService.assert_can_operate(org)
        assert e.value.detail["code"] == "DEMO_EXPIRED"


class TestTokenLedger:
    def test_consume_is_atomic_and_never_overdraws(self, env):
        client, db = env
        from app.billing import tokens
        org = _org(db, "T")
        tokens.allocate(org, 10, source="demo", actor="test", reset=True)
        tokens.consume(org, 7, reason="search")
        with pytest.raises(tokens.TokensExhaustedException):
            tokens.consume(org, 4, reason="search")
        bal = tokens.get_balance(org)
        assert bal["used"] == 7 and bal["remaining"] == 3 and bal["allocated"] == 10
        assert db.token_ledger.count_documents({"organization_id": org, "type": "consume"}) == 1

    def test_threshold_notification(self, env):
        client, db = env
        from app.billing import tokens
        org = _org(db, "T2")
        tokens.allocate(org, 10, source="demo", actor="test", reset=True)
        tokens.consume(org, 9)
        assert db.notifications.find_one({"type": "high_token_usage"})


# ═══════════════════════════════════════════════════════════════════════════
# 5. Payment -> webhook/verification -> Super Admin confirmation
# ═══════════════════════════════════════════════════════════════════════════

class TestSubscriptionActivation:
    def _checkout(self, world):
        client, db, ids, orgs, cookie = world
        db.organizations.update_one({"_id": ObjectId(orgs["a"])}, {"$set": {"status": "demo"}})
        r = client.post("/api/billing/checkout", json={"plan_slug": "pro"},
                        cookies=cookie("a_owner"))
        assert r.status_code == 200, r.text
        return r.json()["checkout"]

    def test_checkout_never_activates(self, world):
        client, db, ids, orgs, cookie = world
        co = self._checkout(world)
        assert co["status"] == "pending_payment"
        sub = db.subscriptions.find_one({"_id": ObjectId(co["subscription_id"])})
        assert sub["status"] == "pending_payment"
        assert db.organizations.find_one({"_id": ObjectId(orgs["a"])})["status"] == "demo"

    def test_payment_success_only_reaches_pending_confirmation(self, world):
        client, db, ids, orgs, cookie = world
        co = self._checkout(world)
        # the browser's "success" page is read-only
        r = client.get(f"/api/billing/checkout/{co['session_id']}", cookies=cookie("a_owner"))
        assert r.json()["checkout"]["status"] == "pending_payment"
        # verified (mock provider) payment
        r = client.post(f"/api/billing/checkout/{co['session_id']}/mock-pay",
                        json={"succeed": True}, cookies=cookie("a_owner"))
        assert r.status_code == 200
        sub = db.subscriptions.find_one({"_id": ObjectId(co["subscription_id"])})
        assert sub["status"] == "pending_admin_confirmation"
        assert db.organizations.find_one({"_id": ObjectId(orgs["a"])})["status"] == "demo"
        assert db.notifications.find_one({"type": "subscription_awaiting_approval"})
        # customer-side endpoints cannot finish the job
        assert client.post("/api/billing/reactivate", cookies=cookie("a_owner")).status_code == 409
        sub = db.subscriptions.find_one({"_id": ObjectId(co["subscription_id"])})
        assert sub["status"] == "pending_admin_confirmation"

    def test_super_admin_confirmation_activates(self, world):
        client, db, ids, orgs, cookie = world
        co = self._checkout(world)
        client.post(f"/api/billing/checkout/{co['session_id']}/mock-pay",
                    json={"succeed": True}, cookies=cookie("a_owner"))
        # an org admin cannot confirm
        r = client.post(f"/api/super-admin/subscriptions/{co['subscription_id']}/confirm",
                        cookies=cookie("a_owner"))
        assert r.status_code in (401, 403)
        r = client.post(f"/api/super-admin/subscriptions/{co['subscription_id']}/confirm",
                        cookies=_super_admin(db))
        assert r.status_code == 200, r.text
        sub = db.subscriptions.find_one({"_id": ObjectId(co["subscription_id"])})
        org = db.organizations.find_one({"_id": ObjectId(orgs["a"])})
        assert sub["status"] == "active"
        assert org["status"] == "active" and org["admin_portal_enabled"] is True
        assert org["plan_id"] == "pro"
        assert db.token_balances.find_one({"organization_id": orgs["a"]})["source"] == "plan"
        for action in ("plan.selected", "payment.received", "subscription.active",
                       "organization.activated", "admin.activated"):
            assert db.audit_logs.find_one({"action": action}), action

    def test_confirm_requires_verified_payment(self, world):
        client, db, ids, orgs, cookie = world
        co = self._checkout(world)
        r = client.post(f"/api/super-admin/subscriptions/{co['subscription_id']}/confirm",
                        cookies=_super_admin(db))
        assert r.status_code == 409  # still pending_payment

    def test_webhook_requires_signature(self, world):
        client, db, ids, orgs, cookie = world
        co = self._checkout(world)
        event = {"id": "evt_1", "type": "checkout.session.completed", "data": {"object": {
            "metadata": {"subscription_id": co["subscription_id"]}, "payment_status": "paid",
            "amount_total": int(co["amount"] * 100)}}}
        body = json.dumps(event).encode()
        r = client.post("/api/billing/webhook", content=body,
                        headers={"Content-Type": "application/json"})
        assert r.status_code == 400
        assert db.security_events.find_one({"type": "invalid_webhook_signature"})
        r = client.post("/api/billing/webhook", content=body,
                        headers={"X-LeadAI-Signature": "0" * 64})
        assert r.status_code == 400
        sig = hmac.new(b"whsec_test", body, hashlib.sha256).hexdigest()
        r = client.post("/api/billing/webhook", content=body, headers={"X-LeadAI-Signature": sig})
        assert r.status_code == 200, r.text
        sub = db.subscriptions.find_one({"_id": ObjectId(co["subscription_id"])})
        assert sub["status"] == "pending_admin_confirmation"  # never active
        # replay is idempotent
        r = client.post("/api/billing/webhook", content=body, headers={"X-LeadAI-Signature": sig})
        assert r.json()["status"] == "duplicate_skipped"

    def test_underpayment_rejected(self, world):
        client, db, ids, orgs, cookie = world
        co = self._checkout(world)
        event = {"id": "evt_2", "type": "checkout.session.completed", "data": {"object": {
            "metadata": {"subscription_id": co["subscription_id"]}, "payment_status": "paid",
            "amount_total": 100}}}
        body = json.dumps(event).encode()
        sig = hmac.new(b"whsec_test", body, hashlib.sha256).hexdigest()
        r = client.post("/api/billing/webhook", content=body, headers={"X-LeadAI-Signature": sig})
        assert r.status_code == 400
        assert db.subscriptions.find_one({"_id": ObjectId(co["subscription_id"])})["status"] == "pending_payment"


# ═══════════════════════════════════════════════════════════════════════════
# 6. Auth hardening, invitations, audit
# ═══════════════════════════════════════════════════════════════════════════

class TestAuthHardening:
    def test_account_lockout(self, world):
        client, db, ids, orgs, cookie = world
        with patch("app.api.routes.auth.login_allowed", return_value=True):
            for _ in range(5):
                r = client.post("/api/auth/login", json={"email": "user1@a.test", "password": "nope"})
                assert r.status_code == 401
            r = client.post("/api/auth/login", json={"email": "user1@a.test", "password": PASSWORD})
        assert r.status_code == 423
        assert db.security_events.find_one({"type": "account_locked"})
        assert db.audit_logs.find_one({"action": "auth.login", "success": False})

    def test_login_success_is_audited(self, world):
        client, db, ids, orgs, cookie = world
        r = client.post("/api/auth/login", json={"email": "user1@a.test", "password": PASSWORD})
        assert r.status_code == 200
        assert r.json()["user"]["organization_id"] == orgs["a"]
        assert db.audit_logs.find_one({"action": "auth.login", "success": True,
                                       "organization_id": orgs["a"]})

    def test_logout_revokes_session(self, world):
        client, db, ids, orgs, cookie = world
        c = cookie("a_user1")
        assert client.get("/api/auth/me", cookies=c).status_code == 200
        client.post("/api/auth/logout", cookies=c)
        assert client.get("/api/auth/me", cookies=c).status_code == 401

    def test_password_reset_flow_single_use(self, world):
        client, db, ids, orgs, cookie = world
        r = client.post("/api/auth/password/forgot", json={"email": "user1@a.test"})
        assert r.status_code == 200
        mail = db.email_outbox.find_one({"kind": "password_reset"})
        token = mail["body"].split("token=")[1].split()[0]
        assert "Str0ng" not in mail["body"]
        r = client.post("/api/auth/password/reset", json={"token": token, "new_password": "N3wPassword"})
        assert r.status_code == 200
        r = client.post("/api/auth/password/reset", json={"token": token, "new_password": "An0therPass"})
        assert r.status_code == 400
        r = client.post("/api/auth/login", json={"email": "user1@a.test", "password": "N3wPassword"})
        assert r.status_code == 200

    def test_forgot_password_does_not_leak_accounts(self, env):
        client, db = env
        r = client.post("/api/auth/password/forgot", json={"email": "ghost@nowhere.test"})
        assert r.status_code == 200 and db.email_outbox.count_documents({}) == 0

    def test_invitation_email_link_no_password(self, world):
        client, db, ids, orgs, cookie = world
        db.subscriptions.insert_one({"organization_id": orgs["a"], "plan_id": "business",
                                     "status": "active", "current_period_start": None})
        r = client.post("/api/organizations/current/invitations",
                        json={"email": "newbie@example.com", "role": "member"}, cookies=cookie("a_owner"))
        assert r.status_code == 200, r.text
        mail = db.email_outbox.find_one({"kind": "invitation"})
        assert "/invite/" in mail["body"] and "password:" not in mail["body"].lower()
        token = r.json()["invite_url"].rsplit("/", 1)[1]
        # someone else's account cannot accept it
        r = client.post(f"/api/invitations/{token}/accept", cookies=cookie("b_user"))
        assert r.status_code == 403
        # the invitee creates a password from the link and is signed in
        r = client.post(f"/api/invitations/{token}/register",
                        json={"name": "Newbie", "password": PASSWORD})
        assert r.status_code == 200, r.text
        m = db.organization_members.find_one({"organization_id": orgs["a"],
                                              "user_id": r.json()["user"]["user_id"]})
        assert m["role"] == "member" and m["status"] == "active"
        # single use
        r = client.post(f"/api/invitations/{token}/register",
                        json={"name": "Again", "password": PASSWORD})
        assert r.status_code in (400, 409)


class TestAuditAndSecrets:
    def test_redaction_is_recursive(self):
        from app.admin.audit import redact
        out = redact({"api_key": "k", "nested": {"password": "p", "list": [{"token": "t"}]},
                      "gemini_api_key": "g", "authorization": "Bearer x", "ok": 1})
        assert out["api_key"] == out["gemini_api_key"] == out["authorization"] == "••••"
        assert out["nested"]["password"] == "••••"
        assert out["nested"]["list"][0]["token"] == "••••"
        assert out["ok"] == 1

    def test_audit_accepts_string_user(self, env):
        client, db = env
        from app.admin.audit import audit
        audit("x.test", "test", user="someone@x.test", organization_id="o1")
        row = db.audit_logs.find_one({"action": "x.test"})
        assert row["actor_email"] == "someone@x.test" and row["organization_id"] == "o1"

    def test_audit_cannot_be_disabled(self, env):
        client, db = env
        from app.admin.audit import audit
        with patch("app.admin.settings.get_setting_cached", return_value=False):
            audit("still.logged", "test")
        assert db.audit_logs.find_one({"action": "still.logged"})

    def test_no_hardcoded_default_password(self):
        root = os.path.join(os.path.dirname(__file__), "..", "app")
        for dirpath, _, files in os.walk(root):
            for f in files:
                if f.endswith(".py"):
                    with open(os.path.join(dirpath, f), encoding="utf-8") as fh:
                        assert "Admin@2026" not in fh.read(), f
