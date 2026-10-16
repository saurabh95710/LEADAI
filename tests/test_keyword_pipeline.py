"""
Keyword-search pipeline (app/discovery/pipeline.py) with mocked adapters —
no real Apify call is ever made.

Proves: discovery dedupe across keywords; enrichment failures are dropped
before sampling (their posts are never fetched); post metrics; the deep scrape
stores pages / posts / comments and calls analyze_comments_for_post; a
per-candidate Apify failure doesn't fail the run; a platform whose discovery
fails entirely fails alone; resume after a crash skips finished stages; a
cache hit avoids Apify and the charge; tokens per stage and a graceful stop
when tokens run out; an organization on its own (missing) Apify key never
falls back to LeadAI's key; cancellation.

Run:  python -m pytest tests/test_keyword_pipeline.py -q -p no:cacheprovider
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.connectors.apify_connector import ScrapeError
from app.discovery import pipeline as PL
from app.discovery.adapters.base import DiscoveryAdapter, empty_profile, post_entry
from app.services.tenant_api_keys import resolve_api_key
from tests.conftest import seed_site_account

NOW = datetime.now(timezone.utc)


def _posts(n, comments, likes=50, platform="instagram", days_ago=2):
    out = []
    for i in range(n):
        url = (f"https://www.instagram.com/p/P{i}x/" if platform == "instagram"
               else f"https://www.facebook.com/page/posts/{i}")
        raw = {"id": f"{platform}{i}", "url": url, "commentsCount": comments, "likesCount": likes,
               "likes": likes, "comments": comments,
               "timestamp": (NOW - timedelta(days=days_ago + i)).isoformat()}
        out.append(post_entry(id=raw["id"], url=url, comments=comments, likes=likes,
                              posted_at=raw["timestamp"], raw=raw))
    return out


def _prof(platform, pid, followers, posts_count=None, recent=None, **kw):
    p = empty_profile(platform)
    url = (f"https://www.instagram.com/{pid}" if platform == "instagram" else f"https://www.facebook.com/{pid}")
    p.update({"profile_id": pid, "url": url, "handle": pid, "name": pid.replace("_", " ").title(),
              "followers": followers, "posts_count": posts_count, "bio": "Bakery in Pune",
              "recent_posts": recent or [], "private": False})
    p.update(kw)
    return p


class FakeAdapter(DiscoveryAdapter):
    """Records every call; results come from the dicts given."""

    def __init__(self, platform, by_keyword, enrich=None, posts=None, fail_discover=False,
                 fail_posts=(), on_discover=None):
        super().__init__(connector=None)
        self.platform = platform
        self.by_keyword = by_keyword
        self.enrich_map = enrich or {}
        self.posts_map = posts or {}
        self.fail_discover = fail_discover
        self.fail_posts = set(fail_posts)
        self.on_discover = on_discover
        self.calls = {"discover": [], "enrich": [], "recent_posts": []}

    def discover(self, keyword, limit, filters=None):
        self.calls["discover"].append(keyword)
        self.calls.setdefault("filters", []).append(filters)
        self.last_query = {"platform": self.platform, "keyword": keyword,
                           "query_text": f"{keyword} q", "locations": [], "extra_inputs": {}}
        if self.on_discover:
            self.on_discover(keyword)
        if self.fail_discover:
            raise ScrapeError("ACTOR_FAILED", "Apify actor failed: boom")
        return [dict(p) for p in self.by_keyword.get(keyword, [])]

    def needs_enrichment(self, profile):
        return profile.get("followers") is None

    def enrich(self, profiles):
        self.calls["enrich"].append([p["profile_id"] for p in profiles])
        return {p["profile_id"]: ({**p, **self.enrich_map[p["profile_id"]]} if p["profile_id"] in self.enrich_map
                                  else None) for p in profiles}

    def recent_posts(self, profile, n):
        self.calls["recent_posts"].append(profile["profile_id"])
        if profile["profile_id"] in self.fail_posts:
            raise ScrapeError("ACTOR_FAILED", "Apify actor failed: posts")
        return self.posts_map.get(profile["profile_id"], [])[:n]


def make_adapters(**over):
    ig_a = _prof("instagram", "ig_a", 5000, 100, recent=_posts(12, 5))
    ig_b = _prof("instagram", "ig_b", 100, 50, recent=_posts(12, 9))
    ig_c = _prof("instagram", "ig_c", 9000, 80, private=True)
    ig_d = _prof("instagram", "ig_d", 3000, 50, recent=_posts(3, 3))
    fb_x = _prof("facebook", "fb_x", None)
    fb_y = _prof("facebook", "fb_y", None)
    fb_z = _prof("facebook", "fb_z", 50)
    ig = FakeAdapter("instagram", {"bakery": [ig_a, ig_b, ig_c], "cakes": [ig_a, ig_d]},
                     posts={"ig_d": _posts(10, 3)}, **over.get("instagram", {}))
    fb = FakeAdapter("facebook", {"bakery": [fb_x, fb_y, fb_z], "cakes": [fb_x]},
                     enrich={"fb_x": {"followers": 20000, "enriched": True}},
                     posts={"fb_x": _posts(10, 4, platform="facebook")}, **over.get("facebook", {}))
    return {"instagram": ig, "facebook": fb}


@pytest.fixture
def env():
    from app.db.mongo import get_sync_db
    db = get_sync_db()
    uid, org = seed_site_account("owner@bakery.example", db=db, org_name="Bakery Co")
    db.token_balances.insert_one({"organization_id": org, "allocated": 1000, "used": 0, "remaining": 1000,
                                  "source": "plan"})
    analyzed = []

    def fake_analyze(post_ref, max_comments=500, comment_refs=None, filter_summary=None):
        analyzed.append(post_ref)
        return {"status": "ok"}

    from app.social.scrapers import get_scraper as real_get_scraper

    def fake_get_scraper(platform, connector=None, organization_id=None):
        sc = real_get_scraper(platform, connector=object())
        sc.fetch_comments = lambda url, n, should_abort=None: [
            {"id": f"{url}-c1", "text": "Price please, call 9876543210", "ownerUsername": "bob"},
            {"id": f"{url}-c2", "text": "Lovely!", "ownerUsername": "amy"}]
        return sc

    with patch("app.services.tenant_api_keys.get_tenant_apify_token_sync", return_value=("tok", "platform")), \
            patch("app.pipeline.comment_ai.analyze_comments_for_post", fake_analyze), \
            patch("app.discovery.pipeline.get_scraper", fake_get_scraper):
        yield {"db": db, "org": org, "uid": uid, "email": "owner@bakery.example", "analyzed": analyzed}


def make_run(env, run_id="KWTEST1", keywords=("bakery", "cakes"), platforms=("instagram", "facebook"),
             filters=None, strict=False, max_candidates=50, stages=None, max_tokens=None):
    filters = filters or {"min_followers": 1000, "min_avg_comments": 2, "recent_posts_to_analyze": 10}
    env["db"].search_history.insert_one({
        "run_id": run_id, "search_type": "keyword", "organization_id": env["org"], "user_id": env["uid"],
        "created_by": env["email"], "status": "running", "phase": "queued",
        "keyword_search": {"keywords": list(keywords), "platforms": list(platforms), "filters": filters,
                           "strict": strict, "max_tokens": max_tokens,
                           "limits": {"max_candidates": max_candidates, "recent_posts": 10,
                                      "comments_per_post": 10}},
        "stages": stages or {s: "pending" for s in PL.STAGES},
        "tokens_charged": {s: 0 for s in PL.STAGES}, "platform_errors": {}, "discovery_pairs_done": [],
        "created_at": NOW})
    return run_id


def run(env, adapters, run_id="KWTEST1"):
    with patch("app.discovery.pipeline.get_adapter", lambda platform, connector, should_abort=None: adapters[platform]):
        return PL.run_keyword_search(run_id)


def cands(env, run_id="KWTEST1"):
    return {c["profile_id"]: c for c in env["db"].discovery_candidates.find({"search_run_id": run_id})}


def remaining(env):
    return env["db"].token_balances.find_one({"organization_id": env["org"]})["remaining"]


# ═══════════════════════════════════════════════════════════════════════════

def test_full_staged_run(env):
    db = env["db"]
    ad = make_adapters()
    make_run(env)
    res = run(env, ad)
    assert res["status"] == "completed", res
    c = cands(env)
    # dedupe across keywords: ig_a and fb_x found twice, stored once with both keywords
    assert set(c) == {"ig_a", "ig_b", "ig_c", "ig_d", "fb_x", "fb_y", "fb_z"}
    assert sorted(c["ig_a"]["keywords"]) == ["bakery", "cakes"]
    assert sorted(c["fb_x"]["keywords"]) == ["bakery", "cakes"]
    assert ad["instagram"].calls["discover"] == ["bakery", "cakes"]
    # statuses
    assert {k: v["status"] for k, v in c.items()} == {
        "ig_a": "passed", "ig_b": "rejected", "ig_c": "rejected", "ig_d": "passed",
        "fb_x": "passed", "fb_y": "error", "fb_z": "rejected"}
    assert any(r["message"] == "followers 100 < min 1000" for r in c["ig_b"]["reasons"])
    assert any(r["filter"] == "private_account" for r in c["ig_c"]["reasons"])
    # enrichment: only facebook profiles lacking followers; fb_y dropped before sampling
    assert ad["facebook"].calls["enrich"] == [["fb_x", "fb_y"]]
    assert "fb_y" not in ad["facebook"].calls["recent_posts"]
    assert "didn't return this profile's details" in c["fb_y"]["error"]
    # sampling: ig_a had 12 posts from discovery (no call), ig_d needed a fetch, rejected ones never sampled
    assert ad["instagram"].calls["recent_posts"] == ["ig_d"]
    assert ad["facebook"].calls["recent_posts"] == ["fb_x"]
    assert c["ig_a"]["posts_source"] == "discovery" and c["ig_d"]["posts_source"] == "apify"
    assert c["ig_a"]["metrics"]["avg_comments"] == 5 and c["ig_a"]["metrics"]["total_comments"] == 50
    assert c["fb_x"]["metrics"]["followers"] == 20000
    assert c["ig_a"]["metrics"]["engagement_rate"] == pytest.approx((50 + 5) / 5000 * 100)
    # deep scrape: page + posts + comments + AI analysis like URL search
    pages = list(db.facebook_pages.find({"search_run_id": "KWTEST1"}))
    assert {p["facebook_url"] for p in pages} == {c[k]["url"] for k in ("ig_a", "ig_d", "fb_x")}
    for p in pages:
        assert p["source"] == "keyword_search" and p["organization_id"] == env["org"]
        assert p["user_id"] == env["uid"] and p["platform"] in ("instagram", "facebook")
    assert c["ig_a"]["page_ref"] and c["ig_a"]["stage_reached"] == "scraped"
    posts = list(db.facebook_posts.find({"search_run_id": "KWTEST1"}))
    assert len(posts) == 30 and all(p["organization_id"] == env["org"] for p in posts)
    comments = db.facebook_comments.count_documents({"search_run_id": "KWTEST1"})
    assert comments == 60
    assert db.facebook_comments.count_documents({"search_run_id": "KWTEST1", "has_contact": True}) == 30
    assert len(env["analyzed"]) == 30
    # tokens per stage actually executed: 4 discover x5, 2 enrich x1, 2 samples x1, 3 collect x5
    sh = db.search_history.find_one({"run_id": "KWTEST1"})
    # fb_y's enrichment share came back (nothing was returned for it)
    assert sh["tokens_charged"] == {"discovery": 20, "enrichment": 1, "sampling": 2, "deep_scrape": 15}
    assert remaining(env) == 1000 - 38
    assert sh["stages"] == {s: "done" for s in PL.STAGES}
    assert sh["stage_counts"]["passed"] == 3 and sh["stage_counts"]["discovered"] == 7
    assert sh["stage_counts"]["comments_scraped"] == 60 and sh["status"] == "completed"


def test_per_candidate_apify_failure_does_not_fail_the_run(env):
    ad = make_adapters(instagram={"fail_posts": ["ig_d"]})
    make_run(env)
    res = run(env, ad)
    assert res["status"] == "completed"
    c = cands(env)
    assert c["ig_d"]["status"] == "error" and "reading recent posts" in c["ig_d"]["error"]
    assert c["ig_a"]["status"] == "passed"
    sh = env["db"].search_history.find_one({"run_id": "KWTEST1"})
    assert sh["tokens_charged"]["sampling"] == 1          # ig_d's sample was refunded
    assert "instagram" not in sh["platform_errors"]


def test_platform_discovery_failure_fails_only_that_platform(env):
    ad = make_adapters(facebook={"fail_discover": True})
    make_run(env)
    res = run(env, ad)
    assert res["status"] == "completed"
    sh = env["db"].search_history.find_one({"run_id": "KWTEST1"})
    assert sh["platform_errors"]["facebook"]["stage"] == "discovery"
    assert sh["tokens_charged"]["discovery"] == 10          # facebook's 2 discover charges refunded
    assert {c["platform"] for c in cands(env).values()} == {"instagram"}
    assert "Facebook stopped while finding profiles" in sh["message"]


def test_all_platforms_failing_fails_the_run(env):
    ad = make_adapters(facebook={"fail_discover": True}, instagram={"fail_discover": True})
    make_run(env)
    res = run(env, ad)
    assert res["status"] == "error"
    sh = env["db"].search_history.find_one({"run_id": "KWTEST1"})
    assert sh["status"] == "error" and sh["stages"]["discovery"] == "failed"
    assert remaining(env) == 1000


def test_resume_after_crash_skips_done_stages(env):
    ad = make_adapters()
    make_run(env)
    # first run dies after discovery (simulated by stopping before enrichment)
    def worker_died(r):
        raise RuntimeError("worker died")

    with patch.dict(PL.STAGE_FUNCS, {"enrichment": worker_died}):
        with pytest.raises(RuntimeError):
            run(env, ad)
    sh = env["db"].search_history.find_one({"run_id": "KWTEST1"})
    assert sh["stages"]["discovery"] == "done" and sh["stages"]["enrichment"] == "running"
    assert len(ad["instagram"].calls["discover"]) == 2
    # the queue worker resumes it: discovery is not run (or charged) again
    res = run(env, ad)
    assert res["status"] == "completed"
    assert len(ad["instagram"].calls["discover"]) == 2 and len(ad["facebook"].calls["discover"]) == 2
    sh = env["db"].search_history.find_one({"run_id": "KWTEST1"})
    assert sh["tokens_charged"]["discovery"] == 20
    assert cands(env)["ig_a"]["status"] == "passed"


def test_cache_hit_skips_apify_and_charge(env):
    from app.discovery import cache
    db = env["db"]
    cached = _prof("facebook", "fb_x", 20000)
    cache.put_profile(db, cached)
    posts = _posts(10, 4, platform="facebook")
    clean = [{k: v for k, v in p.items() if k != "raw"} for p in posts]
    cache.put_posts(db, "facebook", "fb_x", clean, [p["raw"] for p in posts], 10)
    ad = make_adapters()
    make_run(env)
    run(env, ad)
    assert ad["facebook"].calls["enrich"] == [["fb_y"]]          # fb_x came from the cache
    assert "fb_x" not in ad["facebook"].calls["recent_posts"]
    c = cands(env)
    assert c["fb_x"]["status"] == "passed" and c["fb_x"]["posts_source"] == "cache"
    sh = db.search_history.find_one({"run_id": "KWTEST1"})
    # fb_x free (cache); fb_y charged then refunded (actor returned nothing for it)
    assert sh["tokens_charged"]["enrichment"] == 0 and sh["tokens_charged"]["sampling"] == 1


def test_expired_cache_is_not_used(env):
    from app.discovery import cache
    db = env["db"]
    cache.put_profile(db, _prof("facebook", "fb_x", 20000))
    db.discovery_profile_cache.update_one({"profile_id": "fb_x"}, {"$set": {
        "profile_expires_at": datetime.now(timezone.utc) - timedelta(hours=1)}})
    ad = make_adapters()
    make_run(env)
    run(env, ad)
    assert ad["facebook"].calls["enrich"] == [["fb_x", "fb_y"]]


def test_stop_gracefully_when_tokens_run_out(env):
    db = env["db"]
    db.token_balances.update_one({"organization_id": env["org"]}, {"$set": {"remaining": 21, "allocated": 21}})
    ad = make_adapters()
    make_run(env)
    res = run(env, ad)
    sh = db.search_history.find_one({"run_id": "KWTEST1"})
    assert res["phase"] == "stopped_tokens"
    assert sh["status"] == "completed" and sh["phase"] == "stopped_tokens" and "tokens" in sh["message"]
    assert sh["stages"]["discovery"] == "done" and sh["stages"]["enrichment"] == "stopped"
    assert sh["tokens_charged"]["discovery"] == 20 and sh["tokens_charged"]["enrichment"] == 0
    assert len(cands(env)) == 7                                  # results so far are kept
    assert ad["facebook"].calls["enrich"] == []                  # never called without paying
    assert remaining(env) == 1


def test_token_budget_stops_the_run(env):
    ad = make_adapters()
    make_run(env, max_tokens=22)
    res = run(env, ad)
    assert res["phase"] == "stopped_budget"
    sh = env["db"].search_history.find_one({"run_id": "KWTEST1"})
    assert sum(sh["tokens_charged"].values()) <= 22


def test_own_apify_key_missing_never_uses_platform_key(env):
    """BYOK: own key chosen but missing -> the run fails; LeadAI's key is never used."""
    from bson import ObjectId

    from app.config import get_settings
    from app.discovery.adapters import get_adapter as real_get_adapter
    db = env["db"]
    db.organizations.update_one({"_id": ObjectId(env["org"])}, {"$set": {"api_coverage": {"apify": "own"}}})
    get_settings().apify_api_token = "PLATFORM-TOKEN-MUST-NOT-BE-USED"
    make_run(env)
    clients = []
    real_resolve = lambda org: resolve_api_key(org, "apify")  # noqa: E731 — the unpatched resolver
    with patch("app.connectors.apify_connector.ApifyConnector._call_actor",
               side_effect=AssertionError("Apify must not be called")) as call_actor, \
            patch("apify_client.ApifyClient", side_effect=lambda token, *a, **k: clients.append(token)), \
            patch("app.services.tenant_api_keys.get_tenant_apify_token_sync", side_effect=real_resolve), \
            patch("app.discovery.pipeline.get_adapter", real_get_adapter):
        res = PL.run_keyword_search("KWTEST1")
    assert res["status"] == "error"
    sh = db.search_history.find_one({"run_id": "KWTEST1"})
    assert "own" in sh["error"].lower() and "apify" in sh["error"].lower()
    assert not call_actor.called and clients == []
    assert remaining(env) == 1000 and db.discovery_candidates.count_documents({}) == 0
    # and the connector itself refuses (no silent fallback) when used directly
    from app.connectors.apify_connector import ApifyConnector, ApifyError
    with patch("app.services.tenant_api_keys.get_tenant_apify_token_sync", side_effect=real_resolve), \
            patch("apify_client.ApifyClient", side_effect=lambda token, *a, **k: clients.append(token)):
        conn = ApifyConnector(organization_id=env["org"])
        assert conn.token != "PLATFORM-TOKEN-MUST-NOT-BE-USED"
        with pytest.raises(ApifyError):
            real_get_adapter("instagram", conn).discover("bakery", 5)
    assert clients == []


