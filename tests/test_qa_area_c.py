"""QA area C — route coverage for admin analytics, the Apify control center,
admin lead management, comment filters, Global Settings (save → version →
rollback → export → import) and the CMS admin API.

Runs on the in-memory MongoDB from tests/conftest.py (never a real
database); Apify is never called (httpx is replaced at the network boundary)
and background scrapes are never started (search._start is captured).

mongomock does not implement the ``$convert`` aggregation operator that the
analytics / failed-jobs queries use, so this module installs a small shim
implementing MongoDB's documented ``$convert`` → ``date`` semantics for the
duration of each test. The application code is unchanged.
"""
import datetime as _dt
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import mongomock.aggregate as _mm_agg
import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.auth.crypto import hash_password
from app.auth.service import COOKIE_NAME, build_session_value, create_tracked_session

NOW = datetime.now(timezone.utc)


# ═══════════════════════════════════════════════════════════════════════════
# test infrastructure
# ═══════════════════════════════════════════════════════════════════════════

_ORIG_CONVERT = _mm_agg._Parser._handle_type_convertion_operator


def _convert_shim(self, operator, values):
    """MongoDB ``$convert`` to ``date`` (input date → itself, ISO string →
    parsed date, null/missing → onNull, anything else → onError)."""
    if operator != "$convert" or not isinstance(values, dict) or values.get("to") != "date":
        return _ORIG_CONVERT(self, operator, values)
    try:
        value = self.parse(values.get("input"))
    except KeyError:
        value = None
    if value is None:
        return values.get("onNull")
    # mongomock (like BSON) keeps datetimes as naive UTC, including the
    # query-side operands, so the converted value is naive UTC too
    if isinstance(value, _dt.datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, str):
        try:
            d = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            return d.astimezone(timezone.utc).replace(tzinfo=None) if d.tzinfo else d
        except ValueError:
            return values.get("onError")
    return values.get("onError")


@pytest.fixture(autouse=True)
def _mongomock_convert(monkeypatch):
    monkeypatch.setattr(_mm_agg._Parser, "_handle_type_convertion_operator", _convert_shim)


@pytest.fixture
def env():
    from app.admin.settings import clear_settings_cache
    from app.settings.registry import invalidate_public_config_cache
    clear_settings_cache()
    invalidate_public_config_cache()
    with patch("app.admin.settings.is_maintenance_enabled", return_value=False):
        from app.main import app
        with TestClient(app) as client:
            from app.db.mongo import get_sync_db
            db = get_sync_db()
            for coll in ("users", "organizations", "organization_members", "user_sessions",
                         "audit_logs", "settings_history", "website_faq", "website_media"):
                db[coll].delete_many({})
            yield client, db
    clear_settings_cache()
    invalidate_public_config_cache()


def _user(db, email, org_id=None, role="member", platform_role=None):
    uid = str(db.users.insert_one({
        "email": email, "name": email.split("@")[0], "password_hash": hash_password("Str0ngPass!"),
        "status": "active", "is_platform_admin": bool(platform_role), "platform_role": platform_role,
        "default_organization_id": org_id, "created_at": NOW}).inserted_id)
    if org_id:
        db.organization_members.insert_one({"organization_id": org_id, "user_id": uid, "role": role,
                                            "status": "active", "joined_at": NOW})
    return uid


def _cookie(claims):
    return {COOKIE_NAME: build_session_value(create_tracked_session(claims))}


def _super(db):
    from tests.conftest import as_superadmin
    as_superadmin("root@platform.test")
    uid = _user(db, "root@platform.test", platform_role="super_admin")
    return _cookie({"user_id": uid, "email": "root@platform.test", "name": "Root",
                    "scope": "admin", "role": "super_admin"})


def _staff(db, role="viewer"):
    uid = _user(db, f"{role}@platform.test", platform_role=role)
    return _cookie({"user_id": uid, "email": f"{role}@platform.test", "name": role,
                    "scope": "admin", "role": role})


def _tenant(db):
    org = str(db.organizations.insert_one({"name": "Acme", "slug": "acme", "status": "active",
                                           "created_at": NOW}).inserted_id)
    uid = _user(db, "owner@acme.test", org, "owner")
    return _cookie({"user_id": uid, "email": "owner@acme.test", "name": "o", "scope": "site",
                    "organization_id": org, "org_role": "owner"})


@pytest.fixture
def who(env):
    _, db = env
    return {"super": _super(db), "manager": _staff(db, "manager"),
            "viewer": _staff(db, "viewer"), "tenant": _tenant(db)}


def _audit(db, action):
    return list(db.audit_logs.find({"action": action}))


def _msg(r):
    """Error text: HTTPException bodies carry ``detail``; the app's auth
    middleware answers an anonymous API call with ``message``."""
    body = r.json()
    return body.get("detail", body.get("message"))


def _assert_denied(client, method, url, who, *, json=None, files=None, readers=()):
    """Anonymous + tenant sessions → 401; listed staff roles → 403."""
    kw = {}
    if json is not None:
        kw["json"] = json
    if files is not None:
        kw["files"] = files
    r = client.request(method, url, **kw)
    assert r.status_code == 401, (url, r.status_code, r.text)
    assert _msg(r) == "Sign in required"
    r = client.request(method, url, cookies=who["tenant"], **kw)
    assert r.status_code == 401, (url, r.status_code, r.text)
    assert _msg(r) == "Sign in required"
    for role in readers:
        r = client.request(method, url, cookies=who[role], **kw)
        assert r.status_code == 403, (url, role, r.status_code, r.text)
        assert "Requires role" in _msg(r)


# ═══════════════════════════════════════════════════════════════════════════
# admin_analytics
# ═══════════════════════════════════════════════════════════════════════════

def _seed_analytics(db):
    today = NOW.replace(hour=12, minute=0, second=0, microsecond=0)
    old = today - timedelta(days=45)          # previous 30-day window
    docs = [
        # in range: 3 leads (1 converted) + 1 non-lead
        {"is_lead": True, "lifecycle_status": "converted", "lead_score": 85, "analyzed_at": today},
        {"is_lead": True, "lifecycle_status": "new", "lead_score": 45, "analyzed_at": today},
        {"is_lead": True, "lifecycle_status": "contacted", "lead_score": 10,
         "analyzed_at": (today - timedelta(days=2)).isoformat()},   # ISO string also counted
        {"is_lead": False, "lead_score": 5, "analyzed_at": today - timedelta(days=2)},
        # previous window: 1 lead
        {"is_lead": True, "lifecycle_status": "lost", "lead_score": 65, "analyzed_at": old},
        # unparsable date never counted in ranges, still in totals
        {"is_lead": True, "lifecycle_status": "new", "lead_score": 100, "analyzed_at": "garbage"},
    ]
    for i, d in enumerate(docs):
        d["comment_ref"] = f"ref-{i}"           # ai_comments.comment_ref is unique
    db.ai_comments.insert_many(docs)
    db.search_history.insert_many([
        {"run_id": "R1", "status": "completed", "created_at": today},
        {"run_id": "R2", "status": "error", "created_at": today - timedelta(days=1)},
        {"run_id": "R3", "status": "completed", "created_at": old},
    ])
    db.facebook_pages.insert_one({"name": "p"})
    db.facebook_posts.insert_many([{"t": 1}, {"t": 2}])
    return today


