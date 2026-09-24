"""
Step 3.5 — consolidated security & correctness proof.

Runs only against the in-memory MongoDB from tests/conftest.py; Apify, the AI
provider and the payment provider are mocked (URL-search worker replaced by a
fake that writes a page/post/comment/lead, billing uses the signed mock
provider). Never touches a real database or external API.

Items (see the per-section headers):
  1. Org A admin cannot read/write ANY Org B resource on any tenant route
     family — generated from app.routes, not hand-picked.
  2. User 1 cannot access User 2's data inside the same organization.
  3. Org admins / non-super platform staff cannot reach Super Admin routes or
     escalate roles / permissions.
  4. Only a verified webhook + Super Admin confirmation activates a plan.
  5. Demo limits are enforced by the backend.
  6. Plan / pricing is a single source of truth.
  7. Audit actor + redaction; no secrets in any GET response.
  8. Full end-to-end journey.
  9. Feature flags, maintenance mode, suspension.
 10. Invitations: no plaintext passwords, single-use, expiring tokens.

Items already proven elsewhere are referenced, not duplicated:
tests/test_step1_security.py, test_search_isolation.py, test_org_admin.py,
test_user_portal.py, test_super_admin_portal2.py, test_public_website2.py,
test_step2_integration.py.
"""
import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from bson import ObjectId
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.auth.crypto import hash_password
from app.auth.service import COOKIE_NAME, build_session_value, create_tracked_session

PASSWORD = "Str0ngPass!"
MARK_B = "BRAVOLEAK"       # planted in every Org B string field
MARK_U2 = "ALPHATWOLEAK"   # planted in every string of Org A / User 2's records
SECRET_VALUES = {
    "APIFY_API_TOKEN": "apify_api_PLANTEDSECRETapify0001",
    "GEMINI_API_KEY": "AIzaPLANTEDSECRETgemini0000001",
    "OPENAI_API_KEY": "sk-PLANTEDSECRETopenai00000001",
}
WEBHOOK_SECRET = "whsec_PLANTEDSECRETwebhook01"
NOW = datetime.now(timezone.utc)
_PW_HASH = hash_password(PASSWORD)   # bcrypt once per module, not per seeded user


# ═══════════════════════════════════════════════════════════════════════════
# fixtures & helpers
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def env():
    from app.billing import provider as provider_mod
    provider_mod.reset_billing_provider()
    with patch("app.admin.settings.is_maintenance_enabled", return_value=False), \
            patch.dict(os.environ, {"BILLING_WEBHOOK_SECRET": WEBHOOK_SECRET,
                                    "STRIPE_SECRET_KEY": ""}):
        from app.main import app
        with TestClient(app) as client:
            from app.db.mongo import get_sync_db
            yield client, get_sync_db()
    provider_mod.reset_billing_provider()


def _org(db, name, status="active", **extra):
    extra.setdefault("admin_portal_enabled", status == "active")
    return str(db.organizations.insert_one({
        "name": name, "slug": re.sub(r"\W+", "-", name.lower()) + str(ObjectId())[-4:],
        "status": status, "settings": {}, "created_at": NOW, **extra}).inserted_id)


def _user(db, email, org_id=None, role="member", status="active", member_status="active",
          platform_role=None):
    uid = str(db.users.insert_one({
        "email": email, "name": email.split("@")[0], "password_hash": _PW_HASH,
        "status": status, "is_platform_admin": bool(platform_role), "platform_role": platform_role,
        "default_organization_id": org_id, "created_at": NOW}).inserted_id)
    if org_id:
        db.organization_members.insert_one({
            "organization_id": org_id, "user_id": uid, "role": role, "status": member_status,
            "permissions_override": {}, "joined_at": NOW, "created_at": NOW})
    return uid


def _cookie(claims):
    return {COOKIE_NAME: build_session_value(create_tracked_session(claims))}


def _site(uid, email, org_id, role):
    return _cookie({"user_id": uid, "email": email, "name": email, "scope": "site",
                    "organization_id": org_id, "org_role": role})


def _staff(db, role):
    email = f"{role}@platform.test"
    found = db.users.find_one({"email": email})
    uid = str(found["_id"]) if found else _user(db, email, platform_role=role)
    return _cookie({"user_id": uid, "email": email, "name": role, "scope": "admin", "role": role})


def _super(db):
    return _staff(db, "super_admin")


def _seed_tree(db, org_id, uid, email, tag):
    """search run -> page -> post -> comment -> lead (+ notes / follow-up),
    export, token ledger, ticket, notification, audit row — every string
    field carries ``tag`` so a leak is detectable in any response."""
    common = {"organization_id": org_id, "user_id": uid, "created_by": email}
    run_id = f"URL{tag}{ObjectId()}"
    db.search_history.insert_one({**common, "run_id": run_id, "status": "completed",
                                  "query": f"https://facebook.com/{tag}", "url_search": True,
                                  "intent": {"platform": "facebook", "keyword": tag},
                                  "pages_found": 1, "created_at": NOW})
    page = str(db.facebook_pages.insert_one({
        **common, "page_name": f"Page {tag}", "facebook_url": f"https://facebook.com/{tag}",
        "platform": "facebook", "search_run_id": run_id, "created_at": NOW}).inserted_id)
    post = str(db.facebook_posts.insert_one({
        **common, "page_ref": page, "platform": "facebook", "caption": f"caption {tag}",
        "post_url": f"https://facebook.com/{tag}/posts/1", "search_run_id": run_id,
        "created_at": NOW}).inserted_id)
    comment = str(db.facebook_comments.insert_one({
        **common, "post_ref": post, "text": f"interested {tag}", "author_name": f"Author {tag}",
        "platform": "facebook", "search_run_id": run_id, "created_at": NOW}).inserted_id)
    lead = str(db.ai_comments.insert_one({
        **common, "comment_ref": comment, "post_ref": post, "page_ref": page, "is_lead": True,
        "lead_score": 80, "lead_status": "new", "platform": "facebook",
        "commenter_name": f"Prospect {tag}", "comment_text": f"buy {tag}",
        "search_run_id": run_id, "analyzed_at": NOW, "lead_created_at": NOW,
        "notes": [{"text": f"note {tag}", "author": email, "created_at": NOW}],
        "follow_ups": [{"title": f"call {tag}", "status": "pending", "created_at": NOW}]}).inserted_id)
    export = str(db.exports.insert_one({**common, "scope": "comments", "format": "csv",
                                        "status": "completed", "filename": f"{tag}.csv",
                                        "created_at": NOW}).inserted_id)
    db.token_ledger.insert_one({"organization_id": org_id, "user_id": uid, "type": "consume",
                                "amount": 10, "reason": f"search {tag}", "reference": run_id,
                                "created_at": NOW})
    ticket = str(db.support_tickets.insert_one({
        "organization_id": org_id, "organization_name": tag, "number": 5000 + len(tag),
        "subject": f"ticket {tag}", "category": "bug", "priority": "high", "status": "open",
        "created_by": email, "messages": [{"author_email": email, "author_name": tag,
                                           "from_staff": False, "body": f"body {tag}",
                                           "at": NOW}],
        "created_at": NOW, "updated_at": NOW}).inserted_id)
    notification = str(db.notifications.insert_one({
        "audience": "user", "organization_id": org_id, "user_id": uid, "type": "lead_assigned",
        "title": f"note {tag}", "message": tag, "severity": "info", "link": None, "data": {},
        "read_by": [], "created_at": NOW}).inserted_id)
    db.notifications.insert_one({
        "audience": "org_admin", "organization_id": org_id, "user_id": None, "type": "user_suspended",
        "title": f"org note {tag}", "message": tag, "severity": "info", "link": None, "data": {},
        "read_by": [], "created_at": NOW})
    db.audit_logs.insert_one({"action": f"lead.updated", "category": "leads", "at": NOW,
                              "organization_id": org_id, "actor_user_id": uid,
                              "actor_email": email, "resource_id": lead,
                              "details": {"note": tag}, "success": True})
    return {"run": run_id, "page": page, "post": post, "comment": comment, "lead": lead,
            "export": export, "ticket": ticket, "notification": notification}


def _seed_billing(db, org_id, tag, status="pending_payment", plan="pro"):
    sub = db.subscriptions.insert_one({
        "organization_id": org_id, "plan_id": plan, "status": status,
        "current_period_start": NOW, "current_period_end": NOW + timedelta(days=30),
        "provider": "mock", "checkout_session_id": f"cs_{tag}", "amount": 99.0,
        "currency": "USD", "billing_cycle": "monthly", "created_at": NOW, "updated_at": NOW})
    db.payments.insert_one({"organization_id": org_id, "subscription_id": str(sub.inserted_id),
                            "status": "succeeded", "amount": 99.0, "provider": "mock",
                            "provider_payment_id": f"pi_{tag}", "created_at": NOW})
    db.token_balances.insert_one({"organization_id": org_id, "allocated": 1000, "used": 10,
                                  "remaining": 990, "source": "plan", "created_at": NOW})
    return str(sub.inserted_id), f"cs_{tag}"


def _seed_invitation(db, org_id, email, tag):
    raw = f"rawinvite{tag}{ObjectId()}"
    inv = db.organization_invitations.insert_one({
        "organization_id": org_id, "email": email, "role": "member",
        "token_hash": hashlib.sha256(raw.encode()).hexdigest(), "status": "pending",
        "invited_by": tag, "expires_at": NOW + timedelta(days=5), "created_at": NOW})
    return str(inv.inserted_id), raw


