"""
LeadAI SaaS Multi-Tenant Data Models & Enums.

Defines the core data structures for multi-tenancy:
  - Organizations (Tenants)
  - Organization Memberships (User-to-Tenant relationship with roles)
  - Organization Invitations (Expiring token-hashed invitations)
  - Plans (Configurable feature & limit bundles)
  - Subscriptions (Tenant subscriptions & trials)
  - Usage Tracking (Granular records & atomic aggregated counters)
  - Invoices & Payments (Immutable billing logs)
  - Temporary Entitlements & Coupons
  - Users & User Sessions
"""
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OrgStatus(str, Enum):
    ACTIVE = "active"          # paid subscription confirmed by Super Admin
    DEMO = "demo"              # approved demo (User Portal only)
    PENDING = "pending"        # demo requested, awaiting approval (no access)
    REJECTED = "rejected"      # demo request rejected
    TRIAL = "trial"            # legacy self-signup trial (grandfathered)
    SUSPENDED = "suspended"
    DISABLED = "disabled"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"


class OrgRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MANAGER = "manager"
    MEMBER = "member"
    VIEWER = "viewer"


class PlatformRole(str, Enum):
    SUPER_ADMIN = "super_admin"
    OPERATIONS_ADMIN = "operations_admin"
    BILLING_ADMIN = "billing_admin"
    SUPPORT_ADMIN = "support_admin"
    TECHNICAL_ADMIN = "technical_admin"
    VIEWER = "viewer"


class MemberStatus(str, Enum):
    ACTIVE = "active"
    INVITED = "invited"
    SUSPENDED = "suspended"
    REMOVED = "removed"


class InvitationStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class PlanStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ARCHIVED = "archived"


class SubscriptionStatus(str, Enum):
    """State machine: see app/billing/subscriptions.py (ALLOWED_TRANSITIONS)."""
    PENDING_PAYMENT = "pending_payment"
    PAYMENT_RECEIVED = "payment_received"
    PENDING_ADMIN_CONFIRMATION = "pending_admin_confirmation"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    # legacy values still readable on old documents
    TRIALING = "trialing"
    PAST_DUE = "past_due"
    PAUSED = "paused"
    INCOMPLETE = "incomplete"


class DemoRequestStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXTENDED = "extended"
    CANCELLED = "cancelled"
    CONVERTED = "converted"


class BillingCycle(str, Enum):
    MONTHLY = "monthly"
    YEARLY = "yearly"


class InvoiceStatus(str, Enum):
    PAID = "paid"
    OPEN = "open"
    VOID = "void"
    UNCOLLECTIBLE = "uncollectible"


