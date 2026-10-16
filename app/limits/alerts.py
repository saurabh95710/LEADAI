"""Limit alerts: who is told what, once per level and period.

Dedupe (collection ``api_limit_alerts``): one document per
``(scope, provider, metric, period)`` — ``metric`` is ``"status"`` for every
provider except ``plan`` (one per plan meter: searches, exports, ...). The
``period`` is the provider's cycle (calendar month of Apify's usage-cycle
start, Gemini's Pacific day, the UTC day for email / SMS, the plan period,
the token allocation); Apify (35 days) and email / SMS (7 days) are sticky:
an open record carries over a period change, so a lasting state is
announced again at most that often. A notice is sent when a level is first
reached, when it escalates (warning → critical → exhausted), when it changes
within the same rank (critical ↔ error) or after a recovery. Warning records
are never closed (a warning fires once per period); critical / error /
exhausted close with one "recovered" notice only after RECOVERY_OK_CHECKS ok
checks in a row and RECOVERY_QUIET_MINUTES since the last non-ok state.
Severity: warning / critical / error → "warning" (organizations' "usage
warnings" switch applies), exhausted → "danger" (always delivered).

Recipients
  platform scope (LeadAI's own Apify / Gemini / email / SMS):
    * Super Admins: every level, in-app (type api_limit_warning |
      api_limit_critical | api_limit_exhausted | api_limit_recovered), plus
      an email to the Super Admin (SUPERADMIN_EMAIL) at critical / exhausted
      when ``api_limits.email_admins`` is on (never for the email provider
      itself — the mail server is what is out of quota);
    * org admins of the organizations that depend on the service (Apify /
      Gemini: coverage "leadai"; email / SMS: every organization), in-app,
      at critical / exhausted (``api_limits.notify_org_admins``), with plain
      words and no internal numbers;
    * users (non-admin members) of those organizations, in-app only, at
      exhausted (``api_limits.notify_users``; their preferences apply).
  organization scope (own Apify / Gemini keys, LeadAI tokens, plan meters):
    * org admins: every level, in-app + email at critical / exhausted; plan
      meters use ``quota_warning``. Own-key errors seen live by a search are
      already told by ``tenant_api_keys.record_key_failure`` and token
      thresholds by ``app.billing.tokens`` — those are not repeated here;
    * users of the organization: in-app at exhausted.
Fan-out is capped and written with ``insert_many``.
"""
import logging
import threading
from datetime import timedelta
from typing import Any, Dict, Iterable, List, Optional

from app.db.mongo import get_sync_db
from app.limits.model import (
    ALERT_LEVELS,
    ALERT_RANK,
    PLATFORM,
    aware,
    describe_metric,
    metric_level,
    now_utc,
    thresholds,
)
from app.limits.store import ALERTS

logger = logging.getLogger(__name__)

SUPER_LINK = "/superadmin#/api-limits"
ORG_LINK = "/org-admin#api-limits"
USER_LINK = "/dashboard#usage"   # the Limits card
MAX_FANOUT_ORGS = 5000
MAX_FANOUT_USERS = 5000          # users per notice in total ...
MAX_USERS_PER_ORG = 500          # ... and per organization (no organization starves the others)
_CHUNK = 500
# recovery hysteresis: "back to normal" only after this many ok checks in a
# row AND this long since the last non-ok state
RECOVERY_OK_CHECKS = 2
RECOVERY_QUIET_MINUTES = 30
# a lasting state is announced again at most every N days (sticky across
# periods): Apify's usage cycle, the email / SMS rolling day
STICKY_DAYS = {"apify": 35, "email": 7, "sms": 7}

NTYPE = {"warning": "api_limit_warning", "critical": "api_limit_critical", "error": "api_limit_critical",
         "exhausted": "api_limit_exhausted", "recovered": "api_limit_recovered"}
ALL_TYPES = ("api_limit_warning", "api_limit_critical", "api_limit_exhausted", "api_limit_recovered")
# only "used up" is danger (always delivered); critical / error respect the
# organization's "usage warnings" switch
SEVERITY = {"warning": "warning", "critical": "warning", "error": "warning", "exhausted": "danger",
            "recovered": "info"}
