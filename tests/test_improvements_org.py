"""
Improvements round — ORG agent (items 12-15). In-memory MongoDB only.

12. Automatic lead assignment (organizations.settings.lead_assignment):
    round robin across ACTIVE members with leads.view (viewers / suspended /
    inactive / disabled accounts skipped), atomic per-org cursor, search
    owner ("creator" / "search_owner"), manual, never across organizations;
    same fields as the manual endpoint + history + audit + notification.
13. Organization notification settings (notify_* / email_notifications) are
    honoured by app/events/notifications.py; job completion notices.
14. Atomic bulk endpoints for leads and members (one audit entry, all ids
    validated first, foreign id -> 404 for the whole request).
15. Client-side exports are logged (exports collection + audit).
"""
import os
import threading
from datetime import datetime
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


def _no_smtp(name, default=""):
    """Emails stay queued in the outbox - never a real SMTP connection."""
    return "" if name.startswith("SMTP") else default


def _reset_caches():
    from app.auth import permissions as perm_mod
    from app.billing import plans as plans_mod
    from app.lifecycle import config as cfg_mod
    plans_mod.invalidate_plan_cache()
    cfg_mod.clear_cache()
    perm_mod.invalidate_permission_cache()


@pytest.fixture
def sdb():
    """Plain in-memory database (no HTTP app) for the pipeline-level tests."""
    sync_client = mongomock.MongoClient()
    async_client = mongomock_motor.AsyncMongoMockClient(mock_mongo_client=sync_client)
    _reset_caches()
    with patch("app.db.mongo.get_sync_client", return_value=sync_client), \
            patch("app.db.mongo.get_async_client", return_value=async_client), \
            patch("app.events.email._env", side_effect=_no_smtp):
        from app.config import get_settings
        yield sync_client[get_settings().mongo_db_name]
    _reset_caches()


@pytest.fixture
def env():
    sync_client = mongomock.MongoClient()
    async_client = mongomock_motor.AsyncMongoMockClient(mock_mongo_client=sync_client)
    _reset_caches()
    from app.api.routes import auth as auth_routes
    from app.auth import service as svc_mod
    svc_mod._login_attempts.clear()
    auth_routes._reset_attempts.clear()
    with patch("app.db.mongo.get_sync_client", return_value=sync_client), \
            patch("app.db.mongo.get_async_client", return_value=async_client), \
            patch("app.admin.settings.is_maintenance_enabled", return_value=False), \
            patch("app.events.email._env", side_effect=_no_smtp), \
            patch("app.billing.entitlements.EntitlementService.enforce_quota_and_consume",
                  new=_no_quota), \
            patch.dict(os.environ, {"STRIPE_SECRET_KEY": "", "SMTP_HOST": ""}):
        from app.main import app
        from app.config import get_settings
        with TestClient(app) as client:
            yield client, sync_client[get_settings().mongo_db_name]
    _reset_caches()


def _org(db, name, settings=None, status="active"):
    return str(db.organizations.insert_one({
        "name": name, "slug": name.lower().replace(" ", "-") + str(ObjectId())[-4:],
        "status": status, "settings": settings or {}, "admin_portal_enabled": True}).inserted_id)


def _user(db, email, org_id, role="member", member_status="active", account_status="active",
          override=None, prefs=None):
    doc = {"email": email, "name": email.split("@")[0], "password_hash": hash_password(PASSWORD),
           "status": account_status, "is_platform_admin": False, "platform_role": None,
           "default_organization_id": org_id}
    if prefs is not None:
        doc["notification_preferences"] = prefs
    uid = str(db.users.insert_one(doc).inserted_id)
    db.organization_members.insert_one({
        "organization_id": org_id, "user_id": uid, "role": role, "status": member_status,
        "permissions_override": override or {}, "created_at": datetime.utcnow()})
    return uid


def _cookie(db, uid, org_id):
    u = db.users.find_one({"_id": ObjectId(uid)})
    role = db.organization_members.find_one({"user_id": uid, "organization_id": org_id})["role"]
    tracked = create_tracked_session({"user_id": uid, "email": u["email"], "name": u["name"],
                                      "scope": "site", "organization_id": org_id, "org_role": role})
    return {COOKIE_NAME: build_session_value(tracked)}


def _lead(db, org_id, owner_uid, **extra):
    doc = {"organization_id": org_id, "user_id": owner_uid, "created_by": "x@x.test",
           "is_lead": True, "lead_score": 70, "commenter_name": "Buyer", "comment_text": "price?",
           "platform": "facebook", "search_run_id": extra.pop("run_id", "RUN1"),
           "comment_ref": str(ObjectId()), "analyzed_at": datetime.utcnow()}
    doc.update(extra)
    return db.ai_comments.insert_one(doc).inserted_id


