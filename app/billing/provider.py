"""
Billing provider abstraction + verified webhook processing.

Providers
  * RazorpayBillingProvider — creates a Razorpay Order over the REST API; the
    customer pays on /billing/razorpay/<order_id> (Razorpay Checkout). The
    handler's signature — hex HMAC-SHA256("<order_id>|<payment_id>", key
    secret) — is checked on the server (verify_razorpay_checkout); webhooks
    carry X-Razorpay-Signature = hex HMAC-SHA256(body, RAZORPAY_WEBHOOK_SECRET).
  * StripeBillingProvider — Stripe Checkout sessions over the REST API;
    webhooks verified with the Stripe-Signature scheme (HMAC-SHA256 over
    "<t>.<payload>").
  * MockBillingProvider — local/dev. Checkout returns a hosted mock payment
    page (/billing/checkout/<session>). Paying there calls the provider's own
    "verification" step, which is equivalent to a verified webhook.

Which provider takes NEW payments (``get_billing_provider``, resolved on every
call — the setting and env reads are cached for a few seconds):
  Super Admin → Global Settings → Payments (setting ``billing.provider``):
  "razorpay" / "stripe" use that provider while its keys are set (otherwise
  "auto" applies and the reason is shown in payments_status()); "auto" = env
  BILLING_PROVIDER when set, else Razorpay when its keys are set, else Stripe
  when its key is set, else the mock provider.
  Keys live only in the server environment (locked env vars, never editable
  in the app). No API returns them: payments_status() gives yes/no and at most
  the last 4 characters of the (public) key id.

Webhooks
  /api/billing/webhook/razorpay and /api/billing/webhook/stripe verify with
  THAT provider whatever is active (renewals / refunds of the other provider
  keep working); /api/billing/webhook verifies with the active provider.

Security rules
  * A checkout NEVER activates a subscription. A verified payment only moves
    it to PENDING_ADMIN_CONFIRMATION; a Super Admin must confirm.
  * Webhooks must carry a valid signature (Stripe-Signature for Stripe,
    X-Razorpay-Signature for Razorpay, X-LeadAI-Signature = hex
    HMAC-SHA256(body, BILLING_WEBHOOK_SECRET) for the generic/mock provider).
    No secret configured -> webhooks rejected.
  * A webhook event is recorded as processed only AFTER its handler
    succeeds; failures return 500 so the provider retries.
  * The same payment reported twice (Razorpay checkout handler + webhook,
    payment.captured + order.paid, re-deliveries) is recorded once.
"""
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx
from bson import ObjectId
from fastapi import HTTPException

from app.billing.subscriptions import (
    ACTIVE,
    PAYMENT_RECEIVED,
    PENDING_ADMIN_CONFIRMATION,
    PENDING_PAYMENT,
    InvalidTransition,
    record_payment_failed,
    record_payment_verified,
)
from app.config import get_settings
from app.db.models import utcnow
from app.db.mongo import get_async_db

logger = logging.getLogger(__name__)
settings = get_settings()

PROVIDERS = ("razorpay", "stripe")
PROVIDER_LABELS = {"razorpay": "Razorpay", "stripe": "Stripe", "mock": "Test payments (mock)"}
WEBHOOK_PATHS = {"razorpay": "/api/billing/webhook/razorpay", "stripe": "/api/billing/webhook/stripe"}
# what to tick when adding the webhook in each provider's dashboard
WEBHOOK_EVENTS = {
    "razorpay": ["payment.captured", "payment.failed", "order.paid", "refund.processed",
                 "payment.dispute.created"],
    "stripe": ["checkout.session.completed", "checkout.session.async_payment_failed",
               "invoice.paid", "invoice.payment_failed", "charge.refunded",
               "charge.dispute.created", "customer.subscription.deleted"],
}
_KEY_ENV = {"razorpay": "RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET", "stripe": "STRIPE_SECRET_KEY"}
_RECEIVED = (PAYMENT_RECEIVED, PENDING_ADMIN_CONFIRMATION, ACTIVE)

# Tests swap in an httpx.MockTransport; None = the real network.
_HTTP_TRANSPORT: httpx.AsyncBaseTransport | None = None


def _http(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, transport=_HTTP_TRANSPORT)


def _env(name: str, default: str = "") -> str:
    try:
        from app.admin.envvars import get_envvar_str
        return get_envvar_str(name, default) or default
    except Exception:
        return default


class WebhookSignatureError(HTTPException):
    def __init__(self, message: str = "Invalid webhook signature"):
        super().__init__(status_code=400, detail=message)


class BillingProvider(ABC):
    name = "abstract"

    @abstractmethod
    async def create_checkout_session(self, *, subscription: dict[str, Any],
                                      plan: dict[str, Any], customer_email: str,
                                      success_url: str, cancel_url: str) -> dict[str, Any]:
        """Start a hosted checkout for a PENDING_PAYMENT subscription."""

    async def create_portal_session(self, organization_id: str,
                                    return_url: str | None = None) -> dict[str, Any]:
        return {"url": return_url or "/dashboard#billing", "provider": self.name}

    @abstractmethod
    def verify_webhook(self, payload: bytes, headers: dict[str, str]) -> dict[str, Any]:
        """Verify the signature and return the parsed event (raise otherwise)."""


