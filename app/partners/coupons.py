"""Partner coupons.

A coupon belongs to one partner. At checkout (``start_checkout``) a valid
code discounts the subscription amount the provider charges, and records
the coupon on the subscription. The redemption is counted (and an
unattributed organization is attributed to the coupon's partner) only when
the subscription is confirmed ACTIVE — abandoned checkouts never consume a
coupon.
"""
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException

from app.db.models import utcnow
from app.db.mongo import get_sync_db
from app.partners import constants as K
from app.partners.service import (
    actor_email, clean, db_or_503, get_program_settings, money, oid, paudit,
)

logger = logging.getLogger(__name__)
_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{3,29}$")


def _parse_date(v: Any) -> Optional[datetime]:
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(status_code=422, detail="expires_at must be an ISO date")


def save_coupon(data: Dict[str, Any], *, partner_id: str, actor: Any, by_partner: bool = False,
                coupon_id: Optional[str] = None, ip: Optional[str] = None) -> Dict[str, Any]:
    db = db_or_503()
    partner = db[K.PARTNERS].find_one({"_id": oid(partner_id)}) if oid(partner_id) else None
    if not partner:
        raise HTTPException(status_code=404, detail="Partner not found")
    settings = get_program_settings(db)
    if by_partner and not settings.get("partner_coupons_enabled"):
        raise HTTPException(status_code=403, detail="Partner-created coupons are not enabled")
    dtype = data.get("discount_type", "percentage")
    if dtype not in ("percentage", "fixed"):
        raise HTTPException(status_code=422, detail="discount_type must be percentage or fixed")
    value = float(data.get("discount_value") or 0)
    if value <= 0 or (dtype == "percentage" and value >= 100):
        raise HTTPException(status_code=422, detail="Discount value is out of range")
    if by_partner:
        if dtype != "percentage":
            raise HTTPException(status_code=422, detail="Partners can create percentage coupons only")
        tier = db[K.TIERS].find_one({"_id": oid(partner.get("tier_id"))}) if partner.get("tier_id") else None
        if tier and tier.get("can_create_coupons") is False:
            raise HTTPException(status_code=403, detail="Your partner tier does not include creating coupons")
        cap = float(settings.get("max_partner_coupon_percent") or 0)
        if tier and tier.get("max_coupon_percent") is not None:
            cap = min(cap, float(tier["max_coupon_percent"]))
        if value > cap:
            raise HTTPException(status_code=422, detail=f"Maximum partner discount is {cap:g}%")
    eligibility = data.get("eligibility") or "any"
    if eligibility not in ("any", "new_customers", "referred_customers"):
        raise HTTPException(status_code=422, detail="eligibility must be any, new_customers or referred_customers")
    basis = data.get("commission_basis") or "paid"
    if basis not in ("paid", "list", "none"):
        raise HTTPException(status_code=422, detail="commission_basis must be paid, list or none")
    if by_partner:
        basis = "paid"  # only the Super Admin decides how a coupon affects commission
    max_red = data.get("max_redemptions")
    max_red = int(max_red) if max_red not in (None, "", 0, "0") else None
    if max_red is not None and max_red < 1:
        raise HTTPException(status_code=422, detail="max_redemptions must be at least 1")
    doc = {
        "discount_type": dtype, "discount_value": round(value, 2),
        "currency": (data.get("currency") or "USD").upper()[:3],
        "max_redemptions": max_red, "expires_at": _parse_date(data.get("expires_at")),
        "plan_slugs": [re.sub(r"[^a-z0-9_-]", "", str(s).lower())[:40]
                       for s in (data.get("plan_slugs") or []) if str(s).strip()],
        "status": "active" if data.get("status", "active") == "active" else "disabled",
        "updated_at": utcnow(),
    }
    if not (by_partner and coupon_id):
        # a partner's edit keeps the eligibility and commission basis the Super Admin set
        doc.update({"eligibility": eligibility, "commission_basis": basis})
    if coupon_id:
        c = db[K.COUPONS].find_one({"_id": oid(coupon_id), "partner_id": str(partner["_id"])}) if oid(coupon_id) else None
        if not c:
            raise HTTPException(status_code=404, detail="Coupon not found")
        if by_partner and c.get("created_by_role") != "partner":
            raise HTTPException(status_code=403, detail="This coupon is managed by LeadAI")
        db[K.COUPONS].update_one({"_id": c["_id"]}, {"$set": doc})
        action = "partner.coupon.updated"
    else:
        code = re.sub(r"\s+", "", str(data.get("code") or "")).upper()
        if not _CODE_RE.match(code):
            raise HTTPException(status_code=422, detail="Code must be 4–30 letters, digits, - or _")
        if db[K.COUPONS].find_one({"code": code}):
            raise HTTPException(status_code=409, detail="This coupon code is already taken")
        doc.update({"code": code, "partner_id": str(partner["_id"]), "times_redeemed": 0,
                    "created_by": actor_email(actor),
                    "created_by_role": "partner" if by_partner else "super_admin",
                    "created_at": utcnow()})
        try:
            coupon_id = str(db[K.COUPONS].insert_one(doc).inserted_id)
        except Exception:
            raise HTTPException(status_code=409, detail="This coupon code is already taken")
        action = "partner.coupon.created"
    paudit(action, str(partner["_id"]), actor=actor, ip=ip,
           details={"discount_type": dtype, "discount_value": doc["discount_value"],
                    "status": doc["status"]},
           resource_type="partner_coupon", resource_id=coupon_id)
    saved = db[K.COUPONS].find_one({"_id": oid(coupon_id)})
    if by_partner:
        from app.events.notifications import notify_super_admins
        notify_super_admins("partner_coupon", "Partner coupon " + ("created" if action.endswith("created") else "updated"),
                            f"{partner.get('company') or partner.get('name')}: {saved['code']} "
                            f"({saved['discount_value']:g}% , {saved['status']})", severity="info",
                            link="/superadmin#/partners?tab=coupons")
    else:
        from app.partners.service import notify_partner
        notify_partner(partner, "partner_coupon",
                       "New coupon for you" if action.endswith("created") else "Your coupon was updated",
                       f"Coupon {saved['code']}: " + (f"{saved['discount_value']:g}%" if dtype == "percentage"
                                                      else f"{saved['currency']} {saved['discount_value']:.2f}")
                       + f" off ({saved['status']}).", severity="info", link="/partner#/coupons")
    return clean(db[K.COUPONS].find_one({"_id": oid(coupon_id)}))


