"""
Tests for LeadAI SaaS Multi-Tenant Architecture & RBAC Foundation:
- Organization & Membership Models
- Server-Side Tenant Isolation & IDOR Protection
- Role-Based Access Control (Platform Roles vs Org Roles)
- Super Admin Multi-Tenant Management (List, Suspend, Activate, Impersonate)
- Session Revocation
- SaaS Signup & Login Flows
"""
import pytest
from unittest.mock import patch, MagicMock
from bson import ObjectId
from fastapi.testclient import TestClient

from app.db.saas_models import PlatformRole, OrgRole, OrgStatus
from app.auth.permissions import (
    PLATFORM_VIEW,
    ORGS_VIEW,
    ORGS_CREATE,
    ORGS_SUSPEND,
    SYSTEM_MANAGE,
    LEADS_VIEW,
    LEADS_MANAGE,
    SEARCH_CREATE,
    SEARCH_VIEW,
    WORKSPACE_MANAGE,
    MEMBERS_INVITE,
    PLATFORM_ROLE_PERMISSIONS,
    ORG_ROLE_PERMISSIONS,
    has_platform_permission,
    has_org_permission,
)
from app.auth.service import (
    build_session_value,
    COOKIE_NAME,
    parse_session_value,
    create_tracked_session,
    revoke_user_sessions,
)
from app.db.mongo import get_sync_db


# ═══════════════════════════════════════════════════════════════════════
# 1. PERMISSIONS & RBAC MATRIX UNIT TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestRBACPermissions:
    def test_super_admin_has_all_platform_permissions(self):
        perms = PLATFORM_ROLE_PERMISSIONS["super_admin"]
        for perm in perms:
            assert has_platform_permission("super_admin", perm) is True

    def test_viewer_has_restricted_platform_permissions(self):
        assert has_platform_permission("viewer", PLATFORM_VIEW) is True
        assert has_platform_permission("viewer", ORGS_VIEW) is True
        assert has_platform_permission("viewer", ORGS_CREATE) is False
        assert has_platform_permission("viewer", ORGS_SUSPEND) is False
        assert has_platform_permission("viewer", SYSTEM_MANAGE) is False

    def test_org_owner_has_all_org_permissions(self):
        perms = ORG_ROLE_PERMISSIONS["owner"]
        for perm in perms:
            assert has_org_permission("owner", perm) is True

    def test_org_member_permissions(self):
        assert has_org_permission("member", LEADS_VIEW) is True
        assert has_org_permission("member", SEARCH_CREATE) is True
        assert has_org_permission("member", WORKSPACE_MANAGE) is False
        assert has_org_permission("member", MEMBERS_INVITE) is False

    def test_org_viewer_is_read_only(self):
        assert has_org_permission("viewer", LEADS_VIEW) is True
        assert has_org_permission("viewer", SEARCH_VIEW) is True
        assert has_org_permission("viewer", LEADS_MANAGE) is False
        assert has_org_permission("viewer", SEARCH_CREATE) is False


# ═══════════════════════════════════════════════════════════════════════
# 2. CLIENT & INTEGRATION FIXTURES
# ═══════════════════════════════════════════════════════════════════════

def _settings_off_except_registration(key, *args, **kwargs):
    """All admin settings off (0), but demo registration stays open so the
    signup endpoint does not answer 403 "registrations closed"."""
    return True if key == "features.demo_registration.enabled" else 0


@pytest.fixture
def client():
    with patch("app.admin.settings.is_maintenance_enabled", return_value=False), \
         patch("app.admin.settings.maintenance_message", return_value="Under maintenance"), \
         patch("app.admin.settings.get_setting", side_effect=_settings_off_except_registration), \
         patch("app.auth.roles.s.get_setting", side_effect=_settings_off_except_registration), \
         patch("app.auth.roles.s.sessions_epoch", return_value=0):
        # NB: app.auth.roles.s IS app.admin.settings, so both get_setting
        # patches target the same attribute and must agree.
        from app.main import app
        with TestClient(app) as c:
            yield c


def _make_cookie(user_dict):
    return {COOKIE_NAME: build_session_value(user_dict)}


def _make_super_admin_cookie():
    from app.config import get_settings
    settings = get_settings()
    return _make_cookie({
        "email": settings.panel_admin_email,
        "name": "Super Admin",
        "role": "super_admin",
        "scope": "admin",
    })


