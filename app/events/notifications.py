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
                      message: str = "", **kw) -> None:
    _insert(_doc("org_admin", ntype, title, message,
                 organization_id=organization_id, **kw))


def notify_user(user_id: str, ntype: str, title: str, message: str = "",
                organization_id: Optional[str] = None, **kw) -> None:
    """Honours the user's ``users.notification_preferences`` (in-app channel);
    types without a preference key (security, system) are always delivered."""
    try:
        from bson import ObjectId
        from app.api.routes.me import notification_allowed
        db = get_sync_db()
        if db is not None and ObjectId.is_valid(str(user_id)):
            u = db.users.find_one({"_id": ObjectId(str(user_id))}, {"notification_preferences": 1})
            if u is not None and not notification_allowed(u, ntype, "in_app"):
                return
    except Exception as e:  # a preference lookup must never drop a notification silently by error
        logger.debug("notification preference check failed: %s", e)
    _insert(_doc("user", ntype, title, message, user_id=user_id,
                 organization_id=organization_id, **kw))


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
