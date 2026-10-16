"""Changing who provides each external API (Apify, Gemini) for an organization.

Rules:
- An API can only be switched to "own" once a verified key is saved, and only
  on a plan that allows own keys.
- No paid subscription (demo, trial, free, none): the change applies at once.
- Paid subscription: the subscription keeps a snapshot of the coverage it was
  paid for (``subscriptions.api_coverage``).
  * Bringing your own key for an API LeadAI provided -> applies at once (LeadAI
    stops paying for it); the lower price starts at the next renewal.
  * Asking LeadAI to provide an API the subscription did NOT pay for -> costs
    more, so it goes through checkout at the new price (``checkout_required``);
    it applies when that payment is confirmed. LeadAI never provides an API the
    customer hasn't paid for.
  * A Super Admin can apply a switch without payment (a courtesy): it covers the
    rest of the period already paid; the renewal charges the new price.
- Renewal charges for the coverage in use at that moment, keeping any partner /
  coupon discount by adding or taking off only the list-price difference.
"""
from typing import Any, Dict, Optional

from bson import ObjectId
from fastapi import HTTPException

from app.billing.plans import API_PROVIDERS, coverage_label, normalize_coverage, plan_api_pricing, price_for
from app.db.models import utcnow

_PAID_LIVE = ("active", "past_due")


async def _live_paid_subscription(db, org_id: str) -> Optional[Dict[str, Any]]:
    sub = await db.subscriptions.find_one({"organization_id": str(org_id), "status": {"$in": list(_PAID_LIVE)}},
                                          sort=[("current_period_start", -1)])
    if sub and float(sub.get("amount") or 0) > 0:
        return sub
    return None


def _renewal_price(plan: Dict[str, Any], sub: Dict[str, Any], now_cov: Dict[str, str],
                   amount: Optional[float] = None) -> float:
    """What the next renewal charges: the stored amount, moved by the list-price
    difference between the coverage paid for and the coverage in use now."""
    amount = float(sub.get("amount") or 0) if amount is None else float(amount)
    paid_for = normalize_coverage(sub.get("api_coverage"), api_mode=sub.get("api_mode"))
    if normalize_coverage(now_cov) == paid_for:
        return round(amount, 2)
    cycle = sub.get("billing_cycle") or "monthly"
    diff = price_for(plan, paid_for, cycle) - price_for(plan, now_cov, cycle)
    return round(max(0.0, amount - diff), 2)


async def coverage_summary(db, org_id: str) -> Dict[str, Any]:
    """What the organization has now, what it pays, and every option's price."""
    from app.billing.entitlements import EntitlementService
    from app.services.tenant_api_keys import iso_utc, public_config
    org = await db.organizations.find_one({"_id": ObjectId(str(org_id))}) if ObjectId.is_valid(str(org_id)) else None
    plan = await EntitlementService.get_effective_plan(str(org_id), db=db) or {}
    cfg = public_config(org)
    sub = await _live_paid_subscription(db, org_id)
    cycle = (sub or {}).get("billing_cycle") or "monthly"
    paid_for = normalize_coverage((sub or {}).get("api_coverage"), api_mode=(sub or {}).get("api_mode")) if sub else None
    pricing = plan_api_pricing(plan) if plan else {"allows_own": False, "addons": {}}
    options = []
    for cov_key, label, cov in (("all_included", "All included", {"apify": "leadai", "gemini": "leadai"}),
                                ("own_apify", "Own Apify key", {"apify": "own", "gemini": "leadai"}),
                                ("own_gemini", "Own Gemini key", {"apify": "leadai", "gemini": "own"}),
                                ("own_both", "Bring both keys", {"apify": "own", "gemini": "own"})):
        if cov_key != "all_included" and not pricing["allows_own"]:
            continue
        options.append({"key": cov_key, "label": label, "coverage": cov,
                        "price_monthly": price_for(plan, cov, "monthly") if plan else 0.0,
                        "price_yearly": price_for(plan, cov, "yearly") if plan else 0.0})
    next_price = (_renewal_price(plan, sub, cfg["coverage"]) if sub
                  else price_for(plan, cfg["coverage"], cycle) if plan else 0.0)
    return {
        **cfg,
        "label": coverage_label(cfg["coverage"]),
        "plan": {"slug": plan.get("slug"), "name": plan.get("name"), "currency": plan.get("currency") or "USD",
                 "allows_own_keys": pricing["allows_own"], "addons": pricing.get("addons", {}),
                 "is_demo": bool(plan.get("is_demo"))},
        "subscription": ({"id": str(sub["_id"]), "billing_cycle": cycle, "amount": float(sub.get("amount") or 0),
                          "paid_for": paid_for, "paid_for_label": coverage_label(paid_for),
                          "current_period_end": iso_utc(sub.get("current_period_end")),
                          "next_price": next_price} if sub else None),
        "options": options,
    }


