"""
LeadAI CMS Admin Routes

Super-Admin protected endpoints for managing the public website:
- Pages (CRUD, draft → publish / unpublish, restore, version history)
- FAQ (CRUD, reorder)
- Testimonials (CRUD)
- Navigation (header / footer — footer items carry a column ``group``)
- Website settings (branding, logo/favicon, contact info, social links,
  footer, announcement bar, SEO defaults)
- Contact submissions (search / filter / paginate, mark read)
- Media (upload, list, delete)
- Editor schema (section types, icons, setting kinds)

Reads need a platform ``viewer`` role, contact-message triage ``manager``,
every content write ``super_admin``. All payloads are validated in
app.cms.service and every mutation is audited.
"""
import logging
import os
import uuid
from typing import Any, Dict, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile

from app.admin import audit as a
from app.auth.roles import require_manager, require_super, require_viewer
from app.cms import service as svc
from app.cms.models import COLL_MEDIA, clean_list, utcnow
from app.db.mongo import get_async_db

router = APIRouter(prefix="/api/admin/cms", tags=["admin_cms"])
logger = logging.getLogger(__name__)

# SVG is deliberately NOT accepted: it can carry script and is served same-origin.
MEDIA_EXT_BY_MIME = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif",
    "image/x-icon": ".ico", "image/vnd.microsoft.icon": ".ico",
}
ALLOWED_MEDIA_TYPES = set(MEDIA_EXT_BY_MIME)
MEDIA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "static", "media")
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB

# Magic numbers — the declared content type must match the bytes.
_MAGIC = {
    ".jpg": (b"\xff\xd8\xff",), ".png": (b"\x89PNG\r\n\x1a\n",), ".gif": (b"GIF87a", b"GIF89a"),
    ".webp": (b"RIFF",), ".ico": (b"\x00\x00\x01\x00",),
}


async def _db():
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return db


def _admin_email(admin: dict) -> str:
    return (admin or {}).get("email", "system")


async def _audit(action: str, request: Request, admin: dict, *, resource_type: str = "",
                 resource_id: str = "", **details: Any) -> None:
    meta = a.request_meta(request)
    await a.aaudit(action, "cms", user=admin, ip=meta["ip"], user_agent=meta["user_agent"],
                   resource_type=resource_type or None, resource_id=resource_id or None,
                   details=details or None)


# ──────────────────────────────────────────────────────────────────────────────
# SCHEMA
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/schema", dependencies=[Depends(require_viewer)])
async def cms_schema():
    return svc.editor_schema()


# ──────────────────────────────────────────────────────────────────────────────
# PAGES
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/pages", dependencies=[Depends(require_viewer)])
async def list_pages(status: Optional[str] = Query(None, pattern="^(draft|published)$"),
                     q: Optional[str] = Query(None, max_length=100)):
    db = await _db()
    return {"pages": await svc.list_pages(db, status, q)}


@router.get("/pages/{page_id}", dependencies=[Depends(require_viewer)])
async def get_page(page_id: str):
    db = await _db()
    page = await svc.get_page_by_id(db, page_id) or await svc.get_page(db, page_id)
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    return page


@router.post("/pages")
async def create_page(request: Request, payload: Dict[str, Any], admin: dict = Depends(require_super)):
    db = await _db()
    page_id = await svc.create_page(db, payload, _admin_email(admin))
    await _audit("cms.page.create", request, admin, resource_type="page", resource_id=page_id,
                 slug=payload.get("slug"))
    return {"id": page_id, "status": "created"}


@router.put("/pages/{page_id}")
async def update_page(page_id: str, request: Request, payload: Dict[str, Any],
                      admin: dict = Depends(require_super)):
    db = await _db()
    ok = await svc.update_page(db, page_id, payload, _admin_email(admin))
    if not ok:
        raise HTTPException(status_code=404, detail="Page not found")
    await _audit("cms.page.update", request, admin, resource_type="page", resource_id=page_id,
                 fields=sorted(k for k in payload if k not in ("_id", "id")))
    return {"id": page_id, "status": "draft_saved"}