TITLE_WORDS = {"warning": "is nearing its limit", "critical": "is almost at its limit",
               "error": "needs attention", "exhausted": "has reached its limit",
               "recovered": "is back to normal"}
# what an organization that depends on LeadAI's service is told (no internal numbers)
SERVICE_WORDS = {
    "apify": ("scraping service", "searches may pause",
              "new searches are paused until it is restored"),
    "gemini": ("AI analysis service", "comments may be scored by the keyword rules for a while",
               "comments are scored by the keyword rules until it is restored"),
    "email": ("email service", "some emails may be delayed",
              "emails (alerts, invitations, password resets) are delayed until it is restored"),
    "sms": ("SMS service", "some text messages may be delayed",
            "text messages are not delivered until it is restored"),
}
_SOON = ("critical", "error", "exhausted")

_guard = threading.local()


def alert_settings() -> Dict[str, bool]:
    out = {"enabled": True, "notify_org_admins": True, "notify_users": True, "email_admins": True}
    try:
        from app.admin.settings import get_setting
        for k in out:
            v = get_setting(f"api_limits.{k}")
            if v is not None:
                out[k] = bool(v)
    except Exception:
        pass
    return out


def in_alert() -> bool:
    """True while notifications are being sent (email / SMS failure hooks
    must not start another evaluation from inside one)."""
    return bool(getattr(_guard, "active", False))


# ── evaluation ──────────────────────────────────────────────────────────────

def _items(status: Dict[str, Any]) -> List[tuple]:
    """(metric key, level, metric | None) to evaluate."""
    if status.get("provider") == "plan":
        warn, crit = thresholds()
        return [(m["key"], metric_level(m, warn, crit), m) for m in status.get("metrics") or []
                if not m.get("informational")]
    return [("status", status.get("level") or "unknown", None)]


def recovery_ready(status: Dict[str, Any]) -> bool:
    """Hysteresis: ok for RECOVERY_OK_CHECKS checks in a row and
    RECOVERY_QUIET_MINUTES since the last non-ok state."""
    if int(status.get("ok_streak") or 0) < RECOVERY_OK_CHECKS:
        return False
    last_bad = aware(status.get("last_bad_at"))
    return last_bad is None or now_utc() - last_bad >= timedelta(minutes=RECOVERY_QUIET_MINUTES)


def evaluate(status: Optional[Dict[str, Any]], *, from_event: bool = False,
             admins_notified: bool = False) -> List[Dict[str, Any]]:
    """Send what this status calls for. Returns the notices dispatched
    ([{metric, level}]) — never raises. ``admins_notified``: the organization's
    admins were already told about this live error (record_key_failure)."""
    if not status or in_alert() or status.get("provider") == "payments":
        return []
    cfg = alert_settings()
    if not cfg["enabled"]:
        return []
    db = get_sync_db()
    if db is None:
        return []
    sent: List[Dict[str, Any]] = []
    _guard.active = True
    try:
        for key, level, m in _items(status):
            if level in ALERT_LEVELS:
                if _raise(db, status, key, level):
                    _dispatch(status, key, level, m, cfg, admins_notified=admins_notified)
                    sent.append({"metric": key, "level": level})
            elif level == "ok" and recovery_ready(status):
                for prev in _recover(db, status, key):
                    _dispatch(status, key, "recovered", m, cfg, previous_level=prev)
                    sent.append({"metric": key, "level": "recovered"})
    except Exception as e:  # alerting never breaks a check or a search
        logger.warning("api limit alert evaluation failed: %s", e)
    finally:
        _guard.active = False
    return sent


