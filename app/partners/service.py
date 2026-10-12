"""Partner program core: settings, tiers, commission rules, applications,
partner records, permissions, audit and notifications.

All functions are synchronous (pymongo) so they can be called from the
sync lifecycle hooks (demo approval, renewals) as well as from route
handlers (FastAPI runs plain ``def`` handlers in its threadpool).
"""
import hashlib
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from bson import ObjectId
from fastapi import HTTPException

from app.admin.audit import audit
from app.db.models import utcnow
from app.db.mongo import get_sync_db
from app.partners import constants as K

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_URL_RE = re.compile(r"^https?://[^\s<>\"']{3,300}$", re.I)
_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{3,29}$")


# ── helpers ──────────────────────────────────────────────────────────────────

def db_or_503(db=None):
    db = db if db is not None else get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return db


def oid(value: Any) -> Optional[ObjectId]:
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def money(v: Any) -> float:
    try:
        return round(float(v or 0) + 0.0, 2)
    except (TypeError, ValueError):
        return 0.0


def ip_hash(ip: Optional[str]) -> Optional[str]:
    """Same truncated SHA-256 the session store uses (user_sessions.ip_hash)."""
    if not ip or ip == "unknown":
        return None
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()[:16]


def clean(d: Any) -> Any:
    """JSON-safe copy: ObjectId -> str, datetime -> ISO, ``_id`` -> ``id``."""
    if isinstance(d, ObjectId):
        return str(d)
    if isinstance(d, dict):
        return {("id" if k == "_id" else k): clean(v) for k, v in d.items()}
    if isinstance(d, list):
        return [clean(v) for v in d]
    if isinstance(d, datetime) and d.tzinfo is None:
        # MongoDB returns naive UTC datetimes: say so, or browsers read them as local time
        return d.replace(tzinfo=timezone.utc).isoformat()
    if hasattr(d, "isoformat"):
        return d.isoformat()
    return d


def text(v: Any, limit: int = 200) -> Optional[str]:
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(v or "")).strip()
    return s[:limit] or None


def normalize_email(email: str) -> str:
    """Canonical mailbox for self-referral checks (gmail dots / +tags)."""
    email = (email or "").strip().lower()
    if "@" not in email:
        return email
    local, domain = email.split("@", 1)
    local = local.split("+", 1)[0]
    if domain in ("gmail.com", "googlemail.com"):
        local = local.replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"


def digits(v: Any) -> str:
    return re.sub(r"\D", "", str(v or ""))


def mask(value: Any, keep: int = 4) -> Optional[str]:
    s = str(value or "")
    if not s:
        return None
    return ("•" * max(0, min(len(s) - keep, 8))) + s[-keep:] if len(s) > keep else "•" * len(s)


def _e164(phone: Any) -> Optional[str]:
    from app.messaging.phone import normalize_phone
    return normalize_phone(phone)


def paudit(action: str, partner_id: Optional[str], *, actor: Any = None,
           ip: Optional[str] = None, details: Optional[Dict[str, Any]] = None,
           resource_type: str = "partner", resource_id: Optional[str] = None,
           success: bool = True) -> None:
    """Partner audit trail = the platform audit log (category "partner").
    Every entry carries ``details.partner_id`` so a partner's history is one query."""
    d = dict(details or {})
    if partner_id:
        d.setdefault("partner_id", str(partner_id))
    # a Super Admin acting as the partner ("view as partner") stays attributable
    from app.admin.audit import _request_actor
    bound = (_request_actor.get() or {}).get("user") or {}
    if isinstance(bound, dict) and bound.get("impersonated_by") and bound.get("scope") == "partner":
        d.setdefault("impersonated_by", bound["impersonated_by"])
        if not isinstance(actor, dict) or not actor.get("impersonated_by"):
            actor = {"email": f"{bound['impersonated_by']} (as {bound.get('email')})", "role": "super_admin",
                     "impersonated_by": bound["impersonated_by"]}
    audit(action, "partner", user=actor, ip=ip, success=success, details=d,
          resource_type=resource_type, resource_id=resource_id or partner_id)
    # the same event in the partner activity feed (Super Admin → Partners → Activity)
    from app.partners.activity import record
    who = actor.get("email") if isinstance(actor, dict) else getattr(actor, "email", None) or (str(actor) if actor else None)
    record("action", action, partner_id=partner_id, actor=who, ip=ip, success=success,
           details={k: v for k, v in d.items() if k != "partner_id"} | {"resource_type": resource_type,
                                                                        "resource_id": resource_id})


def notify_partner(partner: Dict[str, Any], ntype: str, title: str, message: str = "",
                   *, link: str = "/partner#/dashboard", severity: str = "info",
                   email: bool = False, data: Optional[Dict[str, Any]] = None) -> None:
    """In-app notification (existing notifications collection, audience=user)
    plus an optional email, honouring the partner's notification settings."""
    try:
        from app.events.notifications import notify_user
        user_id = str(partner.get("user_id") or "")
        if user_id:
            notify_user(user_id, ntype, title, message, link=link, severity=severity,
                        data={**(data or {}), "partner_id": str(partner.get("_id") or "")})
        prefs = (partner.get("settings") or {}).get("email_notifications", True)
        if email and prefs and partner.get("email"):
            from app.events.email import absolute_url, send_email
            send_email(partner["email"], title,
                       f"Hi {partner.get('name') or ''},\n\n{message}\n\n"
                       f"Open your Partner Portal: {absolute_url(link)}\n\n— The LeadAI team",
                       kind=ntype if ntype.startswith("partner_") else f"partner_{ntype}")
    except Exception as e:  # notifications are best effort
        logger.warning("partner notification failed: %s", e)


# ── program settings ─────────────────────────────────────────────────────────

def get_program_settings(db=None) -> Dict[str, Any]:
    db = db if db is not None else get_sync_db()
    stored: Dict[str, Any] = {}
    if db is not None:
        stored = db[K.SETTINGS].find_one({"_id": "program"}) or {}
    out = dict(K.DEFAULT_SETTINGS)
    out.update({k: v for k, v in stored.items() if k in K.DEFAULT_SETTINGS})
    return out


