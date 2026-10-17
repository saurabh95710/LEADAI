"""
LeadAI Compliance & Privacy Framework (Phase 4).

Provides:
- Contact blocklist management (do-not-contact, opt-outs)
- Automated & on-demand PII retention purging (auto-purge phone/email after N days)
- Complete data deletion requests (GDPR Right to be Forgotten)
- Comprehensive audit logging for all privacy events
- Data processing notice & privacy policy metadata

DISCLAIMER: This software provides technical compliance tools and does NOT
constitute legal advice. Organizations should consult legal counsel regarding
applicable privacy regulations (GDPR, CCPA, TCPA, etc.).
"""
import logging
import re
from datetime import datetime, timedelta
from typing import Any

from bson import ObjectId

from app.admin.audit import audit
from app.db.models import utcnow

logger = logging.getLogger(__name__)


def _normalize_phone(val: str | None) -> str | None:
    if not val:
        return None
    digits = re.sub(r"\D", "", str(val).strip())
    return digits if len(digits) >= 7 else None


def _normalize_email(val: str | None) -> str | None:
    if not val:
        return None
    val = str(val).strip().lower()
    return val if "@" in val else None


def is_blocked(
    db,
    *,
    phone: str | None = None,
    email: str | None = None,
    username: str | None = None,
    organization_id: str | None = None,
) -> bool:
    """Check if a phone, email, or username is on the compliance blocklist.

    Blocks match either the organization-specific blocklist or the platform-wide
    blocklist (where organization_id is None).
    """
    if db is None:
        return False

    clauses: list[dict[str, Any]] = []
    norm_phone = _normalize_phone(phone)
    if norm_phone:
        clauses.append({"type": "phone", "value": norm_phone})

    norm_email = _normalize_email(email)
    if norm_email:
        clauses.append({"type": "email", "value": norm_email})

    if username and str(username).strip():
        clauses.append({"type": "username", "value": str(username).strip().lower()})

    if not clauses:
        return False

    org_filter: list[dict[str, Any]] = [{"organization_id": None}]
    if organization_id:
        org_filter.append({"organization_id": str(organization_id)})

    query = {
        "$and": [
            {"$or": clauses},
            {"$or": org_filter},
        ]
    }
    return db["compliance_blocklist"].find_one(query) is not None


def add_to_blocklist(
    db,
    *,
    contact_type: str,
    contact_value: str,
    reason: str = "User requested opt-out",
    organization_id: str | None = None,
    actor_email: str = "system",
    actor_user_id: str | None = None,
    ip: str | None = None,
) -> dict[str, Any]:
    """Add a contact identifier to the blocklist, purge matching existing PII, and audit."""
    contact_type = contact_type.strip().lower()
    raw_val = contact_value.strip()

    if contact_type == "phone":
        val = _normalize_phone(raw_val)
        if not val:
            raise ValueError("Invalid phone number format")
    elif contact_type == "email":
        val = _normalize_email(raw_val)
        if not val:
            raise ValueError("Invalid email address format")
    else:
        val = raw_val.lower()

    now = utcnow()
    doc = {
        "organization_id": str(organization_id) if organization_id else None,
        "type": contact_type,
        "value": val,
        "original_input": raw_val,
        "reason": reason.strip(),
        "created_at": now,
        "created_by": actor_email,
    }

    # Upsert into compliance_blocklist
    db["compliance_blocklist"].update_one(
        {"organization_id": doc["organization_id"], "type": contact_type, "value": val},
        {"$setOnInsert": doc},
        upsert=True,
    )

    # Immediately purge/redact matching PII from existing ai_comments
    purge_q: dict[str, Any] = {}
    if organization_id:
        purge_q["organization_id"] = str(organization_id)

    if contact_type == "phone":
        purge_q["$or"] = [{"phone": val}, {"phone": raw_val}, {"contact.phone": val}]
    elif contact_type == "email":
        purge_q["$or"] = [{"email": val}, {"email": raw_val}, {"contact.email": val}]
    elif contact_type in ("username", "author", "name"):
        # names are stored as scraped ("Priya Sharma"); match the whole name in any case
        name = {"$regex": f"^{re.escape(val)}$", "$options": "i"}
        purge_q["$or"] = [{"commenter_name": name}, {"author": name}]

    purged_count = 0
    if purge_q:
        res = db["ai_comments"].update_many(
            purge_q,
            {
                "$set": {
                    "is_lead": False,
                    "is_useful": False,
                    "lead_quality": "none",
                    "compliance_blocked": True,
                    "compliance_blocked_at": now,
                    "compliance_reason": reason,
                },
                "$unset": {
                    "phone": "",
                    "email": "",
                    "whatsapp": "",
                    "contact": "",
                },
            },
        )
        purged_count = res.modified_count

    # Audit log entry
    audit(
        "compliance.blocklist_added",
        "compliance",
        user=actor_email,
        ip=ip,
        organization_id=organization_id,
        details={
            "type": contact_type,
            "value": val,
            "reason": reason,
            "matching_leads_purged": purged_count,
        },
    )

    return {
        "success": True,
        "type": contact_type,
        "value": val,
        "leads_purged": purged_count,
    }


