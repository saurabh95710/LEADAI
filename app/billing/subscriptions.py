"""
SaaS Subscription Lifecycle — a guarded state machine.

    PENDING_PAYMENT ──(verified payment: webhook / provider verification)──►
    PAYMENT_RECEIVED ──► PENDING_ADMIN_CONFIRMATION ──(Super Admin confirm)──►
    ACTIVE ──► SUSPENDED / EXPIRED / CANCELLED

Rules:
  * ``start_checkout`` only ever creates a PENDING_PAYMENT subscription.
  * ``record_payment_verified`` is the ONLY way out of PENDING_PAYMENT and it
    can only move the subscription to PENDING_ADMIN_CONFIRMATION. It is
    called from signature-verified webhooks / provider verification — never
    from a browser "payment success" page.
  * ``confirm_subscription`` (Super Admin only) is the ONLY way to ACTIVE; it
    activates the organization, enables the Admin portal, converts the demo
    and allocates the plan's tokens.
Every transition is validated against ``ALLOWED_TRANSITIONS``, audited and
notified. Legacy statuses (trialing, past_due, paused, incomplete) are still
readable for old documents.
"""
from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Dict, Optional
from bson import ObjectId
from fastapi import HTTPException

from app.billing.plans import get_plan_by_slug_or_id
from app.db.models import utcnow
from app.db.mongo import get_async_db

logger = logging.getLogger(__name__)

PENDING_PAYMENT = "pending_payment"
PAYMENT_RECEIVED = "payment_received"
PENDING_ADMIN_CONFIRMATION = "pending_admin_confirmation"
ACTIVE = "active"
SUSPENDED = "suspended"
EXPIRED = "expired"
CANCELLED = "cancelled"

SUBSCRIPTION_STATUSES = (PENDING_PAYMENT, PAYMENT_RECEIVED, PENDING_ADMIN_CONFIRMATION,
                         ACTIVE, SUSPENDED, EXPIRED, CANCELLED)
ALLOWED_TRANSITIONS = {
    PENDING_PAYMENT: {PAYMENT_RECEIVED, CANCELLED, EXPIRED},
    PAYMENT_RECEIVED: {PENDING_ADMIN_CONFIRMATION, CANCELLED},
    PENDING_ADMIN_CONFIRMATION: {ACTIVE, CANCELLED},
    ACTIVE: {SUSPENDED, EXPIRED, CANCELLED},
    SUSPENDED: {ACTIVE, CANCELLED, EXPIRED},
    EXPIRED: {CANCELLED},
    CANCELLED: set(),
    # legacy
    "trialing": {EXPIRED, CANCELLED, SUSPENDED},
    "past_due": {SUSPENDED, EXPIRED, CANCELLED},
    "paused": {SUSPENDED, CANCELLED},
    "incomplete": {CANCELLED, EXPIRED},
}
OPEN_STATUSES = (PENDING_PAYMENT, PAYMENT_RECEIVED, PENDING_ADMIN_CONFIRMATION)


class InvalidTransition(HTTPException):
    def __init__(self, current: str, target: str):
        super().__init__(status_code=409, detail={
            "code": "INVALID_SUBSCRIPTION_TRANSITION",
            "message": f"Subscription cannot move from '{current}' to '{target}'.",
            "current": current, "target": target})


async def _transition(db, sub: Dict[str, Any], target: str, *, actor: str,
                      extra: Optional[Dict[str, Any]] = None, note: str = "") -> Dict[str, Any]:
    """Atomically move ``sub`` to ``target`` if the state machine allows it."""
    current = sub.get("status")
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise InvalidTransition(current, target)
    now = utcnow()
    res = await db.subscriptions.update_one(
        {"_id": sub["_id"], "status": current},
        {"$set": {"status": target, "updated_at": now, **(extra or {})},
         "$push": {"status_history": {"from": current, "to": target, "at": now,
                                      "by": actor, "note": note}}})
    if res.modified_count == 0:
        raise InvalidTransition(current, target)  # raced with another transition
    from app.admin.audit import aaudit
    await aaudit(f"subscription.{target}", "billing", user=actor,
                 organization_id=sub.get("organization_id"),
                 resource_type="subscription", resource_id=str(sub["_id"]),
                 details={"from": current, "to": target, "plan_id": sub.get("plan_id"),
                          "note": note})
    return await db.subscriptions.find_one({"_id": sub["_id"]})


