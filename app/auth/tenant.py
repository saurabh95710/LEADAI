"""
Tenant context resolution, route guards and query scoping.

Every tenant API resolves a ``TenantContext`` from the signed session and
then from the DATABASE (never from stale cookie claims):

  1. The session cookie must be valid and not revoked (see auth.service).
  2. Platform staff (admin-scope session whose server-side role is a
     platform role) get a platform context; they have no implicit tenant.
  3. Everyone else must have an ACTIVE user record and an ACTIVE membership
     in an organization. Role and permissions are re-read from the
     membership on every request, so removals, suspensions and role changes
     take effect immediately. There is NO fallback organization: a user
     without an active membership gets 403 (fail closed).
  4. The organization's status gates access (pending demo, suspended,
     cancelled, ... are rejected).

Data access goes through ``scope_query`` / ``find_scoped_or_404``:
  * Super Admin (platform context)    -> global
  * org owner/admin (data.view_all)   -> whole organization
  * everyone else                     -> own records (+ leads assigned to them)
Misses on a record that exists outside the caller's scope are logged as
``cross_tenant_access`` / ``cross_user_access`` security events and answered
with 404, so IDs cannot be probed.
"""
import logging
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth.permissions import (
    DATA_VIEW_ALL,
    ORG_ROLE_RANK,
    PLATFORM_ROLE_RANK,
    effective_platform_permissions,
    resolve_org_permissions,
)
from app.auth.service import session_user
from app.db.mongo import get_sync_db

logger = logging.getLogger(__name__)

# Organization statuses that may use the product. "demo" = approved demo,
# "trial" = legacy self-signup trial (grandfathered), "active" = paying.
USABLE_ORG_STATUSES = {"active", "demo", "trial"}
_ORG_STATUS_ERRORS = {
    "pending": ("demo_pending", "Your demo request is awaiting approval by our team."),
    "rejected": ("demo_rejected", "Your demo request was not approved."),
    "suspended": ("organization_suspended", "Organization account is suspended"),
    "disabled": ("organization_disabled", "Organization account is disabled"),
    "cancelled": ("organization_cancelled", "Organization subscription is cancelled"),
    "archived": ("organization_archived", "Organization has been archived"),
}


class TenantContext(BaseModel):
    """The resolved security and tenant context for the current request."""

    user_id: str
    email: str
    name: str = "User"
    organization_id: str = ""
    organization_name: str = ""
    organization_slug: str = ""
    organization_status: str = ""
    org_role: str = ""
    platform_role: Optional[str] = None
    is_super_admin: bool = False
    impersonated_by: Optional[str] = None
    impersonation_reason: Optional[str] = None
    session_id: Optional[str] = None
    permissions: List[str] = Field(default_factory=list)

    @property
    def tenant_id(self) -> str:
        return self.organization_id

    @property
    def user_role(self) -> str:
        return self.org_role

    @property
    def is_platform(self) -> bool:
        """Platform staff context (no implicit tenant)."""
        return bool(self.platform_role) and not self.organization_id

    @property
    def can_view_all_org_data(self) -> bool:
        return self.is_super_admin or DATA_VIEW_ALL in self.permissions

    def has(self, perm: str) -> bool:
        return self.is_super_admin or perm in self.permissions

    def audit_user(self) -> Dict[str, Any]:
        return {"user_id": self.user_id, "email": self.email,
                "role": self.platform_role or self.org_role,
                "organization_id": self.organization_id or None,
                "session_id": self.session_id}


def _forbid(code: str, message: str, status: int = 403):
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


def _oid(value: Any) -> Optional[ObjectId]:
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _platform_context(claims: Dict[str, Any], email: str, platform_role: str,
                      db) -> TenantContext:
    user_id = str(claims.get("user_id") or "")
    if db is not None and not user_id:
        rec = db.users.find_one({"email": email}, {"_id": 1})
        if rec:
            user_id = str(rec["_id"])
    perms = effective_platform_permissions(platform_role)
    return TenantContext(
        user_id=user_id or email,
        email=email,
        name=claims.get("name", "Admin"),
        platform_role=platform_role,
        is_super_admin=platform_role == "super_admin",
        session_id=claims.get("session_id"),
        permissions=sorted(perms),
    )


