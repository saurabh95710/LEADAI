"""Public website (spec 2.2): CMS-driven content, no data leaks, live pricing,
contact form, SEO endpoints. Runs against the in-memory DB from conftest."""
import asyncio
import io

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.routes import admin_cms, public_website
from app.auth.roles import require_manager, require_super, require_viewer
from app.cms import service as svc
from app.cms.models import DEFAULT_PAGES, seed_cms_defaults
from app.db.mongo import get_async_db, get_sync_db

ADMIN = {"email": "root@leadai.test", "name": "Root", "role": "super_admin"}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _fresh_caches():
    svc.clear_cache()
    public_website.reset_contact_rate_limit()
    from app.billing import plans
    plans.invalidate_plan_cache()
    yield
    svc.clear_cache()
    public_website.reset_contact_rate_limit()


def _app():
    app = FastAPI()
    app.include_router(public_website.router)
    app.include_router(admin_cms.router)
    for dep in (require_super, require_viewer, require_manager):
        app.dependency_overrides[dep] = lambda: ADMIN

    @app.get("/website")
    async def website(request: Request):
        return await public_website.render_site_page(request, "website.html", "home")

    @app.get("/privacy")
    async def privacy(request: Request):
        return await public_website.render_site_page(request, "website.html", "privacy")
    return app


@pytest.fixture
def client():
    run(seed_cms_defaults(get_async_db()))
    svc.clear_cache()
    return TestClient(_app())


def _page_id(db, slug):
    return str(db["website_pages"].find_one({"slug": slug})["_id"])


# ── Seed ────────────────────────────────────────────────────────────────────

def test_seed_creates_published_pages_navigation_faq_and_settings(client):
    db = get_sync_db()
    slugs = {p["slug"] for p in db["website_pages"].find({})}
    assert {p["slug"] for p in DEFAULT_PAGES} <= slugs
    for slug in ("privacy", "terms", "cookies", "about", "home", "contact"):
        r = client.get(f"/api/public/page/{slug}")
        assert r.status_code == 200, slug
        assert r.json()["sections"], slug
    nav = client.get("/api/public/navigation").json()
    assert nav["header"] and nav["footer"]
    assert {i["group"] for i in nav["footer"]} >= {"Product", "Legal", "Account"}
    assert client.get("/api/public/faq").json()["faq"]
    assert client.get("/api/public/page/how-it-works").status_code == 200   # SEO-only page


def test_seed_never_overwrites_operator_edits_or_resurrects_deleted_pages(client):
    db = get_sync_db()
    home = _page_id(db, "home")
    assert client.put(f"/api/admin/cms/pages/{home}", json={"title": "My Home"}).status_code == 200
    assert client.post(f"/api/admin/cms/pages/{home}/publish").status_code == 200
    assert client.put("/api/admin/cms/settings", json={"brand_name": "Acme Leads"}).status_code == 200
    assert client.put("/api/admin/cms/navigation/header",
                      json={"items": [{"label": "Only", "url": "/pricing"}]}).status_code == 200
    assert client.delete(f"/api/admin/cms/pages/{_page_id(db, 'cookies')}").status_code == 200
    faq_count = db["website_faq"].count_documents({})
    db["website_faq"].delete_many({})

    run(seed_cms_defaults(get_async_db()))
    svc.clear_cache()

    assert client.get("/api/public/page/home").json()["title"] == "My Home"
    assert client.get("/api/public/settings").json()["brand_name"] == "Acme Leads"
    assert [i["label"] for i in client.get("/api/public/navigation").json()["header"]] == ["Only"]
    assert db["website_pages"].find_one({"slug": "cookies"}) is None
    assert db["website_faq"].count_documents({}) == 0 and faq_count > 0


def test_seed_backfills_untouched_legacy_page_but_not_an_edited_one():
    db = get_sync_db()
    base = {"status": "published", "sections": [], "version": 1, "versions": [], "created_by": "system",
            "seo": {"title": "t", "description": "d", "og_image": "", "robots": "index,follow"}}
    db["website_pages"].insert_one({**base, "slug": "home", "title": "Home"})
    db["website_pages"].insert_one({**base, "slug": "about", "title": "Edited", "updated_by": "op@x.io"})
    run(seed_cms_defaults(get_async_db()))
    assert db["website_pages"].find_one({"slug": "home"})["sections"]
    assert db["website_pages"].find_one({"slug": "about"})["sections"] == []


# ── Pages: drafts never leak ────────────────────────────────────────────────

