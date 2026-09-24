"""
Organization & Team Management API (org-scoped — Admin Portal backend).

  GET/PATCH /api/organizations/current                       profile, branding, settings
  GET       /api/organizations/current/team                  members + pending invitations
  POST      /api/organizations/current/invitations           invite (email with link)
  DELETE    /api/organizations/current/invitations/{id}      revoke
  POST      /api/organizations/current/invitations/{id}/resend
  PATCH     /api/organizations/current/members/{user_id}     role / status
  DELETE    /api/organizations/current/members/{user_id}     remove
  GET       /api/invitations/{token}                         validate invite (public)
  POST      /api/invitations/{token}/accept                  accept with the signed-in account
  POST      /api/invitations/{token}/register                create account + password, then join

Every route resolves the organization from the session (never from the
request), enforces permissions, applies the role hierarchy (an Admin can
never grant owner/super admin or modify another Admin), revokes sessions
when a member's access changes, and writes an audit entry.
"""
import logging
import re
from typing import Any, Dict, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr

from app.admin.audit import aaudit, request_meta
from app.auth.permissions import (
    CONFIGURABLE_ORG_ROLES,
    DELEGABLE_ORG_PERMISSIONS,
    MEMBERS_DELETE,
    MEMBERS_INVITE,
    MEMBERS_SUSPEND,
    MEMBERS_UPDATE,
    MEMBERS_VIEW,
    ROLES_MANAGE,
    SETTINGS_MANAGE,
    WORKSPACE_VIEW,
)
from app.auth.service import revoke_user_sessions, session_user
from app.auth.tenant import (
    TenantContext,
    assert_can_manage_member,
    require_org_permission,
)
from app.billing.invitations import accept_invitation, create_invitation, validate_invitation
from app.db.models import utcnow
from app.db.mongo import get_async_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["organizations"])

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
# Organization-level settings an Admin may change (anything else is rejected)
_SETTING_TYPES: Dict[str, type] = {
    "auto_export": bool, "notify_on_leads": bool, "min_lead_score": int,
    "language": str, "date_format": str, "shared_workspace": bool,
    "lead_assignment": str, "default_posts_per_search": int,
    "default_comments_per_post": int, "notify_job_completion": bool,
    "notify_usage_warnings": bool, "usage_warning_percent": int,
    "invite_expiry_days": int, "default_member_role": str,
    "lead_keywords": list, "lead_statuses": list,
    "lead_exclude_keywords": list, "email_notifications": bool,
    "notify_lead_assigned": bool,
}
# Integer settings with an allowed range (inclusive)
_SETTING_RANGES: Dict[str, tuple] = {
    "min_lead_score": (0, 100), "usage_warning_percent": (1, 100),
    "invite_expiry_days": (1, 30), "default_posts_per_search": (1, 10000),
    "default_comments_per_post": (1, 100000),
}
_SETTING_CHOICES: Dict[str, tuple] = {
    "lead_assignment": ("manual", "round_robin", "creator"),
    "default_member_role": CONFIGURABLE_ORG_ROLES,
}
_URL_FIELDS = ("website", "logo_url")
_MEMBER_STATUSES = ("active", "inactive", "suspended")


class UpdateOrgProfileRequest(BaseModel):
    name: Optional[str] = None
    website: Optional[str] = None
    timezone: Optional[str] = None
    currency: Optional[str] = None
    industry: Optional[str] = None
    country: Optional[str] = None
    description: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    logo_url: Optional[str] = None
    primary_color: Optional[str] = None
    accent_color: Optional[str] = None
    company_name: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None
    role_permissions: Optional[Dict[str, Dict[str, bool]]] = None


class InviteMemberRequest(BaseModel):
    email: EmailStr
    role: Optional[str] = None  # default: the org's settings.default_member_role


class UpdateMemberRequest(BaseModel):
    role: Optional[str] = None
    status: Optional[str] = None
    permissions_override: Optional[Dict[str, bool]] = None


class RegisterFromInviteRequest(BaseModel):
    name: str
    password: str


