"""
Admin data-browser unit tests: score buckets, contact queries and the
query-builders for the comments/pages/posts/analytics endpoints (pure
logic only; DB interaction is validated by the live harness).
"""
import pytest

from app.api.routes.admin import _contact_query, _score_bucket, _page_platform


# ── Score buckets ───────────────────────────────────────────────────────────

def test_score_bucket_boundaries():
    assert _score_bucket(None) == "no score"
    assert _score_bucket("") == "no score"
    assert _score_bucket("abc") == "no score"
    assert _score_bucket(0) == "0–19"
    assert _score_bucket(19) == "0–19"
    assert _score_bucket(20) == "20–39"
    assert _score_bucket(39.9) == "20–39"
    assert _score_bucket(40) == "40–59"
    assert _score_bucket(59) == "40–59"
    assert _score_bucket(60) == "60–79"
    assert _score_bucket(79) == "60–79"
    assert _score_bucket(80) == "80–100"
    assert _score_bucket(100) == "80–100"
    assert _score_bucket("85") == "80–100"


# ── Contact query ───────────────────────────────────────────────────────────

def test_contact_query_shape():
    q = _contact_query()
    assert "$or" in q
    fields = {next(iter(c.keys())) for c in q["$or"]}
    assert fields == {"phone", "email", "whatsapp"}
    # each clause requires a real non-whitespace string
    for clause in q["$or"]:
        assert {"$regex": r"\S"} in clause.values()


# ── Platform resolution for non-facebook pages ──────────────────────────────

def test_page_platform_from_source():
    assert _page_platform({"source": "instagram scraper"}) == "instagram"
    assert _page_platform({"source": "linkedin_company"}) == "linkedin"
    assert _page_platform({"source": "youtube-channel"}) == "youtube"
    assert _page_platform({"source": "facebook"}) == "facebook"
    assert _page_platform({}) == "facebook"
