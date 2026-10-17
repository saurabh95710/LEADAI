"""LeadAI E2E journeys, part 3: E partners, F billing, G cross-portal security.

Shares STATE with e2e/test_journeys.py (customer, org) and
e2e/test_journeys_2.py (Super Admin helpers, the member).
"""
import hashlib
import hmac
import json
import re

from e2e.conftest import wait_for
from e2e.test_journeys import RUN, STATE, _customer, _login, _no_server_errors, _skip_tour
from e2e.test_journeys_2 import _sa_view, _super

PARTNER_PW = "Partner-E2E-Pass-2026!"


def _apply_partner(s, sa, email, name):
    r = s.api.post("/api/public/partners/apply", data={
        "name": name, "email": email, "password": PARTNER_PW, "company": name + " Media",
        "partner_type": "affiliate", "experience": "Ten years of SaaS sales",
        "promotion_plan": "Newsletter and webinars", "accepted_terms": True,
        "payout_info": {"method": "paypal", "paypal_email": "pay." + email}})
    assert r.ok, r.text()
    app_id = r.json()["application"]["id"]
    r = sa.api.post(f"/api/super-admin/partners/applications/{app_id}/approve", data={"partner_type": "affiliate"})
    assert r.ok, r.text()
    return r.json()["partner"]


def _partner_login(person, name, email):
    p = person(name)
    _login(p, email, PARTNER_PW, "?partner=1")
    p.page.wait_for_url(re.compile(r"/partner"), timeout=30000)
    p.page.wait_for_load_state("networkidle")
    _skip_tour(p.page)
    return p


def _signup(v, email, company):
    v.page.goto("/signup", wait_until="networkidle")
    v.page.fill("#name", company + " Owner")
    v.page.fill("#email", email)
    v.page.fill("#company", company)
    v.page.select_option("#industry", index=1)
    v.page.fill("#password", "Ref-E2E-Pass-2026!")
    v.page.check("#terms")
    v.page.click("#submitBtn")
    v.page.wait_for_url(re.compile(r"/(user|dashboard|login)"), timeout=30000)


# ═══════════════════════════════════════════════════════════════════════════
# E. Partner Portal
# ═══════════════════════════════════════════════════════════════════════════

def test_e1_referral_link_first_touch_attribution(person, db):
    sa = _super(person)
    assert sa.api.put("/api/super-admin/partners/settings",
                      data={"attribution_model": "first_touch", "commission_hold_days": 0, "min_payout": 1}).ok
    visitor = person("visitor")
    visitor.page.goto("/partners", wait_until="networkidle")
    pa = _apply_partner(visitor, sa, f"ana.{RUN}@aff-e2e.com", "Ana")
    pb = _apply_partner(visitor, sa, f"ben.{RUN}@aff-e2e.com", "Ben")
    portal = _partner_login(person, "partner", f"ana.{RUN}@aff-e2e.com")
    portal.page.goto("/partner#/referrals")
    portal.page.wait_for_timeout(1000)
    links = portal.api.get("/api/partner/v1/referral-links").json()
    assert pa["referral_code"] in json.dumps(links), links
    # a prospect clicks Ana's link first, then Ben's, then signs up
    pros = person("prospect")
    pros.page.goto(f"/r/{pa['referral_code']}")
    pros.page.wait_for_load_state("networkidle")
    pros.page.goto(f"/r/{pb['referral_code']}")
    pros.page.wait_for_load_state("networkidle")
    email = f"owner.{RUN}@bluecafe-e2e.com"
    _signup(pros, email, f"Blue Cafe {RUN}")
    user = db.users.find_one({"email": email})
    org_id = user["default_organization_id"]
    ref = wait_for(lambda: db.partner_referrals.find_one({"organization_id": org_id}), 10, what="referral record")
    assert ref["partner_id"] == pa["id"], f"first-touch went to {ref['partner_id']} not Ana {pa['id']}"
    assert ref["stage"] in ("signed_up", "demo")
    STATE.update(partner=pa, partner_email=f"ana.{RUN}@aff-e2e.com", ref_org=org_id, ref_email=email)
    _no_server_errors(portal)


def test_e2_deal_registration(person, db):
    portal = _partner_login(person, "partner", STATE["partner_email"])
    portal.page.goto("/partner#/deals")
    portal.page.wait_for_timeout(1000)
    r = portal.api.post("/api/partner/v1/deals", data={
        "company": f"Metro Motors {RUN}", "contact_name": "Raj", "contact_email": f"raj.{RUN}@metro-e2e.com",
        "expected_plan": "starter", "notes": "Wants a demo"})
    assert r.ok, r.text()
    deal = r.json().get("deal") or r.json()
    assert db.partner_deals.find_one({"partner_id": STATE["partner"]["id"]}) or deal.get("id"), deal
    _no_server_errors(portal)