class TestAdminAnalytics:
    def test_overview_kpis_and_totals(self, env, who):
        client, db = env
        _seed_analytics(db)
        r = client.get("/api/admin/analytics/overview?days=30", cookies=who["viewer"])
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["range"]["days"] == 30
        k = body["kpis"]
        assert k["leads"] == {"value": 3, "prev": 1, "change": 200.0}
        assert k["jobs"] == {"value": 2, "prev": 1, "change": 100.0}
        assert k["analyzed"] == {"value": 4}
        # conversion = converted / all leads (5) = 20%
        assert k["conversion"] == {"value": 20.0, "unit": "%"}
        assert body["totals"] == {"total_leads": 5, "total_jobs": 3, "total_comments": 6,
                                  "total_pages": 1, "total_posts": 2}

    def test_overview_custom_range_and_empty_db(self, env, who):
        client, db = env
        r = client.get("/api/admin/analytics/overview", cookies=who["viewer"])
        assert r.status_code == 200
        body = r.json()
        assert body["kpis"]["leads"] == {"value": 0, "prev": 0, "change": None}
        assert body["kpis"]["conversion"]["value"] is None
        _seed_analytics(db)
        frm = (NOW - timedelta(days=50)).strftime("%Y-%m-%dT00:00:00+00:00")
        to = (NOW - timedelta(days=40)).strftime("%Y-%m-%dT23:59:59+00:00")
        r = client.get("/api/admin/analytics/overview", params={"from_date": frm, "to_date": to},
                       cookies=who["viewer"])
        assert r.status_code == 200
        assert r.json()["kpis"]["leads"]["value"] == 1          # only the 45-day-old lead
        assert r.json()["kpis"]["jobs"]["value"] == 1
        assert client.get("/api/admin/analytics/overview?days=0",
                          cookies=who["viewer"]).status_code == 422

    def test_funnel(self, env, who):
        client, db = env
        _seed_analytics(db)
        r = client.get("/api/admin/analytics/funnel", cookies=who["viewer"])
        assert r.status_code == 200
        f = {row["stage"]: row for row in r.json()["funnel"]}
        assert f["Discovered"] == {"stage": "Discovered", "count": 6, "pct": 100}
        assert f["Leads"]["count"] == 5 and f["Leads"]["pct"] == round(5 / 6 * 100, 1)
        assert f["New"]["count"] == 2 and f["New"]["pct"] == 40.0
        assert f["Converted"]["count"] == 1 and f["Follow Up"]["count"] == 0
        assert [row["stage"] for row in r.json()["funnel"]][:3] == ["Discovered", "Leads", "New"]

    def test_score_distribution(self, env, who):
        client, db = env
        _seed_analytics(db)
        r = client.get("/api/admin/analytics/score-distribution", cookies=who["viewer"])
        assert r.status_code == 200
        dist = {row["bucket"]: row["count"] for row in r.json()["distribution"]}
        # leads only (the non-lead score 5 is ignored); 100 lands in 80–100
        assert dist == {"0–19": 1, "20–39": 0, "40–59": 1, "60–79": 1, "80–100": 2}

    def test_activity_daily_series(self, env, who):
        client, db = env
        today = _seed_analytics(db)
        r = client.get("/api/admin/analytics/activity?days=7", cookies=who["viewer"])
        assert r.status_code == 200
        body = r.json()
        assert len(body["labels"]) == 7 and body["labels"][-1] == today.strftime("%Y-%m-%d")
        assert len(body["jobs"]) == len(body["leads"]) == len(body["comments"]) == 7
        assert body["jobs"][-1] == 1 and body["jobs"][-2] == 1
        assert body["leads"][-1] == 2 and body["leads"][-3] == 1
        assert body["comments"][-1] == 2 and body["comments"][-3] == 2
        assert sum(body["leads"]) == 3
        assert client.get("/api/admin/analytics/activity?days=91",
                          cookies=who["viewer"]).status_code == 422

    def test_plans_distribution(self, env, who):
        client, db = env
        db.subscriptions.delete_many({})
        db.subscriptions.insert_many([{"plan_id": "pro"}, {"plan_id": "pro"}, {"plan_id": "starter"},
                                      {"organization_id": "x"}])
        r = client.get("/api/admin/analytics/plans", cookies=who["viewer"])
        assert r.status_code == 200
        assert r.json()["plans"][0] == {"plan_id": "pro", "count": 2}
        assert {p["plan_id"]: p["count"] for p in r.json()["plans"]} == {
            "pro": 2, "starter": 1, "none": 1}

    @pytest.mark.parametrize("path", ["overview", "funnel", "plans", "score-distribution", "activity"])
    def test_analytics_requires_platform_staff(self, env, who, path):
        client, _ = env
        _assert_denied(client, "GET", f"/api/admin/analytics/{path}", who)


# ═══════════════════════════════════════════════════════════════════════════
# admin_apify
# ═══════════════════════════════════════════════════════════════════════════

class _FakeResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


def _fake_httpx(calls, status=200, payload=None):
    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, **kw):
            calls.append({"url": url, "headers": headers})
            return _FakeResp(status, payload or {})
    return _Client


