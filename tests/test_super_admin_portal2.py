"""
Super Admin control center (Step 2.1) — runs on the in-memory MongoDB from
tests/conftest.py, never a real database.

Proves:
  * every new /api/super-admin endpoint rejects anonymous callers, org
    owners/admins/users (401 — they have no admin-scope session) and
    non-super platform staff (403);
  * CSV reports are redacted (no password hashes / tokens / API keys,
    masked lead phone + email) and every export is audited;
  * dashboard + analytics numbers match seeded data;
  * feature-flag writes are validated and audited (before/after);
  * session revoke works (the revoked cookie stops working);
  * reset-access emails a one-time link (never a password) and the outbox
    viewer never shows the link token;
  * org archive is a soft delete with a mandatory reason + session revoke;
  * plan changes never activate a pending subscription.
"""
import csv
import io
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.auth.crypto import hash_password
from app.auth.service import COOKIE_NAME, build_session_value, create_tracked_session

NOW = datetime.now(timezone.utc)


@pytest.fixture
def env():
    from app.billing import provider as provider_mod
    provider_mod.reset_billing_provider()
    with patch("app.admin.settings.is_maintenance_enabled", return_value=False):
        from app.main import app
        with TestClient(app) as client:
            from app.db.mongo import get_sync_db
            yield client, get_sync_db()
    provider_mod.reset_billing_provider()


def _org(db, name, status="active", **extra):
    return str(db.organizations.insert_one({
        "name": name, "slug": name.lower().replace(" ", "-") + str(ObjectId())[-4:],
        "status": status, "settings": {}, "created_at": NOW, **extra}).inserted_id)


def _user(db, email, org_id=None, role="member", status="active", platform_role=None, **extra):
    uid = str(db.users.insert_one({
        "email": email, "name": email.split("@")[0], "password_hash": hash_password("Str0ngPass!"),
        "status": status, "is_platform_admin": bool(platform_role), "platform_role": platform_role,
        "default_organization_id": org_id, "created_at": NOW, **extra}).inserted_id)
    if org_id:
        db.organization_members.insert_one({
            "organization_id": org_id, "user_id": uid, "role": role, "status": "active",
            "joined_at": NOW, "permissions_override": {}})
    return uid


def _cookie(claims):
    return {COOKIE_NAME: build_session_value(create_tracked_session(claims))}


def _super(db):
    uid = _user(db, "root@platform.test", platform_role="super_admin")
    return _cookie({"user_id": uid, "email": "root@platform.test", "name": "Root",
                    "scope": "admin", "role": "super_admin"})


def _staff(db, role="viewer"):
    uid = _user(db, f"{role}@platform.test", platform_role=role)
    return _cookie({"user_id": uid, "email": f"{role}@platform.test", "name": role,
                    "scope": "admin", "role": role})


def _site(db, uid, email, org_id, role):
    return _cookie({"user_id": uid, "email": email, "name": email, "scope": "site",
                    "organization_id": org_id, "org_role": role})


@pytest.fixture
def world(env):
    client, db = env
    # start from a clean tenant slate (startup migration seeds a default org)
    for coll in ("organizations", "users", "organization_members", "subscriptions", "payments",
                 "user_sessions", "audit_logs"):
        db[coll].delete_many({})
    org_a = _org(db, "Acme")
    owner = _user(db, "owner@acme.test", org_a, "owner")
    member = _user(db, "user@acme.test", org_a, "member")
    cookies = {
        "super": _super(db),
        "viewer_staff": _staff(db, "viewer"),
        "ops_staff": _staff(db, "operations_admin"),
        "owner": _site(db, owner, "owner@acme.test", org_a, "owner"),
        "member": _site(db, member, "user@acme.test", org_a, "member"),
    }
    return client, db, {"org_a": org_a, "owner": owner, "member": member}, cookies


# ═══════════════════════════════════════════════════════════════════════════
# 1. Authorization matrix
# ═══════════════════════════════════════════════════════════════════════════

