"""Selling LeadAI: the partner sales kit and deal registration.

Sales kit — the LIVE plan catalog (the same ``plans`` documents billing and
the pricing page use), with, for this partner: the customer's price after
partner pricing, the commission the partner earns on the first payment and
each renewal (the rule that would really apply), and a plan-specific
referral link (``/r/{code}?plan={slug}`` → demo request with that plan).

Deal registration — a partner registers a prospect BEFORE it signs up.
The Super Admin approves or rejects it (an approved deal protects the
partner's claim for ``deal_protection_days``). When the prospect signs up
(any attribution path), the deal is linked to the referral and follows its
stage. A signup attributed to ANOTHER partner while a deal is protected opens
an attribution-conflict fraud flag for review — attribution and money are
never changed automatically.
"""
import re
from datetime import timedelta
from typing import Any, Dict, Optional

from fastapi import HTTPException

from app.db.models import utcnow
from app.partners import constants as K
from app.partners.service import (
    clean, db_or_503, get_program_settings, money, normalize_email, notify_partner, oid, paudit,
    referral_url, resolve_rule, text,
)

DEALS = "partner_deals"
DEAL_STATUSES = ("registered", "approved", "rejected", "won", "lost", "expired")
OPEN_DEAL = ("registered", "approved")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_FREE = {"gmail.com", "googlemail.com", "yahoo.com", "outlook.com", "hotmail.com", "live.com", "icloud.com",
         "aol.com", "proton.me", "protonmail.com", "gmx.com", "yandex.com", "rediffmail.com"}


# ── sales kit ────────────────────────────────────────────────────────────────

def catalog(db, partner: Dict[str, Any]) -> Dict[str, Any]:
    from app.billing.plans import plan_highlights
    from app.partners.commissions import compute_commission_amount
    from app.partners.coupons import best_partner_pricing
    plans = []
    for p in db.plans.find({"status": "active", "is_public": {"$ne": False}}).sort("display_order", 1):
        monthly = float(p.get("price_monthly") or 0)
        yearly = float(p.get("price_yearly") or 0)
        cur = (p.get("currency") or "USD").upper()
        if monthly <= 0 and yearly <= 0:
            plans.append({"slug": p["slug"], "name": p.get("name"), "description": p.get("description"),
                          "currency": cur, "free": True, "highlights": plan_highlights(p),
                          "share_url": _plan_link(partner, p["slug"])})
            continue
        rule = resolve_rule(partner, p["slug"], db)
        cycles = {}
        for cycle, price in (("monthly", monthly), ("yearly", yearly)):
            if price <= 0:
                continue
            pricing = best_partner_pricing(db, partner, plan_slug=p["slug"], price=price, currency=cur)
            customer_price = pricing["final_amount"] if pricing else money(price)
            first = compute_commission_amount(rule, customer_price) if rule else 0.0
            cycles[cycle] = {
                "list_price": money(price), "customer_price": customer_price,
                "discount": pricing and {"name": pricing["name"], "amount": pricing["discount_amount"],
                                         "first_payment_only": pricing["first_payment_only"]},
                "commission_first_payment": first,
                "commission_per_renewal": (compute_commission_amount(rule, money(price) if
                                                                     (pricing or {}).get("first_payment_only")
                                                                     else customer_price)
                                           if rule and rule.get("recurring") else 0.0),
            }
        # every API-coverage choice (customer brings own Apify / Gemini keys = lower price)
        from app.billing.plans import coverage_options
        cov_opts = []
        for opt in coverage_options(p):
            row = {"key": opt["key"], "label": opt["label"], "coverage": opt["coverage"]}
            for cycle in ("monthly", "yearly"):
                base_price = float(opt[f"price_{cycle}"] or 0)
                if base_price <= 0:
                    continue
                pp = best_partner_pricing(db, partner, plan_slug=p["slug"], price=base_price, currency=cur)
                cust = pp["final_amount"] if pp else money(base_price)
                row[cycle] = {"list_price": money(base_price), "customer_price": cust,
                              "commission_first_payment": compute_commission_amount(rule, cust) if rule else 0.0}
            cov_opts.append(row)
        plans.append({"slug": p["slug"], "name": p.get("name"), "description": p.get("description"),
                      "coverage_options": cov_opts,
                      "currency": cur, "free": False, "is_default": bool(p.get("is_default")),
                      "highlights": plan_highlights(p), "trial_days": p.get("trial_days") or 0,
                      "cycles": cycles, "share_url": _plan_link(partner, p["slug"]),
                      "commission_rule": rule and {
                          "name": rule.get("name"), "type": rule["commission_type"], "value": rule["value"],
                          "fixed_amount": rule.get("fixed_amount") or 0, "recurring": bool(rule.get("recurring")),
                          "duration_months": rule.get("duration_months") or 0, "cap_amount": rule.get("cap_amount")}})
    coupons = [clean({k: c.get(k) for k in ("code", "discount_type", "discount_value", "currency", "expires_at",
                                            "plan_slugs", "eligibility")})
               for c in db[K.COUPONS].find({"partner_id": str(partner["_id"]), "status": "active"})]
    settings = get_program_settings(db)
    return {"plans": plans, "coupons": coupons, "referral_url": referral_url(partner.get("referral_code")),
            "referral_code": partner.get("referral_code"),
            "attribution_window_days": settings.get("attribution_window_days"),
            "deal_protection_days": settings.get("deal_protection_days"),
            "pitch": [
                "LeadAI finds buyers in the comments of Facebook, Instagram, YouTube and LinkedIn pages.",
                "AI reads every comment, picks out real purchase and pricing inquiries, and extracts phone, email and WhatsApp.",
                "Leads are scored, de-duplicated across runs and routed to the sales team with SLA timers.",
                "Export to Excel / CSV or push to HubSpot, Zoho and webhooks; every customer starts with a free trial.",
            ]}