class TestAdminApify:
    def test_list_actors_reports_the_actor_ids_the_scrapers_use(self, env, who):
        client, db = env
        from app.admin import settings as s
        r = client.get("/api/admin/apify/actors", cookies=who["viewer"])
        assert r.status_code == 200
        body = r.json()
        assert [a["platform"] for a in body["actors"]] == list(s.PLATFORMS)
        assert body["token_configured"] is False and body["token_hint"] == ""
        actors = {a["platform"]: a for a in body["actors"]}
        # Facebook has a built-in default actor that every scrape uses
        assert s.get_actor_id("facebook") == "apify/facebook-pages-scraper"
        assert actors["facebook"]["actor_id"] == s.get_actor_id("facebook")
        assert actors["facebook"]["configured"] is True

    def test_list_actors_token_hint(self, env, who):
        client, _ = env
        from app.config import get_settings
        get_settings().apify_api_token = "apify_api_SECRETabcd"   # restored by conftest
        r = client.get("/api/admin/apify/actors", cookies=who["viewer"])
        assert r.status_code == 200
        assert r.json()["token_configured"] is True
        assert r.json()["token_hint"] == "••••abcd"
        assert "SECRET" not in r.text

    def test_update_actor_changes_what_the_scraper_runs(self, env, who):
        client, db = env
        from app.admin import settings as s
        r = client.put("/api/admin/apify/actors/instagram", json={"actor_id": " acme/insta-v2 "},
                       cookies=who["manager"])
        assert r.status_code == 200, r.text
        assert r.json() == {"platform": "instagram", "actor_id": "acme/insta-v2", "status": "updated"}
        logs = _audit(db, "apify.actor.update")
        assert len(logs) == 1 and logs[0]["details"] == {"platform": "instagram",
                                                          "actor_id": "acme/insta-v2"}
        assert logs[0]["actor_email"] == "manager@platform.test"
        listed = {a["platform"]: a for a in
                  client.get("/api/admin/apify/actors", cookies=who["viewer"]).json()["actors"]}
        assert listed["instagram"] == {"platform": "instagram", "actor_id": "acme/insta-v2",
                                       "configured": True}
        # the point of the endpoint: the Instagram scraper now runs the new actor
        s.clear_settings_cache()
        assert s.get_actor_id("instagram") == "acme/insta-v2"

    def test_update_actor_rejects_unknown_platform(self, env, who):
        client, db = env
        r = client.put("/api/admin/apify/actors/notaplatform", json={"actor_id": "x/y"},
                       cookies=who["manager"])
        assert r.status_code == 400, r.text
        assert db.system_settings.find_one({"_id": "apify.actor.notaplatform"}) is None

    def test_update_actor_validation_and_roles(self, env, who):
        client, db = env
        r = client.put("/api/admin/apify/actors/facebook", json={"actor_id": "  "},
                       cookies=who["manager"])
        assert r.status_code == 400 and r.json()["detail"] == "actor_id is required"
        _assert_denied(client, "PUT", "/api/admin/apify/actors/facebook", who,
                       json={"actor_id": "x/y"}, readers=("viewer",))
        assert _audit(db, "apify.actor.update") == []

    def test_test_connection_without_token(self, env, who):
        client, db = env
        r = client.post("/api/admin/apify/test-connection", cookies=who["manager"])
        assert r.status_code == 400 and r.json()["detail"] == "Apify token is not configured"
        _assert_denied(client, "POST", "/api/admin/apify/test-connection", who, readers=("viewer",))

    def test_test_connection_ok_and_rejected(self, env, who, monkeypatch):
        client, db = env
        from app.admin import settings as s
        from app.config import get_settings
        get_settings().apify_api_token = "apify_api_TOKEN1234"
        calls = []
        monkeypatch.setattr("httpx.AsyncClient", _fake_httpx(
            calls, 200, {"data": {"username": "acme", "plan": {"id": "STARTER"}}}))
        r = client.post("/api/admin/apify/test-connection", cookies=who["manager"])
        assert r.status_code == 200
        assert r.json() == {"success": True, "username": "acme", "plan": "STARTER"}
        assert calls == [{"url": "https://api.apify.com/v2/users/me",
                          "headers": {"Authorization": "Bearer apify_api_TOKEN1234"}}]
        assert db.system_settings.find_one({"_id": "apify.last_test_ok"})["value"]
        assert len(_audit(db, "apify.connection.test")) == 1
        s.clear_settings_cache()
        monkeypatch.setattr("httpx.AsyncClient", _fake_httpx(calls, 401, {}))
        r = client.post("/api/admin/apify/test-connection", cookies=who["manager"])
        assert r.status_code == 200
        assert r.json() == {"success": False, "status_code": 401,
                            "detail": "Token rejected by Apify API"}
        assert len(_audit(db, "apify.connection.test")) == 1   # failures are not "ok" audits

    def _seed_jobs(self, db):
        db.search_history.delete_many({})
        db.search_history.insert_many([
            {"run_id": "URL-A", "query": "https://facebook.com/acme", "platform": "facebook",
             "status": "completed", "created_at": NOW - timedelta(hours=3)},
            {"run_id": "URL-B", "query": "https://instagram.com/beta", "platform": "instagram",
             "status": "error", "created_at": NOW - timedelta(hours=2), "url_search": True,
             "intent": {"canonical_url": "https://instagram.com/beta", "limit": 10,
                        "max_comments_per_post": 5},
             "organization_id": "org-1", "user_id": "u-1", "created_by": "owner@acme.test"},
            {"run_id": "URL-C", "query": "a.b(c)", "platform": "facebook",
             "status": "running", "created_at": NOW - timedelta(hours=1)},
            {"run_id": "URL-OLD", "query": "old", "platform": "facebook",
             "status": "error", "created_at": NOW - timedelta(days=20)},
        ])

    def test_list_jobs_filters_and_pagination(self, env, who):
        client, db = env
        self._seed_jobs(db)
        def get(**p):
            return client.get("/api/admin/apify/jobs", params=p, cookies=who["viewer"])
        r = get()
        assert r.status_code == 200
        assert [j["run_id"] for j in r.json()["items"]] == ["URL-C", "URL-B", "URL-A", "URL-OLD"]
        assert r.json()["total"] == 4 and isinstance(r.json()["items"][0]["_id"], str)
        assert [j["run_id"] for j in get(status="error").json()["items"]] == ["URL-B", "URL-OLD"]
        assert get(platform="instagram").json()["total"] == 1
        assert [j["run_id"] for j in get(q="a.b(c)").json()["items"]] == ["URL-C"]  # literal, escaped
        assert get(q="url-a").json()["total"] == 1                                   # run_id, case-insensitive
        since = (NOW - timedelta(days=1)).isoformat()
        assert get(from_date=since).json()["total"] == 3
        page = get(offset=1, limit=2).json()
        assert [j["run_id"] for j in page["items"]] == ["URL-B", "URL-A"] and page["total"] == 4
        assert get(limit=0).status_code == 422

    def test_job_detail(self, env, who):
        client, db = env
        self._seed_jobs(db)
        r = client.get("/api/admin/apify/jobs/URL-B", cookies=who["viewer"])
        assert r.status_code == 200
        assert r.json()["run_id"] == "URL-B" and r.json()["organization_id"] == "org-1"
        assert r.json()["created_at"].endswith("+00:00")
        r = client.get("/api/admin/apify/jobs/NOPE", cookies=who["viewer"])
        assert r.status_code == 404 and r.json()["detail"] == "Job not found"

    def test_failed_jobs_window(self, env, who):
        client, db = env
        self._seed_jobs(db)
        r = client.get("/api/admin/apify/failed-jobs?days=7", cookies=who["viewer"])
        assert r.status_code == 200, r.text
        assert [j["run_id"] for j in r.json()["items"]] == ["URL-B"]
        assert r.json()["total"] == 1 and r.json()["days"] == 7
        r = client.get("/api/admin/apify/failed-jobs?days=30", cookies=who["viewer"])
        assert [j["run_id"] for j in r.json()["items"]] == ["URL-B", "URL-OLD"]

    def test_retry_failed_url_job(self, env, who):
        client, db = env
        self._seed_jobs(db)
        started = []
        with patch("app.api.routes.search._start",
                   side_effect=lambda key, fn, *a: started.append((key, fn)) or True):
            r = client.post("/api/admin/apify/jobs/URL-B/retry", cookies=who["manager"])
        assert r.status_code == 200, r.text
        new_id = r.json()["run_id"]
        assert r.json() == {"run_id": new_id, "status": "running", "retried_from": "URL-B",
                            "attempt": 1}
        new = db.search_history.find_one({"run_id": new_id})
        assert new["retry_count"] == 1 and new["retried_from"] == "URL-B"
        assert new["query"] == "https://instagram.com/beta" and new["url_search"] is True
        assert new["limit"] == 10 and new["intent"]["max_comments_per_post"] == 5
        # the retry belongs to the original tenant, not to the admin
        assert (new["organization_id"], new["user_id"], new["created_by"]) == (
            "org-1", "u-1", "owner@acme.test")
        assert len(started) == 1 and started[0][0] == f"url_search:{new_id}"
        thread = started[0][1].__self__
        assert thread.organization_id == "org-1" and thread.url == "https://instagram.com/beta"
        logs = _audit(db, "apify.job.retry")
        assert len(logs) == 1 and logs[0]["details"]["new_run_id"] == new_id

    def test_retry_guards(self, env, who):
        client, db = env
        self._seed_jobs(db)
        db.search_history.insert_many([
            {"run_id": "MAXED", "status": "error", "retry_count": 3, "url_search": True,
             "query": "https://x.test"},
            {"run_id": "KW", "status": "error", "query": "plumbers"},
        ])
        with patch("app.api.routes.search._start") as start:
            cases = {"URL-A": "Only failed or cancelled jobs can be retried",
                     "MAXED": "Maximum retry limit (3) reached for this job",
                     "KW": "Only URL-based jobs can be retried"}
            for run_id, detail in cases.items():
                r = client.post(f"/api/admin/apify/jobs/{run_id}/retry", cookies=who["manager"])
                assert r.status_code == 400 and r.json()["detail"] == detail, run_id
            assert client.post("/api/admin/apify/jobs/NOPE/retry",
                               cookies=who["manager"]).status_code == 404
            _assert_denied(client, "POST", "/api/admin/apify/jobs/URL-B/retry", who,
                           readers=("viewer",))
            start.assert_not_called()
        assert db.search_history.count_documents({}) == 6

    def test_cancel_running_job(self, env, who):
        client, db = env
        self._seed_jobs(db)
        r = client.post("/api/admin/apify/jobs/URL-A/cancel", cookies=who["manager"])
        assert r.status_code == 400 and r.json()["detail"] == "Job is not running"
        assert client.post("/api/admin/apify/jobs/NOPE/cancel",
                           cookies=who["manager"]).status_code == 404
        _assert_denied(client, "POST", "/api/admin/apify/jobs/URL-C/cancel", who, readers=("viewer",))
        assert not db.search_history.find_one({"run_id": "URL-C"}).get("cancel_requested")
        r = client.post("/api/admin/apify/jobs/URL-C/cancel", cookies=who["manager"])
        assert r.status_code == 200
        assert r.json() == {"success": True, "message": "Cancellation requested"}
        assert db.search_history.find_one({"run_id": "URL-C"})["cancel_requested"] is True
        logs = _audit(db, "apify.job.cancel")
        assert len(logs) == 1 and logs[0]["details"] == {"run_id": "URL-C"}

    @pytest.mark.parametrize("path", ["actors", "jobs", "jobs/URL-A", "failed-jobs"])
    def test_reads_require_platform_staff(self, env, who, path):
        client, _ = env
        _assert_denied(client, "GET", f"/api/admin/apify/{path}", who)


# ═══════════════════════════════════════════════════════════════════════════
# admin_leads
# ═══════════════════════════════════════════════════════════════════════════

def _seed_leads(db):
    db.ai_comments.delete_many({})
    post = db.facebook_posts.insert_one({"text": "Selling a flat", "url": "https://fb/p/1"}).inserted_id
    comment = db.facebook_comments.insert_one({"text": "price?", "post_ref": str(post)}).inserted_id
    ids = db.ai_comments.insert_many([
        {"commenter_name": "Alice Smith", "comment_text": "What's the price?", "phone": "9876543210",
         "is_lead": True, "lead_score": 90, "lifecycle_status": "new", "platform": "facebook",
         "intent": "buy", "analyzed_at": NOW - timedelta(days=1), "comment_ref": str(comment)},
        {"commenter_name": "Alice Jones", "comment_text": "nice", "email": "", "is_lead": False,
         "lead_score": 10, "lifecycle_status": "new", "platform": "instagram",
         "analyzed_at": NOW - timedelta(days=2), "comment_ref": "ref-2"},
        {"commenter_name": "Bob", "comment_text": "interested, mail me", "email": "bob@x.test",
         "is_lead": True, "lead_score": 60, "lifecycle_status": "contacted", "platform": "facebook",
         "analyzed_at": NOW - timedelta(days=10), "comment_ref": "ref-3"},
        {"commenter_name": "Carol (c++)", "comment_text": "dm", "is_lead": True,
         "platform": "youtube", "analyzed_at": NOW - timedelta(days=30), "comment_ref": "ref-4"},
    ]).inserted_ids
    return [str(i) for i in ids], str(comment), str(post)


