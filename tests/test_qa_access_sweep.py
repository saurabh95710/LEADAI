"""Route-wide access sweep (runs on the in-memory MongoDB from tests/conftest.py).

Every API route the app serves is called, with every method it declares, by
each kind of caller that must NOT reach it. The route table is read from the
running app, so a route added later is swept automatically:

- anonymous            -> every non-public /api route answers 401/403
- customer (site)      -> /api/super-admin, /api/admin, /api/partner refused
- org member / viewer  -> /api/org-admin refused (403)
- partner session      -> /api/super-admin, /api/admin, /api/org-admin refused
- Super Admin (admin)  -> customer-only APIs refused (no site session)
- platform viewer      -> /api/super-admin refused; /api/admin writes refused

A refusal must come from the access check: 2xx means the caller got in, 5xx
means the route crashed before refusing, and 422 means the body was validated
before any access check ran. Each is reported with the route and the caller.
"""
import re
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.auth.crypto import hash_password
from app.auth.service import COOKIE_NAME, build_session_value, create_tracked_session
from tests.conftest import TEST_SUPERADMIN_PASSWORD, as_superadmin

NOW = datetime.now(timezone.utc)
SUPER = "sweep-root@leadai.example"
PARTNER_PW = "Partner-Pass-2026"
FAKE_ID = "0123456789abcdef01234567"

# Reachable without a session by design (sign-in, sign-up, public site,
# signed webhooks, invitation links, referral links, API-key REST API).
PUBLIC_PREFIXES = ("/api/public/", "/api/billing/webhook", "/api/invitations/", "/api/v1/")
PUBLIC_EXACT = {
    "/api/health", "/api/billing/plans", "/api/business-context/catalog", "/api/comment-filters/catalog",
    "/api/compliance/opt-out", "/api/admin/impersonate/exit", "/api/super-admin/impersonate/exit",
    "/api/auth/signup", "/api/auth/login", "/api/auth/logout", "/api/auth/status",
    "/api/auth/password/forgot", "/api/auth/password/reset", "/api/auth/verify-email",
    # "I didn't get the link": open by design, mails only real unverified accounts (BUG-002)
    "/api/auth/verify-email/resend",
    # prospect opt-out: public by design, nothing applies before the email link / SMS code (BUG-009)
    "/api/compliance/opt-out/confirm",
}
REFUSED = {401, 403}

pytestmark = pytest.mark.own_db


@pytest.fixture(autouse=True)
def _super_admin_env():
    # conftest clears SUPERADMIN_* before every test; the module world needs it
    as_superadmin(SUPER)


def _routes():
    """(method, path) for every API route, including those of included routers."""
    from app.main import app

    def walk(routes):
        for r in routes:
            if type(r).__name__ == "_IncludedRouter":
                yield from walk(r.original_router.routes)
            elif isinstance(r, APIRoute):
                yield r

    out = set()
    for r in walk(app.routes):
        if not r.path.startswith("/api/"):
            continue
        for m in r.methods - {"HEAD", "OPTIONS"}:
            out.add((m, r.path))
    return sorted(out)


def _concrete(path):
    return re.sub(r"\{[^}]+\}", FAKE_ID, path)


def _is_public(path):
    return path in PUBLIC_EXACT or path.startswith(PUBLIC_PREFIXES)


# Routes that check the session inside the handler, after the body is
# validated: an empty body would be refused for the wrong reason (422/400),
# so they are called with a valid one and must still answer 401.
VALID_BODIES = {
    "/api/auth/password/change": {"current_password": "Old-Pass-2026!", "new_password": "New-Pass-2026!x"},
    "/api/auth/2fa/enable": {"secret": "JBSWY3DPEHPK3PXP", "code": "123456"},
    "/api/auth/switch-organization": {"organization_id": FAKE_ID},
}


def _call(client, method, path):
    kw = {"json": VALID_BODIES.get(path, {})} if method in ("POST", "PUT", "PATCH") else {}
    return client.request(method, _concrete(path), **kw)


