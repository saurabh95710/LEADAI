"""
Global Settings that change server behaviour (Super Admin → Global Settings):

  * email.sender_name / email.reply_to / email.footer_text / general.app.name
    on every email (incl. the messaging path)
  * notifications.alerts_enabled — the admin alert bell
  * notifications.job_failed_email / notifications.new_lead_email — platform
    switches for those notification emails
  * exports.max_records / exports.include_leads_only / exports.include_ai_summary
  * leads.require_phone_or_email / leads.default_status
  * ai.enabled is the one AI switch (features.ai_analysis.enabled merged in)
  * Feature Flags PUT refreshes /api/public/config at once
  * localization.timezone / localization.currency for new organizations

In-memory mongomock only (tests/conftest.py).
Run:  python -m pytest tests/test_settings_wiring.py -q -p no:cacheprovider
"""
import csv
import io
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.auth.service import COOKIE_NAME, build_session_value, create_tracked_session


def _no_smtp(name, default=""):
    return "" if name.startswith("SMTP") else default


def _set(**values):
    from app.admin.settings import clear_settings_cache, set_setting
    for key, value in values.items():
        assert set_setting(key.replace("__", "."), value, by="test")
    clear_settings_cache()


def _put(key, value):
    from app.admin.settings import clear_settings_cache, set_setting
    assert set_setting(key, value, by="test")
    clear_settings_cache()


@pytest.fixture
def db():
    from app.admin.settings import clear_settings_cache
    from app.db.mongo import get_sync_db
    from app.settings.registry import invalidate_public_config_cache
    clear_settings_cache()          # the 5 s hot-path cache is process-wide
    invalidate_public_config_cache()
    with patch("app.events.email._env", side_effect=_no_smtp):
        yield get_sync_db()
    clear_settings_cache()
    invalidate_public_config_cache()


@pytest.fixture
def client(db):
    with patch("app.admin.settings.is_maintenance_enabled", return_value=False):
        from app.main import app
        with TestClient(app) as c:
            yield c


def _cookie(claims):
    return {COOKIE_NAME: build_session_value(create_tracked_session(claims))}


def _super(db):
    from tests.conftest import as_superadmin
    email = as_superadmin("root@platform.test")
    found = db.users.find_one({"email": email})
    uid = str(found["_id"]) if found else str(db.users.insert_one({
        "email": email, "name": "root", "status": "active", "is_platform_admin": True,
        "platform_role": "super_admin"}).inserted_id)
    return _cookie({"user_id": uid, "email": email, "name": "root", "scope": "admin",
                    "role": "super_admin"})


def _org_member(db, email="owner@a.test", role="owner", settings=None):
    org_id = str(db.organizations.insert_one({
        "name": "Org A", "slug": f"org-a-{ObjectId()}", "status": "active",
        "admin_portal_enabled": True, "settings": settings or {}}).inserted_id)
    uid = str(db.users.insert_one({
        "email": email, "name": "Owner", "status": "active", "is_platform_admin": False,
        "default_organization_id": org_id}).inserted_id)
    db.organization_members.insert_one({"organization_id": org_id, "user_id": uid, "role": role,
                                        "status": "active", "permissions_override": {}})
    cookie = _cookie({"user_id": uid, "email": email, "name": "Owner", "scope": "site",
                      "organization_id": org_id, "org_role": role})
    return org_id, uid, cookie


def _csv(res):
    raw = res.body if hasattr(res, "body") else res.content   # Starlette Response or HTTP response
    return list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))


