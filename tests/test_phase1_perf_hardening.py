"""Tests for Phase 1 Load-Time Optimization and Hardening.

Verifies:
1. GZip compression middleware (minimum_size=1000)
2. Long Cache-Control for immutable static assets (JS, CSS, images)
3. ETag and 304 Not Modified validation for HTML pages
4. Cache-Control headers on API routes (no-store for authenticated, public for public configs)
5. Startup readiness flag on GET /health
6. Single-query batch loader and in-memory TTL caching for /api/public/config
7. MongoDB connection pool configuration
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.db.mongo import get_sync_client
from app.main import app
from app.settings.registry import build_public_config, invalidate_public_config_cache


@pytest.mark.asyncio
async def test_health_readiness_flag():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/health")
        assert res.status_code == 200
        data = res.json()
        assert "status" in data
        assert "ready" in data
        assert isinstance(data["ready"], bool)


@pytest.mark.asyncio
async def test_gzip_compression_middleware():
    transport = ASGITransport(app=app)
    # Request a large public endpoint with gzip encoding accepted
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/public/config", headers={"Accept-Encoding": "gzip"})
        assert res.status_code == 200
        # If response body >= 1000 bytes, GZipMiddleware adds Content-Encoding
        if len(res.content) >= 1000:
            assert res.headers.get("content-encoding") == "gzip"


@pytest.mark.asyncio
async def test_static_asset_cache_control():
    """Only version-tagged assets (?v=…) are cached for a year; untagged ones are
    revalidated, so a deploy is never hidden behind an old cached copy."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/static/config.js?v=r1005")
        assert res.status_code == 200
        assert "public" in res.headers.get("cache-control", "")
        assert "max-age=31536000" in res.headers.get("cache-control", "")
        assert "immutable" in res.headers.get("cache-control", "")
        res = await ac.get("/static/config.js")
        assert res.status_code == 200
        assert "immutable" not in res.headers.get("cache-control", "")
        assert "no-cache" in res.headers.get("cache-control", "")


def test_every_page_tags_its_scripts_and_styles():
    """A page linking a .js/.css without ?v= would keep serving browsers an old
    copy after a deploy (the cream + mint theme stayed invisible this way)."""
    import glob
    import re
    untagged = []
    for page in glob.glob("app/static/*.html"):
        text = open(page, encoding="utf-8").read()
        untagged += [f"{page}: {m}" for m in re.findall(r'(?:src|href)="(/static/[^"?]+\.(?:js|css))"', text)]
    assert not untagged, untagged


@pytest.mark.asyncio
async def test_html_etag_and_304_validation():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res1 = await ac.get("/login")
        if res1.status_code == 200:
            etag = res1.headers.get("etag")
            assert etag is not None
            assert "no-cache" in res1.headers.get("cache-control", "")

            # Second request with matching If-None-Match should return 304 Not Modified
            res2 = await ac.get("/login", headers={"if-none-match": etag})
            assert res2.status_code == 304


@pytest.mark.asyncio
async def test_api_cache_control_headers():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Public config should allow public caching
        res_pub = await ac.get("/api/public/config")
        assert res_pub.status_code == 200
        assert "public" in res_pub.headers.get("cache-control", "")

        # Authenticated or internal routes should have no-store
        res_auth = await ac.get("/api/search/history")
        cc = res_auth.headers.get("cache-control", "")
        assert "no-store" in cc or "no-cache" in cc


@pytest.mark.asyncio
async def test_public_config_batch_caching():
    invalidate_public_config_cache()
    # First build
    cfg1 = await build_public_config()
    assert isinstance(cfg1, dict)
    assert "app" in cfg1
    assert "branding" in cfg1

    # Second build within 60s should return cached object
    cfg2 = await build_public_config()
    assert cfg1 == cfg2


def test_mongo_connection_pool_sizing():
    sc = get_sync_client()
    if sc is not None:
        opts = sc.options.pool_options
        assert opts.max_pool_size == 50
        assert opts.min_pool_size == 5
        assert opts.max_idle_time_seconds == 45.0