GET_ENDPOINTS = [
    "/api/super-admin/dashboard",
    "/api/super-admin/search?q=ac",
    "/api/super-admin/admins",
    "/api/super-admin/tokens/summary",
    "/api/super-admin/tokens/x/users",
    "/api/super-admin/leadai/summary",
    "/api/super-admin/searches",
    "/api/super-admin/searches/run-x/chain",
    "/api/super-admin/leads",
    "/api/super-admin/analytics",
    "/api/super-admin/security/overview",
    "/api/super-admin/security/logins",
    "/api/super-admin/security/lockouts",
    "/api/super-admin/sessions",
    "/api/super-admin/feature-flags",
    "/api/super-admin/integrations",
    "/api/super-admin/health",
    "/api/super-admin/ai/overview",
    "/api/super-admin/ai/prompts",
    "/api/super-admin/ai/models",
    "/api/super-admin/reports",
    "/api/super-admin/reports/users.csv",
    "/api/super-admin/subscriptions/x/detail",
    "/api/super-admin/audit-logs",
    "/api/super-admin/email-outbox",
    "/api/super-admin/support/tickets",
    "/api/super-admin/support/tickets/x",
]
POST_ENDPOINTS = [
    ("post", "/api/super-admin/users/x/reset-access", {}),
    ("post", "/api/super-admin/users/x/revoke-sessions", {}),
    ("patch", "/api/super-admin/organizations/x", {"name": "x"}),
    ("post", "/api/super-admin/subscriptions/x/change-plan", {"plan": "pro"}),
    ("post", "/api/super-admin/subscriptions/x/extend", {"days": 3, "reason": "x"}),
    ("post", "/api/super-admin/tokens/x/expiry", {"reason": "x"}),
    ("post", "/api/super-admin/security/lockouts/clear", {"email": "a@b.c"}),
    ("post", "/api/super-admin/sessions/x/revoke", {}),
    ("put", "/api/super-admin/feature-flags", {"changes": {"maintenance.enabled": True}}),
    ("post", "/api/super-admin/ai/prompts", {}),
    ("post", "/api/super-admin/ai/prompts/x/activate", {}),
    ("post", "/api/super-admin/ai/prompts/x/rollback", {}),
    ("patch", "/api/super-admin/ai/models/x", {"is_enabled": False}),
    ("post", "/api/super-admin/support/tickets/x/messages", {"message": "hi"}),
    ("post", "/api/super-admin/support/tickets/x/status", {"status": "closed"}),
]


class TestAuthorization:
    def test_reads_reject_non_super(self, world):
        client, db, ids, c = world
        for path in GET_ENDPOINTS:
            client.cookies.clear()
            assert client.get(path).status_code == 401, path
            for who in ("owner", "member"):
                client.cookies.clear()
                assert client.get(path, cookies=c[who]).status_code == 401, (path, who)
            for who in ("viewer_staff", "ops_staff"):
                client.cookies.clear()
                assert client.get(path, cookies=c[who]).status_code == 403, (path, who)

    def test_writes_reject_non_super(self, world):
        client, db, ids, c = world
        for method, path, body in POST_ENDPOINTS:
            call = getattr(client, method)
            client.cookies.clear()
            assert call(path, json=body).status_code == 401, path
            for who in ("owner", "member"):
                client.cookies.clear()
                assert call(path, json=body, cookies=c[who]).status_code == 401, (path, who)
            for who in ("viewer_staff", "ops_staff"):
                client.cookies.clear()
                assert call(path, json=body, cookies=c[who]).status_code == 403, (path, who)
        # nothing was written by the rejected callers
        assert db.audit_logs.count_documents({"action": {"$regex": (
            r"^(feature_flag|session|ai\.|user\.|subscription\.|tokens\.|organization\.)")}}) == 0

    def test_super_admin_reaches_every_read(self, world):
        client, db, ids, c = world
        for path in GET_ENDPOINTS:
            r = client.get(path, cookies=c["super"])
            assert r.status_code in (200, 404), (path, r.status_code, r.text[:200])

    def test_dependency_guard_also_rejects_staff_without_gate(self, world):
        """Defence in depth: the route dependency itself rejects non-super
        platform staff even if the auth gate were bypassed."""
        from app.auth.tenant import TenantContext, require_platform_role
        from fastapi import HTTPException
        from starlette.requests import Request
        dep = require_platform_role("super_admin")
        req = Request({"type": "http", "method": "GET", "path": "/", "headers": [],
                       "query_string": b"", "client": ("1.1.1.1", 1)})
        ctx = TenantContext(user_id="u", email="ops@x.test", platform_role="operations_admin",
                            permissions=[])
        with pytest.raises(HTTPException) as e:
            dep(req, ctx)
        assert e.value.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════
