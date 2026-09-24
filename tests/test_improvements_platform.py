"""Improvements round — platform side (runs on the in-memory MongoDB from
tests/conftest.py, never a real database).

17a. Super Admin analytics: day / ISO-week (Monday, UTC) / month buckets,
     dense and zero-filled, boundaries correct.
17b. Money is never added across currencies: dashboard MRR / revenue,
     payments summary and the analytics revenue series group by currency;
     the historic single-number fields exist only for one currency.
18.  Durable rate limits: login-per-IP, signup, password-reset and contact
     form counters live in MongoDB (rate_limits, TTL indexed), survive a
     restart, are shared by several processes and fail safe when the
     database is down.
15.  Browser-built "Current view" exports are audited (export.client).
"""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.api.routes import super_admin_platform as sap
from app.auth import rate_limit as rl
from app.auth.crypto import hash_password
from app.auth.service import COOKIE_NAME, build_session_value, create_tracked_session

NOW = datetime.now(timezone.utc)


# ═══════════════════════════════════════════════════════════════════════════
# fixtures
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def env():
    with patch("app.admin.settings.is_maintenance_enabled", return_value=False):
        from app.main import app
        with TestClient(app) as client:
            from app.db.mongo import get_sync_db
            db = get_sync_db()
            for coll in ("organizations", "users", "organization_members", "subscriptions", "payments",
                         "user_sessions", "audit_logs"):
                db[coll].delete_many({})
            yield client, db


def _user(db, email, org_id=None, role="member", platform_role=None):
    uid = str(db.users.insert_one({
        "email": email, "name": email.split("@")[0], "password_hash": hash_password("Str0ngPass!"),
        "status": "active", "is_platform_admin": bool(platform_role), "platform_role": platform_role,
        "default_organization_id": org_id, "created_at": NOW}).inserted_id)
    if org_id:
        db.organization_members.insert_one({"organization_id": org_id, "user_id": uid, "role": role,
                                            "status": "active", "joined_at": NOW})
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


def _org_owner(db):
    org = str(db.organizations.insert_one({"name": "Acme", "slug": "acme", "status": "active",
                                           "created_at": NOW}).inserted_id)
    uid = _user(db, "owner@acme.test", org, "owner")
    return org, _cookie({"user_id": uid, "email": "owner@acme.test", "name": "o", "scope": "site",
                         "organization_id": org, "org_role": "owner"})


def _day(s):
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


# ═══════════════════════════════════════════════════════════════════════════
# 17a. bucket math
# ═══════════════════════════════════════════════════════════════════════════

class TestBucketMath:
    def test_week_key_is_iso_monday(self):
        # 2026-09-21 is a Monday, 2026-09-27 a Sunday
        assert sap.bucket_key("2026-09-21", "week") == "2026-09-21"
        assert sap.bucket_key("2026-09-27", "week") == "2026-09-21"
        assert sap.bucket_key("2026-09-28", "week") == "2026-09-28"
        # a week crossing a year boundary belongs to its Monday (Dec 29 2025)
        assert sap.bucket_key("2026-01-01", "week") == "2025-12-29"
        assert sap.bucket_key("2026-01-04", "week") == "2025-12-29"

    def test_month_key_and_day_key(self):
        assert sap.bucket_key("2026-02-28", "month") == "2026-02"
        assert sap.bucket_key("2026-03-01", "month") == "2026-03"
        assert sap.bucket_key("2026-03-01", "day") == "2026-03-01"

    def test_labels_cover_partial_edges_and_are_dense(self):
        days = sap._days(_day("2026-01-28"), _day("2026-03-03"))   # Wed Jan 28 .. Mon Mar 2
        assert sap.bucket_labels(days, "month") == ["2026-01", "2026-02", "2026-03"]
        weeks = sap.bucket_labels(days, "week")
        assert weeks[0] == "2026-01-26" and weeks[-1] == "2026-03-02"
        assert len(weeks) == 6
        assert all(_day(b) - _day(a) == timedelta(days=7) for a, b in zip(weeks, weeks[1:]))
        assert sap.bucket_labels(days, "day") == days

    def test_rollup_zero_fills_empty_buckets(self):
        days = sap._days(_day("2026-01-26"), _day("2026-02-23"))   # four full ISO weeks
        by_day = {"2026-01-26": 2, "2026-02-01": 3,   # week 1 (Mon..Sun)
                  "2026-02-16": 5}                    # week 4; weeks 2 and 3 empty
        out = sap.rollup(by_day, days, "week")
        assert out == {"2026-01-26": 5, "2026-02-02": 0, "2026-02-09": 0, "2026-02-16": 5}
        months = sap.rollup(by_day, days, "month")
        assert months == {"2026-01": 2, "2026-02": 8}

    def test_currency_grouping_helpers(self):
        t = sap.currency_totals([{"currency": "usd", "amount": 10}, {"currency": "EUR", "amount": 5.5},
                                 {"amount": 2}, {"currency": "USD", "amount": "x"}])
        assert t == {"EUR": 5.5, "USD": 12.0}          # missing currency -> USD, bad amounts -> 0
        assert sap.single_amount(t) is None and sap.single_currency(t) is None
        assert sap.single_amount({"INR": 7}) == 7 and sap.single_currency({"INR": 7}) == "INR"
        assert sap.single_amount({}) == 0.0 and sap.single_currency({}) == "USD"