def _plan_link(partner: Dict[str, Any], slug: str) -> Optional[str]:
    base = referral_url(partner.get("referral_code"))
    return f"{base}?plan={slug}" if base else None


# ── deal registration ────────────────────────────────────────────────────────

def _domain(email: str) -> str:
    return (email or "").lower().partition("@")[2]


def _company_key(name: Optional[str]) -> str:
    from app.partners.referrals import _company_key as ck
    return ck(name)


def _conflict(db, partner_id: str, email: str, company: str) -> Optional[Dict[str, Any]]:
    """An open, approved deal or an existing customer that already covers
    this prospect (another partner's claim)."""
    ne = normalize_email(email)
    for d in db[DEALS].find({"status": {"$in": list(OPEN_DEAL)}, "partner_id": {"$ne": partner_id}}).limit(5000):
        if normalize_email(d.get("contact_email") or "") == ne:
            return {"kind": "deal", "id": str(d["_id"])}
        dom = _domain(email)
        if dom and dom not in _FREE and _domain(d.get("contact_email") or "") == dom:
            return {"kind": "deal", "id": str(d["_id"])}
        if _company_key(company) and _company_key(company) == _company_key(d.get("company")):
            return {"kind": "deal", "id": str(d["_id"])}
    if db.users.find_one({"email": email.lower()}, {"_id": 1}):
        return {"kind": "existing_account"}
    return None


def register_deal(partner: Dict[str, Any], data: Dict[str, Any], *, ip: Optional[str] = None) -> Dict[str, Any]:
    db = db_or_503()
    pid = str(partner["_id"])
    company = text(data.get("company"), 160)
    email = (data.get("contact_email") or "").strip().lower()
    if not company:
        raise HTTPException(status_code=422, detail="Company is required")
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="A valid contact email is required")
    from app.partners.referrals import _self_referral_reason
    if _self_referral_reason(partner, user_id=None, email=email, phone=data.get("contact_phone")):
        raise HTTPException(status_code=422, detail="You can't register yourself as a prospect")
    if db[DEALS].find_one({"partner_id": pid, "contact_email": email, "status": {"$in": list(OPEN_DEAL)}}):
        raise HTTPException(status_code=409, detail="You already registered this prospect")
    if db[DEALS].count_documents({"partner_id": pid, "status": "registered"}) >= 200:
        raise HTTPException(status_code=422, detail="Too many deals awaiting review (200)")
    plan = re.sub(r"[^a-z0-9_-]", "", str(data.get("expected_plan") or "").lower())[:40] or None
    if plan and not db.plans.find_one({"slug": plan, "status": "active"}, {"_id": 1}):
        raise HTTPException(status_code=422, detail="Unknown plan")
    value = data.get("expected_value")
    value = money(value) if value not in (None, "") else None
    if value is not None and not 0 <= value <= 10_000_000:
        raise HTTPException(status_code=422, detail="Expected value is out of range")
    conflict = _conflict(db, pid, email, company)
    now = utcnow()
    doc = {"partner_id": pid, "company": company, "contact_name": text(data.get("contact_name"), 120),
           "contact_email": email, "contact_phone": text(data.get("contact_phone"), 30),
           "website": text(data.get("website"), 300), "expected_plan": plan, "expected_value": value,
           "currency": (data.get("currency") or "USD").upper()[:3], "notes": text(data.get("notes"), 2000),
           "status": "registered", "conflict": conflict, "referral_id": None, "organization_id": None,
           "protected_until": None, "created_at": now, "updated_at": now,
           "history": [{"status": "registered", "at": now, "by": partner["email"]}]}
    doc["_id"] = db[DEALS].insert_one(doc).inserted_id
    paudit("partner.deal.registered", pid, actor=partner["email"], ip=ip,
           details={"company": company, "plan": plan, "value": value, "conflict": conflict},
           resource_type="partner_deal", resource_id=str(doc["_id"]))
    from app.events.notifications import notify_super_admins
    notify_super_admins("partner_deal", "Deal registered" + (" — needs a conflict check" if conflict else ""),
                        f"{partner.get('company') or partner.get('name')}: {company}"
                        + (f" ({plan})" if plan else ""), severity="warning" if conflict else "info",
                        link="/superadmin#/partners?tab=deals", data={"deal_id": str(doc["_id"])})
    return deal_out(doc)