# ── Mock provider (local / dev) ─────────────────────────────────────────────

class MockBillingProvider(BillingProvider):
    name = "mock"

    async def create_checkout_session(self, *, subscription, plan, customer_email,
                                      success_url, cancel_url):
        db = get_async_db()
        session_id = f"cs_mock_{secrets.token_hex(12)}"
        await db.subscriptions.update_one({"_id": subscription["_id"]}, {"$set": {
            "provider": self.name, "checkout_session_id": session_id}})
        return {
            "session_id": session_id, "provider": self.name, "status": "open",
            "redirect_url": f"/billing/checkout/{session_id}",
        }

    def verify_webhook(self, payload: bytes, headers: dict[str, str]) -> dict[str, Any]:
        secret = _env("BILLING_WEBHOOK_SECRET")
        sig = headers.get("x-leadai-signature", "")
        if not secret:
            raise WebhookSignatureError("Webhook secret is not configured")
        expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        if not sig or not hmac.compare_digest(expected, sig):
            raise WebhookSignatureError()
        try:
            return json.loads(payload.decode("utf-8"))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON webhook payload")


def _mock_switch_on() -> bool:
    return _env("MOCK_PAYMENTS_ENABLED", "true").strip().lower() not in ("0", "false", "no")


def mock_payments_enabled() -> bool:
    """The mock pay page only works while the mock provider is active and
    MOCK_PAYMENTS_ENABLED is not switched off."""
    return get_billing_provider().name == "mock" and _mock_switch_on()


async def complete_mock_checkout(session_id: str, organization_id: str,
                                 succeed: bool = True) -> dict[str, Any]:
    """Mock provider's server-side payment verification (dev only).

    Equivalent to a verified provider webhook: the subscription can at most
    reach PENDING_ADMIN_CONFIRMATION."""
    if not mock_payments_enabled():
        raise HTTPException(status_code=404, detail="Not found")
    db = get_async_db()
    sub = await db.subscriptions.find_one({"checkout_session_id": session_id,
                                           "organization_id": str(organization_id)})
    if not sub:
        raise HTTPException(status_code=404, detail="Checkout session not found")
    if sub["status"] != PENDING_PAYMENT:
        return {"status": sub["status"]}
    if not succeed:
        await record_payment_failed(str(sub["_id"]), provider="mock", reason="Card declined (mock)")
        return {"status": PENDING_PAYMENT, "payment": "failed"}
    res = await record_payment_verified(
        str(sub["_id"]), provider="mock", provider_payment_id=f"pi_mock_{secrets.token_hex(8)}",
        amount=sub.get("amount"), currency=sub.get("currency"), source="mock_checkout")
    return {"status": res.get("status")}


# ── Stripe provider ─────────────────────────────────────────────────────────