class TestAdminLeads:
    def test_search_filters(self, env, who):
        client, db = env
        ids, _, _ = _seed_leads(db)
        def get(**p):
            return client.get("/api/admin/leads/search", params=p, cookies=who["viewer"])
        r = get()
        assert r.status_code == 200 and r.json()["total"] == 4
        assert [i["_id"] for i in r.json()["items"]] == ids             # analyzed_at desc
        assert {i["commenter_name"] for i in get(q="alice").json()["items"]} == {
            "Alice Smith", "Alice Jones"}
        assert [i["commenter_name"] for i in get(q="c++").json()["items"]] == ["Carol (c++)"]
        assert get(q="98765").json()["total"] == 1
        assert get(status="contacted").json()["items"][0]["commenter_name"] == "Bob"
        assert get(platform="facebook").json()["total"] == 2
        assert get(is_lead="false").json()["total"] == 1
        assert get(min_score=50, max_score=80).json()["items"][0]["commenter_name"] == "Bob"
        assert {i["commenter_name"] for i in get(has_contact="true").json()["items"]} == {
            "Alice Smith", "Bob"}
        assert [i["commenter_name"] for i in get(sort_by="lead_score", sort_dir=1).json()["items"]][-1] \
            == "Alice Smith"
        assert get(from_date=(NOW - timedelta(days=5)).isoformat()).json()["total"] == 2
        assert get(offset=3, limit=1).json()["items"][0]["_id"] == ids[3]
        assert get(min_score=101).status_code == 422

    def test_search_q_and_has_contact_combine_with_and(self, env, who):
        client, db = env
        _seed_leads(db)
        r = client.get("/api/admin/leads/search", params={"q": "alice", "has_contact": "true"},
                       cookies=who["viewer"])
        assert r.status_code == 200
        # only Alice Smith both matches "alice" AND has a contact
        assert [i["commenter_name"] for i in r.json()["items"]] == ["Alice Smith"]
        assert r.json()["total"] == 1

    def test_dossier(self, env, who):
        client, db = env
        ids, comment, post = _seed_leads(db)
        r = client.get(f"/api/admin/leads/{ids[0]}/dossier", cookies=who["viewer"])
        assert r.status_code == 200
        body = r.json()
        assert body["lead"]["_id"] == ids[0] and body["lead"]["commenter_name"] == "Alice Smith"
        assert body["source_comment"]["_id"] == comment
        assert body["source_post"]["_id"] == post and body["source_post"]["text"] == "Selling a flat"
        r = client.get(f"/api/admin/leads/{ids[2]}/dossier", cookies=who["viewer"])
        assert r.json()["source_comment"] is None and r.json()["source_post"] is None
        assert client.get(f"/api/admin/leads/{ObjectId()}/dossier",
                          cookies=who["viewer"]).status_code == 404
        assert client.get("/api/admin/leads/not-an-id/dossier",
                          cookies=who["viewer"]).status_code == 404

    def test_notes_add_list_delete(self, env, who):
        client, db = env
        ids, _, _ = _seed_leads(db)
        url = f"/api/admin/leads/{ids[0]}/notes"
        assert client.get(url, cookies=who["viewer"]).json() == {"notes": []}
        r = client.post(url, json={"text": "  called, no answer  "}, cookies=who["manager"])
        assert r.status_code == 200, r.text
        note = r.json()["note"]
        assert note["text"] == "called, no answer" and note["created_by"] == "manager@platform.test"
        stored = db.ai_comments.find_one({"_id": ObjectId(ids[0])})["notes"]
        assert len(stored) == 1 and stored[0]["id"] == note["id"]
        listed = client.get(url, cookies=who["viewer"]).json()["notes"]
        assert [n["id"] for n in listed] == [note["id"]]
        assert len(_audit(db, "lead.note.add")) == 1
        r = client.delete(f"{url}/{note['id']}", cookies=who["manager"])
        assert r.status_code == 200 and r.json() == {"status": "deleted"}
        assert db.ai_comments.find_one({"_id": ObjectId(ids[0])})["notes"] == []
        assert _audit(db, "lead.note.delete")[0]["details"] == {"lead_id": ids[0],
                                                                 "note_id": note["id"]}
        assert client.get(f"/api/admin/leads/{ObjectId()}/notes",
                          cookies=who["viewer"]).status_code == 404

    def test_notes_validation_and_roles(self, env, who):
        client, db = env
        ids, _, _ = _seed_leads(db)
        url = f"/api/admin/leads/{ids[0]}/notes"
        r = client.post(url, json={"text": " "}, cookies=who["manager"])
        assert r.status_code == 400 and r.json()["detail"] == "Note text is required"
        r = client.post(url, json={"text": "x" * 2001}, cookies=who["manager"])
        assert r.status_code == 400 and "max 2000" in r.json()["detail"]
        _assert_denied(client, "POST", url, who, json={"text": "hi"}, readers=("viewer",))
        _assert_denied(client, "DELETE", f"{url}/abc", who, readers=("viewer",))
        _assert_denied(client, "GET", url, who)
        assert "notes" not in db.ai_comments.find_one({"_id": ObjectId(ids[0])})

    def test_add_note_to_missing_lead_is_404(self, env, who):
        client, db = env
        _seed_leads(db)
        ghost = str(ObjectId())
        r = client.post(f"/api/admin/leads/{ghost}/notes", json={"text": "hello"},
                        cookies=who["manager"])
        assert r.status_code == 404, r.text          # the note was never saved anywhere
        assert _audit(db, "lead.note.add") == []

    def test_follow_ups(self, env, who):
        client, db = env
        ids, _, _ = _seed_leads(db)
        url = f"/api/admin/leads/{ids[2]}/follow-ups"
        r = client.post(url, json={"due_at": "2026-11-01T09:00:00Z", "note": "call back"},
                        cookies=who["manager"])
        assert r.status_code == 200, r.text
        fu = r.json()["follow_up"]
        assert fu["status"] == "pending" and fu["note"] == "call back"
        assert fu["due_at"].startswith("2026-11-01T09:00:00")
        stored = db.ai_comments.find_one({"_id": ObjectId(ids[2])})["follow_ups"]
        assert stored[0]["id"] == fu["id"] and stored[0]["due_at"].year == 2026
        assert len(_audit(db, "lead.followup.add")) == 1
        listed = client.get(url, cookies=who["viewer"]).json()["follow_ups"]
        assert [f["id"] for f in listed] == [fu["id"]]
        r = client.patch(f"{url}/{fu['id']}", json={"status": "done"}, cookies=who["manager"])
        assert r.status_code == 200 and r.json() == {"status": "updated"}
        stored = db.ai_comments.find_one({"_id": ObjectId(ids[2])})["follow_ups"][0]
        assert stored["status"] == "done" and stored["updated_at"]
        r = client.patch(f"{url}/{fu['id']}", json={"status": "snoozed"}, cookies=who["manager"])
        assert r.status_code == 400
        assert db.ai_comments.find_one({"_id": ObjectId(ids[2])})["follow_ups"][0]["status"] == "done"
        assert client.get(f"/api/admin/leads/{ObjectId()}/follow-ups",
                          cookies=who["viewer"]).status_code == 404

    def test_follow_up_validation_and_roles(self, env, who):
        client, db = env
        ids, _, _ = _seed_leads(db)
        url = f"/api/admin/leads/{ids[0]}/follow-ups"
        r = client.post(url, json={}, cookies=who["manager"])
        assert r.status_code == 400 and r.json()["detail"] == "due_at is required"
        r = client.post(url, json={"due_at": "next tuesday"}, cookies=who["manager"])
        assert r.status_code == 400 and "ISO 8601" in r.json()["detail"]
        _assert_denied(client, "POST", url, who, json={"due_at": "2026-11-01"}, readers=("viewer",))
        _assert_denied(client, "PATCH", f"{url}/x", who, json={"status": "done"}, readers=("viewer",))
        _assert_denied(client, "GET", url, who)
        assert "follow_ups" not in db.ai_comments.find_one({"_id": ObjectId(ids[0])})

    def test_bulk_actions(self, env, who):
        client, db = env
        ids, _, _ = _seed_leads(db)
        def post(body, c=who["manager"]):
            return client.post("/api/admin/leads/bulk-action", json=body, cookies=c)
        r = post({"action": "change_status", "lead_ids": ids[:2], "value": "qualified"})
        assert r.status_code == 200 and r.json() == {"success": True, "action": "change_status",
                                                     "updated": 2}
        assert db.ai_comments.count_documents({"lifecycle_status": "qualified"}) == 2
        r = post({"action": "add_tag", "lead_ids": ids, "value": "vip"})
        assert r.json()["updated"] == 4
        assert db.ai_comments.count_documents({"tags": "vip"}) == 4
        r = post({"action": "archive", "lead_ids": [ids[3]]})
        assert r.json()["updated"] == 1
        assert db.ai_comments.find_one({"_id": ObjectId(ids[3])})["lifecycle_status"] == "archived"
        logs = _audit(db, "leads.bulk.change_status")
        assert len(logs) == 1 and logs[0]["details"]["count"] == 2
        assert len(_audit(db, "leads.bulk.add_tag")) == 1 and len(_audit(db, "leads.bulk.archive")) == 1

    def test_bulk_delete_super_admin_only(self, env, who):
        client, db = env
        ids, _, _ = _seed_leads(db)
        r = client.post("/api/admin/leads/bulk-action", json={"action": "delete", "lead_ids": ids[:2]},
                        cookies=who["manager"])
        assert r.status_code == 403 and "super_admin" in r.json()["detail"]
        assert db.ai_comments.count_documents({}) == 4
        r = client.post("/api/admin/leads/bulk-action", json={"action": "delete", "lead_ids": ids[:2]},
                        cookies=who["super"])
        assert r.status_code == 200 and r.json()["updated"] == 2
        assert db.ai_comments.count_documents({}) == 2
        assert _audit(db, "leads.bulk.delete")[0]["actor_email"] == "root@platform.test"

    def test_bulk_validation_and_roles(self, env, who):
        client, db = env
        ids, _, _ = _seed_leads(db)
        def post(body):
            return client.post("/api/admin/leads/bulk-action", json=body, cookies=who["manager"])
        cases = [
            ({"action": "archive", "lead_ids": []}, "lead_ids is required and must be a list"),
            ({"action": "archive", "lead_ids": "abc"}, "lead_ids is required and must be a list"),
            ({"action": "archive", "lead_ids": [str(ObjectId()) for _ in range(501)]},
             "Maximum 500 leads per bulk action"),
            ({"lead_ids": ids}, "action is required"),
            ({"action": "archive", "lead_ids": ["nope"]}, "Invalid lead ID format"),
            ({"action": "explode", "lead_ids": ids}, "Unknown action: explode"),
            ({"action": "add_tag", "lead_ids": ids, "value": " "}, "tag value is required"),
        ]
        for body, detail in cases:
            r = post(body)
            assert r.status_code == 400 and r.json()["detail"] == detail, body
        r = post({"action": "change_status", "lead_ids": ids, "value": "deleted"})
        assert r.status_code == 400 and "Invalid status" in r.json()["detail"]
        _assert_denied(client, "POST", "/api/admin/leads/bulk-action", who,
                       json={"action": "archive", "lead_ids": ids}, readers=("viewer",))
        assert db.ai_comments.count_documents({"lifecycle_status": "archived"}) == 0
        assert db.audit_logs.count_documents({"action": {"$regex": "^leads.bulk"}}) == 0

    def test_data_quality(self, env, who):
        client, db = env
        db.ai_comments.delete_many({})
        r = client.get("/api/admin/leads/data-quality", cookies=who["viewer"])
        assert r.status_code == 200 and r.json() == {"total_leads": 0, "quality": {}}
        _seed_leads(db)
        q = client.get("/api/admin/leads/data-quality", cookies=who["viewer"]).json()
        assert q["total_leads"] == 3
        assert q["quality"]["has_email"] == {"count": 1, "pct": 33.3}
        assert q["quality"]["has_phone"] == {"count": 1, "pct": 33.3}
        assert q["quality"]["has_whatsapp"] == {"count": 0, "pct": 0.0}
        assert q["quality"]["has_any_contact"] == {"count": 2, "pct": 66.7}
        assert q["quality"]["has_score"] == {"count": 2, "pct": 66.7}
        assert q["quality"]["has_status"] == {"count": 2, "pct": 66.7}
        _assert_denied(client, "GET", "/api/admin/leads/data-quality", who)
        _assert_denied(client, "GET", "/api/admin/leads/search", who)


