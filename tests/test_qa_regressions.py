"""Regression tests for bugs found by the QA run (reports/QA_REPORT.md).

Runs on the in-memory MongoDB from tests/conftest.py, never a real database.
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def env():
    with patch("app.admin.settings.is_maintenance_enabled", return_value=False):
        from app.main import app
        with TestClient(app) as client:
            from app.db.mongo import get_sync_db
            from app.api.routes import auth as auth_routes
            auth_routes._email_verify_limiter._memory.clear()  # 5 per hour per IP
            yield client, get_sync_db()


# ── BUG-002: verification links are mailed only to real, unverified accounts ──

def _resend(client, email):
    with patch("app.api.routes.auth.send_email") as send:
        r = client.post("/api/auth/verify-email/resend", json={"email": email})
    return r, send


def test_resend_verification_never_mails_an_unknown_address(env):
    client, db = env
    r, send = _resend(client, "stranger@example.com")
    assert r.status_code == 200 and r.json()["success"] is True
    send.assert_not_called()
    assert db.email_verifications.count_documents({}) == 0


def test_resend_verification_skips_already_verified_account(env):
    client, db = env
    db.users.insert_one({"email": "done@example.com", "status": "active", "email_verified": True})
    r, send = _resend(client, "done@example.com")
    assert r.status_code == 200
    send.assert_not_called()


def test_resend_verification_mails_unverified_account_and_answers_alike(env):
    client, db = env
    uid = db.users.insert_one({"email": "new@example.com", "status": "active",
                               "email_verified": False}).inserted_id
    r, send = _resend(client, "new@example.com")
    unknown, _ = _resend(client, "nobody@example.com")
    assert r.status_code == 200 and send.call_count == 1
    assert send.call_args.args[0] == "new@example.com"
    # an unknown address gets exactly the same answer (no account enumeration)
    assert unknown.json() == r.json()
    doc = db.email_verifications.find_one({"used_at": None})
    assert doc["user_id"] == str(uid)


# ── BUG-004: assignment rules are applied, stamp an SLA, and the sweep runs ──

def _org_with_members(db, roles=("member", "member", "viewer")):
    from bson import ObjectId
    org = str(db.organizations.insert_one({"name": "Rules Co", "status": "active",
                                           "settings": {"lead_assignment": "manual"}}).inserted_id)
    ids = []
    for i, role in enumerate(roles):
        uid = str(db.users.insert_one({"email": f"agent{i}@rules.test", "status": "active"}).inserted_id)
        db.organization_members.insert_one({"organization_id": org, "user_id": uid, "role": role,
                                            "status": "active"})
        ids.append(uid)
    other = str(ObjectId())
    return org, ids, other


def _lead(db, org, score=80, intent="purchase_inquiry", status=None):
    from bson import ObjectId
    doc = {"organization_id": org, "is_lead": True, "lead_score": score, "intent": intent,
           "priority": "hot", "commenter_name": "Ravi", "comment_ref": str(ObjectId())}
    if status:
        doc["lead_status"] = status
    return db.ai_comments.insert_one(doc).inserted_id


def test_rule_assigns_new_leads_round_robin_with_sla(env):
    from app.pipeline.lead_assignment import auto_assign_lead
    from app.services.lead_assignment import create_assignment_rule
    _, db = env
    org, (m1, m2, _viewer), _ = _org_with_members(db)
    create_assignment_rule(db, organization_id=org, name="Hot", assignees=[m1, m2], sla_hours=4,
                           criteria={"min_score": 70})
    got = [auto_assign_lead(db, _lead(db, org)) for _ in range(4)]
    assert [g["user_id"] for g in got] == [m1, m2, m1, m2]
    stored = db.ai_comments.find_one({"_id": db.ai_comments.find_one({"organization_id": org})["_id"]})
    assert stored["sla_hours"] == 4 and stored["sla_status"] == "on_track"
    assert 3.9 < (stored["sla_due_at"] - stored["assigned_at"]).total_seconds() / 3600 <= 4
    assert stored["auto_assignment"]["mode"] == "rule"


def test_rule_criteria_not_met_falls_back_to_org_setting(env):
    from app.pipeline.lead_assignment import auto_assign_lead
    from app.services.lead_assignment import create_assignment_rule
    _, db = env
    org, (m1, m2, _v), _ = _org_with_members(db)
    create_assignment_rule(db, organization_id=org, name="Hot only", assignees=[m1], criteria={"min_score": 90})
    assert auto_assign_lead(db, _lead(db, org, score=40)) is None  # manual org, no rule matched
    lead = db.ai_comments.find_one({"organization_id": org})
    assert not lead.get("assigned_user_id") and "sla_due_at" not in lead


def test_rule_assignees_must_be_eligible_members(env):
    from app.services.lead_assignment import create_assignment_rule
    _, db = env
    org, (m1, _m2, viewer), foreign = _org_with_members(db)
    for bad in ([foreign], [viewer], [m1, "sales_agent_1"]):
        with pytest.raises(ValueError):
            create_assignment_rule(db, organization_id=org, name="x", assignees=bad)


def test_member_removed_after_rule_is_never_assigned(env):
    from app.pipeline.lead_assignment import auto_assign_lead
    from app.services.lead_assignment import create_assignment_rule
    _, db = env
    org, (m1, m2, _v), _ = _org_with_members(db)
    create_assignment_rule(db, organization_id=org, name="All", assignees=[m1, m2])
    db.organization_members.update_one({"user_id": m1}, {"$set": {"status": "removed"}})
    assert {auto_assign_lead(db, _lead(db, org))["user_id"] for _ in range(3)} == {m2}


def test_sla_sweep_flags_uncontacted_leads_and_overdue_follow_ups(env):
    from datetime import timedelta
    from app.db.models import utcnow
    from app.services.lead_assignment import check_sla_and_reminders
    _, db = env
    org, (m1, _m2, _v), _ = _org_with_members(db)
    past, now = utcnow() - timedelta(hours=1), utcnow()
    late = _lead(db, org)
    contacted = _lead(db, org, status="contacted")
    for lid in (late, contacted):
        db.ai_comments.update_one({"_id": lid}, {"$set": {"assigned_user_id": m1, "sla_hours": 4,
                                                          "sla_due_at": past, "sla_status": "on_track"}})
    fu = _lead(db, org)
    db.ai_comments.update_one({"_id": fu}, {"$set": {"follow_ups": [
        {"title": "Call back", "status": "pending", "due_at": past, "created_by_user_id": m1},
        {"title": "Later", "status": "pending", "due_at": now + timedelta(days=1), "created_by_user_id": m1}]}})
    out = check_sla_and_reminders(db)
    assert out == {"sla_breaches_flagged": 1, "overdue_followups_flagged": 1}
    assert db.ai_comments.find_one({"_id": late})["sla_status"] == "breached"
    assert db.ai_comments.find_one({"_id": contacted})["sla_status"] == "on_track"
    kinds = {n["type"] for n in db.notifications.find({"user_id": m1})}
    assert {"lead_sla_breached", "follow_up_overdue"} <= kinds
    # once only
    assert check_sla_and_reminders(db) == {"sla_breaches_flagged": 0, "overdue_followups_flagged": 0}


def test_sweeper_loop_runs_the_sla_sweep():
    import inspect
    from app.lifecycle import maintenance
    assert "check_sla_and_reminders" in inspect.getsource(maintenance._sweeper_loop)


# ── BUG-006: a bulk search goes through every rule of a single search ────────

class _NoopThread:
    started: list = []

    def __init__(self, *a, **kw):
        _NoopThread.started.append(a)

    def run(self):
        return None


@pytest.fixture
def bulk_env():
    _NoopThread.started = []
    with patch("app.admin.settings.is_maintenance_enabled", return_value=False), \
            patch("app.social.url_search.UrlSearchThread", _NoopThread):
        from app.main import app
        with TestClient(app) as client:
            from app.db.mongo import get_sync_db
            from app.auth.service import COOKIE_NAME, build_session_value, create_tracked_session
            from tests.conftest import seed_site_account
            db = get_sync_db()
            uid, org = seed_site_account("bulk@acme.example", db=db, org_name="Bulk Co")
            client.cookies.set(COOKIE_NAME, build_session_value(create_tracked_session({
                "user_id": uid, "email": "bulk@acme.example", "name": "b", "scope": "site",
                "organization_id": org, "org_role": "owner"})))
            yield client, db, org


URLS = ["https://facebook.com/acmeshoes", "https://www.facebook.com/acmeshoes", "",
        "https://example.com/nope", "https://instagram.com/acme.shoes"]


def test_bulk_starts_admitted_searches_charged_and_recorded(bulk_env):
    client, db, org = bulk_env
    db.token_balances.insert_one({"organization_id": org, "remaining": 100, "used": 0})
    r = client.post("/api/search/bulk", json={"urls": URLS, "max_posts": 3})
    assert r.status_code == 200, r.text
    j = r.json()
    # duplicates and blanks skipped; the bad link and the platform the Free plan
    # does not include (Instagram) are refused with their reason
    assert (j["total_submitted"], j["total_queued"], j["total_invalid"]) == (5, 1, 2)
    assert [q["platform"] for q in j["queued_runs"]] == ["facebook"]
    reasons = {i["url"]: i["error"] for i in j["invalid_urls"]}
    assert "not included in Free" in reasons["https://instagram.com/acme.shoes"]
    run = db.search_history.find_one({"run_id": j["queued_runs"][0]["run_id"]})
    assert run and run["organization_id"] == org and run["source"] == "bulk"
    assert db.token_balances.find_one({"organization_id": org})["remaining"] < 100
    assert len(_NoopThread.started) == 1
    assert db.job_queue.count_documents({}) == 0  # nothing bypasses admission through the queue


def test_bulk_refused_when_searches_are_switched_off(bulk_env):
    client, db, org = bulk_env
    db.token_balances.insert_one({"organization_id": org, "remaining": 100, "used": 0})
    with patch("app.admin.settings.get_bool", side_effect=lambda k, *a: k != "features.url_search.enabled"):
        r = client.post("/api/search/bulk", json={"urls": URLS})
    assert r.status_code == 403 and r.json()["detail"]["errorType"] == "feature_disabled"
    assert not _NoopThread.started and db.search_history.count_documents({}) == 0


def test_bulk_without_tokens_starts_nothing(bulk_env):
    client, db, org = bulk_env
    db.token_balances.insert_one({"organization_id": org, "remaining": 0, "used": 0})
    r = client.post("/api/search/bulk", json={"urls": ["https://facebook.com/acmeshoes",
                                                       "https://facebook.com/othershop"]})
    assert r.status_code == 200 and r.json()["total_queued"] == 0
    assert all("token" in i["error"].lower() for i in r.json()["invalid_urls"])
    assert not _NoopThread.started and db.search_history.count_documents({}) == 0


# ── BUG-032: the Environment guard password can't be brute-forced ────────────

def test_env_guard_locks_after_five_wrong_passwords(env):
    from app.admin import envvars as ev
    from app.api.routes import admin as admin_routes
    from app.auth.crypto import hash_password
    from app.auth.service import COOKIE_NAME, build_session_value, create_tracked_session
    client, db = env
    admin_routes._env_unlock_failures.clear()
    uid = str(db.users.insert_one({"email": "viewer@platform.test", "status": "active", "platform_role": "viewer",
                                   "is_platform_admin": True, "password_hash": hash_password("Str0ngPass!")}).inserted_id)
    client.cookies.set(COOKIE_NAME, build_session_value(create_tracked_session(
        {"user_id": uid, "email": "viewer@platform.test", "name": "v", "scope": "admin", "role": "viewer"})))
    assert ev.set_guard_password("Guard-Pass-2026!")
    codes = [client.post("/api/admin/env/unlock", json={"password": "guess"}).status_code for _ in range(6)]
    assert codes == [403] * 5 + [429]
    # even the right password waits out the lockout
    assert client.post("/api/admin/env/unlock", json={"password": "Guard-Pass-2026!"}).status_code == 429
    assert db.audit_logs.count_documents({"action": "env.unlock.failed"}) == 5


# ── A mistyped page is a 404, not a sign-in prompt; real pages still ask to sign in ──

def test_unknown_page_is_404_for_signed_out_visitors(env):
    client, _ = env
    r = client.get("/nope-this-page-does-not-exist", follow_redirects=False)
    assert r.status_code == 404 and "text/html" in r.headers["content-type"]
    for page, target in (("/user", "/login"), ("/org-admin", "/login"), ("/partner", "/login?partner=1")):
        r = client.get(page, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith(target), (page, r.headers.get("location"))
    assert client.get("/api/nope", follow_redirects=False).status_code in (401, 404)


def test_favicon_is_served_not_404(env):
    # browsers request /favicon.ico on their own; a 404 shows up as a console error
    client, _ = env
    r = client.get("/favicon.ico", follow_redirects=False)
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