class TestAnalyticsGranularity:
    def test_week_and_month_buckets_via_api(self, env):
        client, db = env
        c = _super(db)
        # Mon 2026-02-02 and Sun 2026-02-08 are the same ISO week; Mon 02-09 the next
        db.search_history.insert_many([
            {"run_id": "a", "user_id": "u1", "status": "completed", "created_at": _day("2026-02-02")},
            {"run_id": "b", "user_id": "u2", "status": "completed",
             "created_at": _day("2026-02-08") + timedelta(hours=23, minutes=59)},
            {"run_id": "c", "user_id": "u1", "status": "completed", "created_at": _day("2026-02-09")},
            {"run_id": "d", "user_id": "u3", "status": "completed", "created_at": _day("2026-03-31")},
        ])
        base = "/api/super-admin/analytics?from=2026-02-01&to=2026-03-31"
        w = client.get(base + "&granularity=week", cookies=c).json()
        assert w["granularity"] == "week"
        assert w["buckets"][0] == "2026-01-26" and w["buckets"][-1] == "2026-03-30"
        vals = dict(zip(w["buckets"], w["product"]["searches"]["values"]))
        assert vals["2026-01-26"] == 0            # Feb 1 (Sunday) has no data
        assert vals["2026-02-02"] == 2 and vals["2026-02-09"] == 1
        assert vals["2026-03-30"] == 1
        assert vals["2026-02-16"] == 0 and vals["2026-03-02"] == 0   # zero-filled gaps
        assert w["product"]["searches"]["total"] == 4
        # distinct users: bucketed as sets, total = distinct over the whole range
        users = dict(zip(w["buckets"], w["product"]["active_users"]["values"]))
        assert users["2026-02-02"] == 2 and w["product"]["active_users"]["total"] == 3

        m = client.get(base + "&granularity=month", cookies=c).json()
        assert m["buckets"] == ["2026-02", "2026-03"]
        assert m["product"]["searches"]["values"] == [3, 1]

        d = client.get(base, cookies=c).json()     # default stays daily
        assert d["granularity"] == "day" and len(d["days"]) == 59
        assert client.get(base + "&granularity=year", cookies=c).status_code == 422

    def test_analytics_requires_super_admin(self, env):
        client, db = env
        _, owner = _org_owner(db)
        assert client.get("/api/super-admin/analytics?granularity=week", cookies=_staff(db)).status_code == 403
        assert client.get("/api/super-admin/analytics?granularity=week", cookies=owner).status_code in (401, 403)


# ═══════════════════════════════════════════════════════════════════════════
# 17b. multi-currency money
# ═══════════════════════════════════════════════════════════════════════════