async def _payment_event(db, sub: Dict[str, Any], event: str, **data) -> None:
    await db.payment_events.insert_one({
        "organization_id": sub.get("organization_id"), "subscription_id": str(sub["_id"]),
        "event": event, "data": data, "created_at": utcnow()})


async def start_checkout(organization_id: str, plan_slug: str, billing_cycle: str,
                         *, actor: Dict[str, Any], db=None) -> Dict[str, Any]:
    """Customer picked a plan: create a PENDING_PAYMENT subscription + pending
    payment. Never activates anything."""
    if db is None:
        db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    if billing_cycle not in ("monthly", "yearly"):
        raise HTTPException(status_code=422, detail="billing_cycle must be monthly or yearly")
    plan = await get_plan_by_slug_or_id(plan_slug, db=db)
    if not plan or plan.get("status") != "active" or not plan.get("is_public", True):
        raise HTTPException(status_code=404, detail="Requested plan not found")
    price = float(plan.get("price_yearly" if billing_cycle == "yearly" else "price_monthly") or 0)
    if price <= 0:
        raise HTTPException(status_code=422, detail="This plan cannot be purchased")
    s_org_id = str(organization_id)
    now = utcnow()
    # supersede older unpaid checkouts
    await db.subscriptions.update_many(
        {"organization_id": s_org_id, "status": PENDING_PAYMENT},
        {"$set": {"status": CANCELLED, "updated_at": now, "cancel_reason": "superseded"}})
    sub = {
        "organization_id": s_org_id, "plan_id": plan["slug"], "status": PENDING_PAYMENT,
        "provider": None, "provider_customer_id": None, "provider_subscription_id": None,
        "checkout_session_id": None,
        "billing_cycle": billing_cycle, "currency": plan.get("currency"), "amount": price,
        "requested_by": actor.get("user_id"), "requested_by_email": actor.get("email"),
        "started_at": None, "current_period_start": None, "current_period_end": None,
        "cancel_at_period_end": False, "cancelled_at": None,
        "status_history": [{"from": None, "to": PENDING_PAYMENT, "at": now,
                            "by": actor.get("email")}],
        "created_at": now, "updated_at": now,
    }
    sub["_id"] = (await db.subscriptions.insert_one(sub)).inserted_id
    pay = {
        "organization_id": s_org_id, "subscription_id": str(sub["_id"]),
        "amount": price, "currency": plan.get("currency"), "status": "pending",
        "provider": None, "provider_payment_id": None, "created_at": now, "updated_at": now,
    }
    pay["_id"] = (await db.payments.insert_one(pay)).inserted_id
    await _payment_event(db, sub, "checkout_started", plan=plan["slug"], cycle=billing_cycle,
                         amount=price)
    from app.admin.audit import aaudit
    await aaudit("plan.selected", "billing", user=actor, organization_id=s_org_id,
                 resource_type="subscription", resource_id=str(sub["_id"]),
                 details={"plan": plan["slug"], "cycle": billing_cycle, "amount": price})
    return {"subscription": sub, "payment": pay, "plan": plan}