# 2. Dashboard & analytics numbers match seeded data
# ═══════════════════════════════════════════════════════════════════════════

def _seed_business(db, org_a):
    org_s = _org(db, "Suspended Co", "suspended")
    org_d = _org(db, "Demo Co", "demo")
    _org(db, "Gone Co", "archived")
    _user(db, "admin@demo.test", org_d, "admin")
    _user(db, "sus@acme.test", org_a, "member", status="suspended")
    db.subscriptions.insert_many([
        {"organization_id": org_a, "plan_id": "pro", "status": "active", "amount": 100.0,
         "billing_cycle": "monthly", "currency": "USD", "created_at": NOW,
         "status_history": [{"from": "pending_admin_confirmation", "to": "active", "at": NOW}]},
        {"organization_id": org_s, "plan_id": "pro", "status": "active", "amount": 1200.0,
         "billing_cycle": "yearly", "currency": "USD", "created_at": NOW},
        {"organization_id": org_d, "plan_id": "starter", "status": "pending_admin_confirmation",
         "amount": 50.0, "currency": "USD", "created_at": NOW},
        {"organization_id": org_d, "plan_id": "starter", "status": "cancelled", "amount": 50.0,
         "created_at": NOW},
    ])
    db.payments.insert_many([
        {"organization_id": org_a, "amount": 100.0, "status": "succeeded", "currency": "USD", "created_at": NOW},
        {"organization_id": org_s, "amount": 1200.0, "status": "succeeded", "currency": "USD", "created_at": NOW,
         "refund_required": True},
        {"organization_id": org_d, "amount": 50.0, "status": "failed", "currency": "USD", "created_at": NOW},
    ])
    y = NOW - timedelta(days=1)
    db.search_history.insert_many([
        {"run_id": "r1", "organization_id": org_a, "user_id": "u1", "status": "completed", "provider": "apify",
         "created_at": NOW, "query": "https://fb.com/acme"},
        {"run_id": "r2", "organization_id": org_a, "user_id": "u2", "status": "error", "provider": "apify",
         "created_at": NOW, "error": "Actor failed"},
        {"run_id": "r3", "organization_id": org_d, "user_id": "u1", "status": "running", "provider": "apify",
         "created_at": y},
    ])
    db.ai_comments.insert_many([
        {"organization_id": org_a, "is_lead": True, "lead_quality": "hot", "created_at": NOW,
         "phone": "+91 98765 43210", "email": "buyer@gmail.com", "commenter_name": "Ravi",
         "search_run_id": "r1", "comment_text": "=cmd|' /C calc'!A0 interested", "comment_ref": "c1"},
        {"organization_id": org_a, "is_lead": True, "lead_quality": "warm", "created_at": NOW,
         "comment_ref": "c2"},
        {"organization_id": org_a, "is_lead": False, "created_at": NOW, "comment_ref": "c3"},
    ])
    db.token_balances.insert_many([
        {"organization_id": org_a, "allocated": 1000, "used": 900, "remaining": 100},
        {"organization_id": org_d, "allocated": 500, "used": 10, "remaining": 490},
    ])
    db.token_ledger.insert_many([
        {"organization_id": org_a, "user_id": "u1", "type": "consume", "amount": 60, "created_at": NOW},
        {"organization_id": org_a, "user_id": "u2", "type": "consume", "amount": 15, "created_at": NOW},
        {"organization_id": org_a, "type": "allocate", "amount": 1000, "created_at": NOW},
    ])
    db.demo_requests.insert_many([
        {"organization_id": org_d, "company": "Demo Co", "email": "admin@demo.test", "status": "pending",
         "created_at": NOW, "history": [{"status": "pending", "at": NOW}]},
        {"organization_id": org_a, "company": "Acme", "email": "owner@acme.test", "status": "converted",
         "created_at": NOW, "history": [{"status": "pending", "at": NOW}, {"status": "converted", "at": NOW}]},
    ])
    return org_s, org_d


