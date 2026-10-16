"""
API usage & limits monitor (app/limits): provider parsing (Apify, Gemini,
SMTP, Twilio, Fast2SMS), levels and thresholds, live error hooks, alert
audiences with dedupe / escalation / recovery, preference handling, plan
near-limit notices, the Super Admin / staff / org admin / user endpoints
(roles, tenant isolation, rate limits), the settings, and the Apify cost /
AI overview fixes. Every HTTP call is mocked at the network boundary.
"""
import os
import smtplib
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.auth.crypto import hash_password
from tests.conftest import TEST_SUPERADMIN_PASSWORD, as_superadmin

SUPER = "root@leadai.example"
PW = "Member-Pass-2026"


# ── fixtures & helpers ──────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _fresh_limits_state():
    from app.limits import service
    service.reset_hook_throttle()
    yield
    service.reset_hook_throttle()
    from app.pipeline import comment_ai
    comment_ai.reset_circuit_breaker()
    comment_ai._ORG_GEMINI_COOLDOWN.clear()


@pytest.fixture
def env():
    as_superadmin(SUPER)
    with patch("app.admin.settings.is_maintenance_enabled", return_value=False):
        from app.main import app
        with TestClient(app) as client:
            from app.db.mongo import get_sync_db
            from app.services import secret_box
            secret_box.reset_cache()
            yield client, get_sync_db()


@pytest.fixture
def db():
    as_superadmin(SUPER)
    from app.db.mongo import get_sync_db
    from app.services import secret_box
    secret_box.reset_cache()
    return get_sync_db()


def _env_vars(**values):
    from app.admin import envvars
    envvars._CACHE.clear()
    return patch.dict(os.environ, {k: str(v) for k, v in values.items()})


def _set(key, value):
    from app.admin.settings import set_setting
    set_setting(key, value, by="test")


def _org(db, name="Acme", *, apify="leadai", gemini="leadai", status="active", plan="starter",
         settings=None, own_apify_key=None, own_gemini_key=None):
    from app.services import secret_box
    keys = {}
    if own_apify_key:
        keys["apify"] = {"ciphertext": secret_box.encrypt(own_apify_key), "hint": "x", "verified": True}
    if own_gemini_key:
        keys["gemini"] = {"ciphertext": secret_box.encrypt(own_gemini_key), "hint": "x", "verified": True}
    return str(db.organizations.insert_one({
        "name": name, "slug": name.lower().replace(" ", "-"), "status": status, "plan_id": plan,
        "admin_portal_enabled": True, "settings": settings or {},
        "api_coverage": {"apify": apify, "gemini": gemini}, "custom_api_keys": keys,
        "created_at": datetime.now(timezone.utc)}).inserted_id)


def _user(db, email, org_id, role, prefs=None):
    uid = str(db.users.insert_one({"email": email, "name": email.split("@")[0], "status": "active",
                                   "password_hash": hash_password(PW), "default_organization_id": org_id,
                                   "notification_preferences": prefs or {},
                                   "created_at": datetime.now(timezone.utc)}).inserted_id)
    db.organization_members.insert_one({"organization_id": org_id, "user_id": uid, "email": email, "role": role,
                                        "status": "active", "permissions_override": {},
                                        "created_at": datetime.now(timezone.utc)})
    return uid


def _login(client, email, password, scope="site"):
    c = TestClient(client.app)
    r = c.post("/api/auth/login", json={"email": email, "password": password, "scope": scope})
    assert r.status_code == 200, r.text
    return c


def _resp(status=200, body=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body if body is not None else {}
    r.headers = {"content-type": "application/json"}
    return r


def _apify_body(used_usd, limit_usd=49.0, memory=(4, 32)):
    return {"data": {
        "monthlyUsageCycle": {"startAt": "2026-10-01T00:00:00.000Z", "endAt": "2026-10-31T23:59:59.999Z"},
        "limits": {"maxMonthlyUsageUsd": limit_usd, "maxMonthlyActorComputeUnits": 1000,
                   "maxActorMemoryGbytes": memory[1], "maxConcurrentActorJobs": 25},
        "current": {"monthlyUsageUsd": used_usd, "monthlyActorComputeUnits": 10,
                    "actorMemoryGbytes": memory[0], "activeActorJobCount": 1}}}


def _platform_keys(apify="apify_platform_tok", gemini="gemini_platform_key"):
    return patch("app.services.tenant_api_keys.platform_key",
                 side_effect=lambda p: {"apify": apify, "gemini": gemini}[p])


def _notes(db, **q):
    return list(db.notifications.find(q))


def _quiet_for(db, status_id, minutes):
    """Pretend the last non-ok state of ``status_id`` was ``minutes`` ago."""
    db.api_limit_status.update_one({"_id": status_id}, {"$set": {
        "last_bad_at": (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()}})


def _gemini_429(quota_id, retry="16s"):
    return {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded", "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
         "violations": [{"quotaMetric": "generativelanguage.googleapis.com/generate_content_requests",
                         "quotaId": quota_id}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry}]}}


# ═══════════════════════════════════════════════════════════════════════════
# Apify
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("used,level", [(10.0, "ok"), (40.0, "warning"), (47.0, "critical"), (49.0, "exhausted")])
def test_apify_limits_parsing_percent_level_and_reset(db, used, level):
    from app.limits.checkers import check_apify
    with patch("app.limits.checkers.httpx.get", return_value=_resp(200, _apify_body(used))) as get:
        st = check_apify("tok_123", "platform")
    assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer tok_123"
    assert get.call_args.args[0] == "https://api.apify.com/v2/users/me/limits"
    usage = next(m for m in st["metrics"] if m["key"] == "monthly_usage_usd")
    assert usage["used"] == used and usage["limit"] == 49 and usage["unit"] == "USD"
    assert usage["percent"] == round(used * 100 / 49, 1) and usage["source"] == "provider_api"
    assert usage["resets_at"].startswith("2026-10-31T23:59:59")
    assert st["level"] == level and st["period"] == "apify-cycle-2026-10" and st["configured"] is True
    assert st["help_link"] == "/superadmin#/provider-keys"


def test_apify_memory_full_is_informational(db):
    from app.limits.checkers import check_apify
    with patch("app.limits.checkers.httpx.get", return_value=_resp(200, _apify_body(5.0, memory=(32, 32)))):
        st = check_apify("tok", "platform")
    mem = next(m for m in st["metrics"] if m["key"] == "memory_gb")
    assert mem["percent"] == 100.0 and mem["informational"] is True
    assert st["level"] == "ok"


@pytest.mark.parametrize("status,body,level", [
    (401, {"error": {"type": "token-not-valid", "message": "Authentication token is not valid"}}, "error"),
    (402, {"error": {"type": "not-enough-usage-to-run-paid-actor", "message": "Not enough usage"}}, "exhausted"),
    (403, {"error": {"type": "limit-reached", "message": "Monthly usage limit reached"}}, "exhausted"),
    (429, {"error": {"type": "rate-limit-exceeded", "message": "Rate limit exceeded"}}, "unknown"),
    (503, {}, "unknown"),
])
def test_apify_error_classification(db, status, body, level):
    from app.limits.checkers import check_apify
    with patch("app.limits.checkers.httpx.get", return_value=_resp(status, body)):
        st = check_apify("apify_api_SECRET42", "platform")
    assert st["level"] == level
    assert "SECRET42" not in str(st)


def test_apify_network_failure_is_unknown_and_classifier(db):
    from app.limits.checkers import check_apify, classify_apify_error
    with patch("app.limits.checkers.httpx.get", side_effect=OSError("down")):
        assert check_apify("tok", "platform")["level"] == "unknown"
    assert classify_apify_error(None, "actor-memory-limit-exceeded") == "warning"
    assert classify_apify_error(None, "concurrent-runs-limit-exceeded") == "warning"
    assert classify_apify_error(None, None, "By launching this job you will exceed your remaining usage") == "exhausted"
    assert classify_apify_error(400, "invalid-input") is None