def _clean_doc(d: Dict[str, Any]) -> Dict[str, Any]:
    if not d:
        return {}
    out = {}
    for k, v in d.items():
        if k in ("token_hash",):
            continue
        if k == "_id":
            out["id"] = str(v)
        elif isinstance(v, ObjectId):
            out[k] = str(v)
        elif isinstance(v, dict):
            out[k] = _clean_doc(v)
        elif isinstance(v, list):
            out[k] = [_clean_doc(i) if isinstance(i, dict) else (str(i) if isinstance(i, ObjectId) else i) for i in v]
        elif hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


async def _log_foreign_member_probe(db, request: Request, ctx: TenantContext, user_id: str) -> None:
    """A member id that exists only in another organization is a cross-tenant probe."""
    other = await db.organization_members.find_one({"user_id": str(user_id),
                                                    "organization_id": {"$ne": ctx.tenant_id}})
    if other:
        from app.auth.tenant import report_out_of_scope
        report_out_of_scope(request, ctx, "organization_members",
                            {"_id": other["_id"], "organization_id": other["organization_id"]})


async def _log_foreign_invitation_probe(db, request: Request, ctx: TenantContext, oid: ObjectId) -> None:
    """An invitation id that belongs to another organization is a cross-tenant probe."""
    other = await db.organization_invitations.find_one(
        {"_id": oid, "organization_id": {"$ne": ctx.tenant_id}}, {"_id": 1, "organization_id": 1})
    if other:
        from app.auth.tenant import report_out_of_scope
        report_out_of_scope(request, ctx, "organization_invitations", other)

def _db():
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return db


@router.get("/organizations/current")
async def get_current_organization(ctx: TenantContext = Depends(require_org_permission(WORKSPACE_VIEW))):
    db = _db()
    org = await db.organizations.find_one({"_id": ObjectId(ctx.tenant_id)})
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    cleaned = _clean_doc(org)
    cleaned.pop("metadata", None)
    cleaned["user_role"] = ctx.user_role
    cleaned["permissions"] = ctx.permissions
    return {"success": True, "organization": cleaned}


def _validate_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    clean: Dict[str, Any] = {}
    for k, v in settings.items():
        t = _SETTING_TYPES.get(k)
        if t is None:
            raise HTTPException(status_code=422, detail=f"Unknown organization setting '{k}'")
        if t is bool:
            clean[k] = bool(v)
        elif t is int:
            try:
                clean[k] = int(v)
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail=f"'{k}' must be a whole number")
        elif t is list:
            if not isinstance(v, list):
                raise HTTPException(status_code=422, detail=f"'{k}' must be a list")
            clean[k] = [str(x).strip()[:80] for x in v if str(x).strip()][:200]
        else:
            clean[k] = str(v)[:200]
    for k, (lo, hi) in _SETTING_RANGES.items():
        if k in clean and not lo <= clean[k] <= hi:
            raise HTTPException(status_code=422, detail=f"'{k}' must be between {lo} and {hi}")
    for k, choices in _SETTING_CHOICES.items():
        if clean.get(k) and clean[k] not in choices:
            raise HTTPException(status_code=422,
                                detail=f"'{k}' must be one of: {', '.join(choices)}")
    return clean


def _check_search_defaults(org_id: str, clean: Dict[str, Any]) -> None:
    """Search defaults may never exceed the plan's per-search caps nor the
    platform hard caps (Super Admin limits)."""
    if not any(k in clean for k in ("default_posts_per_search", "default_comments_per_post")):
        return
    caps: Dict[str, Any] = {}
    try:
        from app.billing.entitlements import EntitlementService
        caps = EntitlementService.get_run_caps(org_id) or {}
    except Exception:
        caps = {}
    try:
        from app.admin.settings import effective_limits
        lim = effective_limits()
    except Exception:
        lim = {}
    for key, cap_key, hard_key, label in (
            ("default_posts_per_search", "posts_per_search", "max_posts_cap", "posts per search"),
            ("default_comments_per_post", "comments_per_post", "max_comments_per_post_cap",
             "comments per post")):
        if key not in clean:
            continue
        limits = [v for v in (caps.get(cap_key), lim.get(hard_key)) if v]
        if limits and clean[key] > min(limits):
            raise HTTPException(status_code=422,
                                detail=f"Your plan allows at most {min(limits)} {label}.")