class TestDashboardAndAnalytics:
    def test_dashboard_numbers(self, world):
        client, db, ids, c = world
        org_s, org_d = _seed_business(db, ids["org_a"])
        d = client.get("/api/super-admin/dashboard", cookies=c["super"]).json()["dashboard"]
        assert d["organizations"]["total"] == 4
        assert d["organizations"]["active"] == 1
        assert d["organizations"]["suspended"] == 1
        assert d["organizations"]["demo"] == 1
        assert d["organizations"]["archived"] == 1
        assert d["admins"]["total"] == 2          # acme owner + demo admin
        assert d["users"]["suspended"] == 1
        assert d["demo"]["pending_requests"] == 1 and d["demo"]["converted"] == 1
        assert d["subscriptions"]["active"] == 2
        assert d["subscriptions"]["awaiting_confirmation"] == 1
        assert d["subscriptions"]["cancelled"] == 1
        assert d["subscriptions"]["mrr"] == 200.0  # 100 monthly + 1200/12
        assert d["payments"]["succeeded_amount"] == 1300.0
        assert d["payments"]["failed"] == 1 and d["payments"]["refund_required"] == 1
        assert d["tokens"]["allocated"] == 1500 and d["tokens"]["used"] == 910
        assert d["tokens"]["consumed_24h"] == 75
        assert d["searches"] == {"total": 3, "running": 1, "completed": 1, "failed": 1, "last_30d": 3}
        assert d["apify"]["total"] == 3 and d["apify"]["failed"] == 1
        assert d["leads"]["total"] == 2 and d["leads"]["hot"] == 1
        assert len(d["recent"]["subscription_requests"]) == 1

    def test_analytics_series_match(self, world):
        client, db, ids, c = world
        _seed_business(db, ids["org_a"])
        r = client.get("/api/super-admin/analytics?range=7d", cookies=c["super"])
        assert r.status_code == 200
        a = r.json()
        assert len(a["days"]) == 7
        p, b = a["product"], a["business"]
        assert p["searches"]["total"] == 3
        assert p["failed_searches"]["total"] == 1
        assert p["active_users"]["total"] == 2       # u1, u2
        assert p["leads"]["total"] == 2
        assert p["tokens_consumed"]["total"] == 75
        assert b["revenue"]["total"] == 1300.0
        assert b["demo_conversions"]["total"] == 1
        assert b["subscriptions_activated"]["total"] == 1
        assert a["snapshot"]["plan_distribution"] == {"pro": 2}
        assert client.get("/api/super-admin/analytics?range=bogus", cookies=c["super"]).status_code == 422

    def test_tokens_summary_warnings_and_anomalies(self, world):
        client, db, ids, c = world
        _seed_business(db, ids["org_a"])
        s = client.get("/api/super-admin/tokens/summary", cookies=c["super"]).json()
        assert [w["organization_id"] for w in s["warnings"]] == [ids["org_a"]]
        assert s["anomalies"] and s["anomalies"][0]["organization_id"] == ids["org_a"]
        users = client.get(f"/api/super-admin/tokens/{ids['org_a']}/users", cookies=c["super"]).json()
        assert [u["tokens"] for u in users["items"]] == [60, 15]

    def test_search_chain_drilldown(self, world):
        client, db, ids, c = world
        _seed_business(db, ids["org_a"])
        page = db.facebook_pages.insert_one({"search_run_id": "r1", "organization_id": ids["org_a"]}).inserted_id
        post = db.facebook_posts.insert_one({"page_ref": str(page)}).inserted_id
        db.facebook_comments.insert_one({"post_ref": str(post)})
        ch = client.get("/api/super-admin/searches/r1/chain", cookies=c["super"]).json()["chain"]
        assert ch["organization"]["name"] == "Acme"
        assert ch["admins"][0]["email"] == "owner@acme.test"
        assert ch["counts"]["pages"] == 1 and ch["counts"]["posts"] == 1 and ch["counts"]["comments"] == 1
        assert ch["counts"]["leads"] == 1
        failed = client.get("/api/super-admin/searches/r2/chain", cookies=c["super"]).json()["chain"]
        assert failed["error"] == "Actor failed"
        assert client.get("/api/super-admin/searches/nope/chain", cookies=c["super"]).status_code == 404
        lst = client.get("/api/super-admin/searches?status=failed", cookies=c["super"]).json()
        assert [i["run_id"] for i in lst["items"]] == ["r2"]