def validate_settings(values: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, val in (values or {}).items():
        if key not in K.DEFAULT_SETTINGS:
            continue
        default: Any = K.DEFAULT_SETTINGS[key]
        if key == "attribution_model":
            if val not in K.ATTRIBUTION_MODELS:
                raise HTTPException(status_code=422, detail="attribution_model must be first_touch or last_touch")
            out[key] = val
        elif key == "attribution_window_days":
            n = int(val)
            if not 1 <= n <= 365:
                raise HTTPException(status_code=422, detail="Attribution window must be 1–365 days")
            out[key] = n
        elif key in ("deal_protection_days", "activity_retention_days"):
            n = int(val)
            if not 7 <= n <= 3650:
                raise HTTPException(status_code=422, detail=f"{key} must be 7-3650 days")
            out[key] = n
        elif key in ("commission_hold_days", "max_clicks_per_ip_per_hour"):
            n = int(val)
            if not 0 <= n <= 10000:
                raise HTTPException(status_code=422, detail=f"{key} is out of range")
            out[key] = n
        elif key in ("min_payout", "max_partner_coupon_percent"):
            f = float(val)
            if f < 0 or (key == "max_partner_coupon_percent" and f > 100):
                raise HTTPException(status_code=422, detail=f"{key} is out of range")
            out[key] = round(f, 2)
        elif key == "payout_methods":
            methods = [re.sub(r"[^a-z0-9_]", "", str(m).lower())[:30] for m in (val or [])]
            out[key] = [m for m in methods if m] or list(default)
        elif key == "payout_schedule":
            if val not in K.PAYOUT_SCHEDULES:
                raise HTTPException(status_code=422, detail="payout_schedule must be on_request, weekly or monthly")
            out[key] = val
        elif key == "payout_day":
            n = int(val)
            if not 0 <= n <= 28:
                raise HTTPException(status_code=422, detail="payout_day must be 0-6 (weekly) or 1-28 (monthly)")
            out[key] = n
        elif key == "default_landing":
            path = str(val or "").strip()
            if not path.startswith("/") or path.startswith("//"):
                raise HTTPException(status_code=422, detail="Landing must be a site path like /request-demo")
            out[key] = path[:200]
        elif isinstance(default, bool):
            out[key] = bool(val)
        else:
            out[key] = text(val, 40) or default
    return out


def update_program_settings(values: Dict[str, Any], *, actor: Any, ip: Optional[str] = None) -> Dict[str, Any]:
    db = db_or_503()
    clean_values = validate_settings(values)
    if clean_values:
        db[K.SETTINGS].update_one({"_id": "program"},
                                  {"$set": {**clean_values, "updated_at": utcnow(),
                                            "updated_by": getattr(actor, "email", None) or str(actor)}},
                                  upsert=True)
        paudit("partner.settings.updated", None, actor=actor, ip=ip,
               details={"changed": clean_values}, resource_type="partner_settings",
               resource_id="program")
    return get_program_settings(db)


# ── tiers ────────────────────────────────────────────────────────────────────

def ensure_program_defaults(db=None) -> None:
    """Seed the default tier and the global commission rule (idempotent)."""
    db = db if db is not None else get_sync_db()
    if db is None:
        return
    now = utcnow()
    if db[K.TIERS].count_documents({}) == 0:
        db[K.TIERS].insert_one({"slug": "standard", "name": "Standard", "order": 1,
                                "description": "Default tier for new partners.",
                                "benefits": [], "is_default": True, "status": "active",
                                "created_at": now, "updated_at": now})
    if db[K.RULES].count_documents({"scope": "global"}) == 0:
        db[K.RULES].insert_one({
            "name": "Default commission", "scope": "global", "tier_id": None, "partner_id": None,
            "commission_type": "percentage", "value": 20.0, "recurring": True,
            "duration_months": 12, "cap_amount": None, "plan_slugs": [], "status": "active",
            "created_at": now, "updated_at": now, "created_by": "system"})
    migrate_commission_model(db)
    grant_new_default_permissions(db)


def grant_new_default_permissions(db) -> None:
    """Once: partners approved before the sales kit existed get the two new
    default permissions (a Super Admin can still remove them)."""
    if db[K.SETTINGS].find_one({"_id": "program", "sales_permissions_v": 1}):
        return
    db[K.PARTNERS].update_many({}, {"$addToSet": {"permissions": {"$each": [K.SALES_VIEW, K.DEALS_MANAGE]}}})
    db[K.SETTINGS].update_one({"_id": "program"}, {"$set": {"sales_permissions_v": 1}}, upsert=True)


def migrate_commission_model(db) -> None:
    """v1 -> v2 statuses, once: v1 "approved" meant available (now
    "payable") or, inside an open payout, reserved (now "processing")."""
    if db[K.SETTINGS].find_one({"_id": "program", "commission_model_v": 2}):
        return
    db[K.COMMISSIONS].update_many({"status": "approved", "payout_id": None},
                                  {"$set": {"status": K.C_PAYABLE}})
    db[K.COMMISSIONS].update_many({"status": "approved", "payout_id": {"$ne": None}},
                                  {"$set": {"status": K.C_PROCESSING}})
    db[K.SETTINGS].update_one({"_id": "program"}, {"$set": {"commission_model_v": 2}}, upsert=True)


def default_tier_id(db) -> Optional[str]:
    t = db[K.TIERS].find_one({"is_default": True, "status": "active"}) or \
        db[K.TIERS].find_one({"status": "active"}, sort=[("order", 1)])
    return str(t["_id"]) if t else None


def save_tier(data: Dict[str, Any], *, tier_id: Optional[str] = None, actor: Any,
              ip: Optional[str] = None) -> Dict[str, Any]:
    db = db_or_503()
    name = text(data.get("name"), 60)
    if not name:
        raise HTTPException(status_code=422, detail="Tier name is required")
    req = data.get("requirements") or {}
    if not isinstance(req, dict):
        raise HTTPException(status_code=422, detail="requirements must be an object")

    def nonneg(v, f=int):
        try:
            n = f(v or 0)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="Tier numbers must be numeric") from None
        if n < 0:
            raise HTTPException(status_code=422, detail="Tier numbers cannot be negative")
        return n
    max_pct = data.get("max_coupon_percent")
    doc = {"name": name, "description": text(data.get("description"), 500),
           "order": int(data.get("order") or 1),
           "benefits": [text(b, 120) for b in (data.get("benefits") or []) if text(b, 120)][:20],
           "status": "active" if data.get("status", "active") == "active" else "inactive",
           # automatic tiering: every requirement that is set must be met (0 = not required)
           "requirements": {"min_customers": nonneg(req.get("min_customers")),
                            "min_referrals": nonneg(req.get("min_referrals")),
                            "min_revenue": round(nonneg(req.get("min_revenue"), float), 2),
                            "period_days": nonneg(req.get("period_days"))},
           "max_customers": nonneg(data.get("max_customers")),          # reseller-managed, 0 = unlimited
           "can_create_coupons": bool(data.get("can_create_coupons", True)),
           "max_coupon_percent": (min(100.0, nonneg(max_pct, float)) if max_pct not in (None, "") else None),
           "auto_assign": bool(data.get("auto_assign", True)),
           "updated_at": utcnow()}
    if data.get("is_default"):
        db[K.TIERS].update_many({}, {"$set": {"is_default": False}})
        doc["is_default"] = True
    if tier_id:
        res = db[K.TIERS].update_one({"_id": oid(tier_id)}, {"$set": doc})
        if not res.matched_count:
            raise HTTPException(status_code=404, detail="Tier not found")
        action = "partner.tier.updated"
    else:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "tier"
        if db[K.TIERS].find_one({"slug": slug}):
            raise HTTPException(status_code=409, detail="A tier with this name already exists")
        doc.update({"slug": slug, "created_at": utcnow(), "is_default": bool(doc.get("is_default"))})
        tier_id = str(db[K.TIERS].insert_one(doc).inserted_id)
        action = "partner.tier.created"
    paudit(action, None, actor=actor, ip=ip, details={"name": name},
           resource_type="partner_tier", resource_id=tier_id)
    return clean(db[K.TIERS].find_one({"_id": oid(tier_id)}))


