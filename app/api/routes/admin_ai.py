import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.admin import audit as a
from app.auth.roles import require_manager, require_viewer, require_super
from app.db.mongo import get_async_db
from app.db.models import utcnow

router = APIRouter(prefix="/api/admin/ai", tags=["admin_ai"])
logger = logging.getLogger(__name__)


async def _db():
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return db


def _pid(prompt_id: Any):
    """Prompt/model ids are ObjectIds (insert_one); accept legacy string ids too."""
    from bson import ObjectId
    return ObjectId(prompt_id) if ObjectId.is_valid(str(prompt_id)) else prompt_id


def _ser(doc):
    from bson import ObjectId
    return {k: (str(v) if isinstance(v, ObjectId) else v) for k, v in doc.items()}


# Server-owned fields a client payload must never set: the creation time and
# who made the change (taken from the session instead). An update also never
# rewrites the document's _id; a create may choose it (e.g. "gemini-flash").
_SERVER_FIELDS = ("created_at", "created_by", "updated_by")


def _changes(payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
    fields = {k: v for k, v in payload.items() if k != "_id" and k not in _SERVER_FIELDS}
    fields.update({"updated_at": utcnow(), "updated_by": actor})
    return fields


def _new_doc(payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
    now = utcnow()
    doc = {k: v for k, v in payload.items() if k not in _SERVER_FIELDS}
    doc.update({"created_at": now, "updated_at": now, "created_by": actor, "updated_by": actor})
    return doc


# --- Prompt CRUD -----------------------------------------------------------

@router.get("/prompts", dependencies=[Depends(require_viewer)])
async def list_prompts(offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=200)):
    db = await _db()
    total = await db.ai_prompts.count_documents({})
    cursor = db.ai_prompts.find({}).skip(offset).limit(limit).sort("created_at", -1)
    items = [doc async for doc in cursor]
    return {"items": [_ser(d) for d in items], "total": total, "offset": offset, "limit": limit}

@router.get("/prompts/{prompt_id}", dependencies=[Depends(require_viewer)])
async def get_prompt(prompt_id: str):
    db = await _db()
    doc = await db.ai_prompts.find_one({"_id": _pid(prompt_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Prompt not found")
    return _ser(doc)

@router.post("/prompts", dependencies=[Depends(require_manager)])
async def create_prompt(request: Request, payload: Dict[str, Any], admin: dict = Depends(require_manager)):
    db = await _db()
    result = await db.ai_prompts.insert_one(_new_doc(payload, admin.get("email", "system")))
    await a.aaudit("prompt.create", "ai", user=admin, ip=request.client.host if request.client else None, details={"prompt_id": str(result.inserted_id)})
    return {"id": str(result.inserted_id), "status": "created"}

@router.put("/prompts/{prompt_id}", dependencies=[Depends(require_manager)])
async def update_prompt(prompt_id: str, request: Request, payload: Dict[str, Any], admin: dict = Depends(require_manager)):
    db = await _db()
    result = await db.ai_prompts.update_one({"_id": _pid(prompt_id)}, {"$set": _changes(payload, admin.get("email", "system"))})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Prompt not found")
    await a.aaudit("prompt.update", "ai", user=admin, ip=request.client.host if request.client else None, details={"prompt_id": prompt_id})
    return {"id": prompt_id, "status": "updated"}

@router.delete("/prompts/{prompt_id}", dependencies=[Depends(require_super)])
async def delete_prompt(prompt_id: str, request: Request, admin: dict = Depends(require_super)):
    db = await _db()
    result = await db.ai_prompts.delete_one({"_id": _pid(prompt_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Prompt not found")
    await a.aaudit("prompt.delete", "ai", user=admin, ip=request.client.host if request.client else None, details={"prompt_id": prompt_id})
    return {"id": prompt_id, "status": "deleted"}

# --- Model Registry CRUD ---------------------------------------------------

@router.get("/models", dependencies=[Depends(require_viewer)])
async def list_models(offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=200)):
    db = await _db()
    total = await db.ai_models.count_documents({})
    cursor = db.ai_models.find({}).skip(offset).limit(limit).sort("created_at", -1)
    items = [doc async for doc in cursor]
    return {"items": [_ser(d) for d in items], "total": total, "offset": offset, "limit": limit}

@router.get("/models/{model_id}", dependencies=[Depends(require_viewer)])
async def get_model(model_id: str):
    db = await _db()
    doc = await db.ai_models.find_one({"_id": _pid(model_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Model not found")
    return _ser(doc)

@router.post("/models", dependencies=[Depends(require_manager)])
async def create_model(request: Request, payload: Dict[str, Any], admin: dict = Depends(require_manager)):
    db = await _db()
    result = await db.ai_models.insert_one(_new_doc(payload, admin.get("email", "system")))
    await a.aaudit("model.create", "ai", user=admin, ip=request.client.host if request.client else None, details={"model_id": str(result.inserted_id)})
    return {"id": str(result.inserted_id), "status": "created"}

@router.put("/models/{model_id}", dependencies=[Depends(require_manager)])
async def update_model(model_id: str, request: Request, payload: Dict[str, Any], admin: dict = Depends(require_manager)):
    db = await _db()
    result = await db.ai_models.update_one({"_id": _pid(model_id)}, {"$set": _changes(payload, admin.get("email", "system"))})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Model not found")
    await a.aaudit("model.update", "ai", user=admin, ip=request.client.host if request.client else None, details={"model_id": model_id})
    return {"id": model_id, "status": "updated"}

@router.delete("/models/{model_id}", dependencies=[Depends(require_super)])
async def delete_model(model_id: str, request: Request, admin: dict = Depends(require_super)):
    db = await _db()
    result = await db.ai_models.delete_one({"_id": _pid(model_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Model not found")
    await a.aaudit("model.delete", "ai", user=admin, ip=request.client.host if request.client else None, details={"model_id": model_id})
    return {"id": model_id, "status": "deleted"}

# --- Playground endpoint ---------------------------------------------------

@router.post("/playground", dependencies=[Depends(require_viewer)])
async def playground(request: Request, payload: Dict[str, Any]):
    """Execute a prompt against the selected model and return the raw response.
    Expected payload keys:
        - model_id: str (required)
        - prompt_id: str (optional, for stored prompt templates)
        - variables: dict (optional, variables to render the template)
        - raw_input: str (optional, raw prompt text if not using a stored prompt)
    """
    db = await _db()
    model_id = payload.get("model_id")
    if not model_id:
        raise HTTPException(status_code=400, detail="model_id is required")
    model_doc = await db.ai_models.find_one({"_id": _pid(model_id)})
    if not model_doc:
        raise HTTPException(status_code=404, detail="Model not found")

    # Resolve prompt text
    prompt_text: Optional[str] = None
    if "prompt_id" in payload:
        prompt_doc = await db.ai_prompts.find_one({"_id": _pid(payload["prompt_id"])})
        if not prompt_doc:
            raise HTTPException(status_code=404, detail="Prompt not found")
        template = prompt_doc.get("template") or ""
        variables = payload.get("variables", {})
        try:
            prompt_text = template.format(**variables)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Prompt rendering error: {e}")
    elif "raw_input" in payload:
        prompt_text = payload["raw_input"]
    else:
        raise HTTPException(status_code=400, detail="Either prompt_id or raw_input must be provided")

    # Model execution is not implemented: app.pipeline.ai_models_service has no
    # model-calling function (this used to import a non-existent ``call_model``,
    # which raised ImportError and surfaced as an unhandled 500). Fail explicitly.
    logger.warning("Playground run for model %s (%d prompt chars) rejected: model execution is not implemented",
                   model_id, len(prompt_text or ""))
    raise HTTPException(status_code=501, detail="Model execution is not available")