class StripeBillingProvider(BillingProvider):
    name = "stripe"
    API = "https://api.stripe.com/v1"
    TOLERANCE_SEC = 300

    def __init__(self, secret_key: str, webhook_secret: str):
        self.secret_key = secret_key
        self.webhook_secret = webhook_secret

    async def create_checkout_session(self, *, subscription, plan, customer_email,
                                      success_url, cancel_url):
        if not self.secret_key:
            raise HTTPException(status_code=503, detail="Stripe is not configured")
        amount_minor = int(round(float(subscription["amount"]) * 100))
        interval = "year" if subscription.get("billing_cycle") == "yearly" else "month"
        form = {
            "mode": "subscription",
            "customer_email": customer_email,
            "success_url": success_url,
            "cancel_url": cancel_url,
            "client_reference_id": str(subscription["_id"]),
            "metadata[subscription_id]": str(subscription["_id"]),
            "metadata[organization_id]": subscription["organization_id"],
            "subscription_data[metadata][subscription_id]": str(subscription["_id"]),
            "subscription_data[metadata][organization_id]": subscription["organization_id"],
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": (subscription.get("currency") or "usd").lower(),
            "line_items[0][price_data][unit_amount]": str(amount_minor),
            "line_items[0][price_data][recurring][interval]": interval,
            "line_items[0][price_data][product_data][name]": f"LeadAI {plan['name']}",
        }
        try:
            async with _http(20) as client:
                resp = await client.post(f"{self.API}/checkout/sessions", data=form,
                                         auth=(self.secret_key, ""))
        except httpx.HTTPError as exc:
            logger.error("Stripe checkout request failed: %s", type(exc).__name__)
            raise HTTPException(status_code=502, detail="Payment provider error")
        if resp.status_code >= 400:
            logger.error("Stripe checkout failed: %s", resp.text[:300])
            raise HTTPException(status_code=502, detail="Payment provider error")
        data = resp.json()
        db = get_async_db()
        await db.subscriptions.update_one({"_id": subscription["_id"]}, {"$set": {
            "provider": self.name, "checkout_session_id": data["id"]}})
        return {"session_id": data["id"], "provider": self.name, "status": "open",
                "redirect_url": data.get("url")}

    def verify_webhook(self, payload: bytes, headers: dict[str, str]) -> dict[str, Any]:
        if not self.webhook_secret:
            raise WebhookSignatureError("Webhook secret is not configured")
        header = headers.get("stripe-signature", "")
        parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
        ts, sig = parts.get("t"), parts.get("v1")
        if not ts or not sig:
            raise WebhookSignatureError()
        try:
            if abs(time.time() - int(ts)) > self.TOLERANCE_SEC:
                raise WebhookSignatureError("Webhook timestamp outside tolerance")
        except ValueError:
            raise WebhookSignatureError()
        signed = f"{ts}.".encode() + payload
        expected = hmac.new(self.webhook_secret.encode(), signed, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            raise WebhookSignatureError()
        return json.loads(payload.decode("utf-8"))


# ── Razorpay provider ───────────────────────────────────────────────────────

def _entity(payload: dict[str, Any], key: str) -> dict[str, Any]:
    wrapper = payload.get(key)
    entity = wrapper.get("entity") if isinstance(wrapper, dict) else None
    return entity if isinstance(entity, dict) else {}


def normalize_razorpay_event(parsed: dict[str, Any], event_id: str | None = None) -> dict[str, Any]:
    """A verified Razorpay webhook body as the provider-neutral event shape
    ``_handle_webhook_event`` understands (amounts stay in minor units)."""
    event = str(parsed.get("event") or "unknown")
    body = parsed.get("payload") if isinstance(parsed.get("payload"), dict) else {}
    payment, order = _entity(body, "payment"), _entity(body, "order")
    refund, dispute = _entity(body, "refund"), _entity(body, "dispute")
    notes: dict[str, Any] = {}
    for src in (refund, payment, order):      # our order notes win
        if isinstance(src.get("notes"), dict):
            notes.update(src["notes"])
    sub_id = notes.get("subscription_id")
    order_id = payment.get("order_id") or order.get("id")
    payment_id = payment.get("id") or refund.get("payment_id") or dispute.get("payment_id")
    kind = "checkout.session.completed" if event in ("payment.captured", "order.paid") else event
    amount = payment.get("amount") if payment else order.get("amount_paid")
    ref = refund.get("id") or dispute.get("id") or payment_id or order_id or secrets.token_hex(8)
    return {
        # Razorpay sends a unique X-Razorpay-Event-Id; the fallback is deterministic
        # so a re-delivery is still recognised as a duplicate
        "id": event_id or parsed.get("event_id") or f"rzp_{event}_{ref}",
        "type": kind,
        "provider": "razorpay",
        "razorpay_event": event,
        "data": {"object": {
            "id": order_id or payment_id,
            "order_id": order_id,
            "metadata": {"subscription_id": sub_id} if sub_id else {},
            "payment_intent": payment_id,
            "payment_status": "paid" if kind == "checkout.session.completed" else payment.get("status"),
            "amount_total": amount,
            # disputes: the disputed amount (chargeback path reads "amount")
            "amount": dispute.get("amount") if dispute else amount,
            "currency": str(payment.get("currency") or order.get("currency") or "INR").upper(),
            "customer_email": payment.get("email"),
            # refund.processed: the refunded amount of THIS refund (minor units)
            "refund_amount": refund.get("amount") if refund else None,
            "failure_reason": payment.get("error_description") or payment.get("error_reason"),
        }},
    }


class RazorpayBillingProvider(BillingProvider):
    name = "razorpay"
    API = "https://api.razorpay.com/v1"

    def __init__(self, key_id: str | None = None, key_secret: str | None = None,
                 webhook_secret: str | None = None):
        self.key_id = (key_id or _env("RAZORPAY_KEY_ID")).strip()
        self.key_secret = (key_secret or _env("RAZORPAY_KEY_SECRET")).strip()
        self.webhook_secret = (webhook_secret or _env("RAZORPAY_WEBHOOK_SECRET")).strip()

    @property
    def configured(self) -> bool:
        return bool(self.key_id and self.key_secret)

    async def create_checkout_session(self, *, subscription: dict[str, Any],
                                      plan: dict[str, Any], customer_email: str,
                                      success_url: str, cancel_url: str) -> dict[str, Any]:
        """Create a Razorpay Order for the amount the customer owes; the
        customer pays it on /billing/razorpay/<order_id>."""
        if not self.configured:
            raise HTTPException(status_code=503, detail="Razorpay is not configured")
        # the subscription amount (major units, after any coupon) is what the
        # customer owes; Razorpay orders are in minor units (paise). The
        # stored amount stays in major units — webhooks convert back (/100)
        # before record_payment_verified compares them.
        if subscription.get("amount") is not None:
            amount_minor = int(round(float(subscription["amount"]) * 100))
        else:
            amount_minor = int(plan.get("price_cents") or 0)
        if amount_minor <= 0:
            raise HTTPException(status_code=422, detail="Nothing to pay for this plan")
        currency = str(subscription.get("currency") or plan.get("currency") or "INR").upper()
        sub_id = str(subscription["_id"])
        order_req = {
            "amount": amount_minor, "currency": currency, "receipt": sub_id[:40],
            "notes": {"subscription_id": sub_id,
                      "organization_id": str(subscription.get("organization_id") or "")},
        }
        try:
            async with _http(20) as client:
                resp = await client.post(f"{self.API}/orders", json=order_req,
                                         auth=(self.key_id, self.key_secret))
        except httpx.HTTPError as exc:
            logger.error("Razorpay order request failed: %s", type(exc).__name__)
            raise HTTPException(status_code=502, detail="Payment provider error")
        if resp.status_code >= 400:
            logger.error("Razorpay order failed (HTTP %s): %s", resp.status_code, resp.text[:300])
            raise HTTPException(status_code=502, detail="Payment provider error")
        try:
            order_id = str(resp.json()["id"])
        except Exception:
            logger.error("Razorpay order answer without an id: %s", resp.text[:300])
            raise HTTPException(status_code=502, detail="Payment provider error")
        db = get_async_db()
        await db.subscriptions.update_one({"_id": subscription["_id"]}, {"$set": {
            "provider": self.name, "checkout_session_id": order_id, "currency": currency,
            "provider_order_amount": amount_minor}})
        return {
            "session_id": order_id,
            "order_id": order_id,
            "provider": self.name,
            "currency": currency,
            "amount": amount_minor,
            "key_id": self.key_id,   # the public key id Razorpay Checkout needs
            "status": "open",
            "redirect_url": f"/billing/razorpay/{order_id}",
        }

    def verify_webhook(self, payload: bytes, headers: dict[str, str]) -> dict[str, Any]:
        secret = self.webhook_secret
        if not secret:
            raise WebhookSignatureError("Razorpay webhook secret not configured")
        sig = headers.get("x-razorpay-signature", "")
        if not sig:
            raise WebhookSignatureError("Missing X-Razorpay-Signature header")
        expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig.strip()):
            raise WebhookSignatureError("Invalid Razorpay webhook signature")
        try:
            parsed = json.loads(payload.decode("utf-8"))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON webhook payload")
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=400, detail="Invalid JSON webhook payload")
        return normalize_razorpay_event(parsed, headers.get("x-razorpay-event-id"))