def list_tiers(db=None) -> List[Dict[str, Any]]:
    db = db_or_503(db)
    return [clean(t) for t in db[K.TIERS].find().sort("order", 1)]


# ── commission rules ─────────────────────────────────────────────────────────

def validate_rule(data: Dict[str, Any], db) -> Dict[str, Any]:
    ctype = data.get("commission_type", "percentage")
    if ctype not in ("percentage", "fixed", "hybrid"):
        raise HTTPException(status_code=422, detail="commission_type must be percentage, fixed or hybrid")
    value = float(data.get("value") or 0)
    fixed_amount = round(float(data.get("fixed_amount") or 0), 2)
    if ctype == "hybrid":
        # hybrid = percentage of the payment + a fixed amount per payment
        if value < 0 or value > 100 or fixed_amount < 0 or (value == 0 and fixed_amount == 0):
            raise HTTPException(status_code=422, detail="Hybrid needs a percentage (0-100) and/or a fixed amount")
    elif value <= 0 or (ctype == "percentage" and value > 100):
        raise HTTPException(status_code=422, detail="Commission value is out of range")
    scope = data.get("scope", "global")
    if scope not in ("global", "tier", "partner"):
        raise HTTPException(status_code=422, detail="scope must be global, tier or partner")
    tier_id = partner_id = None
    if scope == "tier":
        tier_id = str(data.get("tier_id") or "")
        if not oid(tier_id) or not db[K.TIERS].find_one({"_id": oid(tier_id)}):
            raise HTTPException(status_code=422, detail="Unknown tier")
    if scope == "partner":
        partner_id = str(data.get("partner_id") or "")
        if not oid(partner_id) or not db[K.PARTNERS].find_one({"_id": oid(partner_id)}):
            raise HTTPException(status_code=422, detail="Unknown partner")
    duration = int(data.get("duration_months") or 0)
    if duration < 0 or duration > 120:
        raise HTTPException(status_code=422, detail="duration_months must be 0–120 (0 = lifetime)")
    cap = data.get("cap_amount")
    cap = round(float(cap), 2) if cap not in (None, "", 0, "0") else None
    if cap is not None and cap <= 0:
        raise HTTPException(status_code=422, detail="cap_amount must be positive")
    per_payment = data.get("max_per_payment")
    per_payment = round(float(per_payment), 2) if per_payment not in (None, "", 0, "0") else None
    if per_payment is not None and per_payment <= 0:
        raise HTTPException(status_code=422, detail="max_per_payment must be positive")
    qdays = data.get("qualification_days")
    qdays = int(qdays) if qdays not in (None, "") else None
    if qdays is not None and not 0 <= qdays <= 365:
        raise HTTPException(status_code=422, detail="qualification_days must be 0-365")
    return {
        "name": text(data.get("name"), 80) or "Commission rule", "scope": scope,
        "tier_id": tier_id, "partner_id": partner_id, "commission_type": ctype,
        "value": round(value, 2), "fixed_amount": fixed_amount if ctype == "hybrid" else 0.0,
        "recurring": bool(data.get("recurring", True)),
        "duration_months": duration, "cap_amount": cap, "max_per_payment": per_payment,
        "qualification_days": qdays,
        "plan_slugs": [re.sub(r"[^a-z0-9_-]", "", str(s).lower())[:40]
                       for s in (data.get("plan_slugs") or []) if str(s).strip()],
        "status": "active" if data.get("status", "active") == "active" else "inactive",
    }


def save_rule(data: Dict[str, Any], *, rule_id: Optional[str] = None, actor: Any,
              ip: Optional[str] = None) -> Dict[str, Any]:
    db = db_or_503()
    doc = validate_rule(data, db)
    doc["updated_at"] = utcnow()
    if rule_id:
        before = db[K.RULES].find_one({"_id": oid(rule_id)})
        if not before:
            raise HTTPException(status_code=404, detail="Commission rule not found")
        db[K.RULES].update_one({"_id": before["_id"]}, {"$set": doc})
        action = "partner.commission_rule.updated"
    else:
        doc.update({"created_at": utcnow(), "created_by": getattr(actor, "email", None) or str(actor)})
        rule_id = str(db[K.RULES].insert_one(doc).inserted_id)
        action = "partner.commission_rule.created"
    paudit(action, doc.get("partner_id"), actor=actor, ip=ip,
           details={k: doc[k] for k in ("name", "scope", "commission_type", "value", "fixed_amount",
                                        "recurring", "duration_months", "cap_amount",
                                        "max_per_payment", "qualification_days", "status")},
           resource_type="partner_commission_rule", resource_id=rule_id)
    return clean(db[K.RULES].find_one({"_id": oid(rule_id)}))


