"""
Keyword-search API (app/api/routes/keyword_search.py + public v1) with the
background thread mocked — no Apify call is ever made.

Proves: estimate; confirm required (409 + estimate); plan / trial caps and
402s; feature and platform switches (LinkedIn off by default); filter
validation (422); candidates pagination + filters; re-apply recomputes
without scraping and marks needs_sampling; continue charges only after
confirm; presets; tenant isolation (404 + security event); rate limits
(429); the public v1 endpoints and their scopes; plan / trial migrations.

Run:  python -m pytest tests/test_keyword_api.py -q -p no:cacheprovider
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from tests.conftest import seed_site_account

NOW = datetime.now(timezone.utc)
BODY = {"keywords": ["bakery", "cakes"], "platforms": ["instagram", "facebook"],
        "filters": {"min_followers": 1000, "min_avg_comments": 2, "recent_posts_to_analyze": 10},
        "limits": {"max_candidates": 40}}


class _NoopThread:
    started: list = []

    def __init__(self, run_id, job_id=None, **kw):
        _NoopThread.started.append((run_id, job_id))

    def run(self):
        return None


@pytest.fixture
def env():
    _NoopThread.started = []
    with patch("app.admin.settings.is_maintenance_enabled", return_value=False), \
            patch("app.discovery.pipeline.KeywordSearchThread", _NoopThread), \
            patch("app.connectors.apify_connector.ApifyConnector._call_actor",
                  side_effect=AssertionError("no Apify call in API tests")):
        from app.main import app
        with TestClient(app) as client:
            from app.db.mongo import get_sync_db
            yield client, get_sync_db()


def _session(db, uid, email, org, role="owner"):
    from app.auth.service import COOKIE_NAME, build_session_value, create_tracked_session
    claims = {"user_id": uid, "email": email, "name": email, "scope": "site", "organization_id": org,
              "org_role": role}
    return {COOKIE_NAME: build_session_value(create_tracked_session(claims))}


def _org(db, email="owner@acme.example", plan="pro", tokens=1000, role="owner"):
    uid, org = seed_site_account(email, db=db, org_name=email.split("@")[1], role=role)
    if plan:
        db.organizations.update_one({"_id": ObjectId(org)}, {"$set": {"plan_id": plan}})
    if tokens is not None:
        db.token_balances.insert_one({"organization_id": org, "allocated": tokens, "used": 0,
                                      "remaining": tokens, "source": "plan"})
    return {"uid": uid, "org": org, "email": email, "cookies": _session(db, uid, email, org, role)}


def _member(db, org, email):
    uid = str(db.users.insert_one({"email": email, "name": email, "status": "active",
                                   "default_organization_id": org}).inserted_id)
    db.organization_members.insert_one({"organization_id": org, "user_id": uid, "email": email,
                                        "role": "member", "status": "active"})
    return {"uid": uid, "org": org, "email": email, "cookies": _session(db, uid, email, org, "member")}


def post(client, who, url, body):
    client.cookies.clear()
    return client.post(url, json=body, cookies=who["cookies"])


def get(client, who, url, **params):
    client.cookies.clear()
    return client.get(url, params=params, cookies=who["cookies"])


def _seed_run(db, who, run_id="KWSEED1", status="completed"):
    owner = {"organization_id": who["org"], "user_id": who["uid"], "created_by": who["email"]}
    db.search_history.insert_one({
        **owner, "run_id": run_id, "search_type": "keyword", "status": status, "phase": "completed",
        "keyword_search": {"keywords": ["bakery"], "platforms": ["instagram", "facebook"],
                           "filters": {"min_followers": 1000, "recent_posts_to_analyze": 10},
                           "strict": False, "limits": {"max_candidates": 50, "recent_posts": 10,
                                                       "comments_per_post": 10}},
        "stages": {"discovery": "done", "enrichment": "done", "sampling": "done", "deep_scrape": "done"},
        "discovery_pairs_done": ["instagram|bakery", "facebook|bakery"],
        "tokens_charged": {"discovery": 10}, "stage_counts": {}, "created_at": NOW, "updated_at": NOW,
        "completed_at": None if status == "running" else NOW})

    def cand(pid, platform, followers, status, stage, posts=None, page_ref=None, deep=None):
        prof = {"platform": platform, "profile_id": pid, "name": pid, "followers": followers,
                "posts_count": 40, "bio": "bakery", "contacts": {"emails": [], "phones": [], "websites": []},
                "verified": None, "is_business": True, "private": False}
        db.discovery_candidates.insert_one({
            **owner, "search_run_id": run_id, "platform": platform, "profile_id": pid,
            "url": f"https://www.{platform}.com/{pid}", "name": pid, "keywords": ["bakery"],
            "status": status, "stage_reached": stage, "reasons": [], "unsupported": [], "unknown": [],
            "metrics": {"followers": followers}, "profile": prof, "recent_posts": posts or [],
            "posts_raw": [{"secret_raw": True}] * len(posts or []), "page_ref": page_ref,
            "deep_status": deep, "created_at": NOW})
    recent = [{"id": str(i), "url": f"https://x/{i}", "comments_count": 4, "likes_count": 40,
               "shares_count": None, "posted_at": (NOW - timedelta(days=3)).isoformat()} for i in range(10)]
    cand("ig_big", "instagram", 50000, "passed", "scraped", recent, page_ref="p1", deep="done")
    cand("ig_mid", "instagram", 2000, "passed", "scraped", recent, page_ref="p2", deep="done")
    cand("ig_small", "instagram", 500, "rejected", "enriched")
    cand("fb_small", "facebook", 800, "rejected", "enriched")
    cand("fb_err", "facebook", None, "error", "discovered")
    return run_id


# ── capabilities / estimate / start ─────────────────────────────────────────

def test_capabilities(env):
    client, db = env
    a = _org(db)
    r = get(client, a, "/api/keyword-search/capabilities")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["platforms"]["facebook"]["filters"]["min_posts"]["support"] == "unsupported"
    assert body["platforms"]["instagram"]["filters"]["min_followers"]["stage"] == "discovery"
    assert body["platform_enabled"]["linkedin"] is False and body["platform_enabled"]["instagram"] is True
    assert body["default_filters"] == {"min_followers": 1000, "min_posts": 10, "recent_posts_to_analyze": 10,
                                       "min_avg_comments": 2, "active_within_days": 90}
    # pro plan 5 / 250 / 30, Global Settings 5 / 200 / 30 -> the lower of each
    caps = body["caps"]
    assert (caps["max_keywords"], caps["max_candidates"], caps["max_recent_posts"]) == (5, 200, 30)
    assert body["token_costs"] == {"discover": 5, "enrich": 1, "sample": 1, "collect": 5}


def test_public_config_exposes_keyword_switches(env):
    client, _ = env
    cfg = client.get("/api/public/config").json()
    ks = cfg["keyword_search"]
    assert cfg["features"]["keyword_search"] is True and ks["enabled"] is True
    assert ks["platforms"]["linkedin"] is False and ks["defaults"]["min_followers"] == 1000
    assert ks["caps"]["max_candidates"] == 200


def test_estimate(env):
    client, db = env
    a = _org(db)
    r = post(client, a, "/api/keyword-search/estimate", BODY)
    assert r.status_code == 200, r.text
    est = r.json()
    # 2 keywords x 2 platforms x 5; 40 candidates x collect 5; facebook needs enrichment;
    # worst case every candidate is sampled (an Instagram result may carry < 10 posts)
    assert est["tokens"]["discover"] == 20
    assert est["tokens"]["collect"] == 200
    assert est["tokens"]["enrich"] == 20 and est["tokens"]["sample"] == 40
    assert est["tokens"]["total_max"] == 280
    assert est["candidates_max"] == 40 and est["apify_usd_estimate"] > 0
    assert est["per_platform"]["instagram"]["posts_from_discovery"] is True
    assert est["tokens_available"] == 1000 and est["notes"]
    assert db.search_history.count_documents({}) == 0


def test_start_requires_confirm(env):
    client, db = env
    a = _org(db)
    r = post(client, a, "/api/keyword-search", BODY)
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "CONFIRM_REQUIRED" and detail["estimate"]["tokens"]["total_max"] == 280
    assert db.search_history.count_documents({}) == 0 and not _NoopThread.started
    r = post(client, a, "/api/keyword-search", {**BODY, "confirm": True})
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    run = db.search_history.find_one({"run_id": run_id})
    assert run["search_type"] == "keyword" and run["organization_id"] == a["org"] and run["user_id"] == a["uid"]
    assert run["keyword_search"]["keywords"] == ["bakery", "cakes"]
    assert run["stages"] == {s: "pending" for s in ("discovery", "enrichment", "sampling", "deep_scrape")}
    assert _NoopThread.started == [(run_id, None)]
    assert db.token_balances.find_one({"organization_id": a["org"]})["remaining"] == 1000   # charged per stage
    assert db.audit_logs.find_one({"action": "search.keyword_started", "resource_id": run_id})
    status = get(client, a, f"/api/keyword-search/{run_id}")
    assert status.status_code == 200 and status.json()["status"] == "running"


def test_plan_caps_and_clamping(env):
    client, db = env
    a = _org(db, plan="starter")      # starter: 3 keywords, 100 candidates, 20 posts
    r = post(client, a, "/api/keyword-search/estimate",
             {"keywords": ["a", "b", "c", "d"], "platforms": ["instagram"]})
    assert r.status_code == 402 and r.json()["detail"]["code"] == "PLAN_LIMIT"
    assert r.json()["detail"]["metric"] == "keyword_max_keywords" and r.json()["detail"]["limit"] == 3
    r = post(client, a, "/api/keyword-search/estimate",
             {"keywords": ["a"], "platforms": ["instagram"], "limits": {"max_candidates": 5000},
              "filters": {"recent_posts_to_analyze": 99}})
    assert r.status_code == 200, r.text
    assert r.json()["limits"]["max_candidates"] == 100 and r.json()["limits"]["recent_posts"] == 20
    assert any("lowered" in n for n in r.json()["notes"])
    # starter has no YouTube
    r = post(client, a, "/api/keyword-search/estimate", {"keywords": ["a"], "platforms": ["youtube"]})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "FEATURE_NOT_AVAILABLE"


def test_free_plan_has_no_keyword_search(env):
    client, db = env
    a = _org(db, plan="free")
    r = post(client, a, "/api/keyword-search", {**BODY, "platforms": ["facebook"], "confirm": True})
    assert r.status_code == 403 and r.json()["detail"]["feature"] == "keyword_search"


def test_trial_caps(env):
    client, db = env
    a = _org(db, plan=None, tokens=500)
    cfg = {"allowed_platforms": ["instagram", "facebook"], "max_searches": 10, "posts_per_search": 20,
           "comments_per_post": 30, "max_leads": 200, "ai_enabled": True, "max_users": 1}
    db.organizations.update_one({"_id": ObjectId(a["org"])}, {"$set": {
        "status": "demo", "demo": {"expires_at": NOW + timedelta(days=3), "config": cfg}}})
    # a trial approved before keyword search existed gets the seed: 1 keyword, 20 candidates
    r = post(client, a, "/api/keyword-search/estimate", {"keywords": ["a", "b"], "platforms": ["instagram"]})
    assert r.status_code == 402 and r.json()["detail"]["limit"] == 1
    r = post(client, a, "/api/keyword-search/estimate",
             {"keywords": ["a"], "platforms": ["instagram"], "limits": {"max_candidates": 100}})
    assert r.status_code == 200 and r.json()["limits"]["max_candidates"] == 20
    db.organizations.update_one({"_id": ObjectId(a["org"])}, {"$set": {"demo.config.keyword_search_enabled": False}})
    r = post(client, a, "/api/keyword-search/estimate", {"keywords": ["a"], "platforms": ["instagram"]})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "FEATURE_NOT_AVAILABLE"


def test_not_enough_tokens_for_discovery(env):
    client, db = env
    a = _org(db, tokens=3)
    r = post(client, a, "/api/keyword-search", {**BODY, "confirm": True})
    assert r.status_code == 402 and r.json()["detail"]["code"] == "TOKENS_EXHAUSTED"
    assert db.search_history.count_documents({}) == 0 and not _NoopThread.started


def test_switches(env):
    client, db = env
    a = _org(db)
    from app.admin.settings import set_setting
    r = post(client, a, "/api/keyword-search/estimate", {"keywords": ["a"], "platforms": ["linkedin"]})
    assert r.status_code == 403 and r.json()["detail"]["errorType"] == "platform_disabled"
    assert r.json()["detail"]["platforms"] == ["linkedin"]
    set_setting("keyword.platform.facebook.enabled", False)
    r = post(client, a, "/api/keyword-search/estimate", {"keywords": ["a"], "platforms": ["facebook"]})
    assert r.status_code == 403 and r.json()["detail"]["errorType"] == "platform_disabled"
    set_setting("features.keyword_search.enabled", False)
    r = post(client, a, "/api/keyword-search", {**BODY, "confirm": True})
    assert r.status_code == 403 and r.json()["detail"]["errorType"] == "feature_disabled"
    assert not _NoopThread.started


def test_validation(env):
    client, db = env
    a = _org(db)
    r = post(client, a, "/api/keyword-search/estimate", {**BODY, "filters": {"min_followers": -5,
                                                                             "min_engagement_rate": 5000}})
    assert r.status_code == 422
    errors = r.json()["detail"]["errors"]
    assert set(errors) == {"min_followers", "min_engagement_rate"}
    r = post(client, a, "/api/keyword-search/estimate", {**BODY, "filters": {"bogus": 1}})
    assert r.status_code == 422
    r = post(client, a, "/api/keyword-search/estimate", {**BODY, "filters": {"min_followers": "lots"}})
    assert r.status_code == 422
    r = post(client, a, "/api/keyword-search/estimate", {**BODY, "keywords": ["   "]})
    assert r.status_code == 422 and "keywords" in r.json()["detail"]["errors"]
    r = post(client, a, "/api/keyword-search/estimate", {**BODY, "platforms": ["myspace"]})
    assert r.status_code == 422


# ── candidates / re-apply / continue ────────────────────────────────────────

def test_candidates_pagination_and_filters(env):
    client, db = env
    a = _org(db)
    run_id = _seed_run(db, a)
    r = get(client, a, f"/api/keyword-search/{run_id}/candidates", page=1, page_size=2)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 5 and len(body["items"]) == 2 and body["page_size"] == 2
    assert body["counts"] == {"pending": 0, "passed": 2, "rejected": 2, "needs_sampling": 0, "error": 1}
    assert [i["profile_id"] for i in body["items"]] == ["ig_big", "ig_mid"]      # followers desc
    assert "posts_raw" not in r.text and "organization_id" not in body["items"][0]
    r = get(client, a, f"/api/keyword-search/{run_id}/candidates", page=3, page_size=2)
    assert len(r.json()["items"]) == 1
    r = get(client, a, f"/api/keyword-search/{run_id}/candidates", status="rejected", platform="facebook")
    assert [i["profile_id"] for i in r.json()["items"]] == ["fb_small"]
    r = get(client, a, f"/api/keyword-search/{run_id}/candidates", status="nonsense")
    assert r.status_code == 422


def test_reapply_recomputes_without_scraping(env):
    client, db = env
    a = _org(db)
    run_id = _seed_run(db, a)
    with patch("app.discovery.pipeline.run_keyword_search", side_effect=AssertionError("no run")), \
            patch("app.discovery.adapters.get_adapter", side_effect=AssertionError("no adapter")):
        r = post(client, a, f"/api/keyword-search/{run_id}/reapply",
                 {"filters": {"min_followers": 700, "min_avg_comments": 5, "recent_posts_to_analyze": 10}})
    assert r.status_code == 200, r.text
    body = r.json()
    st = {c["profile_id"]: c["status"] for c in db.discovery_candidates.find({"search_run_id": run_id})}
    # sampled ones re-judged from stored posts (avg 4 < 5); fb_small now passes the profile
    # filters but was never sampled -> needs_sampling; ig_small still too small; errors untouched
    assert st == {"ig_big": "rejected", "ig_mid": "rejected", "ig_small": "rejected",
                  "fb_small": "needs_sampling", "fb_err": "error"}
    assert body["counts"]["needs_sampling"] == 1 and body["changed"] == 3
    assert body["continue_cost"]["sample"] == 1 and body["continue_cost"]["collect"] == 5
    run = db.search_history.find_one({"run_id": run_id})
    assert run["keyword_search"]["filters"]["min_followers"] == 700 and run["filters_history"]
    assert db.token_balances.find_one({"organization_id": a["org"]})["remaining"] == 1000
    assert not _NoopThread.started


def test_reapply_refused_while_running(env):
    client, db = env
    a = _org(db)
    run_id = _seed_run(db, a, status="running")
    r = post(client, a, f"/api/keyword-search/{run_id}/reapply", {"filters": {}})
    assert r.status_code == 409


def test_continue_needs_confirm_then_starts(env):
    client, db = env
    a = _org(db)
    run_id = _seed_run(db, a)
    post(client, a, f"/api/keyword-search/{run_id}/reapply", {"filters": {"min_followers": 700}})
    r = post(client, a, f"/api/keyword-search/{run_id}/continue", {})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "CONFIRM_REQUIRED"
    assert r.json()["detail"]["estimate"]["to_sample"] == 1
    assert not _NoopThread.started
    r = post(client, a, f"/api/keyword-search/{run_id}/continue", {"confirm": True, "max_tokens": 50})
    assert r.status_code == 200, r.text
    run = db.search_history.find_one({"run_id": run_id})
    assert run["status"] == "running" and run["stages"]["sampling"] == "pending"
    # max_tokens of a continue is a budget for THIS continuation: 10 already spent + 50
    assert run["stages"]["discovery"] == "done" and run["keyword_search"]["max_tokens"] == 60
    fb = db.discovery_candidates.find_one({"search_run_id": run_id, "profile_id": "fb_small"})
    assert fb["status"] == "pending" and fb["stage_reached"] == "enriched"
    assert _NoopThread.started == [(run_id, f"{run_id}_C1")]
    # nothing left once everything is processed
    db.discovery_candidates.update_many({"search_run_id": run_id, "status": "pending"},
                                        {"$set": {"status": "rejected"}})
    db.search_history.update_one({"run_id": run_id}, {"$set": {"status": "completed", "completed_at": NOW}})
    r = post(client, a, f"/api/keyword-search/{run_id}/continue", {"confirm": True})
    assert r.status_code == 409 and r.json()["detail"]["errorType"] == "nothing_to_continue"


def test_cancel(env):
    client, db = env
    a = _org(db)
    run_id = _seed_run(db, a, status="running")
    r = post(client, a, f"/api/keyword-search/{run_id}/cancel", {})
    assert r.status_code == 200 and r.json()["status"] == "cancelling"
    assert db.search_history.find_one({"run_id": run_id})["status"] == "cancelled"


def test_deleting_the_run_deletes_its_candidates(env):
    client, db = env
    a = _org(db)
    run_id = _seed_run(db, a)
    client.cookies.clear()
    r = client.delete(f"/api/search/{run_id}", cookies=a["cookies"])
    assert r.status_code == 200 and r.json()["deleted"]["candidates"] == 5
    assert db.discovery_candidates.count_documents({"search_run_id": run_id}) == 0


# ── presets ─────────────────────────────────────────────────────────────────

def test_presets(env):
    client, db = env
    a = _org(db)
    m = _member(db, a["org"], "member@acme.example")
    r = post(client, a, "/api/keyword-search/presets",
             {"name": "Pune bakeries", "keywords": ["bakery pune"], "platforms": ["instagram"],
              "filters": {"min_followers": 1000}, "strict": True})
    assert r.status_code == 200, r.text
    pid = r.json()["preset"]["id"]
    preset = db.search_presets.find_one({"_id": ObjectId(pid)})
    assert preset["kind"] == "keyword" and preset["organization_id"] == a["org"]
    assert preset["filters"] == {"min_followers": 1000, "recent_posts_to_analyze": 10}
    assert [p["name"] for p in get(client, a, "/api/keyword-search/presets").json()["presets"]] == ["Pune bakeries"]
    assert get(client, m, "/api/keyword-search/presets").json()["presets"] == []        # not shared
    r = post(client, a, "/api/keyword-search/presets", {"name": "bad", "filters": {"min_followers": -1}})
    assert r.status_code == 422
    # a URL-search preset is not reachable through the keyword routes
    other = str(db.search_presets.insert_one({"organization_id": a["org"], "user_id": a["uid"],
                                              "name": "url preset"}).inserted_id)
    client.cookies.clear()
    assert client.delete(f"/api/keyword-search/presets/{other}", cookies=a["cookies"]).status_code == 404
    client.cookies.clear()
    assert client.delete(f"/api/keyword-search/presets/{pid}", cookies=m["cookies"]).status_code in (403, 404)
    client.cookies.clear()
    assert client.delete(f"/api/keyword-search/presets/{pid}", cookies=a["cookies"]).status_code == 200
    assert db.audit_logs.find_one({"action": "search.keyword_preset_deleted"})


# ── isolation / rate limits ─────────────────────────────────────────────────

def test_tenant_isolation(env):
    client, db = env
    a = _org(db)
    b = _org(db, email="owner@other.example")
    run_id = _seed_run(db, a)
    for method, url, body in (("GET", f"/api/keyword-search/{run_id}", None),
                              ("GET", f"/api/keyword-search/{run_id}/candidates", None),
                              ("POST", f"/api/keyword-search/{run_id}/reapply", {"filters": {}}),
                              ("POST", f"/api/keyword-search/{run_id}/continue", {"confirm": True}),
                              ("POST", f"/api/keyword-search/{run_id}/cancel", {})):
        n0 = db.security_events.count_documents({"type": "cross_tenant_access"})
        client.cookies.clear()
        r = client.request(method, url, json=body, cookies=b["cookies"])
        assert r.status_code == 404, (url, r.status_code)
        assert db.security_events.count_documents({"type": "cross_tenant_access"}) > n0
    # a member of the same org can't see the owner's run either
    m = _member(db, a["org"], "member@acme.example")
    assert get(client, m, f"/api/keyword-search/{run_id}").status_code == 404
    # a URL-search run is not a keyword run
    db.search_history.insert_one({"run_id": "URLX", "organization_id": a["org"], "user_id": a["uid"],
                                  "status": "completed"})
    assert get(client, a, "/api/keyword-search/URLX").status_code == 404


def test_rate_limits(env):
    client, db = env
    a = _org(db)
    with patch("app.api.routes.keyword_search.ESTIMATE_LIMIT", (2, 60)):
        codes = [post(client, a, "/api/keyword-search/estimate", BODY).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    with patch("app.api.routes.keyword_search.START_LIMIT", (1, 60)):
        assert post(client, a, "/api/keyword-search", {**BODY, "confirm": True}).status_code == 200
        r = post(client, a, "/api/keyword-search", {**BODY, "confirm": True})
    assert r.status_code == 429 and r.headers.get("Retry-After")


# ── public API v1 ───────────────────────────────────────────────────────────

def _key(db, org_id, scopes=("search:create",)):
    from app.services.api_keys import create_api_key
    raw, _ = create_api_key(db, organization_id=org_id, name="CRM", scopes=list(scopes))
    return {"X-API-Key": raw}


def test_public_v1(env):
    client, db = env
    a = _org(db)
    other = _org(db, email="owner@other.example")
    client.cookies.clear()
    r = client.post("/api/v1/searches/keyword", json=BODY, headers=_key(db, a["org"]))
    assert r.status_code == 409 and r.json()["detail"]["estimate"]["tokens"]["discover"] == 20
    r = client.post("/api/v1/searches/keyword", json={**BODY, "confirm": True, "max_tokens": 100},
                    headers=_key(db, a["org"]))
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    assert run_id.startswith("KW_API_")
    run = db.search_history.find_one({"run_id": run_id})
    assert run["source"] == "api" and run["created_by"].startswith("api_key:")
    assert run["keyword_search"]["max_tokens"] == 100
    r = client.post("/api/v1/searches/keyword", json={**BODY, "confirm": True},
                    headers=_key(db, a["org"], scopes=("search:read",)))
    assert r.status_code == 403
    read = _key(db, a["org"], scopes=("search:read",))
    assert client.get(f"/api/v1/searches/keyword/{run_id}", headers=read).status_code == 200
    assert client.get(f"/api/v1/searches/keyword/{run_id}", headers=_key(db, other["org"])).status_code == 404
    assert client.get(f"/api/v1/searches/keyword/{run_id}",
                      headers=_key(db, a["org"], scopes=("leads:read",))).status_code == 403
    seeded = _seed_run(db, a)
    r = client.get(f"/api/v1/searches/keyword/{seeded}/candidates", params={"status": "passed"}, headers=read)
    assert r.status_code == 200 and r.json()["total"] == 2 and "posts_raw" not in r.text
    assert client.get(f"/api/v1/searches/keyword/{seeded}/candidates",
                      headers=_key(db, other["org"])).status_code == 404
    r = client.post(f"/api/v1/searches/keyword/{seeded}/reapply", json={"filters": {"min_followers": 700}},
                    headers=read)
    assert r.status_code == 403
    r = client.post(f"/api/v1/searches/keyword/{seeded}/reapply", json={"filters": {"min_followers": 700}},
                    headers=_key(db, a["org"]))
    assert r.status_code == 200 and r.json()["counts"]["needs_sampling"] == 1


# ── migrations ──────────────────────────────────────────────────────────────

def test_plan_and_trial_migration_is_idempotent(env):
    _, db = env
    from app.billing.plans import KEYWORD_PLANS_MIGRATION, migrate_keyword_search_plans
    from app.lifecycle import config
    db.migrations.delete_many({})
    db.plans.update_one({"slug": "pro"}, {"$pull": {"features": "keyword_search"},
                                          "$unset": {"limits.keyword_max_keywords": "",
                                                     "limits.keyword_max_candidates": ""}})
    db.plans.update_one({"slug": "business"}, {"$set": {"limits.keyword_max_candidates": 42}})
    db.plans.insert_one({"slug": "custom-agency", "name": "Agency", "status": "active", "price_monthly": 99,
                         "features": ["url_search", "facebook"], "limits": {"monthly_searches": 10}})
    db.platform_config.update_one({"_id": "demo"}, {"$set": {"value": {"duration_days": 3, "tokens": 500}}},
                                  upsert=True)
    assert migrate_keyword_search_plans(db) >= 2
    pro = db.plans.find_one({"slug": "pro"})
    assert "keyword_search" in pro["features"] and pro["limits"]["keyword_max_keywords"] == 5
    assert db.plans.find_one({"slug": "business"})["limits"]["keyword_max_candidates"] == 42   # kept
    agency = db.plans.find_one({"slug": "custom-agency"})
    assert "keyword_search" in agency["features"] and agency["limits"]["keyword_max_keywords"] == 3
    assert "keyword_search" not in db.plans.find_one({"slug": "free"})["features"]
    demo = db.platform_config.find_one({"_id": "demo"})["value"]
    assert demo["keyword_max_candidates"] == 20 and demo["duration_days"] == 3
    assert db.migrations.find_one({"_id": KEYWORD_PLANS_MIGRATION})
    # an operator removing the feature later is respected (marker)
    db.plans.update_one({"slug": "pro"}, {"$pull": {"features": "keyword_search"}})
    assert migrate_keyword_search_plans(db) == 0
    assert "keyword_search" not in db.plans.find_one({"slug": "pro"})["features"]
    # stored token costs without the new actions still price them (merge with seed)
    db.platform_config.update_one({"_id": "token_costs"}, {"$set": {"value": {"search": 10, "collect": 5}}},
                                  upsert=True)
    config.clear_cache()
    assert config.token_cost("discover") == 5 and config.token_cost("enrich") == 1
    assert config.token_cost("sample") == 1


# ═══════════════════════════════════════════════════════════════════════════
# Review regressions
# ═══════════════════════════════════════════════════════════════════════════

def _searches_used(org):
    from app.billing.usage import get_current_usage_doc_sync
    return int(get_current_usage_doc_sync(org).get("searches_used") or 0)


def test_absurd_budgets_and_limits_are_refused_before_any_quota_is_used(env):
    """#5: 10**20 used to pass validation, burn the monthly search and crash the insert."""
    client, db = env
    a = _org(db)
    for body in ({**BODY, "confirm": True, "max_tokens": 10 ** 20},
                 {**BODY, "confirm": True, "limits": {"max_candidates": 10 ** 20}},
                 {**BODY, "confirm": True, "limits": {"comments_per_post": 10 ** 9}}):
        r = post(client, a, "/api/keyword-search", body)
        assert r.status_code == 422, (body, r.status_code)
    r = post(client, a, "/api/keyword-search/presets", {"name": "x", "limits": {"recent_posts": 10 ** 12}})
    assert r.status_code == 422
    run_id = _seed_run(db, a)
    r = post(client, a, f"/api/keyword-search/{run_id}/continue", {"confirm": True, "max_tokens": 10 ** 20})
    assert r.status_code == 422
    assert _searches_used(a["org"]) == 0 and db.search_history.count_documents({"source": "app"}) == 0


