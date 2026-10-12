"""
Regression tests for the production audit fixes (in-memory MongoDB only).

Security, lifecycle synchronization, notifications, billing periods, tokens,
exports and portal actions that were verified broken and fixed.
"""
from datetime import timedelta
from unittest.mock import patch

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from tests.test_super_admin_portal2 import _cookie, _org, _staff, _super, _user


@pytest.fixture
def env():
    with patch("app.admin.settings.is_maintenance_enabled", return_value=False):
        from app.main import app
        with TestClient(app) as client:
            from app.db.mongo import get_sync_db
            yield client, get_sync_db()


def _site(uid, email, org, role="owner"):
    return _cookie({"user_id": uid, "email": email, "name": email.split("@")[0],
                    "scope": "site", "organization_id": org, "org_role": role})


def _now():
    from app.db.models import utcnow
    return utcnow()


# ── security ────────────────────────────────────────────────────────────────

def test_org_admin_cannot_reset_password_of_multi_org_member(env):
    client, db = env
    org_a = _org(db, "Reset A", admin_portal_enabled=True)
    org_b = _org(db, "Reset B", admin_portal_enabled=True)
    owner_b = _user(db, "boss@b.example", org_b, role="owner")
    victim = _user(db, "victim@a.example", org_a, role="owner")
    db.organization_members.insert_one({"organization_id": org_b, "user_id": victim, "role": "member",
                                        "status": "active", "permissions_override": {}})
    r = client.post(f"/api/org-admin/users/{victim}/reset-access", json={},
                    cookies=_site(owner_b, "boss@b.example", org_b))
    assert r.status_code == 409 and "reset_url" not in r.text
    assert db.password_resets.count_documents({"user_id": victim}) == 0
    # a member of this organization only can still be reset by its Admin
    solo = _user(db, "solo@b.example", org_b, role="member")
    assert client.post(f"/api/org-admin/users/{solo}/reset-access", json={},
                       cookies=_site(owner_b, "boss@b.example", org_b)).status_code == 200


def test_secret_env_vars_are_environment_only():
    from app.admin import envvars as ev
    # Core system credentials must stay environment-only (never overridden in DB)
    for name in ("SESSION_SECRET", "SUPERADMIN_EMAIL", "SUPERADMIN_PASSWORD"):
        assert ev.is_locked(name), name
    # Gateway secrets can be configured by Super Admin and are sealed / encrypted at rest
    for name in ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET",
                 "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET",
                 "SMTP_PASSWORD"):
        assert name in ev._SEALED, name
        assert ev.is_secret(name), name



def test_manager_with_delegated_settings_cannot_share_workspace(env):
    client, db = env
    org = _org(db, "Share Org", admin_portal_enabled=True)
    mgr = _user(db, "mgr@share.example", org, role="manager")
    db.organization_members.update_one({"user_id": mgr}, {"$set": {"permissions_override": {
        "settings.manage": True, "settings.view": True}}})
    ck = _site(mgr, "mgr@share.example", org, role="manager")
    r = client.patch("/api/organizations/current", cookies=ck, json={"settings": {"shared_workspace": True}})
    assert r.status_code == 403
    assert not (db.organizations.find_one({"_id": ObjectId(org)}).get("settings") or {}).get("shared_workspace")
    owner = _user(db, "own@share.example", org, role="owner")
    assert client.patch("/api/organizations/current", cookies=_site(owner, "own@share.example", org),
                        json={"settings": {"shared_workspace": True}}).status_code == 200


def test_email_outbox_never_stores_live_links():
    from app.events.email import redact_links
    body = redact_links("Reset: https://x/reset-password?token=abcdefgh1234 or https://x/invite/Zz9_abcdefgh")
    assert "abcdefgh1234" not in body and "Zz9_abcdefgh" not in body and body.count("[redacted]") == 2


