"""Razorpay + Stripe payments, the active one chosen by the Super Admin.

Proves:
  * provider resolution: Global Settings choice, 'auto' (env BILLING_PROVIDER,
    then Razorpay, Stripe, mock), an explicit choice without keys falls back;
  * a Razorpay checkout creates a real Order (Orders API mocked with httpx)
    and fails cleanly (502, no secret in the log) when Razorpay errors;
  * POST /api/billing/razorpay/verify: good signature -> at most
    PENDING_ADMIN_CONFIRMATION; bad signature -> 400 + security event;
    another organization's order -> 404 + cross-tenant event; billing
    permission required; the pay page follows the same rules and has its own CSP;
  * the Razorpay webhook finds the subscription by order id or by notes, is
    idempotent with the checkout handler (one payment recorded), records
    failed attempts only while unpaid, and refunds go through the refund path;
  * /api/billing/webhook/{razorpay,stripe} verify with THAT provider whatever
    is active; /api/billing/webhook keeps using the active one;
  * payments_status() / the Super Admin API never return a secret;
    check_provider_keys() turns provider answers into plain messages.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import time
from unittest.mock import patch

import httpx
import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.auth.crypto import hash_password
from app.auth.service import COOKIE_NAME, build_session_value, create_tracked_session

KEY_ID = "rzp_test_AbCdEfGh1234"
KEY_SECRET = "rzpSecretValue-XYZ-987"
WEBHOOK_SECRET = "rzp-webhook-secret-555"
STRIPE_KEY = "sk_test_StripeSecret0000"
STRIPE_WEBHOOK = "whsec_stripe_secret_777"
SECRETS = (KEY_SECRET, WEBHOOK_SECRET, STRIPE_KEY, STRIPE_WEBHOOK)


# ── environment helpers ────────────────────────────────────────────────────

def _clear_caches():
    from app.admin import envvars
    from app.admin.settings import clear_settings_cache
    from app.billing.provider import reset_billing_provider
    envvars._CACHE.clear()
    clear_settings_cache()
    reset_billing_provider()


def _keys(monkeypatch, *, razorpay=True, stripe=False, rzp_webhook=True, stripe_webhook=True):
    from app.config import get_settings
    s = get_settings()
    for name in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET",
                 "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "BILLING_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(s, "stripe_secret_key", STRIPE_KEY if stripe else "")
    monkeypatch.setattr(s, "stripe_webhook_secret", STRIPE_WEBHOOK if stripe and stripe_webhook else "")
    if razorpay:
        monkeypatch.setenv("RAZORPAY_KEY_ID", KEY_ID)
        monkeypatch.setenv("RAZORPAY_KEY_SECRET", KEY_SECRET)
    if rzp_webhook:
        monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", WEBHOOK_SECRET)
    _clear_caches()


def _choose(value):
    from app.admin.settings import set_setting
    set_setting("billing.provider", value)
    _clear_caches()


class FakeRazorpay:
    """The provider APIs behind an httpx.MockTransport."""

    def __init__(self):
        self.requests = []
        self.next_status = 200
        self.counter = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.next_status != 200:
            return httpx.Response(self.next_status, json={"error": {"code": "BAD_REQUEST_ERROR",
                                                                    "description": "Currency not supported"}})
        if request.method == "POST" and request.url.path == "/v1/orders":
            body = json.loads(request.content)
            self.counter += 1
            return httpx.Response(200, json={"id": f"order_Test{self.counter:04d}", "entity": "order",
                                             "amount": body["amount"], "currency": body["currency"],
                                             "receipt": body["receipt"], "notes": body["notes"],
                                             "status": "created"})
        return httpx.Response(200, json={"entity": "collection", "count": 0, "items": []})


@pytest.fixture
def fake_api(monkeypatch):
    from app.billing import provider as prov
    fake = FakeRazorpay()
    monkeypatch.setattr(prov, "_HTTP_TRANSPORT", httpx.MockTransport(fake))
    return fake


@pytest.fixture
def env(monkeypatch, fake_api):
    _keys(monkeypatch)
    with patch("app.admin.settings.is_maintenance_enabled", return_value=False):
        from app.main import app
        with TestClient(app) as client:
            from app.db.mongo import get_sync_db
            yield client, get_sync_db(), fake_api
    _clear_caches()


def _cookie(claims):
    return {COOKIE_NAME: build_session_value(create_tracked_session(claims))}


def _member(db, email, org_id, role):
    uid = str(db.users.insert_one({
        "email": email, "name": email.split("@")[0], "password_hash": hash_password("Str0ngPass!"),
        "status": "active", "default_organization_id": org_id}).inserted_id)
    db.organization_members.insert_one({"organization_id": org_id, "user_id": uid, "role": role,
                                        "status": "active", "permissions_override": {}})
    return _cookie({"user_id": uid, "email": email, "name": email, "scope": "site",
                    "organization_id": org_id, "org_role": role})


def _org(db, name):
    return str(db.organizations.insert_one({
        "name": name, "slug": name.lower().replace(" ", "-") + str(ObjectId())[-4:],
        "status": "demo", "settings": {}, "admin_portal_enabled": False}).inserted_id)


def _super_admin(db):
    from tests.conftest import as_superadmin
    as_superadmin("root@platform.test")
    uid = str(db.users.insert_one({"email": "root@platform.test", "name": "Root", "status": "active",
                                   "is_platform_admin": True, "platform_role": "super_admin"}).inserted_id)
    return _cookie({"user_id": uid, "email": "root@platform.test", "name": "Root",
                    "scope": "admin", "role": "super_admin"})


@pytest.fixture
def world(env):
    client, db, fake = env
    org_a, org_b = _org(db, "Org A"), _org(db, "Org B")
    cookies = {"a_owner": _member(db, "owner@a.test", org_a, "owner"),
               "a_member": _member(db, "member@a.test", org_a, "member"),
               "b_owner": _member(db, "owner@b.test", org_b, "owner")}
    return client, db, fake, {"a": org_a, "b": org_b}, cookies


def _checkout(client, cookie):
    r = client.post("/api/billing/checkout", json={"plan_slug": "pro"}, cookies=cookie)
    assert r.status_code == 200, r.text
    return r.json()["checkout"]


def _sig(order_id, payment_id, secret=KEY_SECRET):
    return hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


def _verify(client, cookie, order_id, payment_id, signature=None):
    return client.post("/api/billing/razorpay/verify", cookies=cookie, json={
        "razorpay_order_id": order_id, "razorpay_payment_id": payment_id,
        "razorpay_signature": signature if signature is not None else _sig(order_id, payment_id)})


def _rzp_hook(client, event, payload, *, event_id=None, path="/api/billing/webhook/razorpay",
              secret=WEBHOOK_SECRET):
    body = json.dumps({"entity": "event", "event": event, "contains": list(payload),
                       "payload": payload, "created_at": int(time.time())}).encode()
    headers = {"Content-Type": "application/json",
               "X-Razorpay-Signature": hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()}
    if event_id:
        headers["X-Razorpay-Event-Id"] = event_id
    return client.post(path, content=body, headers=headers)


def _payment(pay_id, order_id, amount, *, notes=None, status="captured", **extra):
    return {"payment": {"entity": {"id": pay_id, "entity": "payment", "amount": amount, "currency": "USD",
                                   "status": status, "order_id": order_id, "email": "owner@a.test",
                                   "notes": notes if notes is not None else [], **extra}}}


def _sub(db, co):
    return db.subscriptions.find_one({"_id": ObjectId(co["subscription_id"])})


# ═══════════════════════════════════════════════════════════════════════════
# 1. Provider resolution
# ═══════════════════════════════════════════════════════════════════════════

class TestProviderResolution:
    def test_auto_prefers_razorpay_then_stripe_then_mock(self, monkeypatch):
        from app.billing.provider import get_billing_provider, resolve_provider
        _keys(monkeypatch, razorpay=True, stripe=True)
        assert get_billing_provider().name == "razorpay"
        _keys(monkeypatch, razorpay=False, stripe=True)
        assert get_billing_provider().name == "stripe"
        _keys(monkeypatch, razorpay=False, stripe=False)
        assert get_billing_provider().name == "mock"
        assert resolve_provider() == {"active": "mock", "choice": "auto", "fallback_reason": None}

    def test_no_stale_provider_between_calls(self, monkeypatch):
        from app.billing.provider import get_billing_provider
        _keys(monkeypatch, razorpay=False)
        assert get_billing_provider().name == "mock"
        monkeypatch.setenv("RAZORPAY_KEY_ID", KEY_ID)
        monkeypatch.setenv("RAZORPAY_KEY_SECRET", KEY_SECRET)
        from app.admin import envvars
        envvars._CACHE.clear()          # (the env cache lives 5 s)
        assert get_billing_provider().name == "razorpay"

    def test_explicit_choice_and_missing_keys_fall_back(self, monkeypatch, caplog):
        from app.billing.provider import get_billing_provider, resolve_provider
        _keys(monkeypatch, razorpay=True, stripe=True)
        _choose("stripe")
        assert get_billing_provider().name == "stripe"
        _choose("razorpay")
        assert get_billing_provider().name == "razorpay"
        # Stripe chosen but no Stripe key: auto applies (Razorpay), with the reason
        _keys(monkeypatch, razorpay=True, stripe=False)
        _choose("stripe")
        with caplog.at_level("WARNING", logger="app.billing.provider"):
            sel = resolve_provider()
            resolve_provider()
        assert sel["active"] == "razorpay" and sel["choice"] == "stripe"
        assert "STRIPE_SECRET_KEY" in sel["fallback_reason"]
        assert sum("STRIPE_SECRET_KEY" in r.getMessage() for r in caplog.records) == 1  # warned once
        # nothing configured at all: mock
        _keys(monkeypatch, razorpay=False, stripe=False)
        _choose("razorpay")
        assert resolve_provider()["active"] == "mock"

    def test_env_billing_provider_only_applies_to_auto(self, monkeypatch):
        from app.billing.provider import get_billing_provider, resolve_provider
        _keys(monkeypatch, razorpay=True, stripe=True)
        monkeypatch.setenv("BILLING_PROVIDER", "stripe")
        assert get_billing_provider().name == "stripe"
        monkeypatch.setenv("BILLING_PROVIDER", "mock")
        assert get_billing_provider().name == "mock"
        _choose("razorpay")                       # the Super Admin's choice wins
        assert get_billing_provider().name == "razorpay"
        _choose("auto")
        _keys(monkeypatch, razorpay=True, stripe=False)
        monkeypatch.setenv("BILLING_PROVIDER", "stripe")   # no Stripe key: skipped
        sel = resolve_provider()
        assert sel["active"] == "razorpay" and "BILLING_PROVIDER=stripe" in sel["fallback_reason"]

    def test_override_and_reset(self, monkeypatch):
        from app.billing import provider as prov
        _keys(monkeypatch, razorpay=True)
        monkeypatch.setattr(prov, "_GLOBAL_PROVIDER", prov.MockBillingProvider())
        assert prov.get_billing_provider().name == "mock"
        prov.reset_billing_provider()
        assert prov.get_billing_provider().name == "razorpay"

    def test_get_provider_by_name(self, monkeypatch):
        from fastapi import HTTPException

        from app.billing.provider import get_provider_by_name
        _keys(monkeypatch, razorpay=True, stripe=True)
        assert get_provider_by_name("stripe").secret_key == STRIPE_KEY
        assert get_provider_by_name("razorpay").key_id == KEY_ID
        assert get_provider_by_name("mock").name == "mock"
        with pytest.raises(HTTPException):
            get_provider_by_name("paypal")


# ═══════════════════════════════════════════════════════════════════════════
# 2. Razorpay checkout: order creation, pay page, verification
# ═══════════════════════════════════════════════════════════════════════════

class TestRazorpayCheckout:
    def test_checkout_creates_a_real_order(self, world):
        client, db, fake, orgs, cookies = world
        co = _checkout(client, cookies["a_owner"])
        req = fake.requests[-1]
        body = json.loads(req.content)
        sub = _sub(db, co)
        assert str(req.url) == "https://api.razorpay.com/v1/orders"
        assert req.headers["authorization"] == "Basic " + base64.b64encode(
            f"{KEY_ID}:{KEY_SECRET}".encode()).decode()
        assert body["amount"] == round(sub["amount"] * 100) and body["currency"] == "USD"
        assert body["receipt"] == co["subscription_id"] and len(body["receipt"]) <= 40
        assert body["notes"] == {"subscription_id": co["subscription_id"], "organization_id": orgs["a"]}
        assert co["provider"] == "razorpay" and co["order_id"] == "order_Test0001"
        assert co["redirect_url"] == "/billing/razorpay/order_Test0001"
        assert co["key_id"] == KEY_ID and KEY_SECRET not in json.dumps(co)
        assert sub["checkout_session_id"] == "order_Test0001" and sub["status"] == "pending_payment"

    def test_provider_error_is_a_clean_502(self, world, caplog):
        client, db, fake, orgs, cookies = world
        fake.next_status = 400
        with caplog.at_level("ERROR"):
            r = client.post("/api/billing/checkout", json={"plan_slug": "pro"}, cookies=cookies["a_owner"])
        assert r.status_code == 502 and r.json()["detail"] == "Payment provider error"
        assert "Currency not supported" in caplog.text
        assert not any(s in caplog.text for s in SECRETS)

    def test_pay_page_and_options(self, world):
        client, db, fake, orgs, cookies = world
        co = _checkout(client, cookies["a_owner"])
        page = client.get(co["redirect_url"], cookies=cookies["a_owner"])
        assert page.status_code == 200 and "razorpay-checkout.js?v=" in page.text
        csp = page.headers["content-security-policy"]
        assert "script-src 'self' https://checkout.razorpay.com https://cdn.razorpay.com" in csp
        assert "frame-src https://api.razorpay.com https://checkout.razorpay.com" in csp
        assert "https://lumberjack.razorpay.com" in csp
        script_src = [d for d in csp.split("; ") if d.startswith("script-src")][0]
        assert "unsafe-inline" not in script_src
        # every other page keeps the default policy
        assert "checkout.razorpay.com" not in client.get("/login").headers["content-security-policy"]
        r = client.get(f"/api/billing/razorpay/{co['order_id']}", cookies=cookies["a_owner"])
        assert r.status_code == 200, r.text
        opts = r.json()["checkout"]
        assert opts["key_id"] == KEY_ID and opts["order_id"] == co["order_id"]
        assert opts["amount"] == round(co["amount"] * 100) and opts["currency"] == "USD"
        assert opts["prefill"]["email"] == "owner@a.test" and opts["theme_color"].startswith("#")
        assert opts["status"] == "pending_payment"
        assert not any(s in r.text for s in SECRETS)

    def test_pay_page_is_only_for_the_orgs_billing_managers(self, world):
        client, db, fake, orgs, cookies = world
        co = _checkout(client, cookies["a_owner"])
        url = co["redirect_url"]
        assert client.get(url, cookies=cookies["b_owner"]).status_code == 404
        assert db.security_events.find_one({"type": "cross_tenant_access"})
        assert client.get(url, cookies=cookies["a_member"]).status_code == 404
        assert client.get(f"/api/billing/razorpay/{co['order_id']}", cookies=cookies["b_owner"]).status_code == 404
        r = client.get(url, follow_redirects=False)      # signed out -> sign in
        assert r.status_code == 303 and r.headers["location"].startswith("/login")

    def test_verify_good_signature_reaches_pending_confirmation_only(self, world):
        client, db, fake, orgs, cookies = world
        co = _checkout(client, cookies["a_owner"])
        r = _verify(client, cookies["a_owner"], co["order_id"], "pay_Good1")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "pending_admin_confirmation"
        assert r.json()["status_url"] == f"/billing/status?session={co['order_id']}"
        sub = _sub(db, co)
        assert sub["status"] == "pending_admin_confirmation"
        pay = db.payments.find_one({"subscription_id": co["subscription_id"]})
        assert pay["status"] == "succeeded" and pay["provider"] == "razorpay"
        assert pay["provider_payment_id"] == "pay_Good1" and pay["verified_via"] == "razorpay_checkout"
        assert db.organizations.find_one({"_id": ObjectId(orgs["a"])})["status"] == "demo"
        # the status page sees it, without a pay link any more
        st = client.get(f"/api/billing/checkout/{co['order_id']}", cookies=cookies["a_owner"]).json()
        assert st["checkout"]["status"] == "pending_admin_confirmation" and st["checkout"]["pay_url"] is None
        # the pay page's options now only carry the status (the page shows the status page)
        opts = client.get(f"/api/billing/razorpay/{co['order_id']}", cookies=cookies["a_owner"]).json()
        assert opts["checkout"]["status"] == "pending_admin_confirmation" and "key_id" not in opts["checkout"]

    def test_status_page_offers_the_pay_link_while_unpaid(self, world):
        client, db, fake, orgs, cookies = world
        co = _checkout(client, cookies["a_owner"])
        st = client.get(f"/api/billing/checkout/{co['order_id']}", cookies=cookies["a_owner"]).json()
        assert st["checkout"]["pay_url"] == co["redirect_url"]

    def test_verify_bad_signature_rejected(self, world):
        client, db, fake, orgs, cookies = world
        co = _checkout(client, cookies["a_owner"])
        for bad in ("0" * 64, _sig(co["order_id"], "pay_Other"), _sig(co["order_id"], "pay_X", "wrong")):
            r = _verify(client, cookies["a_owner"], co["order_id"], "pay_X", bad)
            assert r.status_code == 400
        assert _sub(db, co)["status"] == "pending_payment"
        assert db.security_events.find_one({"type": "invalid_payment_signature"})

    def test_verify_other_orgs_order_is_not_found(self, world):
        client, db, fake, orgs, cookies = world
        co = _checkout(client, cookies["a_owner"])
        r = _verify(client, cookies["b_owner"], co["order_id"], "pay_Steal")   # valid signature!
        assert r.status_code == 404
        assert _sub(db, co)["status"] == "pending_payment"
        assert db.security_events.find_one({"type": "cross_tenant_access"})

    def test_verify_needs_billing_permission(self, world):
        client, db, fake, orgs, cookies = world
        co = _checkout(client, cookies["a_owner"])
        assert _verify(client, cookies["a_member"], co["order_id"], "pay_M").status_code == 403
        assert _verify(client, {}, co["order_id"], "pay_M").status_code == 401
        assert _sub(db, co)["status"] == "pending_payment"


# ═══════════════════════════════════════════════════════════════════════════
# 3. Razorpay webhooks
# ═══════════════════════════════════════════════════════════════════════════

class TestRazorpayWebhook:
    def test_payment_captured_found_by_order_id(self, world):
        client, db, fake, orgs, cookies = world
        co = _checkout(client, cookies["a_owner"])
        amount = round(co["amount"] * 100)
        r = _rzp_hook(client, "payment.captured", _payment("pay_W1", co["order_id"], amount), event_id="evt_1")
        assert r.status_code == 200, r.text
        assert _sub(db, co)["status"] == "pending_admin_confirmation"
        assert db.payments.find_one({"subscription_id": co["subscription_id"]})["provider_payment_id"] == "pay_W1"

    def test_order_paid_found_by_notes(self, world):
        client, db, fake, orgs, cookies = world
        co = _checkout(client, cookies["a_owner"])
        amount = round(co["amount"] * 100)
        payload = {**_payment("pay_N1", "order_NotOurs", amount),
                   "order": {"entity": {"id": "order_NotOurs", "amount": amount, "amount_paid": amount,
                                        "notes": {"subscription_id": co["subscription_id"]}}}}
        r = _rzp_hook(client, "order.paid", payload, event_id="evt_notes")
        assert r.status_code == 200, r.text
        assert _sub(db, co)["status"] == "pending_admin_confirmation"

    def test_same_payment_from_checkout_and_webhooks_counts_once(self, world):
        client, db, fake, orgs, cookies = world
        co = _checkout(client, cookies["a_owner"])
        amount = round(co["amount"] * 100)
        assert _verify(client, cookies["a_owner"], co["order_id"], "pay_Once").status_code == 200
        captured = _payment("pay_Once", co["order_id"], amount)
        assert _rzp_hook(client, "payment.captured", captured, event_id="evt_a").status_code == 200
        paid = {**captured, "order": {"entity": {"id": co["order_id"], "amount_paid": amount}}}
        assert _rzp_hook(client, "order.paid", paid, event_id="evt_b").status_code == 200
        r = _rzp_hook(client, "payment.captured", captured, event_id="evt_a")      # re-delivery
        assert r.json()["status"] == "duplicate_skipped"
        assert _verify(client, cookies["a_owner"], co["order_id"], "pay_Once").json()["status"] == \
            "pending_admin_confirmation"
        assert db.payment_events.count_documents({"subscription_id": co["subscription_id"],
                                                  "event": "payment_received"}) == 1
        assert db.audit_logs.count_documents({"action": "payment.received"}) == 1

    def test_payment_failed_only_while_unpaid(self, world):
        client, db, fake, orgs, cookies = world
        co = _checkout(client, cookies["a_owner"])
        amount = round(co["amount"] * 100)
        failed = _payment("pay_F1", co["order_id"], amount, status="failed",
                          error_description="Your card was declined.")
        assert _rzp_hook(client, "payment.failed", failed, event_id="evt_f1").status_code == 200
        pay = db.payments.find_one({"subscription_id": co["subscription_id"]})
        assert pay["status"] == "failed" and "declined" in pay["failure_message"]
        # a retry in the same order succeeds: the payment record follows
        assert _verify(client, cookies["a_owner"], co["order_id"], "pay_F2").status_code == 200
        assert db.payments.find_one({"subscription_id": co["subscription_id"]})["status"] == "succeeded"
        failed2 = _payment("pay_F3", co["order_id"], amount, status="failed", error_description="late")
        assert _rzp_hook(client, "payment.failed", failed2, event_id="evt_f3").status_code == 200
        assert db.payments.find_one({"subscription_id": co["subscription_id"]})["status"] == "succeeded"
        assert _sub(db, co)["status"] == "pending_admin_confirmation"

    def test_payment_for_a_superseded_checkout_changes_nothing(self, world):
        client, db, fake, orgs, cookies = world
        old = _checkout(client, cookies["a_owner"])
        _checkout(client, cookies["a_owner"])                  # supersedes the first
        assert _sub(db, old)["status"] == "cancelled"
        cap = _payment("pay_Late", old["order_id"], round(old["amount"] * 100))
        assert _rzp_hook(client, "payment.captured", cap, event_id="evt_late").status_code == 200
        assert _rzp_hook(client, "payment.captured", cap, event_id="evt_late2").status_code == 200
        assert _sub(db, old)["status"] == "cancelled"
        assert db.payment_events.count_documents({"event": "payment_for_closed_checkout"}) == 1
        assert db.notifications.find_one({"title": "Payment for a closed checkout"})

    def test_refund_processed_uses_the_refund_path(self, world):
        client, db, fake, orgs, cookies = world
        co = _checkout(client, cookies["a_owner"])
        assert _verify(client, cookies["a_owner"], co["order_id"], "pay_R1").status_code == 200
        r = client.post(f"/api/super-admin/subscriptions/{co['subscription_id']}/confirm",
                        cookies=_super_admin(db))
        assert r.status_code == 200, r.text
        refund = {"refund": {"entity": {"id": "rfnd_1", "amount": 100, "payment_id": "pay_R1",
                                        "currency": "USD", "notes": []}},
                  **_payment("pay_R1", co["order_id"], round(co["amount"] * 100), status="refunded")}
        assert _rzp_hook(client, "refund.processed", refund, event_id="evt_r1").status_code == 200
        inv = db.invoices.find_one({"subscription_id": co["subscription_id"]})
        assert inv["status"] == "partially_refunded" and inv["refunded_amount"] == 1.0

    def test_signature_required(self, world):
        client, db, fake, orgs, cookies = world
        co = _checkout(client, cookies["a_owner"])
        cap = _payment("pay_S", co["order_id"], round(co["amount"] * 100))
        assert _rzp_hook(client, "payment.captured", cap, secret="not-the-secret").status_code == 400
        body = json.dumps({"event": "payment.captured", "payload": cap}).encode()
        assert client.post("/api/billing/webhook/razorpay", content=body).status_code == 400
        assert _sub(db, co)["status"] == "pending_payment"
        assert db.security_events.find_one({"type": "invalid_webhook_signature"})


# ═══════════════════════════════════════════════════════════════════════════
# 4. Per-provider webhook URLs
# ═══════════════════════════════════════════════════════════════════════════

def _stripe_hook(client, event, path="/api/billing/webhook/stripe", secret=STRIPE_WEBHOOK):
    body = json.dumps(event).encode()
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return client.post(path, content=body, headers={"Stripe-Signature": f"t={ts},v1={sig}"})


def test_each_provider_webhook_works_whatever_is_active(env, monkeypatch):
    client, db, fake = env
    org = _org(db, "Org W")
    owner = _member(db, "owner@w.test", org, "owner")
    co = _checkout(client, owner)                       # a Razorpay order...
    _keys(monkeypatch, razorpay=True, stripe=True)
    _choose("stripe")                                   # ...then the Super Admin switches to Stripe
    from app.billing.provider import get_billing_provider
    assert get_billing_provider().name == "stripe"
    cap = _payment("pay_X1", co["order_id"], round(co["amount"] * 100))
    # the legacy URL verifies with the ACTIVE provider (Stripe): a Razorpay call fails
    assert _rzp_hook(client, "payment.captured", cap, path="/api/billing/webhook").status_code == 400
    # the Razorpay URL still works
    assert _rzp_hook(client, "payment.captured", cap, event_id="evt_x1").status_code == 200
    assert _sub(db, co)["status"] == "pending_admin_confirmation"
    # Stripe URL: Stripe signatures only
    ev = {"id": "evt_s1", "type": "customer.created", "data": {"object": {}}}
    assert _stripe_hook(client, ev).status_code == 200
    assert _stripe_hook(client, ev, secret="whsec_wrong").status_code == 400
    assert _stripe_hook(client, {**ev, "id": "evt_s2"}, path="/api/billing/webhook").status_code == 200
    assert client.post("/api/billing/webhook/paypal", content=b"{}").status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# 5. Setup status, key check, Super Admin API
# ═══════════════════════════════════════════════════════════════════════════

class TestSetup:
    def test_payments_status_never_leaks_secrets(self, monkeypatch):
        from app.billing.provider import payments_status
        _keys(monkeypatch, razorpay=True, stripe=True)
        monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example.com/")
        from app.admin import envvars
        envvars._CACHE.clear()
        st = payments_status()
        dumped = json.dumps(st)
        assert not any(s in dumped for s in SECRETS) and KEY_ID not in dumped
        rzp, stripe = st["providers"]["razorpay"], st["providers"]["stripe"]
        assert st["active"] == "razorpay" and st["choice"] == "auto" and st["fallback_reason"] is None
        assert rzp["configured"] and rzp["mode"] == "test" and rzp["key_id_hint"] == "rzp_test_…1234"
        assert rzp["webhook_secret_configured"] and stripe["configured"] and stripe["mode"] == "test"
        assert rzp["webhook_url"] == "https://app.example.com/api/billing/webhook/razorpay"
        assert stripe["webhook_url"] == "https://app.example.com/api/billing/webhook/stripe"
        assert "payment.captured" in st["webhook_events"]["razorpay"]
        assert "checkout.session.completed" in st["webhook_events"]["stripe"]
        assert st["mock_enabled"] is False and st["public_base_url"] == "https://app.example.com"
        _keys(monkeypatch, razorpay=False, stripe=False, rzp_webhook=False)
        monkeypatch.delenv("PUBLIC_BASE_URL")
        envvars._CACHE.clear()
        st = payments_status()
        assert st["active"] == "mock" and st["mock_enabled"] is True
        assert st["providers"]["razorpay"]["key_id_hint"] is None
        assert st["providers"]["razorpay"]["webhook_url"] == "/api/billing/webhook/razorpay"

    def test_check_provider_keys(self, monkeypatch):
        from app.billing import provider as prov
        answers = {}

        def api(request):
            status, body = answers[request.url.host]
            if status == "timeout":
                raise httpx.ConnectTimeout("slow", request=request)
            return httpx.Response(status, json=body)
        monkeypatch.setattr(prov, "_HTTP_TRANSPORT", httpx.MockTransport(api))
        _keys(monkeypatch, razorpay=True, stripe=True)
        answers["api.razorpay.com"] = (200, {"items": []})
        answers["api.stripe.com"] = (200, {"object": "balance", "livemode": False})
        assert asyncio.run(prov.check_provider_keys("razorpay")) == \
            {"ok": True, "mode": "test", "message": "Keys work (test mode)"}
        assert asyncio.run(prov.check_provider_keys("stripe"))["message"] == "Keys work (test mode)"
        answers["api.razorpay.com"] = (401, {"error": {"code": "BAD_REQUEST_ERROR"}})
        res = asyncio.run(prov.check_provider_keys("razorpay"))
        assert res["ok"] is False
        assert res["message"] == "Razorpay rejected the key: check RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET"
        answers["api.stripe.com"] = ("timeout", None)
        res = asyncio.run(prov.check_provider_keys("stripe"))
        assert res["ok"] is False and "in time" in res["message"]
        answers["api.razorpay.com"] = (500, {})
        assert "HTTP 500" in asyncio.run(prov.check_provider_keys("razorpay"))["message"]
        _keys(monkeypatch, razorpay=False, stripe=False)
        res = asyncio.run(prov.check_provider_keys("razorpay"))
        assert res["ok"] is False and res["message"].startswith("Not configured") and res["mode"] is None
        assert asyncio.run(prov.check_provider_keys("paypal"))["ok"] is False

    def test_super_admin_api(self, env):
        client, db, fake = env
        sa = _super_admin(db)
        r = client.get("/api/super-admin/payments/setup", cookies=sa)
        assert r.status_code == 200, r.text
        assert r.json()["payments"]["active"] == "razorpay"
        assert not any(s in r.text for s in SECRETS)
        r = client.post("/api/super-admin/payments/check", json={"provider": "razorpay"}, cookies=sa)
        assert r.status_code == 200 and r.json()["ok"] is True and r.json()["mode"] == "test"
        assert fake.requests[-1].url.path == "/v1/orders" and fake.requests[-1].method == "GET"
        assert db.audit_logs.find_one({"action": "payments.keys_checked"})
        assert client.post("/api/super-admin/payments/check", json={"provider": "x"},
                           cookies=sa).status_code == 422
        # not for organization owners
        org = _org(db, "Org S")
        owner = _member(db, "owner@s.test", org, "owner")
        assert client.get("/api/super-admin/payments/setup", cookies=owner).status_code in (401, 403)
        integ = client.get("/api/super-admin/integrations", cookies=sa).json()["integrations"]["payments"]
        assert integ["provider"] == "razorpay" and integ["razorpay_key_configured"] is True
        assert integ["razorpay_webhook_secret_configured"] is True
        assert integ["setup_link"] == "/superadmin#/settings?tab=payments"

    def test_super_admin_update_payments_setup(self, env):
        client, db, fake = env
        sa = _super_admin(db)
        try:
            # Update Razorpay credentials via PUT
            payload = {
                "razorpay_key_id": "rzp_test_newcustomkey",
                "razorpay_key_secret": "SuperSecretRazorpayKey999",
                "razorpay_webhook_secret": "NewWhSec123",
                "billing_provider": "razorpay",
            }
            r = client.put("/api/super-admin/payments/setup", json=payload, cookies=sa)
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["success"] is True
            pm = data["payments"]
            assert pm["active"] == "razorpay"
            assert pm["choice"] == "razorpay"
            rzp_prov = pm["providers"]["razorpay"]
            assert rzp_prov["key_id_configured"] is True
            assert rzp_prov["key_id_hint"] == "rzp_test_…mkey"
            assert rzp_prov["key_secret_configured"] is True
            assert rzp_prov["webhook_secret_configured"] is True
            # Secret is never in response body
            assert "SuperSecretRazorpayKey999" not in r.text

            # Verify sealed at rest in env_overrides collection
            from app.services import secret_box
            doc = db.env_overrides.find_one({"_id": "RAZORPAY_KEY_SECRET"})
            assert doc is not None
            assert doc["value"] != "SuperSecretRazorpayKey999"  # encrypted!
            assert secret_box.is_encrypted(doc["value"]) is True

            # Test empty secret preserves existing secret
            r2 = client.put("/api/super-admin/payments/setup", json={
                "razorpay_key_id": "rzp_test_updatedid",
                "razorpay_key_secret": "",  # keep existing
            }, cookies=sa)
            assert r2.status_code == 200
            rzp_prov2 = r2.json()["payments"]["providers"]["razorpay"]
            assert rzp_prov2["key_id_configured"] is True
            assert rzp_prov2["key_id_hint"] == "rzp_test_…edid"
            assert rzp_prov2["key_secret_configured"] is True

            # Test updating Stripe credentials
            r3 = client.put("/api/super-admin/payments/setup", json={
                "stripe_secret_key": "sk_test_superstripekey",
                "stripe_publishable_key": "pk_test_stripepubkey",
                "stripe_webhook_secret": "whsec_stripesecret",
                "billing_provider": "stripe",
            }, cookies=sa)
            assert r3.status_code == 200
            st_prov = r3.json()["payments"]["providers"]["stripe"]
            assert st_prov["publishable_key_configured"] is True
            assert st_prov["publishable_key_hint"] == "pk_test_…bkey"
            assert st_prov["key_secret_configured"] is True
            assert st_prov["webhook_secret_configured"] is True
            assert r3.json()["payments"]["active"] == "stripe"
            assert "sk_test_superstripekey" not in r3.text
        finally:
            for k in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET",
                      "STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY", "STRIPE_WEBHOOK_SECRET"):
                db.env_overrides.delete_one({"_id": k})
            _choose("auto")
            from app.admin.envvars import _CACHE
            _CACHE.clear()