# ═══════════════════════════════════════════════════════════════════════════
# 3. Exports are redacted and audited
# ═══════════════════════════════════════════════════════════════════════════

def _rows(text):
    return list(csv.DictReader(io.StringIO(text)))


class TestExports:
    def test_users_export_has_no_secrets(self, world):
        client, db, ids, c = world
        r = client.get("/api/super-admin/reports/users.csv", cookies=c["super"])
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
        assert "password" not in r.text.lower() and "$2b$" not in r.text
        emails = {row["email"] for row in _rows(r.text)}
        assert "owner@acme.test" in emails

    def test_leads_export_masks_contacts_and_neutralises_formulas(self, world):
        client, db, ids, c = world
        _seed_business(db, ids["org_a"])
        text = client.get("/api/super-admin/reports/leads.csv", cookies=c["super"]).text
        assert "98765 43210" not in text and "9876543210" not in text
        assert "buyer@gmail.com" not in text
        row = next(r for r in _rows(text) if r["commenter_name"] == "Ravi")
        assert row["phone_masked"].endswith("3210") and "•" in row["phone_masked"]
        assert row["email_masked"].startswith("bu") and row["email_masked"].endswith("@gmail.com")
        assert row["comment_excerpt"].startswith("'=")   # CSV-injection guard

    def test_audit_export_redacts_secret_details(self, world):
        client, db, ids, c = world
        db.audit_logs.insert_one({"action": "x.test", "category": "test", "at": NOW,
                                  "details": {"api_key": "sk-live-123", "token": "abc", "note": "ok"}})
        text = client.get("/api/super-admin/reports/audit-logs.csv", cookies=c["super"]).text
        assert "sk-live-123" not in text and "abc\"" not in text
        assert "ok" in text

    def test_every_report_kind_exports_and_is_audited(self, world):
        client, db, ids, c = world
        _seed_business(db, ids["org_a"])
        kinds = [k["key"] for k in client.get("/api/super-admin/reports", cookies=c["super"]).json()["kinds"]]
        for kind in kinds:
            r = client.get(f"/api/super-admin/reports/{kind}.csv", cookies=c["super"])
            assert r.status_code == 200, kind
            assert "password_hash" not in r.text and "token_hash" not in r.text
            assert db.audit_logs.find_one({"action": f"report.exported.{kind}",
                                           "actor_email": "root@platform.test"}), kind
        assert client.get("/api/super-admin/reports/secrets.csv", cookies=c["super"]).status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# 4. Feature flags are validated and audited
# ═══════════════════════════════════════════════════════════════════════════