def list_rules(db=None) -> List[Dict[str, Any]]:
    db = db_or_503(db)
    return [clean(r) for r in db[K.RULES].find().sort([("scope", 1), ("created_at", 1)])]


def _rule_matches_plan(rule: Dict[str, Any], plan_slug: Optional[str]) -> bool:
    return not rule.get("plan_slugs") or (plan_slug or "") in rule["plan_slugs"]


def resolve_rule(partner: Dict[str, Any], plan_slug: Optional[str], db=None) -> Optional[Dict[str, Any]]:
    """Most specific active rule: partner override > tier > global."""
    db = db if db is not None else get_sync_db()
    pid = str(partner["_id"])
    candidates = [{"scope": "partner", "partner_id": pid}]
    if partner.get("tier_id"):
        candidates.append({"scope": "tier", "tier_id": str(partner["tier_id"])})
    candidates.append({"scope": "global"})
    for q in candidates:
        for rule in db[K.RULES].find({**q, "status": "active"}).sort("created_at", -1):
            if _rule_matches_plan(rule, plan_slug):
                return rule
    return None


# ── permissions ──────────────────────────────────────────────────────────────

def effective_permissions(partner: Optional[Dict[str, Any]]) -> List[str]:
    """What a partner may do right now: nothing unless active; the stored
    grant (Super Admin controlled) minus reseller-only rights for affiliates."""
    if not partner or partner.get("status") != K.P_ACTIVE:
        return []
    perms = set(partner.get("permissions") or []) & K.ALL_PERMISSIONS
    if partner.get("partner_type") != K.RESELLER:
        perms -= K.RESELLER_ONLY
    return sorted(perms)


def clean_permissions(perms: Any) -> List[str]:
    if not isinstance(perms, list):
        raise HTTPException(status_code=422, detail="permissions must be a list")
    unknown = [p for p in perms if p not in K.ALL_PERMISSIONS]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown permission(s): {', '.join(map(str, unknown))}")
    return sorted(set(perms))


# ── serialization ────────────────────────────────────────────────────────────

_SENSITIVE_PAYOUT = ("account_number", "iban", "routing_number", "swift", "ifsc", "upi_id",
                     "paypal_email")