def test_cancellation_between_candidates(env):
    db = env["db"]

    def cancel_after_first(keyword):
        if keyword == "cakes":
            db.search_history.update_one({"run_id": "KWTEST1"}, {"$set": {"cancel_requested": True}})

    ad = make_adapters(instagram={"on_discover": cancel_after_first})
    make_run(env)
    res = run(env, ad)
    assert res["status"] == "cancelled"
    sh = db.search_history.find_one({"run_id": "KWTEST1"})
    assert sh["status"] == "cancelled"
    assert ad["facebook"].calls["discover"] == []                # stopped before the next pair
    assert ad["facebook"].calls["enrich"] == [] and db.facebook_pages.count_documents({}) == 0


def test_strict_mode_rejects_unknown_values(env):
    ad = make_adapters()
    make_run(env, platforms=("facebook",), filters={"min_followers": 1000, "min_posts": 1,
                                                    "recent_posts_to_analyze": 10}, strict=True)
    run(env, ad)
    c = cands(env)
    assert c["fb_x"]["status"] == "rejected"                      # min_posts unsupported on facebook
    assert c["fb_x"]["unsupported"] == ["min_posts"]
    make_run(env, run_id="KWTEST2", platforms=("facebook",),
             filters={"min_followers": 1000, "min_posts": 1, "recent_posts_to_analyze": 10}, strict=False)
    run(env, make_adapters(), run_id="KWTEST2")
    c2 = cands(env, "KWTEST2")
    assert c2["fb_x"]["status"] == "passed" and c2["fb_x"]["unsupported"] == ["min_posts"]


