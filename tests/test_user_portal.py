"""
User Portal self-service API (app/api/routes/me.py) + portal entitlement paths.

Proves:
  * /api/me/* returns ONLY the caller's own data — another member's searches,
    leads, exports, usage and token ledger never appear, nor another org's;
  * leads assigned to me by an Admin appear (view=assigned) without exposing
    the owner's other leads;
  * PATCH /api/me/profile cannot change email / role / org / status
    (mass-assignment protection) and notification prefs persist;
  * structured 402 codes on POST /api/url/search (TOKENS_EXHAUSTED,
    DEMO_EXPIRED) with the scraper thread mocked.

Runs entirely against an in-memory mongomock database.
Run:  python -m pytest tests/test_user_portal.py -q -p no:cacheprovider
"""
from datetime import timedelta
from unittest.mock import patch

import mongomock
import mongomock_motor
import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

pytestmark = pytest.mark.own_db


@pytest.fixture(scope="module")
def env():
    mclient = mongomock.MongoClient()
    aclient = mongomock_motor.AsyncMongoMockClient(mock_mongo_client=mclient)
    patches = [
        patch("app.db.mongo.get_sync_client", return_value=mclient),
        patch("app.db.mongo.get_async_client", return_value=aclient),
        patch("app.admin.settings.is_maintenance_enabled", return_value=False),
        patch("app.main.ensure_indexes", lambda: None),
    ]
    for p in patches:
        p.start()
    try:
        from app.auth.permissions import invalidate_permission_cache
        from app.config import get_settings
        from app.main import app

        invalidate_permission_cache()
        db = mclient[get_settings().mongo_db_name]
        with TestClient(app) as client:
            data = _seed(db)
            yield {"client": client, "db": db, **data}
    finally:
        for p in reversed(patches):
            p.stop()


def _seed(db):
    from app.auth.service import build_session_value, create_tracked_session
    from app.db.models import utcnow

    for name in ("organizations", "users", "organization_members", "search_history",
                 "facebook_pages", "facebook_posts", "facebook_comments", "ai_comments",
                 "exports", "token_ledger", "token_balances", "security_events",
                 "role_permissions", "audit_logs", "subscriptions"):
        db[name].delete_many({})

    now = utcnow()
    org_a, org_b, org_demo, org_expired = ObjectId(), ObjectId(), ObjectId(), ObjectId()
    demo_cfg = {"allowed_platforms": ["facebook"], "max_searches": 10,
                "posts_per_search": 20, "comments_per_post": 30, "max_leads": 200,
                "ai_enabled": True, "exports_enabled": False, "max_users": 1}
    db.organizations.insert_many([
        {"_id": org_a, "name": "Org A", "slug": "org-a", "status": "active"},
        {"_id": org_b, "name": "Org B", "slug": "org-b", "status": "active"},
        {"_id": org_demo, "name": "Demo Org", "slug": "demo-org", "status": "demo",
         "demo": {"expires_at": now + timedelta(days=5), "config": demo_cfg}},
        {"_id": org_expired, "name": "Expired Org", "slug": "expired-org", "status": "demo",
         "demo": {"expires_at": now - timedelta(days=1), "config": demo_cfg}},
    ])

    users = {}

    def add_user(key, org, role):
        uid = ObjectId()
        email = f"{key}@example.com"
        db.users.insert_one({"_id": uid, "email": email, "name": key, "status": "active",
                             "default_organization_id": str(org)})
        db.organization_members.insert_one({"user_id": str(uid), "organization_id": str(org),
                                            "role": role, "status": "active"})
        claims = {"user_id": str(uid), "email": email, "name": key, "scope": "site",
                  "organization_id": str(org), "org_role": role}
        users[key] = {"id": str(uid), "email": email, "org": str(org),
                      "cookie": build_session_value(create_tracked_session(claims))}

    add_user("a_owner", org_a, "owner")
    add_user("a_user1", org_a, "member")
    add_user("a_user2", org_a, "member")
    add_user("b_user", org_b, "member")
    add_user("demo_user", org_demo, "owner")
    add_user("expired_user", org_expired, "owner")

    rec = {}
    for key in ("a_owner", "a_user1", "a_user2", "b_user"):
        u = users[key]
        owner = {"organization_id": u["org"], "user_id": u["id"], "created_by": u["email"]}
        run_id = f"URL_{key}"
        db.search_history.insert_one({"run_id": run_id, "status": "completed",
                                      "query": f"https://facebook.com/{key}",
                                      "intent": {"platform": "facebook"},
                                      "created_at": now, **owner})
        lead = db.ai_comments.insert_one({
            "is_lead": True, "lead_score": 85, "lead_quality": "hot", "lead_status": "new",
            "platform": "facebook", "commenter_name": f"prospect of {key}",
            "comment_text": f"interested {key}", "search_run_id": run_id,
            "lead_created_at": now, **owner}).inserted_id
        exp = db.exports.insert_one({"scope": "comments", "format": "csv",
                                     "status": "completed", "created_at": now,
                                     **owner}).inserted_id
        db.token_ledger.insert_one({"organization_id": u["org"], "user_id": u["id"],
                                    "type": "consume", "amount": 10, "reason": "search",
                                    "reference": run_id, "created_at": now})
        rec[key] = {"run": run_id, "lead": str(lead), "export": str(exp)}

    # a_user2's second lead, assigned to a_user1 by an admin
    u2 = users["a_user2"]
    assigned = db.ai_comments.insert_one({
        "is_lead": True, "lead_score": 60, "lead_status": "new", "platform": "facebook",
        "commenter_name": "assigned prospect", "comment_text": "assigned lead",
        "organization_id": u2["org"], "user_id": u2["id"], "created_by": u2["email"],
        "assigned_user_id": users["a_user1"]["id"], "assigned_to": users["a_user1"]["email"],
        "lead_created_at": now}).inserted_id
    rec["assigned"] = str(assigned)

    # tokens: demo org has none left
    db.token_balances.insert_one({"organization_id": str(org_demo), "allocated": 500,
                                  "used": 500, "remaining": 0, "source": "demo",
                                  "expires_at": now + timedelta(days=5)})
    return {"users": users, "rec": rec}


