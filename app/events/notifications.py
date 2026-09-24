"""In-app notifications (event driven).

A notification targets one audience:
  * ``super_admin``  — every platform super admin sees it
  * ``org_admin``    — owners/admins of ``organization_id``
  * ``user``         — one user (``user_id``)

Documents: {audience, organization_id, user_id, type, title, message,
severity (info|success|warning|danger), link, data, read_by: [reader ids],
created_at}. ``read_by`` lets a shared (super_admin / org_admin)
notification be marked read per person.

Super Admin event types: demo_requested, demo_approved, registration,
payment_received, subscription_awaiting_approval, subscription_activated,
payment_failed, organization_suspended, high_token_usage, apify_failure,
system_error, security_event.

Organization notification settings (``organizations.settings``) are honoured
here, in one place, so every emitter gets the same behaviour:

  * ``org_admin`` audience notifications of a *configurable* type are dropped
    when the organization switched that type off (``ORG_SETTING_FOR_TYPE``;
    default on, except ``notify_on_leads`` which defaults off like the UI);
  * security / billing-critical types (``ALWAYS_DELIVERED``) and usage alerts
    with severity ``danger`` (limit reached) are always delivered;
  * ``lead_assigned`` to a user also honours ``notify_lead_assigned``;
  * email for organization events (``email=True``) is only queued when the
    organization's ``email_notifications`` is on AND the recipient's personal
    email preference allows the type.
"""
import logging
from typing import Any, Dict, List, Optional

from app.db.models import utcnow
from app.db.mongo import get_async_db, get_sync_db

logger = logging.getLogger(__name__)

COLLECTION = "notifications"

SUPER_ADMIN_TYPES = (
    "demo_requested", "demo_approved", "registration", "payment_received",
    "subscription_awaiting_approval", "subscription_activated", "payment_failed",
    "organization_suspended", "high_token_usage", "apify_failure",
    "system_error", "security_event", "contact_message",
)


def _doc(audience: str, ntype: str, title: str, message: str, *,
         organization_id: Optional[str] = None, user_id: Optional[str] = None,
         severity: str = "info", link: Optional[str] = None,
         data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "audience": audience,
        "organization_id": str(organization_id) if organization_id else None,
        "user_id": str(user_id) if user_id else None,
        "type": ntype,
        "title": title[:200],
        "message": (message or "")[:1000],
        "severity": severity,
        "link": link,
        "data": data or {},
        "read_by": [],
        "created_at": utcnow(),
    }


# notification type -> organizations.settings key that switches it off.
ORG_SETTING_FOR_TYPE: Dict[str, str] = {
    "job_completed": "notify_job_completion",
    "job_failed": "notify_job_completion",
    "search_completed": "notify_job_completion",
    "search_failed": "notify_job_completion",
    "usage_threshold": "notify_usage_warnings",
    "usage_warning": "notify_usage_warnings",
    "quota_warning": "notify_usage_warnings",
    "tokens_low": "notify_usage_warnings",
    "high_token_usage": "notify_usage_warnings",
    "leads_found": "notify_on_leads",
    "new_leads": "notify_on_leads",
    "lead_assigned": "notify_lead_assigned",
}
# Settings that are OFF unless the organization turned them on (matches the
# Admin portal toggles).
_ORG_SETTING_DEFAULTS: Dict[str, bool] = {"notify_on_leads": False}
# Never suppressible by an organization setting.
ALWAYS_DELIVERED = frozenset({
    "security_event", "payment_status", "payment_failed", "payment_received",
    "subscription_activated", "subscription_awaiting_approval", "subscription_cancelled",
    "organization_suspended", "user_suspended", "demo_expired", "system_error",
})
_USAGE_TYPES = frozenset(t for t, k in ORG_SETTING_FOR_TYPE.items()
                         if k == "notify_usage_warnings")


def _org_settings(organization_id: Optional[str]) -> Dict[str, Any]:
    if not organization_id:
        return {}
    try:
        from bson import ObjectId
        db = get_sync_db()
        if db is None or not ObjectId.is_valid(str(organization_id)):
            return {}
        org = db.organizations.find_one({"_id": ObjectId(str(organization_id))}, {"settings": 1})
        return dict((org or {}).get("settings") or {})
    except Exception as e:
        logger.debug("org settings lookup failed: %s", e)
        return {}