def _set_mode(db, org_id, mode, **more):
    db.organizations.update_one({"_id": ObjectId(org_id)},
                                {"$set": {"settings.lead_assignment": mode,
                                          **{f"settings.{k}": v for k, v in more.items()}}})


# ═══════════════════════════════════════════════════════════════════════════
# 12. Automatic lead assignment
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def team(sdb):
    db = sdb
    org_a = _org(db, "Org A")
    org_b = _org(db, "Org B")
    ids = {
        "owner": _user(db, "owner@a.test", org_a, "owner"),
        "m1": _user(db, "m1@a.test", org_a, "member"),
        "m2": _user(db, "m2@a.test", org_a, "manager"),
        "viewer": _user(db, "viewer@a.test", org_a, "viewer"),
        "suspended": _user(db, "susp@a.test", org_a, "member", member_status="suspended"),
        "inactive": _user(db, "inactive@a.test", org_a, "member", member_status="inactive"),
        "disabled": _user(db, "disabled@a.test", org_a, "member", account_status="disabled"),
        "noleads": _user(db, "noleads@a.test", org_a, "member", override={"leads.view": False}),
        "b_owner": _user(db, "owner@b.test", org_b, "owner"),
        "b_member": _user(db, "member@b.test", org_b, "member"),
    }
    return db, org_a, org_b, ids