def test_candidate_cap_across_platforms(env):
    ad = make_adapters()
    make_run(env, max_candidates=3)
    run(env, ad)
    assert len(cands(env)) == 3


# ── adapters: actor items -> normalized profiles (fake connector) ───────────

class FakeConnector:
    def __init__(self, items):
        self.items = items
        self.calls = []

    def scrape_actor(self, actor_id, run_input, label, keyword=None, should_abort=None):
        self.calls.append((actor_id, run_input, label))
        return self.items.get(label, [])

    def scrape_facebook_pages_by_urls(self, urls, should_abort=None):
        self.calls.append(("pages", urls, "pages"))
        return self.items.get("pages", [])

    def scrape_facebook_posts(self, urls, posts_per_page=20, should_abort=None):
        self.calls.append(("posts", urls, "posts"))
        return self.items.get("posts", [])


def test_instagram_adapter_maps_search_items(env):
    from app.discovery.adapters import get_adapter
    item = {"username": "AcmeBakes", "fullName": "Acme Bakes", "biography": "Cakes! hello@acme.in +91 98765 43210",
            "externalUrl": "https://acme.in", "followersCount": 5400, "postsCount": 210,
            "isBusinessAccount": True, "businessCategoryName": "Bakery", "verified": False, "private": False,
            "latestPosts": [{"id": "1", "shortCode": "AbC", "url": "https://www.instagram.com/p/AbC/",
                             "commentsCount": 7, "likesCount": -1, "timestamp": "2026-10-10T10:00:00.000Z"}]}
    bad = {"username": "x/y"}           # not a valid profile URL -> dropped
    conn = FakeConnector({"keyword-instagram-search": [item, bad]})
    profiles = get_adapter("instagram", conn).discover("bakery", 10)
    assert len(profiles) == 1
    p = profiles[0]
    assert p["url"] == "https://www.instagram.com/AcmeBakes" and p["profile_id"] == "acmebakes"
    assert p["followers"] == 5400 and p["posts_count"] == 210 and p["is_business"] is True
    assert p["contacts"]["emails"] == ["hello@acme.in"] and "https://acme.in" in p["contacts"]["websites"]
    assert p["contacts"]["phones"] and p["country"] is None
    assert p["recent_posts"][0]["comments_count"] == 7 and p["recent_posts"][0]["likes_count"] is None
    assert p["last_post_at"].startswith("2026-10-10")
    assert conn.calls[0][1] == {"search": "bakery", "searchType": "user", "searchLimit": 10}


