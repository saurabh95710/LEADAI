"""LinkedIn companies (third-party actors; OFF by default — LinkedIn's terms
forbid scraping, the Super Admin turns it on knowingly).

Discovery + profile: ``harvestapi/linkedin-company-search`` — input
searchQuery, maxItems, scraperMode "full", locations[] (free text; sent when
the search has a location — industryIds are numeric LinkedIn ids, so the
business type is only checked afterwards); output name, linkedinUrl, website,
phone, description, tagline, followerCount, employeeCount, industries[],
headquarter{city, country, countryFull}, pageVerified.

Recent posts: the existing ``harvestapi/linkedin-company-posts``
(actor.linkedin.posts) through the URL-search scraper -> engagement{likes,
comments, shares}, postedAt{timestamp, date}, linkedinUrl / url, id.
Total posts and email are not available; every result is a company.
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


def _industries(item: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for ind in as_list(item.get("industries")):
        name = ind.get("name") if isinstance(ind, dict) else ind
        if isinstance(name, str) and name.strip():
            out.append(name.strip())
    return out


def _industry(item: Dict[str, Any]) -> Optional[str]:
    for ind in as_list(item.get("industries")):
        name = ind.get("name") if isinstance(ind, dict) else ind
        if isinstance(name, str) and name.strip():
            return name.strip()
    return pick(item, "industry")


class LinkedInAdapter(DiscoveryAdapter):
    platform = "linkedin"

    def profile_from_item(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        url = canonical(pick(item, "linkedinUrl", "url", "companyUrl"), "linkedin")
        if not url:
            return None
        hq = item.get("headquarter") if isinstance(item.get("headquarter"), dict) else {}
        bio = pick(item, "description", "tagline")
        address = ", ".join(str(hq[k]) for k in ("line1", "city", "geographicArea") if hq.get(k)) or None
        prof = empty_profile("linkedin")
        prof.update({
            "profile_id": str(pick(item, "id", "universalName") or url),
            "url": url, "name": pick(item, "name"),
            "followers": as_count(pick(item, "followerCount", "followersCount")),
            "posts_count": None, "bio": bio,
            "contacts": extract_contacts(bio, phones=[pick(item, "phone")],
                                         websites=[pick(item, "website")]),
            "verified": as_bool(item.get("pageVerified")),
            "is_business": True,
            "category": _industry(item),
            "categories": _industries(item),
            "city": pick(hq, "city"),
            "country": pick(hq, "countryFull", "country"),
            "address": address,
            "profile_picture": pick(item, "logo", "logoUrl"),
            "employees": as_count(pick(item, "employeeCount")),
        })
        return prof

    def discovery_request(self, keyword: str, limit: int,
                          filters: Optional[Dict[str, Any]] = None) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
        run_input: Dict[str, Any] = {"searchQuery": keyword, "maxItems": max(1, int(limit)),
                                     "scraperMode": "full"}
        loc = location_text(filters)
        if loc:
            run_input["locations"] = [loc]
        return (resolve_actor("discovery.actor.linkedin.search"), run_input,
                query_record(self.platform, keyword, keyword, locations=run_input.get("locations")))

    def discover(self, keyword: str, limit: int,
                 filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        actor, run_input, self.last_query = self.discovery_request(keyword, limit, filters)
        items = self._run(actor, run_input, "keyword-linkedin-search", keyword=keyword)
        out: List[Dict[str, Any]] = []
        for item in items:
            prof = self.profile_from_item(item)
            if prof:
                out.append(prof)
        return out

    def recent_posts(self, profile: Dict[str, Any], n: int) -> List[Dict[str, Any]]:
        from app.social.scrapers import get_scraper
        items = get_scraper("linkedin", self.connector).fetch_posts(
            profile["url"], int(n), should_abort=self.should_abort)
        posts = []
        for item in items or []:
            if not isinstance(item, dict) or item.get("error"):
                continue
            eng = item.get("engagement") if isinstance(item.get("engagement"), dict) else {}
            posted = item.get("postedAt") if isinstance(item.get("postedAt"), dict) else {}
            posts.append(post_entry(id=pick(item, "id", "urn"), url=pick(item, "linkedinUrl", "url", "postUrl"),
                                    comments=eng.get("comments"), likes=eng.get("likes"),
                                    shares=eng.get("shares"),
                                    posted_at=posted.get("timestamp") or posted.get("date"), raw=item))
        return [p for p in posts if p["url"]][:n]