def test_failed_start_gives_the_monthly_search_back(env):
    """#5: insert / launch failing after the quota was taken -> 503, quota restored, no run left."""
    client, db = env
    a = _org(db)
    with patch("app.discovery.service.launch", side_effect=RuntimeError("thread pool gone")):
        r = post(client, a, "/api/keyword-search", {**BODY, "confirm": True})
    assert r.status_code == 503 and r.json()["detail"]["errorType"] == "start_failed"
    assert _searches_used(a["org"]) == 0
    assert db.search_history.count_documents({"search_type": "keyword"}) == 0
    assert db.token_balances.find_one({"organization_id": a["org"]})["remaining"] == 1000


def test_failed_continue_restores_the_run(env):
    """#5: a continue whose launch fails leaves the run as it was."""
    client, db = env
    a = _org(db)
    run_id = _seed_run(db, a)
    post(client, a, f"/api/keyword-search/{run_id}/reapply", {"filters": {"min_followers": 700}})
    with patch("app.discovery.service.launch", side_effect=RuntimeError("thread pool gone")):
        r = post(client, a, f"/api/keyword-search/{run_id}/continue", {"confirm": True})
    assert r.status_code == 503
    run = db.search_history.find_one({"run_id": run_id})
    assert run["status"] == "completed" and run["completed_at"] is not None
    fb = db.discovery_candidates.find_one({"search_run_id": run_id, "profile_id": "fb_small"})
    assert fb["status"] == "needs_sampling"


