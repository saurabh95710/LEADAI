"""QA area B: the platform-staff console — /api/admin/* (app/api/routes/admin.py)
and the AI model registry /api/admin/ai/* (app/api/routes/admin_ai.py).

Runs on the in-memory MongoDB of tests/conftest.py; Apify is patched at the
network boundary (apify_client.ApifyClient) and Gemini is never reachable
(conftest blanks the key, so the AI test runs the rule stage only).

Roles (app/auth/roles.py): viewer = read-only, manager = viewer + settings /
tests / jobs, super_admin = SUPERADMIN_EMAIL only (user management, security,
destructive operations, billing grants).
"""
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.auth.crypto import hash_password
from app.auth.service import COOKIE_NAME, build_session_value, create_tracked_session

NOW = datetime.now(timezone.utc)
SUPER_EMAIL = "root@platform.test"


# ═══════════════════════════════════════════════════════════════════════════
# fixtures / helpers
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def env():
    with patch("app.admin.settings.is_maintenance_enabled", return_value=False):
        from app.main import app
        # server errors come back as 500 responses, so a bug shows as a status code
        with TestClient(app, raise_server_exceptions=False) as client:
            from app.db.mongo import get_sync_db
            db = get_sync_db()
            for coll in ("organizations", "users", "organization_members", "subscriptions",
                         "user_sessions", "audit_logs", "ai_models", "ai_comments",
                         "search_history", "system_settings", "temporary_entitlements"):
                db[coll].delete_many({})
            from app.admin import settings as s
            s.clear_settings_cache()
            yield client, db


def _user(db, email, org_id=None, role="member", platform_role=None, status="active"):
    uid = str(db.users.insert_one({
        "email": email, "name": email.split("@")[0], "password_hash": hash_password("Str0ngPass!"),
        "status": status, "is_platform_admin": bool(platform_role), "platform_role": platform_role,
        "default_organization_id": org_id, "created_at": NOW}).inserted_id)
    if org_id:
        db.organization_members.insert_one({"organization_id": org_id, "user_id": uid, "role": role,
                                            "status": "active", "joined_at": NOW})
    return uid


def _cookie(claims):
    return {COOKIE_NAME: build_session_value(create_tracked_session(claims))}


def _super(db):
    from tests.conftest import as_superadmin
    as_superadmin(SUPER_EMAIL)
    found = db.users.find_one({"email": SUPER_EMAIL})
    uid = str(found["_id"]) if found else _user(db, SUPER_EMAIL, platform_role="super_admin")
    return _cookie({"user_id": uid, "email": SUPER_EMAIL, "name": "Root",
                    "scope": "admin", "role": "super_admin"})


def _staff(db, role="viewer"):
    email = f"{role}@platform.test"
    if not db.users.find_one({"email": email}):
        _user(db, email, platform_role=role)
    uid = str(db.users.find_one({"email": email})["_id"])
    return _cookie({"user_id": uid, "email": email, "name": role, "scope": "admin", "role": role})


def _site_owner(db):
    org = str(db.organizations.insert_one({"name": "Acme", "slug": "acme", "status": "active",
                                           "created_at": NOW}).inserted_id)
    uid = _user(db, "owner@acme.test", org, "owner")
    return _cookie({"user_id": uid, "email": "owner@acme.test", "name": "o", "scope": "site",
                    "organization_id": org, "org_role": "owner"})


def _call(client, method, path, cookies=None, json=None, params=None):
    client.cookies.clear()
    return client.request(method, path, cookies=cookies or {}, json=json, params=params)


def _audit(db, action):
    return db.audit_logs.find_one({"action": action}, sort=[("at", -1)])


def _setting(db, key):
    doc = db.system_settings.find_one({"_id": key})
    return doc.get("value") if doc else None


def _lead(db, **kw):
    doc = {"comment_text": "Interested, what's the price?", "commenter_name": "Asha",
           "platform": "facebook", "is_lead": True, "lead_status": "new", "lead_score": 70,
           "lead_quality": "high", "page_name": "Acme Store", "analyzed_at": NOW,
           "created_at": NOW, "organization_id": "org-x", "comment_ref": str(ObjectId())}
    doc.update(kw)
    return db.ai_comments.insert_one(doc).inserted_id


def _fake_apify(info=None, error=None):
    """apify_client.ApifyClient stand-in: client.actor(id).get() -> info / raise."""
    client = MagicMock()
    actor = MagicMock()
    if error is not None:
        actor.get.side_effect = error
    else:
        actor.get.return_value = info
    client.actor.return_value = actor
    return MagicMock(return_value=client), client


# ═══════════════════════════════════════════════════════════════════════════
# role matrix: anonymous / site session / viewer / manager
# ═══════════════════════════════════════════════════════════════════════════

OID = "5f0000000000000000000001"

VIEWER_READS = [
    ("GET", "/api/admin/actors", None), ("GET", "/api/admin/ai", None),
    ("GET", "/api/admin/analytics", None), ("GET", "/api/admin/apify", None),
    ("GET", "/api/admin/comment-intelligence", None), ("GET", "/api/admin/comments", None),
    ("GET", "/api/admin/database", None), ("GET", "/api/admin/failed-jobs", None),
    ("GET", "/api/admin/features", None), ("GET", "/api/admin/limits", None),
    ("GET", "/api/admin/logs", None), ("GET", "/api/admin/logs/stats", None),
    ("GET", "/api/admin/pages", None), ("GET", "/api/admin/posts", None),
    ("GET", "/api/admin/scoring", None), ("GET", "/api/admin/ai/models", None),
]

MANAGER_WRITES = [
    ("PATCH", f"/api/admin/leads/{OID}", {"status": "contacted"}),
    ("POST", "/api/admin/leads/bulk", {"action": "delete", "ids": [OID]}),
    ("POST", "/api/admin/actors/test", {"key": "actor.facebook.pages"}),
    ("POST", "/api/admin/ai/test", {"text": "price?"}),
    ("POST", "/api/admin/apify/test", None),
    ("POST", "/api/admin/apify/token", {"token": "apify_api_0123456789"}),
    ("POST", "/api/admin/jobs/RUN1/cancel", None),
    ("POST", "/api/admin/jobs/RUN1/retry", None),
    ("POST", "/api/admin/platforms/facebook/actor", {"actor_id": "x/y"}),
    ("POST", "/api/admin/platforms/facebook/toggle", None),
    ("PUT", "/api/admin/ai", {"ai.enabled": False}),
    ("PUT", "/api/admin/comment-intelligence", {"ci.detect_phone": False}),
    ("PUT", "/api/admin/features", {"features.exports.enabled": False}),
    ("PUT", "/api/admin/limits", {"limits.max_posts_cap": 5}),
    ("PUT", "/api/admin/scoring", {"scoring.hot_min": 90}),
    ("GET", "/api/admin/logs/download", None),
    ("POST", "/api/admin/ai/models", {"_id": "m1", "model_name": "m1"}),
    ("PUT", "/api/admin/ai/models/m1", {"display_name": "x"}),
]

SUPER_ONLY = [
    ("DELETE", "/api/admin/apify/token", None),
    ("POST", f"/api/admin/organizations/{OID}/grant-credits", {"metric": "searches", "credits": 5}),
    ("POST", f"/api/admin/organizations/{OID}/grant-feature", {"feature": "csv_export"}),
    ("POST", "/api/admin/security/revoke-sessions", None),
    ("POST", f"/api/admin/subscriptions/{OID}/extend-trial", {"extra_days": 3}),
    ("POST", f"/api/admin/users/{OID}/activate", None),
    ("DELETE", "/api/admin/ai/models/m1", None),
]

ALL_ROUTES = VIEWER_READS + MANAGER_WRITES + SUPER_ONLY + [
    ("GET", f"/api/admin/leads/{OID}", None),
    ("GET", "/api/admin/ai/models/m1", None),
    ("POST", "/api/admin/ai/playground", {"model_id": "m1", "raw_input": "hi"}),
]


