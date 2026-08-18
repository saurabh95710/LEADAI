"""
Comment Scraping & Keyword Intelligence — admin + user API.

Admin (prefix /api/admin/comment-filters, role-protected):
  GET    /rules                list rules + active state + catalogs
  POST   /rules                create a rule (manager+)
  GET    /rules/{id}           one rule
  PUT    /rules/{id}           update a rule (manager+)
  DELETE /rules/{id}           delete a rule (manager+; deactivates if active)
  POST   /rules/{id}/activate   make this the single active rule (manager+)
  POST   /rules/{id}/deactivate remove the active flag (manager+)
  POST   /rules/{id}/reapply    re-run the filter over existing comments
  GET    /stats                filter pipeline statistics
  GET    /comments             unified filtered-comment browser
  GET    /categories           custom categories
  POST   /categories           create a custom category (manager+)
  PUT    /categories/{id}      update a custom category (manager+)
  DELETE /categories/{id}      delete a custom category (manager+)

User app:
  GET    /api/comment-filters/catalog   categories + presets (URL search form)

Rules store include/exclude keywords, category selections, match mode and
advanced keyword groups. At most ONE rule is active at a time; the active
rule applies to every new scrape unless the run carries its own explicit
configuration (see app/pipeline/comment_filter.resolve_effective_rule).
"""
import logging
import re
import time
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.admin import audit as a
from app.auth.roles import require_manager, require_viewer
from app.db.mongo import get_async_db
from app.db.models import utcnow
from app.pipeline import comment_filter as cf

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/comment-filters", tags=["comment-filters"])


# ── shared helpers ───────────────────────────────────────────────────────────

async def _db():
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return db


