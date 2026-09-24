"""
LeadAI Centralized Role-Based Access Control (RBAC) & Permission Engine.

Defines granular permissions and role-permission matrices for:
  1. Internal Platform Roles (super_admin, operations_admin, billing_admin, support_admin, technical_admin, viewer)
  2. Customer Organization Roles (owner, admin, manager, member, viewer)
"""
import time
from typing import Any, Dict, FrozenSet, Optional, Set

# ── Granular Platform Permissions (Super Admin & Platform Staff) ──────────────
PLATFORM_VIEW = "platform.view"
PLATFORM_MANAGE = "platform.manage"

ORGS_VIEW = "organizations.view"
ORGS_CREATE = "organizations.create"
ORGS_UPDATE = "organizations.update"
ORGS_SUSPEND = "organizations.suspend"
ORGS_DELETE = "organizations.delete"

USERS_VIEW = "users.view"
USERS_CREATE = "users.create"
USERS_UPDATE = "users.update"
USERS_SUSPEND = "users.suspend"
USERS_DELETE = "users.delete"

BILLING_VIEW = "billing.view"
BILLING_MANAGE = "billing.manage"

AI_VIEW = "ai.view"
AI_MANAGE = "ai.manage"

APIFY_VIEW = "apify.view"
APIFY_MANAGE = "apify.manage"

SYSTEM_VIEW = "system.view"
SYSTEM_MANAGE = "system.manage"

AUDIT_VIEW = "audit.view"
IMPERSONATE_USER = "impersonate.user"

# ── Granular Organization Permissions (Customer SaaS Workspace) ──────────────
WORKSPACE_VIEW = "workspace.view"
WORKSPACE_MANAGE = "workspace.manage"

MEMBERS_VIEW = "members.view"
MEMBERS_INVITE = "members.invite"
MEMBERS_MANAGE = "members.manage"

SEARCH_VIEW = "search.view"
SEARCH_CREATE = "search.create"
SEARCH_CANCEL = "search.cancel"

LEADS_VIEW = "leads.view"
LEADS_MANAGE = "leads.manage"
LEADS_EXPORT = "leads.export"

SETTINGS_VIEW = "settings.view"
SETTINGS_MANAGE = "settings.manage"

# Step 1.4 explicit model: Users (view/create/update/suspend/delete),
# Search (create/view/cancel/export), Leads, Exports, Settings, Billing.
MEMBERS_UPDATE = "members.update"
MEMBERS_SUSPEND = "members.suspend"
MEMBERS_DELETE = "members.delete"
SEARCH_EXPORT = "search.export"
LEADS_ASSIGN = "leads.assign"
EXPORTS_VIEW = "exports.view"
EXPORTS_CREATE = "exports.create"
ORG_BILLING_VIEW = "org_billing.view"
ORG_BILLING_MANAGE = "org_billing.manage"
ORG_AUDIT_VIEW = "org_audit.view"
ROLES_MANAGE = "roles.manage"
# See every member's private data inside the organization (Admin default).
DATA_VIEW_ALL = "data.view_all"

# ── Platform Role -> Permissions Matrix ──────────────────────────────────────
PLATFORM_ROLE_PERMISSIONS: Dict[str, FrozenSet[str]] = {
    "super_admin": frozenset({
        PLATFORM_VIEW, PLATFORM_MANAGE,
        ORGS_VIEW, ORGS_CREATE, ORGS_UPDATE, ORGS_SUSPEND, ORGS_DELETE,
        USERS_VIEW, USERS_CREATE, USERS_UPDATE, USERS_SUSPEND, USERS_DELETE,
        BILLING_VIEW, BILLING_MANAGE,
        AI_VIEW, AI_MANAGE,
        APIFY_VIEW, APIFY_MANAGE,
        SYSTEM_VIEW, SYSTEM_MANAGE,
        AUDIT_VIEW, IMPERSONATE_USER,
    }),
    "operations_admin": frozenset({
        PLATFORM_VIEW,
        ORGS_VIEW, ORGS_CREATE, ORGS_UPDATE, ORGS_SUSPEND,
        USERS_VIEW, USERS_UPDATE, USERS_SUSPEND,
        AUDIT_VIEW, IMPERSONATE_USER,
        SYSTEM_VIEW,
    }),
    "billing_admin": frozenset({
        PLATFORM_VIEW,
        ORGS_VIEW,
        BILLING_VIEW, BILLING_MANAGE,
        AUDIT_VIEW,
    }),
    "support_admin": frozenset({
        PLATFORM_VIEW,
        ORGS_VIEW,
        USERS_VIEW,
        AUDIT_VIEW,
        IMPERSONATE_USER,
    }),
    "technical_admin": frozenset({
        PLATFORM_VIEW,
        SYSTEM_VIEW, SYSTEM_MANAGE,
        AI_VIEW, AI_MANAGE,
        APIFY_VIEW, APIFY_MANAGE,
        AUDIT_VIEW,
    }),
    "viewer": frozenset({
        PLATFORM_VIEW,
        ORGS_VIEW,
        USERS_VIEW,
        SYSTEM_VIEW,
        AUDIT_VIEW,
    }),
}

