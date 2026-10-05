import asyncio
import logging
import os
import re
from typing import Optional
from contextlib import asynccontextmanager
from urllib.parse import urlparse
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import Response
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
from app.api.routes.compliance import router as compliance_router
from app.api.routes.public_v1 import router as public_v1_router
from app.api.routes.partner_portal import router as partner_portal_router
from app.api.routes.partner_public import router as partner_public_router
from app.api.routes.super_admin_partners import router as super_admin_partners_router
from app.api.routes.super_admin_access import router as super_admin_access_router
from app.logging_context import RequestIdFilter, RequestIdMiddleware, capture_exception

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
    req_filter = RequestIdFilter()
    root.addFilter(req_filter)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(name)s] [request_id=%(request_id)s] %(message)s", "%H:%M:%S")
    redact = _SecretRedactingFilter()
    console = logging.StreamHandler()
    console.setLevel(getattr(logging, console_level, logging.INFO))
    console.setFormatter(fmt)
    console.addFilter(req_filter)
    console.addFilter(redact)
    root.addHandler(console)
    # test runs (in-memory DB) must not append to the real application log
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
        os.makedirs(log_dir, exist_ok=True)
        from logging.handlers import RotatingFileHandler
        log_file = RotatingFileHandler(
            os.path.join(log_dir, "app.log"), encoding="utf-8",
            maxBytes=10 * 1024 * 1024, backupCount=5)
        log_file.setLevel(getattr(logging, file_level, logging.DEBUG))
        log_file.setFormatter(fmt)
        log_file.addFilter(req_filter)
        log_file.addFilter(redact)
        root.addHandler(log_file)
    # uvicorn's own access logs stay on console only
    logging.getLogger("uvicorn.access").propagate = False
    # mongo driver chatter is not useful at DEBUG level — force to WARNING
    # to prevent log flood from pymongo.topology / pymongo.connection.
    # httpx logs every request URL at INFO — including API keys passed as a
    # query parameter (Gemini's ?key=) — so it is kept at WARNING too.
    for noisy in ("pymongo", "pymongo.topology", "pymongo.connection",
                  "pymongo.monitoring", "motor", "urllib3", "httpcore", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


class _SecretRedactingFilter(logging.Filter):
    """Masks credentials that libraries may put in a log line (API keys in
    query strings, bearer tokens, passwords in connection strings)."""
    _PATTERNS = (
        (re.compile(r"([?&](?:key|api_key|apikey|token|access_token)=)[^&\s\"']+", re.I), r"\1***"),
        (re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.I), r"\1***"),
        (re.compile(r"(mongodb(?:\+srv)?://[^:/\s]+:)[^@\s]+@", re.I), r"\1***@"),
    )

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        cleaned = message
        for pattern, repl in self._PATTERNS:
            cleaned = pattern.sub(repl, cleaned)
        if cleaned != message:
            record.msg, record.args = cleaned, ()
        return True


_is_ready: bool = False


