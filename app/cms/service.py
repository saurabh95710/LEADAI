"""
LeadAI CMS Service

Validation, sanitisation, draft → publish workflow, version history,
public projections and caching for the public website CMS.

Security model
--------------
* Every text field is PLAIN TEXT: HTML tags are stripped server-side and the
  website renders with ``textContent`` — CMS content can never inject markup.
* URLs must be site-relative (``/path``, ``#anchor``), ``https://``/``http://``
  or ``mailto:``/``tel:`` — ``javascript:``/``data:`` are refused.
* Editor payloads are whitelisted field by field (no arbitrary ``$set``).
* Public projections (``public_*``) return only fields the website needs:
  drafts, unpublished pages, version history, contact submissions and
  internal settings never leave the server through ``/api/public``.
"""
import copy
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId
from fastapi import HTTPException

from app.admin import audit as a
from app.cms.models import (
    COLL_CONTACT, COLL_FAQ, COLL_NAVIGATION, COLL_PAGES, COLL_SETTINGS,
    COLL_TESTIMONIALS, DEFAULT_WEBSITE_SETTINGS, ICON_NAMES, SECTION_TYPES,
    SETTING_KINDS, _clean, clean_list, utcnow,
)

logger = logging.getLogger(__name__)

# ── In-memory cache for published content ───────────────────────────────────
_cache: Dict[str, Any] = {}
_CACHE_TTL = 60  # seconds


def _cache_set(key: str, value: Any) -> None:
    _cache[key] = {"data": copy.deepcopy(value), "ts": time.time()}


def _cache_get(key: str) -> Optional[Any]:
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < _CACHE_TTL:
        return copy.deepcopy(entry["data"])
    return None


def _cache_clear(prefix: str = "") -> None:
    keys = [k for k in list(_cache.keys()) if not prefix or k.startswith(prefix)]
    for k in keys:
        _cache.pop(k, None)


def clear_cache() -> None:
    """Drop every cached CMS read (tests, and after bulk edits)."""
    _cache_clear()


# ── Sanitiser / validators ──────────────────────────────────────────────────