def impersonation_expiry() -> int:
    """Epoch seconds when a new impersonation session must end
    (Security setting ``security.impersonation_minutes``, default 30)."""
    import time
    minutes = 30
    try:
        from app.admin.settings import get_setting
        minutes = int(get_setting("security.impersonation_minutes") or minutes)
    except Exception:
        pass
    return int(time.time()) + max(5, min(minutes, 240)) * 60


def _check_impersonation(claims: Dict[str, Any]) -> None:
    """An impersonation session is valid only while the impersonator is still
    a Super Admin and before its hard expiry."""
    import time
    impersonator = claims.get("impersonated_by")
    if not impersonator:
        return
    from app.auth.roles import effective_role
    if effective_role(impersonator) != "super_admin":
        _forbid("unauthorized", "Impersonation is no longer authorized", 401)
    exp = claims.get("impersonation_expires_at")
    if not isinstance(exp, (int, float)) or exp < time.time():
        _forbid("impersonation_expired", "Impersonation session expired", 401)


def _impersonated_org_context(claims: Dict[str, Any], db) -> TenantContext:
    """Super Admin support view of an organization (acts as its Admin)."""
    org_id = str(claims.get("organization_id") or "")
    org = db.organizations.find_one({"_id": _oid(org_id)}) if _oid(org_id) else None
    if org is None:
        _forbid("no_active_organization", "Organization not found.")
    perms = resolve_org_permissions("admin", org, None)
    return TenantContext(
        user_id=str(claims.get("user_id") or claims.get("email")),
        email=(claims.get("email") or "").lower(),
        name=claims.get("name", "Super Admin"),
        organization_id=org_id,
        organization_name=org.get("name", ""),
        organization_slug=org.get("slug", ""),
        organization_status=org.get("status", ""),
        org_role="admin",
        impersonated_by=claims.get("impersonated_by"),
        impersonation_reason=claims.get("impersonation_reason"),
        session_id=claims.get("session_id"),
        permissions=sorted(perms),
    )


def resolve_tenant_context(claims: Dict[str, Any], db=None) -> TenantContext:
    """Build the context from verified session claims + the database."""
    email = (claims.get("email") or "").strip().lower()
    if not email:
        _forbid("unauthorized", "Sign in required", 401)
    if db is None:
        db = get_sync_db()
    scope = claims.get("scope", "site")

    if claims.get("impersonated_by"):
        _check_impersonation(claims)
        if scope == "site" and email == str(claims["impersonated_by"]).lower():
            if db is None:
                _forbid("service_unavailable", "Database unavailable", 503)
            return _impersonated_org_context(claims, db)

    # Platform staff: admin-scope sessions only, role re-read server-side.
    if scope == "admin":
        from app.auth.roles import PANEL_TO_PLATFORM_ROLE, effective_role
        role = effective_role(email)
        if not role:
            _forbid("forbidden", "Platform access required")
        platform_role = PANEL_TO_PLATFORM_ROLE.get(role)
        if db is not None:
            # a SaaS platform user keeps their precise platform role
            rec = db.users.find_one({"email": email, "is_platform_admin": True},
                                    {"platform_role": 1})
            if rec and rec.get("platform_role") in PLATFORM_ROLE_RANK and role != "super_admin":
                platform_role = rec["platform_role"]
        if not platform_role:
            _forbid("forbidden", "Platform access required")
        return _platform_context(claims, email, platform_role, db)

    if db is None:
        _forbid("service_unavailable", "Database unavailable", 503)

    user = None
    uid = _oid(claims.get("user_id"))
    if uid is not None:
        user = db.users.find_one({"_id": uid})
    if user is None:
        user = db.users.find_one({"email": email})
    if user is None or (user.get("email") or "").lower() != email:
        _forbid("unauthorized", "Account not found", 401)
    status = user.get("status", "active")
    if status == "pending_approval":
        _forbid("demo_pending", "Your demo request is awaiting approval by our team.")
    if status != "active":
        _forbid("account_suspended", "Account is suspended")
    user_id = str(user["_id"])

    # Resolve membership — the claim's org first, then the default org.
    membership = None
    for candidate in (claims.get("organization_id"), user.get("default_organization_id")):
        if candidate:
            membership = db.organization_members.find_one({
                "user_id": user_id, "organization_id": str(candidate), "status": "active"})
            if membership:
                break
    if membership is None:
        membership = db.organization_members.find_one({"user_id": user_id, "status": "active"})
    if membership is None:
        _forbid("no_active_organization",
                "You are not an active member of any organization.")

    org_id = str(membership["organization_id"])
    org = db.organizations.find_one({"_id": _oid(org_id)}) if _oid(org_id) else None
    if org is None:
        _forbid("no_active_organization", "Organization not found.")
    org_status = org.get("status", "active")
    if org_status not in USABLE_ORG_STATUSES:
        code, msg = _ORG_STATUS_ERRORS.get(org_status, ("organization_inactive",
                                                        "Organization is not active"))
        _forbid(code, msg)

    role = membership.get("role", "member")
    perms = resolve_org_permissions(role, org, membership)
    return TenantContext(
        user_id=user_id,
        email=email,
        name=user.get("name") or claims.get("name", "User"),
        organization_id=org_id,
        organization_name=org.get("name", ""),
        organization_slug=org.get("slug", ""),
        organization_status=org_status,
        org_role=role,
        platform_role=None,
        is_super_admin=False,
        impersonated_by=claims.get("impersonated_by"),
        impersonation_reason=claims.get("impersonation_reason"),
        session_id=claims.get("session_id"),
        permissions=sorted(perms),
    )


