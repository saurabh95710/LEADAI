"""
Partner / Reseller / Affiliate program — end to end on the real routes
(in-memory MongoDB, mock billing provider).

Public website -> partner application -> Super Admin review (changes,
approval) -> partner sign-in (partner session scope) -> Partner Portal ->
referral click -> attributed demo signup -> demo approval -> checkout with a
partner coupon -> verified payment -> Super Admin confirmation -> commission
-> hold release -> payout -> renewal commission. Plus RBAC, isolation,
self-referral, duplicate and abuse protections, API keys and the existing
portals staying intact.
"""
import time
from datetime import timedelta
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
    from app.billing.provider import reset_billing_provider
    reset_billing_provider()
    with patch("app.admin.settings.is_maintenance_enabled", return_value=False):
        from app.main import app
        with TestClient(app) as client:
            from app.db.mongo import get_sync_db
            yield client, get_sync_db()


def _login(client, email, password, scope="site", expect=200):
    c = TestClient(client.app)
    r = c.post("/api/auth/login", json={"email": email, "password": password, "scope": scope})
    assert r.status_code == expect, (email, scope, r.status_code, r.text)
    return c


def _apply(client, email, *, name="Jane Partner", company="Growth Agency", ptype="affiliate",
           password=PW, **extra):
    body = {"name": name, "email": email, "password": password, "company": company,
            "partner_type": ptype, "country": "India", "city": "Pune",
            "website": "https://growth.example", "business_type": "Marketing agency",
            "experience": "10 years of B2B SaaS marketing", "promotion_plan": "Newsletter + webinars",
            "social_profiles": {"linkedin": "https://linkedin.com/in/jane"},
            "accepted_terms": True, **extra}
    return client.post("/api/public/partners/apply", json=body)


def _approved_partner(client, sa, db, email, *, ptype="affiliate", name="Jane Partner",
                      company="Growth Agency", permissions=None, phone=None):
    r = _apply(client, email, name=name, company=company, ptype=ptype, **({"phone": phone} if phone else {}))
    assert r.status_code == 200, r.text
    app_id = r.json()["application"]["id"]
    body = {"partner_type": ptype}
    if permissions is not None:
        body["permissions"] = permissions
    r = sa.post(f"/api/super-admin/partners/applications/{app_id}/approve", json=body)
    assert r.status_code == 200, r.text
    partner = r.json()["partner"]
    return partner, _login(client, email, PW, scope="partner")


def _settings(sa, **values):
    r = sa.put("/api/super-admin/partners/settings", json=values)
    assert r.status_code == 200, r.text
    return r.json()["settings"]


# ── application -> review -> approval -> portal access ───────────────────────

