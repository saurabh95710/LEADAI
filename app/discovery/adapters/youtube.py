"""YouTube channels.

Discovery (default): the existing video search actor (actor.youtube.main =
``streamers/youtube-scraper``) with ``searchQueries`` -> VIDEO items, deduped
to channels by channelId / channelUrl. Its schema also lists channel fields
(numberOfSubscribers, channelTotalVideos, isChannelVerified, channelLocation,
channelDescription) — UNVERIFIED on search items, so a channel lacking
subscribers or video count is enriched.

Optional third-party channel search (setting discovery.actor.youtube.channel_search,
e.g. ``maximedupre/youtube-channel-search-scraper``): searchTerms[],
maxTotalResults, maxChannelsPerSearchTerm and — documented in its input
schema — minSubscribers / maxSubscribers / minVideos / verifiedOnly /
countryHint (two letters), which the matching filters are passed as
-> channelId?, url?, title?, subscribers, videos, isVerified,
country, description, externalLinks (UNVERIFIED names, parsed defensively).

Both searches get the most specific place and the first business type
appended to the search text (``search_text``).

Enrichment: ``streamers/youtube-channel-scraper`` (startUrls) -> items carrying
numberOfSubscribers, channelTotalVideos, isChannelVerified, channelLocation,
channelDescription, channelDescriptionLinks[], inputChannelUrl, channelId.

Recent videos: actor.youtube.main with startUrls=[channel], sortVideosBy
"NEWEST" -> commentsCount, likes, date, url, id, commentsTurnedOff.
No business flag, no email.
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
    filter_places,
    filter_terms,
    pick,
    post_entry,
    query_record,
    search_text,
)
from app.discovery.capabilities import resolve_actor

# place name -> YouTube country hint (two letters) for the channel-search
# actor's ``countryHint``; only unambiguous country names
COUNTRY_HINTS = {
    "india": "IN", "united states": "US", "united states of america": "US", "usa": "US",
    "united kingdom": "GB", "great britain": "GB", "england": "GB", "canada": "CA",
    "australia": "AU", "new zealand": "NZ", "ireland": "IE", "germany": "DE", "france": "FR",
    "spain": "ES", "italy": "IT", "netherlands": "NL", "portugal": "PT", "brazil": "BR",
    "mexico": "MX", "argentina": "AR", "japan": "JP", "south korea": "KR", "indonesia": "ID",
    "malaysia": "MY", "singapore": "SG", "philippines": "PH", "thailand": "TH", "vietnam": "VN",
    "pakistan": "PK", "bangladesh": "BD", "sri lanka": "LK", "nepal": "NP",
    "united arab emirates": "AE", "uae": "AE", "saudi arabia": "SA", "qatar": "QA",
    "south africa": "ZA", "nigeria": "NG", "kenya": "KE", "egypt": "EG", "turkey": "TR",
}

# keyword filter -> channel-search actor input (documented in its schema)
_CHANNEL_SEARCH_LIMITS = (("min_followers", "minSubscribers"), ("max_followers", "maxSubscribers"),
                          ("min_posts", "minVideos"))


def country_hint(places: List[str]) -> Optional[str]:
    from app.discovery.filters import normalize_text
    for place in places:
        code = COUNTRY_HINTS.get(normalize_text(place))
        if code:
            return code
    return None


def _channel_url(item: Dict[str, Any]) -> Optional[str]:
    url = canonical(pick(item, "channelUrl", "inputChannelUrl", "url"), "youtube")
    if not url:
        cid = pick(item, "channelId", "id")
        if isinstance(cid, str) and cid.startswith("UC"):
            url = canonical(f"https://www.youtube.com/channel/{cid}", "youtube")
    return url


def _links(item: Dict[str, Any], *keys: str) -> List[str]:
    out: List[str] = []
    for key in keys:
        for x in as_list(item.get(key)):
            v = pick(x, "url") if isinstance(x, dict) else x
            if isinstance(v, str):
                out.append(v)
    return out


def _channel_fields(src: Dict[str, Any], video_item: bool = False) -> Dict[str, Any]:
    """Channel fields of an actor item. ``video_item``: the item describes a
    VIDEO (video search / channel scraper) — only its ``channel*`` fields are
    about the channel; its description, thumbnail, location and links are the
    video's and are left unknown."""
    if video_item:
        return {
            "followers": as_count(pick(src, "numberOfSubscribers", "channelSubscribers")),
            "posts_count": as_count(pick(src, "channelTotalVideos")),
            "verified": as_bool(pick(src, "isChannelVerified")),
            "country": pick(src, "channelLocation"),
            "bio": pick(src, "channelDescription"),
            "links": _links(src, "channelDescriptionLinks"),
            "profile_picture": pick(src, "channelAvatarUrl"),
        }
    bio = pick(src, "channelDescription", "description")
    return {
        "followers": as_count(pick(src, "numberOfSubscribers", "subscribers", "subscriberCount",
                                   "channelSubscribers")),
        "posts_count": as_count(pick(src, "channelTotalVideos", "videos", "videoCount")),
        "verified": as_bool(pick(src, "isChannelVerified", "isVerified", "verified")),
        "country": pick(src, "channelLocation", "country"),
        "bio": bio if isinstance(bio, str) else None,
        "links": _links(src, "channelDescriptionLinks", "externalLinks", "links"),
        "profile_picture": pick(src, "channelAvatarUrl", "avatar", "thumbnail"),
    }


