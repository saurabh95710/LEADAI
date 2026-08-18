"""
Social-media URL detection + canonicalization for the URL-based lead search.

  detect_social_url(url) → (platform, canonical_url)
  raises UrlError with a user-facing message for invalid / unsupported URLs.

Canonicalization only ever *drops* tracking query params and trailing
slashes — it never rewrites the path/slug that identifies the target, so
the actual page the actor fetches is always the same one the user gave.
"""
import logging
import re
from urllib.parse import parse_qsl, urlsplit

logger = logging.getLogger(__name__)

SUPPORTED_PLATFORMS = ("facebook", "instagram", "youtube", "linkedin")

INVALID_MSG = (
    "Invalid social media URL. "
    "Please provide a valid Facebook, Instagram, YouTube, or LinkedIn URL."
)
UNSUPPORTED_MSG = "This social media platform is not currently supported."
PRIVATE_MSG = "This page/profile is private or its content is not publicly accessible."

_HOST_ALIASES = {
    "facebook": {"www.facebook.com", "facebook.com", "m.facebook.com",
                 "web.facebook.com", "fb.com"},
    "instagram": {"www.instagram.com", "instagram.com"},
    "youtube": {"www.youtube.com", "youtube.com", "m.youtube.com", "youtu.be"},
    "linkedin": {"www.linkedin.com", "linkedin.com", "in.linkedin.com"},
}

_CANONICAL_BASE = {
    "facebook": "https://www.facebook.com",
    "instagram": "https://www.instagram.com",
    "youtube": "https://www.youtube.com",
    "linkedin": "https://www.linkedin.com",
}