def _raise(db, status: Dict[str, Any], key: str, level: str) -> bool:
    """Record level for (scope, provider, metric, period). True = notify:
    first time in the period, a higher rank, a different level of the same
    rank (critical → error), or again after a recovery. Sticky providers
    carry an open record over a period change for STICKY_DAYS."""
    scope, provider, period = status["scope"], status["provider"], str(status.get("period") or "")
    doc_id = f"{scope}:{provider}:{key}:{period}"
    rank = ALERT_RANK.get(level, 0)
    now = now_utc()
    cond = {"$or": [{"rank": {"$lt": rank}}, {"recovered_at": {"$ne": None}},
                    {"rank": rank, "levels": {"$ne": level}}]}
    upd = {"$set": {"level": level, "rank": rank, "notified_at": now, "recovered_at": None},
           "$addToSet": {"levels": level}}
    sticky = STICKY_DAYS.get(provider)
    if sticky:
        open_doc = db[ALERTS].find_one({"scope": scope, "provider": provider, "metric": key, "recovered_at": None,
                                        "created_at": {"$gte": now - timedelta(days=sticky)}},
                                       sort=[("created_at", -1)])
        if open_doc:
            res = db[ALERTS].update_one({"_id": open_doc["_id"], **cond}, upd)
            return bool(res.modified_count)
    try:
        db[ALERTS].insert_one({"_id": doc_id, "scope": scope, "provider": provider,
                               "organization_id": status.get("organization_id"), "metric": key,
                               "period": period, "level": level, "rank": rank, "levels": [level],
                               "created_at": now, "notified_at": now, "recovered_at": None})
        return True
    except Exception as e:
        if "Duplicate" not in type(e).__name__:
            raise
    res = db[ALERTS].update_one({"_id": doc_id, **cond}, upd)
    return bool(res.modified_count)


def _recover(db, status: Dict[str, Any], key: str) -> List[str]:
    """Close the open critical / error / exhausted alerts of this metric and
    return their levels (one "recovered" notice). Warning-level records are
    never closed: they stay for their period, so a warning can't re-fire
    after every quiet check."""
    out = []
    q = {"scope": status["scope"], "provider": status["provider"], "metric": key, "recovered_at": None,
         "rank": {"$gte": ALERT_RANK["critical"]}}
    for doc in list(db[ALERTS].find(q)):
        res = db[ALERTS].update_one({"_id": doc["_id"], "recovered_at": None},
                                    {"$set": {"recovered_at": now_utc()}})
        if res.modified_count and int(doc.get("rank") or 0) >= ALERT_RANK["critical"]:
            out.append(str(doc.get("level")))
    return out[:1]


# ── dispatch ────────────────────────────────────────────────────────────────

def _data(status: Dict[str, Any], key: str, level: str) -> Dict[str, Any]:
    return {"scope": status["scope"], "provider": status["provider"], "metric": key, "level": level,
            "period": status.get("period"), "organization_id": status.get("organization_id")}


def _dispatch(status: Dict[str, Any], key: str, level: str, m: Optional[Dict[str, Any]],
              cfg: Dict[str, bool], *, admins_notified: bool = False, previous_level: Optional[str] = None) -> None:
    if status["scope"] == PLATFORM:
        _dispatch_platform(status, key, level, cfg, previous_level=previous_level)
    else:
        _dispatch_org(status, key, level, m, cfg, admins_notified=admins_notified)


def _dispatch_platform(status: Dict[str, Any], key: str, level: str, cfg: Dict[str, bool], *,
                       previous_level: Optional[str] = None) -> None:
    from app.events.notifications import _email, notify_super_admins
    provider = status["provider"]
    title = f"{status.get('label') or provider} {TITLE_WORDS[level]}"
    message = status.get("message") or ""
    if level == "recovered":
        message = f"{status.get('label') or provider} is within its limits again. {message}".strip()
    data = _data(status, key, level)
    notify_super_admins(NTYPE[level], title, message, severity=SEVERITY[level], link=SUPER_LINK, data=data)
    if level in ("critical", "error", "exhausted") and cfg["email_admins"] and provider != "email":
        try:
            from app.auth.superadmin import superadmin_email
            to = superadmin_email()
            if to:
                _email(to, title, message, NTYPE[level], None, SUPER_LINK)
        except Exception as e:
            logger.warning("api limit email to the Super Admin failed: %s", e)
    words = SERVICE_WORDS.get(provider)
    if not words:
        return
    svc, soft, hard = words
    told_level = previous_level if level == "recovered" else level
    if told_level not in _SOON:
        return
    orgs = _dependent_orgs(provider)
    if not orgs:
        return
    if level == "recovered":
        o_title = f"LeadAI's {svc} is working normally again"
        o_msg = f"LeadAI's {svc} is back to normal. Nothing for you to do."
    elif level == "exhausted":
        o_title = f"LeadAI's {svc} has reached its limit"
        o_msg = f"LeadAI's {svc} has reached its limit — {hard}. Our team has been notified."
    elif level == "error":
        o_title = f"LeadAI's {svc} has a problem"
        o_msg = f"LeadAI's {svc} is not working right now — {soft}. Our team has been notified."
    else:
        o_title = f"LeadAI's {svc} is reaching its limit"
        o_msg = f"LeadAI's {svc} is reaching its limit — {soft}. Our team has been notified."
    o_data = {"provider": provider, "level": level, "scope": PLATFORM}
    if cfg["notify_org_admins"]:
        _fanout_org_admins(orgs, NTYPE[level], o_title, o_msg, SEVERITY[level], o_data)
    if cfg["notify_users"] and (level == "exhausted" or (level == "recovered" and told_level == "exhausted")):
        _fanout_users([str(o["_id"]) for o in orgs], NTYPE[level], o_title, o_msg, SEVERITY[level], o_data)