def test_staff_change_own_password_needs_current_password(env):
    client, db = env
    ck = _staff(db, "manager")
    db.users.update_one({"email": "manager@platform.test"}, {"$set": {
        "password_hash": __import__("app.auth.crypto", fromlist=["x"]).hash_password("Old-Pass-2026")}})
    url = "/api/admin/security/change-password"
    assert client.post(url, cookies=ck, json={"old_password": "wrong", "new_password": "New-Pass-2026"}).status_code == 400
    assert client.post(url, cookies=ck, json={"old_password": "Old-Pass-2026",
                                              "new_password": "New-Pass-2026"}).status_code == 200
    # the Super Admin's password lives in the environment
    assert client.post(url, cookies=_super(db), json={"old_password": "x",
                                                      "new_password": "Whatever-2026"}).status_code == 409


# ── lifecycle synchronization ───────────────────────────────────────────────

def test_activate_restores_demo_and_requires_subscription_for_active(env):
    client, db = env
    sa = _super(db)
    demo = _org(db, "Demo Co", status="demo", demo={"expires_at": _now() + timedelta(days=5)})
    base = "/api/super-admin/organizations/{}/status"
    assert client.patch(base.format(demo), cookies=sa, json={"status": "suspended", "reason": "x"}).status_code == 200
    r = client.patch(base.format(demo), cookies=sa, json={"status": "active", "reason": ""})
    assert r.status_code == 200 and r.json()["status"] == "demo"
    assert db.organizations.find_one({"_id": ObjectId(demo)})["status"] == "demo"
    # no subscription, no demo -> cannot become a paid active organization
    bare = _org(db, "Bare Co", status="suspended")
    assert client.patch(base.format(bare), cookies=sa, json={"status": "active"}).status_code == 409
    paid = _org(db, "Paid Co", status="suspended")
    db.subscriptions.insert_one({"organization_id": paid, "status": "active", "plan_id": "starter"})
    assert client.patch(base.format(paid), cookies=sa, json={"status": "active"}).json()["status"] == "active"
    # the org's own admins are told
    _user(db, "o@paid.example", paid, role="owner")
    client.patch(base.format(paid), cookies=sa, json={"status": "suspended", "reason": "unpaid"})
    assert db.notifications.find_one({"organization_id": paid, "type": "organization_suspended",
                                      "audience": "org_admin"})


def test_demo_cancel_and_extend_keep_accounts_consistent(env):
    client, db = env
    from app.lifecycle import demo as D
    r = client.post("/api/auth/signup", json={"email": "c@cancel.example", "password": "Cancel-Me-2026!",
                                              "name": "C", "company": "Cancel Co"})
    req = r.json()["demo_request_id"]
    D.cancel(req, actor="root@platform.test", reason="duplicate")
    assert db.users.find_one({"email": "c@cancel.example"})["status"] == "rejected"
    r = client.post("/api/auth/signup", json={"email": "e@ext.example", "password": "Extend-Me-2026!",
                                              "name": "E", "company": "Ext Co"})
    req = r.json()["demo_request_id"]
    D.approve(req, actor="root@platform.test")
    org = db.users.find_one({"email": "e@ext.example"})["default_organization_id"]
    db.organizations.update_one({"_id": ObjectId(org)}, {"$set": {"status": "suspended"}})
    D.extend(req, actor="root@platform.test", days=3)
    assert db.organizations.find_one({"_id": ObjectId(org)})["status"] == "suspended"


def test_rejecting_paid_pending_subscription_notifies_customer_and_refund(env):
    client, db = env
    import asyncio
    from app.billing.subscriptions import set_subscription_status
    org = _org(db, "Refund Org", status="demo")
    sid = db.subscriptions.insert_one({"organization_id": org, "status": "pending_admin_confirmation",
                                       "plan_id": "starter", "status_history": []}).inserted_id
    db.payments.insert_one({"subscription_id": str(sid), "organization_id": org, "status": "succeeded"})
    asyncio.run(set_subscription_status(str(sid), "cancelled", actor={"email": "root"}, reason="Invalid VAT id"))
    assert db.notifications.find_one({"organization_id": org, "type": "subscription_cancelled"})
    assert db.notifications.find_one({"audience": "super_admin", "title": "Refund required"})


