"""
Tenant + per-user isolation of the user-portal data API (app/api/routes/search.py).

Runs entirely against an in-memory mongomock database (sync + async clients
share ONE mongomock client) — it never touches a real MongoDB.

Run:  python -m pytest tests/test_search_isolation.py -q -p no:cacheprovider
"""
from unittest.mock import patch

import mongomock
import mongomock_motor
import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

pytestmark = pytest.mark.own_db


# ── Fixture: app wired to a shared mongomock database ────────────────────────

@pytest.fixture(scope="module")
def env():
    mclient = mongomock.MongoClient()
    aclient = mongomock_motor.AsyncMongoMockClient(mock_mongo_client=mclient)
    patches = [
        patch("app.db.mongo.get_sync_client", return_value=mclient),
        patch("app.db.mongo.get_async_client", return_value=aclient),
        patch("app.admin.settings.is_maintenance_enabled", return_value=False),
        # index options (partial filters etc.) are irrelevant under mongomock
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
            # seed AFTER startup so lifespan migrations can't rewrite the data
            data = _seed(db)
            yield {"client": client, "db": db, **data}
    finally:
        for p in reversed(patches):
            p.stop()


def _seed(db):
    from app.auth.service import build_session_value, create_tracked_session

    for name in ("organizations", "users", "organization_members", "search_history",
                 "facebook_pages", "facebook_posts", "facebook_comments",
                 "ai_comments", "security_events", "role_permissions"):
        db[name].delete_many({})

    org_a, org_b = ObjectId(), ObjectId()
    db.organizations.insert_many([
        {"_id": org_a, "name": "Org A", "slug": "org-a", "status": "active"},
        {"_id": org_b, "name": "Org B", "slug": "org-b", "status": "active"},
    ])

    users = {}

    def add_user(key, org, role, member_status="active"):
        uid = ObjectId()
        email = f"{key}@example.com"
        db.users.insert_one({"_id": uid, "email": email, "name": key,
                             "status": "active",
                             "default_organization_id": str(org)})
        db.organization_members.insert_one({
            "user_id": str(uid), "organization_id": str(org),
            "role": role, "status": member_status})
        claims = {"user_id": str(uid), "email": email, "name": key,
                  "scope": "site", "organization_id": str(org), "org_role": role}
        cookie = build_session_value(create_tracked_session(claims))
        users[key] = {"id": str(uid), "email": email, "org": str(org),
                      "cookie": cookie}

    add_user("a_admin", org_a, "owner")
    add_user("a_user1", org_a, "member")
    add_user("a_user2", org_a, "member")
    add_user("a_viewer", org_a, "viewer")
    add_user("b_admin", org_b, "owner")
    add_user("a_removed", org_a, "member", member_status="removed")

    records = {}

    def add_tree(key):
        u = users[key]
        owner = {"organization_id": u["org"], "user_id": u["id"],
                 "created_by": u["email"]}
        run_id = f"URL_{key}"
        db.search_history.insert_one({"run_id": run_id, "status": "completed",
                                      "query": f"https://facebook.com/{key}",
                                      "url_search": True, **owner})
        page_id = db.facebook_pages.insert_one({
            "page_name": f"page {key}", "facebook_url": f"https://facebook.com/{key}",
            "platform": "facebook", "search_run_id": run_id, **owner}).inserted_id
        post_id = db.facebook_posts.insert_one({
            "post_url": f"https://facebook.com/{key}/posts/1", "platform": "facebook",
            "page_ref": str(page_id), "search_run_id": run_id, **owner}).inserted_id
        comment_id = db.facebook_comments.insert_one({
            "text": f"interested, call me {key}", "author_name": key,
            "comment_url": f"https://facebook.com/{key}/posts/1?comment_id=1",
            "post_ref": str(post_id), "search_run_id": run_id, **owner}).inserted_id
        lead_id = db.ai_comments.insert_one({
            "comment_ref": str(comment_id), "post_ref": str(post_id),
            "page_ref": str(page_id), "is_lead": True, "lead_score": 70,
            "lead_status": "new", "platform": "facebook",
            "comment_text": f"interested {key}", "search_run_id": run_id,
            **owner}).inserted_id
        records[key] = {"run": run_id, "page": str(page_id), "post": str(post_id),
                        "comment": str(comment_id), "lead": str(lead_id)}

    for key in ("a_user1", "a_user2", "b_admin"):
        add_tree(key)

    # a lead owned by a_user2 but assigned to a_user1
    u2 = users["a_user2"]
    assigned = db.ai_comments.insert_one({
        "comment_ref": str(ObjectId()), "is_lead": True, "lead_score": 60,
        "lead_status": "new", "organization_id": u2["org"], "user_id": u2["id"],
        "created_by": u2["email"], "assigned_user_id": users["a_user1"]["id"],
        "comment_text": "assigned lead"}).inserted_id
    records["assigned_lead"] = str(assigned)

    # legacy page (no user_id) created by a_user1 — visible to a_user1 via email
    legacy = db.facebook_pages.insert_one({
        "page_name": "legacy page", "facebook_url": "https://facebook.com/legacy",
        "organization_id": users["a_user1"]["org"],
        "created_by": users["a_user1"]["email"]}).inserted_id
    records["legacy_page"] = str(legacy)

    return {"users": users, "rec": records}


def _as(env, key):
    from app.auth.service import COOKIE_NAME
    client = env["client"]
    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, env["users"][key]["cookie"])
    return client


