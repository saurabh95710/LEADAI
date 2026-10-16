"""
Keyword-search filters (app/discovery/filters.py) — pure functions.

Every filter at its boundary (equal / above / below), missing data ->
"unknown", a metric the platform can't measure -> "unsupported", strict mode,
AND semantics, and validation / clamping of the request values.

Run:  python -m pytest tests/test_keyword_filters.py -q -p no:cacheprovider
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.discovery import capabilities as C
from app.discovery import filters as F

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


def profile(platform="instagram", **kw):
    base = {"platform": platform, "profile_id": "p1", "url": f"https://www.{platform}.com/p1",
            "name": "Acme Bakery", "followers": 5000, "posts_count": 120, "bio": "Fresh bread in Pune",
            "contacts": {"emails": [], "phones": [], "websites": []}, "verified": False,
            "is_business": True, "category": None, "country": None, "address": None,
            "last_post_at": None, "private": False}
    base.update(kw)
    return base


def outcome(outcomes, key):
    return next(o for o in outcomes if o["filter"] == key)


# ── profile filters: boundaries ─────────────────────────────────────────────

@pytest.mark.parametrize("followers,expected", [(5000, "pass"), (5001, "pass"), (4999, "fail")])
def test_min_followers_boundary(followers, expected):
    o = outcome(F.evaluate_profile(profile(followers=followers), {"min_followers": 5000}), "min_followers")
    assert o["outcome"] == expected
    assert o["value"] == followers and o["threshold"] == 5000


def test_min_followers_fail_message():
    o = outcome(F.evaluate_profile(profile(followers=800), {"min_followers": 5000}), "min_followers")
    assert o["message"] == "followers 800 < min 5000"


@pytest.mark.parametrize("followers,expected", [(10000, "pass"), (9999, "pass"), (10001, "fail")])
def test_max_followers_boundary(followers, expected):
    o = outcome(F.evaluate_profile(profile(followers=followers), {"max_followers": 10000}), "max_followers")
    assert o["outcome"] == expected


@pytest.mark.parametrize("posts,expected", [(10, "pass"), (11, "pass"), (9, "fail")])
def test_min_posts_boundary(posts, expected):
    o = outcome(F.evaluate_profile(profile(posts_count=posts), {"min_posts": 10}), "min_posts")
    assert o["outcome"] == expected


def test_missing_value_is_unknown_never_zero():
    outs = F.evaluate_profile(profile(followers=None), {"min_followers": 1, "max_followers": 10})
    assert outcome(outs, "min_followers")["outcome"] == "unknown"
    assert outcome(outs, "max_followers")["outcome"] == "unknown"
    assert outcome(outs, "min_followers")["message"] == "followers unknown"


def test_unsupported_platform_metric():
    # Facebook / LinkedIn don't expose a total post count
    for platform in ("facebook", "linkedin"):
        assert C.capability(platform, "min_posts") == C.UNSUPPORTED
        o = outcome(F.evaluate_profile(profile(platform, posts_count=None), {"min_posts": 5}), "min_posts")
        assert o["outcome"] == "unsupported"
        assert platform in o["message"]
    # YouTube has no business flag
    o = outcome(F.evaluate_profile(profile("youtube", is_business=None), {"business_account_only": True}),
                "business_account_only")
    assert o["outcome"] == "unsupported"


def test_business_verified_contact_flags():
    outs = F.evaluate_profile(profile(is_business=False, verified=True),
                              {"business_account_only": True, "verified_only": True, "has_contact_info": True})
    assert outcome(outs, "business_account_only")["outcome"] == "fail"
    assert outcome(outs, "verified_only")["outcome"] == "pass"
    assert outcome(outs, "has_contact_info")["outcome"] == "fail"
    with_contact = profile(contacts={"emails": ["a@b.co"], "phones": [], "websites": []})
    assert outcome(F.evaluate_profile(with_contact, {"has_contact_info": True}), "has_contact_info")["outcome"] == "pass"
    unknown = profile(verified=None, is_business=None)
    outs = F.evaluate_profile(unknown, {"business_account_only": True, "verified_only": True})
    assert {o["outcome"] for o in outs} == {"unknown"}


def test_location_keyword_matches_country_address_or_bio():
    assert outcome(F.evaluate_profile(profile(bio="Bakery in Pune"), {"location_keyword": "pune"}),
                   "location_keyword")["outcome"] == "pass"
    assert outcome(F.evaluate_profile(profile(bio="x", country="India"), {"location_keyword": "india"}),
                   "location_keyword")["outcome"] == "pass"
    assert outcome(F.evaluate_profile(profile(bio="Bakery in Pune"), {"location_keyword": "Delhi"}),
                   "location_keyword")["outcome"] == "fail"
    assert outcome(F.evaluate_profile(profile(bio=None), {"location_keyword": "Delhi"}),
                   "location_keyword")["outcome"] == "unknown"


def test_language_is_unsupported_unless_data_exists():
    assert outcome(F.evaluate_profile(profile(), {"language": "en"}), "language")["outcome"] == "unsupported"
    assert outcome(F.evaluate_profile(profile(language="en"), {"language": "en"}), "language")["outcome"] == "pass"
    assert outcome(F.evaluate_profile(profile(language="hi"), {"language": "en"}), "language")["outcome"] == "fail"


def test_exclude_keywords_reject_name_or_bio():
    o = outcome(F.evaluate_profile(profile(name="Acme Jobs"), {"exclude_keywords": ["jobs", "hiring"]}),
                "exclude_keywords")
    assert o["outcome"] == "fail" and "jobs" in o["message"]
    o = outcome(F.evaluate_profile(profile(), {"exclude_keywords": ["jobs"]}), "exclude_keywords")
    assert o["outcome"] == "pass"


def test_private_account_always_rejects():
    outs = F.evaluate_profile(profile(private=True), {})
    assert F.decide(outs, strict=False)["passed"] is False


# ── post filters ────────────────────────────────────────────────────────────

def posts(*comments, likes=None, shares=None, days_ago=1):
    out = []
    for i, c in enumerate(comments):
        out.append({"id": str(i), "url": f"https://x/{i}", "comments_count": c,
                    "likes_count": likes, "shares_count": shares,
                    "posted_at": (NOW - timedelta(days=days_ago + i)).isoformat()})
    return out


@pytest.mark.parametrize("comments,expected", [((2, 2), "pass"), ((3, 2), "pass"), ((1, 2), "fail")])
def test_min_avg_comments_boundary(comments, expected):
    outs, m = F.evaluate_posts(profile(), posts(*comments), {"min_avg_comments": 2}, now=NOW)
    assert outcome(outs, "min_avg_comments")["outcome"] == expected


@pytest.mark.parametrize("comments,expected", [((5, 5), "pass"), ((6, 5), "pass"), ((4, 5), "fail")])
def test_min_total_comments_boundary(comments, expected):
    outs, m = F.evaluate_posts(profile(), posts(*comments), {"min_total_comments": 10}, now=NOW)
    assert outcome(outs, "min_total_comments")["outcome"] == expected
    assert m["total_comments"] == sum(comments)


def test_engagement_rate_formula_and_boundary():
    # avg (likes + comments + shares) per post / followers * 100 = (90+10)/1000*100 = 10%
    p = profile(followers=1000)
    outs, m = F.evaluate_posts(p, posts(10, 10, likes=90), {"min_engagement_rate": 10}, now=NOW)
    assert m["engagement_rate"] == 10.0
    assert outcome(outs, "min_engagement_rate")["outcome"] == "pass"
    outs, _ = F.evaluate_posts(p, posts(10, 10, likes=90), {"min_engagement_rate": 10.01}, now=NOW)
    assert outcome(outs, "min_engagement_rate")["outcome"] == "fail"
    outs, m = F.evaluate_posts(p, posts(10, 10, likes=90, shares=100), {"min_engagement_rate": 20}, now=NOW)
    assert m["engagement_rate"] == 20.0
    # unknown followers -> unknown engagement
    outs, m = F.evaluate_posts(profile(followers=None), posts(10, likes=90), {"min_engagement_rate": 1}, now=NOW)
    assert m["engagement_rate"] is None and outcome(outs, "min_engagement_rate")["outcome"] == "unknown"


def test_unknown_comment_counts_stay_unknown():
    outs, m = F.evaluate_posts(profile(), posts(None, None), {"min_avg_comments": 1, "min_total_comments": 1},
                               now=NOW)
    assert m["avg_comments"] is None and m["total_comments"] is None
    assert {o["outcome"] for o in outs} == {"unknown"}
    # a zero is a real value, not unknown
    outs, m = F.evaluate_posts(profile(), posts(0, 0), {"min_avg_comments": 1}, now=NOW)
    assert m["avg_comments"] == 0 and outcome(outs, "min_avg_comments")["outcome"] == "fail"


@pytest.mark.parametrize("days_ago,expected", [(90, "pass"), (89, "pass"), (91, "fail")])
def test_active_within_days_boundary(days_ago, expected):
    outs, _ = F.evaluate_posts(profile(), posts(1, days_ago=days_ago), {"active_within_days": 90}, now=NOW)
    assert outcome(outs, "active_within_days")["outcome"] == expected


def test_active_within_days_judged_at_profile_stage_when_known():
    p = profile(last_post_at=(NOW - timedelta(days=200)).isoformat())
    outs = F.evaluate_profile(p, {"active_within_days": 90}, now=NOW)
    assert outcome(outs, "active_within_days")["outcome"] == "fail"
    assert not F.needs_post_filters({"active_within_days": 90}, outs)
    assert F.needs_post_filters({"active_within_days": 90}, F.evaluate_profile(profile(), {"active_within_days": 90}))


# ── decision: AND + strict ──────────────────────────────────────────────────

def test_and_semantics():
    outs = F.evaluate_profile(profile(followers=6000, posts_count=5), {"min_followers": 5000, "min_posts": 10})
    d = F.decide(outs, strict=False)
    assert d["passed"] is False and d["failed"] == ["min_posts"]
    outs = F.evaluate_profile(profile(followers=6000, posts_count=50), {"min_followers": 5000, "min_posts": 10})
    assert F.decide(outs, strict=False)["passed"] is True


def test_unknown_and_unsupported_listed_and_strict_rejects():
    outs = F.evaluate_profile(profile("facebook", followers=None, posts_count=None),
                              {"min_followers": 100, "min_posts": 5})
    lenient = F.decide(outs, strict=False)
    assert lenient["passed"] is True
    assert lenient["unknown"] == ["min_followers"] and lenient["unsupported"] == ["min_posts"]
    strict = F.decide(outs, strict=True)
    assert strict["passed"] is False
    assert outcome(outs, "min_followers")["message"] == "followers unknown"


# ── validation / clamping ───────────────────────────────────────────────────

@pytest.mark.parametrize("raw,field", [
    ({"min_followers": -1}, "min_followers"),
    ({"min_followers": "abc"}, "min_followers"),
    ({"min_followers": 1e15}, "min_followers"),
    ({"min_posts": 2.5}, "min_posts"),
    ({"min_engagement_rate": float("nan")}, "min_engagement_rate"),
    ({"min_engagement_rate": 5000}, "min_engagement_rate"),
    ({"active_within_days": 0}, "active_within_days"),
    ({"recent_posts_to_analyze": 0}, "recent_posts_to_analyze"),
    ({"verified_only": "maybe"}, "verified_only"),
    ({"min_followers": True}, "min_followers"),
    ({"exclude_keywords": [1, 2]}, "exclude_keywords"),
    ({"bogus": 1}, "bogus"),
    ({"min_followers": 10, "max_followers": 5}, "max_followers"),
])
def test_validation_rejects_bad_values(raw, field):
    with pytest.raises(F.FilterValidationError) as exc:
        F.validate_filters(raw)
    assert field in exc.value.errors


def test_validation_cleans_and_clamps():
    clean, notes = F.validate_filters(
        {"min_followers": "1000", "verified_only": False, "has_contact_info": True, "location_keyword": " Pune ",
         "exclude_keywords": "jobs, Jobs ,  hiring", "recent_posts_to_analyze": 50, "min_avg_comments": 2.5,
         "language": None},
        max_recent_posts=20)
    assert clean == {"min_followers": 1000, "has_contact_info": True, "location_keyword": "Pune",
                     "exclude_keywords": ["jobs", "hiring"], "recent_posts_to_analyze": 20,
                     "min_avg_comments": 2.5}
    assert notes and "20" in notes[0]
    clean, notes = F.validate_filters({}, default_recent_posts=10)
    assert clean == {"recent_posts_to_analyze": 10} and notes == []


def test_relative_and_epoch_dates():
    assert F.to_datetime("3 days ago") is not None
    assert F.to_datetime(1760000000) is not None and F.to_datetime(1760000000000) is not None
    assert F.to_datetime("not a date") is None


def test_eight_digit_dates_are_yyyymmdd_not_epoch():
    """#9: YouTube uploadDate "20240501" is 2024-05-01, not 1970."""
    for v in ("20240501", 20240501):
        dt = F.to_datetime(v)
        assert (dt.year, dt.month, dt.day) == (2024, 5, 1)
    assert F.to_datetime("20241399") is None
    recent = (NOW - timedelta(days=5)).strftime("%Y%m%d")
    outs, _ = F.evaluate_posts(profile(), [{"comments_count": 1, "posted_at": recent}],
                               {"active_within_days": 90}, now=NOW)
    assert outcome(outs, "active_within_days")["outcome"] == "pass"