class TestAutoAssignment:
    def test_round_robin_distributes_evenly_and_skips_ineligible(self, team):
        from app.pipeline.lead_assignment import auto_assign_lead, eligible_assignees
        db, org_a, org_b, ids = team
        _set_mode(db, org_a, "round_robin")
        eligible = {c["user_id"] for c in eligible_assignees(db, org_a)}
        assert eligible == {ids["owner"], ids["m1"], ids["m2"]}
        leads = [_lead(db, org_a, ids["m1"]) for _ in range(9)]
        results = [auto_assign_lead(db, lid) for lid in leads]
        assert all(results)
        counts = {}
        for lid in leads:
            d = db.ai_comments.find_one({"_id": lid})
            counts[d["assigned_user_id"]] = counts.get(d["assigned_user_id"], 0) + 1
        assert counts == {ids["owner"]: 3, ids["m1"]: 3, ids["m2"]: 3}
        for bad in ("viewer", "suspended", "inactive", "disabled", "noleads", "b_owner", "b_member"):
            assert ids[bad] not in counts

    def test_round_robin_rotation_is_consecutive(self, team):
        from app.pipeline.lead_assignment import auto_assign_lead
        db, org_a, _, ids = team
        _set_mode(db, org_a, "round_robin")
        order = sorted([ids["owner"], ids["m1"], ids["m2"]])
        got = [auto_assign_lead(db, _lead(db, org_a, ids["m1"]))["user_id"] for _ in range(6)]
        assert got == order + order

    def test_sets_same_fields_as_manual_endpoint_history_audit_and_notification(self, team):
        from app.pipeline.lead_assignment import auto_assign_lead
        db, org_a, _, ids = team
        _set_mode(db, org_a, "creator")
        lid = _lead(db, org_a, ids["m1"])
        res = auto_assign_lead(db, lid)
        assert res == {"user_id": ids["m1"], "email": "m1@a.test", "mode": "creator"}
        d = db.ai_comments.find_one({"_id": lid})
        assert d["assigned_user_id"] == ids["m1"] and d["assigned_to"] == "m1@a.test"
        assert d["lead_updated_at"]
        hist = d["assignment_history"]
        assert len(hist) == 1 and hist[0]["to_user_id"] == ids["m1"]
        assert hist[0]["method"] == "auto:creator" and hist[0]["changed_by"] == "system"
        a = db.audit_logs.find_one({"action": "leads.auto_assigned"})
        assert a and a["organization_id"] == org_a and a["resource_id"] == str(lid)
        n = db.notifications.find_one({"type": "lead_assigned", "user_id": ids["m1"]})
        assert n and n["audience"] == "user" and n["data"]["lead_id"] == str(lid)

    def test_search_owner_alias_and_ineligible_owner(self, team):
        from app.pipeline.lead_assignment import auto_assign_lead
        db, org_a, _, ids = team
        _set_mode(db, org_a, "search_owner")
        assert auto_assign_lead(db, _lead(db, org_a, ids["m2"]))["user_id"] == ids["m2"]
        # a suspended owner / viewer is never auto-assigned; lead stays unassigned
        for who in ("suspended", "viewer"):
            lid = _lead(db, org_a, ids[who])
            assert auto_assign_lead(db, lid) is None
            assert not db.ai_comments.find_one({"_id": lid}).get("assigned_user_id")

    def test_manual_does_nothing(self, team):
        from app.pipeline.lead_assignment import auto_assign_lead
        db, org_a, _, ids = team
        for mode in (None, "manual"):
            if mode:
                _set_mode(db, org_a, mode)
            lid = _lead(db, org_a, ids["m1"])
            assert auto_assign_lead(db, lid) is None
            d = db.ai_comments.find_one({"_id": lid})
            assert "assigned_user_id" not in d and "assignment_history" not in d
        assert db.notifications.count_documents({"type": "lead_assigned"}) == 0
        assert db.lead_assignment_cursors.count_documents({}) == 0

    def test_never_assigns_across_organizations(self, team):
        from app.pipeline.lead_assignment import auto_assign_lead
        db, org_a, org_b, ids = team
        # search owner belongs to Org B but the lead is Org A's -> impossible
        _set_mode(db, org_a, "creator")
        lid = _lead(db, org_a, ids["b_member"])
        assert auto_assign_lead(db, lid) is None
        assert not db.ai_comments.find_one({"_id": lid}).get("assigned_user_id")
        # round robin in Org B only ever picks Org B members
        _set_mode(db, org_b, "round_robin")
        picked = {auto_assign_lead(db, _lead(db, org_b, ids["b_member"]))["user_id"] for _ in range(6)}
        assert picked == {ids["b_owner"], ids["b_member"]}
        # separate cursors per organization
        assert db.lead_assignment_cursors.find_one({"_id": org_b})["seq"] == 6
        assert db.lead_assignment_cursors.find_one({"_id": org_a}) is None

    def test_already_assigned_or_previously_auto_assigned_is_untouched(self, team):
        from app.pipeline.lead_assignment import auto_assign_lead
        db, org_a, _, ids = team
        _set_mode(db, org_a, "round_robin")
        lid = _lead(db, org_a, ids["m1"], assigned_user_id=ids["m2"], assigned_to="m2@a.test")
        assert auto_assign_lead(db, lid) is None
        assert db.ai_comments.find_one({"_id": lid})["assigned_user_id"] == ids["m2"]
        # an admin unassigned an auto-assigned lead: re-analysis must not re-assign it
        lid2 = _lead(db, org_a, ids["m1"])
        assert auto_assign_lead(db, lid2)
        db.ai_comments.update_one({"_id": lid2}, {"$set": {"assigned_user_id": None}})
        assert auto_assign_lead(db, lid2) is None

    def test_cursor_is_atomic_under_concurrency(self, team):
        from app.pipeline.lead_assignment import next_cursor
        db, org_a, _, _ = team
        seen, lock = [], threading.Lock()

        def work():
            for _ in range(10):
                v = next_cursor(db, org_a)
                with lock:
                    seen.append(v)
        threads = [threading.Thread(target=work) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert sorted(seen) == list(range(1, 81))  # every caller got its own slot

    def test_concurrent_assignment_of_one_lead_happens_once(self, team):
        from app.pipeline.lead_assignment import auto_assign_lead
        db, org_a, _, ids = team
        _set_mode(db, org_a, "round_robin")
        lid = _lead(db, org_a, ids["m1"])
        doc = db.ai_comments.find_one({"_id": lid})
        results = []
        threads = [threading.Thread(target=lambda: results.append(auto_assign_lead(db, dict(doc))))
                   for _ in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert len([r for r in results if r]) == 1
        d = db.ai_comments.find_one({"_id": lid})
        assert len(d["assignment_history"]) == 1
        assert db.notifications.count_documents({"type": "lead_assigned"}) == 1

    def test_notification_respects_user_and_org_preferences(self, team):
        from app.pipeline.lead_assignment import auto_assign_lead
        db, org_a, _, ids = team
        _set_mode(db, org_a, "creator")
        db.users.update_one({"_id": ObjectId(ids["m1"])},
                            {"$set": {"notification_preferences": {"lead_assigned": False}}})
        assert auto_assign_lead(db, _lead(db, org_a, ids["m1"]))  # still assigned
        assert db.notifications.count_documents({"user_id": ids["m1"]}) == 0
        _set_mode(db, org_a, "creator", notify_lead_assigned=False)
        assert auto_assign_lead(db, _lead(db, org_a, ids["m2"]))
        assert db.notifications.count_documents({"user_id": ids["m2"]}) == 0
        _set_mode(db, org_a, "creator", notify_lead_assigned=True)
        assert auto_assign_lead(db, _lead(db, org_a, ids["m2"]))
        assert db.notifications.count_documents({"user_id": ids["m2"], "type": "lead_assigned"}) == 1
        # org email on (default) + personal email on -> email queued too
        assert db.email_outbox.count_documents({"to": "m2@a.test",
                                                "kind": "notification.lead_assigned"}) == 1

    def test_pipeline_hook_assigns_new_leads(self, team):
        """analyze_comments_for_post (where is_lead is set) runs the assignment."""
        from app.pipeline import comment_ai
        db, org_a, _, ids = team
        _set_mode(db, org_a, "round_robin")
        common = {"organization_id": org_a, "user_id": ids["m1"], "created_by": "m1@a.test",
                  "search_run_id": "RUNX"}
        post_id = str(db.facebook_posts.insert_one({**common, "platform": "facebook",
                                                    "caption": "flat"}).inserted_id)
        for i in range(3):
            db.facebook_comments.insert_one({**common, "post_ref": post_id, "text": f"price {i}?",
                                             "author_name": f"A{i}", "published_date": i})
        flat = {"phone": None, "email": None, "whatsapp": None, "website": None, "budget": None,
                "requirement": None, "location": None, "intent": "buying", "urgency": None,
                "priority": "high", "lead_quality": "hot", "confidence": 0.9, "lead_score": 90,
                "signal_score": 90, "is_lead": True, "reason": "test"}
        with patch.object(comment_ai, "analyze_comment_ai", return_value={"analyzed_by": "rules"}), \
                patch.object(comment_ai, "_flat_extract", return_value=flat), \
                patch.object(comment_ai, "_ai_entitlement", return_value=(False, "test")), \
                patch("app.admin.settings.get_int_cached", return_value=0):
            summary = comment_ai.analyze_comments_for_post(post_id)
        assert summary["leads_created"] == 3 and summary.get("auto_assigned") == 3
        assignees = {d["assigned_user_id"] for d in db.ai_comments.find({"post_ref": post_id})}
        assert assignees == {ids["owner"], ids["m1"], ids["m2"]}
        # re-analysis does not reassign
        before = {str(d["_id"]): d["assigned_user_id"] for d in db.ai_comments.find()}
        with patch.object(comment_ai, "analyze_comment_ai", return_value={"analyzed_by": "rules"}), \
                patch.object(comment_ai, "_flat_extract", return_value=flat), \
                patch.object(comment_ai, "_ai_entitlement", return_value=(False, "test")), \
                patch("app.admin.settings.get_int_cached", return_value=0):
            comment_ai.analyze_comments_for_post(post_id)
        assert before == {str(d["_id"]): d["assigned_user_id"] for d in db.ai_comments.find()}

    def test_setting_accepts_search_owner(self, env):
        client, db = env
        org = _org(db, "Org S")
        owner = _user(db, "owner@s.test", org, "owner")
        for mode in ("manual", "round_robin", "creator", "search_owner"):
            r = client.patch("/api/organizations/current", cookies=_cookie(db, owner, org),
                             json={"settings": {"lead_assignment": mode}})
            assert r.status_code == 200, r.text
        r = client.patch("/api/organizations/current", cookies=_cookie(db, owner, org),
                         json={"settings": {"lead_assignment": "everyone"}})
        assert r.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════
# 13. Org notification settings
# ═══════════════════════════════════════════════════════════════════════════

class TestOrgNotificationSettings:
    def test_org_admin_types_follow_settings(self, team):
        from app.events.notifications import notify_org_admins
        db, org_a, _, _ = team
        cases = [("job_completed", "notify_job_completion", "info"),
                 ("job_failed", "notify_job_completion", "danger"),
                 ("usage_threshold", "notify_usage_warnings", "warning"),
                 ("leads_found", "notify_on_leads", "success")]
        for ntype, key, sev in cases:
            db.notifications.delete_many({})
            _set_mode(db, org_a, "manual", **{key: False})
            assert notify_org_admins(org_a, ntype, "t", severity=sev) is False
            assert db.notifications.count_documents({}) == 0, ntype
            _set_mode(db, org_a, "manual", **{key: True})
            assert notify_org_admins(org_a, ntype, "t", severity=sev) is True
            assert db.notifications.count_documents({"type": ntype}) == 1, ntype

    def test_defaults_match_the_ui(self, team):
        from app.events.notifications import org_allows
        db, org_a, _, _ = team
        assert org_allows(org_a, "job_completed") is True
        assert org_allows(org_a, "usage_threshold") is True
        assert org_allows(org_a, "lead_assigned") is True
        assert org_allows(org_a, "leads_found") is False  # "New leads" toggle defaults off

    def test_critical_types_always_delivered(self, team):
        from app.events.notifications import notify_org_admins
        db, org_a, _, _ = team
        _set_mode(db, org_a, "manual", notify_job_completion=False, notify_usage_warnings=False,
                  notify_on_leads=False, email_notifications=False)
        for ntype in ("payment_status", "subscription_activated", "user_suspended",
                      "security_event", "support_reply"):
            assert notify_org_admins(org_a, ntype, "t") is True
        # the limit actually reached (danger) is billing-critical
        assert notify_org_admins(org_a, "usage_threshold", "100%", severity="danger") is True
        assert notify_org_admins(org_a, "usage_threshold", "80%", severity="warning") is False

    def test_org_email_respects_email_notifications_and_personal_prefs(self, team):
        from app.events.notifications import notify_org_admins
        db, org_a, _, ids = team
        admin2 = _user(db, "admin2@a.test", org_a, "admin",
                       prefs={"email_notifications": False})
        notify_org_admins(org_a, "job_completed", "Done", "ok", email=True)
        to = {e["to"] for e in db.email_outbox.find()}
        assert to == {"owner@a.test"}  # admin2 opted out, members/managers never
        assert admin2
        db.email_outbox.delete_many({})
        _set_mode(db, org_a, "manual", email_notifications=False)
        assert notify_org_admins(org_a, "job_completed", "Done", "ok", email=True) is True
        assert db.email_outbox.count_documents({}) == 0  # in-app still delivered
        assert db.notifications.count_documents({"type": "job_completed"}) == 2

    def test_search_finished_helper(self, team):
        from app.events.notifications import notify_search_finished
        db, org_a, org_b, ids = team
        owner = {"organization_id": org_a, "user_id": ids["m1"], "created_by": "m1@a.test"}
        _lead(db, org_a, ids["m1"], run_id="RUNOK")
        _lead(db, org_b, ids["b_member"], run_id="RUNOK")  # other org, same run id: not counted
        _set_mode(db, org_a, "manual", notify_on_leads=True)
        notify_search_finished(owner, "RUNOK", success=True, platform="facebook", posts=3, comments=9)
        done = db.notifications.find_one({"type": "job_completed"})
        assert done["audience"] == "org_admin" and done["organization_id"] == org_a
        leads = db.notifications.find_one({"type": "leads_found"})
        assert leads["data"]["leads"] == 1
        notify_search_finished(owner, "RUNBAD", success=False, platform="facebook", error="boom")
        assert db.notifications.find_one({"type": "job_failed"})["severity"] == "danger"
        db.notifications.delete_many({})
        _set_mode(db, org_a, "manual", notify_job_completion=False, notify_on_leads=False)
        notify_search_finished(owner, "RUNOK", success=True, platform="facebook")
        notify_search_finished(owner, "RUNBAD", success=False, error="boom")
        assert db.notifications.count_documents({}) == 0
        notify_search_finished({}, "RUNOK", success=True)  # no org -> no-op, no error


# ═══════════════════════════════════════════════════════════════════════════
# 14 + 15. HTTP: bulk actions, client export log
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def world(env):
    client, db = env
    org_a, org_b = _org(db, "Org A"), _org(db, "Org B")
    ids = {
        "a_owner": _user(db, "owner@a.test", org_a, "owner"),
        "a_admin": _user(db, "admin@a.test", org_a, "admin"),
        "a_admin2": _user(db, "admin2@a.test", org_a, "admin"),
        "a_user": _user(db, "user@a.test", org_a, "member"),
        "a_user2": _user(db, "user2@a.test", org_a, "member"),
        "a_viewer": _user(db, "viewer@a.test", org_a, "viewer"),
        "b_owner": _user(db, "owner@b.test", org_b, "owner"),
        "b_user": _user(db, "user@b.test", org_b, "member"),
    }
    orgs = {"a": org_a, "b": org_b}

    def cookie(key):
        return _cookie(db, ids[key], orgs[key.split("_")[0]])
    return client, db, ids, orgs, cookie


def _sec(db, etype):
    return db.security_events.count_documents({"event_type": etype}) + \
        db.security_events.count_documents({"type": etype})


class TestLeadBulk:
    URL = "/api/org-admin/leads/bulk"

    def test_assign_many_atomic_one_audit_history_notification(self, world):
        client, db, ids, orgs, cookie = world
        l1, l2 = _lead(db, orgs["a"], ids["a_user"]), _lead(db, orgs["a"], ids["a_user"])
        l3 = _lead(db, orgs["a"], ids["a_user"], assigned_user_id=ids["a_user2"],
                   assigned_to="user2@a.test")
        r = client.post(self.URL, cookies=cookie("a_admin"),
                        json={"ids": [str(l1), str(l2), str(l3)], "action": "assign",
                              "value": ids["a_user2"]})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["updated"] == 2 and sorted(body["updated_ids"]) == sorted([str(l1), str(l2)])
        assert body["skipped"] == [{"id": str(l3), "reason": "Already assigned to this member"}]
        for lid in (l1, l2):
            d = db.ai_comments.find_one({"_id": lid})
            assert d["assigned_user_id"] == ids["a_user2"] and d["assigned_to"] == "user2@a.test"
            assert d["assignment_history"][-1]["method"] == "bulk"
        audits = list(db.audit_logs.find({"action": "leads.bulk_updated"}))
        assert len(audits) == 1
        assert sorted(audits[0]["details"]["ids"]) == sorted([str(l1), str(l2), str(l3)])
        assert db.audit_logs.count_documents({"action": {"$in": ["leads.assigned", "leads.updated"]}}) == 0
        n = db.notifications.find_one({"type": "lead_assigned", "user_id": ids["a_user2"]})
        assert n and "2 leads" in n["title"]

    def test_unassign(self, world):
        client, db, ids, orgs, cookie = world
        l1 = _lead(db, orgs["a"], ids["a_user"], assigned_user_id=ids["a_user2"], assigned_to="user2@a.test")
        r = client.post(self.URL, cookies=cookie("a_admin"),
                        json={"ids": [str(l1)], "action": "assign", "value": None})
        assert r.status_code == 200 and r.json()["updated"] == 1
        d = db.ai_comments.find_one({"_id": l1})
        assert d["assigned_user_id"] is None and d["assigned_to"] is None

    def test_foreign_id_rejects_whole_request(self, world):
        client, db, ids, orgs, cookie = world
        mine = _lead(db, orgs["a"], ids["a_user"])
        theirs = _lead(db, orgs["b"], ids["b_user"])
        for action, value in (("assign", ids["a_user2"]), ("status", "contacted"), ("priority", "high")):
            r = client.post(self.URL, cookies=cookie("a_admin"),
                            json={"ids": [str(mine), str(theirs)], "action": action, "value": value})
            assert r.status_code == 404, (action, r.text)
        for lid in (mine, theirs):
            d = db.ai_comments.find_one({"_id": lid})
            assert not d.get("assigned_user_id") and not d.get("lead_status") and not d.get("lead_priority")
        assert _sec(db, "cross_tenant_access") >= 3
        assert db.audit_logs.count_documents({"action": "leads.bulk_updated"}) == 0
        # a non-existent id is also a 404 for everything
        r = client.post(self.URL, cookies=cookie("a_admin"),
                        json={"ids": [str(mine), str(ObjectId())], "action": "priority", "value": "high"})
        assert r.status_code == 404
        assert client.post(self.URL, cookies=cookie("a_admin"),
                           json={"ids": ["nope"], "action": "priority", "value": "high"}).status_code == 400

    def test_assignee_from_other_org_or_inactive_is_rejected(self, world):
        client, db, ids, orgs, cookie = world
        lid = _lead(db, orgs["a"], ids["a_user"])
        db.organization_members.update_one({"user_id": ids["a_user2"]}, {"$set": {"status": "suspended"}})
        for bad in (ids["b_user"], ids["a_user2"]):
            r = client.post(self.URL, cookies=cookie("a_admin"),
                            json={"ids": [str(lid)], "action": "assign", "value": bad})
            assert r.status_code == 400
        assert not db.ai_comments.find_one({"_id": lid}).get("assigned_user_id")

    def test_status_transitions_validated_per_item(self, world):
        client, db, ids, orgs, cookie = world
        new = _lead(db, orgs["a"], ids["a_user"])  # no lead_status -> "new"
        qual = _lead(db, orgs["a"], ids["a_user"], lead_status="qualified")
        conv = _lead(db, orgs["a"], ids["a_user"], lead_status="converted")
        already = _lead(db, orgs["a"], ids["a_user"], lead_status="contacted")
        r = client.post(self.URL, cookies=cookie("a_owner"),
                        json={"ids": [str(new), str(qual), str(conv), str(already)],
                              "action": "status", "value": "contacted", "reason": "called"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["updated_ids"] == [str(new)]
        reasons = {s["id"]: s["reason"] for s in body["skipped"]}
        assert reasons[str(qual)] == "Cannot move from qualified to contacted"
        assert reasons[str(conv)] == "Cannot move from converted to contacted"
        assert reasons[str(already)] == "Already contacted"
        d = db.ai_comments.find_one({"_id": new})
        assert d["lead_status"] == "contacted"
        h = d["status_history"][-1]
        assert h["from_status"] == "new" and h["to_status"] == "contacted" and h["reason"] == "called"
        assert db.ai_comments.find_one({"_id": conv})["lead_status"] == "converted"
        assert db.audit_logs.count_documents({"action": "leads.bulk_updated"}) == 1
        assert client.post(self.URL, cookies=cookie("a_owner"),
                           json={"ids": [str(new)], "action": "status", "value": "bogus"}).status_code == 422

    def test_priority(self, world):
        client, db, ids, orgs, cookie = world
        a = _lead(db, orgs["a"], ids["a_user"])
        b = _lead(db, orgs["a"], ids["a_user"], lead_priority="high")
        r = client.post(self.URL, cookies=cookie("a_admin"),
                        json={"ids": [str(a), str(b)], "action": "priority", "value": "high"})
        assert r.status_code == 200 and r.json()["updated_ids"] == [str(a)]
        assert db.ai_comments.find_one({"_id": a})["lead_priority"] == "high"
        assert client.post(self.URL, cookies=cookie("a_admin"),
                           json={"ids": [str(a)], "action": "priority", "value": "urgent"}).status_code == 422

    def test_non_admins_and_other_org_forbidden(self, world):
        client, db, ids, orgs, cookie = world
        lid = _lead(db, orgs["a"], ids["a_user"])
        for who in ("a_user", "a_viewer"):
            r = client.post(self.URL, cookies=cookie(who),
                            json={"ids": [str(lid)], "action": "priority", "value": "low"})
            assert r.status_code == 403
        r = client.post(self.URL, cookies=cookie("b_owner"),
                        json={"ids": [str(lid)], "action": "priority", "value": "low"})
        assert r.status_code == 404
        assert "lead_priority" not in db.ai_comments.find_one({"_id": lid})
        assert client.post(self.URL, json={"ids": [str(lid)], "action": "priority",
                                           "value": "low"}).status_code in (401, 403)

    def test_bad_requests(self, world):
        client, db, ids, orgs, cookie = world
        lid = _lead(db, orgs["a"], ids["a_user"])
        assert client.post(self.URL, cookies=cookie("a_admin"),
                           json={"ids": [], "action": "priority", "value": "low"}).status_code == 422
        assert client.post(self.URL, cookies=cookie("a_admin"),
                           json={"ids": [str(lid)], "action": "delete"}).status_code == 422
        assert client.post(self.URL, cookies=cookie("a_admin"),
                           json={"ids": [str(ObjectId()) for _ in range(501)], "action": "priority",
                                 "value": "low"}).status_code == 422


class TestMemberBulk:
    URL = "/api/org-admin/members/bulk"

    def test_suspend_many_one_audit_rules_enforced(self, world):
        client, db, ids, orgs, cookie = world
        want = [ids["a_user"], ids["a_viewer"], ids["a_owner"], ids["a_admin"], ids["a_admin2"]]
        r = client.post(self.URL, cookies=cookie("a_admin"), json={"ids": want, "action": "suspend"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert sorted(body["updated_ids"]) == sorted([ids["a_user"], ids["a_viewer"]])
        reasons = {s["id"]: s["reason"] for s in body["skipped"]}
        assert "owner" in reasons[ids["a_owner"]].lower()
        assert "own" in reasons[ids["a_admin"]].lower()          # yourself
        assert "admin" in reasons[ids["a_admin2"]].lower()       # admin can't modify an admin
        st = {m["user_id"]: m["status"] for m in db.organization_members.find()}
        assert st[ids["a_user"]] == st[ids["a_viewer"]] == "suspended"
        assert st[ids["a_owner"]] == st[ids["a_admin"]] == st[ids["a_admin2"]] == "active"
        audits = list(db.audit_logs.find({"action": "member.bulk_updated"}))
        assert len(audits) == 1 and sorted(audits[0]["details"]["ids"]) == sorted(want)
        assert db.audit_logs.count_documents({"action": "member.updated"}) == 0
        assert db.notifications.count_documents({"type": "user_suspended"}) == 1

    def test_owner_can_manage_admins_and_restore(self, world):
        client, db, ids, orgs, cookie = world
        r = client.post(self.URL, cookies=cookie("a_owner"),
                        json={"ids": [ids["a_admin2"], ids["a_user"]], "action": "deactivate"})
        assert r.status_code == 200 and r.json()["updated"] == 2
        _grant_seats(db, orgs["a"], 50)  # a real plan credit, not a mocked check
        r = client.post(self.URL, cookies=cookie("a_owner"),
                        json={"ids": [ids["a_admin2"], ids["a_user"], ids["a_user2"]],
                              "action": "restore"})
        assert r.status_code == 200
        assert r.json()["updated"] == 2
        assert r.json()["skipped"] == [{"id": ids["a_user2"], "reason": "Already active"}]
        assert all(m["status"] == "active" for m in db.organization_members.find({"organization_id": orgs["a"]}))

    def test_foreign_id_rejects_whole_request(self, world):
        client, db, ids, orgs, cookie = world
        r = client.post(self.URL, cookies=cookie("a_admin"),
                        json={"ids": [ids["a_user"], ids["b_user"]], "action": "suspend"})
        assert r.status_code == 404
        assert db.organization_members.find_one({"user_id": ids["a_user"]})["status"] == "active"
        assert db.organization_members.find_one({"user_id": ids["b_user"]})["status"] == "active"
        assert _sec(db, "cross_tenant_access") >= 1
        assert db.audit_logs.count_documents({"action": "member.bulk_updated"}) == 0
        r = client.post(self.URL, cookies=cookie("b_owner"),
                        json={"ids": [ids["a_user"]], "action": "suspend"})
        assert r.status_code == 404

    def test_permissions_and_validation(self, world):
        client, db, ids, orgs, cookie = world
        for who in ("a_user", "a_viewer"):
            assert client.post(self.URL, cookies=cookie(who),
                               json={"ids": [ids["a_user2"]], "action": "suspend"}).status_code == 403
        assert client.post(self.URL, cookies=cookie("a_admin"),
                           json={"ids": [ids["a_user2"]], "action": "remove"}).status_code == 422
        assert client.post(self.URL, cookies=cookie("a_admin"),
                           json={"ids": [], "action": "suspend"}).status_code == 422

    def test_restore_respects_seat_limit(self, world):
        client, db, ids, orgs, cookie = world
        db.organization_members.update_many({"user_id": {"$in": [ids["a_user"], ids["a_user2"]]}},
                                            {"$set": {"status": "inactive"}})
        # the organization's real (default) plan has fewer seats than 4 active + 2 restored
        r = client.post(self.URL, cookies=cookie("a_owner"),
                        json={"ids": [ids["a_user"], ids["a_user2"]], "action": "restore"})
        assert r.status_code == 402, r.text
        d = r.json()["detail"]
        assert d["code"] == "PLAN_LIMIT" and d["used"] + 2 > d["limit"]
        assert {m["status"] for m in db.organization_members.find(
            {"user_id": {"$in": [ids["a_user"], ids["a_user2"]]}})} == {"inactive"}
        assert db.audit_logs.count_documents({"action": "member.bulk_updated"}) == 0
        # exactly enough seats for both -> allowed; one short -> still refused as a whole
        _grant_seats(db, orgs["a"], d["used"] + 1 - d["limit"])
        r = client.post(self.URL, cookies=cookie("a_owner"),
                        json={"ids": [ids["a_user"], ids["a_user2"]], "action": "restore"})
        assert r.status_code == 402
        _grant_seats(db, orgs["a"], 1)
        r = client.post(self.URL, cookies=cookie("a_owner"),
                        json={"ids": [ids["a_user"], ids["a_user2"]], "action": "restore"})
        assert r.status_code == 200 and r.json()["updated"] == 2


def _grant_seats(db, org_id, n):
    """Extra team seats through the platform's temporary entitlement credits."""
    db.temporary_entitlements.insert_one({"organization_id": org_id, "entitlement_type": "credit",
                                          "key": "team_members", "value": n, "expires_at": None})


class TestBillingCurrencyTotals:
    def test_paid_totals_grouped_by_currency_never_summed_across(self, world):
        client, db, ids, orgs, cookie = world
        for amt, cur, status in ((10, "usd", "paid"), (5.5, None, "succeeded"), (20, "EUR", "paid"),
                                 (99, "EUR", "failed")):
            db.payments.insert_one({"organization_id": orgs["a"], "amount": amt, "currency": cur,
                                    "status": status, "created_at": datetime.utcnow()})
        db.payments.insert_one({"organization_id": orgs["b"], "amount": 1000, "currency": "USD",
                                "status": "paid", "created_at": datetime.utcnow()})
        r = client.get("/api/org-admin/billing/history", cookies=cookie("a_owner"))
        assert r.status_code == 200
        assert r.json()["paid_totals"] == {"EUR": 20.0, "USD": 15.5}