@router.post("/pages/{page_id}/publish")
async def publish_page(page_id: str, request: Request, admin: dict = Depends(require_super)):
    db = await _db()
    ok = await svc.publish_page(db, page_id, _admin_email(admin))
    if not ok:
        raise HTTPException(status_code=404, detail="Page not found")
    await _audit("cms.page.publish", request, admin, resource_type="page", resource_id=page_id)
    return {"id": page_id, "status": "published"}


@router.post("/pages/{page_id}/unpublish")
async def unpublish_page(page_id: str, request: Request, admin: dict = Depends(require_super)):
    db = await _db()
    ok = await svc.unpublish_page(db, page_id, _admin_email(admin))
    if not ok:
        raise HTTPException(status_code=404, detail="Page not found")
    await _audit("cms.page.unpublish", request, admin, resource_type="page", resource_id=page_id)
    return {"id": page_id, "status": "draft"}


@router.get("/pages/{page_id}/versions", dependencies=[Depends(require_viewer)])
async def page_versions(page_id: str):
    db = await _db()
    page = await svc.get_page_by_id(db, page_id)
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    versions = page.get("versions") or []
    return {"items": [{"index": i, "version": v.get("version"), "title": v.get("title"),
                       "snapshot_at": v.get("snapshot_at"), "snapshot_by": v.get("snapshot_by")}
                      for i, v in enumerate(versions)]}


@router.post("/pages/{page_id}/restore/{version_index}")
async def restore_page(page_id: str, version_index: int, request: Request,
                       admin: dict = Depends(require_super)):
    db = await _db()
    ok = await svc.restore_page_version(db, page_id, version_index, _admin_email(admin))
    if not ok:
        raise HTTPException(status_code=404, detail="Page or version not found")
    await _audit("cms.page.restore", request, admin, resource_type="page", resource_id=page_id,
                 version_index=version_index)
    return {"id": page_id, "status": "restored_to_draft", "version_index": version_index}


@router.delete("/pages/{page_id}")
async def delete_page(page_id: str, request: Request, admin: dict = Depends(require_super)):
    db = await _db()
    gone = await svc.delete_page(db, page_id)
    if not gone:
        raise HTTPException(status_code=404, detail="Page not found")
    await _audit("cms.page.delete", request, admin, resource_type="page", resource_id=page_id,
                 slug=gone.get("slug"))
    return {"id": page_id, "status": "deleted"}


# ──────────────────────────────────────────────────────────────────────────────
# FAQ
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/faq", dependencies=[Depends(require_viewer)])
async def list_faq(q: Optional[str] = Query(None, max_length=100)):
    db = await _db()
    return {"faq": await svc.list_faq(db, q=q)}


@router.post("/faq")
async def create_faq(request: Request, payload: Dict[str, Any], admin: dict = Depends(require_super)):
    db = await _db()
    faq_id = await svc.create_faq(db, payload)
    await _audit("cms.faq.create", request, admin, resource_type="faq", resource_id=faq_id)
    return {"id": faq_id, "status": "created"}


@router.post("/faq/reorder")
async def reorder_faq(request: Request, payload: Dict[str, Any], admin: dict = Depends(require_super)):
    db = await _db()
    n = await svc.reorder_faq(db, payload.get("ids"))
    await _audit("cms.faq.reorder", request, admin, resource_type="faq", count=n)
    return {"status": "reordered", "count": n}


@router.put("/faq/{faq_id}")
async def update_faq(faq_id: str, request: Request, payload: Dict[str, Any],
                     admin: dict = Depends(require_super)):
    db = await _db()
    ok = await svc.update_faq(db, faq_id, payload)
    if not ok:
        raise HTTPException(status_code=404, detail="FAQ item not found")
    await _audit("cms.faq.update", request, admin, resource_type="faq", resource_id=faq_id)
    return {"id": faq_id, "status": "updated"}