class TestFeatureFlags:
    def test_flag_write_is_audited(self, world):
        client, db, ids, c = world
        r = client.put("/api/super-admin/feature-flags", cookies=c["super"],
                       json={"changes": {"features.exports.enabled": False,
                                         "maintenance.message": "Back soon"}, "reason": "incident"})
        assert r.status_code == 200, r.text
        assert set(r.json()["changed"]) == {"features.exports.enabled", "maintenance.message"}
        from app.admin.settings import get_setting
        assert get_setting("features.exports.enabled") is False
        row = db.audit_logs.find_one({"action": "feature_flag.updated",
                                      "resource_id": "features.exports.enabled"})
        assert row and row["details"]["before"] is True and row["details"]["after"] is False
        assert row["actor_email"] == "root@platform.test"

    def test_flag_validation(self, world):
        client, db, ids, c = world
        bad = [{"changes": {"apify.token": "x"}}, {"changes": {"features.exports.enabled": "yes"}},
               {"changes": {}}, {"changes": {"maintenance.message": "x" * 600}}]
        for body in bad:
            assert client.put("/api/super-admin/feature-flags", cookies=c["super"], json=body).status_code == 422
        assert db.audit_logs.count_documents({"action": "feature_flag.updated"}) == 0

    def test_integrations_expose_booleans_only(self, world):
        client, db, ids, c = world
        with patch.dict("os.environ", {"STRIPE_WEBHOOK_SECRET": "whsec_supersecret",
                                       "BILLING_WEBHOOK_SECRET": "whsec_other"}):
            r = client.get("/api/super-admin/integrations", cookies=c["super"])
        assert r.status_code == 200
        assert "whsec_" not in r.text
        pay = r.json()["integrations"]["payments"]
        assert isinstance(pay["billing_webhook_secret_configured"], bool)


# ═══════════════════════════════════════════════════════════════════════════
# 5. Sessions, reset access, lifecycle guards
# ═══════════════════════════════════════════════════════════════════════════