def razorpay_checkout_signature_ok(order_id: str, payment_id: str, signature: str,
                                   key_secret: str) -> bool:
    """Razorpay Checkout's handler signature: hex HMAC-SHA256 of
    "<order_id>|<payment_id>" keyed with the key secret."""
    if not (order_id and payment_id and signature and key_secret):
        return False
    expected = hmac.new(key_secret.encode("utf-8"), f"{order_id}|{payment_id}".encode("utf-8"),
                        hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip())


def _order_amount_minor(sub: dict[str, Any]) -> int | None:
    if sub.get("provider_order_amount") is not None:
        return int(sub["provider_order_amount"])
    if sub.get("amount") is not None:
        return int(round(float(sub["amount"]) * 100))
    return None


async def _find_razorpay_checkout(db, order_id: str, organization_id: str) -> dict[str, Any] | None:
    if not order_id:
        return None
    return await db.subscriptions.find_one({"checkout_session_id": str(order_id),
                                            "organization_id": str(organization_id),
                                            "provider": "razorpay"})


async def record_razorpay_payment(sub: dict[str, Any], *, payment_id: str | None,
                                  amount_minor: Any, currency: str | None, source: str,
                                  db) -> str:
    """Record a verified Razorpay payment once. The checkout handler and the
    webhook (payment.captured AND order.paid) all report the same payment:
    the first moves the subscription on, the others find it already moved.
    Returns the subscription status afterwards."""
    status = sub.get("status")
    if status == PENDING_PAYMENT:
        amount = float(amount_minor) / 100.0 if isinstance(amount_minor, (int, float)) else None
        try:
            res = await record_payment_verified(
                str(sub["_id"]), provider="razorpay", provider_payment_id=payment_id,
                amount=amount, currency=(currency or sub.get("currency") or "").upper() or None,
                source=source, db=db)
            return str(res.get("status") or PENDING_ADMIN_CONFIRMATION)
        except InvalidTransition:
            # raced with the other report of the same payment
            fresh = await db.subscriptions.find_one({"_id": sub["_id"]}) or {}
            if fresh.get("status") in _RECEIVED:
                return str(fresh["status"])
            raise
    if status in _RECEIVED:
        return str(status)
    # money arrived for a checkout that is no longer open (superseded by a newer
    # checkout, cancelled or expired): never activate it, tell the Super Admin
    sid = str(sub["_id"])
    seen = await db.payment_events.find_one({"subscription_id": sid,
                                             "event": "payment_for_closed_checkout",
                                             "data.provider_payment_id": payment_id})
    if not seen:
        await db.payment_events.insert_one({
            "organization_id": sub.get("organization_id"), "subscription_id": sid,
            "event": "payment_for_closed_checkout",
            "data": {"provider": "razorpay", "provider_payment_id": payment_id,
                     "amount_minor": amount_minor, "status": status, "source": source},
            "created_at": utcnow()})
        from app.events.notifications import notify_super_admins
        notify_super_admins("payment_received", "Payment for a closed checkout",
                            f"Razorpay payment {payment_id} arrived for subscription {sid}, which is "
                            f"{status}. It did not change any plan — refund it in the Razorpay "
                            "Dashboard if needed.", severity="warning", link="/superadmin#/payments")
    return str(status)


