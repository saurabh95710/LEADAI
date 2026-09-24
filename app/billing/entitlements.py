"""
Centralized SaaS Feature Entitlement and Quota Enforcement Service.

Enforces feature flags, quota limits, and temporary entitlement grants.
Raises structured `QUOTA_EXCEEDED` (HTTP 402) and `FEATURE_NOT_AVAILABLE` (HTTP 403)
exceptions with clear upgrade guidance.
"""
import logging
from typing import Any, Dict, List, Optional, Tuple
from bson import ObjectId
from fastapi import HTTPException

from app.billing.plans import get_plan_by_slug_or_id, get_plan_sync
from app.billing.usage import (
    METRIC_TO_COUNTER_FIELD,
    get_current_usage_doc,
    get_current_usage_doc_sync,
    record_usage_atomic,
    record_usage_atomic_sync,
)
from app.db.models import utcnow
from app.db.mongo import get_async_db, get_sync_db

logger = logging.getLogger(__name__)


class QuotaExceededException(HTTPException):
    """Exception raised when an organization exceeds its quota limit."""

    def __init__(self, metric: str, used: int, limit: int, remaining: int = 0):
        super().__init__(
            status_code=402,
            detail={
                "success": False,
                "code": "QUOTA_EXCEEDED",
                "error": "QUOTA_EXCEEDED",
                "metric": metric,
                "used": used,
                "limit": limit,
                "remaining": max(0, remaining),
                "upgrade_available": True,
                "message": f"Monthly limit reached for {metric.replace('_', ' ')} ({used}/{limit}). Please upgrade your plan to continue.",
            },
        )


class FeatureNotAvailableException(HTTPException):
    """Exception raised when a requested feature is not included in the tenant's plan."""

    def __init__(self, feature_key: str, plan_name: str = "your current plan"):
        super().__init__(
            status_code=403,
            detail={
                "success": False,
                "code": "FEATURE_NOT_AVAILABLE",
                "error": "FEATURE_NOT_AVAILABLE",
                "feature": feature_key,
                "plan": plan_name,
                "upgrade_available": True,
                "message": f"The feature '{feature_key}' is not included in {plan_name}. Please upgrade to access this feature.",
            },
        )


class AccessBlockedException(HTTPException):
    """The organization may not run metered actions (demo expired, inactive)."""

    def __init__(self, code: str, message: str):
        super().__init__(status_code=402, detail={
            "success": False, "code": code, "error": code,
            "upgrade_available": True, "message": message,
        })


UNLIMITED = 10 ** 9
# Statuses of a subscription that entitle the organization to its plan.
ENTITLED_SUB_STATUSES = ("active",)