# ── billing periods / tokens ────────────────────────────────────────────────

def _plan_tokens(db, slug="starter"):
    plan = db.plans.find_one({"slug": slug}) or {}
    return int((plan.get("limits") or {}).get("monthly_tokens") or 0)


def test_lifecycle_sweep_period_end_and_demo_notices(env):
    _client, db = env
    from app.lifecycle.maintenance import run_lifecycle_sweep
    now = _now()
    ending = _org(db, "Ending Co", status="active")
    s1 = db.subscriptions.insert_one({"organization_id": ending, "status": "active", "plan_id": "starter",
                                      "cancel_at_period_end": True,
                                      "current_period_end": now - timedelta(hours=1)}).inserted_id
    db.organizations.update_one({"_id": ObjectId(ending)}, {"$set": {"subscription_id": str(s1)}})
    overdue = _org(db, "Overdue Co", status="active")
    s2 = db.subscriptions.insert_one({"organization_id": overdue, "status": "active", "plan_id": "starter",
                                      "current_period_end": now - timedelta(days=10)}).inserted_id
    soon = _org(db, "Soon Co", status="demo", demo={"expires_at": now + timedelta(days=1)})
    gone = _org(db, "Gone Co", status="demo", demo={"expires_at": now - timedelta(hours=2)})
    out = run_lifecycle_sweep(db)
    assert out == {"cancelled_at_period_end": 1, "past_due": 1, "demo_expiring": 1, "demo_expired": 1}
    assert db.subscriptions.find_one({"_id": s1})["status"] == "cancelled"
    assert db.organizations.find_one({"_id": ObjectId(ending)})["status"] == "cancelled"
    assert db.subscriptions.find_one({"_id": s2})["status"] == "past_due"
    for org, t in ((soon, "demo_expiring"), (gone, "demo_expired")):
        assert db.notifications.find_one({"organization_id": org, "type": t})
    # idempotent: nothing is sent twice
    assert run_lifecycle_sweep(db) == {"cancelled_at_period_end": 0, "past_due": 0,
                                       "demo_expiring": 0, "demo_expired": 0}


def test_renewal_advances_period_and_regrants_tokens(env):
    _client, db = env
    from app.lifecycle.maintenance import renew_subscription
    db.plans.update_one({"slug": "starter"}, {"$set": {"limits.monthly_tokens": 500}}, upsert=True)
    org = _org(db, "Renew Co", status="active")
    end = _now() - timedelta(hours=1)
    sid = db.subscriptions.insert_one({"organization_id": org, "status": "past_due", "plan_id": "starter",
                                       "billing_cycle": "monthly", "amount": 49,
                                       "current_period_end": end}).inserted_id
    out = renew_subscription(db, db.subscriptions.find_one({"_id": sid}), source="test")
    assert out["renewed"]
    sub = db.subscriptions.find_one({"_id": sid})
    assert sub["status"] == "active"
    bal = db.token_balances.find_one({"organization_id": org})
    assert bal["remaining"] == 500 and bal["expires_at"] is not None
    assert db.invoices.count_documents({"subscription_id": str(sid)}) == 1
    # the same period is never renewed twice
    assert not renew_subscription(db, sub, source="test")["renewed"]


def test_extending_subscription_moves_token_expiry(env):
    client, db = env
    org = _org(db, "Extend Co", status="active")
    old_end = _now() + timedelta(days=1)
    sid = db.subscriptions.insert_one({"organization_id": org, "status": "past_due", "plan_id": "starter",
                                       "current_period_end": old_end}).inserted_id
    db.token_balances.insert_one({"organization_id": org, "source": "plan", "allocated": 100, "used": 0,
                                  "remaining": 100, "expires_at": old_end})
    r = client.post(f"/api/super-admin/subscriptions/{sid}/extend", cookies=_super(db),
                    json={"days": 10, "reason": "goodwill"})
    assert r.status_code == 200, r.text
    sub = db.subscriptions.find_one({"_id": sid})
    bal = db.token_balances.find_one({"organization_id": org})
    assert sub["status"] == "active"
    assert bal["expires_at"].replace(tzinfo=None) >= (old_end + timedelta(days=9)).replace(tzinfo=None)


