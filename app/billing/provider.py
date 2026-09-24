"""
Billing provider abstraction + verified webhook processing.

Providers
  * StripeBillingProvider — used when STRIPE_SECRET_KEY is configured.
    Creates Stripe Checkout sessions over the REST API and verifies webhooks
    with the Stripe-Signature scheme (HMAC-SHA256 over "<t>.<payload>").
  * MockBillingProvider — local/dev. Checkout returns a hosted mock payment
    page (/billing/checkout/<session>). Paying there calls the provider's own
    "verification" step, which is equivalent to a verified webhook.

Security rules
  * A checkout NEVER activates a subscription. A verified payment only moves
    it to PENDING_ADMIN_CONFIRMATION; a Super Admin must confirm.
  * Webhooks must carry a valid signature (Stripe-Signature for Stripe,
    X-LeadAI-Signature = hex HMAC-SHA256(body, BILLING_WEBHOOK_SECRET) for
    the generic/mock provider). No secret configured -> webhooks rejected.
  * A webhook event is recorded as processed only AFTER its handler
    succeeds; failures return 500 so the provider retries.
"""
import hashlib
import hmac
import json
import logging
import secrets
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from bson import ObjectId
from fastapi import HTTPException

from app.billing.subscriptions import (
    PENDING_PAYMENT,
    record_payment_failed,
    record_payment_verified,
)
from app.config import get_settings
from app.db.models import utcnow
from app.db.mongo import get_async_db

logger = logging.getLogger(__name__)
settings = get_settings()


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
    async def create_checkout_session(self, *, subscription: Dict[str, Any],
                                      plan: Dict[str, Any], customer_email: str,
                                      success_url: str, cancel_url: str) -> Dict[str, Any]:
        """Start a hosted checkout for a PENDING_PAYMENT subscription."""

    async def create_portal_session(self, organization_id: str,
                                    return_url: Optional[str] = None) -> Dict[str, Any]:
        return {"url": return_url or "/dashboard#billing", "provider": self.name}

    @abstractmethod
    def verify_webhook(self, payload: bytes, headers: Dict[str, str]) -> Dict[str, Any]:
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

    def verify_webhook(self, payload: bytes, headers: Dict[str, str]) -> Dict[str, Any]:
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


def mock_payments_enabled() -> bool:
    """The mock pay page only works while the mock provider is active and
    MOCK_PAYMENTS_ENABLED is not switched off."""
    return (get_billing_provider().name == "mock"
            and _env("MOCK_PAYMENTS_ENABLED", "true").lower() not in ("0", "false", "no"))


async def complete_mock_checkout(session_id: str, organization_id: str,
                                 succeed: bool = True) -> Dict[str, Any]:
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
        import httpx
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
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(f"{self.API}/checkout/sessions", data=form,
                                     auth=(self.secret_key, ""))
        if resp.status_code >= 400:
            logger.error("Stripe checkout failed: %s", resp.text[:300])
            raise HTTPException(status_code=502, detail="Payment provider error")
        data = resp.json()
        db = get_async_db()
        await db.subscriptions.update_one({"_id": subscription["_id"]}, {"$set": {
            "provider": self.name, "checkout_session_id": data["id"]}})
        return {"session_id": data["id"], "provider": self.name, "status": "open",
                "redirect_url": data.get("url")}

    def verify_webhook(self, payload: bytes, headers: Dict[str, str]) -> Dict[str, Any]:
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


_GLOBAL_PROVIDER: Optional[BillingProvider] = None


def get_billing_provider() -> BillingProvider:
    global _GLOBAL_PROVIDER
    if _GLOBAL_PROVIDER is None:
        key = _env("STRIPE_SECRET_KEY", settings.stripe_secret_key or "")
        if key.strip():
            _GLOBAL_PROVIDER = StripeBillingProvider(
                key.strip(), _env("STRIPE_WEBHOOK_SECRET", settings.stripe_webhook_secret or ""))
        else:
            _GLOBAL_PROVIDER = MockBillingProvider()
    return _GLOBAL_PROVIDER


def reset_billing_provider() -> None:
    global _GLOBAL_PROVIDER
    _GLOBAL_PROVIDER = None


# ── Webhook processing ─────────────────────────────────────────────────────

async def process_billing_webhook(event_data: Dict[str, Any], event_id: Optional[str] = None,
                                  provider_name: Optional[str] = None, db=None) -> Dict[str, Any]:
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
        {"$set": {"event_type": event_type, "status": "processed", "processed_at": utcnow()}},
        upsert=True)
    return {"success": True, "status": "processed", "event_type": event_type}


async def _sub_from_metadata(obj: Dict[str, Any], db) -> Optional[Dict[str, Any]]:
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


async def _handle_webhook_event(event_type: str, event: Dict[str, Any], provider: str, db) -> None:
    obj = (event.get("data") or {}).get("object") or {}
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
