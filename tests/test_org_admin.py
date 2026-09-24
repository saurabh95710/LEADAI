"""
Organization Admin Portal (/api/org-admin) — isolation & permission proof.
Runs on an in-memory MongoDB (mongomock), never on a real database.

Proves:
  * Admin A cannot read or modify Org B through any org-admin endpoint:
    ids of B answer 404, lists never contain B's rows.
  * Members / managers / viewers get 403 on the org-admin API.
  * An organization whose Admin portal is not enabled (demo) gets 403
    {code: admin_portal_disabled}.
  * Exports contain only own-org rows, CSV formula injection is sanitised,
    exports are audited and recorded in `exports`.
  * Support tickets are org-scoped and notify super admins.
  * Lead rules (org keywords) are stored per org and drive org_lead_rules.
"""
import csv
import io
import os
from datetime import datetime, timedelta
from unittest.mock import patch

import mongomock
import mongomock_motor
import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.auth.crypto import hash_password
from app.auth.service import COOKIE_NAME, build_session_value, create_tracked_session

PASSWORD = "Str0ngPass!"


async def _no_quota(*a, **kw):
    return 0


@pytest.fixture
def env():
    sync_client = mongomock.MongoClient()
    async_client = mongomock_motor.AsyncMongoMockClient(mock_mongo_client=sync_client)
    from app.auth import permissions as perm_mod
    from app.billing import plans as plans_mod
    from app.lifecycle import config as cfg_mod
    plans_mod.invalidate_plan_cache()
    cfg_mod.clear_cache()
    perm_mod.invalidate_permission_cache()
    from app.api.routes import auth as auth_routes
    from app.auth import service as svc_mod
    svc_mod._login_attempts.clear()
    auth_routes._reset_attempts.clear()
    with patch("app.db.mongo.get_sync_client", return_value=sync_client), \
            patch("app.db.mongo.get_async_client", return_value=async_client), \
            patch("app.admin.settings.is_maintenance_enabled", return_value=False), \
            patch("app.billing.entitlements.EntitlementService.enforce_quota_and_consume",
                  new=_no_quota), \
            patch.dict(os.environ, {"STRIPE_SECRET_KEY": ""}):
        from app.main import app
        from app.config import get_settings
        with TestClient(app) as client:
            yield client, sync_client[get_settings().mongo_db_name]
    plans_mod.invalidate_plan_cache()
    cfg_mod.clear_cache()


def _org(db, name, status="active", portal=True):
    return str(db.organizations.insert_one({
        "name": name, "slug": name.lower().replace(" ", "-") + str(ObjectId())[-4:],
        "status": status, "settings": {}, "admin_portal_enabled": portal}).inserted_id)


def _user(db, email, org_id, role="member", member_status="active"):
    uid = str(db.users.insert_one({
        "email": email, "name": email.split("@")[0], "password_hash": hash_password(PASSWORD),
        "status": "active", "is_platform_admin": False, "platform_role": None,
        "default_organization_id": org_id}).inserted_id)
    db.organization_members.insert_one({
        "organization_id": org_id, "user_id": uid, "role": role, "status": member_status,
        "permissions_override": {}, "created_at": datetime.utcnow()})
    return uid


def _cookie(uid, email, org_id, role):
    tracked = create_tracked_session({"user_id": uid, "email": email, "name": email,
                                      "scope": "site", "organization_id": org_id,
                                      "org_role": role})
    return {COOKIE_NAME: build_session_value(tracked)}