def test_own_apify_key_is_checked_per_organization(db):
    from app.limits.service import run_checks
    from app.limits.store import get_status
    own = _org(db, "Own Co", apify="own", own_apify_key="apify_api_OWNtoken99999")
    lead = _org(db, "Lead Co")
    with patch("app.limits.checkers.httpx.get", return_value=_resp(200, _apify_body(48.0))) as get:
        run_checks(own, force=True)
        run_checks(lead, force=True)
    assert get.call_count == 1
    assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer apify_api_OWNtoken99999"
    st = get_status(f"org:{own}", "apify")
    assert st["level"] == "critical" and st["organization_id"] == own and st["help_link"] == "/org-admin#integrations"
    assert get_status(f"org:{lead}", "apify") is None
    assert "apify_api_OWNtoken99999" not in str(st)
    # own key missing -> error status, no network call
    nokey = _org(db, "No Key", apify="own")
    with patch("app.limits.checkers.httpx.get") as get2:
        run_checks(nokey, force=True)
    assert get2.call_count == 0 and get_status(f"org:{nokey}", "apify")["level"] == "error"


def test_apify_connector_billing_error_records_platform_exhausted(db):
    from app.connectors.apify_connector import ApifyConnector, ApifyError
    from app.limits.store import get_status

    class FakeApiError(Exception):
        status_code = 402
        type = "not-enough-usage-to-run-paid-actor"

    with patch("app.services.tenant_api_keys.get_tenant_apify_token_sync", return_value=("tok", "platform")):
        conn = ApifyConnector()
    client = MagicMock()
    client.actor.return_value.call.side_effect = FakeApiError("You must upgrade to a paid plan to run this")
    conn._client = client
    with pytest.raises(ApifyError):
        conn._call_actor("apify/facebook-posts-scraper", {}, "posts")
    st = get_status("platform", "apify")
    assert st["level"] == "exhausted" and st["event"]["level"] == "exhausted"
    assert db.notifications.find_one({"audience": "super_admin", "type": "api_limit_exhausted"})


# ═══════════════════════════════════════════════════════════════════════════
# Gemini
# ═══════════════════════════════════════════════════════════════════════════

def test_ai_requests_carry_key_source(db):
    from app.pipeline.ai_usage_service import log_ai_request
    log_ai_request(organization_id="o1", key_source="organization", tokens_in=5)
    log_ai_request(organization_id="o1", key_source="custom")
    log_ai_request(organization_id=None)
    assert sorted(d["key_source"] for d in db.ai_requests.find()) == ["organization", "organization", "platform"]


def test_gemini_counts_requests_per_key_source_against_configured_limits(db):
    from app.limits.checkers import check_gemini
    org = _org(db, "Own Gemini", gemini="own", own_gemini_key="AIzaOWNkey000000000")
    now = datetime.now(timezone.utc)
    rows = [{"key_source": "platform", "status": "success", "tokens_in": 100, "created_at": now - timedelta(seconds=10)}
            for _ in range(3)]
    rows.append({"status": "success", "tokens_in": 100, "created_at": now - timedelta(seconds=20)})  # legacy row
    rows.append({"key_source": "platform", "status": "fallback", "created_at": now})                # not sent
    rows += [{"organization_id": org, "key_source": "organization", "status": "success", "tokens_in": 50,
              "created_at": now - timedelta(seconds=5)} for _ in range(2)]
    db.ai_requests.insert_many(rows)
    _set("ai.gemini.requests_per_minute", 4)
    _set("ai.gemini.requests_per_day", 5)
    _set("ai.gemini.input_per_minute", 10000)
    with _platform_keys():
        st = check_gemini("platform")
    m = {x["key"]: x for x in st["metrics"]}
    assert m["requests_per_minute"]["used"] == 4 and m["requests_per_minute"]["percent"] == 100.0
    assert m["requests_per_minute"]["informational"] is True and m["input_per_minute"]["informational"] is True
    assert m["requests_per_minute"]["source"] == "counted" and m["requests_per_minute"]["limit_source"] == "configured"
    assert m["input_per_minute"]["used"] == 400
    assert m["requests_per_day"]["used"] == 4 and m["requests_per_day"]["informational"] is False
    # a full minute is momentary; the day at 80% drives the level
    assert st["level"] == "warning" and st["period"].startswith("pt-")
    own = check_gemini(f"org:{org}")
    o = {x["key"]: x for x in own["metrics"]}
    assert o["requests_per_minute"]["used"] == 2 and o["requests_per_minute"]["limit"] is None
    assert own["level"] == "ok"
    # an organization on LeadAI's Gemini has no own-key status
    assert check_gemini(f"org:{_org(db, 'Lead')}") is None


def test_gemini_429_parsing():
    from app.limits.checkers import parse_gemini_error
    day = parse_gemini_error(429, _gemini_429("GenerateRequestsPerDayPerProjectPerModel-FreeTier"))
    assert day["kind"] == "daily" and day["retry_seconds"] == 16.0
    minute = parse_gemini_error(429, _gemini_429("GenerateRequestsPerMinutePerProjectPerModel", "7s"))
    assert minute["kind"] == "minute" and minute["retry_seconds"] == 7.0
    assert parse_gemini_error(429, {})["kind"] == "rate"
    assert parse_gemini_error(402, {"error": {"code": 402}})["kind"] == "credit"
    assert parse_gemini_error(400, {"error": {"status": "FAILED_PRECONDITION"}})["kind"] == "billing"
    assert parse_gemini_error(400, {"error": {"status": "INVALID_ARGUMENT"}})["kind"] is None


def test_gemini_per_day_429_blocks_until_pacific_midnight(db):
    from app.limits.checkers import check_gemini
    from app.limits.model import aware, pacific_day
    from app.limits.service import record_gemini_error
    st = record_gemini_error(None, "platform", 429, _gemini_429("GenerateRequestsPerDayPerProjectPerModel"))
    assert st["level"] == "exhausted" and st["block"]["kind"] == "daily"
    assert aware(st["block"]["until"]) == pacific_day()[2]
    sa = db.notifications.find_one({"audience": "super_admin", "type": "api_limit_exhausted"})
    assert sa and sa["severity"] == "danger" and sa["link"] == "/superadmin#/api-limits"
    # the next scheduled check keeps it exhausted while the block lasts
    from app.limits.store import get_status, public
    with _platform_keys():
        again = check_gemini("platform", public(get_status("platform", "gemini")))
    assert again["level"] == "exhausted" and "Pacific" in again["message"]


def test_gemini_minute_answer_never_shortens_a_daily_block(db):
    from app.limits.service import record_gemini_error, reset_hook_throttle
    daily = record_gemini_error(None, "platform", 429, _gemini_429("GenerateRequestsPerDayPerProjectPerModel"))
    reset_hook_throttle()
    later = record_gemini_error(None, "platform", 429, _gemini_429("GenerateRequestsPerMinutePerProject", "5s"))
    assert later["block"]["kind"] == "daily" and later["block"]["until"] == daily["block"]["until"]
    from app.limits.store import get_status
    assert get_status("platform", "gemini")["block"]["kind"] == "daily"


def test_gemini_per_minute_and_credit_states(db):
    from app.limits.checkers import check_gemini
    from app.limits.service import record_gemini_error
    from app.limits.store import get_status, public
    org = _org(db, "Own G", gemini="own", own_gemini_key="AIzaOWNkey000000000")
    st = record_gemini_error(org, "organization", 429, _gemini_429("GenerateRequestsPerMinutePerProject", "16s"))
    assert st["scope"] == f"org:{org}" and st["level"] == "warning" and st["block"]["kind"] == "minute"
    credit = record_gemini_error(None, "platform", 402, {"error": {"code": 402, "message": "prepaid credit"}})
    assert credit["level"] == "exhausted" and credit["block"]["until"] is None
    # credit block lasts until a successful call after it
    with _platform_keys():
        assert check_gemini("platform", public(get_status("platform", "gemini")))["level"] == "exhausted"
        db.ai_requests.insert_one({"key_source": "platform", "status": "success", "tokens_in": 1,
                                   "created_at": datetime.now(timezone.utc) + timedelta(seconds=1)})
        assert check_gemini("platform", public(get_status("platform", "gemini")))["level"] == "ok"


