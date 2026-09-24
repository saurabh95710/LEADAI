"""
Secure Team Invitation System.

Generates SHA-256 token-hashed invitations with configurable expiration,
validates tokens, and activates memberships upon acceptance.
"""
from datetime import timedelta, timezone
import hashlib
import logging
import secrets
from typing import Any, Dict, Optional
from bson import ObjectId
from fastapi import HTTPException

from app.db.models import utcnow
from app.db.mongo import get_async_db

logger = logging.getLogger(__name__)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


async def create_invitation(
    organization_id: str,
    email: str,
    role: str = "member",
    invited_by: str = "admin",
    expires_days: Optional[int] = None,
    invited_by_user_id: Optional[str] = None,
    db=None,
    inviter_name: Optional[str] = None,
    organization_name: Optional[str] = None,
    send: bool = True,
) -> Dict[str, Any]:
    """Create a team invitation and email a one-time link (never a password)."""
    if invited_by_user_id:
        invited_by = invited_by_user_id
    if db is None:
        db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    clean_email = email.strip().lower()
    s_org_id = str(organization_id)
    clean_role = role.strip().lower()

    if clean_role not in ("admin", "manager", "member", "viewer"):
        raise HTTPException(status_code=400, detail="Invalid role. Must be admin, manager, member, or viewer.")

    # 1. Check if user is already an active member of this organization
    existing_user = await db.users.find_one({"email": clean_email})
    if existing_user:
        membership = await db.organization_members.find_one({
            "organization_id": s_org_id,
            "user_id": str(existing_user["_id"]),
            "status": "active",
        })
        if membership:
            raise HTTPException(status_code=400, detail="This user is already an active member of the organization.")

    # 2. Check team members limit for the organization's plan
    from app.billing.entitlements import EntitlementService
    allowed, used, limit = await EntitlementService.check_limit(s_org_id, "team_members", requested=1, db=db)
    if not allowed:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "QUOTA_EXCEEDED",
                "metric": "team_members",
                "used": used,
                "limit": limit,
                "message": f"Team member limit reached ({used}/{limit}). Upgrade your plan to invite more members.",
            },
        )

    # 3. Generate token & hash
    if expires_days is None:
        expires_days = 7
        try:
            org_doc = await db.organizations.find_one({"_id": ObjectId(s_org_id)}, {"settings": 1})
            expires_days = int(((org_doc or {}).get("settings") or {}).get("invite_expiry_days") or 7)
        except Exception:
            pass
    expires_days = max(1, min(int(expires_days), 30))
    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw_token)
    now = utcnow()
    expires_at = now + timedelta(days=expires_days)

    # Invalidate previous pending invitations for this email in this org
    await db.organization_invitations.update_many(
        {"organization_id": s_org_id, "email": clean_email, "status": "pending"},
        {"$set": {"status": "cancelled", "updated_at": now}},
    )

    doc = {
        "organization_id": s_org_id,
        "email": clean_email,
        "role": clean_role,
        "token_hash": token_hash,
        "invited_by": invited_by,
        "status": "pending",
        "expires_at": expires_at,
        "accepted_at": None,
        "created_at": now,
        "updated_at": now,
    }

    res = await db.organization_invitations.insert_one(doc)
    doc["id"] = str(res.inserted_id)
    doc["invite_url"] = f"/invite/{raw_token}"
    if send:
        from app.events.email import absolute_url, send_email
        send_email(clean_email, f"You're invited to join {organization_name or 'a team'} on LeadAI",
                   f"Hi,\n\n{inviter_name or 'A teammate'} invited you to join "
                   f"{organization_name or 'their organization'} on LeadAI as {clean_role}.\n\n"
                   f"Accept the invitation and create your password here "
                   f"(valid for {expires_days} days, single use):\n"
                   f"{absolute_url(doc['invite_url'])}\n\n— The LeadAI team",
                   kind="invitation", organization_id=s_org_id)
    doc.pop("token_hash", None)
    return doc


