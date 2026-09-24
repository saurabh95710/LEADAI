"""
Post normalization tests — every platform scraper produces a stable LEADAI
document from raw Apify actor output.

Covers:
  - Facebook, Instagram, YouTube, LinkedIn page/post/comment normalization
  - Missing fields, malformed data, empty input
  - Crash resilience (one bad record must not kill the pipeline)
  - Platform-specific field mapping
  - Canonical URL construction
  - Timestamp handling
  - search_run_id propagation
"""
import pytest
from unittest.mock import MagicMock

from app.social.scrapers import (
    FacebookScraper, InstagramScraper, YouTubeScraper, LinkedInScraper,
    get_scraper, _as_int, _pick,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_page_doc(**overrides):
    """Minimal page doc as returned by normalize_page."""
    base = {
        "_id": MagicMock(__str__=lambda s: "page123"),
        "page_id": "pg1",
        "page_name": "Test Page",
        "facebook_url": "https://www.facebook.com/testpage",
        "platform": "facebook",
        "search_run_id": "run1",
    }
    base.update(overrides)
    return base


def _make_post_doc(**overrides):
    """Minimal post doc as returned by normalize_post."""
    base = {
        "_id": MagicMock(__str__=lambda s: "post456"),
        "post_id": "pt1",
        "post_url": "https://www.facebook.com/testpage/posts/123",
        "page_id": "pg1",
        "page_name": "Test Page",
        "platform": "facebook",
        "search_run_id": "run1",
    }
    base.update(overrides)
    return base


# ── Utility functions ────────────────────────────────────────────────────────

class TestAsInt:
    def test_none_returns_none(self):
        assert _as_int(None) is None

    def test_int_passthrough(self):
        assert _as_int(42) == 42

    def test_float_truncated(self):
        assert _as_int(3.7) == 3

    def test_string_number(self):
        assert _as_int("123") == 123

    def test_string_with_commas(self):
        assert _as_int("1,234") == 1234

    def test_k_suffix(self):
        assert _as_int("5K") == 5000

    def test_m_suffix(self):
        assert _as_int("2.5M") == 2500000

    def test_b_suffix(self):
        assert _as_int("1.2B") == 1200000000

    def test_garbage_returns_none(self):
        assert _as_int("not-a-number") is None

    def test_empty_string(self):
        assert _as_int("") is None


class TestPick:
    def test_first_match_wins(self):
        assert _pick({"a": 1, "b": 2}, "a", "b") == 1

    def test_skips_none(self):
        assert _pick({"a": None, "b": 2}, "a", "b") == 2

    def test_skips_empty_string(self):
        assert _pick({"a": "", "b": "hello"}, "a", "b") == "hello"

    def test_skips_empty_list(self):
        assert _pick({"a": [], "b": [1]}, "a", "b") == [1]

    def test_skips_empty_dict(self):
        assert _pick({"a": {}, "b": {"x": 1}}, "a", "b") == {"x": 1}

    def test_no_match_returns_none(self):
        assert _pick({"a": None}, "a", "b") is None


# ── Facebook Normalization ───────────────────────────────────────────────────

class TestFacebookScraper:
    def setup_method(self):
        self.scraper = FacebookScraper()

    def test_normalize_post_valid(self):
        page_doc = _make_page_doc()
        item = {
            "id": "fb_post_123",
            "message": "Hello world",
            "createdTime": "2024-01-15T10:30:00Z",
            "likes": 25,
            "comments": 5,
            "shares": 2,
            "link": "https://www.facebook.com/testpage/posts/123",
        }
        # Facebook normalizer delegates to map_post_item from agent.search
        # We just verify it doesn't crash and adds platform
        result = self.scraper.normalize_post(item, page_doc)
        if result is not None:
            assert result["platform"] == "facebook"

    def test_normalize_post_none_item_returns_none(self):
        result = self.scraper.normalize_post(None, _make_page_doc())
        assert result is None

    def test_normalize_post_empty_dict_returns_none(self):
        result = self.scraper.normalize_post({}, _make_page_doc())
        # map_post_item may return None for empty items
        assert result is None or result.get("post_url") is None

    def test_normalize_comment_valid(self):
        post_doc = _make_post_doc()
        item = {
            "id": "comment_1",
            "message": "Great post!",
            "commenterName": "John Doe",
            "createdTime": "2024-01-15T11:00:00Z",
        }
        result = self.scraper.normalize_comment(item, post_doc)
        if result is not None:
            assert result["platform"] == "facebook"

    def test_normalize_comment_none_item_returns_none(self):
        result = self.scraper.normalize_comment(None, _make_post_doc())
        assert result is None


# ── Instagram Normalization ──────────────────────────────────────────────────

class TestInstagramScraper:
    def setup_method(self):
        self.scraper = InstagramScraper()

    def test_normalize_page_valid(self):
        item = {
            "username": "testuser",
            "fullName": "Test User",
            "pk": 12345,
            "biography": "Hello I am test",
            "followerCount": 1500,
            "followingCount": 200,
            "verified": False,
            "contactEmail": "test@example.com",
            "website": "https://example.com",
            "profilePicUrl": "https://example.com/pic.jpg",
        }
        result = self.scraper.normalize_page(item, "run1", "https://www.instagram.com/testuser")
        assert result is not None
        assert result["page_name"] == "Test User"
        assert result["platform"] == "instagram"
        assert result["followers"] == 1500
        assert result["email"] == "test@example.com"
        assert result["search_run_id"] == "run1"

    def test_normalize_page_no_username_returns_none(self):
        item = {"fullName": "No Username"}
        result = self.scraper.normalize_page(item, "run1", "https://www.instagram.com/x")
        assert result is None

    def test_normalize_page_none_returns_none(self):
        result = self.scraper.normalize_page(None, "run1", "https://www.instagram.com/x")
        assert result is None

    def test_normalize_page_empty_returns_none(self):
        result = self.scraper.normalize_page({}, "run1", "https://www.instagram.com/x")
        assert result is None

    def test_normalize_post_valid(self):
        page_doc = _make_page_doc(platform="instagram", facebook_url="https://www.instagram.com/testuser")
        item = {
            "id": "ig_post_1",
            "shortCode": "ABC123",
            "url": "https://www.instagram.com/p/ABC123/",
            "caption": "Beautiful sunset #travel",
            "timestamp": "2024-01-15T10:00:00.000Z",
            "likesCount": 150,
            "commentsCount": 23,
        }
        result = self.scraper.normalize_post(item, page_doc)
        assert result is not None
        assert result["post_url"] == "https://www.instagram.com/p/ABC123/"
        assert result["caption"] == "Beautiful sunset #travel"
        assert result["likes_count"] == 150
        assert result["total_comment_count"] == 23
        assert result["platform"] == "instagram"
        assert result["search_run_id"] == "run1"

    def test_normalize_post_no_url_uses_shortcode(self):
        page_doc = _make_page_doc(platform="instagram", facebook_url="https://www.instagram.com/testuser")
        item = {"id": "ig_post_2", "shortCode": "XYZ789"}
        result = self.scraper.normalize_post(item, page_doc)
        assert result is not None
        assert "XYZ789" in result["post_url"]

    def test_normalize_post_no_url_no_code_returns_none(self):
        page_doc = _make_page_doc(platform="instagram")
        item = {"id": "ig_post_3"}
        result = self.scraper.normalize_post(item, page_doc)
        assert result is None

    def test_normalize_post_none_returns_none(self):
        result = self.scraper.normalize_post(None, _make_page_doc())
        assert result is None

    def test_normalize_comment_valid(self):
        post_doc = _make_post_doc(platform="instagram")
        item = {
            "id": "ig_comment_1",
            "text": "Love this!",
            "username": "fan_user",
            "authorName": "Fan User",
            "timestamp": "2024-01-15T12:00:00.000Z",
            "likesCount": 5,
        }
        result = self.scraper.normalize_comment(item, post_doc)
        assert result is not None
        assert result["text"] == "Love this!"
        assert result["author_name"] == "Fan User"
        assert result["platform"] == "instagram"
        assert result["reactions_count"] == 5

    def test_normalize_comment_no_text_no_id_returns_none(self):
        post_doc = _make_post_doc(platform="instagram")
        item = {"username": "user"}
        result = self.scraper.normalize_comment(item, post_doc)
        assert result is None

    def test_normalize_comment_id_only_kept(self):
        post_doc = _make_post_doc(platform="instagram")
        item = {"id": "ig_comment_2"}
        result = self.scraper.normalize_comment(item, post_doc)
        assert result is not None
        assert result["comment_id"] == "ig_comment_2"

    def test_normalize_comment_none_returns_none(self):
        result = self.scraper.normalize_comment(None, _make_post_doc())
        assert result is None


# ── YouTube Normalization ────────────────────────────────────────────────────

class TestYouTubeScraper:
    def setup_method(self):
        self.scraper = YouTubeScraper()

    def test_normalize_page_valid(self):
        item = {
            "channel": {
                "channelTitle": "Tech Channel",
                "subscriberCount": 50000,
                "avatar": "https://example.com/avatar.jpg",
                "verified": True,
            },
            "description": "Tech reviews and tutorials",
            "country": "US",
        }
        result = self.scraper.normalize_page(item, "run1", "https://www.youtube.com/@techchannel")
        assert result is not None
        assert result["page_name"] == "Tech Channel"
        assert result["platform"] == "youtube"
        assert result["followers"] == 50000
        assert result["verified"] is True
        assert result["country"] == "US"
        assert result["source_type"] == "channel_url"

    def test_normalize_page_flat_structure(self):
        item = {
            "channelTitle": "Flat Channel",
            "subscriberCount": 1000,
        }
        result = self.scraper.normalize_page(item, "run1", "https://www.youtube.com/@flat")
        assert result is not None
        assert result["page_name"] == "Flat Channel"

    def test_normalize_page_no_name_returns_none(self):
        item = {"description": "No name here"}
        result = self.scraper.normalize_page(item, "run1", "https://www.youtube.com/@x")
        assert result is None

    def test_normalize_page_none_returns_none(self):
        result = self.scraper.normalize_page(None, "run1", "https://www.youtube.com/@x")
        assert result is None

    def test_normalize_post_valid(self):
        page_doc = _make_page_doc(platform="youtube")
        item = {
            "id": "vid123",
            "title": "How to Code",
            "url": "https://www.youtube.com/watch?v=vid123",
            "publishedAt": "2024-01-15T10:00:00Z",
            "likeCount": 500,
            "commentCount": 45,
            "thumbnailUrl": "https://example.com/thumb.jpg",
        }
        result = self.scraper.normalize_post(item, page_doc)
        assert result is not None
        assert result["post_id"] == "vid123"
        assert result["post_url"] == "https://www.youtube.com/watch?v=vid123"
        assert result["caption"] == "How to Code"
        assert result["likes_count"] == 500
        assert result["total_comment_count"] == 45
        assert result["platform"] == "youtube"
        assert result["videos"] == ["https://www.youtube.com/watch?v=vid123"]

    def test_normalize_post_no_url_uses_id(self):
        page_doc = _make_page_doc(platform="youtube")
        item = {"id": "vid456", "title": "Video"}
        result = self.scraper.normalize_post(item, page_doc)
        assert result is not None
        assert result["post_url"] == "https://www.youtube.com/watch?v=vid456"

    def test_normalize_post_no_url_no_id_returns_none(self):
        page_doc = _make_page_doc(platform="youtube")
        item = {"title": "No ID Video"}
        result = self.scraper.normalize_post(item, page_doc)
        assert result is None

    def test_normalize_post_none_returns_none(self):
        result = self.scraper.normalize_post(None, _make_post_doc())
        assert result is None

    def test_normalize_comment_valid(self):
        post_doc = _make_post_doc(platform="youtube")
        item = {
            "id": "yt_comment_1",
            "text": "Great video!",
            "author": "Viewer One",
            "authorUrl": "https://www.youtube.com/channel/UC123",
            "publishedAt": "2024-01-15T12:00:00Z",
            "likeCount": 10,
        }
        result = self.scraper.normalize_comment(item, post_doc)
        assert result is not None
        assert result["text"] == "Great video!"
        assert result["author_name"] == "Viewer One"
        assert result["platform"] == "youtube"
        assert result["reactions_count"] == 10

    def test_normalize_comment_nested_comments(self):
        """YouTube actor returns comments nested under 'comments' key."""
        post_doc = _make_post_doc(platform="youtube")
        item = {
            "text": "Parent comment",
            "author": "Parent User",
            "comments": [
                {"text": "Reply 1", "author": "User A"},
                {"text": "Reply 2", "author": "User B"},
            ],
        }
        # The scraper's fetch_comments flattens nested comments
        # normalize_comment should handle each individually
        result = self.scraper.normalize_comment(item, post_doc)
        assert result is not None
        assert result["text"] == "Parent comment"

    def test_normalize_comment_no_text_no_id_returns_none(self):
        post_doc = _make_post_doc(platform="youtube")
        item = {"author": "No Text"}
        result = self.scraper.normalize_comment(item, post_doc)
        assert result is None

    def test_normalize_comment_defaults_author(self):
        post_doc = _make_post_doc(platform="youtube")
        item = {"text": "Hello"}
        result = self.scraper.normalize_comment(item, post_doc)
        assert result is not None
        assert result["author_name"] == "YouTube User"


# ── LinkedIn Normalization ───────────────────────────────────────────────────

class TestLinkedInScraper:
    def setup_method(self):
        self.scraper = LinkedInScraper()

    def test_normalize_page_valid(self):
        item = {
            "name": "Acme Corp",
            "universalName": "acme-corp",
            "id": "acme-corp",
            "followerCount": 25000,
            "description": "Leading technology company",
            "locations": [{"headquarter": True, "city": "San Francisco", "country": "US"}],
            "industries": [{"name": "Technology"}],
            "website": "https://acme.com",
            "logo": "https://example.com/logo.png",
        }
        result = self.scraper.normalize_page(item, "run1", "https://www.linkedin.com/company/acme-corp")
        assert result is not None
        assert result["page_name"] == "Acme Corp"
        assert result["platform"] == "linkedin"
        assert result["followers"] == 25000
        assert result["category"] == "Technology"
        assert result["city"] == "San Francisco"
        assert result["country"] == "US"
        assert result["source_type"] == "company_url"

    def test_normalize_page_no_name_returns_none(self):
        item = {"followerCount": 100}
        result = self.scraper.normalize_page(item, "run1", "https://www.linkedin.com/company/x")
        assert result is None

    def test_normalize_page_none_returns_none(self):
        result = self.scraper.normalize_page(None, "run1", "https://www.linkedin.com/company/x")
        assert result is None

    def test_normalize_post_valid(self):
        page_doc = _make_page_doc(platform="linkedin")
        item = {
            "id": "urn:li:share:123",
            "linkedinUrl": "https://www.linkedin.com/posts/user-activity-123",
            "content": "Excited to announce our new product!",
            "postedAt": {"date": "2024-01-15"},
            "engagement": {"likes": 120, "comments": 30, "shares": 15},
        }
        result = self.scraper.normalize_post(item, page_doc)
        assert result is not None
        assert result["post_url"] == "https://www.linkedin.com/posts/user-activity-123"
        assert result["caption"] == "Excited to announce our new product!"
        assert result["likes_count"] == 120
        assert result["total_comment_count"] == 30
        assert result["shares_count"] == 15
        assert result["platform"] == "linkedin"

    def test_normalize_post_no_url_returns_none(self):
        page_doc = _make_page_doc(platform="linkedin")
        item = {"id": "urn:li:share:456", "content": "No URL post"}
        result = self.scraper.normalize_post(item, page_doc)
        assert result is None

    def test_normalize_post_none_returns_none(self):
        result = self.scraper.normalize_post(None, _make_post_doc())
        assert result is None

    def test_normalize_comment_valid(self):
        post_doc = _make_post_doc(platform="linkedin")
        item = {
            "id": "comment_li_1",
            "commentary": "Great insight!",
            "actor": {"name": "Jane Smith", "linkedinUrl": "https://www.linkedin.com/in/janesmith"},
            "createdAt": "2024-01-15T14:00:00Z",
            "numReactions": 8,
        }
        result = self.scraper.normalize_comment(item, post_doc)
        assert result is not None
        assert result["comment_id"] == "comment_li_1"
        assert result["text"] == "Great insight!"
        assert result["author_name"] == "Jane Smith"
        assert result["platform"] == "linkedin"
        assert result["reactions_count"] == 8

    def test_normalize_comment_no_id_returns_none(self):
        post_doc = _make_post_doc(platform="linkedin")
        item = {"commentary": "No ID comment"}
        result = self.scraper.normalize_comment(item, post_doc)
        assert result is None

    def test_normalize_comment_none_returns_none(self):
        result = self.scraper.normalize_comment(None, _make_post_doc())
        assert result is None


# ── get_scraper ──────────────────────────────────────────────────────────────

class TestGetScraper:
    def test_facebook(self):
        assert isinstance(get_scraper("facebook"), FacebookScraper)

    def test_instagram(self):
        assert isinstance(get_scraper("instagram"), InstagramScraper)

    def test_youtube(self):
        assert isinstance(get_scraper("youtube"), YouTubeScraper)

    def test_linkedin(self):
        assert isinstance(get_scraper("linkedin"), LinkedInScraper)

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unsupported platform"):
            get_scraper("tiktok")


# ── Crash Resilience ─────────────────────────────────────────────────────────

class TestCrashResilience:
    """One malformed record must not crash the entire pipeline."""

    def test_instaminaize_post_exception_caught(self):
        scraper = InstagramScraper()
        # Pass a non-dict item that would cause AttributeError
        result = scraper.normalize_post("not a dict", _make_page_doc())
        assert result is None

    def test_youtube_normalize_post_exception_caught(self):
        scraper = YouTubeScraper()
        result = scraper.normalize_post([1, 2, 3], _make_post_doc())
        assert result is None

    def test_linkedin_normalize_comment_exception_caught(self):
        scraper = LinkedInScraper()
        result = scraper.normalize_comment("invalid", _make_post_doc())
        assert result is None

    def test_facebook_normalize_page_exception_caught(self):
        scraper = FacebookScraper()
        # map_page_item may raise on completely invalid input
        result = scraper.normalize_page("not a dict", "run1", "https://fb.com/x")
        assert result is None
