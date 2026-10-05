"""Test isolation: every test runs against a fresh in-memory MongoDB.

The app reads MONGO_URI from .env, which may point at a real (even
production) cluster. This autouse fixture replaces the cached Mongo clients
with a mongomock client shared by sync (pymongo) and async (motor) code, so
no test can ever read or write a real database.
"""
import mongomock
import mongomock_motor
import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "own_db: module wires its own in-memory MongoDB (skip the autouse one)")


# Website sign-up defaults to the self-serve free trial (demo settings →
# auto_approve). Most lifecycle tests exercise the approval workflow, which
# stays available when that setting is off, so tests start in approval mode;
# tests of the self-serve trial switch it on explicitly
# (update_demo_config({"auto_approve": True}, ...)).
from app.lifecycle import config as _lifecycle_config  # noqa: E402

REAL_MIGRATE_SELF_SERVE_TRIAL = _lifecycle_config.migrate_self_serve_trial
SEED_AUTO_APPROVE = _lifecycle_config.DEMO_CONFIG_SEED["auto_approve"]
SEED_DURATION_DAYS = _lifecycle_config.DEMO_CONFIG_SEED["duration_days"]


@pytest.fixture(autouse=True, scope="session")
def _demo_approval_mode():
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(_lifecycle_config.DEMO_CONFIG_SEED, "auto_approve", False)
        mp.setattr(_lifecycle_config, "migrate_self_serve_trial", lambda db: False)
        yield


@pytest.fixture(autouse=True)
def _in_memory_mongo(request, monkeypatch):
    if request.node.get_closest_marker("own_db"):
        yield None
        return
    sync_client = mongomock.MongoClient()
    async_client = mongomock_motor.AsyncMongoMockClient(mock_mongo_client=sync_client)
    monkeypatch.setattr("app.db.mongo.get_sync_client", lambda: sync_client)
    monkeypatch.setattr("app.db.mongo.get_async_client", lambda: async_client)
    # clear process-wide caches that would otherwise leak between tests
    try:
        from app.billing import plans
        plans.invalidate_plan_cache()
    except Exception:
        pass
    try:
        from app.lifecycle import config
        config.clear_cache()
    except Exception:
        pass
    try:
        from app.auth import permissions
        permissions.invalidate_permission_cache()
    except Exception:
        pass
    try:
        from app.admin import envvars
        envvars._CACHE.clear()
    except Exception:
        pass
    try:
        from app.auth import service
        service._login_attempts.clear()
    except Exception:
        pass
    try:
        # per-IP signup / password-reset throttles (TestClient is one IP)
        from app.api.routes import auth as auth_routes
        auth_routes._signup_attempts.clear()
        auth_routes._reset_attempts.clear()
    except Exception:
        pass
    try:
        from app.cms import service as cms
        cms.clear_cache()
        from app.api.routes import public_website
        public_website.reset_contact_rate_limit()
    except Exception:
        pass
    yield sync_client


TEST_SUPERADMIN_PASSWORD = "Test-SuperAdmin-Pass-2026"
_CREDENTIAL_FIELDS = ("superadmin_email", "superadmin_password", "panel_admin_email",
                      "panel_admin_password_hash", "admin_email", "admin_password_hash",
                      "gemini_api_key", "apify_api_token")


@pytest.fixture(autouse=True)
def _restore_credential_settings():
    """Tests may set the Super Admin (``as_superadmin``) or legacy credential
    settings; every test starts and ends with the process's real values."""
    from app.config import get_settings
    s = get_settings()
    saved = {k: getattr(s, k) for k in _CREDENTIAL_FIELDS}
    # tests never depend on the developer's own .env Super Admin, and never
    # call the real Gemini / Apify APIs with the developer's keys (a test
    # that needs them patches the network boundary explicitly)
    s.superadmin_email, s.superadmin_password = "", ""
    s.gemini_api_key, s.apify_api_token = "", ""
    try:
        from app.admin import envvars
        envvars._CACHE.clear()
    except Exception:
        pass
    yield
    for k, v in saved.items():
        setattr(s, k, v)
    try:
        from app.admin import envvars
        envvars._CACHE.clear()
    except Exception:
        pass


def as_superadmin(email: str, password: str = TEST_SUPERADMIN_PASSWORD) -> str:
    """Make ``email`` THE Super Admin exactly as production does it — through
    SUPERADMIN_EMAIL / SUPERADMIN_PASSWORD (a database role never grants it).
    Restored automatically after the test."""
    from app.config import get_settings
    s = get_settings()
    s.superadmin_email, s.superadmin_password = email, password
    try:
        from app.admin import envvars
        envvars._CACHE.clear()
    except Exception:
        pass
    return email.strip().lower()


def seed_site_account(email, *, org_status="active", role="owner", user_status="active",
                      org_name="Test Org", db=None):
    """Insert an active ``users`` record + organization + membership so that a
    site-scope login/session for ``email`` resolves to a real tenant (there is
    no default-organization fallback any more). Returns (user_id, org_id)."""
    import time
    if db is None:
        from app.db.mongo import get_sync_db
        db = get_sync_db()
    email = email.strip().lower()
    now = time.time()
    org_id = db["organizations"].insert_one({
        "name": org_name, "slug": org_name.lower().replace(" ", "-"),
        "status": org_status, "created_at": now}).inserted_id
    user_id = db["users"].insert_one({
        "email": email, "name": email.split("@")[0], "status": user_status,
        "default_organization_id": str(org_id), "created_at": now}).inserted_id
    db["organization_members"].insert_one({
        "organization_id": str(org_id), "user_id": str(user_id), "email": email,
        "role": role, "status": "active", "created_at": now})
    return str(user_id), str(org_id)


@pytest.fixture
def site_account():
    """Factory fixture: ``site_account(email, **kw)`` seeds a usable tenant."""
    return seed_site_account
