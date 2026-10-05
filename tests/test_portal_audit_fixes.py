"""
Backend fixes found by the all-portal audit: invoice / receipt downloads, the
Environment guard password, and no made-up AI scores on unanalysed comments.

Run:  python -m pytest tests/test_portal_audit_fixes.py -q -p no:cacheprovider
"""
from datetime import datetime
from unittest.mock import patch

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.auth.service import COOKIE_NAME, build_session_value, create_tracked_session
from tests.conftest import TEST_SUPERADMIN_PASSWORD, as_superadmin

SUPER = "platform-owner@leadai.example"


@pytest.fixture
def env():
    as_superadmin(SUPER)
    with patch("app.admin.settings.is_maintenance_enabled", return_value=False):
        from app.main import app
        with TestClient(app) as client:
            from app.db.mongo import get_sync_db
            yield client, get_sync_db()


def _owner(db, org_name):
    org = str(db.organizations.insert_one({"name": org_name, "slug": org_name.lower().replace(" ", "-"),
                                           "status": "active", "settings": {}}).inserted_id)
    email = f"owner@{org_name.lower().replace(' ', '')}.test"
    uid = str(db.users.insert_one({"email": email, "name": "Owner", "status": "active",
                                   "default_organization_id": org}).inserted_id)
    db.organization_members.insert_one({"organization_id": org, "user_id": uid, "role": "owner", "status": "active"})
    cookie = build_session_value(create_tracked_session({
        "user_id": uid, "email": email, "name": "Owner", "scope": "site", "organization_id": org,
        "org_role": "owner"}))
    return org, {COOKIE_NAME: cookie}


def test_invoice_and_receipt_download_only_for_their_organization(env):
    client, db = env
    org_a, cookie_a = _owner(db, "Alpha Homes")
    org_b, cookie_b = _owner(db, "Beta Corp")
    now = datetime.utcnow()
    db.invoices.insert_many([
        {"organization_id": org_a, "number": "INV-A-1", "status": "paid", "total": 49.0, "subtotal": 49.0,
         "currency": "USD", "description": "Subscription to Starter <script>", "invoice_date": now, "paid_at": now},
        {"organization_id": org_a, "number": "INV-A-2", "status": "open", "total": 49.0, "currency": "USD",
         "invoice_date": now}])
    client.cookies.clear()
    r = client.get("/api/billing/invoices/INV-A-1/download", cookies=cookie_a)
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "Alpha Homes" in r.text and "49.00 USD" in r.text and "<script>" not in r.text   # escaped
    r = client.get("/api/billing/invoices/INV-A-1/receipt", cookies=cookie_a)
    assert r.status_code == 200 and "Receipt" in r.text
    assert client.get("/api/billing/invoices/INV-A-2/receipt", cookies=cookie_a).status_code == 409   # unpaid
    client.cookies.clear()
    assert client.get("/api/billing/invoices/INV-A-1/download", cookies=cookie_b).status_code == 404  # other org


def _admin_console(client):
    r = client.post("/api/auth/login", json={"email": SUPER, "password": TEST_SUPERADMIN_PASSWORD, "scope": "admin"})
    assert r.status_code == 200, r.text
    return client


def test_super_admin_sets_and_changes_the_environment_guard_password(env):
    client, db = env
    sa = _admin_console(client)
    assert sa.get("/api/admin/env/lock-status").json()["guard_set"] is False
    assert sa.put("/api/admin/env/guard", json={"new_password": "short"}).status_code == 422
    assert sa.put("/api/admin/env/guard", json={"new_password": "Guard-Pass-2026"}).status_code == 200
    assert sa.get("/api/admin/env/lock-status").json()["guard_set"] is True
    assert sa.post("/api/admin/env/unlock", json={"password": "Guard-Pass-2026"}).status_code == 200
    # changing it needs the current one
    assert sa.put("/api/admin/env/guard", json={"new_password": "Other-Pass-2026"}).status_code == 401
    assert sa.put("/api/admin/env/guard", json={"new_password": "Other-Pass-2026",
                                                "current_password": "Guard-Pass-2026"}).status_code == 200
    assert db.audit_logs.find_one({"action": "env.guard.set"})


def test_unanalysed_comments_have_no_made_up_score(env):
    client, db = env
    org, cookie = _owner(db, "Gamma Shop")
    owner = {"organization_id": org, "user_id": db.users.find_one({"default_organization_id": org})["_id"].__str__()}
    post = str(db.facebook_posts.insert_one({"post_url": "https://x.test/p", "platform": "facebook",
                                             "comments_status": "completed", **owner}).inserted_id)
    db.facebook_comments.insert_many([
        {"text": "nice", "post_ref": post, "keyword_filter_status": "NOT_MATCHED", **owner},
        {"text": "call me 9876543210", "post_ref": post, "keyword_filter_status": "NOT_MATCHED", **owner}])
    client.cookies.clear()
    data = client.get(f"/api/posts/{post}/comments", cookies=cookie).json()
    by_text = {c["comment_text"]: c for c in data["comments"]}
    assert by_text["nice"]["lead_score"] is None and by_text["nice"]["priority"] is None
    assert by_text["nice"]["analysed"] is False and by_text["nice"]["is_lead"] is False
    assert by_text["call me 9876543210"]["is_lead"] is True          # contact-ready still counts
    assert ObjectId.is_valid(post)


def test_shipped_demo_copy_becomes_trial_copy_once_and_keeps_admin_edits(env):
    import asyncio
    from app.cms.models import COLL_FAQ, COLL_PAGES, migrate_trial_copy
    from app.db.mongo import get_async_db
    client, db = env
    db.website_seed_state.delete_many({"_id": "trial_copy_v1"})
    db[COLL_PAGES].insert_one({"slug": "zz-old", "sections": [
        {"cta_primary": {"label": "Request a demo", "url": "/request-demo"},
         "items": [{"title": "Get approved", "label": "What happens after you request a demo", "url": "/demo-pending"}],
         "subtitle": "Request a demo and we'll walk you through it."},
        {"cta_primary": {"label": "Request a demo today!", "url": "/request-demo"}}]})       # admin-edited: kept
    db[COLL_FAQ].insert_one({"question": "How do I buy a subscription?", "answer":
                             "Request a demo and create your login. Once our team approves your request you can sign in "
                             "and choose a plan from the billing page in your workspace."})
    assert asyncio.run(migrate_trial_copy(get_async_db())) >= 2
    page = db[COLL_PAGES].find_one({"slug": "zz-old"})
    s0, s1 = page["sections"]
    assert s0["cta_primary"] == {"label": "Start free trial", "url": "/request-demo"}
    assert s0["items"][0]["title"] == "Start finding leads" and s0["items"][0]["url"] == "/demo-pending"
    assert s0["subtitle"].startswith("Start your free trial")
    assert s1["cta_primary"]["label"] == "Request a demo today!"
    assert "free trial" in db[COLL_FAQ].find_one({"question": "How do I buy a subscription?"})["answer"]
    assert asyncio.run(migrate_trial_copy(get_async_db())) == 0                                # only once