def _seed_data(db, org_id, uid, tag):
    """One run -> page -> post -> comment -> lead for an org."""
    now = datetime.utcnow()
    run_id = f"URL{tag}{ObjectId()}"
    common = {"organization_id": org_id, "user_id": uid, "created_by": f"{tag}@x.test"}
    db.search_history.insert_one({**common, "run_id": run_id, "query": f"https://facebook.com/{tag}",
                                  "intent": {"platform": "facebook"}, "status": "completed",
                                  "pages_found": 1, "created_at": now})
    page_id = str(db.facebook_pages.insert_one({**common, "page_name": f"Page {tag}",
                                                "platform": "facebook", "search_run_id": run_id,
                                                "created_at": now}).inserted_id)
    post_id = str(db.facebook_posts.insert_one({**common, "page_ref": page_id, "platform": "facebook",
                                                "caption": f"=cmd|' /C calc'!A0 {tag}",
                                                "search_run_id": run_id,
                                                "created_at": now}).inserted_id)
    comment_id = str(db.facebook_comments.insert_one({**common, "post_ref": post_id,
                                                      "text": f"interested {tag}",
                                                      "author_name": f"Author {tag}",
                                                      "platform": "facebook", "search_run_id": run_id,
                                                      "created_at": now}).inserted_id)
    lead_id = str(db.ai_comments.insert_one({**common, "comment_ref": comment_id, "post_ref": post_id,
                                             "page_ref": page_id, "is_lead": True, "lead_score": 80,
                                             "commenter_name": f"=HYPERLINK(\"http://evil\") {tag}",
                                             "comment_text": f"@SUM(1) buy {tag}",
                                             "platform": "facebook", "search_run_id": run_id,
                                             "lead_status": "new", "analyzed_at": now}).inserted_id)
    return {"run_id": run_id, "page_id": page_id, "post_id": post_id,
            "comment_id": comment_id, "lead_id": lead_id}


@pytest.fixture
def world(env):
    client, db = env
    org_a, org_b = _org(db, "Org A"), _org(db, "Org B")
    org_demo = _org(db, "Org Demo", status="demo", portal=False)
    ids = {
        "a_owner": _user(db, "owner@a.test", org_a, "owner"),
        "a_admin": _user(db, "admin@a.test", org_a, "admin"),
        "a_manager": _user(db, "manager@a.test", org_a, "manager"),
        "a_user": _user(db, "user@a.test", org_a, "member"),
        "a_viewer": _user(db, "viewer@a.test", org_a, "viewer"),
        "b_owner": _user(db, "owner@b.test", org_b, "owner"),
        "b_user": _user(db, "user@b.test", org_b, "member"),
        "d_owner": _user(db, "owner@demo.test", org_demo, "owner"),
    }
    emails = {"a_owner": "owner@a.test", "a_admin": "admin@a.test", "a_manager": "manager@a.test",
              "a_user": "user@a.test", "a_viewer": "viewer@a.test", "b_owner": "owner@b.test",
              "b_user": "user@b.test", "d_owner": "owner@demo.test"}
    orgs = {"a": org_a, "b": org_b, "demo": org_demo}
    data = {"a": _seed_data(db, org_a, ids["a_user"], "alpha"),
            "b": _seed_data(db, org_b, ids["b_user"], "bravo")}

    def cookie(key):
        org = {"a": org_a, "b": org_b, "d": org_demo}[key.split("_")[0]]
        role = db.organization_members.find_one({"user_id": ids[key]})["role"]
        return _cookie(ids[key], emails[key], org, role)
    return client, db, ids, orgs, data, cookie


LIST_ENDPOINTS = [
    "/api/org-admin/overview", "/api/org-admin/users", "/api/org-admin/invitations?status=all",
    "/api/org-admin/roles", "/api/org-admin/searches", "/api/org-admin/leads",
    "/api/org-admin/leads/pipeline", "/api/org-admin/lead-rules",
    "/api/org-admin/data/pages", "/api/org-admin/data/posts", "/api/org-admin/data/comments",
    "/api/org-admin/apify/summary", "/api/org-admin/apify/jobs", "/api/org-admin/apify/runs",
    "/api/org-admin/analytics", "/api/org-admin/billing/history", "/api/org-admin/exports",
    "/api/org-admin/audit-logs", "/api/org-admin/audit-logs/facets",
    "/api/org-admin/support/tickets", "/api/org-admin/profile", "/api/org-admin/context",
]


