"""
Acceptance tests for the URL-based social search (spec: one profile URL in,
page + posts + comments out, platform auto-detected).

    facebook / instagram / youtube / linkedin URLs are detected + canonicalized
    tracking params are dropped, but the identifying path never changes
    non-profile URLs (a video link, a post link, a login page…) are rejected
    the pipeline stores the page/post/comment docs in the existing collections
"""
from app.social.url_detector import (
    UrlError,
    detect_social_url,
)


def test_facebook_page_url():
    platform, url = detect_social_url("https://www.facebook.com/acmeindia")
    assert platform == "facebook"
    assert url == "https://www.facebook.com/acmeindia"


def test_facebook_mobile_and_tracking():
    platform, url = detect_social_url(
        "https://m.facebook.com/acmeindia/?ref=bookmarks&fbclid=abc")
    assert platform == "facebook"
    assert url == "https://www.facebook.com/acmeindia"


def test_facebook_profile_php_keeps_id_only():
    platform, url = detect_social_url(
        "https://www.facebook.com/profile.php?id=1000123456789&ref=nf")
    assert platform == "facebook"
    assert url == "https://www.facebook.com/profile.php?id=1000123456789"


def test_facebook_profile_php_without_id_is_invalid():
    try:
        detect_social_url("https://www.facebook.com/profile.php?ref=nf")
        assert False, "expected UrlError"
    except UrlError as e:
        assert e.kind == "invalid"


def test_facebook_legacy_pages_id_path():
    platform, url = detect_social_url("https://www.facebook.com/pages/Shyam/1234567890")
    assert platform == "facebook"
    assert url == "https://www.facebook.com/pages/Shyam/1234567890"


def test_instagram_profile_keeps_username_drops_tracking():
    platform, url = detect_social_url("https://www.instagram.com/shyam.dealer/?igshid=1")
    assert platform == "instagram"
    assert url == "https://www.instagram.com/shyam.dealer"


def test_instagram_post_link_rejected():
    try:
        detect_social_url("https://www.instagram.com/p/AbCdEfGhIjK/")
        assert False, "expected UrlError"
    except UrlError as e:
        assert e.kind == "invalid"


def test_youtube_handle_and_channel():
    platform, url = detect_social_url("https://www.youtube.com/@acme")
    assert platform == "youtube"
    assert url == "https://www.youtube.com/@acme"
    platform, url = detect_social_url("https://www.youtube.com/channel/UC1aB2CdE3fGh")
    assert platform == "youtube"
    assert url == "https://www.youtube.com/channel/UC1aB2CdE3fGh"


def test_youtube_video_link_rejected():
    try:
        detect_social_url("https://youtu.be/dQw4w9WgXcQ")
        assert False, "expected UrlError"
    except UrlError as e:
        assert e.kind == "invalid"


def test_linkedin_company():
    platform, url = detect_social_url("https://www.linkedin.com/company/acme-corp/?x=1")
    assert platform == "linkedin"
    assert url == "https://www.linkedin.com/company/acme-corp"


def test_unsupported_domain_rejected():
    try:
        detect_social_url("https://www.google.com/search?q=leads")
        assert False, "expected UrlError"
    except UrlError as e:
        assert e.kind == "unsupported"


def test_scheme_is_added_when_missing():
    platform, url = detect_social_url("instagram.com/acme")
    assert platform == "instagram"
    assert url == "https://www.instagram.com/acme"


def test_empty_and_garbage_rejected():
    for bad in ("", "   ", "htp://nope", "://", "this is not a url"):
        try:
            detect_social_url(bad)
            assert False, f"expected UrlError for {bad!r}"
        except UrlError:
            pass


def test_platform_normalizer_detection():
    # instagram username must be a single segment — sub-paths are not profiles
    try:
        detect_social_url("https://www.instagram.com/acme/photos/")
        assert False, "expected UrlError"
    except UrlError as e:
        assert e.kind == "invalid"


# ── Api-level 403 must NOT be claimed as a Facebook block ─────────────────
def test_api_403_with_access_hint_is_access_denied():
    from app.connectors.apify_connector import _classify_api_error

    class Fake503(Exception):
        status_code = 403
        message = "You do not have access to this actor (HTTP 403)"

    err = _classify_api_error(Fake503(), actor_id="x")
    assert err.error_type == "ACCESS_DENIED"
    assert "Facebook" not in err.error["message"]


def test_api_403_plain_is_api_error_not_blocked():
    from app.connectors.apify_connector import _classify_api_error

    class Fake403(Exception):
        status_code = 403
        message = "Forbidden"

    err = _classify_api_error(Fake403(), actor_id="x")
    assert err.error_type == "API_ERROR"
    assert "blocked" not in err.error["message"].lower()


def test_api_429_is_rate_limit_not_facebook_block():
    from app.connectors.apify_connector import _classify_api_error

    class Fake429(Exception):
        status_code = 429
        message = "Too Many Requests"

    err = _classify_api_error(Fake429(), actor_id="x")
    assert err.error_type == "API_ERROR"
    assert "Facebook" not in err.error["message"]


# ── URL-derived fallback page doc (details actor blocked) ─────────────────
def test_url_derived_page_builds_minimal_doc():
    from app.social.url_search import _url_derived_page

    doc = _url_derived_page(
        "facebook", "https://www.facebook.com/acmeindia", "r1",
        {"errorType": "ACCESS_DENIED", "message": "no access"})
    assert doc["facebook_url"] == "https://www.facebook.com/acmeindia"
    assert doc["platform"] == "facebook"
    assert doc["page_name"] == "Acmeindia"
    assert doc["source_type"] == "page_url"
    assert doc["details_error"] == "no access"
    assert doc["posts_status"] == "not_started"
    assert doc["search_run_id"] == "r1"


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:
            print(f"ERROR {fn.__name__}: {e!r}")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)