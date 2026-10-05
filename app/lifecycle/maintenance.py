"""
Time-driven lifecycle transitions (billing periods, cancellations, demos).

Nothing in a request cycle notices that a period has ended, so a periodic
sweep (started by the app lifespan, see ``start_background_sweeper``) keeps
subscriptions, organizations, token balances and notifications in step:

  * renewal — ``renew_subscription`` starts the next billing period: it
    advances ``current_period_*``, re-grants the plan's monthly tokens (the
    old balance expired with the period) and records an invoice. Stripe
    renewals arrive through the ``invoice.paid`` webhook; the sweep never
    renews on its own (no payment = no renewal);
  * cancel at period end — an active subscription with
    ``cancel_at_period_end`` whose period is over becomes ``cancelled`` and
    the organization ``cancelled`` (access ends, data is kept);
  * overdue — an active subscription whose period ended more than
    ``GRACE_DAYS`` ago without a renewal becomes ``past_due``; the org's
    admins and the Super Admin are told once (the Super Admin can extend);
  * demo expiry — demo admins are warned ``DEMO_WARN_DAYS`` before the demo
    ends (``demo_expiring``) and told when it has ended (``demo_expired``),
    once each.

Safe rollout: automation starts in PREVIEW mode. The background loop only
lists the changes it would make (and tells the Super Admin once) until the
Super Admin reviews them and turns automation on (``lifecycle.auto_apply``).

Every function is idempotent and safe to run concurrently with requests.
"""
import asyncio
import logging
from datetime import timedelta
from typing import Any, Dict, Optional

from app.db.models import utcnow

logger = logging.getLogger(__name__)

GRACE_DAYS = 3
DEMO_WARN_DAYS = 2
SWEEP_INTERVAL_SECONDS = 15 * 60


def _aware(dt):
    from app.billing.tokens import _aware as aware
    return aware(dt)


def _cycle_days(sub: Dict[str, Any]) -> int:
    return 365 if sub.get("billing_cycle") == "yearly" else 30


