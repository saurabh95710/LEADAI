import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from app.db.mongo import ensure_indexes
from app.api.routes.search import router as search_router
from app.api.routes.auth import router as auth_router
from app.api.routes.admin import router as admin_router
from app.auth.service import session_user
from app.config import get_settings
from app.admin import settings as admin_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def _setup_logging():
    """Console (INFO) + file (DEBUG) so every Apify request/response,
    classification and agent step is traceable end-to-end."""
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    root.addHandler(console)
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = logging.FileHandler(
        os.path.join(log_dir, "app.log"), encoding="utf-8")
    log_file.setLevel(logging.DEBUG)
    log_file.setFormatter(fmt)
    root.addHandler(log_file)
    # uvicorn's own access logs stay on console only
    logging.getLogger("uvicorn.access").propagate = False
    # mongo driver chatter is not useful at DEBUG level
    logging.getLogger("pymongo").setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_logging()
    if not settings.apify_api_token:
        logger.warning(
            "APIFY_API_TOKEN is not set in .env — searches will fail with an error. "
            "Get a free token at https://apify.com/account/integrations"
        )
    ensure_indexes()
    yield


app = FastAPI(
    title="LeadAI — AI-Orchestrated Social Lead Intelligence Platform",
    description=(
        "Paste a Facebook page, Instagram profile, YouTube channel or LinkedIn "
        "company URL — the agent runs the right Apify actor, normalizes the "
        "real data and you drill from page → posts → comments → AI-analyzed "
        "leads."
    ),
    version="2.3.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(search_router)
app.include_router(auth_router)
app.include_router(admin_router)

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
    the session themselves where needed)."""
    path = request.url.path
    if path.startswith(("/static", "/api/auth")) or path in _OPEN_PAGES:
        return await call_next(request)
    user = session_user(request)
    if path.startswith("/api/"):
        if user is None:
            return JSONResponse(
                {"success": False, "error": "unauthorized",
                 "message": "Sign in required"},
                status_code=401)
    elif user is None:
        return RedirectResponse("/login", status_code=303)
    request.state.user = user
    return await call_next(request)


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
    always_open = (path.startswith(("/static", "/api/auth", "/admin", "/api/admin"))
                   or path in ("/login", "/health"))
    user = session_user(request)
    if always_open or user is not None:
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
    return {"status": "ok",
            "apify_configured": bool(settings.apify_api_token),
            "auth_enabled": True}


@app.get("/admin")
async def admin_page():
    admin_path = os.path.join(static_dir, "admin.html")
    if os.path.exists(admin_path):
        return FileResponse(admin_path)
    return RedirectResponse("/login", status_code=303)


@app.get("/")
@app.get("/dashboard")
async def root():
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {
        "name": "LeadAI",
        "docs": "/docs",
        "endpoints": [
            "POST /api/url/search",
            "GET /api/url/search/{run_id}/report",
            "GET /api/search/history",
            "GET /api/search/{run_id}",
            "POST /api/search/{run_id}/cancel",
            "GET /api/pages",
            "GET /api/pages/{id}",
            "POST /api/pages/{id}/posts",
            "GET /api/pages/{id}/posts",
            "GET /api/posts/{id}",
            "POST /api/posts/{id}/comments",
            "GET /api/posts/{id}/comments",
            "GET /api/comments/{id}",
            "GET /api/export/pages.csv",
            "GET /api/export/posts.csv",
            "GET /api/export/comments.csv",
        ],
    }