# ═══════════════════════════════════════════════════════════════════════════

class TestAccessControl:
    def test_admin_and_owner_can_use_every_endpoint(self, world):
        client, db, ids, orgs, data, cookie = world
        for who in ("a_admin", "a_owner"):
            c = cookie(who)
            for url in LIST_ENDPOINTS:
                r = client.get(url, cookies=c)
                assert r.status_code == 200, (who, url, r.text)

    def test_non_admins_get_403(self, world):
        client, db, ids, orgs, data, cookie = world
        for who in ("a_user", "a_viewer", "a_manager"):
            c = cookie(who)
            for url in LIST_ENDPOINTS:
                r = client.get(url, cookies=c)
                assert r.status_code == 403, (who, url, r.status_code)

    def test_anonymous_gets_401(self, world):
        client = world[0]
        assert client.get("/api/org-admin/overview").status_code in (401, 303, 307)

    def test_admin_portal_disabled_org_gets_403(self, world):
        client, db, ids, orgs, data, cookie = world
        c = cookie("d_owner")
        for url in LIST_ENDPOINTS:
            r = client.get(url, cookies=c)
            assert r.status_code == 403, url
            assert r.json()["detail"]["code"] == "admin_portal_disabled", url
        r = client.post("/api/org-admin/support/tickets", cookies=c,
                        json={"subject": "Hello", "message": "Please help me"})
        assert r.status_code == 403

    def test_portal_enabled_flag_unlocks_demo_org(self, world):
        client, db, ids, orgs, data, cookie = world
        db.organizations.update_one({"_id": ObjectId(orgs["demo"])},
                                    {"$set": {"admin_portal_enabled": True}})
        assert client.get("/api/org-admin/overview", cookies=cookie("d_owner")).status_code == 200

    def test_mutations_forbidden_for_members(self, world):
        client, db, ids, orgs, data, cookie = world
        c = cookie("a_user")
        assert client.put("/api/org-admin/lead-rules", json={"keywords": ["x"]}, cookies=c).status_code == 403
        assert client.post(f"/api/org-admin/users/{ids['a_viewer']}/reset-access", cookies=c).status_code == 403
        assert client.post("/api/org-admin/support/tickets", cookies=c, json={
            "subject": "Help", "message": "Something broke"}).status_code == 403
        assert client.get("/api/org-admin/exports/leads.csv", cookies=c).status_code == 403