async def razorpay_checkout_config(order_id: str, organization_id: str, email: str,
                                   db=None) -> dict[str, Any]:
    """What the /billing/razorpay/<order_id> page needs to open Razorpay
    Checkout (the key id is public by design; no secret is returned)."""
    if db is None:
        db = get_async_db()
    sub = await db.subscriptions.find_one({"checkout_session_id": str(order_id),
                                           "organization_id": str(organization_id)})
    if not sub:
        raise HTTPException(status_code=404, detail="Checkout session not found")
    status_url = f"/billing/status?session={order_id}"
    if sub.get("provider") != "razorpay" or sub.get("status") != PENDING_PAYMENT:
        # nothing to pay here: the page shows the checkout status instead
        return {"order_id": str(order_id), "provider": sub.get("provider"),
                "status": sub.get("status"), "status_url": status_url}
    prov = RazorpayBillingProvider()
    if not prov.key_id:
        raise HTTPException(status_code=503, detail="Razorpay is not configured")
    from app.admin.settings import get_setting
    app_name = str(get_setting("general.app.name") or "LeadAI").strip()[:60] or "LeadAI"
    color = str(get_setting("branding.colors.primary") or "").strip()
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        color = "#2e9573"
    from app.billing.plans import get_plan_by_slug_or_id
    plan = await get_plan_by_slug_or_id(sub.get("plan_id"), db=db) or {}
    cycle = "yearly" if sub.get("billing_cycle") == "yearly" else "monthly"
    return {
        "order_id": str(order_id), "key_id": prov.key_id,
        "amount": _order_amount_minor(sub), "currency": str(sub.get("currency") or "INR").upper(),
        "name": app_name,
        "description": f"{plan.get('name') or sub.get('plan_id') or 'Plan'} ({cycle})",
        "prefill": {"email": email or ""},
        "notes": {"subscription_id": str(sub["_id"]),
                  "organization_id": str(sub.get("organization_id") or "")},
        "theme_color": color,
        "provider": "razorpay",
        "status": sub.get("status"),
        "status_url": status_url,
    }


