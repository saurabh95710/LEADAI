"""Referral tracking: click -> visitor -> signup -> demo -> customer.

* ``/r/{code}[/{campaign}]`` records a click and sets a signed attribution
  cookie holding the first-touch and last-touch partner (``leadai_ref``).
* At signup (``/api/auth/signup``) the cookie — or a typed ``ref`` code when
  the program allows it — attributes the new organization to ONE partner
  (unique ``partner_referrals.organization_id``), after self-referral checks.
* Demo approval and subscription confirmation advance the referral stage;
  commissions are generated in ``app.partners.commissions``.
"""
import json
import logging
import re
import secrets
import time
from typing import Any, Dict, Optional, Tuple

from datetime import timedelta

from fastapi import HTTPException

from app.db.models import utcnow
from app.db.mongo import get_sync_db
from app.partners import constants as K
from app.partners.service import (
    clean, db_or_503, digits, get_program_settings, ip_hash, normalize_email, oid, paudit, text,
)

logger = logging.getLogger(__name__)
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


# ── signed attribution cookie ────────────────────────────────────────────────

def encode_cookie(payload: Dict[str, Any]) -> str:
    from app.auth.service import _b64e, _sign
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    return f"{body}.{_sign(body)}"


def decode_cookie(value: Optional[str]) -> Optional[Dict[str, Any]]:
    import hmac
    from app.auth.service import _b64d, _sign
    if not value or "." not in value:
        return None
    body, sig = value.rsplit(".", 1)
    if not hmac.compare_digest(_sign(body), sig):
        return None
    try:
        data = json.loads(_b64d(body))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def pick_touch(cookie: Optional[Dict[str, Any]], settings: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The touch that wins under the configured model, if still in the window."""
    if not cookie:
        return None
    key = "lt" if settings.get("attribution_model") == "last_touch" else "ft"
    touch = cookie.get(key) or cookie.get("ft") or cookie.get("lt")
    if not isinstance(touch, dict) or not touch.get("p"):
        return None
    window = int(settings.get("attribution_window_days") or 30) * 86400
    if time.time() - float(touch.get("t") or 0) > window:
        return None
    return touch


# ── clicks ───────────────────────────────────────────────────────────────────

def find_active_partner_by_code(code: str, db) -> Optional[Dict[str, Any]]:
    code = (code or "").strip().upper()
    if not re.match(r"^[A-Z0-9_-]{3,40}$", code):
        return None
    p = db[K.PARTNERS].find_one({"referral_code": code})
    if not p or p.get("status") != K.P_ACTIVE:
        return None
    if K.LINKS_CREATE not in (p.get("permissions") or []):
        return None
    return p


def record_click(code: str, campaign_slug: Optional[str], *, ip: Optional[str],
                 user_agent: Optional[str], referer: Optional[str],
                 existing_cookie: Optional[str], visitor_id: Optional[str],
                 session_claims: Optional[Dict[str, Any]] = None) -> Tuple[str, Optional[str], str]:
    """Returns (redirect path, new attribution cookie or None, visitor id).
    Never raises for bad codes: the visitor always lands on the site."""
    db = get_sync_db()
    settings = get_program_settings(db)
    landing = settings.get("default_landing") or "/request-demo"
    vid = visitor_id if visitor_id and re.match(r"^[A-Za-z0-9_-]{16,64}$", visitor_id) else secrets.token_urlsafe(18)
    if db is None:
        return landing, None, vid
    partner = find_active_partner_by_code(code, db)
    if not partner:
        return landing, None, vid
    pid = str(partner["_id"])
    campaign = None
    if campaign_slug:
        campaign = db[K.CAMPAIGNS].find_one({"partner_id": pid, "slug": campaign_slug.lower(),
                                             "status": "active"})
        if campaign and campaign.get("landing_path"):
            landing = campaign["landing_path"]
    sep = "&" if "?" in landing else "?"
    redirect = f"{landing}{sep}ref={partner['referral_code']}"
    # a partner clicking their own link is neither a click nor an attribution
    if session_claims and str(session_claims.get("user_id") or "") == str(partner["user_id"]):
        return redirect, None, vid
    iph = ip_hash(ip)
    from app.auth.rate_limit import RateLimiter
    limit = int(settings.get("max_clicks_per_ip_per_hour") or 30)
    limiter = RateLimiter("partner_click_ip", max(1, limit), 3600)
    over_limit = bool(iph) and not limiter.consume(f"{pid}:{iph}")
    now = utcnow()
    recent = db[K.CLICKS].find_one({"partner_id": pid, "visitor_id": vid,
                                    "created_at": {"$gte": now - timedelta(days=1)}})
    click = {
        "partner_id": pid, "campaign_id": str(campaign["_id"]) if campaign else None,
        "referral_code": partner["referral_code"], "visitor_id": vid, "ip_hash": iph,
        "user_agent": (user_agent or "")[:200] or None,
        "referer": (referer or "")[:300] or None, "landing": landing,
        "unique": recent is None, "suspicious": over_limit, "converted": False,
        "created_at": now,
    }
    click_id = str(db[K.CLICKS].insert_one(click).inserted_id)
    if over_limit:
        # recorded for fraud review, but it never attributes anyone
        from app.partners.fraud import raise_flag
        raise_flag("click_flood", partner_id=pid, severity="medium", subject_type="partner", subject_id=pid,
                   details={"ip_hash": iph, "limit_per_hour": limit})
        return redirect, None, vid
    touch = {"p": pid, "c": click["campaign_id"], "k": click_id, "t": int(time.time())}
    if campaign and campaign.get("kind") == "onboarding":
        touch["o"] = 1  # reseller onboarding link: the signup becomes a managed customer
    current = decode_cookie(existing_cookie) or {}
    ft = current.get("ft")
    if not (isinstance(ft, dict) and pick_touch({"ft": ft}, {**settings, "attribution_model": "first_touch"})):
        ft = touch
    return redirect, encode_cookie({"ft": ft, "lt": touch}), vid


# ── attribution ──────────────────────────────────────────────────────────────

def _self_referral_reason(partner: Dict[str, Any], *, user_id: Optional[str], email: str,
                          phone: Optional[str]) -> Optional[str]:
    if user_id and str(partner.get("user_id")) == str(user_id):
        return "same_account"
    if normalize_email(partner.get("email", "")) == normalize_email(email):
        return "same_email"
    if phone and partner.get("phone") and len(digits(phone)) >= 6 and digits(phone)[-9:] == digits(partner["phone"])[-9:]:
        return "same_phone"
    return None


def _suspicious(partner: Dict[str, Any], signup_ip: Optional[str], db) -> bool:
    iph = ip_hash(signup_ip)
    if not iph:
        return False
    if partner.get("ip_hash") == iph:
        return True
    return db.user_sessions.find_one({"user_id": str(partner["user_id"]), "ip_hash": iph},
                                     {"_id": 1}) is not None


def create_referral(db, partner: Dict[str, Any], *, organization_id: str, user_id: Optional[str],
                    email: str, company: Optional[str], source: str, signup_ip: Optional[str],
                    phone: Optional[str] = None, touch: Optional[Dict[str, Any]] = None,
                    managed: bool = False, actor: Any = None) -> Optional[Dict[str, Any]]:
    """One referral per organization (unique index). Returns None when the
    organization is already attributed or the referral is a self-referral."""
    settings = get_program_settings(db)
    reason = _self_referral_reason(partner, user_id=user_id, email=email, phone=phone)
    if reason and settings.get("block_self_referral", True):
        from app.events.security import log_security_event
        log_security_event("partner_self_referral", "medium", actor_email=partner.get("email"),
                           organization_id=organization_id,
                           details={"partner_id": str(partner["_id"]), "reason": reason})
        paudit("partner.referral.blocked", str(partner["_id"]), actor=actor or "system",
               details={"organization_id": organization_id, "reason": reason}, success=False,
               resource_type="organization", resource_id=organization_id)
        from app.partners.fraud import raise_flag
        raise_flag("self_referral", partner_id=str(partner["_id"]), severity="high",
                   subject_type="organization", subject_id=str(organization_id),
                   details={"reason": reason, "company": company}, db=db)
        return None
    if db[K.REFERRALS].find_one({"organization_id": str(organization_id)}, {"_id": 1}):
        return None
    now = utcnow()
    org = db.organizations.find_one({"_id": oid(organization_id)}) or {}
    stage = K.STAGE_DEMO if org.get("status") in ("demo", "active") else K.STAGE_SIGNED_UP
    doc = {
        "partner_id": str(partner["_id"]), "organization_id": str(organization_id),
        "user_id": str(user_id) if user_id else None, "email": email.strip().lower(),
        "company": company, "source": source, "managed": managed,
        "campaign_id": (touch or {}).get("c"), "click_id": (touch or {}).get("k"),
        "attribution_model": settings.get("attribution_model"),
        "stage": stage, "status": "active",
        "suspicious": _suspicious(partner, signup_ip, db) or bool(reason),
        "self_referral_flag": reason,
        "signed_up_at": now, "demo_at": now if stage == K.STAGE_DEMO else None,
        "converted_at": None, "subscription_id": None, "plan_id": None,
        "revenue_total": 0.0, "commission_total": 0.0,
        "events": [{"stage": K.STAGE_SIGNED_UP, "at": now}] +
                  ([{"stage": K.STAGE_DEMO, "at": now}] if stage == K.STAGE_DEMO else []),
        "created_at": now, "updated_at": now,
    }
    try:
        doc["_id"] = db[K.REFERRALS].insert_one(doc).inserted_id
    except Exception:  # raced: another attribution won (unique organization_id)
        return None
    from app.partners.sales import on_referral_created
    on_referral_created(db, doc)
    from app.partners.fraud import raise_flag
    if doc["suspicious"]:
        raise_flag("suspicious_referral", partner_id=str(partner["_id"]), severity="medium",
                   subject_type="referral", subject_id=str(doc["_id"]), hold_referral_id=str(doc["_id"]),
                   details={"company": company, "signal": "signup from the partner's own IP / session"}, db=db)
    dup = _duplicate_of(db, doc)
    if dup:
        raise_flag("duplicate_customer", partner_id=str(partner["_id"]), severity="medium",
                   subject_type="referral", subject_id=str(doc["_id"]), hold_referral_id=str(doc["_id"]),
                   details={"company": company, "matches_referral": str(dup["_id"]),
                            "matches_company": dup.get("company")}, db=db)
    if doc["click_id"] and oid(doc["click_id"]):
        db[K.CLICKS].update_one({"_id": oid(doc["click_id"])},
                                {"$set": {"converted": True, "referral_id": str(doc["_id"])}})
    db.organizations.update_one({"_id": oid(organization_id)},
                                {"$set": {"referred_by_partner_id": str(partner["_id"]),
                                          "referral_id": str(doc["_id"])}})
    paudit("partner.referral.created", str(partner["_id"]), actor=actor or "system",
           details={"organization_id": organization_id, "source": source,
                    "suspicious": doc["suspicious"]},
           resource_type="partner_referral", resource_id=str(doc["_id"]))
    from app.partners.service import notify_partner
    notify_partner(partner, "partner_referral", "New referral signed up",
                   f"{company or 'A new customer'} signed up through your "
                   f"{'customer onboarding' if managed else 'referral'}.",
                   severity="success", link="/partner#/referrals")
    return doc


_FREE_DOMAINS = {"gmail.com", "googlemail.com", "yahoo.com", "outlook.com", "hotmail.com", "live.com",
                 "icloud.com", "aol.com", "proton.me", "protonmail.com", "gmx.com", "yandex.com", "rediffmail.com"}


def _company_key(name: Optional[str]) -> str:
    key = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    for suffix in ("privatelimited", "pvtltd", "limited", "ltd", "llc", "inc", "co", "company"):
        if key.endswith(suffix) and len(key) > len(suffix) + 2:
            key = key[: -len(suffix)]
    return key


def _duplicate_of(db, doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Another live referral of the same partner that looks like the same
    business (same company name, or same non-free email domain)."""
    domain = (doc.get("email") or "").partition("@")[2]
    clauses = []
    ck = _company_key(doc.get("company"))
    for other in db[K.REFERRALS].find({"partner_id": doc["partner_id"], "_id": {"$ne": doc["_id"]},
                                       "status": "active"}, {"company": 1, "email": 1}).limit(2000):
        if ck and len(ck) >= 3 and _company_key(other.get("company")) == ck:
            return other
        if domain and domain not in _FREE_DOMAINS and (other.get("email") or "").partition("@")[2] == domain:
            clauses.append(other)
    return clauses[0] if clauses else None


def attribute_signup(*, organization_id: str, user_id: Optional[str], email: str, company: Optional[str],
                     phone: Optional[str], ip: Optional[str], cookie_value: Optional[str],
                     ref_code: Optional[str]) -> Optional[Dict[str, Any]]:
    """Called right after a demo request is created. Best effort: never
    blocks a signup."""
    try:
        db = get_sync_db()
        if db is None:
            return None
        settings = get_program_settings(db)
        decoded = decode_cookie(cookie_value)
        if cookie_value and decoded is None:
            from app.partners.fraud import raise_flag
            raise_flag("attribution_tampering", partner_id=None, severity="medium",
                       subject_type="organization", subject_id=str(organization_id),
                       details={"company": company, "signal": "referral cookie with an invalid signature"}, db=db)
        touch = pick_touch(decoded, settings)
        partner, source, managed = None, "link", False
        if touch:
            partner = db[K.PARTNERS].find_one({"_id": oid(touch["p"]), "status": K.P_ACTIVE})
            if partner and touch.get("o"):
                from app.partners.service import effective_permissions
                if K.RESELLER_MANAGE in effective_permissions(partner) and not managed_limit_reached(db, partner):
                    source, managed = "onboarding_link", True
        if partner is None and ref_code and settings.get("allow_code_entry", True):
            partner = find_active_partner_by_code(ref_code, db)
            source, touch = "code", None
        if partner is None:
            return None
        if not user_id:
            org = db.organizations.find_one({"_id": oid(organization_id)}, {"owner_id": 1}) or {}
            user_id = org.get("owner_id")
        ref = create_referral(db, partner, organization_id=organization_id, user_id=user_id,
                              email=email, company=company, source=source, signup_ip=ip,
                              phone=phone, touch=touch, managed=managed)
        if ref and touch and ref_code:
            typed = find_active_partner_by_code(ref_code, db)
            if typed and str(typed["_id"]) != str(partner["_id"]):
                from app.partners.fraud import raise_flag
                raise_flag("attribution_conflict", partner_id=str(partner["_id"]), severity="low",
                           subject_type="referral", subject_id=str(ref["_id"]), hold_referral_id=str(ref["_id"]),
                           details={"cookie_partner": str(partner["_id"]), "typed_code_partner": str(typed["_id"]),
                                    "company": company}, db=db)
        return ref
    except Exception as e:
        logger.warning("partner attribution failed for org %s: %s", organization_id, e)
        return None


_STAGE_TIME = {K.STAGE_DEMO: "demo_at", K.STAGE_SUBSCRIPTION: "subscription_at",
               K.STAGE_PAYMENT: "payment_at", K.STAGE_CUSTOMER: "converted_at"}


def advance_stage(db, organization_id: str, stage: str, *, subscription_id: Optional[str] = None,
                  extra: Optional[Dict[str, Any]] = None) -> bool:
    """Move a referral forward in the funnel (never backwards) and record
    the conversion event. Idempotent: repeating a stage changes nothing."""
    ref = db[K.REFERRALS].find_one({"organization_id": str(organization_id), "status": "active"})
    if not ref:
        return False
    now = utcnow()
    current = K.STAGE_RANK.get(ref.get("stage"), 0)
    target = K.STAGE_RANK[stage]
    upd: Dict[str, Any] = {"updated_at": now, **(extra or {})}
    if subscription_id:
        upd["subscription_id"] = str(subscription_id)
    advanced = target > current
    if advanced:
        upd["stage"] = stage
    field = _STAGE_TIME.get(stage)
    if field and not ref.get(field):
        upd[field] = now
    seen = any(e.get("stage") == stage and (not subscription_id or e.get("subscription_id") == str(subscription_id))
               for e in ref.get("events") or [])
    op: Dict[str, Any] = {"$set": upd}
    if not seen:
        op["$push"] = {"events": {"stage": stage, "at": now, "subscription_id": str(subscription_id) if subscription_id else None}}
    db[K.REFERRALS].update_one({"_id": ref["_id"]}, op)
    return advanced


def _stage_hook(organization_id: str, stage: str, subscription_id: Optional[str], title: str, message: str) -> None:
    try:
        db = get_sync_db()
        if db is None:
            return
        if advance_stage(db, str(organization_id), stage, subscription_id=subscription_id):
            ref = db[K.REFERRALS].find_one({"organization_id": str(organization_id)})
            partner = db[K.PARTNERS].find_one({"_id": oid(ref["partner_id"])})
            if partner:
                from app.partners.service import notify_partner
                notify_partner(partner, "partner_referral", title,
                               message.format(company=ref.get("company") or "Your referral"),
                               severity="info", link="/partner#/referrals")
    except Exception as e:
        logger.warning("partner stage hook (%s) failed for org %s: %s", stage, organization_id, e)


def on_demo_approved(organization_id: str) -> None:
    """lifecycle.demo.approve hook -> stage ``demo``."""
    _stage_hook(organization_id, K.STAGE_DEMO, None, "Referral demo approved",
                "{company} now has an approved demo.")


def on_checkout_started(organization_id: str, subscription_id: str) -> None:
    """billing.start_checkout hook -> stage ``subscription``."""
    _stage_hook(organization_id, K.STAGE_SUBSCRIPTION, subscription_id, "Referral chose a plan",
                "{company} started a subscription checkout.")


def on_payment_verified(organization_id: str, subscription_id: str) -> None:
    """billing.record_payment_verified hook -> stage ``payment``."""
    _stage_hook(organization_id, K.STAGE_PAYMENT, subscription_id, "Referral payment received",
                "{company}'s payment was received and is awaiting confirmation.")


def managed_limit_reached(db, partner: Dict[str, Any]) -> bool:
    """Tier customer limit for reseller-managed customers (0 = unlimited)."""
    tier = db[K.TIERS].find_one({"_id": oid(partner.get("tier_id"))}) if partner.get("tier_id") else None
    limit = int((tier or {}).get("max_customers") or 0)
    if not limit:
        return False
    return db[K.REFERRALS].count_documents({"partner_id": str(partner["_id"]), "managed": True,
                                            "status": "active"}) >= limit


# ── reseller customer onboarding ─────────────────────────────────────────────

def create_reseller_customer(partner: Dict[str, Any], data: Dict[str, Any], *,
                             ip: Optional[str] = None) -> Dict[str, Any]:
    """A reseller onboards a customer through the EXISTING demo-request flow
    (organization + owner + pending demo); the owner sets their own password
    from an emailed link. The organization is attributed to the reseller."""
    db = db_or_503()
    from app.lifecycle.demo import create_demo_request
    email = (data.get("email") or "").strip().lower()
    name, company = text(data.get("name"), 120), text(data.get("company"), 160)
    if not name or not company:
        raise HTTPException(status_code=422, detail="Customer name and company are required")
    reason = _self_referral_reason(partner, user_id=None, email=email, phone=data.get("phone"))
    if reason:
        raise HTTPException(status_code=422, detail="You can't onboard yourself as a customer")
    if managed_limit_reached(db, partner):
        raise HTTPException(status_code=422, detail="You reached the customer limit of your partner tier")
    temp_password = "Tmp-" + secrets.token_urlsafe(18) + "9A"
    res = create_demo_request(name=name, email=email, password=temp_password, company=company,
                              phone=text(data.get("phone"), 30),
                              message=text(data.get("notes"), 2000), ip=ip,
                              source=f"partner:{partner['_id']}", industry=data.get("industry"),
                              requested_plan=data.get("plan"), accepted_terms=True,
                              owner_knows_password=False)
    org_id = res["organization_id"]
    owner = db.users.find_one({"email": email})
    ref = create_referral(db, partner, organization_id=org_id,
                          user_id=str(owner["_id"]) if owner else None, email=email,
                          company=company, source="reseller", signup_ip=None,
                          phone=data.get("phone"), managed=True, actor=partner["email"])
    if owner:
        _send_setup_link(db, owner, partner, trial_started=res.get("status") == "approved")
    paudit("partner.customer.created", str(partner["_id"]), actor=partner["email"], ip=ip,
           details={"organization_id": org_id, "company": company},
           resource_type="organization", resource_id=org_id)
    return {"organization_id": org_id, "demo_request_id": res["id"], "status": res["status"],
            "referral_id": str(ref["_id"]) if ref else None}


def _send_setup_link(db, owner: Dict[str, Any], partner: Dict[str, Any], *, trial_started: bool = False) -> None:
    """Single-use password link (the existing password-reset tokens, 7 days)."""
    import hashlib
    from app.events.email import absolute_url, send_email
    token = secrets.token_urlsafe(32)
    now = utcnow()
    db.password_resets.update_many({"user_id": str(owner["_id"]), "used_at": None},
                                   {"$set": {"used_at": now, "invalidated": True}})
    db.password_resets.insert_one({
        "user_id": str(owner["_id"]), "token_hash": hashlib.sha256(token.encode()).hexdigest(),
        "expires_at": now + timedelta(days=7), "used_at": None, "requested_ip": None,
        "created_at": now, "purpose": "partner_onboarding", "partner_id": str(partner["_id"])})
    send_email(owner["email"], "Set up your LeadAI account",
               f"Hi {owner.get('name') or ''},\n\n{partner.get('company') or partner.get('name')} "
               "created a LeadAI workspace for you. Choose your password here (valid 7 days, "
               f"single use):\n{absolute_url('/reset-password?token=' + token)}\n\n"
               + (_trial_line() if trial_started else "Your demo becomes usable once our team approves it.")
               + "\n\n— The LeadAI team",
               kind="partner_customer_setup")


def _trial_line() -> str:
    from app.lifecycle.config import get_demo_config
    cfg = get_demo_config()
    return (f"Your free {cfg.get('duration_days')}-day trial with {cfg.get('tokens')} tokens has already started — "
            "sign in as soon as you've chosen your password.")


def resend_setup_link(partner: Dict[str, Any], organization_id: str) -> None:
    db = db_or_503()
    ref = db[K.REFERRALS].find_one({"organization_id": str(organization_id),
                                    "partner_id": str(partner["_id"]), "managed": True})
    if not ref:
        raise HTTPException(status_code=404, detail="Customer not found")
    owner = db.users.find_one({"_id": oid(ref.get("user_id"))}) if ref.get("user_id") else None
    if not owner:
        raise HTTPException(status_code=404, detail="Customer owner not found")
    if owner.get("last_login"):
        raise HTTPException(status_code=409, detail="This customer has already signed in")
    org = db.organizations.find_one({"_id": oid(organization_id)}, {"status": 1}) or {}
    _send_setup_link(db, owner, partner, trial_started=org.get("status") == "demo")
    paudit("partner.customer.setup_resent", str(partner["_id"]), actor=partner["email"],
           details={"organization_id": str(organization_id)},
           resource_type="organization", resource_id=str(organization_id))


# ── campaigns ────────────────────────────────────────────────────────────────

def save_campaign(partner: Dict[str, Any], data: Dict[str, Any], *, campaign_id: Optional[str] = None,
                  ip: Optional[str] = None) -> Dict[str, Any]:
    db = db_or_503()
    pid = str(partner["_id"])
    name = text(data.get("name"), 80)
    landing = text(data.get("landing_path"), 200)
    if landing and (not landing.startswith("/") or landing.startswith("//")):
        raise HTTPException(status_code=422, detail="Landing page must be a site path like /pricing")
    status = data.get("status", "active")
    if status not in ("active", "archived"):
        raise HTTPException(status_code=422, detail="status must be active or archived")
    kind = data.get("kind") or "referral"
    if kind not in ("referral", "onboarding"):
        raise HTTPException(status_code=422, detail="kind must be referral or onboarding")
    if kind == "onboarding":
        from app.partners.service import effective_permissions
        if K.RESELLER_MANAGE not in effective_permissions(partner):
            raise HTTPException(status_code=403, detail="Onboarding links are available to resellers only")
        if not landing:
            landing = "/request-demo"
    now = utcnow()
    if campaign_id:
        c = db[K.CAMPAIGNS].find_one({"_id": oid(campaign_id), "partner_id": pid}) if oid(campaign_id) else None
        if not c:
            raise HTTPException(status_code=404, detail="Campaign not found")
        upd = {"updated_at": now, "status": status}
        if name:
            upd["name"] = name
        if "landing_path" in data:
            upd["landing_path"] = landing
        db[K.CAMPAIGNS].update_one({"_id": c["_id"]}, {"$set": upd})
        action = "partner.campaign.updated"
    else:
        if not name:
            raise HTTPException(status_code=422, detail="Campaign name is required")
        slug = (data.get("slug") or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"))[:40]
        if not _SLUG_RE.match(slug):
            raise HTTPException(status_code=422, detail="Slug may use lowercase letters, digits and dashes")
        if db[K.CAMPAIGNS].find_one({"partner_id": pid, "slug": slug}):
            raise HTTPException(status_code=409, detail="You already have a campaign with this slug")
        if db[K.CAMPAIGNS].count_documents({"partner_id": pid}) >= 100:
            raise HTTPException(status_code=422, detail="Campaign limit reached (100)")
        campaign_id = str(db[K.CAMPAIGNS].insert_one({
            "partner_id": pid, "name": name, "slug": slug, "landing_path": landing, "kind": kind,
            "status": status, "created_at": now, "updated_at": now}).inserted_id)
        action = "partner.campaign.created"
    paudit(action, pid, actor=partner["email"], ip=ip, details={"name": name},
           resource_type="partner_campaign", resource_id=campaign_id)
    return campaign_out(db[K.CAMPAIGNS].find_one({"_id": oid(campaign_id)}), partner)


def campaign_out(c: Dict[str, Any], partner: Dict[str, Any]) -> Dict[str, Any]:
    from app.partners.service import referral_url
    out = clean(c)
    out["url"] = referral_url(partner.get("referral_code"), c.get("slug"))
    return out


def referral_stats_for(db, partner_ids) -> Dict[str, Dict[str, Any]]:
    """Per-partner referral / customer / revenue / commission totals (lists)."""
    ids = [str(i) for i in partner_ids]
    out: Dict[str, Dict[str, Any]] = {i: {"referrals": 0, "customers": 0, "revenue": 0.0,
                                         "commission": 0.0} for i in ids}
    for row in db[K.REFERRALS].aggregate([
            {"$match": {"partner_id": {"$in": ids}}},
            {"$group": {"_id": "$partner_id", "n": {"$sum": 1},
                        "c": {"$sum": {"$cond": [{"$eq": ["$stage", K.STAGE_CUSTOMER]}, 1, 0]}},
                        "rev": {"$sum": "$revenue_total"}}}]):
        out[row["_id"]].update({"referrals": row["n"], "customers": row["c"],
                                "revenue": round(row["rev"] or 0, 2)})
    for row in db[K.COMMISSIONS].aggregate([
            {"$match": {"partner_id": {"$in": ids}, "status": {"$ne": K.C_REVERSED}}},
            {"$group": {"_id": "$partner_id", "amt": {"$sum": "$amount"}}}]):
        out[row["_id"]]["commission"] = round(row["amt"] or 0, 2)
    return out



# ── Super Admin relationship management ──────────────────────────────────────

def _load_referral(db, referral_id: str) -> Dict[str, Any]:
    ref = db[K.REFERRALS].find_one({"_id": oid(referral_id)}) if oid(referral_id) else None
    if not ref:
        raise HTTPException(status_code=404, detail="Referral not found")
    return ref


def reassign_referral(referral_id: str, new_partner_id: str, *, actor: Any, reason: str,
                      ip: Optional[str] = None) -> Dict[str, Any]:
    """Move a customer to another partner (attribution dispute). Only while
    nothing has been paid out; unpaid commissions move with the customer."""
    db = db_or_503()
    ref = _load_referral(db, referral_id)
    if not (reason or "").strip():
        raise HTTPException(status_code=422, detail="A reason is required")
    new = db[K.PARTNERS].find_one({"_id": oid(new_partner_id)}) if oid(new_partner_id) else None
    if not new or new.get("status") != K.P_ACTIVE:
        raise HTTPException(status_code=422, detail="The target partner must be an active partner")
    if str(new["_id"]) == ref["partner_id"]:
        raise HTTPException(status_code=409, detail="The customer already belongs to this partner")
    if db[K.COMMISSIONS].find_one({"referral_id": str(ref["_id"]),
                                   "status": {"$in": [K.C_PROCESSING, K.C_PAID]}}, {"_id": 1}):
        raise HTTPException(status_code=409, detail="Commissions of this customer are already in a payout or paid — reverse them first")
    if _self_referral_reason(new, user_id=ref.get("user_id"), email=ref.get("email") or "", phone=None):
        raise HTTPException(status_code=422, detail="That would be a self-referral")
    old_pid = ref["partner_id"]
    now = utcnow()
    db[K.REFERRALS].update_one({"_id": ref["_id"]}, {
        "$set": {"partner_id": str(new["_id"]), "source": "reassigned", "updated_at": now},
        "$push": {"events": {"stage": "reassigned", "at": now, "from": old_pid, "to": str(new["_id"]),
                             "reason": reason}}})
    moved = db[K.COMMISSIONS].update_many({"referral_id": str(ref["_id"]), "status": {"$in": list(K.UNPAID_STATUSES)}},
                                          {"$set": {"partner_id": str(new["_id"]), "updated_at": now}})
    db.organizations.update_one({"_id": oid(ref["organization_id"])},
                                {"$set": {"referred_by_partner_id": str(new["_id"])}})
    from app.partners.commissions import recompute_wallet
    recompute_wallet(old_pid, db)
    recompute_wallet(str(new["_id"]), db)
    for pid in (old_pid, str(new["_id"])):
        paudit("partner.referral.reassigned", pid, actor=actor, ip=ip,
               details={"referral_id": referral_id, "organization_id": ref["organization_id"], "from": old_pid,
                        "to": str(new["_id"]), "commissions_moved": moved.modified_count, "reason": reason},
               resource_type="partner_referral", resource_id=referral_id)
    return clean(db[K.REFERRALS].find_one({"_id": ref["_id"]}))


def review_referral(referral_id: str, decision: str, *, actor: Any, reason: str = "",
                    ip: Optional[str] = None) -> Dict[str, Any]:
    """Fraud review. ``clear``: the flag was a false positive — held
    commissions continue normally. ``invalidate``: the attribution is not
    legitimate — the referral stops earning and every commission is reversed
    (paid ones clawed back)."""
    db = db_or_503()
    ref = _load_referral(db, referral_id)
    now = utcnow()
    if decision == "clear":
        db[K.REFERRALS].update_one({"_id": ref["_id"]}, {"$set": {"suspicious": False, "reviewed_at": now,
                                                                  "review_note": reason, "updated_at": now}})
        db[K.COMMISSIONS].update_many({"referral_id": str(ref["_id"]), "requires_manual_approval": True},
                                      {"$set": {"requires_manual_approval": False, "updated_at": now}})
    elif decision == "invalidate":
        if not (reason or "").strip():
            raise HTTPException(status_code=422, detail="A reason is required")
        from app.partners.commissions import _reduce, recompute_wallet
        for c in db[K.COMMISSIONS].find({"referral_id": str(ref["_id"]), "kind": "commission",
                                         "status": {"$ne": K.C_REVERSED}}):
            left = float(c["amount"]) - (float(c.get("clawed_back") or 0) if c["status"] == K.C_PAID else 0)
            if left > 0:
                _reduce(db, c, left, actor=actor, reason=f"referral invalidated: {reason}", source="fraud")
        db[K.REFERRALS].update_one({"_id": ref["_id"]}, {
            "$set": {"status": "invalid", "suspicious": True, "reviewed_at": now, "review_note": reason,
                     "updated_at": now},
            "$push": {"events": {"stage": "invalidated", "at": now, "reason": reason}}})
        db.organizations.update_one({"_id": oid(ref["organization_id"])},
                                    {"$unset": {"referred_by_partner_id": "", "referral_id": ""}})
        recompute_wallet(ref["partner_id"], db)
        from app.partners.service import notify_partner
        partner = db[K.PARTNERS].find_one({"_id": oid(ref["partner_id"])})
        if partner:
            notify_partner(partner, "partner_referral", "Referral invalidated",
                           f"The referral of {ref.get('company') or 'a customer'} was invalidated after review: {reason}",
                           severity="warning", link="/partner#/referrals", email=True)
    else:
        raise HTTPException(status_code=422, detail="decision must be clear or invalidate")
    # the referral review settles its open fraud flags too
    who = actor.get("email") if isinstance(actor, dict) else getattr(actor, "email", None) or str(actor)
    db["partner_fraud_flags"].update_many(
        {"referral_id": str(ref["_id"]), "status": "open"},
        {"$set": {"status": "dismissed" if decision == "clear" else "confirmed",
                  "resolution": f"referral_{decision}", "resolved_at": now, "resolved_by": who,
                  "note": (reason or "")[:500]}})
    paudit(f"partner.referral.review_{decision}", ref["partner_id"], actor=actor, ip=ip,
           details={"referral_id": referral_id, "organization_id": ref["organization_id"], "reason": reason},
           resource_type="partner_referral", resource_id=referral_id)
    return clean(db[K.REFERRALS].find_one({"_id": ref["_id"]}))