# ── location: places, word boundaries, all / any ────────────────────────────

def test_location_places_are_parsed_most_specific_first():
    assert F.parse_places("kota , Rajasthan;; India ,") == ["kota", "Rajasthan", "India"]
    assert F.parse_places("  ") == [] and F.parse_places(None) == []
    assert F.parse_places("Kota, kota") == ["Kota"]


def test_location_matches_whole_words_only():
    flt = {"location_keyword": "kota"}
    assert outcome(F.evaluate_profile(profile(bio="IT jobs in Kota, Rajasthan"), flt),
                   "location_keyword")["outcome"] == "pass"
    o = outcome(F.evaluate_profile(profile(bio="Remote devs from North Dakota"), flt), "location_keyword")
    assert o["outcome"] == "fail" and o["message"] == "location: 'kota' not found"
    assert outcome(F.evaluate_profile(profile(bio="Kotak Bank partner"), flt), "location_keyword")["outcome"] == "fail"
    # punctuation and case are normalised
    assert outcome(F.evaluate_profile(profile(bio="Office: KOTA-Rajasthan"), {"location_keyword": "kota , rajasthan"}),
                   "location_keyword")["outcome"] == "pass"


def test_location_all_needs_every_place_and_lists_the_missing_ones():
    flt = {"location_keyword": "Kota, Rajasthan"}
    o = outcome(F.evaluate_profile(profile(bio="Kota, Rajasthan"), flt), "location_keyword")
    assert o["outcome"] == "pass" and o["message"] == "location matches Kota, Rajasthan"
    o = outcome(F.evaluate_profile(profile(bio="Kota coaching"), flt), "location_keyword")
    assert o["outcome"] == "fail" and o["message"] == "location: 'Rajasthan' not found"
    assert o["value"] == {"matched": ["Kota"], "missing": ["Rajasthan"], "match": "all"}
    o = outcome(F.evaluate_profile(profile(bio="Jakarta property"), flt), "location_keyword")
    assert o["message"] == "location: 'Kota', 'Rajasthan' not found"