def test_application_review_and_partner_access(env):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")

    prog = client.get("/api/public/partners/program").json()["program"]
    assert prog["applications_open"] and prog["commission"]["value"] == 20.0
    assert client.get("/partners").status_code == 200          # public page

    r = _apply(client, "jane@growth.example", payout_info={"method": "paypal", "paypal_email": "pay@growth.example"},
               tax_info={"tax_id": "ABCDE1234F"})
    assert r.status_code == 200, r.text
    app_id = r.json()["application"]["id"]
    user = db.users.find_one({"email": "jane@growth.example"})
    assert user and user["status"] == "active" and user["password_hash"].startswith("$2")
    assert not db.organization_members.find_one({"user_id": str(user["_id"])})  # no fake org

    # duplicate application / duplicate account protections
    assert _apply(client, "jane@growth.example").status_code == 409
    r = _apply(client, "jane@growth.example", password="Wrong-Pass-123")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "account_exists"

    # applicant can sign in, sees status, but no protected functionality
    applicant = _login(client, "jane@growth.example", PW, scope="partner")
    me = applicant.get("/api/partner/v1/me").json()["me"]
    assert me["application_status"] == "pending" and not me["is_active_partner"]
    r = applicant.get("/api/partner/v1/dashboard")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "partner_not_approved"
    assert applicant.get("/api/partner/v1/referral-links").status_code == 403
    # applicant sees masked sensitive data
    mine = applicant.get("/api/partner/v1/application").json()["application"]
    assert "ABCDE1234F" not in str(mine) and mine["tax_info"]["tax_id"].endswith("234F")

    # Super Admin: notification, queue, request changes
    notes = sa.get("/api/super-admin/notifications").json()
    assert any(n.get("type") == "partner_application" for n in notes.get("items", []))
    q = sa.get("/api/super-admin/partners/applications?status=pending").json()
    assert q["total"] == 1 and q["counts"]["pending"] == 1
    detail = sa.get(f"/api/super-admin/partners/applications/{app_id}").json()["application"]
    assert detail["tax_info"]["tax_id"] == "ABCDE1234F"   # super admin sees full data
    assert sa.post(f"/api/super-admin/partners/applications/{app_id}/request-changes",
                   json={"note": ""}).status_code == 422
    r = sa.post(f"/api/super-admin/partners/applications/{app_id}/request-changes",
                json={"note": "Please add your website traffic numbers"})
    assert r.status_code == 200 and r.json()["application"]["status"] == "changes_requested"
    r = applicant.put("/api/partner/v1/application", json={"experience": "10 years; 40k monthly visitors"})
    assert r.status_code == 200 and r.json()["application"]["status"] == "pending"

    # approve as reseller with explicit tier
    tiers = sa.get("/api/super-admin/partners/tiers").json()["items"]
    r = sa.post(f"/api/super-admin/partners/applications/{app_id}/approve",
                json={"partner_type": "reseller", "tier_id": tiers[0]["id"]})
    assert r.status_code == 200, r.text
    partner = r.json()["partner"]
    assert partner["status"] == "active" and partner["partner_type"] == "reseller"
    assert partner["referral_code"] and partner["partner_code"].startswith("LP-")
    assert "customers.create" in partner["effective_permissions"]
    # approving twice is refused
    assert sa.post(f"/api/super-admin/partners/applications/{app_id}/approve", json={}).status_code == 409

    # the applicant session now has the full portal (permissions re-read per request)
    dash = applicant.get("/api/partner/v1/dashboard")
    assert dash.status_code == 200, dash.text
    assert dash.json()["dashboard"]["kpis"]["clicks"] == 0
    links = applicant.get("/api/partner/v1/referral-links").json()["links"]
    assert links["referral_url"].endswith("/r/" + partner["referral_code"])
    assert applicant.get("/partner").status_code == 200

    # audit trail of the partner
    audit = sa.get(f"/api/super-admin/partners/{partner['id']}/audit").json()["items"]
    actions = {a["action"] for a in audit}
    assert {"partner.application.approved", "partner.created"} <= actions