# ── Customer Organization Role -> Permissions Matrix ────────────────────────
_ADMIN_ORG_PERMS = frozenset({
    WORKSPACE_VIEW,
    MEMBERS_VIEW, MEMBERS_INVITE, MEMBERS_MANAGE,
    MEMBERS_UPDATE, MEMBERS_SUSPEND, MEMBERS_DELETE,
    SEARCH_VIEW, SEARCH_CREATE, SEARCH_CANCEL, SEARCH_EXPORT,
    LEADS_VIEW, LEADS_MANAGE, LEADS_EXPORT, LEADS_ASSIGN,
    EXPORTS_VIEW, EXPORTS_CREATE,
    SETTINGS_VIEW, SETTINGS_MANAGE,
    ORG_BILLING_VIEW, ORG_BILLING_MANAGE,
    ORG_AUDIT_VIEW, ROLES_MANAGE, DATA_VIEW_ALL,
})

ORG_ROLE_PERMISSIONS: Dict[str, FrozenSet[str]] = {
    "owner": _ADMIN_ORG_PERMS | {WORKSPACE_MANAGE},
    "admin": _ADMIN_ORG_PERMS,
    "manager": frozenset({
        WORKSPACE_VIEW,
        MEMBERS_VIEW,
        SEARCH_VIEW, SEARCH_CREATE, SEARCH_CANCEL, SEARCH_EXPORT,
        LEADS_VIEW, LEADS_MANAGE, LEADS_EXPORT, LEADS_ASSIGN,
        EXPORTS_VIEW, EXPORTS_CREATE,
        SETTINGS_VIEW,
        ORG_BILLING_VIEW,
    }),
    # "member" is the spec's USER role
    "member": frozenset({
        WORKSPACE_VIEW,
        SEARCH_VIEW, SEARCH_CREATE, SEARCH_CANCEL, SEARCH_EXPORT,
        LEADS_VIEW, LEADS_MANAGE, LEADS_EXPORT,
        EXPORTS_VIEW, EXPORTS_CREATE,
    }),
    "viewer": frozenset({
        WORKSPACE_VIEW,
        SEARCH_VIEW,
        LEADS_VIEW,
        EXPORTS_VIEW,
    }),
}

ALL_ORG_PERMISSIONS: FrozenSet[str] = frozenset().union(*ORG_ROLE_PERMISSIONS.values())
ALL_PLATFORM_PERMISSIONS: FrozenSet[str] = frozenset().union(*PLATFORM_ROLE_PERMISSIONS.values())

# Roles whose permissions an org Admin may tune (never owner/admin), and the
# permissions an Admin may delegate. Platform permissions live in a separate
# namespace and can never be granted from an organization.
CONFIGURABLE_ORG_ROLES = ("manager", "member", "viewer")
DELEGABLE_ORG_PERMISSIONS: FrozenSet[str] = _ADMIN_ORG_PERMS - {
    ROLES_MANAGE, ORG_BILLING_MANAGE, WORKSPACE_MANAGE,
}

# Spec role names -> internal role keys
SPEC_ROLE_LABELS = {
    "super_admin": "Super Admin",
    "owner": "Admin (Owner)",
    "admin": "Admin",
    "manager": "Manager",
    "member": "User",
    "viewer": "Viewer",
}

# Role Rank for Hierarchy comparisons
PLATFORM_ROLE_RANK = {
    "viewer": 1,
    "support_admin": 2,
    "technical_admin": 2,
    "billing_admin": 2,
    "operations_admin": 3,
    "super_admin": 4,
}

ORG_ROLE_RANK = {
    "viewer": 1,
    "member": 2,
    "manager": 3,
    "admin": 4,
    "owner": 5,
}


def get_platform_role_permissions(role: str) -> Set[str]:
    return set(PLATFORM_ROLE_PERMISSIONS.get(role, frozenset()))


