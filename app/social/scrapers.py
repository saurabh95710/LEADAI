"""
Platform scrapers for URL-based lead search — one implementation per
platform over the shared ApifyConnector + the existing normalizers.

Facebook reuses the actors + mapping already used by the keyword search
(app/agent/search.py). Instagram/YouTube/LinkedIn run configurable Apify
actors (INSTAGRAM_ACTOR_ID / YOUTUBE_ACTOR_ID / LINKEDIN_ACTOR_ID in .env)
with best-effort field mapping into the same document shapes.
"""
import logging
import re
from typing import Any, Dict, List, Optional

from app.config import get_settings
from app.connectors.apify_connector import ApifyConnector

logger = logging.getLogger(__name__)
settings = get_settings()


def _as_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    t = str(value).strip()
    m = re.match(r"([\d.,]+\s*[KMBkmb]?)", t)
    if not m:
        return None
    token = m.group(1).replace(",", "").strip()
    mult = 1
    if token[-1:].upper() == "K":
        mult, token = 1000, token[:-1]
    elif token[-1:].upper() == "M":
        mult, token = 1000000, token[:-1]
    elif token[-1:].upper() == "B":
        mult, token = 1000000000, token[:-1]
    try:
        return int(float(token) * mult)
    except (TypeError, ValueError):
        return None