def _check_coupon_abuse(db, info: Dict[str, Any], org_id: str, partner: Dict[str, Any]) -> None:
    """Flag (never block or reverse): the paying organization's owner shares
    the partner's network, or the coupon was redeemed by another organization
    whose owner signed in from the same network."""
    from app.partners.fraud import raise_flag
    owner = db.organization_members.find_one({"organization_id": org_id, "role": "owner"}) or {}
    owner_id = str(owner.get("user_id") or "")
    hashes = {s.get("ip_hash") for s in db.user_sessions.find({"user_id": owner_id}, {"ip_hash": 1}) if s.get("ip_hash")}
    signal = None
    if hashes and db.user_sessions.find_one({"user_id": str(partner["user_id"]), "ip_hash": {"$in": list(hashes)}}):
        signal = "customer owner and partner use the same network"
    else:
        others = [u["organization_id"] for u in db[K.COUPON_USAGES].find(
            {"coupon_id": info["coupon_id"], "organization_id": {"$ne": org_id}}, {"organization_id": 1})]
        if others and hashes:
            other_owners = [str(m["user_id"]) for m in db.organization_members.find(
                {"organization_id": {"$in": others}, "role": "owner"}, {"user_id": 1})]
            if db.user_sessions.find_one({"user_id": {"$in": other_owners}, "ip_hash": {"$in": list(hashes)}}):
                signal = "several redeeming organizations share one network"
    if signal:
        ref = db[K.REFERRALS].find_one({"organization_id": org_id})
        raise_flag("coupon_abuse", partner_id=str(partner["_id"]), severity="medium",
                   subject_type="coupon", subject_id=info["coupon_id"],
                   hold_referral_id=str(ref["_id"]) if ref else None,
                   details={"code": info["code"], "organization_id": org_id, "signal": signal}, db=db)


