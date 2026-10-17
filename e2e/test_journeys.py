"""LeadAI end-to-end journeys in Microsoft Edge (see e2e/conftest.py).

The journeys run in order and share the people they create (STATE): the
customer who signs up in A searches in B, owns the organization in C, and so
on. Each step asserts what the person sees AND what the server stored.
"""
import re
import time


from e2e.conftest import wait_for
from e2e.server import (PAGE_URL)

STATE = {}
RUN = str(int(time.time()))[-6:]
CUSTOMER = {"name": "Priya Customer", "email": f"priya.{RUN}@acme-e2e.com", "company": f"Acme E2E {RUN}",
            "phone": "+91 98220 11111", "password": "Acme-E2E-Pass-2026!"}


def _no_server_errors(s):
    assert not s.http_errors, f"{s.name} saw server errors: {s.http_errors}"


def _login(s, email, password, portal=""):
    try:
        s.page.goto("/login" + portal)
    except Exception as e:
        # a signed-out page (e.g. after its session was revoked) may be
        # redirecting itself to /login at the same moment: that's fine
        if "interrupted by another navigation" not in str(e):
            raise
        s.page.wait_for_url(re.compile(r"/login"), timeout=15000)
    s.page.wait_for_selector("#loginEmail")
    s.page.fill("#loginEmail", email)
    s.page.fill("#loginPassword", password)
    s.page.click("#signinBtn")


# ═══════════════════════════════════════════════════════════════════════════
# A. Public website + sign-up
# ═══════════════════════════════════════════════════════════════════════════

PUBLIC_PAGES = ["/", "/pricing", "/contact", "/partners"]


def test_a1_public_pages_render(person):
    s = person("visitor")
    for path in PUBLIC_PAGES:
        s.console_errors.clear()
        r = s.page.goto(path, wait_until="networkidle")
        assert r.status == 200, (path, r.status)
        body = s.page.inner_text("body")
        assert len(body.strip()) > 200, f"{path} rendered almost nothing"
        assert "LeadAI" in body or "LeadAI" in s.page.title(), path
        broken = s.page.evaluate("""() => [...document.images]
            .filter(i => i.complete && i.getAttribute('src') && i.naturalWidth === 0).map(i => i.src)""")
        assert not broken, f"{path} broken images: {broken}"
        assert not s.console_errors, f"{path} console errors: {s.console_errors}"
        s.page.mouse.wheel(0, 1200)
        s.page.wait_for_timeout(400)
    # CMS content: the pricing page lists the seeded plans
    s.page.goto("/pricing", wait_until="networkidle")
    text = s.page.inner_text("body")
    assert "Starter" in text and "Professional" in text, "pricing page does not list the CMS plans"
    _no_server_errors(s)


def test_a2_signup_starts_free_trial(person, db):
    s = person("customer")
    s.page.goto("/signup", wait_until="networkidle")
    s.page.fill("#name", CUSTOMER["name"])
    s.page.fill("#email", CUSTOMER["email"])
    s.page.fill("#company", CUSTOMER["company"])
    s.page.select_option("#industry", index=1)
    s.page.fill("#phone", CUSTOMER["phone"])
    s.page.fill("#password", CUSTOMER["password"])
    s.page.fill("#message", "Car dealers asking for prices on Facebook")
    s.page.check("#terms")
    s.page.click("#submitBtn")
    s.page.wait_for_url(re.compile(r"/(user|dashboard)"), timeout=30000)
    s.page.wait_for_load_state("networkidle")

    user = db.users.find_one({"email": CUSTOMER["email"]})
    assert user, "sign-up created no user"
    member = db.organization_members.find_one({"user_id": str(user["_id"])})
    org = db.organizations.find_one({"_id": __import__("bson").ObjectId(member["organization_id"])})
    assert member["role"] == "owner"
    assert org["status"] in ("demo", "trial", "active"), org["status"]
    # default free trial: 3 days and a limited token allowance
    assert org["status"] == "demo"
    demo = org["demo"]
    assert (demo["expires_at"] - demo["started_at"]).days == demo["config"]["duration_days"] == 3
    ends = demo["expires_at"]
    bal = db.token_balances.find_one({"organization_id": str(org["_id"])})
    assert bal and bal["remaining"] == demo["config"]["tokens"] == 500, bal
    # welcome email queued (no mail server in the E2E stack)
    mail = wait_for(lambda: db.email_outbox.find_one({"to": CUSTOMER["email"]}), 15, what="welcome email")
    assert mail["status"] in ("queued", "failed", "sent")
    STATE.update(customer_id=str(user["_id"]), org_id=str(org["_id"]), trial_ends=ends)
    # the user panel shows the trial
    assert re.search(r"trial|demo", s.page.inner_text("body"), re.I), "no trial notice in the user panel"
    _no_server_errors(s)