@router.delete("/faq/{faq_id}")
async def delete_faq(faq_id: str, request: Request, admin: dict = Depends(require_super)):
    db = await _db()
    ok = await svc.delete_faq(db, faq_id)
    if not ok:
        raise HTTPException(status_code=404, detail="FAQ item not found")
    await _audit("cms.faq.delete", request, admin, resource_type="faq", resource_id=faq_id)
    return {"id": faq_id, "status": "deleted"}


# ──────────────────────────────────────────────────────────────────────────────
# TESTIMONIALS
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/testimonials", dependencies=[Depends(require_viewer)])
async def list_testimonials():
    db = await _db()
    return {"testimonials": await svc.list_testimonials(db)}


@router.post("/testimonials")
async def create_testimonial(request: Request, payload: Dict[str, Any],
                             admin: dict = Depends(require_super)):
    db = await _db()
    tid = await svc.create_testimonial(db, payload)
    await _audit("cms.testimonial.create", request, admin, resource_type="testimonial", resource_id=tid)
    return {"id": tid, "status": "created"}


@router.put("/testimonials/{tid}")
async def update_testimonial(tid: str, request: Request, payload: Dict[str, Any],
                             admin: dict = Depends(require_super)):
    db = await _db()
    ok = await svc.update_testimonial(db, tid, payload)
    if not ok:
        raise HTTPException(status_code=404, detail="Testimonial not found")
    await _audit("cms.testimonial.update", request, admin, resource_type="testimonial", resource_id=tid)
    return {"id": tid, "status": "updated"}


@router.delete("/testimonials/{tid}")
async def delete_testimonial(tid: str, request: Request, admin: dict = Depends(require_super)):
    db = await _db()
    ok = await svc.delete_testimonial(db, tid)
    if not ok:
        raise HTTPException(status_code=404, detail="Testimonial not found")
    await _audit("cms.testimonial.delete", request, admin, resource_type="testimonial", resource_id=tid)
    return {"id": tid, "status": "deleted"}


# ──────────────────────────────────────────────────────────────────────────────
# NAVIGATION
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/navigation/{location}", dependencies=[Depends(require_viewer)])
async def get_navigation(location: str):
    db = await _db()
    svc.clean_nav_location(location)
    return {"location": location, "items": await svc.get_navigation(db, location, enabled_only=False)}


@router.put("/navigation/{location}")
async def update_navigation(location: str, request: Request, payload: Dict[str, Any],
                            admin: dict = Depends(require_super)):
    db = await _db()
    count = await svc.upsert_navigation(db, location, payload.get("items"))
    await _audit("cms.navigation.update", request, admin, resource_type="navigation",
                 resource_id=location, count=count)
    return {"location": location, "status": "updated", "count": count}


# ──────────────────────────────────────────────────────────────────────────────
# WEBSITE SETTINGS & BRANDING
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/settings", dependencies=[Depends(require_viewer)])
async def get_settings():
    db = await _db()
    return {"settings": await svc.get_website_settings(db),
            "fields": svc.editor_schema()["settings"]}


@router.put("/settings")
async def update_settings(request: Request, payload: Dict[str, Any], admin: dict = Depends(require_super)):
    db = await _db()
    clean = svc.clean_settings_payload(payload)      # validate everything before writing anything
    before = await svc.get_website_settings(db)
    for key, value in clean.items():
        await svc.update_website_setting(db, key, value, _admin_email(admin))
    await _audit("cms.settings.update", request, admin, resource_type="website_settings",
                 keys=sorted(clean),
                 changes={k: {"before": before.get(k), "after": v} for k, v in clean.items()
                          if before.get(k) != v})
    return {"status": "updated", "count": len(clean)}


