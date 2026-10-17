"""LeadAI E2E journey B6: keyword search in Edge (self-contained).

A new free-trial customer signs up, opens Keyword search, adds a keyword,
fills the quality filters and sees the per-stage cost estimate, starts the
search and reads the results; then strict mode, and a filter the platform
can't measure ("unsupported"). The Apify discovery output is the fake in
e2e/server.py (IG_PROFILES):

  punebakehouse  18k followers, business, Pune, active   -> passes
  tinycakes      300 followers                           -> fails min followers
  cakelover      25k, not a business, no location        -> fails business only;
                                                            location unknown
  oldbakery      Mumbai, last post 200 days ago          -> fails location / activity

Runs on its own customer, so it can follow the other journeys or run alone.
"""
import re
import time

from e2e.conftest import wait_for
from e2e.test_journeys import _no_server_errors, _skip_tour

RUN = str(int(time.time()))[-6:]
PW = "Kw-E2E-Pass-2026!"


def _signup(s, email):
    s.page.goto("/signup", wait_until="networkidle")
    s.page.fill("#name", "Kirti Keyword")
    s.page.fill("#email", email)
    s.page.fill("#company", f"Cake Finders {RUN}")
    s.page.select_option("#industry", index=1)
    s.page.fill("#password", PW)
    s.page.check("#terms")
    s.page.click("#submitBtn")
    s.page.wait_for_url(re.compile(r"/(user|dashboard)"), timeout=30000)
    s.page.wait_for_load_state("networkidle")
    _skip_tour(s.page)


def _done(s, run_id, timeout=120):
    def check():
        r = s.api.get(f"/api/keyword-search/{run_id}").json()
        return r if r.get("status") not in ("running", "queued", "pending", None) else None
    return wait_for(check, timeout, what=f"keyword run {run_id}")


def _candidates(s, run_id):
    r = s.api.get(f"/api/keyword-search/{run_id}/candidates?page_size=100").json()
    items = r.get("items") or r.get("candidates") or []
    return {str(c.get("handle") or c.get("profile_id") or c.get("url")).lower().rstrip("/").split("/")[-1]: c
            for c in items}


def _outcome(c, flt):
    return next((o["outcome"] for o in c.get("reasons") or [] if o.get("filter") == flt), None)


def test_b6_keyword_search_filters_strict_and_unsupported(person, db):
    s = person("kw_customer")
    _signup(s, f"kirti.{RUN}@cakefinders-e2e.com")
    page = s.page
    page.locator(".side-link[data-nav=search]").first.click()
    page.wait_for_timeout(700)
    _skip_tour(page)
    page.click("#searchTabKeyword")
    page.wait_for_timeout(800)
    _skip_tour(page)
    page.fill("#kwKeywordInput", "bakery")
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)
    # Instagram only
    for box in page.locator('#kwPlatforms input[name="kwPlatform"]').all():
        want = box.get_attribute("value") == "instagram"
        if box.is_enabled() and box.is_checked() != want:
            box.click()
    page.locator("#kwFiltersPanel summary").click()
    page.wait_for_timeout(300)
    page.fill("#kwf_min_followers", "1000")
    page.fill("#kwf_location_keyword", "Pune")
    page.check("#kwf_business_account_only")
    page.fill("#kwf_active_within_days", "60")
    page.wait_for_timeout(1500)
    # the cost of each stage is shown before anything is spent
    est = wait_for(lambda: page.inner_text("#kwEstimate").strip(), 20, what="cost estimate")
    assert re.search(r"token", est, re.I), est
    tokens_before = db.token_balances.find_one({"organization_id": {"$exists": True}},
                                               sort=[("_id", -1)])["remaining"]
    page.click("#kwStartBtn")
    confirm = page.get_by_role("button", name=re.compile(r"^(Start|Confirm|Yes|Run|Find)", re.I))
    try:
        confirm.last.wait_for(state="visible", timeout=4000)
        confirm.last.click()
    except Exception:
        pass
    run = wait_for(lambda: db.search_history.find_one({"search_type": "keyword", "created_by": f"kirti.{RUN}@cakefinders-e2e.com"},
                                                      sort=[("created_at", -1)]), 30, what="keyword run created")
    final = _done(s, run["run_id"])
    assert final["status"] == "completed", final
    wait_for(lambda: re.search(r"punebakehouse|Pune Bake House", page.inner_text("body"), re.I), 30,
             what="passing profile on screen")
    page.wait_for_timeout(1500)
    c = _candidates(s, run["run_id"])
    assert c["punebakehouse"]["status"] == "passed", c["punebakehouse"]
    assert c["tinycakes"]["status"] == "rejected" and _outcome(c["tinycakes"], "min_followers") == "fail"
    assert c["cakelover"]["status"] == "rejected" and _outcome(c["cakelover"], "business_account_only") == "fail"
    assert c["oldbakery"]["status"] == "rejected"
    # tokens are charged per stage actually run, and only for what ran
    stored = db.search_history.find_one({"run_id": run["run_id"]})
    charged = stored.get("tokens_charged") or {}
    assert charged.get("discovery", charged.get("discover", 0)) > 0, charged
    org = stored["organization_id"]
    spent = tokens_before - db.token_balances.find_one({"organization_id": org})["remaining"]
    assert spent == sum(int(v or 0) for v in charged.values()), (spent, charged)

    # strict mode: an unknown value (cakelover has no location) rejects instead of passing
    body = {"keywords": ["bakery"], "platforms": ["instagram"], "confirm": True,
            "filters": {"min_followers": 1000, "location_keyword": "Pune"}}
    loose = s.api.post("/api/keyword-search", data={**body, "strict": False})
    assert loose.ok, loose.text()
    strict = s.api.post("/api/keyword-search", data={**body, "strict": True})
    assert strict.ok, strict.text()
    lc = _candidates(s, _done(s, loose.json()["run_id"])["run_id"])
    sc = _candidates(s, _done(s, strict.json()["run_id"])["run_id"])
    assert _outcome(lc["cakelover"], "location_keyword") == "unknown" and lc["cakelover"]["status"] == "passed", \
        lc["cakelover"]
    assert sc["cakelover"]["status"] == "rejected", sc["cakelover"]

    # a filter the platform can't measure is reported as unsupported, not a silent pass/fail
    cap = s.api.get("/api/keyword-search/capabilities").json()
    fb = (cap.get("platforms") or {}).get("facebook", {})
    assert fb.get("filters", {}).get("min_posts", {}).get("support") == "unsupported", fb
    est = s.api.post("/api/keyword-search/estimate", data={"keywords": ["bakery"], "platforms": ["facebook"],
                                                          "filters": {"min_posts": 50}}).json()
    assert est["tokens"]["discover"] > 0, est
    fbr = s.api.post("/api/keyword-search", data={"keywords": ["bakery"], "platforms": ["facebook"],
                                                 "filters": {"min_posts": 50}, "confirm": True})
    assert fbr.ok, fbr.text()
    fc = _candidates(s, _done(s, fbr.json()["run_id"])["run_id"])
    assert fc and all(_outcome(x, "min_posts") == "unsupported" for x in fc.values()), \
        {k: x.get("reasons") for k, x in fc.items()}
    # LinkedIn is off by default
    li = s.api.post("/api/keyword-search/estimate", data={"keywords": ["bakery"], "platforms": ["linkedin"]})
    assert li.status in (403, 422), li.status
    page.reload()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1500)
    _no_server_errors(s)