class TestRoleMatrix:
    @pytest.mark.parametrize("method,path,body", ALL_ROUTES)
    def test_anonymous_and_site_sessions_are_refused(self, env, method, path, body):
        client, db = env
        r = _call(client, method, path, None, body)
        assert r.status_code == 401, (path, r.text)
        assert r.json()["error"] == "unauthorized"
        r = _call(client, method, path, _site_owner(db), body)
        assert r.status_code == 401, (path, r.text)

    @pytest.mark.parametrize("method,path,body", VIEWER_READS)
    def test_viewer_can_read(self, env, method, path, body):
        client, db = env
        r = _call(client, method, path, _staff(db, "viewer"), body)
        assert r.status_code == 200, (path, r.text)
        assert isinstance(r.json(), dict)

    @pytest.mark.parametrize("method,path,body", MANAGER_WRITES + SUPER_ONLY)
    def test_viewer_cannot_write(self, env, method, path, body):
        client, db = env
        db.ai_models.insert_one({"_id": "m1", "model_name": "m1"})
        settings_before = list(db.system_settings.find({}))
        r = _call(client, method, path, _staff(db, "viewer"), body)
        assert r.status_code == 403, (path, r.text)
        assert "manager" in r.json()["detail"] or "super_admin" in r.json()["detail"]
        # nothing was written
        assert list(db.system_settings.find({})) == settings_before
        assert db.ai_models.find_one({"_id": "m1"}) == {"_id": "m1", "model_name": "m1"}
        assert db.temporary_entitlements.count_documents({}) == 0

    @pytest.mark.parametrize("method,path,body", SUPER_ONLY)
    def test_manager_cannot_do_super_admin_actions(self, env, method, path, body):
        client, db = env
        db.ai_models.insert_one({"_id": "m1", "model_name": "m1"})
        r = _call(client, method, path, _staff(db, "manager"), body)
        assert r.status_code == 403, (path, r.text)
        assert r.json()["detail"] == "Requires role 'super_admin' or higher"
        assert db.ai_models.count_documents({"_id": "m1"}) == 1
        assert db.temporary_entitlements.count_documents({}) == 0
        assert _setting(db, "security.session_epoch") is None

    def test_database_super_admin_record_is_capped_at_manager(self, env):
        """A DB account carrying platform_role=super_admin is NOT the Super Admin."""
        client, db = env
        uid = _user(db, "fake-root@platform.test", platform_role="super_admin")
        c = _cookie({"user_id": uid, "email": "fake-root@platform.test", "scope": "admin",
                     "role": "super_admin"})
        r = _call(client, "POST", "/api/admin/security/revoke-sessions", c)
        assert r.status_code == 403
        assert _call(client, "PUT", "/api/admin/limits", c, {"limits.max_posts_cap": 7}).status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# actors
# ═══════════════════════════════════════════════════════════════════════════