def test_token_topup_rearms_warnings_and_exhaustion_alerts(env):
    _client, db = env
    from app.billing import tokens
    org = _org(db, "Tok Co", status="active")
    db.token_balances.insert_one({"organization_id": org, "allocated": 100, "used": 91, "remaining": 9,
                                  "notified_thresholds": [80], "expires_at": None})
    with pytest.raises(Exception):
        tokens.consume(org, 10)
    assert db.notifications.find_one({"organization_id": org, "title": "Token limit reached"})
    tokens.adjust(org, 400, actor="root", reason="top-up")
    bal = db.token_balances.find_one({"organization_id": org})
    assert 80 not in bal["notified_thresholds"] and 100 not in bal["notified_thresholds"]
    # a top-up of an expired balance is usable
    db.token_balances.update_one({"organization_id": org}, {"$set": {"expires_at": _now() - timedelta(days=1)}})
    tokens.adjust(org, 10, actor="root", reason="top-up")
    assert tokens.consume(org, 5)


# ── pipeline / data ─────────────────────────────────────────────────────────

def test_leads_get_created_at_and_org_minimum_score(env):
    _client, db = env
    from app.pipeline import comment_ai
    org = _org(db, "Score Org", settings={"min_lead_score": 99})
    page = db.facebook_pages.insert_one({"page_name": "P", "organization_id": org}).inserted_id
    post = db.facebook_posts.insert_one({"page_ref": str(page), "caption": "offer", "organization_id": org,
                                         "platform": "facebook"}).inserted_id
    db.facebook_comments.insert_one({"post_ref": str(post), "text": "price please? call 9876543210",
                                     "author_name": "Buyer", "organization_id": org})
    comment_ai.analyze_comments_for_post(str(post))
    lead = db.ai_comments.find_one({"post_ref": str(post)})
    assert lead["created_at"] is not None
    assert lead["is_lead"] is False  # below this organization's minimum score
    # backfill for older leads
    db.ai_comments.insert_one({"analyzed_at": _now(), "is_lead": True, "organization_id": org,
                               "comment_ref": "legacy-ref"})
    from app.db.migration import migrate_to_multi_tenant
    migrate_to_multi_tenant(db)
    assert db.ai_comments.count_documents({"created_at": {"$exists": False}}) == 0


def test_product_compliments_are_not_leads():
    from app.pipeline.comment_ai import rule_based_classify
    assert rule_based_classify("nice car")["is_useful"] is False
    assert rule_based_classify("beautiful villa")["is_useful"] is False
    assert rule_based_classify("nice car, price?")["is_useful"] is True


def test_apify_runs_are_recorded_for_the_organization(env):
    _client, db = env
    from app.connectors import apify_connector as ac

    class _Run:
        def call(self, **kw):
            return {"id": "apify-run-1", "status": "SUCCEEDED", "defaultDatasetId": "ds1", "usageUsd": 0.012}

    class _Client:
        def actor(self, actor_id):
            return _Run()
    ac.set_run_context(organization_id="org-x", user_id="u1", search_run_id="URL1", platform="facebook")
    with patch.object(ac.ApifyConnector, "_get_client", return_value=_Client()):
        ac.ApifyConnector()._call_actor("apify/facebook-posts-scraper", {}, "posts")
    job = db.apify_jobs.find_one({"job_id": "apify-run-1"})
    assert job["organization_id"] == "org-x" and job["search_run_id"] == "URL1" and job["status"] == "succeeded"


# ── portal actions ──────────────────────────────────────────────────────────