def _ids(items):
    return {i.get("id") for i in items}


# ── Cross-tenant isolation ───────────────────────────────────────────────────

def test_org_admin_cannot_read_other_tenant_by_id(env):
    b = env["rec"]["b_admin"]
    c = _as(env, "a_admin")
    urls = [
        f"/api/pages/{b['page']}",
        f"/api/pages/{b['page']}/posts",
        f"/api/posts/{b['post']}",
        f"/api/posts/{b['post']}/comments",
        f"/api/comments/{b['comment']}",
        f"/api/comments/{b['lead']}",
        f"/api/search/{b['run']}",
        f"/api/url/search/{b['run']}/report",
        f"/api/leads/{b['lead']}",
    ]
    for url in urls:
        assert c.get(url).status_code == 404, url


def test_org_admin_cannot_modify_other_tenant(env):
    b = env["rec"]["b_admin"]
    db = env["db"]
    c = _as(env, "a_admin")
    assert c.patch(f"/api/leads/{b['lead']}",
                   json={"lead_status": "contacted"}).status_code == 404
    assert c.post(f"/api/leads/{b['lead']}/notes", json={"text": "x"}).status_code == 404
    assert c.post(f"/api/search/{b['run']}/cancel").status_code == 404
    assert c.delete(f"/api/search/{b['run']}").status_code == 404
    assert c.post(f"/api/pages/{b['page']}/posts").status_code == 404
    assert c.post(f"/api/posts/{b['post']}/comments").status_code == 404
    # nothing of B was touched
    assert db.search_history.find_one({"run_id": b["run"]}) is not None
    assert db.facebook_pages.find_one({"_id": ObjectId(b["page"])}) is not None
    lead = db.ai_comments.find_one({"_id": ObjectId(b["lead"])})
    assert lead["lead_status"] == "new" and not lead.get("notes")


def test_org_admin_lists_never_contain_other_tenant(env):
    b = env["rec"]["b_admin"]
    c = _as(env, "a_admin")
    pages = c.get("/api/pages", params={"limit": 200}).json()["pages"]
    assert b["page"] not in _ids(pages)
    assert all(p.get("organization_id") == env["users"]["a_admin"]["org"] for p in pages)
    runs = c.get("/api/search/history", params={"limit": 100}).json()["searches"]
    assert b["run"] not in {r["run_id"] for r in runs}
    leads = c.get("/api/leads", params={"page_size": 100}).json()["leads"]
    assert b["lead"] not in _ids(leads)
    stats = c.get("/api/leads/stats/summary").json()
    assert stats["total"] == 3  # a_user1 + a_user2 + the assigned lead


def test_cross_tenant_probe_logs_security_event(env):
    b = env["rec"]["b_admin"]
    db = env["db"]
    c = _as(env, "a_admin")
    assert c.get(f"/api/pages/{b['page']}").status_code == 404
    ev = db.security_events.find_one({"type": "cross_tenant_access",
                                      "details.resource_id": b["page"]})
    assert ev is not None
    assert ev["actor_email"] == env["users"]["a_admin"]["email"]
    assert ev["target_organization_id"] == env["users"]["b_admin"]["org"]


# ── Per-user isolation inside one organization ───────────────────────────────

def test_member_cannot_see_other_members_records(env):
    u2 = env["rec"]["a_user2"]
    c = _as(env, "a_user1")
    for url in (f"/api/search/{u2['run']}", f"/api/pages/{u2['page']}",
                f"/api/posts/{u2['post']}", f"/api/leads/{u2['lead']}",
                f"/api/comments/{u2['lead']}", f"/api/url/search/{u2['run']}/report"):
        assert c.get(url).status_code == 404, url
    assert c.patch(f"/api/leads/{u2['lead']}",
                   json={"lead_status": "contacted"}).status_code == 404
    assert c.delete(f"/api/search/{u2['run']}").status_code == 404
    runs = {r["run_id"] for r in c.get("/api/search/history").json()["searches"]}
    assert u2["run"] not in runs and env["rec"]["a_user1"]["run"] in runs
    pages = _ids(c.get("/api/pages").json()["pages"])
    assert u2["page"] not in pages
    assert env["rec"]["a_user1"]["page"] in pages
    assert env["rec"]["legacy_page"] in pages  # legacy created_by fallback
    leads = _ids(c.get("/api/leads").json()["leads"])
    assert u2["lead"] not in leads and env["rec"]["a_user1"]["lead"] in leads
    ev = env["db"].security_events.find_one({"type": "cross_user_access",
                                             "details.resource_id": u2["page"]})
    assert ev is not None