async def record_payment_verified(subscription_id: str, *, provider: str,
                                  provider_payment_id: Optional[str], amount: float,
                                  currency: Optional[str], source: str,
                                  db=None) -> Dict[str, Any]:
    """A VERIFIED payment (signed webhook / provider API check) arrived.
    Moves PENDING_PAYMENT -> PAYMENT_RECEIVED -> PENDING_ADMIN_CONFIRMATION."""
    if db is None:
        db = get_async_db()
    sub = await db.subscriptions.find_one({"_id": ObjectId(str(subscription_id))})
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    if sub["status"] in (PAYMENT_RECEIVED, PENDING_ADMIN_CONFIRMATION, ACTIVE):
        return _clean_sub(sub)  # idempotent re-delivery
    expected = float(sub.get("amount") or 0)
    if amount is not None and round(float(amount), 2) + 0.01 < round(expected, 2):
        await _payment_event(db, sub, "amount_mismatch", paid=amount, expected=expected)
        raise HTTPException(status_code=400, detail="Paid amount does not match the plan price")
    actor = f"provider:{provider}"
    sub = await _transition(db, sub, PAYMENT_RECEIVED, actor=actor, extra={
        "provider": provider, "payment_received_at": utcnow()}, note=source)
    now = utcnow()
    await db.payments.update_one(
        {"subscription_id": str(sub["_id"]), "status": "pending"},
        {"$set": {"status": "succeeded", "provider": provider,
                  "provider_payment_id": provider_payment_id, "verified_via": source,
                  "amount_paid": amount, "updated_at": now}})
    await _payment_event(db, sub, "payment_received", provider=provider,
                         provider_payment_id=provider_payment_id, amount=amount, source=source)
    sub = await _transition(db, sub, PENDING_ADMIN_CONFIRMATION, actor=actor,
                            note="awaiting super admin confirmation")
    from app.admin.audit import aaudit
    await aaudit("payment.received", "billing", user=actor,
                 organization_id=sub.get("organization_id"), resource_type="payment",
                 resource_id=provider_payment_id,
                 details={"amount": amount, "currency": currency, "source": source})
    from app.events.notifications import notify_org_admins, notify_super_admins
    org_id = sub.get("organization_id")
    notify_super_admins("payment_received", "Payment received",
                        f"{currency or ''} {amount} for plan {sub.get('plan_id')} (org {org_id})",
                        severity="success", link="/superadmin#subscriptions",
                        data={"subscription_id": str(sub["_id"])})
    notify_super_admins("subscription_awaiting_approval", "Subscription awaiting confirmation",
                        f"Plan {sub.get('plan_id')} for org {org_id}", severity="warning",
                        link="/superadmin#subscriptions",
                        data={"subscription_id": str(sub["_id"])})
    notify_org_admins(org_id, "payment_status", "Payment received",
                      "Your payment was received and is awaiting confirmation by our team.",
                      severity="info", link="/billing/status")
    return _clean_sub(sub)


async def record_payment_failed(subscription_id: str, *, provider: str, reason: str,
                                db=None) -> None:
    if db is None:
        db = get_async_db()
    sub = await db.subscriptions.find_one({"_id": ObjectId(str(subscription_id))})
    if not sub:
        return
    await db.payments.update_one(
        {"subscription_id": str(sub["_id"]), "status": "pending"},
        {"$set": {"status": "failed", "failure_message": reason[:300], "provider": provider,
                  "updated_at": utcnow()}})
    await _payment_event(db, sub, "payment_failed", provider=provider, reason=reason)
    from app.admin.audit import aaudit
    await aaudit("payment.failed", "billing", user=f"provider:{provider}", success=False,
                 organization_id=sub.get("organization_id"), resource_type="subscription",
                 resource_id=str(sub["_id"]), details={"reason": reason})
    from app.events.notifications import notify_org_admins, notify_super_admins
    notify_super_admins("payment_failed", "Payment failed",
                        f"Org {sub.get('organization_id')}: {reason}", severity="danger",
                        link="/superadmin#payments")
    notify_org_admins(sub.get("organization_id"), "payment_status", "Payment failed",
                      reason, severity="danger", link="/billing/status")