def _aware(dt):
    from datetime import timezone
    if dt is not None and getattr(dt, "tzinfo", None) is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def demo_plan_from_org(org: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Synthetic plan for an approved demo, built from the DemoConfig snapshot
    stored on the organization when the demo was approved."""
    if not org or org.get("status") != "demo":
        return None
    cfg = (org.get("demo") or {}).get("config") or {}
    features = ["url_search"] + list(cfg.get("allowed_platforms") or [])
    if cfg.get("ai_enabled"):
        features += ["ai_analysis", "lead_scoring"]
    if cfg.get("exports_enabled"):
        features.append("csv_export")
    return {
        "id": "demo", "name": "Demo", "slug": "demo", "is_demo": True,
        "price_monthly": 0.0, "price_yearly": 0.0, "currency": "USD",
        "features": features,
        "limits": {
            "monthly_searches": int(cfg.get("max_searches", 0)),
            "posts_per_search": int(cfg.get("posts_per_search", 0)),
            "comments_per_post": int(cfg.get("comments_per_post", 0)),
            "max_leads": int(cfg.get("max_leads", 0)),
            "team_members": int(cfg.get("max_users", 1)),
            "monthly_posts": UNLIMITED,
            "monthly_comments": UNLIMITED,
            "monthly_ai_analyses": UNLIMITED if cfg.get("ai_enabled") else 0,
            "monthly_exports": UNLIMITED if cfg.get("exports_enabled") else 0,
        },
    }


def _org_doc_sync(organization_id: str) -> Optional[Dict[str, Any]]:
    db = get_sync_db()
    if db is None:
        return None
    try:
        return db.organizations.find_one({"_id": ObjectId(str(organization_id))})
    except Exception:
        return None


def operating_block_reason(org: Optional[Dict[str, Any]]) -> Optional[Tuple[str, str]]:
    """(code, message) when the org may not run metered actions, else None."""
    if not org:
        return None
    status = org.get("status", "active")
    if status == "demo":
        exp = _aware((org.get("demo") or {}).get("expires_at"))
        if exp is not None and exp <= utcnow():
            return ("DEMO_EXPIRED", "Your demo has expired. Choose a plan to keep using LeadAI.")
        return None
    if status in ("active", "trial"):
        return None
    return ("ORGANIZATION_INACTIVE", f"Organization is {status}.")


class EntitlementService:
    """Centralized service for checking feature access and enforcing usage limits."""

    @staticmethod
    def assert_can_operate(organization_id: str) -> None:
        """Raise 402 when the organization may not consume anything."""
        reason = operating_block_reason(_org_doc_sync(organization_id))
        if reason:
            raise AccessBlockedException(*reason)

    @classmethod
    def get_run_caps(cls, organization_id: str) -> Dict[str, Optional[int]]:
        """Per-search caps from the effective plan (None = no plan cap)."""
        limits = cls.get_effective_plan_sync(organization_id).get("limits", {}) or {}

        def cap(key):
            try:
                v = int(limits.get(key))
                return v if v > 0 else None
            except (TypeError, ValueError):
                return None
        return {"posts_per_search": cap("posts_per_search"),
                "comments_per_post": cap("comments_per_post"),
                "max_leads": cap("max_leads")}

    @staticmethod
    async def get_effective_plan(organization_id: str, db=None) -> Dict[str, Any]:
        """Resolve the effective plan for an organization."""
        if db is None:
            db = get_async_db()
        if not organization_id or db is None:
            return (await get_plan_by_slug_or_id("free")) or {}
        s_org_id = str(organization_id)
        try:
            org = await db.organizations.find_one({"_id": ObjectId(s_org_id)})
        except Exception:
            org = None

        # 1. Approved demo -> synthetic demo plan
        demo = demo_plan_from_org(org)
        if demo:
            return demo

        # 2. Confirmed (active) subscription, newest first
        sub = await db.subscriptions.find_one(
            {"organization_id": s_org_id, "status": {"$in": list(ENTITLED_SUB_STATUSES)}},
            sort=[("current_period_start", -1)])
        if sub and sub.get("plan_id"):
            plan = await get_plan_by_slug_or_id(sub["plan_id"], db=db)
            if plan:
                return plan

        # 3. Legacy self-signup trial — only while it has not expired
        trial = await db.subscriptions.find_one(
            {"organization_id": s_org_id, "status": "trialing"}, sort=[("created_at", -1)])
        if trial and trial.get("plan_id") and _aware(trial.get("trial_end")) \
                and _aware(trial.get("trial_end")) > utcnow():
            plan = await get_plan_by_slug_or_id(trial["plan_id"], db=db)
            if plan:
                return plan

        # 4. Legacy active org without a subscription document keeps its plan
        if org and org.get("status") == "active" and org.get("plan_id"):
            plan = await get_plan_by_slug_or_id(org["plan_id"], db=db)
            if plan:
                return plan

        return (await get_plan_by_slug_or_id("free", db=db)) or {}

    @staticmethod
    def get_effective_plan_sync(organization_id: str) -> Dict[str, Any]:
        """Synchronous version for thread workers."""
        db = get_sync_db()
        if not organization_id or db is None:
            return get_plan_sync("free") or {}
        s_org_id = str(organization_id)
        org = _org_doc_sync(s_org_id)

        demo = demo_plan_from_org(org)
        if demo:
            return demo

        sub = db.subscriptions.find_one(
            {"organization_id": s_org_id, "status": {"$in": list(ENTITLED_SUB_STATUSES)}},
            sort=[("current_period_start", -1)])
        if sub and sub.get("plan_id"):
            plan = get_plan_sync(sub["plan_id"])
            if plan:
                return plan

        trial = db.subscriptions.find_one(
            {"organization_id": s_org_id, "status": "trialing"}, sort=[("created_at", -1)])
        if trial and trial.get("plan_id") and _aware(trial.get("trial_end")) \
                and _aware(trial.get("trial_end")) > utcnow():
            plan = get_plan_sync(trial["plan_id"])
            if plan:
                return plan

        if org and org.get("status") == "active" and org.get("plan_id"):
            plan = get_plan_sync(org["plan_id"])
            if plan:
                return plan

        return get_plan_sync("free") or {}

    @classmethod
    async def has_feature(cls, organization_id: str, feature_key: str, db=None) -> bool:
        """Check if an organization has access to a specific feature."""
        if not organization_id:
            return False

        if db is None:
            db = get_async_db()

        now = utcnow()
        s_org_id = str(organization_id)

        # 1. Check temporary feature grants
        if db is not None:
            temp_grant = await db.temporary_entitlements.find_one({
                "organization_id": s_org_id,
                "entitlement_type": "feature",
                "key": feature_key,
                "value": True,
                "$or": [{"expires_at": None}, {"expires_at": {"$gt": now}}],
            })
            if temp_grant:
                return True

        # 2. Check base plan features
        plan = await cls.get_effective_plan(s_org_id, db=db)
        features = plan.get("features", [])
        return feature_key in features

    @classmethod
    def has_feature_sync(cls, organization_id: str, feature_key: str) -> bool:
        """Synchronous feature check."""
        if not organization_id:
            return False

        db = get_sync_db()
        now = utcnow()
        s_org_id = str(organization_id)

        if db is not None:
            temp_grant = db.temporary_entitlements.find_one({
                "organization_id": s_org_id,
                "entitlement_type": "feature",
                "key": feature_key,
                "value": True,
                "$or": [{"expires_at": None}, {"expires_at": {"$gt": now}}],
            })
            if temp_grant:
                return True

        plan = cls.get_effective_plan_sync(s_org_id)
        features = plan.get("features", [])
        return feature_key in features

    @classmethod
    async def get_limit(cls, organization_id: str, metric: str, db=None) -> int:
        """Calculate effective limit including base plan limits and credit grants."""
        if not organization_id:
            return 999999

        if db is None:
            db = get_async_db()

        plan = await cls.get_effective_plan(organization_id, db=db)
        base_limit = int(plan.get("limits", {}).get(metric, 0) or 0)

        # Add bonus credits from temporary entitlements
        bonus = 0
        if db is not None:
            cursor = db.temporary_entitlements.find({
                "organization_id": str(organization_id),
                "entitlement_type": "credit",
                "key": metric,
                "$or": [{"expires_at": None}, {"expires_at": {"$gt": utcnow()}}],
            })
            async for grant in cursor:
                try:
                    bonus += int(grant.get("value", 0))
                except Exception:
                    pass

        return base_limit + bonus

    @classmethod
    async def check_limit(
        cls, organization_id: str, metric: str, requested: int = 1, db=None
    ) -> Tuple[bool, int, int]:
        """Check if requested quantity is within limits. Returns (allowed, used, limit)."""
        if not organization_id:
            return True, 0, 999999

        limit = await cls.get_limit(organization_id, metric, db=db)
        if metric == "team_members":
            if db is None:
                db = get_async_db()
            s_org = str(organization_id)
            used = await db.organization_members.count_documents(
                {"organization_id": s_org, "status": {"$in": ["active", "suspended"]}})
            used += await db.organization_invitations.count_documents(
                {"organization_id": s_org, "status": "pending"})
            return (used + requested) <= limit, used, limit
        usage_doc = await get_current_usage_doc(organization_id, db=db)

        counter_field = METRIC_TO_COUNTER_FIELD.get(metric, f"{metric}_used")
        used = usage_doc.get(counter_field, 0)

        allowed = (used + requested) <= limit
        return allowed, used, limit

    @classmethod
    async def enforce_quota_and_consume(
        cls,
        organization_id: str,
        feature_key: Optional[str] = None,
        metric: Optional[str] = None,
        quantity: int = 1,
        user_id: Optional[str] = None,
        source: Optional[str] = None,
        db=None,
    ) -> int:
        """Check feature availability and enforce quota, raising structured errors if failed.

        If successful, atomically increments the quota counter and returns the new usage total.
        """
        if not organization_id:
            raise AccessBlockedException("NO_ORGANIZATION", "An organization is required.")

        s_org_id = str(organization_id)
        cls.assert_can_operate(s_org_id)

        # 1. Feature Check
        if feature_key:
            has_feat = await cls.has_feature(s_org_id, feature_key, db=db)
            if not has_feat:
                plan = await cls.get_effective_plan(s_org_id, db=db)
                raise FeatureNotAvailableException(feature_key, plan.get("name", "Current Plan"))

        # 2. Quota Check
        if metric:
            allowed, used, limit = await cls.check_limit(s_org_id, metric, requested=quantity, db=db)
            if not allowed:
                raise QuotaExceededException(metric, used=used, limit=limit, remaining=max(0, limit - used))

            # 3. Atomic Quota Consumption
            new_total = await record_usage_atomic(
                organization_id=s_org_id,
                metric=metric,
                quantity=quantity,
                user_id=user_id,
                source=source,
                db=db,
            )
            return new_total

        return 0

    @classmethod
    def enforce_quota_and_consume_sync(
        cls,
        organization_id: str,
        feature_key: Optional[str],
        metric: Optional[str],
        quantity: int = 1,
        user_id: Optional[str] = None,
        source: Optional[str] = None,
    ) -> int:
        """Synchronous quota check & consumption for worker threads."""
        if not organization_id:
            raise AccessBlockedException("NO_ORGANIZATION", "An organization is required.")

        s_org_id = str(organization_id)
        cls.assert_can_operate(s_org_id)

        if feature_key:
            if not cls.has_feature_sync(s_org_id, feature_key):
                plan = cls.get_effective_plan_sync(s_org_id)
                raise FeatureNotAvailableException(feature_key, plan.get("name", "Current Plan"))

        if metric:
            plan = cls.get_effective_plan_sync(s_org_id)
            base_limit = int(plan.get("limits", {}).get(metric, 0) or 0)

            db = get_sync_db()
            bonus = 0
            if db is not None:
                cursor = db.temporary_entitlements.find({
                    "organization_id": s_org_id,
                    "entitlement_type": "credit",
                    "key": metric,
                    "$or": [{"expires_at": None}, {"expires_at": {"$gt": utcnow()}}],
                })
                for grant in cursor:
                    try:
                        bonus += int(grant.get("value", 0))
                    except Exception:
                        pass
            limit = base_limit + bonus

            usage_doc = get_current_usage_doc_sync(s_org_id)
            counter_field = METRIC_TO_COUNTER_FIELD.get(metric, f"{metric}_used")
            used = usage_doc.get(counter_field, 0)

            if (used + quantity) > limit:
                raise QuotaExceededException(metric, used=used, limit=limit, remaining=max(0, limit - used))

            return record_usage_atomic_sync(
                organization_id=s_org_id,
                metric=metric,
                quantity=quantity,
                user_id=user_id,
                source=source,
            )

        return 0

    @classmethod
    async def get_usage_summary(cls, organization_id: str, db=None) -> Dict[str, Any]:
        """Return a complete breakdown of current usage vs plan limits for frontend UI."""
        if db is None:
            db = get_async_db()

        plan = await cls.get_effective_plan(organization_id, db=db)
        usage_doc = await get_current_usage_doc(organization_id, db=db)
        s_org_id = str(organization_id)

        # Count actual team members
        member_count = 1
        if db is not None:
            member_count = await db.organization_members.count_documents({
                "organization_id": s_org_id,
                "status": "active",
            })

        metrics_summary = {}
        for metric, counter_field in METRIC_TO_COUNTER_FIELD.items():
            used = usage_doc.get(counter_field, 0)
            limit = await cls.get_limit(s_org_id, metric, db=db)
            percentage = round((used / limit * 100), 1) if limit > 0 else 0
            metrics_summary[metric] = {
                "used": used,
                "limit": limit,
                "remaining": max(0, limit - used),
                "percentage": min(100.0, percentage),
            }

        team_limit = int(plan.get("limits", {}).get("team_members", 0) or 0)
        metrics_summary["team_members"] = {
            "used": member_count,
            "limit": team_limit,
            "remaining": max(0, team_limit - member_count),
            "percentage": min(100.0, round((member_count / team_limit * 100), 1)) if team_limit > 0 else 0,
        }

        from app.billing.tokens import get_balance
        return {
            "plan": {
                "id": plan.get("id") or str(plan.get("_id", "")),
                "name": plan.get("name"),
                "slug": plan.get("slug"),
                "price_monthly": plan.get("price_monthly"),
                "price_yearly": plan.get("price_yearly"),
                "currency": plan.get("currency"),
                "features": plan.get("features", []),
                "limits": plan.get("limits", {}),
                "is_demo": bool(plan.get("is_demo")),
            },
            "tokens": get_balance(s_org_id),
            "period": {
                "start": usage_doc.get("period_start"),
                "end": usage_doc.get("period_end"),
            },
            "metrics": metrics_summary,
        }