async def _run_startup_tasks():
    global _is_ready
    try:
        from app.db.mongo import get_sync_db, get_async_db
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, ensure_indexes)

        sdb = get_sync_db()
        if sdb is not None:
            from app.db.migration import migrate_to_multi_tenant
            await loop.run_in_executor(None, migrate_to_multi_tenant, sdb)

        adb = get_async_db()
        if sdb is not None:
            from app.partners.service import ensure_program_defaults
            await loop.run_in_executor(None, ensure_program_defaults, sdb)

        if adb is not None:
            from app.billing.plans import ensure_default_plans
            await ensure_default_plans(adb)
            # older all-or-nothing BYOK data -> per-API coverage + encrypted keys (idempotent)
            try:
                from app.db.mongo import get_sync_db as _sdb
                from app.services.tenant_api_keys import migrate_tenant_api_keys
                await asyncio.to_thread(migrate_tenant_api_keys, _sdb())
            except Exception as e:
                logger.warning("API key migration skipped: %s", e)
            # website sign-ups start a 3-day free trial at once (applied once)
            try:
                from app.lifecycle.config import migrate_self_serve_trial
                await asyncio.to_thread(migrate_self_serve_trial, _sdb())
            except Exception as e:
                logger.warning("Self-serve trial settings migration skipped: %s", e)
            from app.cms.models import ensure_cms_indexes, migrate_trial_copy, seed_cms_defaults
            await ensure_cms_indexes(adb)
            await seed_cms_defaults(adb)
            try:  # once: shipped "request a demo" copy -> free-trial copy
                await migrate_trial_copy(adb)
            except Exception as e:
                logger.warning("Website trial copy update skipped: %s", e)

        if sdb is not None:
            from app.db.models import utcnow
            def _clean_stale():
                return sdb.search_history.update_many(
                    {"status": "running"},
                    {"$set": {"status": "cancelled", "phase": "cancelled", "message": "Interrupted (server restart)", "completed_at": utcnow()}}
                )
            res = await loop.run_in_executor(None, _clean_stale)
            if res and res.modified_count:
                logger.info("Cleaned up %d stale running jobs on startup", res.modified_count)

        from app.admin.envvars import legacy_locked_overrides
        _legacy = legacy_locked_overrides()
        if _legacy:
            logger.warning("Stored in-app values for environment-only variables: %s — move them to "
                           "the environment and remove them in Super Admin → Integrations.", ", ".join(_legacy))
            from app.events.notifications import notify_super_admins
            notify_super_admins("security_event", "Move stored secrets to the environment",
                                "These are environment-only now but still have a value stored in the app: "
                                + ", ".join(_legacy) + ". Copy them to the hosting environment, then remove "
                                "the stored copies (Integrations).", severity="warning",
                                link="/superadmin#/integrations")
    except Exception as e:
        logger.warning(f"Error during async startup initialization: {e}")
    finally:
        _is_ready = True
        logger.info("Application startup background tasks completed; server ready.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
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
    # Non-blocking startup initialization running in background task (await in tests)
    startup_task = None
    if os.environ.get("PYTEST_CURRENT_TEST"):
        await _run_startup_tasks()
    else:
        startup_task = asyncio.create_task(_run_startup_tasks())

    # time-driven lifecycle: billing periods, cancellations, demo expiry
    from app.lifecycle.maintenance import start_background_sweeper
    sweeper = start_background_sweeper()

    # Durable queue worker (Phase 5)
    from app.queue.service import start_queue_worker, stop_queue_worker
    queue_worker = None
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        queue_worker = start_queue_worker()

    yield

    sweeper.cancel()
    if queue_worker:
        stop_queue_worker()
    if startup_task and not startup_task.done():
        startup_task.cancel()
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
                        _client.run(run_id).abort()
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

        # Cache-Control policy:
        # Prevent caching on authenticated API routes; allow caching on public configs
        if request.url.path.startswith("/api/"):
            if request.url.path.startswith("/api/public/"):
                response.headers["Cache-Control"] = "public, max-age=60"
            elif not request.url.path.startswith("/api/billing/plans"):
                response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
                response.headers["Pragma"] = "no-cache"

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
    _SAFE_PATHS = {"/api/auth/login", "/health", "/api/health"}
    _SAFE_PREFIXES = ("/static", "/api/public", "/api/v1")
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
app.include_router(compliance_router)
app.include_router(public_v1_router)
# super_admin_partners before any catch-all; partner portal + public program/referral links
app.include_router(super_admin_partners_router)
app.include_router(super_admin_access_router)
app.include_router(partner_portal_router)
app.include_router(partner_public_router)


# ── Error pages & error reporting ──────────────────────────────────────────
from starlette.exceptions import HTTPException as StarletteHTTPException  # noqa: E402

_ERROR_PAGES = {403: "403.html", 404: "404.html"}


def _wants_html(request: Request) -> bool:
    return (not request.url.path.startswith("/api/")
            and "text/html" in request.headers.get("accept", ""))


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    # 503 on a portal page (DB temporarily down) → redirect to login with a
    # clear query param so the browser doesn't show raw JSON to the user.
    if exc.status_code == 503 and _wants_html(request):
        return RedirectResponse("/login?error=db_unavailable", status_code=303)
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
        capture_exception(exc, tags={"method": request.method, "path": request.url.path})
    except Exception:
        pass
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

# ── Static assets with aggressive caching ─────────────────────────────────
static_dir = os.path.join(os.path.dirname(__file__), "static")


class CachedStaticFiles(StaticFiles):
    """Static file handler: a year-long immutable cache only for assets requested
    with a version tag (``?v=…``, changed whenever the file changes); every other
    asset and HTML is revalidated on each use (a cheap 304), so a deploy is never
    hidden behind a browser's or Cloudflare's old copy."""

    def file_response(self, *args, **kwargs) -> Response:
        resp = super().file_response(*args, **kwargs)
        path = kwargs.get("full_path") or kwargs.get("path") or (args[0] if args else "")
        scope = kwargs.get("scope") or (args[2] if len(args) > 2 else None) or {}
        versioned = b"v=" in (scope.get("query_string") or b"")
        ext = os.path.splitext(str(path))[1].lower()
        if ext in (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".woff2", ".ttf", ".webp"):
            resp.headers["Cache-Control"] = ("public, max-age=31536000, immutable" if versioned
                                             else "no-cache, must-revalidate")
        elif ext in (".html", ".htm"):
            resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp


if os.path.exists(static_dir):
    app.mount("/static", CachedStaticFiles(directory=static_dir), name="static")


# Pages that stay reachable without a session
_OPEN_PAGES = {"/health", "/api/health", "/login", "/docs", "/redoc", "/openapi.json"}


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
    _PUBLIC_PAGES = {"/", "/signup", "/contact", "/website", "/features", "/pricing", "/about",
                     "/faq", "/privacy", "/terms", "/request-demo", "/demo-pending",
                     "/forgot-password", "/reset-password", "/403", "/404",
                     "/how-it-works", "/cookies", "/testimonials", "/sitemap.xml", "/robots.txt",
                     "/partners"}
    if (
        path.startswith(("/static", "/api/auth", "/api/public", "/api/billing/plans", "/api/billing/webhook", "/api/invitations", "/api/business-context/catalog"))
        or path in _OPEN_PAGES
        or path in _PUBLIC_PAGES
        or path.startswith("/invite/")
        or path.startswith("/r/")                 # partner referral links (public)
        or path.startswith("/api/v1/")            # public REST API: X-API-Key checked by the routes
        or path == "/api/admin/impersonate/exit"
        or path == "/api/super-admin/impersonate/exit"
    ):
        return await call_next(request)
    # the server-side session check reads MongoDB with the sync driver: run it
    # off the event loop so one slow round trip never stalls other requests
    from starlette.concurrency import run_in_threadpool
    user = await run_in_threadpool(session_user, request)
    scope = (user or {}).get("scope")
    # An administrator set this account's password: until the person picks
    # their own, only the change-password page (and /api/auth/*, handled
    # above) is reachable — in every portal. Impersonation is never blocked.
    if portal_path == "/change-password":
        if user is None:
            return RedirectResponse("/login", status_code=303)
        return await _call_as(request, call_next, user)
    if user is not None and user.get("must_change_password") and not user.get("impersonated_by"):
        if path.startswith("/api/"):
            return JSONResponse({"success": False, "error": "password_change_required",
                                 "message": "Choose a new password to continue."}, status_code=403)
        return RedirectResponse("/change-password", status_code=303)
    # Partner Portal: its own session scope ("partner"); the API also accepts a
    # partner API key (validated, read-only, by the route dependencies).
    if portal_path == "/partner" or path.startswith("/api/partner/"):
        import time as _time
        from app.partners.activity import record_request
        started = _time.perf_counter()
        if path.startswith("/api/partner/") and _partner_api_key(request):
            response = await call_next(request)
            record_request(request, response.status_code, started)
            return response
        if scope != "partner":
            if path.startswith("/api/"):
                return JSONResponse({"success": False, "error": "unauthorized",
                                     "message": "Partner sign-in required"}, status_code=401)
            return RedirectResponse("/login?partner=1", status_code=303)
        response = await _call_as(request, call_next, user)
        # every Partner Portal page / API request goes to the Super Admin's activity log
        record_request(request, response.status_code, started)
        return response
    if scope == "partner" and path.startswith(("/api/super-admin/", "/api/admin/", "/api/org-admin/")):
        # a partner session probing admin / organization APIs: refused below, reviewed here
        from app.partners.fraud import raise_flag
        from app.events.security import security_event_from_request
        security_event_from_request(request, "partner_privilege_probe", "high",
                                    details={"path": path, "actor": (user or {}).get("email")})
        raise_flag("privilege_probe", partner_id=None, severity="high", subject_type="user",
                   subject_id=str((user or {}).get("user_id") or ""), details={"path": path,
                                                                               "email": (user or {}).get("email")})
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
        if admin_area:
            # If a signed-in site user (org owner / admin / customer) visits /admin, gracefully route them
            if scope == "site" and portal_path == "/admin":
                from app.auth.tenant import resolve_tenant_context
                try:
                    ctx = resolve_tenant_context(user)
                    if ctx.org_role in ("owner", "admin"):
                        return RedirectResponse("/org-admin", status_code=303)
                    return RedirectResponse("/dashboard", status_code=303)
                except Exception:
                    pass
            target = "/login?admin=1"
        else:
            # come back to the page that was asked for after signing in
            from urllib.parse import quote
            back = path + (("?" + request.url.query) if request.url.query else "")
            target = "/login" if path in ("/user", "/dashboard") else "/login?next=" + quote(back, safe="")
        return RedirectResponse(target, status_code=303)
    return await _call_as(request, call_next, user)


def _partner_api_key(request: Request) -> bool:
    from app.partners.constants import API_KEY_PREFIX
    raw = request.headers.get("x-api-key") or ""
    auth = request.headers.get("authorization") or ""
    if not raw and auth.lower().startswith("bearer "):
        raw = auth.split(" ", 1)[1].strip()
    return raw.startswith(API_KEY_PREFIX)


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
                   or path in ("/login", "/health", "/api/health"))
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


