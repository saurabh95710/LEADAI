"""Facebook Pages.

Discovery: ``apify/facebook-search-scraper`` — input ``categories`` (the
search terms) + ``resultsLimit`` + ``locations`` (free text, applied by the
actor to every term; sent when the search has a location); the actor's
default search type ("pages-google") is used. Verified output fields: pageId, pageUrl / url,
title, categories[], info[], likes, followers, email, phone, address,
website, websites[], intro, isVerified. UNVERIFIED: whether every result
carries followers / contacts — when followers is missing the profile is
enriched with the existing pages scraper (actor.facebook.pages), parsed by
the same mapper URL search uses.

Recent posts: the existing posts scraper (actor.facebook.posts) -> likes,
comments, shares, time / timestamp, url. Total post count: not available.
"""
from typing import Any, Dict, List, Optional, Tuple

from app.discovery.adapters.base import (
    DiscoveryAdapter,
    as_bool,
    as_list,
    as_count,
    canonical,
    empty_profile,
    extract_contacts,
    location_text,
    pick,
    post_entry,
    query_record,
)
from app.discovery.capabilities import resolve_actor

ENRICH_CHUNK = 20


def _websites(item: Dict[str, Any]) -> List[str]:
    sites = as_list(item.get("websites"))
    one = item.get("website")
    return ([one] if isinstance(one, str) else []) + [s for s in sites if isinstance(s, str)]


def _categories(item: Dict[str, Any]) -> List[str]:
    return [c.strip() for c in as_list(item.get("categories")) if isinstance(c, str) and c.strip()]


def _category(item: Dict[str, Any]) -> Optional[str]:
    cats = item.get("categories")
    if isinstance(cats, list):
        for c in cats:
            if isinstance(c, str) and c.strip():
                return c.strip()
    return pick(item, "category", "categoryName")


class FacebookAdapter(DiscoveryAdapter):
    platform = "facebook"

    def profile_from_item(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        url = canonical(pick(item, "pageUrl", "url", "facebookUrl", "link"), "facebook")
        if not url:
            return None
        bio = pick(item, "intro", "about", "description", "bio")
        prof = empty_profile("facebook")
        prof.update({
            "profile_id": str(pick(item, "pageId", "id") or url),
            "url": url,
            "name": pick(item, "title", "name", "pageName"),
            "followers": as_count(pick(item, "followers", "followersCount", "followerCount")),
            "posts_count": None,
            "bio": bio,
            "contacts": extract_contacts(bio, emails=[pick(item, "email")], phones=[pick(item, "phone")],
                                         websites=_websites(item)),
            "verified": as_bool(pick(item, "isVerified", "verified")),
            "is_business": True,
            "category": _category(item),
            "categories": _categories(item),
            "address": pick(item, "address"),
            "profile_picture": pick(item, "profilePictureUrl", "profilePhoto"),
            "likes": as_count(pick(item, "likes", "likeCount")),
        })
        return prof

    def discovery_request(self, keyword: str, limit: int,
                          filters: Optional[Dict[str, Any]] = None) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
        run_input: Dict[str, Any] = {"categories": [keyword], "resultsLimit": max(1, int(limit))}
        loc = location_text(filters)
        if loc:
            run_input["locations"] = [loc]
        return (resolve_actor("discovery.actor.facebook.search"), run_input,
                query_record(self.platform, keyword, keyword, locations=run_input.get("locations")))

    def discover(self, keyword: str, limit: int,
                 filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        actor, run_input, self.last_query = self.discovery_request(keyword, limit, filters)
        items = self._run(actor, run_input, "keyword-facebook-search", keyword=keyword)
        out: List[Dict[str, Any]] = []
        for item in items:
            prof = self.profile_from_item(item)
            if prof:
                out.append(prof)
        return out

    def needs_enrichment(self, profile: Dict[str, Any]) -> bool:
        return profile.get("followers") is None

    def enrich(self, profiles: List[Dict[str, Any]]) -> Dict[str, Optional[Dict[str, Any]]]:
        from app.agent.search import map_page_item
        result: Dict[str, Optional[Dict[str, Any]]] = {p["profile_id"]: None for p in profiles}
        by_url = {p["url"].lower(): p for p in profiles}
        items = self.connector.scrape_facebook_pages_by_urls([p["url"] for p in profiles],
                                                            should_abort=self.should_abort)
        for item in items or []:
            if not isinstance(item, dict) or item.get("error"):
                continue
            url = canonical(pick(item, "facebookUrl", "pageUrl", "url", "inputUrl"), "facebook")
            prof = by_url.get((url or "").lower())
            if prof is None:
                pid = str(pick(item, "pageId", "id") or "")
                prof = next((p for p in profiles if pid and p["profile_id"] == pid), None)
            if prof is None and len(profiles) == 1:
                prof = profiles[0]
            if prof is None:
                continue
            mapped = map_page_item(item, "", prof["url"]) or {}
            merged = dict(prof)
            followers = as_count(pick(item, "followers", "followersCount")) or mapped.get("followers")
            if followers is not None:
                merged["followers"] = followers
            bio = mapped.get("about") or prof.get("bio")
            merged["bio"] = bio
            merged["contacts"] = extract_contacts(
                bio, emails=(prof["contacts"]["emails"] + [mapped.get("email")]),
                phones=(prof["contacts"]["phones"] + [mapped.get("phone"), mapped.get("whatsapp")]),
                websites=(prof["contacts"]["websites"] + [mapped.get("website")]))
            for key, src in (("name", "page_name"), ("category", "category"), ("address", "address"),
                             ("country", "country"), ("profile_picture", "profile_picture")):
                if mapped.get(src) and not merged.get(key):
                    merged[key] = mapped[src]
            if merged.get("verified") is None and isinstance(mapped.get("verified"), bool):
                merged["verified"] = mapped["verified"]
            if mapped.get("likes") is not None:
                merged["likes"] = mapped["likes"]
            merged["enriched"] = True
            result[prof["profile_id"]] = merged
        return result

    def recent_posts(self, profile: Dict[str, Any], n: int) -> List[Dict[str, Any]]:
        from app.agent.search import _comment_count
        items = self.connector.scrape_facebook_posts([profile["url"]], posts_per_page=int(n),
                                                     should_abort=self.should_abort)
        posts = []
        for item in items or []:
            if not isinstance(item, dict) or item.get("error"):
                continue
            posts.append(post_entry(
                id=pick(item, "postId", "id"), url=pick(item, "url", "postUrl", "link"),
                comments=_comment_count(item), likes=pick(item, "likes", "likesCount"),
                shares=pick(item, "shares", "sharesCount"),
                posted_at=pick(item, "time", "timestamp", "date"), raw=item))
        return [p for p in posts if p["url"]][:n]