def test_facebook_adapter_enrich_matches_by_url(env):
    from app.discovery.adapters import get_adapter
    conn = FakeConnector({
        "keyword-facebook-search": [{"pageId": "123", "pageUrl": "https://www.facebook.com/acmebakery",
                                     "title": "Acme Bakery", "categories": ["Bakery"], "followers": None},
                                    {"url": "https://www.facebook.com/groups/123"}],
        "pages": [{"facebookUrl": "https://www.facebook.com/acmebakery", "followers": 12000,
                   "email": "info@acme.in", "phone": "+91 98765 43210", "title": "Acme Bakery"}],
    })
    ad = get_adapter("facebook", conn)
    found = ad.discover("bakery", 5)
    assert [p["profile_id"] for p in found] == ["123"] and ad.needs_enrichment(found[0])
    enriched = ad.enrich(found)["123"]
    assert enriched["followers"] == 12000 and "info@acme.in" in enriched["contacts"]["emails"]
    assert enriched["is_business"] is True and enriched["posts_count"] is None


def test_youtube_adapter_dedupes_channels_from_video_search(env):
    from app.discovery.adapters import get_adapter
    vids = [{"id": "v1", "channelId": "UCaaaaaaaaaaaaaaaaaaaaaa", "channelName": "Bake TV",
             "channelUrl": "https://www.youtube.com/@baketv", "numberOfSubscribers": 3000},
            {"id": "v2", "channelId": "UCaaaaaaaaaaaaaaaaaaaaaa", "channelName": "Bake TV",
             "channelUrl": "https://www.youtube.com/@baketv"},
            {"id": "v3", "channelId": "UCbbbbbbbbbbbbbbbbbbbbbb", "channelName": "Cake Lab",
             "channelUrl": "https://www.youtube.com/channel/UCbbbbbbbbbbbbbbbbbbbbbb"}]
    conn = FakeConnector({"keyword-youtube-search": vids,
                          "keyword-youtube-channel": [{"inputChannelUrl": "https://www.youtube.com/@baketv",
                                                       "numberOfSubscribers": 3100, "channelTotalVideos": 80,
                                                       "isChannelVerified": True, "channelLocation": "India"}]})
    ad = get_adapter("youtube", conn)
    found = ad.discover("baking", 5)
    assert [p["name"] for p in found] == ["Bake TV", "Cake Lab"]
    assert found[0]["followers"] == 3000 and found[0]["posts_count"] is None and ad.needs_enrichment(found[0])
    res = ad.enrich(found)
    assert res["UCaaaaaaaaaaaaaaaaaaaaaa"]["posts_count"] == 80
    assert res["UCaaaaaaaaaaaaaaaaaaaaaa"]["verified"] is True and res["UCbbbbbbbbbbbbbbbbbbbbbb"] is None


def test_linkedin_adapter_maps_company(env):
    from app.discovery.adapters import get_adapter
    conn = FakeConnector({"keyword-linkedin-search": [{
        "name": "Acme Foods", "linkedinUrl": "https://www.linkedin.com/company/acme-foods/",
        "website": "https://acme.example", "followerCount": 4200, "employeeCount": 55,
        "industries": [{"name": "Food Production"}], "headquarter": {"city": "Pune", "countryFull": "India"},
        "description": "We bake."}, {"name": "Person", "linkedinUrl": "https://www.linkedin.com/in/someone"}]})
    found = get_adapter("linkedin", conn).discover("food", 5)
    assert len(found) == 1
    p = found[0]
    assert p["url"] == "https://www.linkedin.com/company/acme-foods" and p["followers"] == 4200
    assert p["country"] == "India" and p["category"] == "Food Production" and p["is_business"] is True