def test_gemini_call_hook_from_comment_ai(db):
    from app.limits.store import get_status
    from app.pipeline import comment_ai
    client = MagicMock()
    client.post.return_value = _resp(429, _gemini_429("GenerateRequestsPerDayPerProjectPerModel"))
    with _platform_keys(), patch.object(comment_ai, "_get_gemini_client", return_value=client), \
            patch("app.pipeline.comment_ai.time.sleep") as sleep:
        with pytest.raises(RuntimeError):
            comment_ai._call_gemini("sys", "user", organization_id=None)
    assert client.post.call_count == 1 and sleep.call_count == 0   # no retry on a used-up daily quota
    st = get_status("platform", "gemini")
    assert st["level"] == "exhausted" and st["block"]["kind"] == "daily"
    assert comment_ai.get_circuit_breaker_status()["seconds_remaining"] > 60


# ═══════════════════════════════════════════════════════════════════════════
# Email & SMS
# ═══════════════════════════════════════════════════════════════════════════

def test_email_rolling_24h_count_against_cap(db):
    from app.limits.checkers import check_email
    now = datetime.now(timezone.utc)
    db.email_outbox.insert_many(
        [{"status": "sent", "created_at": now - timedelta(hours=h)} for h in range(1, 10)]
        + [{"status": "sent", "created_at": now - timedelta(hours=30)} for _ in range(5)]
        + [{"status": "queued", "created_at": now}])
    with _env_vars(SMTP_HOST="smtp.example.com"):
        assert check_email()["metrics"][0]["limit"] is None
        _set("email.daily_limit", 10)
        st = check_email()
    m = st["metrics"][0]
    assert m["used"] == 9 and m["limit"] == 10 and m["percent"] == 90.0 and st["level"] == "warning"
    assert m["resets_at"] is not None and m["source"] == "counted" and m["limit_source"] == "configured"
    with _env_vars(SMTP_HOST=""):
        assert check_email()["level"] == "not_configured"


def test_smtp_limit_reply_detection():
    from app.limits.checkers import is_smtp_limit_error
    assert is_smtp_limit_error("(550, b'5.4.5 Daily user sending quota exceeded.')")
    assert is_smtp_limit_error("452 4.5.3 Too many recipients")
    assert is_smtp_limit_error("421 4.7.0 Too many messages, try later (rate limit)")
    assert is_smtp_limit_error("550 5.7.1 Daily sending limit exceeded for this mailbox")
    assert not is_smtp_limit_error("550 5.7.1 Relaying denied")
    assert not is_smtp_limit_error("535 5.7.8 Authentication failed")


def test_smtp_sending_limit_failure_flags_outbox_and_alerts(db):
    from app.events.email import send_email
    from app.limits.store import get_status
    err = smtplib.SMTPSenderRefused(550, b"5.4.5 Daily user sending quota exceeded", "noreply@x.example")
    with _env_vars(SMTP_HOST="smtp.example.com"), patch("app.events.email.deliver", side_effect=err):
        assert send_email("a@b.example", "Hi", "Body") == "failed"
    row = db.email_outbox.find_one({"to": "a@b.example"})
    assert row["limit_hit"] is True and "sending limit" in row["error"]
    st = get_status("platform", "email")
    assert st["level"] == "exhausted"
    assert db.notifications.find_one({"audience": "super_admin", "type": "api_limit_exhausted"})
    # the Super Admin is not emailed about the mail server itself
    assert db.email_outbox.count_documents({}) == 1
    with _env_vars(SMTP_HOST="smtp.example.com"):
        from app.limits.checkers import check_email
        assert check_email()["level"] == "exhausted"


def test_twilio_balance_parsing_and_suspension(db):
    from app.limits.checkers import check_sms
    with _env_vars(SMS_PROVIDER="twilio", TWILIO_ACCOUNT_SID="AC123", TWILIO_AUTH_TOKEN="tw_secret",
                   TWILIO_FROM="+15550001111", SMS_DAILY_LIMIT="500"):
        with patch("app.limits.checkers.httpx.get", return_value=_resp(200, {"balance": "12.50", "currency": "USD"})) as g:
            st = check_sms()
        assert g.call_args.args[0].endswith("/Accounts/AC123/Balance.json") and g.call_args.kwargs["auth"] == ("AC123", "tw_secret")
        bal = next(m for m in st["metrics"] if m["key"] == "balance")
        assert bal["remaining"] == 12.5 and bal["unit"] == "USD" and bal["source"] == "provider_api"
        assert st["level"] == "ok" and "tw_secret" not in str(st)
        with patch("app.limits.checkers.httpx.get", return_value=_resp(200, {"balance": "-0.40", "currency": "USD"})):
            assert check_sms()["level"] == "exhausted"
        with patch("app.limits.checkers.httpx.get", return_value=_resp(401, {"code": 20005, "message": "Account not active"})):
            assert check_sms()["level"] == "exhausted"
        with patch("app.limits.checkers.httpx.get", return_value=_resp(401, {"code": 20003, "message": "Auth"})):
            assert check_sms()["level"] == "error"


@pytest.mark.parametrize("balance,level", [("30.00", "ok"), ("15.00", "warning"), ("9.99", "critical"),
                                           ("0", "exhausted")])
def test_twilio_minimum_balance_levels(db, balance, level):
    from app.limits.checkers import check_sms
    _set("sms.min_balance", 20)
    with _env_vars(SMS_PROVIDER="twilio", TWILIO_ACCOUNT_SID="AC123", TWILIO_AUTH_TOKEN="tw_secret",
                   TWILIO_FROM="+15550001111", SMS_DAILY_LIMIT="500"), \
            patch("app.limits.checkers.httpx.get", return_value=_resp(200, {"balance": balance, "currency": "USD"})):
        st = check_sms()
    bal = next(m for m in st["metrics"] if m["key"] == "balance")
    assert st["level"] == level and bal["warn_below"] == 20


def test_fast2sms_minimum_balance_levels(db):
    from app.limits.checkers import check_sms
    with _env_vars(SMS_PROVIDER="fast2sms", FAST2SMS_API_KEY="f2s_secret", SMS_DAILY_LIMIT="10"):
        rich = _resp(200, {"return": True, "wallet": "150.00", "sms_count": 600})
        with patch("app.limits.checkers.httpx.post", return_value=rich):
            assert check_sms()["level"] == "ok"                       # min balance off by default
            _set("sms.min_balance", 200)
            st = check_sms()
            assert st["level"] == "warning" and "200 INR" in st["message"]
            _set("sms.min_balance", 400)
            assert check_sms()["level"] == "critical"


def test_sms_min_balance_setting_is_registered():
    from app.admin import settings as sa
    from app.settings import registry as R
    spec = R.SPEC_BY_KEY["sms.min_balance"]
    assert spec.category == "notifications" and spec.group == "API limit alerts" and spec.type == "float"
    assert spec.label == "Warn when the SMS balance is below" and sa.SETTING_DEFAULTS["sms.min_balance"] == 0.0
    assert "USD" in spec.description and "INR" in spec.description


def test_twilio_suspended_send_is_flagged(db):
    from app.limits.store import get_status
    from app.messaging.sms import send_sms
    with _env_vars(SMS_PROVIDER="twilio", TWILIO_ACCOUNT_SID="AC123", TWILIO_AUTH_TOKEN="tw_secret",
                   TWILIO_FROM="+15550001111"), \
            patch("app.messaging.sms.httpx.post", return_value=_resp(401, {"code": 20005, "message": "Account not active"})):
        assert send_sms("+919876543210", "hello", kind="generic") == "failed"
    row = db.sms_outbox.find_one({})
    assert row["limit_hit"] is True and "20005" in row["error"]
    assert get_status("platform", "sms")["level"] == "exhausted"


def test_fast2sms_wallet_parsing_and_416(db):
    from app.limits.checkers import check_sms
    from app.messaging.sms import send_sms
    with _env_vars(SMS_PROVIDER="fast2sms", FAST2SMS_API_KEY="f2s_secret", SMS_DAILY_LIMIT="500"):
        ok = _resp(200, {"return": True, "wallet": "5566.01", "sms_count": 27830})
        with patch("app.limits.checkers.httpx.post", return_value=ok) as p:
            st = check_sms()
        assert p.call_args.args[0] == "https://www.fast2sms.com/dev/wallet"
        assert p.call_args.kwargs["headers"] == {"authorization": "f2s_secret"}
        m = {x["key"]: x for x in st["metrics"]}
        assert m["wallet"]["remaining"] == 5566.01 and m["wallet"]["unit"] == "INR"
        assert m["sms_count"]["remaining"] == 27830 and m["sms_count"]["source"] == "provider_api"
        assert st["level"] == "ok"
        low = _resp(200, {"return": True, "wallet": "10.00", "sms_count": 100})
        with patch("app.limits.checkers.httpx.post", return_value=low):
            assert check_sms()["level"] == "warning"
        empty = _resp(200, {"return": False, "status_code": 416, "message": "Insufficient Balance"})
        with patch("app.limits.checkers.httpx.post", return_value=empty):
            assert check_sms()["level"] == "exhausted"
            assert send_sms("+919876543210", "hello") == "failed"
    assert db.sms_outbox.find_one({"limit_hit": True})