def _check_url(field: str, value: str) -> str:
    value = value.strip()
    if value and not re.match(r"^https?://[^\s<>\"']+$", value, re.I):
        raise HTTPException(status_code=422, detail=f"{field} must be an http(s) URL")
    return value


@router.patch("/organizations/current")
async def update_current_organization(body: UpdateOrgProfileRequest, request: Request,
                                      ctx: TenantContext = Depends(require_org_permission(SETTINGS_MANAGE))):
    """Update profile, branding and org settings (own organization only)."""
    db = _db()
    updates: Dict[str, Any] = {}
    for field in ("website", "timezone", "industry", "country", "description",
                  "contact_email", "contact_phone", "logo_url"):
        val = getattr(body, field)
        if val is not None:
            updates[field] = (_check_url(field, val) if field in _URL_FIELDS else val.strip())[:500]
    if body.name is not None and body.name.strip():
        updates["name"] = body.name.strip()[:120]
    if body.currency is not None:
        updates["currency"] = body.currency.strip().upper()[:3]
    for key, val in (("primary_color", body.primary_color), ("accent_color", body.accent_color)):
        if val:
            if not _HEX.match(val.strip()):
                raise HTTPException(status_code=422, detail=f"{key} must be a #RRGGBB color")
            updates[f"branding.{key}"] = val.strip()
    if body.company_name is not None:
        updates["branding.company_name"] = body.company_name.strip()[:120]
    if body.settings:
        clean_settings = _validate_settings(body.settings)
        _check_search_defaults(ctx.tenant_id, clean_settings)
        for k, v in clean_settings.items():
            updates[f"settings.{k}"] = v
    if body.role_permissions is not None:
        if ROLES_MANAGE not in ctx.permissions:
            raise HTTPException(status_code=403, detail="Requires permission 'roles.manage'")
        for role, perms in body.role_permissions.items():
            if role not in CONFIGURABLE_ORG_ROLES:
                raise HTTPException(status_code=422, detail=f"Role '{role}' cannot be configured")
            bad = [p for p in perms if p not in DELEGABLE_ORG_PERMISSIONS]
            if bad:
                raise HTTPException(status_code=422, detail=f"Permissions not delegable: {', '.join(bad)}")
            updates[f"settings.role_permissions.{role}"] = {p: bool(v) for p, v in perms.items()}
    if not updates:
        return {"success": True, "message": "No changes requested"}
    before = await db.organizations.find_one({"_id": ObjectId(ctx.tenant_id)})
    updates["updated_at"] = utcnow()
    await db.organizations.update_one({"_id": ObjectId(ctx.tenant_id)}, {"$set": updates})
    updated = await db.organizations.find_one({"_id": ObjectId(ctx.tenant_id)})
    await aaudit("organization.updated", "organization", user=ctx.audit_user(),
                 organization_id=ctx.tenant_id, resource_type="organization",
                 resource_id=ctx.tenant_id,
                 details={"changed": {k: v for k, v in updates.items() if k != "updated_at"},
                          "had_settings": bool((before or {}).get("settings"))},
                 **request_meta(request))
    return {"success": True, "organization": _clean_doc(updated)}


