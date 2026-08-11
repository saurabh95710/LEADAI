"""
LeadAI data model — five collections only.

  facebook_pages     — one document per real Facebook page (Apify search/pages actors)
  facebook_posts     — one document per post of a selected page (Apify posts actor)
  facebook_comments  — one document per comment of a selected post (Apify comments actor)
  ai_comments        — AI analysis of every comment (upsert on comment_ref)
  search_history     — one document per agent search run (status, progress, counts)

Values are stored from real actor output only. A field that the actor did
not return is stored as None (or omitted) — never fabricated, never faked.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SearchHistory(BaseModel):
    """One AI-agent search run: `POST /api/search`."""

    id: Optional[str] = Field(default=None, alias="_id")
    run_id: str = ""
    query: str = ""
    intent: Dict[str, Any] = Field(default_factory=dict)   # parsed {keyword, city, state, category}
    limit: int = 10

    status: str = "running"          # running | completed | partial | error
    message: Optional[str] = None
    error: Optional[str] = None

    pages_found: int = 0
    pages_stored: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    completed_at: Optional[datetime] = None

    class Config:
        populate_by_name = True


class FacebookPage(BaseModel):
    """One real Facebook page found by the agent. `facebook_pages` collection."""

    id: Optional[str] = Field(default=None, alias="_id")
    page_id: Optional[str] = None
    page_name: Optional[str] = None
    facebook_url: Optional[str] = None

    category: Optional[str] = None
    about: Optional[str] = None
    followers: Optional[int] = None
    likes: Optional[int] = None
    verified: Optional[bool] = None

    phone: Optional[str] = None
    email: Optional[str] = None
    whatsapp: Optional[str] = None
    website: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None

    profile_picture: Optional[str] = None
    cover_image: Optional[str] = None

    # provenance
    search_run_id: Optional[str] = None
    search_keyword: Optional[str] = None
    source: Optional[str] = None          # apify_search | apify_pages
    source_type: Optional[str] = None     # page | group (from the Facebook URL)

    # collection progress (real-time status)
    posts_status: str = "not_started"     # not_started | running | completed | error | empty
    posts_count: int = 0
    posts_error: Optional[str] = None
    posts_collected_at: Optional[datetime] = None

    # post analytics (computed after post collection — real data only)
    total_posts_found: int = 0
    relevant_posts_count: int = 0
    qualifying_posts_count: int = 0
    total_comments_on_qualifying_posts: int = 0
    latest_post_date: Optional[str] = None
    has_qualifying_posts: bool = False
    activity_status: Optional[str] = None # active | recent | inactive | unknown
    lead_score: int = 0

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    class Config:
        populate_by_name = True


class FacebookPost(BaseModel):
    """One post of a selected page. `facebook_posts` collection."""

    id: Optional[str] = Field(default=None, alias="_id")
    post_id: Optional[str] = None
    post_url: Optional[str] = None
    page_id: Optional[str] = None
    page_name: Optional[str] = None

    caption: Optional[str] = None          # full post text, never truncated
    images: List[str] = Field(default_factory=list)
    videos: List[str] = Field(default_factory=list)
    external_links: List[str] = Field(default_factory=list)

    published_date: Optional[str] = None
    likes_count: Optional[int] = None
    total_comment_count: Optional[int] = None     # Facebook-reported total — never overwritten
    scraped_comment_count: Optional[int] = None   # comments actually collected into this DB
    comments_count: Optional[int] = None          # legacy alias of total_comment_count
    shares_count: Optional[int] = None

    # lead qualification (relevant + total_comment_count >= MIN_COMMENTS)
    is_relevant: Optional[bool] = None
    is_qualifying: Optional[bool] = None

    page_ref: Optional[str] = None         # ObjectId of the facebook_pages doc
    search_run_id: Optional[str] = None

    # collection progress (real-time status)
    comments_status: str = "not_started"   # not_started | running | completed | error | empty
    comments_count: int = 0
    comments_error: Optional[str] = None
    comments_collected_at: Optional[datetime] = None

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    class Config:
        populate_by_name = True


class FacebookComment(BaseModel):
    """One comment on a collected post. `facebook_comments` collection."""

    id: Optional[str] = Field(default=None, alias="_id")
    comment_id: Optional[str] = None
    comment_url: Optional[str] = None
    author_name: Optional[str] = None
    author_profile_url: Optional[str] = None
    text: Optional[str] = None
    published_date: Optional[str] = None
    reactions_count: Optional[int] = None

    post_id: Optional[str] = None
    post_url: Optional[str] = None
    page_id: Optional[str] = None
    post_ref: Optional[str] = None          # ObjectId of the facebook_posts doc
    search_run_id: Optional[str] = None

    created_at: datetime = Field(default_factory=utcnow)

    class Config:
        populate_by_name = True


class AICommentAnalysis(BaseModel):
    """AI analysis of one comment. `ai_comments` collection (unique on comment_ref)."""

    id: Optional[str] = Field(default=None, alias="_id")
    comment_ref: Optional[str] = None       # ObjectId of the facebook_comments doc (unique)
    comment_id: Optional[str] = None
    comment_text: Optional[str] = None
    commenter_name: Optional[str] = None

    post_ref: Optional[str] = None
    post_id: Optional[str] = None
    post_url: Optional[str] = None
    page_ref: Optional[str] = None
    page_name: Optional[str] = None

    # flat, display-ready extraction (never fabricated; null when unknown)
    phone: Optional[str] = None
    email: Optional[str] = None
    whatsapp: Optional[str] = None
    website: Optional[str] = None
    budget: Optional[str] = None
    requirement: Optional[str] = None
    location: Optional[str] = None
    intent: Optional[str] = None            # buying | selling | rent | investment | other
    urgency: Optional[str] = None
    priority: str = "low"                   # high | medium | low
    lead_quality: Optional[str] = None      # hot | warm | cold
    confidence: float = 0.0                 # 0..1
    lead_score: int = 0                     # 0..100 deterministic rank
    is_lead: bool = False                   # displayed as a lead candidate
    reason: Optional[str] = None

    details: Dict[str, Any] = Field(default_factory=dict)   # full nested extraction
    analyzed_by: str = "rules"              # rules | gemini
    analyzed_at: datetime = Field(default_factory=utcnow)

    class Config:
        populate_by_name = True