def get_org_role_permissions(role: str, overrides: Dict[str, bool] = None) -> Set[str]:
    base = set(ORG_ROLE_PERMISSIONS.get(role, frozenset()))
    if overrides:
        for perm, allowed in overrides.items():
            if perm not in DELEGABLE_ORG_PERMISSIONS:
                continue  # never grant anything outside the org namespace
            if allowed:
                base.add(perm)
            else:
                base.discard(perm)
    return base


# ── Effective permission resolution (global matrix -> org -> member) ───────

ROLE_MATRIX_COLLECTION = "role_permissions"
_MATRIX_TTL = 30.0
_MATRIX_CACHE: Dict[str, Any] = {"at": 0.0, "org": {}, "platform": {}}


def invalidate_permission_cache() -> None:
    _MATRIX_CACHE["at"] = 0.0


def _load_matrix_overrides() -> Dict[str, Any]:
    """Super-Admin-managed overrides of the default matrices (DB, cached)."""
    now = time.time()
    if now - _MATRIX_CACHE["at"] < _MATRIX_TTL:
        return _MATRIX_CACHE
    org: Dict[str, FrozenSet[str]] = {}
    platform: Dict[str, FrozenSet[str]] = {}
    try:
        from app.db.mongo import get_sync_db
        db = get_sync_db()
        if db is not None:
            for d in db[ROLE_MATRIX_COLLECTION].find({}):
                kind, role = d.get("kind"), d.get("role")
                perms = frozenset(d.get("permissions") or [])
                if kind == "org" and role in ORG_ROLE_PERMISSIONS and role != "owner":
                    org[role] = perms & ALL_ORG_PERMISSIONS
                elif (kind == "platform" and role in PLATFORM_ROLE_PERMISSIONS
                      and role != "super_admin"):
                    # super_admin is immutable: it always has everything
                    platform[role] = perms & ALL_PLATFORM_PERMISSIONS
    except Exception:
        pass
    _MATRIX_CACHE.update({"at": now, "org": org, "platform": platform})
    return _MATRIX_CACHE


def global_org_role_permissions(role: str) -> Set[str]:
    overrides = _load_matrix_overrides()["org"]
    if role in overrides:
        return set(overrides[role])
    return set(ORG_ROLE_PERMISSIONS.get(role, frozenset()))


def effective_platform_permissions(role: Optional[str]) -> Set[str]:
    if not role:
        return set()
    if role == "super_admin":
        return set(PLATFORM_ROLE_PERMISSIONS["super_admin"])
    overrides = _load_matrix_overrides()["platform"]
    if role in overrides:
        return set(overrides[role])
    return set(PLATFORM_ROLE_PERMISSIONS.get(role, frozenset()))


def resolve_org_permissions(role: str, org_doc: Optional[Dict[str, Any]] = None,
                            member_doc: Optional[Dict[str, Any]] = None) -> Set[str]:
    """Effective org permissions: global matrix (Super Admin) -> org role
    config (Admin; configurable roles only) -> per-member override.

    Org- and member-level toggles are limited to DELEGABLE_ORG_PERMISSIONS,
    so nothing outside the organization namespace — never a platform
    permission — can be granted from inside an organization.
    """
    perms = global_org_role_permissions(role)
    if role in CONFIGURABLE_ORG_ROLES and org_doc:
        role_cfg = ((org_doc.get("settings") or {}).get("role_permissions") or {}).get(role) or {}
        for perm, allowed in role_cfg.items():
            if perm in DELEGABLE_ORG_PERMISSIONS:
                (perms.add if allowed else perms.discard)(perm)
    if role in CONFIGURABLE_ORG_ROLES and member_doc:
        for perm, allowed in (member_doc.get("permissions_override") or {}).items():
            if perm in DELEGABLE_ORG_PERMISSIONS:
                (perms.add if allowed else perms.discard)(perm)
    # Shared workspace: the Admin explicitly lets every member read all org data.
    if org_doc and (org_doc.get("settings") or {}).get("shared_workspace"):
        perms.add(DATA_VIEW_ALL)
    return perms


def has_platform_permission(role: str, permission: str) -> bool:
    return permission in PLATFORM_ROLE_PERMISSIONS.get(role, frozenset())


def has_org_permission(role: str, permission: str, overrides: Dict[str, bool] = None) -> bool:
    if overrides and permission in overrides:
        return bool(overrides[permission])
    return permission in ORG_ROLE_PERMISSIONS.get(role, frozenset())