def test_org_admin_sees_all_members(env):
    r = env["rec"]
    c = _as(env, "a_admin")
    runs = {x["run_id"] for x in c.get("/api/search/history").json()["searches"]}
    assert {r["a_user1"]["run"], r["a_user2"]["run"]} <= runs
    pages = _ids(c.get("/api/pages").json()["pages"])
    assert {r["a_user1"]["page"], r["a_user2"]["page"]} <= pages
    leads = _ids(c.get("/api/leads").json()["leads"])
    assert {r["a_user1"]["lead"], r["a_user2"]["lead"]} <= leads
    assert c.get(f"/api/leads/{r['a_user2']['lead']}").status_code == 200
    report = c.get(f"/api/url/search/{r['a_user2']['run']}/report").json()
    assert report["page"]["id"] == r["a_user2"]["page"]
    assert len(report["posts"]) == 1 and len(report["comments"]) == 1


def test_assigned_lead_visible_to_assignee(env):
    lead = env["rec"]["assigned_lead"]
    c = _as(env, "a_user1")
    assert lead in _ids(c.get("/api/leads").json()["leads"])
    assert c.get(f"/api/leads/{lead}").status_code == 200
    # ...but not to an unrelated member
    other = _as(env, "a_viewer")
    assert other.get(f"/api/leads/{lead}").status_code == 404


def test_member_export_of_foreign_page_is_404(env):
    c = _as(env, "a_user1")
    res = c.get("/api/export/posts.csv",
                params={"page_id": env["rec"]["a_user2"]["page"]})
    assert res.status_code == 404


# ── Permissions ──────────────────────────────────────────────────────────────

def test_viewer_cannot_patch_lead(env):
    c = _as(env, "a_viewer")
    assert c.get("/api/leads").status_code == 200
    res = c.patch(f"/api/leads/{env['rec']['a_user1']['lead']}",
                  json={"lead_status": "contacted"})
    assert res.status_code == 403
    assert c.delete(f"/api/search/{env['rec']['a_user1']['run']}").status_code == 403


def test_member_without_assign_permission_cannot_assign(env):
    c = _as(env, "a_user1")
    res = c.patch(f"/api/leads/{env['rec']['a_user1']['lead']}",
                  json={"assigned_user_id": env["users"]["a_user2"]["id"]})
    assert res.status_code == 403


def test_assignee_must_be_active_member_of_same_org(env):
    lead = env["rec"]["a_user1"]["lead"]
    c = _as(env, "a_admin")
    res = c.patch(f"/api/leads/{lead}",
                  json={"assigned_user_id": env["users"]["b_admin"]["id"]})
    assert res.status_code == 400
    res = c.patch(f"/api/leads/{lead}",
                  json={"assigned_user_id": env["users"]["a_removed"]["id"]})
    assert res.status_code == 400
    res = c.patch(f"/api/leads/{lead}",
                  json={"assigned_user_id": env["users"]["a_user2"]["id"]})
    assert res.status_code == 200
    doc = env["db"].ai_comments.find_one({"_id": ObjectId(lead)})
    assert doc["assigned_user_id"] == env["users"]["a_user2"]["id"]
    assert doc["assigned_to"] == env["users"]["a_user2"]["email"]


def test_no_active_membership_is_forbidden(env):
    c = _as(env, "a_removed")
    assert c.get("/api/pages").status_code == 403
    assert c.get("/api/leads").status_code == 403


def test_no_session_is_unauthorized(env):
    client = env["client"]
    client.cookies.clear()
    assert client.get("/api/pages").status_code == 401


# ── Scoped delete ────────────────────────────────────────────────────────────

def test_member_delete_removes_only_own_run_tree(env):
    db = env["db"]
    r = env["rec"]
    c = _as(env, "a_user1")
    res = c.delete(f"/api/search/{r['a_user1']['run']}")
    assert res.status_code == 200
    assert res.json()["deleted"] == {"pages": 1, "posts": 1, "comments": 1, "leads": 1}
    assert db.search_history.find_one({"run_id": r["a_user1"]["run"]}) is None
    assert db.facebook_pages.find_one({"_id": ObjectId(r["a_user1"]["page"])}) is None
    # other members' and tenants' data untouched
    for key in ("a_user2", "b_admin"):
        assert db.search_history.find_one({"run_id": r[key]["run"]}) is not None
        assert db.ai_comments.find_one({"_id": ObjectId(r[key]["lead"])}) is not None
