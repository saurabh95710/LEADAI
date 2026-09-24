"""
Tests for LeadAI SaaS Master Prompt 2:
- SaaS Organization Settings & Branding
- Team Management & Secure Invitation System (Token Hashing & Acceptance)
- SaaS Plan Engine & Configurable Limits/Entitlements
- Centralized EntitlementService & Concurrency-Safe Quota Enforcement
- Structured HTTP 402 QUOTA_EXCEEDED Behavior
- Subscriptions & Trial Lifecycle (Provision Trial, Downgrade Limits, Cancel, Reactivate)
- Billing Provider Abstraction, Mock Checkout, Invoices & Receipts
- Webhook Processing with Idempotency Deduplication
- Super Admin SaaS Operations (Plans CRUD, Subscriptions, Trial Extension, Credit Grants, Invoices CSV)
- Tenant Isolation & Gated Features (URL Search & CSV Export)
"""
import uuid
import pytest
from unittest.mock import patch
from bson import ObjectId
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.auth.service import build_session_value, COOKIE_NAME
from app.billing.entitlements import (
    EntitlementService,
    QuotaExceededException,
    FeatureNotAvailableException,
)
from app.billing.invitations import create_invitation, validate_invitation, accept_invitation
from app.billing.plans import ensure_default_plans, get_all_plans, get_plan_by_slug_or_id
from app.billing.provider import get_billing_provider, process_billing_webhook
from app.billing.subscriptions import (
    provision_trial_subscription,
    change_subscription_plan,
    cancel_subscription,
    reactivate_subscription,
    extend_trial,
)
from app.billing.usage import record_usage_atomic, get_current_usage_doc
from app.config import get_settings
from app.db.models import utcnow
from app.db.mongo import get_sync_db, reset_client_caches


@pytest.fixture(autouse=True)
def reset_db_caches():
    reset_client_caches()
    yield
    reset_client_caches()


@pytest.fixture(autouse=True)
async def seed_plans():
    """Plans come only from the DB (no in-code fallback) — seed the defaults
    exactly as the app lifespan does."""
    await ensure_default_plans()
    yield


@pytest.fixture
def mock_provider(monkeypatch):
    """Force the dev mock billing provider regardless of .env Stripe keys."""
    from app.billing import provider as prov
    monkeypatch.setattr(prov, "_GLOBAL_PROVIDER", prov.MockBillingProvider())
    return prov._GLOBAL_PROVIDER


@pytest.fixture
def client():
    with patch("app.admin.settings.is_maintenance_enabled", return_value=False), \
         patch("app.admin.settings.maintenance_message", return_value="Under maintenance"), \
         patch("app.admin.settings.get_setting", return_value=0), \
         patch("app.admin.settings.get_bool", return_value=True), \
         patch("app.admin.settings.is_platform_enabled", return_value=True), \
         patch("app.auth.roles.s.get_setting", return_value=0), \
         patch("app.auth.roles.s.sessions_epoch", return_value=0):
        from app.main import app
        with TestClient(app) as c:
            yield c


def _make_cookie(user_dict):
    return {COOKIE_NAME: build_session_value(user_dict)}


def _make_super_admin_cookie():
    settings = get_settings()
    return _make_cookie({
        "email": settings.panel_admin_email,
        "name": "Super Admin",
        "role": "super_admin",
        "scope": "admin",
        "is_platform_admin": True,
        "platform_role": "super_admin",
    })