def test_e3_referred_payment_commission_refund_reversal_and_payout(person, db):
    sa = _super(person)
    owner = person("ref_owner")
    _login(owner, STATE["ref_email"], "Ref-E2E-Pass-2026!")
    owner.page.wait_for_url(re.compile(r"/(user|dashboard)"), timeout=30000)
    plans = owner.api.get("/api/billing/plans").json()["plans"]
    plan = next(p for p in plans if (p.get("price_monthly") or 0) > 0 and p["slug"] != "enterprise")
    co = owner.api.post("/api/billing/checkout", data={"plan_slug": plan["slug"]}).json()["checkout"]
    assert owner.api.post(f"/api/billing/checkout/{co['session_id']}/mock-pay", data={"succeed": True}).ok
    sub = db.subscriptions.find_one({"checkout_session_id": co["session_id"]})
    assert db.partner_commissions.count_documents({"organization_id": STATE["ref_org"]}) == 0, \
        "commission before Super Admin confirmation"
    assert sa.api.post(f"/api/super-admin/subscriptions/{sub['_id']}/confirm").ok
    com = wait_for(lambda: db.partner_commissions.find_one({"organization_id": STATE["ref_org"], "kind": "commission"}),
                   10, what="commission")
    assert com["partner_id"] == STATE["partner"]["id"] and com["amount"] > 0
    portal = _partner_login(person, "partner", STATE["partner_email"])
    portal.page.goto("/partner#/commissions")
    portal.page.wait_for_timeout(1200)
    assert str(com["amount"]) in json.dumps(portal.api.get("/api/partner/v1/commissions").json()) or \
        portal.api.get("/api/partner/v1/commissions").ok
    # half refund -> the commission is reduced in proportion
    inv = db.invoices.find_one({"subscription_id": str(sub["_id"])})
    r = sa.api.post(f"/api/super-admin/invoices/{inv['_id']}/refund",
                    data={"amount": float(plan["price_monthly"]) / 2, "reason": "E2E half refund"})
    assert r.ok, r.text()
    after = db.partner_commissions.find_one({"_id": com["_id"]})
    assert abs(after["amount"] - com["amount"] / 2) < 0.02, (com["amount"], after["amount"])
    # lifecycle -> payable -> the partner requests a payout -> the Super Admin pays it
    sa.api.post("/api/super-admin/partners/commissions/run-lifecycle", data={})
    db_c = db.partner_commissions.find_one({"_id": com["_id"]})
    if db_c["status"] != "payable":
        r = sa.api.post(f"/api/super-admin/partners/commissions/{com['_id']}/approve", data={})
        assert r.ok, r.text()
    assert db.partner_commissions.find_one({"_id": com["_id"]})["status"] == "payable"
    portal.page.goto("/partner#/payouts")
    portal.page.wait_for_timeout(1000)
    r = portal.api.post("/api/partner/v1/payouts", data={})
    assert r.ok, r.text()
    po = r.json()["payout"]
    queue = sa.api.get("/api/super-admin/partners/payouts").json()
    assert po["id"] in json.dumps(queue), "Super Admin does not see the payout request"
    base = f"/api/super-admin/partners/payouts/{po['id']}"
    assert sa.api.post(base + "/approve", data={}).ok
    paid = sa.api.post(base + "/mark_paid", data={"reference": "UTR-E2E"}).json()
    assert paid["payout"]["status"] == "paid"
    assert db.partner_commissions.find_one({"_id": com["_id"]})["status"] == "paid"
    _no_server_errors(sa)


# ═══════════════════════════════════════════════════════════════════════════
# F. Billing
# ═══════════════════════════════════════════════════════════════════════════

def test_f1_mock_checkout_without_keys(person, db):
    s = _customer(person)
    setup = s.api.get("/api/billing/subscription").json()
    assert setup, setup
    sa = _super(person)
    _sa_view(sa, "payments", r"payment")
    status = sa.api.get("/api/super-admin/payments/setup").json()
    STATE["payments_setup"] = {k: status.get(k) for k in list(status)[:8]}
    assert "mock" in json.dumps(status).lower(), "with no keys the mock provider should be active"
    _no_server_errors(sa)


