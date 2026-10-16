"""
Public API URL search follows the same rules as the app: feature and platform
switches, URL validation, plan limits, monthly quota and token charges, and a
run record the organization can see. Before, POST /api/v1/search only queued
a job and skipped every check.

Run:  python -m pytest tests/test_public_api_search.py -q -p no:cacheprovider
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tests.conftest import seed_site_account

URL = "https://www.facebook.com/acmerealty"


class _NoopThread:
    started: list = []

    def __init__(self, *a, **kw):
        _NoopThread.started.append(a)

    def run(self):
        return None


@pytest.fixture
def env():
    _NoopThread.started = []
    with patch("app.admin.settings.is_maintenance_enabled", return_value=False), \
            patch("app.social.url_search.UrlSearchThread", _NoopThread):
        from app.main import app
        with TestClient(app) as client:
            from app.db.mongo import get_sync_db
            yield client, get_sync_db()


def _key(db, org_id, scopes=("search:create",)):
    from app.services.api_keys import create_api_key
    raw, _ = create_api_key(db, organization_id=org_id, name="CRM sync", scopes=list(scopes))
    return {"X-API-Key": raw}


def _org(db, email="api-owner@acme.example", tokens=None):
    _, org_id = seed_site_account(email, db=db, org_name=email.split("@")[1])
    if tokens is not None:
        db.token_balances.insert_one({"organization_id": org_id, "remaining": tokens, "used": 0})
    return org_id


def test_search_is_recorded_and_charged_like_the_app(env):
    client, db = env
    org = _org(db, tokens=100)
    r = client.post("/api/v1/search", json={"url": URL}, headers=_key(db, org))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["run_id"].startswith("URL_API_") and body["platform"] == "facebook"
    run = db.search_history.find_one({"run_id": body["run_id"]})
    assert run["organization_id"] == org and run["source"] == "api"
    assert run["created_by"].startswith("api_key:")
    assert db.token_balances.find_one({"organization_id": org})["remaining"] < 100
    assert len(_NoopThread.started) == 1


def test_no_tokens_means_no_search(env):
    client, db = env
    org = _org(db, tokens=0)
    r = client.post("/api/v1/search", json={"url": URL}, headers=_key(db, org))
    assert r.status_code == 402 and r.json()["detail"]["code"] == "TOKENS_EXHAUSTED"
    assert not _NoopThread.started and db.search_history.count_documents({}) == 0


def test_switched_off_feature_or_platform_is_refused(env):
    client, db = env
    org = _org(db)
    with patch("app.admin.settings.get_bool", side_effect=lambda k, *a: k != "features.url_search.enabled"):
        r = client.post("/api/v1/search", json={"url": URL}, headers=_key(db, org))
    assert r.status_code == 403 and r.json()["detail"]["errorType"] == "feature_disabled"
    with patch("app.admin.settings.is_platform_enabled", return_value=False):
        r = client.post("/api/v1/search", json={"url": URL}, headers=_key(db, org))
    assert r.status_code == 403 and r.json()["detail"]["errorType"] == "platform_disabled"
    assert not _NoopThread.started


def test_bad_url_and_missing_scope(env):
    client, db = env
    org = _org(db)
    r = client.post("/api/v1/search", json={"url": "https://www.facebook.com/groups/123"}, headers=_key(db, org))
    assert r.status_code == 422
    r = client.post("/api/v1/search", json={"url": URL}, headers=_key(db, org, scopes=("leads:read",)))
    assert r.status_code == 403


def test_status_is_only_visible_to_the_owning_organization(env):
    client, db = env
    org = _org(db)
    other = _org(db, email="someone@other.example")
    run_id = client.post("/api/v1/search", json={"url": URL}, headers=_key(db, org)).json()["run_id"]
    assert client.get(f"/api/v1/search/{run_id}", headers=_key(db, org, scopes=("search:read",))).status_code == 200
    assert client.get(f"/api/v1/search/{run_id}", headers=_key(db, other)).status_code == 404
    assert client.get(f"/api/v1/search/{run_id}", headers=_key(db, org, scopes=("leads:read",))).status_code == 403
