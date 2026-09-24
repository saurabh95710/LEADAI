"""
Lead Lifecycle — state machine, status history, notes, follow-ups.

The lead lifecycle controls how a qualified AI comment becomes a managed lead
with a traceable history of status changes, notes, and follow-ups.

State Machine:
  new → contacted → qualified → follow_up → converted
  new → disqualified
  new → lost
  contacted → lost
  qualified → lost
  follow_up → lost
  any → archived (admin only)

Rules:
  - AI analysis must NOT overwrite manual status changes
  - Every status change is recorded in status_history
  - Notes and follow-ups are append-only with update capability
  - Terminal states (converted, lost, disqualified, archived) require explicit action
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Lead Statuses ────────────────────────────────────────────────────────────

LEAD_STATUSES = ("new", "contacted", "qualified", "follow_up",
                 "converted", "lost", "disqualified", "archived")

TERMINAL_STATUSES = ("converted", "lost", "disqualified", "archived")

# ── Valid State Transitions ──────────────────────────────────────────────────
# Maps current status → set of allowed next statuses.
# An empty set means the status is terminal (no transitions out).

VALID_TRANSITIONS: Dict[str, Tuple[str, ...]] = {
    "new":         ("contacted", "qualified", "follow_up", "disqualified", "lost", "archived"),
    "contacted":   ("qualified", "follow_up", "lost", "archived"),
    "qualified":   ("follow_up", "converted", "lost", "archived"),
    "follow_up":   ("contacted", "qualified", "converted", "lost", "archived"),
    "converted":   ("archived",),
    "lost":        ("archived",),
    "disqualified": ("archived",),
    "archived":    (),  # terminal — no transitions out
}

# ── Lead Priorities ──────────────────────────────────────────────────────────

LEAD_PRIORITIES = ("high", "medium", "low")

# ── Follow-up Statuses ──────────────────────────────────────────────────────

FOLLOW_UP_STATUSES = ("pending", "completed", "cancelled", "overdue")

# ── Status Display Labels ────────────────────────────────────────────────────

STATUS_LABELS = {
    "new": "New",
    "contacted": "Contacted",
    "qualified": "Qualified",
    "follow_up": "Follow-up",
    "converted": "Converted",
    "lost": "Lost",
    "disqualified": "Disqualified",
    "archived": "Archived",
}


def can_transition(current: str, target: str) -> bool:
    """Check if a status transition is allowed."""
    allowed = VALID_TRANSITIONS.get(current, ())
    return target in allowed


def validate_transition(current: str, target: str) -> Tuple[bool, str]:
    """Validate a transition and return (valid, error_message)."""
    if current == target:
        return True, ""
    if current not in LEAD_STATUSES:
        return False, f"Invalid current status: {current}"
    if target not in LEAD_STATUSES:
        return False, f"Invalid target status: {target}"
    if not can_transition(current, target):
        return False, f"Cannot transition from {current!r} to {target!r}"
    return True, ""


def is_terminal(status: str) -> bool:
    """Check if a status is terminal (no further transitions except archived)."""
    return status in TERMINAL_STATUSES


def create_status_history_entry(
    from_status: str,
    to_status: str,
    changed_by: str = "system",
    reason: str = "",
) -> Dict[str, Any]:
    """Create a status history entry."""
    return {
        "from_status": from_status,
        "to_status": to_status,
        "changed_at": datetime.now(timezone.utc),
        "changed_by": changed_by,
        "reason": reason,
    }


def create_note(
    text: str,
    author: str = "user",
) -> Dict[str, Any]:
    """Create a note entry."""
    now = datetime.now(timezone.utc)
    return {
        "text": text.strip(),
        "author": author,
        "created_at": now,
        "updated_at": now,
    }


def create_follow_up(
    title: str,
    due_at: Optional[datetime] = None,
    notes: str = "",
    created_by: str = "user",
) -> Dict[str, Any]:
    """Create a follow-up entry."""
    return {
        "title": title.strip(),
        "due_at": due_at,
        "status": "pending",
        "notes": notes.strip() if notes else "",
        "created_by": created_by,
        "created_at": datetime.now(timezone.utc),
        "completed_at": None,
    }


def complete_follow_up(follow_up: Dict[str, Any]) -> Dict[str, Any]:
    """Mark a follow-up as completed."""
    follow_up["status"] = "completed"
    follow_up["completed_at"] = datetime.now(timezone.utc)
    return follow_up


def cancel_follow_up(follow_up: Dict[str, Any]) -> Dict[str, Any]:
    """Mark a follow-up as cancelled."""
    follow_up["status"] = "cancelled"
    follow_up["cancelled_at"] = datetime.now(timezone.utc)
    return follow_up


def check_overdue_follow_ups(follow_ups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Check for overdue follow-ups and update their status."""
    now = datetime.now(timezone.utc)
    for fu in follow_ups:
        if fu.get("status") == "pending" and fu.get("due_at"):
            due = fu["due_at"]
            if isinstance(due, datetime):
                if due.tzinfo is None:
                    due = due.replace(tzinfo=timezone.utc)
                if due < now:
                    fu["status"] = "overdue"
    return follow_ups


def should_create_lead(ai_analysis: Dict[str, Any]) -> bool:
    """Determine if an AI analysis qualifies as a lead.
    
    Rules:
      - Must be useful (is_useful=True)
      - Must have is_lead=True (from signal analysis)
      - Must not be spam (spam_score < 0.8)
    """
    if not ai_analysis.get("is_useful"):
        return False
    if not ai_analysis.get("is_lead"):
        return False
    spam_score = ai_analysis.get("spam_score", 0) or 0
    if spam_score >= 0.8:
        return False
    return True


def initialize_lead_status(ai_analysis: Dict[str, Any]) -> str:
    """Determine initial lead status based on AI analysis.
    
    Rules:
      - High priority + hot quality → "new" (strong lead)
      - Medium priority or warm → "new" (qualified lead)
      - Low priority or cold → "new" (basic lead)
      - All start as "new" — human decides next step
    """
    return "new"


def get_lead_priority_from_score(lead_score: int, priority: str = "low") -> str:
    """Derive operational priority from AI score and priority.
    
    This is an initial suggestion. Human can override.
    """
    if lead_score >= 80 or priority == "high":
        return "high"
    if lead_score >= 50 or priority == "medium":
        return "medium"
    return "low"


def update_lead_status(
    lead: Dict[str, Any],
    new_status: str,
    changed_by: str = "user",
    reason: str = "",
) -> Tuple[bool, str, Dict[str, Any]]:
    """Update lead status with validation and history recording.
    
    Returns (success, error_message, updated_lead).
    """
    current_status = lead.get("lead_status", "new")
    valid, error = validate_transition(current_status, new_status)
    if not valid:
        return False, error, lead

    # Record history
    history_entry = create_status_history_entry(
        current_status, new_status, changed_by, reason
    )
    lead.setdefault("status_history", []).append(history_entry)
    lead["lead_status"] = new_status
    lead["lead_updated_at"] = datetime.now(timezone.utc)

    return True, "", lead