def _pick(item: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        v = item.get(key)
        if v not in (None, "", [], {}):
            return v
    return None


class SocialMediaScraper:
    """Common interface: raw Apify items in, normalized LeadAI docs out."""

    platform: str = ""
    comments_supported: bool = True

    def __init__(self, connector: Optional[ApifyConnector] = None):
        self.connector = connector or ApifyConnector()

    def has_token(self) -> bool:
        return self.connector.has_token()

    def fetch_page_details(self, url: str, should_abort: Optional[callable] = None) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def fetch_posts(self, url: str, max_posts: int,
                    should_abort: Optional[callable] = None) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def fetch_comments(self, post_url: str, max_comments: int,
                       should_abort: Optional[callable] = None) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def normalize_page(self, item: Dict[str, Any], run_id: str, url: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def normalize_post(self, item: Dict[str, Any], page_doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def normalize_comment(self, item: Dict[str, Any], post_doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        raise NotImplementedError


class FacebookScraper(SocialMediaScraper):
    """Reuses the existing Facebook actors + app/agent/search.py mapping."""

    platform = "facebook"

    def fetch_page_details(self, url: str,
                           should_abort: Optional[callable] = None) -> List[Dict[str, Any]]:
        logger.info("[URL SEARCH] Facebook: scraping page details")
        return self.connector.scrape_facebook_pages_by_urls([url],
                                                            should_abort=should_abort)

    def fetch_posts(self, url: str, max_posts: int,
                    should_abort: Optional[callable] = None) -> List[Dict[str, Any]]:
        logger.info("[URL SEARCH] Facebook: scraping posts (max %s)", max_posts)
        return self.connector.scrape_facebook_posts(
            [url], posts_per_page=max_posts, should_abort=should_abort)

    def fetch_comments(self, post_url: str, max_comments: int,
                       should_abort: Optional[callable] = None) -> List[Dict[str, Any]]:
        logger.info("[URL SEARCH] Facebook: scraping comments (max %s)", max_comments)
        return self.connector.scrape_facebook_comments(
            [post_url], comments_per_post=max_comments, should_abort=should_abort)

    def normalize_page(self, item: Dict[str, Any], run_id: str, url: str) -> Optional[Dict[str, Any]]:
        # the existing keyword-search normalizer builds the same page document
        from app.agent.search import map_page_item
        doc = map_page_item(item, run_id, url)
        if doc:
            doc["platform"] = "facebook"
            doc["source_type"] = "page_url"
            doc["source_page_url"] = url
        return doc

    def normalize_post(self, item: Dict[str, Any], page_doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        from app.agent.search import map_post_item
        doc = map_post_item(item, page_doc)
        if doc:
            doc["platform"] = "facebook"
        return doc

    def normalize_comment(self, item: Dict[str, Any], post_doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        from app.agent.search import map_comment_item
        doc = map_comment_item(item, post_doc)
        if doc:
            doc["platform"] = "facebook"
        return doc


class InstagramScraper(SocialMediaScraper):
    platform = "instagram"

    @property
    def _actor_id(self) -> str:
        return settings.instagram_actor_id

    def fetch_page_details(self, url: str,
                           should_abort: Optional[callable] = None) -> List[Dict[str, Any]]:
        logger.info("[URL SEARCH] Instagram: scraping profile details")
        return self.connector.scrape_actor(
            self._actor_id,
            {"usernameType": "link", "startUrls": [{"url": url}],
             "resultsType": "details"},
            "instagram-profile",
            keyword=url,
            should_abort=should_abort,
        )

    def fetch_posts(self, url: str, max_posts: int,
                    should_abort: Optional[callable] = None) -> List[Dict[str, Any]]:
        logger.info("[URL SEARCH] Instagram: scraping posts (max %s)", max_posts)
        return self.connector.scrape_actor(
            self._actor_id,
            {"usernameType": "link", "startUrls": [{"url": url}],
             "resultsType": "posts", "postsLimit": max_posts},
            "instagram-posts",
            keyword=url,
            should_abort=should_abort,
        )

    def fetch_comments(self, post_url: str, max_comments: int,
                       should_abort: Optional[callable] = None) -> List[Dict[str, Any]]:
        logger.info("[URL SEARCH] Instagram: scraping comments (max %s)", max_comments)
        return self.connector.scrape_actor(
            self._actor_id,
            {"usernameType": "link", "postUrls": [post_url],
             "resultsType": "comments", "commentsLimit": max_comments},
            "instagram-comments",
            keyword=post_url,
            should_abort=should_abort,
        )

    def normalize_page(self, item: Dict[str, Any], run_id: str, url: str) -> Optional[Dict[str, Any]]:
        if not isinstance(item, dict):
            return None
        username = _pick(item, "username", "ownerUsername", "name")
        if not username:
            return None
        return {
            "page_id": str(_pick(item, "pk", "id") or "").strip() or None,
            "page_name": str(_pick(item, "fullName", "full_name") or username).strip(),
            "facebook_url": url,
            "platform": "instagram",
            "category": None,
            "about": _pick(item, "biography", "bio", "description"),
            "followers": _as_int(_pick(item, "followerCount", "followersCount", "followers")),
            "likes": _as_int(_pick(item, "followingCount", "likesCount")),
            "verified": item.get("verified") if isinstance(item.get("verified"), bool) else None,
            "phone": None,
            "email": _pick(item, "contactEmail", "email"),
            "whatsapp": None,
            "website": _pick(item, "website", "externalUrl"),
            "address": None,
            "city": None,
            "state": None,
            "country": _pick(item, "geolocationCountry"),
            "profile_picture": _pick(item, "profilePicUrl", "profilePicUrlHD", "picUrl"),
            "cover_image": None,
            "source_type": "profile_url",
            "source_page_url": url,
            "search_run_id": run_id,
            "search_keyword": url,
            "source": "apify_url_search",
            "posts_status": "not_started",
            "comments_status": "not_started",
        }

    def normalize_post(self, item: Dict[str, Any], page_doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(item, dict):
            return None
        code = _pick(item, "shortCode", "shortcode", "code")
        post_url = str(_pick(item, "url", "webLink", "postUrl") or "").strip()
        if not post_url and code:
            post_url = f"{(page_doc.get('facebook_url') or '').rstrip('/')}/p/{code}/"
        if not post_url:
            return None
        caption = str(_pick(item, "caption", "text", "description") or "").strip() or None
        image = _pick(item, "displayUrl", "display_url", "imageUrl")
        video = _pick(item, "videoUrl", "video_url")
        caption_split = re.split(r"\n|#", caption) if caption else []
        return {
            "post_id": str(_pick(item, "id", "pk") or "").strip() or None,
            "post_url": post_url,
            "page_id": page_doc.get("page_id"),
            "page_name": page_doc.get("page_name"),
            "platform": "instagram",
            "caption": caption,
            "images": [str(image)] if image else [],
            "videos": [str(video)] if video else [],
            "external_links": [w for w in (caption_split or []) if "http" in w][:5],
            "published_date": str(_pick(item, "timestamp", "date", "publishedAt") or "").strip() or None,
            "likes_count": _as_int(_pick(item, "likesCount", "likeCount", "likes")),
            "total_comment_count": _as_int(_pick(item, "commentsCount", "commentCount")),
            "scraped_comment_count": None,
            "comments_count": _as_int(_pick(item, "commentsCount", "commentCount")),
            "shares_count": None,
            "is_relevant": True,
            "is_qualifying": False,
            "page_ref": str(page_doc["_id"]),
            "search_run_id": page_doc.get("search_run_id"),
            "provider": "apify",
        }

    def normalize_comment(self, item: Dict[str, Any], post_doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(item, dict):
            return None
        comment_id = str(_pick(item, "id", "pk", "commentId") or "").strip() or None
        text = str(_pick(item, "text", "commentText", "comment") or "").strip() or None
        if not text and not comment_id:
            return None
        post_url = post_doc.get("post_url") or ""
        comment_url = str(_pick(item, "url", "commentUrl") or "").strip()
        if not comment_url and comment_id:
            comment_url = post_url.split("?")[0] + f"?comment_id={comment_id}"
        username = str(_pick(item, "username", "ownerUsername") or "").strip()
        author_name = str(_pick(item, "authorName", "authorUsername") or "").strip() or (username or None)
        return {
            "comment_id": comment_id,
            "comment_url": comment_url,
            "author_name": author_name,
            "author_profile_url": _pick(item, "authorProfileUrl", "profileUrl") or (
                f"https://www.instagram.com/{username}" if username else None),
            "text": text,
            "published_date": str(_pick(item, "timestamp", "date", "publishedAt") or "").strip() or None,
            "reactions_count": _as_int(_pick(item, "likesCount", "likes")),
            "post_id": post_doc.get("post_id") or str(post_doc.get("_id") or ""),
            "post_url": post_url,
            "page_id": post_doc.get("page_id"),
            "post_ref": str(post_doc["_id"]),
            "search_run_id": post_doc.get("search_run_id"),
            "platform": "instagram",
        }


class YouTubeScraper(SocialMediaScraper):
    platform = "youtube"
    comments_supported = False  # video comments are not collected in URL mode

    @property
    def _actor_id(self) -> str:
        return settings.youtube_actor_id

    def fetch_page_details(self, url: str,
                           should_abort: Optional[callable] = None) -> List[Dict[str, Any]]:
        logger.info("[URL SEARCH] YouTube: scraping channel details")
        return self.connector.scrape_actor(
            self._actor_id,
            {"startUrls": [{"url": url}], "maxResults": 1,
             "extractFaqs": False, "extractChannelInfo": True},
            "youtube-channel",
            keyword=url,
            should_abort=should_abort,
        )

    def fetch_posts(self, url: str, max_posts: int,
                    should_abort: Optional[callable] = None) -> List[Dict[str, Any]]:
        logger.info("[URL SEARCH] YouTube: scraping videos (max %s)", max_posts)
        return self.connector.scrape_actor(
            self._actor_id,
            {"startUrls": [{"url": url}], "maxResults": max_posts,
             "onlyChannelVideos": True, "extractChannelInfo": False},
            "youtube-videos",
            keyword=url,
            should_abort=should_abort,
        )

    def fetch_comments(self, post_url: str, max_comments: int,
                       should_abort: Optional[callable] = None) -> List[Dict[str, Any]]:
        return []

    def normalize_page(self, item: Dict[str, Any], run_id: str, url: str) -> Optional[Dict[str, Any]]:
        if not isinstance(item, dict):
            return None
        channel = item.get("channel") if isinstance(item.get("channel"), dict) else item
        name = _pick(item, "channelTitle", "title") or _pick(channel, "title", "name")
        if not name:
            return None
        return {
            "page_id": str(_pick(item, "channelId") or _pick(channel, "id") or "").strip() or None,
            "page_name": str(name).strip(),
            "facebook_url": url,
            "platform": "youtube",
            "category": None,
            "about": _pick(item, "description") or _pick(channel, "description"),
            "followers": _as_int(_pick(channel, "subscriberCount", "subscribers")),
            "likes": None,
            "verified": None,
            "phone": None,
            "email": None,
            "whatsapp": None,
            "website": None,
            "address": _pick(channel, "country"),
            "city": None,
            "state": None,
            "country": _pick(channel, "country"),
            "profile_picture": str(_pick(channel, "avatar", "avatarUrl", "thumbnailUrl") or "").strip() or None,
            "cover_image": str(_pick(channel, "bannerUrl", "banner") or "").strip() or None,
            "source_type": "channel_url",
            "source_page_url": url,
            "search_run_id": run_id,
            "search_keyword": url,
            "source": "apify_url_search",
            "posts_status": "not_started",
            "comments_status": "not_started",
        }

    def normalize_post(self, item: Dict[str, Any], page_doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(item, dict):
            return None
        post_url = str(_pick(item, "url", "link") or "").strip()
        if not post_url:
            vid = _pick(item, "id", "videoId")
            if vid:
                post_url = f"https://www.youtube.com/watch?v={vid}"
        if not post_url:
            return None
        caption = str(_pick(item, "title") or "").strip() or None
        return {
            "post_id": str(_pick(item, "id", "videoId") or "").strip() or None,
            "post_url": post_url,
            "page_id": page_doc.get("page_id"),
            "page_name": page_doc.get("page_name"),
            "platform": "youtube",
            "caption": caption,
            "images": [str(_pick(item, "thumbnailUrl", "thumbnails"))] if _pick(item, "thumbnailUrl", "thumbnails") else [],
            "videos": [post_url],
            "external_links": [],
            "published_date": str(_pick(item, "publishedAt", "date", "publishDate") or "").strip() or None,
            "likes_count": _as_int(_pick(item, "likeCount", "likes")),
            "total_comment_count": _as_int(_pick(item, "commentCount", "videoCommentsCount")),
            "scraped_comment_count": None,
            "comments_count": _as_int(_pick(item, "commentCount", "videoCommentsCount")),
            "shares_count": None,
            "is_relevant": True,
            "is_qualifying": False,
            "page_ref": str(page_doc["_id"]),
            "search_run_id": page_doc.get("search_run_id"),
            "provider": "apify",
            "description": str(_pick(item, "description") or "").strip() or None,
        }

    def normalize_comment(self, item: Dict[str, Any], post_doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return None


class LinkedInScraper(SocialMediaScraper):
    platform = "linkedin"
    comments_supported = False  # comments are not collected in URL mode

    @property
    def _actor_id(self) -> str:
        return settings.linkedin_actor_id

    def fetch_page_details(self, url: str,
                           should_abort: Optional[callable] = None) -> List[Dict[str, Any]]:
        logger.info("[URL SEARCH] LinkedIn: scraping company details")
        return self.connector.scrape_actor(
            self._actor_id,
            {"startUrls": [{"url": url}], "deepScrape": False,
             "scrapeCompanyOrOrganizationInfo": True, "scrapePosts": False,
             "maxPosts": 0},
            "linkedin-page",
            keyword=url,
            should_abort=should_abort,
        )

    def fetch_posts(self, url: str, max_posts: int,
                    should_abort: Optional[callable] = None) -> List[Dict[str, Any]]:
        logger.info("[URL SEARCH] LinkedIn: scraping posts (max %s)", max_posts)
        return self.connector.scrape_actor(
            self._actor_id,
            {"startUrls": [{"url": url}], "deepScrape": False,
             "scrapeCompanyOrOrganizationInfo": False, "scrapePosts": True,
             "maxPosts": max_posts},
            "linkedin-posts",
            keyword=url,
            should_abort=should_abort,
        )

    def fetch_comments(self, post_url: str, max_comments: int,
                       should_abort: Optional[callable] = None) -> List[Dict[str, Any]]:
        return []

    def normalize_page(self, item: Dict[str, Any], run_id: str, url: str) -> Optional[Dict[str, Any]]:
        if not isinstance(item, dict):
            return None
        name = _pick(item, "name", "title", "headline")
        if not name:
            return None
        return {
            "page_id": str(_pick(item, "urn", "id", "memberId") or "").strip() or None,
            "page_name": str(name).strip(),
            "facebook_url": url,
            "platform": "linkedin",
            "category": _pick(item, "industry"),
            "about": _pick(item, "description", "about"),
            "followers": _as_int(_pick(item, "followerCount", "followers")),
            "likes": None,
            "verified": None,
            "phone": _pick(item, "phone", "phoneNumber"),
            "email": None,
            "whatsapp": None,
            "website": _pick(item, "website"),
            "address": _pick(item, "address"),
            "city": _pick(item, "city"),
            "state": None,
            "country": _pick(item, "country"),
            "profile_picture": _pick(item, "logo", "logoUrl", "pictureUrl", "imageUrl"),
            "cover_image": _pick(item, "coverImageUrl", "backgroundUrl"),
            "source_type": "company_url",
            "source_page_url": url,
            "search_run_id": run_id,
            "search_keyword": url,
            "source": "apify_url_search",
            "posts_status": "not_started",
            "comments_status": "not_started",
        }

    def normalize_post(self, item: Dict[str, Any], page_doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(item, dict):
            return None
        post_url = str(_pick(item, "url", "postUrl", "link") or "").strip()
        if not post_url:
            share_id = _pick(item, "urn", "id")
            if share_id:
                post_url = f"https://www.linkedin.com/feed/update/urn:li:activity:{str(share_id).split(':')[-1]}"
        if not post_url:
            return None
        text = str(_pick(item, "text", "content", "description") or "").strip() or None
        return {
            "post_id": str(_pick(item, "urn", "id") or "").strip() or None,
            "post_url": post_url,
            "page_id": page_doc.get("page_id"),
            "page_name": page_doc.get("page_name"),
            "platform": "linkedin",
            "caption": text,
            "images": [],
            "videos": [],
            "external_links": [],
            "published_date": str(_pick(item, "date", "publishedAt", "createdAt") or "").strip() or None,
            "likes_count": _as_int(_pick(item, "likesCount", "likeCount")),
            "total_comment_count": _as_int(_pick(item, "commentsCount", "commentCount")),
            "scraped_comment_count": None,
            "comments_count": _as_int(_pick(item, "commentsCount", "commentCount")),
            "shares_count": _as_int(_pick(item, "sharesCount", "shareCount")),
            "is_relevant": True,
            "is_qualifying": False,
            "page_ref": str(page_doc["_id"]),
            "search_run_id": page_doc.get("search_run_id"),
            "provider": "apify",
        }

    def normalize_comment(self, item: Dict[str, Any], post_doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return None


def get_scraper(platform: str) -> SocialMediaScraper:
    cls = {
        "facebook": FacebookScraper,
        "instagram": InstagramScraper,
        "youtube": YouTubeScraper,
        "linkedin": LinkedInScraper,
    }.get(platform)
    if cls is None:
        raise ValueError(f"Unsupported platform: {platform}")
    return cls()