# Outermost response wrappers: CSRF, Security/Cache headers, Request IDs, and GZip compression
app.add_middleware(CSRFProtectionMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=1000)


def _serve_html_file(filename: str, request: Optional[Request] = None, status_code: int = 200) -> Response:
    """Serve an HTML file with ETag generation and 304 Not Modified validation."""
    path = os.path.join(static_dir, filename) if not os.path.isabs(filename) else filename
    if not os.path.exists(path):
        return RedirectResponse("/", status_code=303)
    try:
        stat = os.stat(path)
        etag = f'"{int(stat.st_mtime):x}-{stat.st_size:x}"'
        if request:
            inm = request.headers.get("if-none-match")
            if inm and inm.strip() == etag:
                return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache, must-revalidate"})
        resp = FileResponse(path, status_code=status_code)
        resp.headers["ETag"] = etag
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp
    except Exception:
        return FileResponse(path, status_code=status_code)


@app.get("/login")
async def login_page(request: Request):
    return _serve_html_file("login.html", request)


@app.get("/health")
@app.get("/api/health")
async def health():
    """Public health endpoint — verifies MongoDB connectivity and startup readiness."""
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
    from app.auth.superadmin import config_status
    result = {"status": status, "ready": _is_ready, "auth_enabled": True,
              # deployed revision (Render sets RENDER_GIT_COMMIT) and whether a
              # Super Admin is configured — a yes/no only, never who or how
              "commit": (os.environ.get("RENDER_GIT_COMMIT") or "")[:7] or None,
              "superadmin_configured": bool(config_status()["configured"])}
    if mongo_latency_ms is not None:
        result["mongo_latency_ms"] = mongo_latency_ms
    return result


