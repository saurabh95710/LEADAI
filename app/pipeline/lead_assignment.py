"""
Automatic lead assignment — acts on ``organizations.settings.lead_assignment``.

Modes (see app/api/routes/organizations.py ``_SETTING_CHOICES``):

  * ``manual``        — nothing happens; an Admin/Manager assigns by hand.
  * ``round_robin``   — new leads rotate across the organization's ACTIVE
                        members (membership AND account active) who hold
                        ``leads.view``; viewers are never picked.
  * ``creator`` / ``search_owner`` — the user who ran the search (the lead's
                        ``user_id``), if they are still an eligible member.

Rules:
  * Only a lead that is not assigned yet and was never auto-assigned is
    touched; the write is conditional (``assigned_user_id`` still empty), so
    two workers can never both assign the same lead.
  * The round-robin cursor lives in ``lead_assignment_cursors`` (one document
    per organization) and is advanced with an atomic ``$inc`` — concurrent
    runs each get their own slot, never the same member twice in a row.
  * Candidates are drawn from the lead's own organization only; an assignee
    outside it is impossible by construction and re-checked before writing.
  * The same fields as the manual endpoint (PATCH /api/leads/{id}) are set:
    ``assigned_user_id``, ``assigned_to`` (email) and ``lead_updated_at``;
    plus an ``assignment_history`` entry, an audit record
    (``leads.auto_assigned``) and a ``lead_assigned`` notification that
    respects the assignee's preferences and the org's ``notify_lead_assigned``.

Everything here is best effort and never raises into the pipeline.
"""
import logging
from typing import Any, Dict, List, Optional

from bson import ObjectId

from app.db.models import utcnow

logger = logging.getLogger(__name__)

CURSOR_COLLECTION = "lead_assignment_cursors"
MODES = ("manual", "round_robin", "creator", "search_owner")
_OWNER_MODES = ("creator", "search_owner")
_EXCLUDED_ROLES = ("viewer",)


def _oid(v: Any) -> Optional[ObjectId]:
    try:
        return ObjectId(str(v))
    except Exception:
        return None


def _org_ids(org_id: str) -> List[Any]:
    o = _oid(org_id)
    return [str(org_id), o] if o is not None else [str(org_id)]


def assignment_mode(org_doc: Optional[Dict[str, Any]]) -> str:
    mode = ((org_doc or {}).get("settings") or {}).get("lead_assignment") or "manual"
    return mode if mode in MODES else "manual"


def eligible_assignees(db, org_id: str, org_doc: Optional[Dict[str, Any]] = None
                       ) -> List[Dict[str, Any]]:
    """Active, non-viewer members of ``org_id`` holding ``leads.view``, in a
    stable order (user id) so the round-robin rotation is deterministic."""
    from app.auth import permissions as P
    if org_doc is None:
        org_doc = db.organizations.find_one({"_id": _oid(org_id)}) or {}
    members = list(db.organization_members.find(
        {"organization_id": {"$in": _org_ids(org_id)}, "status": "active",
         "role": {"$nin": list(_EXCLUDED_ROLES)}}))
    oids = [o for o in (_oid(m.get("user_id")) for m in members) if o is not None]
    users = {str(u["_id"]): u for u in db.users.find(
        {"_id": {"$in": oids}}, {"email": 1, "name": 1, "status": 1})} if oids else {}
    out = []
    for m in members:
        u = users.get(str(m.get("user_id")))
        if not u or u.get("status", "active") != "active":
            continue
        try:
            perms = P.resolve_org_permissions(m.get("role") or "member", org_doc, m)
        except Exception:
            perms = set()
        if P.LEADS_VIEW not in perms:
            continue
        out.append({"user_id": str(u["_id"]), "email": u.get("email"), "name": u.get("name")})
    out.sort(key=lambda x: x["user_id"])
    return out


def next_cursor(db, org_id: str) -> int:
    """Atomically advance and return the organization's round-robin cursor
    (1, 2, 3, …). Safe under concurrency: every caller gets a distinct value."""
    from pymongo import ReturnDocument
    from pymongo.errors import DuplicateKeyError
    for _ in range(3):
        try:
            doc = db[CURSOR_COLLECTION].find_one_and_update(
                {"_id": str(org_id)},
                {"$inc": {"seq": 1}, "$set": {"updated_at": utcnow()}},
                upsert=True, return_document=ReturnDocument.AFTER)
            return int(doc.get("seq") or 1)
        except DuplicateKeyError:  # two first-ever upserts raced; retry the $inc
            continue
    raise RuntimeError("could not advance the lead assignment cursor")