def _sweep(client, targets):
    """Call every target; return [(method, path, status, body)] that were not refused."""
    leaks = []
    for method, path in targets:
        r = _call(client, method, path)
        if r.status_code not in REFUSED:
            leaks.append((method, path, r.status_code, r.text[:160]))
    return leaks


def _fmt(leaks):
    return "\n".join(f"  {m:6s} {p}  -> {s}  {b}" for m, p, s, b in leaks)


# ── world ────────────────────────────────────────────────────────────────────

def _user(db, email, org_id=None, role="member", platform_role=None):
    uid = str(db.users.insert_one({
        "email": email, "name": email.split("@")[0], "password_hash": hash_password("Str0ngPass!"),
        "status": "active", "is_platform_admin": bool(platform_role), "platform_role": platform_role,
        "default_organization_id": org_id, "created_at": NOW}).inserted_id)
    if org_id:
        db.organization_members.insert_one({"organization_id": org_id, "user_id": uid, "role": role,
                                            "status": "active", "joined_at": NOW})
    return uid


def _client(app, claims):
    c = TestClient(app)
    c.cookies.set(COOKIE_NAME, build_session_value(create_tracked_session(claims)))
    return c


@pytest.fixture(scope="module")
def world():
    with pytest.MonkeyPatch.context() as mp:
        import mongomock
        import mongomock_motor
        sync_client = mongomock.MongoClient()
        mp.setattr("app.db.mongo.get_sync_client", lambda: sync_client)
        mp.setattr("app.db.mongo.get_async_client",
                   lambda: mongomock_motor.AsyncMongoMockClient(mock_mongo_client=sync_client))
        with patch("app.admin.settings.is_maintenance_enabled", return_value=False), \
                patch("app.main.ensure_indexes", lambda: None):
            from app.config import get_settings
            from app.main import app
            s = get_settings()
            saved = (s.superadmin_email, s.superadmin_password)
            as_superadmin(SUPER)
            try:
                with TestClient(app) as anon:
                    db = sync_client[s.mongo_db_name]
                    org = str(db.organizations.insert_one({
                        "name": "Sweep Org", "slug": "sweep-org", "status": "active",
                        "admin_portal_enabled": True, "created_at": NOW}).inserted_id)
                    callers = {"anon": anon}
                    for role in ("owner", "member", "viewer"):
                        uid = _user(db, f"{role}@sweep.test", org, role)
                        callers[role] = _client(app, {
                            "user_id": uid, "email": f"{role}@sweep.test", "name": role,
                            "scope": "site", "organization_id": org, "org_role": role})
                    for prole in ("viewer", "manager"):
                        uid = _user(db, f"staff-{prole}@sweep.test", platform_role=prole)
                        callers["staff_" + prole] = _client(app, {
                            "user_id": uid, "email": f"staff-{prole}@sweep.test", "name": prole,
                            "scope": "admin", "role": prole})
                    sa = TestClient(app)
                    r = sa.post("/api/auth/login", json={"email": SUPER, "password": TEST_SUPERADMIN_PASSWORD,
                                                         "scope": "admin"})
                    assert r.status_code == 200, r.text
                    callers["super"] = sa
                    callers["partner"] = _partner(anon, sa, app)
                    yield {"app": app, "db": db, "routes": _routes(), **callers}
            finally:
                s.superadmin_email, s.superadmin_password = saved


def _partner(anon, sa, app):
    body = {"name": "Sweep Partner", "email": "partner@sweep.test", "password": PARTNER_PW,
            "company": "Sweep Partner Co", "partner_type": "affiliate",
            "experience": "Ten years of SaaS sales", "promotion_plan": "Newsletter and webinars",
            "accepted_terms": True, "payout_info": {"method": "paypal", "paypal_email": "pay@sweep.test"}}
    r = anon.post("/api/public/partners/apply", json=body)
    assert r.status_code == 200, r.text
    r = sa.post(f"/api/super-admin/partners/applications/{r.json()['application']['id']}/approve",
                json={"partner_type": "affiliate"})
    assert r.status_code == 200, r.text
    p = TestClient(app)
    r = p.post("/api/auth/login", json={"email": "partner@sweep.test", "password": PARTNER_PW,
                                        "scope": "partner"})
    assert r.status_code == 200, r.text
    return p