def _org_texts(status: Dict[str, Any], key: str, level: str, m: Optional[Dict[str, Any]]) -> tuple:
    provider = status["provider"]
    if provider == "plan" and m is not None:
        plan = status.get("plan_name") or "your plan"
        if level == "exhausted":
            title = f"{m['label']}: limit reached"
            msg = (f"{describe_metric(m)} — the limit of {plan} is reached. Upgrade or wait for the next "
                   "period to continue.")
        else:
            pct = f"{m['percent']:g}%" if m.get("percent") is not None else ""
            title = f"{m['label']}: {pct} used".strip()
            msg = f"{describe_metric(m)} on {plan}. At 100% this stops until the next period or an upgrade."
        return title, msg
    label = {"apify": "Your Apify account", "gemini": "Your Gemini key", "tokens": "Your LeadAI tokens"}.get(
        provider, status.get("label") or provider)
    return f"{label} {TITLE_WORDS[level]}", status.get("message") or ""


def _user_texts(status: Dict[str, Any], m: Optional[Dict[str, Any]], level: str) -> tuple:
    provider = status["provider"]
    if level == "recovered":
        return "Back to normal", "Your organization's limit problem is solved. You can continue."
    if provider == "plan" and m is not None:
        return (f"{m['label']}: limit reached",
                f"Your organization reached its limit for {m['label'].lower()}. An admin can upgrade the plan; "
                "otherwise it resets next period.")
    if provider == "tokens":
        return ("Your organization is out of tokens",
                "Your organization has no tokens left for new searches. Ask an admin to top up or upgrade.")
    what = {"apify": ("scraping account", "Searches are paused"),
            "gemini": ("AI analysis key", "Comments are scored by the keyword rules")}.get(
        provider, ("service", "Some features are paused"))
    return (f"Your organization's {what[0]} reached its limit",
            f"{what[1]} until an admin fixes your organization's {what[0]}.")


def _dispatch_org(status: Dict[str, Any], key: str, level: str, m: Optional[Dict[str, Any]],
                  cfg: Dict[str, bool], *, admins_notified: bool = False) -> None:
    from app.events.notifications import notify_org_admins
    org_id = status.get("organization_id")
    if not org_id:
        return
    provider = status["provider"]
    data = _data(status, key, level)
    if provider in ("plan", "tokens") and level == "recovered":
        return  # a new period resets the meters: no "recovered" notice every month
    # token thresholds are told by app.billing.tokens; a live own-key error only
    # when tenant_api_keys.record_key_failure actually told the admins
    admins_told_elsewhere = provider == "tokens" or (admins_notified and provider in ("apify", "gemini"))
    if not admins_told_elsewhere:
        title, msg = _org_texts(status, key, level, m)
        ntype = "quota_warning" if provider == "plan" else NTYPE[level]
        notify_org_admins(str(org_id), ntype, title, msg, severity=SEVERITY[level], link=ORG_LINK, data=data,
                          email=bool(cfg["email_admins"] and level in ("critical", "error", "exhausted")))
    if cfg["notify_users"] and level == "exhausted":
        u_title, u_msg = _user_texts(status, m, level)
        _fanout_users([str(org_id)], "quota_warning" if provider == "plan" else NTYPE[level], u_title, u_msg,
                      SEVERITY[level], {"provider": provider, "level": level, "metric": key})