class _FakeSMTP:
    sent: list = []

    def __init__(self, host, port, timeout=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        pass

    def login(self, *a):
        pass

    def send_message(self, msg):
        _FakeSMTP.sent.append(msg)


def _smtp_env(name, default=""):
    return {"SMTP_HOST": "smtp.test", "SMTP_PORT": "587",
            "SMTP_FROM": "no-reply@acme.test"}.get(name, "" if name.startswith("SMTP") else default)


# ═══════════════════════════════════════════════════════════════════════════
# 1. Email
# ═══════════════════════════════════════════════════════════════════════════

class TestEmail:
    def test_sender_reply_to_footer_and_brand_on_the_sent_email(self, db):
        _FakeSMTP.sent = []
        _set(general__app__name="Acme Leads", email__sender_name="Acme Team",
             email__reply_to="help@acme.test", email__footer_text="Acme Inc · 1 Main Road")
        from app.events.email import send_email
        with patch("app.events.email._env", side_effect=_smtp_env), \
                patch("app.events.email.smtplib.SMTP", _FakeSMTP):
            status = send_email("u@x.test", "Your LeadAI password was changed",
                                "Hi,\n\nYour LeadAI account is safe.\n\n— The LeadAI team")
        assert status == "sent"
        msg = _FakeSMTP.sent[-1]
        assert msg["From"] == "Acme Team <no-reply@acme.test>"
        assert msg["Reply-To"] == "help@acme.test"
        assert msg["Subject"] == "Your Acme Leads password was changed"
        body = msg.get_content()
        assert "LeadAI" not in body and "The Acme Leads team" in body
        assert body.rstrip().endswith("Acme Inc · 1 Main Road")
        out = db.email_outbox.find_one({"to": "u@x.test"})
        assert out["subject"] == "Your Acme Leads password was changed"
        assert out["body"].rstrip().endswith("Acme Inc · 1 Main Road")

    def test_defaults_change_nothing_but_the_display_name(self, db):
        _FakeSMTP.sent = []
        from app.events.email import send_email
        with patch("app.events.email._env", side_effect=_smtp_env), \
                patch("app.events.email.smtplib.SMTP", _FakeSMTP):
            send_email("u@x.test", "Your LeadAI demo request", "Body\n")
        msg = _FakeSMTP.sent[-1]
        assert msg["From"] == "LeadAI <no-reply@acme.test>"      # product name as display name
        assert msg["Reply-To"] is None
        assert msg["Subject"] == "Your LeadAI demo request"
        assert msg.get_content() == "Body\n"

    def test_header_values_are_safe(self, db):
        from app.events.email import brand_name, reply_to_address, sender_header
        _set(general__app__name="   ", email__sender_name="Evil\r\nBcc: x@y.test",
             email__reply_to="not an address")
        assert brand_name() == "LeadAI"
        assert "\n" not in sender_header("no-reply@acme.test")
        assert reply_to_address() == ""
        _set(email__sender_name="")
        # a name already in SMTP_FROM is kept when no sender name is set
        assert sender_header("Ops Desk <ops@acme.test>") == "Ops Desk <ops@acme.test>"

    def test_brand_word_only(self, db):
        from app.events.email import apply_brand
        _set(general__app__name="Acme")
        assert apply_brand("Welcome to LeadAI! LeadAI's team") == "Welcome to Acme! Acme's team"
        assert apply_brand("https://x.test/reset?token=abcLeadAIxyz") == \
            "https://x.test/reset?token=abcLeadAIxyz"

    def test_messaging_path_goes_through_the_same_email(self, db):
        from app.messaging.service import send_to_person
        _set(general__app__name="Acme", email__footer_text="Unsubscribe: reply STOP")
        send_to_person({"email": "p@x.test", "name": "P"}, channels=["email"],
                       subject="New sign-in to your LeadAI account", body="Hello from LeadAI",
                       kind="login_alert")
        out = db.email_outbox.find_one({"to": "p@x.test"})
        assert out["subject"] == "New sign-in to your Acme account"
        assert out["body"] == "Hello from Acme\n\nUnsubscribe: reply STOP"


# ═══════════════════════════════════════════════════════════════════════════
# 2. Alert bell
# ═══════════════════════════════════════════════════════════════════════════

def test_alert_bell_is_empty_while_alerts_are_off(client, db):
    sa = _super(db)
    db.search_history.insert_one({"run_id": "R1", "status": "running",
                                  "created_at": datetime.now(timezone.utc)})
    r = client.get("/api/admin/alerts", cookies=sa)
    assert r.status_code == 200 and r.json()["disabled"] is False
    assert any(a["id"] == "jobs-running" for a in r.json()["alerts"])
    _put("notifications.alerts_enabled", False)
    r = client.get("/api/admin/alerts", cookies=sa)
    assert r.status_code == 200
    assert r.json()["alerts"] == [] and r.json()["disabled"] is True


# ═══════════════════════════════════════════════════════════════════════════
# 3. Platform switches for notification emails
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("ntype,switch", [("job_failed", "notifications.job_failed_email"),
                                          ("leads_found", "notifications.new_lead_email")])
def test_global_email_switch_wins_over_every_organization(db, ntype, switch):
    from app.events.notifications import notify_org_admins
    org_id, _, _ = _org_member(db, settings={"notify_on_leads": True, "notify_job_completion": True})
    _put(switch, True)
    assert notify_org_admins(org_id, ntype, "T", "m", email=True)
    assert db.email_outbox.count_documents({"kind": f"notification.{ntype}"}) == 1
    db.email_outbox.delete_many({})
    _put(switch, False)
    assert notify_org_admins(org_id, ntype, "T", "m", email=True)    # in-app still delivered
    assert db.notifications.count_documents({"type": ntype}) == 2
    assert db.email_outbox.count_documents({}) == 0
    # other notification emails are not affected
    assert notify_org_admins(org_id, "job_completed", "Done", "ok", email=True)
    assert db.email_outbox.count_documents({"kind": "notification.job_completed"}) == 1


# ═══════════════════════════════════════════════════════════════════════════
# 4. Exports
# ═══════════════════════════════════════════════════════════════════════════

def test_every_csv_export_is_capped_by_max_records(db):
    from app.api.routes.search import _csv_response
    rows = [{"a": i} for i in range(10)]
    _put("exports.max_records", 3)
    res = _csv_response(rows, ["a"], "x.csv")
    assert len(_csv(res)) == 1 + 3
    assert res.headers["X-Export-Truncated"] == "true" and res.headers["X-Export-Total"] == "10"
    assert len(_csv(_csv_response(rows, ["a"], "x.csv", max_rows=2))) == 1 + 2   # the lower wins
    assert len(_csv(_csv_response(rows, ["a"], "x.csv", max_rows=50000))) == 1 + 3


def _seed_leads(db, org_id=None, uid=None):
    base = {"platform": "facebook", "comment_text": "price?", "analyzed_at": datetime.now(timezone.utc),
            "reason": "Asked for the price"}
    if org_id:
        base.update({"organization_id": org_id, "user_id": uid, "created_by": "owner@a.test"})
    db.ai_comments.insert_many([
        {**base, "is_lead": True, "lead_score": 90, "commenter_name": "L1", "comment_ref": "c1"},
        {**base, "is_lead": True, "lead_score": 80, "commenter_name": "L2", "comment_ref": "c2"},
        {**base, "is_lead": False, "lead_score": 5, "commenter_name": "N1", "comment_ref": "c3"},
    ])


def test_admin_leads_export_defaults_to_the_leads_only_setting(client, db):
    sa = _super(db)
    _seed_leads(db)
    _put("exports.include_leads_only", True)
    assert len(_csv(client.get("/api/admin/export/leads.csv", cookies=sa))) == 1 + 2
    _put("exports.include_leads_only", False)                 # every analysed comment
    assert len(_csv(client.get("/api/admin/export/leads.csv", cookies=sa))) == 1 + 3
    explicit = client.get("/api/admin/export/leads.csv", params={"only_leads": "true"}, cookies=sa)
    assert len(_csv(explicit)) == 1 + 2                        # an explicit choice wins
    _put("exports.max_records", 1)
    assert len(_csv(client.get("/api/admin/export/leads.csv", cookies=sa))) == 1 + 1


async def _no_quota(**kw):
    return 1


async def _no_tokens(*a, **kw):
    return None


def test_my_leads_csv_ai_summary_column_follows_the_setting(client, db):
    org_id, uid, cookie = _org_member(db)
    _seed_leads(db, org_id, uid)
    with patch("app.billing.entitlements.EntitlementService.enforce_quota_and_consume", new=_no_quota), \
            patch("app.api.routes.search._charge_tokens", new=_no_tokens):
        client.cookies.clear()
        on = _csv(client.get("/api/me/leads.csv", cookies=cookie))
        assert "reason" in on[0] and "Asked for the price" in on[1]
        _put("exports.include_ai_summary", False)
        client.cookies.clear()
        off = _csv(client.get("/api/me/leads.csv", cookies=cookie))
        assert "reason" not in off[0] and len(off) == len(on)
        _put("exports.max_records", 1)
        client.cookies.clear()
        assert len(_csv(client.get("/api/me/leads.csv", cookies=cookie))) == 1 + 1


def test_xlsx_lead_export_is_capped(client, db):
    org_id, uid, cookie = _org_member(db)
    _seed_leads(db, org_id, uid)
    _put("exports.max_records", 2)
    client.cookies.clear()
    res = client.get("/api/leads/export/xlsx", cookies=cookie)
    assert res.status_code == 200, res.text
    assert res.text.count("<Row>") == 1 + 2


# ═══════════════════════════════════════════════════════════════════════════
# 5. Leads
# ═══════════════════════════════════════════════════════════════════════════

_BUYER = {"is_useful": True, "lead_type": "buyer", "confidence_score": 0.9, "priority": "high",
          "lead_quality": "hot", "buyer": {"intent": "purchase_inquiry", "budget": "50 lakh"},
          "contact": {}}
_TEXT = "I want to buy this flat, what is the price? budget 50 lakh"


def test_require_phone_or_email(db):
    from app.pipeline.comment_ai import _flat_extract
    assert _flat_extract(_BUYER, _TEXT)["is_lead"] is True          # off: intent alone is enough
    _put("leads.require_phone_or_email", True)
    assert _flat_extract(_BUYER, _TEXT)["is_lead"] is False
    for contact in ({"phone": "+919876543210"}, {"email": "b@x.test"}, {"whatsapp": "+919876543210"}):
        assert _flat_extract({**_BUYER, "contact": contact}, _TEXT)["is_lead"] is True


def test_default_status_of_new_leads(db):
    from app.pipeline import comment_ai
    from app.settings.effective import default_lead_status

    def run(tag):
        post = str(db.facebook_posts.insert_one({"post_id": tag, "platform": "facebook",
                                                 "caption": "Flat for sale"}).inserted_id)
        db.facebook_comments.insert_one({"post_ref": post, "text": _TEXT, "author_name": tag,
                                         "published_date": "2026-01-01"})
        with patch.object(comment_ai, "analyze_comment_ai",
                          lambda *a, **k: {**_BUYER, "analyzed_by": "rules"}):
            comment_ai.analyze_comments_for_post(post)
        return db.ai_comments.find_one({"commenter_name": tag})

    assert default_lead_status() == "new"
    lead = run("A")
    assert lead["is_lead"] is True and "lead_status" not in lead     # "new" = no status, as before
    _put("leads.default_status", "Contacted")
    assert run("B")["lead_status"] == "contacted"
    _put("leads.default_status", "Rejected")
    assert default_lead_status() == "disqualified"
    _put("leads.default_status", "nonsense")
    assert default_lead_status() == "new"


# ═══════════════════════════════════════════════════════════════════════════
# 6. ai.enabled is the one AI switch
# ═══════════════════════════════════════════════════════════════════════════

def test_ai_entitlement_follows_ai_enabled(db):
    from app.pipeline.comment_ai import _ai_entitlement
    _put("ai.enabled", False)
    assert _ai_entitlement(str(ObjectId())) == (False, "feature_disabled")


def test_legacy_ai_flag_is_merged_once(db):
    from app.pipeline.comment_ai import merge_legacy_ai_flag
    from app.admin.settings import get_bool
    db.system_settings.insert_one({"_id": "features.ai_analysis.enabled", "value": False})
    assert merge_legacy_ai_flag(db) is True
    assert get_bool("ai.enabled") is False
    assert db.system_settings.find_one({"_id": "features.ai_analysis.enabled"}) is None
    assert merge_legacy_ai_flag(db) is False                         # idempotent
    # a stored "on" is only removed: ai.enabled keeps its own value
    db.system_settings.delete_one({"_id": "ai.enabled"})
    db.system_settings.insert_one({"_id": "features.ai_analysis.enabled", "value": True})
    assert merge_legacy_ai_flag(db) is True
    assert db.system_settings.find_one({"_id": "ai.enabled"}) is None
    assert merge_legacy_ai_flag(None) is False


def test_flag_lists_use_ai_enabled(client, db):
    from app.api.routes.super_admin_platform import FLAG_REGISTRY
    keys = [f["key"] for f in FLAG_REGISTRY]
    assert "ai.enabled" in keys and "features.ai_analysis.enabled" not in keys
    from app.api.routes.admin import _FEATURES_KEYS
    assert "ai.enabled" in _FEATURES_KEYS and "features.ai_analysis.enabled" not in _FEATURES_KEYS
    r = client.put("/api/super-admin/feature-flags", cookies=_super(db),
                   json={"changes": {"ai.enabled": False}, "reason": "test"})
    assert r.status_code == 200, r.text
    from app.admin.settings import get_bool
    assert get_bool("ai.enabled") is False


# ═══════════════════════════════════════════════════════════════════════════
# 7. Feature Flags PUT refreshes the public config at once
# ═══════════════════════════════════════════════════════════════════════════

def test_feature_flag_change_reaches_public_config_at_once(client, db):
    from app.settings.registry import invalidate_public_config_cache
    invalidate_public_config_cache()
    assert client.get("/api/public/config").json()["features"]["url_search"] is True   # now cached
    r = client.put("/api/super-admin/feature-flags", cookies=_super(db),
                   json={"changes": {"features.url_search.enabled": False}, "reason": "test"})
    assert r.status_code == 200, r.text
    client.cookies.clear()
    assert client.get("/api/public/config").json()["features"]["url_search"] is False


# ═══════════════════════════════════════════════════════════════════════════
# 8. New organizations take the global timezone / currency
# ═══════════════════════════════════════════════════════════════════════════

def test_locale_defaults_helper(db):
    from app.settings.effective import org_locale_defaults
    assert org_locale_defaults() == ("UTC", "USD")                  # "auto" -> UTC
    _set(localization__timezone="Asia/Kolkata", localization__currency="INR")
    assert org_locale_defaults() == ("Asia/Kolkata", "INR")
    assert org_locale_defaults("Europe/Paris", "eur") == ("Europe/Paris", "EUR")   # a choice wins


def test_new_organizations_use_global_locale(client, db):
    _set(localization__timezone="Asia/Dubai", localization__currency="AED")
    r = client.post("/api/admin/organizations", cookies=_super(db), json={"name": "Globex"})
    assert r.status_code in (200, 201), r.text
    org = db.organizations.find_one({"name": "Globex"})
    assert (org["timezone"], org["currency"]) == ("Asia/Dubai", "AED")

    from app.lifecycle.demo import create_demo_request
    with patch("app.messaging.service.welcome_sms", lambda *a, **k: None):
        create_demo_request(name="Dee", email="dee@initech.test", password="Str0ngPass1",
                            company="Initech")
    org = db.organizations.find_one({"name": "Initech"})
    assert (org["timezone"], org["currency"]) == ("Asia/Dubai", "AED")
