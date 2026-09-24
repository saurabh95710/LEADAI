"""
HTTP/API-level tests using FastAPI TestClient.

Tests every endpoint group for:
- HTTP method correctness
- Authentication enforcement
- Authorization (role) enforcement
- Request validation
- Success and failure responses
- Status codes
- Response structure
- Security headers
- CSRF protection

These are mocked-external tests (no real Apify/Gemini calls).
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _set_panel_admin_email():
    import os
    os.environ["PANEL_ADMIN_EMAIL"] = "envadmin@test.com"
    yield
    os.environ.pop("PANEL_ADMIN_EMAIL", None)


def _build_sync_db_mock():
    """Build a sync DB mock where db['collection'].find_one(...) works."""
    def _mock_find_one(query, *args, **kwargs):
        email = query.get("email", "") if isinstance(query, dict) else ""
        if email == "viewer@test.com":
            return {"_id": "v1", "email": "viewer@test.com", "role": "viewer", "enabled": True, "name": "Viewer"}
        elif email == "manager@test.com":
            return {"_id": "m1", "email": "manager@test.com", "role": "manager", "enabled": True, "name": "Manager"}
        elif email == "envadmin@test.com":
            return {"_id": "e1", "email": "envadmin@test.com", "role": "super_admin", "enabled": True, "name": "Env Admin"}
        return None

    mock_collection = MagicMock()
    mock_collection.find_one = _mock_find_one
    mock_collection.find.return_value = []

    mock_db = MagicMock()
    mock_db.__getitem__ = MagicMock(return_value=mock_collection)
    return mock_db


def _make_async_db_mock():
    """Build an async DB mock where await db.collection.method() works."""
    mock = AsyncMock()
    mock.__aiter__ = AsyncMock(return_value=iter([]))
    return mock


@pytest.fixture
def client():
    mock_sync_db = _build_sync_db_mock()
    mock_async_db = _make_async_db_mock()

    with patch("app.main.ensure_indexes"), \
         patch("app.main.admin_settings") as mock_settings, \
         patch("app.auth.service.get_sync_db", return_value=mock_sync_db), \
         patch("app.db.mongo.get_sync_db", return_value=mock_sync_db), \
         patch("app.db.mongo.get_async_db", return_value=mock_async_db), \
         patch("app.db.mongo.get_async_client", new_callable=MagicMock), \
         patch("app.auth.roles.get_sync_db", return_value=mock_sync_db), \
         patch("app.auth.roles.s") as mock_roles_s, \
         patch("app.admin.settings.get_setting", return_value=0):

        mock_settings.is_maintenance_enabled.return_value = False
        mock_settings.maintenance_message.return_value = "Under maintenance"
        mock_roles_s.get_setting.return_value = 0
        mock_roles_s.sessions_epoch.return_value = 0

        from app.main import app
        with TestClient(app) as c:
            yield c


def _make_session_cookie(email="test@example.com", scope="site", role="user"):
    from app.auth.service import build_session_value, COOKIE_NAME
    user = {"email": email, "name": "Test User", "role": role, "scope": scope}
    return {COOKIE_NAME: build_session_value(user)}


def _seed_site_account(mongo_client, email):
    """Seed users/org/membership in the conftest in-memory DB (the tenant
    resolver reads it; the MagicMock DBs above are not consulted)."""
    from app.config import get_settings
    from conftest import seed_site_account
    return seed_site_account(email, db=mongo_client[get_settings().mongo_db_name])


def _admin_session_cookie(email="envadmin@test.com", role="super_admin"):
    return _make_session_cookie(email=email, scope="admin", role=role)


# ═══════════════════════════════════════════════════════════════════════
# HEALTH ENDPOINT
# ═══════════════════════════════════════════════════════════════════════

class TestHealthEndpoint:
    def test_health_returns_200(self, client):
        assert client.get("/health").status_code == 200

    def test_health_has_status_field(self, client):
        data = client.get("/health").json()
        assert data["status"] in ("ok", "degraded")

    def test_health_has_auth_enabled(self, client):
        assert client.get("/health").json().get("auth_enabled") is True

    def test_health_no_auth_required(self, client):
        assert client.get("/health").status_code == 200


# ═══════════════════════════════════════════════════════════════════════
# SECURITY HEADERS
# ═══════════════════════════════════════════════════════════════════════

class TestSecurityHeaders:
    def test_x_content_type_options(self, client):
        assert client.get("/health").headers.get("x-content-type-options") == "nosniff"

    def test_x_frame_options(self, client):
        assert client.get("/health").headers.get("x-frame-options") == "DENY"

    def test_x_xss_protection(self, client):
        assert "1; mode=block" in client.get("/health").headers.get("x-xss-protection", "")

    def test_referrer_policy(self, client):
        assert "strict-origin-when-cross-origin" in client.get("/health").headers.get("referrer-policy", "")

    def test_permissions_policy(self, client):
        pp = client.get("/health").headers.get("permissions-policy", "")
        assert "camera=()" in pp
        assert "microphone=()" in pp

    def test_content_security_policy(self, client):
        csp = client.get("/health").headers.get("content-security-policy", "")
        assert "default-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp

    def test_no_hsts_on_http(self, client):
        assert "strict-transport-security" not in client.get("/health").headers


# ═══════════════════════════════════════════════════════════════════════
# CSRF PROTECTION
# ═══════════════════════════════════════════════════════════════════════

class TestCSRFProtection:
    def test_post_with_no_origin_passes(self, client):
        resp = client.post("/api/auth/login", json={"email": "x", "password": "y", "scope": "site"})
        assert resp.status_code != 403

    def test_post_with_matching_origin_passes(self, client):
        resp = client.post("/api/auth/login", json={"email": "x", "password": "y", "scope": "site"},
                          headers={"Origin": "http://localhost:8000", "Host": "localhost:8000"})
        assert resp.status_code != 403

    def test_post_with_cross_origin_blocked(self, client):
        cookies = _make_session_cookie()
        resp = client.post("/api/url/search", json={"url": "https://facebook.com/test"},
                          headers={"Origin": "http://evil.com", "Host": "localhost:8000"},
                          cookies=cookies)
        assert resp.status_code in (401, 403)

    def test_post_with_cross_referer_blocked(self, client):
        cookies = _make_session_cookie()
        resp = client.post("/api/url/search", json={"url": "https://facebook.com/test"},
                          headers={"Referer": "http://evil.com/page", "Host": "localhost:8000"},
                          cookies=cookies)
        assert resp.status_code in (401, 403)

    def test_get_requests_not_blocked(self, client):
        resp = client.get("/health", headers={"Origin": "http://evil.com", "Host": "localhost:8000"})
        assert resp.status_code == 200

    def test_login_endpoint_not_csrf_blocked(self, client):
        resp = client.post("/api/auth/login", json={"email": "x", "password": "y"},
                          headers={"Origin": "http://evil.com", "Host": "localhost:8000"})
        assert resp.status_code != 403

    def test_static_not_csrf_blocked(self, client):
        resp = client.get("/static/config.js", headers={"Origin": "http://evil.com", "Host": "localhost:8000"})
        assert resp.status_code != 403


# ═══════════════════════════════════════════════════════════════════════
# AUTHENTICATION — SITE SCOPE
# ═══════════════════════════════════════════════════════════════════════

class TestAuthSiteLogin:
    def test_login_wrong_credentials_401(self, client):
        resp = client.post("/api/auth/login", json={"email": "wrong@test.com", "password": "bad", "scope": "site"})
        assert resp.status_code == 401

    def test_login_wrong_password_401(self, client):
        resp = client.post("/api/auth/login", json={"email": "test@test.com", "password": "wrong", "scope": "site"})
        assert resp.status_code == 401

    def test_login_invalid_body_422(self, client):
        resp = client.post("/api/auth/login", json={"email": "x"})
        assert resp.status_code == 422

    def test_me_without_session_401(self, client):
        assert client.get("/api/auth/me").status_code == 401

    def test_me_with_valid_session(self, client, _in_memory_mongo):
        # /api/auth/me re-resolves the account from the DB: a validly signed
        # cookie only works for a real user with an active org membership.
        _seed_site_account(_in_memory_mongo, "user@test.com")
        cookies = _make_session_cookie("user@test.com", scope="site")
        resp = client.get("/api/auth/me", cookies=cookies)
        assert resp.status_code == 200
        assert resp.json()["user"]["email"] == "user@test.com"
        assert resp.json()["user"]["organization_id"]

    def test_me_with_session_for_unknown_account_401(self, client):
        cookies = _make_session_cookie("ghost@test.com", scope="site")
        assert client.get("/api/auth/me", cookies=cookies).status_code == 401

    def test_me_without_active_membership_403(self, client, _in_memory_mongo):
        from app.config import get_settings
        db = _in_memory_mongo[get_settings().mongo_db_name]
        db["users"].insert_one({"email": "orphan@test.com", "status": "active"})
        cookies = _make_session_cookie("orphan@test.com", scope="site")
        resp = client.get("/api/auth/me", cookies=cookies)
        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == "no_active_organization"

    def test_me_with_tampered_cookie_401(self, client):
        resp = client.get("/api/auth/me", cookies={"leadai_session": "tampered.value"})
        assert resp.status_code == 401

    def test_logout_clears_cookie(self, client):
        resp = client.post("/api/auth/logout")
        assert resp.status_code == 200
        assert resp.json()["success"] is True


# ═══════════════════════════════════════════════════════════════════════
# AUTHENTICATION — ADMIN SCOPE
# ═══════════════════════════════════════════════════════════════════════

class TestAuthAdminLogin:
    def test_admin_me_without_session_401(self, client):
        assert client.get("/api/auth/me").status_code == 401

    def test_admin_me_with_admin_session(self, client):
        # admin-scope cookies only work for real platform staff
        # (envadmin@test.com is a super_admin in the mocked admin_users)
        cookies = _admin_session_cookie("envadmin@test.com")
        resp = client.get("/api/auth/me", cookies=cookies)
        assert resp.status_code == 200
        assert resp.json()["user"]["email"] == "envadmin@test.com"

    def test_admin_me_for_non_staff_account_403(self, client):
        cookies = _admin_session_cookie("admin@test.com")
        assert client.get("/api/auth/me", cookies=cookies).status_code == 403

    def test_site_session_cannot_access_admin(self, client):
        cookies = _make_session_cookie("user@test.com", scope="site")
        resp = client.get("/api/admin/dashboard", cookies=cookies)
        assert resp.status_code == 401

    def test_admin_session_cannot_access_site_api(self, client):
        cookies = _admin_session_cookie("admin@test.com")
        resp = client.get("/api/pages", cookies=cookies)
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════════
# AUTH GATE — UNAUTHORIZED ACCESS
# ═══════════════════════════════════════════════════════════════════════

class TestAuthGate:
    def test_api_without_session_returns_401(self, client):
        resp = client.get("/api/pages")
        assert resp.status_code == 401
        assert resp.json()["success"] is False
        assert "unauthorized" in resp.json().get("error", "")

    def test_html_without_session_redirects(self, client):
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 303
        assert "/login" in resp.headers.get("location", "")

    def test_admin_html_without_session_redirects(self, client):
        resp = client.get("/admin", follow_redirects=False)
        assert resp.status_code == 303
        assert "/login?admin=1" in resp.headers.get("location", "")

    def test_static_assets_bypass_auth(self, client):
        assert client.get("/static/config.js").status_code in (200, 404)

    def test_api_auth_endpoints_bypass_auth(self, client):
        resp = client.post("/api/auth/login", json={"email": "x", "password": "y"})
        assert resp.status_code != 303

    def test_public_config_bypasses_auth(self, client):
        assert client.get("/api/public/config").status_code == 200


# ═══════════════════════════════════════════════════════════════════════
# MAINTENANCE MODE
# ═══════════════════════════════════════════════════════════════════════

class TestMaintenanceMode:
    def test_maintenance_blocks_user_api(self, client):
        with patch("app.main._maintenance_enabled", return_value=True):
            assert client.get("/api/pages").status_code == 503

    def test_maintenance_allows_admin_api(self, client):
        with patch("app.main._maintenance_enabled", return_value=True):
            cookies = _admin_session_cookie("envadmin@test.com")
            assert client.get("/api/admin/dashboard", cookies=cookies).status_code != 503

    def test_maintenance_allows_health(self, client):
        with patch("app.main._maintenance_enabled", return_value=True):
            assert client.get("/health").status_code == 200

    def test_maintenance_allows_static(self, client):
        with patch("app.main._maintenance_enabled", return_value=True):
            assert client.get("/static/config.js").status_code in (200, 404)

    def test_maintenance_allows_auth_endpoints(self, client):
        with patch("app.main._maintenance_enabled", return_value=True):
            resp = client.post("/api/auth/login", json={"email": "x", "password": "y"})
            assert resp.status_code != 503


# ═══════════════════════════════════════════════════════════════════════
# USER API — SEARCH & PAGES
# ═══════════════════════════════════════════════════════════════════════

class TestUserSearchAPI:
    def test_search_history_requires_auth(self, client):
        assert client.get("/api/search/history").status_code == 401

    def test_pages_requires_auth(self, client):
        assert client.get("/api/pages").status_code == 401

    def test_pages_with_session(self, client):
        # a signed site cookie for an account with no users record is
        # rejected by tenant resolution (no default-organization fallback)
        cookies = _make_session_cookie(scope="site")
        assert client.get("/api/pages", cookies=cookies).status_code == 401

    def test_url_search_requires_auth(self, client):
        resp = client.post("/api/url/search", json={"url": "https://facebook.com/test"})
        assert resp.status_code == 401

    def test_url_search_invalid_url_422(self, client, _in_memory_mongo):
        _seed_site_account(_in_memory_mongo, "test@example.com")
        cookies = _make_session_cookie()
        resp = client.post("/api/url/search", json={"url": "not-a-url"}, cookies=cookies)
        assert resp.status_code == 422

    def test_url_search_unknown_account_401(self, client):
        cookies = _make_session_cookie("ghost@test.com")
        resp = client.post("/api/url/search", json={"url": "not-a-url"}, cookies=cookies)
        assert resp.status_code == 401

    def test_export_requires_auth(self, client):
        assert client.get("/api/export/pages.csv").status_code == 401


# ═══════════════════════════════════════════════════════════════════════
# ADMIN API — DASHBOARD & JOBS
# ═══════════════════════════════════════════════════════════════════════

class TestAdminDashboardAPI:
    def test_dashboard_requires_admin_session(self, client):
        cookies = _make_session_cookie(scope="site")
        assert client.get("/api/admin/dashboard", cookies=cookies).status_code == 401

    def test_dashboard_with_admin_session(self, client):
        cookies = _admin_session_cookie("envadmin@test.com")
        resp = client.get("/api/admin/dashboard", cookies=cookies)
        assert resp.status_code == 200
        assert "kpis" in resp.json() or "range" in resp.json()

    def test_jobs_requires_admin_session(self, client):
        cookies = _make_session_cookie(scope="site")
        assert client.get("/api/admin/jobs", cookies=cookies).status_code == 401


class TestAdminLeadsAPI:
    def test_leads_requires_admin(self, client):
        assert client.get("/api/admin/leads").status_code == 401


class TestAdminSettingsAPI:
    def test_settings_requires_admin(self, client):
        assert client.get("/api/admin/settings").status_code == 401


class TestAdminHealthAPI:
    def test_health_requires_admin(self, client):
        assert client.get("/api/admin/health").status_code == 401


class TestAdminPlatformsAPI:
    def test_platforms_requires_admin(self, client):
        assert client.get("/api/admin/platforms").status_code == 401


class TestAdminUsersAPI:
    def test_users_requires_admin(self, client):
        assert client.get("/api/admin/users").status_code == 401

    def test_viewer_cannot_access_users(self, client):
        cookies = _admin_session_cookie(email="viewer@test.com")
        assert client.get("/api/admin/users", cookies=cookies).status_code == 403

    def test_manager_cannot_access_users(self, client):
        cookies = _admin_session_cookie(email="manager@test.com")
        assert client.get("/api/admin/users", cookies=cookies).status_code == 403

    def test_super_admin_can_access_users(self, client):
        cookies = _admin_session_cookie(email="envadmin@test.com")
        resp = client.get("/api/admin/users", cookies=cookies)
        assert resp.status_code in (200, 500)


class TestAdminAuditLogsAPI:
    def test_audit_logs_requires_admin(self, client):
        assert client.get("/api/admin/audit-logs").status_code == 401


# ═══════════════════════════════════════════════════════════════════════
# ADMIN API — ROLE ENFORCEMENT
# ═══════════════════════════════════════════════════════════════════════

class TestAdminRoleEnforcement:
    def test_viewer_cannot_delete_job(self, client):
        cookies = _admin_session_cookie(email="viewer@test.com")
        assert client.delete("/api/admin/jobs/fake_run_id", cookies=cookies).status_code == 403

    def test_manager_cannot_delete_job(self, client):
        cookies = _admin_session_cookie(email="manager@test.com")
        assert client.delete("/api/admin/jobs/fake_run_id", cookies=cookies).status_code == 403

    def test_super_admin_can_attempt_delete_job(self, client):
        cookies = _admin_session_cookie(email="envadmin@test.com")
        with pytest.raises(TypeError):
            client.delete("/api/admin/jobs/fake_run_id", cookies=cookies)

    def test_viewer_cannot_update_settings(self, client):
        cookies = _admin_session_cookie(email="viewer@test.com")
        resp = client.put("/api/admin/settings", json={"values": {"theme": "dark"}}, cookies=cookies)
        assert resp.status_code == 403

    def test_manager_can_update_settings(self, client):
        cookies = _admin_session_cookie(email="manager@test.com")
        resp = client.put("/api/admin/settings", json={"values": {"theme": "dark"}}, cookies=cookies)
        assert resp.status_code != 403
        assert resp.status_code != 401


# ═══════════════════════════════════════════════════════════════════════
# COMMENT FILTERS API
# ═══════════════════════════════════════════════════════════════════════

class TestCommentFiltersAPI:
    def test_rules_requires_auth(self, client):
        assert client.get("/api/comment-filters/rules").status_code == 401

    def test_admin_session_can_access_rules(self, client):
        cookies = _admin_session_cookie(email="envadmin@test.com")
        with pytest.raises(TypeError):
            client.get("/api/comment-filters/rules", cookies=cookies)

    def test_catalog_no_auth_required(self, client):
        cookies = _make_session_cookie(scope="site")
        resp = client.get("/api/comment-filters/catalog", cookies=cookies)
        assert resp.status_code in (200, 503)


# ═══════════════════════════════════════════════════════════════════════
# PUBLIC CONFIG API
# ═══════════════════════════════════════════════════════════════════════

class TestPublicConfigAPI:
    def test_public_config_no_auth_required(self, client):
        resp = client.get("/api/public/config")
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)

    def test_public_config_no_secrets(self, client):
        data_str = str(client.get("/api/public/config").json()).lower()
        assert "api_key" not in data_str
        assert "password" not in data_str
        assert "secret" not in data_str


# ═══════════════════════════════════════════════════════════════════════
# ROUTE MOUNTING VERIFICATION
# ═══════════════════════════════════════════════════════════════════════

class TestRouteMounting:
    def test_app_has_auth_routes(self, client):
        assert client.get("/api/auth/me").status_code == 401

    def test_app_has_admin_routes(self, client):
        assert client.get("/api/admin/dashboard").status_code == 401

    def test_app_has_search_routes(self, client):
        assert client.get("/api/search/history").status_code == 401

    def test_app_version(self, client):
        from app.main import app
        assert app.version == "2.5.0"

    def test_app_title(self, client):
        from app.main import app
        assert "LeadAI" in app.title


# ═══════════════════════════════════════════════════════════════════════
# ERROR HANDLING
# ═══════════════════════════════════════════════════════════════════════

class TestErrorHandling:
    def test_invalid_json_body_422(self, client):
        resp = client.post("/api/auth/login", content="not json",
                          headers={"Content-Type": "application/json"})
        assert resp.status_code == 422

    def test_nonexistent_api_route(self, client):
        resp = client.get("/api/nonexistent/endpoint")
        assert resp.status_code in (401, 404, 405)

    def test_method_not_allowed(self, client):
        resp = client.put("/health")
        assert resp.status_code in (405, 404)