def deal_out(d: Dict[str, Any], *, admin: bool = False) -> Dict[str, Any]:
    out = clean(d)
    if not admin:
        out.pop("conflict", None)
    return out


def _load(db, deal_id: str) -> Dict[str, Any]:
    d = db[DEALS].find_one({"_id": oid(deal_id)}) if oid(deal_id) else None
    if not d:
        raise HTTPException(status_code=404, detail="Deal not found")
    return d


def review_deal(deal_id: str, decision: str, *, actor: Any, note: str = "", ip: Optional[str] = None) -> Dict[str, Any]:
    """Super Admin: approve (protects the claim), reject, or close (won / lost)."""
    db = db_or_503()
    d = _load(db, deal_id)
    target = {"approve": "approved", "reject": "rejected", "won": "won", "lost": "lost"}.get(decision)
    if not target:
        raise HTTPException(status_code=422, detail="decision must be approve, reject, won or lost")
    allowed = {"approved": ("registered",), "rejected": ("registered", "approved"),
               "won": ("registered", "approved"), "lost": ("registered", "approved")}[target]
    if d["status"] not in allowed:
        raise HTTPException(status_code=409, detail=f"Deal is '{d['status']}' — cannot {decision}")
    if target == "rejected" and not (note or "").strip():
        raise HTTPException(status_code=422, detail="A reason is required")
    now = utcnow()
    who = actor.get("email") if isinstance(actor, dict) else str(actor)
    upd: Dict[str, Any] = {"status": target, "updated_at": now, "review_note": (note or "")[:1000] or None,
                           "reviewed_by": who, "reviewed_at": now}
    if target == "approved":
        days = int(get_program_settings(db).get("deal_protection_days") or 90)
        upd["protected_until"] = now + timedelta(days=days)
    res = db[DEALS].update_one({"_id": d["_id"], "status": d["status"]},
                               {"$set": upd, "$push": {"history": {"status": target, "at": now, "by": who,
                                                                   "note": note}}})
    if not res.modified_count:
        raise HTTPException(status_code=409, detail="The deal changed meanwhile — reload and retry")
    paudit(f"partner.deal.{target}", d["partner_id"], actor=actor, ip=ip,
           details={"company": d.get("company"), "note": note}, resource_type="partner_deal", resource_id=deal_id)
    partner = db[K.PARTNERS].find_one({"_id": oid(d["partner_id"])})
    if partner:
        msg = {"approved": f"Your deal with {d['company']} is approved and protected until "
                           f"{upd.get('protected_until', now):%d %b %Y}.",
               "rejected": f"Your deal registration for {d['company']} was not approved: {note}",
               "won": f"Your deal with {d['company']} was marked as won.",
               "lost": f"Your deal with {d['company']} was closed as lost." + (f" {note}" if note else "")}[target]
        notify_partner(partner, "partner_deal", f"Deal {target}", msg,
                       severity="success" if target in ("approved", "won") else "warning",
                       link="/partner#/deals", email=target in ("approved", "rejected"))
    return deal_out(db[DEALS].find_one({"_id": d["_id"]}), admin=True)


