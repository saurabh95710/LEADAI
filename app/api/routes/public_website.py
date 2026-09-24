"""
LeadAI Public Website Routes

Read-only endpoints that serve PUBLISHED CMS content to the public website
(no authentication). Every response is an explicit whitelist projection:
drafts, unpublished pages, version history, contact submissions, internal
settings and non-public plans never leave the server here.

Also: the rate-limited contact form, sitemap.xml / robots.txt and
``render_site_page`` — server-side SEO meta for the website HTML shells.
"""
import html
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from app.admin import audit as a
from app.cms import service as svc
from app.cms.models import PAGE_PATHS
from app.db.mongo import get_async_db

router = APIRouter(prefix="/api/public", tags=["public_website"])
logger = logging.getLogger(__name__)

# Contact form throttles (in-memory sliding windows; accepted messages only)
_contact_rate: Dict[str, List[float]] = {}
CONTACT_WINDOW_SECONDS = 600          # 10 minutes
CONTACT_MAX_PER_IP = 3
CONTACT_MAX_PER_EMAIL = 3


async def _db():
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")
    return db


def _base_url(request: Request, settings: Dict[str, Any]) -> str:
    site = str(settings.get("site_url") or "").strip().rstrip("/")
    if site.lower().startswith(("https://", "http://")):
        return site
    return str(request.base_url).rstrip("/")


# ── Theme / Branding (public fallback used by design/theme.js) ──────────────

@router.get("/theme")
async def public_theme():
    """Publicly safe branding tokens (brand name, logo, favicon)."""
    db = await _db()
    s = await svc.public_settings(db)
    raw = await svc.get_website_settings(db)
    return {
        "brand_name":    s.get("brand_name") or "LeadAI",
        "tagline":       s.get("tagline", ""),
        "logo_url":      s.get("logo_url", ""),
        "favicon_url":   s.get("favicon_url", ""),
        "primary_color": raw.get("primary_color", ""),
        "accent_color":  raw.get("accent_color", ""),
    }


# ── Navigation ───────────────────────────────────────────────────────────────

@router.get("/navigation")
async def public_navigation():
    """Enabled header and footer navigation items (footer items have a ``group``)."""
    db = await _db()
    return {"header": await svc.public_navigation(db, "header"),
            "footer": await svc.public_navigation(db, "footer")}


# ── Pages ────────────────────────────────────────────────────────────────────

@router.get("/page/{slug}")
async def public_page(slug: str):
    """Published page (live snapshot: title, SEO, enabled sections).
    Draft / unpublished / unknown pages all return the same 404."""
    db = await _db()
    page = await svc.get_public_page(db, slug)
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    page["path"] = PAGE_PATHS.get(page["slug"], f"/{page['slug']}")
    return page


# ── FAQ / Testimonials ───────────────────────────────────────────────────────

@router.get("/faq")
async def public_faq():
    db = await _db()
    return {"faq": await svc.public_faq(db)}


@router.get("/testimonials")
async def public_testimonials():
    db = await _db()
    return {"testimonials": await svc.public_testimonials(db)}


# ── Pricing (single source of truth: the billing plan collection) ───────────

@router.get("/pricing")
async def public_pricing():
    """Active, public plans from the billing plan catalog (app.billing)."""
    try:
        from app.billing.plans import PLAN_LIMIT_KEYS, get_all_plans, plan_highlights
        plans = await get_all_plans(active_only=True)
    except Exception as e:
        logger.warning("public_pricing failed: %s", e)
        return {"plans": []}
    out = []
    for p in plans:
        if p.get("status") != "active" or not p.get("is_public", True):
            continue
        limits = p.get("limits") or {}
        out.append({
            "slug": p.get("slug"), "name": p.get("name"),
            "description": p.get("description", ""),
            "price_monthly": p.get("price_monthly"), "price_yearly": p.get("price_yearly"),
            "currency": p.get("currency") or "USD", "trial_days": p.get("trial_days") or 0,
            "features": list(p.get("features") or []),
            "limits": {k: limits[k] for k in PLAN_LIMIT_KEYS if k in limits},
            "highlights": plan_highlights(p),
            "popular": bool(p.get("popular", p.get("is_default", False))),
            "display_order": p.get("display_order", 0),
        })
    out.sort(key=lambda x: (x.get("display_order") or 0, x.get("price_monthly") or 0))
    return {"plans": out}


# ── Website Settings (safe subset) ───────────────────────────────────────────

@router.get("/settings")
async def public_settings():
    """Publicly safe website settings: brand, logo/favicon, contact details,
    social links, footer, cookie notice, announcement bar, default OG image."""
    db = await _db()
    return await svc.public_settings(db)


# ── Contact Form ─────────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"^[A-Za-z0-9_.+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
_TOPICS = {"", "general", "sales", "demo", "support", "partnership", "billing", "other"}