class YouTubeAdapter(DiscoveryAdapter):
    platform = "youtube"

    def _profile(self, url: str, cid: Optional[str], name: Any, src: Dict[str, Any],
                 video_item: bool = False) -> Dict[str, Any]:
        f = _channel_fields(src, video_item)
        prof = empty_profile("youtube")
        prof.update({
            "profile_id": cid or url, "url": url, "name": name,
            "followers": f["followers"], "posts_count": f["posts_count"], "bio": f["bio"],
            "contacts": extract_contacts(f["bio"], websites=f["links"]),
            "verified": f["verified"], "is_business": None, "country": f["country"],
            "profile_picture": f["profile_picture"],
        })
        return prof

    def discovery_request(self, keyword: str, limit: int,
                          filters: Optional[Dict[str, Any]] = None) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
        limit = max(1, int(limit))
        filters = filters or {}
        places, terms = filter_places(filters), filter_terms(filters)
        query = search_text(keyword, places[0] if places else None, terms[0] if terms else None)
        channel_actor = resolve_actor("discovery.actor.youtube.channel_search")
        if channel_actor:
            extra: Dict[str, Any] = {}
            for key, actor_key in _CHANNEL_SEARCH_LIMITS:
                if isinstance(filters.get(key), int) and not isinstance(filters.get(key), bool):
                    extra[actor_key] = int(filters[key])
            if filters.get("verified_only") is True:
                extra["verifiedOnly"] = True
            hint = country_hint(places)
            if hint:
                extra["countryHint"] = hint
            run_input = {"searchTerms": [query], "maxTotalResults": limit, "maxChannelsPerSearchTerm": limit,
                         **extra}
            return channel_actor, run_input, query_record(self.platform, keyword, query, extra_inputs=extra)
        run_input = {"searchQueries": [query], "maxResults": min(limit * 2, 200), "downloadComments": False}
        return resolve_actor("actor.youtube.main"), run_input, query_record(self.platform, keyword, query)

    def discover(self, keyword: str, limit: int,
                 filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        limit = max(1, int(limit))
        actor, run_input, self.last_query = self.discovery_request(keyword, limit, filters)
        out: Dict[str, Dict[str, Any]] = {}
        if "searchTerms" in run_input:          # the channel-search actor is configured
            items = self._run(actor, run_input, "keyword-youtube-channel-search", keyword=keyword)
            for item in items:
                url = _channel_url(item)
                if not url:
                    continue
                cid = pick(item, "channelId")
                prof = self._profile(url, str(cid) if cid else None,
                                     pick(item, "title", "channelName", "name"), item)
                out.setdefault(prof["profile_id"], prof)
        else:
            items = self._run(actor, run_input, "keyword-youtube-search", keyword=keyword)
            for item in items:
                url = _channel_url({"channelUrl": item.get("channelUrl"), "channelId": item.get("channelId")})
                if not url:
                    continue
                cid = pick(item, "channelId")
                prof = self._profile(url, str(cid) if cid else None, pick(item, "channelName"), item,
                                     video_item=True)
                out.setdefault(prof["profile_id"], prof)
        return list(out.values())[:limit]

    def needs_enrichment(self, profile: Dict[str, Any]) -> bool:
        return profile.get("followers") is None or profile.get("posts_count") is None

    def enrich(self, profiles: List[Dict[str, Any]]) -> Dict[str, Optional[Dict[str, Any]]]:
        result: Dict[str, Optional[Dict[str, Any]]] = {p["profile_id"]: None for p in profiles}
        actor = resolve_actor("discovery.actor.youtube.channel")
        items = self._run(actor, {"startUrls": [{"url": p["url"]} for p in profiles], "maxResults": 1},
                          "keyword-youtube-channel")
        by_url = {p["url"].lower(): p for p in profiles}
        by_id = {p["profile_id"]: p for p in profiles}
        for item in items:
            src = item.get("aboutChannelInfo") if isinstance(item.get("aboutChannelInfo"), dict) else {}
            src = {**item, **src}
            prof = by_id.get(str(pick(src, "channelId") or ""))
            if prof is None:
                for key in ("inputChannelUrl", "channelUrl"):
                    u = canonical(src.get(key), "youtube")
                    if u and u.lower() in by_url:
                        prof = by_url[u.lower()]
                        break
            if prof is None and len(profiles) == 1:
                prof = profiles[0]
            if prof is None or result.get(prof["profile_id"]) is not None:
                continue
            # the channel scraper returns VIDEO items carrying channel* fields;
            # aboutChannelInfo (when present) describes the channel itself
            about = item.get("aboutChannelInfo") if isinstance(item.get("aboutChannelInfo"), dict) else None
            f = _channel_fields(item, video_item=True)
            if about:
                fa = _channel_fields(about)
                f = {k: (fa[k] if fa[k] not in (None, []) else f[k]) for k in f}
            merged = dict(prof)
            for key in ("followers", "posts_count", "verified", "country", "bio", "profile_picture"):
                if f[key] is not None:
                    merged[key] = f[key]
            merged["name"] = merged.get("name") or pick(src, "channelName")
            merged["contacts"] = extract_contacts(
                merged.get("bio"), emails=prof["contacts"]["emails"], phones=prof["contacts"]["phones"],
                websites=prof["contacts"]["websites"] + f["links"])
            merged["enriched"] = True
            result[prof["profile_id"]] = merged
        return result

    def recent_posts(self, profile: Dict[str, Any], n: int) -> List[Dict[str, Any]]:
        items = self._run(resolve_actor("actor.youtube.main"),
                          {"startUrls": [{"url": profile["url"]}], "maxResults": int(n),
                           "sortVideosBy": "NEWEST", "downloadComments": False},
                          "keyword-youtube-videos", keyword=profile.get("url"))
        posts = []
        for item in items:
            vid = pick(item, "id", "videoId")
            url = pick(item, "url") or (f"https://www.youtube.com/watch?v={vid}" if vid else None)
            comments = pick(item, "commentsCount", "commentCount")
            if comments is None and item.get("commentsTurnedOff") is True:
                comments = 0
            posts.append(post_entry(id=vid, url=url, comments=comments,
                                    likes=pick(item, "likes", "likeCount"),
                                    posted_at=pick(item, "date", "publishedAt", "uploadDate"), raw=item))
        return [p for p in posts if p["url"]][:n]
