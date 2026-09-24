"""
Improvements round — main-agent integration (in-memory MongoDB only).

Proves:
  * the org's ``usage_warning_percent`` sets the token warning threshold;
  * a manual lead assignment (PATCH /api/leads/{id}) writes assignment_history
    and notifies the assignee, like bulk / automatic assignment;
  * a crashed URL-search worker emits the org job-failed notice.
"""
from unittest.mock import patch

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from tests.test_super_admin_portal2 import NOW, _cookie, _org, _user


@pytest.fixture
def env():
    with patch("app.admin.settings.is_maintenance_enabled", return_value=False):
        from app.main import app
        with TestClient(app) as client:
            from app.db.mongo import get_sync_db
            yield client, get_sync_db()


def test_usage_warning_percent_sets_threshold(env):
    from app.billing.tokens import allocate, consume
    _client, db = env
    org = _org(db, "Warn Org", settings={"usage_warning_percent": 50})
    allocate(org, 100, source="test", actor="test", reset=True)
    consume(org, 40, reason="search")
    assert db.notifications.count_documents({"type": "usage_threshold", "organization_id": org}) == 0
    consume(org, 15, reason="search")  # 55% >= the org's 50% (default would be 80%)
    n = db.notifications.find_one({"type": "usage_threshold", "organization_id": org})
    assert n and n["data"]["percent"] == 50


def test_manual_assignment_history_and_notice(env):
    client, db = env
    org = _org(db, "Assign Org", admin_portal_enabled=True)
    owner = _user(db, "own@example.com", org, role="owner")
    member = _user(db, "mem@example.com", org, role="member")
    lead = db.ai_comments.insert_one({"is_lead": True, "lead_status": "new", "organization_id": org,
                                      "user_id": owner, "created_by": "own@example.com",
                                      "created_at": NOW}).inserted_id
    cookie = _cookie({"user_id": owner, "email": "own@example.com", "name": "own", "scope": "site",
                      "organization_id": org, "org_role": "owner"})
    r = client.patch(f"/api/leads/{lead}", cookies=cookie, json={"assigned_user_id": member})
    assert r.status_code == 200, r.text
    doc = db.ai_comments.find_one({"_id": lead})
    assert doc["assigned_user_id"] == member
    hist = doc["assignment_history"][-1]
    assert hist["to_user_id"] == member and hist["method"] == "manual"
    assert db.notifications.find_one({"type": "lead_assigned", "user_id": member})
    # re-sending the same assignee adds no duplicate history / notice
    client.patch(f"/api/leads/{lead}", cookies=cookie, json={"assigned_user_id": member})
    assert len(db.ai_comments.find_one({"_id": lead})["assignment_history"]) == 1
    assert db.notifications.count_documents({"type": "lead_assigned", "user_id": member}) == 1


def test_crashed_search_worker_emits_job_failed():
    from app.social import url_search
    calls = []
    with patch.object(url_search, "run_url_search", side_effect=RuntimeError("boom")), \
            patch("app.events.notifications.notify_search_finished",
                  side_effect=lambda *a, **kw: calls.append((a, kw))):
        url_search.UrlSearchThread("URL_x", "https://facebook.com/x", 5,
                                   organization_id="org1", user_id="u1").run()
    assert calls and calls[0][1]["success"] is False and calls[0][0][1] == "URL_x"
