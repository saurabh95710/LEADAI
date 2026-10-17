"""
LeadAI Scheduled Scans Service (Phase 6 Product Feature).

Supports:
- Recurring / scheduled social scans (daily, weekly, custom interval)
- Incremental scans (fetching only new posts and comments since last run)

Bulk URL searches are started by POST /api/search/bulk, one admitted search
per URL (app.api.routes.search.admit_url_search).
"""
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from bson import ObjectId

from app.db.models import utcnow
from app.queue.service import enqueue_job
from app.social.url_detector import detect_social_url

logger = logging.getLogger(__name__)

COLL_SCHEDULED_SCANS = "scheduled_scans"

INTERVAL_HOURS_MAP = {
    "hourly": 1,
    "daily": 24,
    "weekly": 168,
}


def create_scheduled_scan(
    db,
    *,
    organization_id: str,
    user_id: str | None,
    url: str,
    frequency: str = "daily",
    interval_hours: int | None = None,
    only_new_posts: bool = True,
    max_posts: int = 20,
    max_comments_per_post: int = 30,
    created_by: str | None = None,
) -> dict[str, Any]:
    """Create a recurring scheduled scan for an organization."""
    # 1. Validate URL
    platform, canonical_url = detect_social_url(url)

    # 2. Determine interval
    effective_interval = interval_hours or INTERVAL_HOURS_MAP.get(frequency.lower(), 24)
    now = utcnow()
    next_run = now + timedelta(hours=effective_interval)

    doc = {
        "_id": ObjectId(),
        "scan_id": f"scan_{uuid.uuid4().hex[:12]}",
        "organization_id": str(organization_id),
        "user_id": str(user_id) if user_id else None,
        "created_by": created_by,
        "url": canonical_url,
        "raw_url": url,
        "platform": platform,
        "frequency": frequency.lower(),
        "interval_hours": effective_interval,
        "only_new_posts": bool(only_new_posts),
        "max_posts": max_posts,
        "max_comments_per_post": max_comments_per_post,
        "status": "active",
        "last_run_at": None,
        "last_run_id": None,
        "next_run_at": next_run,
        "total_runs": 0,
        "created_at": now,
        "updated_at": now,
    }

    db[COLL_SCHEDULED_SCANS].insert_one(doc)
    doc["id"] = str(doc["_id"])
    logger.info("Created scheduled scan %s for org %s (interval: %dh)", doc["scan_id"], organization_id, effective_interval)
    return doc


def list_scheduled_scans(
    db,
    organization_id: str,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """List scheduled scans scoped to an organization."""
    query: dict[str, Any] = {"organization_id": str(organization_id)}
    if status:
        query["status"] = status

    scans = list(db[COLL_SCHEDULED_SCANS].find(query).sort("created_at", -1))
    for s in scans:
        s["id"] = str(s["_id"])
    return scans


def delete_scheduled_scan(db, scan_id: str, organization_id: str) -> bool:
    """Delete a scheduled scan, scoped to organization."""
    q: dict[str, Any] = {"organization_id": str(organization_id)}
    if ObjectId.is_valid(scan_id):
        q["$or"] = [{"_id": ObjectId(scan_id)}, {"scan_id": scan_id}]
    else:
        q["scan_id"] = scan_id

    res = db[COLL_SCHEDULED_SCANS].delete_one(q)
    return res.deleted_count > 0


def trigger_due_scheduled_scans(db, now: datetime | None = None) -> list[str]:
    """Execute all active scheduled scans whose next_run_at <= now."""
    current_time = now or utcnow()
    due_cursor = db[COLL_SCHEDULED_SCANS].find({
        "status": "active",
        "next_run_at": {"$lte": current_time},
    })

    triggered_run_ids = []

    for scan in due_cursor:
        scan_id = scan.get("scan_id") or str(scan["_id"])
        org_id = scan.get("organization_id")
        run_id = f"URL_REC_{uuid.uuid4().hex[:12]}"

        payload = {
            "run_id": run_id,
            "url": scan["url"],
            "max_posts": scan.get("max_posts", 20),
            "max_comments_per_post": scan.get("max_comments_per_post", 30),
            "organization_id": org_id,
            "created_by": scan.get("created_by") or "scheduled_job",
            "user_id": scan.get("user_id"),
            "only_new_posts": scan.get("only_new_posts", True),
            "scheduled_scan_id": scan_id,
        }

        # Enqueue in durable queue
        enqueue_job(
            task_name="url_search",
            payload=payload,
            run_id=run_id,
            organization_id=org_id,
            created_by=scan.get("created_by"),
            user_id=scan.get("user_id"),
        )

        interval = scan.get("interval_hours", 24)
        next_run = current_time + timedelta(hours=interval)

        db[COLL_SCHEDULED_SCANS].update_one(
            {"_id": scan["_id"]},
            {
                "$set": {
                    "last_run_at": current_time,
                    "last_run_id": run_id,
                    "next_run_at": next_run,
                    "updated_at": current_time,
                },
                "$inc": {"total_runs": 1},
            },
        )
        triggered_run_ids.append(run_id)
        logger.info("Triggered recurring scan %s -> run %s", scan_id, run_id)

    return triggered_run_ids