class Organization(BaseModel):
    """A customer organization / tenant in LeadAI."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    name: str
    slug: str
    legal_name: Optional[str] = None
    display_name: Optional[str] = None
    logo_url: Optional[str] = None
    favicon_url: Optional[str] = None
    website: Optional[str] = None
    domain: Optional[str] = None
    industry: Optional[str] = None
    country: Optional[str] = None
    description: Optional[str] = None
    status: OrgStatus = OrgStatus.ACTIVE
    owner_id: Optional[str] = None
    plan_id: str = "starter"
    subscription_id: Optional[str] = None
    timezone: str = "UTC"
    currency: str = "USD"

    trial_started_at: Optional[datetime] = None
    trial_ends_at: Optional[datetime] = None

    branding: Dict[str, Any] = Field(
        default_factory=lambda: {
            "primary_color": "#8b5cf6",
            "accent_color": "#ec4899",
            "company_name": "",
        }
    )
    settings: Dict[str, Any] = Field(
        default_factory=lambda: {
            "auto_export": True,
            "notify_on_leads": True,
            "min_lead_score": 70,
            "language": "en",
            "date_format": "YYYY-MM-DD",
        }
    )
    metadata: Dict[str, Any] = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    last_activity_at: datetime = Field(default_factory=utcnow)


class OrganizationMember(BaseModel):
    """Membership record linking a User to an Organization with a specific role."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    organization_id: str
    user_id: str
    role: OrgRole = OrgRole.MEMBER
    status: MemberStatus = MemberStatus.ACTIVE

    joined_at: datetime = Field(default_factory=utcnow)
    invited_by: Optional[str] = None
    last_activity_at: datetime = Field(default_factory=utcnow)
    permissions_override: Dict[str, bool] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class OrganizationInvitation(BaseModel):
    """An expiring invitation sent to an email to join an Organization."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    organization_id: str
    email: str
    role: OrgRole = OrgRole.MEMBER
    token_hash: str
    invited_by: Optional[str] = None
    status: InvitationStatus = InvitationStatus.PENDING

    expires_at: datetime
    accepted_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Plan(BaseModel):
    """A SaaS subscription tier with machine-readable features and limits."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    name: str
    slug: str
    description: str = ""
    status: PlanStatus = PlanStatus.ACTIVE

    price_monthly: float = 0.0
    price_yearly: float = 0.0
    currency: str = "USD"
    trial_days: int = 14

    features: List[str] = Field(default_factory=list)
    limits: Dict[str, int] = Field(default_factory=dict)

    display_order: int = 0
    is_public: bool = True
    is_default: bool = False
    is_trial: bool = False

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Subscription(BaseModel):
    """A commercial subscription associating an Organization with a Plan."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    organization_id: str
    plan_id: str
    status: SubscriptionStatus = SubscriptionStatus.TRIALING

    provider: str = "mock"  # "mock" | "stripe" | "razorpay"
    provider_customer_id: Optional[str] = None
    provider_subscription_id: Optional[str] = None

    billing_cycle: BillingCycle = BillingCycle.MONTHLY
    currency: str = "USD"
    amount: float = 0.0

    started_at: datetime = Field(default_factory=utcnow)
    current_period_start: datetime = Field(default_factory=utcnow)
    current_period_end: datetime = Field(default_factory=utcnow)

    trial_start: Optional[datetime] = None
    trial_end: Optional[datetime] = None

    cancel_at_period_end: bool = False
    cancelled_at: Optional[datetime] = None
    grace_period_end: Optional[datetime] = None

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class UsageRecord(BaseModel):
    """An immutable audit trail event for consumption of a metered action."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    organization_id: str
    user_id: Optional[str] = None
    metric: str
    quantity: int = 1
    period: str  # e.g. "2026-09"
    source: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)


class OrganizationUsage(BaseModel):
    """Aggregate atomic counters per organization per billing cycle."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    organization_id: str
    period_start: datetime
    period_end: datetime

    searches_used: int = 0
    posts_used: int = 0
    comments_used: int = 0
    ai_used: int = 0
    exports_used: int = 0
    api_requests_used: int = 0

    updated_at: datetime = Field(default_factory=utcnow)


class Invoice(BaseModel):
    """An invoice record generated for a subscription billing period."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    organization_id: str
    subscription_id: Optional[str] = None
    provider_invoice_id: Optional[str] = None

    number: str
    status: InvoiceStatus = InvoiceStatus.PAID

    subtotal: float = 0.0
    discount: float = 0.0
    tax: float = 0.0
    total: float = 0.0
    currency: str = "USD"

    invoice_date: datetime = Field(default_factory=utcnow)
    due_date: Optional[datetime] = None
    paid_at: Optional[datetime] = None

    invoice_url: Optional[str] = None
    receipt_url: Optional[str] = None

    created_at: datetime = Field(default_factory=utcnow)


class Payment(BaseModel):
    """A financial transaction payment attempt."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    organization_id: str
    invoice_id: Optional[str] = None
    provider_payment_id: Optional[str] = None

    amount: float = 0.0
    currency: str = "USD"
    status: str = "succeeded"  # succeeded | failed | pending

    payment_method_type: str = "card"
    failure_code: Optional[str] = None
    failure_message: Optional[str] = None

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class TemporaryEntitlement(BaseModel):
    """Manual feature or credit override granted by Super Admin to a tenant."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    organization_id: str
    entitlement_type: str  # "feature" | "credit"
    key: str  # feature key (e.g. "advanced_analytics") or metric key (e.g. "monthly_searches")
    value: Any  # True for feature or int quantity for credits
    granted_by: str
    reason: str
    expires_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)