# ═══════════════════════════════════════════════════════════════════════════
# B. Customer app
# ═══════════════════════════════════════════════════════════════════════════

def _skip_tour(page):
    """Close the welcome tour if it is open (it covers the page)."""
    skip = page.get_by_role("button", name=re.compile(r"Skip guide", re.I))
    try:
        skip.wait_for(state="visible", timeout=4000)
        skip.click()
    except Exception:
        pass
    page.wait_for_timeout(300)


def _customer(person):
    s = person("customer")
    _login(s, CUSTOMER["email"], CUSTOMER["password"])
    s.page.wait_for_url(re.compile(r"/(user|dashboard)"), timeout=30000)
    s.page.wait_for_load_state("networkidle")
    _skip_tour(s.page)
    return s


def _nav(page, name):
    page.locator(f".side-link[data-nav={name}]").first.click()
    page.wait_for_timeout(700)


def _wizard_next(page, step):
    btn = page.locator(f"#btnStep{step}Next")
    btn.click()
    page.wait_for_timeout(500)


def test_b1_login_shows_trial(person):
    s = _customer(person)
    banner = s.page.get_by_text(re.compile(r"free trial ends in", re.I)).first
    assert banner.is_visible(), "trial banner not visible after login"
    assert "500" in s.page.inner_text("body"), "token allowance not shown"
    _no_server_errors(s)


def test_b2_link_search_end_to_end(person, db):
    s = _customer(person)
    page = s.page
    _nav(page, "search")
    _skip_tour(page)
    # 1 industry -> 2 business type -> 3 keywords -> 4 link -> 5 review
    card = page.locator(".industry-card-item[data-ind=automotive]")
    (card if card.count() else page.locator(".industry-card-item").first).click()
    _wizard_next(page, 1)
    _wizard_next(page, 2)
    if page.locator("#btnStep3Next").is_disabled():
        page.locator("#kwSuggestionsProduct button, #kwSuggestionsProduct .kw-chip").first.click()
    _wizard_next(page, 3)
    page.fill("#urlSearchInput", PAGE_URL)
    wait_for(lambda: "facebook" in page.inner_text("#urlDetectionHint").lower()
             or "facebook" in page.inner_text("#urlPlatformChip").lower(), 10, what="platform detection")
    page.fill("#urlSearchLimit", "5")
    page.fill("#urlSearchCommentsPerPost", "20")
    _wizard_next(page, 4)
    # cost estimate shown before anything is spent
    cost = wait_for(lambda: page.inner_text("#searchCostRow").strip(), 15, what="cost estimate")
    assert re.search(r"token", cost, re.I), f"no token cost estimate: {cost!r}"
    before = db.search_history.count_documents({"organization_id": STATE["org_id"]})
    page.click("#urlSearchBtn")
    # an expensive search asks for confirmation first
    confirm = page.get_by_role("button", name=re.compile(r"^(Confirm|Start|Yes|Continue|Run)", re.I))
    try:
        confirm.first.wait_for(state="visible", timeout=3000)
        confirm.first.click()
    except Exception:
        pass
    run = wait_for(lambda: db.search_history.find_one({"organization_id": STATE["org_id"]},
                                                       sort=[("created_at", -1)])
                   if db.search_history.count_documents({"organization_id": STATE["org_id"]}) > before else None,
                   30, what="search job created")
    done = wait_for(lambda: (lambda r: r if r and r.get("status") not in ("running", "queued", "pending") else None)(
        db.search_history.find_one({"_id": run["_id"]})), 120, what="search completion")
    assert done["status"] == "completed", f"search ended {done['status']}: {done.get('error')}"
    comments = db.facebook_comments.count_documents({"organization_id": STATE["org_id"]})
    assert comments >= 4, f"only {comments} comments stored"
    STATE["run_id"] = done.get("run_id") or str(done["_id"])
    # the results show the page, its post and the comments
    wait_for(lambda: "Metro Cars" in page.inner_text("body"), 30, what="results on screen")
    page.wait_for_timeout(1500)
    _no_server_errors(s)