def get_tenant_context(request: Request) -> TenantContext:
    """FastAPI dependency: the validated TenantContext (cached per request)."""
    cached = getattr(request.state, "tenant_ctx", None)
    if isinstance(cached, TenantContext):
        return cached
    claims = session_user(request)
    if not claims or not claims.get("email"):
        _forbid("unauthorized", "Sign in required", 401)
    ctx = resolve_tenant_context(claims)
    request.state.tenant_ctx = ctx
    return ctx


def get_org_context(request: Request) -> TenantContext:
    """Like get_tenant_context but requires an organization (tenant APIs)."""
    ctx = get_tenant_context(request)
    if not ctx.organization_id:
        _forbid("no_active_organization", "An organization context is required.")
    return ctx


def _permission_denied(request: Request, ctx: TenantContext, perm: str):
    try:
        from app.events.security import security_event_from_request
        security_event_from_request(request, "permission_denied", "low", ctx=ctx,
                                    details={"permission": perm})
    except Exception:
        pass
    _forbid("permission_denied", f"Requires permission '{perm}'")


def require_org_permission(perm: str):
    """Dependency: tenant context with the given organization permission."""
    def _dep(request: Request, tenant: TenantContext = Depends(get_org_context)):
        if perm not in tenant.permissions:
            _permission_denied(request, tenant, perm)
        return tenant
    return _dep


def require_org_admin():
    """Dependency: organization owner or admin."""
    def _dep(request: Request, tenant: TenantContext = Depends(get_org_context)):
        if tenant.org_role not in ("owner", "admin"):
            _permission_denied(request, tenant, "org.admin")
        return tenant
    return _dep


def require_platform_permission(perm: str):
    """Dependency: platform staff with the given platform permission."""
    def _dep(request: Request, tenant: TenantContext = Depends(get_tenant_context)):
        if tenant.is_super_admin:
            return tenant
        if not tenant.platform_role or perm not in tenant.permissions:
            _permission_denied(request, tenant, perm)
        return tenant
    return _dep


def require_platform_role(min_role: str = "viewer"):
    """Dependency: platform staff with at least ``min_role``."""
    def _dep(request: Request, tenant: TenantContext = Depends(get_tenant_context)):
        if not tenant.platform_role:
            _permission_denied(request, tenant, f"platform:{min_role}")
        if PLATFORM_ROLE_RANK.get(tenant.platform_role, 0) < PLATFORM_ROLE_RANK.get(min_role, 99):
            _permission_denied(request, tenant, f"platform:{min_role}")
        return tenant
    return _dep


# ── Role assignment guard (no escalation) ───────────────────────────────────

ASSIGNABLE_ORG_ROLES = ("admin", "manager", "member", "viewer")


