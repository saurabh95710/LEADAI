"""Pydantic request/response models for API endpoints.

Provides type-safe validation for all POST/PUT/PATCH request bodies,
replacing manual Dict[str, Any] validation. Each model enforces field
constraints at the boundary, before any business logic runs.
"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


# ── Auth ──────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    """POST /api/auth/login request body."""
    email: str = Field(..., min_length=1, max_length=254)
    password: str = Field(..., min_length=1)
    scope: str = Field(default="site", pattern="^(site|admin)$")


# ── Admin Users ───────────────────────────────────────────────────────────────

class CreateUserRequest(BaseModel):
    """POST /api/admin/users request body."""
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=8, max_length=128)
    name: str = Field(..., min_length=1, max_length=80)
    role: str = Field(..., pattern="^(viewer|manager|super_admin)$")

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Invalid email format")
        return v


class UpdateUserRequest(BaseModel):
    """PATCH /api/admin/users/{user_id} request body."""
    name: Optional[str] = Field(None, max_length=80)
    role: Optional[str] = Field(None, pattern="^(viewer|manager|super_admin)$")
    password: Optional[str] = Field(None, min_length=8, max_length=128)


class ChangePasswordRequest(BaseModel):
    """POST /api/admin/security/change-password request body."""
    old_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=128)


# ── Admin Leads ───────────────────────────────────────────────────────────────

class UpdateLeadRequest(BaseModel):
    """PATCH /api/admin/leads/{lead_id} request body."""
    status: Optional[str] = Field(None, pattern="^(new|contacted|qualified|follow_up|converted|lost|disqualified|archived)$")
    notes: Optional[str] = Field(None, max_length=500)


class BulkLeadRequest(BaseModel):
    """POST /api/admin/leads/bulk request body."""
    ids: List[str] = Field(..., min_length=1)
    action: str = Field(..., pattern="^(set_status|set_priority|assign|delete)$")
    value: Optional[str] = None

    @field_validator("ids")
    @classmethod
    def validate_ids(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("At least one ID required")
        return v


# ── Admin Settings ────────────────────────────────────────────────────────────

class AdminSettingsUpdateRequest(BaseModel):
    """PUT /api/admin/settings request body."""
    values: Dict[str, Any] = Field(..., min_length=1)
    reason: Optional[str] = Field(None, max_length=300)


class AdminSettingsResetRequest(BaseModel):
    """POST /api/admin/settings/reset request body."""
    section: Optional[str] = None
    keys: Optional[List[str]] = None
    all: Optional[bool] = None
    reason: Optional[str] = Field(None, max_length=300)


class AdminSettingsRestoreRequest(BaseModel):
    """POST /api/admin/settings/history/{version}/restore request body."""
    reason: Optional[str] = Field(None, max_length=300)


# ── Admin Apify ───────────────────────────────────────────────────────────────

class ApifyTokenRequest(BaseModel):
    """POST /api/admin/apify/token request body."""
    token: str = Field(..., min_length=10, max_length=200)


class ApifyActorRequest(BaseModel):
    """POST /api/admin/platforms/{platform}/actor request body."""
    actor_id: str = Field(..., min_length=1, max_length=200)

    @field_validator("actor_id")
    @classmethod
    def strip_actor_id(cls, v: str) -> str:
        return v.strip()


# ── Admin Environment ─────────────────────────────────────────────────────────

class EnvVarUpdateRequest(BaseModel):
    """PUT /api/admin/env/{name} request body."""
    value: Any = Field(...)


class EnvUnlockRequest(BaseModel):
    """POST /api/admin/env/unlock request body."""
    password: str = Field(..., min_length=1)


class EnvPasswordRequest(BaseModel):
    """POST /api/admin/env/password request body."""
    new_password: str = Field(..., min_length=8, max_length=128)


# ── Admin Settings Groups ─────────────────────────────────────────────────────

class LimitsUpdateRequest(BaseModel):
    """PUT /api/admin/limits request body."""
    values: Dict[str, Any] = Field(..., min_length=1)


class AIUpdateRequest(BaseModel):
    """PUT /api/admin/ai request body."""
    values: Dict[str, Any] = Field(..., min_length=1)


class AITestRequest(BaseModel):
    """POST /api/admin/ai/test request body."""
    text: str = Field(..., min_length=1, max_length=5000)


class ScoringUpdateRequest(BaseModel):
    """PUT /api/admin/scoring request body."""
    values: Dict[str, Any] = Field(..., min_length=1)


class CommentIntelligenceUpdateRequest(BaseModel):
    """PUT /api/admin/comment-intelligence request body."""
    values: Dict[str, Any] = Field(..., min_length=1)


class FeaturesUpdateRequest(BaseModel):
    """PUT /api/admin/features request body."""
    values: Dict[str, Any] = Field(..., min_length=1)


class SecurityUpdateRequest(BaseModel):
    """PUT /api/admin/security request body."""
    values: Dict[str, Any] = Field(..., min_length=1)


class MaintenanceToggleRequest(BaseModel):
    """POST /api/admin/maintenance request body."""
    enabled: bool = Field(...)


# ── Comment Filters ───────────────────────────────────────────────────────────

class CommentFilterRuleCreateRequest(BaseModel):
    """POST /api/comment-filters/rules request body."""
    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = Field(None, max_length=500)
    platform: Optional[str] = None
    business_category: Optional[str] = None
    categories: Optional[List[str]] = None
    include_keywords: Optional[List[str]] = None
    exclude_keywords: Optional[List[str]] = None
    match_mode: Optional[str] = Field(None, pattern="^(any|all|category|advanced)$")
    group_operator: Optional[str] = Field(None, pattern="^(and|or)$")
    groups: Optional[List[Dict[str, Any]]] = None
    language: Optional[str] = None
    detect_contacts: Optional[bool] = None
    intent_type: Optional[str] = None
    active: Optional[bool] = None


class CommentFilterCategoryCreateRequest(BaseModel):
    """POST /api/comment-filters/categories request body."""
    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = Field(None, max_length=500)
    icon: Optional[str] = Field(None, max_length=4)
    keywords: Optional[List[str]] = None


class CommentFilterCategoryUpdateRequest(BaseModel):
    """PUT /api/comment-filters/categories/{category_id} request body."""
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    description: Optional[str] = Field(None, max_length=500)
    icon: Optional[str] = Field(None, max_length=4)
    keywords: Optional[List[str]] = None
    active: Optional[bool] = None


# ── Common Response Models ────────────────────────────────────────────────────

class SuccessResponse(BaseModel):
    """Standard success response."""
    success: bool = True
    message: Optional[str] = None


class ErrorResponse(BaseModel):
    """Standard error response."""
    success: bool = False
    error: str
    message: str
    detail: Optional[Dict[str, Any]] = None


class PaginatedResponse(BaseModel):
    """Standard paginated list response."""
    items: List[Any]
    total: int
    offset: int
    limit: int