def _session_id_for(db, uid):
    return db.user_sessions.find_one({"user_id": uid, "revoked_at": None})["session_id"]


@pytest.fixture
def world(env):
    client, db = env
    org_a, org_b = _org(db, "Org Alpha"), _org(db, "Org " + MARK_B)
    u = {
        "a_owner": _user(db, "owner@a.test", org_a, "owner"),
        "a_admin": _user(db, "admin@a.test", org_a, "admin"),
        "a_user1": _user(db, "user1@a.test", org_a, "member"),
        "a_user2": _user(db, "user2@a.test", org_a, "member"),
        "b_owner": _user(db, "owner@b.test", org_b, "owner"),
        "b_user": _user(db, "user@b.test", org_b, "member"),
    }
    email = {"a_owner": "owner@a.test", "a_admin": "admin@a.test", "a_user1": "user1@a.test",
             "a_user2": "user2@a.test", "b_owner": "owner@b.test", "b_user": "user@b.test"}
    org = {"a": org_a, "b": org_b}

    def cookie(key):
        role = db.organization_members.find_one({"user_id": u[key]})["role"]
        return _site(u[key], email[key], org[key[0]], role)

    cookies = {k: cookie(k) for k in u}   # creates one tracked session per user
    data = {
        "a1": _seed_tree(db, org_a, u["a_user1"], email["a_user1"], "alphaone"),
        "a2": _seed_tree(db, org_a, u["a_user2"], email["a_user2"], MARK_U2),
        "b": _seed_tree(db, org_b, u["b_user"], email["b_user"], MARK_B),
    }
    data["a_sub"], data["a_cs"] = _seed_billing(db, org_a, "alphabill", status="active",
                                                plan="business")
    data["b_sub"], data["b_cs"] = _seed_billing(db, org_b, MARK_B)
    data["a_inv"], data["a_inv_token"] = _seed_invitation(db, org_a, "pending@a.test", "alphainv")
    data["b_inv"], data["b_inv_token"] = _seed_invitation(db, org_b, f"{MARK_B.lower()}@b.test",
                                                          MARK_B)
    return {"client": client, "db": db, "u": u, "email": email, "org": org,
            "cookies": cookies, "data": data}


def _all_routes():
    from app.main import app

    def walk(routes):
        for r in routes:
            if isinstance(r, APIRoute):
                yield r
            elif hasattr(r, "original_router"):
                yield from walk(r.original_router.routes)
    out = []
    for r in walk(app.routes):
        for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
            out.append((m, r.path))
    return out


# Route families a tenant (site-scope) session can reach.
_NON_TENANT = ("/api/admin/", "/api/super-admin/", "/api/comment-filters/", "/api/public/")


def _tenant_routes():
    return [(m, p) for m, p in _all_routes()
            if p.startswith("/api/") and not p.startswith(_NON_TENANT)]


# Token-bearer routes: the path segment is an unguessable secret (the invite
# link), not a resource id — covered by item 10 instead of id tampering.
_TOKEN_ROUTES = {"/api/invitations/{token}", "/api/invitations/{token}/accept",
                 "/api/invitations/{token}/register"}
# Path params that are enumerations, not ids — covered by the list sweeps.
_KIND_ROUTES = {"/api/org-admin/data/{kind}", "/api/org-admin/exports/{kind}.csv"}

_BODIES = {
    ("PATCH", "/api/leads/{lead_id}"): {"lead_status": "contacted"},
    ("POST", "/api/leads/{lead_id}/notes"): {"text": "tamper"},
    ("POST", "/api/leads/{lead_id}/follow-ups"): {"title": "tamper"},
    ("PATCH", "/api/leads/{lead_id}/follow-ups/{fu_index}"): {"status": "completed"},
    ("PATCH", "/api/organizations/current/members/{member_user_id}"): {"status": "suspended"},
    ("POST", "/api/billing/checkout/{session_id}/mock-pay"): {"succeed": True},
    ("POST", "/api/org-admin/users/{user_id}/reset-access"): {"revoke_sessions": True},
    ("POST", "/api/org-admin/support/tickets/{ticket_id}/messages"): {"message": "tamper message"},
    ("POST", "/api/org-admin/support/tickets/{ticket_id}/status"): {"status": "closed"},
}


def _param_values(w, owner_key, rec_key, sub_key, cs_key, inv_key):
    d = w["data"]
    rec = d[rec_key]
    return {
        "run_id": rec["run"], "page_id": rec["page"], "post_id": rec["post"],
        "comment_id": rec["comment"], "lead_id": rec["lead"], "ticket_id": rec["ticket"],
        "note_index": "0", "fu_index": "0",
        "user_id": w["u"][owner_key], "member_user_id": w["u"][owner_key],
        "short_id": _session_id_for(w["db"], w["u"][owner_key])[:12],
        "session_id": d[cs_key], "invitation_id": d[inv_key], "scope": "posts",
    }


def _fill(path, values):
    return re.sub(r"\{(\w+)\}", lambda m: values[m.group(1)], path)


def _id_routes():
    return [(m, p) for m, p in _tenant_routes()
            if "{" in p and p not in _TOKEN_ROUTES and p not in _KIND_ROUTES]


def _call(client, method, url, cookies, body=None, params=None):
    client.cookies.clear()
    kw = {"cookies": cookies, "params": params}
    if method in ("POST", "PUT", "PATCH"):
        kw["json"] = body if body is not None else {}
    return client.request(method, url, **kw)


_TENANT_COLLECTIONS = ("search_history", "facebook_pages", "facebook_posts", "facebook_comments",
                       "ai_comments", "exports", "token_ledger", "token_balances", "subscriptions",
                       "payments", "support_tickets", "notifications", "organization_members",
                       "organization_invitations")


def _snapshot(db, org_id, user_id=None):
    snap = {}
    for coll in _TENANT_COLLECTIONS:
        q = {"organization_id": org_id}
        if user_id:
            q["user_id"] = user_id
        snap[coll] = sorted((repr(sorted(d.items())) for d in db[coll].find(q)))
    sess_q = {"user_id": user_id} if user_id else {"organization_id": org_id}
    snap["sessions"] = sorted(repr((d["session_id"], d.get("revoked_at")))
                              for d in db.user_sessions.find(sess_q))
    return snap


# ═══════════════════════════════════════════════════════════════════════════
# 1. Admin of Org A cannot read or write ANY Org B resource
# ═══════════════════════════════════════════════════════════════════════════

class TestItem1CrossTenant:
    def test_every_id_route_is_covered(self, world):
        """Guard: every tenant route with a path id has a tamper value, so a new
        route cannot silently escape the sweep below."""
        values = _param_values(world, "b_user", "b", "b_sub", "b_cs", "b_inv")
        missing = [p for _, p in _id_routes()
                   if any(n not in values for n in re.findall(r"\{(\w+)\}", p))]
        assert not missing, missing
        assert len(_id_routes()) >= 30

    @pytest.mark.parametrize("actor", ["a_owner", "a_admin"])
    def test_org_b_ids_rejected_on_every_route(self, world, actor):
        client, db = world["client"], world["db"]
        values = _param_values(world, "b_user", "b", "b_sub", "b_cs", "b_inv")
        before = _snapshot(db, world["org"]["b"])
        failures, unlogged = [], []
        for method, path in _id_routes():
            n0 = db.security_events.count_documents({"type": "cross_tenant_access"})
            r = _call(client, method, _fill(path, values), world["cookies"][actor],
                      _BODIES.get((method, path)),
                      params={"page_id": values["page_id"]} if "{scope}" in path else None)
            if r.status_code not in (403, 404) or MARK_B in r.text or "@b.test" in r.text:
                failures.append((method, path, r.status_code, r.text[:200]))
            elif not db.security_events.count_documents({"type": "cross_tenant_access"}) > n0:
                unlogged.append((method, path))
        assert not failures, failures
        assert not unlogged, f"cross-tenant attempts not logged: {unlogged}"
        assert _snapshot(db, world["org"]["b"]) == before
        ev = db.security_events.find_one({"type": "cross_tenant_access",
                                          "actor_email": world["email"][actor]})
        assert ev and ev["target_organization_id"] == world["org"]["b"]

    def test_positive_control_own_ids_work(self, world):
        """The same GET routes answer 200 for Org A's own ids, so the 404s above
        are tenant isolation, not broken routes."""
        client = world["client"]
        values = _param_values(world, "a_user1", "a1", "a_sub", "a_cs", "a_inv")
        bad = []
        for method, path in _id_routes():
            if method != "GET":
                continue
            r = _call(client, method, _fill(path, values), world["cookies"]["a_owner"],
                      params={"page_id": values["page_id"]} if "{scope}" in path else None)
            if r.status_code != 200:
                bad.append((path, r.status_code, r.text[:150]))
        assert not bad, bad

    @pytest.mark.parametrize("actor", ["a_owner", "a_admin"])
    def test_query_param_tampering_never_returns_org_b(self, world, actor):
        """Every tenant GET list / kind route, fed Org B ids through every
        filter parameter name in use, returns no Org B data."""
        client, db = world["client"], world["db"]
        b, org_b = world["data"]["b"], world["org"]["b"]
        params = {"organization_id": org_b, "org_id": org_b, "tenant_id": org_b,
                  "user_id": world["u"]["b_user"], "owner": world["u"]["b_user"],
                  "assignee": world["u"]["b_user"], "run_id": b["run"], "page_id": b["page"],
                  "post_id": b["post"], "limit": 100}
        urls = [p for m, p in _tenant_routes() if m == "GET" and "{" not in p]
        urls += [f"/api/org-admin/data/{k}" for k in ("pages", "posts", "comments")]
        from app.api.routes.org_admin import EXPORT_KINDS
        urls += [f"/api/org-admin/exports/{k}.csv" for k in EXPORT_KINDS]
        leaks = []
        for url in urls:
            r = _call(client, "GET", url, world["cookies"][actor], params=params)
            if MARK_B in r.text or "@b.test" in r.text:
                leaks.append((url, r.status_code))
        assert not leaks, leaks
        assert len(urls) >= 40

    def test_forged_org_claims_are_ignored_everywhere(self, world):
        """A session whose claims name Org B still resolves to the user's real
        membership on every list route (the org never comes from the request)."""
        client = world["client"]
        forged = _site(world["u"]["a_owner"], "owner@a.test", world["org"]["b"], "owner")
        for url in [p for m, p in _tenant_routes() if m == "GET" and "{" not in p]:
            r = _call(client, "GET", url, forged)
            assert MARK_B not in r.text, url