def test_admin_console_page_and_post_detail(env):
    client, db = env
    ck = _staff(db, "viewer")
    page = db.facebook_pages.insert_one({"page_name": "Console page", "organization_id": "o1"}).inserted_id
    post = db.facebook_posts.insert_one({"caption": "Console post", "organization_id": "o1"}).inserted_id
    assert client.get(f"/api/admin/pages/{page}", cookies=ck).json()["page_name"] == "Console page"
    assert client.get(f"/api/admin/posts/{post}", cookies=ck).json()["caption"] == "Console post"
    assert client.get(f"/api/admin/pages/{ObjectId()}", cookies=ck).status_code == 404


def test_expired_invitation_can_be_resent_and_all_filter(env):
    client, db = env
    org = _org(db, "Inv Org", admin_portal_enabled=True)
    owner = _user(db, "own@inv.example", org, role="owner")
    ck = _site(owner, "own@inv.example", org)
    db.subscriptions.insert_one({"organization_id": org, "plan_id": "business", "status": "active"})
    r = client.post("/api/organizations/current/invitations", cookies=ck, json={"email": "x@inv.example", "role": "member"})
    assert r.status_code == 200 and "Email is not configured" in r.json()["message"]
    inv_id = r.json()["invitation"]["id"]
    db.organization_invitations.update_one({"_id": ObjectId(inv_id)}, {"$set": {"status": "expired"}})
    assert client.post(f"/api/organizations/current/invitations/{inv_id}/resend", cookies=ck).status_code == 200
    items = client.get("/api/org-admin/invitations?status=all", cookies=ck).json()["items"]
    assert {i["status"] for i in items} >= {"cancelled", "pending"}


def test_org_admin_lead_export_honours_table_filters(env):
    client, db = env
    org = _org(db, "Exp Org", admin_portal_enabled=True, status="active")
    owner = _user(db, "own@exp.example", org, role="owner")
    db.subscriptions.insert_one({"organization_id": org, "plan_id": "business", "status": "active"})
    for q in ("hot", "cold"):
        db.ai_comments.insert_one({"organization_id": org, "user_id": owner, "is_lead": True,
                                   "comment_ref": f"ref-{q}", "lead_quality": q,
                                   "commenter_name": f"{q}-lead", "lead_score": 50})
    with patch("app.billing.entitlements.EntitlementService.enforce_quota_and_consume", return_value=None):
        r = client.get("/api/org-admin/exports/leads.csv?quality=hot", cookies=_site(owner, "own@exp.example", org))
    assert r.status_code == 200, r.text
    assert "hot-lead" in r.text and "cold-lead" not in r.text


def test_plan_limit_can_be_cleared(env):
    client, db = env
    sa = _super(db)
    r = client.post("/api/super-admin/plans", cookies=sa, json={
        "name": "Clearable", "slug": "clearable", "price_monthly": 5, "limits": {"monthly_searches": 10}})
    assert r.status_code in (200, 201), r.text
    pid = (r.json().get("plan") or {}).get("id") or (r.json().get("plan") or {}).get("_id")
    r = client.patch(f"/api/super-admin/plans/{pid}", cookies=sa, json={"limits": {}, "limits_unset": ["monthly_searches"]})
    assert r.status_code == 200, r.text
    assert "monthly_searches" not in (db.plans.find_one({"slug": "clearable"}).get("limits") or {})


def test_super_admin_report_status_aliases_and_user_leads(env):
    client, db = env
    sa = _super(db)
    db.subscriptions.insert_one({"organization_id": "o1", "status": "pending_admin_confirmation",
                                 "plan_id": "starter", "created_at": _now()})
    csv = client.get("/api/super-admin/reports/subscriptions.csv?status=awaiting", cookies=sa).text
    assert "pending_admin_confirmation" in csv
    db.ai_comments.insert_many([{"is_lead": True, "user_id": "u-1", "comment_ref": "r-1", "created_at": _now()},
                                {"is_lead": True, "user_id": "u-2", "comment_ref": "r-2", "created_at": _now()}])
    assert client.get("/api/super-admin/leads?user_id=u-1", cookies=sa).json()["total"] == 1


def test_login_redirect_keeps_the_requested_page(env):
    client, _db = env
    r = client.get("/billing/status?session=cs_123", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=%2Fbilling%2Fstatus")