class TestSessionsAndAccess:
    def test_session_revoke_works(self, world):
        client, db, ids, c = world
        member_cookie = c["member"]
        assert client.get("/api/organizations/current", cookies=member_cookie).status_code == 200
        lst = client.get("/api/super-admin/sessions?q=user@acme.test", cookies=c["super"])
        assert lst.status_code == 200
        items = lst.json()["items"]
        assert items and "session_id" not in items[0]
        assert all("session_id" not in str(i.keys()) for i in items)
        r = client.post(f"/api/super-admin/sessions/{items[0]['ref']}/revoke", cookies=c["super"],
                        json={"reason": "test"})
        assert r.status_code == 200 and r.json()["revoked"] is True
        client.cookies.clear()
        assert client.get("/api/organizations/current", cookies=member_cookie).status_code == 401
        assert db.audit_logs.find_one({"action": "session.revoked"})

    def test_reset_access_emails_link_never_password(self, world):
        client, db, ids, c = world
        r = client.post(f"/api/super-admin/users/{ids['member']}/reset-access", cookies=c["super"],
                        json={"reason": "user locked out"})
        assert r.status_code == 200, r.text
        assert "password" not in {k.lower() for k in r.json()}
        reset = db.password_resets.find_one({"user_id": ids["member"], "used_at": None})
        assert reset and reset["purpose"] == "admin_reset" and "token_hash" in reset
        mail = db.email_outbox.find_one({"to": "user@acme.test", "kind": "password_reset"})
        assert mail and "reset-password?token=" in mail["body"]
        out = client.get("/api/super-admin/email-outbox?q=user@acme.test", cookies=c["super"]).json()
        body = out["items"][0]["body"]
        assert "token=••••" in body
        raw_token = mail["body"].split("token=")[1].split()[0]
        assert raw_token not in body
        assert db.audit_logs.find_one({"action": "user.access_reset", "resource_id": ids["member"]})

    def test_org_archive_is_soft_and_needs_reason(self, world):
        client, db, ids, c = world
        url = f"/api/super-admin/organizations/{ids['org_a']}/status"
        assert client.patch(url, cookies=c["super"], json={"status": "archived"}).status_code == 422
        r = client.patch(url, cookies=c["super"], json={"status": "archived", "reason": "customer request"})
        assert r.status_code == 200
        org = db.organizations.find_one({"_id": ObjectId(ids["org_a"])})
        assert org["status"] == "archived" and org["archive_reason"] == "customer request"
        assert db.users.count_documents({"email": "owner@acme.test"}) == 1   # data kept
        client.cookies.clear()
        assert client.get("/api/organizations/current", cookies=c["owner"]).status_code in (401, 403)
        assert db.audit_logs.find_one({"action": "organization.archived"})["details"]["reason"] == "customer request"
        lst = client.get("/api/super-admin/organizations", cookies=c["super"]).json()
        assert ids["org_a"] not in [o["id"] for o in lst["organizations"]]

    def test_super_admin_account_status_is_protected(self, world):
        client, db, ids, c = world
        other = _user(db, "root2@platform.test", platform_role="super_admin")
        r = client.patch(f"/api/super-admin/users/{other}/status", cookies=c["super"],
                         json={"status": "suspended"})
        assert r.status_code == 403
        r = client.patch(f"/api/super-admin/users/{ids['member']}/status", cookies=c["super"],
                         json={"status": "disabled", "reason": "abuse"})
        assert r.status_code == 200
        assert db.users.find_one({"_id": ObjectId(ids["member"])})["status"] == "disabled"

    def test_change_plan_never_activates_pending(self, world):
        client, db, ids, c = world
        sub = db.subscriptions.insert_one({"organization_id": ids["org_a"], "plan_id": "starter",
                                           "status": "pending_admin_confirmation", "created_at": NOW}).inserted_id
        r = client.post(f"/api/super-admin/subscriptions/{sub}/change-plan", cookies=c["super"],
                        json={"plan": "pro"})
        assert r.status_code == 409
        assert db.subscriptions.find_one({"_id": sub})["status"] == "pending_admin_confirmation"

    def test_extend_subscription_audited_without_status_change(self, world):
        client, db, ids, c = world
        end = NOW + timedelta(days=5)
        sub = db.subscriptions.insert_one({"organization_id": ids["org_a"], "plan_id": "pro",
                                           "status": "active", "current_period_end": end,
                                           "created_at": NOW}).inserted_id
        r = client.post(f"/api/super-admin/subscriptions/{sub}/extend", cookies=c["super"],
                        json={"days": 10, "reason": "goodwill"})
        assert r.status_code == 200, r.text
        doc = db.subscriptions.find_one({"_id": sub})
        assert doc["status"] == "active"
        new_end = doc["current_period_end"]
        if new_end.tzinfo is None:
            new_end = new_end.replace(tzinfo=timezone.utc)
        assert abs((new_end - end).total_seconds() - 10 * 86400) < 5
        assert db.audit_logs.find_one({"action": "subscription.extended"})

    def test_admins_listing(self, world):
        client, db, ids, c = world
        r = client.get("/api/super-admin/admins", cookies=c["super"]).json()
        assert [i["email"] for i in r["items"]] == ["owner@acme.test"]
        assert r["items"][0]["organization_name"] == "Acme"

    def test_impersonation_requires_reason(self, world):
        client, db, ids, c = world
        r = client.post("/api/super-admin/impersonate", cookies=c["super"],
                        json={"organization_id": ids["org_a"], "reason": ""})
        assert r.status_code == 422
        r = client.post("/api/super-admin/impersonate", cookies=c["super"],
                        json={"organization_id": ids["org_a"], "reason": "ticket #42 debugging"})
        assert r.status_code == 200 and r.json()["expires_at"]
        assert db.user_sessions.find_one({"impersonated_by": "root@platform.test"})
        assert db.audit_logs.find_one({"action": "impersonation.start"})


# ═══════════════════════════════════════════════════════════════════════════
# 6. Support tickets (staff side)
# ═══════════════════════════════════════════════════════════════════════════

