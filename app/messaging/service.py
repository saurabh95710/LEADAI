"""Who gets which message, on which channel.

- Automatic messages (Super Admin → Messaging → Automatic messages), stored in
  ``platform_config`` (_id "messaging"):
    welcome_sms         — a text when someone signs up (the welcome / trial email
                          is always sent);
    login_alert_email / login_alert_sms — "new sign-in to your account", at most
                          once every ``login_alert_every_minutes`` per person.
- Admin messages: the Super Admin to anyone (users, organization admins, partners),
  an organization's admin to their own team. Each send is a campaign in
  ``message_campaigns``; every email / SMS lands in its outbox tagged with the
  campaign id, so delivery can be followed per person.
"""
import copy
import logging
import threading
from datetime import timedelta
from typing import Any, Dict, Iterable, List, Optional

from bson import ObjectId

from app.db.models import utcnow
from app.db.mongo import get_sync_db
from app.messaging.phone import mask_phone, normalize_phone

logger = logging.getLogger(__name__)

CONFIG_ID = "messaging"
CAMPAIGNS = "message_campaigns"
CHANNELS = ("email", "sms")
AUTO_DEFAULTS: Dict[str, Any] = {
    "welcome_sms": True,
    "login_alert_email": True,
    "login_alert_sms": True,
    "login_alert_every_minutes": 10,
}
MAX_RECIPIENTS = 5000


# ── settings ────────────────────────────────────────────────────────────────

def auto_settings(db=None) -> Dict[str, Any]:
    db = db if db is not None else get_sync_db()
    out = copy.deepcopy(AUTO_DEFAULTS)
    try:
        doc = db.platform_config.find_one({"_id": CONFIG_ID}) if db is not None else None
        out.update({k: v for k, v in ((doc or {}).get("value") or {}).items() if k in AUTO_DEFAULTS})
    except Exception:
        pass
    return out


def update_auto_settings(patch: Dict[str, Any], actor: str, db=None) -> Dict[str, Any]:
    db = db if db is not None else get_sync_db()
    value = auto_settings(db)
    for k, v in (patch or {}).items():
        if k not in AUTO_DEFAULTS:
            raise ValueError(f"Unknown setting: {k}")
        if k == "login_alert_every_minutes":
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise ValueError("login_alert_every_minutes must be a number")
            if not 0 <= v <= 1440:
                raise ValueError("login_alert_every_minutes must be between 0 and 1440")
        else:
            v = bool(v)
        value[k] = v
    db.platform_config.update_one({"_id": CONFIG_ID}, {"$set": {"value": value, "updated_at": utcnow(),
                                                                "updated_by": actor}}, upsert=True)
    return value


# ── one person ──────────────────────────────────────────────────────────────

def contact_of(user: Dict[str, Any]) -> Dict[str, Optional[str]]:
    return {"email": (user.get("email") or "").strip().lower() or None,
            "phone": user.get("phone_e164") or normalize_phone(user.get("phone"))}


def send_to_person(user: Dict[str, Any], *, channels: Iterable[str], subject: str, body: str,
                   sms_body: Optional[str] = None, kind: str = "generic",
                   organization_id: Optional[str] = None, campaign_id: Optional[str] = None) -> Dict[str, str]:
    """Email and/or SMS to one person. Returns {channel: status}; a channel
    without an address is "skipped"."""
    from app.events.email import send_email
    from app.messaging.sms import send_sms
    c = contact_of(user)
    org = organization_id or user.get("default_organization_id")
    out: Dict[str, str] = {}
    for ch in channels:
        if ch == "email":
            out["email"] = send_email(c["email"], subject, body, kind=kind, organization_id=org,
                                      campaign_id=campaign_id) if c["email"] else "skipped"
        elif ch == "sms":
            text = sms_body if sms_body is not None else _sms_from(subject, body)
            out["sms"] = send_sms(c["phone"], text, kind=kind, organization_id=org,
                                  user_id=str(user.get("_id") or "") or None,
                                  campaign_id=campaign_id) if c["phone"] else "skipped"
    return out