def pick_assignee(db, org_id: str, lead: Dict[str, Any],
                  org_doc: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """The member a new lead should go to under the org's mode, or None."""
    if org_doc is None:
        org_doc = db.organizations.find_one({"_id": _oid(org_id)}) or {}
    mode = assignment_mode(org_doc)
    if mode == "manual":
        return None
    candidates = eligible_assignees(db, org_id, org_doc)
    if not candidates:
        return None
    if mode in _OWNER_MODES:
        owner = str(lead.get("user_id") or "")
        return next((c for c in candidates if c["user_id"] == owner), None)
    seq = next_cursor(db, org_id)
    return candidates[(seq - 1) % len(candidates)]


def _is_member(db, org_id: str, user_id: str) -> bool:
    return db.organization_members.find_one(
        {"organization_id": {"$in": _org_ids(org_id)}, "user_id": str(user_id),
         "status": "active"}, {"_id": 1}) is not None


def auto_assign_lead(db, lead_ref: Any) -> Optional[Dict[str, Any]]:
    """Assign one lead according to its organization's setting.

    ``lead_ref`` is the ai_comments ``_id`` (or the document itself).
    Returns ``{"user_id", "email", "mode"}`` when assigned, else None.
    Never raises."""
    try:
        lead = lead_ref if isinstance(lead_ref, dict) else db.ai_comments.find_one({"_id": _oid(lead_ref)})
        if not lead or not lead.get("is_lead"):
            return None
        if lead.get("assigned_user_id") or lead.get("auto_assignment"):
            return None
        org_id = str(lead.get("organization_id") or "")
        if not org_id:
            return None
        org_doc = db.organizations.find_one({"_id": _oid(org_id)})
        if not org_doc:
            return None
        mode = assignment_mode(org_doc)
        if mode == "manual":
            return None
        assignee = pick_assignee(db, org_id, lead, org_doc)
        if not assignee or not _is_member(db, org_id, assignee["user_id"]):
            return None
        now = utcnow()
        from app.pipeline.lead_lifecycle import create_assignment_history_entry
        entry = create_assignment_history_entry(None, assignee["user_id"], assignee.get("email"),
                                                changed_by="system", method=f"auto:{mode}")
        res = db.ai_comments.update_one(
            {"_id": lead["_id"], "organization_id": {"$in": _org_ids(org_id)}, "is_lead": True,
             "assigned_user_id": {"$in": [None, ""]}, "auto_assignment": {"$exists": False}},
            {"$set": {"assigned_user_id": assignee["user_id"], "assigned_to": assignee.get("email"),
                      "lead_updated_at": now,
                      "auto_assignment": {"mode": mode, "at": now, "user_id": assignee["user_id"]}},
             "$push": {"assignment_history": entry}})
        if not res.modified_count:
            return None  # assigned concurrently (by a person or another worker)
        _after_assign(lead, org_id, assignee, mode)
        return {"user_id": assignee["user_id"], "email": assignee.get("email"), "mode": mode}
    except Exception as e:
        logger.warning("[LeadAssignment] auto-assign failed: %s", e)
        return None


def _after_assign(lead: Dict[str, Any], org_id: str, assignee: Dict[str, Any], mode: str) -> None:
    lead_id = str(lead["_id"])
    try:
        from app.admin.audit import audit
        audit("leads.auto_assigned", "leads",
              user={"user_id": None, "email": "system", "role": "pipeline"},
              organization_id=org_id, resource_type="lead", resource_id=lead_id,
              details={"assigned_user_id": assignee["user_id"], "assigned_to": assignee.get("email"),
                       "mode": mode, "search_run_id": lead.get("search_run_id")})
    except Exception as e:  # pragma: no cover
        logger.warning("[LeadAssignment] audit failed: %s", e)
    try:
        from app.events.notifications import notify_user
        who = lead.get("commenter_name") or "A new lead"
        notify_user(assignee["user_id"], "lead_assigned", "New lead assigned to you",
                    f"{who} was assigned to you automatically"
                    + (f" ({lead.get('platform')})" if lead.get("platform") else "") + ".",
                    organization_id=org_id, severity="info", link="/dashboard#leads",
                    data={"lead_id": lead_id, "search_run_id": lead.get("search_run_id"),
                          "mode": mode}, email=True)
    except Exception as e:  # pragma: no cover
        logger.warning("[LeadAssignment] notify failed: %s", e)