# ═══════════════════════════════════════════════════════════════════════════
# Alerts: audiences, dedupe, escalation, recovery, preferences
# ═══════════════════════════════════════════════════════════════════════════

def _platform_apify(used):
    from app.limits.service import check_platform
    with _platform_keys(), patch("app.limits.checkers.httpx.get", return_value=_resp(200, _apify_body(used))):
        check_platform(force=True)


def test_platform_alerts_reach_the_right_people_once_per_level(db):
    lead = _org(db, "Lead Co")
    own = _org(db, "Own Co", apify="own", own_apify_key="apify_api_OWNtoken99999")
    _user(db, "owner@lead.example", lead, "owner")
    member = _user(db, "member@lead.example", lead, "member")
    _user(db, "owner@own.example", own, "owner")
    own_member = _user(db, "member@own.example", own, "member")

    _platform_apify(42.0)                                                     # 86% -> warning
    assert len(_notes(db, audience="super_admin", type="api_limit_warning")) == 1
    assert not _notes(db, audience="org_admin") and not _notes(db, audience="user")
    _platform_apify(43.0)                                                     # same level: no repeat
    assert len(_notes(db, audience="super_admin", type="api_limit_warning")) == 1

    _platform_apify(47.5)                                                     # 97% -> critical
    assert len(_notes(db, audience="super_admin", type="api_limit_critical")) == 1
    org_notes = _notes(db, audience="org_admin", type="api_limit_critical")
    assert [n["organization_id"] for n in org_notes] == [lead]                # only orgs using LeadAI's Apify
    assert org_notes[0]["severity"] == "warning"                              # only "used up" is danger
    assert "scraping service" in org_notes[0]["message"] and "USD" not in org_notes[0]["message"]
    assert not _notes(db, audience="user")
    assert db.email_outbox.find_one({"to": SUPER, "kind": "notification.api_limit_critical"})

    _platform_apify(49.0)                                                     # 100% -> exhausted
    assert len(_notes(db, audience="org_admin", type="api_limit_exhausted", organization_id=lead)) == 1
    users = _notes(db, audience="user", type="api_limit_exhausted")
    assert [u["user_id"] for u in users] == [member]                          # not the owner, not other orgs
    assert own_member not in [u["user_id"] for u in users]
    _platform_apify(49.0)
    assert len(_notes(db, audience="user", type="api_limit_exhausted")) == 1

    _platform_apify(5.0)                                                      # back to ok: 1st check
    assert not _notes(db, audience="super_admin", type="api_limit_recovered")  # hysteresis
    _quiet_for(db, "platform:apify", 31)
    _platform_apify(5.0)                                                      # 2nd ok check, 31 min quiet
    assert len(_notes(db, audience="super_admin", type="api_limit_recovered")) == 1
    assert _notes(db, audience="org_admin", type="api_limit_recovered", organization_id=lead)
    assert not _notes(db, audience="org_admin", organization_id=own)
    _platform_apify(5.0)
    assert len(_notes(db, audience="super_admin", type="api_limit_recovered")) == 1


def test_alert_switches(db):
    lead = _org(db, "Lead Co")
    _user(db, "member@lead.example", lead, "member")
    _set("api_limits.notify_org_admins", False)
    _set("api_limits.notify_users", False)
    _set("api_limits.email_admins", False)
    _platform_apify(49.0)
    assert _notes(db, audience="super_admin", type="api_limit_exhausted")
    assert not _notes(db, audience="org_admin") and not _notes(db, audience="user")
    assert not db.email_outbox.find_one({"to": SUPER})
    _set("api_limits.enabled", False)
    from app.limits.service import record_gemini_error
    record_gemini_error(None, "platform", 402, {})
    assert not _notes(db, audience="super_admin", type="api_limit_exhausted", **{"data.provider": "gemini"})


def test_org_scope_alerts_preferences_and_email(db):
    from app.limits.service import run_checks
    org = _org(db, "Own Co", apify="own", own_apify_key="apify_api_OWNtoken99999",
               settings={"notify_usage_warnings": False})
    _user(db, "owner@own.example", org, "owner")
    quiet = _user(db, "quiet@own.example", org, "member", prefs={"usage_warnings": False})
    loud = _user(db, "loud@own.example", org, "member")
    with patch("app.limits.checkers.httpx.get", return_value=_resp(200, _apify_body(42.0))):
        run_checks(org, force=True)                                           # warning, org opted out
    assert not _notes(db, audience="org_admin", organization_id=org)
    with patch("app.limits.checkers.httpx.get", return_value=_resp(402, {"error": {"type": "limit-reached"}})):
        run_checks(org, force=True)                                           # exhausted = always delivered
    note = db.notifications.find_one({"audience": "org_admin", "organization_id": org, "type": "api_limit_exhausted"})
    assert note and note["severity"] == "danger" and note["link"] == "/org-admin#api-limits"
    assert db.email_outbox.find_one({"to": "owner@own.example", "kind": "notification.api_limit_exhausted"})
    users = [n["user_id"] for n in _notes(db, audience="user", type="api_limit_exhausted")]
    assert users == [loud] and quiet not in users
    assert not _notes(db, audience="super_admin")                             # an org's own account


def test_plan_near_limit_quota_warning_once(env):
    from app.billing.usage import get_current_usage_doc_sync
    from app.limits.service import run_checks
    client, db = env
    org = _org(db, "Plan Co", plan="starter")
    _user(db, "owner@plan.example", org, "owner")
    member = _user(db, "member@plan.example", org, "member")
    doc = get_current_usage_doc_sync(org)
    db.organization_usage.update_one({"_id": doc["_id"]}, {"$set": {"searches_used": 41}})
    run_checks(org, force=True)
    run_checks(org, force=True)
    warns = _notes(db, audience="org_admin", type="quota_warning", organization_id=org)
    assert len(warns) == 1 and warns[0]["severity"] == "warning" and "Searches this month" in warns[0]["title"]
    assert warns[0]["data"]["metric"] == "monthly_searches"
    db.organization_usage.update_one({"_id": doc["_id"]}, {"$set": {"searches_used": 50}})
    run_checks(org, force=True)
    danger = [n for n in _notes(db, audience="org_admin", type="quota_warning") if n["severity"] == "danger"]
    assert len(danger) == 1 and "limit reached" in danger[0]["title"]
    assert [n["user_id"] for n in _notes(db, audience="user", type="quota_warning")] == [member]


# ═══════════════════════════════════════════════════════════════════════════
# Endpoints: roles, isolation, rate limits
# ═══════════════════════════════════════════════════════════════════════════

def _seed_two_orgs(db):
    a = _org(db, "Alpha", apify="own", own_apify_key="apify_api_ALPHAtoken9999")
    b = _org(db, "Beta", apify="own", own_apify_key="apify_api_BETAtoken99999")
    _user(db, "owner@alpha.example", a, "owner")
    _user(db, "member@alpha.example", a, "member")
    _user(db, "owner@beta.example", b, "owner")
    from app.limits.service import run_checks
    with patch("app.limits.checkers.httpx.get", return_value=_resp(200, _apify_body(48.0))):
        run_checks(a, force=True)
    with patch("app.limits.checkers.httpx.get", return_value=_resp(200, _apify_body(2.0))):
        run_checks(b, force=True)
    _platform_apify(10.0)
    return a, b