def _seed_money(db, currencies=("USD", "EUR")):
    org = str(db.organizations.insert_one({"name": "M", "slug": "m", "status": "active",
                                           "created_at": NOW}).inserted_id)
    subs, pays = [], []
    for i, cur in enumerate(currencies):
        subs.append({"organization_id": org, "plan_id": "pro", "status": "active", "amount": 100.0 * (i + 1),
                     "billing_cycle": "monthly", "currency": cur, "created_at": NOW})
        subs.append({"organization_id": org, "plan_id": "pro", "status": "active", "amount": 1200.0,
                     "billing_cycle": "yearly", "currency": cur, "created_at": NOW})
        pays.append({"organization_id": org, "amount": 50.0 * (i + 1), "status": "succeeded",
                     "currency": cur, "created_at": NOW})
        pays.append({"organization_id": org, "amount": 7.0, "status": "pending", "currency": cur,
                     "created_at": NOW})
    db.subscriptions.insert_many(subs)
    db.payments.insert_many(pays)
    return org


class TestMultiCurrency:
    def test_dashboard_single_currency_keeps_flat_fields(self, env):
        client, db = env
        c = _super(db)
        _seed_money(db, ("USD",))
        d = client.get("/api/super-admin/dashboard", cookies=c).json()["dashboard"]
        assert d["subscriptions"]["mrr"] == 200.0                  # 100 + 1200/12
        assert d["subscriptions"]["mrr_by_currency"] == {"USD": 200.0}
        assert d["payments"]["revenue_30d"] == 50.0
        assert d["payments"]["succeeded_amount"] == 50.0
        assert d["payments"]["currency"] == "USD"
        assert d["payments"]["multi_currency"] is False

    def test_dashboard_never_adds_currencies(self, env):
        client, db = env
        c = _super(db)
        _seed_money(db, ("USD", "EUR"))
        d = client.get("/api/super-admin/dashboard", cookies=c).json()["dashboard"]
        s, p = d["subscriptions"], d["payments"]
        assert s["mrr"] is None and s["monthly_revenue"] is None and s["multi_currency"] is True
        assert s["mrr_by_currency"] == {"EUR": 300.0, "USD": 200.0}
        assert s["by_status"]["active"]["total_amount"] is None
        assert s["by_status"]["active"]["amount_by_currency"] == {"EUR": 1400.0, "USD": 1300.0}
        assert p["revenue_30d"] is None and p["succeeded_amount"] is None and p["currency"] is None
        assert p["revenue_30d_by_currency"] == {"EUR": 100.0, "USD": 50.0}
        assert p["by_status"]["succeeded"]["amount"] is None
        assert p["by_status"]["succeeded"]["amount_by_currency"] == {"EUR": 100.0, "USD": 50.0}
        assert p["by_status"]["succeeded"]["count"] == 2
        assert p["currencies"] == ["EUR", "USD"]

    def test_payments_summary_grouped_by_currency(self, env):
        client, db = env
        c = _super(db)
        _seed_money(db, ("USD", "INR"))
        r = client.get("/api/super-admin/payments", cookies=c).json()
        s = r["summary"]
        assert s["succeeded"]["count"] == 2 and s["succeeded"]["amount"] is None
        assert s["succeeded"]["amount_by_currency"] == {"INR": 100.0, "USD": 50.0}
        assert s["pending"]["amount_by_currency"] == {"INR": 7.0, "USD": 7.0}
        assert r["currencies"] == ["INR", "USD"]
        db.payments.delete_many({"currency": "INR"})
        s1 = client.get("/api/super-admin/payments", cookies=c).json()["summary"]
        assert s1["succeeded"]["amount"] == 50.0                     # one currency -> flat number

    def test_analytics_revenue_series_per_currency(self, env):
        client, db = env
        c = _super(db)
        _seed_money(db, ("USD", "EUR"))
        a = client.get("/api/super-admin/analytics?range=7d&granularity=week", cookies=c).json()
        rev = a["business"]["revenue"]
        assert rev["multi_currency"] is True and rev["total"] is None and rev["values"] is None
        assert rev["currencies"] == ["EUR", "USD"]
        assert rev["by_currency"]["USD"]["total"] == 50.0 and rev["by_currency"]["EUR"]["total"] == 100.0
        assert len(rev["by_currency"]["USD"]["values"]) == len(a["buckets"])
        db.payments.delete_many({"currency": "EUR"})
        one = client.get("/api/super-admin/analytics?range=7d", cookies=c).json()["business"]["revenue"]
        assert one["total"] == 50.0 and one["currency"] == "USD" and "multi_currency" not in one

    def test_dashboard_forbidden_for_non_super(self, env):
        client, db = env
        _, owner = _org_owner(db)
        assert client.get("/api/super-admin/dashboard", cookies=_staff(db)).status_code == 403
        assert client.get("/api/super-admin/dashboard", cookies=owner).status_code in (401, 403)