def test_zero_keyword_cap_means_not_allowed(env):
    """#7: an explicit 0 (trial config or plan) refuses keyword search; a missing key = global cap."""
    client, db = env
    a = _org(db, plan=None, tokens=500)
    cfg = {"allowed_platforms": ["instagram"], "max_searches": 10, "posts_per_search": 20,
           "comments_per_post": 30, "max_leads": 200, "ai_enabled": True, "max_users": 1,
           "keyword_search_enabled": True, "keyword_max_keywords": 0, "keyword_max_candidates": 20,
           "keyword_recent_posts": 10}
    db.organizations.update_one({"_id": ObjectId(a["org"])}, {"$set": {
        "status": "demo", "demo": {"expires_at": NOW + timedelta(days=3), "config": cfg}}})
    r = post(client, a, "/api/keyword-search/estimate", {"keywords": ["a"], "platforms": ["instagram"]})
    assert r.status_code == 402
    assert r.json()["detail"]["code"] == "PLAN_LIMIT" and r.json()["detail"]["limit"] == 0
    assert r.json()["detail"]["metric"] == "keyword_max_keywords"
    # a paid plan with the candidates limit set to 0 is refused too
    b = _org(db, email="owner@zero.example")
    db.plans.update_one({"slug": "pro"}, {"$set": {"limits.keyword_max_candidates": 0}})
    from app.billing.plans import invalidate_plan_cache
    invalidate_plan_cache()
    r = post(client, b, "/api/keyword-search/estimate", {"keywords": ["a"], "platforms": ["instagram"]})
    assert r.status_code == 402 and r.json()["detail"]["metric"] == "keyword_max_candidates"
    # without the key the Global Settings cap applies
    db.plans.update_one({"slug": "pro"}, {"$unset": {"limits.keyword_max_candidates": ""}})
    invalidate_plan_cache()
    r = post(client, b, "/api/keyword-search/estimate", {"keywords": ["a"], "platforms": ["instagram"]})
    assert r.status_code == 200 and r.json()["limits"]["max_candidates"] == 200