def _as(env, key):
    from app.auth.service import COOKIE_NAME
    client = env["client"]
    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, env["users"][key]["cookie"])
    return client


# ── own data only ────────────────────────────────────────────────────────────

def test_searches_are_own_only(env):
    rec = env["rec"]
    for key in ("a_user1", "a_owner"):  # even the owner sees only their own here
        body = _as(env, key).get("/api/me/searches").json()
        runs = {s["run_id"] for s in body["items"]}
        assert runs == {rec[key]["run"]}, key
        assert body["total"] == 1


def test_exports_are_own_only(env):
    rec = env["rec"]
    c = _as(env, "a_user1")
    body = c.get("/api/me/exports").json()
    ids = {e["id"] for e in body["items"]}
    assert ids == {rec["a_user1"]["export"]}
    for other in ("a_user2", "a_owner", "b_user"):
        assert rec[other]["export"] not in ids
    # the org owner sees only their own exports too
    owner_ids = {e["id"] for e in _as(env, "a_owner").get("/api/me/exports").json()["items"]}
    assert owner_ids == {rec["a_owner"]["export"]}


def test_exports_pagination_and_filter(env):
    c = _as(env, "a_user1")
    body = c.get("/api/me/exports", params={"page_size": 1, "page": 1}).json()
    assert body["page_size"] == 1 and body["total"] == 1 and body["total_pages"] == 1
    assert c.get("/api/me/exports", params={"scope": "pages"}).json()["total"] == 0
    assert c.get("/api/me/exports", params={"scope": "bogus"}).status_code == 422


def test_usage_and_ledger_are_own_only(env):
    c = _as(env, "a_user1")
    body = c.get("/api/me/usage").json()
    me = body["me"]
    assert me["searches"] == 1
    assert me["exports"] == 1
    assert me["tokens_consumed"] == 10  # only a_user1's ledger rows (others also spent 10)
    assert me["leads"] == 2  # own + assigned
    assert me["assigned_to_me"] == 1
    assert "organization" in body and "caps" in body and "token_costs" in body
    ledger = c.get("/api/me/usage/ledger").json()
    assert ledger["total"] == 1
    assert ledger["items"][0]["reference"] == env["rec"]["a_user1"]["run"]
    # another org's member: own ledger only, nothing from org A
    b = _as(env, "b_user").get("/api/me/usage/ledger").json()
    assert [i["reference"] for i in b["items"]] == [env["rec"]["b_user"]["run"]]


