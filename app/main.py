import logging
import os
from contextlib import asynccontextmanager
from urllib.parse import urlparse
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from app.db.mongo import ensure_indexes
from app.api.routes.search import router as search_router
from app.api.routes.auth import router as auth_router
from app.api.routes.admin import router as admin_router
from app.api.routes.comment_filters import router as comment_filters_router
from app.api.routes.settings import (
    public_router as public_config_router,
    router as settings_router,
)
from app.api.routes.organizations import router as organizations_router
from app.api.routes.billing import router as billing_router
from app.auth.service import session_user
from app.config import get_settings
from app.admin import settings as admin_settings
from app.api.routes.admin_ai import router as admin_ai_router
from app.api.routes.admin_cms import router as admin_cms_router
from app.api.routes.admin_apify import router as admin_apify_router
from app.api.routes.admin_leads import router as admin_leads_router
from app.api.routes.admin_analytics import router as admin_analytics_router
from app.api.routes.public_website import router as public_website_router
from app.api.routes.super_admin import router as super_admin_router
from app.api.routes.super_admin_lifecycle import router as super_admin_lifecycle_router
from app.api.routes.notifications import router as notifications_router
from app.api.routes.org_admin import router as org_admin_router
from app.api.routes.super_admin_platform import router as super_admin_platform_router
from app.api.routes.me import router as me_router

logger = logging.getLogger(__name__)
settings = get_settings()