@router.get("/organizations/current/team")
async def list_team_members(ctx: TenantContext = Depends(require_org_permission(MEMBERS_VIEW))):
    db = _db()
    members = []
    async for m in db.organization_members.find({"organization_id": ctx.tenant_id,
                                                 "status": {"$ne": "removed"}}):
        m_doc = _clean_doc(m)
        try:
            u = await db.users.find_one({"_id": ObjectId(m["user_id"])},
                                        {"email": 1, "name": 1, "last_login": 1, "status": 1})
        except Exception:
            u = None
        if u:
            m_doc.update({"email": u.get("email"), "name": u.get("name"),
                          "last_login": u["last_login"].isoformat() if hasattr(u.get("last_login"), "isoformat") else u.get("last_login"),
                          "account_status": u.get("status")})
        members.append(m_doc)
    invitations = []
    if MEMBERS_INVITE in ctx.permissions:
        async for inv in db.organization_invitations.find({"organization_id": ctx.tenant_id,
                                                           "status": "pending"}):
            invitations.append(_clean_doc(inv))
    return {"success": True, "members": members, "invitations": invitations,
            "total_members": len(members), "total_invitations": len(invitations)}


@router.post("/organizations/current/invitations")
async def invite_team_member(body: InviteMemberRequest, request: Request,
                             ctx: TenantContext = Depends(require_org_permission(MEMBERS_INVITE))):
    db = _db()
    role = (body.role or "").strip().lower()
    if not role:
        org = await db.organizations.find_one({"_id": ObjectId(ctx.tenant_id)}, {"settings": 1})
        role = ((org or {}).get("settings") or {}).get("default_member_role") or "member"
    assert_can_manage_member(ctx, target_role=None, new_role=role, request=request)
    invitation = await create_invitation(organization_id=ctx.tenant_id, email=body.email,
                                         role=role, invited_by=ctx.user_id, db=db,
                                         inviter_name=ctx.name,
                                         organization_name=ctx.organization_name)
    await aaudit("member.invited", "team", user=ctx.audit_user(), organization_id=ctx.tenant_id,
                 resource_type="invitation", resource_id=invitation.get("id"),
                 details={"email": body.email, "role": role}, **request_meta(request))
    return {"success": True, "invitation": _clean_doc(invitation),
            "invite_url": invitation.get("invite_url"),
            "message": f"Invitation emailed to {body.email}"}