def test_location_any_needs_one_place():
    flt = {"location_keyword": "Kota, Rajasthan", "location_match": "any"}
    o = outcome(F.evaluate_profile(profile(bio="Jaipur, Rajasthan"), flt), "location_keyword")
    assert o["outcome"] == "pass" and o["message"] == "location matches Rajasthan"
    o = outcome(F.evaluate_profile(profile(bio="Yogyakarta"), flt), "location_keyword")
    assert o["outcome"] == "fail" and o["message"] == "location: none of 'Kota', 'Rajasthan' found"


def test_location_searches_name_address_city_country_and_category():
    flt = {"location_keyword": "Kota, Rajasthan"}
    for kw in ({"name": "Kota Devs", "address": "Rajasthan"}, {"city": "Kota", "country": "India", "bio": "Rajasthan"},
               {"address": "12 MG Road, Kota", "category": "Rajasthan tourism"}):
        assert outcome(F.evaluate_profile(profile(**{"bio": None, **kw}), flt),
                       "location_keyword")["outcome"] == "pass", kw


def test_location_unknown_without_location_data_and_stored_runs_default_to_all():
    # a name alone is not location data -> unknown, like before
    o = outcome(F.evaluate_profile(profile(bio=None, name="Kota Rajasthan Devs"), {"location_keyword": "Kota"}),
                "location_keyword")
    assert o["outcome"] == "unknown"
    # a run stored before location_match existed: every place is required
    o = outcome(F.evaluate_profile(profile(bio="Kota"), {"location_keyword": "kota , rajasthan"}), "location_keyword")
    assert o["outcome"] == "fail" and o["value"]["match"] == "all"


