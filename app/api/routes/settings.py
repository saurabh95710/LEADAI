"""Global Settings API.

Admin endpoints under ``/api/admin/settings`` (role-protected) manage the
whole non-secret settings surface with validation from the schema registry,
versioned revisions in ``settings_history`` and full audit coverage.

The public endpoint ``GET /api/public/config`` serves only safe values
(app name, branding, colors, maintenance status, public feature flags) to
every page so branding applies at runtime without a session.

Secrets policy: the Apify token and other secret/infra keys are not
registered here and can never be read or written through this API.
"""
import logging
import os
import re
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from app.admin import audit as a
from app.admin import settings as s
from app.auth.roles import require_manager, require_viewer
from app.db.mongo import get_async_db
from app.settings import registry as R

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/settings", tags=["settings"])
public_router = APIRouter(prefix="/api/public", tags=["public"])

_UPLOAD_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "static", "uploads", "branding")
_ALLOWED_EXTS = {"png", "jpg", "jpeg", "svg", "webp", "ico"}
_MAX_UPLOAD_BYTES = 2 * 1024 * 1024
_JS_DANGER_RE = re.compile(rb"<\s*script|on[a-z]+\s*=|javascript\s*:", re.IGNORECASE)

_MAGIC = {
    "png": b"\x89PNG\r\n\x1a\n",
    "jpg": b"\xff\xd8\xff",
    "jpeg": b"\xff\xd8\xff",
    "ico": b"\x00\x00\x01\x00",
}


async def _db():
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return db


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


# ── Read ────────────────────────────────────────────────────────────────────

@router.get("", dependencies=[Depends(require_viewer)])
async def get_settings():
    db = await _db()
    stored = {}
    async for doc in db[s.COLLECTION].find(
            {"_id": {"$in": list(R.REGISTERED_KEYS)}},
            {"value": 1, "updated_at": 1}):
        stored[doc["_id"]] = doc
    values = {}
    meta = {}
    for key in R.REGISTERED_KEYS:
        doc = stored.get(key)
        values[key] = doc["value"] if doc else R.SPEC_BY_KEY[key].default
        meta[key] = {"updated_at": doc.get("updated_at") if doc else None,
                     "is_default": doc is None}
    return {
        "categories": R.schema_categories(),
        "settings": values,
        "meta": meta,
        "version": await s.current_revision(),
    }


# ── Update / reset ──────────────────────────────────────────────────────────

@router.put("", dependencies=[Depends(require_manager)])
async def put_settings(body: Dict[str, Any], request: Request,
                       admin: dict = Depends(require_manager)):
    raw = body.get("values")
    if not isinstance(raw, dict) or not raw:
        raise HTTPException(status_code=400, detail="'values' must be a non-empty object")
    reason = str(body.get("reason") or "").strip()[:300]
    clean, errors = R.validate_patch(raw)
    if errors:
        sample = "; ".join(f"{k}: {v}" for k, v in list(errors.items())[:3])
        raise HTTPException(
            status_code=400,
            detail={"message": f"Invalid values ({len(errors)} error(s)): {sample}",
                    "errors": errors})
    changed = {}
    for key, new_value in clean.items():
        old_value = await s.aget_setting(key)
        if old_value != new_value:
            await s.aset_setting(key, new_value, by=admin.get("email", "admin"))
            changed[key] = {"old": old_value, "new": new_value}
    version = 0
    if changed:
        version = await s.push_revision(
            await s.snapshot_all(), changed,
            by=admin.get("email", "admin"), ip=_client_ip(request),
            reason=reason or "settings update")
        await a.aaudit("settings.update", "settings", user=admin,
                       ip=_client_ip(request),
                       details={"changed": list(changed), "version": version})
    return {"success": True, "changed": list(changed), "version": version}


@router.post("/reset", dependencies=[Depends(require_manager)])
async def reset_settings(body: Dict[str, Any], request: Request,
                         admin: dict = Depends(require_manager)):
    """Delete overrides so env defaults apply again. Scope: a section, an
    explicit key list, or everything (all=true)."""
    section = str(body.get("section") or "").strip()
    keys = body.get("keys")
    all_keys = bool(body.get("all"))
    reason = str(body.get("reason") or "").strip()[:300]
    target: List[str] = []
    if all_keys:
        target = sorted(R.REGISTERED_KEYS)
    elif section:
        if not any(cat["id"] == section for cat in R.CATEGORIES):
            raise HTTPException(status_code=400, detail=f"Unknown section: {section}")
        target = [spec.key for spec in R.SETTINGS if spec.category == section]
    elif isinstance(keys, list) and keys:
        unknown = [k for k in keys if not R.is_registered(k)]
        if unknown:
            raise HTTPException(status_code=400,
                                detail=f"Unknown keys: {', '.join(unknown)}")
        target = list(keys)
    else:
        raise HTTPException(status_code=400,
                            detail="Provide 'section', 'keys' or all=true")
    changed = {}
    for key in target:
        old_value = await s.aget_setting(key)
        await s.adelete_setting(key)
        new_value = await s.aget_setting(key)
        if old_value != new_value:
            changed[key] = {"old": old_value, "new": new_value}
    version = 0
    if changed:
        version = await s.push_revision(
            await s.snapshot_all(), changed,
            by=admin.get("email", "admin"), ip=_client_ip(request),
            reason=reason or f"reset {section or 'custom'}")
        await a.aaudit("settings.reset", "settings", user=admin,
                       ip=_client_ip(request),
                       details={"reset": list(changed), "version": version})
    return {"success": True, "reset": list(changed), "version": version}


# ── History / revisions ─────────────────────────────────────────────────────