def org_allows(organization_id: Optional[str], ntype: str, severity: str = "info",
               settings: Optional[Dict[str, Any]] = None) -> bool:
    """Whether the organization's notification settings allow ``ntype``."""
    if ntype in ALWAYS_DELIVERED:
        return True
    if ntype in _USAGE_TYPES and severity == "danger":
        return True  # a limit actually reached is billing-critical
    key = ORG_SETTING_FOR_TYPE.get(ntype)
    if not key:
        return True
    s = _org_settings(organization_id) if settings is None else settings
    return bool(s.get(key, _ORG_SETTING_DEFAULTS.get(key, True)))


def org_email_enabled(organization_id: Optional[str],
                      settings: Optional[Dict[str, Any]] = None) -> bool:
    s = _org_settings(organization_id) if settings is None else settings
    return bool(s.get("email_notifications", True))


def _email(to_email: Optional[str], title: str, message: str, ntype: str,
           organization_id: Optional[str], link: Optional[str]) -> None:
    if not to_email:
        return
    try:
        from app.events.email import absolute_url, send_email
        body = (message or title) + (f"\n\n{absolute_url(link)}" if link else "")
        send_email(to_email, title, body, kind=f"notification.{ntype}",
                   organization_id=str(organization_id) if organization_id else None)
    except Exception as e:
        logger.warning("notification email failed: %s", e)


def _insert(doc: Dict[str, Any]) -> None:
    try:
        db = get_sync_db()
        if db is not None:
            db[COLLECTION].insert_one(doc)
    except Exception as e:
        logger.warning("notification write failed: %s", e)


def notify_super_admins(ntype: str, title: str, message: str = "", **kw) -> None:
    _insert(_doc("super_admin", ntype, title, message, **kw))


def notify_org_admins(organization_id: str, ntype: str, title: str,
                      message: str = "", *, email: bool = False, **kw) -> bool:
    """Notify the owners/admins of an organization. Dropped when the org
    turned this type off; with ``email=True`` also emails each active
    owner/admin whose personal preferences allow it, if the org has email on.
    Returns whether the in-app notification was written."""
    settings = _org_settings(organization_id)
    if not org_allows(organization_id, ntype, kw.get("severity", "info"), settings):
        return False
    _insert(_doc("org_admin", ntype, title, message,
                 organization_id=organization_id, **kw))
    if email and org_email_enabled(organization_id, settings):
        try:
            from bson import ObjectId
            from app.api.routes.me import notification_allowed
            db = get_sync_db()
            if db is not None:
                uids = [m["user_id"] for m in db.organization_members.find(
                    {"organization_id": str(organization_id), "role": {"$in": ["owner", "admin"]},
                     "status": "active"}, {"user_id": 1})]
                oids = [ObjectId(str(u)) for u in uids if ObjectId.is_valid(str(u))]
                if oids:
                    for u in db.users.find({"_id": {"$in": oids}},
                                           {"email": 1, "status": 1, "notification_preferences": 1}):
                        if u.get("status", "active") == "active" and notification_allowed(u, ntype, "email"):
                            _email(u.get("email"), title, message, ntype, organization_id,
                                   kw.get("link"))
        except Exception as e:
            logger.warning("org admin notification email failed: %s", e)
    return True


def notify_user(user_id: str, ntype: str, title: str, message: str = "",
                organization_id: Optional[str] = None, *, email: bool = False, **kw) -> bool:
    """Honours the user's ``users.notification_preferences`` (in-app channel);
    types without a preference key (security, system) are always delivered.
    ``lead_assigned`` also honours the organization's ``notify_lead_assigned``.
    With ``email=True`` the user is also emailed when the organization has
    ``email_notifications`` on and their own email preference allows it.
    Returns whether the in-app notification was written."""
    settings: Optional[Dict[str, Any]] = None
    if organization_id and ntype == "lead_assigned":
        settings = _org_settings(organization_id)
        if not org_allows(organization_id, ntype, kw.get("severity", "info"), settings):
            return False
    u = None
    try:
        from bson import ObjectId
        from app.api.routes.me import notification_allowed
        db = get_sync_db()
        if db is not None and ObjectId.is_valid(str(user_id)):
            u = db.users.find_one({"_id": ObjectId(str(user_id))},
                                  {"notification_preferences": 1, "email": 1})
            if u is not None and not notification_allowed(u, ntype, "in_app"):
                return False
    except Exception as e:  # a preference lookup must never drop a notification silently by error
        logger.debug("notification preference check failed: %s", e)
    _insert(_doc("user", ntype, title, message, user_id=user_id,
                 organization_id=organization_id, **kw))
    if email and u is not None and organization_id:
        try:
            from app.api.routes.me import notification_allowed
            if org_email_enabled(organization_id, settings) and notification_allowed(u, ntype, "email"):
                _email(u.get("email"), title, message, ntype, organization_id, kw.get("link"))
        except Exception as e:
            logger.warning("user notification email failed: %s", e)
    return True