class Coupon(BaseModel):
    """A promotional discount code for subscription billing."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    code: str
    discount_type: str = "percentage"  # "percentage" | "fixed"
    discount_value: float = 0.0
    currency: str = "USD"
    duration: str = "once"  # "once" | "repeating" | "forever"
    duration_in_months: Optional[int] = None
    max_redemptions: Optional[int] = None
    times_redeemed: int = 0
    is_active: bool = True
    expires_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)


class User(BaseModel):
    """A registered user account in the LeadAI SaaS platform."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    email: str
    name: str
    password_hash: str
    status: str = "active"  # active | suspended | pending_verification

    is_platform_admin: bool = False
    platform_role: Optional[PlatformRole] = None
    default_organization_id: Optional[str] = None

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    last_login: Optional[datetime] = None


class UserSession(BaseModel):
    """A persisted, trackable session for server-side revocation and security tracking."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    session_id: str
    user_id: str
    organization_id: Optional[str] = None
    role: Optional[str] = None

    ip_hash: Optional[str] = None
    user_agent: Optional[str] = None

    impersonated_by: Optional[str] = None
    impersonation_reason: Optional[str] = None

    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    last_activity_at: datetime = Field(default_factory=utcnow)
    revoked_at: Optional[datetime] = None
    revoked_by: Optional[str] = None


# ── MASTER PROMPT 3: AI & APIFY CONTROL CENTER, LEADS & ANALYTICS MODELS ──────

class AIPrompt(BaseModel):
    """A versioned system or user prompt for the AI Intelligence Pipeline."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    prompt_key: str  # e.g. "comment_lead_analysis", "lead_scoring", "intent_detection", "comment_quality"
    name: str
    purpose: str
    version: int = 1
    is_active: bool = True
    provider: str = "gemini"
    model: str = "gemini-2.5-flash"
    system_instructions: str = ""
    user_template: str
    variables: List[str] = Field(default_factory=list)
    created_by: Optional[str] = None
    change_reason: Optional[str] = None
    previous_version: Optional[int] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class AIModel(BaseModel):
    """A registered AI model in the Super Admin AI Model Registry."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    provider: str = "gemini"
    model_name: str  # e.g. "gemini-2.5-flash", "gemini-2.5-pro"
    display_name: str
    purpose: str = "Comment Analysis"
    is_enabled: bool = True
    is_default: bool = False
    max_tokens: int = 2048
    temperature: float = 0.2
    cost_input_per_1k: float = 0.0001
    cost_output_per_1k: float = 0.0004
    context_limit: int = 32000
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class AIRequestLog(BaseModel):
    """Tracks token consumption, latency, and estimated cost per AI request for SaaS analytics."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    organization_id: Optional[str] = None
    user_id: Optional[str] = None
    search_id: Optional[str] = None
    run_id: Optional[str] = None
    provider: str = "gemini"
    model: str = "gemini-2.5-flash"
    prompt_key: Optional[str] = None
    prompt_version: Optional[int] = None
    tokens_in: int = 0
    tokens_out: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0
    estimated_cost: float = 0.0
    status: str = "success"  # success | failure | fallback
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)


class ApifyActor(BaseModel):
    """A registered scraping actor in the Super Admin Apify Registry."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    actor_id: str  # e.g. "apify/facebook-pages-scraper"
    platform: str  # facebook | instagram | youtube | linkedin
    name: str
    purpose: str  # pages | posts | comments | search
    enabled: bool = True
    is_default: bool = False
    timeout_sec: int = 120
    retry_count: int = 2
    max_posts: int = 10
    max_comments: int = 50
    runs_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    avg_duration_sec: float = 0.0
    last_run_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ApifyJob(BaseModel):
    """Execution record for an Apify scraping job."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    job_id: str
    run_id: Optional[str] = None
    organization_id: Optional[str] = None
    user_id: Optional[str] = None
    search_id: Optional[str] = None
    actor_id: str
    platform: str
    input_params: Dict[str, Any] = Field(default_factory=dict)
    status: str = "QUEUED"  # QUEUED | RUNNING | COMPLETED | FAILED | TIMED_OUT | CANCELLED | BLOCKED
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_sec: float = 0.0
    items_count: int = 0
    error_type: Optional[str] = None  # BLOCKED | ACTOR_FAILED | ACTOR_TIMED_OUT | INVALID_INPUT | API_ERROR | ACCESS_DENIED | NETWORK_ERROR | NO_RESULTS | DATASET_ERROR
    error_message: Optional[str] = None
    retry_count: int = 0
    dataset_id: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)