def test_reapply_keeps_the_runs_recent_posts_and_waits_for_the_worker(env):
    """#12 + #1: re-apply without recent_posts_to_analyze keeps the run's n;
    a cancelled run whose worker hasn't stopped yet is refused."""
    client, db = env
    now = datetime.now(timezone.utc)  # fresh: module NOW is minutes old in a full run, so its lease would be expired
    a = _org(db)
    run_id = _seed_run(db, a)
    db.search_history.update_one({"run_id": run_id}, {"$set": {"keyword_search.filters.recent_posts_to_analyze": 7}})
    r = post(client, a, f"/api/keyword-search/{run_id}/reapply", {"filters": {"min_followers": 700}})
    assert r.status_code == 200
    assert db.search_history.find_one({"run_id": run_id})["keyword_search"]["filters"]["recent_posts_to_analyze"] == 7
    # cancelled but the worker hasn't acknowledged (no completed_at, fresh) -> 409
    db.search_history.update_one({"run_id": run_id}, {"$set": {"status": "cancelled", "completed_at": None,
                                                               "updated_at": now}})
    assert post(client, a, f"/api/keyword-search/{run_id}/reapply", {"filters": {}}).status_code == 409
    assert post(client, a, f"/api/keyword-search/{run_id}/continue", {"confirm": True}).status_code == 409
    # ...or a continuation job still holds a live lease
    db.search_history.update_one({"run_id": run_id}, {"$set": {"completed_at": now}})
    db.job_queue.insert_one({"job_id": f"{run_id}_C1", "run_id": f"{run_id}_C1", "status": "running",
                             "locked_until": now + timedelta(minutes=2)})
    assert post(client, a, f"/api/keyword-search/{run_id}/reapply", {"filters": {}}).status_code == 409
    db.job_queue.update_one({"job_id": f"{run_id}_C1"}, {"$set": {"status": "completed"}})
    assert post(client, a, f"/api/keyword-search/{run_id}/reapply", {"filters": {}}).status_code == 200