def _ticket(db, org_id, status="open", number=1001, subject="Export is broken"):
    return str(db.support_tickets.insert_one({
        "organization_id": org_id, "organization_name": "Acme", "number": number, "subject": subject,
        "category": "bug", "priority": "high", "status": status, "created_by": "owner@acme.test",
        "messages": [{"author_email": "owner@acme.test", "author_name": "Owner", "from_staff": False,
                      "body": "CSV export fails", "at": NOW}],
        "created_at": NOW, "updated_at": NOW}).inserted_id)


class TestSupportTickets:
    def test_list_filters_and_detail(self, world):
        client, db, ids, c = world
        other = _org(db, "Other")
        t1 = _ticket(db, ids["org_a"])
        _ticket(db, other, status="closed", number=1002, subject="Billing question")
        r = client.get("/api/super-admin/support/tickets?status=active", cookies=c["super"]).json()
        assert [i["id"] for i in r["items"]] == [t1] and r["items"][0]["awaiting_staff"] is True
        assert r["counts"] == {"open": 1, "closed": 1}
        assert client.get("/api/super-admin/support/tickets?q=billing", cookies=c["super"]).json()["total"] == 1
        assert client.get("/api/super-admin/support/tickets?q=%231001", cookies=c["super"]).json()["total"] == 1
        assert client.get(f"/api/super-admin/support/tickets?organization_id={other}",
                          cookies=c["super"]).json()["total"] == 1
        d = client.get(f"/api/super-admin/support/tickets/{t1}", cookies=c["super"]).json()["ticket"]
        assert d["messages"][0]["body"] == "CSV export fails"
        assert client.get("/api/super-admin/support/tickets/000000000000000000000000",
                          cookies=c["super"]).status_code == 404

    def test_staff_reply_appends_notifies_and_audits(self, world):
        client, db, ids, c = world
        t1 = _ticket(db, ids["org_a"])
        r = client.post(f"/api/super-admin/support/tickets/{t1}/messages", cookies=c["super"],
                        json={"message": "Fixed — please retry"})
        assert r.status_code == 200, r.text
        t = r.json()["ticket"]
        assert t["status"] == "waiting" and t["messages"][-1]["from_staff"] is True
        doc = db.support_tickets.find_one({"_id": ObjectId(t1)})
        assert doc["messages"][-1]["body"] == "Fixed — please retry"
        assert db.notifications.find_one({"audience": "org_admin", "organization_id": ids["org_a"],
                                          "type": "support_reply"})
        assert db.audit_logs.find_one({"action": "support.staff_replied", "resource_id": t1,
                                       "actor_email": "root@platform.test"})
        assert client.post(f"/api/super-admin/support/tickets/{t1}/messages", cookies=c["super"],
                           json={"message": "   "}).status_code == 422
        # the org admin sees the staff reply through their own portal API
        mine = client.get(f"/api/org-admin/support/tickets/{t1}", cookies=c["owner"])
        if mine.status_code == 200:
            assert mine.json()["ticket"]["messages"][-1]["from_staff"] is True

    def test_status_change_notifies_and_audits(self, world):
        client, db, ids, c = world
        t1 = _ticket(db, ids["org_a"])
        assert client.post(f"/api/super-admin/support/tickets/{t1}/status", cookies=c["super"],
                           json={"status": "deleted"}).status_code == 422
        r = client.post(f"/api/super-admin/support/tickets/{t1}/status", cookies=c["super"],
                        json={"status": "resolved", "reason": "shipped fix"})
        assert r.status_code == 200 and r.json()["ticket"]["status"] == "resolved"
        doc = db.support_tickets.find_one({"_id": ObjectId(t1)})
        assert doc["status_history"][-1]["to"] == "resolved"
        assert db.notifications.find_one({"audience": "org_admin", "organization_id": ids["org_a"],
                                          "type": "support_status"})
        row = db.audit_logs.find_one({"action": "support.status_changed", "resource_id": t1})
        assert row["details"]["before"] == "open" and row["details"]["after"] == "resolved"