# ═══════════════════════════════════════════════════════════════════════════
# Review regressions
# ═══════════════════════════════════════════════════════════════════════════

class _Crash(BaseException):
    """Simulates the process dying (escapes every ``except Exception``)."""


def _ok_scraper(platform, comments=None, fail=False, on_fetch=None):
    from app.social.scrapers import get_scraper as real
    sc = real(platform, connector=object())
    calls = {"n": 0}

    def fc(url, n, should_abort=None):
        calls["n"] += 1
        if on_fetch:
            on_fetch(url)
        if fail:
            raise RuntimeError("actor down")
        return list(comments or [])
    sc.fetch_comments = fc
    sc._calls = calls
    return sc


def _reset_for_continue(db, run_id="KWTEST1"):
    """What POST /continue does to the run doc before relaunching."""
    db.search_history.update_one({"run_id": run_id}, {"$set": {
        "status": "running", "cancel_requested": False, "completed_at": None,
        "stages.sampling": "pending", "stages.deep_scrape": "pending"}})


async def _noop(*a, **k):
    return None


def test_cancel_then_continue_refused_until_the_worker_finished(env):
    """#1: /cancel flips status at once while the thread still runs; /continue
    (and re-apply) must wait for the worker, then exactly one continue wins."""
    import asyncio
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.db.mongo import get_async_db
    from app.discovery import service as S
    db = env["db"]
    ad = make_adapters()
    make_run(env, platforms=("facebook",), keywords=("bakery",))
    db.job_queue.insert_one({"job_id": "KWTEST1", "run_id": "KWTEST1", "status": "running"})
    ctx = SimpleNamespace(organization_id=env["org"], user_id=env["uid"], email=env["email"])
    launched, refused, kept_scraping = [], [], []

    def attempt_continue():
        run_doc = db.search_history.find_one({"run_id": "KWTEST1"})

        async def go():
            with patch.object(S, "check_access", _noop), \
                    patch.object(S, "launch", lambda rid, c, job_id=None: launched.append(job_id)), \
                    patch("app.api.routes.search._audit", _noop):
                return await S.continue_run(ctx, None, run_doc, SimpleNamespace(confirm=True, max_tokens=None),
                                            db=get_async_db())
        return asyncio.run(go())

    def on_fetch(url):
        if not refused:
            from app.queue.service import cancel_job
            cancel_job("KWTEST1")                              # what POST /cancel does
            try:
                attempt_continue()
            except HTTPException as e:
                refused.append(e.status_code)
            run_doc = db.search_history.find_one({"run_id": "KWTEST1"})
            try:
                S.ensure_finished(db, run_doc)                 # the re-apply guard
            except HTTPException as e:
                refused.append(e.status_code)
        else:
            kept_scraping.append(url)

    with patch("app.discovery.pipeline.get_scraper",
               lambda p, connector=None, organization_id=None: _ok_scraper(p, on_fetch=on_fetch)):
        res = run(env, ad)
    assert refused == [409, 409] and launched == []
    assert res["status"] == "cancelled" and not kept_scraping
    sh = db.search_history.find_one({"run_id": "KWTEST1"})
    assert sh["completed_at"] is not None
    # the worker is done now: one continue wins, a second one is refused (atomic flip)
    assert attempt_continue()["status"] == "running" and launched == ["KWTEST1_C1"]
    with pytest.raises(HTTPException) as exc:
        attempt_continue()
    assert exc.value.status_code == 409


def test_cancel_during_deep_scrape_refunds_collect_and_continue_charges_once(env):
    """#2"""
    db = env["db"]
    ad = make_adapters()
    make_run(env, platforms=("facebook",), keywords=("bakery",))

    def cancel(url):
        db.search_history.update_one({"run_id": "KWTEST1"}, {"$set": {"cancel_requested": True}})
    with patch("app.discovery.pipeline.get_scraper",
               lambda p, connector=None, organization_id=None: _ok_scraper(
                   p, comments=[{"id": "c1", "text": "hi"}], on_fetch=cancel)):
        res = run(env, ad)
    sh = db.search_history.find_one({"run_id": "KWTEST1"})
    c = cands(env)["fb_x"]
    assert res["status"] == "cancelled"
    assert sh["tokens_charged"]["deep_scrape"] == 0 and c.get("deep_status") != "done"
    assert "deep_scrape" not in (c.get("charges") or {})
    _reset_for_continue(db)
    with patch("app.discovery.pipeline.get_scraper",
               lambda p, connector=None, organization_id=None: _ok_scraper(p)):
        run(env, ad)
    sh = db.search_history.find_one({"run_id": "KWTEST1"})
    assert sh["tokens_charged"]["deep_scrape"] == 5 and cands(env)["fb_x"]["deep_status"] == "done"


def test_collect_not_charged_when_nothing_is_fetched(env):
    """#3a: a passed profile with no sampled posts never calls Apify -> free."""
    fb = FakeAdapter("facebook", {"bakery": [_prof("facebook", "fb_x", 20000)]}, posts={"fb_x": []})
    make_run(env, platforms=("facebook",), keywords=("bakery",),
             filters={"min_followers": 1000, "recent_posts_to_analyze": 10})
    sc = {}
    with patch("app.discovery.pipeline.get_scraper",
               lambda p, connector=None, organization_id=None: sc.setdefault(p, _ok_scraper(p))):
        run(env, {"facebook": fb})
    sh = env["db"].search_history.find_one({"run_id": "KWTEST1"})
    c = cands(env)["fb_x"]
    assert c["status"] == "passed" and c["deep_status"] == "done"
    assert sc["facebook"]._calls["n"] == 0 and sh["tokens_charged"]["deep_scrape"] == 0


def test_collect_refunded_when_every_comment_fetch_fails_and_retried_by_continue(env):
    """#3b"""
    db = env["db"]
    ad = make_adapters()
    make_run(env, platforms=("facebook",), keywords=("bakery",))
    with patch("app.discovery.pipeline.get_scraper",
               lambda p, connector=None, organization_id=None: _ok_scraper(p, fail=True)):
        run(env, ad)
    sh = db.search_history.find_one({"run_id": "KWTEST1"})
    c = cands(env)["fb_x"]
    assert sh["tokens_charged"]["deep_scrape"] == 0 and c["deep_status"] == "error"
    assert remaining(env) == 1000 - sum(sh["tokens_charged"].values())
    from app.discovery.service import continue_plan
    assert continue_plan(db, sh)["to_collect"] == 1                 # retryable by continue
    _reset_for_continue(db)
    with patch("app.discovery.pipeline.get_scraper",
               lambda p, connector=None, organization_id=None: _ok_scraper(p, comments=[{"id": "c", "text": "x"}])):
        run(env, ad)
    sh = db.search_history.find_one({"run_id": "KWTEST1"})
    assert sh["tokens_charged"]["deep_scrape"] == 5 and cands(env)["fb_x"]["deep_status"] == "done"