def _setup_logging():
    """Console (INFO) + file (DEBUG) so every Apify request/response,
    classification and agent step is traceable end-to-end.

    Configurable via environment variables:
      LOG_LEVEL      — root logger level (default: DEBUG)
      LOG_FILE_LEVEL — file handler level (default: DEBUG)
      LOG_CONSOLE_LEVEL — console handler level (default: INFO)
    """
    root = logging.getLogger()
    if root.handlers:
        return

    root_level = os.environ.get("LOG_LEVEL", "DEBUG").upper()
    file_level = os.environ.get("LOG_FILE_LEVEL", "DEBUG").upper()
    console_level = os.environ.get("LOG_CONSOLE_LEVEL", "INFO").upper()

    root.setLevel(getattr(logging, root_level, logging.DEBUG))
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
    console = logging.StreamHandler()
    console.setLevel(getattr(logging, console_level, logging.INFO))
    console.setFormatter(fmt)
    root.addHandler(console)
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
    os.makedirs(log_dir, exist_ok=True)
    from logging.handlers import RotatingFileHandler
    log_file = RotatingFileHandler(
        os.path.join(log_dir, "app.log"), encoding="utf-8",
        maxBytes=10 * 1024 * 1024, backupCount=5)
    log_file.setLevel(getattr(logging, file_level, logging.DEBUG))
    log_file.setFormatter(fmt)
    root.addHandler(log_file)
    # uvicorn's own access logs stay on console only
    logging.getLogger("uvicorn.access").propagate = False
    # mongo driver chatter is not useful at DEBUG level — force to WARNING
    # to prevent log flood from pymongo.topology / pymongo.connection
    for noisy in ("pymongo", "pymongo.topology", "pymongo.connection",
                  "pymongo.monitoring", "motor", "urllib3", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_logging()
    # The permanent Super Admin (SUPERADMIN_EMAIL / SUPERADMIN_PASSWORD) is the
    # only credential the deployment needs; report its state (never the value).
    from app.auth.superadmin import validate_superadmin_config
    validate_superadmin_config()
    if not settings.apify_api_token:
        logger.warning(
            "APIFY_API_TOKEN is not set in .env — searches will fail with an error. "
            "Get a free token at https://apify.com/account/integrations"
        )
    ensure_indexes()
    # Ensure multi-tenant migration and Default Organization backfill
    try:
        from app.db.migration import migrate_to_multi_tenant
        from app.db.mongo import get_sync_db
        sdb = get_sync_db()
        if sdb is not None:
            migrate_to_multi_tenant(sdb)
    except Exception as e:
        logger.warning("Failed to run multi-tenant migration on startup: %s", e)

    # Seed default SaaS subscription plans (Master Prompt 2)
    try:
        from app.billing.plans import ensure_default_plans
        from app.db.mongo import get_async_db
        adb = get_async_db()
        if adb is not None:
            await ensure_default_plans(adb)
    except Exception as e:
        logger.warning("Failed to seed default plans on startup: %s", e)

    # Seed CMS defaults (pages, FAQ, website settings)
    try:
        from app.cms.models import ensure_cms_indexes, seed_cms_defaults
        from app.db.mongo import get_async_db
        adb = get_async_db()
        if adb is not None:
            await ensure_cms_indexes(adb)
            await seed_cms_defaults(adb)
    except Exception as e:
        logger.warning("Failed to seed CMS defaults on startup: %s", e)

    # Reconcile stale running jobs from previous interruptions/restarts
    try:
        from app.db.mongo import get_sync_db
        from app.db.models import utcnow
        sdb = get_sync_db()
        if sdb is not None:
            res = sdb.search_history.update_many(
                {"status": "running"},
                {"$set": {"status": "cancelled", "phase": "cancelled", "message": "Interrupted (server restart)", "completed_at": utcnow()}}
            )
            if res.modified_count:
                logger.info("Cleaned up %d stale running jobs on startup", res.modified_count)
    except Exception as e:
        logger.warning("Failed to reconcile stale running jobs on startup: %s", e)
    yield
    # ── Graceful shutdown ──────────────────────────────────────────────
    # Cancel in-flight background tasks so they can update MongoDB status
    # before the process exits.  Daemon threads (UrlSearchThread) are killed
    # by the runtime anyway, but asyncio Tasks get a chance to finish.
    try:
        from app.api.routes.search import _tasks as _bg_tasks
        for _key, _task in list(_bg_tasks.items()):
            if not _task.done():
                _task.cancel()
        # Give cancelled tasks a brief window to clean up
        if _bg_tasks:
            import asyncio as _aio
            await _aio.gather(
                *[t for t in _bg_tasks.values() if not t.done()],
                return_exceptions=True,
            )
            logger.info("Gracefully cancelled %d background task(s)", len(_bg_tasks))
    except Exception as e:
        logger.warning("Error during background task cleanup: %s", e)
    # Abort any in-flight Apify actor runs to save credits
    try:
        from app.connectors.apify_connector import _active_apify_runs
        if _active_apify_runs:
            from app.admin import settings as _s
            token = _s.get_apify_token()
            if token:
                from apify_client import ApifyClient as _AC
                _client = _AC(token)
                for run_id in list(_active_apify_runs):
                    try:
                        _client.runs().get(run_id).abort()
                        logger.info("Aborted Apify run %s", run_id)
                    except Exception:
                        pass
                _active_apify_runs.clear()
    except Exception as e:
        logger.warning("Error aborting Apify runs: %s", e)
    # Close database clients cleanly
    try:
        from app.db.mongo import get_async_client, get_sync_client, reset_client_caches
        ac = get_async_client()
        if ac is not None:
            ac.close()
        sc = get_sync_client()
        if sc is not None:
            sc.close()
        reset_client_caches()
        logger.info("Database clients closed")
    except Exception as e:
        logger.warning("Error closing database clients: %s", e)


# ── CORS configuration ─────────────────────────────────────────────────────
# Production: restrict to configured origins. Development: allow localhost.
def _get_cors_origins() -> list[str]:
    """Return allowed CORS origins from config."""
    raw = getattr(settings, "allowed_origins", "") or ""
    if raw.strip():
        origins = [o.strip() for o in raw.split(",") if o.strip()]
        if origins:
            return origins
    # Default: same-origin only (no cross-origin requests allowed)
    return []


_cors_origins = _get_cors_origins()

# Only create docs URL if API docs are enabled
_docs_url = "/docs" if settings.enable_api_docs else None
_redoc_url = "/redoc" if settings.enable_api_docs else None

app = FastAPI(
    title="LeadAI — AI-Orchestrated Social Lead Intelligence Platform",
    description=(
        "Paste a Facebook page, Instagram profile, YouTube channel or LinkedIn "
        "company URL — the agent runs the right Apify actor, normalizes the "
        "real data and you drill from page → posts → comments → AI-analyzed "
        "leads."
    ),
    version="2.5.0",
    lifespan=lifespan,
    docs_url=_docs_url,
    redoc_url=_redoc_url,
)

# CORS middleware — configured origins or same-origin only
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["*"],
        allow_credentials=True,
    )