async def validate_invitation(token: str, db=None) -> Dict[str, Any]:
    """Validate an invitation token and return organization metadata."""
    if db is None:
        db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    token_hash = _hash_token(token)
    now = utcnow()

    inv = await db.organization_invitations.find_one({"token_hash": token_hash})
    if not inv:
        raise HTTPException(status_code=404, detail="Invitation not found or invalid link.")

    if inv.get("status") != "pending":
        raise HTTPException(status_code=400, detail=f"This invitation has already been {inv.get('status')}.")

    expires_at = inv.get("expires_at")
    if expires_at:
        if expires_at.tzinfo is None and now.tzinfo is not None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        elif expires_at.tzinfo is not None and now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        if expires_at < now:
            await db.organization_invitations.update_one({"_id": inv["_id"]}, {"$set": {"status": "expired"}})
            raise HTTPException(status_code=400, detail="This invitation link has expired. Please request a new invite.")

    # Retrieve organization metadata
    org = await db.organizations.find_one({"_id": ObjectId(inv["organization_id"])})
    org_name = org.get("name", "Organization") if org else "Organization"
    branding = org.get("branding", {}) if org else {}

    if org and org.get("status") not in ("active", "demo", "trial"):
        raise HTTPException(status_code=400, detail="This organization is not accepting new members.")

    return {
        "valid": True,
        "invitation_id": str(inv["_id"]),
        "organization_id": inv["organization_id"],
        "organization_name": org_name,
        "email": inv["email"],
        "role": inv["role"],
        "branding": branding,
        "expires_at": inv["expires_at"],
    }


async def accept_invitation(token: str, user_id: str, email: str = "", db=None) -> Dict[str, Any]:
    """Accept an invitation (bound to the invited email), activate membership."""
    if db is None:
        db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    inv = await validate_invitation(token, db=db)
    now = utcnow()
    s_user_id = str(user_id)
    s_org_id = str(inv["organization_id"])
    if not email or email.strip().lower() != inv["email"]:
        from app.events.security import alog_security_event
        await alog_security_event("invitation_email_mismatch", "medium", actor_email=email,
                                  actor_user_id=s_user_id, organization_id=s_org_id,
                                  details={"invited_email": inv["email"]})
        raise HTTPException(status_code=403,
                            detail="This invitation was sent to a different email address.")
    # Claim the invitation atomically (single use, even under concurrency)
    claimed = await db.organization_invitations.update_one(
        {"_id": ObjectId(inv["invitation_id"]), "status": "pending"},
        {"$set": {"status": "accepted", "accepted_at": now, "accepted_by": s_user_id,
                  "updated_at": now}})
    if claimed.modified_count == 0:
        raise HTTPException(status_code=400, detail="This invitation has already been used.")

    # 1. Create or activate membership
    existing_member = await db.organization_members.find_one({
        "organization_id": s_org_id,
        "user_id": s_user_id,
    })

    if existing_member:
        await db.organization_members.update_one(
            {"_id": existing_member["_id"]},
            {
                "$set": {
                    "role": inv["role"],
                    "status": "active",
                    "joined_at": now,
                    "last_activity_at": now,
                    "updated_at": now,
                }
            },
        )
    else:
        await db.organization_members.insert_one({
            "organization_id": s_org_id,
            "user_id": s_user_id,
            "role": inv["role"],
            "status": "active",
            "joined_at": now,
            "invited_by": inv.get("invited_by"),
            "last_activity_at": now,
            "permissions_override": {},
            "created_at": now,
            "updated_at": now,
        })

    # 2. Update user default_organization_id
    try:
        await db.users.update_one(
            {"_id": ObjectId(s_user_id)},
            {"$set": {"default_organization_id": s_org_id, "updated_at": now}},
        )
    except Exception:
        pass

    from app.events.notifications import notify_org_admins
    notify_org_admins(s_org_id, "invitation_accepted", "Invitation accepted",
                      f"{inv['email']} joined as {inv['role']}.", severity="success",
                      link="/org-admin#team")

    return {
        "success": True,
        "accepted": True,
        "organization_id": s_org_id,
        "organization_name": inv["organization_name"],
        "role": inv["role"],
        "message": f"Successfully joined {inv['organization_name']}.",
    }