def test_estimate_counts_every_instagram_candidate_as_a_sample(env):
    """#4: an Instagram result may carry fewer than n posts -> worst case samples it."""
    from app.discovery import service as S
    clean = {"keywords": ["bakery", "cakes"], "platforms": ["instagram"], "filters": {"recent_posts_to_analyze": 10},
             "strict": False, "limits": {"max_candidates": 50, "recent_posts": 10, "comments_per_post": 10},
             "notes": []}
    est = S.compute_estimate(env["org"], clean)
    make_run(env, platforms=("instagram",))
    run(env, make_adapters())
    sh = env["db"].search_history.find_one({"run_id": "KWTEST1"})
    assert sh["tokens_charged"]["sampling"] > 0
    assert est["tokens"]["sample"] == 50 >= sh["tokens_charged"]["sampling"]
    assert est["tokens"]["total_max"] >= sum(sh["tokens_charged"].values())


def test_resume_discovery_failure_keeps_earlier_candidates(env):
    """#6: a later keyword failing must not strand candidates already paid for."""
    from app.discovery.pipeline import KeywordRun, _insert_candidate
    db = env["db"]
    fb_x = _prof("facebook", "fb_x", 20000)
    fb = FakeAdapter("facebook", {"bakery": [fb_x]}, posts={"fb_x": _posts(5, 4, platform="facebook")})
    make_run(env, platforms=("facebook",), keywords=("bakery", "cakes"))
    db.search_history.update_one({"run_id": "KWTEST1"}, {"$set": {"discovery_pairs_done": ["facebook|bakery"],
                                                                   "stages.discovery": "stopped"}})
    _insert_candidate(KeywordRun(db, db.search_history.find_one({"run_id": "KWTEST1"})), dict(fb_x), "bakery")
    fb.fail_discover = True                                           # continue: "cakes" fails
    with patch("app.discovery.pipeline.get_scraper",
               lambda p, connector=None, organization_id=None: _ok_scraper(p)):
        res = run(env, {"facebook": fb})
    c = cands(env)["fb_x"]
    sh = db.search_history.find_one({"run_id": "KWTEST1"})
    assert res["status"] == "completed" and "facebook" not in sh["platform_errors"]
    assert c["stage_reached"] == "scraped" and c["status"] == "passed"
    assert sh["discovery_errors"][0]["keyword"] == "cakes"


def test_adapters_tolerate_odd_field_shapes():
    """#8: strings / numbers where lists are expected; video text is not the channel bio."""
    from app.discovery.adapters.instagram import InstagramAdapter
    from app.discovery.adapters.linkedin import LinkedInAdapter
    from app.discovery.adapters.youtube import YouTubeAdapter, _channel_fields
    prof = InstagramAdapter(None).profile_from_item({"username": "abc", "externalUrls": "https://x.com",
                                                    "latestPosts": "nope"})
    assert prof["contacts"]["websites"] == ["https://x.com"] and prof["recent_posts"] == []
    assert _channel_fields({"links": 3})["links"] == []
    assert _channel_fields({"links": "https://y.com"})["links"] == ["https://y.com"]
    li = LinkedInAdapter(None).profile_from_item({"name": "Acme", "industries": "Food Production",
                                                  "linkedinUrl": "https://www.linkedin.com/company/acme"})
    assert li["category"] == "Food Production"

    class Conn:
        def scrape_actor(self, actor_id, run_input, label, keyword=None, should_abort=None):
            return [{"id": "v1", "channelId": "UCaaaaaaaaaaaaaaaaaaaaaa", "channelName": "Bake TV",
                     "channelUrl": "https://www.youtube.com/@baketv", "description": "VIDEO text jobs@x.com",
                     "thumbnailUrl": "https://img/video.jpg", "location": "Video place", "links": 5}]
    found = YouTubeAdapter(Conn()).discover("baking", 5)
    assert found[0]["bio"] is None and found[0]["profile_picture"] is None and found[0]["country"] is None
    assert found[0]["contacts"] == {"emails": [], "phones": [], "websites": []}


def test_duplicate_post_fetched_once_in_keyword_deep_scrape(env):
    """#10 (keyword side): the same post twice -> one fetch, the real post keeps its comments."""
    from app.discovery.adapters.base import split_raw
    from app.discovery.pipeline import KeywordRun, deep_scrape_candidate
    db = env["db"]
    p = _posts(1, 4, platform="facebook")
    posts, raws = split_raw(p + p)
    make_run(env, platforms=("facebook",), keywords=("bakery",))
    cid = db.discovery_candidates.insert_one({
        "search_run_id": "KWTEST1", "platform": "facebook", "profile_id": "fb_x",
        "url": "https://www.facebook.com/fb_x", "profile": {"profile_id": "fb_x", "name": "X"},
        "recent_posts": posts, "posts_raw": raws, "keywords": ["bakery"], "status": "passed"}).inserted_id
    sc = _ok_scraper("facebook", comments=[{"id": "c1", "text": "Price please"}])
    with patch("app.discovery.pipeline.get_scraper", lambda p, connector=None, organization_id=None: sc):
        r = KeywordRun(db, db.search_history.find_one({"run_id": "KWTEST1"}))
        res = deep_scrape_candidate(r, db.discovery_candidates.find_one({"_id": cid}), 10, 500)
    stored = list(db.facebook_posts.find({"search_run_id": "KWTEST1"}))
    assert len(stored) == 1 and sc._calls["n"] == 1 and res["fetches"] == 1
    assert stored[0]["comments_status"] == "completed" and stored[0]["scraped_comment_count"] == 1


def test_duplicate_post_fetched_once_in_url_search(env):
    """#10 (URL search regression)."""
    from app.social import url_search
    db = env["db"]
    item = {"id": "p1", "url": "https://www.facebook.com/acme/posts/1", "likes": 3, "comments": 2,
            "text": "Fresh bread"}
    sc = _ok_scraper("facebook", comments=[{"id": "c1", "text": "Price please"}])
    sc.fetch_page_details = lambda url, should_abort=None: []
    sc.fetch_posts = lambda url, n, should_abort=None: [dict(item), dict(item)]
    db.search_history.insert_one({"run_id": "URLDUP", "organization_id": env["org"], "user_id": env["uid"],
                                  "created_by": env["email"], "status": "running"})
    with patch("app.social.url_search.get_scraper", lambda platform, organization_id=None: sc), \
            patch("app.admin.settings.is_platform_enabled", return_value=True):
        res = url_search.run_url_search("URLDUP", "https://www.facebook.com/acme", 10, 10,
                                        organization_id=env["org"], created_by=env["email"], user_id=env["uid"])
    assert res["status"] == "completed", res
    stored = list(db.facebook_posts.find({"search_run_id": "URLDUP"}))
    assert len(stored) == 1 and sc._calls["n"] == 1
    assert stored[0]["comments_status"] == "completed" and stored[0]["scraped_comment_count"] == 1
    assert res["posts"] == 1 and res["comments"] == 1


