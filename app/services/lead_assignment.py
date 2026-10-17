"""
LeadAI Lead Assignment Rules & SLA Timers Service (Phase 6 Product Feature).

Manages:
- Assignment rules (round-robin over chosen members, optionally limited to a
  priority, intent or minimum score), each with an SLA in hours. They are
  applied to new leads by ``app.pipeline.lead_assignment.auto_assign_lead``.
- The SLA / follow-up sweep, run by the background sweeper
  (``app.lifecycle.maintenance``).
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from bson import ObjectId

from app.db.models import utcnow

logger = logging.getLogger(__name__)

COLL_ASSIGNMENT_RULES = "lead_assignment_rules"
COLL_LEADS = "ai_comments"


def create_assignment_rule(
    db,
    *,
    organization_id: str,
    name: str,
    rule_type: str = "round_robin",
    assignees: list[str],
    criteria: dict[str, Any] | None = None,
    sla_hours: int = 24,
    created_by: str | None = None,
) -> dict[str, Any]:
    """Create an automated lead assignment rule.

    Every assignee must be an active member of the organization who can see
    leads (not a viewer): a lead is never handed to someone outside it."""
    if not assignees:
        raise ValueError("At least one assignee is required")
    from app.pipeline.lead_assignment import eligible_assignees
    eligible = {a["user_id"] for a in eligible_assignees(db, str(organization_id))}
    unknown = [str(a) for a in assignees if str(a) not in eligible]
    if unknown:
        oids = [ObjectId(u) for u in unknown if ObjectId.is_valid(u)]
        emails = {str(u["_id"]): u.get("email") for u in db["users"].find({"_id": {"$in": oids}}, {"email": 1})}
        who = ", ".join(emails.get(u) or "an unknown person" for u in unknown)
        raise ValueError(f"These people can't be given leads: {who}. Choose active members of this "
                         "organization who aren't viewers.")
    assignees = [str(a) for a in assignees]

    rule_id = f"rule_{uuid.uuid4().hex[:10]}"
    now = utcnow()

    doc = {
        "_id": ObjectId(),
        "rule_id": rule_id,
        "organization_id": str(organization_id),
        "name": name,
        "rule_type": rule_type,
        "assignees": assignees,
        "current_index": 0,
        "criteria": criteria or {},
        "sla_hours": max(1, int(sla_hours)),
        "is_active": True,
        "created_by": created_by,
        "created_at": now,
        "updated_at": now,
    }

    db[COLL_ASSIGNMENT_RULES].insert_one(doc)
    doc["id"] = str(doc["_id"])
    logger.info("Created lead assignment rule %s for org %s", rule_id, organization_id)
    return doc


def list_assignment_rules(db, organization_id: str) -> list[dict[str, Any]]:
    """List all assignment rules for an organization."""
    rules = list(db[COLL_ASSIGNMENT_RULES].find({"organization_id": str(organization_id)}).sort("created_at", 1))
    for r in rules:
        r["id"] = str(r["_id"])
    return rules


def delete_assignment_rule(db, rule_id: str, organization_id: str) -> bool:
    """Delete an assignment rule."""
    q: dict[str, Any] = {"organization_id": str(organization_id)}
    if ObjectId.is_valid(rule_id):
        q["$or"] = [{"_id": ObjectId(rule_id)}, {"rule_id": rule_id}]
    else:
        q["rule_id"] = rule_id

    res = db[COLL_ASSIGNMENT_RULES].delete_one(q)
    return res.deleted_count > 0


def assign_lead(
    db,
    lead_id: str,
    lead_doc: dict[str, Any],
    organization_id: str,
) -> dict[str, Any] | None:
    """Assign a lead by the organization's rules, with an SLA timer.

    The one assignment engine is ``app.pipeline.lead_assignment`` (also used by
    the comment pipeline for every new lead); this looks the lead up inside
    ``organization_id`` and hands it over. Returns the assignment
    (``assigned_user_id``, ``sla_hours``, ``sla_due_at`` ...) or None."""
    from app.pipeline.lead_assignment import auto_assign_lead
    q: dict[str, Any] = {"organization_id": str(organization_id)}
    if ObjectId.is_valid(lead_id):
        q["_id"] = ObjectId(lead_id)
    else:
        q["lead_id"] = lead_id
    lead = db[COLL_LEADS].find_one(q)
    if lead is None:
        return None
    done = auto_assign_lead(db, lead)
    if not done:
        return None
    return {"assigned_user_id": done["user_id"], "assigned_to": done.get("email"),
            **{k: v for k, v in done.items() if k.startswith(("sla_", "assignment_rule"))}}


def check_sla_and_reminders(db, now: datetime | None = None) -> dict[str, int]:
    """Flag breached SLAs and overdue follow-ups (run by the background sweep).

    - A lead assigned by a rule that is still ``new`` (not contacted) after its
      ``sla_due_at`` becomes ``sla_status: breached``; its assignee is told once.
    - A pending follow-up whose ``due_at`` has passed is reported to the person
      who set it, once (``overdue_notified_at`` on the follow-up)."""
    from app.events.notifications import notify_user

    current_time = now or utcnow()
    breached = 0
    for lead in db[COLL_LEADS].find({
        "is_lead": True,
        "sla_status": "on_track",
        "sla_due_at": {"$lte": current_time},
        "lead_status": {"$in": [None, "new"]},
    }):
        res = db[COLL_LEADS].update_one(
            {"_id": lead["_id"], "sla_status": "on_track"},
            {"$set": {"sla_status": "breached", "sla_breached_at": current_time,
                      "lead_updated_at": current_time}})
        if not res.modified_count:
            continue
        breached += 1
        logger.warning("Lead %s breached its %sh SLA (assignee: %s)",
                       lead["_id"], lead.get("sla_hours"), lead.get("assigned_user_id"))
        if lead.get("assigned_user_id"):
            try:
                notify_user(str(lead["assigned_user_id"]), "lead_sla_breached", "Lead SLA missed",
                            f"{lead.get('commenter_name') or 'A lead'} was not contacted within "
                            f"{lead.get('sla_hours')} hours.",
                            organization_id=lead.get("organization_id"), severity="warning",
                            link="/dashboard#leads", data={"lead_id": str(lead["_id"])})
            except Exception as e:  # a failed notice never stops the sweep
                logger.warning("SLA breach notice failed for lead %s: %s", lead["_id"], e)

    overdue = 0
    for lead in db[COLL_LEADS].find({"follow_ups": {"$elemMatch": {
            "status": "pending", "due_at": {"$lte": current_time},
            "overdue_notified_at": {"$exists": False}}}}):
        follow_ups = lead.get("follow_ups") or []
        due = [f for f in follow_ups if f.get("status") == "pending" and f.get("due_at")
               and _aware(f["due_at"]) <= _aware(current_time) and not f.get("overdue_notified_at")]
        if not due:
            continue
        for f in due:
            f["overdue_notified_at"] = current_time
        db[COLL_LEADS].update_one({"_id": lead["_id"]}, {"$set": {"follow_ups": follow_ups}})
        overdue += len(due)
        for f in due:
            if not f.get("created_by_user_id"):
                continue
            try:
                notify_user(str(f["created_by_user_id"]), "follow_up_overdue", "Follow-up overdue",
                            f"\"{f.get('title')}\" for {lead.get('commenter_name') or 'a lead'} is overdue.",
                            organization_id=lead.get("organization_id"), severity="warning",
                            link="/dashboard#leads", data={"lead_id": str(lead["_id"])})
            except Exception as e:
                logger.warning("Overdue follow-up notice failed for lead %s: %s", lead["_id"], e)

    return {"sla_breaches_flagged": breached, "overdue_followups_flagged": overdue}


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