async def change_coverage(db, org_id: str, requested: Dict[str, str], *, actor: Dict[str, Any],
                          request_meta: Optional[Dict[str, Any]] = None, via: str = "org_admin") -> Dict[str, Any]:
    """Apply the rules above. Returns {"status": "applied"|"scheduled"|"checkout_required", ...}."""
    from app.admin.audit import aaudit
    from app.billing.entitlements import EntitlementService
    from app.services.tenant_api_keys import PROVIDER_NAMES, coverage_of, public_config, set_coverage
    if not isinstance(requested, dict) or not set(requested) & set(API_PROVIDERS):
        raise HTTPException(status_code=422, detail="Choose who provides Apify and Gemini")
    org = await db.organizations.find_one({"_id": ObjectId(str(org_id))}) if ObjectId.is_valid(str(org_id)) else None
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    current = coverage_of(org)
    new = normalize_coverage({**current, **{k: v for k, v in requested.items() if k in API_PROVIDERS}})
    for api, v in requested.items():
        if api in API_PROVIDERS and str(v).strip().lower() not in ("own", "leadai", "byok", "platform"):
            raise HTTPException(status_code=422, detail=f"{PROVIDER_NAMES[api]} must be 'leadai' or 'own'")
    if new == current:
        return {"status": "unchanged", "coverage": current}
    plan = await EntitlementService.get_effective_plan(str(org_id), db=db) or {}
    if "own" in new.values() and not plan_api_pricing(plan)["allows_own"]:
        raise HTTPException(status_code=409, detail=f"The {plan.get('name') or 'current'} plan doesn't support "
                                                    "your own API keys. Choose a paid plan first.")
    keys = public_config(org)["keys"]
    for api in API_PROVIDERS:
        if new[api] == "own" and current[api] != "own" and not (keys[api]["configured"] and keys[api]["verified"]):
            raise HTTPException(status_code=409, detail=f"Add and verify your {PROVIDER_NAMES[api]} key first.")
    sub = await _live_paid_subscription(db, org_id)
    meta = request_meta or {}
    if sub:
        paid_for = normalize_coverage(sub.get("api_coverage"), api_mode=sub.get("api_mode"))
        needs_payment = [api for api in API_PROVIDERS if new[api] == "leadai" and paid_for[api] == "own"]
        if needs_payment:
            cycle = sub.get("billing_cycle") or "monthly"
            return {"status": "checkout_required", "coverage": current, "requested": new,
                    "plan_slug": plan.get("slug"), "billing_cycle": cycle,
                    "new_price": price_for(plan, new, cycle), "currency": plan.get("currency") or "USD",
                    "message": (f"LeadAI providing {', '.join(PROVIDER_NAMES[a] for a in needs_payment)} costs more. "
                                "Complete checkout at the new price; it applies once the payment is confirmed.")}
    await set_coverage(str(org_id), new, db=db)
    status = "applied"
    message = "Saved. The change applies right away."
    if sub:
        nxt = _renewal_price(plan, sub, new)
        if nxt < float(sub.get("amount") or 0):
            status = "scheduled"
            message = ("Saved: your own key is used from now on. Your price drops to "
                       f"{nxt:.2f} {plan.get('currency') or 'USD'} at your next renewal.")
        await db.subscriptions.update_one({"_id": sub["_id"]}, {"$set": {"next_api_coverage": new,
                                                                          "updated_at": utcnow()}})
    await aaudit("organization.api_coverage_changed", "billing", user=actor, organization_id=str(org_id),
                 resource_type="organization", resource_id=str(org_id),
                 details={"before": current, "after": new, "status": status, "via": via},
                 ip=meta.get("ip"), user_agent=meta.get("user_agent"))
    return {"status": status, "coverage": new, "message": message}


def renewal_amount(db, sub: Dict[str, Any], amount: float) -> float:
    """Called at renewal: charges for the coverage in use now (lower after the
    organization brought its own key, higher after a Super Admin courtesy switch
    back to LeadAI-provided). Updates the snapshot."""
    from app.services.tenant_api_keys import coverage_of
    try:
        org = db.organizations.find_one({"_id": ObjectId(str(sub["organization_id"]))}, {"api_coverage": 1, "api_mode": 1,
                                                                                        "custom_api_keys": 1})
        plan = db.plans.find_one({"slug": sub.get("plan_id")}) or {}
    except Exception:
        return amount
    if not org or not plan:
        return amount
    paid_for = normalize_coverage(sub.get("api_coverage"), api_mode=sub.get("api_mode"))
    now_cov = coverage_of(org)
    if now_cov == paid_for:
        return amount
    new_amount = _renewal_price(plan, sub, now_cov, amount)
    db.subscriptions.update_one({"_id": sub["_id"]}, {"$set": {"api_coverage": now_cov, "amount": new_amount},
                                                       "$unset": {"next_api_coverage": "", "api_mode": ""}})
    return new_amount


# Which paid APIs each token-metered action uses, and each one's share of the cost.
ACTION_API_SHARE: Dict[str, Dict[str, float]] = {
    "search": {"apify": 0.6, "gemini": 0.4},
    "collect": {"apify": 1.0},
    # keyword / discovery search stages are pure Apify work
    "discover": {"apify": 1.0},
    "enrich": {"apify": 1.0},
    "sample": {"apify": 1.0},
}


def token_cost_for(organization_id: Optional[str], action: str, cost: int) -> int:
    """Platform tokens track what LeadAI pays for: the share of an action that
    runs on the organization's OWN keys is not charged."""
    shares = ACTION_API_SHARE.get(action)
    if not cost or not shares or not organization_id:
        return cost
    from app.services.tenant_api_keys import _org_sync, coverage_of
    cov = coverage_of(_org_sync(organization_id))
    leadai_share = sum(share for api, share in shares.items() if cov.get(api) != "own")
    if leadai_share >= 0.999:
        return cost
    import math
    return int(math.ceil(cost * leadai_share)) if leadai_share > 0 else 0
