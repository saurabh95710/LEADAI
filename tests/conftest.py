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