async def confirm_subscription(subscription_id: str, *, actor: Dict[str, Any],
                               db=None) -> Dict[str, Any]:
    """Super Admin confirmation — the ONLY path to ACTIVE."""
    if db is None:
        db = get_async_db()
    try:
        sub = await db.subscriptions.find_one({"_id": ObjectId(str(subscription_id))})
    except Exception:
        sub = None
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    plan = await get_plan_by_slug_or_id(sub.get("plan_id"), db=db)
    if not plan:
        raise HTTPException(status_code=409, detail="The subscription's plan no longer exists")
    now = utcnow()
    cycle_days = 365 if sub.get("billing_cycle") == "yearly" else 30
    period_end = now + timedelta(days=cycle_days)
    actor_email = actor.get("email", "super_admin")
    sub = await _transition(db, sub, ACTIVE, actor=actor_email, extra={
        "started_at": now, "current_period_start": now, "current_period_end": period_end,
        "confirmed_at": now, "confirmed_by": actor_email})
    s_org_id = sub["organization_id"]
    sub_id = str(sub["_id"])
    # Supersede any previously active subscription of this organization
    async for old in db.subscriptions.find({"organization_id": s_org_id, "_id": {"$ne": sub["_id"]},
                                            "status": {"$in": [ACTIVE, "trialing", "past_due"]}}):
        await db.subscriptions.update_one({"_id": old["_id"]}, {"$set": {
            "status": CANCELLED, "cancel_reason": "superseded", "cancelled_at": now,
            "updated_at": now}})
    await db.organizations.update_one({"_id": ObjectId(s_org_id)}, {"$set": {
        "status": "active", "plan_id": plan["slug"], "subscription_id": sub_id,
        "admin_portal_enabled": True, "activated_at": now, "updated_at": now}})
    from app.billing.invoices import create_invoice_record
    await create_invoice_record(organization_id=s_org_id, subscription_id=sub_id,
                                amount=sub.get("amount", 0.0), currency=sub.get("currency"),
                                plan_name=plan["name"], db=db)
    monthly_tokens = int((plan.get("limits") or {}).get("monthly_tokens") or 0)
    if monthly_tokens > 0:
        from app.billing.tokens import allocate
        allocate(s_org_id, monthly_tokens, source="plan", actor=actor_email,
                 reason=f"{plan['name']} plan activated", expires_at=period_end, reset=True)
    from app.lifecycle.demo import mark_converted
    mark_converted(s_org_id, actor=actor_email, subscription_id=sub_id)
    from app.admin.audit import aaudit
    await aaudit("organization.activated", "lifecycle", user=actor, organization_id=s_org_id,
                 resource_type="organization", resource_id=s_org_id,
                 details={"plan": plan["slug"], "subscription_id": sub_id})
    await aaudit("admin.activated", "lifecycle", user=actor, organization_id=s_org_id,
                 resource_type="organization", resource_id=s_org_id,
                 details={"admin_portal_enabled": True})
    from app.events.notifications import notify_org_admins, notify_super_admins
    notify_super_admins("subscription_activated", "Subscription activated",
                        f"{plan['name']} for org {s_org_id} by {actor_email}",
                        severity="success", link="/superadmin#subscriptions")
    notify_org_admins(s_org_id, "subscription_activated", "Your subscription is active",
                      f"Welcome to {plan['name']}! Your Admin portal is now enabled.",
                      severity="success", link="/org-admin")
    return await get_organization_subscription(s_org_id, db=db)


async def set_subscription_status(subscription_id: str, target: str, *,
                                  actor: Dict[str, Any], reason: str = "",
                                  db=None) -> Dict[str, Any]:
    """Super Admin: reject (cancel) a pending one, suspend, resume, expire, cancel."""
    if db is None:
        db = get_async_db()
    if target == ACTIVE:
        sub0 = await db.subscriptions.find_one({"_id": ObjectId(str(subscription_id))})
        if sub0 and sub0.get("status") == PENDING_ADMIN_CONFIRMATION:
            return await confirm_subscription(subscription_id, actor=actor, db=db)
    if target not in SUBSCRIPTION_STATUSES:
        raise HTTPException(status_code=422, detail="Unknown status")
    try:
        sub = await db.subscriptions.find_one({"_id": ObjectId(str(subscription_id))})
    except Exception:
        sub = None
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    was = sub.get("status")
    extra: Dict[str, Any] = {}
    if target == CANCELLED:
        extra = {"cancelled_at": utcnow(), "cancel_reason": reason or "cancelled by admin"}
        if was in OPEN_STATUSES:
            await db.payments.update_many(
                {"subscription_id": str(sub["_id"]), "status": "succeeded"},
                {"$set": {"refund_required": True, "updated_at": utcnow()}})
    sub = await _transition(db, sub, target, actor=actor.get("email", "super_admin"),
                            extra=extra, note=reason)
    org_id = sub["organization_id"]
    if was in (ACTIVE, SUSPENDED, "trialing", "past_due"):
        org_status = {SUSPENDED: "suspended", ACTIVE: "active",
                      EXPIRED: "cancelled", CANCELLED: "cancelled"}.get(target)
        if org_status:
            await db.organizations.update_one({"_id": ObjectId(org_id)},
                                              {"$set": {"status": org_status,
                                                        "updated_at": utcnow()}})
            if org_status == "suspended":
                from app.events.notifications import notify_super_admins
                notify_super_admins("organization_suspended", "Organization suspended",
                                    f"Org {org_id}: {reason}", severity="warning")
    return _clean_sub(sub)