# ═══════════════════════════════════════════════════════════════════════════
# comment_filters
# ═══════════════════════════════════════════════════════════════════════════

CF = "/api/comment-filters"


def _rule(client, who, **body):
    r = client.post(f"{CF}/rules", json={"name": "Buyers", "include_keywords": ["price"], **body},
                    cookies=who["manager"])
    assert r.status_code == 200, r.text
    return r.json()["rule"]["id"]


class TestCommentFilterCategories:
    def test_category_crud(self, env, who):
        client, db = env
        db.comment_categories.delete_many({})
        r = client.post(f"{CF}/categories", json={"name": "  Solar ", "description": "panels",
                                                  "icon": "SUN!!", "keywords": "Solar Panel, inverter,solar panel"},
                        cookies=who["manager"])
        assert r.status_code == 200, r.text
        cat = r.json()["category"]
        assert cat["name"] == "Solar" and cat["icon"] == "SUN!" and cat["active"] is True
        assert cat["keywords"] == ["solar panel", "inverter"]
        stored = db.comment_categories.find_one({"_id": ObjectId(cat["_id"])})
        assert stored["created_by"] == "manager@platform.test"
        assert _audit(db, "comment_filter.category.create")[0]["details"]["name"] == "Solar"

        listed = client.get(f"{CF}/categories", cookies=who["viewer"]).json()
        assert [c["_id"] for c in listed["categories"]] == [cat["_id"]]
        assert listed["builtin"] and all("key" in b for b in listed["builtin"])

        r = client.put(f"{CF}/categories/{cat['_id']}", json={"keywords": ["Battery"], "active": False},
                       cookies=who["manager"])
        assert r.status_code == 200
        assert r.json()["category"]["keywords"] == ["battery"]
        assert r.json()["category"]["active"] is False and r.json()["category"]["name"] == "Solar"
        assert db.comment_categories.find_one({"_id": ObjectId(cat["_id"])})["active"] is False
        assert len(_audit(db, "comment_filter.category.update")) == 1
        # an inactive custom category no longer appears in the user catalog
        r = client.get(f"{CF}/catalog", cookies=who["tenant"])
        assert r.status_code == 200, r.text
        assert r.json()["custom_categories"] == []

        r = client.delete(f"{CF}/categories/{cat['_id']}", cookies=who["manager"])
        assert r.status_code == 200 and r.json() == {"success": True, "message": "Category deleted"}
        assert db.comment_categories.count_documents({}) == 0
        assert _audit(db, "comment_filter.category.delete")[0]["details"]["name"] == "Solar"
        assert client.delete(f"{CF}/categories/{cat['_id']}", cookies=who["manager"]).status_code == 404
        assert client.put(f"{CF}/categories/{cat['_id']}", json={"name": "x"},
                          cookies=who["manager"]).status_code == 404
        r = client.delete(f"{CF}/categories/bad-id", cookies=who["manager"])
        assert r.status_code == 400 and r.json()["detail"] == "Invalid id: bad-id"

    def test_category_validation_and_roles(self, env, who):
        client, db = env
        db.comment_categories.delete_many({})
        r = client.post(f"{CF}/categories", json={"name": " "}, cookies=who["manager"])
        assert r.status_code == 400 and r.json()["detail"] == "Name is required"
        r = client.post(f"{CF}/categories", json={"name": "Solar"}, cookies=who["manager"])
        cid = r.json()["category"]["_id"]
        r = client.post(f"{CF}/categories", json={"name": "Solar"}, cookies=who["manager"])
        assert r.status_code == 400 and "already exists" in r.json()["detail"]
        r = client.put(f"{CF}/categories/{cid}", json={"name": ""}, cookies=who["manager"])
        assert r.status_code == 400
        _assert_denied(client, "POST", f"{CF}/categories", who, json={"name": "X"}, readers=("viewer",))
        _assert_denied(client, "PUT", f"{CF}/categories/{cid}", who, json={"name": "X"},
                       readers=("viewer",))
        _assert_denied(client, "DELETE", f"{CF}/categories/{cid}", who, readers=("viewer",))
        _assert_denied(client, "GET", f"{CF}/categories", who)
        assert db.comment_categories.count_documents({}) == 1
        assert db.comment_categories.find_one({})["name"] == "Solar"

    def test_category_rename_to_existing_name_is_rejected_cleanly(self, env, who):
        client, db = env
        db.comment_categories.delete_many({})
        client.post(f"{CF}/categories", json={"name": "Solar"}, cookies=who["manager"])
        other = client.post(f"{CF}/categories", json={"name": "Wind"},
                            cookies=who["manager"]).json()["category"]["_id"]
        r = client.put(f"{CF}/categories/{other}", json={"name": "Solar"}, cookies=who["manager"])
        # same contract as create: duplicate name → 400, not a server error
        assert r.status_code == 400, r.text
        assert "already exists" in r.json()["detail"]
        assert db.comment_categories.find_one({"_id": ObjectId(other)})["name"] == "Wind"