# ── Security headers middleware ─────────────────────────────────────────────
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"
        # Clickjacking protection
        response.headers["X-Frame-Options"] = "DENY"
        # XSS protection (legacy browsers)
        response.headers["X-XSS-Protection"] = "1; mode=block"
        # Referrer policy — send origin only on cross-origin requests
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # Permissions policy — restrict browser features
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        # HSTS — force HTTPS for 1 year (only effective when served over HTTPS)
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        # Content Security Policy — allow inline styles/scripts for existing vanilla JS frontend
        # This is intentionally permissive for the current architecture.
        # A stricter CSP should be adopted when migrating to a framework with bundled assets.
        csp_directives = [
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline'",
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
            "img-src 'self' data: https:",
            "font-src 'self' https://fonts.gstatic.com",
            "connect-src 'self'",
            "frame-ancestors 'none'",
            "base-uri 'self'",
            "form-action 'self'",
        ]
        response.headers["Content-Security-Policy"] = "; ".join(csp_directives)
        return response


# ── CSRF protection: validate Origin/Referer for state-changing requests ──
class CSRFProtectionMiddleware(BaseHTTPMiddleware):
    """Defense-in-depth CSRF protection.

    Validates Origin/Referer headers on state-changing requests (POST, PUT,
    PATCH, DELETE) to prevent cross-site request forgery beyond the SameSite
    cookie policy. SameSite=Lax already blocks cookies on cross-origin POSTs,
    but this adds a second layer of defense.

    Allowed exceptions:
    - /api/auth/login (login itself must be reachable from login page)
    - /api/public/* (public config endpoint)
    - /health (healthcheck)
    - /static/* (static assets)
    """
    _SAFE_PATHS = {"/api/auth/login", "/health"}
    _SAFE_PREFIXES = ("/static", "/api/public")
    _STATE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

    async def dispatch(self, request: Request, call_next):
        if request.method not in self._STATE_METHODS:
            return await call_next(request)
        path = request.url.path
        if path in self._SAFE_PATHS or any(path.startswith(p) for p in self._SAFE_PREFIXES):
            return await call_next(request)
        # Check Origin header first, then Referer
        origin = request.headers.get("origin")
        referer = request.headers.get("referer")
        host = request.headers.get("host", "")
        if origin:
            try:
                parsed = urlparse(origin)
                origin_host = parsed.netloc
                if origin_host and host and origin_host != host:
                    return JSONResponse(
                        {"success": False, "error": "csrf_blocked",
                         "message": "Cross-origin request rejected"},
                        status_code=403)
            except Exception:
                return JSONResponse(
                    {"success": False, "error": "csrf_blocked",
                     "message": "Invalid Origin header"},
                    status_code=403)
        elif referer:
            try:
                parsed = urlparse(referer)
                referer_host = parsed.netloc
                if referer_host and host and referer_host != host:
                    return JSONResponse(
                        {"success": False, "error": "csrf_blocked",
                         "message": "Cross-origin request rejected"},
                        status_code=403)
            except Exception:
                pass
        return await call_next(request)


