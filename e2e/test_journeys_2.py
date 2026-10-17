"""LeadAI E2E journeys, part 2 (after e2e/test_journeys.py, sharing its STATE).

Order: D (Super Admin, including confirming the customer's payment) runs
before C (Org Admin), because the Org Admin portal only opens for an
organization with a confirmed plan. Then B's exports (a paid plan allows
them), E (partners), F (billing) and G (cross-portal security).
"""
import csv
import hashlib
import hmac
import http.server
import io
import json
import re
import threading

from bson import ObjectId

from e2e.conftest import wait_for
from e2e.server import E2E_SUPERADMIN, E2E_SUPERADMIN_PASSWORD, PAGE_URL
from e2e.test_journeys import CUSTOMER, RUN, STATE, _customer, _login, _nav, _no_server_errors, _skip_tour


# ═══════════════════════════════════════════════════════════════════════════
# D. Super Admin
# ═══════════════════════════════════════════════════════════════════════════

def _super(person):
    s = person("superadmin")
    _login(s, E2E_SUPERADMIN, E2E_SUPERADMIN_PASSWORD, "?superadmin=1")
    s.page.wait_for_url(re.compile(r"/superadmin"), timeout=30000)
    s.page.wait_for_load_state("networkidle")
    _skip_tour(s.page)
    return s


def _sa_view(s, route, expect=None):
    s.page.goto(f"/superadmin#/{route}")
    s.page.wait_for_load_state("networkidle")
    s.page.wait_for_timeout(900)
    _skip_tour(s.page)
    if expect:
        wait_for(lambda: re.search(expect, s.page.inner_text("body"), re.I), 20, what=f"{route} view")


def test_d1_super_admin_portal_only(person):
    s = _super(person)
    _sa_view(s, "dashboard")
    # a Super Admin session is not a customer session
    assert s.api.get("/api/search/history").status == 401
    s.page.goto("/user")
    s.page.wait_for_load_state("networkidle")
    assert "/login" in s.page.url, f"Super Admin session opened the customer app: {s.page.url}"
    _no_server_errors(s)


def test_d2_customer_pays_and_super_admin_confirms(person, db):
    cust = _customer(person)
    _nav(cust.page, "billing")
    plans = cust.api.get("/api/billing/plans").json()
    paid = next(p for p in plans.get("plans", plans.get("items", []))
                if (p.get("price_monthly") or 0) > 0 and p.get("slug") != "enterprise")
    r = cust.api.post("/api/billing/checkout", data={"plan_slug": paid["slug"], "billing_cycle": "monthly"})
    assert r.ok, r.text()
    checkout = r.json()["checkout"]
    # no gateway keys in the E2E stack: the test-only mock checkout, never a real charge
    assert "mock" in json.dumps(checkout).lower(), checkout
    cust.page.goto(checkout.get("url") or checkout.get("checkout_url") or f"/billing/checkout/{checkout['session_id']}")
    cust.page.wait_for_load_state("networkidle")
    cust.page.wait_for_timeout(1200)
    assert cust.api.post(f"/api/billing/checkout/{checkout['session_id']}/mock-pay", data={"succeed": True}).ok
    sub = db.subscriptions.find_one({"checkout_session_id": checkout["session_id"]})
    assert sub["status"] == "pending_admin_confirmation", sub["status"]
    assert db.organizations.find_one({"_id": ObjectId(STATE["org_id"])})["status"] == "demo", \
        "a payment started the plan without Super Admin confirmation"
    sa = _super(person)
    _sa_view(sa, "subscriptions", r"subscription")
    r = sa.api.post(f"/api/super-admin/subscriptions/{sub['_id']}/confirm")
    assert r.ok, r.text()
    org = db.organizations.find_one({"_id": ObjectId(STATE["org_id"])})
    assert org["status"] == "active", org["status"]
    assert db.subscriptions.find_one({"_id": sub["_id"]})["status"] == "active"
    STATE.update(sub_id=str(sub["_id"]), plan=paid["slug"])
    sa.page.reload()
    sa.page.wait_for_timeout(1200)
    _no_server_errors(sa)


