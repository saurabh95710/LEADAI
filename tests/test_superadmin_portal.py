"""
Regression tests for the Super Admin portal routing/auth bug.

Original bug: GET /superadmin (and /superadmin/) redirected to plain
/login instead of the super-admin sign-in flow when the visitor had no
session. Root cause was exact-path matching in the auth_gate middleware
plus no /superadmin/ route — trailing-slash variants fell through to the
generic site-scope check.

These tests pin the intended behavior without disabling or weakening
authentication.
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
    def _mock_find_one(query, *args, **kwargs):
        email = query.get("email", "") if isinstance(query, dict) else ""
        if email == "viewer@test.com":
            return {"_id": "v1", "email": "viewer@test.com", "role": "viewer",
                    "enabled": True, "name": "Viewer"}
        elif email == "manager@test.com":
            return {"_id": "m1", "email": "manager@test.com", "role": "manager",
                    "enabled": True, "name": "Manager"}
        elif email == "envadmin@test.com":
            return {"_id": "e1", "email": "envadmin@test.com",
                    "role": "super_admin", "enabled": True, "name": "Env Admin"}
        return None

    mock_collection = MagicMock()
    mock_collection.find_one = _mock_find_one
    mock_collection.find.return_value = []

    mock_db = MagicMock()
    mock_db.__getitem__ = MagicMock(return_value=mock_collection)
    return mock_db


class _AsyncEmptyCursor:
    """Minimal async-iterable stand-in for a Motor aggregate/find cursor."""
    def __aiter__(self):
        async def _gen():
            return
            yield  # pragma: no cover — makes this an async generator
        return _gen()


def _make_async_db_mock():
    mock = AsyncMock()
    mock.__aiter__ = AsyncMock(return_value=iter([]))
    for name in (
        "users", "organizations", "subscriptions", "invoices",
        "search_history", "ai_comments", "plans", "audit_logs",
    ):
        coll = AsyncMock()
        coll.count_documents = AsyncMock(return_value=0)
        coll.aggregate = MagicMock(return_value=_AsyncEmptyCursor())
        coll.find = MagicMock(return_value=[])
        coll.find_one = MagicMock(return_value=None)
        coll.insert_one = AsyncMock(return_value=MagicMock(inserted_id="x"))
        setattr(mock, name, coll)
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
         patch("app.api.routes.super_admin.get_async_db", return_value=mock_async_db), \
         patch("app.api.routes.super_admin.get_sync_db", return_value=mock_sync_db), \
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


def _cookie(email="envadmin@test.com", scope="admin", role="super_admin"):
    from app.auth.service import build_session_value, COOKIE_NAME
    user = {"email": email, "name": "Test", "role": role, "scope": scope}
    return {COOKIE_NAME: build_session_value(user)}


def _super_cookie():
    return _cookie("envadmin@test.com", "admin", "super_admin")


def _viewer_cookie():
    return _cookie("viewer@test.com", "admin", "viewer")


def _site_cookie():
    return _cookie("user@example.com", "site", "user")


def _location(resp):
    return resp.headers.get("location", "")


# ═══════════════════════════════════════════════════════════════════════
# Route exists / original bug regression
# ═══════════════════════════════════════════════════════════════════════

class TestSuperAdminRouteExists:
    def test_superadmin_route_registered(self, client):
        from app.main import app
        paths = {getattr(r, "path", None) for r in app.routes}
        assert "/superadmin" in paths
        assert "/superadmin/" in paths

    def test_no_cookie_redirects_to_superadmin_login_not_plain_login(self, client):
        """THE ORIGINAL BUG: unauthenticated /superadmin must not go to /login."""
        for path in ("/superadmin", "/superadmin/"):
            resp = client.get(path, follow_redirects=False)
            assert resp.status_code in (302, 303, 307), path
            loc = _location(resp)
            assert loc != "/login", f"{path} redirected to plain /login"
            assert "superadmin" in loc, f"{path} -> {loc} missing superadmin hint"
            assert loc.startswith("/login"), f"{path} -> {loc} not a login page"

    def test_trailing_slash_same_as_canonical(self, client):
        a = client.get("/superadmin", follow_redirects=False)
        b = client.get("/superadmin/", follow_redirects=False)
        assert a.status_code == b.status_code
        assert _location(a) == _location(b)


# ═══════════════════════════════════════════════════════════════════════
# Authentication required
# ═══════════════════════════════════════════════════════════════════════

class TestSuperAdminRequiresAuth:
    def test_no_session_redirects_to_login(self, client):
        for path in ("/superadmin", "/superadmin/"):
            resp = client.get(path, follow_redirects=False)
            assert resp.status_code in (302, 303, 307)
            assert "login" in _location(resp)

    def test_invalid_cookie_redirects_to_login(self, client):
        from app.auth.service import COOKIE_NAME
        resp = client.get("/superadmin",
                          cookies={COOKIE_NAME: "not-a-valid.signature"},
                          follow_redirects=False)
        assert resp.status_code in (302, 303, 307)
        assert "login" in _location(resp)

    def test_expired_cookie_redirects_to_login(self, client):
        import base64 as b64
        import json
        import time
        from app.auth.service import build_session_value, COOKIE_NAME, _sign

        payload = {
            "v": 1,
            "user": {"email": "envadmin@test.com", "name": "Env",
                     "role": "super_admin", "scope": "admin"},
            "iat": int(time.time()) - 30 * 86400,
            "exp": int(time.time()) - 1,
        }
        b = b64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode()
        ).rstrip(b"=").decode()
        value = f"{b}.{_sign(b)}"
        resp = client.get("/superadmin", cookies={COOKIE_NAME: value},
                          follow_redirects=False)
        assert resp.status_code in (302, 303, 307)
        assert "login" in _location(resp)

    def test_api_without_session_returns_401_json(self, client):
        resp = client.get("/api/super-admin/dashboard")
        assert resp.status_code == 401
        body = resp.json()
        assert body.get("error") in ("unauthorized", "not_authenticated") or \
               "sign" in str(body.get("message", "")).lower()


# ═══════════════════════════════════════════════════════════════════════
# Authorization (super_admin role required)
# ═══════════════════════════════════════════════════════════════════════

class TestSuperAdminAuthorization:
    def test_super_admin_can_access_page(self, client):
        for path in ("/superadmin", "/superadmin/"):
            resp = client.get(path, cookies=_super_cookie(),
                              follow_redirects=False)
            assert resp.status_code == 200, path

    def test_viewer_admin_cannot_access_page(self, client):
        resp = client.get("/superadmin", cookies=_viewer_cookie(),
                          follow_redirects=False)
        assert resp.status_code in (302, 303, 307)
        loc = _location(resp)
        assert loc != "/login"
        assert loc.rstrip("/") == "/admin" or "admin" in loc

    def test_site_session_cannot_access_page(self, client):
        resp = client.get("/superadmin", cookies=_site_cookie(),
                          follow_redirects=False)
        assert resp.status_code in (302, 303, 307)
        loc = _location(resp)
        assert "login" in loc

    def test_api_with_super_admin_cookie_passes_auth_gate(self, client):
        resp = client.get("/api/super-admin/dashboard",
                          cookies=_super_cookie())
        # Auth gate must let super admin through (200 or 503 if DB down).
        assert resp.status_code != 401
        assert resp.status_code != 403

    def test_api_with_viewer_cookie_forbidden(self, client):
        resp = client.get("/api/super-admin/dashboard",
                          cookies=_viewer_cookie())
        assert resp.status_code in (401, 403)

    def test_api_with_site_cookie_unauthorized(self, client):
        resp = client.get("/api/super-admin/dashboard",
                          cookies=_site_cookie())
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════════
# Logout / session revocation
# ═══════════════════════════════════════════════════════════════════════

class TestSuperAdminLogout:
    def test_after_logout_redirects_to_login(self, client):
        cookies = _super_cookie()
        logout = client.post("/api/auth/logout", cookies=cookies)
        assert logout.status_code in (200, 204)
        # Follow-up with cleared cookie jar
        resp = client.get("/superadmin", follow_redirects=False)
        assert resp.status_code in (302, 303, 307)
        assert "login" in _location(resp)

    def test_cleared_cookie_blocks_superadmin(self, client):
        client.cookies.clear()
        resp = client.get("/superadmin", follow_redirects=False)
        assert resp.status_code in (302, 303, 307)
        assert "login" in _location(resp)


# ═══════════════════════════════════════════════════════════════════════
# Other portals unaffected (login / admin / user)
# ═══════════════════════════════════════════════════════════════════════

class TestOtherPortalsUnaffected:
    def test_login_page_public(self, client):
        resp = client.get("/login", follow_redirects=False)
        assert resp.status_code == 200

    def test_admin_trailing_slash_not_plain_login(self, client):
        resp = client.get("/admin/", follow_redirects=False)
        assert resp.status_code in (302, 303, 307)
        loc = _location(resp)
        assert loc != "/login"
        assert "admin" in loc

    def test_admin_requires_admin_scope(self, client):
        resp = client.get("/admin", cookies=_site_cookie(),
                          follow_redirects=False)
        assert resp.status_code in (302, 303, 307)
        assert "admin" in _location(resp)

    def test_admin_with_admin_session_200(self, client):
        resp = client.get("/admin", cookies=_viewer_cookie(),
                          follow_redirects=False)
        assert resp.status_code == 200

    def test_root_requires_site_or_admin_session(self, client):
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code in (302, 303, 307)
        assert "login" in _location(resp)

    def test_health_still_public(self, client):
        assert client.get("/health").status_code == 200

    def test_admin_api_unaffected_for_admin_users(self, client):
        resp = client.get("/api/admin/dashboard",
                          cookies=_viewer_cookie())
        assert resp.status_code != 401


# ═══════════════════════════════════════════════════════════════════════
# OpenAPI / docs not blocked by auth gate
# ═══════════════════════════════════════════════════════════════════════

class TestOpenAPISchema:
    def test_app_openapi_generates(self):
        from app.main import app
        schema = app.openapi()
        assert schema
        assert "/api/super-admin/dashboard" in schema.get("paths", {})

    def test_openapi_json_reachable_without_session(self, client):
        resp = client.get("/openapi.json", follow_redirects=False)
        assert resp.status_code == 200

    def test_docs_route_not_redirect_when_enabled(self, client):
        # enable_api_docs is False by default → 404, but must NOT be a
        # login redirect (auth gate must skip these paths).
        resp = client.get("/docs", follow_redirects=False)
        assert resp.status_code != 303
        if resp.status_code in (302, 303, 307):
            assert "login" not in _location(resp)