app.add_middleware(CSRFProtectionMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

app.include_router(search_router)
app.include_router(auth_router)
app.include_router(comment_filters_router)
app.include_router(settings_router)
app.include_router(public_config_router)
app.include_router(organizations_router)
app.include_router(billing_router)

app.include_router(admin_leads_router)
app.include_router(admin_ai_router)
app.include_router(admin_cms_router)
app.include_router(admin_apify_router)
app.include_router(admin_router)
app.include_router(admin_analytics_router)
app.include_router(public_website_router)
app.include_router(super_admin_router)
app.include_router(super_admin_lifecycle_router)
app.include_router(notifications_router)
app.include_router(org_admin_router)
app.include_router(super_admin_platform_router)
app.include_router(me_router)


# ── Error pages & error reporting ──────────────────────────────────────────
from fastapi.exceptions import RequestValidationError  # noqa: E402
from starlette.exceptions import HTTPException as StarletteHTTPException  # noqa: E402

_ERROR_PAGES = {403: "403.html", 404: "404.html"}


def _wants_html(request: Request) -> bool:
    return (not request.url.path.startswith("/api/")
            and "text/html" in request.headers.get("accept", ""))


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    page = _ERROR_PAGES.get(exc.status_code)
    if page and _wants_html(request):
        path = os.path.join(static_dir, page)
        if os.path.exists(path):
            return FileResponse(path, status_code=exc.status_code)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                        headers=getattr(exc, "headers", None))


_last_error_notice = {"at": 0.0}


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    """500s are logged, audited and (at most once a minute) notified to Super Admins."""
    import time as _t
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    try:
        if _t.time() - _last_error_notice["at"] > 60:
            _last_error_notice["at"] = _t.time()
            from app.events.notifications import notify_super_admins
            notify_super_admins("system_error", "Server error",
                                f"{request.method} {request.url.path}: {type(exc).__name__}",
                                severity="danger", link="/superadmin#health")
    except Exception:
        pass
    return JSONResponse({"success": False, "error": "internal_error",
                         "message": "Something went wrong. Please try again."}, status_code=500)

# ── Static assets ───────────────────────────────────────────────────────────
static_dir = os.path.join(os.path.dirname(__file__), "static")

if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


# Pages that stay reachable without a session
_OPEN_PAGES = {"/health", "/login", "/docs", "/redoc", "/openapi.json"}


def _maintenance_enabled() -> bool:
    """Maintenance flag read through the settings service (Mongo-backed)."""
    try:
        return admin_settings.is_maintenance_enabled()
    except Exception:
        return False


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    """Require a valid session for every /api call and HTML page. Static
    assets and the auth endpoints stay open (the /api/auth endpoints check
    the session themselves where needed).

    Sessions are scoped: the main website (``/``, ``/api/*``) needs a
    ``site`` session (a database user); the admin portal (``/admin``,
    ``/api/admin/*``) needs an ``admin`` session (SUPERADMIN_EMAIL from the
    environment, or an admin_users / platform-staff database account). The super admin portal (``/superadmin``,
    ``/api/super-admin/*``) also needs an ``admin`` session with
    super_admin role.

    Portal detection normalizes a trailing slash (``/superadmin/`` ≡
    ``/superadmin``) so those paths never fall through to the generic
    site-login redirect."""
    path = request.url.path
    # Normalize trailing slash ONLY for exact portal-page matches so
    # /superadmin/ and /admin/ take the same auth branch as their
    # canonical forms (previously they fell through to RedirectResponse("/login")).
    portal_path = path.rstrip("/") or "/"
    _PUBLIC_PAGES = {"/signup", "/contact", "/website", "/features", "/pricing", "/about",
                     "/faq", "/privacy", "/terms", "/request-demo", "/demo-pending",
                     "/forgot-password", "/reset-password", "/403", "/404",
                     "/how-it-works", "/cookies", "/testimonials", "/sitemap.xml", "/robots.txt"}
    if (
        path.startswith(("/static", "/api/auth", "/api/public", "/api/billing/plans", "/api/billing/webhook", "/api/invitations"))
        or path in _OPEN_PAGES
        or path in _PUBLIC_PAGES
        or path.startswith("/invite/")
        or path == "/api/admin/impersonate/exit"
        or path == "/api/super-admin/impersonate/exit"
    ):
        return await call_next(request)
    user = session_user(request)
    scope = (user or {}).get("scope")
    super_admin_area = portal_path == "/superadmin" or path.startswith("/api/super-admin/")
    admin_area = portal_path == "/admin" or (
        path.startswith(("/api/admin/", "/api/comment-filters/"))
        and not path.startswith("/api/comment-filters/catalog")
    )
    if super_admin_area:
        # Super admin area requires admin scope + super_admin role
        if scope != "admin":
            if path.startswith("/api/"):
                return JSONResponse(
                    {"success": False, "error": "unauthorized",
                     "message": "Sign in required"},
                    status_code=401)
            return RedirectResponse("/login?superadmin=1", status_code=303)
        # Verify super_admin role
        from app.auth.roles import effective_role
        role = effective_role((user or {}).get("email", ""))
        if role != "super_admin":
            if path.startswith("/api/"):
                return JSONResponse(
                    {"success": False, "error": "forbidden",
                     "message": "Super Admin access required"},
                    status_code=403)
            return RedirectResponse("/admin", status_code=303)
        return await _call_as(request, call_next, user)
    required_scope = "admin" if admin_area else "site"
    if admin_area and scope == "admin":
        # /admin is the platform staff console: the server-side role must
        # still be a platform role (no fallback for unknown accounts).
        from app.auth.roles import effective_role
        if not effective_role((user or {}).get("email", "")):
            if path.startswith("/api/"):
                return JSONResponse({"success": False, "error": "forbidden",
                                     "message": "Platform access required"}, status_code=403)
            return RedirectResponse("/login?admin=1", status_code=303)
    if scope != required_scope:
        if path.startswith("/api/"):
            return JSONResponse(
                {"success": False, "error": "unauthorized",
                 "message": "Sign in required"},
                status_code=401)
        target = "/login?admin=1" if admin_area else "/login"
        return RedirectResponse(target, status_code=303)
    return await _call_as(request, call_next, user)