# ═══════════════════════════════════════════════════════════════════════
# 3. SAAS SIGNUP & LOGIN FLOWS
# ═══════════════════════════════════════════════════════════════════════

class TestSaaSSignupAndLogin:
    def test_saas_signup_creates_org_user_and_membership(self, client):
        """Signup is a DEMO REQUEST: pending user + pending org + owner
        membership + pending demo_requests row, and no session cookie."""
        db = get_sync_db()
        email = f"user_{ObjectId()}@example.com"
        org_name = f"Test Corp {ObjectId()}"

        resp = client.post("/api/auth/signup", json={
            "name": "Jane Founder",
            "email": email,
            "password": "Password123!",
            "organization_name": org_name,
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["success"] is True
        assert data["status"] == "pending"
        assert data["demo_request_id"]
        assert data.get("message")
        assert COOKIE_NAME not in resp.cookies, "A demo request must not sign the visitor in"

        # Verify DB records
        user_doc = db.users.find_one({"email": email.lower()})
        assert user_doc is not None
        assert user_doc["name"] == "Jane Founder"
        assert user_doc["status"] == "pending_approval"

        org_doc = db.organizations.find_one({"name": org_name})
        assert org_doc is not None
        assert org_doc["status"] == "pending"

        member_doc = db.organization_members.find_one({
            "organization_id": {"$in": [str(org_doc["_id"]), org_doc["_id"]]},
            "user_id": {"$in": [str(user_doc["_id"]), user_doc["_id"]]},
        })
        assert member_doc is not None
        assert member_doc["role"] == "owner"

        demo_req = db.demo_requests.find_one({"_id": ObjectId(data["demo_request_id"])})
        assert demo_req is not None
        assert demo_req["status"] == "pending"
        assert demo_req["organization_id"] == str(org_doc["_id"])

        # Duplicate signup prevention
        dup_resp = client.post("/api/auth/signup", json={
            "name": "Jane Founder 2",
            "email": email,
            "password": "Password123!",
            "organization_name": "Another Corp",
        })
        assert dup_resp.status_code == 400
        assert "already exists" in dup_resp.text.lower()

    def test_signup_validation_rules(self, client):
        base = {"name": "Val Tester", "email": f"val_{ObjectId()}@example.com",
                "organization_name": "Val Corp"}
        # >= 8 chars, an uppercase letter and a digit
        for weak in ("Short1", "alllowercase1", "NoDigitsHere"):
            r = client.post("/api/auth/signup", json={**base, "password": weak})
            assert r.status_code in (400, 422), (weak, r.text)
        # company is required by the demo-request lifecycle
        from fastapi import HTTPException
        from app.lifecycle.demo import create_demo_request
        with pytest.raises(HTTPException) as exc:
            create_demo_request(name="Val Tester", email=base["email"],
                                password="Password123!", company="   ")
        assert exc.value.status_code == 422

    def test_signup_closed_when_registration_disabled(self):
        with patch("app.admin.settings.is_maintenance_enabled", return_value=False), \
             patch("app.admin.settings.get_setting", return_value=0):
            from app.main import app
            with TestClient(app) as c:
                r = c.post("/api/auth/signup", json={
                    "name": "Closed", "email": f"closed_{ObjectId()}@example.com",
                    "password": "Password123!", "organization_name": "Closed Corp"})
        assert r.status_code == 403
        assert "closed" in r.text.lower()

    def test_saas_login_and_logout(self, client):
        db = get_sync_db()
        email = f"login_{ObjectId()}@example.com"
        pwd = "SecurePassword2026!"

        # Create account (demo request)
        signup = client.post("/api/auth/signup", json={
            "name": "Login Tester",
            "email": email,
            "password": pwd,
            "organization_name": "Login Corp",
        })
        assert signup.status_code == 200, signup.text
        req_id = signup.json()["demo_request_id"]

        # Pending demo -> login blocked with a structured 403
        pending = client.post("/api/auth/login", json={"email": email, "password": pwd})
        assert pending.status_code == 403
        assert pending.json()["detail"]["code"] == "demo_pending"
        assert COOKIE_NAME not in pending.cookies

        # Super Admin approves the demo -> org "demo", user "active"
        approve = client.post(f"/api/super-admin/demo-requests/{req_id}/approve", json={},
                              cookies=_make_super_admin_cookie())
        assert approve.status_code == 200, approve.text
        assert db.users.find_one({"email": email.lower()})["status"] == "active"
        org_doc = db.organizations.find_one({"name": "Login Corp"})
        assert org_doc["status"] == "demo"

        # Login with correct credentials
        resp = client.post("/api/auth/login", json={"email": email, "password": pwd})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["success"] is True
        assert data["user"]["email"] == email.lower()
        assert "organization_id" in data["user"]
        cookie_val = resp.cookies.get(COOKIE_NAME)
        assert cookie_val is not None

        # Check /api/auth/me
        me_resp = client.get("/api/auth/me", cookies={COOKIE_NAME: cookie_val})
        assert me_resp.status_code == 200
        me_data = me_resp.json()
        assert me_data["user"]["email"] == email.lower()
        assert me_data["user"]["organization_name"] == "Login Corp"

        # Logout with session revocation
        logout_resp = client.post("/api/auth/logout", cookies={COOKIE_NAME: cookie_val})
        assert logout_resp.status_code == 200

        # After logout, accessing authenticated endpoints fails
        me_after = client.get("/api/auth/me", cookies={COOKIE_NAME: cookie_val})
        assert me_after.status_code == 401


# ═══════════════════════════════════════════════════════════════════════
# 4. SERVER-SIDE TENANT ISOLATION & IDOR PREVENTION
# ═══════════════════════════════════════════════════════════════════════

class TestTenantIsolation:
    def test_tenant_isolation_searches_and_leads(self, client):
        db = get_sync_db()

        # Create Org A and Org B
        org_a_id = ObjectId()
        org_b_id = ObjectId()

        db.organizations.insert_many([
            {"_id": org_a_id, "name": "Tenant Alpha", "slug": f"alpha-{org_a_id}", "status": "active"},
            {"_id": org_b_id, "name": "Tenant Beta", "slug": f"beta-{org_b_id}", "status": "active"},
        ])

        # Insert private customer data for Org A
        run_id_a = f"run_{ObjectId()}"
        lead_id_a = ObjectId()

        db.search_history.insert_one({
            "run_id": run_id_a,
            "organization_id": str(org_a_id),
            "query": "https://facebook.com/AlphaRealEstate",
            "platform": "facebook",
            "status": "completed",
        })

        db.ai_comments.insert_one({
            "_id": lead_id_a,
            "comment_ref": str(ObjectId()),
            "run_id": run_id_a,
            "organization_id": str(org_a_id),
            "is_lead": True,
            "lead_score": 95,
            "lead_quality": "hot",
            "sentiment": "positive",
            "intent_label": "commercial_inquiry",
            "text": "Interested in buying property in NY",
            "phone": "+1234567890",
        })

        # Real accounts + active memberships (there is no default-org
        # fallback): A owns Org A, B owns Org B, C is a plain member of Org A.
        def _account(email, org_id, role):
            uid = db.users.insert_one({"email": email, "name": email.split("@")[0],
                                       "status": "active",
                                       "default_organization_id": str(org_id)}).inserted_id
            db.organization_members.insert_one({"organization_id": str(org_id),
                                                "user_id": str(uid), "role": role,
                                                "status": "active"})
            cookie = _make_cookie({"user_id": str(uid), "email": email,
                                   "organization_id": str(org_id), "role": role,
                                   "scope": "site"})
            return str(uid), cookie

        user_a_id, cookie_a = _account("user_a@alpha.com", org_a_id, "owner")
        _user_b_id, cookie_b = _account("user_b@beta.com", org_b_id, "owner")
        _user_c_id, cookie_c = _account("user_c@alpha.com", org_a_id, "member")
        # records are owned by the user who created them
        db.search_history.update_one({"run_id": run_id_a}, {"$set": {"user_id": user_a_id}})
        db.ai_comments.update_one({"_id": lead_id_a}, {"$set": {"user_id": user_a_id}})

        # 1. User A can access Org A's search history
        resp_a = client.get("/api/search/history", cookies=cookie_a)
        assert resp_a.status_code == 200
        runs_a = [r["run_id"] for r in resp_a.json().get("searches", [])]
        assert run_id_a in runs_a

        # 2. User B CANNOT see Org A's search history
        resp_b = client.get("/api/search/history", cookies=cookie_b)
        assert resp_b.status_code == 200
        runs_b = [r["run_id"] for r in resp_b.json().get("searches", [])]
        assert run_id_a not in runs_b

        # 3. User A can access Org A's lead report
        lead_resp_a = client.get("/api/leads", cookies=cookie_a)
        assert lead_resp_a.status_code == 200
        leads_a = [str(item.get("_id") or item.get("id")) for item in lead_resp_a.json().get("leads", [])]
        assert str(lead_id_a) in leads_a

        # 4. User B CANNOT see Org A's lead report
        lead_resp_b = client.get("/api/leads", cookies=cookie_b)
        assert lead_resp_b.status_code == 200
        leads_b = [str(item.get("_id") or item.get("id")) for item in lead_resp_b.json().get("leads", [])]
        assert str(lead_id_a) not in leads_b

        # 4b. Member C of Org A only sees their OWN records (owner/admin see the org)
        resp_c = client.get("/api/search/history", cookies=cookie_c)
        assert resp_c.status_code == 200
        assert run_id_a not in [r["run_id"] for r in resp_c.json().get("searches", [])]
        lead_resp_c = client.get("/api/leads", cookies=cookie_c)
        assert lead_resp_c.status_code == 200
        assert str(lead_id_a) not in [str(i.get("_id") or i.get("id"))
                                      for i in lead_resp_c.json().get("leads", [])]

        # 4c. A signed cookie for an account without a users record is rejected
        ghost = _make_cookie({"email": "ghost@alpha.com", "organization_id": str(org_a_id),
                              "role": "owner", "scope": "site"})
        assert client.get("/api/search/history", cookies=ghost).status_code == 401

        # 5. IDOR Attack: User B attempts to access Org A's lead directly by ID
        idor_resp = client.get(f"/api/leads/{lead_id_a}", cookies=cookie_b)
        assert idor_resp.status_code == 404, "Cross-tenant lead detail access MUST return 404"

        # 6. IDOR Attack: User B attempts to add note to Org A's lead
        note_resp = client.post(f"/api/leads/{lead_id_a}/notes", json={"note": "Hacked"}, cookies=cookie_b)
        assert note_resp.status_code == 404, "Cross-tenant lead note MUST return 404"


# ═══════════════════════════════════════════════════════════════════════
# 5. SUPER ADMIN PLATFORM MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════

class TestSuperAdminPlatform:
    def test_super_admin_saas_overview(self, client):
        admin_cookie = _make_super_admin_cookie()
        resp = client.get("/api/admin/saas/overview", cookies=admin_cookie)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "platform" in data
        assert "product" in data
        assert "health" in data
        assert "organizations_total" in data["platform"]
        assert "fastapi" in data["health"]

    def test_organization_lifecycle_management(self, client):
        admin_cookie = _make_super_admin_cookie()
        db = get_sync_db()

        # 1. Create Organization via Super Admin
        org_slug = f"acme-{ObjectId()}"
        create_resp = client.post("/api/admin/organizations", json={
            "name": "Acme Global",
            "slug": org_slug,
            "owner_email": "owner@acme.com",
            "plan_id": "pro",
        }, cookies=admin_cookie)
        assert create_resp.status_code == 200, create_resp.text
        org_data = create_resp.json()["organization"]
        org_id = org_data["id"]

        # 2. List Organizations
        list_resp = client.get("/api/admin/organizations", cookies=admin_cookie)
        assert list_resp.status_code == 200
        slugs = [o.get("slug") for o in list_resp.json()["organizations"]]
        assert org_slug in slugs

        # 3. View Organization Details
        detail_resp = client.get(f"/api/admin/organizations/{org_id}", cookies=admin_cookie)
        assert detail_resp.status_code == 200
        assert detail_resp.json()["organization"]["name"] == "Acme Global"
        assert len(detail_resp.json()["members"]) >= 1

        # 4. Suspend Organization
        suspend_resp = client.post(f"/api/admin/organizations/{org_id}/suspend", json={
            "reason": "Payment overdue"
        }, cookies=admin_cookie)
        assert suspend_resp.status_code == 200

        # Verify status is suspended
        suspended_org = db.organizations.find_one({"_id": ObjectId(org_id)})
        assert suspended_org["status"] == "suspended"

        # Verify customer in suspended org cannot login
        db.users.update_one({"email": "owner@acme.com"}, {"$set": {"password_hash": "dummy"}})
        with patch("app.auth.crypto.verify_password", return_value=(True, None)):
            blocked_login = client.post("/api/auth/login", json={
                "email": "owner@acme.com",
                "password": "any",
            })
            assert blocked_login.status_code == 403
            assert "suspended" in blocked_login.text.lower()

        # 5. Activate Organization
        activate_resp = client.post(f"/api/admin/organizations/{org_id}/activate", cookies=admin_cookie)
        assert activate_resp.status_code == 200
        active_org = db.organizations.find_one({"_id": ObjectId(org_id)})
        assert active_org["status"] == "active"

    def test_session_revocation_by_super_admin(self, client):
        admin_cookie = _make_super_admin_cookie()
        user_id = str(ObjectId())

        # Create an active tracked session for this user
        session_user = create_tracked_session({
            "user_id": user_id,
            "email": "victim@customer.com",
            "organization_id": str(ObjectId()),
            "role": "member",
        })
        session_token = build_session_value(session_user)

        # Confirm session is valid
        parsed = parse_session_value(session_token)
        assert parsed is not None
        assert parsed["email"] == "victim@customer.com"

        # Super Admin revokes sessions for this user
        revoke_resp = client.post(f"/api/admin/users/{user_id}/revoke-sessions", cookies=admin_cookie)
        assert revoke_resp.status_code == 200
        assert revoke_resp.json()["revoked_count"] >= 1

        # Now the session is revoked and rejected
        parsed_after = parse_session_value(session_token)
        assert parsed_after is None, "Revoked session must be rejected immediately"

    def test_support_impersonation_lifecycle(self, client):
        admin_cookie = _make_super_admin_cookie()
        db = get_sync_db()

        target_user_id = ObjectId()
        target_org_id = ObjectId()
        target_email = f"customer_target_{ObjectId()}@example.com"

        db.users.insert_one({
            "_id": target_user_id,
            "email": target_email,
            "name": "Target Customer",
            "status": "active",
        })
        db.organizations.insert_one({
            "_id": target_org_id,
            "name": "Target Tenant",
            "slug": f"target-tenant-{target_org_id}",
            "status": "active",
        })
        db.users.update_one({"_id": target_user_id},
                            {"$set": {"default_organization_id": str(target_org_id)}})
        db.organization_members.insert_one({
            "organization_id": str(target_org_id),
            "user_id": str(target_user_id),
            "role": "owner",
            "status": "active",
        })

        # A reason is mandatory
        no_reason = client.post("/api/admin/impersonate", json={
            "user_id": str(target_user_id),
            "organization_id": str(target_org_id),
            "reason": "   ",
        }, cookies=admin_cookie)
        assert no_reason.status_code == 422
        assert COOKIE_NAME not in no_reason.cookies

        # Super Admin initiates impersonation
        imp_resp = client.post("/api/admin/impersonate", json={
            "user_id": str(target_user_id),
            "organization_id": str(target_org_id),
            "reason": "Debugging customer search issue ticket #402",
        }, cookies=admin_cookie)
        assert imp_resp.status_code == 200
        imp_cookie = imp_resp.cookies.get(COOKIE_NAME)
        assert imp_cookie is not None

        # Verify active impersonation session reflects target user and org
        me_resp = client.get("/api/auth/me", cookies={COOKIE_NAME: imp_cookie})
        assert me_resp.status_code == 200
        me_data = me_resp.json()
        assert me_data["user"]["email"] == target_email
        assert me_data["user"]["impersonated_by"] is not None
        assert "402" in me_data["user"]["impersonation_reason"]
        # the impersonation session is time-limited
        assert me_data["user"]["impersonation_expires_at"]
        assert me_data["user"]["organization_id"] == str(target_org_id)

        # Super Admin exits impersonation
        exit_resp = client.post("/api/admin/impersonate/exit", cookies={COOKIE_NAME: imp_cookie})
        assert exit_resp.status_code == 200
        restored = exit_resp.cookies.get(COOKIE_NAME)
        assert restored is not None
        assert parse_session_value(restored)["scope"] == "admin"

    def test_impersonation_requires_real_platform_staff(self, client):
        """An admin-scope cookie for a non-staff email cannot impersonate."""
        db = get_sync_db()
        uid = db.users.insert_one({"email": f"t_{ObjectId()}@example.com",
                                   "status": "active"}).inserted_id
        fake_admin = _make_cookie({"email": "not-staff@example.com", "name": "Mallory",
                                   "role": "super_admin", "scope": "admin",
                                   "is_platform_admin": True, "platform_role": "super_admin"})
        resp = client.post("/api/admin/impersonate", json={
            "user_id": str(uid), "reason": "ticket #1"}, cookies=fake_admin)
        assert resp.status_code in (401, 403)
        assert COOKIE_NAME not in resp.cookies
