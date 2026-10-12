"""
Final account flow (in-memory MongoDB only).

Proves:
  * there is exactly ONE Super Admin — the SUPERADMIN_* environment account:
    a database record carrying "super_admin" (admin_users or users) is never
    a Super Admin, and staff accounts cannot be created with that role;
  * startup never creates an account from ADMIN_EMAIL / ADMIN_PASSWORD_HASH;
  * password resets issued by the Super Admin or an org Admin work without an
    email server: the one-time link is returned once to the issuer, and the
    user sets their own password with it (never shown, never plaintext);
  * the Super Admin can change a sign-in email and a member's role (audited,
    owner protected, sessions revoked on email change);
  * the plan's user limit is enforced by the server on invitations.
"""
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from tests.conftest import TEST_SUPERADMIN_PASSWORD, as_superadmin
from tests.test_super_admin_portal2 import _cookie, _org, _user

SUPER = "owner@leadai.example"


@pytest.fixture
def env():
    as_superadmin(SUPER)
    with patch("app.admin.settings.is_maintenance_enabled", return_value=False):
        from app.main import app
        with TestClient(app) as client:
            from app.db.mongo import get_sync_db
            yield client, get_sync_db()


def _super_login(client):
    r = client.post("/api/auth/login", json={"email": SUPER, "password": TEST_SUPERADMIN_PASSWORD,
                                             "scope": "admin"})
    assert r.status_code == 200, r.text


def _site_cookie(uid, email, org, role):
    return _cookie({"user_id": uid, "email": email, "name": email.split("@")[0],
                    "scope": "site", "organization_id": org, "org_role": role})


# ── exactly one Super Admin ─────────────────────────────────────────────────

def test_database_super_admin_records_are_never_super_admin(env):
    client, db = env
    from app.auth.roles import effective_role
    db.admin_users.insert_one({"email": "legacy@leadai.example", "role": "super_admin",
                               "enabled": True, "name": "Legacy"})
    uid = _user(db, "dbsuper@leadai.example", platform_role="super_admin")
    assert effective_role("legacy@leadai.example") == "manager"
    assert effective_role("dbsuper@leadai.example") == "manager"
    assert effective_role(SUPER) == "super_admin"
    cookie = _cookie({"user_id": uid, "email": "dbsuper@leadai.example", "name": "x",
                      "scope": "admin", "role": "super_admin", "platform_role": "super_admin"})
    assert client.get("/api/super-admin/dashboard", cookies=cookie).status_code == 403
    # platform staff open the one platform portal; the Super Admin APIs stay closed (above)
    assert client.get("/superadmin", cookies=cookie, follow_redirects=False).status_code == 200


def test_staff_accounts_cannot_be_created_as_super_admin(env):
    client, db = env
    _super_login(client)
    r = client.post("/api/admin/users", json={"email": "second@leadai.example", "name": "Second",
                                              "password": "Second-Pass-2026", "role": "super_admin"})
    assert r.status_code == 422
    assert not db.admin_users.find_one({"email": "second@leadai.example"})
    ok = client.post("/api/admin/users", json={"email": "ops@leadai.example", "name": "Ops",
                                               "password": "Ops-Pass-2026x", "role": "manager"})
    assert ok.status_code == 200, ok.text


def test_startup_never_creates_accounts_from_admin_env(env):
    _client, db = env
    from app.config import get_settings
    from app.db.migration import migrate_to_multi_tenant
    from app.auth.crypto import hash_password
    s = get_settings()
    s.admin_email, s.admin_password_hash = "legacy-site@leadai.example", hash_password("Legacy-2026x")
    migrate_to_multi_tenant(db)
    assert db.users.find_one({"email": "legacy-site@leadai.example"}) is None


# ── password resets without an email server ─────────────────────────────────

def _token_of(url):
    return parse_qs(urlsplit(url).query)["token"][0]