def find_coupon(code: str, db=None) -> Optional[Dict[str, Any]]:
    db = db if db is not None else get_sync_db()
    code = re.sub(r"\s+", "", str(code or "")).upper()
    if db is None or not _CODE_RE.match(code):
        return None
    return db[K.COUPONS].find_one({"code": code})


def validate_for_checkout(code: str, *, organization_id: str, plan_slug: str,
                          price: float, currency: Optional[str], db=None) -> Dict[str, Any]:
    """Returns the discount to apply, or raises 422 with a customer-facing reason."""
    db = db if db is not None else get_sync_db()
    c = find_coupon(code, db)
    if not c or c.get("status") != "active":
        raise HTTPException(status_code=422, detail="This coupon code is not valid")
    exp = c.get("expires_at")
    if exp is not None:
        exp = exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)
        if exp <= utcnow():
            raise HTTPException(status_code=422, detail="This coupon has expired")
    if c.get("max_redemptions") and int(c.get("times_redeemed") or 0) >= int(c["max_redemptions"]):
        raise HTTPException(status_code=422, detail="This coupon has been fully redeemed")
    if c.get("plan_slugs") and plan_slug not in c["plan_slugs"]:
        raise HTTPException(status_code=422, detail="This coupon doesn't apply to the selected plan")
    partner = db[K.PARTNERS].find_one({"_id": oid(c["partner_id"])})
    if not partner or partner.get("status") != K.P_ACTIVE:
        raise HTTPException(status_code=422, detail="This coupon code is not valid")
    # self-use: the partner's own account is a member of the paying organization
    if db.organization_members.find_one({"organization_id": str(organization_id),
                                         "user_id": str(partner["user_id"])}):
        raise HTTPException(status_code=422, detail="Partners can't use their own coupon")
    if db[K.COUPON_USAGES].find_one({"coupon_id": str(c["_id"]), "organization_id": str(organization_id)}):
        raise HTTPException(status_code=422, detail="This coupon was already used by your organization")
    eligibility = c.get("eligibility") or "any"
    if eligibility == "new_customers" and db.subscriptions.find_one(
            {"organization_id": str(organization_id),
             "status": {"$in": ["active", "suspended", "expired", "cancelled", "past_due"]},
             "confirmed_at": {"$ne": None}}, {"_id": 1}):
        raise HTTPException(status_code=422, detail="This coupon is for new customers only")
    if eligibility == "referred_customers":
        ref = db[K.REFERRALS].find_one({"organization_id": str(organization_id), "status": "active"})
        if not ref or ref.get("partner_id") != c["partner_id"]:
            raise HTTPException(status_code=422, detail="This coupon is only for customers referred by this partner")
    if c["discount_type"] == "fixed" and (c.get("currency") or "USD").upper() != (currency or "USD").upper():
        raise HTTPException(status_code=422, detail="This coupon doesn't apply to this currency")
    if c["discount_type"] == "percentage":
        discount = money(price * float(c["discount_value"]) / 100.0)
    else:
        discount = money(min(float(c["discount_value"]), price))
    final = money(price - discount)
    if final <= 0:
        raise HTTPException(status_code=422, detail="This coupon can't make the plan free")
    return {"coupon_id": str(c["_id"]), "code": c["code"], "partner_id": c["partner_id"],
            "discount_type": c["discount_type"], "discount_value": c["discount_value"],
            "commission_basis": c.get("commission_basis") or "paid",
            "list_amount": money(price), "discount_amount": discount, "final_amount": final}