def _create_test_tenant(db, name="Acme SaaS", plan_slug="pro"):
    """Helper to create a fully wired test organization with an owner and trial subscription."""
    org_id = str(ObjectId())
    user_id = str(ObjectId())
    uid = uuid.uuid4().hex[:10]
    email = f"owner_{uid}@example.com"

    org_doc = {
        "_id": ObjectId(org_id),
        "name": name,
        "slug": f"org-{uid}",
        "owner_id": user_id,
        "plan_id": plan_slug,
        "status": "active",
        "timezone": "America/New_York",
        "currency": "USD",
        "created_at": utcnow(),
        "updated_at": utcnow(),
        "branding": {
            "company_name": name,
            "primary_color": "#7c5cff",
            "accent_color": "#f0a531",
        },
    }
    db.organizations.insert_one(org_doc)

    user_doc = {
        "_id": ObjectId(user_id),
        "email": email,
        "name": "Owner User",
        "status": "active",
        "default_organization_id": org_id,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    db.users.insert_one(user_doc)

    member_doc = {
        "_id": ObjectId(),
        "organization_id": org_id,
        "user_id": user_id,
        "role": "owner",
        "status": "active",
        "joined_at": utcnow(),
    }
    db.organization_members.insert_one(member_doc)

    return {
        "org_id": org_id,
        "user_id": user_id,
        "email": email,
        "cookie": _make_cookie({
            "user_id": user_id,
            "email": email,
            "name": "Owner User",
            "organization_id": org_id,
            "organization_name": name,
            "org_role": "owner",
            "role": "owner",
            "scope": "site",
        })
    }


# ═══════════════════════════════════════════════════════════════════════
# 1. PLAN ENGINE & LIMIT SYSTEM TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestPlanEngine:
    @pytest.mark.asyncio
    async def test_default_plans_exist(self):
        plans = await get_all_plans(active_only=True)
        slugs = [p["slug"] for p in plans]
        assert "free" in slugs
        assert "starter" in slugs
        assert "pro" in slugs
        assert "business" in slugs
        assert "enterprise" in slugs

    @pytest.mark.asyncio
    async def test_plan_lookup_and_limits(self):
        pro_plan = await get_plan_by_slug_or_id("pro")
        assert pro_plan is not None
        assert pro_plan["price_monthly"] == 149.0
        assert pro_plan["limits"]["monthly_searches"] == 500
        assert pro_plan["limits"]["monthly_ai_analyses"] == 5000
        assert pro_plan["limits"]["team_members"] == 10
        # unified plan system limit keys
        for key in ("monthly_tokens", "posts_per_search", "comments_per_post",
                    "max_leads", "storage_mb"):
            assert key in pro_plan["limits"], key
        assert pro_plan["limits"]["monthly_tokens"] == 10000
        assert "url_search" in pro_plan["features"]
        assert "csv_export" in pro_plan["features"]

    def test_public_plans_api(self, client):
        resp = client.get("/api/billing/plans")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["plans"]) >= 5


# ═══════════════════════════════════════════════════════════════════════
# 2. ENTITLEMENT & ATOMIC QUOTA SERVICE TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestEntitlementsAndQuotas:
    @pytest.mark.asyncio
    async def test_feature_entitlement_gating(self):
        db = get_sync_db()
        tenant = _create_test_tenant(db, name="Entitlement Tenant", plan_slug="starter")
        org_id = tenant["org_id"]

        # Starter plan: has url_search and csv_export, but no custom_branding
        has_search = await EntitlementService.has_feature(org_id, "url_search")
        assert has_search is True

        has_branding = await EntitlementService.has_feature(org_id, "custom_branding")
        assert has_branding is False

    @pytest.mark.asyncio
    async def test_quota_consumption_and_exceeded_exception(self):
        db = get_sync_db()
        tenant = _create_test_tenant(db, name="Quota Tenant", plan_slug="free")
        org_id = tenant["org_id"]

        # Free plan has limit of 5 monthly_searches
        # Consume 4
        for _ in range(4):
            await EntitlementService.enforce_quota_and_consume(org_id, metric="monthly_searches", quantity=1)

        # 5th should succeed
        await EntitlementService.enforce_quota_and_consume(org_id, metric="monthly_searches", quantity=1)

        # 6th MUST raise QuotaExceededException
        with pytest.raises(QuotaExceededException) as exc_info:
            await EntitlementService.enforce_quota_and_consume(org_id, metric="monthly_searches", quantity=1)

        detail = exc_info.value.detail
        assert detail["code"] == "QUOTA_EXCEEDED"
        assert detail["metric"] == "monthly_searches"
        assert detail["used"] >= 5
        assert detail["limit"] == 5
        assert detail["upgrade_available"] is True

    @pytest.mark.asyncio
    async def test_bonus_credits_increase_limit(self):
        db = get_sync_db()
        tenant = _create_test_tenant(db, name="Credit Tenant", plan_slug="free")
        org_id = tenant["org_id"]

        # Grant 50 bonus search credits
        db.temporary_entitlements.insert_one({
            "organization_id": org_id,
            "entitlement_type": "credit",
            "key": "monthly_searches",
            "value": 50,
            "reason": "Promotion",
            "created_at": utcnow(),
        })

        allowed, used, limit = await EntitlementService.check_limit(org_id, "monthly_searches")
        assert limit == 5 + 50  # 5 on Free + 50 bonus credits = 55