class TestCrossTenantIsolation:
    def test_lists_never_contain_org_b(self, world):
        client, db, ids, orgs, data, cookie = world
        c = cookie("a_admin")
        users = client.get("/api/org-admin/users?limit=100", cookies=c).json()["items"]
        assert {u["user_id"] for u in users} >= {ids["a_user"], ids["a_owner"]}
        assert not {ids["b_owner"], ids["b_user"]} & {u["user_id"] for u in users}
        runs = client.get("/api/org-admin/searches", cookies=c).json()["items"]
        assert [r["run_id"] for r in runs] == [data["a"]["run_id"]]
        leads = client.get("/api/org-admin/leads", cookies=c).json()["items"]
        assert [lead["id"] for lead in leads] == [data["a"]["lead_id"]]
        for kind, key in (("pages", "page_id"), ("posts", "post_id"), ("comments", "comment_id")):
            items = client.get(f"/api/org-admin/data/{kind}", cookies=c).json()["items"]
            assert [i["id"] for i in items] == [data["a"][key]], kind
        jobs = client.get("/api/org-admin/apify/jobs", cookies=c).json()["items"]
        assert all(j["run_id"] != data["b"]["run_id"] for j in jobs)
        summary = client.get("/api/org-admin/apify/summary", cookies=c).json()
        assert summary["pages"] == 1 and summary["comments"] == 1
        overview = client.get("/api/org-admin/overview", cookies=c).json()
        assert overview["leads"]["total"] == 1 and overview["searches"]["total"] == 1
        assert overview["users"]["total"] == 5

    def test_org_b_ids_answer_404(self, world):
        client, db, ids, orgs, data, cookie = world
        c = cookie("a_admin")
        assert client.get(f"/api/org-admin/users/{ids['b_user']}", cookies=c).status_code == 404
        assert client.post(f"/api/org-admin/users/{ids['b_user']}/reset-access",
                           cookies=c).status_code == 404
        assert client.get(f"/api/org-admin/searches/{data['b']['run_id']}", cookies=c).status_code == 404
        # filtering by an id of org B yields nothing (never B's rows)
        r = client.get(f"/api/org-admin/data/comments?post_id={data['b']['post_id']}", cookies=c).json()
        assert r["total"] == 0
        r = client.get(f"/api/org-admin/leads?owner={ids['b_user']}", cookies=c).json()
        assert r["total"] == 0
        # probes are logged as security events
        assert db.security_events.count_documents({"type": "cross_tenant_access"}) >= 2

    def test_existing_lead_endpoints_stay_scoped_for_admin(self, world):
        client, db, ids, orgs, data, cookie = world
        c = cookie("a_admin")
        assert client.get(f"/api/leads/{data['b']['lead_id']}", cookies=c).status_code == 404
        r = client.patch(f"/api/leads/{data['b']['lead_id']}", cookies=c,
                         json={"assigned_user_id": ids["a_user"]})
        assert r.status_code == 404
        # assigning an org-B member to an org-A lead is rejected
        r = client.patch(f"/api/leads/{data['a']['lead_id']}", cookies=c,
                         json={"assigned_user_id": ids["b_user"]})
        assert r.status_code == 400
        r = client.patch(f"/api/leads/{data['a']['lead_id']}", cookies=c,
                         json={"assigned_user_id": ids["a_viewer"]})
        assert r.status_code == 200
        assigned = client.get(f"/api/org-admin/leads?assignee={ids['a_viewer']}", cookies=c).json()
        assert assigned["total"] == 1

    def test_audit_logs_are_own_org_only(self, world):
        client, db, ids, orgs, data, cookie = world
        db.audit_logs.insert_many([
            {"organization_id": orgs["a"], "action": "search.started", "category": "search",
             "actor_email": "user@a.test", "at": datetime.utcnow(), "status": "success"},
            {"organization_id": orgs["b"], "action": "search.started", "category": "search",
             "actor_email": "user@b.test", "at": datetime.utcnow(), "status": "success"},
            {"organization_id": None, "action": "platform.settings", "category": "system",
             "actor_email": "root@platform.test", "at": datetime.utcnow(), "status": "success"},
        ])
        items = client.get("/api/org-admin/audit-logs?limit=100", cookies=cookie("a_admin")).json()["items"]
        assert items and all(i["actor_email"] != "user@b.test" for i in items)
        assert all(i["action"] != "platform.settings" for i in items)
        filtered = client.get("/api/org-admin/audit-logs?action=search", cookies=cookie("a_admin")).json()
        assert filtered["total"] == 1

    def test_invitations_are_scoped(self, world):
        client, db, ids, orgs, data, cookie = world
        future = datetime.utcnow() + timedelta(days=3)
        db.organization_invitations.insert_many([
            {"organization_id": orgs["a"], "email": "new@a.test", "role": "member",
             "status": "pending", "expires_at": future, "created_at": datetime.utcnow(),
             "token_hash": "tha"},
            {"organization_id": orgs["b"], "email": "new@b.test", "role": "member",
             "status": "pending", "expires_at": future, "created_at": datetime.utcnow(),
             "token_hash": "thb"},
        ])
        items = client.get("/api/org-admin/invitations", cookies=cookie("a_admin")).json()["items"]
        assert [i["email"] for i in items] == ["new@a.test"]