def record_coupon_redemption(sub: Dict[str, Any], db=None) -> None:
    """Subscription confirmed ACTIVE with a coupon: count the redemption once
    and attribute an unattributed organization to the coupon's partner."""
    info = sub.get("coupon")
    if not info:
        return
    db = db if db is not None else get_sync_db()
    if db is None:
        return
    org_id = str(sub["organization_id"])
    try:
        db[K.COUPON_USAGES].insert_one({
            "coupon_id": info["coupon_id"], "partner_id": info["partner_id"], "code": info["code"],
            "organization_id": org_id, "subscription_id": str(sub["_id"]),
            "discount_amount": info.get("discount_amount"), "currency": sub.get("currency"),
            "created_at": utcnow()})
    except Exception:
        return  # already recorded (unique coupon + organization)
    db[K.COUPONS].update_one({"_id": oid(info["coupon_id"])}, {"$inc": {"times_redeemed": 1}})
    org_doc = db.organizations.find_one({"_id": oid(org_id)}, {"name": 1}) or {}
    partner_doc = db[K.PARTNERS].find_one({"_id": oid(info["partner_id"])})
    if partner_doc:
        from app.partners.service import notify_partner
        notify_partner(partner_doc, "partner_coupon", "Your coupon was redeemed",
                       f"{org_doc.get('name') or 'A customer'} subscribed with coupon {info['code']}.",
                       severity="success", link="/partner#/coupons")
        _check_coupon_abuse(db, info, org_id, partner_doc)
    paudit("partner.coupon.redeemed", info["partner_id"], actor="system",
           details={"code": info["code"], "organization_id": org_id,
                    "discount": info.get("discount_amount")},
           resource_type="partner_coupon", resource_id=info["coupon_id"])
    if not db[K.REFERRALS].find_one({"organization_id": org_id}, {"_id": 1}):
        partner = db[K.PARTNERS].find_one({"_id": oid(info["partner_id"]), "status": K.P_ACTIVE})
        owner = db.organization_members.find_one({"organization_id": org_id, "role": "owner"}) or {}
        user = db.users.find_one({"_id": oid(owner.get("user_id"))}) if owner.get("user_id") else None
        org = db.organizations.find_one({"_id": oid(org_id)}) or {}
        if partner:
            from app.partners.referrals import create_referral
            create_referral(db, partner, organization_id=org_id,
                            user_id=str(user["_id"]) if user else None,
                            email=(user or {}).get("email", ""), company=org.get("name"),
                            source="coupon", signup_ip=None, phone=(user or {}).get("phone"))


# ── partner pricing (customer prices for referred organizations) ────────────