def _throttled(key: str, limit: int, now: float) -> bool:
    hits = [t for t in _contact_rate.get(key, []) if now - t < CONTACT_WINDOW_SECONDS]
    _contact_rate[key] = hits
    return len(hits) >= limit


def reset_contact_rate_limit() -> None:
    _contact_rate.clear()


@router.post("/contact")
async def submit_contact(request: Request):
    """Public contact form. Validates, rate-limits (per IP and per email,
    accepted messages only), stores the message and notifies Super Admins."""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request body")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Invalid request body")

    name = str(data.get("name") or "").strip()
    email = str(data.get("email") or "").strip().lower()
    company = str(data.get("company") or "").strip()
    topic = str(data.get("topic") or "").strip().lower()
    message = str(data.get("message") or "").strip()

    errors: Dict[str, str] = {}
    if not name or len(name) > 100:
        errors["name"] = "Name is required (max 100 characters)"
    if not email or len(email) > 200 or not _EMAIL_RE.match(email):
        errors["email"] = "A valid email address is required"
    if len(company) > 200:
        errors["company"] = "Company is too long (max 200 characters)"
    if topic not in _TOPICS:
        errors["topic"] = "Unknown topic"
    if len(message) < 10:
        errors["message"] = "Message must be at least 10 characters"
    elif len(message) > 2000:
        errors["message"] = "Message is too long (max 2000 characters)"
    if errors:
        raise HTTPException(status_code=422, detail={"message": next(iter(errors.values())),
                                                     "errors": errors})

    meta = a.request_meta(request)
    ip = meta["ip"] or "unknown"

    # Honeypot: bots fill the hidden "website" field — pretend success, store nothing.
    if str(data.get("website") or "").strip():
        return {"success": True, "message": "Thank you! We'll be in touch shortly."}

    now = time.time()
    if _throttled(f"ip:{ip}", CONTACT_MAX_PER_IP, now) or \
            _throttled(f"email:{email}", CONTACT_MAX_PER_EMAIL, now):
        raise HTTPException(status_code=429,
                            detail="Too many messages. Please wait a few minutes before sending another.")

    db = await _db()
    sub_id = await svc.create_contact_submission(
        db, {"name": name, "email": email, "company": company, "topic": topic, "message": message},
        ip=ip, user_agent=meta["user_agent"] or "")
    _contact_rate.setdefault(f"ip:{ip}", []).append(now)
    _contact_rate.setdefault(f"email:{email}", []).append(now)

    try:
        from app.events.notifications import notify_super_admins
        clean_name = svc.clean_text(name, "name", 100)
        notify_super_admins(
            "contact_message", f"New contact message from {clean_name}",
            f"{clean_name} <{email}>{' · ' + svc.clean_text(company, 'company', 200) if company else ''}: "
            f"{svc.clean_text(message, 'message', 2000)[:280]}",
            link="/admin#cms", data={"submission_id": sub_id, "email": email, "topic": topic})
    except Exception as e:  # notification failure must not lose the message
        logger.warning("contact notification failed: %s", e)
    await a.aaudit("website.contact_submitted", "cms", user={"email": email, "name": name},
                   ip=ip, user_agent=meta["user_agent"], resource_type="contact_submission",
                   resource_id=sub_id, details={"topic": topic})
    logger.info("Contact submission %s received", sub_id)
    return {"success": True, "message": "Thank you! We'll be in touch shortly.", "id": sub_id}


# ── Sitemap / robots ─────────────────────────────────────────────────────────

def _iso_date(value: Any) -> str:
    if isinstance(value, str):
        return value[:10]
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d")
    return ""


async def build_sitemap(request: Request) -> str:
    db = await _db()
    base = _base_url(request, await svc.get_website_settings(db))
    urls = []
    for page in await svc.list_public_pages(db):
        if "noindex" in (page.get("seo") or {}).get("robots", ""):
            continue
        path = PAGE_PATHS.get(page["slug"], f"/{page['slug']}")
        lastmod = _iso_date(page.get("published_at"))
        prio = "1.0" if page["slug"] == "home" else "0.8"
        urls.append("  <url><loc>{}</loc>{}<priority>{}</priority></url>".format(
            html.escape(base + path), f"<lastmod>{lastmod}</lastmod>" if lastmod else "", prio))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(urls) + "\n</urlset>\n")


async def build_robots(request: Request) -> str:
    db = await _db()
    base = _base_url(request, await svc.get_website_settings(db))
    disallow = ["/api/", "/admin", "/superadmin", "/org-admin", "/dashboard", "/billing/",
                "/invite/", "/reset-password", "/demo-pending"]
    return ("User-agent: *\n" + "".join(f"Disallow: {p}\n" for p in disallow)
            + "Allow: /\n\n" + f"Sitemap: {base}/sitemap.xml\n")


