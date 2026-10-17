"""
QA area D — routes that had no test coverage: Super Admin partner management,
the Partner Portal (profile, coupons, deals, payouts), referral campaign
links, customer lifecycle / free trials, tokens, messaging, webhooks and
platform maintenance.

Everything runs on the real routes against the in-memory MongoDB from
tests/conftest.py. Mail, SMS and provider-key checks are patched at the
network boundary; nothing leaves the process.

Money rules exercised (docs/PARTNERS.md):
  commission  pending -> qualified -> approved -> payable -> processing -> paid,
              reversed from any unpaid state, paid ones clawed back (negative entry);
  payout      requested -> under_review -> approved -> processing -> paid,
              or failed / rejected / cancelled -> the money is payable again.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from tests.conftest import TEST_SUPERADMIN_PASSWORD, as_superadmin, seed_site_account

SUPER = "platform-owner@leadai.example"
PW = "Partner-Pass-2026"
CUST_PW = "Customer-Pass-2026"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
RAW_APIFY = "apify_api_QAsecretTOKEN1234567890"
VALID = AsyncMock(return_value={"valid": True, "message": "ok"})
INVALID = AsyncMock(return_value={"valid": False, "detail": "Invalid token"})
BASE = "/api/super-admin/partners"


def _offline(*_a, **_k):
    raise RuntimeError("network access is disabled in tests")


@pytest.fixture
def env(tmp_path, monkeypatch):
    as_superadmin(SUPER)
    from app.billing.provider import reset_billing_provider
    reset_billing_provider()
    from app.partners import marketing
    monkeypatch.setattr(marketing, "PRIVATE_DIR", str(tmp_path / "assets"))
    # the network boundary: no mail server, no SMS gateway
    monkeypatch.setattr("app.events.email.deliver", _offline)
    for fn in ("_send_twilio", "_send_fast2sms", "_send_webhook"):
        monkeypatch.setattr(f"app.messaging.sms.{fn}", _offline)
    with patch("app.admin.settings.is_maintenance_enabled", return_value=False):
        from app.main import app
        with TestClient(app) as client:
            from app.db.mongo import get_sync_db
            from app.services import secret_box
            secret_box.reset_cache()
            yield client, get_sync_db()


# ── helpers ──────────────────────────────────────────────────────────────────

def _login(client, email, password, scope="site", expect=200):
    c = TestClient(client.app)
    r = c.post("/api/auth/login", json={"email": email, "password": password, "scope": scope})
    assert r.status_code == expect, (email, scope, r.text)
    return c


def _sa(client):
    return _login(client, SUPER, TEST_SUPERADMIN_PASSWORD, scope="admin")


def _staff(client, db, role):
    """Platform staff (admin-scope session) with a non-super platform role."""
    from app.auth.crypto import hash_password
    from app.auth.service import COOKIE_NAME, build_session_value, create_tracked_session
    email = f"{role.replace('_', '-')}-{ObjectId()}@staff.example"
    uid = str(db.users.insert_one({
        "email": email, "name": role, "password_hash": hash_password("Staff-Pass-2026"), "status": "active",
        "is_platform_admin": True, "platform_role": role, "created_at": datetime.now(timezone.utc)}).inserted_id)
    c = TestClient(client.app)
    c.cookies.set(COOKIE_NAME, build_session_value(create_tracked_session(
        {"user_id": uid, "email": email, "name": role, "scope": "admin", "role": role})))
    return c


def _settings(sa, **values):
    r = sa.put(f"{BASE}/settings", json=values)
    assert r.status_code == 200, r.text


def _partner(client, sa, email, *, ptype="affiliate", name="P", permissions=None, tax_info=None):
    body = {"name": name, "email": email, "password": PW, "company": name + " Co", "partner_type": ptype,
            "experience": "Ten years of SaaS sales", "promotion_plan": "Newsletter and webinars",
            "accepted_terms": True,
            "payout_info": {"method": "paypal", "paypal_email": "pay@" + email.split("@")[1]}}
    if tax_info:
        body["tax_info"] = tax_info
    r = client.post("/api/public/partners/apply", json=body)
    assert r.status_code == 200, r.text
    approve = {"partner_type": ptype, **({"permissions": permissions} if permissions is not None else {})}
    r = sa.post(f"{BASE}/applications/{r.json()['application']['id']}/approve", json=approve)
    assert r.status_code == 200, r.text
    return r.json()["partner"], _login(client, email, PW, scope="partner")


def _signup_via(client, partner, email, company, campaign=None):
    """Website signup through the partner's referral link (approval mode)."""
    v = TestClient(client.app)
    v.get(f"/r/{partner['referral_code']}" + (f"/{campaign}" if campaign else ""), follow_redirects=False)
    r = v.post("/api/auth/signup", json={"email": email, "password": CUST_PW, "name": company + " Owner",
                                         "company": company, "accepted_terms": True})
    assert r.status_code == 200, r.text
    return r.json()


def _signup(client, email, company):
    r = TestClient(client.app).post("/api/auth/signup", json={
        "email": email, "password": CUST_PW, "name": company + " Owner", "company": company,
        "accepted_terms": True})
    assert r.status_code == 200, r.text
    return r.json()


def _seed_commission(db, partner_id, *, status, amount, currency="USD", kind="commission",
                     referral_id=None, hold_until=None, manual=False, company="Seed Co"):
    """A commission document shaped exactly like the engine writes it
    (app.partners.commissions.create_commission_for_payment)."""
    now = datetime.now(timezone.utc)
    doc = {"partner_id": str(partner_id), "referral_id": referral_id, "organization_id": str(ObjectId()),
           "company": company, "subscription_id": str(ObjectId()), "invoice_id": str(ObjectId()),
           "period_key": "initial", "event": "initial", "kind": kind, "plan_id": "starter",
           "paid_amount": amount * 5, "base_amount": amount * 5, "currency": currency, "coupon_code": None,
           "rule_id": None, "rule_name": "Default", "rate_type": "percentage", "rate_value": 20.0,
           "rate_fixed": 0.0, "amount": amount, "original_amount": amount, "reversed_amount": 0.0,
           "status": status, "requires_manual_approval": manual,
           "hold_until": hold_until or (now - timedelta(days=1)), "payout_id": None,
           "created_at": now, "updated_at": now, "history": [{"status": status, "at": now, "by": "seed"}]}
    return str(db.partner_commissions.insert_one(doc).inserted_id)


def _com(db, cid):
    return db.partner_commissions.find_one({"_id": ObjectId(cid)})


def _audit(db, action, **extra):
    return db.audit_logs.find_one({"action": action, **extra})


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _now():
    return datetime.now(timezone.utc)


# ══ Super Admin → partner records ═══════════════════════════════════════════

def test_meta_returns_catalog_and_manage_flag_per_platform_role(env):
    client, db = env
    from app.partners import constants as K
    sa = _sa(client)
    r = sa.get(f"{BASE}/meta")
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["permissions"] == K.PERMISSION_LABELS
    assert m["default_permissions"] == {k: list(v) for k, v in K.DEFAULT_PERMISSIONS.items()}
    assert m["reseller_only"] == sorted(K.RESELLER_ONLY)
    assert m["commission_statuses"] == list(K.COMMISSION_STATUSES)
    assert m["payout_statuses"] == list(K.PAYOUT_STATUSES)
    assert m["partner_types"] == list(K.PARTNER_TYPES)
    assert m["can_manage"] is True
    # /api/super-admin/* is Super Admin only (app/main.py): staff with partners.view are refused too
    viewer = _staff(client, db, "billing_admin")
    r = viewer.get(f"{BASE}/meta")
    assert r.status_code == 403
    # technical_admin has no partner permission at all
    assert _staff(client, db, "technical_admin").get(f"{BASE}/meta").status_code == 403
    assert TestClient(client.app).get(f"{BASE}/meta").status_code == 401