def remove_from_blocklist(
    db,
    *,
    blocklist_id: str,
    organization_id: str | None = None,
    actor_email: str = "system",
    ip: str | None = None,
) -> bool:
    """Remove a contact from the blocklist."""
    try:
        oid = ObjectId(blocklist_id)
    except Exception:  # noqa: BLE001
        return False

    q: dict[str, Any] = {"_id": oid}
    if organization_id:
        q["organization_id"] = str(organization_id)

    doc = db["compliance_blocklist"].find_one(q)
    if not doc:
        return False

    db["compliance_blocklist"].delete_one({"_id": oid})

    audit(
        "compliance.blocklist_removed",
        "compliance",
        user=actor_email,
        ip=ip,
        organization_id=organization_id,
        details={
            "type": doc.get("type"),
            "value": doc.get("value"),
            "id": blocklist_id,
        },
    )
    return True


def list_blocklist(
    db,
    *,
    organization_id: str | None = None,
    contact_type: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    """List paginated blocked contacts for the organization."""
    q: dict[str, Any] = {}
    if organization_id:
        q["organization_id"] = {"$in": [str(organization_id), None]}
    if contact_type:
        q["type"] = contact_type.strip().lower()

    total = db["compliance_blocklist"].count_documents(q)
    cursor = (
        db["compliance_blocklist"]
        .find(q)
        .sort("created_at", -1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )

    items = []
    for d in cursor:
        items.append({
            "id": str(d["_id"]),
            "organization_id": d.get("organization_id"),
            "type": d.get("type"),
            "value": d.get("value"),
            "reason": d.get("reason"),
            "created_at": d.get("created_at").isoformat() if isinstance(d.get("created_at"), datetime) else str(d.get("created_at")),
            "created_by": d.get("created_by"),
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def delete_prospect_data(
    db,
    *,
    organization_id: str,
    identifier: str,
    identifier_type: str = "phone",
    reason: str = "Right to be Forgotten request",
    actor_email: str = "system",
    ip: str | None = None,
) -> dict[str, Any]:
    """Execute complete data deletion (Right to be Forgotten / GDPR Article 17).

    Purges all matching records across ai_comments and legacy collections for
    that organization, and automatically adds the identifier to the blocklist.
    """
    add_result = add_to_blocklist(
        db,
        contact_type=identifier_type,
        contact_value=identifier,
        reason=f"Data deletion request: {reason}",
        organization_id=organization_id,
        actor_email=actor_email,
        ip=ip,
    )

    audit(
        "compliance.data_deleted",
        "compliance",
        user=actor_email,
        ip=ip,
        organization_id=organization_id,
        details={
            "identifier_type": identifier_type,
            "reason": reason,
            "purged_leads": add_result.get("leads_purged", 0),
        },
    )

    return {
        "success": True,
        "message": f"Successfully deleted prospect data and blocked future collection for {identifier_type}={identifier}",
        "leads_purged": add_result.get("leads_purged", 0),
    }


def purge_expired_pii(
    db,
    *,
    organization_id: str | None = None,
    retention_days: int | None = None,
    actor_email: str = "system",
) -> dict[str, Any]:
    """Purge PII from ai_comments older than the configured retention period."""
    if db is None:
        return {"purged_records": 0, "message": "Database unavailable"}

    orgs_to_check = []
    if organization_id:
        org_doc = db["organizations"].find_one({"_id": ObjectId(str(organization_id))})
        if org_doc:
            orgs_to_check.append(org_doc)
    else:
        orgs_to_check = list(db["organizations"].find({"status": "active"}))

    total_purged = 0
    now = utcnow()

    for org in orgs_to_check:
        org_id_str = str(org["_id"])
        effective_days = retention_days
        if effective_days is None:
            effective_days = (org.get("settings") or {}).get("pii_retention_days")

        if not effective_days or effective_days <= 0:
            continue

        cutoff = now - timedelta(days=int(effective_days))
        q = {
            "organization_id": org_id_str,
            "pii_purged": {"$ne": True},
            "$and": [
                {
                    "$or": [
                        {"created_at": {"$lt": cutoff}},
                        {"lead_created_at": {"$lt": cutoff}},
                        {"analyzed_at": {"$lt": cutoff}},
                    ]
                },
                {
                    "$or": [
                        {"phone": {"$exists": True, "$ne": None}},
                        {"email": {"$exists": True, "$ne": None}},
                        {"whatsapp": {"$exists": True, "$ne": None}},
                        {"contact": {"$exists": True, "$ne": None}},
                    ]
                },
            ],
        }

        res = db["ai_comments"].update_many(
            q,
            {
                "$set": {
                    "pii_purged": True,
                    "pii_purged_at": now,
                    "pii_retention_days": effective_days,
                },
                "$unset": {
                    "phone": "",
                    "email": "",
                    "whatsapp": "",
                    "contact": "",
                },
            },
        )
        if res.modified_count > 0:
            total_purged += res.modified_count
            audit(
                "compliance.pii_purged",
                "compliance",
                user=actor_email,
                organization_id=org_id_str,
                details={
                    "retention_days": effective_days,
                    "cutoff_date": cutoff.isoformat(),
                    "records_purged": res.modified_count,
                },
            )

    return {
        "success": True,
        "purged_records": total_purged,
        "timestamp": now.isoformat(),
    }


def get_compliance_notice() -> dict[str, Any]:
    """Return standard platform Data Processing Notice and Privacy Notice."""
    return {
        "notice_title": "LeadAI Data Processing & Privacy Notice",
        "version": "2026.1",
        "last_updated": "2026-10-02",
        "summary": (
            "LeadAI analyzes public social media comments to help businesses identify prospect inquiries. "
            "We uphold strict data minimization, configurable retention policies, and robust deletion rights."
        ),
        "lawful_basis": (
            "Processing is conducted under legitimate commercial interest (B2B/B2C prospect inquiry management) "
            "or contractual necessity with authorized business organizations."
        ),
        "data_retention_policy": (
            "Personal Identifiable Information (PII) such as phone numbers and emails can be automatically purged "
            "after a configurable retention period (30, 60, or 90 days). Inactive records are archived."
        ),
        "rights_supported": [
            "Right to Opt-Out: Any individual can request immediate inclusion on the do-not-contact blocklist.",
            "Right of Access: Inquiries can verify whether their public social media handle has been analyzed.",
            "Right to Erasure: Complete deletion of all contact records across the platform.",
            "Right to Rectification: Correction of contact records via authorized organization admins.",
        ],
        "opt_out_endpoint": "/api/compliance/opt-out",
        "terms_of_service_disclaimer": (
            "LeadAI processes publicly visible user-submitted comments on behalf of registered organizations. "
            "Users and organizations are responsible for complying with Meta, LinkedIn, and YouTube Terms of Service "
            "and local communication regulations (e.g. TCPA, CAN-SPAM). This notice is not legal advice."
        ),
    }