def test_d3_free_trial_config_applies_to_new_signups(person, db):
    sa = _super(person)
    _sa_view(sa, "demo", r"trial|demo")
    before = sa.api.get("/api/super-admin/demo-config").json()
    cfg = before.get("config") or before
    r = sa.api.put("/api/super-admin/demo-config", data={"duration_days": 5, "tokens": 350})
    assert r.ok, r.text()
    try:
        v = person("visitor2")
        email = f"newtrial.{RUN}@acme-e2e.com"
        v.page.goto("/signup", wait_until="networkidle")
        v.page.fill("#name", "New Trial")
        v.page.fill("#email", email)
        v.page.fill("#company", f"Trial Co {RUN}")
        v.page.select_option("#industry", index=1)
        v.page.fill("#password", "Trial-E2E-Pass-2026!")
        v.page.check("#terms")
        v.page.click("#submitBtn")
        v.page.wait_for_url(re.compile(r"/(user|dashboard)"), timeout=30000)
        user = db.users.find_one({"email": email})
        m = db.organization_members.find_one({"user_id": str(user["_id"])})
        org = db.organizations.find_one({"_id": ObjectId(m["organization_id"])})
        assert org["demo"]["config"]["duration_days"] == 5
        assert (org["demo"]["expires_at"] - org["demo"]["started_at"]).days == 5
        assert db.token_balances.find_one({"organization_id": str(org["_id"])})["remaining"] == 350
    finally:
        sa.api.put("/api/super-admin/demo-config", data={"duration_days": cfg.get("duration_days", 3),
                                                           "tokens": cfg.get("tokens", 500)})
    _no_server_errors(sa)


def test_d4_messaging_test_email(person, db):
    sa = _super(person)
    _sa_view(sa, "messaging", r"email|messag")
    r = sa.api.post("/api/super-admin/messaging/test", data={"channel": "email", "to": "qa-inbox@acme-e2e.com"})
    STATE["test_email"] = (r.status, r.text()[:300])
    # no mail server in the E2E stack: a clear refusal (not a crash) is the right answer
    assert r.status < 500, r.text()
    assert r.status != 404, "test-email endpoint not found"
    _no_server_errors(sa)


def test_d5_global_settings_version_rollback_export_import(person):
    sa = _super(person)
    _sa_view(sa, "settings", r"settings")
    key = "general.contact.support_email"

    def value():
        cur = sa.api.get("/api/admin/settings").json()
        return (cur.get("settings") or cur.get("values") or cur).get(key)

    def save(v, why):
        r = sa.api.put("/api/admin/settings", data={"values": {key: v}, "reason": why})
        assert r.ok, r.text()
        return r.json().get("version")

    first, second = f"qa-a-{RUN}@acme-e2e.com", f"qa-b-{RUN}@acme-e2e.com"
    v1 = save(first, "E2E change 1")
    v2 = save(second, "E2E change 2")
    versions = sa.api.get("/api/admin/settings/history").json()["versions"]
    assert v2 == v1 + 1 and versions[0]["version"] == v2, (v1, v2, versions[:2])
    assert value() == second
    exported = sa.api.get("/api/admin/settings/export").json()
    assert exported["settings"][key] == second
    # roll back to version 1
    r = sa.api.post(f"/api/admin/settings/history/{v1}/restore", data={})
    assert r.ok, r.text()
    assert value() == first, "rollback did not bring version 1 back"
    assert sa.api.get("/api/admin/settings/history").json()["versions"][0]["version"] > v2
    # import the export: version 2's value returns and the round trip is exact
    r = sa.api.post("/api/admin/settings/import", data={"payload": exported})
    assert r.ok, r.text()
    assert value() == second
    assert sa.api.get("/api/admin/settings/export").json()["settings"] == exported["settings"]
    sa.page.reload()
    sa.page.wait_for_timeout(1000)
    _no_server_errors(sa)


def test_d6_environment_guard(person):
    sa = _super(person)
    _sa_view(sa, "environment")
    st = sa.api.get("/api/admin/env/lock-status").json()
    STATE["env_lock_status"] = st
    if not (st.get("guard_set") or st.get("configured") or st.get("password_set")):
        assert sa.api.put("/api/admin/env/guard", data={"new_password": "Guard-E2E-Pass-2026!"}).ok
    codes = [sa.api.post("/api/admin/env/unlock", data={"password": "wrong-guess"}).status for _ in range(8)]
    STATE["env_wrong_codes"] = codes
    assert all(c in (400, 401, 403, 429) for c in codes), codes
    assert 429 in codes, f"no lockout after repeated wrong guard passwords: {codes}"
    env = sa.api.get("/api/admin/env")
    assert env.status in (401, 403, 423), f"environment readable without the guard: {env.status}"
    _no_server_errors(sa)


def test_d7_audit_log_records_the_run(person, db):
    sa = _super(person)
    _sa_view(sa, "audit", r"audit")
    actions = {a["action"] for a in db.audit_logs.find({}, {"action": 1})}
    STATE["audit_actions"] = sorted(actions)
    for prefix in ("auth.login", "settings", "subscription"):
        assert any(a.startswith(prefix) or prefix in a for a in actions), (prefix, sorted(actions))
    settings = sa.api.get("/api/admin/settings").json()
    assert not [k for k in (settings.get("settings") or settings) if "audit" in k and "enabled" in k], \
        "a setting can switch the audit log off"
    _no_server_errors(sa)