# ═══════════════════════════════════════════════════════════════════════════
# 18. durable rate limits
# ═══════════════════════════════════════════════════════════════════════════

class TestDurableRateLimits:
    def test_login_limit_is_stored_in_mongo_and_survives_restart(self):
        from app.auth import service
        from app.db.mongo import get_sync_db
        ip = "203.0.113.7"
        for _ in range(service._LOGIN_MAX_ATTEMPTS):
            service.record_login_failure(ip)
        assert service.login_allowed(ip) is False
        assert service.login_denied_seconds(ip) > 0
        doc = get_sync_db()["rate_limits"].find_one({"bucket": "login_ip", "key": ip})
        assert doc and doc["count"] == service._LOGIN_MAX_ATTEMPTS and doc["expires_at"]
        # "restart": the process loses all in-memory state
        service._login_attempts.clear()
        assert service.login_allowed(ip) is False
        # success resets the counter everywhere
        service.reset_login_attempts(ip)
        assert service.login_allowed(ip) is True

    def test_two_processes_share_the_counter(self):
        proc_a = rl.RateLimiter("shared_test", 3, 60)
        proc_b = rl.RateLimiter("shared_test", 3, 60)   # a second server process
        assert proc_a.consume("1.2.3.4") and proc_b.consume("1.2.3.4") and proc_a.consume("1.2.3.4")
        assert proc_b.consume("1.2.3.4") is False
        assert proc_a.consume("1.2.3.4") is False
        assert proc_a.count("1.2.3.4") == 3               # rejected hits are rolled back
        assert proc_b.consume("5.6.7.8") is True          # other keys unaffected

    def test_window_expiry(self):
        lim = rl.RateLimiter("expiry_test", 2, 100)
        t = [1_000_000.0]
        with patch.object(rl, "_now", lambda: t[0]):
            lim.hit("k"); lim.hit("k")
            assert lim.allowed("k") is False
            t[0] += 50
            assert lim.allowed("k") is False
            t[0] += 111                                    # window + one slot later
            assert lim.allowed("k") is True

    def test_signup_throttle_durable(self, env):
        client, db = env
        from app.api.routes import auth as auth_routes
        with patch("app.api.routes.auth._feature_enabled", return_value=True), \
                patch("app.lifecycle.demo.create_demo_request",
                      return_value={"status": "pending", "id": "x"}):
            codes = [client.post("/api/auth/signup", json={"email": f"s{i}@x.test", "password": "Str0ngPass!"}
                                 ).status_code for i in range(auth_routes._MAX_SIGNUP_ATTEMPTS)]
            assert 429 not in codes
            auth_routes._signup_attempts.clear()          # restart
            r = client.post("/api/auth/signup", json={"email": "late@x.test", "password": "Str0ngPass!"})
            assert r.status_code == 429
        assert db.rate_limits.count_documents({"bucket": "signup_ip"}) >= 1

    def test_password_reset_throttle_durable(self, env):
        client, db = env
        from app.api.routes import auth as auth_routes
        for i in range(auth_routes._MAX_RESET_ATTEMPTS):
            assert client.post("/api/auth/password/forgot", json={"email": f"r{i}@x.test"}).status_code == 200
        auth_routes._reset_attempts.clear()               # restart
        assert auth_routes._reset_attempts.consume("testclient") is False
        assert db.rate_limits.find_one({"bucket": "password_reset_ip"})["count"] >= 5

    def test_contact_form_limit_durable(self, env):
        client, db = env
        from app.api.routes import public_website
        body = {"name": "Ann", "email": "ann@example.com", "message": "Hello there, this is long enough"}
        for _ in range(public_website.CONTACT_MAX_PER_IP):
            assert client.post("/api/public/contact", json=body).status_code == 200
        public_website.reset_contact_rate_limit()         # restart
        r = client.post("/api/public/contact", json={**body, "email": "other@example.com"})
        assert r.status_code == 429                        # still limited per IP
        assert db.contact_submissions.count_documents({}) == public_website.CONTACT_MAX_PER_IP

    def test_ttl_index_exists(self):
        from app.db.mongo import ensure_indexes, get_sync_db
        ensure_indexes()
        info = get_sync_db()["rate_limits"].index_information()
        ttl = info["rate_limits_expires_at_ttl"]
        assert ttl["key"] == [("expires_at", 1)] and ttl["expireAfterSeconds"] == 0
        assert info["rate_limits_bucket_key_window"]["unique"] is True

    def test_fail_safe_when_db_down(self):
        class Broken:
            def __getitem__(self, k):
                raise RuntimeError("down")

        lim = rl.RateLimiter("failsafe_test", 2, 60)
        rl.reset_db_health()
        try:
            with patch("app.db.mongo.get_sync_db", return_value=Broken()):
                # never "everyone locked out"...
                assert lim.consume("a") is True and lim.consume("b") is True
                # ...and never "everyone unlimited": the in-memory window still applies
                assert lim.consume("a") is True
                assert lim.consume("a") is False
            with patch("app.db.mongo.get_sync_db", return_value=None):
                assert lim.allowed("a") is False and lim.allowed("zzz") is True
        finally:
            rl.reset_db_health()

    def test_db_errors_trigger_cooldown(self):
        calls = {"n": 0}

        class Coll:
            def __getattr__(self, name):
                def f(*a, **k):
                    calls["n"] += 1
                    raise RuntimeError("timeout")
                return f

        class DB:
            def __getitem__(self, k):
                return Coll()

        lim = rl.RateLimiter("cooldown_test", 5, 60)
        rl.reset_db_health()
        try:
            with patch("app.db.mongo.get_sync_db", return_value=DB()):
                lim.hit("x"); lim.hit("x"); lim.allowed("x")
            assert calls["n"] == 1        # after the first failure the DB is skipped for a while
            assert lim.count("x") == 2    # in-memory fallback kept counting
        finally:
            rl.reset_db_health()