# ═══════════════════════════════════════════════════════════════════════════
# 2. User 1 cannot access User 2's data in the same organization
# ═══════════════════════════════════════════════════════════════════════════

class TestItem2SameOrgUsers:
    def test_no_users_by_id_route_exists(self, world):
        """The spec's /api/users/USER_2/... is not a route: user-addressed data
        lives under /api/org-admin/users/{id} (admin only) and the owner-scoped
        data routes. The literal path answers 404."""
        paths = {p for _, p in _all_routes()}
        assert not [p for p in paths if p.startswith("/api/users")]
        u2 = world["u"]["a_user2"]
        r = _call(world["client"], "GET", f"/api/users/{u2}", world["cookies"]["a_user1"])
        assert r.status_code == 404

    def test_user1_rejected_on_every_id_route_for_user2(self, world):
        client, db = world["client"], world["db"]
        values = _param_values(world, "a_user2", "a2", "a_sub", "a_cs", "a_inv")
        before = _snapshot(db, world["org"]["a"], world["u"]["a_user2"])
        failures, unlogged = [], []
        for method, path in _id_routes():
            n0 = db.security_events.count_documents(
                {"type": {"$in": ["cross_user_access", "permission_denied"]}})
            r = _call(client, method, _fill(path, values), world["cookies"]["a_user1"],
                      _BODIES.get((method, path)),
                      params={"page_id": values["page_id"]} if "{scope}" in path else None)
            if r.status_code not in (403, 404) or MARK_U2 in r.text:
                failures.append((method, path, r.status_code, r.text[:200]))
            elif r.status_code == 404 and not db.security_events.count_documents(
                    {"type": {"$in": ["cross_user_access", "permission_denied"]}}) > n0:
                unlogged.append((method, path))
        assert not failures, failures
        assert _snapshot(db, world["org"]["a"], world["u"]["a_user2"]) == before
        # user-owned records probed by id are logged as cross_user_access
        assert db.security_events.find_one({"type": "cross_user_access",
                                            "actor_email": "user1@a.test"})
        assert not unlogged, unlogged

    def test_user1_lists_never_contain_user2(self, world):
        client = world["client"]
        u2, a2 = world["u"]["a_user2"], world["data"]["a2"]
        params = {"user_id": u2, "owner": u2, "assignee": u2, "run_id": a2["run"],
                  "page_id": a2["page"], "post_id": a2["post"], "limit": 100}
        leaks = []
        for url in [p for m, p in _tenant_routes() if m == "GET" and "{" not in p]:
            r = _call(client, "GET", url, world["cookies"]["a_user1"], params=params)
            if MARK_U2 in r.text:
                leaks.append((url, r.status_code))
        assert not leaks, leaks

    def test_org_admin_does_see_user2(self, world):
        """Control: the org admin legitimately sees both members' data."""
        client = world["client"]
        r = _call(client, "GET", f"/api/org-admin/users/{world['u']['a_user2']}",
                  world["cookies"]["a_admin"])
        assert r.status_code == 200 and r.json()["user"]["email"] == "user2@a.test"
        r = _call(client, "GET", "/api/org-admin/searches", world["cookies"]["a_admin"])
        assert MARK_U2 in r.text


# ═══════════════════════════════════════════════════════════════════════════
# 3. No Super Admin access, no role / permission escalation
# ═══════════════════════════════════════════════════════════════════════════

_STAFF_ROLES = ["viewer", "support_admin", "technical_admin", "billing_admin", "operations_admin"]


_OPEN_SUPER_ROUTES = {"/api/super-admin/impersonate/exit"}


def _super_routes():
    return [(m, p) for m, p in _all_routes()
            if p.startswith("/api/super-admin/") and p not in _OPEN_SUPER_ROUTES]