def _clean_sub(doc: Dict[str, Any]) -> Dict[str, Any]:
    if not doc:
        return {}
    out = {}
    for k, v in doc.items():
        if k == "_id":
            out["id"] = str(v)
        elif isinstance(v, ObjectId):
            out[k] = str(v)
        elif isinstance(v, dict):
            out[k] = _clean_sub(v)
        elif isinstance(v, list):
            out[k] = [_clean_sub(item) if isinstance(item, dict) else (str(item) if isinstance(item, ObjectId) else item) for item in v]
        else:
            out[k] = v
    return out


async def provision_trial_subscription(
    organization_id: str,
    plan_slug: str = "pro",
    trial_days: int = 14,
    db=None,
) -> Dict[str, Any]:
    """Auto-provision a trial subscription when a new organization is created."""
    if db is None:
        db = get_async_db()
    if db is None:
        return {}

    plan = await get_plan_by_slug_or_id(plan_slug, db=db)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    actual_slug = plan["slug"]
    days = int(trial_days or plan.get("trial_days") or 0)

    now = utcnow()
    trial_end = now + timedelta(days=days)
    s_org_id = str(organization_id)

    sub_doc = {
        "organization_id": s_org_id,
        "plan_id": actual_slug,
        "status": "trialing",
        "provider": "mock",
        "provider_customer_id": f"cus_{s_org_id[:12]}",
        "provider_subscription_id": f"sub_trial_{s_org_id[:12]}",
        "billing_cycle": "monthly",
        "currency": plan.get("currency"),
        "amount": plan.get("price_monthly"),
        "started_at": now,
        "current_period_start": now,
        "current_period_end": trial_end,
        "trial_start": now,
        "trial_end": trial_end,
        "cancel_at_period_end": False,
        "cancelled_at": None,
        "grace_period_end": None,
        "created_at": now,
        "updated_at": now,
    }

    res = await db.subscriptions.insert_one(sub_doc)
    sub_id = str(res.inserted_id)
    sub_doc["id"] = sub_id

    # Update organization with trial metadata
    try:
        await db.organizations.update_one(
            {"_id": ObjectId(s_org_id)},
            {
                "$set": {
                    "status": "trial",
                    "plan_id": actual_slug,
                    "subscription_id": sub_id,
                    "trial_started_at": now,
                    "trial_ends_at": trial_end,
                    "updated_at": now,
                }
            },
        )
    except Exception as e:
        logger.warning(f"Error linking trial subscription to org {s_org_id}: {e}")

    return _clean_sub(sub_doc)