# ═══════════════════════════════════════════════════════════════════════
# 3. SUBSCRIPTIONS & TRIAL LIFECYCLE TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestSubscriptionLifecycle:
    @pytest.mark.asyncio
    async def test_trial_provisioning(self):
        db = get_sync_db()
        org_id = str(ObjectId())
        uid = uuid.uuid4().hex[:10]
        db.organizations.insert_one({
            "_id": ObjectId(org_id),
            "name": "Trial Org",
            "slug": f"trial-org-{uid}",
        })

        sub = await provision_trial_subscription(org_id, plan_slug="pro", trial_days=14)
        assert sub["status"] == "trialing"
        assert sub["plan_id"] == "pro"
        assert sub["trial_end"] is not None

        # Verify org was updated with subscription_id
        org = db.organizations.find_one({"_id": ObjectId(org_id)})
        assert org["subscription_id"] == sub["id"]

    @pytest.mark.asyncio
    async def test_trial_provisioning_unknown_plan_404(self):
        """No hardcoded fallback plan: an unknown plan is a 404."""
        db = get_sync_db()
        org_id = str(ObjectId())
        db.organizations.insert_one({"_id": ObjectId(org_id), "name": "No Plan Org",
                                     "slug": f"no-plan-{uuid.uuid4().hex[:8]}"})
        with pytest.raises(HTTPException) as exc:
            await provision_trial_subscription(org_id, plan_slug="does-not-exist")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_downgrade_safety_blocks_if_members_exceed(self):
        db = get_sync_db()
        tenant = _create_test_tenant(db, name="Large Team Org", plan_slug="pro")
        org_id = tenant["org_id"]
        await provision_trial_subscription(org_id, plan_slug="pro")

        # Add 3 extra members (total 4 members in org)
        for i in range(3):
            db.organization_members.insert_one({
                "organization_id": org_id,
                "user_id": str(ObjectId()),
                "role": "member",
                "status": "active",
            })

        # Free plan has team_members limit of 1
        # Downgrading to Free should raise HTTPException(400)
        with pytest.raises(HTTPException) as exc:
            await change_subscription_plan(org_id, target_plan_slug="free")
        assert exc.value.status_code == 400
        assert exc.value.detail["code"] == "DOWNGRADE_MEMBER_LIMIT_EXCEEDED"

    @pytest.mark.asyncio
    async def test_cancel_and_reactivate_subscription(self):
        db = get_sync_db()
        tenant = _create_test_tenant(db, name="Cancel Org", plan_slug="pro")
        org_id = tenant["org_id"]
        await provision_trial_subscription(org_id, plan_slug="pro")

        # Reactivate only undoes a scheduled cancellation of an ACTIVE
        # subscription — a (cancelled) trial cannot be "reactivated".
        await cancel_subscription(org_id)
        with pytest.raises(HTTPException) as exc:
            await reactivate_subscription(org_id)
        assert exc.value.status_code == 409

        # An active subscription (set by the platform) can be cancelled at
        # period end and the scheduled cancellation undone.
        await change_subscription_plan(org_id, target_plan_slug="pro")
        cancelled = await cancel_subscription(org_id)
        assert cancelled["status"] == "active"
        assert cancelled["cancel_at_period_end"] is True

        reactivated = await reactivate_subscription(org_id)
        assert reactivated["status"] == "active"
        assert reactivated["cancel_at_period_end"] is False