def test_exception_after_discovery_charge_is_refunded(env):
    """#11: a failure after paying (e.g. storing candidates) refunds and the run goes on."""
    ad = make_adapters()
    make_run(env, platforms=("instagram",), keywords=("bakery",))
    with patch("app.discovery.pipeline._insert_candidate", side_effect=RuntimeError("db hiccup")):
        res = run(env, ad)
    sh = env["db"].search_history.find_one({"run_id": "KWTEST1"})
    assert sh["tokens_charged"]["discovery"] == 0 and remaining(env) == 1000
    assert res["status"] == "error" and sh["platform_errors"]["instagram"]["stage"] == "discovery"


def test_crash_after_paying_never_charges_twice_on_resume(env):
    """#11: the charge marker is written before the tokens are taken."""
    db = env["db"]
    ad = make_adapters()
    make_run(env, platforms=("facebook",), keywords=("bakery",))
    state = {"crash": True}
    real = ad["facebook"].discover

    def crashing_discover(kw, limit, filters=None):
        if state["crash"]:
            state["crash"] = False
            raise _Crash()
        return real(kw, limit, filters=filters)
    ad["facebook"].discover = crashing_discover
    with pytest.raises(_Crash):
        run(env, ad)
    assert db.search_history.find_one({"run_id": "KWTEST1"})["tokens_charged"]["discovery"] == 5

    # resume; this time the process dies inside the deep scrape, after paying collect
    crash_fetch = {"on": True}

    def fetch(url):
        if crash_fetch["on"]:
            crash_fetch["on"] = False
            raise _Crash()
    with patch("app.discovery.pipeline.get_scraper",
               lambda p, connector=None, organization_id=None: _ok_scraper(p, on_fetch=fetch)):
        with pytest.raises(_Crash):
            run(env, ad)
        run(env, ad)                                                 # second resume finishes
    sh = db.search_history.find_one({"run_id": "KWTEST1"})
    assert sh["tokens_charged"]["discovery"] == 5                   # not 10
    assert sh["tokens_charged"]["deep_scrape"] == 5                 # not 10
    assert cands(env)["fb_x"]["deep_status"] == "done"
    assert remaining(env) == 1000 - sum(sh["tokens_charged"].values())


def test_same_page_with_and_without_native_id_is_one_candidate(env):
    """#11: profile_id falls back to the canonical URL; dedupe matches by URL too."""
    from app.discovery.pipeline import KeywordRun, _insert_candidate
    db = env["db"]
    make_run(env, platforms=("facebook",), keywords=("bakery", "cakes"))
    r = KeywordRun(db, db.search_history.find_one({"run_id": "KWTEST1"}))
    with_id = _prof("facebook", "fb_x", 100)
    with_id["profile_id"] = "123456"
    no_id = _prof("facebook", "fb_x", 100)
    no_id["profile_id"] = no_id["url"]
    assert _insert_candidate(r, with_id, "bakery") is True
    assert _insert_candidate(r, no_id, "cakes") is False
    docs = list(db.discovery_candidates.find({"search_run_id": "KWTEST1"}))
    assert len(docs) == 1 and sorted(docs[0]["keywords"]) == ["bakery", "cakes"]


def test_cache_parts_expire_separately(env):
    """#12: caching posts later never extends a stale profile."""
    from app.discovery import cache
    db = env["db"]
    cache.put_profile(db, _prof("facebook", "fb_x", 20000))
    db.discovery_profile_cache.update_one({"profile_id": "fb_x"}, {"$set": {
        "profile_expires_at": datetime.now(timezone.utc) - timedelta(minutes=1)}})
    cache.put_posts(db, "facebook", "fb_x", [{"id": "1"}], [None], 1)
    assert cache.get_profile(db, "facebook", "fb_x") is None
    assert cache.get_posts(db, "facebook", "fb_x", 1)["posts"] == [{"id": "1"}]


# ═══════════════════════════════════════════════════════════════════════════
# Filters pushed into the Apify search
# ═══════════════════════════════════════════════════════════════════════════

KOTA = {"location_keyword": "kota, Rajasthan", "category_keyword": "software, IT services", "min_followers": 1000,
        "max_followers": 90000, "min_posts": 10, "verified_only": True, "recent_posts_to_analyze": 10}


def test_instagram_search_text_gets_one_place_and_one_business_type(env):
    from app.discovery.adapters import get_adapter
    conn = FakeConnector({})
    ad = get_adapter("instagram", conn)
    ad.discover("developer job", 7, filters=KOTA)
    assert conn.calls[0][1] == {"search": "developer job kota", "searchType": "user", "searchLimit": 7}
    assert ad.last_query == {"platform": "instagram", "keyword": "developer job",
                             "query_text": "developer job kota", "locations": [], "extra_inputs": {}}
    # a place already in the keyword isn't repeated; no filters -> the keyword alone
    ad.discover("kota coaching", 7, filters={"location_keyword": "Kota, Rajasthan"})
    assert conn.calls[1][1]["search"] == "kota coaching"
    ad.discover("developer job", 7)
    assert conn.calls[2][1]["search"] == "developer job"


def test_search_text_is_capped(env):
    from app.discovery.adapters import get_adapter
    from app.discovery.adapters.base import MAX_QUERY_LEN, search_text
    kw54 = "independent residential property developer and builder"
    kw56 = "independent residential property developers and builders"
    assert (len(kw54), len(kw56), MAX_QUERY_LEN) == (54, 56, 60)
    assert search_text(kw54, "kota", "software") == kw54 + " kota"      # 59: the business type doesn't fit
    assert search_text(kw56, "kota", "IT") == kw56 + " IT"              # the place doesn't fit, "IT" does
    assert search_text(kw56 + " in kota", "Kota") == kw56 + " in kota"  # never shortens the keyword
    conn = FakeConnector({})
    get_adapter("instagram", conn).discover(kw54, 5, filters={"location_keyword": "Kota", "category_keyword": "software"})
    q = conn.calls[0][1]["search"]
    assert q == kw54 + " Kota" and len(q) <= MAX_QUERY_LEN