def test_leads_own_plus_assigned_never_others(env):
    rec = env["rec"]
    c = _as(env, "a_user1")
    all_ids = {l["id"] for l in c.get("/api/me/leads").json()["items"]}
    assert all_ids == {rec["a_user1"]["lead"], rec["assigned"]}
    assert rec["a_user2"]["lead"] not in all_ids  # owner's other lead stays private
    assert rec["b_user"]["lead"] not in all_ids

    assigned = c.get("/api/me/leads", params={"view": "assigned"}).json()
    assert [l["id"] for l in assigned["items"]] == [rec["assigned"]]
    assert assigned["items"][0]["assigned_to_me"] is True
    assert assigned["view_counts"] == {"all": 2, "mine": 1, "assigned": 1}

    mine = c.get("/api/me/leads", params={"view": "mine"}).json()
    assert [l["id"] for l in mine["items"]] == [rec["a_user1"]["lead"]]
    # filters stay inside the scope
    assert c.get("/api/me/leads", params={"q": "interested a_user2"}).json()["total"] == 0
    assert c.get("/api/me/leads", params={"quality": "hot"}).json()["total"] == 1


def test_summary_is_own_only(env):
    rec = env["rec"]
    body = _as(env, "a_user1").get("/api/me/summary").json()
    assert {s["run_id"] for s in body["recent_searches"]} == {rec["a_user1"]["run"]}
    lead_ids = {l["id"] for l in body["recent_leads"]}
    assert lead_ids <= {rec["a_user1"]["lead"], rec["assigned"]}
    assert body["counts"]["assigned_to_me"] == 1
    assert any(a["code"] == "LEADS_ASSIGNED" for a in body["alerts"])


def test_me_requires_session(env):
    client = env["client"]
    client.cookies.clear()
    for url in ("/api/me/profile", "/api/me/usage", "/api/me/exports", "/api/me/leads",
                "/api/me/searches", "/api/me/summary"):
        assert client.get(url).status_code == 401, url


# ── profile / mass assignment / prefs ────────────────────────────────────────

def test_profile_email_read_only_and_update(env):
    db = env["db"]
    u = env["users"]["a_user2"]
    c = _as(env, "a_user2")
    prof = c.get("/api/me/profile").json()["profile"]
    assert prof["email"] == u["email"] and prof["role"] == "member"

    res = c.patch("/api/me/profile", json={"name": "Jane Doe", "phone": "+91 98765 43210"})
    assert res.status_code == 200, res.text
    assert set(res.json()["changed"]) == {"name", "phone"}
    doc = db.users.find_one({"_id": ObjectId(u["id"])})
    assert doc["name"] == "Jane Doe" and doc["phone"] == "+91 98765 43210"
    assert db.audit_logs.count_documents({"action": "profile.updated"}) >= 1 or \
        db["audit_log"].count_documents({"action": "profile.updated"}) >= 1


@pytest.mark.parametrize("payload", [
    {"email": "evil@example.com"},
    {"name": "ok", "role": "owner"},
    {"org_role": "admin"},
    {"organization_id": str(ObjectId())},
    {"default_organization_id": str(ObjectId())},
    {"status": "suspended"},
    {"platform_role": "super_admin"},
    {"is_platform_admin": True},
    {"password_hash": "x"},
    {"permissions": ["data.view_all"]},
])
def test_profile_mass_assignment_rejected(env, payload):
    db = env["db"]
    u = env["users"]["a_user1"]
    before = db.users.find_one({"_id": ObjectId(u["id"])})
    member_before = db.organization_members.find_one({"user_id": u["id"]})
    res = _as(env, "a_user1").patch("/api/me/profile", json=payload)
    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "FIELD_NOT_EDITABLE"
    after = db.users.find_one({"_id": ObjectId(u["id"])})
    assert after == before
    assert db.organization_members.find_one({"user_id": u["id"]}) == member_before
    assert db.security_events.count_documents({"event_type": "mass_assignment_attempt"}) >= 1 \
        or db.security_events.count_documents({"type": "mass_assignment_attempt"}) >= 1


