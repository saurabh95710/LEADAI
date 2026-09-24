"""
LeadAI Admin — Lead & Data Management Routes

Advanced lead management: search, dossier, notes, follow-ups,
bulk actions, data quality dashboard. All organization-scoped.
"""
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.admin import audit as a
from app.auth.roles import require_manager, require_viewer, require_super
from app.db.models import utcnow
from app.db.mongo import get_async_db

router = APIRouter(prefix="/api/admin/leads", tags=["admin_leads"])
logger = logging.getLogger(__name__)


async def _db():
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return db


def _serialize(doc: Any) -> Any:
    if isinstance(doc, ObjectId):
        return str(doc)
    if isinstance(doc, datetime):
        return doc.replace(tzinfo=timezone.utc).isoformat() if doc.tzinfo is None else doc.isoformat()
    if isinstance(doc, dict):
        return {k: _serialize(v) for k, v in doc.items()}
    if isinstance(doc, list):
        return [_serialize(v) for v in doc]
    return doc


VALID_STATUSES = ["new", "contacted", "qualified", "follow_up", "converted", "lost", "disqualified", "archived"]


# ── Lead Search ──────────────────────────────────────────────────────────────

@router.get("/search", dependencies=[Depends(require_viewer)])
async def search_leads(
    q: Optional[str] = Query(None, max_length=200),
    status: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    intent: Optional[str] = Query(None),
    min_score: Optional[float] = Query(None, ge=0, le=100),
    max_score: Optional[float] = Query(None, ge=0, le=100),
    is_lead: Optional[bool] = Query(None),
    has_contact: Optional[bool] = Query(None),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    sort_by: str = Query("analyzed_at", enum=["analyzed_at", "lead_score", "commenter_name"]),
    sort_dir: int = Query(-1, enum=[-1, 1]),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
):
    db = await _db()
    query: Dict[str, Any] = {}

    if q:
        needle = re.escape(q)
        query["$or"] = [
            {"commenter_name": {"$regex": needle, "$options": "i"}},
            {"comment_text": {"$regex": needle, "$options": "i"}},
            {"phone": {"$regex": needle}},
            {"email": {"$regex": needle, "$options": "i"}},
            {"whatsapp": {"$regex": needle}},
        ]
    if status:
        query["lifecycle_status"] = status
    if platform:
        query["platform"] = platform
    if intent:
        query["intent"] = intent
    if is_lead is not None:
        query["is_lead"] = is_lead
    if has_contact is True:
        query["$or"] = query.get("$or", []) + [
            {"phone": {"$regex": r"\S"}},
            {"email": {"$regex": r"\S"}},
            {"whatsapp": {"$regex": r"\S"}},
        ]
    score_q: Dict[str, Any] = {}
    if min_score is not None:
        score_q["$gte"] = min_score
    if max_score is not None:
        score_q["$lte"] = max_score
    if score_q:
        query["lead_score"] = score_q

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
            query["analyzed_at"] = date_q

    rows = [doc async for doc in db.ai_comments.find(query).sort(sort_by, sort_dir).skip(offset).limit(limit)]
    total = await db.ai_comments.count_documents(query)
    return {"items": [_serialize(r) for r in rows], "total": total, "offset": offset, "limit": limit}


# ── Lead Dossier ─────────────────────────────────────────────────────────────