class TestCommentFilterRules:
    def test_get_and_update_rule(self, env, who):
        client, db = env
        rid = _rule(client, who)
        r = client.get(f"{CF}/rules/{rid}", cookies=who["viewer"])
        assert r.status_code == 200
        rule = r.json()["rule"]
        assert rule["id"] == rid and rule["include_keywords"] == ["price"] and rule["active"] is False
        assert isinstance(rule["created_at"], str)
        r = client.put(f"{CF}/rules/{rid}", json={"name": "Hot buyers", "include_keywords": "Cost, PRICE",
                                                  "exclude_keywords": ["spam"], "match_mode": "bogus"},
                       cookies=who["manager"])
        assert r.status_code == 200, r.text
        rule = r.json()["rule"]
        assert rule["name"] == "Hot buyers" and rule["include_keywords"] == ["cost", "price"]
        assert rule["exclude_keywords"] == ["spam"] and rule["match_mode"] == "any"
        assert rule["updated_by"] == "manager@platform.test"
        stored = db.comment_filter_rules.find_one({"_id": ObjectId(rid)})
        assert stored["name"] == "Hot buyers"
        assert _audit(db, "comment_filter.rule.update")[0]["details"] == {"rule_id": rid,
                                                                          "name": "Hot buyers"}
        r = client.put(f"{CF}/rules/{rid}", json={"categories": ["no_such_category"]},
                       cookies=who["manager"])
        assert r.status_code == 400 and "Unknown categories" in r.json()["detail"]
        assert db.comment_filter_rules.find_one({"_id": ObjectId(rid)})["name"] == "Hot buyers"
        assert client.get(f"{CF}/rules/{ObjectId()}", cookies=who["viewer"]).status_code == 404
        assert client.get(f"{CF}/rules/xyz", cookies=who["viewer"]).status_code == 400
        assert client.put(f"{CF}/rules/{ObjectId()}", json={"name": "a"},
                          cookies=who["manager"]).status_code == 404

    def test_rule_accepts_custom_category_by_id(self, env, who):
        client, db = env
        db.comment_categories.delete_many({})
        cid = client.post(f"{CF}/categories", json={"name": "Solar", "keywords": ["inverter"]},
                          cookies=who["manager"]).json()["category"]["_id"]
        rid = _rule(client, who)
        r = client.put(f"{CF}/rules/{rid}", json={"name": "S", "categories": [cid]}, cookies=who["manager"])
        assert r.status_code == 200 and r.json()["rule"]["categories"] == [cid]

    def test_activate_is_exclusive_and_deactivate(self, env, who):
        client, db = env
        db.comment_filter_rules.delete_many({})
        a_id, b_id = _rule(client, who, name="A"), _rule(client, who, name="B")
        r = client.post(f"{CF}/rules/{a_id}/activate", cookies=who["manager"])
        assert r.status_code == 200 and r.json()["active_rule_id"] == a_id
        r = client.post(f"{CF}/rules/{b_id}/activate", cookies=who["manager"])
        assert r.status_code == 200
        assert [str(d["_id"]) for d in db.comment_filter_rules.find({"active": True})] == [b_id]
        listed = client.get(f"{CF}/rules", cookies=who["viewer"]).json()
        assert listed["active_rule_id"] == b_id
        assert len(_audit(db, "comment_filter.rule.activate")) == 2
        r = client.post(f"{CF}/rules/{b_id}/deactivate", cookies=who["manager"])
        assert r.status_code == 200 and r.json()["active_rule_id"] is None
        assert db.comment_filter_rules.count_documents({"active": True}) == 0
        assert _audit(db, "comment_filter.rule.deactivate")[0]["details"]["rule_id"] == b_id
        for action in ("activate", "deactivate"):
            assert client.post(f"{CF}/rules/{ObjectId()}/{action}",
                               cookies=who["manager"]).status_code == 404
            _assert_denied(client, "POST", f"{CF}/rules/{a_id}/{action}", who, readers=("viewer",))
        assert db.comment_filter_rules.count_documents({"active": True}) == 0

    def test_delete_rule(self, env, who):
        client, db = env
        rid = _rule(client, who)
        client.post(f"{CF}/rules/{rid}/activate", cookies=who["manager"])
        _assert_denied(client, "DELETE", f"{CF}/rules/{rid}", who, readers=("viewer",))
        r = client.delete(f"{CF}/rules/{rid}", cookies=who["manager"])
        assert r.status_code == 200
        assert r.json() == {"success": True, "was_active": True,
                            "message": "Rule deleted; no filter is now active"}
        assert db.comment_filter_rules.find_one({"_id": ObjectId(rid)}) is None
        assert _audit(db, "comment_filter.rule.delete")[0]["details"]["was_active"] is True
        assert client.delete(f"{CF}/rules/{rid}", cookies=who["manager"]).status_code == 404
        rid2 = _rule(client, who)
        assert client.delete(f"{CF}/rules/{rid2}", cookies=who["manager"]).json()["message"] == "Rule deleted"

    def test_reapply_and_browse_comments(self, env, who):
        client, db = env
        db.facebook_comments.delete_many({})
        c1, c2, c3, c4 = db.facebook_comments.insert_many([
            {"text": "What is the PRICE please", "platform": "facebook", "post_ref": "P1",
             "author_name": "Alice"},
            {"text": "lovely photo", "platform": "facebook", "post_ref": "P1"},
            {"text": "price of spam", "platform": "facebook", "post_ref": "P2"},
            {"text": "price?", "platform": "instagram", "post_ref": "P3"},
        ]).inserted_ids
        db.ai_comments.delete_many({})
        db.ai_comments.insert_one({"comment_ref": str(c1), "is_lead": True, "lead_score": 77})
        rid = _rule(client, who, exclude_keywords=["spam"])
        _assert_denied(client, "POST", f"{CF}/rules/{rid}/reapply", who, readers=("viewer",))
        r = client.post(f"{CF}/rules/{rid}/reapply?platform=facebook", cookies=who["manager"])
        assert r.status_code == 200, r.text
        summary = r.json()["summary"]
        assert (summary["processed"], summary["matched"], summary["not_matched"], summary["no_filter"]) \
            == (3, 1, 2, 0)
        assert summary["rule_id"] == rid and summary["processed_at"]
        assert db.facebook_comments.find_one({"_id": c1})["keyword_filter_status"] == "MATCHED"
        assert db.facebook_comments.find_one({"_id": c1})["matched_keywords"] == ["price"]
        assert db.facebook_comments.find_one({"_id": c3})["excluded_keywords"] == ["spam"]
        assert "keyword_filter_status" not in db.facebook_comments.find_one({"_id": c4})  # other platform
        assert db.comment_filter_results.count_documents({"rule_id": rid}) == 3
        assert db.facebook_comments.count_documents({}) == 4                # never deletes comments
        log = _audit(db, "comment_filter.rule.reapply")[0]
        assert log["details"] == {"rule_id": rid, "processed": 3, "matched": 1, "not_matched": 2,
                                  "no_filter": 0}
        assert client.post(f"{CF}/rules/{ObjectId()}/reapply",
                           cookies=who["manager"]).status_code == 404

        def get(**p):
            return client.get(f"{CF}/comments", params=p, cookies=who["viewer"])
        r = get(status="MATCHED")
        assert r.status_code == 200
        body = r.json()
        assert [i["_id"] for i in body["items"]] == [str(c1)]
        item = body["items"][0]
        assert item["commenter_name"] == "Alice" and item["comment_text"] == "What is the PRICE please"
        assert item["is_lead"] is True and item["lead_score"] == 77            # AI fields attached
        assert body["totals"] == {"MATCHED": 1, "NOT_MATCHED": 2, "NO_FILTER": 0, "all": 3}
        assert body["total"] == 1
        assert get(status="NOT_MATCHED").json()["total"] == 2
        assert get(status="all").json()["total"] == 4
        assert get(q="lovely").json()["items"][0]["_id"] == str(c2)
        assert get(post_id="P2").json()["items"][0]["_id"] == str(c3)
        assert get(matched_keyword="price").json()["total"] == 1
        assert get(rule_id=rid).json()["total"] == 3
        r = get(status="maybe")
        assert r.status_code == 400 and "status must be" in r.json()["detail"]
        _assert_denied(client, "GET", f"{CF}/comments", who)
        _assert_denied(client, "GET", f"{CF}/rules/{rid}", who)
        _assert_denied(client, "PUT", f"{CF}/rules/{rid}", who, json={"name": "x"}, readers=("viewer",))


# ═══════════════════════════════════════════════════════════════════════════
# Global Settings (settings.py)
# ═══════════════════════════════════════════════════════════════════════════

S = "/api/admin/settings"
KEY = "general.app.name"


