import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from app.db.mongo import ensure_indexes
from app.api.routes.search import router as search_router
from app.config import get_settings

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
    title="LeadAI — AI-Orchestrated Facebook Lead Intelligence Platform",
    description=(
        "The AI agent understands your search intent, runs the right Apify "
        "Facebook actor, normalizes the real data and lets you drill from "
        "pages → posts → comments → AI-analyzed leads."
    ),
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(search_router)

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/health")
async def health():
    return {"status": "ok",
            "apify_configured": bool(settings.apify_api_token),
            "brightdata_configured": bool(settings.brightdata_api_key)}


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
            "POST /api/search",
            "GET /api/search/history",
            "GET /api/search/{run_id}",
            "POST /api/url/search",
            "GET /api/url/search/{run_id}/report",
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