def test_super_admin_overview_and_history(env):
    client, db = env
    a, b = _seed_two_orgs(db)
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    r = sa.get("/api/super-admin/api-limits")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [s["provider"] for s in body["platform"]] == ["apify", "gemini", "email", "sms", "payments"]
    assert body["organizations"]["total"] == 2 and body["organizations"]["items"][0]["organization_id"] == a
    assert body["organizations"]["summary"]["critical"] == 1
    assert body["settings"]["warn_percent"] == 80 and body["settings"]["check_minutes"] == 15
    assert body["settings"]["sms_min_balance"] == 0
    assert "ALPHAtoken" not in r.text and "apify_platform_tok" not in r.text
    only = sa.get("/api/super-admin/api-limits", params={"level": "ok"}).json()["organizations"]
    assert [i["organization_id"] for i in only["items"]] == [b]
    h = sa.get("/api/super-admin/api-limits/history", params={"provider": "apify", "scope": "platform", "days": 7})
    assert h.status_code == 200 and h.json()["items"] and h.json()["items"][0]["metrics"]
    assert sa.get("/api/super-admin/api-limits/history", params={"scope": "evil"}).status_code == 422
    # /health and /integrations carry the limits
    health = sa.get("/api/super-admin/health").json()["health"]
    comp = next(c for c in health["components"] if c["key"] == "api_limits")
    assert comp["status"] == "ok" and comp["levels"]["apify"] == "ok"
    integ = sa.get("/api/super-admin/integrations").json()["integrations"]
    assert integ["sms"]["provider"] is None and "setup_link" in integ["sms"]
    assert integ["apify"]["limits"]["level"] == "ok"


def test_refresh_is_rate_limited_and_audited(env):
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    with _platform_keys(), patch("app.limits.checkers.httpx.get", return_value=_resp(200, _apify_body(1.0))):
        codes = [sa.post("/api/super-admin/api-limits/refresh", json={"scope": "platform"}).status_code
                 for _ in range(6)]
    assert codes == [200] * 5 + [429]
    assert db.audit_logs.count_documents({"action": "api_limits.refreshed"}) == 5
    assert sa.post("/api/super-admin/api-limits/refresh", json={"scope": "nope"}).status_code in (422, 429)


def test_refresh_one_organization(env):
    client, db = env
    org = _org(db, "Solo", apify="own", own_apify_key="apify_api_SOLOtoken9999")
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    with patch("app.limits.checkers.httpx.get", return_value=_resp(200, _apify_body(30.0))):
        r = sa.post("/api/super-admin/api-limits/refresh", json={"scope": org})
    assert r.status_code == 200, r.text
    assert {s["provider"] for s in r.json()["statuses"]} >= {"apify", "plan"}
    missing = sa.post("/api/super-admin/api-limits/refresh", json={"scope": str(ObjectId())})
    assert missing.status_code == 404


def test_staff_viewer_reads_manager_refreshes(env):
    client, db = env
    for email, role in (("viewer@leadai.example", "viewer"), ("manager@leadai.example", "manager")):
        db.admin_users.insert_one({"email": email, "name": role, "role": role, "enabled": True,
                                   "password_hash": hash_password(PW)})
    viewer = _login(client, "viewer@leadai.example", PW, scope="admin")
    assert viewer.get("/api/admin/api-limits").status_code == 200
    assert viewer.get("/api/admin/api-limits/history").status_code == 200
    assert viewer.post("/api/admin/api-limits/refresh", json={}).status_code == 403
    assert viewer.get("/api/super-admin/api-limits").status_code == 403
    manager = _login(client, "manager@leadai.example", PW, scope="admin")
    with _platform_keys(), patch("app.limits.checkers.httpx.get", return_value=_resp(200, _apify_body(1.0))):
        assert manager.post("/api/admin/api-limits/refresh", json={}).status_code == 200
    site = _login_site_user(client, db)
    assert site.get("/api/admin/api-limits").status_code in (401, 403)
    assert site.get("/api/super-admin/api-limits").status_code in (401, 403)


def _login_site_user(client, db):
    org = _org(db, "Site Org")
    _user(db, "plain@site.example", org, "member")
    return _login(client, "plain@site.example", PW)


def test_org_admin_sees_only_its_organization(env):
    client, db = env
    a, b = _seed_two_orgs(db)
    owner = _login(client, "owner@alpha.example", PW)
    r = owner.get("/api/org-admin/api-limits")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["organization_id"] == a and body["coverage"]["apify"] == "own"
    apify = next(s for s in body["own_keys"] if s["provider"] == "apify")
    assert apify["level"] == "critical" and apify["organization_id"] == a
    assert b not in r.text and "Beta" not in r.text and "ALPHAtoken" not in r.text
    assert body["plan"]["provider"] == "plan" and body["plan"]["metrics"]
    for s in body["services"]:
        assert set(s) == {"provider", "label", "level", "message", "checked_at"}
    assert [s["provider"] for s in body["services"]] == ["gemini", "email", "sms"] or \
        [s["provider"] for s in body["services"]][:1] == ["gemini"]
    member = _login(client, "member@alpha.example", PW)
    assert member.get("/api/org-admin/api-limits").status_code == 403


def test_org_admin_service_view_hides_leadai_numbers(env):
    client, db = env
    lead = _org(db, "Lead Co")
    _user(db, "owner@lead.example", lead, "owner")
    _platform_apify(49.0)
    owner = _login(client, "owner@lead.example", PW)
    body = owner.get("/api/org-admin/api-limits").json()
    svc = next(s for s in body["services"] if s["provider"] == "apify")
    assert svc["level"] == "exhausted" and "paused" in svc["message"]
    text = str([{k: v for k, v in s.items() if k != "checked_at"} for s in body["services"]])
    assert "49" not in text and "USD" not in text and "provider-keys" not in text
    assert body["own_keys"] == []


def test_org_refresh_is_rate_limited(env):
    client, db = env
    org = _org(db, "Own Co", apify="own", own_apify_key="apify_api_OWNtoken99999")
    _user(db, "owner@own.example", org, "owner")
    owner = _login(client, "owner@own.example", PW)
    with patch("app.limits.checkers.httpx.get", return_value=_resp(200, _apify_body(10.0))) as get:
        codes = [owner.post("/api/org-admin/api-limits/refresh").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429] and get.call_count == 3
    assert db.audit_logs.find_one({"action": "api_limits.refreshed", "organization_id": org})


def test_user_sees_what_they_can_do_without_provider_internals(env):
    client, db = env
    lead = _org(db, "Lead Co")
    _user(db, "member@lead.example", lead, "member")
    member = _login(client, "member@lead.example", PW)
    ok = member.get("/api/me/api-limits")
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["can_search"] is True and body["can_analyze"] is True and body["issues"] == []
    assert body["plan"]["metrics"]["monthly_searches"]["limit"] > 0
    assert body["searches_left"] == body["plan"]["metrics"]["monthly_searches"]["remaining"]
    _platform_apify(49.0)
    from app.limits.service import record_gemini_error
    record_gemini_error(None, "platform", 429, _gemini_429("GenerateRequestsPerDayPerProjectPerModel"))
    r = member.get("/api/me/api-limits")
    body = r.json()
    assert body["can_search"] is False and body["can_analyze"] is False
    codes = {i["code"] for i in body["issues"]}
    assert {"SCRAPING_UNAVAILABLE", "AI_UNAVAILABLE"} <= codes
    low = r.text.lower()
    for word in ("apify", "gemini", "usd", "provider_api", "quota_ids", "metrics\": [{"):
        assert word not in low


# ═══════════════════════════════════════════════════════════════════════════
# Settings, scheduling, fixes
# ═══════════════════════════════════════════════════════════════════════════

def test_settings_are_registered():
    from app.admin import settings as sa
    from app.settings import registry as R
    keys = ("api_limits.enabled", "api_limits.check_minutes", "api_limits.warn_percent",
            "api_limits.critical_percent", "api_limits.notify_org_admins", "api_limits.notify_users",
            "api_limits.email_admins", "ai.gemini.requests_per_minute", "ai.gemini.requests_per_day",
            "ai.gemini.input_per_minute", "email.daily_limit")
    for k in keys:
        assert k in R.REGISTERED_KEYS and k in sa.SETTING_DEFAULTS and "token" not in k
    assert R.SPEC_BY_KEY["api_limits.check_minutes"].min == 5 and R.SPEC_BY_KEY["api_limits.check_minutes"].max == 1440
    assert ("notifications", "API limit alerts") in R.GROUP_INFO
    assert len(R.schema_categories()) == 13
    assert R.SPEC_BY_KEY["ai.gemini.requests_per_day"].group == "Gemini analysis"


