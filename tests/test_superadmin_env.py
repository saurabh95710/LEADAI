"""
Super Admin from the environment (SUPERADMIN_EMAIL / SUPERADMIN_PASSWORD) —
in-memory MongoDB only.

Proves, over HTTP, with ONLY the Super Admin credentials configured (no
ADMIN_* / PANEL_ADMIN_* variables):
  * the Super Admin signs in at /login?superadmin=1 and reaches /superadmin
    and the Super Admin API; a wrong password is refused;
  * organization admins and users sign in from their database accounts and
    keep their portals; the env credentials never grant a site session;
  * SUPERADMIN_* cannot be edited, reset or replaced from the admin panel;
  * the startup migration copies a legacy ADMIN_PASSWORD_HASH onto the
    ADMIN_EMAIL database account only when it has no password of its own.
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.auth.crypto import hash_password
from app.config import get_settings
from tests.test_super_admin_portal2 import _org, _user

SUPER_EMAIL = "owner@leadai.example"
SUPER_PASSWORD = "Perm@nent-Pass-2026"
_FIELDS = ("superadmin_email", "superadmin_password", "admin_email", "admin_password_hash",
           "panel_admin_email", "panel_admin_password_hash")


@pytest.fixture
def only_superadmin_env():
    """Settings as on a Render deploy that sets only SUPERADMIN_*."""
    from app.admin.envvars import clear_cache
    s = get_settings()
    saved = {k: getattr(s, k) for k in _FIELDS}
    for k in _FIELDS:
        setattr(s, k, "")
    s.superadmin_email, s.superadmin_password = SUPER_EMAIL, SUPER_PASSWORD
    clear_cache()
    yield s
    for k, v in saved.items():
        setattr(s, k, v)
    clear_cache()


@pytest.fixture
def client(only_superadmin_env):
    with patch("app.admin.settings.is_maintenance_enabled", return_value=False):
        from app.main import app
        with TestClient(app) as c:
            yield c


def _login(client, email, password, scope):
    return client.post("/api/auth/login", json={"email": email, "password": password,
                                                "scope": scope})


def test_superadmin_login_and_portal_with_only_env_credentials(client):
    assert _login(client, SUPER_EMAIL, "wrong-password", "admin").status_code == 401
    r = _login(client, SUPER_EMAIL.upper(), SUPER_PASSWORD, "admin")
    assert r.status_code == 200, r.text
    assert client.get("/superadmin", follow_redirects=False).status_code == 200
    assert client.get("/api/super-admin/dashboard").status_code == 200
    assert client.get("/api/admin/users").status_code == 200  # platform console too


def test_org_admin_and_user_login_from_database(client):
    from app.db.mongo import get_sync_db
    db = get_sync_db()
    org = _org(db, "Tenant Org", admin_portal_enabled=True)
    _user(db, "orgadmin@example.com", org, role="admin")
    _user(db, "member@example.com", org, role="member")
    # _user (test_super_admin_portal2) seeds bcrypt("Str0ngPass!")
    r = _login(client, "orgadmin@example.com", "Str0ngPass!", "site")
    assert r.status_code == 200, r.text
    assert client.get("/org-admin", follow_redirects=False).status_code == 200
    assert client.get("/api/org-admin/overview").status_code == 200
    client.cookies.clear()
    assert _login(client, "member@example.com", "Str0ngPass!", "site").status_code == 200
    assert client.get("/api/me/summary").status_code == 200
    assert client.get("/api/super-admin/dashboard").status_code in (401, 403)
    client.cookies.clear()
    # the Super Admin credentials never open a customer (site) session
    assert _login(client, SUPER_EMAIL, SUPER_PASSWORD, "site").status_code == 401


def test_superadmin_vars_cannot_be_changed_from_the_app(client):
    from app.auth.service import ENV_UNLOCK_COOKIE, build_env_unlock_value
    assert _login(client, SUPER_EMAIL, SUPER_PASSWORD, "admin").status_code == 200
    client.cookies.set(ENV_UNLOCK_COOKIE, build_env_unlock_value())
    for name in ("SUPERADMIN_EMAIL", "SUPERADMIN_PASSWORD"):
        assert client.put(f"/api/admin/env/{name}", json={"value": "x@example.com"}).status_code == 409
        assert client.delete(f"/api/admin/env/{name}").status_code == 409
    r = client.post("/api/admin/env/password", json={"new_password": "Another-Pass-2026!"})
    assert r.status_code == 409
    listed = {v["name"]: v for v in client.get("/api/admin/env").json()["vars"]}
    assert listed["SUPERADMIN_PASSWORD"]["managed_elsewhere"] is True
    assert SUPER_PASSWORD not in str(listed) and "2026" not in listed["SUPERADMIN_PASSWORD"]["masked"]
    # still signs in with the environment password
    client.cookies.clear()
    assert _login(client, SUPER_EMAIL, SUPER_PASSWORD, "admin").status_code == 200


def test_startup_migration_copies_legacy_site_password(only_superadmin_env):
    from app.db.migration import migrate_to_multi_tenant
    from app.db.mongo import get_sync_db
    import app.db.migration as mig
    db = get_sync_db()
    legacy_hash = hash_password("Legacy-Site-2026")
    db.users.insert_one({"email": "site-owner@example.com", "status": "active"})
    db.users.insert_one({"email": "has-own@example.com", "status": "active",
                         "password_hash": "keep-me"})
    with patch.object(mig, "settings", only_superadmin_env):
        only_superadmin_env.admin_email = "site-owner@example.com"
        only_superadmin_env.admin_password_hash = legacy_hash
        report = migrate_to_multi_tenant(db)
        assert report.get("owner_password_migrated") is True
        assert db.users.find_one({"email": "site-owner@example.com"})["password_hash"] == legacy_hash
        # an account that already has a password is never overwritten
        only_superadmin_env.admin_email = "has-own@example.com"
        migrate_to_multi_tenant(db)
        assert db.users.find_one({"email": "has-own@example.com"})["password_hash"] == "keep-me"


def test_app_starts_without_any_credentials(caplog):
    """No SUPERADMIN_* at all: startup still succeeds (health works) and
    logs a clear error instead of crashing."""
    from app.admin.envvars import clear_cache
    s = get_settings()
    saved = {k: getattr(s, k) for k in _FIELDS}
    for k in _FIELDS:
        setattr(s, k, "")
    clear_cache()
    try:
        with patch("app.admin.settings.is_maintenance_enabled", return_value=False):
            from app.main import app
            with caplog.at_level("ERROR"), TestClient(app) as c:
                assert c.get("/health").status_code == 200
        assert any("No Super Admin configured" in r.getMessage() for r in caplog.records)
    finally:
        for k, v in saved.items():
            setattr(s, k, v)
        clear_cache()