def test_location_match_validation():
    clean, _ = F.validate_filters({"location_keyword": " Kota, Rajasthan ", "location_match": "ANY"})
    assert clean["location_keyword"] == "Kota, Rajasthan" and clean["location_match"] == "any"
    clean, _ = F.validate_filters({"location_match": "any"})           # nothing to apply it to
    assert "location_match" not in clean
    for bad in ("some", 1, True):
        with pytest.raises(F.FilterValidationError) as exc:
            F.validate_filters({"location_keyword": "Kota", "location_match": bad})
        assert "location_match" in exc.value.errors
    with pytest.raises(F.FilterValidationError) as exc:
        F.validate_filters({"location_keyword": " , ; "})
    assert "location_keyword" in exc.value.errors


# ── business type ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("platform,fields", [
    ("instagram", {"category": "Software Company"}),            # businessCategoryName
    ("facebook", {"categories": ["Local business", "IT Services"]}),
    ("linkedin", {"categories": ["IT Services and IT Consulting"], "category": "IT Services and IT Consulting"}),
    ("youtube", {"bio": "Tutorials from a software studio"}),   # description only
])
def test_category_matches_the_platforms_category_fields(platform, fields):
    p = profile(platform=platform, **{"bio": None, **fields})
    o = outcome(F.evaluate_profile(p, {"category_keyword": "software, IT services"}), "category_keyword")
    assert o["outcome"] == "pass", o
    assert o["message"].startswith("business type matches")