_TAG_RE = re.compile(r"<[^>]*>")
_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1\s*>", re.DOTALL | re.IGNORECASE)
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,58}[a-z0-9]$|^[a-z0-9]$")
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_EMAIL_RE = re.compile(r"^[A-Za-z0-9_.+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?$")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _bad(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _sanitize(value: Any) -> Any:
    """Plain-text sanitiser: drops <script>/<style> blocks, every other HTML
    tag and control characters. Non-strings pass through unchanged."""
    if not isinstance(value, str):
        return value
    value = _SCRIPT_RE.sub("", value)
    value = _TAG_RE.sub("", value)
    value = _CTRL_RE.sub("", value)
    return value.strip()


def sanitize_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    return {k: _sanitize(v) if isinstance(v, str) else v for k, v in data.items()}


def clean_text(value: Any, field: str, max_len: int, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, (str, int, float)):
        raise _bad(f"{field} must be text")
    text = _sanitize(str(value))
    if required and not text:
        raise _bad(f"{field} is required")
    if len(text) > max_len:
        raise _bad(f"{field} is too long (max {max_len} characters)")
    return text


def clean_url(value: Any, field: str, *, allow_empty: bool = True) -> str:
    url = clean_text(value, field, 500)
    if not url:
        if allow_empty:
            return ""
        raise _bad(f"{field} is required")
    low = url.lower()
    if " " in url or "\\" in url:
        raise _bad(f"{field} is not a valid URL")
    if url.startswith("//"):
        raise _bad(f"{field} must be a site path (/…) or a full https:// URL")
    if url.startswith(("/", "#")) or low.startswith(("https://", "http://", "mailto:", "tel:")):
        return url
    raise _bad(f"{field} must be a site path (/…), #anchor, https://, mailto: or tel: URL")


def _as_bool(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.lower() in ("true", "false", "1", "0", "yes", "no", "on", "off"):
        return value.lower() in ("true", "1", "yes", "on")
    raise _bad(f"{field} must be true or false")


def _as_int(value: Any, field: str, lo: int = 0, hi: int = 100000) -> int:
    try:
        iv = int(value)
    except (TypeError, ValueError):
        raise _bad(f"{field} must be a whole number")
    if iv < lo or iv > hi:
        raise _bad(f"{field} must be between {lo} and {hi}")
    return iv


def _oid_query(value: str) -> Dict[str, Any]:
    return {"_id": ObjectId(value)} if ObjectId.is_valid(str(value)) else {"_id": value}


# ── Section / page validation ───────────────────────────────────────────────

_ITEM_LIMITS = {"icon": 16, "title": 160, "description": 600, "label": 80, "value": 80, "tone": 16}
_TONES = {"", "primary", "success", "warning", "danger", "info", "muted"}
MAX_SECTIONS = 30
MAX_ITEMS = 24


def _clean_cta(value: Any, field: str) -> Dict[str, str]:
    value = value or {}
    if not isinstance(value, dict):
        raise _bad(f"{field} must be an object with label and url")
    label = clean_text(value.get("label"), f"{field}.label", 60)
    url = clean_url(value.get("url"), f"{field}.url")
    if bool(label) != bool(url):
        raise _bad(f"{field} needs both a label and a url (or neither)")
    return {"label": label, "url": url}


def _clean_item(raw: Any, where: str) -> Dict[str, str]:
    if not isinstance(raw, dict):
        raise _bad(f"{where} must be an object")
    item = {k: clean_text(raw.get(k), f"{where}.{k}", n) for k, n in _ITEM_LIMITS.items()}
    if item["tone"] not in _TONES:
        raise _bad(f"{where}.tone must be one of {', '.join(sorted(t for t in _TONES if t))}")
    item["url"] = clean_url(raw.get("url"), f"{where}.url")
    return item


def clean_section(raw: Any, index: int) -> Dict[str, Any]:
    where = f"sections[{index}]"
    if not isinstance(raw, dict):
        raise _bad(f"{where} must be an object")
    stype = str(raw.get("type") or "").strip()
    if stype not in SECTION_TYPES:
        raise _bad(f"{where}.type must be one of: {', '.join(SECTION_TYPES)}")
    key = str(raw.get("key") or stype).strip().lower()
    if not _KEY_RE.match(key):
        raise _bad(f"{where}.key may use lowercase letters, digits, - and _")
    items = raw.get("items") or []
    if not isinstance(items, list):
        raise _bad(f"{where}.items must be a list")
    if len(items) > MAX_ITEMS:
        raise _bad(f"{where} can have at most {MAX_ITEMS} items")
    return {
        "key": key, "type": stype,
        "enabled": _as_bool(raw.get("enabled", True), f"{where}.enabled"),
        "eyebrow": clean_text(raw.get("eyebrow"), f"{where}.eyebrow", 80),
        "title": clean_text(raw.get("title"), f"{where}.title", 200),
        "highlight": clean_text(raw.get("highlight"), f"{where}.highlight", 120),
        "subtitle": clean_text(raw.get("subtitle"), f"{where}.subtitle", 600),
        "body": clean_text(raw.get("body"), f"{where}.body", 30000),
        "note": clean_text(raw.get("note"), f"{where}.note", 300),
        "card_title": clean_text(raw.get("card_title"), f"{where}.card_title", 120),
        "cta_primary": _clean_cta(raw.get("cta_primary"), f"{where}.cta_primary"),
        "cta_secondary": _clean_cta(raw.get("cta_secondary"), f"{where}.cta_secondary"),
        "items": [_clean_item(it, f"{where}.items[{i}]") for i, it in enumerate(items)],
    }


def clean_sections(raw: Any) -> List[Dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise _bad("sections must be a list")
    if len(raw) > MAX_SECTIONS:
        raise _bad(f"A page can have at most {MAX_SECTIONS} sections")
    out = [clean_section(s, i) for i, s in enumerate(raw)]
    keys = [s["key"] for s in out]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    if dupes:
        raise _bad(f"Duplicate section key(s): {', '.join(dupes)}")
    return out


_ROBOTS = {"index,follow", "noindex,follow", "index,nofollow", "noindex,nofollow"}


def clean_seo(raw: Any) -> Dict[str, str]:
    raw = raw or {}
    if not isinstance(raw, dict):
        raise _bad("seo must be an object")
    robots = str(raw.get("robots") or "index,follow").replace(" ", "").lower()
    if robots not in _ROBOTS:
        raise _bad(f"seo.robots must be one of: {', '.join(sorted(_ROBOTS))}")
    return {"title": clean_text(raw.get("title"), "seo.title", 120),
            "description": clean_text(raw.get("description"), "seo.description", 320),
            "og_image": clean_url(raw.get("og_image"), "seo.og_image"),
            "robots": robots}


def clean_slug(value: Any) -> str:
    slug = str(value or "").strip().lower()
    if not _SLUG_RE.match(slug):
        raise _bad("slug may use lowercase letters, digits and - (max 60 characters)")
    return slug


# ── Pages ────────────────────────────────────────────────────────────────────

def _public_page_projection(doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Only the published snapshot, only published pages."""
    if not doc or doc.get("status") != "published":
        return None
    live = doc.get("live") or {          # legacy docs (no snapshot) serve themselves
        "title": doc.get("title"), "seo": doc.get("seo"), "sections": doc.get("sections")}
    sections = [s for s in (live.get("sections") or []) if s.get("enabled", True)]
    published = doc.get("published_at")
    return _clean({
        "slug": doc.get("slug"),
        "title": live.get("title") or "",
        "seo": {k: (live.get("seo") or {}).get(k, "") for k in ("title", "description", "og_image", "robots")},
        "sections": sections,
        "published_at": published,
    })


async def get_public_page(db, slug: str) -> Optional[Dict[str, Any]]:
    slug = str(slug or "").strip().lower()
    cached = _cache_get(f"page:{slug}")
    if cached is not None:
        return cached or None
    doc = await db[COLL_PAGES].find_one({"slug": slug})
    result = _public_page_projection(doc)
    _cache_set(f"page:{slug}", result or {})
    return result


async def list_public_pages(db) -> List[Dict[str, Any]]:
    docs = [d async for d in db[COLL_PAGES].find(
        {"status": "published"}, {"slug": 1, "status": 1, "live": 1, "seo": 1,
                                  "title": 1, "published_at": 1, "updated_at": 1})]
    out = []
    for d in docs:
        p = _public_page_projection(d)
        if p:
            out.append(p)
    return sorted(out, key=lambda p: p["slug"])


async def list_pages(db, status: Optional[str] = None, q: Optional[str] = None) -> List[Dict]:
    query: Dict[str, Any] = {}
    if status:
        query["status"] = status
    if q:
        rx = {"$regex": re.escape(q.strip()), "$options": "i"}
        query["$or"] = [{"slug": rx}, {"title": rx}]
    docs = [d async for d in db[COLL_PAGES].find(query, {"versions": 0, "live": 0}).sort("slug", 1)]
    return clean_list(docs)


async def get_page(db, slug: str) -> Optional[Dict]:
    """Admin read by slug (full document incl. draft)."""
    doc = await db[COLL_PAGES].find_one({"slug": str(slug).strip().lower()})
    return _clean(doc)


async def get_page_by_id(db, page_id: str) -> Optional[Dict]:
    doc = await db[COLL_PAGES].find_one(_oid_query(page_id))
    return _clean(doc)


async def create_page(db, data: Dict[str, Any], created_by: str) -> str:
    if not isinstance(data, dict):
        raise _bad("Invalid page payload")
    slug = clean_slug(data.get("slug"))
    if await db[COLL_PAGES].find_one({"slug": slug}):
        raise HTTPException(status_code=409, detail="A page with this slug already exists")
    now = utcnow()
    doc = {
        "slug": slug,
        "title": clean_text(data.get("title"), "title", 120, required=True),
        "seo": clean_seo(data.get("seo")),
        "sections": clean_sections(data.get("sections")),
        "status": "draft",
        "has_draft_changes": True,
        "version": 1,
        "versions": [],
        "created_at": now,
        "updated_at": now,
        "published_at": None,
        "created_by": created_by,
        "updated_by": created_by,
    }
    result = await db[COLL_PAGES].insert_one(doc)
    return str(result.inserted_id)


async def update_page(db, page_id: str, data: Dict[str, Any], updated_by: str) -> bool:
    """Edit the DRAFT. The live (published) snapshot is untouched until the
    next publish, so editing never takes a page offline."""
    if not isinstance(data, dict):
        raise _bad("Invalid page payload")
    doc = await db[COLL_PAGES].find_one(_oid_query(page_id))
    if not doc:
        return False
    updates: Dict[str, Any] = {}
    if "title" in data:
        updates["title"] = clean_text(data.get("title"), "title", 120, required=True)
    if "seo" in data:
        updates["seo"] = clean_seo(data.get("seo"))
    if "sections" in data:
        updates["sections"] = clean_sections(data.get("sections"))
    if "slug" in data:
        slug = clean_slug(data.get("slug"))
        if slug != doc.get("slug"):
            if await db[COLL_PAGES].find_one({"slug": slug}):
                raise HTTPException(status_code=409, detail="A page with this slug already exists")
            updates["slug"] = slug
    unknown = set(data) - {"title", "seo", "sections", "slug", "_id", "id", "status"}
    if unknown:
        raise _bad(f"Unknown page field(s): {', '.join(sorted(unknown))}")
    if not updates:
        return True
    updates.update({"updated_at": utcnow(), "updated_by": updated_by, "has_draft_changes": True})
    await db[COLL_PAGES].update_one({"_id": doc["_id"]}, {"$set": updates})
    _cache_clear("page:")
    return True


async def publish_page(db, page_id: str, published_by: str) -> bool:
    """Copy the draft to the live snapshot, keep a version history (20)."""
    doc = await db[COLL_PAGES].find_one(_oid_query(page_id))
    if not doc:
        return False
    snapshot = {k: copy.deepcopy(v) for k, v in doc.items() if k not in ("versions", "live", "_id")}
    snapshot["snapshot_at"] = utcnow()
    snapshot["snapshot_by"] = published_by
    live = {"title": doc.get("title", ""), "seo": copy.deepcopy(doc.get("seo") or {}),
            "sections": copy.deepcopy(doc.get("sections") or [])}
    now = utcnow()
    try:
        await db[COLL_PAGES].update_one(
            {"_id": doc["_id"]},
            {
                "$set": {"status": "published", "live": live, "has_draft_changes": False,
                         "published_at": now, "updated_at": now, "updated_by": published_by,
                         "version": int(doc.get("version") or 1) + 1},
                "$push": {"versions": {"$each": [snapshot], "$slice": -20}},
            }
        )
    except Exception as e:
        logger.error("publish_page failed: %s", e)
        return False
    _cache_clear("page:")
    return True


async def unpublish_page(db, page_id: str, by: str) -> bool:
    doc = await db[COLL_PAGES].find_one(_oid_query(page_id))
    if not doc:
        return False
    await db[COLL_PAGES].update_one(
        {"_id": doc["_id"]},
        {"$set": {"status": "draft", "updated_at": utcnow(), "updated_by": by},
         "$unset": {"live": ""}})
    _cache_clear("page:")
    return True


async def restore_page_version(db, page_id: str, version_index: int, restored_by: str) -> bool:
    """Load a previous version snapshot into the draft (publish to go live)."""
    doc = await db[COLL_PAGES].find_one(_oid_query(page_id))
    if not doc:
        return False
    versions = doc.get("versions", [])
    if version_index < 0 or version_index >= len(versions):
        return False
    snap = versions[version_index]
    restore = {"title": snap.get("title", doc.get("title", "")),
               "seo": snap.get("seo") or {}, "sections": snap.get("sections") or [],
               "has_draft_changes": True, "updated_at": utcnow(), "updated_by": restored_by}
    try:
        await db[COLL_PAGES].update_one({"_id": doc["_id"]}, {"$set": restore})
    except Exception as e:
        logger.error("restore_page_version failed: %s", e)
        return False
    _cache_clear("page:")
    return True


async def delete_page(db, page_id: str) -> Optional[Dict[str, Any]]:
    doc = await db[COLL_PAGES].find_one(_oid_query(page_id))
    if not doc:
        return None
    await db[COLL_PAGES].delete_one({"_id": doc["_id"]})
    _cache_clear("page:")
    return {"slug": doc.get("slug")}


# ── FAQ ──────────────────────────────────────────────────────────────────────

def clean_faq(data: Dict[str, Any], *, partial: bool = False) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise _bad("Invalid FAQ payload")
    out: Dict[str, Any] = {}
    if not partial or "question" in data:
        out["question"] = clean_text(data.get("question"), "question", 300, required=True)
    if not partial or "answer" in data:
        out["answer"] = clean_text(data.get("answer"), "answer", 3000, required=True)
    if not partial or "category" in data:
        out["category"] = clean_text(data.get("category") or "general", "category", 40)
    if "order" in data:
        out["order"] = _as_int(data.get("order"), "order")
    if not partial or "enabled" in data:
        out["enabled"] = _as_bool(data.get("enabled", True), "enabled")
    return out


async def list_faq(db, enabled_only: bool = False, q: Optional[str] = None) -> List[Dict]:
    key = f"faq:{'public' if enabled_only else 'all'}"
    if not q:
        cached = _cache_get(key)
        if cached is not None:
            return cached
    query: Dict[str, Any] = {"enabled": True} if enabled_only else {}
    if q:
        rx = {"$regex": re.escape(q.strip()), "$options": "i"}
        query["$or"] = [{"question": rx}, {"answer": rx}, {"category": rx}]
    docs = [d async for d in db[COLL_FAQ].find(query).sort("order", 1)]
    result = clean_list(docs)
    if not q:
        _cache_set(key, result)
    return result


async def public_faq(db) -> List[Dict[str, Any]]:
    return [{"id": f.get("_id"), "question": f.get("question", ""), "answer": f.get("answer", ""),
             "category": f.get("category", "")} for f in await list_faq(db, enabled_only=True)]


async def create_faq(db, data: Dict[str, Any]) -> str:
    doc = clean_faq(data)
    if "order" not in doc:
        last = [d async for d in db[COLL_FAQ].find({}).sort("order", -1).limit(1)]
        doc["order"] = int((last[0].get("order") if last else 0) or 0) + 1
    now = utcnow()
    doc.update({"created_at": now, "updated_at": now})
    r = await db[COLL_FAQ].insert_one(doc)
    _cache_clear("faq:")
    return str(r.inserted_id)


async def update_faq(db, faq_id: str, data: Dict[str, Any]) -> bool:
    upd = clean_faq(data, partial=True)
    upd["updated_at"] = utcnow()
    r = await db[COLL_FAQ].update_one(_oid_query(faq_id), {"$set": upd})
    _cache_clear("faq:")
    return r.matched_count > 0


async def reorder_faq(db, ids: List[str]) -> int:
    if not isinstance(ids, list) or not ids or len(ids) > 500:
        raise _bad("ids must be a non-empty list")
    n = 0
    for i, fid in enumerate(ids):
        r = await db[COLL_FAQ].update_one(_oid_query(str(fid)), {"$set": {"order": i + 1, "updated_at": utcnow()}})
        n += r.matched_count
    _cache_clear("faq:")
    return n


async def delete_faq(db, faq_id: str) -> bool:
    r = await db[COLL_FAQ].delete_one(_oid_query(faq_id))
    _cache_clear("faq:")
    return r.deleted_count > 0


# ── Testimonials ─────────────────────────────────────────────────────────────

def clean_testimonial(data: Dict[str, Any], *, partial: bool = False) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise _bad("Invalid testimonial payload")
    if "text" in data and "quote" not in data:
        data = {**data, "quote": data.get("text")}
    out: Dict[str, Any] = {}
    if not partial or "quote" in data:
        out["quote"] = clean_text(data.get("quote"), "quote", 800, required=True)
    if not partial or "author_name" in data:
        out["author_name"] = clean_text(data.get("author_name"), "author_name", 100, required=True)
    for k, n in (("author_title", 120), ("company", 120)):
        if not partial or k in data:
            out[k] = clean_text(data.get(k), k, n)
    if not partial or "avatar_url" in data:
        out["avatar_url"] = clean_url(data.get("avatar_url"), "avatar_url")
    if "rating" in data and data.get("rating") not in (None, ""):
        out["rating"] = _as_int(data.get("rating"), "rating", 1, 5)
    if "order" in data:
        out["order"] = _as_int(data.get("order"), "order")
    if not partial or "enabled" in data:
        out["enabled"] = _as_bool(data.get("enabled", True), "enabled")
    return out


async def list_testimonials(db, enabled_only: bool = False) -> List[Dict]:
    key = f"testimonials:{'public' if enabled_only else 'all'}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    q: Dict[str, Any] = {"enabled": True} if enabled_only else {}
    docs = [d async for d in db[COLL_TESTIMONIALS].find(q).sort("order", 1)]
    result = clean_list(docs)
    _cache_set(key, result)
    return result


async def public_testimonials(db) -> List[Dict[str, Any]]:
    out = []
    for t in await list_testimonials(db, enabled_only=True):
        out.append({"id": t.get("_id"), "quote": t.get("quote") or t.get("text") or "",
                    "author_name": t.get("author_name", ""), "author_title": t.get("author_title", ""),
                    "company": t.get("company", ""), "avatar_url": t.get("avatar_url", ""),
                    "rating": t.get("rating")})
    return out


async def create_testimonial(db, data: Dict[str, Any]) -> str:
    doc = clean_testimonial(data)
    doc.setdefault("order", await db[COLL_TESTIMONIALS].count_documents({}) + 1)
    doc.update({"created_at": utcnow(), "updated_at": utcnow()})
    r = await db[COLL_TESTIMONIALS].insert_one(doc)
    _cache_clear("testimonials:")
    return str(r.inserted_id)


async def update_testimonial(db, tid: str, data: Dict[str, Any]) -> bool:
    upd = clean_testimonial(data, partial=True)
    upd["updated_at"] = utcnow()
    r = await db[COLL_TESTIMONIALS].update_one(_oid_query(tid), {"$set": upd})
    _cache_clear("testimonials:")
    return r.matched_count > 0


async def delete_testimonial(db, tid: str) -> bool:
    r = await db[COLL_TESTIMONIALS].delete_one(_oid_query(tid))
    _cache_clear("testimonials:")
    return r.deleted_count > 0


# ── Navigation ───────────────────────────────────────────────────────────────

NAV_LOCATIONS = ("header", "footer")


def clean_nav_location(location: str) -> str:
    if location not in NAV_LOCATIONS:
        raise HTTPException(status_code=404, detail="Unknown navigation location")
    return location


def clean_nav_items(items: Any) -> List[Dict[str, Any]]:
    if not isinstance(items, list):
        raise _bad("items must be a list")
    if len(items) > 60:
        raise _bad("A menu can have at most 60 items")
    out = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            raise _bad(f"items[{i}] must be an object")
        target = str(it.get("target") or "_self")
        if target not in ("_self", "_blank"):
            raise _bad(f"items[{i}].target must be _self or _blank")
        out.append({"label": clean_text(it.get("label"), f"items[{i}].label", 60, required=True),
                    "url": clean_url(it.get("url"), f"items[{i}].url", allow_empty=False),
                    "group": clean_text(it.get("group"), f"items[{i}].group", 40),
                    "target": target,
                    "enabled": _as_bool(it.get("enabled", True), f"items[{i}].enabled")})
    return out


async def get_navigation(db, location: str = "header", *, enabled_only: bool = True) -> List[Dict]:
    key = f"nav:{location}:{'public' if enabled_only else 'all'}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    q: Dict[str, Any] = {"location": location}
    if enabled_only:
        q["enabled"] = True
    docs = [d async for d in db[COLL_NAVIGATION].find(q).sort("order", 1)]
    result = clean_list(docs)
    _cache_set(key, result)
    return result


async def public_navigation(db, location: str) -> List[Dict[str, str]]:
    return [{"label": n.get("label", ""), "url": n.get("url", ""), "group": n.get("group", ""),
             "target": n.get("target", "_self")} for n in await get_navigation(db, location)]


async def upsert_navigation(db, location: str, items: List[Dict]) -> int:
    location = clean_nav_location(location)
    clean = clean_nav_items(items)
    await db[COLL_NAVIGATION].delete_many({"location": location})
    now = utcnow()
    docs = [{"location": location, **item, "order": i, "created_at": now}
            for i, item in enumerate(clean)]
    if docs:
        await db[COLL_NAVIGATION].insert_many(docs)
    _cache_clear("nav:")
    return len(docs)


# ── Website Settings ─────────────────────────────────────────────────────────

SETTING_KEYS = [s["key"] for s in DEFAULT_WEBSITE_SETTINGS]

# Only these keys ever leave the server through /api/public/settings
PUBLIC_SETTING_KEYS = [
    "brand_name", "tagline", "footer_tagline", "site_url", "contact_email", "contact_phone",
    "contact_address", "footer_copyright", "social_twitter", "social_linkedin",
    "social_facebook", "social_instagram", "social_youtube", "logo_url", "favicon_url",
    "og_image", "cookie_notice", "announcement_enabled", "announcement_text",
    "announcement_link_label", "announcement_link_url",
]


def clean_setting(key: str, value: Any) -> Any:
    if key not in SETTING_KEYS:
        raise _bad(f"Unknown website setting '{key}'")
    kind = SETTING_KINDS.get(key, "text")
    if kind == "bool":
        return _as_bool(value, key)
    if kind == "url":
        url = clean_url(value, key)
        if key == "site_url" and url and not url.lower().startswith(("https://", "http://")):
            raise _bad("site_url must be a full https:// URL")
        return url.rstrip("/") if key == "site_url" else url
    if kind == "email":
        email = clean_text(value, key, 200)
        if email and not _EMAIL_RE.match(email):
            raise _bad(f"{key} must be a valid email address")
        return email
    if kind == "color":
        color = clean_text(value, key, 7)
        if color and not _COLOR_RE.match(color):
            raise _bad(f"{key} must be a hex colour like #8b5cf6")
        return color
    return clean_text(value, key, 1000 if key == "cookie_notice" else 300)


def clean_settings_payload(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict) or not payload:
        raise _bad("Send at least one setting")
    return {k: clean_setting(k, v) for k, v in payload.items()}


async def get_website_settings(db) -> Dict[str, Any]:
    cached = _cache_get("website_settings")
    if cached is not None:
        return cached
    docs = [d async for d in db[COLL_SETTINGS].find({})]
    result = {d["key"]: d.get("value") for d in docs if d.get("key")}
    _cache_set("website_settings", result)
    return result


async def public_settings(db) -> Dict[str, Any]:
    s = await get_website_settings(db)
    defaults = {d["key"]: d["value"] for d in DEFAULT_WEBSITE_SETTINGS}
    out = {k: s.get(k, defaults.get(k, "")) for k in PUBLIC_SETTING_KEYS}
    out["announcement_enabled"] = bool(out.get("announcement_enabled"))
    return out


async def update_website_setting(db, key: str, value: Any, updated_by: str = "system") -> None:
    value = clean_setting(key, value)
    await db[COLL_SETTINGS].update_one(
        {"key": key},
        {"$set": {"value": value, "updated_at": utcnow(), "updated_by": updated_by}},
        upsert=True,
    )
    _cache_clear("website_settings")


# ── Contact Submissions ──────────────────────────────────────────────────────

async def create_contact_submission(db, data: Dict[str, Any], ip: str = "",
                                    user_agent: str = "") -> str:
    doc = {
        "name": _sanitize(data.get("name", "")),
        "email": _sanitize(data.get("email", "")).lower(),
        "company": _sanitize(data.get("company", "")),
        "topic": _sanitize(data.get("topic", "")),
        "message": _sanitize(data.get("message", "")),
        "ip": ip,
        "user_agent": (user_agent or "")[:300],
        "created_at": utcnow(),
        "read": False,
    }
    r = await db[COLL_CONTACT].insert_one(doc)
    return str(r.inserted_id)


async def list_contact_submissions(db, offset: int = 0, limit: int = 50, *,
                                   q: Optional[str] = None, read: Optional[bool] = None,
                                   sort: str = "-created_at") -> Tuple[List[Dict], int]:
    query: Dict[str, Any] = {}
    if read is not None:
        query["read"] = read
    if q:
        rx = {"$regex": re.escape(q.strip()[:100]), "$options": "i"}
        query["$or"] = [{"name": rx}, {"email": rx}, {"company": rx}, {"message": rx}]
    field = sort.lstrip("-")
    if field not in ("created_at", "name", "email"):
        field = "created_at"
    direction = -1 if sort.startswith("-") else 1
    total = await db[COLL_CONTACT].count_documents(query)
    docs = [d async for d in db[COLL_CONTACT].find(query).sort(field, direction).skip(offset).limit(limit)]
    return clean_list(docs), total


async def set_contact_read(db, sub_id: str, read: bool = True) -> bool:
    if not ObjectId.is_valid(str(sub_id)):
        return False
    r = await db[COLL_CONTACT].update_one({"_id": ObjectId(str(sub_id))},
                                          {"$set": {"read": bool(read), "read_at": utcnow() if read else None}})
    return r.matched_count > 0


# ── Editor schema ────────────────────────────────────────────────────────────

def editor_schema() -> Dict[str, Any]:
    """Everything a CMS editor UI needs to render forms generically."""
    return {
        "section_types": SECTION_TYPES,
        "section_fields": ["key", "type", "enabled", "eyebrow", "title", "highlight", "subtitle",
                           "body", "note", "card_title", "cta_primary", "cta_secondary", "items"],
        "item_fields": list(_ITEM_LIMITS) + ["url"],
        "item_tones": sorted(t for t in _TONES if t),
        "icons": ICON_NAMES,
        "robots": sorted(_ROBOTS),
        "navigation_locations": list(NAV_LOCATIONS),
        "settings": [{"key": s["key"], "label": s["label"], "kind": SETTING_KINDS.get(s["key"], "text")}
                     for s in DEFAULT_WEBSITE_SETTINGS],
        "body_format": "Plain text. Blank line = paragraph, '## ' = heading, '- ' = bullet.",
    }

