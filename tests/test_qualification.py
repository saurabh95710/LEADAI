"""
Acceptance tests for the lead-qualification workflow (spec: MIN_COMMENTS=10).

    relevant post + total_comment_count >= MIN_COMMENTS  →  qualifying
    page with >= 1 qualifying post                       →  kept as a result
    comment scrape runs ONLY on qualifying posts
    total_comment_count (Facebook) is never overwritten by the scraped count
"""
from datetime import datetime, timedelta, timezone

from app.agent.search import (
    _MIN_COMMENTS,
    _activity_status,
    _compute_page_stats,
    _is_qualifying_post,
    _lead_score,
    _parse_iso,
    _post_relevant,
    _relevance_tokens,
    map_page_item,
    map_post_item,
)

PAGE = {
    "_id": "p1",
    "page_name": "Shyam Property Dealer",
    "page_id": "123",
    "facebook_url": "https://www.facebook.com/shyamprop",
    "search_keyword": "property dealers in kota",
    "city": "Kota",
    "state": "Rajasthan",
}


def post(url, caption, comments, **kw):
    return map_post_item({"url": url, "text": caption, "commentsCount": comments, **kw}, PAGE)


def test_min_comments_default_is_10():
    assert _MIN_COMMENTS == 10


def test_page_and_group_source_type():
    page = map_page_item({"facebookUrl": "https://www.facebook.com/dealerx", "title": "X"}, "r", "kw")
    group = map_page_item({"facebookUrl": "https://www.facebook.com/groups/12345", "title": "G"}, "r", "kw")
    assert page["source_type"] == "page"
    assert group["source_type"] == "group"


def test_relevance_tokens():
    tokens = _relevance_tokens("property dealers in kota")
    assert "kota" in tokens and "property" in tokens and "in" not in tokens


def test_relevant_post_qualifies():
    p = post("https://fb.com/x/1", "2 BHK flat available in Kota, budget 20 lakh", 37)
    assert p["is_relevant"] is True
    assert p["total_comment_count"] == 37
    assert p["is_qualifying"] is True


def test_low_comment_post_does_not_qualify():
    p = post("https://fb.com/x/2", "Brand new flat in Kota for sale", 3)
    assert p["is_relevant"] is True
    assert p["is_qualifying"] is False


def test_irrelevant_post_does_not_qualify_even_with_many_comments():
    p = post("https://fb.com/x/3", "Happy Diwali to all our friends", 150)
    assert p["is_relevant"] is False
    assert p["is_qualifying"] is False


def test_qualification_rule_matches_old_threshold_boundary():
    p = post("https://fb.com/x/4", "Kota property market update", 9)
    assert p["is_qualifying"] is False
    p = post("https://fb.com/x/5", "Kota property market update", 10)
    assert p["is_qualifying"] is True


def test_page_stats_aggregation():
    posts = []
    for i in range(30):  # 30 posts found
        caption = "Kota property listing #%d" % i
        comments = 20 if i < 5 else (12 if 5 <= i < 10 else 4)  # 10 relevant, 5 qualifying
        p = post(f"https://fb.com/x/{i}", caption, comments)
        assert p is not None
        p["_id"] = "post%d" % i
        posts.append(p)
    stats = _compute_page_stats(posts)
    assert stats["total_posts_found"] == 30
    assert stats["relevant_posts_count"] == 10
    assert stats["qualifying_posts_count"] == 5
    assert stats["has_qualifying_posts"] is True
    assert stats["total_comments_on_qualifying_posts"] == 5 * 20
    assert stats["lead_score"] > 0


def test_page_without_qualifying_posts_is_not_a_result():
    posts = [post(f"https://fb.com/x/{i}", "festival wishes", 3) for i in range(5)]
    stats = _compute_page_stats(posts)
    assert stats["qualifying_posts_count"] == 0
    assert stats["has_qualifying_posts"] is False
    assert stats["lead_score"] == 0


def test_total_comment_count_never_overwritten_by_scrape():
    p = post("https://fb.com/x/7", "Kota flats", 37)
    p["scraped_comment_count"] = 20  # comment scrape result
    assert p["total_comment_count"] == 37
    assert p["scraped_comment_count"] == 20
    assert _is_qualifying_post(p) is True


def test_activity_status_boundaries():
    now = datetime.now(timezone.utc)
    def iso(days):
        return (now - timedelta(days=days)).isoformat()
    assert _activity_status(iso(1)) == "active"
    assert _activity_status(iso(120)) == "recent"
    assert _activity_status(iso(400)) == "inactive"
    assert _activity_status(None) == "unknown"
    assert _activity_status("garbage") == "unknown"
    assert _activity_status("2026-05-01") in ("active", "recent", "inactive")


def test_lead_score_ordering():
    active = _lead_score(5, 100, "active")
    inactive = _lead_score(5, 100, "inactive")
    assert active > inactive
    assert _lead_score(0, 100, "active") == 0


def test_parse_iso_variants():
    assert _parse_iso("2026-06-01T12:00:00Z") is not None
    assert _parse_iso("2026-06-01") is not None
    assert _parse_iso("2026-06-01T12:00:00.000Z") is not None


# ── contact filter: only comments with a phone number or email are kept ──
def test_has_contact_info_phone_or_email():
    from app.pipeline.comment_ai import has_contact_info
    assert has_contact_info("please call me on 9876543210") is True
    assert has_contact_info("contact me at ramesh@gmail.com") is True
    assert has_contact_info("+91 98765 43210 is my number") is True
    assert has_contact_info("whatsapp 98765-43210") is True
    assert has_contact_info("nice post, keep it up") is False
    assert has_contact_info("") is False
    assert has_contact_info(None) is False
    assert has_contact_info("my email is info@shyamprop.in") is True