@app.get("/admin")
@app.get("/admin/")
async def admin_page(request: Request):
    return _serve_html_file("admin.html", request)


@app.get("/superadmin")
@app.get("/superadmin/")
async def super_admin_page(request: Request):
    # Authorization is enforced by auth_gate above: only an admin-scope
    # session whose server-side effective role is super_admin reaches here.
    return _serve_html_file("super-admin.html", request)


@app.get("/org-admin")
@app.get("/org-admin/")
def org_admin_page(request: Request):
    """Organization Admin portal — owners/admins of an organization whose
    Admin portal has been enabled (after Super Admin confirmation).
    Plain ``def``: the tenant lookup is sync, so it runs in the threadpool."""
    from fastapi import HTTPException as _HTTPException
    from app.auth.tenant import get_tenant_context
    try:
        ctx = get_tenant_context(request)
    except _HTTPException:
        return RedirectResponse("/login", status_code=303)
    if ctx.org_role not in ("owner", "admin"):
        return _static_status("403.html", 403, request)
    return _static("org-admin.html", request)


@app.get("/partner")
@app.get("/partner/")
async def partner_portal_page(request: Request):
    """Partner Portal — partner-scope sessions only (enforced by auth_gate);
    applicants see their application status, active partners the portal."""
    return _static("partner.html", request)


@app.get("/partners")
async def partner_program_page(request: Request):
    """Public partner program page + application form."""
    return _static("partners.html", request)


@app.get("/")
async def root(request: Request):
    """The public website's home page (same as /website)."""
    from app.api.routes.public_website import render_site_page
    return await render_site_page(request, "website.html", "home")


@app.get("/user")
@app.get("/dashboard")
async def user_panel(request: Request):
    """The customer user panel (sign-in required, see auth_gate)."""
    return _serve_html_file("index.html", request)


def _static(filename: str, request: Optional[Request] = None):
    """Return a static file from the static directory."""
    return _serve_html_file(filename, request)


def _static_status(filename: str, status: int, request: Optional[Request] = None):
    path = os.path.join(static_dir, filename)
    if os.path.exists(path):
        return _serve_html_file(filename, request, status_code=status)
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


@app.get("/change-password")
async def change_password_page():
    """Signed-in people whose password an administrator set choose their own
    here (auth_gate sends them here; it needs a session of any portal)."""
    return _static("change-password.html")


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