def masked_payout(info: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not info:
        return None
    out = {}
    for k, v in info.items():
        out[k] = mask(v) if k in _SENSITIVE_PAYOUT else v
    return out


def masked_tax(info: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not info:
        return None
    return {k: (mask(v) if k == "tax_id" else v) for k, v in info.items()}


def public_partner(p: Dict[str, Any], *, admin: bool = False) -> Dict[str, Any]:
    """Partner record for an API response. Sensitive payout/tax fields are
    masked unless ``admin`` (Super Admin processes payouts)."""
    out = clean({k: v for k, v in p.items() if k not in ("payout_info", "tax_info", "ip_hash")})
    out["payout_info"] = clean(p.get("payout_info")) if admin else masked_payout(p.get("payout_info"))
    out["tax_info"] = clean(p.get("tax_info")) if admin else masked_tax(p.get("tax_info"))
    out["has_payout_info"] = bool(p.get("payout_info"))
    out["effective_permissions"] = effective_permissions(p)
    out["referral_url"] = referral_url(p.get("referral_code"))
    return out


def referral_url(code: Optional[str], campaign_slug: Optional[str] = None) -> Optional[str]:
    if not code:
        return None
    from app.events.email import absolute_url
    return absolute_url(f"/r/{code}" + (f"/{campaign_slug}" if campaign_slug else ""))


def public_application(a: Dict[str, Any], *, admin: bool = False) -> Dict[str, Any]:
    out = clean({k: v for k, v in a.items() if k not in ("payout_info", "tax_info", "ip_hash")})
    out["payout_info"] = clean(a.get("payout_info")) if admin else masked_payout(a.get("payout_info"))
    out["tax_info"] = clean(a.get("tax_info")) if admin else masked_tax(a.get("tax_info"))
    return out


# ── application form ─────────────────────────────────────────────────────────

def _clean_socials(raw: Any) -> Dict[str, str]:
    out: Dict[str, str] = {}
    items: Iterable[Tuple[Any, Any]]
    if isinstance(raw, dict):
        items = raw.items()
    elif isinstance(raw, list):
        items = ((f"link{i + 1}", v) for i, v in enumerate(raw))
    else:
        items = ()
    for k, v in list(items)[:10]:
        url = str(v or "").strip()
        if url:
            if not _URL_RE.match(url):
                raise HTTPException(status_code=422, detail=f"Social profile '{k}' must be an http(s) URL")
            out[re.sub(r"[^a-z0-9_]", "", str(k).lower())[:20] or "link"] = url
    return out


def clean_payout_info(raw: Any, db=None) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    if not isinstance(raw, dict):
        raise HTTPException(status_code=422, detail="payout_info must be an object")
    methods = get_program_settings(db).get("payout_methods") or []
    method = str(raw.get("method") or "").strip().lower()
    if method not in methods:
        raise HTTPException(status_code=422, detail=f"Payout method must be one of: {', '.join(methods)}")
    out: Dict[str, Any] = {"method": method}
    for k in ("account_name", "bank_name", "account_number", "iban", "routing_number", "swift",
              "ifsc", "upi_id", "paypal_email", "currency", "notes"):
        v = text(raw.get(k), 120)
        if v:
            out[k] = v
    if method == "paypal" and not _EMAIL_RE.match(out.get("paypal_email", "")):
        raise HTTPException(status_code=422, detail="A valid PayPal email is required")
    if method == "upi" and not out.get("upi_id"):
        raise HTTPException(status_code=422, detail="A UPI ID is required")
    if method == "bank_transfer" and not (out.get("account_number") or out.get("iban")):
        raise HTTPException(status_code=422, detail="An account number or IBAN is required")
    return out


def clean_tax_info(raw: Any) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    if not isinstance(raw, dict):
        raise HTTPException(status_code=422, detail="tax_info must be an object")
    out = {k: text(raw.get(k), 120) for k in ("legal_name", "tax_id", "tax_country", "gst_vat_number")}
    out = {k: v for k, v in out.items() if v}
    return out or None


def clean_application_fields(data: Dict[str, Any], db=None) -> Dict[str, Any]:
    name = text(data.get("name"), 120)
    if not name:
        raise HTTPException(status_code=422, detail="Name is required")
    ptype = data.get("partner_type") or K.AFFILIATE
    if ptype not in K.PARTNER_TYPES:
        raise HTTPException(status_code=422, detail="partner_type must be affiliate or reseller")
    phone = text(data.get("phone"), 30)
    if phone and not re.match(r"^[+()\-.\s\d]{6,30}$", phone):
        raise HTTPException(status_code=422, detail="Phone may contain digits, spaces and + ( ) - only")
    website = text(data.get("website"), 300)
    if website and not _URL_RE.match(website):
        raise HTTPException(status_code=422, detail="Website must be an http(s) URL")
    return {
        "name": name, "company": text(data.get("company"), 160), "phone": phone,
        "country": text(data.get("country"), 80), "city": text(data.get("city"), 80),
        "website": website, "business_type": text(data.get("business_type"), 80),
        "partner_type": ptype, "experience": text(data.get("experience"), 2000),
        "promotion_plan": text(data.get("promotion_plan"), 3000),
        "social_profiles": _clean_socials(data.get("social_profiles")),
        "tax_info": clean_tax_info(data.get("tax_info")),
        "payout_info": clean_payout_info(data.get("payout_info"), db),
    }


def submit_application(data: Dict[str, Any], *, ip: Optional[str] = None) -> Dict[str, Any]:
    """Public application. Creates the applicant's ``users`` account (or links
    an existing one after verifying its password — never a duplicate account)."""
    db = db_or_503()
    settings = get_program_settings(db)
    if not settings.get("applications_open"):
        raise HTTPException(status_code=403, detail="Partner applications are currently closed")
    email = (data.get("email") or "").strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="A valid email address is required")
    if not data.get("accepted_terms"):
        raise HTTPException(status_code=422, detail="You must accept the Partner Program terms")
    fields = clean_application_fields(data, db)
    password = data.get("password") or ""
    now = utcnow()

    user = db.users.find_one({"email": email})
    if user:
        from app.auth.service import _verify_and_migrate_password
        ok, _ = _verify_and_migrate_password(password, (user.get("password_hash") or "").strip())
        if not ok:
            raise HTTPException(status_code=409, detail={
                "code": "account_exists",
                "message": "An account with this email already exists. Enter your current "
                           "LeadAI password to apply with that account."})
        if user.get("is_platform_admin"):
            raise HTTPException(status_code=409, detail="Platform staff accounts cannot become partners")
        if user.get("status") in ("suspended", "disabled", "deactivated"):
            raise HTTPException(status_code=403, detail="This account is suspended")
        user_id = str(user["_id"])
    else:
        from app.auth.crypto import hash_password
        from app.lifecycle.demo import validate_password
        validate_password(password)
        user_id = str(db.users.insert_one({
            "email": email, "name": fields["name"], "phone": fields["phone"],
            "phone_e164": _e164(fields["phone"]),
            "password_hash": hash_password(password), "status": "active",
            "account_type": "partner", "is_platform_admin": False, "platform_role": None,
            "default_organization_id": None, "email_verified": False,
            "created_at": now, "updated_at": now, "last_login": None,
        }).inserted_id)

    if db[K.PARTNERS].find_one({"user_id": user_id}):
        raise HTTPException(status_code=409, detail="This account is already a LeadAI partner")
    if db[K.APPLICATIONS].find_one({"user_id": user_id,
                                    "status": {"$in": [K.APP_PENDING, K.APP_CHANGES]}}):
        raise HTTPException(status_code=409, detail="You already have an application under review")

    app_doc = {
        "user_id": user_id, "email": email, **fields,
        "terms_accepted_at": now, "terms_version": settings.get("terms_version"),
        "status": K.APP_PENDING, "review_note": None, "ip_hash": ip_hash(ip),
        "history": [{"status": K.APP_PENDING, "at": now, "by": email}],
        "created_at": now, "updated_at": now,
    }
    app_id = str(db[K.APPLICATIONS].insert_one(app_doc).inserted_id)
    paudit("partner.application.submitted", None, actor=email, ip=ip,
           details={"application_id": app_id, "partner_type": fields["partner_type"],
                    "company": fields["company"]},
           resource_type="partner_application", resource_id=app_id)
    from app.events.notifications import notify_super_admins
    notify_super_admins("partner_application", "New partner application",
                        f"{fields['name']} <{email}> · {fields['partner_type']}",
                        severity="warning", link="/superadmin#/partners?tab=applications",
                        data={"application_id": app_id})
    from app.events.email import absolute_url, send_email
    send_email(email, "We received your LeadAI partner application",
               f"Hi {fields['name']},\n\nThanks for applying to the LeadAI Partner Program. "
               "Our team reviews every application — we'll email you when there's a decision.\n\n"
               f"You can check your application status any time: {absolute_url('/login?partner=1')}"
               "\n\n— The LeadAI team", kind="partner_application_received")
    return {"id": app_id, "status": K.APP_PENDING}


def update_own_application(user_id: str, data: Dict[str, Any], *, ip: Optional[str] = None) -> Dict[str, Any]:
    """Applicant edits an application the team sent back for changes; it
    returns to the review queue."""
    db = db_or_503()
    app_doc = db[K.APPLICATIONS].find_one({"user_id": str(user_id)}, sort=[("created_at", -1)])
    if not app_doc:
        raise HTTPException(status_code=404, detail="Application not found")
    if app_doc["status"] != K.APP_CHANGES:
        raise HTTPException(status_code=409, detail="This application can't be edited right now")
    fields = clean_application_fields({**app_doc, **data}, db)
    now = utcnow()
    db[K.APPLICATIONS].update_one({"_id": app_doc["_id"]}, {
        "$set": {**fields, "status": K.APP_PENDING, "updated_at": now, "resubmitted_at": now},
        "$push": {"history": {"status": K.APP_PENDING, "at": now, "by": app_doc["email"],
                              "note": "resubmitted"}}})
    paudit("partner.application.resubmitted", None, actor=app_doc["email"], ip=ip,
           details={"application_id": str(app_doc["_id"])},
           resource_type="partner_application", resource_id=str(app_doc["_id"]))
    from app.events.notifications import notify_super_admins
    notify_super_admins("partner_application", "Partner application resubmitted",
                        f"{fields['name']} <{app_doc['email']}>", severity="warning",
                        link="/superadmin#/partners?tab=applications",
                        data={"application_id": str(app_doc["_id"])})
    return public_application(db[K.APPLICATIONS].find_one({"_id": app_doc["_id"]}))


def _load_application(app_id: str, db) -> Dict[str, Any]:
    doc = db[K.APPLICATIONS].find_one({"_id": oid(app_id)}) if oid(app_id) else None
    if not doc:
        raise HTTPException(status_code=404, detail="Application not found")
    return doc


def _app_transition(db, doc, status: str, actor_email: str, note: Optional[str] = None,
                    extra: Optional[Dict[str, Any]] = None) -> None:
    now = utcnow()
    res = db[K.APPLICATIONS].update_one(
        {"_id": doc["_id"], "status": doc["status"]},
        {"$set": {"status": status, "updated_at": now, "review_note": note,
                  "reviewed_by": actor_email, "reviewed_at": now, **(extra or {})},
         "$push": {"history": {"status": status, "at": now, "by": actor_email, "note": note}}})
    if not res.modified_count:
        raise HTTPException(status_code=409, detail="The application changed meanwhile — reload and retry")


def _unique_code(db, field: str, make) -> str:
    for _ in range(25):
        code = make()
        if not db[K.PARTNERS].find_one({field: code}):
            return code
    raise HTTPException(status_code=500, detail="Could not allocate a unique partner code")


def _referral_code_from(name: str) -> str:
    base = re.sub(r"[^A-Z0-9]", "", (name or "").upper())[:8] or "PARTNER"
    return f"{base}{secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}{secrets.randbelow(900) + 100}"


def approve_application(app_id: str, *, actor: Any, partner_type: Optional[str] = None,
                        tier_id: Optional[str] = None, permissions: Optional[List[str]] = None,
                        note: Optional[str] = None, ip: Optional[str] = None) -> Dict[str, Any]:
    db = db_or_503()
    doc = _load_application(app_id, db)
    if doc["status"] not in (K.APP_PENDING, K.APP_CHANGES):
        raise HTTPException(status_code=409, detail=f"Application is '{doc['status']}' — cannot approve")
    if db[K.PARTNERS].find_one({"user_id": doc["user_id"]}):
        raise HTTPException(status_code=409, detail="This applicant is already a partner")
    ptype = partner_type or doc.get("partner_type") or K.AFFILIATE
    if ptype not in K.PARTNER_TYPES:
        raise HTTPException(status_code=422, detail="partner_type must be affiliate or reseller")
    if tier_id and not db[K.TIERS].find_one({"_id": oid(tier_id)}):
        raise HTTPException(status_code=422, detail="Unknown tier")
    perms = clean_permissions(permissions) if permissions is not None else list(K.DEFAULT_PERMISSIONS[ptype])
    actor_email = getattr(actor, "email", None) or str(actor)
    _app_transition(db, doc, K.APP_APPROVED, actor_email, note)
    now = utcnow()
    partner = {
        "user_id": doc["user_id"], "application_id": str(doc["_id"]), "email": doc["email"],
        "name": doc["name"], "company": doc.get("company"), "phone": doc.get("phone"),
        "country": doc.get("country"), "city": doc.get("city"), "website": doc.get("website"),
        "business_type": doc.get("business_type"), "experience": doc.get("experience"),
        "promotion_plan": doc.get("promotion_plan"), "social_profiles": doc.get("social_profiles") or {},
        "tax_info": doc.get("tax_info"), "payout_info": doc.get("payout_info"),
        "partner_type": ptype, "status": K.P_ACTIVE,
        "partner_code": _unique_code(db, "partner_code",
                                     lambda: f"LP-{secrets.randbelow(900000) + 100000}"),
        "referral_code": _unique_code(db, "referral_code", lambda: _referral_code_from(doc.get("company") or doc["name"])),
        "tier_id": tier_id or default_tier_id(db), "permissions": perms,
        "settings": {"email_notifications": True},
        "ip_hash": doc.get("ip_hash"), "notes": None,
        "approved_at": now, "approved_by": actor_email,
        "status_history": [{"status": K.P_ACTIVE, "at": now, "by": actor_email, "note": "approved"}],
        "created_at": now, "updated_at": now,
    }
    try:
        partner["_id"] = db[K.PARTNERS].insert_one(partner).inserted_id
    except Exception as e:  # unique user_id / codes — roll the application back
        db[K.APPLICATIONS].update_one({"_id": doc["_id"]}, {"$set": {"status": doc["status"]}})
        logger.warning("partner creation failed: %s", e)
        raise HTTPException(status_code=409, detail="Could not create the partner (duplicate)")
    pid = str(partner["_id"])
    db[K.WALLETS].update_one({"partner_id": pid}, {"$setOnInsert": {
        "partner_id": pid, "balances": {}, "created_at": now, "updated_at": now}}, upsert=True)
    db[K.APPLICATIONS].update_one({"_id": doc["_id"]}, {"$set": {"partner_id": pid}})
    # activity from the applicant days (sign-ins, status checks) joins the partner's history
    db["partner_activity"].update_many({"user_id": doc["user_id"], "partner_id": None}, {"$set": {"partner_id": pid}})
    paudit("partner.application.approved", pid, actor=actor, ip=ip,
           details={"application_id": str(doc["_id"]), "partner_type": ptype, "note": note},
           resource_type="partner_application", resource_id=str(doc["_id"]))
    paudit("partner.created", pid, actor=actor, ip=ip,
           details={"partner_type": ptype, "permissions": perms, "tier_id": partner["tier_id"],
                    "referral_code": partner["referral_code"]})
    notify_partner(partner, "partner_approved", "Your partner application is approved",
                   f"Welcome to the LeadAI Partner Program! Your referral code is "
                   f"{partner['referral_code']}. Sign in to the Partner Portal to get your links.",
                   severity="success", email=True, link="/partner#/dashboard")
    return public_partner(partner, admin=True)


def reject_application(app_id: str, *, actor: Any, reason: str = "", ip: Optional[str] = None) -> Dict[str, Any]:
    db = db_or_503()
    doc = _load_application(app_id, db)
    if doc["status"] not in (K.APP_PENDING, K.APP_CHANGES):
        raise HTTPException(status_code=409, detail=f"Application is '{doc['status']}' — cannot reject")
    _app_transition(db, doc, K.APP_REJECTED, getattr(actor, "email", None) or str(actor), reason)
    paudit("partner.application.rejected", None, actor=actor, ip=ip,
           details={"application_id": app_id, "reason": reason},
           resource_type="partner_application", resource_id=app_id)
    notify_partner({"user_id": doc["user_id"], "email": doc["email"], "name": doc["name"]},
                   "partner_rejected", "Your partner application",
                   "Thank you for your interest. We're unable to approve your partner application "
                   "at this time." + (f" Reason: {reason}" if reason else ""),
                   severity="warning", email=True, link="/partners")
    return public_application(db[K.APPLICATIONS].find_one({"_id": doc["_id"]}), admin=True)


def request_application_changes(app_id: str, *, actor: Any, note: str, ip: Optional[str] = None) -> Dict[str, Any]:
    db = db_or_503()
    doc = _load_application(app_id, db)
    if doc["status"] != K.APP_PENDING:
        raise HTTPException(status_code=409, detail=f"Application is '{doc['status']}' — cannot request changes")
    if not (note or "").strip():
        raise HTTPException(status_code=422, detail="Tell the applicant what to change")
    _app_transition(db, doc, K.APP_CHANGES, getattr(actor, "email", None) or str(actor), note.strip()[:1000])
    paudit("partner.application.changes_requested", None, actor=actor, ip=ip,
           details={"application_id": app_id, "note": note},
           resource_type="partner_application", resource_id=app_id)
    notify_partner({"user_id": doc["user_id"], "email": doc["email"], "name": doc["name"]},
                   "partner_changes_requested", "Changes requested on your partner application",
                   f"Our team asked for changes: {note.strip()[:500]}. Sign in to update and resubmit.",
                   severity="warning", email=True, link="/partner#/application")
    return public_application(db[K.APPLICATIONS].find_one({"_id": doc["_id"]}), admin=True)


# ── partner records ──────────────────────────────────────────────────────────

def load_partner(partner_id: str, db=None) -> Dict[str, Any]:
    db = db_or_503(db)
    p = db[K.PARTNERS].find_one({"_id": oid(partner_id)}) if oid(partner_id) else None
    if not p:
        raise HTTPException(status_code=404, detail="Partner not found")
    return p


def set_partner_status(partner_id: str, status: str, *, actor: Any, reason: str = "",
                       ip: Optional[str] = None) -> Dict[str, Any]:
    db = db_or_503()
    p = load_partner(partner_id, db)
    if status not in K.PARTNER_STATUSES:
        raise HTTPException(status_code=422, detail="Unknown partner status")
    if p["status"] == status:
        raise HTTPException(status_code=409, detail=f"Partner is already {status}")
    if status == K.P_SUSPENDED and not (reason or "").strip():
        raise HTTPException(status_code=422, detail="A reason is required to suspend a partner")
    now = utcnow()
    actor_email = getattr(actor, "email", None) or str(actor)
    db[K.PARTNERS].update_one({"_id": p["_id"], "status": p["status"]}, {
        "$set": {"status": status, "updated_at": now,
                 **({"suspended_at": now, "suspension_reason": reason} if status == K.P_SUSPENDED
                    else {"reactivated_at": now, "suspension_reason": None})},
        "$push": {"status_history": {"status": status, "at": now, "by": actor_email, "note": reason}}})
    if status == K.P_SUSPENDED:
        # suspended partners lose access immediately: revoke sessions + API keys
        from app.auth.service import revoke_user_sessions
        revoke_user_sessions(p["user_id"], revoked_by="partner_suspended")
        db.api_keys.update_many({"partner_id": str(p["_id"]), "is_active": True},
                                {"$set": {"is_active": False, "updated_at": now,
                                          "revoked_reason": "partner_suspended"}})
    action = "partner.suspended" if status == K.P_SUSPENDED else "partner.reactivated"
    paudit(action, str(p["_id"]), actor=actor, ip=ip, details={"reason": reason})
    notify_partner(p, "partner_status", "Your partner account was suspended" if status == K.P_SUSPENDED
                   else "Your partner account is active again",
                   (reason or "Contact the LeadAI partner team for details.") if status == K.P_SUSPENDED
                   else "You can sign in to the Partner Portal again.",
                   severity="warning" if status == K.P_SUSPENDED else "success", email=True)
    return public_partner(load_partner(partner_id, db), admin=True)


_ADMIN_EDITABLE = ("name", "company", "phone", "country", "city", "website", "business_type",
                   "notes")


def admin_update_partner(partner_id: str, data: Dict[str, Any], *, actor: Any,
                         ip: Optional[str] = None) -> Dict[str, Any]:
    """Super Admin edits: type, tier, permissions, profile fields, notes, payout info."""
    db = db_or_503()
    p = load_partner(partner_id, db)
    upd: Dict[str, Any] = {}
    changes: Dict[str, Any] = {}
    if "partner_type" in data and data["partner_type"] != p.get("partner_type"):
        if data["partner_type"] not in K.PARTNER_TYPES:
            raise HTTPException(status_code=422, detail="partner_type must be affiliate or reseller")
        upd["partner_type"] = data["partner_type"]
        changes["partner_type"] = {"from": p.get("partner_type"), "to": data["partner_type"]}
    if "tier_id" in data and str(data["tier_id"] or "") != str(p.get("tier_id") or ""):
        if data["tier_id"] and not db[K.TIERS].find_one({"_id": oid(data["tier_id"])}):
            raise HTTPException(status_code=422, detail="Unknown tier")
        upd["tier_id"] = str(data["tier_id"]) if data["tier_id"] else None
        changes["tier_id"] = {"from": p.get("tier_id"), "to": upd["tier_id"]}
    if "tier_id" in changes and "tier_locked" not in data:
        data = {**data, "tier_locked": True}   # a hand-picked tier is not overridden by auto tiering
    if "tier_locked" in data and bool(data["tier_locked"]) != bool(p.get("tier_locked")):
        upd["tier_locked"] = bool(data["tier_locked"])
        changes["tier_locked"] = upd["tier_locked"]
    if "permissions" in data:
        perms = clean_permissions(data["permissions"])
        if perms != sorted(p.get("permissions") or []):
            upd["permissions"] = perms
            changes["permissions"] = {"added": sorted(set(perms) - set(p.get("permissions") or [])),
                                      "removed": sorted(set(p.get("permissions") or []) - set(perms))}
    for f in _ADMIN_EDITABLE:
        if f in data:
            v = text(data[f], 2000 if f == "notes" else 160)
            if f == "website" and v and not _URL_RE.match(v):
                raise HTTPException(status_code=422, detail="Website must be an http(s) URL")
            if v != p.get(f):
                upd[f] = v
                changes[f] = "updated"
    if "payout_info" in data:
        upd["payout_info"] = clean_payout_info(data["payout_info"], db)
        changes["payout_info"] = "updated"
    if "tax_info" in data:
        upd["tax_info"] = clean_tax_info(data["tax_info"])
        changes["tax_info"] = "updated"
    if not upd:
        return public_partner(p, admin=True)
    upd["updated_at"] = utcnow()
    db[K.PARTNERS].update_one({"_id": p["_id"]}, {"$set": upd})
    paudit("partner.updated", str(p["_id"]), actor=actor, ip=ip, details={"changes": changes})
    if "permissions" in changes:
        paudit("partner.permissions.changed", str(p["_id"]), actor=actor, ip=ip,
               details=changes["permissions"])
        if "api.access" in changes["permissions"].get("removed", []):
            db.api_keys.update_many({"partner_id": str(p["_id"]), "is_active": True},
                                    {"$set": {"is_active": False, "updated_at": utcnow(),
                                              "revoked_reason": "api_access_removed"}})
    if "payout_info" in changes or "tax_info" in changes:
        paudit("partner.payout_info.changed", str(p["_id"]), actor=actor, ip=ip,
               details={"fields": [k for k in ("payout_info", "tax_info") if k in changes],
                        "by": "super_admin"})
    if "partner_type" in changes or "permissions" in changes:
        notify_partner(p, "partner_access_changed", "Your partner access was updated",
                       "The LeadAI team updated your partner type or permissions.")
    return public_partner(load_partner(partner_id, db), admin=True)


_SELF_EDITABLE = ("name", "company", "phone", "country", "city", "website", "business_type")


def update_own_profile(partner: Dict[str, Any], data: Dict[str, Any], *, ip: Optional[str] = None) -> Dict[str, Any]:
    db = db_or_503()
    upd: Dict[str, Any] = {}
    for f in _SELF_EDITABLE:
        if f in data:
            v = text(data[f], 160)
            if f == "name" and not v:
                raise HTTPException(status_code=422, detail="Name is required")
            if f == "website" and v and not _URL_RE.match(v):
                raise HTTPException(status_code=422, detail="Website must be an http(s) URL")
            if f == "phone" and v and not re.match(r"^[+()\-.\s\d]{6,30}$", v):
                raise HTTPException(status_code=422, detail="Phone may contain digits, spaces and + ( ) - only")
            upd[f] = v
    if "social_profiles" in data:
        upd["social_profiles"] = _clean_socials(data["social_profiles"])
    if "settings" in data and isinstance(data["settings"], dict):
        upd["settings.email_notifications"] = bool(data["settings"].get("email_notifications", True))
    if upd:
        upd["updated_at"] = utcnow()
        db[K.PARTNERS].update_one({"_id": partner["_id"]}, {"$set": upd})
        paudit("partner.profile.updated", str(partner["_id"]), actor=partner["email"], ip=ip,
               details={"fields": sorted(k for k in upd if k != "updated_at")})
    return public_partner(load_partner(str(partner["_id"]), db))


def update_own_payout(partner: Dict[str, Any], payout_info: Any, tax_info: Any = None, *,
                      ip: Optional[str] = None) -> Dict[str, Any]:
    """Sensitive: payout details changes are audited (values redacted) and the
    partner is notified, so an account takeover cannot silently redirect money."""
    db = db_or_503()
    upd: Dict[str, Any] = {"payout_info": clean_payout_info(payout_info, db), "updated_at": utcnow(),
                           "payout_info_changed_at": utcnow()}
    if tax_info is not None:
        upd["tax_info"] = clean_tax_info(tax_info)
    if not upd["payout_info"]:
        raise HTTPException(status_code=422, detail="Payout details are required")
    db[K.PARTNERS].update_one({"_id": partner["_id"]}, {"$set": upd})
    paudit("partner.payout_info.changed", str(partner["_id"]), actor=partner["email"], ip=ip,
           details={"method": upd["payout_info"]["method"], "tax_info": tax_info is not None,
                    "by": "partner"})
    notify_partner(partner, "partner_security", "Your payout details were changed",
                   "If you didn't make this change, contact LeadAI support immediately.",
                   severity="warning", email=True, link="/partner#/settings")
    from app.events.notifications import notify_super_admins
    notify_super_admins("security_event", "Partner payout details changed",
                        f"{partner.get('name')} <{partner.get('email')}> updated payout details",
                        severity="info", link=f"/superadmin#/partners/{partner['_id']}")
    return public_partner(load_partner(str(partner["_id"]), db))


def partner_audit_query(partner_id: str) -> Dict[str, Any]:
    return {"category": "partner", "$or": [{"details.partner_id": str(partner_id)},
                                           {"resource_type": "partner", "resource_id": str(partner_id)}]}


def since(days: int):
    return utcnow() - timedelta(days=days)