# ──────────────────────────────────────────────────────────────────────────────
# CONTACT SUBMISSIONS
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/contact-submissions", dependencies=[Depends(require_viewer)])
async def list_contact_submissions(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                                   q: Optional[str] = Query(None, max_length=100),
                                   read: Optional[bool] = Query(None),
                                   sort: str = Query("-created_at", pattern="^-?(created_at|name|email)$")):
    db = await _db()
    items, total = await svc.list_contact_submissions(db, offset, limit, q=q, read=read, sort=sort)
    unread = await db["contact_submissions"].count_documents({"read": False})
    return {"items": items, "total": total, "unread": unread, "offset": offset, "limit": limit}


@router.patch("/contact-submissions/{sub_id}/read")
async def mark_read(sub_id: str, request: Request, admin: dict = Depends(require_manager),
                    read: bool = Query(True)):
    db = await _db()
    if not await svc.set_contact_read(db, sub_id, read):
        raise HTTPException(status_code=404, detail="Submission not found")
    await _audit("cms.contact.mark_read" if read else "cms.contact.mark_unread", request, admin,
                 resource_type="contact_submission", resource_id=sub_id)
    return {"id": sub_id, "status": "marked_read" if read else "marked_unread"}


# ──────────────────────────────────────────────────────────────────────────────
# MEDIA MANAGEMENT
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/media", dependencies=[Depends(require_viewer)])
async def list_media(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    db = await _db()
    docs = [d async for d in db[COLL_MEDIA].find({}).sort("uploaded_at", -1).skip(offset).limit(limit)]
    total = await db[COLL_MEDIA].count_documents({})
    return {"items": clean_list(docs), "total": total, "offset": offset, "limit": limit}


@router.post("/media/upload")
async def upload_media(request: Request, file: UploadFile = File(...), admin: dict = Depends(require_super)):
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 5 MB)")
    mime = (file.content_type or "").lower()
    if mime not in ALLOWED_MEDIA_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported file type (JPEG, PNG, WebP, GIF or ICO)")
    # Extension comes from the validated type, never from the uploaded filename.
    ext = MEDIA_EXT_BY_MIME[mime]
    if not content.startswith(_MAGIC[ext]):
        raise HTTPException(status_code=400, detail="File content does not match its type")
    safe_name = f"{uuid.uuid4().hex}{ext}"
    os.makedirs(MEDIA_DIR, exist_ok=True)
    with open(os.path.join(MEDIA_DIR, safe_name), "wb") as f:
        f.write(content)
    url = f"/static/media/{safe_name}"
    db = await _db()
    doc = {"filename": safe_name, "original_name": svc.clean_text(file.filename or "", "filename", 200),
           "url": url, "mime": mime, "size": len(content), "uploaded_at": utcnow(),
           "uploaded_by": _admin_email(admin)}
    r = await db[COLL_MEDIA].insert_one(doc)
    await _audit("cms.media.upload", request, admin, resource_type="media",
                 resource_id=str(r.inserted_id), filename=safe_name, size=len(content))
    return {"id": str(r.inserted_id), "url": url, "filename": safe_name, "status": "uploaded"}


@router.delete("/media/{media_id}")
async def delete_media(media_id: str, request: Request, admin: dict = Depends(require_super)):
    db = await _db()
    doc = await db[COLL_MEDIA].find_one({"_id": ObjectId(media_id)}) if ObjectId.is_valid(media_id) else None
    if not doc:
        raise HTTPException(status_code=404, detail="Media not found")
    filename = os.path.basename(doc.get("filename", ""))
    dest = os.path.join(MEDIA_DIR, filename)
    if filename and os.path.isfile(dest):
        os.remove(dest)
    await db[COLL_MEDIA].delete_one({"_id": doc["_id"]})
    await _audit("cms.media.delete", request, admin, resource_type="media", resource_id=media_id,
                 filename=filename)
    return {"id": media_id, "status": "deleted"}