class TestTeamManagement:
    def test_reset_access_sends_link_never_password(self, world):
        client, db, ids, orgs, data, cookie = world
        r = client.post(f"/api/org-admin/users/{ids['a_user']}/reset-access", cookies=cookie("a_admin"))
        assert r.status_code == 200, r.text
        mail = db.email_outbox.find_one({"to": "user@a.test", "kind": "password_reset"})
        assert mail and "/reset-password?token=" in mail["body"] and PASSWORD not in mail["body"]
        assert db.password_resets.count_documents({"user_id": ids["a_user"], "used_at": None}) == 1
        assert db.audit_logs.find_one({"action": "member.access_reset", "organization_id": orgs["a"]})

    def test_admin_cannot_reset_owner_or_other_admin(self, world):
        client, db, ids, orgs, data, cookie = world
        admin2 = _user(db, "admin2@a.test", orgs["a"], "admin")
        c = cookie("a_admin")
        assert client.post(f"/api/org-admin/users/{ids['a_owner']}/reset-access", cookies=c).status_code == 403
        assert client.post(f"/api/org-admin/users/{admin2}/reset-access", cookies=c).status_code == 403
        # the owner may
        assert client.post(f"/api/org-admin/users/{admin2}/reset-access",
                           cookies=cookie("a_owner")).status_code == 200

    def test_deactivate_and_restore_member(self, world):
        client, db, ids, orgs, data, cookie = world
        c = cookie("a_admin")
        r = client.patch(f"/api/organizations/current/members/{ids['a_viewer']}", cookies=c,
                         json={"status": "inactive"})
        assert r.status_code == 200
        assert db.organization_members.find_one({"user_id": ids["a_viewer"]})["status"] == "inactive"
        # inactive members lose access immediately
        assert client.get("/api/org-admin/overview", cookies=cookie("a_viewer")).status_code == 403
        with patch("app.billing.entitlements.EntitlementService.check_limit",
                   new=lambda *a, **k: _async((True, 1, 10))):
            r = client.patch(f"/api/organizations/current/members/{ids['a_viewer']}", cookies=c,
                             json={"status": "active"})
        assert r.status_code == 200

    def test_user_detail_and_roles_catalog(self, world):
        client, db, ids, orgs, data, cookie = world
        c = cookie("a_admin")
        d = client.get(f"/api/org-admin/users/{ids['a_user']}", cookies=c).json()
        assert d["usage"]["searches"] == 1 and d["usage"]["leads"] == 1
        roles = client.get("/api/org-admin/roles", cookies=c).json()
        from app.auth.permissions import DELEGABLE_ORG_PERMISSIONS
        keys = {p["key"] for g in roles["catalog"] for p in g["permissions"]}
        assert keys <= set(DELEGABLE_ORG_PERMISSIONS)
        assert not any(k.startswith(("platform.", "organizations.", "users.", "billing.", "system."))
                       for k in keys)
        assert "roles.manage" not in keys
        assert {r["role"] for r in roles["roles"]} == {"manager", "member", "viewer"}

    def test_role_permissions_reject_platform_perms(self, world):
        client, db, ids, orgs, data, cookie = world
        r = client.patch("/api/organizations/current", cookies=cookie("a_admin"),
                         json={"role_permissions": {"member": {"platform.manage": True}}})
        assert r.status_code == 422
        r = client.patch("/api/organizations/current", cookies=cookie("a_admin"),
                         json={"role_permissions": {"admin": {"leads.view": True}}})
        assert r.status_code == 422

    def test_settings_validation(self, world):
        client, db, ids, orgs, data, cookie = world
        c = cookie("a_admin")
        assert client.patch("/api/organizations/current", cookies=c,
                            json={"settings": {"usage_warning_percent": 150}}).status_code == 422
        assert client.patch("/api/organizations/current", cookies=c,
                            json={"logo_url": "javascript:alert(1)"}).status_code == 422
        assert client.patch("/api/organizations/current", cookies=c,
                            json={"settings": {"default_posts_per_search": 99999999}}).status_code == 422
        r = client.patch("/api/organizations/current", cookies=c,
                         json={"settings": {"shared_workspace": True, "usage_warning_percent": 75,
                                            "email_notifications": False}})
        assert r.status_code == 200
        s = db.organizations.find_one({"_id": ObjectId(orgs["a"])})["settings"]
        assert s["shared_workspace"] is True and s["usage_warning_percent"] == 75