def test_b3_lead_scoring_and_pipeline(person, db):
    s = _customer(person)
    org = STATE["org_id"]
    leads = list(db.ai_comments.find({"organization_id": org}))
    assert leads, "no comment was analysed"
    scores = [ld.get("lead_score") for ld in leads if ld.get("lead_score") is not None]
    assert scores and all(0 <= x <= 100 for x in scores), scores
    # emoji-only / link-only comments are filtered before any AI call: kept
    # with a reason, score 0, never a lead
    noise = [ld for ld in leads if ld.get("comment_text") in ("🔥🔥🔥", "https://spam.example/win")]
    assert len(noise) == 2, [ld.get("comment_text") for ld in leads]
    for n in noise:
        assert n["is_lead"] is False and n["lead_score"] == 0 and n["analyzed_by"] != "gemini", n
        assert re.search(r"emoji|link", str(n.get("reason")), re.I), n.get("reason")
    # no Gemini key in the E2E stack: every comment was scored by the keyword rules
    assert {ld.get("analyzed_by") for ld in leads} <= {"rules", "rule_based", "keywords", None}, \
        {ld.get("analyzed_by") for ld in leads}
    real = [ld for ld in leads if ld.get("is_lead")]
    assert real and max(ld["lead_score"] for ld in real) >= 50, [(ld.get("commenter_name"), ld.get("lead_score")) for ld in leads]
    _nav(s.page, "leads")
    wait_for(lambda: re.search(r"Ravi|Anita|Meera", s.page.inner_text("body")), 30, what="leads list")
    r = s.api.get("/api/leads?limit=50")
    assert r.ok, r.text()
    items = r.json().get("leads") or r.json().get("items") or []
    hot = next(ld for ld in items if re.search(r"Ravi|Anita|Meera", str(ld.get("commenter_name") or "")))
    lid = str(hot["id"])
    # open it in the UI
    s.page.get_by_text(hot["commenter_name"]).filter(visible=True).first.click()
    s.page.wait_for_timeout(1200)
    # status, note, follow-up, owner (the same API the lead drawer uses)
    r = s.api.patch(f"/api/leads/{lid}", data={"lead_status": "contacted"})
    assert r.ok, r.text()
    assert s.api.post(f"/api/leads/{lid}/notes", data={"text": "Called, wants a test drive"}).ok
    due = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 86400))
    fu = s.api.post(f"/api/leads/{lid}/follow-ups", data={"title": "Test drive Saturday", "due_at": due})
    assert fu.ok, fu.text()
    a = s.api.patch(f"/api/leads/{lid}", data={"assigned_user_id": STATE["customer_id"]})
    assert a.ok, a.text()
    lead = s.api.get(f"/api/leads/{lid}").json()
    lead = lead.get("lead") or lead
    assert lead.get("lead_status") == "contacted"
    assert any("test drive" in str(n.get("text", "")).lower() for n in lead.get("notes") or [])
    assert lead.get("follow_ups") and lead["follow_ups"][0]["status"] == "pending"
    assert str(lead.get("assigned_user_id")) == STATE["customer_id"]
    # a manual assignment sets the owner; SLA timers come from assignment rules (journey C)
    STATE["lead_id"] = lid
    s.page.reload()
    s.page.wait_for_load_state("networkidle")
    _no_server_errors(s)