# ═══════════════════════════════════════════════════════════════════════════
# C. Org Admin
# ═══════════════════════════════════════════════════════════════════════════

def _owner_admin(person):
    s = person("org_owner")
    _login(s, CUSTOMER["email"], CUSTOMER["password"])
    s.page.wait_for_url(re.compile(r"/(user|dashboard)"), timeout=30000)
    s.page.goto("/org-admin")
    s.page.wait_for_load_state("networkidle")
    _skip_tour(s.page)
    return s


def test_c1_invite_member_who_accepts(person, db):
    s = _owner_admin(person)
    assert "/org-admin" in s.page.url, s.page.url
    member_email = f"sam.{RUN}@acme-e2e.com"
    r = s.api.post("/api/organizations/current/invitations", data={"email": member_email, "role": "member"})
    assert r.status in (200, 201), r.text()
    invite_url = r.json()["invite_url"]
    mail = wait_for(lambda: db.email_outbox.find_one({"to": member_email}), 15, what="invitation email")
    token = invite_url.rstrip("/").split("/")[-1].split("token=")[-1]
    assert token not in mail["body"], "live invitation link stored in the outbox"
    m = person("member")
    m.page.goto(invite_url)
    m.page.wait_for_load_state("networkidle")
    m.page.wait_for_timeout(800)
    assert m.api.post(f"/api/invitations/{token}/register",
                      data={"name": "Sam Member", "password": "Sam-E2E-Pass-2026!"}).ok
    _login(m, member_email, "Sam-E2E-Pass-2026!")
    m.page.wait_for_url(re.compile(r"/(user|dashboard)"), timeout=30000)
    me = m.api.get("/api/auth/me").json()["user"]
    assert me["organization_id"] == STATE["org_id"] and me["org_role"] == "member"
    assert m.api.get("/api/org-admin/overview").status in (401, 403)
    STATE.update(member_email=member_email, member_id=me["user_id"])
    _no_server_errors(s)


def test_c2_role_change_takes_effect_on_next_request(person, db):
    s = _owner_admin(person)
    s.page.goto("/org-admin#team")
    s.page.wait_for_timeout(1000)
    m = person("member")
    _login(m, STATE["member_email"], "Sam-E2E-Pass-2026!")
    m.page.wait_for_url(re.compile(r"/(user|dashboard)"), timeout=30000)
    assert m.api.get("/api/org-admin/overview").status in (401, 403)
    r = s.api.patch(f"/api/organizations/current/members/{STATE['member_id']}", data={"role": "admin"})
    assert r.ok, r.text()
    assert db.organization_members.find_one({"user_id": STATE["member_id"]})["role"] == "admin"
    # the change revokes the member's sessions: the very next request is refused ...
    assert m.api.get("/api/auth/me").status == 401
    # ... and after signing in again the new role applies
    _login(m, STATE["member_email"], "Sam-E2E-Pass-2026!")
    m.page.wait_for_url(re.compile(r"/(user|dashboard|org-admin)"), timeout=30000)
    assert m.api.get("/api/auth/me").json()["user"]["org_role"] == "admin"
    assert m.api.get("/api/org-admin/overview").ok, "new role not applied"
    # back to member for the rest of the run
    assert s.api.patch(f"/api/organizations/current/members/{STATE['member_id']}", data={"role": "member"}).ok
    _no_server_errors(s)


def _search(s, db, slug):
    r = s.api.post(f"/api/url/search?url={PAGE_URL.replace('metrocarsdealer', slug)}&max_posts=2&max_comments_per_post=20")
    assert r.ok, r.text()
    run_id = r.json()["run_id"]
    wait_for(lambda: (db.search_history.find_one({"run_id": run_id}) or {}).get("status") in ("completed", "error"),
             120, what=f"search {slug}")
    return run_id