def _oid(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid id: {value}")


def _serialize(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    return value


def _rule_view(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Stored rule doc → API view (ObjectIds stringified, timestamps iso)."""
    view = _serialize(doc)
    for key in ("created_at", "updated_at"):
        value = view.get(key)
        view[key] = value.isoformat() if hasattr(value, "isoformat") else value
    if "id" not in view and isinstance(view.get("_id"), str):
        view["id"] = view.pop("_id")
    return view


def _catalog() -> Dict[str, Any]:
    """Built-in category + preset catalogs for the UI (keywords included)."""
    return {
        "categories": [
            {"key": key, "name": meta["name"], "icon": meta.get("icon", ""),
             "description": meta.get("description", ""),
             "keywords": meta.get("keywords", [])}
            for key, meta in cf.CATEGORIES.items()
        ],
        "presets": [
            {"key": key, "name": meta["name"], "icon": meta.get("icon", ""),
             "description": meta.get("description", ""),
             "keywords": meta.get("keywords", [])}
            for key, meta in cf.PRESETS.items()
        ],
        "multilingual": cf.MULTILINGUAL_KEYWORDS,
        "match_modes": cf.MATCH_MODES,
    }


async def _validate_categories(keys: List[str], db) -> None:
    """Reject unknown category keys (built-in, preset or custom)."""
    valid = set(cf.CATEGORY_KEYS) | set(cf.PRESET_KEYS)
    try:
        async for doc in db[cf.CATEGORIES_COLLECTION].find({}, {"_id": 1, "key": 1}):
            valid.add(str(doc["_id"]))
            if doc.get("key"):
                valid.add(str(doc["key"]))
    except Exception:
        pass
    unknown = [k for k in keys if k not in valid]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown categories: {', '.join(unknown)}")


async def _custom_categories(db) -> List[Dict[str, Any]]:
    try:
        return [doc async for doc in
                db[cf.CATEGORIES_COLLECTION].find({"active": {"$ne": False}})]
    except Exception:
        return []


# ── public catalog (user app URL-search form) ────────────────────────────────

@router.get("/catalog")
async def catalog():
    db = await _db()
    return {**_catalog(),
            "custom_categories": _serialize(await _custom_categories(db))}


# ── rules ────────────────────────────────────────────────────────────────────

@router.get("/rules", dependencies=[Depends(require_viewer)])
async def list_rules(platform: Optional[str] = Query(None)):
    db = await _db()
    query: Dict[str, Any] = {}
    if platform and isinstance(platform, str):
        query["platform"] = platform
    rules = [doc async for doc in
             db[cf.RULES_COLLECTION].find(query).sort("created_at", -1)]
    active_doc = await db[cf.RULES_COLLECTION].find_one({"active": True})
    return {
        "rules": [_rule_view(r) for r in rules],
        "active_rule_id": str(active_doc["_id"]) if active_doc else None,
        "active_rule": _rule_view(active_doc) if active_doc else None,
        "catalog": {**_catalog(),
                    "custom_categories": _serialize(
                        await _custom_categories(db))},
    }


@router.post("/rules", dependencies=[Depends(require_manager)])
async def create_rule(body: Dict[str, Any], request: Request,
                      admin: dict = Depends(require_manager)):
    db = await _db()
    payload = cf.normalize_rule_payload(body)
    await _validate_categories(payload["categories"], db)
    doc = {
        **payload,
        "active": False,
        "created_at": utcnow(),
        "updated_at": utcnow(),
        "created_by": admin.get("email", ""),
    }
    result = await db[cf.RULES_COLLECTION].insert_one(doc)
    await a.aaudit("comment_filter.rule.create", "comment_filters", user=admin,
                   ip=request.client.host if request.client else None,
                   details={"rule_id": str(result.inserted_id),
                            "name": payload["name"]})
    return {"success": True,
            "rule": _rule_view({**doc, "_id": result.inserted_id})}


@router.get("/rules/{rule_id}", dependencies=[Depends(require_viewer)])
async def get_rule(rule_id: str):
    db = await _db()
    doc = await db[cf.RULES_COLLECTION].find_one({"_id": _oid(rule_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {"rule": _rule_view(doc)}


@router.put("/rules/{rule_id}", dependencies=[Depends(require_manager)])
async def update_rule(rule_id: str, body: Dict[str, Any], request: Request,
                      admin: dict = Depends(require_manager)):
    db = await _db()
    oid = _oid(rule_id)
    existing = await db[cf.RULES_COLLECTION].find_one({"_id": oid})
    if not existing:
        raise HTTPException(status_code=404, detail="Rule not found")
    payload = cf.normalize_rule_payload(body)
    await _validate_categories(payload["categories"], db)
    await db[cf.RULES_COLLECTION].update_one(
        {"_id": oid},
        {"$set": {**payload, "updated_at": utcnow(),
                  "updated_by": admin.get("email", "")}})
    await a.aaudit("comment_filter.rule.update", "comment_filters", user=admin,
                   ip=request.client.host if request.client else None,
                   details={"rule_id": rule_id, "name": payload["name"]})
    doc = await db[cf.RULES_COLLECTION].find_one({"_id": oid})
    return {"success": True, "rule": _rule_view(doc)}


@router.delete("/rules/{rule_id}", dependencies=[Depends(require_manager)])
async def delete_rule(rule_id: str, request: Request,
                      admin: dict = Depends(require_manager)):
    db = await _db()
    oid = _oid(rule_id)
    doc = await db[cf.RULES_COLLECTION].find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Rule not found")
    was_active = bool(doc.get("active"))
    await db[cf.RULES_COLLECTION].delete_one({"_id": oid})
    # if the deleted rule was active, NO rule is active now → NO_FILTER
    # (all comments processed), which is the safe default
    await a.aaudit("comment_filter.rule.delete", "comment_filters", user=admin,
                   ip=request.client.host if request.client else None,
                   details={"rule_id": rule_id, "name": doc.get("name"),
                            "was_active": was_active})
    return {"success": True, "was_active": was_active,
            "message": ("Rule deleted; no filter is now active"
                        if was_active else "Rule deleted")}


@router.post("/rules/{rule_id}/activate", dependencies=[Depends(require_manager)])
async def activate_rule(rule_id: str, request: Request,
                        admin: dict = Depends(require_manager)):
    db = await _db()
    oid = _oid(rule_id)
    doc = await db[cf.RULES_COLLECTION].find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Rule not found")
    # exactly one active rule at a time: clear the flag everywhere first
    await db[cf.RULES_COLLECTION].update_many(
        {"active": True}, {"$set": {"active": False, "updated_at": utcnow()}})
    await db[cf.RULES_COLLECTION].update_one(
        {"_id": oid}, {"$set": {"active": True, "updated_at": utcnow()}})
    await a.aaudit("comment_filter.rule.activate", "comment_filters", user=admin,
                   ip=request.client.host if request.client else None,
                   details={"rule_id": rule_id, "name": doc.get("name")})
    return {"success": True, "active_rule_id": rule_id,
            "message": f"Rule '{doc.get('name')}' is now active for all new scrapes"}


@router.post("/rules/{rule_id}/deactivate", dependencies=[Depends(require_manager)])
async def deactivate_rule(rule_id: str, request: Request,
                          admin: dict = Depends(require_manager)):
    db = await _db()
    oid = _oid(rule_id)
    doc = await db[cf.RULES_COLLECTION].find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Rule not found")
    await db[cf.RULES_COLLECTION].update_one(
        {"_id": oid}, {"$set": {"active": False, "updated_at": utcnow()}})
    await a.aaudit("comment_filter.rule.deactivate", "comment_filters",
                   user=admin,
                   ip=request.client.host if request.client else None,
                   details={"rule_id": rule_id, "name": doc.get("name")})
    return {"success": True, "active_rule_id": None,
            "message": "No filter active — every comment is processed (NO_FILTER)"}


@router.post("/rules/{rule_id}/reapply", dependencies=[Depends(require_manager)])
async def reapply_rule(rule_id: str, request: Request,
                       admin: dict = Depends(require_manager),
                       platform: Optional[str] = Query(None),
                       post_id: Optional[str] = Query(None),
                       run_id: Optional[str] = Query(None),
                       limit: int = Query(2000, ge=1, le=20000)):
    """Re-run one rule over EXISTING stored comments (backfill). Comments are
    never deleted — every one gets a fresh filter status. ai_comments are NOT
    rewritten here; matched comments get analyzed on their next natural pass."""
    db = await _db()
    oid = _oid(rule_id)
    rule = await db[cf.RULES_COLLECTION].find_one({"_id": oid})
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    query: Dict[str, Any] = {}
    if platform and isinstance(platform, str):
        query["platform"] = platform
    if post_id and isinstance(post_id, str):
        query["post_ref"] = post_id
    if run_id and isinstance(run_id, str):
        query["search_run_id"] = run_id

    custom = await _custom_categories(db)
    comments = [doc async for doc in
                db.facebook_comments.find(query, {"_id": 1, "text": 1,
                                                  "post_ref": 1,
                                                  "search_run_id": 1,
                                                  "platform": 1})
                .limit(limit)]

    summary = {"rule_id": rule_id, "name": rule.get("name", ""),
               "processed": 0, "matched": 0, "not_matched": 0,
               "no_filter": 0, "started_at": time.time(),
               "processed_at": None}

    async def write_result(comment: Dict[str, Any], result: Dict[str, Any]):
        await db[cf.RESULTS_COLLECTION].update_one(
            {"comment_id": str(comment["_id"])},
            {"$set": {
                "comment_id": str(comment["_id"]),
                "rule_id": str(rule["_id"]),
                "status": result["status"],
                "matched_keywords": result.get("matched_keywords") or [],
                "matched_categories": result.get("matched_categories") or [],
                "excluded_keywords": result.get("excluded_keywords") or [],
                "filter_score": result.get("filter_score", 0),
                "processed_at": time.time(),
                "post_ref": comment.get("post_ref"),
                "search_run_id": comment.get("search_run_id"),
                "platform": comment.get("platform"),
            }},
            upsert=True)
        await db.facebook_comments.update_one(
            {"_id": comment["_id"]},
            {"$set": {
                "keyword_filter_status": result["status"],
                "matched_keywords": result.get("matched_keywords") or [],
                "matched_categories": result.get("matched_categories") or [],
                "excluded_keywords": result.get("excluded_keywords") or [],
                "filter_score": result.get("filter_score", 0),
                "filter_rule_id": str(rule["_id"]),
                "filter_timestamp": result.get("filter_timestamp"),
            }})

    for comment in comments:
        result = cf.evaluate_rule(comment.get("text") or "", rule, custom)
        summary["processed"] += 1
        if result["status"] == cf.STATUS_MATCHED:
            summary["matched"] += 1
        elif result["status"] == cf.STATUS_NOT_MATCHED:
            summary["not_matched"] += 1
        else:
            summary["no_filter"] += 1
        await write_result(comment, result)

    summary["processed_at"] = time.time()
    await a.aaudit("comment_filter.rule.reapply", "comment_filters", user=admin,
                   ip=request.client.host if request.client else None,
                   details={"rule_id": rule_id,
                            **{k: summary[k] for k in
                               ("processed", "matched", "not_matched",
                                "no_filter")}})
    return {"success": True, "summary": summary}


# ── custom categories ────────────────────────────────────────────────────────

@router.get("/categories", dependencies=[Depends(require_viewer)])
async def list_categories():
    db = await _db()
    docs = [doc async for doc in
            db[cf.CATEGORIES_COLLECTION].find({}).sort("created_at", -1)]
    return {"categories": _serialize(docs),
            "builtin": _catalog()["categories"]}


@router.post("/categories", dependencies=[Depends(require_manager)])
async def create_category(body: Dict[str, Any], request: Request,
                          admin: dict = Depends(require_manager)):
    db = await _db()
    name = str(body.get("name") or "").strip()[:120]
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    keywords = cf.normalize_keyword_list(body.get("keywords"))
    doc = {
        "name": name,
        "description": str(body.get("description") or "").strip()[:500],
        "icon": str(body.get("icon") or "").strip()[:4],
        "keywords": keywords,
        "active": True,
        "created_at": utcnow(),
        "updated_at": utcnow(),
        "created_by": admin.get("email", ""),
    }
    try:
        result = await db[cf.CATEGORIES_COLLECTION].insert_one(doc)
    except Exception as e:
        if "E11000" in str(e):
            raise HTTPException(status_code=400,
                                detail="A category with this name already exists")
        raise
    await a.aaudit("comment_filter.category.create", "comment_filters",
                   user=admin,
                   ip=request.client.host if request.client else None,
                   details={"category_id": str(result.inserted_id), "name": name})
    return {"success": True,
            "category": _serialize({**doc, "_id": result.inserted_id})}


@router.put("/categories/{category_id}", dependencies=[Depends(require_manager)])
async def update_category(category_id: str, body: Dict[str, Any],
                          request: Request,
                          admin: dict = Depends(require_manager)):
    db = await _db()
    oid = _oid(category_id)
    existing = await db[cf.CATEGORIES_COLLECTION].find_one({"_id": oid})
    if not existing:
        raise HTTPException(status_code=404, detail="Category not found")
    update: Dict[str, Any] = {"updated_at": utcnow()}
    if "name" in body:
        name = str(body.get("name") or "").strip()[:120]
        if not name:
            raise HTTPException(status_code=400, detail="Name is required")
        update["name"] = name
    if "description" in body:
        update["description"] = str(body.get("description") or "").strip()[:500]
    if "icon" in body:
        update["icon"] = str(body.get("icon") or "").strip()[:4]
    if "keywords" in body:
        update["keywords"] = cf.normalize_keyword_list(body.get("keywords"))
    if "active" in body:
        update["active"] = bool(body.get("active"))
    await db[cf.CATEGORIES_COLLECTION].update_one({"_id": oid}, {"$set": update})
    await a.aaudit("comment_filter.category.update", "comment_filters",
                   user=admin,
                   ip=request.client.host if request.client else None,
                   details={"category_id": category_id})
    doc = await db[cf.CATEGORIES_COLLECTION].find_one({"_id": oid})
    return {"success": True, "category": _serialize(doc)}


@router.delete("/categories/{category_id}", dependencies=[Depends(require_manager)])
async def delete_category(category_id: str, request: Request,
                          admin: dict = Depends(require_manager)):
    db = await _db()
    oid = _oid(category_id)
    doc = await db[cf.CATEGORIES_COLLECTION].find_one({"_id": oid})
    if not doc:
        raise HTTPException(status_code=404, detail="Category not found")
    await db[cf.CATEGORIES_COLLECTION].delete_one({"_id": oid})
    await a.aaudit("comment_filter.category.delete", "comment_filters",
                   user=admin,
                   ip=request.client.host if request.client else None,
                   details={"category_id": category_id, "name": doc.get("name")})
    return {"success": True, "message": "Category deleted"}


# ── stats + filtered-comment browser ─────────────────────────────────────────

@router.get("/stats", dependencies=[Depends(require_viewer)])
async def filter_stats():
    db = await _db()
    total_comments = await db.facebook_comments.count_documents({})
    filtered_comments = await db.facebook_comments.count_documents(
        {"keyword_filter_status": {"$in": [
            cf.STATUS_MATCHED, cf.STATUS_NOT_MATCHED, cf.STATUS_NO_FILTER]}})
    matched = await db.facebook_comments.count_documents(
        {"keyword_filter_status": cf.STATUS_MATCHED})
    not_matched = await db.facebook_comments.count_documents(
        {"keyword_filter_status": cf.STATUS_NOT_MATCHED})
    no_filter = await db.facebook_comments.count_documents(
        {"keyword_filter_status": cf.STATUS_NO_FILTER})

    per_rule: List[Dict[str, Any]] = []
    async for doc in db[cf.RESULTS_COLLECTION].aggregate([
        {"$group": {"_id": "$rule_id", "count": {"$sum": 1},
                    "matched": {"$sum": {"$cond": [
                        {"$eq": ["$status", cf.STATUS_MATCHED]}, 1, 0]}},
                    "not_matched": {"$sum": {"$cond": [
                        {"$eq": ["$status", cf.STATUS_NOT_MATCHED]}, 1, 0]}}}},
        {"$sort": {"count": -1}}, {"$limit": 10},
    ]):
        rule_id = doc["_id"]
        active = False
        name = str(rule_id or "unknown")
        try:
            if rule_id and not str(rule_id).startswith(("preset:", "inline:")):
                r = await db[cf.RULES_COLLECTION].find_one(
                    {"_id": ObjectId(rule_id)}, {"name": 1, "active": 1})
                if r:
                    name = r.get("name") or name
                    active = bool(r.get("active"))
        except Exception:
            pass
        per_rule.append({
            "rule_id": str(rule_id) if rule_id else None,
            "name": name,
            "count": doc.get("count", 0),
            "matched": doc.get("matched", 0),
            "not_matched": doc.get("not_matched", 0),
            "active": active,
        })

    top_keywords: List[Dict[str, Any]] = []
    async for doc in db[cf.RESULTS_COLLECTION].aggregate([
        {"$match": {"status": cf.STATUS_MATCHED}},
        {"$unwind": "$matched_keywords"},
        {"$group": {"_id": "$matched_keywords", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}}, {"$limit": 10},
    ]):
        top_keywords.append({"keyword": doc["_id"], "count": doc.get("count", 0)})

    # AI calls avoided: comments skipped by the filter never reach the AI
    # stage. Cost figures are NOT fabricated — only counts are reported.
    ai_saved = {
        "comment_count": not_matched,
        "est_gemini_calls": not_matched,
        "cost_saved": None,
        "note": "Estimated only as call counts; no cost figures are fabricated.",
    }

    return {
        "totals": {
            "comments": total_comments,
            "filtered": filtered_comments,
            "matched": matched,
            "not_matched": not_matched,
            "no_filter": no_filter,
            "coverage_pct": round(filtered_comments / total_comments * 100, 1)
            if total_comments else None,
            "match_pct": round(matched / filtered_comments * 100, 1)
            if filtered_comments else None,
        },
        "per_rule": per_rule,
        "top_keywords": top_keywords,
        "ai_saved": ai_saved,
    }


@router.get("/comments", dependencies=[Depends(require_viewer)])
async def filtered_comments(
    status: Optional[str] = Query(None,
                                  description="MATCHED|NOT_MATCHED|NO_FILTER|all"),
    rule_id: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    post_id: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    matched_keyword: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    contact: bool = Query(False),
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=200),
):
    """Unified browser over filtered comments: every stored comment carries
    a keyword_filter_status + matched keywords; AI fields are attached when
    an analysis exists. NOT_MATCHED comments are shown here too — filtering
    never deletes comments."""
    db = await _db()
    query: Dict[str, Any] = {}
    if status and status != "all":
        if status not in (cf.STATUS_MATCHED, cf.STATUS_NOT_MATCHED,
                          cf.STATUS_NO_FILTER):
            raise HTTPException(
                status_code=400,
                detail="status must be MATCHED, NOT_MATCHED, NO_FILTER or all")
        query["keyword_filter_status"] = status
    if rule_id and isinstance(rule_id, str):
        query["filter_rule_id"] = rule_id
    if platform and isinstance(platform, str):
        query["platform"] = platform
    if post_id and isinstance(post_id, str):
        query["post_ref"] = post_id
    if matched_keyword and isinstance(matched_keyword, str):
        query["matched_keywords"] = matched_keyword
    if category and isinstance(category, str):
        query["matched_categories"] = category
    if contact:
        query["has_contact"] = True
    if q and isinstance(q, str):
        query["text"] = {"$regex": re.escape(q), "$options": "i"}

    offset = max(0, offset)
    limit = min(max(1, limit), 200)
    rows = [doc async for doc in
            db.facebook_comments.find(query)
            .sort("filter_timestamp", -1)
            .skip(offset).limit(limit)]

    items = []
    for raw in rows:
        item = _serialize(raw)
        item["commenter_name"] = raw.get("author_name") or "Unknown"
        item["comment_text"] = raw.get("text") or ""
        analysis = await db.ai_comments.find_one(
            {"comment_ref": str(raw["_id"])})
        if analysis:
            for key in ("is_lead", "lead_score", "priority", "lead_quality",
                        "confidence", "intent", "phone", "email", "whatsapp",
                        "budget", "requirement", "location", "analyzed_by"):
                if analysis.get(key) is not None:
                    item[key] = analysis[key]
        items.append(item)

    totals: Dict[str, int] = {}
    for s in (cf.STATUS_MATCHED, cf.STATUS_NOT_MATCHED, cf.STATUS_NO_FILTER):
        totals[s] = await db.facebook_comments.count_documents(
            {"keyword_filter_status": s})
    totals["all"] = await db.facebook_comments.count_documents(
        {"keyword_filter_status": {"$exists": True}})

    return {
        "items": items,
        "total": await db.facebook_comments.count_documents(query),
        "offset": offset, "limit": limit,
        "totals": totals,
    }