class TestActors:
    def test_list_reports_overrides(self, env):
        client, db = env
        from app.admin import settings as s
        s.set_setting("actor.facebook.posts", "acme/custom-posts", by="ops@platform.test")
        r = _call(client, "GET", "/api/admin/actors", _staff(db, "viewer"))
        assert r.status_code == 200
        items = {a["key"]: a for a in r.json()["actors"]}
        assert set(items) == {k for k in s.SETTING_DEFAULTS if k.startswith("actor.")}
        assert [a["key"] for a in r.json()["actors"]] == sorted(items)
        posts = items["actor.facebook.posts"]
        assert posts["value"] == "acme/custom-posts" and posts["overridden"] is True
        assert posts["platform"] == "facebook" and posts["default"] == "apify/facebook-posts-scraper"
        assert posts["updated_at"] is not None
        pages = items["actor.facebook.pages"]
        assert pages["value"] == "apify/facebook-pages-scraper" and pages["overridden"] is False

    def test_actor_test_calls_apify_with_the_configured_actor(self, env):
        client, db = env
        from app.admin import settings as s
        s.set_setting("apify.token", "apify_api_secret_token_1234")
        s.set_setting("actor.facebook.posts", "acme/custom-posts")
        factory, apify = _fake_apify({"title": "Custom posts"})
        with patch("apify_client.ApifyClient", factory):
            r = _call(client, "POST", "/api/admin/actors/test", _staff(db, "manager"),
                      {"key": "actor.facebook.posts"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body == {"key": "actor.facebook.posts", "actor_id": "acme/custom-posts", "ok": True,
                        "actor": "acme/custom-posts", "title": "Custom posts"}
        factory.assert_called_once_with("apify_api_secret_token_1234")
        apify.actor.assert_called_once_with("acme/custom-posts")
        row = _audit(db, "actor.test")
        assert row["success"] is True and row["actor_email"] == "manager@platform.test"
        assert row["details"] == {"key": "actor.facebook.posts", "actor": "acme/custom-posts"}

    def test_actor_test_without_token_and_unknown_key(self, env):
        client, db = env
        m = _staff(db, "manager")
        factory, _ = _fake_apify({"title": "x"})
        with patch("apify_client.ApifyClient", factory):
            r = _call(client, "POST", "/api/admin/actors/test", m, {"key": "actor.facebook.pages"})
            assert r.status_code == 200
            assert r.json()["ok"] is False and r.json()["error"] == "No Apify token configured"
            assert factory.call_count == 0
            for bad in ({"key": "limits.max_posts_cap"}, {"key": "actor.nope"}, {}):
                r = _call(client, "POST", "/api/admin/actors/test", m, bad)
                assert r.status_code == 400 and r.json()["detail"] == "Unknown actor key", bad
        assert _audit(db, "actor.test")["success"] is False


# ═══════════════════════════════════════════════════════════════════════════
# AI / Gemini settings
# ═══════════════════════════════════════════════════════════════════════════

class TestAiSettings:
    def test_get_ai_counts_and_settings(self, env):
        client, db = env
        _lead(db)
        _lead(db, is_lead=False, analyzed_at=NOW - timedelta(days=400))
        r = _call(client, "GET", "/api/admin/ai", _staff(db, "viewer"))
        assert r.status_code == 200
        body = r.json()
        assert body["counts"]["analyzed"] == 2 and body["counts"]["leads"] == 1
        # this_month uses $expr/$convert, which mongomock does not implement
        # (admin._count swallows the error), so only its type is checked here
        assert isinstance(body["counts"]["this_month"], int)
        assert body["settings"]["ai.enabled"] is True and "ai.temperature" in body["settings"]
        assert all(k.startswith("ai.") for k in body["settings"])
        assert body["gemini_key_configured"] is False

    def test_put_ai_persists_coerced_values_and_audits(self, env):
        client, db = env
        r = _call(client, "PUT", "/api/admin/ai", _staff(db, "manager"),
                  {"ai.enabled": "false", "ai.temperature": "0.4", "limits.max_posts_cap": 1})
        assert r.status_code == 200, r.text
        assert r.json() == {"success": True, "changed": ["ai.enabled", "ai.temperature"]}
        assert _setting(db, "ai.enabled") is False
        assert _setting(db, "ai.temperature") == 0.4
        assert _setting(db, "limits.max_posts_cap") is None          # foreign key ignored
        row = _audit(db, "ai.update")
        assert row["details"] == {"keys": ["ai.enabled", "ai.temperature"]}
        assert row["actor_email"] == "manager@platform.test"
        got = _call(client, "GET", "/api/admin/ai", _staff(db, "viewer")).json()
        assert got["settings"]["ai.enabled"] is False and got["settings"]["ai.temperature"] == 0.4

    def test_put_ai_without_valid_keys_is_rejected(self, env):
        client, db = env
        r = _call(client, "PUT", "/api/admin/ai", _staff(db, "manager"), {"bogus": 1})
        assert r.status_code == 400 and r.json()["detail"] == "No valid ai keys"
        assert db.system_settings.count_documents({}) == 0
        assert _audit(db, "ai.update") is None

    def test_ai_test_runs_rule_pipeline_without_gemini(self, env):
        client, db = env
        m = _staff(db, "manager")
        assert _call(client, "POST", "/api/admin/ai/test", m, {"text": "   "}).status_code == 400
        text = "I want to buy this, what is the price? Call me on 9876543210"
        r = _call(client, "POST", "/api/admin/ai/test", m, {"text": text, "author": "Ravi"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["sample_text"] == text
        assert body["analyzed_by"] == "rules"              # no Gemini key in tests
        assert body["is_useful"] is True
        assert isinstance(body["lead_score"], (int, float)) and 0 <= body["lead_score"] <= 100
        assert isinstance(body["signal_score"], (int, float))
        assert isinstance(body["signals"], (list, dict))


# ═══════════════════════════════════════════════════════════════════════════
# analytics
# ═══════════════════════════════════════════════════════════════════════════

class TestAnalytics:
    def test_totals_series_and_platform_breakdown(self, env):
        client, db = env
        db.search_history.insert_many([
            {"run_id": "a", "platform": "facebook", "status": "completed", "created_at": NOW},
            {"run_id": "b", "platform": "facebook", "status": "error", "created_at": NOW},
            {"run_id": "c", "platform": "instagram", "status": "completed", "created_at": NOW},
            {"run_id": "old", "platform": "facebook", "status": "error",
             "created_at": NOW - timedelta(days=60)},
        ])
        _lead(db, platform="facebook", lead_score=85, lead_status="contacted")
        _lead(db, platform="instagram", lead_score=30, lead_quality="low")
        _lead(db, platform="facebook", analyzed_at=NOW - timedelta(days=60))
        r = _call(client, "GET", "/api/admin/analytics", _staff(db, "viewer"), params={"days": 14})
        assert r.status_code == 200, r.text
        body = r.json()
        t = body["totals"]
        assert (t["jobs"], t["completed"], t["failed"]) == (3, 2, 1)
        assert t["success_rate"] == 66.7
        assert t["leads"] == 2 and t["analyzed"] == 2
        assert body["job_statuses"] == {"completed": 2, "error": 1}
        assert body["leads_by_platform"] == {"facebook": 1, "instagram": 1}
        assert body["score_distribution"] == {"80–100": 1, "20–39": 1}
        assert body["top_pages"] == [{"page": "Acme Store", "leads": 2}]
        today = NOW.strftime("%Y-%m-%d")
        assert body["jobs_series"] == [{"date": today, "jobs": 3, "failed": 1}]
        assert body["leads_series"] == [{"date": today, "leads": 2}]
        perf = {p["platform"]: p for p in body["platform_perf"]}
        assert perf["facebook"]["runs"] == 2 and perf["facebook"]["success_rate"] == 50.0
        assert perf["instagram"]["runs"] == 1 and perf["instagram"]["leads"] == 1
        assert perf["youtube"]["runs"] == 0 and perf["youtube"]["success_rate"] is None
        assert body["lead_statuses"]["contacted"] == 1 and body["lead_statuses"]["new"] == 1

    def test_platform_and_explicit_date_range_filters(self, env):
        client, db = env
        db.search_history.insert_many([
            {"run_id": "a", "platform": "facebook", "status": "completed",
             "created_at": datetime(2026, 3, 5, 12, tzinfo=timezone.utc)},
            {"run_id": "b", "platform": "instagram", "status": "completed",
             "created_at": datetime(2026, 3, 6, 12, tzinfo=timezone.utc)},
            {"run_id": "c", "platform": "facebook", "status": "completed",
             "created_at": datetime(2026, 4, 1, tzinfo=timezone.utc)},
        ])
        r = _call(client, "GET", "/api/admin/analytics", _staff(db, "viewer"),
                  params={"from_date": "2026-03-01", "to_date": "2026-03-31", "platform": "facebook"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["totals"]["jobs"] == 1
        assert body["range"]["from"].startswith("2026-03-01") and body["range"]["to"].startswith("2026-03-31")
        assert body["range"]["days"] == 30
        assert body["jobs_series"] == [{"date": "2026-03-05", "jobs": 1, "failed": 0}]


# ═══════════════════════════════════════════════════════════════════════════
# Apify token / connection test
# ═══════════════════════════════════════════════════════════════════════════

class TestApify:
    def test_status_never_returns_the_token(self, env):
        client, db = env
        v = _staff(db, "viewer")
        r = _call(client, "GET", "/api/admin/apify", v)
        assert r.status_code == 200
        assert r.json() == {"token_configured": False, "token_hint": "", "env_token_configured": False,
                            "last_test_ok": None, "last_test_at": None}
        from app.admin import settings as s
        s.set_setting("apify.token", "apify_api_SECRETVALUE_9876")
        r = _call(client, "GET", "/api/admin/apify", v)
        assert r.json()["token_configured"] is True and r.json()["token_hint"] == "••••9876"
        assert "SECRETVALUE" not in r.text

    def test_connection_test_records_result(self, env):
        client, db = env
        from app.admin import settings as s
        s.set_setting("apify.token", "apify_api_secret_token_1234")
        factory, apify = _fake_apify({"title": "Facebook Pages Scraper"})
        with patch("apify_client.ApifyClient", factory):
            r = _call(client, "POST", "/api/admin/apify/test", _staff(db, "manager"))
        assert r.status_code == 200
        assert r.json() == {"ok": True, "actor": "apify/facebook-pages-scraper",
                            "title": "Facebook Pages Scraper"}
        apify.actor.assert_called_once_with("apify/facebook-pages-scraper")
        assert _setting(db, "apify.last_test_ok") is True
        assert _setting(db, "apify.last_test_at")
        row = _audit(db, "apify.test")
        assert row["success"] is True and row["details"] == {"ok": True}
        status = _call(client, "GET", "/api/admin/apify", _staff(db, "viewer")).json()
        assert status["last_test_ok"] is True

    def test_connection_test_maps_a_rejected_token(self, env):
        client, db = env
        from app.admin import settings as s
        s.set_setting("apify.token", "apify_api_secret_token_1234")
        err = Exception("Unauthorized")
        err.status_code = 401
        factory, _ = _fake_apify(error=err)
        with patch("apify_client.ApifyClient", factory):
            r = _call(client, "POST", "/api/admin/apify/test", _staff(db, "manager"))
        assert r.status_code == 200
        assert r.json()["ok"] is False and "rejected the token (401)" in r.json()["error"]
        assert _setting(db, "apify.last_test_ok") is False
        assert _audit(db, "apify.test")["success"] is False

    def test_set_token_is_encrypted_at_rest_and_never_audited(self, env):
        client, db = env
        token = "apify_api_brandnewtoken_5555"
        factory, _ = _fake_apify({"title": "ok"})
        with patch("apify_client.ApifyClient", factory):
            r = _call(client, "POST", "/api/admin/apify/token", _staff(db, "manager"), {"token": f"  {token} "})
        assert r.status_code == 200, r.text
        assert r.json() == {"success": True, "test": {"ok": True, "actor": "apify/facebook-pages-scraper",
                                                      "title": "ok"}}
        factory.assert_called_once_with(token)
        stored = _setting(db, "apify.token")
        assert stored and stored != token and stored.startswith("fernet:")
        from app.admin import settings as s
        assert s.get_apify_token() == token
        row = _audit(db, "apify.token.update")
        assert row["details"] == {"ok": True} and token not in str(row)
        assert token not in r.text

    def test_set_token_validates_length(self, env):
        client, db = env
        r = _call(client, "POST", "/api/admin/apify/token", _staff(db, "manager"), {"token": "short"})
        assert r.status_code == 422
        assert _setting(db, "apify.token") is None

    def test_super_admin_clears_the_override(self, env):
        """clear_apify_token() does ``await s.delete_setting(...)`` but
        delete_setting is synchronous (returns a bool): the row is deleted,
        then ``await True`` raises TypeError -> 500 and no audit entry."""
        client, db = env
        from app.admin import settings as s
        s.set_setting("apify.token", "apify_api_secret_token_1234")
        r = _call(client, "DELETE", "/api/admin/apify/token", _super(db))
        assert r.status_code == 200, r.text                  # FAILS: 500
        assert r.json()["success"] is True and "env token still applies" in r.json()["message"]
        assert db.system_settings.find_one({"_id": "apify.token"}) is None
        assert s.get_apify_token() == ""
        row = _audit(db, "apify.token.clear")
        assert row and row["actor_email"] == SUPER_EMAIL


# ═══════════════════════════════════════════════════════════════════════════
# settings groups: limits, scoring, comment intelligence, features
# ═══════════════════════════════════════════════════════════════════════════

class TestSettingGroups:
    def test_limits_read_and_write(self, env):
        client, db = env
        db.facebook_pages.insert_many([{"page_name": "a", "facebook_url": "https://fb.com/a"},
                                       {"page_name": "b", "facebook_url": "https://fb.com/b"}])
        _lead(db)
        r = _call(client, "GET", "/api/admin/limits", _staff(db, "viewer"))
        assert r.status_code == 200
        body = r.json()
        assert body["usage"] == {"pages": 2, "posts": 0, "comments": 0, "leads": 1}
        assert body["settings"]["limits.max_posts_cap"] == 100
        assert body["settings"]["cost.stop_on_limit"] is True
        assert all(k.startswith(("limits.", "cost.")) for k in body["settings"])
        r = _call(client, "PUT", "/api/admin/limits", _staff(db, "manager"),
                  {"limits.max_posts_cap": "40", "cost.stop_on_limit": False, "ai.enabled": False})
        assert r.status_code == 200
        assert r.json()["changed"] == ["limits.max_posts_cap", "cost.stop_on_limit"]
        assert _setting(db, "limits.max_posts_cap") == 40
        assert _setting(db, "cost.stop_on_limit") is False and _setting(db, "ai.enabled") is None
        from app.admin import settings as s
        assert s.effective_limits()["max_posts_cap"] == 40
        assert _audit(db, "limits.update")["details"] == {"keys": ["limits.max_posts_cap", "cost.stop_on_limit"]}
        r = _call(client, "PUT", "/api/admin/limits", _staff(db, "manager"), {"x": 1})
        assert r.status_code == 400 and r.json()["detail"] == "No valid limit keys"

    def test_scoring_read_and_write(self, env):
        client, db = env
        r = _call(client, "GET", "/api/admin/scoring", _staff(db, "viewer"))
        assert r.status_code == 200
        assert r.json()["settings"]["scoring.hot_min"] == 80
        assert all(k.startswith("scoring.") for k in r.json()["settings"])
        r = _call(client, "PUT", "/api/admin/scoring", _staff(db, "manager"),
                  {"scoring.hot_min": 90, "scoring.derive_quality": "yes"})
        assert r.status_code == 200 and r.json()["changed"] == ["scoring.hot_min", "scoring.derive_quality"]
        assert _setting(db, "scoring.hot_min") == 90 and _setting(db, "scoring.derive_quality") is True
        assert _call(client, "GET", "/api/admin/scoring", _staff(db, "viewer")).json()["settings"]["scoring.hot_min"] == 90
        assert _audit(db, "scoring.update")["details"] == {"keys": ["scoring.hot_min", "scoring.derive_quality"]}
        r = _call(client, "PUT", "/api/admin/scoring", _staff(db, "manager"), {"ci.detect_phone": True})
        assert r.status_code == 400 and r.json()["detail"] == "No valid scoring keys"

    def test_comment_intelligence_read_and_write(self, env):
        client, db = env
        r = _call(client, "GET", "/api/admin/comment-intelligence", _staff(db, "viewer"))
        assert r.status_code == 200
        assert r.json()["settings"]["ci.detect_selling_intent"] is False
        assert all(k.startswith("ci.") for k in r.json()["settings"])
        r = _call(client, "PUT", "/api/admin/comment-intelligence", _staff(db, "manager"),
                  {"ci.min_lead_score": "35", "ci.detect_selling_intent": True})
        assert r.status_code == 200
        assert _setting(db, "ci.min_lead_score") == 35 and _setting(db, "ci.detect_selling_intent") is True
        row = _audit(db, "comment_intelligence.update")
        assert row["details"] == {"keys": ["ci.min_lead_score", "ci.detect_selling_intent"]}
        r = _call(client, "PUT", "/api/admin/comment-intelligence", _staff(db, "manager"), {})
        assert r.status_code == 400 and r.json()["detail"] == "No valid ci keys"

    def test_features_read_and_write(self, env):
        client, db = env
        r = _call(client, "GET", "/api/admin/features", _staff(db, "viewer"))
        assert r.status_code == 200
        keys = r.json()["settings"]
        assert "ai.enabled" in keys and "platform.facebook.enabled" in keys
        assert "features.ai_analysis.enabled" not in keys            # merged into ai.enabled
        r = _call(client, "PUT", "/api/admin/features", _staff(db, "manager"),
                  {"features.exports.enabled": False, "features.ai_analysis.enabled": False})
        assert r.status_code == 200 and r.json()["changed"] == ["features.exports.enabled"]
        assert _setting(db, "features.exports.enabled") is False
        assert _setting(db, "features.ai_analysis.enabled") is None
        assert _audit(db, "features.update")["details"] == {"keys": ["features.exports.enabled"]}
        r = _call(client, "PUT", "/api/admin/features", _staff(db, "manager"),
                  {"features.ai_analysis.enabled": True})
        assert r.status_code == 400 and r.json()["detail"] == "No valid feature keys"


# ═══════════════════════════════════════════════════════════════════════════
# comments / pages / posts browsers
# ═══════════════════════════════════════════════════════════════════════════

class TestBrowsers:
    def test_comments_filters_and_summary(self, env):
        client, db = env
        _lead(db, intent="buy", confidence=0.9, phone="98765 43210", lead_score=90, search_run_id="R1")
        _lead(db, intent="buy", confidence=0.5, platform="instagram", lead_score=40, lead_quality="low")
        _lead(db, intent="praise", confidence=0.1, is_lead=False, lead_score=5,
              comment_text="Nice photo", commenter_name="Bo")
        v = _staff(db, "viewer")
        r = _call(client, "GET", "/api/admin/comments", v)
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 3 and [i["lead_score"] for i in body["items"]] == [90, 40, 5]
        assert isinstance(body["items"][0]["_id"], str)
        s = body["summary"]
        assert (s["total"], s["leads"], s["contacts"], s["high_value"]) == (3, 2, 1, 1)
        assert s["intents"] == {"buy": 2, "praise": 1}
        assert s["avg_score"] == 45.0
        for params, expected in (({"platform": "instagram"}, 1), ({"is_lead": "false"}, 1),
                                 ({"contact": "true"}, 1), ({"min_confidence": 0.4}, 2),
                                 ({"q": "nice"}, 1), ({"quality": "cold"}, 1), ({"run_id": "R1"}, 1),
                                 ({"intent": "buy"}, 2)):
            got = _call(client, "GET", "/api/admin/comments", v, params=params).json()
            assert got["total"] == expected == len(got["items"]), params
        page = _call(client, "GET", "/api/admin/comments", v, params={"limit": 1, "offset": 1}).json()
        assert [i["lead_score"] for i in page["items"]] == [40] and page["total"] == 3

    def test_pages_filters(self, env):
        client, db = env
        db.facebook_pages.insert_many([
            {"page_name": "Acme Bakery", "facebook_url": "https://fb.com/acme", "platform": "facebook", "city": "Pune", "phone": "123",
             "lead_score": 50, "search_run_id": "R1", "created_at": NOW},
            {"page_name": "Zed Gym", "facebook_url": "https://instagram.com/zed", "source": "instagram_profile", "lead_score": 80, "created_at": NOW},
        ])
        v = _staff(db, "viewer")
        body = _call(client, "GET", "/api/admin/pages", v).json()
        assert body["total"] == 2
        assert [i["page_name"] for i in body["items"]] == ["Zed Gym", "Acme Bakery"]
        assert body["items"][0]["platform"] == "instagram"          # derived from source
        for params, names in (({"q": "pune"}, ["Acme Bakery"]), ({"contact": "true"}, ["Acme Bakery"]),
                              ({"run_id": "R1"}, ["Acme Bakery"]), ({"platform": "facebook"}, ["Acme Bakery"])):
            got = _call(client, "GET", "/api/admin/pages", v, params=params).json()
            assert [i["page_name"] for i in got["items"]] == names and got["total"] == len(names), params

    def test_posts_filters_and_comment_count_fallback(self, env):
        client, db = env
        db.facebook_posts.insert_many([
            {"caption": "Diwali sale now", "page_name": "Acme", "platform": "facebook",
             "comments_count": 12, "collection_run_id": "R1",
             "published_date": datetime(2026, 9, 1, tzinfo=timezone.utc)},
            {"caption": "Gym opening", "page_name": "Zed", "source": "youtube_video",
             "total_comment_count": 3, "search_run_id": "R2",
             "published_date": datetime(2026, 9, 5, tzinfo=timezone.utc)},
        ])
        v = _staff(db, "viewer")
        body = _call(client, "GET", "/api/admin/posts", v).json()
        assert body["total"] == 2
        assert [i["caption"] for i in body["items"]] == ["Gym opening", "Diwali sale now"]
        assert body["items"][1]["total_comment_count"] == 12
        assert body["items"][0]["platform"] == "youtube"
        for params, caps in (({"q": "diwali"}, ["Diwali sale now"]), ({"run_id": "R2"}, ["Gym opening"]),
                             ({"run_id": "R1", "q": "gym"}, []),
                             ({"from_date": "2026-09-03"}, ["Gym opening"])):
            got = _call(client, "GET", "/api/admin/posts", v, params=params).json()
            assert [i["caption"] for i in got["items"]] == caps and got["total"] == len(caps), params


# ═══════════════════════════════════════════════════════════════════════════
# database / failed jobs / logs
# ═══════════════════════════════════════════════════════════════════════════

class TestOps:
    def test_database_overview_counts_collections(self, env):
        client, db = env
        db.search_history.insert_many([{"run_id": "a"}, {"run_id": "b"}])
        _lead(db)
        r = _call(client, "GET", "/api/admin/database", _staff(db, "viewer"))
        assert r.status_code == 200
        body = r.json()
        colls = {c["name"]: c for c in body["collections"]}
        assert list(colls) == ["search_history", "facebook_pages", "facebook_posts", "facebook_comments",
                               "ai_comments", "system_settings", "admin_users", "audit_logs"]
        assert colls["search_history"]["documents"] == 2 and colls["ai_comments"]["documents"] == 1
        assert isinstance(colls["search_history"]["indexes"], list)
        assert isinstance(body["db_stats"], dict)

    def test_failed_jobs_summary(self, env):
        client, db = env
        db.search_history.insert_many([
            {"run_id": "e1", "status": "error", "message": "Apify quota exceeded", "created_at": NOW},
            {"run_id": "e2", "status": "error", "message": "Apify quota exceeded",
             "created_at": NOW - timedelta(days=3)},
            {"run_id": "e3", "status": "error", "message": "Timeout", "created_at": NOW - timedelta(days=30)},
            {"run_id": "ok", "status": "completed", "created_at": NOW},
        ])
        r = _call(client, "GET", "/api/admin/failed-jobs", _staff(db, "viewer"))
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 3
        assert [i["run_id"] for i in body["items"]] == ["e1", "e2", "e3"]
        assert body["summary"]["total"] == 3
        assert body["summary"]["top_error"] == {"message": "Apify quota exceeded", "count": 2}
        # today / this_week use $expr/$convert (not implemented by mongomock)
        assert isinstance(body["summary"]["today"], int) and isinstance(body["summary"]["this_week"], int)
        page = _call(client, "GET", "/api/admin/failed-jobs", _staff(db, "viewer"),
                     params={"limit": 1, "offset": 2}).json()
        assert [i["run_id"] for i in page["items"]] == ["e3"]

    @pytest.fixture
    def log_file(self, tmp_path, monkeypatch):
        from app.api.routes import admin as admin_routes
        path = tmp_path / "app.log"
        path.write_text(
            "10:00:00 INFO [app.main] started\n"
            "10:00:01 ERROR [app.db.mongo] connection lost\n"
            "Traceback (most recent call last):\n"
            "ValueError: boom\n"
            "10:00:02 WARNING [app.api.routes.admin] slow request\n"
            "10:00:03 INFO [app.connectors.apify_connector] run done\n", encoding="utf-8")
        monkeypatch.setattr(admin_routes, "_log_file_path", lambda: str(path))
        return path

    def test_logs_filters_and_summary(self, env, log_file):
        client, db = env
        v = _staff(db, "viewer")
        r = _call(client, "GET", "/api/admin/logs", v)
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 4 and body["entries"] is None and len(body["lines"]) == 4
        assert body["summary"] == {"critical": 0, "error": 1, "warning": 1, "info": 2, "debug": 0, "other": 0}
        assert body["modules"] == ["app.api.routes.admin", "app.connectors.apify_connector",
                                   "app.db.mongo", "app.main"]
        assert "Database" in body["sources"] and "Apify" in body["sources"]
        err = _call(client, "GET", "/api/admin/logs", v, params={"level": "error", "structured": "true"}).json()
        assert err["total"] == 1 and err["lines"] is None
        assert err["entries"][0]["module"] == "app.db.mongo" and "ValueError: boom" in err["entries"][0]["raw"]
        assert _call(client, "GET", "/api/admin/logs", v, params={"source": "apify"}).json()["total"] == 1
        assert _call(client, "GET", "/api/admin/logs", v, params={"module": "routes"}).json()["total"] == 1
        assert _call(client, "GET", "/api/admin/logs", v, params={"q": "slow"}).json()["total"] == 1

    def test_logs_stats(self, env, log_file):
        client, db = env
        r = _call(client, "GET", "/api/admin/logs/stats", _staff(db, "viewer"))
        assert r.status_code == 200
        assert r.json()["summary"] == {"critical": 0, "error": 1, "warning": 1, "info": 2, "debug": 0,
                                       "other": 0, "total": 4}
        assert "app.main" in r.json()["modules"]

    def test_logs_download_for_managers(self, env, log_file):
        client, db = env
        r = _call(client, "GET", "/api/admin/logs/download", _staff(db, "manager"))
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/plain")
        assert r.headers["content-disposition"] == 'attachment; filename="leadai.log"'
        assert r.text == log_file.read_text(encoding="utf-8")

    def test_logs_without_a_log_file(self, env, tmp_path, monkeypatch):
        client, db = env
        from app.api.routes import admin as admin_routes
        monkeypatch.setattr(admin_routes, "_log_file_path", lambda: str(tmp_path / "missing.log"))
        r = _call(client, "GET", "/api/admin/logs/download", _staff(db, "manager"))
        assert r.status_code == 404 and r.json()["detail"] == "Log file not found"
        r = _call(client, "GET", "/api/admin/logs", _staff(db, "viewer"))
        assert r.status_code == 200 and r.json()["total"] == 0 and r.json()["lines"] == []


# ═══════════════════════════════════════════════════════════════════════════
# leads
# ═══════════════════════════════════════════════════════════════════════════

class TestLeads:
    def test_get_lead(self, env):
        client, db = env
        lid = _lead(db)
        v = _staff(db, "viewer")
        r = _call(client, "GET", f"/api/admin/leads/{lid}", v)
        assert r.status_code == 200
        assert r.json()["lead"]["_id"] == str(lid) and r.json()["lead"]["commenter_name"] == "Asha"
        assert _call(client, "GET", "/api/admin/leads/not-an-id", v).status_code == 400
        r = _call(client, "GET", f"/api/admin/leads/{ObjectId()}", v)
        assert r.status_code == 404 and r.json()["detail"] == "Lead not found"

    def test_status_transition_records_history_and_audit(self, env):
        client, db = env
        lid = _lead(db)
        r = _call(client, "PATCH", f"/api/admin/leads/{lid}", _staff(db, "manager"),
                  {"status": "contacted", "reason": "called", "changed_by": "manager@platform.test",
                   "priority": "high", "assigned_to": "sam@acme.test"})
        assert r.status_code == 200, r.text
        assert r.json()["success"] is True and r.json()["lead"]["lead_status"] == "contacted"
        doc = db.ai_comments.find_one({"_id": lid})
        assert doc["lead_status"] == "contacted" and doc["lead_priority"] == "high"
        assert doc["assigned_to"] == "sam@acme.test"
        assert len(doc["status_history"]) == 1
        h = doc["status_history"][0]
        assert (h["from_status"], h["to_status"], h["reason"]) == ("new", "contacted", "called")
        row = _audit(db, "lead.update")
        assert row["details"] == {"lead_id": str(lid), "status": "contacted"}
        assert row["actor_email"] == "manager@platform.test"

    def test_invalid_transitions_and_values_change_nothing(self, env):
        client, db = env
        lid = _lead(db)
        m = _staff(db, "manager")
        r = _call(client, "PATCH", f"/api/admin/leads/{lid}", m, {"status": "converted"})
        assert r.status_code == 400 and r.json()["detail"] == "Cannot transition from 'new' to 'converted'"
        r = _call(client, "PATCH", f"/api/admin/leads/{lid}", m, {"status": "bogus"})
        assert r.status_code == 400 and r.json()["detail"] == "Invalid status: bogus"
        r = _call(client, "PATCH", f"/api/admin/leads/{lid}", m, {"priority": "urgent"})
        assert r.status_code == 400 and r.json()["detail"] == "Invalid priority: urgent"
        doc = db.ai_comments.find_one({"_id": lid})
        assert doc["lead_status"] == "new" and "status_history" not in doc and "lead_priority" not in doc
        r = _call(client, "PATCH", f"/api/admin/leads/{ObjectId()}", m, {"status": "contacted"})
        assert r.status_code == 404
        db.ai_comments.update_one({"_id": lid}, {"$set": {"lead_status": "archived"}})
        r = _call(client, "PATCH", f"/api/admin/leads/{lid}", m, {"status": "new"})
        assert r.status_code == 400 and "'archived'" in r.json()["detail"]

    def test_note_is_appended(self, env):
        client, db = env
        lid = _lead(db)
        m = _staff(db, "manager")
        for text in ("first call", "second call"):
            r = _call(client, "PATCH", f"/api/admin/leads/{lid}", m, {"note": text, "changed_by": "m"})
            assert r.status_code == 200
        notes = db.ai_comments.find_one({"_id": lid})["notes"]
        assert [n["text"] for n in notes] == ["first call", "second call"]
        assert notes[0]["author"] == "m"
        assert db.audit_logs.count_documents({"action": "lead.note.add"}) == 2

    def test_patch_with_malformed_id_is_a_400_not_a_500(self, env):
        """GET /leads/{id} answers 400 for a malformed id; PATCH must too
        (it currently calls ObjectId(lead_id) unguarded)."""
        client, db = env
        r = _call(client, "PATCH", "/api/admin/leads/not-an-id", _staff(db, "manager"), {"status": "contacted"})
        assert r.status_code == 400, r.text

    def test_status_change_sent_with_a_note_is_saved(self, env):
        """A status change in the same PATCH as a note must be persisted: the
        handler pushes the status_history entry and answers with the new
        status, but the note branch returns before the $set is applied."""
        client, db = env
        lid = _lead(db)
        r = _call(client, "PATCH", f"/api/admin/leads/{lid}", _staff(db, "manager"),
                  {"status": "contacted", "note": "called, will follow up", "priority": "high"})
        assert r.status_code == 200 and r.json()["lead"]["lead_status"] == "contacted"
        doc = db.ai_comments.find_one({"_id": lid})
        assert doc["status_history"][0]["to_status"] == "contacted"
        assert doc["notes"][0]["text"] == "called, will follow up"
        assert doc["lead_status"] == "contacted"            # FAILS: still "new"
        assert doc["lead_priority"] == "high"

    def test_bulk_actions(self, env):
        client, db = env
        a, b, c = _lead(db), _lead(db), _lead(db)
        m = _staff(db, "manager")
        ids = [str(a), str(b)]
        r = _call(client, "POST", "/api/admin/leads/bulk", m, {"action": "set_status", "ids": ids, "value": "qualified"})
        assert r.status_code == 200 and r.json() == {"success": True, "affected": 2}
        assert {d["lead_status"] for d in db.ai_comments.find({"_id": {"$in": [a, b]}})} == {"qualified"}
        assert db.ai_comments.find_one({"_id": c})["lead_status"] == "new"
        assert _audit(db, "lead.bulk.set_status")["details"] == {"count": 2, "value": "qualified"}
        r = _call(client, "POST", "/api/admin/leads/bulk", m, {"action": "set_priority", "ids": ids, "value": "low"})
        assert r.json()["affected"] == 2 and db.ai_comments.find_one({"_id": a})["lead_priority"] == "low"
        r = _call(client, "POST", "/api/admin/leads/bulk", m, {"action": "assign", "ids": [str(c)], "value": "zoe"})
        assert r.json()["affected"] == 1 and db.ai_comments.find_one({"_id": c})["assigned_to"] == "zoe"
        for body, detail in (({"action": "set_status", "ids": ids, "value": "nope"}, "Invalid status: nope"),
                             ({"action": "set_priority", "ids": ids, "value": "max"}, "Invalid priority: max"),
                             ({"action": "explode", "ids": ids}, "Unknown action: explode"),
                             ({"action": "delete", "ids": []}, "No leads selected"),
                             ({"action": "delete", "ids": ["zzz"]}, "Invalid lead id: zzz")):
            r = _call(client, "POST", "/api/admin/leads/bulk", m, body)
            assert r.status_code == 400 and r.json()["detail"] == detail, body
        assert db.ai_comments.count_documents({}) == 3
        r = _call(client, "POST", "/api/admin/leads/bulk", m, {"action": "delete", "ids": ids})
        assert r.json() == {"success": True, "affected": 2}
        assert [d["_id"] for d in db.ai_comments.find({})] == [c]
        assert _audit(db, "lead.bulk.delete")["actor_email"] == "manager@platform.test"


# ═══════════════════════════════════════════════════════════════════════════
# jobs: cancel / retry
# ═══════════════════════════════════════════════════════════════════════════

class TestJobs:
    def test_cancel_running_job(self, env):
        client, db = env
        db.search_history.insert_many([{"run_id": "RUN1", "status": "running"},
                                       {"run_id": "DONE", "status": "completed"}])
        m = _staff(db, "manager")
        r = _call(client, "POST", "/api/admin/jobs/RUN1/cancel", m)
        assert r.status_code == 200 and r.json() == {"success": True, "message": "Cancellation requested"}
        doc = db.search_history.find_one({"run_id": "RUN1"})
        assert doc["cancel_requested"] is True and doc["message"] == "Cancelling search…"
        row = _audit(db, "job.cancel")
        assert row["details"] == {"run_id": "RUN1"} and row["actor_email"] == "manager@platform.test"
        r = _call(client, "POST", "/api/admin/jobs/DONE/cancel", m)
        assert r.status_code == 400 and r.json()["detail"] == "Run is not running"
        assert "cancel_requested" not in db.search_history.find_one({"run_id": "DONE"})
        assert _call(client, "POST", "/api/admin/jobs/NOPE/cancel", m).status_code == 404

    def test_retry_url_run_keeps_the_original_tenant(self, env):
        client, db = env
        db.search_history.insert_one({
            "run_id": "URLOLD", "status": "error", "url_search": True, "query": "https://fb.com/acme",
            "intent": {"canonical_url": "https://facebook.com/acme", "platform": "facebook",
                       "limit": 5000, "max_comments_per_post": 10},
            "organization_id": "org-1", "user_id": "u-1", "created_by": "owner@acme.test"})
        started = []
        with patch("app.api.routes.search._start", lambda key, fn, *a: started.append(key) or True):
            r = _call(client, "POST", "/api/admin/jobs/URLOLD/retry", _staff(db, "manager"))
        assert r.status_code == 200, r.text
        new_id = r.json()["run_id"]
        assert r.json() == {"run_id": new_id, "status": "running", "retried_from": "URLOLD"}
        assert new_id.startswith("URL") and new_id != "URLOLD"
        assert started == [f"url_search:{new_id}"]
        doc = db.search_history.find_one({"run_id": new_id})
        assert doc["retried_from"] == "URLOLD" and doc["status"] == "running"
        assert (doc["organization_id"], doc["user_id"], doc["created_by"]) == ("org-1", "u-1", "owner@acme.test")
        assert doc["query"] == "https://facebook.com/acme"
        assert doc["limit"] == 100                        # capped at limits.max_posts_cap
        assert doc["intent"]["max_comments_per_post"] == 10
        row = _audit(db, "job.retry")
        assert row["details"] == {"run_id": "URLOLD", "new_run_id": new_id}
        assert row["actor_email"] == "manager@platform.test"

    def test_retry_rejects_non_url_and_missing_runs(self, env):
        client, db = env
        db.search_history.insert_one({"run_id": "KW1", "status": "error", "query": "bakery"})
        m = _staff(db, "manager")
        with patch("app.api.routes.search._start") as start:
            r = _call(client, "POST", "/api/admin/jobs/KW1/retry", m)
            assert r.status_code == 400 and r.json()["detail"] == "Only URL-based search runs can be retried"
            r = _call(client, "POST", "/api/admin/jobs/NOPE/retry", m)
            assert r.status_code == 404
            assert start.call_count == 0
        assert db.search_history.count_documents({}) == 1
        assert _audit(db, "job.retry") is None


# ═══════════════════════════════════════════════════════════════════════════
# platforms
# ═══════════════════════════════════════════════════════════════════════════

class TestPlatforms:
    def test_toggle(self, env):
        client, db = env
        m = _staff(db, "manager")
        r = _call(client, "POST", "/api/admin/platforms/facebook/toggle", m)
        assert r.status_code == 200 and r.json() == {"success": True, "platform": "facebook", "enabled": False}
        assert _setting(db, "platform.facebook.enabled") is False
        from app.admin import settings as s
        assert s.is_platform_enabled("facebook") is False
        assert _audit(db, "platform.toggle")["details"] == {"platform": "facebook", "enabled": False}
        r = _call(client, "POST", "/api/admin/platforms/facebook/toggle", m)
        assert r.json()["enabled"] is True and _setting(db, "platform.facebook.enabled") is True
        r = _call(client, "POST", "/api/admin/platforms/tiktok/toggle", m)
        assert r.status_code == 400 and r.json()["detail"] == "Unknown platform: tiktok"
        assert _setting(db, "platform.tiktok.enabled") is None

    def test_update_actor(self, env):
        client, db = env
        m = _staff(db, "manager")
        r = _call(client, "POST", "/api/admin/platforms/facebook/actor", m,
                  {"kind": "posts", "actor_id": "  acme/fb-posts  "})
        assert r.status_code == 200
        assert r.json() == {"success": True, "key": "actor.facebook.posts", "value": "acme/fb-posts"}
        assert _setting(db, "actor.facebook.posts") == "acme/fb-posts"
        from app.admin import settings as s
        assert s.get_actor_id("facebook", "posts") == "acme/fb-posts"
        row = _audit(db, "platform.actor.update")
        assert row["details"] == {"platform": "facebook", "key": "actor.facebook.posts", "actor_id": "acme/fb-posts"}
        r = _call(client, "POST", "/api/admin/platforms/instagram/actor", m, {"actor_id": "acme/ig"})
        assert r.json()["key"] == "actor.instagram.main" and _setting(db, "actor.instagram.main") == "acme/ig"
        r = _call(client, "POST", "/api/admin/platforms/facebook/actor", m, {"actor_id": "   "})
        assert r.status_code == 400 and r.json()["detail"] == "actor_id is required"
        r = _call(client, "POST", "/api/admin/platforms/myspace/actor", m, {"actor_id": "a/b"})
        assert r.status_code == 400
        assert _setting(db, "actor.facebook.pages") is None


# ═══════════════════════════════════════════════════════════════════════════
# security, users, billing grants (Super Admin only)
# ═══════════════════════════════════════════════════════════════════════════

class TestSuperAdminActions:
    def test_revoke_sessions_bumps_epoch_and_audits(self, env):
        client, db = env
        sa = _super(db)
        r = _call(client, "POST", "/api/admin/security/revoke-sessions", sa)
        assert r.status_code == 200 and r.json()["success"] is True
        first = _setting(db, "security.session_epoch")
        assert first and first > 0
        row = _audit(db, "security.revoke_sessions")
        assert row["actor_email"] == SUPER_EMAIL and row["details"]["epoch"] == first
        # the epoch is the revocation time (unix seconds), comparable with a cookie's iat
        assert abs(first - time.time()) < 60
        assert row["details"]["sessions_revoked"] >= 1  # the tracked sessions are revoked too

    def test_revoke_sessions_invalidates_existing_cookies(self, env):
        """"Bump the session epoch: every existing cookie becomes invalid."
        current_admin() rejects a cookie whose iat (unix seconds) is older
        than the epoch, but the handler stores 0 -> 1 -> 2..., so no real
        cookie (iat ~ 1.7e9) is ever older than the epoch."""
        client, db = env
        real = time.time
        with patch("time.time", lambda: real() - 120):       # sessions issued 2 minutes ago
            viewer = _staff(db, "viewer")
            sa = _super(db)
        assert _call(client, "GET", "/api/admin/features", viewer).status_code == 200
        r = _call(client, "POST", "/api/admin/security/revoke-sessions", sa)
        assert r.status_code == 200
        after = _call(client, "GET", "/api/admin/features", viewer)
        assert after.status_code == 401, after.text          # FAILS: 200, the old cookie still works

    def test_activate_customer_user_and_admin_record(self, env):
        client, db = env
        sa = _super(db)
        uid = _user(db, "pat@acme.test", status="suspended")
        r = _call(client, "POST", f"/api/admin/users/{uid}/activate", sa)
        assert r.status_code == 200 and r.json() == {"success": True, "message": "User activated"}
        assert db.users.find_one({"_id": ObjectId(uid)})["status"] == "active"
        row = _audit(db, "user.activate")
        assert row["category"] == "users" and row["details"] == {"user_id": uid}
        assert row["actor_email"] == SUPER_EMAIL
        aid = db.admin_users.insert_one({"email": "ops@platform.test", "role": "manager",
                                         "enabled": False}).inserted_id
        r = _call(client, "POST", f"/api/admin/users/{aid}/activate", sa)
        assert r.status_code == 200 and r.json()["message"] == "Admin user enabled"
        assert db.admin_users.find_one({"_id": aid})["enabled"] is True
        assert _call(client, "POST", "/api/admin/users/bad-id/activate", sa).status_code == 400
        r = _call(client, "POST", f"/api/admin/users/{ObjectId()}/activate", sa)
        assert r.status_code == 404 and r.json()["detail"] == "User not found"

    @pytest.fixture
    def trial_org(self, env):
        client, db = env
        org = str(db.organizations.insert_one({"name": "Trial Co", "slug": "trial-co", "status": "trial",
                                               "created_at": NOW}).inserted_id)
        sub = db.subscriptions.insert_one({
            "organization_id": org, "plan_id": "starter", "status": "trialing",
            "trial_end": NOW + timedelta(days=2), "current_period_end": NOW + timedelta(days=2),
            "created_at": NOW}).inserted_id
        return org, sub

    def test_extend_trial(self, env, trial_org):
        client, db = env
        org, sub = trial_org
        r = _call(client, "POST", f"/api/admin/subscriptions/{sub}/extend-trial", _super(db),
                  {"extra_days": 10, "reason": "VIP"})
        assert r.status_code == 200, r.text
        assert r.json()["success"] is True
        doc = db.subscriptions.find_one({"_id": sub})
        end = doc["trial_end"].replace(tzinfo=timezone.utc)
        assert abs((end - (NOW + timedelta(days=12))).total_seconds()) < 120
        assert doc["status"] == "trialing"
        org_doc = db.organizations.find_one({"_id": ObjectId(org)})
        assert org_doc["status"] == "trial"
        row = _audit(db, "subscription.extend_trial")
        assert row["details"] == {"sub_id": str(sub), "extra_days": 10} and row["actor_email"] == SUPER_EMAIL
        # extending again adds on top of the already-extended end
        r = _call(client, "POST", f"/api/admin/subscriptions/{sub}/extend-trial", _super(db), {"extra_days": 1})
        assert r.status_code == 200
        end2 = db.subscriptions.find_one({"_id": sub})["trial_end"].replace(tzinfo=timezone.utc)
        assert abs((end2 - end - timedelta(days=1)).total_seconds()) < 5

    def test_extend_trial_refuses_paid_and_missing_subscriptions(self, env, trial_org):
        client, db = env
        org, sub = trial_org
        db.subscriptions.update_one({"_id": sub}, {"$set": {"status": "active"}})
        r = _call(client, "POST", f"/api/admin/subscriptions/{sub}/extend-trial", _super(db), {"extra_days": 5})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "SUBSCRIPTION_NOT_TRIAL"
        assert db.subscriptions.find_one({"_id": sub})["status"] == "active"
        r = _call(client, "POST", f"/api/admin/subscriptions/{ObjectId()}/extend-trial", _super(db), {})
        assert r.status_code == 404 and r.json()["detail"] == "Subscription not found"

    def test_extend_trial_rejects_negative_days(self, env, trial_org):
        """"Extend trial" with extra_days=-30 must not shorten (end) the trial;
        the console only allows 1..90 days, the API accepts any int."""
        client, db = env
        org, sub = trial_org
        r = _call(client, "POST", f"/api/admin/subscriptions/{sub}/extend-trial", _super(db), {"extra_days": -30})
        assert r.status_code in (400, 422), r.text        # FAILS: 200
        end = db.subscriptions.find_one({"_id": sub})["trial_end"].replace(tzinfo=timezone.utc)
        assert end > NOW

    def test_grant_feature(self, env):
        client, db = env
        org = str(db.organizations.insert_one({"name": "VIP", "slug": "vip", "status": "active"}).inserted_id)
        r = _call(client, "POST", f"/api/admin/organizations/{org}/grant-feature", _super(db),
                  {"feature": " csv_export ", "expires_days": 7, "reason": "VIP trial"})
        assert r.status_code == 200, r.text
        ent = r.json()["entitlement"]
        assert (ent["organization_id"], ent["entitlement_type"], ent["key"], ent["value"]) == \
            (org, "feature", "csv_export", True)
        doc = db.temporary_entitlements.find_one({"_id": ObjectId(ent["_id"])})
        assert doc["reason"] == "VIP trial"
        exp = doc["expires_at"].replace(tzinfo=timezone.utc)
        # measured from now, not from the module's import time (a long run drifts)
        assert abs((exp - (datetime.now(timezone.utc) + timedelta(days=7))).total_seconds()) < 120
        from app.billing.entitlements import EntitlementService
        assert EntitlementService.has_feature_sync(org, "csv_export") is True
        row = _audit(db, "entitlement.grant_feature")
        assert row["details"] == {"org_id": org, "feature": " csv_export "} and row["actor_email"] == SUPER_EMAIL
        r = _call(client, "POST", f"/api/admin/organizations/{org}/grant-feature", _super(db),
                  {"feature": "api_access", "expires_days": None})
        assert r.status_code == 200 and r.json()["entitlement"]["expires_at"] is None
        assert _call(client, "POST", f"/api/admin/organizations/{org}/grant-feature", _super(db),
                     {}).status_code == 422

    def test_grant_credits(self, env):
        client, db = env
        org = str(db.organizations.insert_one({"name": "VIP", "slug": "vip", "status": "active"}).inserted_id)
        r = _call(client, "POST", f"/api/admin/organizations/{org}/grant-credits", _super(db),
                  {"metric": "searches_per_month", "credits": 100})
        assert r.status_code == 200, r.text
        ent = r.json()["entitlement"]
        assert (ent["entitlement_type"], ent["key"], ent["value"]) == ("credit", "searches_per_month", 100)
        doc = db.temporary_entitlements.find_one({"_id": ObjectId(ent["_id"])})
        assert doc["organization_id"] == org and doc["granted_by"] == "super_admin"
        row = _audit(db, "entitlement.grant_credits")
        assert row["details"] == {"org_id": org, "metric": "searches_per_month", "credits": 100}
        assert _call(client, "POST", f"/api/admin/organizations/{org}/grant-credits", _super(db),
                     {"metric": "x", "credits": "lots"}).status_code == 422

    def test_grant_credits_rejects_negative_amounts(self, env):
        """"Grant extra usage quota credits": a negative grant silently LOWERS
        the organization's plan limit (EntitlementService.get_limit adds it)."""
        client, db = env
        org = str(db.organizations.insert_one({"name": "VIP", "slug": "vip", "status": "active"}).inserted_id)
        r = _call(client, "POST", f"/api/admin/organizations/{org}/grant-credits", _super(db),
                  {"metric": "searches_per_month", "credits": -500})
        assert r.status_code in (400, 422), r.text          # FAILS: 200
        assert db.temporary_entitlements.count_documents({}) == 0


# ═══════════════════════════════════════════════════════════════════════════
# AI model registry (admin_ai.py)
# ═══════════════════════════════════════════════════════════════════════════

class TestAiModels:
    def test_crud_with_string_ids(self, env):
        client, db = env
        db.ai_models.insert_one({"_id": "gemini-test", "model_name": "gemini-test", "display_name": "Gemini",
                                 "created_at": NOW})
        v, m = _staff(db, "viewer"), _staff(db, "manager")
        r = _call(client, "GET", "/api/admin/ai/models", v)
        assert r.status_code == 200, r.text
        assert r.json()["total"] == 1 and r.json()["items"][0]["_id"] == "gemini-test"
        assert (r.json()["offset"], r.json()["limit"]) == (0, 20)
        r = _call(client, "GET", "/api/admin/ai/models/gemini-test", v)
        assert r.status_code == 200 and r.json()["display_name"] == "Gemini"
        assert _call(client, "GET", "/api/admin/ai/models/nope", v).status_code == 404
        r = _call(client, "PUT", "/api/admin/ai/models/gemini-test", m, {"display_name": "Gemini Pro"})
        assert r.status_code == 200 and r.json() == {"id": "gemini-test", "status": "updated"}
        doc = db.ai_models.find_one({"_id": "gemini-test"})
        assert doc["display_name"] == "Gemini Pro" and doc["updated_at"]
        assert _audit(db, "model.update")["details"] == {"model_id": "gemini-test"}
        r = _call(client, "PUT", "/api/admin/ai/models/nope", m, {"display_name": "x"})
        assert r.status_code == 404 and r.json()["detail"] == "Model not found"
        r = _call(client, "DELETE", "/api/admin/ai/models/gemini-test", _super(db))
        assert r.status_code == 200 and r.json() == {"id": "gemini-test", "status": "deleted"}
        assert db.ai_models.count_documents({}) == 0
        row = _audit(db, "model.delete")
        assert row["details"] == {"model_id": "gemini-test"} and row["actor_email"] == SUPER_EMAIL
        assert _call(client, "DELETE", "/api/admin/ai/models/gemini-test", _super(db)).status_code == 404

    def test_create_model_and_audit(self, env):
        client, db = env
        r = _call(client, "POST", "/api/admin/ai/models", _staff(db, "manager"),
                  {"_id": "flash-x", "model_name": "gemini-flash-x", "display_name": "Flash X"})
        assert r.status_code == 200 and r.json() == {"id": "flash-x", "status": "created"}
        doc = db.ai_models.find_one({"_id": "flash-x"})
        assert doc["display_name"] == "Flash X" and doc["created_at"] and doc["updated_by"] == "manager@platform.test"
        row = _audit(db, "model.create")
        assert row["details"] == {"model_id": "flash-x"} and row["actor_email"] == "manager@platform.test"

    def test_list_pagination(self, env):
        client, db = env
        for i in range(3):
            db.ai_models.insert_one({"_id": f"m{i}", "model_name": f"m{i}", "created_at": NOW + timedelta(seconds=i)})
        r = _call(client, "GET", "/api/admin/ai/models", _staff(db, "viewer"), params={"limit": 2, "offset": 1})
        assert r.status_code == 200
        assert [i["_id"] for i in r.json()["items"]] == ["m1", "m0"] and r.json()["total"] == 3
        assert _call(client, "GET", "/api/admin/ai/models", _staff(db, "viewer"),
                     params={"limit": 0}).status_code == 422

    def test_model_created_through_the_api_can_be_read_updated_and_deleted(self, env):
        """POST /models returns str(inserted_id) (an ObjectId), but GET/PUT/DELETE
        look the id up as a plain string, so the model can never be found
        again (the prompt routes convert with _pid(); the model routes don't)."""
        client, db = env
        r = _call(client, "POST", "/api/admin/ai/models", _staff(db, "manager"),
                  {"model_name": "gemini-2.5-pro", "display_name": "Pro"})
        assert r.status_code == 200
        mid = r.json()["id"]
        got = _call(client, "GET", f"/api/admin/ai/models/{mid}", _staff(db, "viewer"))
        assert got.status_code == 200, got.text            # FAILS: 404
        assert got.json()["display_name"] == "Pro"
        r = _call(client, "PUT", f"/api/admin/ai/models/{mid}", _staff(db, "manager"), {"display_name": "Pro 2"})
        assert r.status_code == 200
        r = _call(client, "DELETE", f"/api/admin/ai/models/{mid}", _super(db))
        assert r.status_code == 200 and db.ai_models.count_documents({}) == 0

    def test_list_models_with_registry_seeded_models(self, env):
        """The platform seeds ai_models with ObjectId ids
        (ai_models_service.seed_default_models); listing them must work."""
        client, db = env
        from app.pipeline.ai_models_service import seed_default_models
        seed_default_models()
        n = db.ai_models.count_documents({})
        assert n > 0
        r = _call(client, "GET", "/api/admin/ai/models", _staff(db, "viewer"))
        assert r.status_code == 200, r.text                 # FAILS: 500 (ObjectId not JSON serializable)
        assert r.json()["total"] == n and all(isinstance(i["_id"], str) for i in r.json()["items"])

    def test_audit_actor_cannot_be_forged_through_the_payload(self, env):
        """audit_logs is the "tamper-resistant trail"; create/update pass the
        client-supplied payload["updated_by"] as the audit user, so a manager
        can attribute the change to anyone."""
        client, db = env
        r = _call(client, "POST", "/api/admin/ai/models", _staff(db, "manager"),
                  {"_id": "spoof", "model_name": "spoof", "updated_by": "ceo@victim.test"})
        assert r.status_code == 200
        row = _audit(db, "model.create")
        assert row["actor_email"] == "manager@platform.test", row["actor_email"]   # FAILS: ceo@victim.test


class TestPlayground:
    def test_validation_and_explicit_not_implemented(self, env):
        client, db = env
        db.ai_models.insert_one({"_id": "gemini-test", "model_name": "gemini-test"})
        pid = db.ai_prompts.insert_one({"name": "p", "template": "Hello {name}"}).inserted_id
        v = _staff(db, "viewer")
        url = "/api/admin/ai/playground"
        r = _call(client, "POST", url, v, {})
        assert r.status_code == 400 and r.json()["detail"] == "model_id is required"
        r = _call(client, "POST", url, v, {"model_id": "nope", "raw_input": "x"})
        assert r.status_code == 404 and r.json()["detail"] == "Model not found"
        r = _call(client, "POST", url, v, {"model_id": "gemini-test"})
        assert r.status_code == 400 and r.json()["detail"] == "Either prompt_id or raw_input must be provided"
        r = _call(client, "POST", url, v, {"model_id": "gemini-test", "prompt_id": str(ObjectId())})
        assert r.status_code == 404 and r.json()["detail"] == "Prompt not found"
        r = _call(client, "POST", url, v, {"model_id": "gemini-test", "prompt_id": str(pid), "variables": {}})
        assert r.status_code == 400 and r.json()["detail"].startswith("Prompt rendering error")
        # a well-formed request reaches the model step, which is not implemented (known)
        r = _call(client, "POST", url, v, {"model_id": "gemini-test", "prompt_id": str(pid),
                                           "variables": {"name": "Asha"}})
        assert r.status_code == 501 and r.json()["detail"] == "Model execution is not available"
        r = _call(client, "POST", url, v, {"model_id": "gemini-test", "raw_input": "hi"})
        assert r.status_code == 501