def assert_can_manage_member(ctx: TenantContext, target_role: Optional[str],
                             new_role: Optional[str] = None,
                             target_user_id: Optional[str] = None,
                             request: Optional[Request] = None) -> None:
    """Enforce the org role hierarchy for any member change.

    * nobody can grant ``owner`` or any platform role from an organization;
    * nobody can change their own role/status;
    * only the owner may grant ``admin`` or modify an existing admin
      (an Admin cannot modify another Admin);
    * the owner can never be modified here.
    """
    def deny(reason: str, event: str = "escalation_attempt"):
        if request is not None:
            try:
                from app.events.security import security_event_from_request
                security_event_from_request(request, event, "high", ctx=ctx,
                                            details={"reason": reason,
                                                     "target_user_id": target_user_id,
                                                     "new_role": new_role})
            except Exception:
                pass
        _forbid("forbidden", reason)

    if new_role is not None and new_role not in ASSIGNABLE_ORG_ROLES:
        deny(f"Role '{new_role}' cannot be assigned from an organization")
    if target_user_id and str(target_user_id) == str(ctx.user_id):
        deny("You cannot change your own role or status", "permission_denied")
    if target_role == "owner":
        deny("The organization owner cannot be modified", "permission_denied")
    if ctx.org_role != "owner":
        if target_role == "admin":
            deny("Only the organization owner can modify an Admin", "permission_denied")
        if new_role == "admin":
            deny("Only the organization owner can grant the Admin role")
    if ORG_ROLE_RANK.get(new_role or "", 0) > ORG_ROLE_RANK.get(ctx.org_role, 0):
        deny("You cannot grant a role above your own")


# ── Query scoping ───────────────────────────────────────────────────────────

def org_match(org_id: Any) -> Any:
    """Match an organization_id stored as string or ObjectId."""
    s_id = str(org_id)
    o = _oid(s_id)
    return {"$in": [s_id, o]} if o is not None else s_id


def scope_filter(ctx: TenantContext, *, owner_field: str = "user_id",
                 legacy_email_field: Optional[str] = "created_by",
                 assigned_field: Optional[str] = None,
                 force_own: bool = False) -> Dict[str, Any]:
    """Mongo filter limiting a tenant collection to what ``ctx`` may see."""
    if ctx.is_platform:
        return {}
    if not ctx.organization_id:
        return {"_id": None}  # fail closed
    clauses: List[Dict[str, Any]] = [{"organization_id": org_match(ctx.organization_id)}]
    if force_own or not ctx.can_view_all_org_data:
        own: List[Dict[str, Any]] = [{owner_field: ctx.user_id}]
        if legacy_email_field:
            # Records created before user_id stamping carry only the email.
            own.append({owner_field: {"$in": [None, ""]}, legacy_email_field: ctx.email})
        if assigned_field:
            own.append({assigned_field: ctx.user_id})
        clauses.append({"$or": own})
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def scope_query(ctx: TenantContext, query: Optional[Dict[str, Any]] = None,
                **kw) -> Dict[str, Any]:
    """AND the caller's scope onto ``query``."""
    scope = scope_filter(ctx, **kw)
    query = query or {}
    if not scope:
        return dict(query)
    if not query:
        return scope
    return {"$and": [query, scope]}


def stamp(ctx: TenantContext, doc: Dict[str, Any]) -> Dict[str, Any]:
    """Tenant ownership fields for a new document."""
    doc.setdefault("organization_id", ctx.organization_id or None)
    doc.setdefault("user_id", ctx.user_id)
    doc.setdefault("created_by", ctx.email)
    return doc


def report_out_of_scope(request: Optional[Request], ctx: TenantContext,
                        collection: str, doc: Dict[str, Any]) -> None:
    """Log an access attempt on a record outside the caller's scope."""
    try:
        from app.events.security import security_event_from_request
        target_org = str(doc.get("organization_id") or "")
        same_org = bool(ctx.organization_id) and target_org == str(ctx.organization_id)
        security_event_from_request(
            request,
            "cross_user_access" if same_org else "cross_tenant_access",
            "medium" if same_org else "high",
            ctx=ctx,
            target_organization_id=target_org or None,
            details={"collection": collection, "resource_id": str(doc.get("_id"))},
        )
    except Exception as e:  # pragma: no cover
        logger.warning("could not log out-of-scope access: %s", e)


async def find_scoped_or_404(db, collection: str, query: Dict[str, Any],
                             ctx: TenantContext, request: Optional[Request] = None,
                             **scope_kw) -> Dict[str, Any]:
    """find_one within the caller's scope; 404 (and a security event when
    the record exists elsewhere) otherwise."""
    doc = await db[collection].find_one(scope_query(ctx, query, **scope_kw))
    if doc:
        return doc
    other = await db[collection].find_one(query, {"_id": 1, "organization_id": 1})
    if other:
        report_out_of_scope(request, ctx, collection, other)
    raise HTTPException(status_code=404, detail=f"{collection} document not found")