def test_rejected_and_suspended_partners_are_locked_out(env):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    app_id = _apply(client, "rex@spam.example").json()["application"]["id"]
    assert sa.post(f"/api/super-admin/partners/applications/{app_id}/reject",
                   json={"reason": "Not a fit"}).status_code == 200
    r = client.post("/api/auth/login", json={"email": "rex@spam.example", "password": PW, "scope": "partner"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "partner_rejected"

    partner, portal = _approved_partner(client, sa, db, "sam@agency.example")
    assert sa.post(f"/api/super-admin/partners/{partner['id']}/suspend", json={"reason": ""}).status_code == 422
    assert sa.post(f"/api/super-admin/partners/{partner['id']}/suspend",
                   json={"reason": "Brand misuse"}).status_code == 200
    # existing session revoked immediately, new sign-in refused
    assert portal.get("/api/partner/v1/dashboard").status_code == 401
    r = client.post("/api/auth/login", json={"email": "sam@agency.example", "password": PW, "scope": "partner"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "partner_suspended"
    assert sa.post(f"/api/super-admin/partners/{partner['id']}/reactivate", json={}).status_code == 200
    portal = _login(client, "sam@agency.example", PW, scope="partner")
    assert portal.get("/api/partner/v1/dashboard").status_code == 200
    # wrong password never reveals partner state
    r = client.post("/api/auth/login", json={"email": "sam@agency.example", "password": "nope", "scope": "partner"})
    assert r.status_code == 401


# ── full referral -> customer -> commission -> payout lifecycle ──────────────

def test_referral_to_commission_to_payout(env):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    _settings(sa, commission_hold_days=0, min_payout=1)
    partner, portal = _approved_partner(client, sa, db, "ana@affiliates.example", name="Ana Affiliate",
                                        company="Ana Media")
    pid = partner["id"]

    # campaign link
    r = portal.post("/api/partner/v1/campaigns", json={"name": "Spring Webinar", "landing_path": "/pricing"})
    assert r.status_code == 200, r.text
    camp = r.json()["campaign"]
    assert camp["url"].endswith(f"/r/{partner['referral_code']}/spring-webinar")

    # a visitor clicks the campaign link -> signed cookie -> lands on /pricing
    visitor = TestClient(client.app)
    r = visitor.get(f"/r/{partner['referral_code']}/spring-webinar", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("/pricing?ref=")
    assert visitor.cookies.get("leadai_ref") and visitor.cookies.get("leadai_vid")
    assert db.partner_referral_clicks.count_documents({"partner_id": pid}) == 1
    # an unknown code still lands the visitor on the site, with no attribution
    r = TestClient(client.app).get("/r/NOPE123", follow_redirects=False)
    assert r.status_code == 302 and "leadai_ref" not in r.cookies

    # signup (demo request) with the cookie -> attributed referral
    email, pwd = "owner@cafe.example", "Cafe-Owner-2026"
    r = visitor.post("/api/auth/signup", json={"email": email, "password": pwd, "name": "Ravi Owner",
                                               "company": "Blue Cafe", "accepted_terms": True})
    assert r.status_code == 200, r.text
    user = db.users.find_one({"email": email})
    org_id = user["default_organization_id"]
    ref = db.partner_referrals.find_one({"organization_id": org_id})
    assert ref and ref["partner_id"] == pid and ref["stage"] == "signed_up"
    assert ref["source"] == "link" and ref["campaign_id"] == camp["id"]
    assert db.organizations.find_one({"_id": ObjectId(org_id)})["referred_by_partner_id"] == pid

    # demo approval advances the funnel
    req_id = r.json()["demo_request_id"]
    assert sa.post(f"/api/super-admin/demo-requests/{req_id}/approve", json={}).status_code == 200
    assert db.partner_referrals.find_one({"organization_id": org_id})["stage"] == "demo"

    # Super Admin coupon for the partner; the customer checks out with it
    r = sa.post("/api/super-admin/partners/coupons", json={"partner_id": pid, "code": "ANA10",
                                                          "discount_type": "percentage", "discount_value": 10})
    assert r.status_code == 200, r.text
    owner = _login(client, email, pwd)
    plans = owner.get("/api/billing/plans").json()["plans"]
    paid = next(p for p in plans if (p.get("price_monthly") or 0) > 0 and p["slug"] != "enterprise")
    price = float(paid["price_monthly"])
    r = owner.post("/api/billing/checkout", json={"plan_slug": paid["slug"], "coupon_code": "BOGUS1"})
    assert r.status_code == 422
    r = owner.post("/api/billing/checkout", json={"plan_slug": paid["slug"], "coupon_code": "ana10"})
    assert r.status_code == 200, r.text
    checkout = r.json()["checkout"]
    assert checkout["amount"] == round(price * 0.9, 2) and checkout["coupon"]["code"] == "ANA10"
    session_id = checkout["session_id"]
    assert owner.post(f"/api/billing/checkout/{session_id}/mock-pay", json={"succeed": True}).status_code == 200
    sub = db.subscriptions.find_one({"checkout_session_id": session_id})
    assert sub["status"] == "pending_admin_confirmation"
    # no commission before Super Admin confirmation
    assert db.partner_commissions.count_documents({"partner_id": pid}) == 0
    assert sa.post(f"/api/super-admin/subscriptions/{sub['_id']}/confirm").status_code == 200

    com = db.partner_commissions.find_one({"partner_id": pid})
    assert com and com["status"] == "pending" and com["event"] == "initial"
    assert com["amount"] == round(round(price * 0.9, 2) * 0.20, 2)
    assert db.partner_referrals.find_one({"organization_id": org_id})["stage"] == "customer"
    assert db.partner_coupons.find_one({"code": "ANA10"})["times_redeemed"] == 1
    assert db.partner_coupon_usages.count_documents({"organization_id": org_id}) == 1
    # confirming the same payment again never duplicates the commission
    from app.partners.commissions import on_subscription_activated
    on_subscription_activated(db.subscriptions.find_one({"_id": sub["_id"]}))
    assert db.partner_commissions.count_documents({"partner_id": pid}) == 1

    dash = portal.get("/api/partner/v1/dashboard").json()["dashboard"]
    k = dash["kpis"]
    assert k["clicks"] == 1 and k["referrals"] == 1 and k["customers"] == 1
    assert k["active_subscriptions"] == 1 and k["revenue"] == round(price * 0.9, 2)
    cur = com["currency"]
    assert dash["balances"][cur]["pending"] == com["amount"]
    assert dash["recent_customers"][0]["company"] == "Blue Cafe"
    assert "owner@cafe.example" not in str(dash)   # partner sees a masked contact only
    analytics = portal.get("/api/partner/v1/analytics").json()["analytics"]
    assert analytics["totals"]["clicks"] == 1 and analytics["campaigns"][0]["referrals"] == 1

    # payout needs payout details and an available balance
    r = portal.post("/api/partner/v1/payouts", json={})
    assert r.status_code == 422
    r = portal.put("/api/partner/v1/profile/payout",
                   json={"payout_info": {"method": "bank_transfer", "account_name": "Ana Media",
                                         "account_number": "123456789012", "ifsc": "HDFC0001234"}})
    assert r.status_code == 200 and "123456789012" not in r.text
    assert r.json()["partner"]["payout_info"]["account_number"].endswith("9012")
    r = portal.post("/api/partner/v1/payouts", json={"currency": cur})
    assert r.status_code == 422                  # still pending (hold period)

    # same IP as the partner's own application (one test client) -> suspicious:
    # it qualifies after the (0-day) qualification period but is NOT auto-approved
    from app.partners.commissions import release_matured
    assert com["requires_manual_approval"] is True
    assert release_matured() == 1
    assert db.partner_commissions.find_one({"_id": com["_id"]})["status"] == "qualified"
    r = sa.get("/api/super-admin/partners/commissions?status=qualified&manual=true").json()
    assert r["total"] == 1 and r["items"][0]["partner_name"] == "Ana Media"
    assert sa.post(f"/api/super-admin/partners/commissions/{com['_id']}/approve").status_code == 200
    # payout schedule "on_request": approved commissions are payable at once
    assert db.partner_commissions.find_one({"_id": com["_id"]})["status"] == "payable"
    wallet = portal.get("/api/partner/v1/wallet").json()["wallet"]
    assert wallet["balances"][cur]["available"] == com["amount"]
    r = portal.post("/api/partner/v1/payouts", json={"currency": cur})
    assert r.status_code == 200, r.text
    payout = r.json()["payout"]
    assert payout["amount"] == com["amount"] and payout["status"] == "requested"
    assert "123456789012" not in str(payout)
    assert portal.post("/api/partner/v1/payouts", json={"currency": cur}).status_code == 409
    wallet = portal.get("/api/partner/v1/wallet").json()["wallet"]
    assert wallet["balances"][cur]["available"] == 0 and wallet["balances"][cur]["processing"] == com["amount"]
    assert db.partner_commissions.find_one({"_id": com["_id"]})["status"] == "processing"

    # Super Admin: request -> review -> approve -> processing -> paid
    po_url = f"/api/super-admin/partners/payouts/{payout['id']}"
    assert sa.post(po_url + "/mark_paid", json={"reference": "X"}).status_code == 409   # not approved yet
    assert sa.post(po_url + "/review", json={}).json()["payout"]["status"] == "under_review"
    assert portal.post(f"/api/partner/v1/payouts/{payout['id']}/cancel").status_code == 409  # partner can't now
    assert sa.post(po_url + "/approve", json={}).json()["payout"]["status"] == "approved"
    assert sa.post(po_url + "/process", json={}).json()["payout"]["status"] == "processing"
    assert sa.post(po_url + "/mark_paid", json={"reference": ""}).status_code == 422
    r = sa.post(f"/api/super-admin/partners/payouts/{payout['id']}/mark_paid", json={"reference": "UTR-77881"})
    assert r.status_code == 200 and r.json()["payout"]["status"] == "paid"
    assert db.partner_commissions.find_one({"_id": com["_id"]})["status"] == "paid"
    wallet = portal.get("/api/partner/v1/wallet").json()["wallet"]
    assert wallet["balances"][cur]["paid"] == com["amount"] and wallet["balances"][cur]["processing"] == 0
    assert {t["type"] for t in wallet["transactions"]} >= {"commission_earned", "commission_approved",
                                                            "payout_requested", "payout_paid"}

    # renewal payment -> recurring commission (default rule: 20% for 12 months)
    from app.lifecycle.maintenance import renew_subscription
    from app.db.models import utcnow
    live = db.subscriptions.find_one({"_id": sub["_id"]})
    db.subscriptions.update_one({"_id": sub["_id"]}, {"$set": {"current_period_end": utcnow() + timedelta(days=1)}})
    live = db.subscriptions.find_one({"_id": sub["_id"]})
    res = renew_subscription(db, live, source="test", amount=live["amount"])
    assert res["renewed"], res
    renewals = list(db.partner_commissions.find({"partner_id": pid, "event": "renewal"}))
    assert len(renewals) == 1 and renewals[0]["amount"] == com["amount"]
    # Super Admin reverses it (e.g. refund) -> wallet follows
    r = sa.post(f"/api/super-admin/partners/commissions/{renewals[0]['_id']}/reverse", json={"reason": "Refunded"})
    assert r.status_code == 200
    assert portal.get("/api/partner/v1/wallet").json()["wallet"]["balances"][cur]["pending"] == 0

    # Super Admin partner detail reconciles
    d = sa.get(f"/api/super-admin/partners/{pid}").json()["partner"]
    assert d["stats"]["customers"] == 1 and d["balances"][cur]["paid"] == com["amount"]
    assert d["payout_info"]["account_number"] == "123456789012"   # unmasked for super admin


def test_reseller_onboards_customer_through_demo_flow(env):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    partner, portal = _approved_partner(client, sa, db, "res@resell.example", ptype="reseller",
                                        name="Ravi Reseller", company="Resell Co")
    r = portal.post("/api/partner/v1/customers", json={"company": "Green Gym", "name": "Gita Owner",
                                                       "email": "gita@greengym.example"})
    assert r.status_code == 200, r.text
    org_id = r.json()["customer"]["organization_id"]
    org = db.organizations.find_one({"_id": ObjectId(org_id)})
    assert org["status"] == "pending"            # existing demo approval still applies
    ref = db.partner_referrals.find_one({"organization_id": org_id})
    assert ref["managed"] and ref["source"] == "reseller" and ref["partner_id"] == partner["id"]
    reset = db.password_resets.find_one({"purpose": "partner_onboarding"})
    assert reset and reset["user_id"] == ref["user_id"]
    detail = portal.get(f"/api/partner/v1/customers/{org_id}").json()["customer"]
    assert detail["organization"]["status"] == "pending" and detail["owner_activated"] is False
    assert portal.post(f"/api/partner/v1/customers/{org_id}/resend-setup").status_code == 200
    # an affiliate cannot onboard customers
    _, affiliate = _approved_partner(client, sa, db, "aff@only.example", name="Aff Only")
    r = affiliate.post("/api/partner/v1/customers", json={"company": "X", "name": "Y", "email": "y@x.example"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "permission_denied"


# ── isolation / RBAC ─────────────────────────────────────────────────────────

def test_cross_partner_and_cross_portal_isolation(env, site_account):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    p1, portal1 = _approved_partner(client, sa, db, "one@p1.example", ptype="reseller", name="One")
    p2, portal2 = _approved_partner(client, sa, db, "two@p2.example", ptype="reseller", name="Two")
    org_id = portal1.post("/api/partner/v1/customers", json={
        "company": "Acme", "name": "Al", "email": "al@acme.example"}).json()["customer"]["organization_id"]
    camp = portal1.post("/api/partner/v1/campaigns", json={"name": "Private"}).json()["campaign"]

    # partner 2 cannot see or touch partner 1's records
    assert portal2.get(f"/api/partner/v1/customers/{org_id}").status_code == 404
    assert portal2.patch(f"/api/partner/v1/campaigns/{camp['id']}", json={"name": "Hijack"}).status_code == 404
    assert portal2.get("/api/partner/v1/referrals").json()["total"] == 0
    assert db.security_events.find_one({"type": "cross_partner_access"}) is not None

    # partner sessions never reach Super Admin / tenant / org APIs
    assert portal1.get("/api/super-admin/partners").status_code == 401
    assert portal1.get("/api/super-admin/organizations").status_code == 401
    assert portal1.get("/api/notifications").status_code == 401
    assert portal1.get("/api/billing/subscription").status_code == 401
    r = portal1.get("/api/auth/me")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "partner_session"
    r = portal1.get("/superadmin", follow_redirects=False)
    assert r.status_code == 303

    # org users and anonymous visitors cannot use the Partner Portal
    site_account("member@tenant.example")
    db.users.update_one({"email": "member@tenant.example"}, {"$set": {"password_hash": _hash(PW)}})
    member = _login(client, "member@tenant.example", PW)
    assert member.get("/api/partner/v1/dashboard").status_code == 401
    assert member.get("/partner", follow_redirects=False).status_code == 303
    assert TestClient(client.app).get("/api/partner/v1/me").status_code == 401
    # an org member who is not a partner cannot use the partner scope
    r = client.post("/api/auth/login", json={"email": "member@tenant.example", "password": PW, "scope": "partner"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "not_a_partner"
    # existing portals still work for the member
    assert member.get("/api/auth/me").status_code == 200

    # permissions are enforced server-side and revocable by the Super Admin
    r = sa.patch(f"/api/super-admin/partners/{p1['id']}",
                 json={"permissions": ["dashboard.view", "profile.manage"]})
    assert r.status_code == 200
    assert portal1.get("/api/partner/v1/commissions").status_code == 403
    assert portal1.get("/api/partner/v1/dashboard").status_code == 200
    # a platform viewer can read partners but not change them
    assert sa.patch(f"/api/super-admin/partners/{p1['id']}", json={"permissions": ["bogus"]}).status_code == 422


def _hash(pw):
    from app.auth.crypto import hash_password
    return hash_password(pw)


def test_platform_staff_partner_permissions(env):
    client, db = env
    from app.db.models import utcnow
    db.users.insert_one({"email": "ops@leadai.example", "name": "Ops", "password_hash": _hash(PW),
                         "status": "active", "is_platform_admin": True, "platform_role": "support_admin",
                         "created_at": utcnow()})
    staff = _login(client, "ops@leadai.example", PW, scope="admin")
    # /api/super-admin/* is Super Admin only at the gate
    assert staff.get("/api/super-admin/partners").status_code == 403
    from app.auth.permissions import PLATFORM_ROLE_PERMISSIONS
    assert "partners.manage" in PLATFORM_ROLE_PERMISSIONS["super_admin"]
    assert "partners.manage" not in PLATFORM_ROLE_PERMISSIONS["support_admin"]


# ── abuse protections ────────────────────────────────────────────────────────

def test_self_referral_is_blocked(env):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    partner, portal = _approved_partner(client, sa, db, "jane.doe@gmail.com", name="Jane Doe")
    visitor = TestClient(client.app)
    visitor.get(f"/r/{partner['referral_code']}", follow_redirects=False)
    r = visitor.post("/api/auth/signup", json={"email": "janedoe+shop@gmail.com", "password": "Shop-Owner-2026",
                                               "name": "Jane", "company": "Jane Shop", "accepted_terms": True})
    assert r.status_code == 200, r.text
    org_id = db.users.find_one({"email": "janedoe+shop@gmail.com"})["default_organization_id"]
    assert db.partner_referrals.find_one({"organization_id": org_id}) is None
    assert db.security_events.find_one({"type": "partner_self_referral"}) is not None
    # the partner's own click is never recorded
    before = db.partner_referral_clicks.count_documents({})
    portal.get(f"/r/{partner['referral_code']}", follow_redirects=False)
    assert db.partner_referral_clicks.count_documents({}) == before


def test_attribution_models_and_code_entry(env):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    a, _ = _approved_partner(client, sa, db, "first@a.example", name="First")
    b, _ = _approved_partner(client, sa, db, "second@b.example", name="Second")
    visitor = TestClient(client.app)
    visitor.get(f"/r/{a['referral_code']}", follow_redirects=False)
    visitor.get(f"/r/{b['referral_code']}", follow_redirects=False)
    visitor.post("/api/auth/signup", json={"email": "ft@shop.example", "password": "Shop-Owner-2026",
                                           "name": "F", "company": "FT Shop", "accepted_terms": True})
    org = db.users.find_one({"email": "ft@shop.example"})["default_organization_id"]
    assert db.partner_referrals.find_one({"organization_id": org})["partner_id"] == a["id"]   # first touch

    _settings(sa, attribution_model="last_touch")
    visitor2 = TestClient(client.app)
    visitor2.get(f"/r/{a['referral_code']}", follow_redirects=False)
    visitor2.get(f"/r/{b['referral_code']}", follow_redirects=False)
    visitor2.post("/api/auth/signup", json={"email": "lt@shop.example", "password": "Shop-Owner-2026",
                                            "name": "L", "company": "LT Shop", "accepted_terms": True})
    org = db.users.find_one({"email": "lt@shop.example"})["default_organization_id"]
    assert db.partner_referrals.find_one({"organization_id": org})["partner_id"] == b["id"]   # last touch

    # typed code without a click
    r = client.post("/api/auth/signup", json={"email": "code@shop.example", "password": "Shop-Owner-2026",
                                              "name": "C", "company": "Code Shop", "accepted_terms": True,
                                              "ref_code": a["referral_code"].lower()})
    assert r.status_code == 200
    org = db.users.find_one({"email": "code@shop.example"})["default_organization_id"]
    ref = db.partner_referrals.find_one({"organization_id": org})
    assert ref["partner_id"] == a["id"] and ref["source"] == "code"

    # an expired attribution window attributes nothing
    from app.partners.referrals import decode_cookie, encode_cookie
    visitor3 = TestClient(client.app)
    visitor3.get(f"/r/{a['referral_code']}", follow_redirects=False)
    data = decode_cookie(visitor3.cookies.get("leadai_ref"))
    data["ft"]["t"] = data["lt"]["t"] = int(time.time()) - 400 * 86400
    visitor3.cookies.set("leadai_ref", encode_cookie(data))
    visitor3.post("/api/auth/signup", json={"email": "old@shop.example", "password": "Shop-Owner-2026",
                                            "name": "O", "company": "Old Shop", "accepted_terms": True})
    org = db.users.find_one({"email": "old@shop.example"})["default_organization_id"]
    assert db.partner_referrals.find_one({"organization_id": org}) is None
    # a forged cookie is ignored
    visitor4 = TestClient(client.app)
    visitor4.cookies.set("leadai_ref", "eyJmdCI6e319.deadbeef")
    visitor4.post("/api/auth/signup", json={"email": "forge@shop.example", "password": "Shop-Owner-2026",
                                            "name": "G", "company": "Forge Shop", "accepted_terms": True})
    org = db.users.find_one({"email": "forge@shop.example"})["default_organization_id"]
    assert db.partner_referrals.find_one({"organization_id": org}) is None


def test_click_flood_is_rate_limited(env):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    _settings(sa, max_clicks_per_ip_per_hour=3)
    p, _ = _approved_partner(client, sa, db, "flood@p.example", name="Flood")
    for _ in range(5):
        TestClient(client.app).get(f"/r/{p['referral_code']}", follow_redirects=False)
    assert db.partner_referral_clicks.count_documents({"partner_id": p["id"], "suspicious": True}) == 2


def test_partner_cannot_use_own_coupon_and_coupon_rules(env):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    p, portal = _approved_partner(client, sa, db, "cp@p.example", name="Coupon P",
                                  permissions=["dashboard.view", "coupons.manage"])
    # partner coupon creation is off by default, then capped
    r = portal.post("/api/partner/v1/coupons", json={"code": "MYCODE", "discount_value": 10})
    assert r.status_code == 403
    _settings(sa, partner_coupons_enabled=True, max_partner_coupon_percent=15)
    assert portal.post("/api/partner/v1/coupons", json={"code": "MYCODE", "discount_value": 50}).status_code == 422
    r = portal.post("/api/partner/v1/coupons", json={"code": "MYCODE", "discount_value": 15})
    assert r.status_code == 200, r.text
    assert portal.post("/api/partner/v1/coupons", json={"code": "mycode", "discount_value": 5}).status_code == 409
    # the partner's own organization cannot redeem it
    from app.partners.coupons import validate_for_checkout
    from fastapi import HTTPException
    user_id = db.partners.find_one({"_id": ObjectId(p["id"])})["user_id"]
    org_id = str(db.organizations.insert_one({"name": "Own", "status": "demo"}).inserted_id)
    db.organization_members.insert_one({"organization_id": org_id, "user_id": user_id, "role": "owner",
                                        "status": "active"})
    with pytest.raises(HTTPException) as e:
        validate_for_checkout("MYCODE", organization_id=org_id, plan_slug="starter", price=49, currency="USD")
    assert "own coupon" in str(e.value.detail)


# ── partner API keys ─────────────────────────────────────────────────────────

def test_partner_api_keys_are_read_only_and_isolated(env):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    p, portal = _approved_partner(client, sa, db, "api@p.example", name="Api P")
    assert portal.post("/api/partner/v1/api-keys", json={"name": "CI"}).status_code == 403
    perms = p["permissions"] + ["api.access"]
    assert sa.patch(f"/api/super-admin/partners/{p['id']}", json={"permissions": perms}).status_code == 200
    r = portal.post("/api/partner/v1/api-keys", json={"name": "CI"})
    assert r.status_code == 200, r.text
    raw = r.json()["api_key"]
    assert raw.startswith("lap_live_") and db.api_keys.find_one({"key_hash": {"$exists": True},
                                                                  "partner_id": p["id"]})["key_hash"] != raw
    api = TestClient(client.app)
    hdr = {"X-API-Key": raw}
    assert api.get("/api/partner/v1/dashboard", headers=hdr).status_code == 200
    assert api.get("/api/partner/v1/referrals", headers=hdr).status_code == 200
    r = api.post("/api/partner/v1/campaigns", json={"name": "X"}, headers=hdr)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "api_key_read_only"
    assert api.get("/api/partner/v1/api-keys", headers=hdr).status_code == 403
    # partner keys never work on the organization API, and vice versa
    assert api.get("/api/v1/leads", headers=hdr).status_code == 401
    assert api.get("/api/partner/v1/dashboard", headers={"X-API-Key": "lap_live_forged"}).status_code == 401
    # revoking api.access disables the key at once
    assert sa.patch(f"/api/super-admin/partners/{p['id']}", json={"permissions": p["permissions"]}).status_code == 200
    assert api.get("/api/partner/v1/dashboard", headers=hdr).status_code == 401


# ── existing features fixed on the way ──────────────────────────────────────

def test_public_api_v1_works_with_org_api_key(env, site_account):
    """Was unreachable (auth gate demanded a session) and crashed (awaited a
    non-coroutine). Now: key auth, org scoping, per-key rate limit."""
    client, db = env
    _, org_id = site_account("dev@tenant.example")
    from app.services.api_keys import create_api_key
    raw, _ = create_api_key(db, organization_id=org_id, name="t", scopes=["leads:read"],
                            rate_limit_per_minute=2)
    api = TestClient(client.app)
    assert api.get("/api/v1/leads", headers={"X-API-Key": raw}).status_code == 200
    assert api.get("/api/v1/leads", headers={"X-API-Key": raw}).status_code == 200
    assert api.get("/api/v1/leads", headers={"X-API-Key": raw}).status_code == 429
    assert api.get("/api/v1/leads").status_code == 401


def test_two_factor_is_enforced_at_login(env, site_account):
    """2FA used to be skipped (the flag was looked up on session claims)."""
    client, db = env
    site_account("twofa@tenant.example")
    from app.auth.totp import generate_totp_secret
    secret = generate_totp_secret()
    db.users.update_one({"email": "twofa@tenant.example"},
                        {"$set": {"password_hash": _hash(PW), "totp_enabled": True, "totp_secret": secret}})
    r = client.post("/api/auth/login", json={"email": "twofa@tenant.example", "password": PW})
    assert r.status_code == 200 and r.json().get("requires_2fa") is True
    assert "leadai_session" not in r.cookies
    r = client.post("/api/auth/login", json={"email": "twofa@tenant.example", "password": PW,
                                             "totp_code": "000000"})
    assert r.status_code == 401
    import hmac
    import struct
    import base64
    import hashlib
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    msg = struct.pack(">Q", int(time.time()) // 30)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    o = h[-1] & 0x0F
    code = str((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 1000000).zfill(6)
    r = client.post("/api/auth/login", json={"email": "twofa@tenant.example", "password": PW, "totp_code": code})
    assert r.status_code == 200 and r.json()["success"] is True


def test_razorpay_checkout_keeps_major_unit_amount(env, monkeypatch):
    client, db = env
    import asyncio
    import json

    import httpx

    from app.billing import provider as prov
    sent = {}

    def razorpay_api(request):  # the Razorpay Orders API, mocked
        sent.update(json.loads(request.content))
        return httpx.Response(200, json={"id": "order_Major1", "amount": sent["amount"]})
    monkeypatch.setattr(prov, "_HTTP_TRANSPORT", httpx.MockTransport(razorpay_api))
    sub_id = db.subscriptions.insert_one({"status": "pending_payment", "amount": 2499.0}).inserted_id
    out = asyncio.run(prov.RazorpayBillingProvider("k", "s", "w").create_checkout_session(
        subscription={"_id": sub_id, "amount": 2499.0}, plan={"name": "Starter", "currency": "INR"},
        customer_email="b@x.example", success_url="/s", cancel_url="/c"))
    # the order is in paise, the stored subscription amount stays in rupees
    assert out["amount"] == 249900 and sent["amount"] == 249900 and sent["currency"] == "INR"
    assert out["order_id"] == "order_Major1"
    doc = db.subscriptions.find_one({"_id": sub_id})
    assert doc["amount"] == 2499.0 and doc["checkout_session_id"] == "order_Major1"


def test_hold_period_releases_clean_commissions_automatically(env):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    _settings(sa, commission_hold_days=0)
    p, _ = _approved_partner(client, sa, db, "clean@p.example", name="Clean")
    org_id = str(db.organizations.insert_one({"name": "Clean Co", "status": "active"}).inserted_id)
    from app.partners.commissions import create_commission_for_payment, release_matured
    from app.partners.referrals import create_referral
    partner = db.partners.find_one({"_id": ObjectId(p["id"])})
    ref = create_referral(db, partner, organization_id=org_id, user_id=None, email="boss@clean.example",
                          company="Clean Co", source="link", signup_ip="198.51.100.7")
    assert ref and not ref["suspicious"]
    com = create_commission_for_payment(organization_id=org_id, subscription_id=str(ObjectId()), amount=100,
                                        currency="USD", plan_slug="pro", period_key="initial", event="initial", db=db)
    assert com["amount"] == 20.0 and not com["requires_manual_approval"]
    assert release_matured() == 1
    # qualified -> auto-approved -> payable (schedule on_request) in one pass
    assert db.partner_commissions.find_one({"_id": com["_id"]})["status"] == "payable"
    # settings validation
    assert sa.put("/api/super-admin/partners/settings", json={"attribution_model": "middle"}).status_code == 422
    assert sa.put("/api/super-admin/partners/settings", json={"attribution_window_days": 0}).status_code == 422
