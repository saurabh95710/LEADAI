"""
Idempotent SaaS Multi-Tenancy Migration & Backfill Engine.

Ensures that existing single-tenant data (searches, pages, posts, comments,
leads, and rules) are safely associated with a Default Organization without
loss or disruption.
"""
import logging
from typing import Any, Dict
from bson import ObjectId

from app.config import get_settings
from app.db.models import utcnow

logger = logging.getLogger(__name__)
settings = get_settings()


def migrate_to_multi_tenant(db) -> Dict[str, Any]:
    """Idempotently migrate single-tenant database collections to multi-tenancy.

    1. Ensures a 'Default Organization' exists.
    2. Ensures default owner exists in `users` collection.
    3. Ensures owner membership exists in `organization_members`.
    4. Backfills `organization_id` on existing customer documents lacking it.
    """
    if db is None:
        logger.warning("[Migration] Database unavailable — skipping multi-tenant migration.")
        return {"status": "skipped", "reason": "db_unavailable"}

    report: Dict[str, Any] = {"status": "ok", "backfilled": {}}

    try:
        # 1. Ensure Default Organization exists
        default_org = db.organizations.find_one({"slug": "default-org"})
        if not default_org:
            default_org_doc = {
                "name": "Default Organization",
                "slug": "default-org",
                "status": "active",
                "plan_id": "enterprise",
                "timezone": "UTC",
                "currency": "USD",
                "settings": {"auto_export": True, "lead_scoring_enabled": True},
                "metadata": {"system_created": True},
                "created_at": utcnow(),
                "updated_at": utcnow(),
                "last_activity_at": utcnow(),
            }
            res = db.organizations.insert_one(default_org_doc)
            default_org_id = str(res.inserted_id)
            logger.info("[Migration] Created Default Organization with id: %s", default_org_id)
            report["default_org_created"] = True
        else:
            default_org_id = str(default_org["_id"])
            report["default_org_created"] = False

        # 2. Ensure the legacy site owner exists in `users` (ADMIN_EMAIL).
        #    Never invent credentials: without ADMIN_EMAIL + a password hash
        #    no account is seeded, and a seeded owner is an ORGANIZATION owner,
        #    never a platform super admin (that is PANEL_ADMIN_EMAIL's job).
        admin_email = (settings.admin_email or "").strip().lower()
        owner_user = db.users.find_one({"email": admin_email}) if admin_email else None
        owner_user_id = str(owner_user["_id"]) if owner_user else None
        if admin_email and not owner_user:
            legacy_admin = db.admin_users.find_one({"email": admin_email})
            pwd_hash = (legacy_admin or {}).get("password_hash") or (settings.admin_password_hash or "")
            if not pwd_hash:
                logger.warning("[Migration] ADMIN_EMAIL is set without ADMIN_PASSWORD_HASH — "
                               "not seeding an owner account (use the password reset flow).")
            else:
                owner_user_doc = {
                    "email": admin_email,
                    "name": "Admin",
                    "password_hash": pwd_hash,
                    "status": "active",
                    "is_platform_admin": False,
                    "platform_role": None,
                    "default_organization_id": default_org_id,
                    "created_at": utcnow(),
                    "updated_at": utcnow(),
                    "last_login": None,
                }
                owner_user_id = str(db.users.insert_one(owner_user_doc).inserted_id)
                logger.info("[Migration] Seeded default owner user '%s' (_id: %s)", admin_email, owner_user_id)
        if not owner_user_id:
            report["owner"] = "skipped"

        # Link owner as owner in Default Organization if not set
        if owner_user_id:
            db.organizations.update_one(
                {"_id": ObjectId(default_org_id), "owner_id": {"$in": [None, ""]}},
                {"$set": {"owner_id": owner_user_id, "admin_portal_enabled": True,
                          "updated_at": utcnow()}}
            )

        # 3. Ensure membership exists in `organization_members`
        existing_membership = db.organization_members.find_one({
            "organization_id": default_org_id,
            "user_id": owner_user_id,
        }) if owner_user_id else True
        if not existing_membership:
            member_doc = {
                "organization_id": default_org_id,
                "user_id": owner_user_id,
                "role": "owner",
                "status": "active",
                "joined_at": utcnow(),
                "invited_by": None,
                "last_activity_at": utcnow(),
                "permissions_override": {},
            }
            db.organization_members.insert_one(member_doc)
            logger.info("[Migration] Linked user %s as owner of organization %s", owner_user_id, default_org_id)

        # 4. Idempotently backfill existing customer collections
        collections_to_backfill = [
            "search_history",
            "facebook_pages",
            "facebook_posts",
            "facebook_comments",
            "ai_comments",
            "comment_filter_rules",
            "comment_filter_results",
        ]

        for col_name in collections_to_backfill:
            try:
                coll = db[col_name]
                query = {"$or": [{"organization_id": {"$exists": False}}, {"organization_id": None}, {"organization_id": ""}]}
                mod_res = coll.update_many(query, {"$set": {"organization_id": default_org_id}})
                report["backfilled"][col_name] = mod_res.modified_count
                if mod_res.modified_count > 0:
                    logger.info("[Migration] Backfilled %d documents in '%s' with organization_id %s",
                                mod_res.modified_count, col_name, default_org_id)
            except Exception as col_err:
                logger.warning("[Migration] Error backfilling '%s': %s", col_name, col_err)
                report["backfilled"][col_name] = 0

        return report
    except Exception as e:
        logger.exception("[Migration] Unexpected error during multi-tenant migration: %s", e)
        return {"status": "error", "error": str(e)}