def test_preference_maps_include_the_new_types():
    from app.api.routes import org_admin
    from app.api.routes.me import NOTIFICATION_PREFERENCES, notification_allowed
    from app.events.notifications import ORG_SETTING_FOR_TYPE, org_allows
    from app.limits.alerts import ALL_TYPES
    assert set(NOTIFICATION_PREFERENCES) == set(org_admin._PREF_KEYS)
    for t in ALL_TYPES:
        assert t in NOTIFICATION_PREFERENCES["usage_warnings"]["types"]
        assert ORG_SETTING_FOR_TYPE[t] == "notify_usage_warnings"
        assert notification_allowed({"notification_preferences": {"usage_warnings": False}}, t) is False
    off = {"notify_usage_warnings": False}
    assert org_allows("x", "api_limit_warning", "warning", off) is False
    assert org_allows("x", "api_limit_exhausted", "danger", off) is True


def test_limits_loop_runs_after_a_minute_then_every_interval(db):
    import asyncio

    from app.limits import service
    _set("api_limits.check_minutes", 2)                                      # below the floor of 5
    assert service.loop_sleep_seconds() == 300
    _set("api_limits.check_minutes", 30)
    assert service.loop_sleep_seconds() == 1800
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 3:
            raise asyncio.CancelledError

    calls = []
    with patch.object(service.asyncio, "sleep", side_effect=fake_sleep), \
            patch.object(service, "run_scheduled", side_effect=lambda: calls.append(1)):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(service._limits_loop())
    assert sleeps == [60, 1800, 1800] and len(calls) == 2


def test_limits_loop_is_not_started_in_tests_and_sweeper_does_not_run_checks(env):
    import inspect

    from app.lifecycle import maintenance
    from app.limits import service
    assert "run_scheduled" not in inspect.getsource(maintenance._sweeper_loop)
    with patch.object(service, "start_limits_loop") as start:
        from app.main import app
        with TestClient(app):
            pass
    assert start.call_count == 0


def test_scheduled_run_is_idempotent_while_running(db):
    from app.limits import service
    assert service._run_lock.acquire(blocking=False)
    try:
        assert service.run_scheduled() is None                               # a pass is already running
    finally:
        service._run_lock.release()


def test_scheduled_run_honours_interval_and_switch(db):
    from app.limits import service
    with _platform_keys(), patch("app.limits.checkers.httpx.get", return_value=_resp(200, _apify_body(1.0))):
        assert service.run_scheduled() is not None
        assert service.run_scheduled() is None                               # younger than check_minutes
        db.api_limit_status.update_one({"_id": "_meta"}, {"$set": {
            "last_run_at": datetime.now(timezone.utc) - timedelta(minutes=16)}})
        assert service.run_scheduled() is not None
        _set("api_limits.enabled", False)
        db.api_limit_status.update_one({"_id": "_meta"}, {"$set": {
            "last_run_at": datetime.now(timezone.utc) - timedelta(days=1)}})
        assert service.run_scheduled() is None


def test_record_key_failure_uses_danger_severity(db):
    from app.services.tenant_api_keys import record_key_failure
    org = _org(db, "Own Co", apify="own")
    record_key_failure(org, "apify", "OWN_APIFY_QUOTA_EXCEEDED", "Out of credit")
    assert db.notifications.find_one({"type": "api_key_problem"})["severity"] == "danger"


def test_apify_cost_totals_use_the_stored_field(env):
    client, db = env
    now = datetime.now(timezone.utc)
    db.search_history.insert_many([
        {"run_id": "r1", "created_at": now - timedelta(days=1), "scrape_info": {"usageUsd": 1.5, "runId": "x1"}},
        {"run_id": "r2", "created_at": now - timedelta(days=2), "scrape_info": {"usageUsd": 2.0, "runId": "x2"}},
        {"run_id": "r3", "created_at": now - timedelta(days=40), "scrape_info": {"usageUsd": 9.0}}])
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    assert sa.get("/api/super-admin/integrations").json()["integrations"]["apify"]["usage_usd_30d"] == 3.5
    rows = sa.get("/api/super-admin/searches").json()
    items = rows.get("items") or rows.get("searches") or []
    assert {i.get("usage_usd") for i in items if i.get("run_id") in ("r1", "r2")} == {1.5, 2.0}
    # recorded runs win for their search; searches without recorded runs still count
    db.apify_jobs.insert_many([{"job_id": "j1", "search_run_id": "r1", "usage_total_usd": 0.7,
                                "created_at": now - timedelta(hours=3)},
                               {"job_id": "j2", "search_run_id": "r1", "usage_usd": None, "created_at": now}])
    assert sa.get("/api/super-admin/integrations").json()["integrations"]["apify"]["usage_usd_30d"] == 2.7
    usage = sa.get("/api/admin/usage", params={"days": 30}).json()
    assert usage["total_cost"] == 2.7 and usage["runs_with_usage"] == 2


def test_ai_overview_24h_range(db):
    from app.pipeline.ai_usage_service import get_ai_overview_metrics
    now = datetime.now(timezone.utc)
    db.ai_requests.insert_many([{"status": "success", "created_at": now - timedelta(hours=2), "total_tokens": 1,
                                 "estimated_cost": 0.0, "latency_ms": 1.0},
                                {"status": "success", "created_at": now - timedelta(days=3), "total_tokens": 1,
                                 "estimated_cost": 0.0, "latency_ms": 1.0}])
    assert get_ai_overview_metrics("24h")["total_requests"] == 1
    assert get_ai_overview_metrics("30d")["total_requests"] == 2



# ═══════════════════════════════════════════════════════════════════════════
# Review regressions
# ═══════════════════════════════════════════════════════════════════════════

def _check_platform_now():
    from app.limits.service import check_platform
    with _platform_keys(), patch("app.limits.checkers.httpx.get", return_value=_resp(200, _apify_body(5.0))):
        check_platform(force=True)


def test_gemini_minute_burst_never_alerts_or_pauses_ai(db):
    """#1: a burst during a check is momentary — no exhausted / recovered fan-out."""
    from app.limits.store import get_status
    from app.limits.views import service_issues
    lead = _org(db, "Lead Co")
    _user(db, "owner@lead.example", lead, "owner")
    _user(db, "member@lead.example", lead, "member")
    _set("ai.gemini.requests_per_minute", 10)
    now = datetime.now(timezone.utc)
    for _ in range(3):
        db.ai_requests.insert_many([{"key_source": "platform", "status": "success", "tokens_in": 1,
                                     "created_at": now} for _ in range(10)])
        _check_platform_now()
        assert get_status("platform", "gemini")["level"] == "ok"
        db.ai_requests.update_many({}, {"$set": {"created_at": now - timedelta(minutes=5)}})
        _check_platform_now()
    for t in ("api_limit_exhausted", "api_limit_recovered", "api_limit_warning", "api_limit_critical"):
        assert not _notes(db, type=t, **{"data.provider": "gemini"})
    assert service_issues(lead)["ai"] is None


def test_recovery_needs_two_ok_checks_and_quiet_time(db):
    """#1 hysteresis; a warning that drops to ok is never "recovered" (or re-fired)."""
    _platform_apify(47.5)                                                     # critical
    _platform_apify(5.0)
    _platform_apify(5.0)                                                      # 2 ok checks, not quiet yet
    assert not _notes(db, type="api_limit_recovered")
    _quiet_for(db, "platform:apify", 31)
    _platform_apify(5.0)
    assert len(_notes(db, type="api_limit_recovered", audience="super_admin")) == 1
    # warnings: once per period, never closed by an ok check
    for _ in range(3):
        _platform_apify(42.0)
        _platform_apify(5.0)
        _quiet_for(db, "platform:apify", 31)
        _platform_apify(5.0)
    assert len(_notes(db, audience="super_admin", type="api_limit_warning")) == 1


def test_apify_memory_warning_notified_once_per_period(db):
    """#3: a live memory-full warning is momentary and announced at most once."""
    from app.limits.service import record_apify_error, reset_hook_throttle
    from app.limits.store import get_status
    _platform_apify(5.0)
    for _ in range(3):
        reset_hook_throttle()
        record_apify_error(None, "platform", 400, "actor-memory-limit-exceeded", "memory")
        _platform_apify(5.0)
    assert len(_notes(db, audience="super_admin", type="api_limit_warning")) == 1
    ev = get_status("platform", "apify")["event"]
    assert ev["expires_at"] is not None                                       # transient (30 min)