def test_category_word_boundary_unknown_and_messages():
    o = outcome(F.evaluate_profile(profile(bio="Softwares? no, soft toys"), {"category_keyword": "soft ware"}),
                "category_keyword")
    assert o["outcome"] == "fail" and o["message"] == "business type: 'soft ware' not found"
    o = outcome(F.evaluate_profile(profile(bio="Bakery"), {"category_keyword": "software, IT services"}),
                "category_keyword")
    assert o["message"] == "business type: none of 'software', 'IT services' found"
    o = outcome(F.evaluate_profile(profile(bio=None, name=None, category=None), {"category_keyword": "software"}),
                "category_keyword")
    assert o["outcome"] == "unknown"


def test_category_capabilities_and_language_hidden():
    assert C.capability("instagram", "category_keyword") == C.PARTIAL
    assert C.capability("facebook", "category_keyword") == C.SUPPORTED
    assert C.capability("linkedin", "category_keyword") == C.SUPPORTED
    assert C.capability("youtube", "category_keyword") == C.PARTIAL
    assert C.FILTERS["language"]["hidden"] is True
    assert all(C.capability(p, "language") == C.UNSUPPORTED for p in C.PLATFORMS)
    # hidden filters never show up as "can't measure" in estimates
    assert C.unsupported_filters("instagram", {"language": "hi", "min_posts": 1}) == []
    assert C.unsupported_filters("facebook", {"language": "hi", "min_posts": 1}) == ["min_posts"]