class TestItem3NoSuperAdmin:
    def test_org_roles_get_401_on_every_super_admin_route(self, world):
        client = world["client"]
        routes = _super_routes()
        assert len(routes) >= 70
        for key in ("a_owner", "a_admin", "a_user1"):
            for method, path in routes:
                url = re.sub(r"\{\w+\}", str(ObjectId()), path)
                r = _call(client, method, url, world["cookies"][key], {})
                assert r.status_code == 401, (key, method, path, r.status_code)
            # the one open route only ends an impersonation — nothing to end here
            r = _call(client, "POST", "/api/super-admin/impersonate/exit", world["cookies"][key])
            assert r.status_code in (400, 401, 403) and COOKIE_NAME not in r.cookies

    @pytest.mark.parametrize("role", _STAFF_ROLES)
    def test_non_super_staff_get_403_on_every_super_admin_route(self, world, role):
        client, db = world["client"], world["db"]
        cookie = _staff(db, role)
        for method, path in _super_routes():
            url = re.sub(r"\{\w+\}", str(ObjectId()), path)
            r = _call(client, method, url, cookie, {})
            assert r.status_code == 403, (role, method, path, r.status_code)

    def test_forged_admin_scope_cookie_for_tenant_owner(self, world):
        client = world["client"]
        forged = _cookie({"user_id": world["u"]["a_owner"], "email": "owner@a.test",
                          "scope": "admin", "role": "super_admin"})
        for method, path in _super_routes():
            url = re.sub(r"\{\w+\}", str(ObjectId()), path)
            assert _call(client, method, url, forged, {}).status_code == 403, path

    def test_superadmin_page_redirects_non_super(self, world):
        client, db = world["client"], world["db"]
        client.cookies.clear()
        r = client.get("/superadmin", cookies=world["cookies"]["a_owner"], follow_redirects=False)
        assert r.status_code == 303 and "/login" in r.headers["location"]
        r = client.get("/superadmin", cookies=_staff(db, "operations_admin"), follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/admin"
        r = client.get("/superadmin", cookies=_super(db), follow_redirects=False)
        assert r.status_code == 200

    @pytest.mark.parametrize("actor", ["a_owner", "a_admin"])
    @pytest.mark.parametrize("role", ["owner", "super_admin", "platform_admin", "root"])
    def test_cannot_assign_privileged_roles(self, world, actor, role):
        client, db = world["client"], world["db"]
        for target in ("a_user1", actor):
            r = _call(client, "PATCH",
                      f"/api/organizations/current/members/{world['u'][target]}",
                      world["cookies"][actor], {"role": role})
            assert r.status_code == 403, (target, r.text)
        r = _call(client, "POST", "/api/organizations/current/invitations",
                  world["cookies"][actor], {"email": "x@example.com", "role": role})
        assert r.status_code == 403
        assert db.organization_members.find_one({"user_id": world["u"]["a_user1"]})["role"] == "member"
        assert db.organization_members.find_one({"user_id": world["u"][actor]})["role"] == \
            actor.split("_")[1]
        assert not db.users.find_one({"platform_role": "super_admin",
                                      "email": {"$in": list(world["email"].values())}})

    def test_admin_cannot_change_own_role_or_touch_owner(self, world):
        client = world["client"]
        c = world["cookies"]["a_admin"]
        assert _call(client, "PATCH", f"/api/organizations/current/members/{world['u']['a_admin']}",
                     c, {"role": "member"}).status_code == 403
        assert _call(client, "PATCH", f"/api/organizations/current/members/{world['u']['a_owner']}",
                     c, {"status": "suspended"}).status_code == 403

    @pytest.mark.parametrize("actor", ["a_owner", "a_admin"])
    @pytest.mark.parametrize("perm", ["roles.manage", "billing.manage", "workspace.manage",
                                      "platform.manage", "impersonate.user", "orgs.manage"])
    def test_non_delegable_permissions_cannot_be_granted(self, world, actor, perm):
        client, db = world["client"], world["db"]
        r = _call(client, "PATCH", f"/api/organizations/current/members/{world['u']['a_user1']}",
                  world["cookies"][actor], {"permissions_override": {perm: True}})
        assert r.status_code == 422, r.text
        r = _call(client, "PATCH", "/api/organizations/current", world["cookies"][actor],
                  {"role_permissions": {"member": {perm: True}}})
        assert r.status_code == 422, r.text
        m = db.organization_members.find_one({"user_id": world["u"]["a_user1"]})
        assert perm not in (m.get("permissions_override") or {})

    @pytest.mark.parametrize("role", ["owner", "admin", "super_admin"])
    def test_privileged_role_matrix_is_not_configurable(self, world, role):
        r = _call(world["client"], "PATCH", "/api/organizations/current", world["cookies"]["a_owner"],
                  {"role_permissions": {role: {"leads.view": True}}})
        assert r.status_code == 422
        org = world["db"].organizations.find_one({"_id": ObjectId(world["org"]["a"])})
        assert role not in ((org.get("settings") or {}).get("role_permissions") or {})

    def test_members_cannot_edit_role_permissions(self, world):
        r = _call(world["client"], "PATCH", "/api/organizations/current", world["cookies"]["a_user1"],
                  {"role_permissions": {"member": {"leads.assign": True}}})
        assert r.status_code == 403

    def test_platform_role_matrix_only_super_admin(self, world):
        client, db = world["client"], world["db"]
        body = {"kind": "org", "role": "member", "permissions": ["leads.view", "members.manage"]}
        assert _call(client, "PUT", "/api/super-admin/role-permissions",
                     world["cookies"]["a_owner"], body).status_code == 401
        assert _call(client, "PUT", "/api/super-admin/role-permissions",
                     _staff(db, "operations_admin"), body).status_code == 403
        assert db.role_permissions.count_documents({}) == 0

    @pytest.mark.parametrize("role", _STAFF_ROLES)
    def test_staff_cannot_create_or_promote_platform_users(self, world, role):
        client, db = world["client"], world["db"]
        cookie = _staff(db, role)
        r = _call(client, "POST", "/api/admin/users", cookie,
                  {"email": "evil@platform-example.com", "password": PASSWORD, "role": "super_admin"})
        assert r.status_code == 403
        target = str(db.admin_users.insert_one({"email": "t@platform.test", "role": "viewer"}).inserted_id)
        r = _call(client, "PATCH", f"/api/admin/users/{target}", cookie, {"role": "super_admin"})
        assert r.status_code == 403
        assert db.admin_users.find_one({"_id": ObjectId(target)})["role"] == "viewer"
        assert not db.admin_users.find_one({"email": "evil@platform-example.com"})


# ═══════════════════════════════════════════════════════════════════════════
# 4. Only verified webhook + Super Admin confirmation activates a plan
#    (also: tests/test_step1_security.py::TestSubscriptionActivation)
# ═══════════════════════════════════════════════════════════════════════════

def _signed(event, secret=WEBHOOK_SECRET):
    body = json.dumps(event).encode()
    return body, hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _checkout(client, cookie, plan="pro"):
    r = client.post("/api/billing/checkout", json={"plan_slug": plan}, cookies=cookie)
    assert r.status_code == 200, r.text
    return r.json()["checkout"]


class TestItem4Activation:
    def _demo_org(self, world):
        db = world["db"]
        db.organizations.update_one({"_id": ObjectId(world["org"]["a"])},
                                    {"$set": {"status": "demo", "admin_portal_enabled": False}})
        db.subscriptions.delete_many({"organization_id": world["org"]["a"]})

    def test_client_side_calls_never_activate(self, world):
        self._demo_org(world)
        client, db, c = world["client"], world["db"], world["cookies"]["a_owner"]
        client.cookies.clear()
        co = _checkout(client, c)
        sid = ObjectId(co["subscription_id"])
        # the payment-success page and its API are read-only
        client.cookies.clear()
        assert client.get(f"/billing/status?session={co['session_id']}", cookies=c).status_code == 200
        assert client.get(f"/billing/checkout/{co['session_id']}", cookies=c).status_code == 200
        assert client.get(f"/api/billing/checkout/{co['session_id']}", cookies=c).json()[
            "checkout"]["status"] == "pending_payment"
        # every other customer billing write
        for path, body in (("/api/billing/reactivate", {}), ("/api/billing/portal", {}),
                           ("/api/billing/checkout", {"plan_slug": "pro"})):
            _call(client, "POST", path, c, body)
        assert db.subscriptions.find_one({"_id": sid})["status"] in ("pending_payment", "cancelled")
        assert db.subscriptions.count_documents({"organization_id": world["org"]["a"],
                                                 "status": "active"}) == 0
        assert db.organizations.find_one({"_id": ObjectId(world["org"]["a"])})["status"] == "demo"

    def test_bad_signatures_rejected_and_logged(self, world):
        self._demo_org(world)
        client, db = world["client"], world["db"]
        co = _checkout(client, world["cookies"]["a_owner"])
        event = {"id": "evt_bad", "type": "checkout.session.completed", "data": {"object": {
            "metadata": {"subscription_id": co["subscription_id"]}, "payment_status": "paid",
            "amount_total": int(co["amount"] * 100)}}}
        body, _ = _signed(event)
        for headers in ({}, {"X-LeadAI-Signature": "0" * 64},
                        {"X-LeadAI-Signature": _signed(event, "whsec_wrong")[1]},
                        {"X-LeadAI-Signature": _signed({**event, "id": "other"})[1]}):
            client.cookies.clear()
            r = client.post("/api/billing/webhook", content=body,
                            headers={"Content-Type": "application/json", **headers})
            assert r.status_code == 400
        assert db.security_events.count_documents({"type": "invalid_webhook_signature"}) == 4
        assert db.subscriptions.find_one({"_id": ObjectId(co["subscription_id"])})["status"] == \
            "pending_payment"

    def test_stripe_provider_rejects_forged_and_stale_signatures(self):
        import time as _t
        from app.billing.provider import StripeBillingProvider, WebhookSignatureError
        p = StripeBillingProvider("sk_test_x", "whsec_stripe")
        body = b'{"id":"evt_s","type":"checkout.session.completed"}'
        ts = str(int(_t.time()))
        good = hmac.new(b"whsec_stripe", f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
        assert p.verify_webhook(body, {"stripe-signature": f"t={ts},v1={good}"})["id"] == "evt_s"
        for header in ("", f"t={ts},v1={'0' * 64}", f"t={int(ts) - 3600},v1={good}"):
            with pytest.raises(WebhookSignatureError):
                p.verify_webhook(body, {"stripe-signature": header})

    def test_verified_webhook_then_super_admin_confirmation(self, world):
        self._demo_org(world)
        client, db = world["client"], world["db"]
        co = _checkout(client, world["cookies"]["a_owner"])
        body, sig = _signed({"id": "evt_ok", "type": "checkout.session.completed", "data": {
            "object": {"metadata": {"subscription_id": co["subscription_id"]},
                       "payment_status": "paid", "amount_total": int(round(co["amount"] * 100))}}})
        client.cookies.clear()
        r = client.post("/api/billing/webhook", content=body, headers={"X-LeadAI-Signature": sig})
        assert r.status_code == 200, r.text
        sid = co["subscription_id"]
        assert db.subscriptions.find_one({"_id": ObjectId(sid)})["status"] == \
            "pending_admin_confirmation"
        assert db.organizations.find_one({"_id": ObjectId(world["org"]["a"])})["status"] == "demo"
        # nobody but a Super Admin can confirm
        assert _call(client, "POST", f"/api/super-admin/subscriptions/{sid}/confirm",
                     world["cookies"]["a_owner"]).status_code == 401
        assert _call(client, "POST", f"/api/super-admin/subscriptions/{sid}/confirm",
                     _staff(db, "billing_admin")).status_code == 403
        r = _call(client, "POST", f"/api/super-admin/subscriptions/{sid}/confirm", _super(db))
        assert r.status_code == 200, r.text
        assert db.subscriptions.find_one({"_id": ObjectId(sid)})["status"] == "active"
        org = db.organizations.find_one({"_id": ObjectId(world["org"]["a"])})
        assert org["status"] == "active" and org["admin_portal_enabled"] is True

    def test_super_admin_cannot_skip_payment(self, world):
        """Confirmation needs a verified payment, and the generic status
        endpoint cannot jump a pending_payment subscription to active."""
        self._demo_org(world)
        client, db = world["client"], world["db"]
        co = _checkout(client, world["cookies"]["a_owner"])
        sid, sa = co["subscription_id"], _super(db)
        assert _call(client, "POST", f"/api/super-admin/subscriptions/{sid}/confirm", sa).status_code == 409
        r = _call(client, "POST", f"/api/super-admin/subscriptions/{sid}/status", sa,
                  {"status": "active", "reason": "skip"})
        assert r.status_code in (400, 409, 422), r.text
        assert db.subscriptions.find_one({"_id": ObjectId(sid)})["status"] == "pending_payment"
        assert db.organizations.find_one({"_id": ObjectId(world["org"]["a"])})["status"] == "demo"


# ═══════════════════════════════════════════════════════════════════════════
# 5. Demo limits are enforced by the backend
#    (also: test_user_portal.py test_url_search_* and test_step1_security.py
#     TestDemoLifecycle / TestTokenLedger)
# ═══════════════════════════════════════════════════════════════════════════

class FakeUrlSearch:
    """Stands in for the Apify-backed worker: records its caps and writes a
    completed run with one page / post / comment / lead."""
    calls = []

    def __init__(self, run_id, url, max_posts, max_comments, organization_id=None,
                 created_by=None, user_id=None):
        from app.db.mongo import get_sync_db
        FakeUrlSearch.calls.append({"run_id": run_id, "max_posts": max_posts,
                                    "max_comments": max_comments, "org": organization_id})
        db = get_sync_db()
        own = {"organization_id": organization_id, "user_id": user_id, "created_by": created_by}
        page = str(db.facebook_pages.insert_one({**own, "page_name": f"Fake page {run_id}",
                                                "facebook_url": url, "platform": "facebook",
                                                "search_run_id": run_id}).inserted_id)
        post = str(db.facebook_posts.insert_one({**own, "page_ref": page, "platform": "facebook",
                                                "search_run_id": run_id}).inserted_id)
        comment = str(db.facebook_comments.insert_one({**own, "post_ref": post,
                                                      "text": "price please", "platform": "facebook",
                                                      "search_run_id": run_id}).inserted_id)
        db.ai_comments.insert_one({**own, "comment_ref": comment, "post_ref": post, "page_ref": page,
                                   "is_lead": True, "lead_score": 90, "lead_status": "new",
                                   "platform": "facebook", "search_run_id": run_id,
                                   "analyzed_at": datetime.utcnow()})
        db.search_history.update_one({"run_id": run_id}, {"$set": {
            "status": "completed", "pages_found": 1, "completed_at": datetime.utcnow()}})

    def run(self):
        return None


def _search(client, cookie, **params):
    client.cookies.clear()
    params.setdefault("url", "https://www.facebook.com/somepage")
    with patch("app.social.url_search.UrlSearchThread", FakeUrlSearch), \
            patch("app.api.routes.search._check_api_rate", return_value=True):
        return client.post("/api/url/search", params=params, cookies=cookie)


def _approved_demo(client, db, email="demo@corp.test", overrides=None):
    client.cookies.clear()
    r = client.post("/api/auth/signup", json={"name": "Dee Demo", "email": email,
                                              "company": "Demo Corp", "password": PASSWORD})
    assert r.status_code == 200, r.text
    req = db.demo_requests.find_one({"email": email})
    r = _call(client, "POST", f"/api/super-admin/demo-requests/{req['_id']}/approve", _super(db),
              {"overrides": overrides} if overrides else {})
    assert r.status_code == 200, r.text
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    cookie = {COOKIE_NAME: r.cookies[COOKIE_NAME]}
    client.cookies.clear()
    return cookie, req["organization_id"]


class TestItem5DemoLimits:
    def test_limits_come_from_super_admin_config_and_are_enforced(self, env):
        client, db = env
        FakeUrlSearch.calls.clear()
        cookie, org = _approved_demo(client, db, overrides={
            "max_searches": 2, "posts_per_search": 5, "comments_per_post": 7, "tokens": 25})
        # explicit requests above the per-search caps are refused
        r = _search(client, cookie, max_posts=6)
        assert r.status_code == 402 and r.json()["detail"]["metric"] == "posts_per_search"
        r = _search(client, cookie, max_comments_per_post=8)
        assert r.status_code == 402 and r.json()["detail"]["metric"] == "comments_per_post"
        # an unspecified request is clamped to the caps server-side
        r = _search(client, cookie)
        assert r.status_code == 200, r.text
        assert FakeUrlSearch.calls[-1]["max_posts"] <= 5
        assert FakeUrlSearch.calls[-1]["max_comments"] <= 7
        bal = db.token_balances.find_one({"organization_id": org})
        assert bal["used"] == 10 and bal["remaining"] == 15
        r = _search(client, cookie)
        assert r.status_code == 200
        # tokens: 5 left < search cost 10
        r = _search(client, cookie)
        assert r.status_code == 402 and r.json()["detail"]["code"] == "TOKENS_EXHAUSTED"
        assert len(FakeUrlSearch.calls) == 2

    def test_max_searches_enforced(self, env):
        client, db = env
        cookie, org = _approved_demo(client, db, overrides={"max_searches": 1, "tokens": 1000})
        assert _search(client, cookie).status_code == 200
        r = _search(client, cookie)
        assert r.status_code == 402, r.text
        # the refused search did not keep its tokens
        assert db.token_balances.find_one({"organization_id": org})["used"] == 10

    def test_expired_demo_blocks_search_collect_and_export(self, env):
        client, db = env
        cookie, org = _approved_demo(client, db, overrides={"exports_enabled": True})
        assert _search(client, cookie).status_code == 200
        page = db.facebook_pages.find_one({"organization_id": org})
        post = db.facebook_posts.find_one({"organization_id": org})
        past = datetime.now(timezone.utc) - timedelta(days=1)
        db.organizations.update_one({"_id": ObjectId(org)}, {"$set": {"demo.expires_at": past}})
        db.token_balances.update_one({"organization_id": org}, {"$set": {"expires_at": past}})
        r = _search(client, cookie)
        assert r.status_code == 402 and r.json()["detail"]["code"] == "DEMO_EXPIRED"
        with patch("app.api.routes.search._start", return_value=True):
            assert _call(client, "POST", f"/api/pages/{page['_id']}/posts", cookie).status_code == 402
            assert _call(client, "POST", f"/api/posts/{post['_id']}/comments", cookie).status_code == 402
        # reading existing results stays possible
        assert _call(client, "GET", "/api/leads", cookie).status_code == 200

    def test_demo_exports_disabled_by_config(self, env):
        client, db = env
        cookie, org = _approved_demo(client, db)
        assert _search(client, cookie).status_code == 200
        page = db.facebook_pages.find_one({"organization_id": org})
        r = _call(client, "GET", "/api/export/posts.csv", cookie, params={"page_id": str(page["_id"])})
        assert r.status_code in (402, 403), r.text

    def test_demo_platform_restriction(self, env):
        client, db = env
        cookie, _ = _approved_demo(client, db, overrides={"allowed_platforms": ["facebook"]})
        r = _search(client, cookie, url="https://www.instagram.com/somebrand/")
        assert r.status_code in (402, 403), r.text

    def test_demo_team_size_enforced(self, env):
        client, db = env
        cookie, org = _approved_demo(client, db, overrides={"max_users": 1})
        r = _call(client, "POST", "/api/organizations/current/invitations", cookie,
                  {"email": "second@example.com", "role": "member"})
        assert r.status_code == 402, r.text


# ═══════════════════════════════════════════════════════════════════════════
# 6. Plan / pricing: one source of truth
#    (also: test_public_website2.py::test_pricing_reflects_plan_catalog...)
# ═══════════════════════════════════════════════════════════════════════════

class TestItem6PricingSingleSource:
    def _views(self, world, slug):
        client, db = world["client"], world["db"]
        client.cookies.clear()
        public = {p["slug"]: p for p in client.get("/api/public/pricing").json()["plans"]}[slug]
        billing = {p["slug"]: p for p in client.get("/api/billing/plans").json()["plans"]}[slug]
        sa = _call(client, "GET", "/api/super-admin/plans", _super(db)).json()
        sa_plans = sa.get("plans") or sa.get("items")
        admin = {p["slug"]: p for p in sa_plans}[slug]
        usage = _call(client, "GET", "/api/billing/usage", world["cookies"]["a_owner"]).json()["usage"]
        sub = _call(client, "GET", "/api/billing/subscription",
                    world["cookies"]["a_owner"]).json()["subscription"]
        return public, billing, admin, usage, sub

    def test_all_views_agree_and_follow_a_change(self, world):
        client, db = world["client"], world["db"]
        org_a = world["org"]["a"]
        db.subscriptions.delete_many({"organization_id": org_a})
        db.subscriptions.insert_one({"organization_id": org_a, "plan_id": "pro", "status": "active",
                                     "billing_cycle": "monthly", "current_period_start": NOW,
                                     "current_period_end": NOW + timedelta(days=30)})
        db.organizations.update_one({"_id": ObjectId(org_a)}, {"$set": {"plan_id": "pro"}})
        public, billing, admin, usage, sub = self._views(world, "pro")
        for field in ("name", "price_monthly", "price_yearly", "currency"):
            assert public[field] == billing[field] == admin[field], field
        assert usage["plan"]["name"] == admin["name"]
        # change the plan through the Super Admin API
        r = _call(client, "PATCH", f"/api/super-admin/plans/{admin.get('id') or admin.get('_id')}",
                  _super(db), {"price_monthly": 123, "name": "Pro Plus",
                               "limits": {**(admin.get("limits") or {}), "monthly_searches": 77}})
        assert r.status_code == 200, r.text
        public, billing, admin, usage, sub = self._views(world, "pro")
        assert public["price_monthly"] == billing["price_monthly"] == admin["price_monthly"] == 123
        assert public["name"] == billing["name"] == admin["name"] == "Pro Plus"
        assert usage["plan"]["name"] == "Pro Plus"
        assert db.plans.count_documents({"slug": "pro"}) == 1
        # checkout charges the catalog price
        db.organizations.update_one({"_id": ObjectId(org_a)}, {"$set": {"status": "demo"}})
        db.subscriptions.delete_many({"organization_id": org_a})
        assert _checkout(client, world["cookies"]["a_owner"])["amount"] == 123

    def test_no_second_price_list_in_code_paths(self):
        """Every pricing endpoint reads the plans collection through
        app.billing.plans.get_all_plans (no hard-coded price tables)."""
        import inspect
        from app.api.routes import billing, public_website
        assert "get_all_plans" in inspect.getsource(billing.list_public_plans)
        assert "get_all_plans" in inspect.getsource(public_website.public_pricing)


# ═══════════════════════════════════════════════════════════════════════════
# 7. Audit actor, redaction, no secrets in any GET response
#    (also: test_step2_integration.py audit actor tests, test_step1 TestAuditAndSecrets,
#     test_super_admin_portal2.py TestExports)
# ═══════════════════════════════════════════════════════════════════════════

_FORBIDDEN_KEYS = {"password_hash", "token_hash", "password", "session_secret", "secret_key",
                   "webhook_secret", "stripe_secret_key", "raw_token"}
_KEY_RE = re.compile(r"(api_key|apikey|api_token|secret|access_token|refresh_token)$", re.I)


def _scan_json(obj, path="$"):
    """Yield (path, key) for keys that must never be in an API response with a
    non-empty, non-masked value."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            lk = str(k).lower()
            if isinstance(v, (str, int)) and v not in ("", None, 0) and not isinstance(v, bool):
                sv = str(v)
                masked = "•" in sv or set(sv) <= {"*", "•", "x", "X"}
                if (lk in _FORBIDDEN_KEYS or _KEY_RE.search(lk)) and not masked:
                    yield f"{path}.{k}", sv[:12]
            yield from _scan_json(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:200]):
            yield from _scan_json(v, f"{path}[{i}]")


def _plant_secrets(db):
    from app.admin import envvars
    for name, value in SECRET_VALUES.items():
        envvars.set_envvar_override(name, value, by="test")
    db.admin_users.insert_one({"email": "ops@platform.test", "role": "viewer", "enabled": True,
                               "password_hash": _PW_HASH})
    db.password_resets.insert_one({"user_id": "x", "token_hash": "f" * 64,
                                   "expires_at": NOW + timedelta(hours=1), "used_at": None})
    envvars._CACHE.clear()


# Aggregations mongomock cannot run ($convert); these work on real MongoDB and
# are excluded from the in-memory scan only.
_MONGOMOCK_UNSUPPORTED = {"/api/admin/apify/stats", "/api/admin/analytics/platforms",
                          "/api/admin/analytics/categories", "/api/admin/analytics/performance"}


def _admin_get_values(world):
    d, u = world["data"], world["u"]
    return {"org_id": world["org"]["a"], "user_id": u["a_user1"], "run_id": d["a1"]["run"],
            "lead_id": d["a1"]["lead"], "sub_id": d["a_sub"], "ticket_id": d["a1"]["ticket"],
            "kind": "users", "scope": "leads", "plan_id": "pro"}


def _leaks(text, db):
    found = [v for v in list(SECRET_VALUES.values()) + [WEBHOOK_SECRET] if v in text]
    if "$2b$" in text or "$2a$" in text:
        found.append("bcrypt hash")
    for s in db.user_sessions.find({}, {"session_id": 1}):
        if s["session_id"] in text:
            found.append("full session id")
            break
    for inv in db.organization_invitations.find({}, {"token_hash": 1}):
        if inv.get("token_hash") and inv["token_hash"] in text:
            found.append("invitation token hash")
    if "f" * 64 in text:
        found.append("reset token hash")
    return found


class TestItem7AuditAndSecrets:
    def test_leak_detector_is_not_vacuous(self):
        assert list(_scan_json({"user": {"password_hash": "abc"}}))
        assert list(_scan_json({"items": [{"gemini_api_key": "AIza123"}]}))
        assert list(_scan_json({"token_hash": "f00"}))
        assert not list(_scan_json({"api_key": "••••0001", "secret": True, "api_key_set": True}))

    def test_super_admin_get_responses_have_no_secrets(self, world):
        client, db = world["client"], world["db"]
        _plant_secrets(db)
        sa = _super(db)
        from app.admin import envvars
        envvars.s_set_apify_token(SECRET_VALUES["APIFY_API_TOKEN"], by="test")
        assert _call(client, "PUT", "/api/admin/settings", sa,
                     {"values": {"maintenance.message": "Back soon"}}).status_code == 200
        assert _call(client, "POST", "/api/admin/ai/prompts", sa,
                     {"name": "p", "template": "t"}).status_code == 200
        from app.pipeline.comment_filter import RULES_COLLECTION
        db[RULES_COLLECTION].insert_one({"name": "r", "keywords": ["price"], "active": False})
        db.ai_models.insert_one({"_id": "gemini-test", "name": "Gemini test"})
        from app.auth.service import ENV_UNLOCK_COOKIE
        envvars.set_guard_password("Gu4rdPassw0rd!", by="test")
        r = _call(client, "POST", "/api/admin/env/unlock", sa, {"password": "Gu4rdPassw0rd!"})
        assert r.status_code == 200, r.text
        sa = {**sa, ENV_UNLOCK_COOKIE: r.cookies[ENV_UNLOCK_COOKIE]}
        values = _admin_get_values(world)
        values.update({"version": "1", "location": "header", "model_id": "gemini-test",
                       "prompt_id": str(db.ai_prompts.find_one()["_id"]),
                       "rule_id": str(db[RULES_COLLECTION].find_one()["_id"])})
        client.cookies.clear()
        assert client.post("/api/auth/signup", json={"name": "Pending Pat", "email": "pat@example.com",
                                                     "company": "Pat Co", "password": PASSWORD,
                                                     "phone": "+1 555 0100"}).status_code == 200
        values["req_id"] = str(db.demo_requests.find_one({"email": "pat@example.com"})["_id"])
        cms_page = db.website_pages.find_one()
        if cms_page:
            values["page_id"] = str(cms_page["_id"])
        problems, skipped, mongomock_gaps, not_ok, called = [], [], [], [], 0
        for method, path in _all_routes():
            if method != "GET" or not path.startswith(("/api/super-admin/", "/api/admin/",
                                                       "/api/comment-filters/rules",
                                                       "/api/comment-filters/c",
                                                       "/api/comment-filters/stats"))                     or path == "/api/comment-filters/catalog":
                continue
            names = re.findall(r"\{(\w+)\}", path)
            if any(n not in values for n in names):
                skipped.append(path)
                continue
            try:
                r = _call(client, "GET", _fill(path, values), sa,
                          params={"q": "org"} if path == "/api/admin/search" else None)
            except NotImplementedError as e:
                assert "Mongomock" in str(e), (path, e)
                mongomock_gaps.append(path)
                continue
            called += 1
            if r.status_code != 200:
                not_ok.append((path, r.status_code))
            if r.status_code >= 500:
                problems.append((path, r.status_code))
                continue
            leaks = _leaks(r.text, db)
            if "json" in r.headers.get("content-type", ""):
                leaks += list(_scan_json(r.json()))
            if leaks:
                problems.append((path, leaks[:5]))
        assert not problems, problems
        assert called >= 110
        assert not skipped, skipped
        assert not not_ok, not_ok
        # aggregations mongomock cannot execute ($convert) — real MongoDB only
        assert set(mongomock_gaps) <= _MONGOMOCK_UNSUPPORTED, mongomock_gaps

    def test_tenant_get_responses_have_no_secrets(self, world):
        client, db = world["client"], world["db"]
        _plant_secrets(db)
        values = _param_values(world, "a_user1", "a1", "a_sub", "a_cs", "a_inv")
        problems = []
        for key in ("a_owner", "a_admin", "a_user1"):
            for method, path in _tenant_routes() + [("GET", "/api/comment-filters/catalog")]:
                if method != "GET" or path in _TOKEN_ROUTES or path in _KIND_ROUTES:
                    continue
                r = _call(client, "GET", _fill(path, values), world["cookies"][key])
                if r.status_code >= 500:
                    problems.append((key, path, r.status_code))
                    continue
                leaks = _leaks(r.text, db)
                if "json" in r.headers.get("content-type", ""):
                    leaks += list(_scan_json(r.json()))
                if leaks:
                    problems.append((key, path, leaks[:5]))
        assert not problems, problems

    def test_admin_user_write_responses_have_no_password_hash(self, world):
        client, db = world["client"], world["db"]
        sa = _super(db)
        r = _call(client, "POST", "/api/admin/users", sa,
                  {"email": "newstaff@example.com", "password": PASSWORD, "role": "viewer",
                   "name": "New"})
        assert r.status_code == 200, r.text
        assert "password_hash" not in r.text and "$2b$" not in r.text
        uid = r.json()["user"].get("_id") or r.json()["user"].get("id")
        r = _call(client, "PATCH", f"/api/admin/users/{uid}", sa, {"password": "N3wPassword!"})
        assert r.status_code == 200, r.text
        assert "password_hash" not in r.text and "$2b$" not in r.text
        assert db.admin_users.find_one({"email": "newstaff@example.com"})["password_hash"]

    def test_important_actions_are_audited_with_actor(self, world):
        client, db = world["client"], world["db"]
        c = world["cookies"]
        _call(client, "PATCH", f"/api/leads/{world['data']['a1']['lead']}", c["a_owner"],
              {"lead_status": "contacted"})
        _call(client, "POST", "/api/organizations/current/invitations", c["a_owner"],
              {"email": "audited@example.com", "role": "member"})
        _call(client, "PATCH", f"/api/organizations/current/members/{world['u']['a_user2']}",
              c["a_owner"], {"status": "suspended"})
        _call(client, "PATCH", "/api/organizations/current", c["a_owner"], {"name": "Org Alpha 2"})
        _call(client, "PUT", "/api/super-admin/feature-flags", _super(db),
              {"changes": {"features.exports.enabled": False}, "reason": "audit test"})
        for action in ("member.invited", "member.updated", "organization.updated",
                       "feature_flag.updated"):
            row = db.audit_logs.find_one({"action": action})
            assert row, action
            assert row.get("actor_email"), action
        assert db.audit_logs.find_one({"action": "member.invited"})["actor_email"] == "owner@a.test"
        assert db.audit_logs.find_one({"action": "feature_flag.updated"})["actor_email"] == \
            "super_admin@platform.test"

    def test_audit_details_are_redacted(self, env):
        client, db = env
        from app.admin.audit import audit
        audit("secret.test", "test", user="x@y.test",
              details={"api_key": "sk-live-SHOULDNOTAPPEAR", "nested": {"password": "hunter2X"},
                       "token": "tok-SHOULDNOTAPPEAR"})
        row = db.audit_logs.find_one({"action": "secret.test"})
        assert "SHOULDNOTAPPEAR" not in repr(row) and "hunter2X" not in repr(row)
        r = _call(client, "GET", "/api/super-admin/audit-logs", _super(db))
        assert r.status_code == 200 and "SHOULDNOTAPPEAR" not in r.text


# ═══════════════════════════════════════════════════════════════════════════
# 8. Full end-to-end journey
# ═══════════════════════════════════════════════════════════════════════════

def test_item8_full_journey(env):
    client, db = env
    FakeUrlSearch.calls.clear()
    # website pricing
    client.cookies.clear()
    plans = client.get("/api/public/pricing").json()["plans"]
    assert "pro" in {p["slug"] for p in plans}
    assert client.get("/pricing").status_code == 200
    # demo request / signup -> nothing usable yet
    r = client.post("/api/auth/signup", json={"name": "Jo Founder", "email": "jo@startup.test",
                                              "company": "Startup Inc", "password": PASSWORD})
    assert r.status_code == 200 and r.json()["status"] == "pending"
    assert client.post("/api/auth/login", json={"email": "jo@startup.test",
                                                "password": PASSWORD}).status_code == 403
    # Super Admin approval -> demo tokens
    sa = _super(db)
    req = db.demo_requests.find_one({"email": "jo@startup.test"})
    org = req["organization_id"]
    r = _call(client, "POST", f"/api/super-admin/demo-requests/{req['_id']}/approve", sa, {})
    assert r.status_code == 200, r.text
    assert db.token_balances.find_one({"organization_id": org})["remaining"] == 500
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"email": "jo@startup.test", "password": PASSWORD})
    assert r.status_code == 200
    owner = {COOKIE_NAME: r.cookies[COOKIE_NAME]}
    # demo user runs a (mocked) search
    r = _search(client, owner)
    assert r.status_code == 200, r.text
    demo_run = r.json()["run_id"]
    assert _call(client, "GET", f"/api/search/{demo_run}", owner).status_code == 200
    assert db.token_balances.find_one({"organization_id": org})["used"] == 10
    # the Admin portal is not open during the demo
    assert _call(client, "GET", "/api/org-admin/overview", owner).status_code == 403
    # chooses a plan -> checkout -> signed webhook
    co = _checkout(client, owner)
    body, sig = _signed({"id": "evt_journey", "type": "checkout.session.completed", "data": {
        "object": {"metadata": {"subscription_id": co["subscription_id"]},
                   "payment_status": "paid", "amount_total": int(round(co["amount"] * 100))}}})
    client.cookies.clear()
    assert client.post("/api/billing/webhook", content=body,
                       headers={"X-LeadAI-Signature": sig}).status_code == 200
    assert db.organizations.find_one({"_id": ObjectId(org)})["status"] == "demo"
    # Super Admin confirmation -> org active
    r = _call(client, "POST", f"/api/super-admin/subscriptions/{co['subscription_id']}/confirm", sa)
    assert r.status_code == 200, r.text
    org_doc = db.organizations.find_one({"_id": ObjectId(org)})
    assert org_doc["status"] == "active" and org_doc["admin_portal_enabled"] is True
    assert db.demo_requests.find_one({"_id": req["_id"]})["status"] == "converted"
    assert _call(client, "GET", "/api/org-admin/overview", owner).status_code == 200
    # Admin invites a user: link, no password
    r = _call(client, "POST", "/api/organizations/current/invitations", owner,
              {"email": "sam@example.com", "role": "member"})
    assert r.status_code == 200, r.text
    token = r.json()["invite_url"].rsplit("/", 1)[1]
    mail = db.email_outbox.find_one({"kind": "invitation", "to": "sam@example.com"}) or \
        db.email_outbox.find_one({"kind": "invitation"})
    assert f"/invite/{token}" in mail["body"] and "password:" not in mail["body"].lower()
    # invited user registers and runs a search
    client.cookies.clear()
    assert client.get(f"/api/invitations/{token}").status_code == 200
    r = client.post(f"/api/invitations/{token}/register", json={"name": "Sam", "password": PASSWORD})
    assert r.status_code == 200, r.text
    sam = {COOKIE_NAME: r.cookies[COOKIE_NAME]}
    sam_id = r.json()["user"]["user_id"]
    r = _search(client, sam, url="https://www.facebook.com/another-page")
    assert r.status_code == 200, r.text
    sam_run = r.json()["run_id"]
    assert db.search_history.find_one({"run_id": sam_run})["user_id"] == sam_id
    # Admin sees it
    r = _call(client, "GET", "/api/org-admin/searches", owner)
    assert sam_run in {s.get("run_id") for s in r.json()["items"]}
    assert _call(client, "GET", f"/api/org-admin/searches/{sam_run}", owner).status_code == 200
    r = _call(client, "GET", f"/api/org-admin/users/{sam_id}", owner)
    assert r.status_code == 200 and r.json()["usage"]["searches"] == 1
    # ...but Sam does not see the owner's demo search
    assert _call(client, "GET", f"/api/search/{demo_run}", sam).status_code == 404
    # Super Admin sees it in the global views
    r = _call(client, "GET", "/api/super-admin/searches", sa)
    assert r.status_code == 200 and sam_run in r.text and demo_run in r.text
    r = _call(client, "GET", f"/api/super-admin/searches/{sam_run}/chain", sa)
    assert r.status_code == 200
    assert "Startup Inc" in _call(client, "GET", "/api/super-admin/organizations", sa).text
    for action in ("demo.requested", "demo.approved", "plan.selected", "payment.received",
                   "subscription.active", "organization.activated", "member.invited",
                   "member.joined", "search.started"):
        assert db.audit_logs.find_one({"action": action}), action


# ═══════════════════════════════════════════════════════════════════════════
# 9. Feature flags, maintenance mode, suspension
#    (also: test_super_admin_portal2.py TestFeatureFlags, test_step1 suspended org)
# ═══════════════════════════════════════════════════════════════════════════

def _flag(client, db, key, value):
    r = _call(client, "PUT", "/api/super-admin/feature-flags", _super(db),
              {"changes": {key: value}, "reason": "test"})
    assert r.status_code == 200, r.text
    from app.admin import settings as s
    s._CACHE.clear()


class TestItem9FlagsMaintenanceSuspension:
    def test_url_search_flag(self, world):
        client, db = world["client"], world["db"]
        _flag(client, db, "features.url_search.enabled", False)
        r = _search(client, world["cookies"]["a_user1"])
        assert r.status_code == 403 and r.json()["detail"]["errorType"] == "feature_disabled"
        _flag(client, db, "features.url_search.enabled", True)
        assert _search(client, world["cookies"]["a_user1"]).status_code == 200

    def test_platform_flag(self, world):
        client, db = world["client"], world["db"]
        _flag(client, db, "platform.facebook.enabled", False)
        r = _search(client, world["cookies"]["a_user1"])
        assert r.status_code == 403 and r.json()["detail"]["errorType"] == "platform_disabled"

    def test_exports_flag(self, world):
        client, db = world["client"], world["db"]
        _flag(client, db, "features.exports.enabled", False)
        assert _call(client, "GET", "/api/export/comments.csv",
                     world["cookies"]["a_owner"]).status_code == 403
        assert _call(client, "GET", "/api/org-admin/exports/leads.csv",
                     world["cookies"]["a_owner"]).status_code == 403
        # reading existing data is unaffected
        assert _call(client, "GET", "/api/leads", world["cookies"]["a_owner"]).status_code == 200

    def test_demo_registration_flag(self, world):
        client, db = world["client"], world["db"]
        _flag(client, db, "features.demo_registration.enabled", False)
        client.cookies.clear()
        r = client.post("/api/auth/signup", json={"name": "N", "email": "n@x.test",
                                                  "company": "X", "password": PASSWORD})
        assert r.status_code == 403
        assert not db.demo_requests.find_one({"email": "n@x.test"})

    def test_ai_analysis_flag_blocks_ai_stage(self, world):
        """The comment pipeline asks the AI stage only while the flag is on
        (org A is on the business plan, which includes ai_analysis)."""
        client, db = world["client"], world["db"]
        from app.pipeline import comment_ai
        post = world["data"]["a1"]["post"]
        seen = []
        real = comment_ai.analyze_comment_ai

        def spy(*a, **kw):
            seen.append(kw.get("allow_ai"))
            return real(*a, **kw)

        with patch.object(comment_ai, "analyze_comment_ai", spy):
            comment_ai.analyze_comments_for_post(post)
            assert seen and all(seen), seen
            assert comment_ai._ai_entitlement(world["org"]["a"]) == (True, None)
            seen.clear()
            _flag(client, db, "features.ai_analysis.enabled", False)
            summary = comment_ai.analyze_comments_for_post(post)
        assert seen and not any(seen), seen
        assert summary["ai_blocked"] == "feature_disabled"

    def test_maintenance_mode(self, world):
        client, db = world["client"], world["db"]
        sa = _super(db)
        with patch("app.admin.settings.is_maintenance_enabled", return_value=True):
            client.cookies.clear()
            r = client.get("/api/leads", cookies=world["cookies"]["a_user1"])
            assert r.status_code == 503 and r.json()["error"] == "maintenance"
            assert client.get("/dashboard", cookies=world["cookies"]["a_user1"]).status_code == 503
            assert _call(client, "GET", "/api/org-admin/overview",
                         world["cookies"]["a_owner"]).status_code == 503
            # admin portals, sign-in and public config stay reachable
            assert _call(client, "GET", "/api/super-admin/dashboard", sa).status_code == 200
            client.cookies.clear()
            assert client.get("/login").status_code == 200
            assert client.get("/health").status_code == 200
            assert client.post("/api/auth/login", json={"email": "user1@a.test",
                                                        "password": "wrong"}).status_code == 401
        assert _call(client, "GET", "/api/leads", world["cookies"]["a_user1"]).status_code == 200

    def test_maintenance_flag_round_trip(self, world):
        """The Super Admin flag is what the gate reads."""
        client, db = world["client"], world["db"]
        from app.admin import settings as s
        _flag(client, db, "maintenance.enabled", True)
        assert s.get_bool("maintenance.enabled") is True
        _flag(client, db, "maintenance.enabled", False)
        assert s.get_bool("maintenance.enabled") is False

    def test_organization_suspension(self, world):
        client, db = world["client"], world["db"]
        org = world["org"]["a"]
        r = _call(client, "POST", f"/api/admin/organizations/{org}/suspend", _super(db),
                  {"reason": "abuse"})
        assert r.status_code == 200, r.text
        for key in ("a_owner", "a_admin", "a_user1"):
            assert _call(client, "GET", "/api/leads", world["cookies"][key]).status_code == 403
            assert _call(client, "GET", "/api/org-admin/overview",
                         world["cookies"][key]).status_code == 403
        client.cookies.clear()
        r = client.post("/api/auth/login", json={"email": "user1@a.test", "password": PASSWORD})
        assert r.status_code == 403
        # other tenants unaffected
        assert _call(client, "GET", "/api/leads", world["cookies"]["b_user"]).status_code == 200
        _call(client, "POST", f"/api/admin/organizations/{org}/activate", _super(db), {})
        assert _call(client, "GET", "/api/leads", world["cookies"]["a_user1"]).status_code == 200

    def test_admin_member_suspension(self, world):
        """An owner suspends the org Admin: the admin loses both portals at once."""
        client, db = world["client"], world["db"]
        r = _call(client, "PATCH", f"/api/organizations/current/members/{world['u']['a_admin']}",
                  world["cookies"]["a_owner"], {"status": "suspended"})
        assert r.status_code == 200, r.text
        # sessions were revoked; even a freshly minted cookie is refused
        fresh = _site(world["u"]["a_admin"], "admin@a.test", world["org"]["a"], "admin")
        for c in (world["cookies"]["a_admin"], fresh):
            assert _call(client, "GET", "/api/org-admin/overview", c).status_code in (401, 403)
            assert _call(client, "GET", "/api/leads", c).status_code in (401, 403)

    def test_user_account_suspension(self, world):
        client, db = world["client"], world["db"]
        uid = world["u"]["a_user1"]
        r = _call(client, "POST", f"/api/admin/users/{uid}/suspend", _super(db), {"reason": "abuse"})
        assert r.status_code == 200, r.text
        fresh = _site(uid, "user1@a.test", world["org"]["a"], "member")
        for c in (world["cookies"]["a_user1"], fresh):
            assert _call(client, "GET", "/api/leads", c).status_code in (401, 403)
        client.cookies.clear()
        r = client.post("/api/auth/login", json={"email": "user1@a.test", "password": PASSWORD})
        assert r.status_code in (401, 403)
        # teammates unaffected
        assert _call(client, "GET", "/api/leads", world["cookies"]["a_user2"]).status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# 10. Invitations: no plaintext passwords, single-use, expiring
#     (also: test_step1_security.py::test_invitation_email_link_no_password,
#      test_org_admin.py::test_reset_access_sends_link_never_password)
# ═══════════════════════════════════════════════════════════════════════════

class TestItem10Invitations:
    def _invite(self, world, email="invitee@example.com"):
        r = _call(world["client"], "POST", "/api/organizations/current/invitations",
                  world["cookies"]["a_owner"], {"email": email, "role": "member"})
        assert r.status_code == 200, r.text
        return r.json(), r.json()["invite_url"].rsplit("/", 1)[1]

    def test_no_plaintext_secret_stored_emailed_or_returned(self, world):
        client, db = world["client"], world["db"]
        res, token = self._invite(world)
        inv = db.organization_invitations.find_one({"email": "invitee@example.com"})
        assert inv["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
        assert token not in repr(inv)
        assert not {"password", "password_hash", "temp_password"} & set(inv)
        assert "token_hash" not in json.dumps(res) and "password" not in json.dumps(res).lower()
        mail = db.email_outbox.find_one({"kind": "invitation"})
        assert "password:" not in mail["body"].lower() and "/invite/" in mail["body"]
        # team / invitation listings never show the token or its hash
        for url in ("/api/organizations/current/team", "/api/org-admin/invitations?status=all"):
            text = _call(client, "GET", url, world["cookies"]["a_owner"]).text
            assert token not in text and inv["token_hash"] not in text
        # registration stores only a bcrypt hash and returns no secret
        client.cookies.clear()
        r = client.post(f"/api/invitations/{token}/register",
                        json={"name": "Invitee", "password": PASSWORD})
        assert r.status_code == 200, r.text
        assert PASSWORD not in r.text and "password_hash" not in r.text
        user = db.users.find_one({"email": "invitee@example.com"})
        assert user["password_hash"].startswith("$2") and PASSWORD not in repr(user)
        assert not db.email_outbox.find_one({"body": {"$regex": re.escape(PASSWORD)}})

    def test_token_is_single_use(self, world):
        client, db = world["client"], world["db"]
        _, token = self._invite(world)
        client.cookies.clear()
        assert client.post(f"/api/invitations/{token}/register",
                           json={"name": "One", "password": PASSWORD}).status_code == 200
        client.cookies.clear()
        assert client.post(f"/api/invitations/{token}/register",
                           json={"name": "Two", "password": PASSWORD}).status_code in (400, 409)
        assert client.get(f"/api/invitations/{token}").status_code == 400
        # accept by an existing account with the used token also fails
        assert _call(client, "POST", f"/api/invitations/{token}/accept",
                     world["cookies"]["a_user1"]).status_code in (400, 403)
        assert db.users.count_documents({"email": "invitee@example.com"}) == 1

    def test_token_expires(self, world):
        client, db = world["client"], world["db"]
        _, token = self._invite(world)
        db.organization_invitations.update_one(
            {"email": "invitee@example.com"},
            {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(minutes=1)}})
        client.cookies.clear()
        r = client.post(f"/api/invitations/{token}/register", json={"name": "Late", "password": PASSWORD})
        assert r.status_code == 400 and "expired" in r.text
        assert db.organization_invitations.find_one({"email": "invitee@example.com"})["status"] == "expired"
        assert not db.users.find_one({"email": "invitee@example.com"})

    def test_expiry_window_bounded(self, world):
        world["db"].organizations.update_one({"_id": ObjectId(world["org"]["a"])},
                                             {"$set": {"settings.invite_expiry_days": 365}})
        self._invite(world)
        inv = world["db"].organization_invitations.find_one({"email": "invitee@example.com"})
        exp = inv["expires_at"].replace(tzinfo=timezone.utc) if inv["expires_at"].tzinfo is None \
            else inv["expires_at"]
        assert exp <= datetime.now(timezone.utc) + timedelta(days=30, minutes=1)

    def test_revoked_and_resent_tokens_are_dead(self, world):
        client, db = world["client"], world["db"]
        res, token = self._invite(world)
        inv_id = res["invitation"]["id"]
        r = _call(client, "POST", f"/api/organizations/current/invitations/{inv_id}/resend",
                  world["cookies"]["a_owner"])
        assert r.status_code == 200, r.text
        new_token = r.json()["invite_url"].rsplit("/", 1)[1]
        client.cookies.clear()
        assert client.get(f"/api/invitations/{token}").status_code == 400
        assert client.get(f"/api/invitations/{new_token}").status_code == 200
        new_id = r.json()["invitation"]["id"]
        _call(client, "DELETE", f"/api/organizations/current/invitations/{new_id}",
              world["cookies"]["a_owner"])
        client.cookies.clear()
        assert client.get(f"/api/invitations/{new_token}").status_code == 400

    def test_guessed_token_and_wrong_account(self, world):
        client = world["client"]
        _, token = self._invite(world)
        client.cookies.clear()
        assert client.get(f"/api/invitations/{token[:-2]}xx").status_code == 404
        assert client.post("/api/invitations/not-a-token/register",
                           json={"name": "X", "password": PASSWORD}).status_code == 404
        assert _call(client, "POST", f"/api/invitations/{token}/accept",
                     world["cookies"]["b_user"]).status_code == 403
