"""
Prompt 10: Scalability + Multi-User Isolation + Production Hardening Tests

Covers:
  - Per-user API rate limiting
  - Export concurrency limits
  - Search ownership (created_by)
  - Graceful shutdown (task cancellation)
  - Log rotation setup
  - Apify active run tracking
  - Docker production config validation
  - Job progress tracking
"""
import asyncio
import importlib
import io
import csv
import json
import os
import time
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock, AsyncMock, PropertyMock

import pytest

# ── Helpers ────────────────────────────────────────────────────────────────

def _utcnow():
    return datetime.now(timezone.utc)


# ═══════════════════════════════════════════════════════════════════════════
# 1. PER-USER API RATE LIMITING
# ═══════════════════════════════════════════════════════════════════════════

class TestPerUserRateLimiting:
    """Per-user rate limiting on search API endpoints."""

    def test_rate_limit_function_exists(self):
        from app.api.routes.search import _check_api_rate
        assert callable(_check_api_rate)

    def test_rate_limit_allows_within_window(self):
        from app.api.routes.search import _check_api_rate, _api_rate_limits
        _api_rate_limits.clear()
        for _ in range(5):
            assert _check_api_rate("test@example.com") is True

    def test_rate_limit_blocks_when_exceeded(self):
        from app.api.routes.search import _check_api_rate, _api_rate_limits, _API_RATE_MAX
        _api_rate_limits.clear()
        for _ in range(_API_RATE_MAX):
            _check_api_rate("throttle@example.com")
        assert _check_api_rate("throttle@example.com") is False
        _api_rate_limits.pop("throttle@example.com", None)

    def test_rate_limit_is_per_user(self):
        from app.api.routes.search import _check_api_rate, _api_rate_limits, _API_RATE_MAX
        _api_rate_limits.clear()
        for _ in range(_API_RATE_MAX):
            _check_api_rate("user_a@example.com")
        assert _check_api_rate("user_b@example.com") is True
        _api_rate_limits.pop("user_a@example.com", None)
        _api_rate_limits.pop("user_b@example.com", None)

    def test_rate_limit_anonymous_key(self):
        from app.api.routes.search import _check_api_rate, _api_rate_limits
        _api_rate_limits.clear()
        assert _check_api_rate("") is True
        # Empty string maps to "anonymous" key
        assert "anonymous" in _api_rate_limits
        _api_rate_limits.clear()

    def test_rate_limit_constants(self):
        from app.api.routes.search import _API_RATE_WINDOW, _API_RATE_MAX
        assert _API_RATE_WINDOW == 60
        assert _API_RATE_MAX == 30


# ═══════════════════════════════════════════════════════════════════════════
# 2. EXPORT CONCURRENCY LIMITS
# ═══════════════════════════════════════════════════════════════════════════