# ═══════════════════════════════════════════════════════════════════════
# 4. TEAM MANAGEMENT & SECURE INVITATIONS TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestTeamAndInvitations:
    @pytest.mark.asyncio
    async def test_invitation_token_hashing_and_acceptance(self):
        db = get_sync_db()
        tenant = _create_test_tenant(db, name="Invite Org")
        org_id = tenant["org_id"]
        owner_id = tenant["user_id"]

        invite_email = f"invited_{str(ObjectId())[:8]}@example.com"
        res = await create_invitation(org_id, invite_email, role="manager", invited_by_user_id=owner_id)
        # the raw token is only delivered inside the emailed link
        assert "token" not in res and "token_hash" not in res
        assert res["invite_url"].startswith("/invite/")
        raw_token = res["invite_url"].rsplit("/", 1)[1]
        assert raw_token
        assert db.email_outbox.count_documents({}) >= 1

        # Verify plaintext token is NOT stored in DB
        inv_doc = db.organization_invitations.find_one({"organization_id": org_id, "email": invite_email})
        assert "token" not in inv_doc
        assert "token_hash" in inv_doc
        assert len(inv_doc["token_hash"]) == 64  # SHA-256

        # Validate token
        valid_res = await validate_invitation(raw_token)
        assert valid_res["valid"] is True
        assert valid_res["role"] == "manager"

        # Accept invitation
        new_user_id = str(ObjectId())
        db.users.insert_one({
            "_id": ObjectId(new_user_id),
            "email": invite_email,
            "name": "New Team Member",
            "status": "active",
        })
        # the invitation is bound to the invited email
        with pytest.raises(HTTPException) as exc:
            await accept_invitation(raw_token, new_user_id, email="someone-else@example.com")
        assert exc.value.status_code == 403

        accepted = await accept_invitation(raw_token, new_user_id, email=invite_email)
        assert accepted["accepted"] is True

        # single use
        with pytest.raises(HTTPException):
            await accept_invitation(raw_token, new_user_id, email=invite_email)

        # Member should now exist in organization_members
        member = db.organization_members.find_one({"organization_id": org_id, "user_id": new_user_id})
        assert member is not None
        assert member["role"] == "manager"

    def test_customer_team_endpoints(self, client):
        db = get_sync_db()
        tenant = _create_test_tenant(db, name="Customer Team Org")
        cookies = tenant["cookie"]

        # GET team
        resp = client.get("/api/organizations/current/team", cookies=cookies)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["members"]) >= 1

        # POST invite
        new_email = f"colleague_{str(ObjectId())[:8]}@example.com"
        inv_resp = client.post(
            "/api/organizations/current/invitations",
            json={"email": new_email, "role": "member"},
            cookies=cookies,
        )
        assert inv_resp.status_code == 200
        assert inv_resp.json()["success"] is True