def test_apify_paid_plan_error_holds_until_a_successful_run(db):
    """#2: "upgrade to a paid plan" = that actor needs a paid plan (error), and a
    live event stays in force over an ok-looking check until a run succeeds."""
    from app.limits.service import record_apify_error, reset_hook_throttle
    from app.limits.store import get_status
    lead = _org(db, "Lead Co")
    _user(db, "member@lead.example", lead, "member")
    _platform_apify(5.0)
    for _ in range(2):
        reset_hook_throttle()
        record_apify_error(None, "platform", 403, None, "This Actor requires you to upgrade to a paid plan")
        _platform_apify(5.0)
    st = get_status("platform", "apify")
    assert st["level"] == "error" and st["event"]["code"] == "APIFY_ACTOR_NEEDS_PAID_PLAN"
    assert "paid Apify plan" in st["message"]
    assert len(_notes(db, audience="super_admin", type="api_limit_critical")) == 1
    assert not _notes(db, audience="user") and not _notes(db, type="api_limit_recovered")
    # a successful run with LeadAI's token after the event ends it
    db.apify_jobs.insert_one({"job_id": "ok1", "status": "succeeded", "token_source": "platform",
                              "organization_id": lead, "created_at": datetime.now(timezone.utc) + timedelta(seconds=1)})
    _platform_apify(5.0)
    assert get_status("platform", "apify")["level"] == "ok" and "event" not in get_status("platform", "apify")
    _quiet_for(db, "platform:apify", 31)
    _platform_apify(5.0)
    assert len(_notes(db, audience="super_admin", type="api_limit_recovered")) == 1


def test_live_402_survives_an_ok_looking_check(db):
    from app.limits.service import record_apify_error
    from app.limits.store import get_status
    record_apify_error(None, "platform", 402, "not-enough-usage-to-run-paid-actor", "")
    _platform_apify(5.0)
    _platform_apify(5.0)
    assert get_status("platform", "apify")["level"] == "exhausted"
    assert not _notes(db, type="api_limit_recovered")


def test_hook_then_first_check_single_notice(db):
    """#4: hook and scheduled check share one period scheme."""
    from app.limits.service import record_apify_error
    lead = _org(db, "Lead Co")
    _user(db, "member@lead.example", lead, "member")
    record_apify_error(None, "platform", 402, "not-enough-usage-to-run-paid-actor", "")
    _platform_apify(49.0)
    assert len(_notes(db, audience="super_admin", type="api_limit_exhausted")) == 1
    assert len(_notes(db, audience="user", type="api_limit_exhausted")) == 1
    assert db.api_limit_alerts.count_documents({}) == 1


def test_same_rank_level_change_is_notified(db):
    """#5: critical -> error (token revoked) is announced."""
    from app.limits.service import check_platform
    _platform_apify(47.5)
    with _platform_keys(), patch("app.limits.checkers.httpx.get",
                                 return_value=_resp(401, {"error": {"type": "token-not-valid"}})):
        check_platform(force=True)
    notes = _notes(db, audience="super_admin", **{"data.provider": "apify"})
    assert [n["data"]["level"] for n in notes] == ["critical", "error"]
    with _platform_keys(), patch("app.limits.checkers.httpx.get",
                                 return_value=_resp(401, {"error": {"type": "token-not-valid"}})):
        check_platform(force=True)
    assert len(_notes(db, audience="super_admin", **{"data.provider": "apify"})) == 2


def test_critical_respects_org_opt_out_exhausted_does_not(db):
    """#9: critical / error notices are severity warning (opt-out applies)."""
    quiet = _org(db, "Quiet Co", settings={"notify_usage_warnings": False})
    _platform_apify(47.5)
    assert not _notes(db, audience="org_admin", organization_id=quiet)
    _platform_apify(49.0)
    ex = _notes(db, audience="org_admin", organization_id=quiet, type="api_limit_exhausted")
    assert len(ex) == 1 and ex[0]["severity"] == "danger"


def test_gemini_daily_block_cleared_by_later_success(db):
    """#6: a success after the 429 ends the daily block."""
    from app.limits.checkers import check_gemini
    from app.limits.service import record_gemini_error
    from app.limits.store import get_status, public, save_status
    from app.limits.views import service_issues
    lead = _org(db, "Lead Co")
    record_gemini_error(None, "platform", 429, _gemini_429("GenerateRequestsPerDayPerProjectPerModel"))
    db.ai_requests.insert_one({"key_source": "platform", "status": "success", "tokens_in": 1,
                               "created_at": datetime.now(timezone.utc) + timedelta(seconds=2)})
    with _platform_keys():
        st = check_gemini("platform", public(get_status("platform", "gemini")))
    save_status(st)
    assert st["level"] == "ok" and st["block"] is None
    assert service_issues(lead)["ai"] is None


def test_gemini_mixed_violations_and_failed_precondition_kinds():
    """#6 + LOW: daily only when every violation is per-day; location != billing."""
    from app.limits.checkers import GEMINI_BLOCK_LEVEL, parse_gemini_error
    body = _gemini_429("GenerateRequestsPerDayPerProjectPerModel")
    body["error"]["details"][0]["violations"].append({"quotaId": "GenerateRequestsPerMinutePerProjectPerModel"})
    assert parse_gemini_error(429, body)["kind"] == "minute"
    loc = parse_gemini_error(400, {"error": {"status": "FAILED_PRECONDITION",
                                             "message": "User location is not supported for the API use."}})
    assert loc["kind"] == "region" and GEMINI_BLOCK_LEVEL["region"] == "error"
    bill = parse_gemini_error(400, {"error": {"status": "FAILED_PRECONDITION",
                                              "message": "Billing is not enabled for this project."}})
    assert bill["kind"] == "billing"
    from app.limits.checkers import gemini_block, gemini_block_message
    assert "region" in gemini_block_message("platform", gemini_block(loc))


@pytest.mark.parametrize("text", [
    "(421, b'4.7.0 This message is suspicious due to the very low reputation of the sending domain.')",
    "(554, b'5.7.1 Message rejected: the message was moderated by the list owner')",
    "(550, b'5.7.1 Message generated by an automated system was rejected')",
    "(550, b'5.7.1 Message rejected as spam by Content Filtering.')",
    "(535, b'5.7.8 Authentication credentials invalid; too many attempts')",
])
def test_smtp_non_limit_replies_are_not_limits(text):
    """#7"""
    from app.limits.checkers import is_smtp_limit_error
    assert not is_smtp_limit_error(text)


def test_apify_run_model_cost_is_stored_as_a_float(db):
    """#8: apify_client 3.x Run.usageUsd is a breakdown object; store usageTotalUsd."""
    import bson
    from apify_client._models import Run, RunUsageUsd

    from app.agent.search import persist_scrape_info
    from app.connectors.apify_connector import ApifyConnector, set_run_context, usage_total_usd
    run = Run.model_construct(id="run1", act_id="act1", status="SUCCEEDED", default_dataset_id="ds1",
                              usage_total_usd=0.25,
                              usage_usd=RunUsageUsd(ACTOR_COMPUTE_UNITS=0.2, DATASET_READS=0.05))
    assert usage_total_usd(run) == 0.25
    breakdown_only = Run.model_construct(id="run2", usage_total_usd=None,
                                         usage_usd=RunUsageUsd(ACTOR_COMPUTE_UNITS=0.2, DATASET_READS=0.05))
    assert usage_total_usd(breakdown_only) == 0.25
    org = _org(db, "Cost Co")
    with patch("app.services.tenant_api_keys.get_tenant_apify_token_sync", return_value=("tok", "platform")):
        conn = ApifyConnector()
    client = MagicMock()
    client.actor.return_value.call.return_value = run
    conn._client = client
    set_run_context(organization_id=org, user_id=None, search_run_id="sr1", platform="facebook")
    try:
        conn._call_actor("apify/facebook-posts-scraper", {}, "posts")
    finally:
        set_run_context()
    job = db.apify_jobs.find_one({"run_id": "run1"})
    assert job["usage_total_usd"] == 0.25 and job["usage_usd"] == 0.25 and job["token_source"] == "platform"
    assert conn.last_call["usageTotalUsd"] == 0.25 and "usageUsd" not in conn.last_call
    bson.encode(dict(conn.last_call))
    db.search_history.insert_one({"run_id": "sr1", "created_at": datetime.now(timezone.utc)})
    persist_scrape_info(db, "sr1", conn)
    assert db.search_history.find_one({"run_id": "sr1"})["scrape_info"]["usageTotalUsd"] == 0.25