# ── fan-out ─────────────────────────────────────────────────────────────────

def _dependent_orgs(provider: str) -> List[Dict[str, Any]]:
    """Organizations that use LeadAI's ``provider``: Apify / Gemini when their
    coverage is "leadai"; email / SMS: every working organization."""
    db = get_sync_db()
    if db is None:
        return []
    from app.services.tenant_api_keys import coverage_of
    proj = {"name": 1, "status": 1, "settings": 1, "admin_portal_enabled": 1, "api_coverage": 1,
            "api_mode": 1, "custom_api_keys": 1}
    out = []
    for org in db.organizations.find({"status": {"$in": ["active", "demo", "trial"]}}, proj):
        if provider in ("apify", "gemini") and coverage_of(org).get(provider) != "leadai":
            continue
        org.pop("custom_api_keys", None)
        out.append(org)
        if len(out) >= MAX_FANOUT_ORGS:
            logger.warning("api limit fan-out capped at %d organizations", MAX_FANOUT_ORGS)
            break
    return out


def _insert_many(docs: Iterable[Dict[str, Any]]) -> int:
    db = get_sync_db()
    if db is None:
        return 0
    batch: List[Dict[str, Any]] = []
    n = 0
    for d in docs:
        batch.append(d)
        if len(batch) >= _CHUNK:
            db.notifications.insert_many(batch)
            n += len(batch)
            batch = []
    if batch:
        db.notifications.insert_many(batch)
        n += len(batch)
    return n


def _fanout_org_admins(orgs: List[Dict[str, Any]], ntype: str, title: str, message: str, severity: str,
                       data: Dict[str, Any]) -> int:
    from app.events.notifications import _doc, org_allows

    def docs():
        for org in orgs:
            oid = str(org["_id"])
            if not org_allows(oid, ntype, severity, dict(org.get("settings") or {})):
                continue
            portal = bool(org.get("admin_portal_enabled")) or org.get("status") == "active"
            yield _doc("org_admin", ntype, title, message, organization_id=oid, severity=severity,
                       link=ORG_LINK if portal else "/dashboard#billing", data=data)
    try:
        return _insert_many(docs())
    except Exception as e:
        logger.warning("api limit org admin fan-out failed: %s", e)
        return 0


def _fanout_users(org_ids: List[str], ntype: str, title: str, message: str, severity: str,
                  data: Dict[str, Any]) -> int:
    """In-app notice to the non-admin members of ``org_ids`` (preferences apply)."""
    db = get_sync_db()
    if db is None or not org_ids:
        return 0
    from bson import ObjectId

    from app.api.routes.me import notification_allowed
    from app.events.notifications import _doc
    try:
        members: List[Dict[str, Any]] = []
        for org_id in org_ids:
            room = MAX_FANOUT_USERS - len(members)
            if room <= 0:
                logger.warning("api limit user fan-out capped at %d users", MAX_FANOUT_USERS)
                break
            members += list(db.organization_members.find(
                {"organization_id": org_id, "status": "active", "role": {"$nin": ["owner", "admin"]}},
                {"user_id": 1, "organization_id": 1}).limit(min(MAX_USERS_PER_ORG, room)))
        oids = [ObjectId(str(mb["user_id"])) for mb in members if ObjectId.is_valid(str(mb.get("user_id")))]
        users = {str(u["_id"]): u for u in db.users.find({"_id": {"$in": oids}},
                                                          {"status": 1, "notification_preferences": 1})}

        def docs():
            for mb in members:
                u = users.get(str(mb.get("user_id")))
                if not u or u.get("status", "active") != "active" or not notification_allowed(u, ntype, "in_app"):
                    continue
                yield _doc("user", ntype, title, message, user_id=str(mb["user_id"]),
                           organization_id=str(mb["organization_id"]), severity=severity, link=USER_LINK,
                           data=data)
        return _insert_many(docs())
    except Exception as e:
        logger.warning("api limit user fan-out failed: %s", e)
        return 0