def test_profile_unknown_field_and_bad_values_rejected(env):
    c = _as(env, "a_user1")
    assert c.patch("/api/me/profile", json={"favourite_colour": "red"}).status_code == 422
    assert c.patch("/api/me/profile", json={"name": "   "}).status_code == 422
    assert c.patch("/api/me/profile", json={"name": "<script>"}).status_code == 422
    assert c.patch("/api/me/profile", json={"phone": "call me maybe"}).status_code == 422
    assert c.patch("/api/me/profile",
                   json={"notification_preferences": {"nope": True}}).status_code == 422
    assert c.patch("/api/me/profile",
                   json={"notification_preferences": {"lead_assigned": "yes please"}}).status_code == 422


def test_notification_prefs_persist(env):
    """Same field + keys as the Admin portal (/api/org-admin/profile):
    users.notification_preferences = {key: bool}."""
    db = env["db"]
    u = env["users"]["a_user1"]
    c = _as(env, "a_user1")
    res = c.patch("/api/me/profile", json={"notification_preferences": {
        "search_completed": False, "product_updates": True}})
    assert res.status_code == 200, res.text
    assert res.json()["changed"] == ["notification_preferences"]
    prefs = c.get("/api/me/profile").json()["profile"]["notification_preferences"]
    assert prefs["search_completed"] is False
    assert prefs["product_updates"] is True
    assert prefs["lead_assigned"] is True and prefs["email_notifications"] is True  # defaults
    stored = db.users.find_one({"_id": ObjectId(u["id"])})["notification_preferences"]
    assert stored == {"search_completed": False, "product_updates": True}  # partial merge
    # a second partial update keeps the first
    c.patch("/api/me/profile", json={"notification_preferences": {"weekly_summary": False}})
    stored = db.users.find_one({"_id": ObjectId(u["id"])})["notification_preferences"]
    assert stored == {"search_completed": False, "product_updates": True, "weekly_summary": False}

    # keys stay compatible with the Admin portal's profile endpoint
    from app.api.routes import org_admin
    from app.api.routes.me import NOTIFICATION_PREFERENCES, notification_allowed
    assert set(NOTIFICATION_PREFERENCES) == set(org_admin._PREF_KEYS)

    assert notification_allowed({"notification_preferences": {"search_completed": False}},
                                "search_completed") is False
    assert notification_allowed({"notification_preferences": {"email_notifications": False}},
                                "lead_assigned", channel="email") is False
    assert notification_allowed({}, "lead_assigned", channel="email") is True
    assert notification_allowed({}, "security_event") is True


# ── 402 entitlement paths (scraper thread mocked) ────────────────────────────

class _NoopThread:
    started = []

    def __init__(self, *a, **kw):
        _NoopThread.started.append(a)

    def run(self):
        return None


def test_url_search_tokens_exhausted_402(env):
    c = _as(env, "demo_user")
    with patch("app.social.url_search.UrlSearchThread", _NoopThread):
        res = c.post("/api/url/search", params={
            "url": "https://www.facebook.com/testpage", "max_posts": 5,
            "max_comments_per_post": 5})
    assert res.status_code == 402, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "TOKENS_EXHAUSTED"
    assert detail["upgrade_available"] is True
    assert detail["remaining"] == 0 and detail["needed"] > 0
    assert isinstance(detail["message"], str) and detail["message"]
    assert not _NoopThread.started
    # the portal knows BEFORE running: summary reports the blocker
    summary = c.get("/api/me/summary").json()
    assert any(b["code"] == "TOKENS_EXHAUSTED" for b in summary["blockers"])
    assert summary["caps"]["posts_per_search"] == 20
    assert summary["caps"]["comments_per_post"] == 30


def test_url_search_demo_expired_402(env):
    c = _as(env, "expired_user")
    with patch("app.social.url_search.UrlSearchThread", _NoopThread):
        res = c.post("/api/url/search", params={"url": "https://www.facebook.com/testpage"})
    assert res.status_code == 402, res.text
    assert res.json()["detail"]["code"] == "DEMO_EXPIRED"
    assert any(b["code"] == "DEMO_EXPIRED"
               for b in c.get("/api/me/usage").json()["blockers"])