@router.get("/history", dependencies=[Depends(require_viewer)])
async def history(limit: int = Query(50, ge=1, le=200)):
    entries = await s.list_history(limit)
    return {"versions": [
        {k: v for k, v in e.items() if k not in ("_id", "snapshot")}
        for e in entries
    ]}


@router.get("/history/{version}", dependencies=[Depends(require_viewer)])
async def history_entry(version: int):
    doc = await s.get_history_version(version)
    if not doc:
        raise HTTPException(status_code=404, detail="Revision not found")
    doc["_id"] = str(doc["_id"])
    return {"entry": doc}


@router.post("/history/{version}/restore", dependencies=[Depends(require_manager)])
async def restore(version: int, body: Dict[str, Any], request: Request,
                  admin: dict = Depends(require_manager)):
    doc = await s.get_history_version(version)
    if not doc or not isinstance(doc.get("snapshot"), dict):
        raise HTTPException(status_code=404, detail="Revision not found")
    reason = str(body.get("reason") or "").strip()[:300] or f"restore v{version}"
    new_version, applied = await s.apply_snapshot(
        doc["snapshot"], by=admin.get("email", "admin"),
        ip=_client_ip(request), reason=reason)
    await a.aaudit("settings.restore", "settings", user=admin,
                   ip=_client_ip(request),
                   details={"from_version": version, "to_version": new_version,
                            "applied": len(applied)})
    return {"success": True, "applied": applied, "version": new_version}


# ── Export / import ─────────────────────────────────────────────────────────

@router.get("/export", dependencies=[Depends(require_viewer)])
async def export_settings(download: int = Query(0)):
    payload = s.export_payload()
    payload["version"] = await s.current_revision()
    if download:
        return JSONResponse(
            payload,
            headers={"Content-Disposition":
                     'attachment; filename="settings-export.json"'})
    return payload


@router.post("/import/preview", dependencies=[Depends(require_viewer)])
async def import_preview(body: Dict[str, Any]):
    payload = body.get("payload")
    if not isinstance(payload, dict) or not isinstance(
            payload.get("settings"), dict):
        raise HTTPException(status_code=400,
                            detail="payload.settings must be an object")
    valid_keys, invalid = [], {}
    for key, value in payload["settings"].items():
        spec = R.SPEC_BY_KEY.get(key)
        if spec is None:
            invalid[key] = "Unknown setting key"
            continue
        _, error = R.validate_value(spec, value)
        if error:
            invalid[key] = error
        else:
            valid_keys.append(key)
    return {"valid": not invalid, "count": len(valid_keys),
            "invalid": invalid, "valid_keys": valid_keys}


@router.post("/import", dependencies=[Depends(require_manager)])
async def import_settings(body: Dict[str, Any], request: Request,
                          admin: dict = Depends(require_manager)):
    payload = body.get("payload")
    if not isinstance(payload, dict) or not isinstance(
            payload.get("settings"), dict):
        raise HTTPException(status_code=400,
                            detail="payload.settings must be an object")
    reason = str(body.get("reason") or "").strip()[:300] or "settings import"
    applied, skipped = [], {}
    changed = {}
    for key, new_value in payload["settings"].items():
        spec = R.SPEC_BY_KEY.get(key)
        if spec is None:
            skipped[key] = "Unknown setting key"
            continue
        coerced, error = R.validate_value(spec, new_value)
        if error:
            skipped[key] = error
            continue
        old_value = await s.aget_setting(key)
        if old_value != coerced:
            await s.aset_setting(key, coerced, by=admin.get("email", "admin"))
            changed[key] = {"old": old_value, "new": coerced}
            applied.append(key)
    version = 0
    if changed:
        version = await s.push_revision(
            await s.snapshot_all(), changed,
            by=admin.get("email", "admin"), ip=_client_ip(request),
            reason=reason)
        await a.aaudit("settings.import", "settings", user=admin,
                       ip=_client_ip(request),
                       details={"applied": len(applied),
                                "skipped": len(skipped), "version": version})
    return {"success": True, "applied": applied, "skipped": skipped,
            "version": version}


# ── Branding file uploads ───────────────────────────────────────────────────

@router.post("/upload", dependencies=[Depends(require_manager)])
async def upload_branding(request: Request, admin: dict = Depends(require_manager),
                          file: UploadFile = File(...)):
    name = (file.filename or "").lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    if ext not in _ALLOWED_EXTS:
        raise HTTPException(status_code=400,
                            detail=f"Unsupported file type '.{ext}' — allowed: "
                                   "png, jpg, jpeg, svg, webp, ico")
    content = await file.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400,
                            detail="File too large (max 2 MB)")
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Empty file")
    magic = _MAGIC.get(ext)
    if ext == "webp":
        if not (content[:4] == b"RIFF" and content[8:12] == b"WEBP"):
            raise HTTPException(status_code=400, detail="Not a valid WEBP image")
    elif magic:
        if not content.startswith(magic):
            raise HTTPException(status_code=400,
                                detail=f"Content does not match .{ext}")
    else:  # svg
        head = content.lstrip()[:200]
        if not head.lstrip().startswith(b"<"):
            raise HTTPException(status_code=400, detail="Not a valid SVG file")
        if _JS_DANGER_RE.search(content):
            raise HTTPException(status_code=400,
                                detail="SVG contains scripts — rejected for safety")
    os.makedirs(_UPLOAD_DIR, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.{ext}"
    with open(os.path.join(_UPLOAD_DIR, filename), "wb") as fh:
        fh.write(content)
    await a.aaudit("settings.upload", "settings", user=admin,
                   ip=_client_ip(request),
                   details={"file": filename, "kind": ext})
    return {"success": True, "url": f"/static/uploads/branding/{filename}"}


# ── Public config (no session required) ─────────────────────────────────────

@public_router.get("/config")
async def public_config():
    return await R.build_public_config(s.aget_setting)