def test_facebook_and_linkedin_get_the_full_location(env):
    from app.discovery.adapters import get_adapter
    conn = FakeConnector({})
    fb = get_adapter("facebook", conn)
    fb.discover("developer job", 5, filters=KOTA)
    assert conn.calls[0][1] == {"categories": ["developer job"], "resultsLimit": 5, "locations": ["kota, Rajasthan"]}
    assert fb.last_query["locations"] == ["kota, Rajasthan"] and fb.last_query["query_text"] == "developer job"
    li = get_adapter("linkedin", conn)
    li.discover("developer job", 5, filters=KOTA)
    assert conn.calls[1][1] == {"searchQuery": "developer job", "maxItems": 5, "scraperMode": "full",
                                "locations": ["kota, Rajasthan"]}
    # without a location nothing extra is sent
    fb.discover("bakery", 5, filters={"min_followers": 10})
    assert conn.calls[2][1] == {"categories": ["bakery"], "resultsLimit": 5}


def test_youtube_video_search_gets_place_and_business_type_only(env):
    from app.discovery.adapters.youtube import YouTubeAdapter
    conn = FakeConnector({})
    with patch("app.discovery.adapters.youtube.resolve_actor",
               lambda key: "streamers/youtube-scraper" if key == "actor.youtube.main" else ""):
        ad = YouTubeAdapter(conn)
        ad.discover("developer job", 5, filters=KOTA)
    assert conn.calls[0][0] == "streamers/youtube-scraper"
    assert conn.calls[0][1] == {"searchQueries": ["developer job kota software"], "maxResults": 10,
                                "downloadComments": False}
    assert ad.last_query["extra_inputs"] == {}


def test_youtube_channel_search_gets_its_documented_limits(env):
    from app.discovery.adapters.youtube import YouTubeAdapter
    conn = FakeConnector({})
    actors = {"discovery.actor.youtube.channel_search": "maximedupre/youtube-channel-search-scraper",
              "actor.youtube.main": "streamers/youtube-scraper"}
    with patch("app.discovery.adapters.youtube.resolve_actor", lambda key: actors.get(key, "")):
        ad = YouTubeAdapter(conn)
        ad.discover("developer job", 5, filters={**KOTA, "location_keyword": "Pune, India"})
    assert conn.calls[0][0] == "maximedupre/youtube-channel-search-scraper"
    assert conn.calls[0][1] == {"searchTerms": ["developer job Pune software"], "maxTotalResults": 5,
                                "maxChannelsPerSearchTerm": 5, "minSubscribers": 1000, "maxSubscribers": 90000,
                                "minVideos": 10, "verifiedOnly": True, "countryHint": "IN"}
    assert ad.last_query["extra_inputs"] == {"minSubscribers": 1000, "maxSubscribers": 90000, "minVideos": 10,
                                             "verifiedOnly": True, "countryHint": "IN"}
    # no country among the places -> no hint; no limits -> none sent
    with patch("app.discovery.adapters.youtube.resolve_actor", lambda key: actors.get(key, "")):
        YouTubeAdapter(conn).discover("bakery", 5, filters={"location_keyword": "Kota, Rajasthan"})
    assert conn.calls[1][1] == {"searchTerms": ["bakery Kota"], "maxTotalResults": 5, "maxChannelsPerSearchTerm": 5}


def test_pipeline_passes_filters_and_records_what_was_searched(env):
    db = env["db"]
    ad = make_adapters()
    flt = {"min_followers": 1000, "location_keyword": "Pune", "recent_posts_to_analyze": 10}
    make_run(env, filters=flt)
    run(env, ad)
    assert ad["instagram"].calls["filters"] == [flt, flt] and ad["facebook"].calls["filters"] == [flt, flt]
    sh = db.search_history.find_one({"run_id": "KWTEST1"})
    queries = {(q["platform"], q["keyword"]): q for q in sh["discovery_queries"]}
    assert set(queries) == {("instagram", "bakery"), ("instagram", "cakes"), ("facebook", "bakery"),
                            ("facebook", "cakes")}
    assert queries[("instagram", "cakes")]["query_text"] == "cakes q"
    from app.discovery.service import run_status
    assert len(run_status(sh)["discovery_queries"]) == 4
    # a resumed run re-searching a pair replaces its entry instead of adding one
    from app.discovery.pipeline import KeywordRun, record_query
    r = KeywordRun(db, sh)
    ad["facebook"].last_query = {"query_text": "cakes again", "locations": ["Pune"], "extra_inputs": {}}
    record_query(r, "facebook", "cakes", ad["facebook"])
    sh = db.search_history.find_one({"run_id": "KWTEST1"})
    fb_cakes = [q for q in sh["discovery_queries"] if q["platform"] == "facebook" and q["keyword"] == "cakes"]
    assert len(sh["discovery_queries"]) == 4 and fb_cakes[0]["query_text"] == "cakes again"


def test_errors_are_explained_in_plain_words():
    from app.connectors.apify_connector import ScrapeError
    from app.connectors.error_explain import explain_error
    bad_chars = ScrapeError("INVALID_INPUT", 'Invalid actor input (HTTP 400): Input is not valid: Field '
                            'input.search must match pattern "^[^!?.,:;\-+=*&%$#@/\~^|<>()[\]{}"\'`]+$"')
    e = explain_error(bad_chars, platform="instagram", stage="discovery")
    assert e["code"] == "invalid_characters" and "Instagram doesn't accept some characters" in e["reason"]
    assert "must match pattern" in e["technical"] and e["action"]
    for err, code in [("Monthly usage hard limit exceeded", "no_credit"),
                      (ScrapeError("ACTOR_TIMED_OUT", "run timed out"), "timed_out"),
                      ("Actor not found", "actor_not_found"),
                      ("401 Unauthorized", "key_rejected"),
                      ("This actor requires a paid plan", "actor_needs_paid_plan"),
                      ("weird", "unknown")]:
        assert explain_error(err, platform="facebook", stage="discovery")["code"] == code, err


def test_instagram_search_text_has_no_refused_characters():
    from app.discovery.adapters.instagram import InstagramAdapter, instagram_safe
    assert instagram_safe("e-commerce kota") == "e commerce kota"
    assert instagram_safe("real estate, Goa (villas) & co.") == "real estate Goa villas co"
    _actor, run_input, _rec = InstagramAdapter.__new__(InstagramAdapter).discovery_request(
        "e-commerce", 10, {"location_keyword": "kota, Rajasthan"})
    assert run_input["search"] == "e commerce kota"


def test_platform_failure_stores_reason_action_and_technical(env):
    """A platform that fails records what went wrong in words, what to do, and the raw error."""
    from app.discovery.pipeline import KeywordRun
    r = KeywordRun.__new__(KeywordRun)
    r.platform_errors, r.progress = {}, (lambda **kw: None)
    r.fail_platform("instagram", "discovery", "Invalid actor input (HTTP 400): Field input.search must match pattern x")
    pe = r.platform_errors["instagram"]
    assert pe["code"] == "invalid_characters" and pe["action"] and "must match pattern" in pe["technical"]