@router.delete("/organizations/current/invitations/{invitation_id}")
async def revoke_team_invitation(invitation_id: str, request: Request,
                                 ctx: TenantContext = Depends(require_org_permission(MEMBERS_INVITE))):
    db = _db()
    try:
        oid = ObjectId(invitation_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid invitation id")
    res = await db.organization_invitations.update_one(
        {"_id": oid, "organization_id": ctx.tenant_id, "status": "pending"},
        {"$set": {"status": "cancelled", "updated_at": utcnow()}})
    if res.matched_count == 0:
        await _log_foreign_invitation_probe(db, request, ctx, oid)
        raise HTTPException(status_code=404, detail="Pending invitation not found")
    await aaudit("member.invitation_revoked", "team", user=ctx.audit_user(),
                 organization_id=ctx.tenant_id, resource_type="invitation",
                 resource_id=invitation_id, **request_meta(request))
    return {"success": True, "message": "Invitation cancelled"}


@router.post("/organizations/current/invitations/{invitation_id}/resend")
async def resend_team_invitation(invitation_id: str, request: Request,
                                 ctx: TenantContext = Depends(require_org_permission(MEMBERS_INVITE))):
    db = _db()
    try:
        oid = ObjectId(invitation_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid invitation id")
    existing = await db.organization_invitations.find_one(
        {"_id": oid, "organization_id": ctx.tenant_id, "status": "pending"})
    if not existing:
        await _log_foreign_invitation_probe(db, request, ctx, oid)
        raise HTTPException(status_code=404, detail="Pending invitation not found")
    assert_can_manage_member(ctx, target_role=None, new_role=existing["role"], request=request)
    # cancel first so the seat is not double counted
    await db.organization_invitations.update_one({"_id": oid}, {"$set": {"status": "cancelled"}})
    new_invitation = await create_invitation(organization_id=ctx.tenant_id, email=existing["email"],
                                             role=existing["role"], invited_by=ctx.user_id, db=db,
                                             inviter_name=ctx.name,
                                             organization_name=ctx.organization_name)
    await aaudit("member.invitation_resent", "team", user=ctx.audit_user(),
                 organization_id=ctx.tenant_id, resource_type="invitation",
                 resource_id=new_invitation.get("id"), details={"email": existing["email"]},
                 **request_meta(request))
    return {"success": True, "invitation": _clean_doc(new_invitation),
            "invite_url": new_invitation.get("invite_url"),
            "message": f"Invitation resent to {existing['email']}"}


@router.patch("/organizations/current/members/{member_user_id}")
async def update_team_member(member_user_id: str, body: UpdateMemberRequest, request: Request,
                             ctx: TenantContext = Depends(require_org_permission(MEMBERS_UPDATE))):
    """Change a member's role, status (suspend / reactivate) or permission overrides."""
    db = _db()
    membership = await db.organization_members.find_one(
        {"organization_id": ctx.tenant_id, "user_id": str(member_user_id),
         "status": {"$ne": "removed"}})
    if not membership:
        await _log_foreign_member_probe(db, request, ctx, member_user_id)
        raise HTTPException(status_code=404, detail="Team member not found")
    new_role = body.role.strip().lower() if body.role else None
    assert_can_manage_member(ctx, target_role=membership.get("role"), new_role=new_role,
                             target_user_id=member_user_id, request=request)
    updates: Dict[str, Any] = {}
    if new_role:
        updates["role"] = new_role
    if body.status:
        status = body.status.strip().lower()
        if status not in _MEMBER_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid status")
        if status in ("suspended", "inactive") and MEMBERS_SUSPEND not in ctx.permissions:
            raise HTTPException(status_code=403, detail="Requires permission 'members.suspend'")
        if status == "active" and membership.get("status") == "inactive":
            # an inactive member frees a seat; restoring must fit the plan again
            from app.billing.entitlements import EntitlementService
            allowed, used, limit = await EntitlementService.check_limit(
                ctx.tenant_id, "team_members", requested=1, db=db)
            if not allowed:
                raise HTTPException(status_code=402, detail={
                    "code": "PLAN_LIMIT", "metric": "team_members", "used": used, "limit": limit,
                    "message": f"Your plan allows {limit} team members. Upgrade to restore this user."})
        updates["status"] = status
    if body.permissions_override is not None:
        if ROLES_MANAGE not in ctx.permissions:
            raise HTTPException(status_code=403, detail="Requires permission 'roles.manage'")
        bad = [p for p in body.permissions_override if p not in DELEGABLE_ORG_PERMISSIONS]
        if bad:
            raise HTTPException(status_code=422, detail=f"Permissions not delegable: {', '.join(bad)}")
        updates["permissions_override"] = {p: bool(v) for p, v in body.permissions_override.items()}
    if not updates:
        return {"success": True, "message": "No changes requested"}
    updates["updated_at"] = utcnow()
    await db.organization_members.update_one({"_id": membership["_id"]}, {"$set": updates})
    # access changed -> force re-authentication everywhere
    revoked = revoke_user_sessions(member_user_id, revoked_by=f"org_admin:{ctx.email}")
    await aaudit("member.updated", "team", user=ctx.audit_user(), organization_id=ctx.tenant_id,
                 resource_type="member", resource_id=member_user_id,
                 details={"before": {"role": membership.get("role"), "status": membership.get("status")},
                          "after": {k: v for k, v in updates.items() if k != "updated_at"},
                          "sessions_revoked": revoked}, **request_meta(request))
    if updates.get("status") == "suspended":
        from app.events.notifications import notify_org_admins
        notify_org_admins(ctx.tenant_id, "user_suspended", "Team member suspended",
                          f"User {member_user_id} was suspended by {ctx.email}", severity="warning")
    return {"success": True, "message": "Member updated successfully"}


@router.delete("/organizations/current/members/{member_user_id}")
async def remove_team_member(member_user_id: str, request: Request,
                             ctx: TenantContext = Depends(require_org_permission(MEMBERS_DELETE))):
    db = _db()
    membership = await db.organization_members.find_one(
        {"organization_id": ctx.tenant_id, "user_id": str(member_user_id),
         "status": {"$ne": "removed"}})
    if not membership:
        await _log_foreign_member_probe(db, request, ctx, member_user_id)
        raise HTTPException(status_code=404, detail="Team member not found")
    assert_can_manage_member(ctx, target_role=membership.get("role"),
                             target_user_id=member_user_id, request=request)
    await db.organization_members.update_one(
        {"_id": membership["_id"]}, {"$set": {"status": "removed", "updated_at": utcnow()}})
    revoked = revoke_user_sessions(member_user_id, revoked_by=f"org_admin:{ctx.email}")
    await aaudit("member.removed", "team", user=ctx.audit_user(), organization_id=ctx.tenant_id,
                 resource_type="member", resource_id=member_user_id,
                 details={"role": membership.get("role"), "sessions_revoked": revoked},
                 **request_meta(request))
    return {"success": True, "message": "Member removed from workspace"}


# ── Invitations (public / semi-public) ─────────────────────────────────────

@router.get("/invitations/{token}")
async def get_invitation_details(token: str):
    """Validate an invitation link (used by the accept-invite page)."""
    db = get_async_db()
    details = await validate_invitation(token, db=db)
    exists = bool(await db.users.find_one({"email": details["email"]}, {"_id": 1}))
    return {"success": True, "invitation": {**_clean_doc(details), "account_exists": exists}}


@router.post("/invitations/{token}/accept")
async def accept_invitation_endpoint(token: str, request: Request):
    """Accept with the signed-in account — which must be the invited email."""
    user = session_user(request)
    if not user or not user.get("user_id"):
        raise HTTPException(status_code=401, detail="Please sign in to accept this invitation")
    db = get_async_db()
    result = await accept_invitation(token, user_id=user["user_id"],
                                     email=user.get("email", ""), db=db)
    await aaudit("member.joined", "team", user=user, organization_id=result.get("organization_id"),
                 resource_type="member", resource_id=user["user_id"],
                 details={"role": result.get("role")}, **request_meta(request))
    return result


@router.post("/invitations/{token}/register")
async def register_from_invitation(token: str, body: RegisterFromInviteRequest,
                                   request: Request, response: Response):
    """Invite link -> create password -> account activated -> signed in.
    Only for an email that has no account yet."""
    from app.auth.crypto import hash_password
    from app.auth.service import build_user_claims, create_tracked_session, set_session_cookie, public_user
    from app.db.mongo import get_sync_db
    from app.lifecycle.demo import validate_password
    db = get_async_db()
    details = await validate_invitation(token, db=db)
    validate_password(body.password)
    if not body.name.strip():
        raise HTTPException(status_code=422, detail="Name is required")
    email = details["email"]
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=409, detail="An account already exists — sign in to accept the invitation")
    now = utcnow()
    user_id = str((await db.users.insert_one({
        "email": email, "name": body.name.strip()[:120],
        "password_hash": hash_password(body.password), "status": "active",
        "is_platform_admin": False, "platform_role": None,
        "default_organization_id": details["organization_id"],
        "created_at": now, "updated_at": now, "last_login": now,
    })).inserted_id)
    result = await accept_invitation(token, user_id=user_id, email=email, db=db)
    meta = request_meta(request)
    await aaudit("user.created", "team", user={"user_id": user_id, "email": email},
                 organization_id=details["organization_id"], resource_type="user",
                 resource_id=user_id, details={"via": "invitation", "role": details["role"]},
                 **meta)
    await aaudit("member.joined", "team", user={"user_id": user_id, "email": email},
                 organization_id=details["organization_id"], resource_type="member",
                 resource_id=user_id, details={"role": result.get("role")}, **meta)
    sdb = get_sync_db()
    claims, err = build_user_claims(sdb, sdb.users.find_one({"_id": ObjectId(user_id)}))
    if claims:
        tracked = create_tracked_session(claims, ip=meta["ip"] or "unknown",
                                         user_agent=meta["user_agent"] or "unknown")
        set_session_cookie(response, tracked)
        return {"success": True, "user": public_user(tracked), **result}
    return {"success": True, **result, "message": err or result.get("message")}