async def _call_as(request: Request, call_next, user):
    """Run the request as ``user``: exposed on request.state and bound as the
    default audit actor, so every audit entry written while serving it names
    who acted (and from where) even when a handler passes no user."""
    from app.admin.audit import request_meta, reset_request_actor, set_request_actor
    request.state.user = user
    meta = request_meta(request)
    token = set_request_actor(user, meta.get("ip"), meta.get("user_agent"))
    try:
        return await call_next(request)
    finally:
        reset_request_actor(token)


# Registered AFTER auth_gate so it runs FIRST (outermost): maintenance mode
# gates the user app before any auth check, but never the admin panel.
@app.middleware("http")
async def maintenance_gate(request: Request, call_next):
    """When maintenance mode is on, block the user app (pages and /api/*)
    with a 503 while keeping the admin panel, sign-in, health and static
    assets reachable. Any valid session also passes, so signed-in admins
    are never locked out."""
    if not _maintenance_enabled():
        return await call_next(request)
    path = request.url.path
    always_open = (path.startswith(("/static", "/api/auth", "/admin", "/api/admin",
                                    "/api/comment-filters", "/api/public",
                                    "/superadmin", "/api/super-admin"))
                   or path in ("/login", "/health"))
    user = session_user(request)
    if always_open or (user is not None and (user.get("scope") == "admin"
                                             or user.get("impersonated_by"))):
        return await call_next(request)
    message = admin_settings.maintenance_message()
    if path.startswith("/api/"):
        return JSONResponse(
            {"success": False, "error": "maintenance",
             "message": message},
            status_code=503)
    return FileResponse(
        os.path.join(static_dir, "maintenance.html"),
        status_code=503) if os.path.exists(
            os.path.join(static_dir, "maintenance.html")) else JSONResponse(
        {"success": False, "error": "maintenance", "message": message},
        status_code=503)


@app.get("/login")
async def login_page():
    login_path = os.path.join(static_dir, "login.html")
    if os.path.exists(login_path):
        return FileResponse(login_path)
    return RedirectResponse("/", status_code=303)