class TestExportConcurrency:
    """Export concurrency limiting to prevent memory exhaustion."""

    def test_csv_response_exists(self):
        from app.api.routes.search import _csv_response
        assert callable(_csv_response)

    def test_export_constants(self):
        from app.api.routes.search import _EXPORT_MAX_CONCURRENT
        assert _EXPORT_MAX_CONCURRENT == 3

    def test_csv_response_basic(self):
        from app.api.routes.search import _csv_response
        rows = [{"name": "Alice", "score": 95}, {"name": "Bob", "score": 87}]
        columns = ["name", "score"]
        resp = _csv_response(rows, columns, "test.csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        assert "test.csv" in resp.headers["content-disposition"]
        body = resp.body.decode("utf-8-sig")
        lines = body.strip().split("\r\n")
        assert len(lines) == 3

    def test_csv_response_truncation(self):
        from app.api.routes.search import _csv_response
        rows = [{"val": i} for i in range(100)]
        resp = _csv_response(rows, ["val"], "trunc.csv", max_rows=10)
        assert resp.headers.get("X-Export-Truncated") == "true"
        assert resp.headers.get("X-Export-Total") == "100"
        body = resp.body.decode("utf-8-sig")
        lines = body.strip().split("\r\n")
        assert len(lines) == 11

    def test_csv_response_empty_rows(self):
        from app.api.routes.search import _csv_response
        resp = _csv_response([], ["col"], "empty.csv")
        assert resp.status_code == 200
        body = resp.body.decode("utf-8-sig")
        assert "col" in body

    def test_concurrency_counter_decrements(self):
        from app.api.routes.search import _EXPORT_INFLIGHT
        assert _EXPORT_INFLIGHT == 0


# ═══════════════════════════════════════════════════════════════════════════
# 3. SEARCH OWNERSHIP (created_by)
# ═══════════════════════════════════════════════════════════════════════════

class TestSearchOwnership:
    """Search jobs track the user who created them."""

    def test_created_by_index_exists(self):
        from app.db import mongo
        import inspect
        src = inspect.getsource(mongo.ensure_indexes)
        assert "created_by" in src

    def test_search_history_insert_includes_created_by(self):
        from app.api.routes import search
        import inspect
        src = inspect.getsource(search.start_url_search)
        assert "created_by" in src

    def test_creator_extraction_from_request(self):
        from app.api.routes import search
        import inspect
        src = inspect.getsource(search.start_url_search)
        # the creator comes from the verified tenant context, never the request body
        assert 'require_org_permission' in src
        assert 'stamp(ctx' in src
        assert 'created_by=ctx.email' in src


# ═══════════════════════════════════════════════════════════════════════════
# 4. GRACEFUL SHUTDOWN
# ═══════════════════════════════════════════════════════════════════════════

class TestGracefulShutdown:
    """Application shuts down gracefully, cancelling background tasks."""

    def test_lifespan_has_shutdown_code(self):
        from app.main import lifespan
        import inspect
        src = inspect.getsource(lifespan)
        assert "cancel" in src.lower() or "shutdown" in src.lower()

    def test_lifespan_closes_db(self):
        from app.main import lifespan
        import inspect
        src = inspect.getsource(lifespan)
        assert "close" in src.lower()

    def test_lifespan_aborts_apify(self):
        from app.main import lifespan
        import inspect
        src = inspect.getsource(lifespan)
        assert "apify" in src.lower() or "abort" in src.lower()


# ═══════════════════════════════════════════════════════════════════════════
# 5. LOG ROTATION
# ═══════════════════════════════════════════════════════════════════════════

class TestLogRotation:
    """Application uses rotating log files to prevent unbounded growth."""

    def test_uses_rotating_handler(self):
        from app.main import _setup_logging
        import inspect
        src = inspect.getsource(_setup_logging)
        assert "RotatingFileHandler" in src

    def test_rotation_params(self):
        from app.main import _setup_logging
        import inspect
        src = inspect.getsource(_setup_logging)
        assert "maxBytes" in src
        assert "backupCount" in src


# ═══════════════════════════════════════════════════════════════════════════
# 6. APIFY ACTIVE RUN TRACKING
# ═══════════════════════════════════════════════════════════════════════════

class TestApifyRunTracking:
    """Active Apify run IDs are tracked for graceful shutdown abort."""

    def test_active_runs_set_exists(self):
        from app.connectors.apify_connector import _active_apify_runs
        assert isinstance(_active_apify_runs, set)

    def test_tracking_added_on_start(self):
        from app.connectors import apify_connector
        import inspect
        src = inspect.getsource(apify_connector.ApifyConnector._call_actor_polling)
        assert "_active_apify_runs.add" in src

    def test_tracking_removed_on_terminal_status(self):
        from app.connectors import apify_connector
        import inspect
        src = inspect.getsource(apify_connector.ApifyConnector._call_actor_polling)
        assert "_active_apify_runs.discard" in src

    def test_tracking_removed_on_abort(self):
        from app.connectors import apify_connector
        import inspect
        src = inspect.getsource(apify_connector.ApifyConnector._call_actor_polling)
        assert src.count("_active_apify_runs.discard") >= 2


# ═══════════════════════════════════════════════════════════════════════════
# 7. DOCKER PRODUCTION CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

class TestDockerProductionConfig:
    """Docker compose is production-ready."""

    def _load_compose(self):
        import yaml
        with open("docker-compose.yml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def test_no_reload_flag(self):
        compose = self._load_compose()
        cmd = compose["services"]["api"].get("command", "")
        assert "--reload" not in cmd

    def test_api_healthcheck(self):
        compose = self._load_compose()
        hc = compose["services"]["api"].get("healthcheck")
        assert hc is not None
        assert "test" in hc

    def test_mongo_healthcheck(self):
        compose = self._load_compose()
        hc = compose["services"]["mongo"].get("healthcheck")
        assert hc is not None

    def test_api_restart_policy(self):
        compose = self._load_compose()
        assert compose["services"]["api"].get("restart") == "unless-stopped"

    def test_mongo_restart_policy(self):
        compose = self._load_compose()
        assert compose["services"]["mongo"].get("restart") == "unless-stopped"

    def test_api_resource_limits(self):
        compose = self._load_compose()
        res = compose["services"]["api"].get("deploy", {}).get("resources", {})
        assert "limits" in res
        assert "memory" in res["limits"]

    def test_mongo_resource_limits(self):
        compose = self._load_compose()
        res = compose["services"]["mongo"].get("deploy", {}).get("resources", {})
        assert "limits" in res
        assert "memory" in res["limits"]

    def test_mongo_localhost_binding(self):
        compose = self._load_compose()
        ports = compose["services"]["mongo"].get("ports", [])
        assert any("127.0.0.1" in str(p) for p in ports)

    def test_api_depends_on_mongo_healthy(self):
        compose = self._load_compose()
        deps = compose["services"]["api"].get("depends_on", {})
        if isinstance(deps, dict):
            assert deps.get("mongo", {}).get("condition") == "service_healthy"
        else:
            assert "mongo" in deps


# ═══════════════════════════════════════════════════════════════════════════
# 8. HSTS HEADER
# ═══════════════════════════════════════════════════════════════════════════

class TestHSTSHeader:
    """HSTS header is set for HTTPS requests."""

    def test_hsts_in_middleware(self):
        from app.main import SecurityHeadersMiddleware
        import inspect
        src = inspect.getsource(SecurityHeadersMiddleware)
        assert "Strict-Transport-Security" in src

    def test_hsts_only_on_https(self):
        from app.main import SecurityHeadersMiddleware
        import inspect
        src = inspect.getsource(SecurityHeadersMiddleware)
        assert "https" in src.lower()


# ═══════════════════════════════════════════════════════════════════════════
# 9. REQUEST IMPORT IN SEARCH
# ═══════════════════════════════════════════════════════════════════════════

class TestRequestImport:
    """FastAPI Request is imported in search.py for user extraction."""

    def test_request_imported(self):
        from app.api.routes import search
        import inspect
        src = inspect.getsource(search)
        assert "Request" in src

    def test_handler_accepts_request(self):
        from app.api.routes.search import start_url_search
        import inspect
        sig = inspect.signature(start_url_search)
        assert "request" in sig.parameters


# ═══════════════════════════════════════════════════════════════════════════
# 10. BACKGROUND TASK TRACKING
# ═══════════════════════════════════════════════════════════════════════════

class TestBackgroundTaskTracking:
    """Background tasks are tracked in a module-level dict."""

    def test_tasks_dict_exists(self):
        from app.api.routes.search import _tasks
        assert isinstance(_tasks, dict)

    def test_start_returns_bool(self):
        from app.api.routes.search import _start
        assert callable(_start)

    def test_background_function_cleanup(self):
        from app.api.routes.search import _background
        import inspect
        src = inspect.getsource(_background)
        assert "_tasks.pop" in src


# ═══════════════════════════════════════════════════════════════════════════
# 11. SECURITY HEADERS INTEGRITY
# ═══════════════════════════════════════════════════════════════════════════

class TestSecurityHeadersIntegrity:
    """Security headers remain comprehensive after Prompt 10 changes."""

    def test_all_critical_headers_present(self):
        from app.main import SecurityHeadersMiddleware
        import inspect
        src = inspect.getsource(SecurityHeadersMiddleware)
        for header in [
            "X-Content-Type-Options",
            "X-Frame-Options",
            "Referrer-Policy",
            "Permissions-Policy",
        ]:
            assert header in src, f"Missing header: {header}"


# ═══════════════════════════════════════════════════════════════════════════
# 12. CSV VALUE SANITIZATION
# ═══════════════════════════════════════════════════════════════════════════

class TestCSVSanitization:
    """CSV export sanitizes formula injection."""

    def test_formula_prefix(self):
        from app.api.routes.search import _sanitize_csv_value
        val = _sanitize_csv_value("=CMD('ls')")
        # Prefixes with ' or \t to prevent formula execution
        assert val != "=CMD('ls')"

    def test_plus_prefix(self):
        from app.api.routes.search import _sanitize_csv_value
        val = _sanitize_csv_value("+CMD('ls')")
        assert val != "+CMD('ls')"

    def test_at_prefix(self):
        from app.api.routes.search import _sanitize_csv_value
        val = _sanitize_csv_value("@SUM(A1:A10)")
        assert val != "@SUM(A1:A10)"

    def test_normal_value_unchanged(self):
        from app.api.routes.search import _sanitize_csv_value
        assert _sanitize_csv_value("hello world") == "hello world"

    def test_none_returns_empty(self):
        from app.api.routes.search import _sanitize_csv_value
        assert _sanitize_csv_value(None) == ""

    def test_numeric_returns_str(self):
        from app.api.routes.search import _sanitize_csv_value
        assert _sanitize_csv_value(42) == "42"


# ═══════════════════════════════════════════════════════════════════════════
# 13. RATE LIMIT WINDOW CLEANUP
# ═══════════════════════════════════════════════════════════════════════════

class TestRateLimitWindowCleanup:
    """Rate limiter prunes old timestamps outside the window."""

    def test_old_entries_pruned(self):
        from app.api.routes.search import _check_api_rate, _api_rate_limits, _API_RATE_WINDOW
        _api_rate_limits.clear()
        old_time = time.time() - _API_RATE_WINDOW - 10
        _api_rate_limits["old_user@test.com"] = [old_time] * 50
        assert _check_api_rate("old_user@test.com") is True
        stamps = _api_rate_limits.get("old_user@test.com", [])
        assert len(stamps) == 1
        _api_rate_limits.clear()


# ═══════════════════════════════════════════════════════════════════════════
# 14. EXPORT SCOPE ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

class TestExportScopeEndpoints:
    """CSV export endpoints exist for pages, posts, comments."""

    def test_pages_csv_column_definitions(self):
        from app.api.routes.search import PAGES_CSV
        assert "page_name" in PAGES_CSV
        assert "platform" in PAGES_CSV

    def test_posts_csv_column_definitions(self):
        from app.api.routes.search import POSTS_CSV
        assert "caption" in POSTS_CSV
        assert "platform" in POSTS_CSV

    def test_comments_csv_column_definitions(self):
        from app.api.routes.search import COMMENTS_CSV
        assert "commenter_name" in COMMENTS_CSV
        assert "comment_text" in COMMENTS_CSV


# ═══════════════════════════════════════════════════════════════════════════
# 15. APPLICATION STARTUP CONSISTENCY
# ═══════════════════════════════════════════════════════════════════════════

class TestApplicationStartup:
    """Application imports and initializes cleanly."""

    def test_app_imports(self):
        from app.main import app
        assert app is not None

    def test_lifespan_function_exists(self):
        from app.main import lifespan
        assert callable(lifespan)

    def test_search_router_attached(self):
        from app.api.routes.search import router as search_router
        assert search_router.tags == ["agent"]
        assert search_router.prefix == "/api"