def _select(routes, *prefixes, methods=None, exclude=()):
    return [(m, p) for m, p in routes
            if p.startswith(prefixes) and (methods is None or m in methods)
            and not p.startswith(tuple(exclude)) and not _is_public(p)]


# ── sweeps ───────────────────────────────────────────────────────────────────

def test_route_table_is_large(world):
    # guards the sweep itself: if route discovery breaks, every sweep would pass vacuously
    assert len(world["routes"]) > 500


def test_anonymous_is_refused_everywhere_but_public_routes(world):
    targets = [(m, p) for m, p in world["routes"] if not _is_public(p)]
    leaks = _sweep(world["anon"], targets)
    assert not leaks, f"{len(leaks)} route(s) answered an anonymous caller:\n{_fmt(leaks)}"


def test_customer_session_cannot_reach_platform_or_partner_apis(world):
    targets = _select(world["routes"], "/api/super-admin/", "/api/admin/", "/api/partner/")
    for caller in ("owner", "member"):
        leaks = _sweep(world[caller], targets)
        assert not leaks, f"org {caller} reached:\n{_fmt(leaks)}"


def test_org_member_and_viewer_cannot_use_org_admin_api(world):
    targets = _select(world["routes"], "/api/org-admin/")
    assert targets
    for caller in ("member", "viewer"):
        leaks = _sweep(world[caller], targets)
        assert not leaks, f"org {caller} reached Org Admin:\n{_fmt(leaks)}"


def test_partner_session_cannot_reach_admin_or_org_apis(world):
    targets = _select(world["routes"], "/api/super-admin/", "/api/admin/", "/api/org-admin/")
    leaks = _sweep(world["partner"], targets)
    assert not leaks, f"partner reached:\n{_fmt(leaks)}"


CUSTOMER_ONLY = ("/api/search", "/api/url/", "/api/pages", "/api/posts", "/api/comments",
                 "/api/keyword-search", "/api/me", "/api/org-admin/", "/api/leads")


def test_super_admin_session_cannot_use_customer_app_apis(world):
    targets = _select(world["routes"], *CUSTOMER_ONLY)
    assert targets
    leaks = _sweep(world["super"], targets)
    assert not leaks, f"Super Admin session reached customer APIs:\n{_fmt(leaks)}"


def test_platform_staff_cannot_use_super_admin_api(world):
    targets = _select(world["routes"], "/api/super-admin/")
    for caller in ("staff_viewer", "staff_manager"):
        leaks = _sweep(world[caller], targets)
        assert not leaks, f"{caller} reached Super Admin API:\n{_fmt(leaks)}"


# POSTs a platform viewer may make: none of them changes platform data.
VIEWER_POSTS = (
    "/api/admin/env/lock", "/api/admin/env/unlock",   # the Environment page guard
    "/api/admin/security/change-password",            # the viewer's own password
    "/api/admin/settings/import/preview",             # validates a file, saves nothing
    "/api/admin/ai/playground",                       # read-only test run (answers 501, BUG-003)
)


def test_platform_viewer_cannot_write_through_admin_api(world):
    targets = _select(world["routes"], "/api/admin/", "/api/comment-filters/",
                      methods={"POST", "PUT", "PATCH", "DELETE"},
                      exclude=VIEWER_POSTS)
    leaks = _sweep(world["staff_viewer"], targets)
    assert not leaks, f"platform viewer wrote through:\n{_fmt(leaks)}"


def test_cross_origin_write_is_rejected(world):
    r = world["owner"].post("/api/auth/password/change", json={},
                            headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["error"] == "csrf_blocked"
    r = world["owner"].delete(f"/api/search/{FAKE_ID}", headers={"Referer": "https://evil.example/x"})
    assert r.status_code == 403 and r.json()["error"] == "csrf_blocked"