def test_continue_clears_errors_of_stages_it_reruns(env):
    """#6: platform_errors of re-run stages no longer make the pipeline skip the platform."""
    client, db = env
    a = _org(db)
    run_id = _seed_run(db, a)
    db.search_history.update_one({"run_id": run_id}, {"$set": {"platform_errors": {
        "facebook": {"stage": "sampling", "message": "actor down"},
        "linkedin": {"stage": "discovery", "message": "no actor"}}}})
    post(client, a, f"/api/keyword-search/{run_id}/reapply", {"filters": {"min_followers": 700}})
    r = post(client, a, f"/api/keyword-search/{run_id}/continue", {"confirm": True})
    assert r.status_code == 200, r.text
    errs = db.search_history.find_one({"run_id": run_id})["platform_errors"]
    assert "facebook" not in errs and errs["linkedin"]["stage"] == "discovery"


# ── location / business type pushed into the search; why profiles failed ────

def _reason(flt, outcome, message):
    return {"filter": flt, "outcome": outcome, "message": message, "value": None, "threshold": None}


def _seed_rejections(db, who, run_id="KWREJ1", strict=False):
    owner = {"organization_id": who["org"], "user_id": who["uid"], "created_by": who["email"]}
    db.search_history.insert_one({
        **owner, "run_id": run_id, "search_type": "keyword", "status": "completed", "phase": "completed",
        "keyword_search": {"keywords": ["developer job"], "platforms": ["instagram", "facebook"],
                           "filters": {"location_keyword": "kota, Rajasthan", "min_posts": 10,
                                       "recent_posts_to_analyze": 10},
                           "strict": strict, "limits": {"max_candidates": 50, "recent_posts": 10}},
        "stages": {"discovery": "done", "enrichment": "done", "sampling": "done", "deep_scrape": "done"},
        "discovery_queries": [
            {"platform": "instagram", "keyword": "developer job", "query_text": "developer job kota",
             "locations": [], "extra_inputs": {}},
            {"platform": "facebook", "keyword": "developer job", "query_text": "developer job",
             "locations": ["kota, Rajasthan"], "extra_inputs": {}}],
        "stage_counts": {"passed": 1, "rejected": 4}, "tokens_charged": {}, "created_at": NOW,
        "updated_at": NOW, "completed_at": NOW})
    rows = [
        ("r1", "rejected", [_reason("location_keyword", "fail", "location: 'Rajasthan' not found"),
                            _reason("min_posts", "fail", "posts 3 < min 10")]),
        ("r2", "rejected", [_reason("location_keyword", "fail", "location: 'Rajasthan' not found")]),
        ("r3", "rejected", [_reason("location_keyword", "fail", "location: 'kota', 'Rajasthan' not found")]),
        ("r4", "rejected", [_reason("min_posts", "unknown", "posts unknown"),
                            _reason("min_followers", "pass", "ok")]),
        ("ok", "passed", [_reason("location_keyword", "pass", "location matches kota, Rajasthan"),
                          _reason("min_posts", "unknown", "posts unknown")]),
    ]
    for pid, status, reasons in rows:
        db.discovery_candidates.insert_one({
            **owner, "search_run_id": run_id, "platform": "instagram", "profile_id": pid,
            "url": f"https://www.instagram.com/{pid}", "name": pid, "keywords": ["developer job"],
            "status": status, "stage_reached": "enriched", "reasons": reasons, "unsupported": [], "unknown": [],
            "metrics": {}, "profile": {"platform": "instagram", "profile_id": pid}, "created_at": NOW})
    return run_id