def renew_subscription(db, sub: Dict[str, Any], *, source: str, actor: str = "system",
                       amount: Optional[float] = None) -> Dict[str, Any]:
    """Start the next billing period of an active subscription (idempotent
    per period: a renewal for a period that already started is ignored)."""
    now = utcnow()
    end = _aware(sub.get("current_period_end")) or now
    if end > now + timedelta(days=_cycle_days(sub) // 2):
        return {"renewed": False, "reason": "period_already_current"}
    start = max(end, now) if end < now - timedelta(days=GRACE_DAYS) else end
    new_end = start + timedelta(days=_cycle_days(sub))
    res = db.subscriptions.update_one(
        {"_id": sub["_id"], "current_period_end": sub.get("current_period_end")},
        {"$set": {"status": "active", "current_period_start": start, "current_period_end": new_end,
                  "renewed_at": now, "past_due_notified": False, "updated_at": now}})
    if not res.modified_count:
        return {"renewed": False, "reason": "concurrent_update"}
    org_id = str(sub["organization_id"])
    # brought their own Apify / Gemini key since the last payment -> the new period costs less
    from app.billing.api_coverage import renewal_amount
    period_amount = renewal_amount(db, sub, float(sub.get("amount") or 0))
    if amount is None:
        amount = period_amount
    plan = db.plans.find_one({"slug": sub.get("plan_id")}) or {}
    tokens = int((plan.get("limits") or {}).get("monthly_tokens") or 0)
    if tokens > 0:
        from app.billing.tokens import allocate
        allocate(org_id, tokens, source="plan", actor=actor,
                 reason=f"{plan.get('name') or sub.get('plan_id')} renewed", expires_at=new_end, reset=True)
    db.invoices.insert_one({
        "organization_id": org_id, "subscription_id": str(sub["_id"]),
        "invoice_number": f"INV-{now:%Y%m%d}-{str(sub['_id'])[-6:]}-{int(now.timestamp()) % 100000}",
        "amount": amount if amount is not None else float(sub.get("amount") or 0),
        "currency": sub.get("currency") or "USD", "status": "paid",
        "plan_name": plan.get("name") or sub.get("plan_id"), "source": source, "created_at": now})
    from app.admin.audit import audit
    audit("subscription.renewed", "billing", user=actor, organization_id=org_id,
          resource_type="subscription", resource_id=str(sub["_id"]),
          details={"period_end": new_end.isoformat(), "source": source, "tokens": tokens})
    from app.partners.commissions import on_subscription_renewed
    on_subscription_renewed(sub, amount=amount if amount is not None else float(sub.get("amount") or 0),
                            period_start=start, db=db)
    from app.events.notifications import notify_org_admins
    notify_org_admins(org_id, "subscription_renewed", "Subscription renewed",
                      f"Your {plan.get('name') or 'plan'} renewed until {new_end:%d %b %Y}.",
                      severity="success", link="/org-admin#subscription")
    return {"renewed": True, "current_period_end": new_end}


def _oid(value):
    from bson import ObjectId
    return ObjectId(str(value))


def run_lifecycle_sweep(db=None, *, dry_run: bool = False, planned: Optional[list] = None) -> Dict[str, int]:
    """One pass over time-driven transitions. Returns counts per action.
    ``dry_run`` changes nothing and appends what WOULD happen to ``planned``."""
    if db is None:
        from app.db.mongo import get_sync_db
        db = get_sync_db()
    out = {"cancelled_at_period_end": 0, "past_due": 0, "demo_expiring": 0, "demo_expired": 0}
    if db is None:
        return out
    planned = planned if planned is not None else []

    def plan(action: str, org_id: Any, **info: Any) -> None:
        planned.append({"action": action, "organization_id": str(org_id), **{
            k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in info.items()}})
        out[action] += 1
    from app.events.notifications import notify_org_admins, notify_super_admins
    from app.admin.audit import audit
    now = utcnow()

    # 1) cancel at period end
    for sub in db.subscriptions.find({"status": "active", "cancel_at_period_end": True,
                                      "current_period_end": {"$lte": now}}):
        if dry_run:
            plan("cancelled_at_period_end", sub["organization_id"], subscription_id=str(sub["_id"]),
                 period_end=sub.get("current_period_end"))
            continue
        res = db.subscriptions.update_one({"_id": sub["_id"], "status": "active"}, {"$set": {
            "status": "cancelled", "cancel_reason": "cancelled_at_period_end",
            "ended_at": now, "updated_at": now}})
        if not res.modified_count:
            continue
        org_id = str(sub["organization_id"])
        db.organizations.update_one({"_id": _oid(org_id), "subscription_id": str(sub["_id"])},
                                    {"$set": {"status": "cancelled", "updated_at": now}})
        audit("subscription.ended", "billing", user="system", organization_id=org_id,
              resource_type="subscription", resource_id=str(sub["_id"]),
              details={"reason": "cancelled_at_period_end"})
        from app.partners.commissions import on_subscription_ended
        on_subscription_ended(sub, reason="cancelled_at_period_end", db=db)
        notify_org_admins(org_id, "subscription_cancelled", "Your subscription has ended",
                          "It was cancelled at the end of the billing period. Choose a plan to continue.",
                          severity="warning", email=True, link="/dashboard#billing")
        notify_super_admins("subscription_cancelled", "Subscription ended",
                            f"Organization {org_id}: cancelled at period end.",
                            severity="info", link="/superadmin#/subscriptions")
        out["cancelled_at_period_end"] += 1

    # 2) overdue (period ended, no renewal)
    cutoff = now - timedelta(days=GRACE_DAYS)
    for sub in db.subscriptions.find({"status": "active", "current_period_end": {"$lte": cutoff},
                                      "cancel_at_period_end": {"$ne": True}}):
        if dry_run:
            plan("past_due", sub["organization_id"], subscription_id=str(sub["_id"]),
                 period_end=sub.get("current_period_end"))
            continue
        res = db.subscriptions.update_one({"_id": sub["_id"], "status": "active"}, {"$set": {
            "status": "past_due", "past_due_since": now, "past_due_notified": True,
            "updated_at": now}})
        if not res.modified_count:
            continue
        org_id = str(sub["organization_id"])
        audit("subscription.past_due", "billing", user="system", organization_id=org_id,
              resource_type="subscription", resource_id=str(sub["_id"]),
              details={"period_end": str(sub.get("current_period_end"))})
        notify_org_admins(org_id, "payment_failed", "Your subscription payment is overdue",
                          "The billing period ended without a renewal payment. Renew to keep your tokens.",
                          severity="warning", email=True, link="/dashboard#billing")
        notify_super_admins("payment_failed", "Subscription overdue",
                            f"Organization {org_id}: period ended {GRACE_DAYS}+ days ago without renewal.",
                            severity="warning", link="/superadmin#/subscriptions")
        out["past_due"] += 1

    # 3) demo expiring / expired (notify once each)
    for org in db.organizations.find({"status": "demo", "demo.expires_at": {"$ne": None}},
                                     {"demo": 1, "name": 1}):
        exp = _aware((org.get("demo") or {}).get("expires_at"))
        if exp is None:
            continue
        demo = org.get("demo") or {}
        org_id = str(org["_id"])
        if dry_run:
            if exp <= now and not demo.get("expired_notified"):
                plan("demo_expired", org_id, name=org.get("name"), expires_at=exp)
            elif now < exp <= now + timedelta(days=DEMO_WARN_DAYS) and not demo.get("expiring_notified"):
                plan("demo_expiring", org_id, name=org.get("name"), expires_at=exp)
            continue
        if exp <= now and not demo.get("expired_notified"):
            db.organizations.update_one({"_id": org["_id"]}, {"$set": {"demo.expired_notified": True}})
            notify_org_admins(org_id, "demo_expired", "Your LeadAI free trial has ended",
                              "Choose a plan to keep using LeadAI — your searches and leads are kept.",
                              severity="warning", email=True, link="/user#billing")
            notify_super_admins("demo_expired", "Free trial ended", f"{org.get('name') or org_id}",
                                link="/superadmin#/organizations/" + org_id)
            out["demo_expired"] += 1
        elif now < exp <= now + timedelta(days=DEMO_WARN_DAYS) and not demo.get("expiring_notified"):
            db.organizations.update_one({"_id": org["_id"]}, {"$set": {"demo.expiring_notified": True}})
            notify_org_admins(org_id, "demo_expiring", "Your LeadAI free trial ends soon",
                              f"Your free trial ends on {exp:%d %b %Y %H:%M} UTC. Choose a plan to continue without interruption.",
                              severity="info", email=True, link="/user#billing")
            out["demo_expiring"] += 1
    if any(out.values()):
        logger.info("[lifecycle] sweep%s: %s", " (preview)" if dry_run else "", out)
    return out


AUTO_APPLY_SETTING = "lifecycle.auto_apply"


def automatic_pass(db=None) -> Dict[str, Any]:
    """What the background loop does each interval. Until the Super Admin
    switches automation on (``lifecycle.auto_apply``) nothing is changed:
    the pending changes are only previewed, and the Super Admin is told once
    how many are waiting for review."""
    from app.admin.settings import get_bool, get_setting, set_setting
    if db is None:
        from app.db.mongo import get_sync_db
        db = get_sync_db()
    if get_bool(AUTO_APPLY_SETTING):
        return {"mode": "on", "counts": run_lifecycle_sweep(db)}
    planned: list = []
    counts = run_lifecycle_sweep(db, dry_run=True, planned=planned)
    total = sum(counts.values())
    if total and not get_setting("lifecycle.preview_notified"):
        from app.events.notifications import notify_super_admins
        notify_super_admins("system_error", "Lifecycle automation is waiting for your review",
                            f"{total} pending change(s): {counts}. Review them and turn automation on "
                            "in Super Admin → Subscriptions → Lifecycle automation.",
                            severity="warning", link="/superadmin#/subscriptions")
        set_setting("lifecycle.preview_notified", True, by="system")
    return {"mode": "preview", "counts": counts, "planned": planned}


async def _sweeper_loop() -> None:
    await asyncio.sleep(60)  # let startup finish (and keep short test runs free of sweeps)
    while True:
        try:
            await asyncio.to_thread(automatic_pass)
        except Exception as e:  # never let the loop die
            logger.warning("[lifecycle] sweep failed: %s", e)
        try:
            # partner commissions past their hold period become payable
            from app.partners.commissions import release_matured
            await asyncio.to_thread(release_matured)
            from app.partners.tiers import evaluate_if_due
            await asyncio.to_thread(evaluate_if_due)
            from app.db.mongo import get_sync_db
            from app.partners.sales import expire_deals
            await asyncio.to_thread(expire_deals, get_sync_db())
        except Exception as e:
            logger.warning("[partners] commission release failed: %s", e)
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)


def start_background_sweeper() -> "asyncio.Task":
    return asyncio.create_task(_sweeper_loop(), name="lifecycle-sweeper")