@router.get("/{lead_id}/dossier", dependencies=[Depends(require_viewer)])
async def lead_dossier(lead_id: str):
    """Full lead profile: comment, source post, notes, follow-ups, activity."""
    db = await _db()
    try:
        doc = await db.ai_comments.find_one({"_id": ObjectId(lead_id)})
    except Exception:
        doc = await db.ai_comments.find_one({"_id": lead_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Lead not found")

    # Fetch source comment
    source_comment = None
    if doc.get("comment_ref"):
        try:
            source_comment = await db.facebook_comments.find_one({"_id": ObjectId(doc["comment_ref"])})
        except Exception:
            pass

    # Fetch source post
    source_post = None
    if source_comment and source_comment.get("post_ref"):
        try:
            source_post = await db.facebook_posts.find_one({"_id": ObjectId(source_comment["post_ref"])})
        except Exception:
            pass

    return _serialize({
        "lead": doc,
        "source_comment": source_comment,
        "source_post": source_post,
    })


# ── Notes ────────────────────────────────────────────────────────────────────

@router.get("/{lead_id}/notes", dependencies=[Depends(require_viewer)])
async def list_notes(lead_id: str):
    db = await _db()
    try:
        doc = await db.ai_comments.find_one({"_id": ObjectId(lead_id)}, {"notes": 1})
    except Exception:
        doc = await db.ai_comments.find_one({"_id": lead_id}, {"notes": 1})
    if not doc:
        raise HTTPException(status_code=404, detail="Lead not found")
    return {"notes": _serialize(doc.get("notes") or [])}


@router.post("/{lead_id}/notes", dependencies=[Depends(require_manager)])
async def add_note(lead_id: str, request: Request, payload: Dict[str, Any], admin: dict = Depends(require_manager)):
    text = str(payload.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail="Note text is required")
    if len(text) > 2000:
        raise HTTPException(status_code=400, detail="Note too long (max 2000 characters)")
    note = {"id": str(ObjectId()), "text": text, "created_at": utcnow(), "created_by": admin.get("email", "system")}
    try:
        r = await db_push_note(lead_id, note)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to save note")
    await a.aaudit("lead.note.add", "leads", user=admin, ip=request.client.host if request.client else None, details={"lead_id": lead_id})
    return {"note": _serialize(note)}


async def db_push_note(lead_id: str, note: dict):
    db = get_async_db()
    try:
        return await db.ai_comments.update_one({"_id": ObjectId(lead_id)}, {"$push": {"notes": note}})
    except Exception:
        return await db.ai_comments.update_one({"_id": lead_id}, {"$push": {"notes": note}})


@router.delete("/{lead_id}/notes/{note_id}", dependencies=[Depends(require_manager)])
async def delete_note(lead_id: str, note_id: str, request: Request, admin: dict = Depends(require_manager)):
    db = await _db()
    try:
        await db.ai_comments.update_one({"_id": ObjectId(lead_id)}, {"$pull": {"notes": {"id": note_id}}})
    except Exception:
        await db.ai_comments.update_one({"_id": lead_id}, {"$pull": {"notes": {"id": note_id}}})
    await a.aaudit("lead.note.delete", "leads", user=admin, ip=request.client.host if request.client else None, details={"lead_id": lead_id, "note_id": note_id})
    return {"status": "deleted"}


# ── Follow-ups ───────────────────────────────────────────────────────────────

@router.get("/{lead_id}/follow-ups", dependencies=[Depends(require_viewer)])
async def list_follow_ups(lead_id: str):
    db = await _db()
    try:
        doc = await db.ai_comments.find_one({"_id": ObjectId(lead_id)}, {"follow_ups": 1})
    except Exception:
        doc = await db.ai_comments.find_one({"_id": lead_id}, {"follow_ups": 1})
    if not doc:
        raise HTTPException(status_code=404, detail="Lead not found")
    return {"follow_ups": _serialize(doc.get("follow_ups") or [])}


@router.post("/{lead_id}/follow-ups", dependencies=[Depends(require_manager)])
async def add_follow_up(lead_id: str, request: Request, payload: Dict[str, Any], admin: dict = Depends(require_manager)):
    due_str = str(payload.get("due_at", "")).strip()
    if not due_str:
        raise HTTPException(status_code=400, detail="due_at is required")
    try:
        due_at = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid due_at date format (use ISO 8601)")
    fu = {
        "id": str(ObjectId()),
        "note": str(payload.get("note", "")).strip()[:500],
        "due_at": due_at,
        "status": "pending",
        "created_at": utcnow(),
        "created_by": admin.get("email", "system"),
    }
    db = await _db()
    try:
        await db.ai_comments.update_one({"_id": ObjectId(lead_id)}, {"$push": {"follow_ups": fu}})
    except Exception:
        await db.ai_comments.update_one({"_id": lead_id}, {"$push": {"follow_ups": fu}})
    await a.aaudit("lead.followup.add", "leads", user=admin, ip=request.client.host if request.client else None, details={"lead_id": lead_id})
    return {"follow_up": _serialize(fu)}


@router.patch("/{lead_id}/follow-ups/{fu_id}", dependencies=[Depends(require_manager)])
async def update_follow_up_status(lead_id: str, fu_id: str, payload: Dict[str, Any], admin: dict = Depends(require_manager)):
    status = str(payload.get("status", "")).strip()
    if status not in ("pending", "done", "cancelled"):
        raise HTTPException(status_code=400, detail="Invalid status. Must be: pending, done, cancelled")
    db = await _db()
    try:
        await db.ai_comments.update_one(
            {"_id": ObjectId(lead_id), "follow_ups.id": fu_id},
            {"$set": {"follow_ups.$.status": status, "follow_ups.$.updated_at": utcnow()}}
        )
    except Exception:
        pass
    return {"status": "updated"}


# ── Bulk Actions ─────────────────────────────────────────────────────────────

@router.post("/bulk-action", dependencies=[Depends(require_manager)])
async def bulk_action(request: Request, payload: Dict[str, Any], admin: dict = Depends(require_manager)):
    """Perform a bulk action on multiple leads.
    Actions: change_status, add_tag, archive, delete (super_admin only)
    """
    action = str(payload.get("action", "")).strip()
    lead_ids = payload.get("lead_ids", [])

    if not lead_ids or not isinstance(lead_ids, list):
        raise HTTPException(status_code=400, detail="lead_ids is required and must be a list")
    if len(lead_ids) > 500:
        raise HTTPException(status_code=400, detail="Maximum 500 leads per bulk action")
    if not action:
        raise HTTPException(status_code=400, detail="action is required")

    # Convert IDs
    try:
        oid_list = [ObjectId(lid) for lid in lead_ids]
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid lead ID format")

    db = await _db()
    updated = 0

    if action == "change_status":
        new_status = str(payload.get("value", "")).strip()
        if new_status not in VALID_STATUSES:
            raise HTTPException(status_code=400, detail=f"Invalid status. Valid: {VALID_STATUSES}")
        r = await db.ai_comments.update_many(
            {"_id": {"$in": oid_list}},
            {"$set": {"lifecycle_status": new_status, "updated_at": utcnow()}}
        )
        updated = r.modified_count

    elif action == "add_tag":
        tag = str(payload.get("value", "")).strip()[:50]
        if not tag:
            raise HTTPException(status_code=400, detail="tag value is required")
        r = await db.ai_comments.update_many(
            {"_id": {"$in": oid_list}},
            {"$addToSet": {"tags": tag}, "$set": {"updated_at": utcnow()}}
        )
        updated = r.modified_count

    elif action == "archive":
        r = await db.ai_comments.update_many(
            {"_id": {"$in": oid_list}},
            {"$set": {"lifecycle_status": "archived", "updated_at": utcnow()}}
        )
        updated = r.modified_count

    elif action == "delete":
        if admin.get("role") != "super_admin":
            raise HTTPException(status_code=403, detail="Only super_admin can delete leads in bulk")
        r = await db.ai_comments.delete_many({"_id": {"$in": oid_list}})
        updated = r.deleted_count

    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")

    await a.aaudit(f"leads.bulk.{action}", "leads", user=admin, ip=request.client.host if request.client else None, details={"action": action, "count": updated, "lead_ids": lead_ids[:10]})
    return {"success": True, "action": action, "updated": updated}


# ── Data Quality ─────────────────────────────────────────────────────────────

@router.get("/data-quality", dependencies=[Depends(require_viewer)])
async def data_quality():
    """Lead data completeness and quality metrics."""
    db = await _db()
    total = await db.ai_comments.count_documents({"is_lead": True})
    if total == 0:
        return {"total_leads": 0, "quality": {}}

    has_email   = await db.ai_comments.count_documents({"is_lead": True, "email":    {"$regex": r"\S"}})
    has_phone   = await db.ai_comments.count_documents({"is_lead": True, "phone":    {"$regex": r"\S"}})
    has_whatsapp= await db.ai_comments.count_documents({"is_lead": True, "whatsapp": {"$regex": r"\S"}})
    has_score   = await db.ai_comments.count_documents({"is_lead": True, "lead_score": {"$exists": True, "$ne": None}})
    has_status  = await db.ai_comments.count_documents({"is_lead": True, "lifecycle_status": {"$exists": True, "$ne": None}})
    has_any_contact = await db.ai_comments.count_documents({
        "is_lead": True,
        "$or": [{"phone": {"$regex": r"\S"}}, {"email": {"$regex": r"\S"}}, {"whatsapp": {"$regex": r"\S"}}]
    })

    def pct(n):
        return round(n / total * 100, 1) if total else 0

    return {
        "total_leads": total,
        "quality": {
            "has_email":       {"count": has_email,       "pct": pct(has_email)},
            "has_phone":       {"count": has_phone,       "pct": pct(has_phone)},
            "has_whatsapp":    {"count": has_whatsapp,    "pct": pct(has_whatsapp)},
            "has_any_contact": {"count": has_any_contact, "pct": pct(has_any_contact)},
            "has_score":       {"count": has_score,       "pct": pct(has_score)},
            "has_status":      {"count": has_status,      "pct": pct(has_status)},
        },
    }