def test_url_search_plan_limit_402(env):
    """Explicit request above the demo cap is refused with PLAN_LIMIT."""
    db = env["db"]
    db.token_balances.update_one({"organization_id": env["users"]["demo_user"]["org"]},
                                 {"$set": {"remaining": 400, "used": 100}})
    try:
        c = _as(env, "demo_user")
        with patch("app.social.url_search.UrlSearchThread", _NoopThread):
            res = c.post("/api/url/search", params={
                "url": "https://www.facebook.com/testpage", "max_posts": 50})
        assert res.status_code == 402, res.text
        d = res.json()["detail"]
        assert d["code"] == "PLAN_LIMIT" and d["limit"] == 20 and d["metric"] == "posts_per_search"
    finally:
        db.token_balances.update_one({"organization_id": env["users"]["demo_user"]["org"]},
                                     {"$set": {"remaining": 0, "used": 500}})


def test_tokens_exhausted_does_not_use_a_monthly_search(env):
    """Tokens are charged before the monthly_searches quota is counted."""
    from fastapi import HTTPException
    calls = []

    async def _spy(**kw):
        calls.append(kw.get("metric"))

    c = _as(env, "demo_user")
    with patch("app.social.url_search.UrlSearchThread", _NoopThread), \
            patch("app.billing.entitlements.EntitlementService.enforce_quota_and_consume", _spy):
        res = c.post("/api/url/search", params={"url": "https://www.facebook.com/testpage"})
    assert res.status_code == 402 and res.json()["detail"]["code"] == "TOKENS_EXHAUSTED"
    assert "monthly_searches" not in calls


def test_search_quota_refusal_refunds_tokens(env):
    from fastapi import HTTPException
    db = env["db"]
    org = env["users"]["demo_user"]["org"]
    db.token_balances.update_one({"organization_id": org}, {"$set": {"remaining": 400, "used": 100}})

    async def _refuse(**kw):
        if kw.get("metric") == "monthly_searches":
            raise HTTPException(status_code=402, detail={"code": "QUOTA_EXCEEDED"})

    try:
        c = _as(env, "demo_user")
        with patch("app.social.url_search.UrlSearchThread", _NoopThread), \
                patch("app.billing.entitlements.EntitlementService.enforce_quota_and_consume", _refuse):
            res = c.post("/api/url/search", params={"url": "https://www.facebook.com/testpage"})
        assert res.status_code == 402 and res.json()["detail"]["code"] == "QUOTA_EXCEEDED"
        bal = db.token_balances.find_one({"organization_id": org})
        assert bal["remaining"] == 400 and bal["used"] == 100
        assert db.token_ledger.find_one({"organization_id": org, "type": "refund"})
    finally:
        db.token_balances.update_one({"organization_id": org}, {"$set": {"remaining": 0, "used": 500}})


def test_notify_user_respects_preferences(env):
    from app.events.notifications import notify_user
    db = env["db"]
    u = env["users"]["a_user2"]
    db.users.update_one({"_id": ObjectId(u["id"])},
                        {"$set": {"notification_preferences": {"search_completed": False}}})
    db.notifications.delete_many({"user_id": u["id"]})
    notify_user(u["id"], "search_completed", "Search done")
    assert db.notifications.count_documents({"user_id": u["id"]}) == 0
    notify_user(u["id"], "security_event", "New sign-in")  # no preference key: always delivered
    assert db.notifications.count_documents({"user_id": u["id"]}) == 1


def test_lead_detail_resolves_raw_comment_id_to_analysis(env):
    """ai_comments are upserted by comment_ref: opening a raw comment id shows
    its analysis (score, lifecycle), still within the caller's scope."""
    db = env["db"]
    u = env["users"]["a_user1"]
    owner = {"organization_id": u["org"], "user_id": u["id"], "created_by": u["email"]}
    raw_id = db.facebook_comments.insert_one({"text": "want to buy", **owner}).inserted_id
    db.ai_comments.insert_one({"comment_ref": str(raw_id), "is_lead": True,
                               "lead_score": 77, "lead_status": "contacted", **owner})
    body = _as(env, "a_user1").get(f"/api/comments/{raw_id}").json()
    assert body.get("lead_score") == 77 and body.get("lead_status") == "contacted"
    assert _as(env, "b_user").get(f"/api/comments/{raw_id}").status_code == 404