@app.get("/health")
async def health():
    """Public health endpoint — verifies MongoDB connectivity."""
    import time as _time
    mongo_ok = False
    mongo_latency_ms = None
    try:
        from app.db.mongo import get_async_db
        db = get_async_db()
        if db is not None:
            started = _time.perf_counter()
            await db.command("ping")
            mongo_latency_ms = round((_time.perf_counter() - started) * 1000, 1)
            mongo_ok = True
    except Exception:
        pass
    status = "ok" if mongo_ok else "degraded"
    result = {"status": status, "auth_enabled": True}
    if mongo_latency_ms is not None:
        result["mongo_latency_ms"] = mongo_latency_ms
    return result


@app.get("/admin")
@app.get("/admin/")
async def admin_page():
    admin_path = os.path.join(static_dir, "admin.html")
    if os.path.exists(admin_path):
        return FileResponse(admin_path)
    return RedirectResponse("/login?admin=1", status_code=303)


@app.get("/superadmin")
@app.get("/superadmin/")
async def super_admin_page():
    # Authorization is enforced by auth_gate above: only an admin-scope
    # session whose server-side effective role is super_admin reaches here.
    super_admin_path = os.path.join(static_dir, "super-admin.html")
    if os.path.exists(super_admin_path):
        return FileResponse(super_admin_path)
    return RedirectResponse("/login?superadmin=1", status_code=303)


@app.get("/org-admin")
@app.get("/org-admin/")
async def org_admin_page(request: Request):
    """Organization Admin portal — owners/admins of an organization whose
    Admin portal has been enabled (after Super Admin confirmation)."""
    from fastapi import HTTPException as _HTTPException
    from app.auth.tenant import get_tenant_context
    try:
        ctx = get_tenant_context(request)
    except _HTTPException:
        return RedirectResponse("/login", status_code=303)
    if ctx.org_role not in ("owner", "admin"):
        return _static_status("403.html", 403)
    return _static("org-admin.html")


@app.get("/")
@app.get("/dashboard")
async def root():
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"name": "LeadAI"}


def _static(filename: str):
    """Return a static file from the static directory."""
    path = os.path.join(static_dir, filename)
    if os.path.exists(path):
        return FileResponse(path)
    return RedirectResponse("/", status_code=303)


def _static_status(filename: str, status: int):
    path = os.path.join(static_dir, filename)
    if os.path.exists(path):
        return FileResponse(path, status_code=status)
    return JSONResponse({"detail": "Forbidden" if status == 403 else "Not found"}, status_code=status)


@app.get("/request-demo")
async def request_demo_page():
    return _static("signup.html")


@app.get("/demo-pending")
async def demo_pending_page():
    return _static("demo-pending.html")


@app.get("/forgot-password")
@app.get("/reset-password")
async def reset_password_page():
    return _static("reset-password.html")


@app.get("/invite/{token}")
async def invite_page(token: str):
    return _static("invite.html")


@app.get("/billing/status")
@app.get("/billing/checkout/{session_id}")
async def billing_status_page(session_id: str = ""):
    return _static("billing-status.html")


@app.get("/403")
async def forbidden_page():
    return _static_status("403.html", 403)


@app.get("/404")
async def not_found_page():
    return _static_status("404.html", 404)


# ── Public website pages (no auth required) ─────────────────────────────────
# Paths, HTML shells and CMS slugs live in public_website.SITE_ROUTES; each
# page is served with its CMS SEO meta rendered server-side.
from app.api.routes.public_website import (  # noqa: E402
    SITE_ROUTES as _SITE_ROUTES, site_route_handler as _site_route_handler,
    build_sitemap as _build_sitemap, build_robots as _build_robots,
)
from fastapi import Response as _Response  # noqa: E402

for _site_path in _SITE_ROUTES:
    app.add_api_route(_site_path, _site_route_handler(_site_path), methods=["GET"],
                      include_in_schema=False)


@app.get("/sitemap.xml", include_in_schema=False)
async def sitemap_xml(request: Request):
    return _Response(await _build_sitemap(request), media_type="application/xml")


@app.get("/robots.txt", include_in_schema=False)
async def robots_txt(request: Request):
    return _Response(await _build_robots(request), media_type="text/plain")


@app.get("/signup")
async def signup_page():
    return _static("signup.html")