async def get_organization_subscription(organization_id: str, db=None) -> Dict[str, Any]:
    """Retrieve active subscription details with calculated trial countdown and plan info."""
    if db is None:
        db = get_async_db()
    if db is None:
        return {}

    s_org_id = str(organization_id)
    sub = await db.subscriptions.find_one(
        {"organization_id": s_org_id,
         "status": {"$in": [ACTIVE, SUSPENDED, "trialing", "past_due", "paused"]}},
        sort=[("created_at", -1)],
    )
    pending = await db.subscriptions.find_one(
        {"organization_id": s_org_id, "status": {"$in": list(OPEN_STATUSES)}},
        sort=[("created_at", -1)])
    pending_clean = None
    if pending:
        pending_clean = _clean_sub(pending)
        pending_clean["plan"] = await get_plan_by_slug_or_id(pending.get("plan_id"), db=db)

    now = utcnow()
    if not sub:
        org = None
        try:
            org = await db.organizations.find_one({"_id": ObjectId(s_org_id)})
        except Exception:
            pass
        from app.billing.entitlements import EntitlementService
        plan = await EntitlementService.get_effective_plan(s_org_id, db=db)
        return {
            "has_subscription": False,
            "status": (org or {}).get("status", "none"),
            "plan_id": plan.get("slug"),
            "plan": plan,
            "billing_cycle": None,
            "is_trial": False,
            "is_demo": bool(plan.get("is_demo")),
            "trial_days_remaining": 0,
            "pending": pending_clean,
        }

    sub_cleaned = _clean_sub(sub)
    sub_cleaned["pending"] = pending_clean
    plan = await get_plan_by_slug_or_id(sub.get("plan_id"), db=db)
    sub_cleaned["plan"] = plan
    sub_cleaned["has_subscription"] = True

    # Trial countdown
    is_trial = sub.get("status") == "trialing"
    trial_days_remaining = 0
    if is_trial and sub.get("trial_end"):
        trial_end = sub["trial_end"]
        if trial_end.tzinfo is None:
            trial_end = trial_end.replace(tzinfo=timezone.utc)
        diff = (trial_end - now).total_seconds()
        trial_days_remaining = max(0, int(diff // 86400) + (1 if diff % 86400 > 0 else 0))

        if diff <= 0:
            sub_cleaned["status"] = "expired"
            # Auto-expire if past trial end
            await db.subscriptions.update_one(
                {"_id": sub["_id"]},
                {"$set": {"status": "expired", "updated_at": now}},
            )

    sub_cleaned["is_trial"] = is_trial
    sub_cleaned["trial_days_remaining"] = trial_days_remaining

    return sub_cleaned


async def change_subscription_plan(
    organization_id: str,
    target_plan_slug: str,
    billing_cycle: str = "monthly",
    db=None,
) -> Dict[str, Any]:
    """Upgrade or downgrade an organization's subscription plan with limit verification."""
    if db is None:
        db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    s_org_id = str(organization_id)
    target_plan = await get_plan_by_slug_or_id(target_plan_slug, db=db)
    if not target_plan:
        raise HTTPException(status_code=404, detail="Requested plan not found")

    # 1. Downgrade safety check: verify active team members do not exceed target plan limit
    target_member_limit = target_plan.get("limits", {}).get("team_members", 1)
    current_members_count = await db.organization_members.count_documents({
        "organization_id": s_org_id,
        "status": "active",
    })

    if current_members_count > target_member_limit:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "DOWNGRADE_MEMBER_LIMIT_EXCEEDED",
                "error": "DOWNGRADE_MEMBER_LIMIT_EXCEEDED",
                "current_members": current_members_count,
                "plan_limit": target_member_limit,
                "message": f"Cannot change plan: you have {current_members_count} active team members, but the {target_plan['name']} plan allows a maximum of {target_member_limit}. Please remove members before downgrading.",
            },
        )

    now = utcnow()
    cycle_days = 365 if billing_cycle == "yearly" else 30
    period_end = now + timedelta(days=cycle_days)
    price = (
        target_plan.get("price_yearly", 0.0)
        if billing_cycle == "yearly"
        else target_plan.get("price_monthly", 0.0)
    )

    sub = await db.subscriptions.find_one(
        {"organization_id": s_org_id},
        sort=[("created_at", -1)],
    )
    # A plan change only applies to a live subscription. Anything pending,
    # suspended, expired or cancelled must go through checkout + Super Admin
    # confirmation (confirm_subscription) - never be activated from here.
    if not sub or sub.get("status") not in ("active", "trialing"):
        raise HTTPException(status_code=409, detail={
            "code": "SUBSCRIPTION_NOT_LIVE", "error": "SUBSCRIPTION_NOT_LIVE",
            "status": (sub or {}).get("status"),
            "message": "Only an active subscription can change plan. Pending subscriptions "
                       "are activated through the confirmation queue."})

    if sub:
        await db.subscriptions.update_one(
            {"_id": sub["_id"]},
            {
                "$set": {
                    "plan_id": target_plan["slug"],
                    "status": "active",
                    "billing_cycle": billing_cycle,
                    "amount": price,
                    "currency": target_plan.get("currency", "USD"),
                    "current_period_start": now,
                    "current_period_end": period_end,
                    "cancel_at_period_end": False,
                    "cancelled_at": None,
                    "updated_at": now,
                }
            },
        )
        sub_id = str(sub["_id"])
    else:
        new_sub = {
            "organization_id": s_org_id,
            "plan_id": target_plan["slug"],
            "status": "active",
            "provider": "mock",
            "provider_customer_id": f"cus_{s_org_id[:12]}",
            "provider_subscription_id": f"sub_{s_org_id[:12]}",
            "billing_cycle": billing_cycle,
            "currency": target_plan.get("currency", "USD"),
            "amount": price,
            "started_at": now,
            "current_period_start": now,
            "current_period_end": period_end,
            "cancel_at_period_end": False,
            "created_at": now,
            "updated_at": now,
        }
        res = await db.subscriptions.insert_one(new_sub)
        sub_id = str(res.inserted_id)

    # Update organization
    try:
        await db.organizations.update_one(
            {"_id": ObjectId(s_org_id)},
            {
                "$set": {
                    "plan_id": target_plan["slug"],
                    "status": "active",
                    "subscription_id": sub_id,
                    "updated_at": now,
                }
            },
        )
    except Exception:
        pass

    # Create paid invoice record for the new plan
    from app.billing.invoices import create_invoice_record
    await create_invoice_record(
        organization_id=s_org_id,
        subscription_id=sub_id,
        amount=price,
        currency=target_plan.get("currency", "USD"),
        plan_name=target_plan["name"],
        db=db,
    )

    return await get_organization_subscription(s_org_id, db=db)