# ═══════════════════════════════════════════════════════════════════════
# 5. BILLING PROVIDER, CHECKOUT & WEBHOOK IDEMPOTENCY TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestBillingAndWebhooks:
    def test_mock_checkout_activates_plan_and_generates_invoice(self, client, mock_provider):
        """Checkout never activates: pending_payment -> (mock pay) ->
        pending_admin_confirmation -> (Super Admin confirm) -> active."""
        db = get_sync_db()
        tenant = _create_test_tenant(db, name="Billing Org", plan_slug="free")
        cookies = tenant["cookie"]
        org_id = tenant["org_id"]

        # 1. Checkout Pro plan -> PENDING_PAYMENT, nothing activated
        resp = client.post(
            "/api/billing/checkout",
            json={"plan_slug": "pro", "billing_cycle": "monthly"},
            cookies=cookies,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["success"] is True
        checkout = data["checkout"]
        assert checkout["status"] == "pending_payment"
        assert checkout["session_id"] and checkout["redirect_url"]
        sub_id = checkout["subscription_id"]
        assert db.subscriptions.find_one({"_id": ObjectId(sub_id)})["status"] == "pending_payment"
        assert db.invoices.count_documents({"organization_id": org_id}) == 0

        sub_data = client.get("/api/billing/subscription", cookies=cookies).json()["subscription"]
        assert sub_data.get("status") != "active" or sub_data.get("plan_id") != "pro"
        assert sub_data["pending"]["status"] == "pending_payment"

        # 2. Mock provider payment -> PENDING_ADMIN_CONFIRMATION (still not active)
        pay = client.post(f"/api/billing/checkout/{checkout['session_id']}/mock-pay",
                          json={"succeed": True}, cookies=cookies)
        assert pay.status_code == 200, pay.text
        assert pay.json()["status"] == "pending_admin_confirmation"
        assert db.subscriptions.find_one({"_id": ObjectId(sub_id)})["status"] == \
            "pending_admin_confirmation"
        assert db.invoices.count_documents({"organization_id": org_id}) == 0

        # the customer cannot confirm their own subscription
        denied = client.post(f"/api/super-admin/subscriptions/{sub_id}/confirm", cookies=cookies)
        assert denied.status_code in (401, 403)

        # 3. Super Admin confirms -> ACTIVE + invoice + org active + admin portal
        conf = client.post(f"/api/super-admin/subscriptions/{sub_id}/confirm",
                           cookies=_make_super_admin_cookie())
        assert conf.status_code == 200, conf.text

        sub_resp = client.get("/api/billing/subscription", cookies=cookies)
        assert sub_resp.status_code == 200
        sub_data = sub_resp.json()["subscription"]
        assert sub_data["plan_id"] == "pro"
        assert sub_data["status"] == "active"

        org = db.organizations.find_one({"_id": ObjectId(org_id)})
        assert org["status"] == "active"
        assert org["admin_portal_enabled"] is True
        assert org["plan_id"] == "pro"

        inv_resp = client.get("/api/billing/invoices", cookies=cookies)
        assert inv_resp.status_code == 200
        invs = inv_resp.json()["invoices"]
        assert len(invs) >= 1
        assert invs[0]["status"] == "paid"
        assert invs[0]["total"] == 149.0

    def test_mock_webhook_requires_valid_signature(self, monkeypatch):
        import hashlib, hmac, json
        from app.billing.provider import MockBillingProvider, WebhookSignatureError
        from app.admin import envvars
        monkeypatch.setattr(envvars, "get_envvar_str",
                            lambda name, fallback="": "whsec_test"
                            if name == "BILLING_WEBHOOK_SECRET" else fallback)
        prov = MockBillingProvider()
        body = json.dumps({"id": "evt_1", "type": "invoice.paid"}).encode()
        good = hmac.new(b"whsec_test", body, hashlib.sha256).hexdigest()
        assert prov.verify_webhook(body, {"x-leadai-signature": good})["id"] == "evt_1"
        with pytest.raises(WebhookSignatureError):
            prov.verify_webhook(body, {"x-leadai-signature": "0" * 64})
        with pytest.raises(WebhookSignatureError):
            prov.verify_webhook(body, {})

    @pytest.mark.asyncio
    async def test_webhook_idempotency_deduplication(self):
        db = get_sync_db()
        tenant = _create_test_tenant(db, name="Webhook Org")
        org_id = tenant["org_id"]

        event_id = f"evt_{str(ObjectId())[:8]}"
        event_payload = {
            "id": event_id,
            "type": "invoice.paid",
            "data": {
                "organization_id": org_id,
                "amount": 149.0,
                "currency": "USD",
                "invoice_number": f"INV-{str(ObjectId())[:6]}",
            }
        }

        # First delivery -> processed
        res1 = await process_billing_webhook(event_payload, event_id=event_id, provider_name="stripe")
        assert res1["status"] == "processed"

        # Duplicate delivery -> duplicate_skipped
        res2 = await process_billing_webhook(event_payload, event_id=event_id, provider_name="stripe")
        assert res2["status"] == "duplicate_skipped"


# ═══════════════════════════════════════════════════════════════════════
# 6. SUPER ADMIN SAAS MANAGEMENT & EXPORT TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestSuperAdminSaaSManagement:
    def test_super_admin_plans_crud(self, client):
        cookie = _make_super_admin_cookie()
        plan_slug = f"custom_{uuid.uuid4().hex[:8]}"

        # Create
        create_resp = client.post("/api/admin/plans", json={
            "name": "Custom Enterprise",
            "slug": plan_slug,
            "price_monthly": 299.0,
            "price_yearly": 2990.0,
            "trial_days": 30,
            "limits": {
                "monthly_searches": 1500,
                "monthly_ai_analyses": 25000,
                "team_members": 25,
            },
            "features": [
                "url_search",
                "facebook",
                "instagram",
                "youtube",
                "linkedin",
                "ai_analysis",
                "csv_export",
                "custom_branding",
            ],
        }, cookies=cookie)
        assert create_resp.status_code == 200
        plan_id = create_resp.json()["plan"]["id"]

        # Update
        patch_resp = client.patch(f"/api/admin/plans/{plan_id}", json={
            "price_monthly": 349.0,
        }, cookies=cookie)
        assert patch_resp.status_code == 200
        assert patch_resp.json()["plan"]["price_monthly"] == 349.0

        # Archive (Delete)
        del_resp = client.delete(f"/api/admin/plans/{plan_id}", cookies=cookie)
        assert del_resp.status_code == 200
        assert del_resp.json()["success"] is True
        archived = get_sync_db().plans.find_one({"slug": plan_slug})
        assert archived["status"] == "archived"

    def test_super_admin_subscriptions_and_invoices_view(self, client):
        cookie = _make_super_admin_cookie()

        # List subscriptions
        sub_resp = client.get("/api/admin/subscriptions", cookies=cookie)
        assert sub_resp.status_code == 200
        assert "subscriptions" in sub_resp.json()

        # List invoices
        inv_resp = client.get("/api/admin/invoices", cookies=cookie)
        assert inv_resp.status_code == 200
        assert "invoices" in inv_resp.json()

        # CSV Export
        exp_resp = client.get("/api/admin/billing/export?type=invoices", cookies=cookie)
        assert exp_resp.status_code == 200
        assert "text/csv" in exp_resp.headers.get("content-type", "")


# ═══════════════════════════════════════════════════════════════════════
# 7. CROSS-TENANT SECURITY & GATED SEARCH API TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestCrossTenantSecurityAndGating:
    def test_cross_tenant_isolation(self, client):
        db = get_sync_db()
        tenant_a = _create_test_tenant(db, name="Tenant A")
        tenant_b = _create_test_tenant(db, name="Tenant B")

        # Tenant A user attempts to access Tenant B team
        # Current API routes resolve tenant strictly from session context
        resp = client.get("/api/organizations/current/team", cookies=tenant_a["cookie"])
        assert resp.status_code == 200
        members = resp.json()["members"]
        # Must only see Tenant A members, never Tenant B
        user_ids = [m["user_id"] for m in members]
        assert tenant_a["user_id"] in user_ids
        assert tenant_b["user_id"] not in user_ids

    @pytest.mark.asyncio
    async def test_url_search_returns_402_when_quota_exhausted(self, client):
        db = get_sync_db()
        tenant = _create_test_tenant(db, name="Zero Quota Tenant", plan_slug="free")
        org_id = tenant["org_id"]

        # Max out quota by consuming all 5 searches allowed on the free plan
        for _ in range(5):
            await EntitlementService.enforce_quota_and_consume(org_id, metric="monthly_searches", quantity=1)

        # Attempt search
        resp = client.post(
            "/api/url/search?url=https://www.facebook.com/testpage&max_posts=5&max_comments_per_post=5",
            cookies=tenant["cookie"],
        )
        assert resp.status_code == 402
        data = resp.json()
        assert data["detail"]["code"] == "QUOTA_EXCEEDED"
        assert data["detail"]["metric"] == "monthly_searches"
        assert data["detail"]["upgrade_available"] is True