def test_draft_and_unpublished_pages_are_never_public(client):
    db = get_sync_db()
    r = client.post("/api/admin/cms/pages", json={"slug": "secret-launch", "title": "Secret",
                                                  "sections": [{"type": "rich_text", "body": "TOP SECRET"}]})
    assert r.status_code == 200
    assert client.get("/api/public/page/secret-launch").status_code == 404

    about = _page_id(db, "about")
    before = client.get("/api/public/page/about").json()
    client.put(f"/api/admin/cms/pages/{about}", json={"title": "DRAFT TITLE",
               "sections": [{"type": "rich_text", "key": "x", "body": "draft only body"}]})
    after = client.get("/api/public/page/about").json()
    assert after == before and "draft only body" not in str(after)       # edit stays a draft

    client.post(f"/api/admin/cms/pages/{about}/publish")
    live = client.get("/api/public/page/about").json()
    assert live["title"] == "DRAFT TITLE"
    assert set(live) <= {"slug", "title", "seo", "sections", "published_at", "path"}

    client.post(f"/api/admin/cms/pages/{about}/unpublish")
    assert client.get("/api/public/page/about").status_code == 404


def test_disabled_sections_are_not_public(client):
    db = get_sync_db()
    pid = _page_id(db, "about")
    client.put(f"/api/admin/cms/pages/{pid}", json={"sections": [
        {"type": "page_header", "key": "h", "title": "Visible"},
        {"type": "rich_text", "key": "hidden", "enabled": False, "body": "HIDDEN BODY"}]})
    client.post(f"/api/admin/cms/pages/{pid}/publish")
    assert "HIDDEN BODY" not in client.get("/api/public/page/about").text


def test_cms_text_is_sanitised_and_unsafe_urls_rejected(client):
    db = get_sync_db()
    pid = _page_id(db, "about")
    r = client.put(f"/api/admin/cms/pages/{pid}", json={"sections": [
        {"type": "page_header", "key": "h", "title": "Hi<script>alert(1)</script><b onclick=x>there</b>"}]})
    assert r.status_code == 200
    doc = db["website_pages"].find_one({"_id": db["website_pages"].find_one({"slug": "about"})["_id"]})
    assert doc["sections"][0]["title"] == "Hithere"
    bad = client.put(f"/api/admin/cms/pages/{pid}", json={"sections": [
        {"type": "cta", "cta_primary": {"label": "Go", "url": "javascript:alert(1)"}}]})
    assert bad.status_code == 422
    assert client.put(f"/api/admin/cms/pages/{pid}", json={"versions": []}).status_code == 422
    client.post(f"/api/admin/cms/pages/{pid}/unpublish")
    assert client.put(f"/api/admin/cms/pages/{pid}", json={"status": "published"}).status_code == 200
    assert db["website_pages"].find_one({"slug": "about"})["status"] == "draft"   # only /publish publishes
    assert client.put(f"/api/admin/cms/pages/{pid}", json={"evil": 1}).status_code == 422
    assert client.put(f"/api/admin/cms/pages/{pid}", json={"sections": [{"type": "iframe"}]}).status_code == 422


def test_faq_and_testimonials_expose_only_enabled_whitelisted_fields(client):
    client.post("/api/admin/cms/faq", json={"question": "Hidden?", "answer": "Not shown", "enabled": False})
    client.post("/api/admin/cms/testimonials", json={"quote": "Great tool", "author_name": "Ana",
                                                     "company": "Acme", "rating": 5})
    client.post("/api/admin/cms/testimonials", json={"quote": "Draft quote", "author_name": "Bo", "enabled": False})
    faq = client.get("/api/public/faq").json()["faq"]
    assert all("Hidden?" != f["question"] for f in faq)
    assert all(set(f) == {"id", "question", "answer", "category"} for f in faq)
    ts = client.get("/api/public/testimonials").json()["testimonials"]
    assert [t["author_name"] for t in ts] == ["Ana"]
    assert set(ts[0]) == {"id", "quote", "author_name", "author_title", "company", "avatar_url", "rating"}
    assert client.post("/api/admin/cms/testimonials", json={"quote": "x"}).status_code == 422


def test_settings_public_subset_and_validation(client):
    db = get_sync_db()
    db["website_settings"].update_one({"key": "ga_id"}, {"$set": {"value": "G-INTERNAL"}})
    svc.clear_cache()
    pub = client.get("/api/public/settings").json()
    assert "ga_id" not in pub and "G-INTERNAL" not in str(pub)
    assert client.put("/api/admin/cms/settings", json={"made_up": "x"}).status_code == 422
    assert client.put("/api/admin/cms/settings", json={"logo_url": "javascript:alert(1)"}).status_code == 422
    assert client.put("/api/admin/cms/settings", json={"contact_email": "nope"}).status_code == 422
    r = client.put("/api/admin/cms/settings", json={"announcement_enabled": True,
                                                    "announcement_text": "Launch <b>week</b>!"})
    assert r.status_code == 200
    pub = client.get("/api/public/settings").json()
    assert pub["announcement_enabled"] is True and pub["announcement_text"] == "Launch week!"
    assert db["audit_logs"].find_one({"action": "cms.settings.update"})