async def cancel_subscription(
    organization_id: str,
    at_period_end: bool = True,
    db=None,
) -> Dict[str, Any]:
    """Schedule or execute subscription cancellation."""
    if db is None:
        db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    s_org_id = str(organization_id)
    sub = await db.subscriptions.find_one(
        {"organization_id": s_org_id, "status": {"$in": [ACTIVE, "trialing", "past_due"]}},
        sort=[("created_at", -1)],
    )
    if not sub:
        raise HTTPException(status_code=404, detail="No active subscription found")

    now = utcnow()
    if at_period_end:
        await db.subscriptions.update_one(
            {"_id": sub["_id"]},
            {"$set": {"cancel_at_period_end": True, "cancelled_at": now, "updated_at": now}},
        )
    else:
        # Immediate cancellation
        await db.subscriptions.update_one(
            {"_id": sub["_id"]},
            {
                "$set": {
                    "status": "cancelled",
                    "cancel_at_period_end": False,
                    "cancelled_at": now,
                    "updated_at": now,
                }
            },
        )
        try:
            await db.organizations.update_one(
                {"_id": ObjectId(s_org_id)},
                {"$set": {"status": "cancelled", "updated_at": now}},
            )
        except Exception:
            pass

    return await get_organization_subscription(s_org_id, db=db)


async def reactivate_subscription(organization_id: str, db=None) -> Dict[str, Any]:
    """Reactivate a subscription that was scheduled for cancellation."""
    if db is None:
        db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    s_org_id = str(organization_id)
    sub = await db.subscriptions.find_one(
        {"organization_id": s_org_id, "status": ACTIVE, "cancel_at_period_end": True},
        sort=[("created_at", -1)],
    )
    if not sub:
        raise HTTPException(status_code=409, detail={
            "code": "NOTHING_TO_REACTIVATE",
            "message": "Only an active subscription scheduled for cancellation can be "
                       "reactivated. Choose a plan to subscribe again."})

    now = utcnow()
    await db.subscriptions.update_one(
        {"_id": sub["_id"], "status": ACTIVE},
        {"$set": {"cancel_at_period_end": False, "cancelled_at": None, "updated_at": now}},
    )

    return await get_organization_subscription(s_org_id, db=db)


async def extend_trial(organization_id: str, extra_days: int, reason: str = "", db=None) -> Dict[str, Any]:
    """Super Admin action to extend an organization's trial."""
    if db is None:
        db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    s_org_id = str(organization_id)
    sub = await db.subscriptions.find_one(
        {"organization_id": s_org_id},
        sort=[("created_at", -1)],
    )
    # Extending a trial never overrides a paid, pending or deliberately
    # stopped subscription (that would bypass payment / confirmation).
    if sub and sub.get("status") not in ("trialing", "expired"):
        raise HTTPException(status_code=409, detail={
            "code": "SUBSCRIPTION_NOT_TRIAL", "error": "SUBSCRIPTION_NOT_TRIAL",
            "status": sub.get("status"),
            "message": f"Cannot extend a trial on a {sub.get('status')} subscription."})
    now = utcnow()
    base_end = sub.get("trial_end") if (sub and sub.get("trial_end")) else now
    if base_end.tzinfo is None:
        base_end = base_end.replace(tzinfo=timezone.utc)
    new_end = max(now, base_end) + timedelta(days=extra_days)

    if sub:
        await db.subscriptions.update_one(
            {"_id": sub["_id"]},
            {
                "$set": {
                    "status": "trialing",
                    "trial_end": new_end,
                    "current_period_end": new_end,
                    "updated_at": now,
                }
            },
        )

    try:
        await db.organizations.update_one(
            {"_id": ObjectId(s_org_id)},
            {"$set": {"status": "trial", "trial_ends_at": new_end, "updated_at": now}},
        )
    except Exception:
        pass

    return await get_organization_subscription(s_org_id, db=db)
