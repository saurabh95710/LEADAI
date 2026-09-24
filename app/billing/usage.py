"""
Centralized SaaS Usage Tracking and Atomic Quota Counters.

Maintains concurrency-safe aggregated counters in `organization_usage`
and immutable audit event logs in `usage_records`.
"""
import calendar
from datetime import datetime, timezone
import logging
from typing import Any, Dict, Optional, Tuple
from bson import ObjectId

from app.db.models import utcnow
from app.db.mongo import get_async_db, get_sync_db

logger = logging.getLogger(__name__)

METRIC_TO_COUNTER_FIELD = {
    "monthly_searches": "searches_used",
    "monthly_posts": "posts_used",
    "monthly_comments": "comments_used",
    "monthly_ai_analyses": "ai_used",
    "monthly_exports": "exports_used",
    "api_requests": "api_requests_used",
}


def get_current_calendar_period() -> Tuple[datetime, datetime, str]:
    """Calculate start and end of the current UTC calendar month."""
    now = utcnow()
    year = now.year
    month = now.month
    _, last_day = calendar.monthrange(year, month)

    start = datetime(year, month, 1, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(year, month, last_day, 23, 59, 59, 999999, tzinfo=timezone.utc)
    period_str = f"{year:04d}-{month:02d}"
    return start, end, period_str


async def get_organization_period(organization_id: str, db=None) -> Tuple[datetime, datetime, str]:
    """Determine the active billing cycle period for an organization.

    Uses the organization's subscription billing cycle if available;
    falls back to calendar month.
    """
    if db is None:
        db = get_async_db()

    start, end, period_str = get_current_calendar_period()
    if db is not None:
        try:
            sub = await db.subscriptions.find_one({
                "organization_id": str(organization_id),
                "status": {"$in": ["active", "trialing", "past_due"]},
            })
            if sub and sub.get("current_period_start") and sub.get("current_period_end"):
                p_start = sub["current_period_start"]
                p_end = sub["current_period_end"]
                p_str = f"{p_start.strftime('%Y%m%d')}_{p_end.strftime('%Y%m%d')}"
                return p_start, p_end, p_str
        except Exception as e:
            logger.warning(f"Error resolving subscription period for {organization_id}: {e}")

    return start, end, period_str


def get_organization_period_sync(organization_id: str) -> Tuple[datetime, datetime, str]:
    """Synchronous version for background threads."""
    start, end, period_str = get_current_calendar_period()
    db = get_sync_db()
    if db is not None:
        try:
            sub = db.subscriptions.find_one({
                "organization_id": str(organization_id),
                "status": {"$in": ["active", "trialing", "past_due"]},
            })
            if sub and sub.get("current_period_start") and sub.get("current_period_end"):
                p_start = sub["current_period_start"]
                p_end = sub["current_period_end"]
                p_str = f"{p_start.strftime('%Y%m%d')}_{p_end.strftime('%Y%m%d')}"
                return p_start, p_end, p_str
        except Exception:
            pass
    return start, end, period_str


async def get_current_usage_doc(organization_id: str, db=None) -> Dict[str, Any]:
    """Retrieve or initialize the active usage counter document for an organization."""
    if db is None:
        db = get_async_db()
    if db is None:
        return {}

    p_start, p_end, _ = await get_organization_period(organization_id, db=db)
    s_org_id = str(organization_id)

    doc = await db.organization_usage.find_one({
        "organization_id": s_org_id,
        "period_start": p_start,
        "period_end": p_end,
    })

    if not doc:
        doc = {
            "organization_id": s_org_id,
            "period_start": p_start,
            "period_end": p_end,
            "searches_used": 0,
            "posts_used": 0,
            "comments_used": 0,
            "ai_used": 0,
            "exports_used": 0,
            "api_requests_used": 0,
            "updated_at": utcnow(),
        }
        try:
            res = await db.organization_usage.insert_one(doc)
            doc["_id"] = res.inserted_id
        except Exception:
            # Another concurrent request may have created it
            doc = await db.organization_usage.find_one({
                "organization_id": s_org_id,
                "period_start": p_start,
                "period_end": p_end,
            }) or doc

    return doc


def get_current_usage_doc_sync(organization_id: str) -> Dict[str, Any]:
    """Synchronous usage document retriever."""
    db = get_sync_db()
    if db is None:
        return {}

    p_start, p_end, _ = get_organization_period_sync(organization_id)
    s_org_id = str(organization_id)

    doc = db.organization_usage.find_one({
        "organization_id": s_org_id,
        "period_start": p_start,
        "period_end": p_end,
    })
    if not doc:
        doc = {
            "organization_id": s_org_id,
            "period_start": p_start,
            "period_end": p_end,
            "searches_used": 0,
            "posts_used": 0,
            "comments_used": 0,
            "ai_used": 0,
            "exports_used": 0,
            "api_requests_used": 0,
            "updated_at": utcnow(),
        }
        try:
            res = db.organization_usage.insert_one(doc)
            doc["_id"] = res.inserted_id
        except Exception:
            doc = db.organization_usage.find_one({
                "organization_id": s_org_id,
                "period_start": p_start,
                "period_end": p_end,
            }) or doc
    return doc


async def record_usage_atomic(
    organization_id: str,
    metric: str,
    quantity: int = 1,
    user_id: Optional[str] = None,
    source: Optional[str] = None,
    db=None,
) -> int:
    """Atomically increment usage counter and log an immutable audit record."""
    if db is None:
        db = get_async_db()
    if db is None or not organization_id:
        return 0

    s_org_id = str(organization_id)
    counter_field = METRIC_TO_COUNTER_FIELD.get(metric, f"{metric}_used")
    p_start, p_end, p_str = await get_organization_period(s_org_id, db=db)

    # 1. Atomic counter update
    update_res = await db.organization_usage.find_one_and_update(
        {
            "organization_id": s_org_id,
            "period_start": p_start,
            "period_end": p_end,
        },
        {
            "$inc": {counter_field: quantity},
            "$set": {"updated_at": utcnow()},
            "$setOnInsert": {
                "organization_id": s_org_id,
                "period_start": p_start,
                "period_end": p_end,
            },
        },
        upsert=True,
        return_document=True,
    )

    new_total = update_res.get(counter_field, quantity) if update_res else quantity

    # 2. Immutable usage event log
    try:
        await db.usage_records.insert_one({
            "organization_id": s_org_id,
            "user_id": str(user_id) if user_id else None,
            "metric": metric,
            "quantity": quantity,
            "period": p_str,
            "source": source or "system",
            "created_at": utcnow(),
        })
    except Exception as e:
        logger.warning(f"Failed to insert usage_record: {e}")

    return new_total


def record_usage_atomic_sync(
    organization_id: str,
    metric: str,
    quantity: int = 1,
    user_id: Optional[str] = None,
    source: Optional[str] = None,
) -> int:
    """Synchronous atomic usage counter increment for worker threads."""
    db = get_sync_db()
    if db is None or not organization_id:
        return 0

    s_org_id = str(organization_id)
    counter_field = METRIC_TO_COUNTER_FIELD.get(metric, f"{metric}_used")
    p_start, p_end, p_str = get_organization_period_sync(s_org_id)

    from pymongo import ReturnDocument
    update_res = db.organization_usage.find_one_and_update(
        {
            "organization_id": s_org_id,
            "period_start": p_start,
            "period_end": p_end,
        },
        {
            "$inc": {counter_field: quantity},
            "$set": {"updated_at": utcnow()},
            "$setOnInsert": {
                "organization_id": s_org_id,
                "period_start": p_start,
                "period_end": p_end,
            },
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )

    new_total = update_res.get(counter_field, quantity) if update_res else quantity

    try:
        db.usage_records.insert_one({
            "organization_id": s_org_id,
            "user_id": str(user_id) if user_id else None,
            "metric": metric,
            "quantity": quantity,
            "period": p_str,
            "source": source or "system",
            "created_at": utcnow(),
        })
    except Exception:
        pass

    return new_total