class LeadFollowUp(BaseModel):
    """Scheduled follow-up reminder for a lead."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    lead_id: str
    organization_id: Optional[str] = None
    user_id: Optional[str] = None
    assigned_user_id: Optional[str] = None
    follow_up_date: datetime
    note: str = ""
    status: str = "pending"  # pending | completed | cancelled | overdue
    completed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class LeadNote(BaseModel):
    """A note attached to a lead dossier."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    lead_id: str
    organization_id: Optional[str] = None
    author_id: Optional[str] = None
    author_name: str = "System"
    content: str
    created_at: datetime = Field(default_factory=utcnow)


# ── STEP 1: CUSTOMER LIFECYCLE, TOKENS, NOTIFICATIONS, SECURITY ─────────────

class DemoConfig(BaseModel):
    """Super-Admin-configurable demo entitlements (platform_config/_id="demo")."""

    duration_days: int = 7
    tokens: int = 500
    max_searches: int = 10
    posts_per_search: int = 20
    comments_per_post: int = 30
    allowed_platforms: List[str] = Field(default_factory=lambda: ["facebook", "instagram", "youtube", "linkedin"])
    ai_enabled: bool = True
    exports_enabled: bool = False
    max_leads: int = 200
    max_users: int = 1
    auto_approve: bool = False


class DemoRequest(BaseModel):
    """A public demo request (collection ``demo_requests``)."""

    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    organization_id: str
    user_id: str
    name: str
    email: str
    phone: Optional[str] = None
    company: str
    message: Optional[str] = None
    status: DemoRequestStatus = DemoRequestStatus.PENDING
    source: str = "website"
    requested_config: Dict[str, Any] = Field(default_factory=dict)
    granted_config: Optional[Dict[str, Any]] = None
    demo_expires_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    rejection_reason: Optional[str] = None
    history: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class TokenBalance(BaseModel):
    """Allocated / used / remaining tokens of an organization (``token_balances``)."""

    organization_id: str
    allocated: int = 0
    used: int = 0
    remaining: int = 0
    source: str = "demo"  # demo | plan | manual
    expires_at: Optional[datetime] = None
    notified_thresholds: List[int] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utcnow)


class TokenLedgerEntry(BaseModel):
    """Append-only token movement (``token_ledger``)."""

    organization_id: str
    user_id: Optional[str] = None
    type: str  # allocate | consume | refund | adjust | expire
    amount: int
    reason: str = ""
    reference: Optional[str] = None
    actor: Optional[str] = None
    balance_after: Optional[int] = None
    created_at: datetime = Field(default_factory=utcnow)


class PaymentEvent(BaseModel):
    """Provider/payment timeline entry (``payment_events``)."""

    organization_id: str
    subscription_id: str
    event: str  # checkout_started | payment_received | payment_failed | amount_mismatch | ...
    data: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)


class Notification(BaseModel):
    """In-app notification (``notifications``)."""

    audience: str  # super_admin | org_admin | user
    organization_id: Optional[str] = None
    user_id: Optional[str] = None
    type: str
    title: str
    message: str = ""
    severity: str = "info"
    link: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)
    read_by: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)


class SecurityEvent(BaseModel):
    """Suspicious / security-relevant event (``security_events``)."""

    type: str
    severity: str = "medium"
    actor_email: str = ""
    actor_user_id: Optional[str] = None
    organization_id: Optional[str] = None
    target_organization_id: Optional[str] = None
    ip: str = ""
    path: Optional[str] = None
    method: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    at: datetime = Field(default_factory=utcnow)


class AuditLog(BaseModel):
    """Unified audit record (``audit_logs``) — see app/admin/audit.py."""

    action: str
    category: str
    actor_user_id: Optional[str] = None
    actor_email: str = ""
    actor_role: str = ""
    organization_id: Optional[str] = None
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    status: str = "success"
    ip: str = ""
    session_id: Optional[str] = None
    user_agent: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    at: datetime = Field(default_factory=utcnow)


class ExportRecord(BaseModel):
    """A tenant export (``exports``)."""

    organization_id: str
    user_id: str
    created_by: str
    scope: str  # pages | posts | comments | leads | ...
    format: str = "csv"
    status: str = "completed"
    run_id: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)


class RolePermission(BaseModel):
    """Super-Admin override of a role's permission set (``role_permissions``)."""

    kind: str  # org | platform
    role: str
    permissions: List[str] = Field(default_factory=list)
    updated_by: Optional[str] = None