class TestExports:
    def _rows(self, r):
        text = r.content.decode("utf-8-sig")
        return list(csv.DictReader(io.StringIO(text)))

    def test_leads_export_own_org_only_and_sanitised(self, world):
        client, db, ids, orgs, data, cookie = world
        r = client.get("/api/org-admin/exports/leads.csv", cookies=cookie("a_admin"))
        assert r.status_code == 200, r.text
        rows = self._rows(r)
        assert len(rows) == 1
        assert "bravo" not in r.text
        assert rows[0]["name"].startswith("'=")
        assert rows[0]["text"].startswith("'@")
        assert db.exports.find_one({"scope": "leads", "organization_id": orgs["a"], "rows": 1})
        assert db.audit_logs.find_one({"action": "export.csv", "organization_id": orgs["a"],
                                       "resource_type": "leads"})

    def test_every_export_is_org_scoped(self, world):
        client, db, ids, orgs, data, cookie = world
        db.audit_logs.insert_one({"organization_id": orgs["b"], "action": "secret.bravo",
                                  "actor_email": "user@b.test", "at": datetime.utcnow()})
        for kind in ("users", "activity", "usage", "searches", "posts", "comments"):
            r = client.get(f"/api/org-admin/exports/{kind}.csv", cookies=cookie("a_admin"))
            assert r.status_code == 200, (kind, r.text)
            assert "bravo" not in r.text and "@b.test" not in r.text, kind
            if kind == "posts":
                assert "'=cmd" in r.text
        hist = client.get("/api/org-admin/exports", cookies=cookie("a_admin")).json()
        assert hist["total"] >= 1 and all(i["scope"] for i in hist["items"])
        hist_b = client.get("/api/org-admin/exports", cookies=cookie("b_owner")).json()
        assert hist_b["total"] == 0

    def test_audit_csv_export(self, world):
        client, db, ids, orgs, data, cookie = world
        db.audit_logs.insert_one({"organization_id": orgs["a"], "action": "=danger()",
                                  "actor_email": "admin@a.test", "at": datetime.utcnow()})
        r = client.get("/api/org-admin/audit-logs.csv", cookies=cookie("a_admin"))
        assert r.status_code == 200 and "'=danger()" in r.text

    def test_unknown_export(self, world):
        client, db, ids, orgs, data, cookie = world
        assert client.get("/api/org-admin/exports/secrets.csv", cookies=cookie("a_admin")).status_code == 404


class TestSupportTickets:
    def test_tickets_are_org_scoped(self, world):
        client, db, ids, orgs, data, cookie = world
        r = client.post("/api/org-admin/support/tickets", cookies=cookie("a_admin"),
                        json={"subject": "Export broken", "message": "CSV is empty",
                              "category": "bug", "priority": "high"})
        assert r.status_code == 200, r.text
        tid = r.json()["ticket"]["id"]
        assert db.notifications.find_one({"audience": "super_admin", "type": "support_ticket"})
        assert db.audit_logs.find_one({"action": "support.ticket_created", "organization_id": orgs["a"]})
        # org B cannot see / reply / close it
        cb = cookie("b_owner")
        assert client.get("/api/org-admin/support/tickets", cookies=cb).json()["total"] == 0
        assert client.get(f"/api/org-admin/support/tickets/{tid}", cookies=cb).status_code == 404
        assert client.post(f"/api/org-admin/support/tickets/{tid}/messages", cookies=cb,
                           json={"message": "hi"}).status_code == 404
        assert client.post(f"/api/org-admin/support/tickets/{tid}/status", cookies=cb,
                           json={"status": "closed"}).status_code == 404
        # org A can reply and close
        ca = cookie("a_owner")
        assert client.post(f"/api/org-admin/support/tickets/{tid}/messages", cookies=ca,
                           json={"message": "More details"}).json()["ticket"]["messages_count"] == 2
        assert client.post(f"/api/org-admin/support/tickets/{tid}/status", cookies=ca,
                           json={"status": "closed"}).json()["ticket"]["status"] == "closed"

    def test_ticket_validation(self, world):
        client, db, ids, orgs, data, cookie = world
        r = client.post("/api/org-admin/support/tickets", cookies=cookie("a_admin"),
                        json={"subject": "x", "message": "short"})
        assert r.status_code == 422


