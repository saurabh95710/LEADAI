"""
Improvements round — User portal (app/api/routes/me.py).

  16. GET /api/me/leads.csv — same filters as GET /api/me/leads, own + assigned
      scope only, quota-metered (csv_export / monthly_exports), token-charged,
      audited, recorded in ``exports``, formula-injection safe.
  15. POST /api/me/exports/client-log — in-browser table CSV downloads are
      audited (``export.client``) and recorded, scoped to the caller.

In-memory mongomock only.
Run:  python -m pytest tests/test_improvements_user.py -q -p no:cacheprovider
"""
import csv
import io
from datetime import timedelta
from unittest.mock import patch

import mongomock
import mongomock_motor
import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

pytestmark = pytest.mark.own_db


async def _no_quota(**kw):
    _QUOTA_CALLS.append(kw)
    return 1


_QUOTA_CALLS: list = []


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


@pytest.fixture()
def quota_ok():
    _QUOTA_CALLS.clear()
    with patch("app.billing.entitlements.EntitlementService.enforce_quota_and_consume",
               new=_no_quota):
        yield _QUOTA_CALLS


def _seed(db):
    from app.auth.service import build_session_value, create_tracked_session
    from app.db.models import utcnow

    for name in ("organizations", "users", "organization_members", "search_history",
                 "ai_comments", "exports", "token_ledger", "token_balances",
                 "security_events", "role_permissions", "audit_logs", "subscriptions"):
        db[name].delete_many({})

    now = utcnow()
    org_a, org_b, org_demo = ObjectId(), ObjectId(), ObjectId()
    demo_cfg = {"allowed_platforms": ["facebook"], "max_searches": 10,
                "posts_per_search": 20, "comments_per_post": 30, "max_leads": 200,
                "ai_enabled": True, "exports_enabled": False, "max_users": 1}
    db.organizations.insert_many([
        {"_id": org_a, "name": "Org A", "slug": "org-a", "status": "active"},
        {"_id": org_b, "name": "Org B", "slug": "org-b", "status": "active"},
        {"_id": org_demo, "name": "Demo Org", "slug": "demo-org", "status": "demo",
         "demo": {"expires_at": now + timedelta(days=5), "config": demo_cfg}},
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
    add_user("a_viewer", org_a, "viewer")
    add_user("b_user", org_b, "member")
    add_user("demo_user", org_demo, "owner")

    def lead(owner_key, **kw):
        u = users[owner_key]
        doc = {"is_lead": True, "lead_status": "new", "platform": "facebook",
               "organization_id": u["org"], "user_id": u["id"], "created_by": u["email"],
               "lead_created_at": now, **kw}
        return str(db.ai_comments.insert_one(doc).inserted_id)

    rec = {
        # a_user1's own leads — varied so every filter has something to cut
        "u1_hot": lead("a_user1", commenter_name="Hot Harry", comment_text="need price asap",
                       lead_score=92, lead_quality="hot", lead_priority="high",
                       search_run_id="RUN_1", lead_created_at=now - timedelta(hours=1)),
        "u1_warm": lead("a_user1", commenter_name="Warm Wendy", comment_text="maybe later",
                        lead_score=65, lead_quality="warm", priority="medium",
                        lead_status="contacted", platform="instagram", search_run_id="RUN_2",
                        lead_created_at=now - timedelta(hours=2)),
        "u1_cold": lead("a_user1", commenter_name="Cold Carl", comment_text="just browsing",
                        lead_score=20, lead_quality="cold", lead_priority="low",
                        search_run_id="RUN_1", lead_created_at=now - timedelta(hours=3)),
        # formula injection payloads in user-controlled text
        "u1_evil": lead("a_user1", commenter_name="=HYPERLINK(\"http://evil\",\"x\")",
                        comment_text="+cmd|' /C calc'!A0", requirement="@SUM(1+1)",
                        location="-2+3", lead_score=50, lead_quality="warm",
                        search_run_id="RUN_3", lead_created_at=now - timedelta(hours=4)),
        # a_user2's lead assigned to a_user1 → visible to a_user1 (view=assigned/all)
        "u2_assigned": lead("a_user2", commenter_name="Assigned Anna", comment_text="call me",
                            lead_score=75, lead_quality="hot",
                            assigned_user_id=users["a_user1"]["id"],
                            assigned_to=users["a_user1"]["email"],
                            lead_created_at=now - timedelta(hours=5)),
        # a_user2's unassigned lead → NEVER visible to a_user1
        "u2_private": lead("a_user2", commenter_name="Private Pete", comment_text="secret",
                           lead_score=99, lead_quality="hot"),
        # owner's lead → not visible to a_user1 either (force-own even for the owner)
        "owner_lead": lead("a_owner", commenter_name="Owner Olga", comment_text="owner stuff",
                           lead_score=88, lead_quality="hot"),
        # another org
        "b_lead": lead("b_user", commenter_name="Bob Other Org", comment_text="org b",
                       lead_score=95, lead_quality="hot"),
    }
    # non-lead analysis row of a_user1 (must never be exported)
    db.ai_comments.insert_one({"is_lead": False, "commenter_name": "Not A Lead",
                               "organization_id": users["a_user1"]["org"],
                               "user_id": users["a_user1"]["id"], "lead_score": 5})
    # org A is token-metered so the export token cost is observable
    db.token_balances.insert_one({"organization_id": users["a_user1"]["org"],
                                  "allocated": 1000, "used": 0, "remaining": 1000,
                                  "source": "plan", "expires_at": now + timedelta(days=30)})
    return {"users": users, "rec": rec}


def _as(env, key):
    from app.auth.service import COOKIE_NAME
    client = env["client"]
    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, env["users"][key]["cookie"])
    return client


def _csv_rows(res):
    text = res.content.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def _names_json(c, params):
    body = c.get("/api/me/leads", params={**params, "page_size": 100}).json()
    return [i["commenter_name"] for i in body["items"]]


# ── 16. leads.csv ────────────────────────────────────────────────────────────

FILTER_CASES = [
    {},
    {"view": "mine"},
    {"view": "assigned"},
    {"quality": "hot"},
    {"quality": "warm", "view": "mine"},
    {"status": "new"},
    {"status": "contacted"},
    {"priority": "high"},
    {"priority": "medium"},          # legacy ``priority`` fallback
    {"min_score": 60},
    {"platform": "instagram"},
    {"run_id": "RUN_1"},
    {"q": "harry"},
    {"q": "call"},
    {"sort": "newest"},
    {"sort": "oldest"},
    {"sort": "score", "min_score": 50, "view": "all"},
]


@pytest.mark.parametrize("params", FILTER_CASES)
def test_leads_csv_filter_parity_with_list(env, quota_ok, params):
    c = _as(env, "a_user1")
    expected = _names_json(c, params)
    res = c.get("/api/me/leads.csv", params=params)
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/csv")
    got = [r["commenter_name"].lstrip("'") for r in _csv_rows(res)]
    assert got == expected, params  # same rows, same order


def test_leads_csv_scope_is_own_plus_assigned_only(env, quota_ok):
    rec_names = {"Hot Harry", "Warm Wendy", "Cold Carl", "Assigned Anna"}
    c = _as(env, "a_user1")
    names = {r["commenter_name"].lstrip("'") for r in _csv_rows(c.get("/api/me/leads.csv"))}
    assert rec_names <= names
    for foreign in ("Private Pete", "Owner Olga", "Bob Other Org", "Not A Lead"):
        assert foreign not in names
    assigned = _csv_rows(c.get("/api/me/leads.csv", params={"view": "assigned"}))
    assert [r["commenter_name"] for r in assigned] == ["Assigned Anna"]
    assert assigned[0]["assigned_to_me"] == "Yes" and assigned[0]["owned_by_me"] == "No"
    mine = {r["commenter_name"].lstrip("'") for r in
            _csv_rows(c.get("/api/me/leads.csv", params={"view": "mine"}))}
    assert "Assigned Anna" not in mine


def test_leads_csv_owner_still_force_own(env, quota_ok):
    names = {r["commenter_name"] for r in
             _csv_rows(_as(env, "a_owner").get("/api/me/leads.csv"))}
    assert names == {"Owner Olga"}


def test_leads_csv_other_org_isolated(env, quota_ok):
    names = {r["commenter_name"] for r in
             _csv_rows(_as(env, "b_user").get("/api/me/leads.csv"))}
    assert names == {"Bob Other Org"}
    # a foreign run id is refused before anything is metered or recorded
    before = env["db"].exports.count_documents({})
    assert _as(env, "b_user").get("/api/me/leads.csv",
                                  params={"run_id": "RUN_1"}).status_code == 404
    assert env["db"].exports.count_documents({}) == before


def test_leads_csv_colleague_run_id_404_and_not_echoed(env, quota_ok):
    """A same-org colleague's run id is not visible: 404, no quota, no export
    record echoing the foreign id into my export history."""
    db = env["db"]
    db.search_history.insert_one({"run_id": "RUN_U2_PRIVATE", "status": "completed",
                                  "organization_id": env["users"]["a_user2"]["org"],
                                  "user_id": env["users"]["a_user2"]["id"]})
    c = _as(env, "a_user1")
    assert c.get("/api/me/leads.csv", params={"run_id": "RUN_U2_PRIVATE"}).status_code == 404
    assert quota_ok == []
    assert "RUN_U2_PRIVATE" not in c.get("/api/me/exports", params={"page_size": 100}).text
    # the colleague can export their own run
    assert _as(env, "a_user2").get("/api/me/leads.csv",
                                   params={"run_id": "RUN_U2_PRIVATE"}).status_code == 200


def test_leads_csv_viewer_forbidden(env, quota_ok):
    res = _as(env, "a_viewer").get("/api/me/leads.csv")
    assert res.status_code == 403
    assert quota_ok == []  # nothing metered


def test_leads_csv_requires_login(env):
    env["client"].cookies.clear()
    assert env["client"].get("/api/me/leads.csv").status_code == 401


def test_leads_csv_invalid_filters_422(env, quota_ok):
    c = _as(env, "a_user1")
    assert c.get("/api/me/leads.csv", params={"view": "everyone"}).status_code == 422
    assert c.get("/api/me/leads.csv", params={"quality": "$ne"}).status_code == 422
    assert quota_ok == []


def test_leads_csv_formula_injection_sanitised(env, quota_ok):
    rows = _csv_rows(_as(env, "a_user1").get("/api/me/leads.csv", params={"run_id": "RUN_3"}))
    assert len(rows) == 1
    r = rows[0]
    assert r["commenter_name"].startswith("'=")
    assert r["comment_text"].startswith("'+")
    assert r["requirement"].startswith("'@")
    assert r["location"].startswith("'-")


def test_leads_csv_metered_charged_recorded_audited(env, quota_ok):
    db = env["db"]
    u = env["users"]["a_user1"]
    before_bal = db.token_balances.find_one({"organization_id": u["org"]})["remaining"]
    before_exports = db.exports.count_documents({"user_id": u["id"], "scope": "leads"})
    res = _as(env, "a_user1").get("/api/me/leads.csv",
                                  params={"quality": "hot", "view": "all"})
    assert res.status_code == 200
    # quota: csv_export feature + monthly_exports metric, attributed to me
    assert len(quota_ok) == 1
    call = quota_ok[0]
    assert call["feature_key"] == "csv_export" and call["metric"] == "monthly_exports"
    assert call["quantity"] == 1 and call["user_id"] == u["id"]
    assert str(call["organization_id"]) == u["org"]
    # token cost of "export"
    after_bal = db.token_balances.find_one({"organization_id": u["org"]})["remaining"]
    from app.lifecycle.config import token_cost
    assert before_bal - after_bal == token_cost("export")
    assert db.token_ledger.find_one({"organization_id": u["org"], "user_id": u["id"],
                                     "reason": "export", "reference": "export:leads"})
    # exports record owned by me, with filters and row count
    assert db.exports.count_documents({"user_id": u["id"], "scope": "leads"}) == before_exports + 1
    exp = db.exports.find_one({"user_id": u["id"], "scope": "leads"}, sort=[("_id", -1)])
    assert exp["organization_id"] == u["org"] and exp["rows"] == 2
    assert exp["filters"]["quality"] == "hot"
    # audited
    audit = db.audit_logs.find_one({"action": "export.csv", "resource_type": "leads"},
                                   sort=[("_id", -1)])
    assert audit is not None
    assert audit["details"]["rows"] == 2 and audit["details"]["filters"]["quality"] == "hot"
    # visible in my export history, not in a colleague's
    mine = _as(env, "a_user1").get("/api/me/exports", params={"scope": "leads"}).json()
    assert mine["total"] >= 1 and mine["items"][0]["scope"] == "leads"
    assert str(exp["_id"]) in {e["id"] for e in mine["items"]}
    other = _as(env, "a_user2").get("/api/me/exports", params={"scope": "leads"}).json()
    assert str(exp["_id"]) not in {e["id"] for e in other["items"]}


def test_leads_csv_quota_exceeded_402(env):
    from app.billing.entitlements import QuotaExceededException  # noqa: F401

    async def _refuse(**kw):
        from app.billing.entitlements import QuotaExceededException as Q
        raise Q("monthly_exports", used=2, limit=2, remaining=0)

    db = env["db"]
    u = env["users"]["a_user1"]
    before_bal = db.token_balances.find_one({"organization_id": u["org"]})["remaining"]
    before = db.exports.count_documents({"user_id": u["id"]})
    with patch("app.billing.entitlements.EntitlementService.enforce_quota_and_consume",
               new=_refuse):
        res = _as(env, "a_user1").get("/api/me/leads.csv")
    assert res.status_code == 402, res.text
    assert "monthly_exports" in res.text
    # nothing charged or recorded when refused
    assert db.token_balances.find_one({"organization_id": u["org"]})["remaining"] == before_bal
    assert db.exports.count_documents({"user_id": u["id"]}) == before


def test_leads_csv_feature_not_in_plan_real_entitlements(env):
    """Demo org with exports disabled → the real EntitlementService refuses
    with the structured FEATURE_NOT_AVAILABLE upgrade error (same as the other
    exports), and nothing is recorded."""
    db = env["db"]
    before = db.exports.count_documents({})
    res = _as(env, "demo_user").get("/api/me/leads.csv")
    assert res.status_code in (402, 403), res.text
    detail = res.json()["detail"]
    assert detail["code"] == "FEATURE_NOT_AVAILABLE" and detail["feature"] == "csv_export"
    assert detail["upgrade_available"] is True
    assert db.exports.count_documents({}) == before


def test_leads_csv_disabled_by_admin_403(env, quota_ok):
    with patch("app.admin.settings.get_bool", return_value=False):
        res = _as(env, "a_user1").get("/api/me/leads.csv")
    assert res.status_code == 403
    assert quota_ok == []


# ── 15. client-log ───────────────────────────────────────────────────────────

def test_client_log_records_and_audits(env):
    db = env["db"]
    u = env["users"]["a_user1"]
    body = {"table": "history", "rows": 12, "columns": ["URL", "Status"],
            "filters": {"status": "completed", "q": "facebook"}}
    res = _as(env, "a_user1").post("/api/me/exports/client-log", json=body)
    assert res.status_code == 200, res.text
    doc = db.exports.find_one({"_id": ObjectId(res.json()["id"])})
    assert doc["user_id"] == u["id"] and doc["organization_id"] == u["org"]
    assert doc["scope"] == "history" and doc["rows"] == 12 and doc["client"] is True
    assert doc["source"] == "user_portal_client"
    assert doc["filters"] == {"status": "completed", "q": "facebook"}
    audit = db.audit_logs.find_one({"action": "export.client",
                                    "resource_id": res.json()["id"]})
    assert audit is not None and audit["organization_id"] == u["org"]
    assert audit["details"]["table"] == "history" and audit["details"]["rows"] == 12
    # visible only in MY export history
    ids = {e["id"] for e in _as(env, "a_user1").get(
        "/api/me/exports", params={"scope": "history"}).json()["items"]}
    assert res.json()["id"] in ids
    for other in ("a_user2", "a_owner", "b_user"):
        ids = {e["id"] for e in _as(env, other).get("/api/me/exports").json()["items"]}
        assert res.json()["id"] not in ids


def test_client_log_does_not_count_as_metered_export(env):
    c = _as(env, "a_user2")
    before = c.get("/api/me/usage").json()["me"]["exports"]
    assert c.post("/api/me/exports/client-log",
                  json={"table": "ledger", "rows": 3}).status_code == 200
    assert c.get("/api/me/usage").json()["me"]["exports"] == before


def test_client_log_ledger_allowed_for_viewer_but_exports_permission_checked(env):
    v = _as(env, "a_viewer")
    assert v.post("/api/me/exports/client-log", json={"table": "ledger", "rows": 1}).status_code == 200
    assert v.post("/api/me/exports/client-log", json={"table": "exports", "rows": 1}).status_code == 200
    assert v.post("/api/me/exports/client-log", json={"table": "history", "rows": 1}).status_code == 200


def test_client_log_permission_denied_when_role_lacks_it(env):
    """A member whose Admin removed exports.view / search.view cannot log
    those tables (403 permission_denied); the own ledger stays allowed."""
    db = env["db"]
    u = env["users"]["a_viewer"]
    q = {"user_id": u["id"], "organization_id": u["org"]}
    db.organization_members.update_one(q, {"$set": {"permissions_override": {
        "exports.view": False, "search.view": False}}})
    try:
        c = _as(env, "a_viewer")
        for table in ("exports", "history"):
            res = c.post("/api/me/exports/client-log", json={"table": table, "rows": 1})
            assert res.status_code == 403, (table, res.text)
            assert res.json()["detail"]["code"] == "permission_denied"
        assert c.post("/api/me/exports/client-log",
                      json={"table": "ledger", "rows": 1}).status_code == 200
    finally:
        db.organization_members.update_one(q, {"$unset": {"permissions_override": ""}})


@pytest.mark.parametrize("body", [
    {"table": "leads", "rows": 1},                   # not a client-side table
    {"table": "../audit_logs", "rows": 1},
    {"table": "history", "rows": -1},
    {"table": "history", "rows": 1, "organization_id": "x"},   # extra fields forbidden
    {"table": "history", "rows": 1, "user_id": "x"},
    {"table": "history", "rows": 1, "filters": {"$where": "1"}},
    {"table": "history", "rows": 1, "filters": {"a": {"$ne": 1}}},
    {"table": "history", "rows": 1, "columns": ["c"] * 51},
])
def test_client_log_validation(env, body):
    res = _as(env, "a_user1").post("/api/me/exports/client-log", json=body)
    assert res.status_code == 422, (body, res.text)


def test_client_log_cannot_spoof_owner(env):
    db = env["db"]
    res = _as(env, "b_user").post("/api/me/exports/client-log",
                                  json={"table": "ledger", "rows": 2})
    assert res.status_code == 200
    doc = db.exports.find_one({"_id": ObjectId(res.json()["id"])})
    assert doc["organization_id"] == env["users"]["b_user"]["org"]
    assert doc["user_id"] == env["users"]["b_user"]["id"]


def test_client_log_requires_login_and_json(env):
    env["client"].cookies.clear()
    assert env["client"].post("/api/me/exports/client-log",
                              json={"table": "history", "rows": 1}).status_code == 401
    c = _as(env, "a_user1")
    assert c.post("/api/me/exports/client-log", content=b"nope",
                  headers={"Content-Type": "application/json"}).status_code == 400
    assert c.post("/api/me/exports/client-log", json=["x"]).status_code == 400


def test_client_log_rate_limited(env):
    from app.api.routes import me
    me._client_log_hits.clear()
    c = _as(env, "a_user2")
    try:
        codes = [c.post("/api/me/exports/client-log",
                        json={"table": "ledger", "rows": 1}).status_code
                 for _ in range(me._CLIENT_LOG_MAX + 1)]
        assert codes[:-1] == [200] * me._CLIENT_LOG_MAX
        assert codes[-1] == 429
    finally:
        me._client_log_hits.clear()