def update_own_deal(partner: Dict[str, Any], deal_id: str, data: Dict[str, Any], *,
                    ip: Optional[str] = None) -> Dict[str, Any]:
    """Partners may add notes / adjust the expected plan & value, or close a
    deal as lost. Status changes beyond that are the Super Admin's."""
    db = db_or_503()
    d = _load(db, deal_id)
    if d["partner_id"] != str(partner["_id"]):
        raise HTTPException(status_code=404, detail="Deal not found")
    if d["status"] not in OPEN_DEAL:
        raise HTTPException(status_code=409, detail="This deal is closed")
    upd: Dict[str, Any] = {"updated_at": utcnow()}
    if "notes" in data:
        upd["notes"] = text(data.get("notes"), 2000)
    if "expected_value" in data:
        v = data.get("expected_value")
        upd["expected_value"] = money(v) if v not in (None, "") else None
    if "expected_plan" in data:
        plan = re.sub(r"[^a-z0-9_-]", "", str(data.get("expected_plan") or "").lower())[:40] or None
        if plan and not db.plans.find_one({"slug": plan, "status": "active"}, {"_id": 1}):
            raise HTTPException(status_code=422, detail="Unknown plan")
        upd["expected_plan"] = plan
    push = None
    if data.get("close_as_lost"):
        upd["status"] = "lost"
        push = {"history": {"status": "lost", "at": utcnow(), "by": partner["email"], "note": "closed by partner"}}
    db[DEALS].update_one({"_id": d["_id"]}, {"$set": upd, **({"$push": push} if push else {})})
    paudit("partner.deal.updated", str(partner["_id"]), actor=partner["email"], ip=ip,
           details={"fields": sorted(k for k in upd if k != "updated_at")}, resource_type="partner_deal",
           resource_id=deal_id)
    return deal_out(db[DEALS].find_one({"_id": d["_id"]}))


def on_referral_created(db, ref: Dict[str, Any]) -> None:
    """Link a registered deal to the signup, and flag claim conflicts."""
    try:
        email = (ref.get("email") or "").lower()
        if not email:
            return
        dom = _domain(email)
        q_open = {"status": {"$in": list(OPEN_DEAL)}}
        own = db[DEALS].find_one({**q_open, "partner_id": ref["partner_id"], "contact_email": email})
        if own:
            db[DEALS].update_one({"_id": own["_id"]}, {"$set": {"referral_id": str(ref["_id"]),
                                                                "organization_id": ref["organization_id"],
                                                                "updated_at": utcnow()},
                                                       "$push": {"history": {"status": own["status"], "at": utcnow(),
                                                                             "by": "system",
                                                                             "note": "prospect signed up"}}})
        for other in db[DEALS].find({**q_open, "partner_id": {"$ne": ref["partner_id"]},
                                     "protected_until": {"$gt": utcnow()}}).limit(5000):
            ce = (other.get("contact_email") or "").lower()
            if ce == email or (dom and dom not in _FREE and _domain(ce) == dom):
                from app.partners.fraud import raise_flag
                raise_flag("attribution_conflict", partner_id=ref["partner_id"], severity="medium",
                           subject_type="referral", subject_id=str(ref["_id"]), hold_referral_id=str(ref["_id"]),
                           details={"company": ref.get("company"), "signal": "prospect protected by another "
                                    "partner's approved deal", "deal_id": str(other["_id"]),
                                    "deal_partner_id": other["partner_id"]}, db=db)
                break
    except Exception:
        pass


def deal_stage(db, d: Dict[str, Any]) -> Optional[str]:
    if not d.get("referral_id"):
        return None
    ref = db[K.REFERRALS].find_one({"_id": oid(d["referral_id"])}, {"stage": 1})
    return (ref or {}).get("stage")


def list_query(status: Optional[str], q: Optional[str]) -> Dict[str, Any]:
    query: Dict[str, Any] = {}
    if status:
        query["status"] = status
    if q:
        rx = {"$regex": re.escape(q.strip()[:80]), "$options": "i"}
        query["$or"] = [{"company": rx}, {"contact_email": rx}, {"contact_name": rx}]
    return query


def expire_deals(db) -> int:
    """Approved deals past protection that never signed up -> expired."""
    now = utcnow()
    res = db[DEALS].update_many({"status": "approved", "protected_until": {"$lte": now}, "referral_id": None},
                                {"$set": {"status": "expired", "updated_at": now},
                                 "$push": {"history": {"status": "expired", "at": now, "by": "system"}}})
    return res.modified_count


