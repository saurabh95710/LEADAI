"""
One brand for the whole platform: Super Admin → Global Settings (General,
Branding) drives the public website too; image settings can't be page links;
garbled (double-encoded) settings text is repaired;
logos saved as a page address are cleared; website data revalidates.

Run:  python -m pytest tests/test_brand_everywhere.py -q -p no:cacheprovider
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def env():
    with patch("app.admin.settings.is_maintenance_enabled", return_value=False):
        from app.main import app
        with TestClient(app) as client:
            from app.db.mongo import get_sync_db
            yield client, get_sync_db()


def test_website_takes_the_brand_from_global_settings(env):
    from app.admin.settings import clear_settings_cache, set_setting
    client, db = env
    set_setting("general.app.name", "Acme Leads", by="test")
    set_setting("general.app.tagline", "Buyers, found", by="test")
    set_setting("branding.logo_primary", "/static/uploads/branding/acme.png", by="test")
    set_setting("branding.favicon", "/static/uploads/branding/acme.ico", by="test")
    clear_settings_cache()
    s = client.get("/api/public/settings").json()
    assert s["brand_name"] == "Acme Leads" and s["tagline"] == "Buyers, found"
    assert s["logo_url"] == "/static/uploads/branding/acme.png" and s["favicon_url"].endswith("acme.ico")
    page = client.get("/").text                                   # server-rendered title uses it too
    assert "Acme Leads" in page


def test_a_page_link_is_never_used_as_a_logo(env):
    from app.cms.service import _image_ref
    assert _image_ref("http://127.0.0.1:8000/superadmin#/settings?tab=branding") == ""
    assert _image_ref("/static/uploads/branding/x.png") == "/static/uploads/branding/x.png"
    assert _image_ref("javascript:alert(1)") == ""


def test_image_settings_reject_page_addresses():
    from app.settings.registry import SETTINGS, validate_value
    spec = next(s for s in SETTINGS if s.key == "branding.logo_light")
    _, err = validate_value(spec, "https://leadorbit.example/superadmin#/settings?tab=branding")
    assert err and "image" in err
    _, err = validate_value(spec, "/static/uploads/branding/dark.png")
    assert err is None


def test_garbled_settings_text_is_repaired_once(env):
    from app.admin.settings import fix_mojibake, repair_mojibake_settings
    client, db = env
    assert fix_mojibake("AI â€” leads") == "AI — leads"
    assert fix_mojibake("Protected Â· LeadAI Â© 2026") == "Protected · LeadAI © 2026"
    assert fix_mojibake("café au lait") == "café au lait"            # correct text is left alone
    db.system_settings.update_one({"_id": "general.app.description"},
                                  {"$set": {"value": "AI â€” leads"}}, upsert=True)
    assert repair_mojibake_settings(db) >= 1
    assert db.system_settings.find_one({"_id": "general.app.description"})["value"] == "AI — leads"
    assert repair_mojibake_settings(db) == 0


def test_unusable_stored_settings_are_cleaned_once(env):
    from app.admin.settings import clean_stored_settings
    client, db = env
    page = "http://127.0.0.1:8000/superadmin#/settings?tab=branding"
    db.system_settings.update_one({"_id": "branding.logo_light"}, {"$set": {"value": page}}, upsert=True)
    db.system_settings.update_one({"_id": "branding.favicon"}, {"$set": {"value": page}}, upsert=True)
    db.system_settings.update_one({"_id": "branding.logo_primary"},
                                  {"$set": {"value": "/static/uploads/branding/ok.png"}}, upsert=True)
    db.system_settings.update_one({"_id": "6a82dc8d1ef0eddab246d839"}, {"$set": {"value": False}}, upsert=True)
    db.system_settings.update_one({"_id": "apify.last_test_ok"}, {"$set": {"value": True}}, upsert=True)
    assert clean_stored_settings(db) == 3
    left = {d["_id"] for d in db.system_settings.find({}, {"_id": 1})}
    assert "branding.logo_light" not in left and "branding.favicon" not in left
    assert "6a82dc8d1ef0eddab246d839" not in left
    assert {"branding.logo_primary", "apify.last_test_ok"} <= left      # real values are kept
    assert clean_stored_settings(db) == 0
    data = client.get("/api/public/settings").json()
    assert "#" not in (data.get("favicon_url") or "")


def test_public_website_data_revalidates(env):
    client, db = env
    cc = client.get("/api/public/settings").headers.get("cache-control", "")
    assert "public" in cc and "no-cache" in cc


def test_public_config_never_sends_a_page_link_as_an_image():
    import asyncio
    from app.settings.registry import build_public_config
    page = "http://127.0.0.1:8000/superadmin#/settings?tab=branding"
    cfg = asyncio.run(build_public_config(all_settings={
        "branding.favicon": page, "branding.logo_primary": "/static/uploads/branding/ok.png",
        "seo.og_image": page}))
    assert cfg["branding"]["favicon"] == "" and cfg["seo"]["og_image"] == ""
    assert cfg["branding"]["logo_primary"] == "/static/uploads/branding/ok.png"
