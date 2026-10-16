"""Request / response models of the keyword-search API (app + public v1)."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

Platform = Literal["instagram", "facebook", "youtube", "linkedin"]

# upper bound of a token budget (fits a BSON int64 with room to spare)
MAX_TOKENS_BUDGET = 10_000_000


class KeywordFilters(BaseModel):
    """Every filter is optional; enabled filters are ANDed. Out-of-range values
    are refused (422 ``invalid_filters``). Unknown filter names are refused."""
    model_config = ConfigDict(extra="forbid")

    min_followers: Optional[int] = Field(None, description="Followers / subscribers at least this (inclusive).")
    max_followers: Optional[int] = Field(None, description="Followers / subscribers at most this (inclusive).")
    min_posts: Optional[int] = Field(None, description="Total posts on the profile at least this.")
    recent_posts_to_analyze: Optional[int] = Field(
        None, description="Recent posts sampled per profile (default: Global Setting, clamped to your plan).")
    min_avg_comments: Optional[float] = Field(None, description="Average comments per analyzed post.")
    min_total_comments: Optional[int] = Field(None, description="Comments summed over the analyzed posts.")
    min_engagement_rate: Optional[float] = Field(
        None, description="Percent: average (likes + comments [+ shares]) per analyzed post / followers x 100.")
    active_within_days: Optional[int] = Field(None, description="Last post no older than this many days.")
    business_account_only: Optional[bool] = None
    has_contact_info: Optional[bool] = Field(None, description="An email, phone or website was found.")
    location_keyword: Optional[str] = Field(None, description=(
        "Places separated by commas, most specific first (\"Kota, Rajasthan\"). Sent to the Apify search "
        "(Facebook / LinkedIn locations, Instagram / YouTube search text) and matched as whole words in "
        "the name, bio, address, city, country and category."))
    location_match: Optional[str] = Field(None, description=(
        "\"all\" (default): every place of location_keyword must appear; \"any\": at least one."))
    category_keyword: Optional[str] = Field(None, description=(
        "Business type alternatives separated by commas (\"software, IT services\"), matched as whole words "
        "in the category / industries, bio and name; the first one also narrows Instagram / YouTube searches."))
    language: Optional[str] = Field(None, description=(
        "Deprecated: no platform reports a language (always unsupported). Accepted for compatibility."))
    exclude_keywords: Optional[List[str]] = Field(None, description="Reject profiles whose name/bio contains any.")
    verified_only: Optional[bool] = None


class KeywordLimits(BaseModel):
    """Optional per-search limits; each is clamped to the plan and Global
    Settings caps (the clamped values are echoed back)."""
    model_config = ConfigDict(extra="forbid")

    max_candidates: Optional[int] = Field(None, ge=1, le=100_000, description="Profiles discovered across all platforms.")
    recent_posts: Optional[int] = Field(None, ge=1, le=1000, description="Same as filters.recent_posts_to_analyze.")
    comments_per_post: Optional[int] = Field(None, ge=1, le=100_000, description="Comments collected per post (deep scrape).")


class CommentFilterSpec(BaseModel):
    """Same comment filter as URL search: all | preset (key or saved rule id) | custom."""
    mode: Literal["all", "preset", "custom"] = "all"
    preset: Optional[str] = Field(None, max_length=100)
    include_keywords: List[str] = Field(default_factory=list)
    exclude_keywords: List[str] = Field(default_factory=list)
    categories: List[str] = Field(default_factory=list)
    match_mode: Literal["any", "all", "category", "advanced"] = "any"


class BusinessContext(BaseModel):
    industry: Optional[str] = Field(None, max_length=100)
    business_type: Optional[str] = Field(None, max_length=100)
    target_customer: Optional[str] = Field(None, max_length=100)
    keywords: List[str] = Field(default_factory=list)


class KeywordSearchRequest(BaseModel):
    keywords: List[str] = Field(..., min_length=1, max_length=100,
                                description="Search terms (plan caps how many).")
    platforms: List[Platform] = Field(..., min_length=1, max_length=4)
    filters: KeywordFilters = Field(default_factory=lambda: KeywordFilters.model_validate({}))
    strict: bool = Field(False, description="Reject candidates whose filter values are unknown / unsupported.")
    limits: KeywordLimits = Field(default_factory=lambda: KeywordLimits.model_validate({}))
    confirm: bool = Field(False, description="Must be true to start (otherwise 409 with the estimate).")
    max_tokens: Optional[int] = Field(None, ge=1, le=MAX_TOKENS_BUDGET, description="Optional token budget: the run stops "
                                      "(phase stopped_budget) before spending more.")
    comment_filter: Optional[CommentFilterSpec] = None
    business_context: Optional[BusinessContext] = None


class ReapplyRequest(BaseModel):
    filters: KeywordFilters = Field(default_factory=lambda: KeywordFilters.model_validate({}))
    strict: bool = False


class ContinueRequest(BaseModel):
    confirm: bool = Field(False, description="Must be true to spend tokens (otherwise 409 with the cost).")
    max_tokens: Optional[int] = Field(None, ge=1, le=MAX_TOKENS_BUDGET, description=(
        "Token budget for THIS continuation: it may spend at most this many more tokens "
        "(the run stops with phase stopped_budget)."))


class KeywordPresetCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    keywords: List[str] = Field(default_factory=list, max_length=100)
    platforms: List[Platform] = Field(default_factory=list, max_length=4)
    filters: KeywordFilters = Field(default_factory=lambda: KeywordFilters.model_validate({}))
    strict: bool = False
    limits: KeywordLimits = Field(default_factory=lambda: KeywordLimits.model_validate({}))
    is_shared: bool = False


# ── responses ────────────────────────────────────────────────────────────────

class TokenEstimate(BaseModel):
    discover: int
    enrich: int
    sample: int
    collect: int
    total_max: int


class Estimate(BaseModel):
    tokens: TokenEstimate
    apify_usd_estimate: float
    candidates_max: int
    per_platform: Dict[str, Dict[str, Any]]
    limits: Dict[str, Any]
    filters: Dict[str, Any]
    notes: List[str]
    tokens_available: Optional[int] = None


class StartResponse(BaseModel):
    run_id: str
    status: str
    estimate: Estimate
    message: str


class FilterOutcome(BaseModel):
    filter: str
    outcome: Literal["pass", "fail", "unsupported", "unknown"]
    message: str
    value: Any = None
    threshold: Any = None


class DiscoveryQuery(BaseModel):
    """What one keyword search sent to Apify (filters pushed into the search)."""
    platform: str
    keyword: str
    query_text: Optional[str] = None
    locations: List[str] = []
    extra_inputs: Dict[str, Any] = {}


class RejectionReason(BaseModel):
    filter: str
    label: str
    count: int
    outcome: Literal["fail", "unknown"] = "fail"
    message: str


class RunStatus(BaseModel):
    run_id: str
    status: str
    phase: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None
    keywords: List[str]
    platforms: List[str]
    filters: Dict[str, Any]
    strict: bool
    limits: Dict[str, Any]
    max_tokens: Optional[int] = None
    estimate: Optional[Dict[str, Any]] = None
    stages: Dict[str, str]
    stage_counts: Dict[str, int]
    tokens_charged: Dict[str, int]
    platform_errors: Dict[str, Any]
    discovery_queries: List[DiscoveryQuery] = []
    passed_count: int = 0
    rejection_summary: List[RejectionReason] = []
    source: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    completed_at: Optional[str] = None


class CandidateOut(BaseModel):
    id: str
    platform: str
    profile_id: str
    url: Optional[str] = None
    name: Optional[str] = None
    keywords: List[str] = []
    status: str
    stage_reached: str
    reasons: List[FilterOutcome] = []
    unsupported: List[str] = []
    unknown: List[str] = []
    metrics: Dict[str, Any] = {}
    profile: Dict[str, Any] = {}
    recent_posts: List[Dict[str, Any]] = []
    error: Optional[str] = None
    error_technical: Optional[str] = None
    page_ref: Optional[str] = None
    deep_status: Optional[str] = None
    charges: Dict[str, Any] = {}
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class CandidatesPage(BaseModel):
    run_id: str
    items: List[CandidateOut]
    total: int
    page: int
    page_size: int
    counts: Dict[str, int]


class ReapplyResponse(BaseModel):
    run_id: str
    counts: Dict[str, int]
    changed: int
    continue_cost: Dict[str, int]
    notes: List[str]


class ContinueResponse(BaseModel):
    run_id: str
    status: str
    estimate: Dict[str, Any]
    message: str