class UrlError(ValueError):
    """A URL the user gave cannot be searched. `.kind`: invalid | unsupported."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


def _platform_of_host(host: str) -> str | None:
    host = (host or "").strip(".").lower()
    for platform, aliases in _HOST_ALIASES.items():
        for alias in aliases:
            if host == alias or host.endswith("." + alias):
                return platform
    return None


def platform_from_url(url: str) -> str | None:
    """Return the normalized platform of any URL: facebook|instagram|
    linkedin|youtube — or None when it cannot be determined.

    Single source of truth for platform lookup; used by the scrapers,
    normalizers and the API so the platform is never guessed from context.
    """
    raw = (url or "").strip()
    if not raw:
        return None
    if not re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.IGNORECASE):
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https"):
        return None
    return _platform_of_host(parts.netloc)


def _canonical(platform: str, path: str, query: str = "") -> str:
    path = "/" + path.lstrip("/") if path else "/"
    path = path.rstrip("/") or "/"
    base = _CANONICAL_BASE[platform]
    return f"{base}{path}" + (f"?{query}" if query else "")


def _norm_facebook(parts) -> str:
    path = parts.path or "/"
    raw = (path).rstrip("/") or "/"
    # profile.php?id=12345 is the ONLY case where the query identifies the page
    if raw.lower().startswith("/profile.php"):
        params = dict(parse_qsl(parts.query))
        if not params.get("id"):
            raise UrlError("invalid", INVALID_MSG)
        return _canonical("facebook", raw, f"id={params['id']}")
    segs = [s for s in raw.split("/") if s]
    if not segs:
        raise UrlError("invalid", INVALID_MSG)
    # legacy pages path — /pages/<name>/<numeric-id> is a real page, only
    # the /pages/ directory listing-style links are not
    if segs[0].lower() == "pages":
        if any(re.fullmatch(r"\d{5,}", s) for s in segs[1:]):
            return _canonical("facebook", raw)
        raise UrlError("invalid", INVALID_MSG)
    if segs[0].lower() in (
        "login", "checkpoint", "sharer", "share", "photo", "watch",
        "events", "messages", "friends", "settings", "help", "stories",
        "stories_", "recover", "reg", "act", "r", "l", "policy",
    ):
        raise UrlError("invalid", INVALID_MSG)
    return _canonical("facebook", raw)


def _norm_instagram(parts) -> str:
    path = parts.path or "/"
    raw = path.rstrip("/") or "/"
    segs = [s for s in raw.split("/") if s]
    # profile URL required: <host>/<username>
    if len(segs) != 1 or not re.fullmatch(r"[A-Za-z0-9._]{1,30}", segs[0]):
        raise UrlError("invalid", INVALID_MSG)
    return _canonical("instagram", segs[0])


def _norm_youtube(parts) -> str:
    host = parts.netloc.split(":")[0].lower()
    path = parts.path or "/"
    raw = path.rstrip("/") or "/"
    segs = [s for s in raw.split("/") if s]

    # 1. Shortened video link: youtu.be/VIDEO_ID
    if host == "youtu.be":
        if segs:
            return f"https://www.youtube.com/watch?v={segs[0]}"
        raise UrlError("invalid", INVALID_MSG)

    if not segs:
        raise UrlError("invalid", INVALID_MSG)

    # 2. Watch video link: youtube.com/watch?v=VIDEO_ID
    if segs[0].lower() == "watch":
        params = dict(parse_qsl(parts.query))
        vid = params.get("v")
        if vid:
            return f"https://www.youtube.com/watch?v={vid}"
        raise UrlError("invalid", INVALID_MSG)

    # 3. Shorts link: youtube.com/shorts/VIDEO_ID
    if segs[0].lower() == "shorts" and len(segs) >= 2:
        return f"https://www.youtube.com/watch?v={segs[1]}"

    # 4. Handle: youtube.com/@handle or youtube.com/@handle/videos etc.
    if segs[0].startswith("@"):
        return _canonical("youtube", segs[0])

    # 5. Channel/User/Custom URLs: youtube.com/channel/UC..., youtube.com/c/Name, youtube.com/user/Name
    if segs[0].lower() in ("channel", "user", "c", "handle"):
        if len(segs) >= 2:
            return _canonical("youtube", "/".join(segs[:2]))

    # 6. Direct channel slug: youtube.com/ChannelName (legacy custom URL)
    if len(segs) >= 1 and segs[0].lower() not in ("feed", "gaming", "music", "trending", "live", "premium", "browse"):
        return _canonical("youtube", segs[0])

    raise UrlError("invalid", INVALID_MSG)


def _norm_linkedin(parts) -> str:
    path = parts.path or "/"
    raw = path.rstrip("/") or "/"
    segs = [s for s in raw.split("/") if s]
    # company/page URL required: <host>/company/<slug>  or /school/<slug>
    if len(segs) >= 2 and segs[0].lower() in ("company", "school", "showcase"):
        return _canonical("linkedin", "/".join(segs[:2]))
    raise UrlError("invalid", INVALID_MSG)


_NORMALIZERS = {
    "facebook": _norm_facebook,
    "instagram": _norm_instagram,
    "youtube": _norm_youtube,
    "linkedin": _norm_linkedin,
}


def detect_social_url(raw_url: str):
    """Return (platform, canonical_url) for a supported social URL.

    Raises UrlError ("invalid" | "unsupported") with a user-facing message.
    """
    raw = (raw_url or "").strip()
    if not raw:
        raise UrlError("invalid", INVALID_MSG)
    if not re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.IGNORECASE):
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
    except ValueError as e:
        raise UrlError("invalid", INVALID_MSG) from e
    if parts.scheme.lower() not in ("http", "https"):
        raise UrlError("invalid", INVALID_MSG)
    platform = _platform_of_host(parts.netloc)
    if not platform:
        raise UrlError("unsupported", UNSUPPORTED_MSG)
    canonical = _NORMALIZERS[platform](parts)
    logger.info(f"[URL SEARCH] URL received: {raw_url} | detected platform: {platform} | "
                f"canonical: {canonical}")
    return platform, canonical