class TestGlobalSettings:
    def _put(self, client, who, values, role="manager", reason=""):
        return client.put(S, json={"values": values, "reason": reason}, cookies=who[role])

    def test_round_trip_save_version_rollback_export_import(self, env, who):
        client, db = env
        from app.admin.settings import get_setting
        assert client.get(S, cookies=who["viewer"]).json()["settings"][KEY] == "LeadAI"

        # 1. save → version 1
        r = self._put(client, who, {KEY: "Acme Leads", "limits.max_posts_default": "25"},
                      reason="rebrand")
        assert r.status_code == 200, r.text
        assert r.json() == {"success": True, "changed": [KEY, "limits.max_posts_default"],
                            "version": 1}
        assert db.system_settings.find_one({"_id": KEY})["value"] == "Acme Leads"
        assert db.system_settings.find_one({"_id": "limits.max_posts_default"})["value"] == 25
        # 2. save again → version bump to 2
        r = self._put(client, who, {KEY: "Zeta CRM"})
        assert r.json()["version"] == 2 and r.json()["changed"] == [KEY]
        got = client.get(S, cookies=who["viewer"]).json()
        assert got["version"] == 2 and got["settings"][KEY] == "Zeta CRM"
        assert got["meta"][KEY]["is_default"] is False
        audits = _audit(db, "settings.update")
        assert [a["details"]["version"] for a in audits] == [1, 2]
        assert audits[0]["details"]["changed"] == [KEY, "limits.max_posts_default"]

        # history
        hist = client.get(f"{S}/history", cookies=who["viewer"]).json()["versions"]
        assert [h["version"] for h in hist] == [2, 1]
        assert "snapshot" not in hist[0] and "_id" not in hist[0]
        assert hist[1]["changed"][KEY] == {"old": "LeadAI", "new": "Acme Leads"}
        assert hist[1]["reason"] == "rebrand" and hist[1]["changed_by"] == "manager@platform.test"
        entry = client.get(f"{S}/history/1", cookies=who["viewer"]).json()["entry"]
        assert entry["version"] == 1 and entry["snapshot"][KEY] == "Acme Leads"
        assert isinstance(entry["_id"], str)
        assert client.get(f"{S}/history/99", cookies=who["viewer"]).status_code == 404

        # 3. roll back to v1 → old value, new version 3
        r = client.post(f"{S}/history/1/restore", json={"reason": "undo"}, cookies=who["manager"])
        assert r.status_code == 200, r.text
        assert r.json() == {"success": True, "applied": [KEY], "version": 3}
        assert db.system_settings.find_one({"_id": KEY})["value"] == "Acme Leads"
        assert client.get(S, cookies=who["viewer"]).json()["settings"][KEY] == "Acme Leads"
        assert get_setting(KEY) == "Acme Leads"
        log = _audit(db, "settings.restore")
        assert len(log) == 1 and log[0]["details"] == {"from_version": 1, "to_version": 3,
                                                        "applied": 1}
        assert db.settings_history.find_one({"version": 3})["reason"] == "undo"

        # 4. export
        r = client.get(f"{S}/export", cookies=who["viewer"])
        assert r.status_code == 200
        exported = r.json()
        assert exported["schema"] == "leadai.settings.v1" and exported["version"] == 3
        assert exported["settings"][KEY] == "Acme Leads"
        assert exported["settings"]["limits.max_posts_default"] == 25
        assert "apify.token" not in exported["settings"]
        dl = client.get(f"{S}/export?download=1", cookies=who["viewer"])
        assert dl.headers["content-disposition"] == 'attachment; filename="settings-export.json"'
        assert dl.json()["settings"] == exported["settings"]

        # 5. drift, then import the export → back to the exported state
        self._put(client, who, {KEY: "Drifted", "limits.max_posts_default": 40})
        assert client.get(S, cookies=who["viewer"]).json()["version"] == 4
        prev = client.post(f"{S}/import/preview", json={"payload": exported}, cookies=who["viewer"])
        assert prev.status_code == 200
        assert prev.json()["valid"] is True and prev.json()["invalid"] == {}
        assert prev.json()["count"] == len(exported["settings"])
        r = client.post(f"{S}/import", json={"payload": exported}, cookies=who["manager"])
        assert r.status_code == 200, r.text
        assert sorted(r.json()["applied"]) == sorted([KEY, "limits.max_posts_default"])
        assert r.json()["skipped"] == {} and r.json()["version"] == 5
        assert _audit(db, "settings.import")[0]["details"] == {"applied": 2, "skipped": 0, "version": 5}
        again = client.get(f"{S}/export", cookies=who["viewer"]).json()
        assert again["settings"] == exported["settings"]          # equality after the round trip
        # importing the same file again is a no-op (no new version)
        r = client.post(f"{S}/import", json={"payload": exported}, cookies=who["manager"])
        assert r.json()["applied"] == [] and r.json()["version"] == 0
        assert client.get(S, cookies=who["viewer"]).json()["version"] == 5

    def test_put_validation_and_noop(self, env, who):
        client, db = env
        r = client.put(S, json={"values": {}}, cookies=who["manager"])
        assert r.status_code == 400 and "non-empty" in r.json()["detail"]
        r = self._put(client, who, {"limits.max_posts_default": 0, "nope.key": 1, KEY: "ok"})
        assert r.status_code == 400
        assert r.json()["detail"]["errors"] == {"limits.max_posts_default": "Minimum is 1",
                                                "nope.key": "Unknown setting key"}
        assert db.system_settings.find_one({"_id": KEY}) is None        # nothing written
        r = self._put(client, who, {KEY: "LeadAI"})                     # equals the default
        assert r.json() == {"success": True, "changed": [], "version": 0}
        assert db.settings_history.count_documents({}) == 0 and _audit(db, "settings.update") == []

    def test_import_skips_unknown_and_invalid(self, env, who):
        client, db = env
        payload = {"settings": {KEY: "Imported", "limits.max_posts_default": "many",
                                "apify.token": "steal-me", "security.session_epoch": 9}}
        prev = client.post(f"{S}/import/preview", json={"payload": payload}, cookies=who["viewer"])
        assert prev.json()["valid"] is False and prev.json()["valid_keys"] == [KEY]
        assert set(prev.json()["invalid"]) == {"limits.max_posts_default", "apify.token",
                                               "security.session_epoch"}
        assert db.system_settings.find_one({"_id": KEY}) is None        # preview writes nothing
        r = client.post(f"{S}/import", json={"payload": payload}, cookies=who["manager"])
        assert r.status_code == 200
        assert r.json()["applied"] == [KEY] and r.json()["version"] == 1
        assert r.json()["skipped"]["apify.token"] == "Unknown setting key"
        assert db.system_settings.find_one({"_id": "apify.token"}) is None
        assert db.system_settings.find_one({"_id": "security.session_epoch"}) is None
        for url in (f"{S}/import", f"{S}/import/preview"):
            r = client.post(url, json={"payload": {"settings": []}}, cookies=who["manager"])
            assert r.status_code == 400 and r.json()["detail"] == "payload.settings must be an object"

    def test_reset(self, env, who):
        client, db = env
        self._put(client, who, {KEY: "Acme", "general.app.tagline": "Leads!",
                                "limits.max_posts_default": 30})
        r = client.post(f"{S}/reset", json={"keys": ["general.app.tagline"]}, cookies=who["manager"])
        assert r.status_code == 200
        assert r.json() == {"success": True, "reset": ["general.app.tagline"], "version": 2}
        assert db.system_settings.find_one({"_id": "general.app.tagline"}) is None
        assert db.system_settings.find_one({"_id": KEY})["value"] == "Acme"
        r = client.post(f"{S}/reset", json={"section": "general"}, cookies=who["manager"])
        assert r.json()["reset"] == [KEY] and r.json()["version"] == 3
        assert db.system_settings.find_one({"_id": "limits.max_posts_default"})["value"] == 30
        r = client.post(f"{S}/reset", json={"all": True}, cookies=who["manager"])
        assert r.json()["reset"] == ["limits.max_posts_default"]
        assert client.get(S, cookies=who["viewer"]).json()["settings"]["limits.max_posts_default"] == 20
        assert [a["details"]["version"] for a in _audit(db, "settings.reset")] == [2, 3, 4]
        for body, detail in (({}, "Provide 'section', 'keys' or all=true"),
                             ({"section": "nope"}, "Unknown section: nope"),
                             ({"keys": ["x.y"]}, "Unknown keys: x.y")):
            r = client.post(f"{S}/reset", json=body, cookies=who["manager"])
            assert r.status_code == 400 and r.json()["detail"] == detail

    def test_upload_branding(self, env, who):
        client, db = env
        uploads = []

        class _Store:
            async def upload_file(self, content, filename, mime, folder="media"):
                uploads.append((content, filename, mime, folder))
                return f"/static/{folder}/{filename}"

        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
        with patch("app.api.routes.settings.get_storage_provider", return_value=_Store()):
            r = client.post(f"{S}/upload", files={"file": ("logo.PNG", png, "image/png")},
                            cookies=who["manager"])
            assert r.status_code == 200, r.text
            url = r.json()["url"]
            assert r.json()["success"] is True and url.startswith("/static/uploads/branding/")
            assert url.endswith(".png") and "logo" not in url
            assert uploads[0][0] == png and uploads[0][3] == "uploads/branding"
            log = _audit(db, "settings.upload")
            assert len(log) == 1 and log[0]["details"]["kind"] == "png"
            bad = [
                (("x.exe", b"MZ", "application/octet-stream"), "Unsupported file type"),
                (("x.png", b"", "image/png"), "Empty file"),
                (("x.png", b"GIF89a....", "image/png"), "Content does not match .png"),
                (("x.webp", b"RIFF0000JUNK", "image/webp"), "Not a valid WEBP image"),
                (("x.svg", b"hello", "image/svg+xml"), "Not a valid SVG file"),
                (("x.svg", b"<svg onload=alert(1)></svg>", "image/svg+xml"), "SVG contains scripts"),
                (("x.png", b"\x89PNG\r\n\x1a\n" + b"0" * (2 * 1024 * 1024), "image/png"), "File too large"),
            ]
            for f, detail in bad:
                r = client.post(f"{S}/upload", files={"file": f}, cookies=who["manager"])
                assert r.status_code == 400 and detail in r.json()["detail"], f[0]
            r = client.post(f"{S}/upload", files={"file": ("ok.svg", b"<svg></svg>", "image/svg+xml")},
                            cookies=who["manager"])
            assert r.status_code == 200
            _assert_denied(client, "POST", f"{S}/upload", who,
                           files={"file": ("logo.png", png, "image/png")}, readers=("viewer",))
        assert len(uploads) == 2 and len(_audit(db, "settings.upload")) == 2

    def test_write_routes_need_manager(self, env, who):
        client, db = env
        db.settings_history.insert_one({"version": 1, "snapshot": {KEY: "Evil"}, "changed": {}})
        _assert_denied(client, "POST", f"{S}/history/1/restore", who, json={}, readers=("viewer",))
        _assert_denied(client, "POST", f"{S}/import", who,
                       json={"payload": {"settings": {KEY: "Evil"}}}, readers=("viewer",))
        _assert_denied(client, "POST", f"{S}/reset", who, json={"all": True}, readers=("viewer",))
        _assert_denied(client, "PUT", S, who, json={"values": {KEY: "Evil"}}, readers=("viewer",))
        assert db.system_settings.find_one({"_id": KEY}) is None
        assert client.post(f"{S}/history/42/restore", json={},
                           cookies=who["manager"]).status_code == 404
        for path in ("/export", "/history", "/history/1"):
            _assert_denied(client, "GET", S + path, who)
        _assert_denied(client, "POST", f"{S}/import/preview", who, json={"payload": {"settings": {}}})