def test_c3_round_robin_rule_rotates_owner_with_sla(person, db):
    s = _owner_admin(person)
    s.page.goto("/org-admin#rules")
    s.page.wait_for_timeout(1000)
    owner, member = STATE["customer_id"], STATE["member_id"]
    bad = s.api.post("/api/org-admin/assignment-rules", data={"name": "x", "assignees": ["0123456789abcdef01234567"]})
    assert bad.status == 400, "a rule may name someone outside the organization"
    r = s.api.post("/api/org-admin/assignment-rules",
                   data={"name": "Leads RR", "rule_type": "round_robin", "assignees": [owner, member], "sla_hours": 4})
    assert r.ok, r.text()
    before = {ld["_id"] for ld in db.ai_comments.find({"organization_id": STATE["org_id"]}, {"_id": 1})}
    _search(s, db, "metrocarsdealer2")
    new = [ld for ld in db.ai_comments.find({"organization_id": STATE["org_id"], "is_lead": True})
           if ld["_id"] not in before]
    assert len(new) >= 2, f"only {len(new)} new leads"
    assert {ld.get("assigned_user_id") for ld in new} == {owner, member}, [ld.get("assigned_user_id") for ld in new]
    assert all(ld.get("sla_hours") == 4 and ld.get("sla_due_at") and ld.get("sla_status") == "on_track" for ld in new)
    _no_server_errors(s)


def test_c4_webhook_delivery_is_signed(person, db):
    got = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            got.append((dict(self.headers), self.rfile.read(int(self.headers.get("Content-Length") or 0))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        s = _owner_admin(person)
        s.page.goto("/org-admin#integrations")
        s.page.wait_for_timeout(1000)
        r = s.api.post("/api/org-admin/webhooks", data={
            "url": f"http://127.0.0.1:{srv.server_port}/hook", "secret": "whsec_e2e_secret_123",
            "events": ["lead.created", "search.completed"]})
        assert r.ok, r.text()
        assert "whsec_e2e_secret_123" not in s.api.get("/api/org-admin/webhooks").text(), \
            "signing secret returned by the listing"
        _search(s, db, "metrocarsdealer3")
        wait_for(lambda: got, 20, what="a webhook delivery (BUG-027: nothing dispatches webhook events)")
        headers, body = got[0]
        sig = "sha256=" + hmac.new(b"whsec_e2e_secret_123", body, hashlib.sha256).hexdigest()
        assert headers.get("X-LeadAI-Signature") == sig
    finally:
        srv.shutdown()


def test_c5_api_key_use_and_revoke(person):
    s = _owner_admin(person)
    s.page.goto("/org-admin#integrations")
    s.page.wait_for_timeout(1000)
    r = s.api.post("/api/org-admin/api-keys", data={"name": "CRM sync", "scopes": ["leads:read"]})
    assert r.ok, r.text()
    body = r.json()
    raw, kid = body["api_key"], body["key_id"]
    assert raw.startswith("lai_live_"), body
    crm = person("crm")
    assert crm.api.get("/api/v1/leads", headers={"X-API-Key": raw}).ok
    assert s.api.delete(f"/api/org-admin/api-keys/{kid}").ok
    assert crm.api.get("/api/v1/leads", headers={"X-API-Key": raw}).status == 401, "revoked key still works"
    _no_server_errors(s)


def test_c6_invoices_show_the_paid_invoice(person):
    s = _owner_admin(person)
    s.page.goto("/org-admin#subscription")
    s.page.wait_for_timeout(1200)
    inv = s.api.get("/api/billing/invoices").json()
    items = inv.get("invoices") or inv.get("items") or []
    assert items, inv
    assert any(str(i.get("status")).lower() == "paid" for i in items), [i.get("status") for i in items]
    _no_server_errors(s)


# ═══════════════════════════════════════════════════════════════════════════
# B (cont.) Exports and the Limits card
# ═══════════════════════════════════════════════════════════════════════════

def test_b4_exports_csv_and_excel(person, db):
    s = _customer(person)
    _nav(s.page, "exports")
    r = s.api.get("/api/me/leads.csv")
    assert r.ok, r.text()[:300]
    rows = list(csv.reader(io.StringIO(r.text())))
    header = [h.lower() for h in rows[0]]
    assert any("name" in h for h in header) and any("score" in h for h in header), header
    mine = db.ai_comments.count_documents({"organization_id": STATE["org_id"], "is_lead": True,
                                           "$or": [{"user_id": STATE["customer_id"]},
                                                   {"assigned_user_id": STATE["customer_id"]}]})
    assert len(rows) - 1 == mine, (len(rows) - 1, mine)
    x = s.api.get("/api/leads/export/xlsx")
    assert x.ok and len(x.body()) > 200, x.status
    STATE["xlsx_type"] = x.headers.get("content-type")
    _no_server_errors(s)


def test_b5_limits_card(person):
    s = _customer(person)
    _nav(s.page, "usage")
    wait_for(lambda: re.search(r"token", s.page.inner_text("main"), re.I), 20, what="usage view")
    lim = s.api.get("/api/me/api-limits")
    assert lim.ok, lim.text()
    _no_server_errors(s)