@router.get("/sitemap.xml", response_class=Response)
async def sitemap(request: Request):
    """Sitemap of PUBLISHED, indexable CMS pages only."""
    return Response(content=await build_sitemap(request), media_type="application/xml")


@router.get("/robots.txt", response_class=Response)
async def robots(request: Request):
    return Response(content=await build_robots(request), media_type="text/plain")


# ── Server-side SEO meta for the website HTML shells ────────────────────────

_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "static")
_SEO_BLOCK = re.compile(r"<!--seo:start-->.*?<!--seo:end-->", re.DOTALL)


def _meta_block(*, title: str, description: str, robots: str, url: str, image: str,
                site_name: str, favicon: str) -> str:
    e = lambda v: html.escape(str(v or ""), quote=True)  # noqa: E731
    parts = [f"<title>{e(title)}</title>",
             f'<meta name="description" content="{e(description)}"/>',
             f'<meta name="robots" content="{e(robots)}"/>',
             f'<link rel="canonical" href="{e(url)}"/>',
             '<meta property="og:type" content="website"/>',
             f'<meta property="og:site_name" content="{e(site_name)}"/>',
             f'<meta property="og:title" content="{e(title)}"/>',
             f'<meta property="og:description" content="{e(description)}"/>',
             f'<meta property="og:url" content="{e(url)}"/>',
             f'<meta name="twitter:card" content="{"summary_large_image" if image else "summary"}"/>',
             f'<meta name="twitter:title" content="{e(title)}"/>',
             f'<meta name="twitter:description" content="{e(description)}"/>']
    if image:
        parts.append(f'<meta property="og:image" content="{e(image)}"/>')
    if favicon:
        parts.append(f'<link rel="icon" href="{e(favicon)}"/>')
    return "<!--seo:start-->\n  " + "\n  ".join(parts) + "\n  <!--seo:end-->"


async def render_site_page(request: Request, filename: str, slug: str) -> Response:
    """Serve a website HTML shell with the page's CMS SEO meta rendered
    server-side (crawlers without JS get the right title/description/OG).
    An unpublished page still renders (the shell shows its own not-found
    state) but is marked noindex."""
    path = os.path.join(_STATIC_DIR, filename)
    try:
        with open(path, encoding="utf-8") as f:
            shell = f.read()
    except OSError:
        return HTMLResponse("Not found", status_code=404)
    try:
        db = get_async_db()
        if db is None:
            return HTMLResponse(shell)
        settings = await svc.public_settings(db)
        page = await svc.get_public_page(db, slug) or {}
        home = page if slug == "home" else (await svc.get_public_page(db, "home") or {})
        seo = page.get("seo") or {}
        brand = settings.get("brand_name") or "LeadAI"
        title = seo.get("title") or (f"{page['title']} · {brand}" if page.get("title") else brand)
        description = seo.get("description") or (home.get("seo") or {}).get("description") or settings.get("tagline") or ""
        robots = seo.get("robots") or ("index,follow" if page else "noindex,follow")
        base = _base_url(request, settings)
        req_path = request.url.path if request.url.path != "/" else "/website"
        image = seo.get("og_image") or settings.get("og_image") or ""
        if image.startswith("/"):
            image = base + image
        block = _meta_block(title=title, description=description, robots=robots,
                            url=base + req_path, image=image, site_name=brand,
                            favicon=settings.get("favicon_url") or "")
        shell = _SEO_BLOCK.sub(lambda _m: block, shell, count=1)
    except Exception as e:  # never fail the page because of SEO
        logger.warning("render_site_page(%s) meta failed: %s", slug, e)
    return HTMLResponse(shell, headers={"Cache-Control": "no-cache"})


# Route → (html shell, CMS page slug). main.py serves these paths through
# render_site_page (see SITE_ROUTES) so both lists stay in one place.
SITE_ROUTES: Dict[str, tuple] = {
    "/website": ("website.html", "home"),
    "/features": ("website.html", "features"),
    "/how-it-works": ("website.html", "how-it-works"),
    "/pricing": ("website.html", "pricing"),
    "/faq": ("website.html", "faq"),
    "/testimonials": ("website.html", "home"),
    "/about": ("website.html", "about"),
    "/privacy": ("website.html", "privacy"),
    "/terms": ("website.html", "terms"),
    "/cookies": ("website.html", "cookies"),
    "/contact": ("contact.html", "contact"),
}


def site_route_handler(path: str):
    """FastAPI handler factory for a SITE_ROUTES entry (used by main.py)."""
    filename, slug = SITE_ROUTES[path]

    async def _handler(request: Request):
        return await render_site_page(request, filename, slug)
    _handler.__name__ = "site_page_" + re.sub(r"[^a-z0-9]", "_", path.strip("/") or "root")
    return _handler