def save_pricing_rule(data: Dict[str, Any], *, actor: Any, rule_id: Optional[str] = None,
                      ip: Optional[str] = None) -> Dict[str, Any]:
    """Super Admin: a discount that customers referred by a partner (or by any
    partner of a tier) get automatically at checkout. Never stacks with a
    coupon: the better of the two applies."""
    db = db_or_503()
    name = (str(data.get("name") or "").strip() or "Partner pricing")[:80]
    scope = data.get("scope", "partner")
    if scope not in ("partner", "tier", "all"):
        raise HTTPException(status_code=422, detail="scope must be partner, tier or all")
    partner_id = tier_id = None
    if scope == "partner":
        partner_id = str(data.get("partner_id") or "")
        if not oid(partner_id) or not db[K.PARTNERS].find_one({"_id": oid(partner_id)}):
            raise HTTPException(status_code=422, detail="Unknown partner")
    if scope == "tier":
        tier_id = str(data.get("tier_id") or "")
        if not oid(tier_id) or not db[K.TIERS].find_one({"_id": oid(tier_id)}):
            raise HTTPException(status_code=422, detail="Unknown tier")
    dtype = data.get("discount_type", "percentage")
    if dtype not in ("percentage", "fixed"):
        raise HTTPException(status_code=422, detail="discount_type must be percentage or fixed")
    value = float(data.get("discount_value") or 0)
    if value <= 0 or (dtype == "percentage" and value >= 100):
        raise HTTPException(status_code=422, detail="Discount value is out of range")
    doc = {"name": name, "scope": scope, "partner_id": partner_id, "tier_id": tier_id,
           "discount_type": dtype, "discount_value": round(value, 2),
           "currency": (data.get("currency") or "USD").upper()[:3],
           "plan_slugs": [re.sub(r"[^a-z0-9_-]", "", str(x).lower())[:40]
                          for x in (data.get("plan_slugs") or []) if str(x).strip()],
           "first_payment_only": bool(data.get("first_payment_only", False)),
           "status": "active" if data.get("status", "active") == "active" else "disabled",
           "updated_at": utcnow()}
    if rule_id:
        if not db[K.PRICING].find_one({"_id": oid(rule_id)}):
            raise HTTPException(status_code=404, detail="Pricing rule not found")
        db[K.PRICING].update_one({"_id": oid(rule_id)}, {"$set": doc})
        action = "partner.pricing.updated"
    else:
        doc.update({"created_at": utcnow(), "created_by": actor_email(actor)})
        rule_id = str(db[K.PRICING].insert_one(doc).inserted_id)
        action = "partner.pricing.created"
    paudit(action, partner_id, actor=actor, ip=ip,
           details={k: doc[k] for k in ("name", "scope", "tier_id", "discount_type", "discount_value",
                                        "plan_slugs", "status")},
           resource_type="partner_pricing", resource_id=rule_id)
    return clean(db[K.PRICING].find_one({"_id": oid(rule_id)}))


def partner_price_for(*, organization_id: str, plan_slug: str, price: float, currency: Optional[str],
                      db=None) -> Optional[Dict[str, Any]]:
    """Best partner pricing discount for a referred organization (or None)."""
    db = db if db is not None else get_sync_db()
    if db is None:
        return None
    ref = db[K.REFERRALS].find_one({"organization_id": str(organization_id), "status": "active"})
    if not ref:
        return None
    partner = db[K.PARTNERS].find_one({"_id": oid(ref["partner_id"]), "status": K.P_ACTIVE})
    if not partner:
        return None
    has_paid_before = db.subscriptions.find_one({"organization_id": str(organization_id),
                                                 "confirmed_at": {"$ne": None}}, {"_id": 1}) is not None
    return best_partner_pricing(db, partner, plan_slug=plan_slug, price=price, currency=currency,
                                has_paid_before=has_paid_before)


def best_partner_pricing(db, partner: Dict[str, Any], *, plan_slug: str, price: float,
                         currency: Optional[str], has_paid_before: bool = False) -> Optional[Dict[str, Any]]:
    """The best active partner pricing rule for this partner and plan (the
    one checkout applies; also used to show partners their customer price)."""
    scopes = [{"scope": "partner", "partner_id": str(partner["_id"])}, {"scope": "all"}]
    if partner.get("tier_id"):
        scopes.append({"scope": "tier", "tier_id": str(partner["tier_id"])})
    best = None
    for rule in db[K.PRICING].find({"status": "active", "$or": scopes}):
        if rule.get("plan_slugs") and plan_slug not in rule["plan_slugs"]:
            continue
        if rule.get("first_payment_only") and has_paid_before:
            continue
        if rule["discount_type"] == "fixed" and (rule.get("currency") or "USD") != (currency or "USD").upper():
            continue
        if rule["discount_type"] == "percentage":
            discount = money(price * float(rule["discount_value"]) / 100.0)
        else:
            discount = money(min(float(rule["discount_value"]), price))
        if price - discount <= 0:
            continue
        if best is None or discount > best["discount_amount"]:
            best = {"pricing_rule_id": str(rule["_id"]), "name": rule["name"], "partner_id": str(partner["_id"]),
                    "discount_type": rule["discount_type"], "discount_value": rule["discount_value"],
                    "first_payment_only": bool(rule.get("first_payment_only")),
                    "list_amount": money(price), "discount_amount": discount,
                    "final_amount": money(price - discount)}
    return best