def _org_id_variants(org_id: str) -> List[Any]:
    from bson import ObjectId
    return [org_id, ObjectId(org_id)] if ObjectId.is_valid(org_id) else [org_id]


def notify_search_finished(owner: Optional[Dict[str, Any]], run_id: Optional[str], *,
                           success: bool, platform: str = "", posts: int = 0,
                           comments: int = 0, error: Optional[str] = None) -> None:
    """Organization-level job completion / failure notice for the owners and
    admins (``notify_job_completion``), plus a "new leads" notice
    (``notify_on_leads``) when the run produced leads. Both also email when
    the organization has ``email_notifications`` on. Never raises.

    Called where a URL search finishes (app/social/url_search.py)."""
    try:
        owner = owner or {}
        org_id = str(owner.get("organization_id") or "") or None
        if not org_id:
            return
        who = owner.get("created_by") or "A team member"
        data = {"search_run_id": run_id, "user_id": str(owner.get("user_id") or "") or None}
        link = f"/org-admin#searches/{run_id}" if run_id else "/org-admin#searches"
        if not success:
            notify_org_admins(org_id, "job_failed", "Search failed",
                              f"{who}'s {platform} search failed: {(error or 'unknown error')[:300]}",
                              severity="danger", link=link, data=data, email=True)
            return
        notify_org_admins(org_id, "job_completed", "Search completed",
                          f"{who}'s {platform} search finished — {posts} posts, {comments} comments.",
                          severity="success", link=link, data=data, email=True)
        db = get_sync_db()
        leads = 0
        if db is not None and run_id:
            leads = db.ai_comments.count_documents(
                {"search_run_id": run_id, "is_lead": True,
                 "organization_id": {"$in": _org_id_variants(org_id)}})
        if leads:
            plural = "s" if leads != 1 else ""
            notify_org_admins(org_id, "leads_found", f"{leads} new lead{plural} found",
                              f"{who}'s {platform} search found {leads} lead{plural}.",
                              severity="success", link=f"/org-admin#leads?run_id={run_id}",
                              data={**data, "leads": leads}, email=True)
    except Exception as e:  # pragma: no cover - notifications are best effort
        logger.warning("search finished notification failed: %s", e)


def audience_query(*, is_super_admin: bool, user_id: Optional[str],
                   organization_id: Optional[str], org_role: Optional[str]) -> Dict[str, Any]:
    """Mongo filter for the notifications one person may see."""
    clauses: List[Dict[str, Any]] = []
    if user_id:
        clauses.append({"audience": "user", "user_id": str(user_id)})
    if organization_id and org_role in ("owner", "admin"):
        clauses.append({"audience": "org_admin", "organization_id": str(organization_id)})
    if is_super_admin:
        clauses.append({"audience": "super_admin"})
    if not clauses:
        return {"_id": None}
    return {"$or": clauses}


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


async def list_for(query: Dict[str, Any], reader_id: str, *, unread_only: bool = False,
                   limit: int = 50, skip: int = 0) -> Dict[str, Any]:
    db = get_async_db()
    if db is None:
        return {"items": [], "total": 0, "unread": 0}
    unread_q = {"$and": [query, {"read_by": {"$ne": reader_id}}]}
    q = unread_q if unread_only else query
    total = await db[COLLECTION].count_documents(q)
    unread = await db[COLLECTION].count_documents(unread_q)
    items = []
    cursor = db[COLLECTION].find(q).sort("created_at", -1).skip(skip).limit(limit)
    async for d in cursor:
        items.append({
            "id": str(d["_id"]),
            "type": d.get("type"),
            "title": d.get("title"),
            "message": d.get("message"),
            "severity": d.get("severity", "info"),
            "link": d.get("link"),
            "data": d.get("data", {}),
            "read": reader_id in (d.get("read_by") or []),
            "created_at": _iso(d.get("created_at")),
        })
    return {"items": items, "total": total, "unread": unread}


async def mark_read(query: Dict[str, Any], reader_id: str,
                    notification_id: Optional[str] = None) -> int:
    from bson import ObjectId
    db = get_async_db()
    if db is None:
        return 0
    q = query
    if notification_id:
        try:
            q = {"$and": [query, {"_id": ObjectId(notification_id)}]}
        except Exception:
            return 0
    res = await db[COLLECTION].update_many(q, {"$addToSet": {"read_by": reader_id}})
    return res.modified_count
