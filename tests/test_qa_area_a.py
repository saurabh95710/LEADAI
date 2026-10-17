"""QA area A — routes that had no test coverage: auth (sessions, 2FA, email
verification), customer search API, Org Admin API, notifications, billing,
public website and compliance.

Runs on the in-memory MongoDB from tests/conftest.py (never a real
database). No network: e-mail sending is patched at the route's
``send_email`` name, Apify is never reached (bulk / scheduled searches only
enqueue jobs and the queue worker is not started under pytest).

Tests that fail here fail because the APP is wrong — each one names the bug
in its docstring.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
import re
from unittest.mock import patch
from urllib.parse import urlparse

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.auth.crypto import hash_password
from app.auth.service import COOKIE_NAME, build_session_value, create_tracked_session
from app.auth.totp import generate_totp_code, generate_totp_secret

PASSWORD = "Str0ngPass!"
NOW = datetime.now(timezone.utc)


# ═══════════════════════════════════════════════════════════════════════════
# fixtures / helpers
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def env():
    from app.api.routes import auth as auth_routes
    from app.api.routes import compliance as compliance_routes
    auth_routes._email_verify_limiter.clear()
    compliance_routes._opt_out_limiter.clear()
    with patch("app.admin.settings.is_maintenance_enabled", return_value=False):
        from app.main import app
        with TestClient(app) as client:
            from app.db.mongo import get_sync_db
            yield client, get_sync_db()
    auth_routes._email_verify_limiter.clear()
    compliance_routes._opt_out_limiter.clear()


def _org(db, name, status="active", portal=True, **extra):
    return str(db.organizations.insert_one({
        "name": name, "slug": name.lower().replace(" ", "-") + str(ObjectId())[-4:],
        "status": status, "settings": {}, "admin_portal_enabled": portal,
        "created_at": NOW, **extra}).inserted_id)


def _session(uid, email, org_id, role, user_agent="unknown"):
    """(cookie, session_id) of a fresh tracked site session."""
    tracked = create_tracked_session({"user_id": uid, "email": email, "name": email,
                                      "scope": "site", "organization_id": org_id,
                                      "org_role": role}, user_agent=user_agent)
    return {COOKIE_NAME: build_session_value(tracked)}, tracked["session_id"]


def _member(db, org_id, email, role="member", **user_extra):
    """Active user + membership; returns (user_id, cookie)."""
    uid = str(db.users.insert_one({
        "email": email, "name": email.split("@")[0], "password_hash": hash_password(PASSWORD),
        "status": "active", "is_platform_admin": False, "platform_role": None,
        "default_organization_id": org_id, "created_at": NOW, **user_extra}).inserted_id)
    db.organization_members.insert_one({
        "organization_id": org_id, "user_id": uid, "email": email, "role": role,
        "status": "active", "permissions_override": {}, "created_at": NOW})
    cookie, _ = _session(uid, email, org_id, role)
    return uid, cookie


class Tenants:
    """Two organizations, each with an owner / admin / manager / member / viewer."""

    def __init__(self, db):
        self.a = _org(db, "Alpha Co")
        self.b = _org(db, "Beta Co")
        for tag, org in (("a", self.a), ("b", self.b)):
            for role in ("owner", "admin", "manager", "member", "viewer"):
                uid, cookie = _member(db, org, f"{role}@{tag}.test", role)
                setattr(self, f"{role}_{tag}", uid)
                setattr(self, f"c_{role}_{tag}", cookie)


@pytest.fixture
def t(env):
    _, db = env
    return Tenants(db)


def _lead(db, org_id, uid, **kw):
    doc = {"organization_id": org_id, "user_id": uid, "created_by": "seed@x.test",
           "comment_ref": str(ObjectId()), "is_lead": True, "lead_score": 80, "commenter_name": "Someone",
           "comment_text": "interested, price?", "platform": "facebook",
           "lead_status": "new", "analyzed_at": datetime.utcnow(), **kw}
    return str(db.ai_comments.insert_one(doc).inserted_id)


def _audits(db, action, **q):
    return list(db.audit_logs.find({"action": action, **q}))


# ═══════════════════════════════════════════════════════════════════════════
# app.main — error pages, reset-password page, health
# ═══════════════════════════════════════════════════════════════════════════

class TestMainPages:
    def test_403_page_is_public_html_with_403_status(self, env):
        client, _ = env
        r = client.get("/403", follow_redirects=False)
        assert r.status_code == 403
        assert r.headers["content-type"].startswith("text/html")
        assert "Access denied" in r.text

    def test_404_page_is_public_html_with_404_status(self, env):
        client, _ = env
        r = client.get("/404", follow_redirects=False)
        assert r.status_code == 404
        assert r.headers["content-type"].startswith("text/html")
        assert "Page not found" in r.text

    def test_forgot_password_page_needs_no_session(self, env):
        client, _ = env
        r = client.get("/forgot-password", follow_redirects=False)
        assert r.status_code == 200
        assert "<title>Reset password" in r.text
        # same shell as /reset-password (the page handles both steps)
        assert r.text == client.get("/reset-password").text

    def test_error_pages_reject_other_methods(self, env):
        client, _ = env
        assert client.post("/403").status_code == 405
        assert client.post("/forgot-password").status_code == 405

    def test_health_ok_and_superadmin_flag_without_leaking_it(self, env):
        client, _ = env
        r = client.get("/api/health")
        j = r.json()
        assert r.status_code == 200
        assert j["status"] == "ok" and j["ready"] is True and j["auth_enabled"] is True
        assert j["superadmin_configured"] is False
        assert isinstance(j["mongo_latency_ms"], float)
        from tests.conftest import TEST_SUPERADMIN_PASSWORD, as_superadmin
        as_superadmin("boss@platform.test")
        r2 = client.get("/api/health")
        assert r2.json()["superadmin_configured"] is True
        assert "boss@platform.test" not in r2.text
        assert TEST_SUPERADMIN_PASSWORD not in r2.text

    def test_health_degraded_when_database_ping_fails(self, env):
        client, _ = env

        class _Down:
            async def command(self, *a, **kw):
                raise RuntimeError("mongo down")

        with patch("app.db.mongo.get_async_db", return_value=_Down()):
            r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "degraded"
        assert "mongo_latency_ms" not in r.json()


# ═══════════════════════════════════════════════════════════════════════════
# public_website — /api/public/theme, /features, /cookies
# ═══════════════════════════════════════════════════════════════════════════

class TestPublicWebsite:
    def test_theme_is_a_whitelisted_projection(self, env):
        client, db = env
        from app.cms import service as cms
        db.website_settings.update_one({"key": "primary_color"},
                                       {"$set": {"value": "#112233"}}, upsert=True)
        db.website_settings.insert_one({"key": "internal_notes", "value": "secret-xyz-123"})
        cms.clear_cache()
        r = client.get("/api/public/theme")
        assert r.status_code == 200
        j = r.json()
        assert set(j) == {"brand_name", "tagline", "logo_url", "favicon_url",
                          "primary_color", "accent_color"}
        assert j["primary_color"] == "#112233"
        assert j["brand_name"]  # never empty: falls back to LeadAI
        assert "secret-xyz-123" not in r.text

    def test_theme_brand_follows_global_settings(self, env):
        client, _ = env
        from app.admin.settings import set_setting
        from app.cms import service as cms
        assert set_setting("general.app.name", "Acme Leads", by="test")
        cms.clear_cache()
        assert client.get("/api/public/theme").json()["brand_name"] == "Acme Leads"

    @pytest.mark.parametrize("path,slug", [("/features", "features"), ("/cookies", "cookies")])
    def test_site_page_renders_published_cms_seo(self, env, path, slug):
        client, db = env
        from app.cms import service as cms
        title = f"QA {slug} title"
        db.website_pages.update_one({"slug": slug}, {"$set": {
            "status": "published", "title": title,
            "live": {"title": title, "seo": {"title": title, "description": f"desc {slug}",
                                            "robots": "index,follow"}, "sections": []}}},
            upsert=True)
        cms.clear_cache()
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")
        assert f"<title>{title}</title>" in r.text
        assert f'content="desc {slug}"' in r.text
        assert '<meta name="robots" content="index,follow"/>' in r.text
        assert f'{path}"/>' in r.text  # canonical / og:url is this page
        # conditional GET
        etag = r.headers["etag"]
        assert client.get(path, headers={"If-None-Match": etag}).status_code == 304

    @pytest.mark.parametrize("path,slug", [("/features", "features"), ("/cookies", "cookies")])
    def test_unpublished_site_page_is_noindex(self, env, path, slug):
        client, db = env
        from app.cms import service as cms
        db.website_pages.update_one({"slug": slug}, {"$set": {
            "status": "draft", "title": "Secret draft title",
            "live": {"title": "Secret draft title", "seo": {"title": "Secret draft title"}}}},
            upsert=True)
        cms.clear_cache()
        r = client.get(path)
        assert r.status_code == 200
        assert '<meta name="robots" content="noindex,follow"/>' in r.text
        assert "Secret draft title" not in r.text


# ═══════════════════════════════════════════════════════════════════════════
# auth — sessions
# ═══════════════════════════════════════════════════════════════════════════

class TestSessions:
    def _two_users(self, db):
        a, b = _org(db, "Alpha"), _org(db, "Beta")
        ua, _ = _member(db, a, "a@alpha.test", "owner")
        ub, _ = _member(db, b, "b@beta.test", "owner")
        return a, b, ua, ub

    def test_list_shows_only_my_active_sessions(self, env):
        client, db = env
        a, b, ua, ub = self._two_users(db)
        c1, s1 = _session(ua, "a@alpha.test", a, "owner", "Firefox/1")
        _, s2 = _session(ua, "a@alpha.test", a, "owner", "Chrome/2")
        _, s3 = _session(ua, "a@alpha.test", a, "owner", "Old/0")
        db.user_sessions.update_one({"session_id": s3}, {"$set": {"revoked_at": datetime.utcnow()}})
        _, sb = _session(ub, "b@beta.test", b, "owner", "Other/9")
        r = client.get("/api/auth/sessions", cookies=c1)
        assert r.status_code == 200
        items = r.json()["sessions"]
        ids = {i["id"] for i in items}
        assert sb[:12] not in ids and s3[:12] not in ids
        assert {s1[:12], s2[:12]} <= ids
        assert [i["id"] for i in items if i["current"]] == [s1[:12]]
        assert "Other/9" not in r.text and "Old/0" not in r.text

    def test_list_requires_sign_in(self, env):
        client, _ = env
        assert client.get("/api/auth/sessions").status_code == 401
        assert client.get("/api/auth/sessions", cookies={COOKIE_NAME: "forged.value"}).status_code == 401

    def test_revoke_one_of_my_sessions(self, env):
        client, db = env
        a, _, ua, _ = self._two_users(db)
        c1, s1 = _session(ua, "a@alpha.test", a, "owner")
        c2, s2 = _session(ua, "a@alpha.test", a, "owner")
        r = client.delete(f"/api/auth/sessions/{s2[:12]}", cookies=c1)
        assert r.status_code == 200 and r.json() == {"success": True, "revoked": 1}
        rec = db.user_sessions.find_one({"session_id": s2})
        assert rec["revoked_at"] is not None and rec["revoked_by"] == "user"
        assert db.user_sessions.find_one({"session_id": s1})["revoked_at"] is None
        assert client.get("/api/auth/sessions", cookies=c2).status_code == 401
        assert client.get("/api/auth/sessions", cookies=c1).status_code == 200
        assert _audits(db, "auth.sessions_revoked", actor_email="a@alpha.test")[0]["details"]["count"] == 1

    def test_revoke_all_other_sessions_keeps_the_current_one(self, env):
        client, db = env
        a, b, ua, ub = self._two_users(db)
        c1, s1 = _session(ua, "a@alpha.test", a, "owner")
        _, s2 = _session(ua, "a@alpha.test", a, "owner")
        _, s3 = _session(ua, "a@alpha.test", a, "owner")
        _, sb = _session(ub, "b@beta.test", b, "owner")
        others = db.user_sessions.count_documents({"user_id": ua, "revoked_at": None,
                                                   "session_id": {"$ne": s1}})
        assert others == 3  # s2, s3 and the one _member() opened
        r = client.delete("/api/auth/sessions/others", cookies=c1)
        assert r.status_code == 200 and r.json()["revoked"] == 3
        assert db.user_sessions.find_one({"session_id": s1})["revoked_at"] is None
        for s in (s2, s3):
            assert db.user_sessions.find_one({"session_id": s})["revoked_at"] is not None
        assert db.user_sessions.count_documents({"user_id": ua, "revoked_at": None}) == 1
        assert db.user_sessions.find_one({"session_id": sb})["revoked_at"] is None

    def test_cannot_revoke_another_tenants_session(self, env):
        client, db = env
        a, b, ua, ub = self._two_users(db)
        c1, _ = _session(ua, "a@alpha.test", a, "owner")
        _, sb = _session(ub, "b@beta.test", b, "owner")
        r = client.delete(f"/api/auth/sessions/{sb[:12]}", cookies=c1)
        assert r.status_code == 404
        assert db.user_sessions.find_one({"session_id": sb})["revoked_at"] is None
        ev = db.security_events.find_one({"type": "cross_tenant_access",
                                          "actor_email": "a@alpha.test"})
        assert ev and ev["details"]["collection"] == "user_sessions"

    def test_revoke_rejects_short_ids_and_anonymous(self, env):
        client, db = env
        a, _, ua, _ = self._two_users(db)
        c1, _ = _session(ua, "a@alpha.test", a, "owner")
        assert client.delete("/api/auth/sessions/abc", cookies=c1).status_code == 400
        assert client.delete("/api/auth/sessions/others").status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# auth — two-factor authentication
# ═══════════════════════════════════════════════════════════════════════════

class TestTwoFactor:
    def _user(self, db):
        org = _org(db, "Alpha")
        uid, cookie = _member(db, org, "two@alpha.test", "owner")
        return uid, cookie

    def test_setup_enable_status_disable_with_password(self, env):
        client, db = env
        uid, c = self._user(db)
        assert client.get("/api/auth/2fa/status", cookies=c).json() == {"totp_enabled": False}

        r = client.post("/api/auth/2fa/setup", cookies=c)
        assert r.status_code == 200
        secret = r.json()["secret"]
        assert len(secret) == 32
        assert r.json()["otpauth_url"].startswith("otpauth://totp/LeadAI%3Atwo%40alpha.test?secret=" + secret)
        # setup alone stores nothing
        assert "totp_secret" not in db.users.find_one({"_id": ObjectId(uid)})

        r = client.post("/api/auth/2fa/enable", cookies=c,
                        json={"secret": secret, "code": generate_totp_code(secret)})
        assert r.status_code == 200 and r.json()["success"] is True
        u = db.users.find_one({"_id": ObjectId(uid)})
        assert u["totp_enabled"] is True and u["totp_secret"] == secret
        assert client.get("/api/auth/2fa/status", cookies=c).json() == {"totp_enabled": True}
        assert _audits(db, "auth.2fa_enabled", actor_email="two@alpha.test")

        r = client.post("/api/auth/2fa/disable", cookies=c, json={"password": "wrong-pass"})
        assert r.status_code == 400
        assert db.users.find_one({"_id": ObjectId(uid)})["totp_enabled"] is True

        r = client.post("/api/auth/2fa/disable", cookies=c, json={"password": PASSWORD})
        assert r.status_code == 200
        u = db.users.find_one({"_id": ObjectId(uid)})
        assert u["totp_enabled"] is False and "totp_secret" not in u and "totp_enabled_at" not in u
        assert _audits(db, "auth.2fa_disabled", actor_email="two@alpha.test")

    def test_enable_rejects_a_wrong_code(self, env):
        client, db = env
        uid, c = self._user(db)
        secret = generate_totp_secret()
        good = generate_totp_code(secret)
        bad = f"{(int(good) + 500000) % 1000000:06d}"
        for code in (bad, "12345", "abcdef"):
            r = client.post("/api/auth/2fa/enable", cookies=c, json={"secret": secret, "code": code})
            assert r.status_code == 400
        u = db.users.find_one({"_id": ObjectId(uid)})
        assert not u.get("totp_enabled") and "totp_secret" not in u
        assert client.post("/api/auth/2fa/enable", cookies=c, json={"secret": secret}).status_code == 422

    def test_disable_with_a_valid_totp_code(self, env):
        client, db = env
        uid, c = self._user(db)
        secret = generate_totp_secret()
        db.users.update_one({"_id": ObjectId(uid)}, {"$set": {"totp_enabled": True, "totp_secret": secret}})
        assert client.post("/api/auth/2fa/disable", cookies=c, json={"code": "000000" if generate_totp_code(secret) != "000000" else "111111"}).status_code == 400
        r = client.post("/api/auth/2fa/disable", cookies=c, json={"code": generate_totp_code(secret)})
        assert r.status_code == 200
        assert db.users.find_one({"_id": ObjectId(uid)})["totp_enabled"] is False

    def test_all_2fa_endpoints_require_sign_in(self, env):
        client, _ = env
        assert client.get("/api/auth/2fa/status").status_code == 401
        assert client.post("/api/auth/2fa/setup").status_code == 401
        assert client.post("/api/auth/2fa/enable", json={"secret": "A" * 32, "code": "123456"}).status_code == 401
        assert client.post("/api/auth/2fa/disable", json={"password": PASSWORD}).status_code == 401

    def test_enable_cannot_silently_replace_an_active_2fa_secret(self, env):
        """BUG: with 2FA already on, POST /2fa/enable overwrites totp_secret
        using only the session (no password, no code of the CURRENT secret),
        although /2fa/disable deliberately demands one of them. A stolen
        session can therefore take over the second factor."""
        client, db = env
        uid, c = self._user(db)
        s1 = generate_totp_secret()
        assert client.post("/api/auth/2fa/enable", cookies=c,
                           json={"secret": s1, "code": generate_totp_code(s1)}).status_code == 200
        s2 = generate_totp_secret()
        r = client.post("/api/auth/2fa/enable", cookies=c,
                        json={"secret": s2, "code": generate_totp_code(s2)})
        assert db.users.find_one({"_id": ObjectId(uid)})["totp_secret"] == s1
        assert 400 <= r.status_code < 500


# ═══════════════════════════════════════════════════════════════════════════
# auth — email verification
# ═══════════════════════════════════════════════════════════════════════════

def _sha(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class TestEmailVerification:
    def _unverified(self, db, email="new@alpha.test"):
        org = _org(db, "Alpha")
        uid, c = _member(db, org, email, "owner", email_verified=False)
        return uid, c

    def test_verify_with_a_valid_token_is_single_use(self, env):
        client, db = env
        uid, _ = self._unverified(db)
        token = secrets.token_urlsafe(32)
        db.email_verifications.insert_one({
            "user_id": uid, "email": "new@alpha.test", "token_hash": _sha(token),
            "expires_at": datetime.utcnow() + timedelta(hours=1), "used_at": None})
        r = client.post("/api/auth/verify-email", json={"token": f"  {token} "})
        assert r.status_code == 200 and r.json()["success"] is True
        assert db.users.find_one({"_id": ObjectId(uid)})["email_verified"] is True
        assert db.email_verifications.find_one({"token_hash": _sha(token)})["used_at"] is not None
        again = client.post("/api/auth/verify-email", json={"token": token})
        assert again.status_code == 400
        assert "invalid or has expired" in again.json()["detail"]

    def test_expired_or_unknown_tokens_are_rejected(self, env):
        client, db = env
        uid, _ = self._unverified(db)
        token = secrets.token_urlsafe(32)
        db.email_verifications.insert_one({
            "user_id": uid, "email": "new@alpha.test", "token_hash": _sha(token),
            "expires_at": datetime.utcnow() - timedelta(minutes=1), "used_at": None})
        assert client.post("/api/auth/verify-email", json={"token": token}).status_code == 400
        assert client.post("/api/auth/verify-email", json={"token": "nope"}).status_code == 400
        assert client.post("/api/auth/verify-email", json={}).status_code == 422
        assert db.users.find_one({"_id": ObjectId(uid)})["email_verified"] is False

    def test_resend_issues_a_new_link_and_cancels_old_ones(self, env):
        client, db = env
        uid, _ = self._unverified(db)
        old = db.email_verifications.insert_one({
            "user_id": uid, "email": "new@alpha.test", "token_hash": _sha("old"),
            "expires_at": datetime.utcnow() + timedelta(hours=5), "used_at": None}).inserted_id
        with patch("app.api.routes.auth.send_email") as send:
            r = client.post("/api/auth/verify-email/resend", json={"email": " New@Alpha.test "})
        assert r.status_code == 200 and r.json()["success"] is True
        assert db.email_verifications.find_one({"_id": old})["invalidated"] is True
        live = list(db.email_verifications.find({"user_id": uid, "used_at": None}))
        assert len(live) == 1
        send.assert_called_once()
        to, subject, body = send.call_args.args[:3]
        assert to == "new@alpha.test" and "Verify" in subject
        token = body.split("token=")[1].split()[0]
        assert live[0]["token_hash"] == _sha(token)
        # the emailed token verifies the account
        assert client.post("/api/auth/verify-email", json={"token": token}).status_code == 200
        assert db.users.find_one({"_id": ObjectId(uid)})["email_verified"] is True
        # the cancelled one no longer works
        assert client.post("/api/auth/verify-email", json={"token": "old"}).status_code == 400

    def test_resend_never_mails_unknown_or_verified_addresses(self, env):
        client, db = env
        org = _org(db, "Alpha")
        _member(db, org, "done@alpha.test", "owner", email_verified=True)
        with patch("app.api.routes.auth.send_email") as send:
            r1 = client.post("/api/auth/verify-email/resend", json={"email": "stranger@else.test"})
            r2 = client.post("/api/auth/verify-email/resend", json={"email": "done@alpha.test"})
        assert r1.status_code == r2.status_code == 200
        assert r1.json() == r2.json()  # same answer: no account enumeration
        send.assert_not_called()
        assert db.email_verifications.count_documents({}) == 0

    def test_resend_uses_the_session_email_and_needs_one(self, env):
        client, db = env
        uid, c = self._unverified(db, "sess@alpha.test")
        with patch("app.api.routes.auth.send_email") as send:
            assert client.post("/api/auth/verify-email/resend", json={}).status_code == 400
            r = client.post("/api/auth/verify-email/resend", json={}, cookies=c)
        assert r.status_code == 200
        assert send.call_args.args[0] == "sess@alpha.test"
        assert db.email_verifications.count_documents({"user_id": uid}) == 1

    def test_resend_is_rate_limited_per_ip(self, env):
        client, db = env
        with patch("app.api.routes.auth.send_email"):
            codes = [client.post("/api/auth/verify-email/resend",
                                 json={"email": f"x{i}@else.test"}).status_code for i in range(6)]
        assert codes == [200] * 5 + [429]

    def test_emailed_verification_link_opens_a_page(self, env):
        """BUG: the resend e-mail links to /verify-email?token=..., but no
        such page exists (no route, no static file, nothing in the UI POSTs
        the token) — the link redirects a signed-out person to /login and
        the token can never be used from the e-mail."""
        client, db = env
        self._unverified(db)
        with patch("app.api.routes.auth.send_email") as send:
            client.post("/api/auth/verify-email/resend", json={"email": "new@alpha.test"})
        link = send.call_args.args[2].split("\n")[3].strip()
        parsed = urlparse(link)
        assert parsed.path == "/verify-email"
        r = client.get(f"{parsed.path}?{parsed.query}", follow_redirects=False)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/html")


# ═══════════════════════════════════════════════════════════════════════════
# billing — POST /api/billing/cancel
# ═══════════════════════════════════════════════════════════════════════════

class TestBillingCancel:
    def _sub(self, db, org, status="active"):
        return db.subscriptions.insert_one({
            "organization_id": org, "plan_id": "starter", "status": status,
            "billing_cycle": "monthly", "cancel_at_period_end": False,
            "current_period_end": datetime.utcnow() + timedelta(days=20),
            "created_at": datetime.utcnow()}).inserted_id

    def test_owner_schedules_cancellation_at_period_end(self, env, t):
        client, db = env
        sa = self._sub(db, t.a)
        sb = self._sub(db, t.b)
        r = client.post("/api/billing/cancel", cookies=t.c_owner_a)
        assert r.status_code == 200
        j = r.json()
        assert j["success"] is True and j["subscription"]["id"] == str(sa)
        assert j["subscription"]["cancel_at_period_end"] is True
        doc = db.subscriptions.find_one({"_id": sa})
        assert doc["cancel_at_period_end"] is True and doc["status"] == "active"
        assert doc.get("cancelled_at") is not None
        assert db.subscriptions.find_one({"_id": sb})["cancel_at_period_end"] is False
        a = _audits(db, "subscription.cancel_requested", organization_id=t.a)
        assert a and a[0]["resource_id"] == str(sa)

    def test_admin_and_member_cannot_cancel(self, env, t):
        client, db = env
        sa = self._sub(db, t.a)
        r = client.post("/api/billing/cancel", cookies=t.c_admin_a)
        assert r.status_code == 403 and "owner" in r.json()["detail"]
        assert client.post("/api/billing/cancel", cookies=t.c_member_a).status_code == 403
        assert client.post("/api/billing/cancel").status_code == 401
        assert db.subscriptions.find_one({"_id": sa})["cancel_at_period_end"] is False

    def test_no_active_subscription_is_404(self, env, t):
        client, db = env
        self._sub(db, t.a, status="cancelled")
        r = client.post("/api/billing/cancel", cookies=t.c_owner_a)
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# compliance
# ═══════════════════════════════════════════════════════════════════════════

class TestCompliancePublic:
    def test_public_notice(self, env):
        client, _ = env
        r = client.get("/api/public/compliance-notice")
        assert r.status_code == 200
        j = r.json()
        assert j["opt_out_endpoint"] == "/api/compliance/opt-out"
        assert j["version"] and len(j["rights_supported"]) == 4

    def test_anonymous_prospect_can_opt_out_by_email_link(self, env, t):
        # public (no LeadAI account), but nothing changes until the emailed link is opened
        client, db = env
        lead = _lead(db, t.b, t.member_b, email="prospect@mail.test")
        with patch("app.events.email.send_email") as send:
            r = client.post("/api/compliance/opt-out",
                            json={"contact_type": "email", "contact_value": "Prospect@Mail.test"})
        assert r.status_code == 200 and r.json()["status"] == "confirmation_sent"
        assert db.compliance_blocklist.count_documents({}) == 0
        assert db.ai_comments.find_one({"_id": ObjectId(lead)})["email"] == "prospect@mail.test"
        # the email links to the /opt-out page (opening it changes nothing: mail
        # scanners open links); the page's "Yes, remove" button calls the confirm API
        page_link = re.search(r"(/opt-out\?\S+)", send.call_args.args[2]).group(1)
        assert send.call_args.args[0] == "prospect@mail.test"
        assert client.get(page_link).status_code == 200
        assert db.compliance_blocklist.count_documents({}) == 0
        link = "/api/compliance/opt-out/confirm?" + page_link.split("?", 1)[1]
        bad = client.get(link.replace("token=", "token=x"))
        assert bad.status_code == 400 and db.compliance_blocklist.count_documents({}) == 0
        ok = client.get(link)
        assert ok.status_code == 200 and "Opt-out confirmed" in ok.text
        assert db.compliance_blocklist.find_one({"organization_id": None, "type": "email",
                                                 "value": "prospect@mail.test"})
        assert "email" not in db.ai_comments.find_one({"_id": ObjectId(lead)})
        assert client.get(link).status_code == 400  # single use

    def test_opt_out_by_sms_code_blocks_platform_wide_and_purges_leads(self, env, t):
        client, db = env
        la = _lead(db, t.a, t.member_a, phone="15551234567")
        lb = _lead(db, t.b, t.member_b, phone="15551234567")
        keep = _lead(db, t.a, t.member_a, phone="15550000000")
        with patch("app.messaging.sms.send_sms") as sms:
            r = client.post("/api/compliance/opt-out",
                            json={"contact_type": "phone", "contact_value": "+1 (555) 123-4567"})
        assert r.status_code == 200 and db.compliance_blocklist.count_documents({}) == 0
        code = re.search(r"\b(\d{6})\b", sms.call_args.args[1]).group(1)
        wrong = "000000" if code != "000000" else "111111"
        assert client.post("/api/compliance/opt-out/confirm",
                           json={"contact_value": "+15551234567", "code": wrong}).status_code == 400
        r = client.post("/api/compliance/opt-out/confirm",
                        json={"contact_value": "+1 555 123 4567", "code": code})
        assert r.status_code == 200
        assert r.json()["data"] == {"success": True, "type": "phone", "value": "15551234567",
                                    "leads_purged": 2}
        for lid in (la, lb):
            d = db.ai_comments.find_one({"_id": ObjectId(lid)})
            assert "phone" not in d and d["is_lead"] is False and d["compliance_blocked"] is True
        assert db.ai_comments.find_one({"_id": ObjectId(keep)})["phone"] == "15550000000"

    def test_signed_in_member_cannot_wipe_another_tenants_leads(self, env, t):
        # before: any signed-in customer purged every tenant's matching leads at once
        client, db = env
        lb = _lead(db, t.b, t.member_b, phone="15551234567")
        with patch("app.messaging.sms.send_sms"):
            r = client.post("/api/compliance/opt-out", cookies=t.c_member_a,
                            json={"contact_type": "phone", "contact_value": "+15551234567"})
        assert r.status_code == 200 and r.json()["status"] == "confirmation_sent"
        assert db.ai_comments.find_one({"_id": ObjectId(lb)})["phone"] == "15551234567"
        r = client.post("/api/compliance/opt-out", cookies=t.c_member_a,
                        json={"contact_type": "username", "contact_value": "Priya Sharma"})
        assert r.status_code == 422  # a username can't be verified
        assert db.compliance_blocklist.count_documents({}) == 0

    def test_sms_code_dies_after_five_wrong_tries(self, env, t):
        client, db = env
        with patch("app.messaging.sms.send_sms") as sms:
            client.post("/api/compliance/opt-out", json={"contact_type": "phone", "contact_value": "+15559990000"})
        code = re.search(r"\b(\d{6})\b", sms.call_args.args[1]).group(1)
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            client.post("/api/compliance/opt-out/confirm", json={"contact_value": "+15559990000", "code": wrong})
        assert client.post("/api/compliance/opt-out/confirm",
                           json={"contact_value": "+15559990000", "code": code}).status_code == 400
        assert db.compliance_blocklist.count_documents({}) == 0

    def test_opt_out_rejects_invalid_values(self, env, t):
        client, db = env
        r = client.post("/api/compliance/opt-out", cookies=t.c_member_a,
                        json={"contact_type": "email", "contact_value": "not-an-email"})
        assert r.status_code == 422
        assert client.post("/api/compliance/opt-out", cookies=t.c_member_a,
                           json={"contact_type": "phone"}).status_code == 422
        assert db.compliance_blocklist.count_documents({}) == 0


class TestComplianceBlocklist:
    def test_admin_adds_lists_and_removes_own_entries(self, env, t):
        client, db = env
        la = _lead(db, t.a, t.member_a, email="buyer@x.test")
        lb = _lead(db, t.b, t.member_b, email="buyer@x.test")
        r = client.post("/api/compliance/blocklist", cookies=t.c_admin_a,
                        json={"contact_type": "email", "contact_value": " Buyer@X.test ",
                              "reason": "asked us"})
        assert r.status_code == 200
        assert r.json()["entry"]["value"] == "buyer@x.test" and r.json()["entry"]["leads_purged"] == 1
        assert "email" not in db.ai_comments.find_one({"_id": ObjectId(la)})
        assert db.ai_comments.find_one({"_id": ObjectId(lb)})["email"] == "buyer@x.test"
        entry = db.compliance_blocklist.find_one({"organization_id": t.a})
        assert entry["reason"] == "asked us" and entry["created_by"] == "admin@a.test"

        # B's own entry and a platform-wide one
        b_entry = db.compliance_blocklist.insert_one({"organization_id": t.b, "type": "phone",
                                                      "value": "19998887777", "created_at": datetime.utcnow()}).inserted_id
        db.compliance_blocklist.insert_one({"organization_id": None, "type": "email",
                                            "value": "global@x.test", "created_at": datetime.utcnow()})
        lst = client.get("/api/compliance/blocklist", cookies=t.c_owner_a).json()
        values = {i["value"] for i in lst["items"]}
        assert values == {"buyer@x.test", "global@x.test"} and lst["total"] == 2
        only_email = client.get("/api/compliance/blocklist?type=email&page_size=1", cookies=t.c_owner_a).json()
        assert only_email["total"] == 2 and len(only_email["items"]) == 1

        # B's id from A -> 404, untouched
        r = client.delete(f"/api/compliance/blocklist/{b_entry}", cookies=t.c_admin_a)
        assert r.status_code == 404
        assert db.compliance_blocklist.find_one({"_id": b_entry})
        # platform-wide entries cannot be removed by an organization
        g = db.compliance_blocklist.find_one({"organization_id": None})
        assert client.delete(f"/api/compliance/blocklist/{g['_id']}", cookies=t.c_admin_a).status_code == 404
        assert client.delete("/api/compliance/blocklist/not-an-id", cookies=t.c_admin_a).status_code == 404
        # own entry
        r = client.delete(f"/api/compliance/blocklist/{entry['_id']}", cookies=t.c_admin_a)
        assert r.status_code == 200 and r.json()["deleted_id"] == str(entry["_id"])
        assert db.compliance_blocklist.find_one({"_id": entry["_id"]}) is None
        assert _audits(db, "compliance.blocklist_removed", organization_id=t.a)

    def test_blocklist_needs_settings_manage(self, env, t):
        client, db = env
        entry = db.compliance_blocklist.insert_one({"organization_id": t.a, "type": "phone",
                                                    "value": "15551112222"}).inserted_id
        for c in (t.c_manager_a, t.c_member_a, t.c_viewer_a):
            assert client.get("/api/compliance/blocklist", cookies=c).status_code == 403
            assert client.post("/api/compliance/blocklist", cookies=c,
                               json={"contact_type": "phone", "contact_value": "15553334444"}).status_code == 403
            assert client.delete(f"/api/compliance/blocklist/{entry}", cookies=c).status_code == 403
        assert client.get("/api/compliance/blocklist").status_code == 401
        assert db.compliance_blocklist.count_documents({}) == 1

    def test_blocklist_rejects_invalid_phone(self, env, t):
        client, db = env
        r = client.post("/api/compliance/blocklist", cookies=t.c_admin_a,
                        json={"contact_type": "phone", "contact_value": "12"})
        assert r.status_code == 422 and "phone" in r.json()["detail"].lower()
        assert db.compliance_blocklist.count_documents({}) == 0


class TestComplianceDeletionAndRetention:
    def test_delete_request_purges_own_org_only(self, env, t):
        client, db = env
        la = _lead(db, t.a, t.member_a, phone="15557776666")
        lb = _lead(db, t.b, t.member_b, phone="15557776666")
        r = client.post("/api/compliance/delete-request", cookies=t.c_admin_a,
                        json={"identifier": "+1 555 777 6666", "identifier_type": "phone"})
        assert r.status_code == 200
        assert r.json()["success"] is True and r.json()["leads_purged"] == 1
        assert "phone" not in db.ai_comments.find_one({"_id": ObjectId(la)})
        assert db.ai_comments.find_one({"_id": ObjectId(lb)})["phone"] == "15557776666"
        assert db.compliance_blocklist.find_one({"organization_id": t.a, "value": "15557776666"})
        assert _audits(db, "compliance.data_deleted", organization_id=t.a)

    def test_delete_request_validation_and_roles(self, env, t):
        client, db = env
        assert client.post("/api/compliance/delete-request", cookies=t.c_admin_a,
                           json={"identifier": "nope", "identifier_type": "email"}).status_code == 422
        assert client.post("/api/compliance/delete-request", cookies=t.c_viewer_a,
                           json={"identifier": "15557776666"}).status_code == 403
        assert db.compliance_blocklist.count_documents({}) == 0

    def test_delete_request_by_username_purges_mixed_case_names(self, env, t):
        """BUG: a username / name deletion lower-cases the value
        (add_to_blocklist: ``val = raw_val.lower()``) and then matches
        ``commenter_name`` exactly, but leads store the author's name as
        scraped ("Priya Sharma"). A Right-to-be-Forgotten request by name
        therefore purges nothing for any name with a capital letter."""
        client, db = env
        lid = _lead(db, t.a, t.member_a, commenter_name="Priya Sharma", phone="15550001111")
        r = client.post("/api/compliance/delete-request", cookies=t.c_admin_a,
                        json={"identifier": "Priya Sharma", "identifier_type": "username"})
        assert r.status_code == 200
        d = db.ai_comments.find_one({"_id": ObjectId(lid)})
        assert d.get("compliance_blocked") is True and "phone" not in d
        assert r.json()["leads_purged"] == 1

    def test_purge_pii_uses_retention_and_stays_in_org(self, env, t):
        client, db = env
        old = datetime.utcnow() - timedelta(days=45)
        a_old = _lead(db, t.a, t.member_a, phone="15551110000", created_at=old, analyzed_at=old)
        a_new = _lead(db, t.a, t.member_a, phone="15552220000", created_at=datetime.utcnow())
        b_old = _lead(db, t.b, t.member_b, phone="15553330000", created_at=old, analyzed_at=old)
        db.organizations.update_many({}, {"$set": {"settings.pii_retention_days": 30}})
        # no retention configured / passed -> nothing
        r0 = client.post("/api/compliance/purge-pii?retention_days=0", cookies=t.c_admin_a)
        assert r0.status_code == 200 and r0.json()["purged_records"] == 0
        r = client.post("/api/compliance/purge-pii", cookies=t.c_admin_a)
        assert r.status_code == 200 and r.json()["purged_records"] == 1
        d = db.ai_comments.find_one({"_id": ObjectId(a_old)})
        assert "phone" not in d and d["pii_purged"] is True and d["pii_retention_days"] == 30
        assert db.ai_comments.find_one({"_id": ObjectId(a_new)})["phone"] == "15552220000"
        assert db.ai_comments.find_one({"_id": ObjectId(b_old)})["phone"] == "15553330000"
        assert client.post("/api/compliance/purge-pii", cookies=t.c_member_a).status_code == 403

    def test_retention_settings_round_trip(self, env, t):
        client, db = env
        g = client.get("/api/compliance/settings", cookies=t.c_owner_a)
        assert g.status_code == 200 and g.json()["pii_retention_days"] == 0
        assert g.json()["data_processing_notice"]["opt_out_endpoint"] == "/api/compliance/opt-out"
        r = client.put("/api/compliance/settings", cookies=t.c_owner_a, json={"pii_retention_days": 60})
        assert r.status_code == 200 and r.json()["pii_retention_days"] == 60
        assert db.organizations.find_one({"_id": ObjectId(t.a)})["settings"]["pii_retention_days"] == 60
        assert "pii_retention_days" not in (db.organizations.find_one({"_id": ObjectId(t.b)})["settings"])
        assert client.get("/api/compliance/settings", cookies=t.c_admin_a).json()["pii_retention_days"] == 60
        for bad in (-1, 366, "x"):
            assert client.put("/api/compliance/settings", cookies=t.c_owner_a,
                              json={"pii_retention_days": bad}).status_code == 422
        assert client.put("/api/compliance/settings", cookies=t.c_manager_a,
                          json={"pii_retention_days": 1}).status_code == 403
        assert client.get("/api/compliance/settings", cookies=t.c_member_a).status_code == 403
        assert db.organizations.find_one({"_id": ObjectId(t.a)})["settings"]["pii_retention_days"] == 60


# ═══════════════════════════════════════════════════════════════════════════
# notifications — POST /api/notifications/read
# ═══════════════════════════════════════════════════════════════════════════

class TestNotificationsRead:
    def _n(self, db, audience, org=None, user=None, read_by=None):
        return db.notifications.insert_one({
            "audience": audience, "organization_id": org, "user_id": user, "type": "x",
            "title": "t", "message": "m", "read_by": read_by or [],
            "created_at": datetime.utcnow()}).inserted_id

    def test_member_marks_all_only_marks_own(self, env, t):
        client, db = env
        mine = self._n(db, "user", t.a, t.member_a)
        org_a = self._n(db, "org_admin", t.a)
        other_user = self._n(db, "user", t.a, t.manager_a)
        sup = self._n(db, "super_admin")
        r = client.post("/api/notifications/read", cookies=t.c_member_a, json={})
        assert r.status_code == 200 and r.json() == {"success": True, "updated": 1}
        assert db.notifications.find_one({"_id": mine})["read_by"] == [t.member_a]
        for nid in (org_a, other_user, sup):
            assert db.notifications.find_one({"_id": nid})["read_by"] == []

    def test_admin_marks_one_and_read_is_per_person(self, env, t):
        client, db = env
        org_a = self._n(db, "org_admin", t.a, read_by=[t.owner_a])
        org_a2 = self._n(db, "org_admin", t.a)
        r = client.post("/api/notifications/read", cookies=t.c_admin_a, json={"id": str(org_a)})
        assert r.json()["updated"] == 1
        assert sorted(db.notifications.find_one({"_id": org_a})["read_by"]) == sorted([t.owner_a, t.admin_a])
        assert db.notifications.find_one({"_id": org_a2})["read_by"] == []
        listing = client.get("/api/notifications", cookies=t.c_admin_a).json()
        state = {i["id"]: i["read"] for i in listing["items"]}
        assert state == {str(org_a): True, str(org_a2): False} and listing["unread"] == 1
        # idempotent
        assert client.post("/api/notifications/read", cookies=t.c_admin_a,
                           json={"id": str(org_a)}).json()["updated"] == 0

    def test_other_tenant_or_bad_ids_change_nothing(self, env, t):
        client, db = env
        org_b = self._n(db, "org_admin", t.b)
        user_b = self._n(db, "user", t.b, t.member_b)
        for nid in (org_b, user_b):
            r = client.post("/api/notifications/read", cookies=t.c_owner_a, json={"id": str(nid)})
            assert r.status_code == 200 and r.json()["updated"] == 0
            assert db.notifications.find_one({"_id": nid})["read_by"] == []
        assert client.post("/api/notifications/read", cookies=t.c_owner_a,
                           json={"id": "garbage"}).json()["updated"] == 0
        assert client.post("/api/notifications/read", json={}).status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# org_admin — API keys, assignment rules, webhooks
# ═══════════════════════════════════════════════════════════════════════════

class TestOrgAdminApiKeys:
    def test_revoke_own_key_disables_it(self, env, t):
        client, db = env
        from app.services.api_keys import authenticate_api_key
        made = client.post("/api/org-admin/api-keys", cookies=t.c_admin_a,
                           json={"name": "CRM", "scopes": ["leads:read"]}).json()
        raw, key_id = made["api_key"], made["key_id"]
        assert authenticate_api_key(db, raw, "leads:read")["organization_id"] == t.a
        r = client.delete(f"/api/org-admin/api-keys/{key_id}", cookies=t.c_admin_a)
        assert r.status_code == 200 and r.json() == {"success": True, "revoked_id": key_id}
        assert db.api_keys.find_one({"key_id": key_id})["is_active"] is False
        with pytest.raises(Exception) as ei:
            authenticate_api_key(db, raw, "leads:read")
        assert getattr(ei.value, "status_code", None) == 401
        assert _audits(db, "api_key.revoked", organization_id=t.a, resource_id=key_id)

    def test_revoke_by_object_id_and_cross_tenant_404(self, env, t):
        client, db = env
        a_key = client.post("/api/org-admin/api-keys", cookies=t.c_owner_a, json={"name": "A"}).json()
        b_key = client.post("/api/org-admin/api-keys", cookies=t.c_owner_b, json={"name": "B"}).json()
        b_doc = db.api_keys.find_one({"key_id": b_key["key_id"]})
        for ident in (b_key["key_id"], str(b_doc["_id"])):
            assert client.delete(f"/api/org-admin/api-keys/{ident}", cookies=t.c_owner_a).status_code == 404
        assert db.api_keys.find_one({"_id": b_doc["_id"]})["is_active"] is True
        assert db.security_events.find_one({"type": "cross_tenant_access", "actor_email": "owner@a.test"})
        a_doc = db.api_keys.find_one({"key_id": a_key["key_id"]})
        assert client.delete(f"/api/org-admin/api-keys/{a_doc['_id']}", cookies=t.c_owner_a).status_code == 200
        assert db.api_keys.find_one({"_id": a_doc["_id"]})["is_active"] is False

    def test_revoke_needs_an_org_admin_with_portal(self, env, t):
        client, db = env
        k = client.post("/api/org-admin/api-keys", cookies=t.c_owner_a, json={"name": "A"}).json()["key_id"]
        for c in (t.c_manager_a, t.c_member_a, t.c_viewer_a):
            assert client.delete(f"/api/org-admin/api-keys/{k}", cookies=c).status_code == 403
        db.organizations.update_one({"_id": ObjectId(t.a)}, {"$set": {"status": "demo",
                                                                      "admin_portal_enabled": False}})
        r = client.delete(f"/api/org-admin/api-keys/{k}", cookies=t.c_owner_a)
        assert r.status_code == 403 and r.json()["detail"]["code"] == "admin_portal_disabled"
        assert db.api_keys.find_one({"key_id": k})["is_active"] is True


class TestOrgAdminAssignmentRules:
    def test_create_list_delete_own_rule(self, env, t):
        client, db = env
        r = client.post("/api/org-admin/assignment-rules", cookies=t.c_admin_a, json={
            "name": "Hot leads", "assignees": [t.member_a, t.manager_a],
            "criteria": {"priority": "high"}, "sla_hours": 0})
        assert r.status_code == 200
        rule = r.json()["rule"]
        assert rule["name"] == "Hot leads" and rule["organization_id"] == t.a
        assert rule["sla_hours"] == 1  # clamped to at least one hour
        doc = db.lead_assignment_rules.find_one({"rule_id": rule["rule_id"]})
        assert doc["assignees"] == [t.member_a, t.manager_a] and doc["created_by"] == "admin@a.test"
        assert _audits(db, "assignment_rule.created", organization_id=t.a)

        client.post("/api/org-admin/assignment-rules", cookies=t.c_admin_b,
                    json={"name": "B rule", "assignees": [t.member_b]})
        lst = client.get("/api/org-admin/assignment-rules", cookies=t.c_owner_a).json()
        assert lst["total"] == 1 and [i["name"] for i in lst["items"]] == ["Hot leads"]

        r = client.delete(f"/api/org-admin/assignment-rules/{rule['rule_id']}", cookies=t.c_admin_a)
        assert r.status_code == 200 and r.json()["deleted_id"] == rule["rule_id"]
        assert db.lead_assignment_rules.find_one({"rule_id": rule["rule_id"]}) is None

    def test_cross_tenant_delete_is_404_and_invalid_input_400(self, env, t):
        client, db = env
        b_rule = client.post("/api/org-admin/assignment-rules", cookies=t.c_admin_b,
                             json={"name": "B rule", "assignees": [t.member_b]}).json()["rule"]
        for ident in (b_rule["rule_id"], b_rule["id"]):
            assert client.delete(f"/api/org-admin/assignment-rules/{ident}", cookies=t.c_admin_a).status_code == 404
        assert db.lead_assignment_rules.count_documents({"organization_id": t.b}) == 1
        r = client.post("/api/org-admin/assignment-rules", cookies=t.c_admin_a,
                        json={"name": "Empty", "assignees": []})
        assert r.status_code == 400 and "assignee" in r.json()["detail"]
        assert client.post("/api/org-admin/assignment-rules", cookies=t.c_admin_a,
                           json={"name": "No list"}).status_code == 422
        assert db.lead_assignment_rules.count_documents({"organization_id": t.a}) == 0

    def test_rules_need_org_admin(self, env, t):
        client, db = env
        for c in (t.c_manager_a, t.c_member_a):
            assert client.get("/api/org-admin/assignment-rules", cookies=c).status_code == 403
            assert client.post("/api/org-admin/assignment-rules", cookies=c,
                               json={"name": "x", "assignees": [t.member_a]}).status_code == 403
        assert db.lead_assignment_rules.count_documents({}) == 0


class TestOrgAdminWebhooks:
    def test_register_list_delete_own_webhook(self, env, t):
        client, db = env
        r = client.post("/api/org-admin/webhooks", cookies=t.c_admin_a,
                        json={"url": "https://hooks.alpha.test/leadai", "events": ["lead.created"]})
        assert r.status_code == 200
        w = r.json()["webhook"]
        assert w["url"] == "https://hooks.alpha.test/leadai" and w["events"] == ["lead.created"]
        doc = db.outbound_webhooks.find_one({"webhook_id": w["webhook_id"]})
        assert doc["organization_id"] == t.a and len(doc["secret"]) == 32 and doc["is_active"] is True
        client.post("/api/org-admin/webhooks", cookies=t.c_admin_b, json={"url": "https://b.test/h"})
        lst = client.get("/api/org-admin/webhooks", cookies=t.c_owner_a).json()
        assert lst["total"] == 1 and lst["items"][0]["webhook_id"] == w["webhook_id"]
        assert lst["items"][0]["masked_secret"] == doc["secret"][:4] + "..." + doc["secret"][-4:]

        r = client.delete(f"/api/org-admin/webhooks/{w['webhook_id']}", cookies=t.c_admin_a)
        assert r.status_code == 200 and r.json()["deleted_id"] == w["webhook_id"]
        assert db.outbound_webhooks.find_one({"webhook_id": w["webhook_id"]}) is None
        assert _audits(db, "webhook.deleted", organization_id=t.a)

    def test_cross_tenant_delete_404_and_roles(self, env, t):
        client, db = env
        wb = client.post("/api/org-admin/webhooks", cookies=t.c_admin_b,
                         json={"url": "https://b.test/h"}).json()["webhook"]
        for ident in (wb["webhook_id"], wb["id"]):
            assert client.delete(f"/api/org-admin/webhooks/{ident}", cookies=t.c_owner_a).status_code == 404
        assert db.outbound_webhooks.count_documents({"organization_id": t.b}) == 1
        for c in (t.c_manager_a, t.c_member_a):
            assert client.get("/api/org-admin/webhooks", cookies=c).status_code == 403
            assert client.post("/api/org-admin/webhooks", cookies=c, json={"url": "https://x.test"}).status_code == 403
        assert client.post("/api/org-admin/webhooks", cookies=t.c_admin_a, json={}).status_code == 422
        assert db.outbound_webhooks.count_documents({"organization_id": t.a}) == 0

    def test_listing_never_returns_the_signing_secret(self, env, t):
        """BUG: list_outbound_webhooks adds ``masked_secret`` ("Mask secret
        in listing") but leaves the full ``secret`` in the document, and the
        route's _ser() only strips token_hash / password_hash — GET
        /api/org-admin/webhooks returns every webhook's HMAC signing secret."""
        client, db = env
        client.post("/api/org-admin/webhooks", cookies=t.c_admin_a,
                    json={"url": "https://hooks.alpha.test/x", "secret": "whsec_super_private_value"})
        r = client.get("/api/org-admin/webhooks", cookies=t.c_admin_a)
        assert r.status_code == 200 and r.json()["total"] == 1
        assert "whsec_super_private_value" not in r.text
        assert "secret" not in r.json()["items"][0]


# ═══════════════════════════════════════════════════════════════════════════
# org_admin — business summary, keyword library, recommendations, client export log
# ═══════════════════════════════════════════════════════════════════════════

class TestOrgAdminBusiness:
    def test_business_summary_reflects_the_profile(self, env, t):
        client, db = env
        db.organizations.update_one({"_id": ObjectId(t.a)}, {"$set": {
            "industry": "real_estate",
            "settings.business_profile": {"business_type": "residential",
                                          "target_customer_types": ["Buyers", "Investors"]},
            "settings.lead_keywords": ["2 bhk", "villa", "plot"],
            "settings.lead_exclude_keywords": ["job"]}})
        r = client.get("/api/org-admin/business-summary", cookies=t.c_admin_a)
        assert r.status_code == 200
        j = r.json()
        assert j["industry_key"] == "real_estate" and j["industry_name"] == "Real Estate"
        assert j["business_type"] == "residential"
        assert j["business_type_name"].startswith("Residential Property")
        assert j["target_customers"] == ["Buyers", "Investors"]
        assert (j["active_keywords_count"], j["excluded_keywords_count"], j["using_defaults"]) == (3, 1, False)
        # B (no profile) sees its own defaults
        jb = client.get("/api/org-admin/business-summary", cookies=t.c_owner_b).json()
        assert jb["industry_key"] == "general" and jb["using_defaults"] is True
        assert jb["business_type_name"] == "Not configured" and jb["target_customers"] == ["General Buyers"]

    def test_business_summary_needs_org_admin(self, env, t):
        client, _ = env
        for c in (t.c_manager_a, t.c_member_a, t.c_viewer_a):
            assert client.get("/api/org-admin/business-summary", cookies=c).status_code == 403
        assert client.get("/api/org-admin/business-summary").status_code == 401

    def test_keyword_library_save_and_read(self, env, t):
        client, db = env
        db.organizations.update_one({"_id": ObjectId(t.a)}, {"$set": {
            "industry": "real_estate", "settings.business_profile": {"business_type": "residential"}}})
        r = client.put("/api/org-admin/keyword-library", cookies=t.c_admin_a, json={
            "active_keywords": ["Villa", "villa", "  my custom term ", ""],
            "excluded_keywords": ["Job", "free"]})
        assert r.status_code == 200
        j = r.json()
        assert j["active_keywords"] == ["villa", "my custom term"] and j["excluded_keywords"] == ["job", "free"]
        s = db.organizations.find_one({"_id": ObjectId(t.a)})["settings"]
        assert s["lead_keywords"] == ["villa", "my custom term"]
        assert s["lead_exclude_keywords"] == ["job", "free"]
        assert s["business_profile"]["active_keywords"] == ["villa", "my custom term"]
        assert "lead_keywords" not in db.organizations.find_one({"_id": ObjectId(t.b)})["settings"]
        assert _audits(db, "keyword_library.updated", organization_id=t.a)[0]["details"] == \
            {"active_count": 2, "excluded_count": 2}

        lib = client.get("/api/org-admin/keyword-library", cookies=t.c_admin_a).json()
        assert lib["industry"] == "real_estate" and lib["can_edit"] is True
        types = {i["keyword"]: i["type"] for i in lib["active"]}
        assert types == {"villa": "suggested", "my custom term": "custom"}
        assert [i["keyword"] for i in lib["custom"]] == ["my custom term"]
        assert [i["keyword"] for i in lib["excluded"]] == ["job", "free"]
        sugg = {i["keyword"] for i in lib["suggested"]}
        assert "apartment" in sugg and "villa" not in sugg
        assert lib["summary"]["active_count"] == 2 and lib["using_defaults"] is False

    def test_keyword_library_roles_and_validation(self, env, t):
        client, db = env
        for c in (t.c_manager_a, t.c_member_a):
            assert client.get("/api/org-admin/keyword-library", cookies=c).status_code == 403
            assert client.put("/api/org-admin/keyword-library", cookies=c,
                              json={"active_keywords": ["x"]}).status_code == 403
        assert client.put("/api/org-admin/keyword-library", cookies=t.c_admin_a,
                          json={"active_keywords": "not-a-list"}).status_code == 422
        assert "lead_keywords" not in db.organizations.find_one({"_id": ObjectId(t.a)})["settings"]

    def test_keyword_library_honours_a_custom_catalog_industry(self, env, t):
        """BUG: GET /keyword-library calls get_org_keyword_library(org, db)
        with the ASYNC (motor) database; build_context -> list_industries
        iterates ``db.industries.find({})`` synchronously, which raises and is
        swallowed, so the Super Admin's industry catalog is ignored there: an
        organization on a custom industry is shown "general" while
        /business-summary (sync DB) shows the right one."""
        client, db = env
        db.industries.insert_one({"key": "pet_grooming", "name": "Pet Grooming", "enabled": True,
                                  "default_keywords": ["grooming"], "requirement_terms": ["groom"]})
        db.organizations.update_one({"_id": ObjectId(t.a)}, {"$set": {"industry": "pet_grooming"}})
        summary = client.get("/api/org-admin/business-summary", cookies=t.c_admin_a).json()
        assert summary["industry_key"] == "pet_grooming"
        lib = client.get("/api/org-admin/keyword-library", cookies=t.c_admin_a).json()
        assert lib["industry"] == "pet_grooming"
        assert lib["industry_name"] == "Pet Grooming"

    def test_recommend_keywords(self, env, t):
        client, _ = env
        r = client.post("/api/org-admin/recommend-keywords", cookies=t.c_admin_a, json={
            "industry": "real_estate", "business_type": "commercial",
            "products_services": ["Co-Working Desk"]})
        assert r.status_code == 200
        j = r.json()
        assert j["success"] is True and j["industry"] == "real_estate" and j["business_type"] == "commercial"
        assert j["recommended"][0] == "co-working desk"
        assert "office space" in j["groups"]["product_service"]
        assert j["total"] == len(j["recommended"]) and j["recommended_exclusions"]
        unknown = client.post("/api/org-admin/recommend-keywords", cookies=t.c_admin_a,
                              json={"industry": "no_such_thing"}).json()
        assert unknown["industry"] == "no_such_thing" and unknown["recommended"]  # falls back to general
        assert client.post("/api/org-admin/recommend-keywords", cookies=t.c_member_a, json={}).status_code == 403
        assert client.post("/api/org-admin/recommend-keywords", cookies=t.c_admin_a,
                           json={"products_services": "x"}).status_code == 422

    def test_client_export_log_is_recorded_and_audited(self, env, t):
        client, db = env
        r = client.post("/api/org-admin/exports/client-log", cookies=t.c_admin_a, json={
            "table": "Leads <script>", "rows": 99_999_999, "columns": ["name", "x" * 200],
            "filters": {"status": "new", "bad key!": "v", "empty": "", "n": 3}})
        assert r.status_code == 200 and r.json()["success"] is True
        doc = db.exports.find_one({"_id": ObjectId(r.json()["id"])})
        assert doc["organization_id"] == t.a and doc["user_id"] == t.admin_a
        assert doc["scope"] == "Leads script" and doc["rows"] == 10_000_000
        assert doc["columns"] == ["name", "x" * 80]
        assert doc["filters"] == {"status": "new", "badkey": "v", "n": 3}
        assert doc["source"] == "admin_portal_client"
        a = _audits(db, "export.client", organization_id=t.a)
        assert a and a[0]["resource_id"] == r.json()["id"] and a[0]["details"]["client_side"] is True
        # B never sees it
        assert db.exports.count_documents({"organization_id": t.b}) == 0

    def test_client_export_log_roles_and_validation(self, env, t):
        client, db = env
        for c in (t.c_manager_a, t.c_member_a):
            assert client.post("/api/org-admin/exports/client-log", cookies=c,
                               json={"table": "leads"}).status_code == 403
        assert client.post("/api/org-admin/exports/client-log", cookies=t.c_admin_a,
                           json={"rows": 3}).status_code == 422
        assert db.exports.count_documents({}) == 0


# ═══════════════════════════════════════════════════════════════════════════
# search — notes, catalog, scheduled scans, bulk, cancel
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def mongo_array_unset(monkeypatch):
    """mongomock silently ignores ``$unset`` on an array element; real MongoDB
    sets that element to null (which the route then ``$pull``s). Emulate the
    real semantics so the route's DB effect can be checked."""
    import mongomock.collection as mc
    orig = mc.Collection.update_one

    def update_one(self, filter, update, *a, **kw):
        unset = dict((update or {}).get("$unset") or {})
        arr = {k: None for k in unset if k.rsplit(".", 1)[-1].isdigit()}
        if arr:
            update = dict(update)
            rest = {k: v for k, v in unset.items() if k not in arr}
            if rest:
                update["$unset"] = rest
            else:
                update.pop("$unset")
            update["$set"] = {**update.get("$set", {}), **arr}
        return orig(self, filter, update, *a, **kw)
    monkeypatch.setattr(mc.Collection, "update_one", update_one)


@pytest.mark.usefixtures("mongo_array_unset")
class TestLeadNoteDelete:
    def _with_notes(self, db, org, uid):
        return _lead(db, org, uid, notes=[{"text": "n0"}, {"text": "n1"}, {"text": "n2"}])

    def test_delete_note_by_index(self, env, t):
        client, db = env
        lid = self._with_notes(db, t.a, t.member_a)
        r = client.delete(f"/api/leads/{lid}/notes/1", cookies=t.c_member_a)
        assert r.status_code == 200 and r.json() == {"ok": True}
        d = db.ai_comments.find_one({"_id": ObjectId(lid)})
        assert [n["text"] for n in d["notes"]] == ["n0", "n2"]
        assert d.get("lead_updated_at") is not None
        assert client.delete(f"/api/leads/{lid}/notes/1", cookies=t.c_member_a).status_code == 200
        assert [n["text"] for n in db.ai_comments.find_one({"_id": ObjectId(lid)})["notes"]] == ["n0"]

    def test_bad_index_and_bad_id(self, env, t):
        client, db = env
        lid = self._with_notes(db, t.a, t.member_a)
        assert client.delete(f"/api/leads/{lid}/notes/3", cookies=t.c_member_a).status_code == 400
        assert client.delete(f"/api/leads/{lid}/notes/-1", cookies=t.c_member_a).status_code == 400
        assert client.delete(f"/api/leads/{lid}/notes/x", cookies=t.c_member_a).status_code == 422
        assert client.delete("/api/leads/not-an-id/notes/0", cookies=t.c_member_a).status_code == 400
        assert len(db.ai_comments.find_one({"_id": ObjectId(lid)})["notes"]) == 3

    def test_other_tenant_colleague_and_viewer_cannot_delete(self, env, t):
        client, db = env
        lid = self._with_notes(db, t.a, t.member_a)
        assert client.delete(f"/api/leads/{lid}/notes/0", cookies=t.c_owner_b).status_code == 404
        assert db.security_events.find_one({"type": "cross_tenant_access", "actor_email": "owner@b.test"})
        # a colleague without data.view_all who neither owns nor is assigned the lead
        other_member, c_other = _member(db, t.a, "member2@a.test", "member")
        assert client.delete(f"/api/leads/{lid}/notes/0", cookies=c_other).status_code == 404
        assert client.delete(f"/api/leads/{lid}/notes/0", cookies=t.c_viewer_a).status_code == 403
        assert len(db.ai_comments.find_one({"_id": ObjectId(lid)})["notes"]) == 3
        # the org admin (data.view_all) may
        assert client.delete(f"/api/leads/{lid}/notes/0", cookies=t.c_admin_a).status_code == 200
        assert len(db.ai_comments.find_one({"_id": ObjectId(lid)})["notes"]) == 2


class TestBusinessContextCatalog:
    def test_catalog_is_public_without_org_defaults(self, env):
        client, _ = env
        r = client.get("/api/business-context/catalog")
        assert r.status_code == 200
        j = r.json()
        assert j["success"] is True and j["org_defaults"] == {}
        re_ = j["catalog"]["real_estate"]
        assert re_["name"] == "Real Estate"
        assert "Residential Property (Flats, Villas, Houses)" in re_["business_types"]
        assert "apartment" in re_["keyword_groups"]["product_service"]

    def test_catalog_includes_own_org_defaults_only(self, env, t):
        client, db = env
        db.organizations.update_one({"_id": ObjectId(t.a)}, {"$set": {
            "industry": "automotive",
            "settings.business_profile": {"business_type": "dealer", "primary_offerings": ["SUV"]},
            "settings.lead_keywords": ["test drive"]}})
        db.organizations.update_one({"_id": ObjectId(t.b)}, {"$set": {
            "industry": "education", "settings.lead_keywords": ["admission"]}})
        j = client.get("/api/business-context/catalog", cookies=t.c_member_a).json()
        assert j["org_defaults"]["industry"] == "automotive"
        assert j["org_defaults"]["business_type"] == "dealer"
        assert j["org_defaults"]["primary_offerings"] == ["SUV"]
        assert j["org_defaults"]["active_keywords"] == ["test drive"]
        jb = client.get("/api/business-context/catalog", cookies=t.c_member_b).json()
        assert jb["org_defaults"]["industry"] == "education"
        assert jb["org_defaults"]["active_keywords"] == ["admission"]
        assert jb["catalog"] == j["catalog"]


class TestScheduledScans:
    def test_create_scheduled_scan(self, env, t):
        client, db = env
        r = client.post("/api/search/scheduled", cookies=t.c_member_a, json={
            "url": "https://facebook.com/acmeshoes", "frequency": "Weekly", "max_posts": 5})
        assert r.status_code == 200
        scan = r.json()["scan"]
        assert scan["platform"] == "facebook" and scan["url"] == "https://www.facebook.com/acmeshoes"
        assert scan["interval_hours"] == 168 and scan["frequency"] == "weekly"
        doc = db.scheduled_scans.find_one({"scan_id": scan["scan_id"]})
        assert doc["organization_id"] == t.a and doc["user_id"] == t.member_a
        assert doc["status"] == "active" and doc["max_posts"] == 5

    def test_create_rejects_bad_input_and_viewers(self, env, t):
        client, db = env
        r = client.post("/api/search/scheduled", cookies=t.c_member_a, json={"url": "https://example.com/x"})
        assert r.status_code == 400 and "not currently supported" in r.json()["detail"]
        assert client.post("/api/search/scheduled", cookies=t.c_member_a,
                           json={"url": "https://facebook.com/x", "max_posts": 500}).status_code == 422
        assert client.post("/api/search/scheduled", cookies=t.c_viewer_a,
                           json={"url": "https://facebook.com/x"}).status_code == 403
        assert client.post("/api/search/scheduled", json={"url": "https://facebook.com/x"}).status_code == 401
        assert db.scheduled_scans.count_documents({}) == 0

    def test_list_scheduled_scans(self, env, t):
        """BUG: GET /api/search/scheduled is declared AFTER GET
        /api/search/{run_id} on the same router, so FastAPI routes it to
        get_search_run(run_id="scheduled") -> 404 "Search run not found";
        the scheduled-scan list can never be read."""
        client, db = env
        mine = client.post("/api/search/scheduled", cookies=t.c_member_a,
                           json={"url": "https://facebook.com/acmeshoes"}).json()["scan"]
        client.post("/api/search/scheduled", cookies=t.c_member_b,
                    json={"url": "https://facebook.com/betashoes"})
        r = client.get("/api/search/scheduled", cookies=t.c_member_a)
        assert r.status_code == 200
        assert r.json()["total"] == 1 and r.json()["items"][0]["scan_id"] == mine["scan_id"]

    def test_delete_scheduled_scan(self, env, t):
        client, db = env
        a = client.post("/api/search/scheduled", cookies=t.c_member_a,
                        json={"url": "https://facebook.com/acmeshoes"}).json()["scan"]
        b = client.post("/api/search/scheduled", cookies=t.c_member_b,
                        json={"url": "https://facebook.com/betashoes"}).json()["scan"]
        for ident in (b["scan_id"], b["id"]):
            assert client.delete(f"/api/search/scheduled/{ident}", cookies=t.c_owner_a).status_code == 404
        assert db.scheduled_scans.find_one({"scan_id": b["scan_id"]})
        assert client.delete(f"/api/search/scheduled/{a['scan_id']}", cookies=t.c_viewer_a).status_code == 403
        r = client.delete(f"/api/search/scheduled/{a['scan_id']}", cookies=t.c_member_a)
        assert r.status_code == 200 and r.json() == {"success": True, "deleted_id": a["scan_id"]}
        assert db.scheduled_scans.find_one({"scan_id": a["scan_id"]}) is None
        assert client.delete(f"/api/search/scheduled/{a['scan_id']}", cookies=t.c_member_a).status_code == 404


class TestBulkSearch:
    def test_bulk_validates_and_deduplicates_urls(self, env, t):
        # every URL is admitted like a single search (tests/test_qa_regressions.py
        # covers charging and the run records); here: dedupe, blanks, bad links
        client, db = env
        with patch("app.api.routes.search.admit_url_search") as admit:
            admit.side_effect = lambda ctx, request, url, **kw: {
                "run_id": f"URL_{len(admit.call_args_list)}", "platform": "instagram" if "instagram" in url
                else "facebook", "canonical_url": url, "status": "running"}
            r = client.post("/api/search/bulk", cookies=t.c_member_a, json={"urls": [
                "https://facebook.com/acmeshoes", "https://www.facebook.com/acmeshoes", "",
                "https://example.com/nope", "https://instagram.com/acme.shoes"], "max_posts": 3})
        assert r.status_code == 200
        j = r.json()
        assert (j["total_submitted"], j["total_queued"], j["total_invalid"]) == (5, 2, 1)
        assert {q["platform"] for q in j["queued_runs"]} == {"facebook", "instagram"}
        assert j["invalid_urls"][0]["url"] == "https://example.com/nope"
        assert admit.call_count == 2 and all(c.kwargs["max_posts"] == 3 for c in admit.call_args_list)

    def test_bulk_needs_search_create(self, env, t):
        client, db = env
        assert client.post("/api/search/bulk", cookies=t.c_viewer_a,
                           json={"urls": ["https://facebook.com/x"]}).status_code == 403
        assert client.post("/api/search/bulk", json={"urls": ["https://facebook.com/x"]}).status_code == 401
        assert client.post("/api/search/bulk", cookies=t.c_member_a, json={}).status_code == 422
        assert db.job_queue.count_documents({}) == 0

    def test_bulk_respects_the_search_kill_switch(self, env, t):
        """BUG (billing/limits bypass): POST /api/search/bulk enqueues
        url_search jobs straight through process_bulk_urls, skipping every
        check admit_url_search documents as mandatory for a new URL search
        ("so neither can skip a rule": feature + platform switches, admin and
        plan caps, tokens, monthly search quota, run record). With searches
        disabled by the administrator it still queues Apify runs."""
        client, db = env
        from app.admin.settings import set_setting
        assert set_setting("features.url_search.enabled", False, by="test")
        # the switch is on effect for the single-URL search ...
        single = client.post("/api/url/search?url=https://facebook.com/acmeshoes", cookies=t.c_member_a)
        assert single.status_code == 403 and single.json()["detail"]["errorType"] == "feature_disabled"
        # ... and must be for the bulk one too
        r = client.post("/api/search/bulk", cookies=t.c_member_a,
                        json={"urls": ["https://facebook.com/acmeshoes"]})
        assert db.job_queue.count_documents({}) == 0
        assert r.status_code == 403


class TestCancelSearch:
    def _run(self, db, org, uid, status="running"):
        rid = f"URL{ObjectId()}"
        db.search_history.insert_one({"run_id": rid, "organization_id": org, "user_id": uid,
                                      "created_by": "x", "status": status, "created_at": datetime.utcnow()})
        db.job_queue.insert_one({"job_id": rid, "run_id": rid, "status": "running",
                                 "organization_id": org})
        return rid

    def test_cancel_running_search(self, env, t):
        client, db = env
        rid = self._run(db, t.a, t.member_a)
        r = client.post(f"/api/search/{rid}/cancel", cookies=t.c_member_a)
        assert r.status_code == 200
        assert r.json() == {"run_id": rid, "status": "cancelling",
                            "message": "Cancellation requested — stopping soon"}
        sh = db.search_history.find_one({"run_id": rid})
        assert sh["cancel_requested"] is True and sh["status"] == "cancelled"
        assert db.job_queue.find_one({"run_id": rid})["status"] != "running"
        assert _audits(db, "search.cancel_requested", resource_id=rid)

    def test_finished_search_is_left_alone(self, env, t):
        client, db = env
        rid = self._run(db, t.a, t.member_a, status="completed")
        r = client.post(f"/api/search/{rid}/cancel", cookies=t.c_member_a)
        assert r.status_code == 200
        assert r.json() == {"run_id": rid, "status": "completed",
                            "message": "This search is no longer running"}
        sh = db.search_history.find_one({"run_id": rid})
        assert sh["status"] == "completed" and "cancel_requested" not in sh

    def test_cannot_cancel_other_tenant_or_colleague_or_as_viewer(self, env, t):
        client, db = env
        rid = self._run(db, t.a, t.member_a)
        assert client.post(f"/api/search/{rid}/cancel", cookies=t.c_owner_b).status_code == 404
        _, c_other = _member(db, t.a, "member2@a.test", "member")
        assert client.post(f"/api/search/{rid}/cancel", cookies=c_other).status_code == 404
        assert client.post(f"/api/search/{rid}/cancel", cookies=t.c_viewer_a).status_code == 403
        assert client.post("/api/search/URLnope/cancel", cookies=t.c_member_a).status_code == 404
        sh = db.search_history.find_one({"run_id": rid})
        assert sh["status"] == "running" and "cancel_requested" not in sh
        # an org admin may cancel any run of the organization
        assert client.post(f"/api/search/{rid}/cancel", cookies=t.c_admin_a).json()["status"] == "cancelling"
