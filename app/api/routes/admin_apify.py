"""
LeadAI Admin — Apify Control Center Routes

Endpoints for managing Apify actors and monitoring job execution.
Protected by role-based authentication (viewer/manager/super_admin).
Implements max 3 retry policy for failed jobs.
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.admin import audit as a
from app.admin import settings as s
from app.auth.roles import require_manager, require_super, require_viewer
from app.db.models import utcnow
from app.db.mongo import get_async_db

router = APIRouter(prefix="/api/admin/apify", tags=["admin_apify"])
logger = logging.getLogger(__name__)
MAX_RETRIES = 3


async def _db():
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return db


def _serialize(doc: Any) -> Any:
    from bson import ObjectId
    if isinstance(doc, ObjectId):
        return str(doc)
    if isinstance(doc, datetime):
        return doc.replace(tzinfo=timezone.utc).isoformat() if doc.tzinfo is None else doc.isoformat()
    if isinstance(doc, dict):
        return {k: _serialize(v) for k, v in doc.items()}
    if isinstance(doc, list):
        return [_serialize(v) for v in doc]
    return doc


# ── Actor Registry ─────────────────────────────────────────────────────────

@router.get("/actors", dependencies=[Depends(require_viewer)])
async def list_actors():
    """List all configured Apify actors from admin settings."""
    token_hint = s.get_apify_token_hint()
    # Read actor IDs from settings
    actors = []
    platforms = s.PLATFORMS if hasattr(s, "PLATFORMS") else ["facebook", "instagram", "youtube", "linkedin"]
    for platform in platforms:
        actor_id = await s.aget_setting(f"apify.actor.{platform}")
        actors.append({
            "platform": platform,
            "actor_id": actor_id or "",
            "configured": bool(actor_id),
        })
    return {
        "actors": actors,
        "token_configured": bool(s.get_apify_token()),
        "token_hint": token_hint,
    }


@router.put("/actors/{platform}", dependencies=[Depends(require_manager)])
async def update_actor(platform: str, request: Request, payload: Dict[str, Any], admin: dict = Depends(require_manager)):
    """Update the Apify actor ID for a platform."""
    actor_id = str(payload.get("actor_id", "")).strip()
    if not actor_id:
        raise HTTPException(status_code=400, detail="actor_id is required")
    await s.aset_setting(f"apify.actor.{platform}", actor_id, updated_by=admin.get("email", "system"))
    await a.aaudit("apify.actor.update", "apify", user=admin, ip=request.client.host if request.client else None, details={"platform": platform, "actor_id": actor_id})
    return {"platform": platform, "actor_id": actor_id, "status": "updated"}


@router.post("/test-connection", dependencies=[Depends(require_manager)])
async def test_apify_connection(request: Request, admin: dict = Depends(require_manager)):
    """Test the Apify API connection using the configured token."""
    token = s.get_apify_token()
    if not token:
        raise HTTPException(status_code=400, detail="Apify token is not configured")
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.apify.com/v2/users/me",
                headers={"Authorization": f"Bearer {token}"},
            )
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            await s.aset_setting("apify.last_test_ok", utcnow().isoformat())
            await a.aaudit("apify.connection.test", "apify", user=admin, ip=request.client.host if request.client else None, details={"status": "ok"})
            return {"success": True, "username": data.get("username"), "plan": data.get("plan", {}).get("id")}
        else:
            return {"success": False, "status_code": resp.status_code, "detail": "Token rejected by Apify API"}
    except Exception as e:
        logger.warning("Apify connection test failed: %s", e)
        return {"success": False, "detail": "Connection test failed"}


# ── Jobs ───────────────────────────────────────────────────────────────────

@router.get("/jobs", dependencies=[Depends(require_viewer)])
async def list_jobs(
    status: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
):
    db = await _db()
    query: Dict[str, Any] = {}
    if status:
        query["status"] = status
    if platform:
        query["platform"] = platform
    if q:
        query["$or"] = [
            {"query": {"$regex": re.escape(q), "$options": "i"}},
            {"run_id": {"$regex": re.escape(q), "$options": "i"}},
        ]
    if from_date or to_date:
        date_q: Dict[str, Any] = {}
        if from_date:
            try:
                date_q["$gte"] = datetime.fromisoformat(from_date.replace("Z", "+00:00"))
            except Exception:
                pass
        if to_date:
            try:
                date_q["$lte"] = datetime.fromisoformat(to_date.replace("Z", "+00:00"))
            except Exception:
                pass
        if date_q:
            query["created_at"] = date_q

    rows = [doc async for doc in db.search_history.find(query).sort("created_at", -1).skip(offset).limit(limit)]
    total = await db.search_history.count_documents(query)
    return {"items": [_serialize(r) for r in rows], "total": total, "offset": offset, "limit": limit}


@router.get("/jobs/{run_id}", dependencies=[Depends(require_viewer)])
async def job_detail(run_id: str):
    db = await _db()
    doc = await db.search_history.find_one({"run_id": run_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Job not found")
    return _serialize(doc)


@router.get("/failed-jobs", dependencies=[Depends(require_viewer)])
async def failed_jobs(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
    days: int = Query(7, ge=1, le=90),
):
    """List failed jobs within the last N days, including retry count."""
    db = await _db()
    since = datetime.now(timezone.utc) - timedelta(days=days)
    query: Dict[str, Any] = {
        "status": "error",
        "$expr": {"$gte": [{"$convert": {"input": "$created_at", "to": "date", "onError": None, "onNull": None}}, since]},
    }
    rows = [doc async for doc in db.search_history.find(query).sort("created_at", -1).skip(offset).limit(limit)]
    total = await db.search_history.count_documents(query)
    return {"items": [_serialize(r) for r in rows], "total": total, "days": days}


@router.post("/jobs/{run_id}/retry", dependencies=[Depends(require_manager)])
async def retry_job(run_id: str, request: Request, admin: dict = Depends(require_manager)):
    """Retry a failed Apify job. Maximum 3 retries allowed."""
    db = await _db()
    doc = await db.search_history.find_one({"run_id": run_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Job not found")
    if doc.get("status") not in ("error", "cancelled"):
        raise HTTPException(status_code=400, detail="Only failed or cancelled jobs can be retried")

    # Check retry count
    retry_count = doc.get("retry_count", 0)
    if retry_count >= MAX_RETRIES:
        raise HTTPException(status_code=400, detail=f"Maximum retry limit ({MAX_RETRIES}) reached for this job")

    if not doc.get("url_search"):
        raise HTTPException(status_code=400, detail="Only URL-based jobs can be retried")

    intent = doc.get("intent") or {}
    url = intent.get("canonical_url") or doc.get("query")
    if not url:
        raise HTTPException(status_code=400, detail="Job has no URL to retry")

    from app.social.url_search import UrlSearchThread
    from app.api.routes.search import _start, new_url_run_id

    lim = s.effective_limits()
    max_posts = min(int(intent.get("limit") or lim["max_posts_default"]), lim["max_posts_cap"])
    max_comments = min(int(intent.get("max_comments_per_post") or lim["max_comments_per_post_default"]), lim["max_comments_per_post_cap"])

    new_run_id = new_url_run_id()
    new_doc = {
        "run_id": new_run_id, "query": url, "intent": {**intent, "limit": max_posts, "max_comments_per_post": max_comments},
        "limit": max_posts, "provider": "apify", "url_search": True, "status": "running", "phase": "queued",
        "message": f"Retried from admin (attempt {retry_count + 1}/{MAX_RETRIES})",
        "retry_count": retry_count + 1, "retried_from": run_id,
        "pages_found": 0, "pages_stored": 0, "created_at": utcnow(), "completed_at": None,
    }
    # the retried job belongs to the ORIGINAL owner/tenant, not the admin
    for key in ("organization_id", "user_id", "created_by"):
        if doc.get(key):
            new_doc[key] = doc[key]
    await db.search_history.insert_one(new_doc)
    _start(f"url_search:{new_run_id}", UrlSearchThread(
        new_run_id, url, max_posts, max_comments,
        organization_id=new_doc.get("organization_id"),
        created_by=new_doc.get("created_by"),
        user_id=new_doc.get("user_id")).run)
    await a.aaudit("apify.job.retry", "apify", user=admin, ip=request.client.host if request.client else None, details={"run_id": run_id, "new_run_id": new_run_id, "attempt": retry_count + 1})
    return {"run_id": new_run_id, "status": "running", "retried_from": run_id, "attempt": retry_count + 1}


@router.post("/jobs/{run_id}/cancel", dependencies=[Depends(require_manager)])
async def cancel_job(run_id: str, request: Request, admin: dict = Depends(require_manager)):
    from app.api.routes.search import mark_cancel_requested
    db = await _db()
    run = await db.search_history.find_one({"run_id": run_id})
    if not run:
        raise HTTPException(status_code=404, detail="Job not found")
    if run.get("status") != "running":
        raise HTTPException(status_code=400, detail="Job is not running")
    await mark_cancel_requested(run_id)
    await a.aaudit("apify.job.cancel", "apify", user=admin, ip=request.client.host if request.client else None, details={"run_id": run_id})
    return {"success": True, "message": "Cancellation requested"}


# ── Stats ──────────────────────────────────────────────────────────────────

@router.get("/stats", dependencies=[Depends(require_viewer)])
async def apify_stats(days: int = Query(30, ge=1, le=365)):
    """Overall Apify usage statistics for the given time window."""
    db = await _db()
    since = datetime.now(timezone.utc) - timedelta(days=days)
    pipeline = [
        {"$match": {"$expr": {"$gte": [{"$convert": {"input": "$created_at", "to": "date", "onError": None, "onNull": None}}, since]}}},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]
    status_counts: Dict[str, int] = {}
    async for doc in db.search_history.aggregate(pipeline):
        status_counts[doc["_id"] or "unknown"] = doc["count"]
    total = sum(status_counts.values())
    completed = status_counts.get("completed", 0)
    return {
        "days": days,
        "total_jobs": total,
        "completed": completed,
        "failed": status_counts.get("error", 0),
        "running": status_counts.get("running", 0),
        "cancelled": status_counts.get("cancelled", 0),
        "success_rate": round(completed / total * 100, 1) if total else None,
        "status_breakdown": status_counts,
    }