def test_hook_alerts_run_off_the_calling_thread(db, monkeypatch):
    """#10: the live hook returns at once; evaluation runs on a background worker."""
    import threading

    from app.limits import alerts, service
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    done = threading.Event()
    seen = {}

    def slow_evaluate(status, **kw):
        seen["thread"] = threading.current_thread().name
        done.set()
    monkeypatch.setattr(alerts, "evaluate", slow_evaluate)
    st = service.record_gemini_error(None, "platform", 402, {})
    assert st["level"] == "exhausted"
    assert done.wait(5) and seen["thread"].startswith("api-limit-alerts")
    assert seen["thread"] != threading.current_thread().name


def test_scheduled_pass_is_claimed_by_one_worker(db):
    """LOW: atomic claim + lease (multi-process)."""
    from app.limits import service, store
    assert store.claim_scheduled_run(15) is True
    assert store.claim_scheduled_run(15) is False                             # another worker: no
    assert store.acquire_lease(60) is True
    with pytest.raises(service.ChecksBusy):
        service.run_checks("all")
    store.release_lease()
    with _platform_keys(), patch("app.limits.checkers.httpx.get", return_value=_resp(200, _apify_body(1.0))):
        assert service.run_checks("all")["organizations"] == 0


def test_refresh_all_while_running_is_409(env):
    from app.limits import store
    client, db = env
    sa = _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")
    assert store.acquire_lease(60)
    r = sa.post("/api/super-admin/api-limits/refresh", json={"scope": "all"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "CHECK_RUNNING"


def test_history_is_throttled_and_newest_points_kept(db):
    """LOW: points only on change (>= 1 point / level) or hourly; newest kept, oldest first."""
    from app.limits import store
    from app.limits.model import make_status, metric
    def st(pct):
        return make_status("email", "platform", configured=True, level="ok", message="x",
                           metrics=[metric("emails_24h", "E", used=pct, limit=100)])
    store.save_status(st(10))
    store.save_status(st(10.5))
    assert db.api_limit_history.count_documents({}) == 1
    store.save_status(st(12))
    assert db.api_limit_history.count_documents({}) == 2
    now = datetime.now(timezone.utc)
    db.api_limit_history.insert_many([{"scope": "platform", "provider": "email", "level": "ok", "metrics": [],
                                       "at": now - timedelta(days=d)} for d in (3, 2)])
    pts = store.history("email", "platform", 7)
    ats = [p["at"] for p in pts]
    assert ats == sorted(ats) and len(pts) == 4
    with patch.object(store, "HISTORY_EVERY", timedelta(seconds=0)):
        store.save_status(st(12))
    assert db.api_limit_history.count_documents({"status_id": "platform:email"}) == 3


@pytest.mark.parametrize("at,day,start,reset", [
    (datetime(2026, 3, 8, 20, tzinfo=timezone.utc), "2026-03-08",
     datetime(2026, 3, 8, 8, tzinfo=timezone.utc), datetime(2026, 3, 9, 7, tzinfo=timezone.utc)),
    (datetime(2026, 3, 8, 9, tzinfo=timezone.utc), "2026-03-08",
     datetime(2026, 3, 8, 8, tzinfo=timezone.utc), datetime(2026, 3, 9, 7, tzinfo=timezone.utc)),
    (datetime(2026, 11, 1, 20, tzinfo=timezone.utc), "2026-11-01",
     datetime(2026, 11, 1, 7, tzinfo=timezone.utc), datetime(2026, 11, 2, 8, tzinfo=timezone.utc)),
    (datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc), "2026-10-31",
     datetime(2026, 10, 31, 7, tzinfo=timezone.utc), datetime(2026, 11, 1, 7, tzinfo=timezone.utc)),
])
def test_pacific_day_on_dst_change_days(at, day, start, reset):
    """LOW: correct without tzdata (the rule) on both DST change days."""
    from app.limits import model
    with patch.object(model, "pacific_offset", model.pacific_offset_rule):
        assert model.pacific_day(at) == (day, start, reset)


def test_user_fanout_is_capped_per_organization(db, monkeypatch):
    """LOW: no organization starves the others."""
    from app.limits import alerts
    a, b = _org(db, "A"), _org(db, "B")
    for org, n in ((a, 3), (b, 3)):
        for i in range(n):
            _user(db, f"u{i}@{org}.example", org, "member")
    monkeypatch.setattr(alerts, "MAX_USERS_PER_ORG", 2)
    monkeypatch.setattr(alerts, "MAX_FANOUT_USERS", 3)
    assert alerts._fanout_users([a, b], "api_limit_exhausted", "t", "m", "danger", {}) == 3
    per_org = {o: db.notifications.count_documents({"organization_id": o}) for o in (a, b)}
    assert per_org == {a: 2, b: 1}


def test_own_key_live_warning_reaches_admins_unless_already_told(db):
    """LOW: admins are skipped only when record_key_failure told them."""
    from app.limits.service import record_apify_error, reset_hook_throttle
    told = _org(db, "Told Co", apify="own", own_apify_key="apify_api_TOLDtoken9999")
    fresh = _org(db, "Fresh Co", apify="own", own_apify_key="apify_api_FRESHtoken999")
    record_apify_error(fresh, "organization", None, "actor-memory-limit-exceeded", "memory")
    assert _notes(db, audience="org_admin", organization_id=fresh, type="api_limit_warning")
    reset_hook_throttle()
    record_apify_error(told, "organization", 402, "not-enough-usage-to-run-paid-actor", "",
                       admins_notified=True)
    assert not _notes(db, audience="org_admin", organization_id=told)


def test_lasting_email_exhaustion_renotifies_at_most_weekly(db):
    """LOW: email / SMS periods are sticky for 7 days; escalation still notifies."""
    from app.limits.alerts import evaluate
    from app.limits.model import make_status
    def ex(day):
        return make_status("email", "platform", configured=True, level="exhausted", message="full",
                           period=day)
    evaluate(ex("2026-10-01"))
    evaluate(ex("2026-10-02"))
    evaluate(ex("2026-10-05"))
    assert len(_notes(db, audience="super_admin", type="api_limit_exhausted")) == 1
    db.api_limit_alerts.update_many({}, {"$set": {"created_at": datetime.now(timezone.utc) - timedelta(days=8)}})
    evaluate(ex("2026-10-09"))
    assert len(_notes(db, audience="super_admin", type="api_limit_exhausted")) == 2


def test_notification_times_carry_their_zone():
    from datetime import datetime
    from app.events.notifications import _iso
    assert _iso(datetime(2026, 10, 15, 10, 20, 55)).endswith("+00:00")


def test_last_live_error_stays_visible_after_a_check():
    from datetime import timedelta
    from app.limits import service
    from app.limits.model import now_utc
    ev = {"code": "GEMINI_DAILY", "level": "exhausted", "message": "Daily quota used up",
          "at": (now_utc() - timedelta(hours=1)).isoformat()}
    st = {"scope": "platform", "provider": "gemini", "level": "ok"}
    service.carry_last_event(st, {"event": ev})
    assert st["event"] == ev                                   # recent: still shown
    old = dict(ev, at=(now_utc() - timedelta(hours=30)).isoformat())
    st2 = {"scope": "platform", "provider": "gemini", "level": "ok"}
    service.carry_last_event(st2, {"event": old})
    assert "event" not in st2                                  # old and recovered: dropped
    st3 = {"scope": "platform", "provider": "gemini", "level": "critical"}
    service.carry_last_event(st3, {"event": old})
    assert st3["event"] == old                                 # still not ok: kept
    gone = dict(ev, expires_at=(now_utc() - timedelta(minutes=1)).isoformat())
    st4 = {"scope": "platform", "provider": "gemini", "level": "critical"}
    service.carry_last_event(st4, {"event": gone})
    assert "event" not in st4                                  # expired transient: dropped


def test_user_limit_notices_open_the_usage_page():
    from app.limits import alerts
    assert alerts.USER_LINK == "/dashboard#usage"