async def verify_razorpay_checkout(*, order_id: str, payment_id: str, signature: str,
                                   organization_id: str, db=None) -> dict[str, Any]:
    """Server-side check of Razorpay Checkout's success handler. The order
    must be this organization's Razorpay checkout and the signature must
    match; then the payment is recorded (at most PENDING_ADMIN_CONFIRMATION)."""
    if db is None:
        db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    sub = await _find_razorpay_checkout(db, order_id, organization_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Checkout session not found")
    prov = RazorpayBillingProvider()
    if not prov.configured:
        raise HTTPException(status_code=503, detail="Razorpay is not configured")
    if not razorpay_checkout_signature_ok(order_id, payment_id, signature, prov.key_secret):
        raise WebhookSignatureError("Payment verification failed")
    status = await record_razorpay_payment(
        sub, payment_id=payment_id, amount_minor=_order_amount_minor(sub),
        currency=sub.get("currency"), source="razorpay_checkout", db=db)
    return {"status": status, "session_id": str(order_id)}


# ── Provider selection ─────────────────────────────────────────────────────

def _razorpay_keys() -> tuple[str, str, str]:
    return (_env("RAZORPAY_KEY_ID").strip(), _env("RAZORPAY_KEY_SECRET").strip(),
            _env("RAZORPAY_WEBHOOK_SECRET").strip())


def _stripe_keys() -> tuple[str, str]:
    return (_env("STRIPE_SECRET_KEY", settings.stripe_secret_key or "").strip(),
            _env("STRIPE_WEBHOOK_SECRET", settings.stripe_webhook_secret or "").strip())


def provider_configured(name: str) -> bool:
    if name == "razorpay":
        key_id, key_secret, _ = _razorpay_keys()
        return bool(key_id and key_secret)
    if name == "stripe":
        return bool(_stripe_keys()[0])
    return name == "mock"


def _setting_choice() -> str:
    try:
        from app.admin.settings import get_setting
        value = str(get_setting("billing.provider") or "auto").strip().lower()
    except Exception:
        value = "auto"
    return value if value in ("auto",) + PROVIDERS else "auto"


def _auto_choice() -> tuple[str, str | None]:
    """'auto': env BILLING_PROVIDER when set (and usable), else the first
    provider with keys, else mock. Returns (provider, why the env choice
    was skipped or None)."""
    env_choice = (os.environ.get("BILLING_PROVIDER") or "").strip().lower()
    if env_choice == "mock":
        return "mock", None
    note = None
    if env_choice in PROVIDERS:
        if provider_configured(env_choice):
            return env_choice, None
        note = (f"BILLING_PROVIDER={env_choice} is set but {_KEY_ENV[env_choice]} "
                "is not set in the server environment")
    for name in PROVIDERS:
        if provider_configured(name):
            return name, note
    return "mock", note


_WARNED: set[str] = set()


def _warn_once(message: str) -> None:
    if message not in _WARNED:
        _WARNED.add(message)
        logger.warning("Payments: %s", message)


def resolve_provider() -> dict[str, Any]:
    """{active, choice, fallback_reason}: which provider takes new payments."""
    choice = _setting_choice()
    if choice in PROVIDERS:
        if provider_configured(choice):
            return {"active": choice, "choice": choice, "fallback_reason": None}
        active, _ = _auto_choice()
        reason = (f"{PROVIDER_LABELS[choice]} is selected but {_KEY_ENV[choice]} is not set in "
                  f"the server environment, so {PROVIDER_LABELS[active]} is used.")
        _warn_once(reason)
        return {"active": active, "choice": choice, "fallback_reason": reason}
    active, note = _auto_choice()
    reason = f"{note}, so {PROVIDER_LABELS[active]} is used." if note else None
    if reason:
        _warn_once(reason)
    return {"active": active, "choice": choice, "fallback_reason": reason}


def get_provider_by_name(name: str) -> BillingProvider:
    """A provider built from the current environment, whether or not it is
    the active one (webhooks of every provider keep working)."""
    name = (name or "").strip().lower()
    if name == "razorpay":
        return RazorpayBillingProvider()
    if name == "stripe":
        key, webhook_secret = _stripe_keys()
        return StripeBillingProvider(key, webhook_secret)
    if name == "mock":
        return MockBillingProvider()
    raise HTTPException(status_code=404, detail="Unknown payment provider")


# An explicit override (tests); None = resolve on every call.
_GLOBAL_PROVIDER: BillingProvider | None = None


def get_billing_provider() -> BillingProvider:
    """The provider that takes NEW payments (see the module docstring)."""
    if _GLOBAL_PROVIDER is not None:
        return _GLOBAL_PROVIDER
    return get_provider_by_name(resolve_provider()["active"])


def reset_billing_provider() -> None:
    global _GLOBAL_PROVIDER
    _GLOBAL_PROVIDER = None
    _WARNED.clear()


# ── Setup status + key check (Super Admin) ─────────────────────────────────

def _key_mode(name: str, key: str) -> str | None:
    if name == "razorpay":
        return "test" if key.startswith("rzp_test_") else "live" if key.startswith("rzp_live_") else None
    if name == "stripe":
        if key.startswith(("sk_test_", "rk_test_")):
            return "test"
        return "live" if key.startswith(("sk_live_", "rk_live_")) else None
    return None


def _key_hint(key: str) -> str | None:
    """'rzp_test_…ABCD': the mode prefix and at most the last 4 characters."""
    if not key:
        return None
    prefix = next((p for p in ("rzp_test_", "rzp_live_", "pk_test_", "pk_live_", "sk_test_", "sk_live_") if key.startswith(p)), "")
    if len(key) - len(prefix) < 8:
        return f"{prefix}…"
    return f"{prefix}…{key[-4:]}"


def webhook_url(name: str) -> str:
    return _env("PUBLIC_BASE_URL").strip().rstrip("/") + WEBHOOK_PATHS[name]


def payments_status() -> dict[str, Any]:
    """Payment setup as the Super Admin sees it. No network call and never a
    secret: configured yes/no, test/live mode and a 4-character key hint."""
    sel = resolve_provider()
    rzp_id, rzp_secret, rzp_webhook = _razorpay_keys()
    stripe_key, stripe_webhook = _stripe_keys()
    stripe_pub = _env("STRIPE_PUBLISHABLE_KEY", settings.stripe_publishable_key or "").strip()
    base = _env("PUBLIC_BASE_URL").strip().rstrip("/")
    return {
        "active": sel["active"],
        "active_label": PROVIDER_LABELS.get(sel["active"], sel["active"]),
        "choice": sel["choice"],
        "fallback_reason": sel["fallback_reason"],
        "providers": {
            "razorpay": {
                "label": "Razorpay",
                "configured": bool(rzp_id and rzp_secret),
                "key_id_configured": bool(rzp_id),
                "key_secret_configured": bool(rzp_secret),
                "mode": _key_mode("razorpay", rzp_id),
                "key_id_hint": _key_hint(rzp_id),
                "webhook_secret_configured": bool(rzp_webhook),
                "webhook_url": webhook_url("razorpay"),
                "env_vars": ["RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET"],
            },
            "stripe": {
                "label": "Stripe",
                "configured": bool(stripe_key),
                "key_secret_configured": bool(stripe_key),
                "publishable_key_configured": bool(stripe_pub),
                "publishable_key_hint": _key_hint(stripe_pub),
                "mode": _key_mode("stripe", stripe_key),
                "webhook_secret_configured": bool(stripe_webhook),
                "webhook_url": webhook_url("stripe"),
                "env_vars": ["STRIPE_SECRET_KEY", "STRIPE_PUBLISHABLE_KEY", "STRIPE_WEBHOOK_SECRET"],
            },
        },
        "mock_enabled": sel["active"] == "mock" and _mock_switch_on(),
        "public_base_url": base or None,
        "webhook_events": {k: list(v) for k, v in WEBHOOK_EVENTS.items()},
    }


async def check_provider_keys(name: str) -> dict[str, Any]:
    """Ask the provider whether the keys in the environment work (read-only
    API call, short timeout). {ok, message, mode}; never echoes a key."""
    name = (name or "").strip().lower()
    if name == "razorpay":
        key_id, key_secret, _ = _razorpay_keys()
        if not (key_id and key_secret):
            return {"ok": False, "mode": None, "message": "Not configured: add RAZORPAY_KEY_ID and "
                    "RAZORPAY_KEY_SECRET to the server .env and restart the app."}
        url, auth, params = f"{RazorpayBillingProvider.API}/orders", (key_id, key_secret), {"count": "1"}
        mode = _key_mode("razorpay", key_id)
    elif name == "stripe":
        key, _ = _stripe_keys()
        if not key:
            return {"ok": False, "mode": None, "message": "Not configured: add STRIPE_SECRET_KEY "
                    "to the server .env and restart the app."}
        url, auth, params = f"{StripeBillingProvider.API}/balance", (key, ""), {}
        mode = _key_mode("stripe", key)
    else:
        return {"ok": False, "mode": None, "message": "Unknown payment provider"}
    label = PROVIDER_LABELS[name]
    try:
        async with _http(8) as client:
            resp = await client.get(url, params=params, auth=auth)
    except httpx.TimeoutException:
        return {"ok": False, "mode": mode, "message": f"{label} did not answer in time. Try again."}
    except httpx.HTTPError:
        return {"ok": False, "mode": mode,
                "message": f"Couldn't reach {label} from the server. Check its internet access."}
    if resp.status_code == 200:
        if name == "stripe":
            try:
                live = resp.json().get("livemode")
                if isinstance(live, bool):
                    mode = "live" if live else "test"
            except Exception:
                pass
        return {"ok": True, "mode": mode,
                "message": f"Keys work ({mode} mode)" if mode else "Keys work"}
    if resp.status_code in (401, 403):
        return {"ok": False, "mode": mode,
                "message": f"{label} rejected the key: check {_KEY_ENV[name]}"}
    logger.warning("%s key check: HTTP %s %s", label, resp.status_code, resp.text[:200])
    return {"ok": False, "mode": mode,
            "message": f"{label} answered with an error (HTTP {resp.status_code}). Try again later."}


# ── Webhook processing ─────────────────────────────────────────────────────

async def process_billing_webhook(event_data: dict[str, Any], event_id: str | None = None,
                                  provider_name: str | None = None, db=None) -> dict[str, Any]:
    """Idempotently process a VERIFIED webhook event."""
    if db is None:
        db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    event_id = event_id or event_data.get("id") or event_data.get("event_id")
    if not event_id:
        raise HTTPException(status_code=400, detail="Webhook event id missing")
    event_type = event_data.get("type", "unknown")
    if await db.processed_webhooks.find_one({"provider_event_id": event_id, "status": "processed"}):
        return {"success": True, "status": "duplicate_skipped", "event_id": event_id}
    try:
        await _handle_webhook_event(event_type, event_data, provider_name or "unknown", db)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Webhook processing error for %s", event_type)
        await db.processed_webhooks.update_one(
            {"provider_event_id": event_id},
            {"$set": {"event_type": event_type, "status": "failed", "error": str(exc)[:300],
                      "updated_at": utcnow()}}, upsert=True)
        raise HTTPException(status_code=500, detail="Webhook handler failed")
    await db.processed_webhooks.update_one(
        {"provider_event_id": event_id},
        {"$set": {"event_type": event_type, "status": "processed", "processed_at": utcnow(),
                  "provider": provider_name}},
        upsert=True)
    return {"success": True, "status": "processed", "event_type": event_type}


async def _sub_from_metadata(obj: dict[str, Any], db) -> dict[str, Any] | None:
    meta = obj.get("metadata") or {}
    sub_id = meta.get("subscription_id") or obj.get("client_reference_id")
    if sub_id:
        try:
            return await db.subscriptions.find_one({"_id": ObjectId(str(sub_id))})
        except Exception:
            return None
    if obj.get("subscription"):
        return await db.subscriptions.find_one({"provider_subscription_id": obj["subscription"]})
    return None


async def _razorpay_sub(obj: dict[str, Any], db) -> dict[str, Any] | None:
    """The subscription of a Razorpay event: by its order (our checkout
    reference), else by the subscription_id in the order / payment notes."""
    order_id = obj.get("order_id")
    if order_id:
        sub = await db.subscriptions.find_one({"checkout_session_id": str(order_id),
                                               "provider": "razorpay"})
        if sub:
            return sub
    return await _sub_from_metadata(obj, db)


async def _handle_razorpay_event(event_type: str, obj: dict[str, Any], db) -> bool:
    """Razorpay payment events; False = not handled here (refunds and
    disputes use the shared branches)."""
    if event_type == "checkout.session.completed":       # payment.captured / order.paid
        sub = await _razorpay_sub(obj, db)
        if not sub:
            logger.warning("Razorpay payment for an unknown order %s", obj.get("order_id"))
            return True
        await record_razorpay_payment(sub, payment_id=obj.get("payment_intent"),
                                      amount_minor=obj.get("amount_total"),
                                      currency=obj.get("currency"), source="webhook", db=db)
        return True
    if event_type == "payment.failed":
        sub = await _razorpay_sub(obj, db)
        # a failed attempt only matters while the checkout is still unpaid
        if sub and sub.get("status") == PENDING_PAYMENT:
            await record_payment_failed(str(sub["_id"]), provider="razorpay",
                                        reason=str(obj.get("failure_reason") or "Payment failed"), db=db)
        return True
    return False


async def _handle_webhook_event(event_type: str, event: dict[str, Any], provider: str, db) -> None:
    obj = (event.get("data") or {}).get("object") or {}
    if provider == "razorpay" and await _handle_razorpay_event(event_type, obj, db):
        return
    if event_type == "checkout.session.completed":
        sub = await _sub_from_metadata(obj, db)
        if not sub:
            logger.warning("checkout.session.completed for unknown subscription")
            return
        if obj.get("subscription"):
            await db.subscriptions.update_one({"_id": sub["_id"]}, {"$set": {
                "provider_subscription_id": obj["subscription"],
                "provider_customer_id": obj.get("customer")}})
        if obj.get("payment_status") in ("paid", "no_payment_required"):
            total = obj.get("amount_total")
            await record_payment_verified(
                str(sub["_id"]), provider=provider,
                provider_payment_id=obj.get("payment_intent") or obj.get("id"),
                amount=(total / 100) if isinstance(total, (int, float)) else None,
                currency=(obj.get("currency") or "").upper() or None, source="webhook", db=db)
    elif event_type in ("checkout.session.async_payment_failed", "invoice.payment_failed"):
        sub = await _sub_from_metadata(obj, db)
        if sub:
            await record_payment_failed(str(sub["_id"]), provider=provider,
                                        reason=event_type.replace(".", " "), db=db)
    elif event_type == "invoice.paid":
        sub = await _sub_from_metadata(obj, db)
        if sub:
            await db.payment_events.insert_one({
                "organization_id": sub.get("organization_id"),
                "subscription_id": str(sub["_id"]), "event": "invoice_paid",
                "data": {"invoice": obj.get("id"), "amount_paid": obj.get("amount_paid")},
                "created_at": utcnow()})
            # a renewal (not the first invoice, which checkout already
            # covers) starts the next period and re-grants the plan tokens
            if obj.get("billing_reason") == "subscription_cycle" and \
                    sub.get("status") in ("active", "past_due"):
                import asyncio

                from app.db.mongo import get_sync_db
                from app.lifecycle.maintenance import renew_subscription
                paid = obj.get("amount_paid")
                await asyncio.to_thread(
                    renew_subscription, get_sync_db(), sub, source="stripe_invoice",
                    amount=(paid / 100) if isinstance(paid, (int, float)) else None)
    elif event_type in ("charge.refunded", "refund.processed", "payment.refunded",
                        "charge.dispute.created", "payment.dispute.created"):
        # refunds / chargebacks: invoice + payments updated, partner commission reversed
        from app.billing.refunds import refund_from_provider_event
        await refund_from_provider_event(obj, provider=provider, chargeback="dispute" in event_type, db=db)
    elif event_type == "customer.subscription.deleted":
        sub = await _sub_from_metadata(obj, db)
        if sub:
            await db.payment_events.insert_one({
                "organization_id": sub.get("organization_id"),
                "subscription_id": str(sub["_id"]), "event": "provider_subscription_deleted",
                "data": {}, "created_at": utcnow()})
            from app.events.notifications import notify_super_admins
            notify_super_admins("payment_failed", "Provider cancelled a subscription",
                                f"Subscription {sub['_id']} was cancelled at the provider. "
                                "Review it in Subscriptions.", severity="warning")
    else:
        logger.info("Unhandled webhook event type: %s", event_type)
