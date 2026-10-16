"""Instagram: ``apify/instagram-search-scraper`` (searchType "user") returns
profile fields AND ~12 latest posts in one call, so there is no enrichment
stage. More posts come from ``apify/instagram-post-scraper``.

Verified fields: username, fullName, biography, externalUrl, externalUrls[],
followersCount, postsCount, isBusinessAccount, businessCategoryName, verified,
private, latestPosts[]{id, shortCode, url, commentsCount, likesCount,
timestamp}. Not in the output: country, email (only regex on bio / links).
Defensive: businessEmail / publicEmail / businessPhoneNumber / city /
businessAddress may be absent.
"""
import re
from typing import Any, Dict, List, Optional, Tuple

from app.discovery.adapters.base import (
    DiscoveryAdapter,
    as_bool,
    as_list,
    as_count,
    canonical,
    empty_profile,
    extract_contacts,
    filter_places,
    latest_post_at,
    pick,
    post_entry,
    query_record,
    search_text,
)
from app.discovery.capabilities import resolve_actor

# Characters Instagram's search actor refuses in `search` (its input pattern:
# ^[^!?.,:;\-+=*&%$#@/\\~^|<>()[\]{}"'`]+…); a comma would split the search
# into several terms. They are replaced by spaces: "e-commerce" -> "e commerce".
_IG_FORBIDDEN = re.compile(r"""[!?.,:;\-+=*&%$#@/\\~^|<>()\[\]{}"'`]""")


def instagram_safe(text: str) -> str:
    """Search text Instagram's search actor accepts."""
    return " ".join(_IG_FORBIDDEN.sub(" ", str(text or "")).split())


def _post(p: Dict[str, Any]) -> Dict[str, Any]:
    code = pick(p, "shortCode", "shortcode", "code")
    url = pick(p, "url", "postUrl") or (f"https://www.instagram.com/p/{code}/" if code else None)
    return post_entry(id=pick(p, "id", "pk") or code, url=url,
                      comments=pick(p, "commentsCount", "commentCount"),
                      likes=pick(p, "likesCount", "likeCount"),
                      posted_at=pick(p, "timestamp", "takenAt", "date"), raw=p)


class InstagramAdapter(DiscoveryAdapter):
    platform = "instagram"

    def profile_from_item(self, item: Dict[str, Any]) -> Dict[str, Any] | None:
        username = pick(item, "username")
        if not username:
            return None
        url = canonical(f"https://www.instagram.com/{str(username).strip()}/", "instagram")
        if not url:
            return None
        bio = pick(item, "biography", "bio")
        links = [pick(x, "url") if isinstance(x, dict) else x for x in as_list(item.get("externalUrls"))]
        links = [x for x in links if isinstance(x, str)]
        address = item.get("businessAddress")
        if isinstance(address, dict):
            address = ", ".join(str(v) for v in address.values() if v and isinstance(v, (str, int)))
        posts = [_post(p) for p in as_list(item.get("latestPosts")) if isinstance(p, dict)]
        posts = [p for p in posts if p["url"]]
        prof = empty_profile("instagram")
        prof.update({
            "profile_id": str(username).strip().lower(),
            "url": url, "handle": str(username).strip(),
            "name": pick(item, "fullName", "full_name") or str(username),
            "followers": as_count(pick(item, "followersCount", "followerCount")),
            "posts_count": as_count(pick(item, "postsCount", "mediaCount")),
            "bio": bio,
            "contacts": extract_contacts(
                bio, emails=[pick(item, "businessEmail", "publicEmail", "email")],
                phones=[pick(item, "businessPhoneNumber", "publicPhoneNumber", "phone")],
                websites=[pick(item, "externalUrl", "website")] + links),
            "verified": as_bool(item.get("verified")),
            "is_business": as_bool(item.get("isBusinessAccount")),
            "category": pick(item, "businessCategoryName", "categoryName"),
            "country": None,
            "address": address if isinstance(address, str) and address.strip() else pick(item, "city"),
            "private": as_bool(item.get("private")),
            "profile_picture": pick(item, "profilePicUrlHD", "profilePicUrl"),
            "recent_posts": posts,
            "last_post_at": latest_post_at(posts),
        })
        return prof

    def discovery_request(self, keyword: str, limit: int,
                          filters: Optional[Dict[str, Any]] = None) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
        # only the most specific place goes into the search text: Instagram's
        # account search matches names / usernames, and a longer phrase
        # (keyword + place + business type) finds almost nothing. The business
        # type is checked on each profile afterwards.
        places = filter_places(filters)
        query = instagram_safe(search_text(keyword, places[0] if places else None))
        run_input = {"search": query, "searchType": "user", "searchLimit": max(1, min(int(limit), 250))}
        return (resolve_actor("discovery.actor.instagram.search"), run_input,
                query_record(self.platform, keyword, query))

    def discover(self, keyword: str, limit: int,
                 filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        actor, run_input, self.last_query = self.discovery_request(keyword, limit, filters)
        items = self._run(actor, run_input, "keyword-instagram-search", keyword=keyword)
        out: List[Dict[str, Any]] = []
        for item in items:
            prof = self.profile_from_item(item)
            if prof:
                out.append(prof)
        return out

    def recent_posts(self, profile: Dict[str, Any], n: int) -> List[Dict[str, Any]]:
        username = profile.get("handle") or profile.get("profile_id")
        items = self._run(resolve_actor("discovery.actor.instagram.posts"),
                          {"username": [username], "resultsLimit": int(n), "skipPinnedPosts": False},
                          "keyword-instagram-posts", keyword=profile.get("url"))
        posts = [_post(p) for p in items]
        return [p for p in posts if p["url"]][:n]