def test_run_status_has_discovery_queries_and_rejection_summary(env):
    client, db = env
    a = _org(db)
    run_id = _seed_rejections(db, a)
    r = get(client, a, f"/api/keyword-search/{run_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["passed_count"] == 1
    assert [q["query_text"] for q in body["discovery_queries"]] == ["developer job kota", "developer job"]
    assert body["discovery_queries"][1]["locations"] == ["kota, Rajasthan"]
    # location fails 3x (the most common message wins), min_posts fails once; unknown isn't a
    # rejection reason outside strict mode
    assert body["rejection_summary"] == [
        {"filter": "location_keyword", "label": "Location contains", "count": 3, "outcome": "fail",
         "message": "location: 'Rajasthan' not found"},
        {"filter": "min_posts", "label": "Minimum total posts", "count": 1, "outcome": "fail",
         "message": "fewer than 10 posts"}]
    # the default candidates listing is unchanged (every status)
    assert get(client, a, f"/api/keyword-search/{run_id}/candidates").json()["total"] == 5
    # public v1 answers the same
    client.cookies.clear()
    v1 = client.get(f"/api/v1/searches/keyword/{run_id}", headers=_key(db, a["org"], scopes=("search:read",)))
    assert v1.status_code == 200 and v1.json()["rejection_summary"] == body["rejection_summary"]
    assert v1.json()["discovery_queries"] == body["discovery_queries"]


def test_rejection_summary_counts_unknown_values_in_strict_mode(env):
    client, db = env
    a = _org(db)
    run_id = _seed_rejections(db, a, strict=True)
    summary = get(client, a, f"/api/keyword-search/{run_id}").json()["rejection_summary"]
    unknown = [s for s in summary if s["outcome"] == "unknown"]
    assert unknown == [{"filter": "min_posts", "label": "Minimum total posts", "count": 1, "outcome": "unknown",
                        "message": "minimum total posts not known (strict mode rejects unknown values)"}]


def test_old_runs_without_new_fields_still_read(env):
    client, db = env
    a = _org(db)
    run_id = _seed_run(db, a)          # no discovery_queries, no location_match, plain reasons
    body = get(client, a, f"/api/keyword-search/{run_id}").json()
    assert body["discovery_queries"] == [] and body["rejection_summary"] == [] and body["passed_count"] == 2
    # re-applying a multi-place location without location_match requires every place ("all")
    db.discovery_candidates.update_one({"search_run_id": run_id, "profile_id": "ig_small"},
                                       {"$set": {"profile.bio": "Bakery in Kota", "profile.followers": 5000}})
    r = post(client, a, f"/api/keyword-search/{run_id}/reapply",
             {"filters": {"location_keyword": "Kota, Rajasthan", "recent_posts_to_analyze": 10}})
    assert r.status_code == 200, r.text
    c = db.discovery_candidates.find_one({"search_run_id": run_id, "profile_id": "ig_small"})
    loc = next(o for o in c["reasons"] if o["filter"] == "location_keyword")
    assert c["status"] == "rejected" and loc["message"] == "location: 'Rajasthan' not found"
    r = post(client, a, f"/api/keyword-search/{run_id}/reapply",
             {"filters": {"location_keyword": "Kota, Rajasthan", "location_match": "any",
                          "recent_posts_to_analyze": 10}})
    assert r.status_code == 200, r.text
    c = db.discovery_candidates.find_one({"search_run_id": run_id, "profile_id": "ig_small"})
    assert c["status"] == "needs_sampling"


def test_capabilities_hide_language_and_offer_business_type(env):
    client, db = env
    a = _org(db)
    body = get(client, a, "/api/keyword-search/capabilities").json()
    assert body["filters"]["language"]["hidden"] is True
    assert body["filters"]["category_keyword"]["label"] == "Business type contains"
    assert body["filters"]["location_match"]["choices"] == ["all", "any"]
    assert body["platforms"]["facebook"]["filters"]["category_keyword"]["support"] == "supported"
    assert body["platforms"]["youtube"]["filters"]["category_keyword"]["support"] == "partial"


def test_estimate_validates_location_match_and_explains_the_narrowed_search(env):
    client, db = env
    a = _org(db)
    bad = {**BODY, "filters": {**BODY["filters"], "location_keyword": "Kota", "location_match": "most"}}
    r = post(client, a, "/api/keyword-search/estimate", bad)
    assert r.status_code == 422 and "location_match" in r.json()["detail"]["errors"]
    body = {**BODY, "keywords": ["developer job"],
            "filters": {**BODY["filters"], "location_keyword": "kota, Rajasthan", "location_match": "any",
                        "category_keyword": "software", "language": "hi"}}
    r = post(client, a, "/api/keyword-search/estimate", body)
    assert r.status_code == 200, r.text
    est = r.json()
    assert est["filters"]["location_match"] == "any" and est["filters"]["category_keyword"] == "software"
    ig = est["per_platform"]["instagram"]["search_queries"][0]
    fb = est["per_platform"]["facebook"]["search_queries"][0]
    assert ig["query_text"] == "developer job kota"
    assert fb["query_text"] == "developer job" and fb["locations"] == ["kota, Rajasthan"]
    note = next(n for n in est["notes"] if "narrow" in n)
    assert "'developer job kota' (Instagram)" in note
    assert "'developer job' near 'kota, Rajasthan' (Facebook)" in note
    # language is accepted (compatibility) but never listed as "can't measure"
    assert not any("language" in n.lower() for n in est["notes"])
    assert est["per_platform"]["instagram"]["unsupported_filters"] == []