def test_suspend_and_reactivate_partner(env):
    client, db = env
    sa = _sa(client)
    p, portal = _partner(client, sa, "sus@aff.example", name="Sus")
    pid = p["id"]
    assert sa.patch(f"{BASE}/{pid}", json={"permissions": p["permissions"] + ["api.access"]}).status_code == 200
    raw = portal.post("/api/partner/v1/api-keys", json={"name": "BI"}).json()["api_key"]
    # a reason is mandatory; platform staff without partners.manage are refused
    assert sa.post(f"{BASE}/{pid}/suspend", json={"reason": "  "}).status_code == 422
    assert _staff(client, db, "operations_admin").post(f"{BASE}/{pid}/suspend",
                                                       json={"reason": "x"}).status_code == 403
    assert sa.post(f"{BASE}/{ObjectId()}/suspend", json={"reason": "x"}).status_code == 404
    r = sa.post(f"{BASE}/{pid}/suspend", json={"reason": "Chargeback investigation"})
    assert r.status_code == 200, r.text
    out = r.json()["partner"]
    assert out["status"] == "suspended" and out["suspension_reason"] == "Chargeback investigation"
    assert out["effective_permissions"] == []
    doc = db.partners.find_one({"_id": ObjectId(pid)})
    assert doc["status"] == "suspended" and doc["status_history"][-1]["note"] == "Chargeback investigation"
    # access ends immediately: session, API key and sign-in
    assert portal.get("/api/partner/v1/me").status_code == 401
    key = db.api_keys.find_one({"partner_id": pid})
    assert key["is_active"] is False and key["revoked_reason"] == "partner_suspended"
    assert TestClient(client.app).get("/api/partner/v1/dashboard", headers={"X-API-Key": raw}).status_code == 401
    r = TestClient(client.app).post("/api/auth/login", json={"email": "sus@aff.example", "password": PW,
                                                             "scope": "partner"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "partner_suspended"
    a = _audit(db, "partner.suspended", **{"details.partner_id": pid})
    assert a and a["details"]["reason"] == "Chargeback investigation" and a["actor_email"] == SUPER
    assert db.notifications.find_one({"user_id": p["user_id"], "title": "Your partner account was suspended"})
    # invalid transition: already suspended
    assert sa.post(f"{BASE}/{pid}/suspend", json={"reason": "again"}).status_code == 409

    r = sa.post(f"{BASE}/{pid}/reactivate", json={"reason": "Cleared"})
    assert r.status_code == 200 and r.json()["partner"]["status"] == "active"
    doc = db.partners.find_one({"_id": ObjectId(pid)})
    assert doc["suspension_reason"] is None and doc["reactivated_at"]
    assert _audit(db, "partner.reactivated", **{"details.partner_id": pid})
    assert sa.post(f"{BASE}/{pid}/reactivate", json={}).status_code == 409
    _login(client, "sus@aff.example", PW, scope="partner")
    # the revoked API key stays revoked after reactivation
    assert db.api_keys.find_one({"partner_id": pid})["is_active"] is False


def test_force_sign_out_everywhere(env):
    client, db = env
    sa = _sa(client)
    p, portal = _partner(client, sa, "out@aff.example", name="Out")
    second = _login(client, "out@aff.example", PW, scope="partner")
    other, other_portal = _partner(client, sa, "stay@aff.example", name="Stay")
    r = sa.post(f"{BASE}/{p['id']}/sessions/revoke", json={"reason": "Device lost"})
    assert r.status_code == 200 and r.json()["revoked"] == 2
    assert portal.get("/api/partner/v1/me").status_code == 401
    assert second.get("/api/partner/v1/me").status_code == 401
    assert other_portal.get("/api/partner/v1/me").status_code == 200          # other partners untouched
    assert db.user_sessions.count_documents({"user_id": p["user_id"], "scope": "partner", "revoked_at": None}) == 0
    a = _audit(db, "partner.session.revoked_all", **{"details.partner_id": p["id"]})
    assert a["details"]["sessions"] == 2 and a["details"]["reason"] == "Device lost"
    n = db.notifications.find_one({"user_id": p["user_id"], "type": "partner_security"})
    assert n and n["message"] == "Device lost"
    assert sa.post(f"{BASE}/{p['id']}/sessions/revoke", json={}).json()["revoked"] == 0
    assert sa.post(f"{BASE}/{ObjectId()}/sessions/revoke", json={}).status_code == 404
    assert _staff(client, db, "billing_admin").post(f"{BASE}/{other['id']}/sessions/revoke",
                                                    json={}).status_code == 403


def test_notify_one_partner(env):
    client, db = env
    sa = _sa(client)
    p, portal = _partner(client, sa, "note@aff.example", name="Note")
    _, other_portal = _partner(client, sa, "else@aff.example", name="Else")
    assert sa.post(f"{BASE}/{p['id']}/notify", json={"title": "  ", "message": "x"}).status_code == 422
    assert sa.post(f"{BASE}/{p['id']}/notify", json={"title": "x", "message": ""}).status_code == 422
    assert sa.post(f"{BASE}/{ObjectId()}/notify", json={"title": "x", "message": "y"}).status_code == 404
    r = sa.post(f"{BASE}/{p['id']}/notify", json={"title": "Quarterly kickoff", "message": "Join us Monday",
                                                  "email": True})
    assert r.status_code == 200 and r.json() == {"success": True}
    titles = [n["title"] for n in portal.get("/api/partner/v1/notifications?limit=100").json()["items"]]
    assert "Quarterly kickoff" in titles
    assert "Quarterly kickoff" not in [n["title"] for n in
                                       other_portal.get("/api/partner/v1/notifications?limit=100").json()["items"]]
    mail = db.email_outbox.find_one({"to": "note@aff.example", "subject": {"$regex": "Quarterly kickoff"}})
    assert mail and mail["kind"] == "partner_message" and "Join us Monday" in mail["body"]
    a = _audit(db, "partner.notification.sent", **{"details.partner_id": p["id"]})
    assert a and a["details"]["title"] == "Quarterly kickoff"


def test_broadcast_reaches_active_partners_of_the_chosen_type_only(env):
    client, db = env
    sa = _sa(client)
    aff, _ = _partner(client, sa, "b1@aff.example", name="B1")
    res, _ = _partner(client, sa, "b2@res.example", ptype="reseller", name="B2")
    gone, _ = _partner(client, sa, "b3@aff.example", name="B3")
    sa.post(f"{BASE}/{gone['id']}/suspend", json={"reason": "Inactive"})
    assert sa.post(f"{BASE}/notify", json={"title": "", "message": "x"}).status_code == 422
    r = sa.post(f"{BASE}/notify", json={"title": "Affiliate news", "message": "New banners",
                                        "partner_type": "affiliate"})
    assert r.status_code == 200 and r.json()["sent"] == 1
    got = {n["user_id"] for n in db.notifications.find({"type": "partner_announcement", "title": "Affiliate news"})}
    assert got == {aff["user_id"]}
    r = sa.post(f"{BASE}/notify", json={"title": "Everyone", "message": "Price update"})
    assert r.json()["sent"] == 2
    got = {n["user_id"] for n in db.notifications.find({"type": "partner_announcement", "title": "Everyone"})}
    assert got == {aff["user_id"], res["user_id"]}                          # never the suspended partner
    a = db.audit_logs.find_one({"action": "partner.notification.broadcast", "details.title": "Everyone"})
    assert a["details"]["recipients"] == 2 and a["details"]["partner_type"] is None
    assert _staff(client, db, "support_admin").post(f"{BASE}/notify",
                                                    json={"title": "a", "message": "b"}).status_code == 403


def test_set_partner_password(env):
    client, db = env
    sa = _sa(client)
    p, portal = _partner(client, sa, "pw@aff.example", name="Pw")
    assert sa.post(f"{BASE}/{p['id']}/password", json={"password": "weak"}).status_code == 422
    assert sa.post(f"{BASE}/{p['id']}/password", json={"password": "alllowercase1"}).status_code == 422
    assert sa.post(f"{BASE}/{ObjectId()}/password", json={"password": "Brand-New-Pass-9"}).status_code == 404
    assert _staff(client, db, "operations_admin").post(f"{BASE}/{p['id']}/password",
                                                       json={"password": "Brand-New-Pass-9"}).status_code == 403
    r = sa.post(f"{BASE}/{p['id']}/password", json={"password": "Brand-New-Pass-9", "reason": "Locked out"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["must_change_password"] is True and body["sessions_revoked"] == 1
    assert "must choose their own password" in body["message"] and "Brand-New-Pass-9" not in r.text
    assert portal.get("/api/partner/v1/me").status_code == 401
    _login(client, "pw@aff.example", PW, scope="partner", expect=401)
    r = TestClient(client.app).post("/api/auth/login", json={"email": "pw@aff.example",
                                                             "password": "Brand-New-Pass-9", "scope": "partner"})
    assert r.status_code == 200 and r.json()["must_change_password"] is True
    user = db.users.find_one({"_id": ObjectId(p["user_id"])})
    assert user["must_change_password"] is True and user["password_set_by"] == SUPER
    a = _audit(db, "partner.password_set", **{"details.partner_id": p["id"]})
    assert a["details"]["reason"] == "Locked out" and "must_change_password" in a["details"]
    assert "Brand-New-Pass-9" not in str(a)
    mail = db.email_outbox.find_one({"to": "pw@aff.example", "subject": "Your LeadAI password was changed"})
    assert mail and "Brand-New-Pass-9" not in mail["body"]


def test_change_partner_email(env):
    client, db = env
    sa = _sa(client)
    p, portal = _partner(client, sa, "old@aff.example", name="Mail")
    _partner(client, sa, "taken@aff.example", name="Taken")
    url = f"{BASE}/{p['id']}/email"
    assert sa.patch(url, json={"email": "not-an-email"}).status_code == 422
    assert sa.patch(url, json={"email": "taken@aff.example"}).status_code == 409
    assert sa.patch(url, json={"email": SUPER}).status_code == 409                # the Super Admin's address
    assert sa.patch(f"{BASE}/{ObjectId()}/email", json={"email": "x@y.example"}).status_code == 404
    r = sa.patch(url, json={"email": "OLD@aff.example"})
    assert r.status_code == 200 and r.json()["message"] == "Email unchanged"
    assert _audit(db, "partner.email_changed") is None
    r = sa.patch(url, json={"email": "New@Aff.example", "reason": "Rebrand"})
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "new@aff.example" and r.json()["sessions_revoked"] == 1
    assert db.users.find_one({"_id": ObjectId(p["user_id"])})["email"] == "new@aff.example"
    assert db.partners.find_one({"_id": ObjectId(p["id"])})["email"] == "new@aff.example"
    assert db.partner_applications.find_one({"user_id": p["user_id"]})["email"] == "new@aff.example"
    assert portal.get("/api/partner/v1/me").status_code == 401
    _login(client, "old@aff.example", PW, scope="partner", expect=401)
    _login(client, "new@aff.example", PW, scope="partner")
    a = _audit(db, "partner.email_changed", **{"details.partner_id": p["id"]})
    assert a["details"]["before"] == "old@aff.example" and a["details"]["after"] == "new@aff.example"
    for addr in ("old@aff.example", "new@aff.example"):
        assert db.email_outbox.find_one({"to": addr, "subject": "Your LeadAI sign-in email was changed"})
    assert _staff(client, db, "billing_admin").patch(url, json={"email": "z@aff.example"}).status_code == 403


def test_view_as_partner_impersonation(env):
    client, db = env
    sa = _sa(client)
    p, _ = _partner(client, sa, "imp@aff.example", name="Imp")
    url = f"{BASE}/{p['id']}/impersonate"
    assert sa.post(url, json={"reason": "abc"}).status_code == 422
    assert sa.post(f"{BASE}/{ObjectId()}/impersonate", json={"reason": "Support ticket 42"}).status_code == 404
    # only the Super Admin role, not other platform staff
    assert _staff(client, db, "operations_admin").post(url, json={"reason": "Support ticket 42"}).status_code == 403
    viewer = _sa(client)
    r = viewer.post(url, json={"reason": "Support ticket 42"})
    assert r.status_code == 200, r.text
    assert r.json()["redirect"] == "/partner#/dashboard" and r.json()["expires_at"] > _now().timestamp()
    me = viewer.get("/api/partner/v1/me")
    assert me.status_code == 200, me.text
    me = me.json()["me"]
    assert me["email"] == "imp@aff.example" and me["impersonated_by"] == SUPER and me["is_active_partner"]
    sess = db.user_sessions.find_one({"scope": "partner", "impersonated_by": SUPER})
    assert sess["user_id"] == p["user_id"] and sess["impersonation_reason"] == "Support ticket 42"
    a = _audit(db, "partner.impersonation.start", **{"details.partner_id": p["id"]})
    assert a["details"]["reason"] == "Support ticket 42" and a["actor_email"] == SUPER
    # an action taken while viewing as the partner stays attributable to the Super Admin
    assert viewer.patch("/api/partner/v1/profile", json={"city": "Pune"}).status_code == 200
    upd = _audit(db, "partner.profile.updated", **{"details.partner_id": p["id"]})
    assert upd["details"]["impersonated_by"] == SUPER


def test_partner_audit_trail_is_per_partner_and_paged(env):
    client, db = env
    sa = _sa(client)
    a, _ = _partner(client, sa, "au1@aff.example", name="Au1")
    b, _ = _partner(client, sa, "au2@aff.example", name="Au2")
    sa.post(f"{BASE}/{a['id']}/notify", json={"title": "Only A", "message": "hi"})
    ra = sa.get(f"{BASE}/{a['id']}/audit?limit=200").json()
    acts = {i["action"] for i in ra["items"]}
    assert {"partner.created", "partner.notification.sent"} <= acts
    assert all(i["category"] == "partner" for i in ra["items"])
    assert ra["total"] == db.audit_logs.count_documents({"category": "partner", "$or": [
        {"details.partner_id": a["id"]}, {"resource_type": "partner", "resource_id": a["id"]}]})
    rb = sa.get(f"{BASE}/{b['id']}/audit?limit=200").json()
    assert "partner.notification.sent" not in {i["action"] for i in rb["items"]}
    assert all(i["details"].get("partner_id") in (b["id"], None) for i in rb["items"])
    one = sa.get(f"{BASE}/{a['id']}/audit?limit=1").json()
    assert len(one["items"]) == 1 and one["pages"] == ra["total"]
    # /api/super-admin/* is Super Admin only (app/main.py), even for partners.view staff
    assert _staff(client, db, "support_admin").get(f"{BASE}/{a['id']}/audit").status_code == 403
    assert _staff(client, db, "viewer").get(f"{BASE}/{a['id']}/audit").status_code == 403


def test_partner_clicks_and_referrals_views(env):
    client, db = env
    sa = _sa(client)
    a, _ = _partner(client, sa, "cl@aff.example", name="Clk")
    b, _ = _partner(client, sa, "cl2@aff.example", name="Clk2")
    v = TestClient(client.app)
    v.get(f"/r/{a['referral_code']}", follow_redirects=False)
    v.get(f"/r/{a['referral_code']}", follow_redirects=False)
    clicks = sa.get(f"{BASE}/{a['id']}/clicks").json()
    assert clicks["total"] == 2
    assert sorted(c["unique"] for c in clicks["items"]) == [False, True]
    assert all(c["referral_code"] == a["referral_code"] and c["ip_hash"] and "ip" not in c for c in clicks["items"])
    assert sa.get(f"{BASE}/{a['id']}/clicks?suspicious=true").json()["total"] == 0
    assert sa.get(f"{BASE}/{b['id']}/clicks").json()["total"] == 0
    # referrals
    _signup_via(client, a, "own@clk.example", "Clk Cafe")
    refs = sa.get(f"{BASE}/{a['id']}/referrals").json()
    assert refs["total"] == 1
    row = refs["items"][0]
    assert row["company"] == "Clk Cafe" and row["stage"] == "signed_up" and row["partner_id"] == a["id"]
    assert row["organization_status"] == "pending" and "payments_seen" not in row
    assert sa.get(f"{BASE}/{a['id']}/referrals?stage=customer").json()["total"] == 0
    assert sa.get(f"{BASE}/{a['id']}/referrals?stage=signed_up").json()["total"] == 1
    assert sa.get(f"{BASE}/{b['id']}/referrals").json()["total"] == 0
    assert _staff(client, db, "technical_admin").get(f"{BASE}/{a['id']}/referrals").status_code == 403


def test_wallet_adjustments_and_ledger(env):
    client, db = env
    sa = _sa(client)
    p, portal = _partner(client, sa, "adj@aff.example", name="Adj")
    url = f"{BASE}/{p['id']}/adjustments"
    assert sa.post(url, json={"amount": 0, "reason": "x"}).status_code == 422
    assert sa.post(url, json={"amount": 2_000_000, "reason": "x"}).status_code == 422
    assert sa.post(url, json={"amount": 25, "reason": "  "}).status_code == 422
    assert sa.post(f"{BASE}/{ObjectId()}/adjustments", json={"amount": 5, "reason": "x"}).status_code == 404
    assert _staff(client, db, "billing_admin").post(url, json={"amount": 5, "reason": "x"}).status_code == 403
    r = sa.post(url, json={"amount": 25.5, "currency": "usd", "reason": "Event bonus"})
    assert r.status_code == 200, r.text
    adj = r.json()["adjustment"]
    assert adj["kind"] == "adjustment" and adj["status"] == "payable" and adj["currency"] == "USD"
    assert adj["amount"] == 25.5
    assert sa.post(url, json={"amount": -5.5, "reason": "Correction"}).status_code == 200
    led = sa.get(f"{BASE}/{p['id']}/ledger").json()
    assert led["total"] == 2 and [t["type"] for t in led["items"]] == ["adjustment", "adjustment"]
    assert sorted(t["amount"] for t in led["items"]) == [-5.5, 25.5]
    assert led["balances"]["USD"]["available"] == 20.0
    assert portal.get("/api/partner/v1/wallet").json()["wallet"]["balances"]["USD"]["available"] == 20.0
    assert db.audit_logs.count_documents({"action": "partner.wallet.adjusted", "details.partner_id": p["id"]}) == 2
    assert db.notifications.find_one({"user_id": p["user_id"], "title": "Wallet adjustment"})
    assert sa.get(f"{BASE}/{p['id']}/ledger?limit=1&page=2").json()["items"][0]["amount"] == 25.5
    assert sa.get(f"{BASE}/{ObjectId()}/ledger").status_code == 404
    assert _staff(client, db, "billing_admin").get(f"{BASE}/{p['id']}/ledger").status_code == 403


# ══ commissions ═════════════════════════════════════════════════════════════

def test_commission_approve_and_make_payable(env):
    client, db = env
    sa = _sa(client)
    other_day = (_now().weekday() + 3) % 7
    _settings(sa, payout_schedule="weekly", payout_day=other_day)        # approved stays approved
    p, portal = _partner(client, sa, "com@aff.example", name="Com")
    q = _seed_commission(db, p["id"], status="qualified", amount=40.0, manual=True)
    assert sa.post(f"{BASE}/commissions/{q}/payable").status_code == 409     # not approved yet
    assert _staff(client, db, "operations_admin").post(f"{BASE}/commissions/{q}/approve").status_code == 403
    r = sa.post(f"{BASE}/commissions/{q}/approve")
    assert r.status_code == 200, r.text
    c = r.json()["commission"]
    assert c["status"] == "approved" and c["requires_manual_approval"] is False and c["approved_at"]
    assert c["history"][-1]["by"] == SUPER
    a = _audit(db, "partner.commission.approved", resource_id=q)
    assert a["details"]["early"] is False and a["details"]["amount"] == 40.0
    assert db.partner_wallet_transactions.find_one({"commission_id": q, "type": "commission_approved"})
    assert sa.post(f"{BASE}/commissions/{q}/approve").status_code == 409     # approved -> approve
    r = sa.post(f"{BASE}/commissions/{q}/payable")
    assert r.status_code == 200 and r.json()["commission"]["status"] == "payable"
    assert db.partner_wallet_transactions.find_one({"commission_id": q, "type": "commission_payable"})
    assert _audit(db, "partner.commission.payable", resource_id=q)
    assert sa.post(f"{BASE}/commissions/{q}/payable").status_code == 409
    assert portal.get("/api/partner/v1/wallet").json()["wallet"]["balances"]["USD"]["available"] == 40.0
    # early approval of a pending commission is allowed and recorded as such
    pend = _seed_commission(db, p["id"], status="pending", amount=10.0, hold_until=_now() + timedelta(days=9))
    assert sa.post(f"{BASE}/commissions/{pend}/approve").json()["commission"]["status"] == "approved"
    assert _audit(db, "partner.commission.approved", resource_id=pend)["details"]["early"] is True
    # with the on-request schedule an approval is payable at once
    _settings(sa, payout_schedule="on_request")
    q2 = _seed_commission(db, p["id"], status="qualified", amount=5.0)
    assert sa.post(f"{BASE}/commissions/{q2}/approve").json()["commission"]["status"] == "payable"
    # reversed / paid / unknown commissions cannot be approved
    for st in ("reversed", "paid", "processing"):
        cid = _seed_commission(db, p["id"], status=st, amount=1.0)
        assert sa.post(f"{BASE}/commissions/{cid}/approve").status_code == 409, st
        assert sa.post(f"{BASE}/commissions/{cid}/payable").status_code == 409, st
    assert sa.post(f"{BASE}/commissions/{ObjectId()}/approve").status_code == 404
    assert sa.post(f"{BASE}/commissions/not-an-id/payable").status_code == 404


def test_run_commission_lifecycle(env):
    client, db = env
    sa = _sa(client)
    p, _ = _partner(client, sa, "life@aff.example", name="Life")
    due = _seed_commission(db, p["id"], status="pending", amount=12.0)
    held = _seed_commission(db, p["id"], status="pending", amount=8.0, manual=True)
    later = _seed_commission(db, p["id"], status="pending", amount=3.0, hold_until=_now() + timedelta(days=5))
    assert _staff(client, db, "operations_admin").post(f"{BASE}/commissions/run-lifecycle").status_code == 403
    r = sa.post(f"{BASE}/commissions/run-lifecycle")
    assert r.status_code == 200 and r.json()["qualified"] == 2
    assert _com(db, due)["status"] == "payable"          # pending -> qualified -> approved -> payable
    assert _com(db, held)["status"] == "qualified"       # flagged: held for review, amount untouched
    assert _com(db, held)["amount"] == 8.0
    assert _com(db, later)["status"] == "pending"        # still in its qualification period
    assert [h["status"] for h in _com(db, due)["history"]][-3:] == ["qualified", "approved", "payable"]
    a = _audit(db, "partner.commission.lifecycle_run")
    assert a and a["details"]["qualified"] == 2 and a["actor_email"] == SUPER
    assert sa.post(f"{BASE}/commissions/run-lifecycle").json()["qualified"] == 0
    assert sa.get(f"{BASE}/{p['id']}/ledger").json()["balances"]["USD"]["available"] == 12.0


def test_reverse_commission_partial_full_and_clawback(env):
    client, db = env
    sa = _sa(client)
    p, portal = _partner(client, sa, "rev@aff.example", name="Rev")
    c = _seed_commission(db, p["id"], status="payable", amount=50.0)
    url = f"{BASE}/commissions/{c}/reverse"
    assert sa.post(url, json={"reason": ""}).status_code == 422
    assert sa.post(url, json={"reason": "x", "amount": 60}).status_code == 422
    assert sa.post(url, json={"reason": "x", "amount": -1}).status_code == 422
    assert _staff(client, db, "billing_admin").post(url, json={"reason": "x"}).status_code == 403
    r = sa.post(url, json={"reason": "Partial dispute", "amount": 20})
    assert r.status_code == 200, r.text
    got = r.json()["commission"]
    assert got["status"] == "payable" and got["amount"] == 30.0 and got["reversed_amount"] == 20.0
    assert got["reversal_source"] == "manual"
    tx = db.partner_wallet_transactions.find_one({"commission_id": c, "type": "commission_reversed"})
    assert tx["amount"] == -20.0
    r = sa.post(url, json={"reason": "Fraud confirmed"})
    got = r.json()["commission"]
    assert got["status"] == "reversed" and got["amount"] == 0.0 and got["reversed_amount"] == 50.0
    assert sa.post(url, json={"reason": "again"}).status_code == 409             # already reversed
    # paid history is never rewritten: a negative clawback entry is created
    paid = _seed_commission(db, p["id"], status="paid", amount=30.0)
    r = sa.post(f"{BASE}/commissions/{paid}/reverse", json={"reason": "Refund", "amount": 10})
    assert r.status_code == 200
    assert r.json()["commission"]["status"] == "paid" and r.json()["commission"]["clawed_back"] == 10.0
    claw = db.partner_commissions.find_one({"kind": "clawback", "clawback_of": paid})
    assert claw["amount"] == -10.0 and claw["status"] == "payable"
    assert sa.post(f"{BASE}/commissions/{paid}/reverse", json={"reason": "x", "amount": 25}).status_code == 422
    a = _audit(db, "partner.commission.reversed", resource_id=paid)
    assert a["details"]["clawback"] == 10.0 and a["details"]["reversed"] == 0.0
    bal = portal.get("/api/partner/v1/wallet").json()["wallet"]["balances"]["USD"]
    assert bal["available"] == -10.0 and bal["paid"] == 30.0 and bal["reversed"] == 60.0
    # clawbacks and adjustments are not reversible commissions
    assert sa.post(f"{BASE}/commissions/{claw['_id']}/reverse", json={"reason": "x"}).status_code == 409
    assert sa.post(f"{BASE}/commissions/{ObjectId()}/reverse", json={"reason": "x"}).status_code == 404
    assert db.notifications.find_one({"user_id": p["user_id"], "title": "Commission reversed"})


def test_reversal_takes_commission_out_of_a_requested_payout(env):
    client, db = env
    sa = _sa(client)
    _settings(sa, min_payout=1)
    p, portal = _partner(client, sa, "out2@aff.example", name="OutPo")
    c1 = _seed_commission(db, p["id"], status="payable", amount=30.0)
    c2 = _seed_commission(db, p["id"], status="payable", amount=20.0)
    po = portal.post("/api/partner/v1/payouts", json={}).json()["payout"]
    assert po["amount"] == 50.0
    assert sa.post(f"{BASE}/commissions/{c2}/reverse", json={"reason": "Refund"}).status_code == 200
    doc = db.partner_payouts.find_one({"_id": ObjectId(po["id"])})
    assert doc["amount"] == 30.0 and doc["commission_ids"] == [c1] and doc["status"] == "requested"
    assert _com(db, c2)["status"] == "reversed" and _com(db, c2)["payout_id"] is None
    assert sa.post(f"{BASE}/commissions/{c1}/reverse", json={"reason": "Refund"}).status_code == 200
    doc = db.partner_payouts.find_one({"_id": ObjectId(po["id"])})
    assert doc["status"] == "cancelled" and doc["amount"] == 0.0


# ══ payouts ═════════════════════════════════════════════════════════════════

def test_payout_workflow_lists_and_invalid_transitions(env):
    client, db = env
    sa = _sa(client)
    _settings(sa, min_payout=1)
    p, portal = _partner(client, sa, "po@aff.example", name="Po")
    other, _ = _partner(client, sa, "po2@aff.example", name="Po2")
    c1 = _seed_commission(db, p["id"], status="payable", amount=30.0)
    c2 = _seed_commission(db, p["id"], status="payable", amount=20.0)
    po = portal.post("/api/partner/v1/payouts", json={}).json()["payout"]
    assert po["status"] == "requested" and po["amount"] == 50.0
    # Super Admin queue: unmasked payout details (they process the transfer)
    lst = sa.get(f"{BASE}/payouts?status=requested").json()
    assert lst["total"] == 1 and lst["counts"]["requested"] == 1
    row = lst["items"][0]
    assert row["partner_email"] == "po@aff.example" and row["partner_name"] == "Po Co"
    assert row["payout_snapshot"]["paypal_email"] == "pay@aff.example"
    assert sa.get(f"{BASE}/payouts?partner_id={other['id']}").json()["total"] == 0
    mine = sa.get(f"{BASE}/{p['id']}/payouts").json()
    assert mine["total"] == 1 and mine["items"][0]["payout_snapshot"]["paypal_email"] == "pay@aff.example"
    assert sa.get(f"{BASE}/{other['id']}/payouts").json()["total"] == 0
    # platform staff never reach the Super Admin payout queue
    assert _staff(client, db, "billing_admin").get(f"{BASE}/payouts").status_code == 403
    url = f"{BASE}/payouts/{po['id']}"
    assert sa.post(url + "/explode", json={}).status_code == 404
    assert sa.post(url + "/process", json={}).status_code == 409                 # requested -> processing
    assert sa.post(url + "/mark_paid", json={"reference": "UTR-1"}).status_code == 409
    assert sa.post(f"{BASE}/payouts/{ObjectId()}/approve", json={}).status_code == 404
    assert _staff(client, db, "billing_admin").post(url + "/approve", json={}).status_code == 403
    assert sa.post(url + "/review", json={}).json()["payout"]["status"] == "under_review"
    assert sa.post(url + "/review", json={}).status_code == 409
    assert sa.post(url + "/approve", json={}).json()["payout"]["status"] == "approved"
    assert sa.post(url + "/process", json={}).json()["payout"]["status"] == "processing"
    assert sa.post(url + "/mark_paid", json={"reference": "  "}).status_code == 422
    r = sa.post(url + "/mark_paid", json={"reference": "UTR-778"})
    assert r.status_code == 200
    paid = r.json()["payout"]
    assert paid["status"] == "paid" and paid["reference"] == "UTR-778" and paid["paid_at"]
    assert [h["status"] for h in paid["history"]] == ["requested", "under_review", "approved", "processing", "paid"]
    assert {_com(db, c1)["status"], _com(db, c2)["status"]} == {"paid"}
    bal = sa.get(f"{BASE}/{p['id']}/ledger").json()["balances"]["USD"]
    assert bal["paid"] == 50.0 and bal["available"] == 0.0 and bal["processing"] == 0.0
    for step in ("review", "approve", "process", "mark_paid"):
        assert _audit(db, f"partner.payout.{step}", resource_id=po["id"]), step
    assert sa.post(url + "/reject", json={"reason": "late"}).status_code == 409   # paid is final
    assert sa.post(url + "/fail", json={"reason": "late"}).status_code == 409
    assert db.notifications.find_one({"user_id": p["user_id"], "title": "Payout sent"})
    assert sa.get(f"{BASE}/payouts").json()["counts"]["paid"] == 1


def test_rejected_payout_releases_the_money(env):
    client, db = env
    sa = _sa(client)
    _settings(sa, min_payout=1)
    p, portal = _partner(client, sa, "rej@aff.example", name="Rej")
    c = _seed_commission(db, p["id"], status="payable", amount=40.0)
    po = portal.post("/api/partner/v1/payouts", json={}).json()["payout"]
    url = f"{BASE}/payouts/{po['id']}"
    assert _com(db, c)["status"] == "processing"
    assert sa.post(url + "/reject", json={"reason": " "}).status_code == 422
    r = sa.post(url + "/reject", json={"reason": "Bank details unclear"})
    assert r.status_code == 200 and r.json()["payout"]["status"] == "rejected"
    assert r.json()["payout"]["failure_reason"] == "Bank details unclear"
    com = _com(db, c)
    assert com["status"] == "payable" and com["payout_id"] is None
    assert db.partner_wallet_transactions.find_one({"payout_id": po["id"], "type": "payout_released"})["amount"] == 40.0
    assert portal.get("/api/partner/v1/wallet").json()["wallet"]["balances"]["USD"]["available"] == 40.0
    assert sa.post(url + "/approve", json={}).status_code == 409                   # rejected is final
    assert db.notifications.find_one({"user_id": p["user_id"], "title": "Payout rejected"})


def test_partner_cancels_own_requested_payout_only(env):
    client, db = env
    sa = _sa(client)
    _settings(sa, min_payout=1)
    a, pa = _partner(client, sa, "can1@aff.example", name="Can1")
    _, pb = _partner(client, sa, "can2@aff.example", name="Can2")
    c = _seed_commission(db, a["id"], status="payable", amount=30.0)
    po = pa.post("/api/partner/v1/payouts", json={}).json()["payout"]
    # another partner's payout: 404, recorded as a cross-partner probe
    b_id = db.partners.find_one({"email": "can2@aff.example"})["_id"]
    r = pb.post(f"/api/partner/v1/payouts/{po['id']}/cancel")
    assert r.status_code == 404
    assert db.partner_fraud_flags.find_one({"type": "cross_partner_access", "partner_id": str(b_id)})
    assert db.security_events.find_one({"type": "cross_partner_access", "details.resource_id": po["id"]})
    assert db.partner_payouts.find_one({"_id": ObjectId(po["id"])})["status"] == "requested"
    assert pa.post(f"/api/partner/v1/payouts/{ObjectId()}/cancel").status_code == 404
    r = pa.post(f"/api/partner/v1/payouts/{po['id']}/cancel")
    assert r.status_code == 200, r.text
    assert r.json()["payout"]["status"] == "cancelled"
    com = _com(db, c)
    assert com["status"] == "payable" and com["payout_id"] is None
    assert db.partner_wallet_transactions.find_one({"payout_id": po["id"], "type": "payout_released"})
    assert _audit(db, "partner.payout.cancelled", resource_id=po["id"])["actor_email"] == "can1@aff.example"
    assert pa.post(f"/api/partner/v1/payouts/{po['id']}/cancel").status_code == 409
    # once the Super Admin approved it, the partner can no longer cancel
    po2 = pa.post("/api/partner/v1/payouts", json={}).json()["payout"]
    sa.post(f"{BASE}/payouts/{po2['id']}/approve", json={})
    assert pa.post(f"/api/partner/v1/payouts/{po2['id']}/cancel").status_code == 409
    assert db.partner_payouts.find_one({"_id": ObjectId(po2["id"])})["status"] == "approved"
    # the permission is required
    sa.patch(f"{BASE}/{a['id']}", json={"permissions": ["dashboard.view"]})
    assert pa.post(f"/api/partner/v1/payouts/{po2['id']}/cancel").status_code == 403


# ══ rules, coupons, pricing ═════════════════════════════════════════════════

def test_update_commission_rule(env):
    client, db = env
    sa = _sa(client)
    p, _ = _partner(client, sa, "rule@aff.example", name="Rule")
    r = sa.post(f"{BASE}/commission-rules", json={"name": "Spring", "scope": "partner", "partner_id": p["id"],
                                                  "commission_type": "percentage", "value": 25})
    rid = r.json()["rule"]["id"]
    url = f"{BASE}/commission-rules/{rid}"
    r = sa.patch(url, json={"value": 30, "cap_amount": 500})
    assert r.status_code == 200, r.text
    rule = r.json()["rule"]
    assert rule["value"] == 30 and rule["cap_amount"] == 500 and rule["name"] == "Spring"
    assert rule["scope"] == "partner" and rule["partner_id"] == p["id"]          # merged, not replaced
    assert sa.get(f"{BASE}/{p['id']}").json()["partner"]["rule"]["value"] == 30   # the rule that applies
    assert sa.patch(url, json={"value": 150}).status_code == 422
    assert sa.patch(url, json={"commission_type": "bogus"}).status_code == 422
    assert sa.patch(url, json={"scope": "tier", "tier_id": str(ObjectId())}).status_code == 422
    assert db.partner_commission_rules.find_one({"_id": ObjectId(rid)})["value"] == 30
    a = _audit(db, "partner.commission_rule.updated", resource_id=rid)
    assert a["details"]["value"] == 30 and a["details"]["partner_id"] == p["id"]
    # disabling it falls back to the default global rule
    assert sa.patch(url, json={"status": "inactive"}).json()["rule"]["status"] == "inactive"
    assert sa.get(f"{BASE}/{p['id']}").json()["partner"]["rule"]["scope"] == "global"
    assert sa.patch(f"{BASE}/commission-rules/{ObjectId()}", json={"value": 5}).status_code == 404
    assert _staff(client, db, "billing_admin").patch(url, json={"value": 5}).status_code == 403


def test_super_admin_updates_a_coupon(env):
    client, db = env
    sa = _sa(client)
    p, portal = _partner(client, sa, "cp@aff.example", name="Cp")
    cp = sa.post(f"{BASE}/coupons", json={"partner_id": p["id"], "code": "SAVE15", "discount_value": 15}).json()["coupon"]
    url = f"{BASE}/coupons/{cp['id']}"
    r = sa.patch(url, json={"discount_value": 25, "max_redemptions": 10, "code": "HACKED",
                            "eligibility": "new_customers"})
    assert r.status_code == 200, r.text
    c = r.json()["coupon"]
    assert c["discount_value"] == 25 and c["max_redemptions"] == 10 and c["eligibility"] == "new_customers"
    assert c["code"] == "SAVE15" and c["partner_id"] == p["id"]                # code / owner never change
    assert sa.patch(url, json={"discount_value": 120}).status_code == 422
    assert sa.patch(url, json={"eligibility": "vip"}).status_code == 422
    assert sa.patch(url, json={"commission_basis": "half"}).status_code == 422
    assert sa.patch(url, json={"expires_at": "tomorrow"}).status_code == 422
    assert db.partner_coupons.find_one({"_id": ObjectId(cp["id"])})["discount_value"] == 25
    assert sa.patch(url, json={"status": "paused"}).json()["coupon"]["status"] == "disabled"
    assert _audit(db, "partner.coupon.updated", resource_id=cp["id"])
    assert db.notifications.find_one({"user_id": p["user_id"], "title": "Your coupon was updated"})
    assert sa.patch(f"{BASE}/coupons/{ObjectId()}", json={"discount_value": 5}).status_code == 404
    assert _staff(client, db, "billing_admin").patch(url, json={"discount_value": 5}).status_code == 403


def test_partner_records_name_the_super_admin_by_email_not_the_session_object(env):
    """created_by / status_history.by must be the Super Admin's email. They
    are written from the route's actor dict with ``getattr(actor, "email",
    None) or str(actor)`` (getattr is always None for a dict), so the stored
    value is ``str(dict)`` holding the Super Admin's user id and live session
    id — and partners read their coupons in full (GET /api/partner/v1/coupons)."""
    client, db = env
    sa = _sa(client)
    p, portal = _partner(client, sa, "who@aff.example", name="Who")
    sa.put(f"{BASE}/settings", json={"partner_coupons_enabled": True})
    sa.patch(f"{BASE}/{p['id']}", json={"permissions": p["permissions"] + ["coupons.manage"]})
    cp = sa.post(f"{BASE}/coupons", json={"partner_id": p["id"], "code": "WHO10", "discount_value": 10}).json()["coupon"]
    r = sa.patch(f"{BASE}/coupons/{cp['id']}", json={"discount_value": 12})
    assert r.status_code == 200
    rule = sa.post(f"{BASE}/commission-rules", json={"scope": "partner", "partner_id": p["id"],
                                                     "commission_type": "percentage", "value": 22}).json()["rule"]
    r2 = sa.patch(f"{BASE}/commission-rules/{rule['id']}", json={"value": 23})
    assert r2.status_code == 200
    seen = portal.get("/api/partner/v1/coupons").json()["items"][0]
    sa.post(f"{BASE}/{p['id']}/suspend", json={"reason": "Review"})
    recorded = {
        "coupon.created_by": r.json()["coupon"]["created_by"],
        "commission_rule.created_by": r2.json()["rule"]["created_by"],
        "partner.status_history.by": db.partners.find_one({"_id": ObjectId(p["id"])})["status_history"][-1]["by"],
    }
    assert recorded == {k: SUPER for k in recorded}
    assert "session_id" not in str(seen)                    # the partner never sees a Super Admin session id


def test_partner_updates_own_coupon_only(env):
    client, db = env
    sa = _sa(client)
    _settings(sa, partner_coupons_enabled=True, max_partner_coupon_percent=20)
    a, pa = _partner(client, sa, "pc1@aff.example", name="Pc1")
    b, pb = _partner(client, sa, "pc2@aff.example", name="Pc2")
    for p in (a, b):
        sa.patch(f"{BASE}/{p['id']}", json={"permissions": p["permissions"] + ["coupons.manage"]})
    mine = pa.post("/api/partner/v1/coupons", json={"code": "ANA10", "discount_value": 10}).json()["coupon"]
    url = f"/api/partner/v1/coupons/{mine['id']}"
    r = pa.patch(url, json={"discount_type": "percentage", "discount_value": 15, "max_redemptions": 5,
                            "status": "active"})
    assert r.status_code == 200, r.text
    c = r.json()["coupon"]
    assert c["discount_value"] == 15 and c["max_redemptions"] == 5 and c["code"] == "ANA10"
    assert c["commission_basis"] == "paid"
    assert pa.patch(url, json={"discount_value": 25}).status_code == 422            # program cap 20 %
    assert pa.patch(url, json={"discount_type": "fixed", "discount_value": 5}).status_code == 422
    assert db.partner_coupons.find_one({"_id": ObjectId(mine["id"])})["discount_value"] == 15
    a_audit = _audit(db, "partner.coupon.updated", resource_id=mine["id"])
    assert a_audit["actor_email"] == "pc1@aff.example"
    # another partner's coupon: 404 + cross-partner flag, nothing changed
    assert pb.patch(url, json={"discount_value": 5}).status_code == 404
    assert db.partner_fraud_flags.find_one({"type": "cross_partner_access", "partner_id": b["id"]})
    assert db.partner_coupons.find_one({"_id": ObjectId(mine["id"])})["discount_value"] == 15
    # a coupon issued by LeadAI is not the partner's to edit
    issued = sa.post(f"{BASE}/coupons", json={"partner_id": a["id"], "code": "LEADAI20",
                                              "discount_value": 20}).json()["coupon"]
    assert pa.patch(f"/api/partner/v1/coupons/{issued['id']}", json={"discount_value": 5}).status_code == 403
    # program switch off: refused
    _settings(sa, partner_coupons_enabled=False)
    assert pa.patch(url, json={"discount_value": 12}).status_code == 403


def test_partner_coupon_edit_keeps_the_super_admins_commission_basis(env):
    """docs/PARTNERS.md + coupons.save_coupon: only the Super Admin decides how
    a coupon affects commission. A partner editing their own coupon must not
    undo a Super Admin's "no commission" / eligibility decision on it."""
    client, db = env
    sa = _sa(client)
    _settings(sa, partner_coupons_enabled=True, max_partner_coupon_percent=20)
    p, portal = _partner(client, sa, "basis@aff.example", name="Basis")
    sa.patch(f"{BASE}/{p['id']}", json={"permissions": p["permissions"] + ["coupons.manage"]})
    cp = portal.post("/api/partner/v1/coupons", json={"code": "BASIS10", "discount_value": 10}).json()["coupon"]
    r = sa.patch(f"{BASE}/coupons/{cp['id']}", json={"commission_basis": "none", "eligibility": "referred_customers"})
    assert r.status_code == 200 and r.json()["coupon"]["commission_basis"] == "none"
    r = portal.patch(f"/api/partner/v1/coupons/{cp['id']}", json={"discount_type": "percentage", "discount_value": 12,
                                                                  "status": "active"})
    assert r.status_code == 200
    saved = db.partner_coupons.find_one({"_id": ObjectId(cp["id"])})
    assert saved["discount_value"] == 12
    assert saved["commission_basis"] == "none"
    assert saved["eligibility"] == "referred_customers"


def test_update_partner_pricing_rule(env):
    client, db = env
    sa = _sa(client)
    p, _ = _partner(client, sa, "pr@aff.example", name="Pr")
    rule = sa.post(f"{BASE}/pricing", json={"name": "Deal", "scope": "partner", "partner_id": p["id"],
                                            "discount_type": "percentage", "discount_value": 10}).json()["rule"]
    url = f"{BASE}/pricing/{rule['id']}"
    r = sa.patch(url, json={"discount_value": 12.5, "first_payment_only": True})
    assert r.status_code == 200, r.text
    got = r.json()["rule"]
    assert got["discount_value"] == 12.5 and got["first_payment_only"] is True
    assert got["name"] == "Deal" and got["partner_id"] == p["id"] and got["scope"] == "partner"
    assert sa.patch(url, json={"discount_value": 100}).status_code == 422
    assert sa.patch(url, json={"discount_type": "bogo"}).status_code == 422
    assert sa.patch(url, json={"scope": "tier", "tier_id": "nope"}).status_code == 422
    assert db.partner_pricing.find_one({"_id": ObjectId(rule["id"])})["discount_value"] == 12.5
    assert [x["discount_value"] for x in sa.get(f"{BASE}/pricing").json()["items"]] == [12.5]
    assert _audit(db, "partner.pricing.updated", resource_id=rule["id"])
    assert sa.patch(f"{BASE}/pricing/{ObjectId()}", json={"discount_value": 5}).status_code == 404
    assert _staff(client, db, "billing_admin").patch(url, json={"discount_value": 5}).status_code == 403


# ══ marketing asset files ═══════════════════════════════════════════════════

def test_asset_file_upload_and_admin_download(env):
    client, db = env
    sa = _sa(client)
    _, portal = _partner(client, sa, "as@aff.example", name="As")
    asset = sa.post(f"{BASE}/assets", json={"title": "Logo", "category": "logos"}).json()["asset"]
    fu = f"{BASE}/assets/{asset['id']}/file"
    r = sa.get(fu)
    assert r.status_code == 404 and r.json()["detail"] == "This asset has no file"
    assert sa.post(fu, files={"file": ("x.svg", b"<svg/>", "image/svg+xml")}).status_code == 415
    assert sa.post(fu, files={"file": ("fake.png", b"not a png", "image/png")}).status_code == 400
    assert sa.post(fu, files={"file": ("empty.png", b"", "image/png")}).status_code == 422
    assert sa.post(f"{BASE}/assets/{ObjectId()}/file", files={"file": ("l.png", PNG, "image/png")}).status_code == 404
    assert _staff(client, db, "billing_admin").post(fu, files={"file": ("l.png", PNG, "image/png")}).status_code == 403
    r = sa.post(fu, files={"file": ("logo.png", PNG, "image/png")})
    assert r.status_code == 200, r.text
    out = r.json()["asset"]
    assert out["has_file"] and out["file_size"] == len(PNG) and out["file_type"] == "image/png"
    assert out["file_name"] == "logo.png" and out["file_path"] == fu and "storage_key" not in out
    doc = db.partner_marketing_assets.find_one({"_id": ObjectId(asset["id"])})
    assert doc["storage_key"] and doc["file_url"] is None
    a = _audit(db, "partner.asset.file_uploaded", resource_id=asset["id"])
    assert a["details"]["size"] == len(PNG)
    r = sa.get(fu)
    assert r.status_code == 200 and r.content == PNG
    assert r.headers["content-type"] == "image/png" and r.headers["content-disposition"].startswith("inline")
    assert "no-store" in r.headers["cache-control"] and "private" in r.headers["cache-control"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert _staff(client, db, "billing_admin").get(fu).status_code == 403
    assert portal.get(fu).status_code == 401                                         # partner session: no
    assert TestClient(client.app).get(fu).status_code == 401
    assert sa.get(f"{BASE}/assets/{ObjectId()}/file").status_code == 404
    # a provider-hosted file (S3 / Cloudinary) is a redirect
    db.partner_marketing_assets.update_one({"_id": ObjectId(asset["id"])},
                                           {"$set": {"file_url": "https://cdn.example/logo.png"}})
    r = sa.get(fu, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "https://cdn.example/logo.png"


# ══ deals, fraud flags, referral relationships ══════════════════════════════

def test_super_admin_deal_decisions(env):
    client, db = env
    sa = _sa(client)
    p, portal = _partner(client, sa, "deal@aff.example", name="Dealer")

    def deal(company, email):
        r = portal.post("/api/partner/v1/deals", json={"company": company, "contact_name": "Ravi",
                                                      "contact_email": email})
        assert r.status_code == 200, r.text
        return r.json()["deal"]["id"]
    d1, d2, d3 = deal("Alpha Retail", "a@alpha.example"), deal("Beta Foods", "b@beta.example"), \
        deal("Gamma Gyms", "g@gamma.example")
    assert sa.post(f"{BASE}/deals/{d1}/escalate", json={}).status_code == 404
    assert sa.post(f"{BASE}/deals/{d1}/reject", json={"note": " "}).status_code == 422
    assert _staff(client, db, "billing_admin").post(f"{BASE}/deals/{d1}/approve", json={}).status_code == 403
    r = sa.post(f"{BASE}/deals/{d1}/approve", json={"note": "Looks real"})
    assert r.status_code == 200, r.text
    got = r.json()["deal"]
    assert got["status"] == "approved" and got["reviewed_by"] == SUPER
    until = _aware(db.partner_deals.find_one({"_id": ObjectId(d1)})["protected_until"])
    assert abs((until - (_now() + timedelta(days=90))).total_seconds()) < 120   # deal_protection_days
    assert _audit(db, "partner.deal.approved", resource_id=d1)
    assert db.notifications.find_one({"user_id": p["user_id"], "title": "Deal approved"})
    assert sa.post(f"{BASE}/deals/{d1}/approve", json={}).status_code == 409       # approved -> approve
    assert sa.post(f"{BASE}/deals/{d1}/won", json={}).json()["deal"]["status"] == "won"
    for decision in ("approve", "reject", "won", "lost"):                          # won is final
        assert sa.post(f"{BASE}/deals/{d1}/{decision}", json={"note": "x"}).status_code == 409, decision
    assert sa.post(f"{BASE}/deals/{d2}/lost", json={"note": "Budget cut"}).json()["deal"]["status"] == "lost"
    r = sa.post(f"{BASE}/deals/{d3}/reject", json={"note": "Duplicate"})
    assert r.json()["deal"]["status"] == "rejected" and r.json()["deal"]["review_note"] == "Duplicate"
    hist = db.partner_deals.find_one({"_id": ObjectId(d1)})["history"]
    assert [h["status"] for h in hist][-2:] == ["approved", "won"]
    assert sa.post(f"{BASE}/deals/{ObjectId()}/approve", json={}).status_code == 404


def test_reseller_deal_conversion_rules(env):
    client, db = env
    sa = _sa(client)
    r1, p1 = _partner(client, sa, "rs1@res.example", ptype="reseller", name="Rs1")
    r2, p2 = _partner(client, sa, "rs2@res.example", ptype="reseller", name="Rs2")
    _, aff = _partner(client, sa, "af@aff.example", name="Af")
    d = p1.post("/api/partner/v1/deals", json={"company": "Gym Plus", "contact_name": "Gita",
                                              "contact_email": "gita@gymplus.example"}).json()["deal"]
    noname = p1.post("/api/partner/v1/deals", json={"company": "No Name Ltd",
                                                   "contact_email": "x@noname.example"}).json()["deal"]
    lost = p1.post("/api/partner/v1/deals", json={"company": "Lost Ltd", "contact_name": "L",
                                                 "contact_email": "l@lost.example"}).json()["deal"]
    own = aff.post("/api/partner/v1/deals", json={"company": "Aff Prospect", "contact_name": "A",
                                                 "contact_email": "a@affp.example"}).json()["deal"]
    # another reseller's deal: 404 + cross-partner flag
    assert p2.post(f"/api/partner/v1/deals/{d['id']}/convert").status_code == 404
    assert db.partner_fraud_flags.find_one({"type": "cross_partner_access", "partner_id": r2["id"]})
    # affiliates may not onboard customers
    assert aff.post(f"/api/partner/v1/deals/{own['id']}/convert").status_code == 403
    assert p1.post(f"/api/partner/v1/deals/{noname['id']}/convert").status_code == 422
    sa.post(f"{BASE}/deals/{lost['id']}/lost", json={"note": "gone"})
    assert p1.post(f"/api/partner/v1/deals/{lost['id']}/convert").status_code == 409
    assert p1.post(f"/api/partner/v1/deals/{ObjectId()}/convert").status_code == 404
    r = p1.post(f"/api/partner/v1/deals/{d['id']}/convert")
    assert r.status_code == 200, r.text
    cust = r.json()["customer"]
    org = db.organizations.find_one({"_id": ObjectId(cust["organization_id"])})
    assert org["status"] == "pending" and org["name"] == "Gym Plus"
    ref = db.partner_referrals.find_one({"organization_id": cust["organization_id"]})
    assert ref["partner_id"] == r1["id"] and ref["managed"] is True and str(ref["_id"]) == cust["referral_id"]
    saved = db.partner_deals.find_one({"_id": ObjectId(d["id"])})
    assert saved["organization_id"] == cust["organization_id"] and saved["referral_id"] == cust["referral_id"]
    assert saved["history"][-1]["note"] == "converted to customer"
    assert db.demo_requests.find_one({"_id": ObjectId(cust["demo_request_id"])})["status"] == "pending"
    assert db.email_outbox.find_one({"to": "gita@gymplus.example"})
    assert _audit(db, "partner.customer.created", **{"details.partner_id": r1["id"]})
    assert p1.post(f"/api/partner/v1/deals/{d['id']}/convert").status_code == 409    # already a customer


def _seed_referral(db, partner, company="Flag Co"):
    org_id = str(db.organizations.insert_one({"name": company, "slug": company.lower().replace(" ", "-"),
                                           "status": "demo", "created_at": _now()}).inserted_id)
    now = _now()
    return str(db.partner_referrals.insert_one({
        "partner_id": partner["id"], "organization_id": org_id, "company": company,
        "email": f"owner@{company.split()[0].lower()}.example", "user_id": None, "source": "link",
        "stage": "demo", "status": "active", "suspicious": True, "managed": False, "revenue_total": 0.0,
        "commission_total": 0.0, "signed_up_at": now, "events": [{"stage": "signed_up", "at": now}],
        "created_at": now, "updated_at": now}).inserted_id)


def test_resolve_fraud_flags(env):
    client, db = env
    sa = _sa(client)
    p, _ = _partner(client, sa, "ff@aff.example", name="Ff")
    rid = _seed_referral(db, p)
    com = _seed_commission(db, p["id"], status="qualified", amount=15.0, referral_id=rid)
    from app.partners.fraud import raise_flag
    f1 = raise_flag("duplicate_customer", partner_id=p["id"], subject_type="referral", subject_id=rid,
                    hold_referral_id=rid, db=db)
    f2 = raise_flag("suspicious_referral", partner_id=p["id"], subject_type="referral", subject_id=rid,
                    hold_referral_id=rid, db=db)
    f3 = raise_flag("click_flood", partner_id=p["id"], subject_type="partner", subject_id=p["id"], db=db)
    assert _com(db, com)["requires_manual_approval"] is True                 # held, not modified
    u1, u2, u3 = (f"{BASE}/fraud-flags/{f['_id']}/resolve" for f in (f1, f2, f3))
    assert sa.post(u1, json={"decision": "maybe"}).status_code == 422
    assert sa.post(u1, json={"decision": "confirm", "note": ""}).status_code == 422
    assert sa.post(f"{BASE}/fraud-flags/{ObjectId()}/resolve", json={"decision": "dismiss"}).status_code == 404
    assert _staff(client, db, "billing_admin").post(u1, json={"decision": "dismiss"}).status_code == 403
    r = sa.post(u1, json={"decision": "dismiss", "note": "Different shop"})
    assert r.status_code == 200 and r.json()["flag"]["status"] == "dismissed"
    assert r.json()["flag"]["resolved_by"] == SUPER
    assert _com(db, com)["requires_manual_approval"] is True                 # another flag still open
    r = sa.post(u2, json={"decision": "dismiss", "note": "Office wifi"})
    assert r.status_code == 200
    assert _com(db, com)["requires_manual_approval"] is False                # last flag: hold released
    assert db.partner_referrals.find_one({"_id": ObjectId(rid)})["suspicious"] is False
    assert _com(db, com)["amount"] == 15.0 and _com(db, com)["status"] == "qualified"
    assert sa.post(u1, json={"decision": "dismiss"}).status_code == 409      # already resolved
    r = sa.post(u3, json={"decision": "confirm", "note": "Bot traffic"})
    assert r.json()["flag"]["status"] == "confirmed" and r.json()["flag"]["resolution"] == "confirm"
    assert _audit(db, "partner.fraud.dismissed", resource_id=str(f1["_id"]))
    assert _audit(db, "partner.fraud.confirmed", resource_id=str(f3["_id"]))


def test_reassign_and_review_referral(env):
    client, db = env
    sa = _sa(client)
    a, pa = _partner(client, sa, "ra@aff.example", name="Ra")
    b, pb = _partner(client, sa, "rb@aff.example", name="Rb")
    c, _ = _partner(client, sa, "rc@aff.example", name="Rc")
    sa.post(f"{BASE}/{c['id']}/suspend", json={"reason": "Paused"})
    _signup_via(client, a, "own@reas.example", "Reas Cafe")
    ref = db.partner_referrals.find_one({"partner_id": a["id"]})
    rid = str(ref["_id"])
    com = _seed_commission(db, a["id"], status="payable", amount=20.0, referral_id=rid, manual=True)
    url = f"{BASE}/referrals/{rid}/reassign"
    assert sa.post(url, json={"partner_id": b["id"], "reason": ""}).status_code == 422
    assert sa.post(url, json={"partner_id": c["id"], "reason": "x"}).status_code == 422      # suspended target
    assert sa.post(url, json={"partner_id": a["id"], "reason": "x"}).status_code == 409      # same partner
    assert sa.post(f"{BASE}/referrals/{ObjectId()}/reassign", json={"partner_id": b["id"],
                                                                    "reason": "x"}).status_code == 404
    assert _staff(client, db, "billing_admin").post(url, json={"partner_id": b["id"], "reason": "x"}).status_code == 403
    r = sa.post(url, json={"partner_id": b["id"], "reason": "Customer confirmed B"})
    assert r.status_code == 200, r.text
    out = r.json()["referral"]
    assert out["partner_id"] == b["id"] and out["source"] == "reassigned"
    assert out["events"][-1]["stage"] == "reassigned" and out["events"][-1]["from"] == a["id"]
    assert _com(db, com)["partner_id"] == b["id"]
    assert db.organizations.find_one({"_id": ObjectId(ref["organization_id"])})["referred_by_partner_id"] == b["id"]
    assert db.audit_logs.count_documents({"action": "partner.referral.reassigned", "resource_id": rid}) == 2
    assert sa.get(f"{BASE}/{b['id']}/ledger").json()["balances"]["USD"]["available"] == 20.0
    assert sa.get(f"{BASE}/{a['id']}/ledger").json()["balances"].get("USD", {}).get("available", 0.0) == 0.0
    # once something was paid out, the customer can no longer move
    paid = _seed_commission(db, b["id"], status="paid", amount=10.0, referral_id=rid)
    assert sa.post(url, json={"partner_id": a["id"], "reason": "back"}).status_code == 409
    # review: clear releases the hold
    rv = f"{BASE}/referrals/{rid}/review"
    assert sa.post(rv, json={"decision": "maybe"}).status_code == 422
    assert sa.post(rv, json={"decision": "invalidate", "reason": " "}).status_code == 422
    assert sa.post(f"{BASE}/referrals/{ObjectId()}/review", json={"decision": "clear"}).status_code == 404
    r = sa.post(rv, json={"decision": "clear", "reason": "Verified by phone"})
    assert r.status_code == 200 and r.json()["referral"]["suspicious"] is False
    assert _com(db, com)["requires_manual_approval"] is False
    assert db.partner_fraud_flags.count_documents({"referral_id": rid, "status": "open"}) == 0
    # invalidate: unpaid reversed, paid clawed back, organization detached
    r = sa.post(rv, json={"decision": "invalidate", "reason": "Fake signup"})
    assert r.status_code == 200 and r.json()["referral"]["status"] == "invalid"
    assert _com(db, com)["status"] == "reversed" and _com(db, com)["reversal_source"] == "fraud"
    assert _com(db, paid)["status"] == "paid" and _com(db, paid)["clawed_back"] == 10.0
    assert db.partner_commissions.find_one({"kind": "clawback", "clawback_of": paid})["amount"] == -10.0
    assert "referred_by_partner_id" not in db.organizations.find_one({"_id": ObjectId(ref["organization_id"])})
    assert _audit(db, "partner.referral.review_invalidate", resource_id=rid)
    assert db.notifications.find_one({"user_id": b["user_id"], "title": "Referral invalidated"})


# ══ Partner Portal: profile ═════════════════════════════════════════════════

def test_partner_profile_read_and_update(env):
    client, db = env
    sa = _sa(client)
    p, portal = _partner(client, sa, "prof@aff.example", name="Prof", tax_info={"tax_id": "ABCDE1234F"})
    sa.patch(f"{BASE}/{p['id']}", json={"permissions": p["permissions"] + ["api.access"]})
    r = portal.get("/api/partner/v1/profile")
    assert r.status_code == 200, r.text
    prof = r.json()["partner"]
    assert prof["id"] == p["id"] and prof["name"] == "Prof" and prof["has_payout_info"] is True
    assert prof["payout_info"]["method"] == "paypal"
    assert prof["payout_info"]["paypal_email"] != "pay@aff.example"
    assert "pay@aff.example" not in r.text and "ABCDE1234F" not in r.text           # masked
    assert prof["referral_url"].endswith(f"/r/{p['referral_code']}")
    # update: allowed fields only; status / permissions / codes are never self-editable
    r = portal.patch("/api/partner/v1/profile", json={
        "city": "Pune", "website": "https://prof.example", "settings": {"email_notifications": False},
        "status": "active", "permissions": ["api.access", "coupons.manage"], "referral_code": "HIJACK"})
    assert r.status_code == 200, r.text
    assert r.json()["partner"]["city"] == "Pune" and r.json()["partner"]["website"] == "https://prof.example"
    doc = db.partners.find_one({"_id": ObjectId(p["id"])})
    assert doc["settings"]["email_notifications"] is False
    assert doc["referral_code"] == p["referral_code"] and "coupons.manage" not in doc["permissions"]
    a = _audit(db, "partner.profile.updated", **{"details.partner_id": p["id"]})
    assert a["details"]["fields"] == ["city", "settings.email_notifications", "website"]
    assert portal.patch("/api/partner/v1/profile", json={"name": "  "}).status_code == 422
    assert portal.patch("/api/partner/v1/profile", json={"website": "ftp://x.example"}).status_code == 422
    assert portal.patch("/api/partner/v1/profile", json={"phone": "call me"}).status_code == 422
    assert db.partners.find_one({"_id": ObjectId(p["id"])})["name"] == "Prof"
    # API keys are read-only
    raw = portal.post("/api/partner/v1/api-keys", json={"name": "BI"}).json()["api_key"]
    api = TestClient(client.app)
    assert api.get("/api/partner/v1/profile", headers={"X-API-Key": raw}).status_code == 200
    r = api.patch("/api/partner/v1/profile", json={"city": "Goa"}, headers={"X-API-Key": raw})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "api_key_read_only"
    assert TestClient(client.app).get("/api/partner/v1/profile").status_code == 401
    # without profile.manage
    sa.patch(f"{BASE}/{p['id']}", json={"permissions": ["dashboard.view"]})
    assert portal.get("/api/partner/v1/profile").status_code == 403
    assert portal.patch("/api/partner/v1/profile", json={"city": "Goa"}).status_code == 403
    assert db.partners.find_one({"_id": ObjectId(p["id"])})["city"] == "Pune"


# ══ referral campaign link ═════════════════════════════════════════════════

def test_campaign_referral_link_records_click_and_attribution(env):
    client, db = env
    from app.partners.referrals import decode_cookie
    sa = _sa(client)
    a, pa = _partner(client, sa, "camp@aff.example", name="Camp")
    b, pb = _partner(client, sa, "camp2@aff.example", name="Camp2")
    camp = pa.post("/api/partner/v1/campaigns", json={"name": "Webinar", "landing_path": "/pricing"}).json()["campaign"]
    other = pb.post("/api/partner/v1/campaigns", json={"name": "Theirs"}).json()["campaign"]
    v = TestClient(client.app)
    r = v.get(f"/r/{a['referral_code']}/{camp['slug']}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == f"/pricing?ref={a['referral_code']}"
    assert r.headers["cache-control"] == "no-store" and r.headers["x-robots-tag"] == "noindex"
    cookie = decode_cookie(v.cookies.get("leadai_ref"))
    assert cookie["lt"]["p"] == a["id"] and cookie["lt"]["c"] == camp["id"] and cookie["ft"]["c"] == camp["id"]
    assert v.cookies.get("leadai_vid")
    click = db.partner_referral_clicks.find_one({"partner_id": a["id"]})
    assert click["campaign_id"] == camp["id"] and click["landing"] == "/pricing"
    # the signup made after the click carries the campaign
    v.post("/api/auth/signup", json={"email": "own@webinar.example", "password": CUST_PW, "name": "W",
                                     "company": "Webinar Co", "accepted_terms": True})
    ref = db.partner_referrals.find_one({"email": "own@webinar.example"})
    assert ref["partner_id"] == a["id"] and ref["campaign_id"] == camp["id"]
    # another partner's campaign slug, or an unknown one: counted for the partner, no campaign
    for slug in (other["slug"], "no-such-campaign"):
        v2 = TestClient(client.app)
        r = v2.get(f"/r/{a['referral_code']}/{slug}", follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == f"/request-demo?ref={a['referral_code']}"
        assert decode_cookie(v2.cookies.get("leadai_ref"))["lt"]["c"] is None
    assert db.partner_referral_clicks.count_documents({"partner_id": a["id"], "campaign_id": None}) == 2
    assert db.partner_referral_clicks.count_documents({"partner_id": b["id"]}) == 0
    # plan-specific share link keeps the plan
    r = TestClient(client.app).get(f"/r/{a['referral_code']}/{camp['slug']}?plan=Starter", follow_redirects=False)
    assert r.headers["location"] == f"/pricing?ref={a['referral_code']}&plan=starter"
    # unknown code and suspended partner: plain landing, no attribution, no click
    before = db.partner_referral_clicks.count_documents({})
    v3 = TestClient(client.app)
    r = v3.get(f"/r/NOPE123/{camp['slug']}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/request-demo" and v3.cookies.get("leadai_ref") is None
    sa.post(f"{BASE}/{a['id']}/suspend", json={"reason": "Paused"})
    v4 = TestClient(client.app)
    r = v4.get(f"/r/{a['referral_code']}/{camp['slug']}", follow_redirects=False)
    assert r.headers["location"] == "/request-demo" and v4.cookies.get("leadai_ref") is None
    assert db.partner_referral_clicks.count_documents({}) == before


# ══ customer lifecycle: demo config, trials, tokens ═════════════════════════

def test_demo_config_read_update_and_effect_on_approval(env):
    client, db = env
    sa = _sa(client)
    from app.lifecycle.config import KNOWN_PLATFORMS
    r = sa.get("/api/super-admin/demo-config")
    assert r.status_code == 200
    cfg = r.json()["config"]
    assert cfg["duration_days"] == 3 and cfg["tokens"] == 500 and cfg["auto_approve"] is False
    assert r.json()["platforms"] == list(KNOWN_PLATFORMS)
    for bad in ({"duration_days": 0}, {"tokens": "lots"}, {"bogus": 1}, {"allowed_platforms": ["tiktok"]},
                {"allowed_platforms": []}):
        assert sa.put("/api/super-admin/demo-config", json=bad).status_code == 422, bad
    assert _staff(client, db, "operations_admin").get("/api/super-admin/demo-config").status_code == 403
    assert _staff(client, db, "operations_admin").put("/api/super-admin/demo-config",
                                                      json={"tokens": 1}).status_code == 403
    r = sa.put("/api/super-admin/demo-config", json={"duration_days": 7, "tokens": 900,
                                                     "allowed_platforms": ["facebook", "facebook"]})
    assert r.status_code == 200, r.text
    cfg = r.json()["config"]
    assert cfg["duration_days"] == 7 and cfg["tokens"] == 900 and cfg["allowed_platforms"] == ["facebook"]
    assert cfg["max_searches"] == 10                                            # untouched keys kept
    assert sa.get("/api/super-admin/demo-config").json()["config"]["tokens"] == 900
    stored = db.platform_config.find_one({"_id": "demo"})
    assert stored["value"]["tokens"] == 900 and stored["updated_by"] == SUPER
    a = _audit(db, "demo_config.updated")
    assert a["details"]["before"]["tokens"] == 500 and a["details"]["after"]["tokens"] == 900
    # the next approval grants the new allowance
    s = _signup(client, "own@cfg.example", "Cfg Co")
    assert sa.post(f"/api/super-admin/demo-requests/{s['demo_request_id']}/approve", json={}).status_code == 200
    org_id = db.users.find_one({"email": "own@cfg.example"})["default_organization_id"]
    bal = db.token_balances.find_one({"organization_id": org_id})
    assert bal["allocated"] == 900
    exp = _aware(db.organizations.find_one({"_id": ObjectId(org_id)})["demo"]["expires_at"])
    assert abs((exp - (_now() + timedelta(days=7))).total_seconds()) < 120


def test_token_costs_read_and_update(env):
    client, db = env
    sa = _sa(client)
    from app.lifecycle.config import TOKEN_COSTS_SEED, token_cost
    r = sa.get("/api/super-admin/token-costs")
    assert r.status_code == 200 and r.json()["costs"] == TOKEN_COSTS_SEED
    assert sa.put("/api/super-admin/token-costs", json={"search": -1}).status_code == 422
    assert sa.put("/api/super-admin/token-costs", json={"search": "abc"}).status_code == 422
    assert sa.put("/api/super-admin/token-costs", json={"search": 100_001}).status_code == 422
    assert _staff(client, db, "billing_admin").put("/api/super-admin/token-costs", json={"search": 1}).status_code == 403
    assert _staff(client, db, "billing_admin").get("/api/super-admin/token-costs").status_code == 403
    r = sa.put("/api/super-admin/token-costs", json={"search": 12, "export": "0"})
    assert r.status_code == 200
    assert r.json()["costs"]["search"] == 12 and r.json()["costs"]["export"] == 0
    assert r.json()["costs"]["ai_call"] == TOKEN_COSTS_SEED["ai_call"]
    assert token_cost("search") == 12                                             # what metering charges now
    a = _audit(db, "token_costs.updated")
    assert a["details"]["before"]["search"] == 10 and a["details"]["after"]["search"] == 12


def test_extend_and_cancel_free_trials(env):
    client, db = env
    sa = _sa(client)
    d1 = _signup(client, "own@ext.example", "Ext Co")["demo_request_id"]
    d2 = _signup(client, "own@pend.example", "Pend Co")["demo_request_id"]
    # a pending request can't be extended (only approved / extended trials)
    assert sa.post(f"/api/super-admin/demo-requests/{d1}/extend", json={"days": 2}).status_code == 409
    sa.post(f"/api/super-admin/demo-requests/{d1}/approve", json={})
    org_id = db.users.find_one({"email": "own@ext.example"})["default_organization_id"]
    exp0 = _aware(db.organizations.find_one({"_id": ObjectId(org_id)})["demo"]["expires_at"])
    assert sa.post(f"/api/super-admin/demo-requests/{d1}/extend", json={"days": 0, "tokens": 0}).status_code == 422
    assert _staff(client, db, "operations_admin").post(f"/api/super-admin/demo-requests/{d1}/extend",
                                                       json={"days": 1}).status_code == 403
    r = sa.post(f"/api/super-admin/demo-requests/{d1}/extend", json={"days": 5, "tokens": 100})
    assert r.status_code == 200, r.text
    assert r.json()["request"]["status"] == "extended"
    org = db.organizations.find_one({"_id": ObjectId(org_id)})
    assert abs((_aware(org["demo"]["expires_at"]) - (exp0 + timedelta(days=5))).total_seconds() < 5)
    assert org["status"] == "demo"
    bal = db.token_balances.find_one({"organization_id": org_id})
    assert bal["allocated"] == 600 and bal["remaining"] == 600
    a = _audit(db, "demo.extended", resource_id=d1)
    assert a["details"]["days"] == 5 and a["details"]["tokens"] == 100
    assert db.notifications.find_one({"type": "demo_extended", "title": "Your free trial was extended"})
    # extending again is fine (extended -> extended)
    assert sa.post(f"/api/super-admin/demo-requests/{d1}/extend", json={"days": 1}).status_code == 200
    # cancel the running trial
    assert _staff(client, db, "operations_admin").post(f"/api/super-admin/demo-requests/{d1}/cancel",
                                                       json={}).status_code == 403
    r = sa.post(f"/api/super-admin/demo-requests/{d1}/cancel", json={"reason": "Abuse"})
    assert r.status_code == 200 and r.json()["request"]["status"] == "cancelled"
    assert db.organizations.find_one({"_id": ObjectId(org_id)})["status"] == "cancelled"
    assert _audit(db, "demo.cancelled", resource_id=d1)["details"]["reason"] == "Abuse"
    mail = db.email_outbox.find_one({"to": "own@ext.example", "kind": "demo_cancelled"})
    assert "ended" in mail["body"] and "Abuse" in mail["body"]
    assert sa.post(f"/api/super-admin/demo-requests/{d1}/cancel", json={}).status_code == 409
    assert sa.post(f"/api/super-admin/demo-requests/{d1}/extend", json={"days": 3}).status_code == 409
    # cancelling a pending request closes the account instead of leaving it awaiting approval
    r = sa.post(f"/api/super-admin/demo-requests/{d2}/cancel", json={"reason": "Spam"})
    assert r.status_code == 200
    assert db.users.find_one({"email": "own@pend.example"})["status"] == "rejected"
    assert "closed before approval" in db.email_outbox.find_one({"to": "own@pend.example",
                                                                 "kind": "demo_cancelled"})["body"]
    for path in ("extend", "cancel"):
        assert sa.post(f"/api/super-admin/demo-requests/nope/{path}", json={"days": 1}).status_code == 404
        assert sa.post(f"/api/super-admin/demo-requests/{ObjectId()}/{path}", json={"days": 1}).status_code == 404


def test_token_balances_ledger_and_adjust(env):
    client, db = env
    sa = _sa(client)
    from app.billing.tokens import allocate
    alpha = str(db.organizations.insert_one({"name": "Alpha Labs", "slug": "alpha-labs", "status": "active", "created_at": _now()}).inserted_id)
    beta = str(db.organizations.insert_one({"name": "Beta Works", "slug": "beta-works", "status": "active", "created_at": _now()}).inserted_id)
    bare = str(db.organizations.insert_one({"name": "No Tokens", "slug": "no-tokens", "status": "active", "created_at": _now()}).inserted_id)
    allocate(alpha, 200, source="plan", actor="test", reset=True)
    allocate(beta, 100, source="plan", actor="test", reset=True)
    db.token_balances.update_one({"organization_id": beta}, {"$set": {"used": 25, "remaining": 75}})
    r = sa.get("/api/super-admin/tokens")
    assert r.status_code == 200 and r.json()["total"] == 2
    rows = {i["organization_id"]: i for i in r.json()["items"]}
    assert rows[beta]["organization_name"] == "Beta Works" and rows[beta]["percentage"] == 25.0
    assert rows[alpha]["percentage"] == 0
    assert [i["organization_id"] for i in r.json()["items"]] == [beta, alpha]          # sorted by usage
    q = sa.get("/api/super-admin/tokens?q=alpha").json()
    assert q["total"] == 1 and q["items"][0]["organization_id"] == alpha
    url = f"/api/super-admin/tokens/{alpha}/adjust"
    assert sa.post(url, json={"delta": 10, "reason": "  "}).status_code == 422
    assert sa.post(url, json={"delta": -500, "reason": "x"}).status_code == 400          # would go negative
    assert _staff(client, db, "billing_admin").post(url, json={"delta": 5, "reason": "x"}).status_code == 403
    r = sa.post(url, json={"delta": 50, "reason": "Goodwill"})
    assert r.status_code == 200 and r.json()["balance"]["remaining"] == 250 and r.json()["balance"]["allocated"] == 250
    r = sa.post(url, json={"delta": -30, "reason": "Correction"})
    assert r.json()["balance"]["remaining"] == 220
    a = db.audit_logs.find_one({"action": "tokens.adjusted", "details.reason": "Goodwill"})
    assert a["organization_id"] == alpha and a["details"]["delta"] == 50
    # an organization without a balance: a grant creates it, a debit is refused
    assert sa.post(f"/api/super-admin/tokens/{bare}/adjust", json={"delta": -5, "reason": "x"}).status_code == 400
    r = sa.post(f"/api/super-admin/tokens/{bare}/adjust", json={"delta": 40, "reason": "Trial top-up"})
    assert r.status_code == 200 and r.json()["balance"]["remaining"] == 40
    led = sa.get(f"/api/super-admin/tokens/{alpha}/ledger").json()
    assert led["total"] == 3 and led["balance"]["remaining"] == 220
    assert [(e["type"], e["amount"]) for e in led["items"]] == [("adjust", -30), ("adjust", 50), ("allocate", 200)]
    assert led["items"][0]["actor"] == SUPER and led["items"][0]["balance_after"] == 220
    assert sa.get(f"/api/super-admin/tokens/{beta}/ledger").json()["total"] == 1
    empty = sa.get(f"/api/super-admin/tokens/{ObjectId()}/ledger").json()
    assert empty["total"] == 0 and empty["balance"] is None
    assert _staff(client, db, "billing_admin").get("/api/super-admin/tokens").status_code == 403


def test_security_events_filters(env):
    client, db = env
    sa = _sa(client)
    from app.events.security import log_security_event
    log_security_event("login_failed", "low", actor_email="mallory@evil.example", ip="10.0.0.9",
                       path="/api/auth/login")
    log_security_event("login_failed", "low", actor_email="eve@evil.example", ip="10.0.0.8")
    log_security_event("permission_denied", "medium", actor_email="mallory@evil.example", path="/api/x")
    r = sa.get("/api/super-admin/security-events?type=login_failed")
    assert r.status_code == 200
    assert r.json()["total"] == db.security_events.count_documents({"type": "login_failed"})
    assert {i["actor_email"] for i in r.json()["items"]} >= {"mallory@evil.example", "eve@evil.example"}
    assert r.json()["counts"]["permission_denied"] == db.security_events.count_documents({"type": "permission_denied"})
    r = sa.get("/api/super-admin/security-events?q=mallory@evil")
    assert r.json()["total"] == 2 and all(i["actor_email"] == "mallory@evil.example" for i in r.json()["items"])
    r = sa.get("/api/super-admin/security-events?severity=medium&q=mallory")
    assert r.json()["total"] == 1 and r.json()["items"][0]["type"] == "permission_denied"
    assert len(sa.get("/api/super-admin/security-events?q=evil.example&limit=1").json()["items"]) == 1
    assert _staff(client, db, "operations_admin").get("/api/super-admin/security-events").status_code == 403


def test_super_admin_notifications_mark_read(env):
    client, db = env
    sa = _sa(client)
    from app.events.notifications import notify_org_admins, notify_super_admins
    db.notifications.delete_many({})
    notify_super_admins("system_error", "Disk almost full", "92 %")
    notify_super_admins("system_error", "Queue backlog", "300 jobs")
    org = str(db.organizations.insert_one({"name": "Org N", "slug": "org-n", "status": "active"}).inserted_id)
    notify_org_admins(org, "security_event", "Org only", "x")
    first = db.notifications.find_one({"title": "Disk almost full"})
    assert sa.get("/api/super-admin/notifications?unread=true").json()["unread"] == 2
    r = sa.post("/api/super-admin/notifications/read", json={"id": str(first["_id"])})
    assert r.status_code == 200 and r.json()["updated"] == 1
    items = {n["title"]: n for n in sa.get("/api/super-admin/notifications").json()["items"]}
    assert items["Disk almost full"]["read"] is True and items["Queue backlog"]["read"] is False
    assert "Org only" not in items
    assert sa.post("/api/super-admin/notifications/read", json={"id": "garbage"}).json()["updated"] == 0
    r = sa.post("/api/super-admin/notifications/read", json={})
    assert r.json()["updated"] == 1
    assert sa.get("/api/super-admin/notifications?unread=true").json()["total"] == 0
    assert db.notifications.find_one({"title": "Org only"})["read_by"] == []          # other audiences untouched
    assert SUPER in db.notifications.find_one({"title": "Queue backlog"})["read_by"]
    assert _staff(client, db, "operations_admin").post("/api/super-admin/notifications/read",
                                                       json={}).status_code == 403


# ══ messaging ═══════════════════════════════════════════════════════════════

def test_sms_outbox_masks_numbers_and_codes(env):
    client, db = env
    sa = _sa(client)
    from app.messaging.sms import send_sms
    send_sms("+91 98765 43210", "Your LeadAI code is 482913", kind="verification_code", bypass_limits=True)
    db.sms_outbox.insert_one({"to": "+14155550123", "body": "Hello", "kind": "generic", "status": "failed",
                              "error": "Provider down", "provider": "twilio", "created_at": _now()})
    r = sa.get("/api/super-admin/messaging/sms-outbox")
    assert r.status_code == 200
    rows = r.json()["items"]
    assert len(rows) == 2 and "482913" not in r.text and "9876543210" not in r.text
    code_row = next(x for x in rows if x["kind"] == "verification_code")
    assert code_row["to"] == "+91•••••43210" and "••••••" in code_row["body"]
    failed = sa.get("/api/super-admin/messaging/sms-outbox?status=failed").json()["items"]
    assert len(failed) == 1 and failed[0]["error"] == "Provider down" and failed[0]["to"] == "+14••••50123"
    assert len(sa.get("/api/super-admin/messaging/sms-outbox?limit=1").json()["items"]) == 1
    assert sa.get("/api/super-admin/messaging/sms-outbox?limit=0").status_code == 422
    assert _staff(client, db, "operations_admin").get("/api/super-admin/messaging/sms-outbox").status_code == 403


def test_automatic_messages_settings(env):
    client, db = env
    sa = _sa(client)
    from app.messaging.service import AUTO_DEFAULTS, auto_settings
    url = "/api/super-admin/messaging/automatic"
    assert sa.put(url, json={"login_alert_every_minutes": 5000}).status_code == 422
    assert sa.put(url, json={"login_alert_every_minutes": -1}).status_code == 422
    assert _staff(client, db, "operations_admin").put(url, json={"welcome_sms": False}).status_code == 403
    r = sa.put(url, json={"welcome_sms": False, "login_alert_every_minutes": 30})
    assert r.status_code == 200
    assert r.json()["automatic"] == {**AUTO_DEFAULTS, "welcome_sms": False, "login_alert_every_minutes": 30}
    assert auto_settings(db) == r.json()["automatic"]
    assert db.platform_config.find_one({"_id": "messaging"})["updated_by"] == SUPER
    # a later partial change keeps the earlier one
    r = sa.put(url, json={"login_alert_sms": False})
    assert r.json()["automatic"]["welcome_sms"] is False and r.json()["automatic"]["login_alert_sms"] is False
    a = db.audit_logs.find_one({"action": "messaging.automatic_changed", "details.login_alert_sms": False})
    assert a and a["actor_email"] == SUPER


# ══ webhooks ════════════════════════════════════════════════════════════════

def test_super_admin_disables_and_enables_a_webhook(env):
    client, db = env
    sa = _sa(client)
    _, org = seed_site_account("owner@hooks.example", org_name="Hook Org")
    db.outbound_webhooks.insert_one({"webhook_id": "wh_qa_1", "organization_id": org, "url": "https://hooks.example/in",
                                     "events": ["lead.created"], "is_active": True, "secret": "whsec_x",
                                     "created_at": _now()})
    base = "/api/super-admin/webhooks/wh_qa_1"
    assert sa.post(base + "/pause", json={}).status_code == 404
    assert sa.post("/api/super-admin/webhooks/wh_missing/disable", json={}).status_code == 404
    assert _staff(client, db, "operations_admin").post(base + "/disable", json={}).status_code == 403
    r = sa.post(base + "/disable", json={"reason": "Endpoint leaking data"})
    assert r.status_code == 200 and r.json() == {"success": True, "is_active": False}
    w = db.outbound_webhooks.find_one({"webhook_id": "wh_qa_1"})
    assert w["is_active"] is False and w["disabled_reason"] == "Endpoint leaking data" and w["disabled_by"] == SUPER
    a = _audit(db, "webhook.disabled_by_super_admin", resource_id="wh_qa_1")
    assert a["organization_id"] == org and a["details"]["url"] == "https://hooks.example/in"
    assert db.notifications.find_one({"organization_id": org, "title": "A webhook was disabled"})
    listed = sa.get("/api/super-admin/webhooks").json()["items"][0]
    assert listed["is_active"] is False and "secret" not in listed
    r = sa.post(base + "/enable", json={})
    assert r.json()["is_active"] is True
    w = db.outbound_webhooks.find_one({"webhook_id": "wh_qa_1"})
    assert w["is_active"] is True and w["disabled_reason"] is None and w["disabled_by"] is None
    assert _audit(db, "webhook.enabled_by_super_admin", resource_id="wh_qa_1")


# ══ plans / economics ═══════════════════════════════════════════════════════

def test_plan_schema_and_unit_economics(env):
    client, db = env
    sa = _sa(client)
    from app.billing.plans import API_LABELS, PLAN_LIMIT_KEYS
    r = sa.get("/api/super-admin/plans/schema")
    assert r.status_code == 200
    assert r.json()["limit_keys"] == list(PLAN_LIMIT_KEYS) and r.json()["api_providers"] == API_LABELS
    assert isinstance(r.json()["features"], dict) and r.json()["features"]
    r = sa.get("/api/super-admin/unit-economics")
    assert r.status_code == 200
    data = r.json()
    costs = data["unit_costs"]
    assert costs["apify_cost_per_search_run_usd"] == 0.005
    assert costs["ai_cost_per_single_comment_usd"] == round(115 * 0.075 / 1e6 + 35 * 0.30 / 1e6, 6)
    for key, plan in data["plan_margins"].items():
        if key == "free":
            assert plan["price_usd"] == 0.0 and plan["margin_usd"] == -plan["cogs_usd"]
            continue
        expected = round((plan["price_usd"] - plan["max_cogs_usd"]) / plan["price_usd"] * 100, 1)
        assert abs(plan["max_margin_percent"] - expected) <= 0.1, key
        assert plan["projected_avg_cogs_usd"] == round(plan["max_cogs_usd"] / 2, 2) or \
            abs(plan["projected_avg_cogs_usd"] - plan["max_cogs_usd"] / 2) < 0.01
    for path in ("/api/super-admin/plans/schema", "/api/super-admin/unit-economics"):
        assert _staff(client, db, "billing_admin").get(path).status_code == 403
        assert TestClient(client.app).get(path).status_code == 401


# ══ platform maintenance ════════════════════════════════════════════════════

def test_legacy_overrides_list_names_only(env):
    client, db = env
    sa = _sa(client)
    r = sa.get("/api/super-admin/config/legacy-overrides")
    assert r.status_code == 200 and r.json()["names"] == [] and r.json()["message"] == "None"
    db.env_overrides.insert_one({"_id": "SUPERADMIN_PASSWORD", "value": "stale-stored-copy-123"})
    r = sa.get("/api/super-admin/config/legacy-overrides")
    assert r.json()["names"] == ["SUPERADMIN_PASSWORD"] and "stale-stored-copy-123" not in r.text
    assert "hosting environment" in r.json()["message"]
    assert sa.delete("/api/super-admin/config/legacy-overrides/SMTP_HOST").status_code == 404
    assert _staff(client, db, "technical_admin").get("/api/super-admin/config/legacy-overrides").status_code == 403
    assert _staff(client, db, "technical_admin").delete(
        "/api/super-admin/config/legacy-overrides/SUPERADMIN_PASSWORD").status_code == 403
    assert db.env_overrides.find_one({"_id": "SUPERADMIN_PASSWORD"})


def test_legacy_override_can_be_removed(env):
    """The listed leftover copy of an environment-only secret must be
    removable (the startup warning tells the Super Admin to remove it here)."""
    client, db = env
    sa = _sa(client)
    db.env_overrides.insert_one({"_id": "SUPERADMIN_PASSWORD", "value": "stale-stored-copy-123"})
    r = sa.delete("/api/super-admin/config/legacy-overrides/SUPERADMIN_PASSWORD")
    assert r.status_code == 200, r.text
    assert db.env_overrides.find_one({"_id": "SUPERADMIN_PASSWORD"}) is None
    assert sa.get("/api/super-admin/config/legacy-overrides").json()["names"] == []
    assert _audit(db, "env.legacy_override_removed", resource_id="SUPERADMIN_PASSWORD")


def test_lifecycle_preview_toggle_and_run(env):
    client, db = env
    sa = _sa(client)
    from app.admin.settings import get_bool
    past = _now() - timedelta(days=1)
    org_c = db.organizations.insert_one({"name": "Ending Co", "slug": "ending-co", "status": "active", "created_at": _now()}).inserted_id
    sub = db.subscriptions.insert_one({"organization_id": str(org_c), "status": "active", "plan_id": "starter",
                                       "amount": 49.0, "currency": "USD", "cancel_at_period_end": True,
                                       "current_period_end": past, "created_at": _now()}).inserted_id
    db.organizations.update_one({"_id": org_c}, {"$set": {"subscription_id": str(sub)}})
    org_d = db.organizations.insert_one({"name": "Trial Co", "slug": "trial-co", "status": "demo", "created_at": _now(),
                                         "demo": {"expires_at": past}}).inserted_id
    r = sa.get("/api/super-admin/lifecycle")
    assert r.status_code == 200
    body = r.json()
    assert body["auto_apply"] is False
    assert body["counts"]["cancelled_at_period_end"] == 1 and body["counts"]["demo_expired"] == 1
    assert {(p["action"], p["organization_id"]) for p in body["pending"]} == {
        ("cancelled_at_period_end", str(org_c)), ("demo_expired", str(org_d))}
    assert db.subscriptions.find_one({"_id": sub})["status"] == "active"          # preview changes nothing
    assert _staff(client, db, "operations_admin").get("/api/super-admin/lifecycle").status_code == 403
    assert _staff(client, db, "operations_admin").post("/api/super-admin/lifecycle",
                                                       json={"run_now": True}).status_code == 403
    r = sa.post("/api/super-admin/lifecycle", json={"auto_apply": True})
    assert r.status_code == 200 and r.json() == {"success": True, "auto_apply": True, "applied": None}
    assert get_bool("lifecycle.auto_apply") is True
    assert db.subscriptions.find_one({"_id": sub})["status"] == "active"
    r = sa.post("/api/super-admin/lifecycle", json={"run_now": True})
    assert r.json()["applied"]["cancelled_at_period_end"] == 1 and r.json()["applied"]["demo_expired"] == 1
    assert db.subscriptions.find_one({"_id": sub})["status"] == "cancelled"
    assert db.organizations.find_one({"_id": org_c})["status"] == "cancelled"
    assert db.organizations.find_one({"_id": org_d})["demo"]["expired_notified"] is True
    assert db.audit_logs.count_documents({"action": "lifecycle.automation_updated"}) == 2
    assert _audit(db, "lifecycle.automation_updated", **{"details.run_now": True})["details"]["applied"][
        "demo_expired"] == 1
    assert sum(sa.get("/api/super-admin/lifecycle").json()["counts"].values()) == 0
    r = sa.post("/api/super-admin/lifecycle", json={"auto_apply": False})
    assert r.json()["auto_apply"] is False


def test_manual_subscription_renewal(env):
    client, db = env
    sa = _sa(client)
    _, org = seed_site_account("owner@renew.example", org_name="Renew Org")
    end = (_now() - timedelta(days=1)).replace(microsecond=0)
    sid = db.subscriptions.insert_one({"organization_id": org, "status": "active", "plan_id": "starter",
                                       "billing_cycle": "monthly", "amount": 49.0, "currency": "USD",
                                       "current_period_end": end, "created_at": _now()}).inserted_id
    url = f"/api/super-admin/subscriptions/{sid}/renew"
    assert sa.post(url, json={"reason": " "}).status_code == 422
    assert sa.post(f"/api/super-admin/subscriptions/{ObjectId()}/renew", json={"reason": "x"}).status_code == 404
    assert _staff(client, db, "billing_admin").post(url, json={"reason": "x"}).status_code == 403
    r = sa.post(url, json={"reason": "Bank transfer UTR-991", "amount": 45.0})
    assert r.status_code == 200, r.text
    new_end = datetime.fromisoformat(r.json()["current_period_end"])
    assert abs((_aware(new_end) - (end + timedelta(days=30))).total_seconds()) < 2
    sub = db.subscriptions.find_one({"_id": sid})
    assert abs((_aware(sub["current_period_start"]) - end).total_seconds()) < 2 and sub["status"] == "active"
    inv = db.invoices.find_one({"subscription_id": str(sid), "source": "manual"})
    assert inv["amount"] == 45.0 and inv["status"] == "paid" and inv["organization_id"] == org
    a = _audit(db, "subscription.renewed_manually", resource_id=str(sid))
    assert a["details"]["reason"] == "Bank transfer UTR-991" and a["details"]["amount"] == 45.0
    plan = db.plans.find_one({"slug": "starter"}) or {}
    tokens = int((plan.get("limits") or {}).get("monthly_tokens") or 0)
    if tokens:
        assert db.token_balances.find_one({"organization_id": org})["allocated"] == tokens
    # the new period is current: nothing to renew
    assert sa.post(url, json={"reason": "again"}).status_code == 409
    assert db.invoices.count_documents({"subscription_id": str(sid)}) == 1
    db.subscriptions.update_one({"_id": sid}, {"$set": {"status": "cancelled"}})
    assert sa.post(url, json={"reason": "x"}).status_code == 409


def test_platform_provider_key_test(env):
    client, db = env
    sa = _sa(client)
    url = "/api/super-admin/provider-keys/platform/apify/test"
    assert sa.post("/api/super-admin/provider-keys/platform/openai/test").status_code == 404
    assert _staff(client, db, "technical_admin").post(url).status_code == 403
    with patch("app.services.platform_api_keys.platform_key", return_value=""):
        r = sa.post(url)
    assert r.status_code == 200 and r.json()["test"]["valid"] is False and "No Apify key" in r.json()["test"]["detail"]
    with patch("app.services.platform_api_keys.platform_key", return_value=RAW_APIFY), \
            patch("app.services.platform_api_keys.test_apify_token_connection", INVALID):
        r = sa.post(url)
    assert r.status_code == 200 and r.json()["test"] == {"valid": False, "detail": "Invalid token"}
    assert db.platform_api_key_status.find_one({"_id": "apify"})["valid"] is False
    with patch("app.services.platform_api_keys.platform_key", return_value=RAW_APIFY), \
            patch("app.services.platform_api_keys.test_apify_token_connection", VALID):
        r = sa.post(url)
    assert r.status_code == 200 and r.json()["test"]["valid"] is True
    assert RAW_APIFY not in r.text
    st = db.platform_api_key_status.find_one({"_id": "apify"})
    assert st["valid"] is True and st["hint"] != RAW_APIFY and st["hint"].endswith(RAW_APIFY[-4:])
    assert r.json()["platform"]["apify"]["last_test"]["valid"] is True
    logs = list(db.audit_logs.find({"action": "platform_api_key.tested"}).sort("at", 1))
    assert [x["details"]["valid"] for x in logs] == [False, False, True]


def test_organization_api_key_test(env):
    client, db = env
    sa = _sa(client)
    org = str(db.organizations.insert_one({"name": "Keyed", "slug": "keyed", "status": "active", "plan_id": "starter",
                                           "admin_portal_enabled": True, "settings": {},
                                           "created_at": _now()}).inserted_id)
    url = f"/api/super-admin/organizations/{org}/api-keys/apify/test"
    assert sa.post(f"/api/super-admin/organizations/{org}/api-keys/openai/test").status_code == 404
    assert sa.post(f"/api/super-admin/organizations/{ObjectId()}/api-keys/apify/test").status_code == 404
    assert _staff(client, db, "technical_admin").post(url).status_code == 403
    r = sa.post(url)
    assert r.status_code == 200 and r.json()["test"] == {"valid": False, "detail": "No key saved"}
    from app.services.tenant_api_keys import save_key
    with patch("app.services.tenant_api_keys.test_apify_token_connection", VALID):
        asyncio.run(save_key(org, "apify", RAW_APIFY, actor_email="owner@keyed.example"))
    with patch("app.services.tenant_api_keys.test_apify_token_connection", INVALID):
        r = sa.post(url)
    assert r.status_code == 200 and r.json()["test"]["valid"] is False
    keys = db.organizations.find_one({"_id": ObjectId(org)})["custom_api_keys"]["apify"]
    assert keys["verified"] is False and keys["last_error"] == "Invalid token"
    with patch("app.services.tenant_api_keys.test_apify_token_connection", VALID):
        r = sa.post(url)
    assert r.json()["test"]["valid"] is True and RAW_APIFY not in r.text
    keys = db.organizations.find_one({"_id": ObjectId(org)})["custom_api_keys"]["apify"]
    assert keys["verified"] is True and keys["last_error"] is None
    logs = list(db.audit_logs.find({"action": "api_key.tested", "organization_id": org}).sort("at", 1))
    assert [x["details"]["valid"] for x in logs] == [False, False, True]
    assert all(x["details"]["via"] == "super_admin" for x in logs)


def test_set_any_users_password(env):
    client, db = env
    sa = _sa(client)
    from app.auth.crypto import hash_password, verify_password
    uid, _ = seed_site_account("member@pw.example", org_name="Pw Org")
    db.users.update_one({"_id": ObjectId(uid)}, {"$set": {"password_hash": hash_password(CUST_PW)}})
    owner = _login(client, "member@pw.example", CUST_PW)
    db.password_resets.insert_one({"user_id": uid, "token_hash": "abc", "used_at": None, "created_at": _now()})
    url = f"/api/super-admin/users/{uid}/password"
    assert sa.post(url, json={"password": "short1A"}).status_code == 422
    assert sa.post(url, json={"password": "nouppercase123"}).status_code == 422
    assert sa.post(f"/api/super-admin/users/{ObjectId()}/password", json={"password": "Fresh-Pass-2026"}).status_code == 404
    assert _staff(client, db, "operations_admin").post(url, json={"password": "Fresh-Pass-2026"}).status_code == 403
    # the env Super Admin's sign-in can never be changed here
    sa_uid = str(db.users.insert_one({"email": SUPER, "name": "Root", "status": "active"}).inserted_id)
    assert sa.post(f"/api/super-admin/users/{sa_uid}/password", json={"password": "Fresh-Pass-2026"}).status_code == 409
    r = sa.post(url, json={"password": "Fresh-Pass-2026", "reason": "Locked out"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["email"] == "member@pw.example" and body["must_change_password"] is True
    assert body["sessions_revoked"] == 1 and "Fresh-Pass-2026" not in r.text
    user = db.users.find_one({"_id": ObjectId(uid)})
    ok = verify_password("Fresh-Pass-2026", user["password_hash"])
    assert (ok[0] if isinstance(ok, tuple) else ok) and user["must_change_password"] is True
    assert db.password_resets.find_one({"user_id": uid})["invalidated"] is True
    assert owner.get("/api/auth/me").status_code == 401
    _login(client, "member@pw.example", CUST_PW, expect=401)
    r = TestClient(client.app).post("/api/auth/login", json={"email": "member@pw.example",
                                                             "password": "Fresh-Pass-2026", "scope": "site"})
    assert r.status_code == 200 and r.json()["must_change_password"] is True
    a = _audit(db, "user.password_set", resource_id=uid)
    assert a["details"]["reason"] == "Locked out" and "must_change_password" in a["details"]
    assert "Fresh-Pass-2026" not in str(a)
    mail = db.email_outbox.find_one({"to": "member@pw.example", "subject": "Your LeadAI password was changed"})
    assert mail and "Fresh-Pass-2026" not in mail["body"]
    # not temporary + no email when asked
    r = sa.post(url, json={"password": "Second-Pass-2026", "must_change": False, "notify": False})
    assert r.json()["must_change_password"] is False and r.json()["email_delivery"] == "skipped"