# ═══════════════════════════════════════════════════════════════════════════
# 15. client-side export audit
# ═══════════════════════════════════════════════════════════════════════════

class TestClientExportLog:
    URL = "/api/super-admin/exports/client-log"

    def test_logs_audit_entry(self, env):
        client, db = env
        c = _super(db)
        r = client.post(self.URL, cookies=c, json={"table": "payments", "rows": 25,
                                                   "columns": ["Organization", "Amount"],
                                                   "filters": {"status": "succeeded", "q": "acme",
                                                               "nested": {"x": 1}}})
        assert r.status_code == 200 and r.json()["success"] is True
        doc = db.audit_logs.find_one({"action": "export.client"})
        assert doc is not None
        assert doc["actor_email"] == "root@platform.test"
        d = doc["details"]
        assert d["table"] == "payments" and d["rows"] == 25
        assert d["columns"] == ["Organization", "Amount"]
        assert d["filters"] == {"status": "succeeded", "q": "acme"}   # non-scalar filters dropped

    def test_validation(self, env):
        client, db = env
        c = _super(db)
        assert client.post(self.URL, cookies=c, json={"table": "", "rows": 1}).status_code == 422
        assert client.post(self.URL, cookies=c, json={"table": "x", "rows": -1}).status_code == 422
        assert client.post(self.URL, cookies=c, json={"table": "x", "rows": 1,
                                                      "columns": ["c"] * 201}).status_code == 422
        assert db.audit_logs.count_documents({"action": "export.client"}) == 0

    def test_super_admin_only(self, env):
        client, db = env
        _, owner = _org_owner(db)
        body = {"table": "payments", "rows": 1}
        assert client.post(self.URL, json=body).status_code == 401
        assert client.post(self.URL, cookies=_staff(db), json=body).status_code == 403
        assert client.post(self.URL, cookies=owner, json=body).status_code in (401, 403)
        assert db.audit_logs.count_documents({"action": "export.client"}) == 0

    def test_ui_calls_it_on_current_view_export(self):
        import pathlib
        js = pathlib.Path(__file__).resolve().parents[1].joinpath("app/static/super-admin.js").read_text(encoding="utf-8")
        assert "/api/super-admin/exports/client-log" in js
        assert js.count("logClientExport(") >= 2          # defined + called from the Current view export