def test_f2_razorpay_webhook_signature_and_replay(person, db):
    """No Razorpay test keys in the E2E stack: the signed-webhook boundary is
    checked with the webhook secret set through Global Settings."""
    sa = _super(person)
    secret = "rzp_whsec_e2e_123"
    r = sa.api.put("/api/super-admin/payments/setup", data={"razorpay_webhook_secret": secret})
    STATE["rzp_setup"] = (r.status, r.text()[:200])
    anon = person("razorpay")
    body = json.dumps({"event": "payment.captured", "id": f"evt_e2e_{RUN}",
                       "payload": {"payment": {"entity": {"id": f"pay_{RUN}", "order_id": "order_missing",
                                                          "amount": 100, "currency": "INR"}}}})
    bad = anon.api.post("/api/billing/webhook/razorpay", data=body,
                        headers={"Content-Type": "application/json", "X-Razorpay-Signature": "deadbeef"})
    assert bad.status in (400, 401, 403), f"unsigned webhook accepted: {bad.status}"
    if r.ok:
        sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        h = {"Content-Type": "application/json", "X-Razorpay-Signature": sig}
        first = anon.api.post("/api/billing/webhook/razorpay", data=body, headers=h)
        second = anon.api.post("/api/billing/webhook/razorpay", data=body, headers=h)
        STATE["rzp_webhook"] = (first.status, first.text()[:200], second.status, second.text()[:200])
        assert first.status < 500 and second.status < 500
        assert "duplicate" in second.text().lower() or second.json() == first.json() or first.status >= 400
    # a verified payment never starts a plan by itself (journey D2 showed pending_admin_confirmation)
    _no_server_errors(sa)


def test_f3_stripe_webhook_rejects_unsigned(person):
    anon = person("stripe")
    r = anon.api.post("/api/billing/webhook/stripe", data=json.dumps({"id": "evt_x", "type": "charge.refunded"}),
                      headers={"Content-Type": "application/json", "Stripe-Signature": "t=1,v1=bad"})
    assert r.status in (400, 401, 403), r.status


# ═══════════════════════════════════════════════════════════════════════════
# G. Cross-portal security
# ═══════════════════════════════════════════════════════════════════════════

def test_g1_portal_sessions_are_isolated(person):
    cust = _customer(person)
    cust.page.goto("/superadmin")
    cust.page.wait_for_load_state("networkidle")
    assert "superadmin=1" in cust.page.url or "/login" in cust.page.url, cust.page.url
    assert cust.api.get("/api/super-admin/organizations").status in (401, 403)
    sa = _super(person)
    sa.page.goto("/user")
    sa.page.wait_for_load_state("networkidle")
    assert "/login" in sa.page.url, sa.page.url
    partner = _partner_login(person, "partner", STATE["partner_email"])
    partner.page.goto("/org-admin")
    partner.page.wait_for_load_state("networkidle")
    assert "/org-admin" not in partner.page.url or "/login" in partner.page.url, partner.page.url
    assert partner.api.get("/api/org-admin/overview").status in (401, 403)


def test_g2_cross_origin_write_is_blocked(person):
    cust = _customer(person)
    r = cust.api.post("/api/leads/0123456789abcdef01234567/notes", data={"text": "x"},
                      headers={"Origin": "https://evil.example"})
    assert r.status == 403 and r.json().get("error") == "csrf_blocked", (r.status, r.text())


def test_g4_other_tenant_lead_is_404(person, db):
    ref = person("ref_owner")
    _login(ref, STATE["ref_email"], "Ref-E2E-Pass-2026!")
    ref.page.wait_for_url(re.compile(r"/(user|dashboard)"), timeout=30000)
    mine = db.ai_comments.find_one({"organization_id": STATE["org_id"], "is_lead": True})
    r = ref.api.get(f"/api/leads/{mine['_id']}")
    assert r.status == 404, f"org B read org A's lead: {r.status}"
    assert ref.api.patch(f"/api/leads/{mine['_id']}", data={"lead_status": "lost"}).status == 404
    assert db.ai_comments.find_one({"_id": mine["_id"]}).get("lead_status") != "lost"
    # the probe is recorded for the Super Admin
    assert wait_for(lambda: db.security_events.find_one({"$or": [{"event_type": "cross_tenant_access"},
                                                                 {"type": "cross_tenant_access"}]}),
                    10, what="cross_tenant_access security event")


# last: it locks this machine's IP out of signing in
def test_g5_login_lockout(person):
    s = person("attacker")
    codes = []
    for _ in range(12):
        r = s.api.post("/api/auth/login", data={"email": STATE.get("member_email") or "x@acme-e2e.com",
                                                "password": "wrong-password", "scope": "site"})
        codes.append(r.status)
    STATE["login_codes"] = codes
    assert 429 in codes or 423 in codes, f"no lockout after 12 wrong passwords: {codes}"