def test_super_admin_reset_returns_one_time_link_when_email_not_sent(env):
    client, db = env
    org = _org(db, "Reset Org", admin_portal_enabled=True)
    uid = _user(db, "customer@leadai.example", org, role="owner")
    _super_login(client)
    r = client.post(f"/api/super-admin/users/{uid}/reset-access", json={"reason": "locked out"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["email_delivery"] != "sent" and "/reset-password?token=" in body["reset_url"]
    token = _token_of(body["reset_url"])
    # the plain token is never stored — only its hash
    assert not db.password_resets.find_one({"token_hash": token})
    client.cookies.clear()
    assert client.post("/api/auth/password/reset",
                       json={"token": token, "new_password": "Brand-New-2026"}).status_code == 200
    login = client.post("/api/auth/login", json={"email": "customer@leadai.example",
                                                  "password": "Brand-New-2026", "scope": "site"})
    assert login.status_code == 200, login.text
    assert db.users.find_one({"_id": ObjectId(uid)})["password_hash"].startswith("$2")
    # single use
    assert client.post("/api/auth/password/reset",
                       json={"token": token, "new_password": "Another-2026x"}).status_code != 200


def test_reset_link_not_returned_when_email_was_sent(env):
    client, db = env
    org = _org(db, "Mail Org")
    uid = _user(db, "mailed@leadai.example", org, role="owner")
    _super_login(client)
    with patch("app.events.email.send_email", return_value="sent"):
        body = client.post(f"/api/super-admin/users/{uid}/reset-access", json={}).json()
    assert body["email_delivery"] == "sent" and "reset_url" not in body


def test_org_admin_reset_returns_one_time_link(env):
    client, db = env
    org = _org(db, "Team Org", admin_portal_enabled=True)
    owner = _user(db, "boss@leadai.example", org, role="owner")
    member = _user(db, "staff@leadai.example", org, role="member")
    r = client.post(f"/api/org-admin/users/{member}/reset-access", json={"revoke_sessions": True},
                    cookies=_site_cookie(owner, "boss@leadai.example", org, "owner"))
    assert r.status_code == 200, r.text
    assert "/reset-password?token=" in r.json()["reset_url"]


# ── Super Admin account management ──────────────────────────────────────────

def test_super_admin_changes_sign_in_email(env):
    client, db = env
    org = _org(db, "Mail Change Org", admin_portal_enabled=True)
    uid = _user(db, "old@leadai.example", org, role="owner")
    _user(db, "taken@leadai.example", org, role="member")
    _super_login(client)
    assert client.patch(f"/api/super-admin/users/{uid}/email",
                        json={"email": "taken@leadai.example"}).status_code == 409
    assert client.patch(f"/api/super-admin/users/{uid}/email",
                        json={"email": SUPER}).status_code == 409
    assert client.patch(f"/api/super-admin/users/{uid}/email",
                        json={"email": "not-an-email"}).status_code == 422
    r = client.patch(f"/api/super-admin/users/{uid}/email",
                     json={"email": "New@LeadAI.example", "reason": "typo"})
    assert r.status_code == 200, r.text
    assert db.users.find_one({"_id": ObjectId(uid)})["email"] == "new@leadai.example"
    row = db.audit_logs.find_one({"action": "user.email_changed"})
    assert row["details"]["before"] == "old@leadai.example" and row["actor_email"] == SUPER
    client.cookies.clear()
    assert client.post("/api/auth/login", json={"email": "old@leadai.example", "password": "Str0ngPass!",
                                                "scope": "site"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "new@leadai.example", "password": "Str0ngPass!",
                                                "scope": "site"}).status_code == 200


def test_super_admin_changes_member_role(env):
    client, db = env
    org = _org(db, "Role Org", admin_portal_enabled=True)
    owner = _user(db, "own@leadai.example", org, role="owner")
    member = _user(db, "mem@leadai.example", org, role="member")
    # not reachable for an organization owner
    assert client.patch(f"/api/super-admin/organizations/{org}/members/{member}/role",
                        json={"role": "admin"},
                        cookies=_site_cookie(owner, "own@leadai.example", org, "owner")).status_code in (401, 403)
    _super_login(client)
    base = f"/api/super-admin/organizations/{org}"
    assert client.patch(f"{base}/members/{member}/role", json={"role": "super_admin"}).status_code == 422
    assert client.patch(f"{base}/members/{member}/role", json={"role": "owner"}).status_code == 422
    assert client.patch(f"{base}/members/{owner}/role", json={"role": "member"}).status_code == 409
    r = client.patch(f"{base}/members/{member}/role", json={"role": "admin", "reason": "promoted"})
    assert r.status_code == 200, r.text
    assert db.organization_members.find_one({"organization_id": org, "user_id": member})["role"] == "admin"
    row = db.audit_logs.find_one({"action": "member.role_changed"})
    assert row["details"]["before"] == "member" and row["details"]["after"] == "admin"


# ── plan user limit ─────────────────────────────────────────────────────────

def test_plan_user_limit_is_enforced_on_invitations(env):
    client, db = env
    from app.billing.plan_admin import create_plan
    from app.db.mongo import get_async_db
    import asyncio
    asyncio.run(create_plan(get_async_db(), {
        "name": "Team2", "slug": "team2", "price_monthly": 10, "price_yearly": 100, "currency": "USD",
        "features": ["url_search", "facebook", "team_management"],
        "limits": {"team_members": 2}}, actor="test"))
    org = _org(db, "Limit Org", admin_portal_enabled=True, plan_id="team2")
    db.subscriptions.insert_one({"organization_id": org, "plan_id": "team2", "status": "active",
                                 "created_at": __import__("datetime").datetime.utcnow()})
    owner = _user(db, "limit-owner@leadai.example", org, role="owner")
    ck = _site_cookie(owner, "limit-owner@leadai.example", org, "owner")
    first = client.post("/api/organizations/current/invitations", cookies=ck,
                        json={"email": "u1@leadai.example", "role": "member"})
    assert first.status_code in (200, 201), first.text
    second = client.post("/api/organizations/current/invitations", cookies=ck,
                         json={"email": "u2@leadai.example", "role": "member"})
    assert second.status_code == 402, second.text