class TestLeadRules:
    def test_org_keywords_saved_and_used(self, world):
        client, db, ids, orgs, data, cookie = world
        c = cookie("a_admin")
        r = client.put("/api/org-admin/lead-rules", cookies=c,
                       json={"keywords": ["Property", "buy", "rent", "buy"], "exclude_keywords": ["spam"]})
        assert r.status_code == 200
        assert r.json()["keywords"] == ["property", "buy", "rent"]
        from app.pipeline import org_lead_rules as R
        assert R.org_keywords(orgs["a"]) == ["property", "buy", "rent"]
        assert R.org_keywords(orgs["b"]) is None
        rule = R.org_rule(orgs["a"])
        assert rule["include_keywords"] == ["property", "buy", "rent"]
        assert R.org_rule(orgs["b"]) is None
        assert R.matches("I want to buy a flat", ["buy"]) is True
        assert R.matches("career advice", ["car"]) is False
        assert R.matches("buy now spam", ["buy"], ["spam"]) is False
        t = client.post("/api/org-admin/lead-rules/test", cookies=c, json={"text": "Want to rent"}).json()
        assert t["source"] == "organization" and t["matched"] is True
        assert db.audit_logs.find_one({"action": "lead_rules.updated", "organization_id": orgs["a"]})
        # org B unaffected
        assert client.get("/api/org-admin/lead-rules", cookies=cookie("b_owner")).json()["using_defaults"]
        # pipeline: run's org keywords apply; inline run config still wins; other org falls back
        from app.pipeline.comment_filter import resolve_effective_rule
        from app.db.mongo import get_sync_db
        sdb = get_sync_db()
        eff = resolve_effective_rule(sdb, {"organization_id": orgs["a"]})
        assert eff and eff["include_keywords"] == ["property", "buy", "rent"]
        inline = resolve_effective_rule(sdb, {"organization_id": orgs["a"],
                                              "comment_filter": {"include_keywords": ["hire"]}})
        assert inline and inline.get("_id") != f"org:{orgs['a']}"
        other = resolve_effective_rule(sdb, {"organization_id": orgs["b"]})
        assert not other or other.get("_id") != f"org:{orgs['b']}"


class TestAnalyticsAndProfile:
    def test_analytics_range_validation(self, world):
        client, db, ids, orgs, data, cookie = world
        c = cookie("a_admin")
        r = client.get("/api/org-admin/analytics?from=2020-01-01&to=2023-01-01", cookies=c)
        assert r.status_code == 422
        r = client.get("/api/org-admin/analytics", cookies=c).json()
        assert r["totals"]["searches"] == 1 and r["totals"]["leads"] == 1

    def test_profile_update(self, world):
        client, db, ids, orgs, data, cookie = world
        c = cookie("a_admin")
        r = client.patch("/api/org-admin/profile", cookies=c,
                         json={"name": "Ada Admin", "notification_preferences": {"weekly_summary": False}})
        assert r.status_code == 200
        p = client.get("/api/org-admin/profile", cookies=c).json()["profile"]
        assert p["name"] == "Ada Admin" and p["notification_preferences"]["weekly_summary"] is False
        assert client.patch("/api/org-admin/profile", cookies=c,
                            json={"notification_preferences": {"is_admin": True}}).status_code == 422


def _async(value):
    async def _f():
        return value
    return _f()