def test_cms_mutations_are_audited(client):
    db = get_sync_db()
    pid = _page_id(db, "terms")
    client.put(f"/api/admin/cms/pages/{pid}", json={"title": "Terms"})
    client.post(f"/api/admin/cms/pages/{pid}/publish")
    tid = client.post("/api/admin/cms/testimonials", json={"quote": "q", "author_name": "a"}).json()["id"]
    client.delete(f"/api/admin/cms/testimonials/{tid}")
    actions = {d["action"] for d in db["audit_logs"].find({})}
    assert {"cms.page.update", "cms.page.publish", "cms.testimonial.create",
            "cms.testimonial.delete"} <= actions


def test_media_upload_rejects_svg_and_mismatched_content(client):
    svg = io.BytesIO(b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>")
    assert client.post("/api/admin/cms/media/upload",
                       files={"file": ("x.svg", svg, "image/svg+xml")}).status_code == 400
    fake = io.BytesIO(b"<html><script>alert(1)</script></html>")
    assert client.post("/api/admin/cms/media/upload",
                       files={"file": ("x.html", fake, "image/png")}).status_code == 400


# ── Pricing = the plan collection ───────────────────────────────────────────

def test_pricing_reflects_plan_catalog_and_hides_non_public_plans(client):
    from app.billing import plan_admin
    db = get_async_db()
    created = run(plan_admin.create_plan(db, {"name": "Growth X", "slug": "growth-x", "price_monthly": 49,
                                              "price_yearly": 490, "currency": "eur",
                                              "limits": {"monthly_searches": 25}, "features": ["csv_export"]},
                                         actor=ADMIN))
    run(plan_admin.create_plan(db, {"name": "Internal", "slug": "internal-only", "price_monthly": 1,
                                    "is_public": False}, actor=ADMIN))
    run(plan_admin.create_plan(db, {"name": "Old", "slug": "old-plan", "status": "inactive"}, actor=ADMIN))

    plans = {p["slug"]: p for p in client.get("/api/public/pricing").json()["plans"]}
    assert "internal-only" not in plans and "old-plan" not in plans
    gx = plans["growth-x"]
    assert (gx["price_monthly"], gx["price_yearly"], gx["currency"]) == (49, 490, "EUR")
    assert "25 searches / month" in gx["highlights"]
    assert not {"id", "_id", "is_public", "created_at", "updated_at", "status"} & set(gx)

    run(plan_admin.update_plan(db, created["id"], {"price_monthly": 59, "name": "Growth Y"}, actor=ADMIN))
    gx = {p["slug"]: p for p in client.get("/api/public/pricing").json()["plans"]}["growth-x"]
    assert gx["price_monthly"] == 59 and gx["name"] == "Growth Y"

    run(plan_admin.update_plan(db, created["id"], {"is_public": False}, actor=ADMIN))
    assert "growth-x" not in {p["slug"] for p in client.get("/api/public/pricing").json()["plans"]}

    # Exactly the active + public plans of the collection, nothing else
    expected = {p["slug"] for p in get_sync_db()["plans"].find({"status": "active"})
                if p.get("is_public", True)}
    assert {p["slug"] for p in client.get("/api/public/pricing").json()["plans"]} == expected


# ── Contact form ────────────────────────────────────────────────────────────

GOOD = {"name": "Jane Buyer", "email": "jane@example.com", "company": "Acme",
        "topic": "sales", "message": "We'd like pricing for 10 seats please."}


def test_contact_validation(client):
    r = client.post("/api/public/contact", json={**GOOD, "email": "bad", "message": "short"})
    assert r.status_code == 422
    errs = r.json()["detail"]["errors"]
    assert {"email", "message"} <= set(errs)
    assert client.post("/api/public/contact", json={**GOOD, "topic": "hack"}).status_code == 422
    assert client.post("/api/public/contact", content=b"not json",
                       headers={"content-type": "application/json"}).status_code == 400
    assert get_sync_db()["contact_submissions"].count_documents({}) == 0


def test_contact_stores_notifies_audits_and_never_leaks(client):
    db = get_sync_db()
    r = client.post("/api/public/contact", json={**GOOD, "message": "Hello <script>x</script> team, call me"})
    assert r.status_code == 200 and r.json()["success"]
    sub = db["contact_submissions"].find_one({})
    assert sub["email"] == "jane@example.com" and "<script>" not in sub["message"] and sub["topic"] == "sales"
    note = db["notifications"].find_one({"type": "contact_message"})
    assert note and note["audience"] == "super_admin" and note["data"]["submission_id"] == r.json()["id"]
    assert db["audit_logs"].find_one({"action": "website.contact_submitted"})
    # no public read path for submissions
    assert client.get("/api/public/contact").status_code == 405
    for path in ("/api/public/settings", "/api/public/page/contact", "/api/public/navigation", "/api/public/faq"):
        assert "jane@example.com" not in client.get(path).text
    # admin list is searchable + counts
    listing = client.get("/api/admin/cms/contact-submissions", params={"q": "jane"}).json()
    assert listing["total"] == 1 and listing["unread"] == 1
    sid = listing["items"][0]["_id"]
    assert client.patch(f"/api/admin/cms/contact-submissions/{sid}/read").status_code == 200
    assert client.get("/api/admin/cms/contact-submissions", params={"read": False}).json()["total"] == 0


def test_contact_honeypot_and_rate_limit(client):
    db = get_sync_db()
    r = client.post("/api/public/contact", json={**GOOD, "website": "http://spam"})
    assert r.status_code == 200 and db["contact_submissions"].count_documents({}) == 0
    # invalid attempts don't burn the quota
    for _ in range(5):
        client.post("/api/public/contact", json={**GOOD, "message": "x"})
    for i in range(public_website.CONTACT_MAX_PER_IP):
        assert client.post("/api/public/contact", json={**GOOD, "email": f"p{i}@example.com"}).status_code == 200
    blocked = client.post("/api/public/contact", json={**GOOD, "email": "other@example.com"})
    assert blocked.status_code == 429
    assert db["contact_submissions"].count_documents({}) == public_website.CONTACT_MAX_PER_IP


# ── SEO: sitemap, robots, server-rendered meta ──────────────────────────────

def test_sitemap_lists_only_published_indexable_pages(client):
    db = get_sync_db()
    client.post("/api/admin/cms/pages", json={"slug": "draft-page", "title": "Draft"})
    client.post(f"/api/admin/cms/pages/{_page_id(db, 'terms')}/unpublish")
    pid = _page_id(db, "faq")
    client.put(f"/api/admin/cms/pages/{pid}", json={"seo": {"title": "FAQ", "robots": "noindex,follow"}})
    client.post(f"/api/admin/cms/pages/{pid}/publish")
    xml = client.get("/api/public/sitemap.xml").text
    assert "/draft-page" not in xml and "/terms" not in xml and "/faq<" not in xml
    assert "/privacy" in xml and "/website" in xml and "/pricing" in xml
    client.put("/api/admin/cms/settings", json={"site_url": "https://www.example.com/"})
    assert "<loc>https://www.example.com/privacy</loc>" in client.get("/api/public/sitemap.xml").text
    robots = client.get("/api/public/robots.txt").text
    assert "Sitemap: https://www.example.com/sitemap.xml" in robots and "Disallow: /api/" in robots


def test_server_rendered_meta_is_escaped_and_from_cms(client):
    db = get_sync_db()
    pid = _page_id(db, "home")
    client.put(f"/api/admin/cms/pages/{pid}", json={"seo": {
        "title": 'Best "leads" </title><script>x</script>', "description": "Find buyers & more"}})
    client.post(f"/api/admin/cms/pages/{pid}/publish")
    html = client.get("/website").text
    assert "<title>Best &quot;leads&quot;</title>" in html
    assert "<script>x</script>" not in html
    assert 'content="Find buyers &amp; more"' in html
    assert 'rel="canonical"' in html and "og:title" in html
    # unpublished page → noindex
    client.post(f"/api/admin/cms/pages/{_page_id(db, 'privacy')}/unpublish")
    assert 'name="robots" content="noindex,follow"' in client.get("/privacy").text


def test_public_endpoints_open_through_main_app_auth_gate():
    from app.main import app
    run(seed_cms_defaults(get_async_db()))
    c = TestClient(app)
    for path in ("/api/public/settings", "/api/public/navigation", "/api/public/page/home",
                 "/api/public/pricing", "/api/public/faq", "/api/public/testimonials",
                 "/api/public/sitemap.xml", "/api/public/robots.txt"):
        assert c.get(path).status_code == 200, path
    assert c.get("/api/admin/cms/pages").status_code in (401, 403)