# ═══════════════════════════════════════════════════════════════════════════
# admin_cms
# ═══════════════════════════════════════════════════════════════════════════

CMS = "/api/admin/cms"


class TestAdminCms:
    def test_schema(self, env, who):
        client, _ = env
        r = client.get(f"{CMS}/schema", cookies=who["viewer"])
        assert r.status_code == 200
        body = r.json()
        assert body["section_types"] and "header" in body["navigation_locations"]
        assert {"key", "label", "kind"} <= set(body["settings"][0])
        assert "title" in body["section_fields"]
        _assert_denied(client, "GET", f"{CMS}/schema", who)

    def test_media_list(self, env, who):
        client, db = env
        db.website_media.insert_many([
            {"filename": f"f{i}.png", "url": f"/static/media/f{i}.png",
             "uploaded_at": NOW - timedelta(minutes=i)} for i in range(3)])
        r = client.get(f"{CMS}/media", cookies=who["viewer"])
        assert r.status_code == 200
        assert [m["filename"] for m in r.json()["items"]] == ["f0.png", "f1.png", "f2.png"]
        assert r.json()["total"] == 3 and isinstance(r.json()["items"][0]["_id"], str)
        r = client.get(f"{CMS}/media?offset=1&limit=1", cookies=who["viewer"])
        assert [m["filename"] for m in r.json()["items"]] == ["f1.png"] and r.json()["total"] == 3
        _assert_denied(client, "GET", f"{CMS}/media", who)

    def _page(self, client, who, slug="qa-page"):
        r = client.post(f"{CMS}/pages", json={"slug": slug, "title": "First title"}, cookies=who["super"])
        assert r.status_code == 200, r.text
        return r.json()["id"]

    def test_versions_and_restore(self, env, who):
        client, db = env
        pid = self._page(client, who)
        assert client.get(f"{CMS}/pages/{pid}/versions", cookies=who["viewer"]).json() == {"items": []}
        assert client.post(f"{CMS}/pages/{pid}/publish", cookies=who["super"]).status_code == 200
        client.put(f"{CMS}/pages/{pid}", json={"title": "Second title"}, cookies=who["super"])
        client.post(f"{CMS}/pages/{pid}/publish", cookies=who["super"])
        client.put(f"{CMS}/pages/{pid}", json={"title": "Broken draft"}, cookies=who["super"])
        r = client.get(f"{CMS}/pages/{pid}/versions", cookies=who["viewer"])
        assert r.status_code == 200
        items = r.json()["items"]
        assert [(v["index"], v["title"]) for v in items] == [(0, "First title"), (1, "Second title")]
        assert all(v["snapshot_by"] == "root@platform.test" for v in items)

        _assert_denied(client, "POST", f"{CMS}/pages/{pid}/restore/0", who, readers=("viewer", "manager"))
        r = client.post(f"{CMS}/pages/{pid}/restore/0", cookies=who["super"])
        assert r.status_code == 200
        assert r.json() == {"id": pid, "status": "restored_to_draft", "version_index": 0}
        doc = db.website_pages.find_one({"_id": ObjectId(pid)})
        assert doc["title"] == "First title" and doc["has_draft_changes"] is True
        assert doc["live"]["title"] == "Second title"          # live copy untouched until publish
        log = _audit(db, "cms.page.restore")
        assert len(log) == 1 and log[0]["resource_id"] == pid
        assert log[0]["details"] == {"version_index": 0}
        for bad in (2, -1):
            r = client.post(f"{CMS}/pages/{pid}/restore/{bad}", cookies=who["super"])
            assert r.status_code == 404 and r.json()["detail"] == "Page or version not found"
        assert client.post(f"{CMS}/pages/{ObjectId()}/restore/0", cookies=who["super"]).status_code == 404
        assert client.get(f"{CMS}/pages/{ObjectId()}/versions", cookies=who["viewer"]).status_code == 404
        _assert_denied(client, "GET", f"{CMS}/pages/{pid}/versions", who)
        assert len(_audit(db, "cms.page.restore")) == 1

    def _faq(self, client, who, q):
        r = client.post(f"{CMS}/faq", json={"question": q, "answer": f"A to {q}"}, cookies=who["super"])
        assert r.status_code == 200, r.text
        return r.json()["id"]

    def test_faq_update(self, env, who):
        client, db = env
        fid = self._faq(client, who, "Q1?")
        r = client.put(f"{CMS}/faq/{fid}", json={"question": "  New <b>Q</b>?  ", "enabled": False},
                       cookies=who["super"])
        assert r.status_code == 200 and r.json() == {"id": fid, "status": "updated"}
        doc = db.website_faq.find_one({"_id": ObjectId(fid)})
        assert doc["question"] == "New Q?" and doc["enabled"] is False and doc["answer"] == "A to Q1?"
        assert _audit(db, "cms.faq.update")[0]["resource_id"] == fid
        assert client.get(f"{CMS}/faq", cookies=who["viewer"]).json()["faq"][0]["question"] == "New Q?"
        r = client.put(f"{CMS}/faq/{fid}", json={"question": ""}, cookies=who["super"])
        assert r.status_code == 422 and r.json()["detail"] == "question is required"
        assert client.put(f"{CMS}/faq/{ObjectId()}", json={"answer": "x"},
                          cookies=who["super"]).status_code == 404
        _assert_denied(client, "PUT", f"{CMS}/faq/{fid}", who, json={"question": "Hacked?"},
                       readers=("viewer", "manager"))
        assert db.website_faq.find_one({"_id": ObjectId(fid)})["question"] == "New Q?"
        assert len(_audit(db, "cms.faq.update")) == 1

    def test_faq_delete(self, env, who):
        client, db = env
        fid = self._faq(client, who, "Q1?")
        _assert_denied(client, "DELETE", f"{CMS}/faq/{fid}", who, readers=("viewer", "manager"))
        assert db.website_faq.count_documents({}) == 1
        r = client.delete(f"{CMS}/faq/{fid}", cookies=who["super"])
        assert r.status_code == 200 and r.json() == {"id": fid, "status": "deleted"}
        assert db.website_faq.count_documents({}) == 0
        assert _audit(db, "cms.faq.delete")[0]["resource_id"] == fid
        r = client.delete(f"{CMS}/faq/{fid}", cookies=who["super"])
        assert r.status_code == 404 and r.json()["detail"] == "FAQ item not found"
        assert client.get(f"{CMS}/faq", cookies=who["viewer"]).json()["faq"] == []

    def test_faq_reorder(self, env, who):
        client, db = env
        ids = [self._faq(client, who, f"Q{i}?") for i in range(3)]
        assert [d["order"] for d in db.website_faq.find({}).sort("order", 1)] == [1, 2, 3]
        new_order = [ids[2], ids[0], ids[1]]
        _assert_denied(client, "POST", f"{CMS}/faq/reorder", who, json={"ids": new_order},
                       readers=("viewer", "manager"))
        r = client.post(f"{CMS}/faq/reorder", json={"ids": new_order + [str(ObjectId())]},
                        cookies=who["super"])
        assert r.status_code == 200 and r.json() == {"status": "reordered", "count": 3}
        listed = client.get(f"{CMS}/faq", cookies=who["viewer"]).json()["faq"]
        assert [f["_id"] for f in listed] == new_order
        assert [f["order"] for f in listed] == [1, 2, 3]
        log = _audit(db, "cms.faq.reorder")
        assert len(log) == 1 and log[0]["details"] == {"count": 3}
        for body in ({"ids": []}, {"ids": "abc"}, {}):
            r = client.post(f"{CMS}/faq/reorder", json=body, cookies=who["super"])
            assert r.status_code == 422 and r.json()["detail"] == "ids must be a non-empty list"
        assert len(_audit(db, "cms.faq.reorder")) == 1
