"""Per-platform discovery adapters (the only code that knows actor fields)."""
from typing import Any, Callable, Optional

from app.discovery.adapters.base import AdapterError, DiscoveryAdapter
from app.discovery.adapters.facebook import FacebookAdapter
from app.discovery.adapters.instagram import InstagramAdapter
from app.discovery.adapters.linkedin import LinkedInAdapter
from app.discovery.adapters.youtube import YouTubeAdapter

ADAPTERS = {
    "instagram": InstagramAdapter,
    "facebook": FacebookAdapter,
    "youtube": YouTubeAdapter,
    "linkedin": LinkedInAdapter,
}


def get_adapter(platform: str, connector: Any,
                should_abort: Optional[Callable[[], bool]] = None) -> DiscoveryAdapter:
    cls = ADAPTERS.get(platform)
    if cls is None:
        raise ValueError(f"Unsupported platform: {platform}")
    return cls(connector, should_abort=should_abort)


__all__ = ["ADAPTERS", "AdapterError", "DiscoveryAdapter", "get_adapter"]