def _sms_from(subject: str, body: str, limit: int = 306) -> str:
    """No separate SMS text: "subject: message", kept to two SMS segments."""
    text = " ".join(f"{subject}: {body}".split()) if subject else " ".join((body or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _in_background(fn, *args, **kwargs) -> None:
    """Messages never slow down a sign-up or a sign-in."""
    def run():
        try:
            fn(*args, **kwargs)
        except Exception as e:  # pragma: no cover - best effort
            logger.warning("background message failed: %s", e)
    threading.Thread(target=run, daemon=True).start()


def welcome_sms(user_id: str, text: str, *, organization_id: Optional[str] = None,
                background: bool = True) -> None:
    """The sign-up text (the email is sent by the sign-up flow itself)."""
    def go():
        db = get_sync_db()
        if db is None or not auto_settings(db)["welcome_sms"]:
            return
        user = db.users.find_one({"_id": ObjectId(str(user_id))}) if ObjectId.is_valid(str(user_id)) else None
        if user:
            send_to_person(user, channels=("sms",), subject="", body=text, sms_body=text, kind="welcome",
                           organization_id=organization_id)
    _in_background(go) if background else go()


def login_alert(user_id: str, *, ip: Optional[str], user_agent: Optional[str], portal: str,
                background: bool = True) -> None:
    """"New sign-in to your LeadAI account" by email / SMS, throttled per person."""
    def go():
        from app.events.email import absolute_url
        db = get_sync_db()
        if db is None or not ObjectId.is_valid(str(user_id)):
            return
        cfg = auto_settings(db)
        channels = [ch for ch in CHANNELS if cfg[f"login_alert_{ch}"]]
        if not channels:
            return
        now = utcnow()
        every = int(cfg["login_alert_every_minutes"] or 0)
        # claim the alert atomically so two quick sign-ins send one message
        q: Dict[str, Any] = {"_id": ObjectId(str(user_id))}
        if every:
            q["$or"] = [{"last_login_alert_at": None}, {"last_login_alert_at": {"$exists": False}},
                        {"last_login_alert_at": {"$lt": now - timedelta(minutes=every)}}]
        user = db.users.find_one_and_update(q, {"$set": {"last_login_alert_at": now}})
        if not user:
            return
        when = now.strftime("%d %b %Y, %H:%M UTC")
        device = (user_agent or "an unknown device")[:120]
        body = (f"Hi {user.get('name') or ''},\n\nYour LeadAI account ({user.get('email')}) was just used to sign in "
                f"to the {portal}.\n\nWhen: {when}\nFrom: {ip or 'unknown IP'}\nDevice: {device}\n\n"
                "If this was you, there's nothing to do. If it wasn't, reset your password right away: "
                f"{absolute_url('/forgot-password')}\n\n— The LeadAI team")
        sms = (f"LeadAI: new sign-in to your account on {when} from {ip or 'unknown IP'}. "
               "Not you? Reset your password at once.")
        send_to_person(user, channels=channels, subject="New sign-in to your LeadAI account", body=body,
                       sms_body=sms, kind="login_alert")
    _in_background(go) if background else go()


# ── admin messages ──────────────────────────────────────────────────────────

def _person(u: Dict[str, Any], org_names: Dict[str, str], role: Optional[str] = None) -> Dict[str, Any]:
    c = contact_of(u)
    org_id = u.get("default_organization_id")
    return {"id": str(u["_id"]), "name": u.get("name") or "", "email": c["email"] or "",
            "phone": mask_phone(c["phone"]), "has_phone": bool(c["phone"]),
            "organization_id": org_id, "organization": org_names.get(str(org_id or ""), ""),
            "role": role or ("partner" if u.get("account_type") == "partner" else (u.get("platform_role") or "")),
            "status": u.get("status")}


def search_people(db, q: str = "", *, organization_id: Optional[str] = None, limit: int = 25) -> List[Dict[str, Any]]:
    """For the recipient picker: name / email / phone search, masked phones."""
    import re as _re
    query: Dict[str, Any] = {"status": "active"}       # only people who can receive a message
    if q.strip():
        rx = {"$regex": _re.escape(q.strip()), "$options": "i"}
        query["$or"] = [{"name": rx}, {"email": rx}, {"phone": rx}]
    if organization_id:
        ids = [m["user_id"] for m in db.organization_members.find(
            {"organization_id": str(organization_id), "status": "active"}, {"user_id": 1})]
        query["_id"] = {"$in": [ObjectId(i) for i in ids if ObjectId.is_valid(i)]}
    users = list(db.users.find(query, {"password_hash": 0, "totp_secret": 0}).sort("name", 1).limit(limit))
    roles = {}
    for m in db.organization_members.find({"user_id": {"$in": [str(u["_id"]) for u in users]}, "status": "active"},
                                          {"user_id": 1, "role": 1}):
        roles[m["user_id"]] = m.get("role")
    org_ids = [ObjectId(str(u["default_organization_id"])) for u in users
               if u.get("default_organization_id") and ObjectId.is_valid(str(u["default_organization_id"]))]
    org_names = {str(o["_id"]): o.get("name") for o in db.organizations.find({"_id": {"$in": org_ids}}, {"name": 1})}
    return [_person(u, org_names, roles.get(str(u["_id"]))) for u in users]


def check_channels(channels: Iterable[str]) -> List[str]:
    chosen = [c for c in channels if c in CHANNELS]
    if not chosen:
        raise ValueError("Choose email and/or SMS")
    return chosen


def resolve_recipients(db, *, user_ids: Iterable[str] = (), organization_ids: Iterable[str] = (),
                       audience: Optional[str] = None, only_organization: Optional[str] = None) -> List[Dict[str, Any]]:
    """Unique active users. audience: org_admins (owners + admins of every
    organization) | all_users | partners. ``only_organization`` limits everything
    to that organization's members (an org admin messaging their team)."""
    ids: set = {str(i) for i in user_ids if ObjectId.is_valid(str(i))}
    member_q: Dict[str, Any] = {"status": "active"}
    if organization_ids:
        member_q["organization_id"] = {"$in": [str(o) for o in organization_ids]}
        ids |= {m["user_id"] for m in db.organization_members.find(member_q, {"user_id": 1})}
    if audience == "org_admins":
        ids |= {m["user_id"] for m in db.organization_members.find(
            {"status": "active", "role": {"$in": ["owner", "admin"]}}, {"user_id": 1})}
    elif audience == "all_users":
        ids |= {str(u["_id"]) for u in db.users.find({"status": "active", "account_type": {"$ne": "partner"}},
                                                      {"_id": 1}).limit(MAX_RECIPIENTS + 1)}
    elif audience == "partners":
        ids |= {str(p.get("user_id")) for p in db.partners.find({"status": "active"}, {"user_id": 1})
                if p.get("user_id")}
    if only_organization:
        allowed = {m["user_id"] for m in db.organization_members.find(
            {"organization_id": str(only_organization), "status": "active"}, {"user_id": 1})}
        ids &= allowed
    ids = {i for i in ids if ObjectId.is_valid(i)}
    if len(ids) > MAX_RECIPIENTS:
        raise ValueError(f"Too many recipients ({len(ids)}); the limit is {MAX_RECIPIENTS} per message")
    return list(db.users.find({"_id": {"$in": [ObjectId(i) for i in ids]}, "status": "active"},
                              {"password_hash": 0, "totp_secret": 0}))


def start_campaign(db, recipients: List[Dict[str, Any]], *, channels: List[str], subject: str, body: str,
                   sms_body: Optional[str], sender: Dict[str, Any], scope: str,
                   organization_id: Optional[str] = None, background: bool = True) -> Dict[str, Any]:
    """Record the message and send it to everyone (in the background)."""
    channels = [c for c in channels if c in CHANNELS]
    if not channels:
        raise ValueError("Choose email and/or SMS")
    if not recipients:
        raise ValueError("Nobody to send to")
    if "email" in channels and not (subject or "").strip():
        raise ValueError("An email needs a subject")
    if not (body or "").strip():
        raise ValueError("Write a message")
    now = utcnow()
    doc = {"scope": scope, "organization_id": organization_id, "channels": channels,
           "subject": (subject or "").strip()[:200], "body": body.strip()[:5000],
           "sms_body": (sms_body or "").strip()[:640] or None,
           "sender": {"email": sender.get("email"), "user_id": sender.get("user_id"), "role": sender.get("role")},
           "recipient_count": len(recipients), "status": "sending",
           "counts": {ch: {"sent": 0, "queued": 0, "failed": 0, "skipped": 0, "other": 0} for ch in channels},
           "created_at": now, "finished_at": None}
    cid = db[CAMPAIGNS].insert_one(doc).inserted_id

    def go():
        sdb = get_sync_db()
        counts = doc["counts"]
        for n, u in enumerate(recipients, 1):
            res = send_to_person(u, channels=channels, subject=doc["subject"], body=doc["body"],
                                 sms_body=doc["sms_body"], kind="admin_message",
                                 organization_id=organization_id, campaign_id=str(cid))
            for ch, st in res.items():
                counts[ch][st if st in counts[ch] else "other"] += 1
            if n % 25 == 0:      # progress for big sends
                sdb[CAMPAIGNS].update_one({"_id": cid}, {"$set": {"counts": counts}})
        sdb[CAMPAIGNS].update_one({"_id": cid}, {"$set": {"counts": counts, "status": "done",
                                                          "finished_at": utcnow()}})
    _in_background(go) if background else go()
    return {"id": str(cid), "recipient_count": len(recipients), "channels": channels}


def campaign_view(db, cid: str, *, with_deliveries: bool = False) -> Optional[Dict[str, Any]]:
    doc = db[CAMPAIGNS].find_one({"_id": ObjectId(cid)}) if ObjectId.is_valid(cid) else None
    if not doc:
        return None
    from app.messaging.sms import COLLECTION as SMS
    out = {k: v for k, v in doc.items() if k != "_id"}
    out["id"] = str(doc["_id"])
    for k in ("created_at", "finished_at"):
        if out.get(k) is not None and hasattr(out[k], "isoformat"):
            out[k] = out[k].isoformat() + ("" if out[k].tzinfo else "+00:00")
    if with_deliveries:
        out["deliveries"] = (
            [{"channel": "email", "to": e.get("to"), "status": e.get("status"), "error": e.get("error")}
             for e in db.email_outbox.find({"campaign_id": cid}).limit(2000)] +
            [{"channel": "sms", "to": mask_phone(s.get("to")), "status": s.get("status"), "error": s.get("error")}
             for s in db[SMS].find({"campaign_id": cid}).limit(2000)])
    return out


def backfill_phone_e164(db) -> int:
    """Once per user: the standard (E.164) form of the phone number, used to
    find an account by phone (password reset by SMS). Idempotent."""
    if db is None:
        return 0
    n = 0
    for u in db.users.find({"phone": {"$nin": [None, ""]}, "phone_e164": {"$exists": False}},
                           {"phone": 1}).limit(20000):
        db.users.update_one({"_id": u["_id"]}, {"$set": {"phone_e164": normalize_phone(u.get("phone"))}})
        n += 1
    return n
