from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Google Gemini — comment analysis (rule-based fallback when unset)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    # Minimum total (Facebook-reported) comment count for a post to qualify as
    # lead material: relevant post + total_comment_count >= min_comments.
    # Configurable via MIN_COMMENTS in .env (0 disables the requirement).
    min_comments: int = 10

    # Mongo
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db_name: str = "LeadAI"
    # Comma-separated DNS servers used for mongodb+srv:// SRV resolution.
    # Empty string falls back to the system DNS. Defaults to public resolvers
    # because some local network DNS servers fail on SRV lookups.
    dns_servers: str = "8.8.8.8,1.1.1.1"

    # Apify — the Facebook data source
    apify_api_token: str = ""

    # ── Site sign-in (locks the user app) ──────────────────────────────
    # Email + password hash for the main website login page.
    # Password hashes are stored as bcrypt hashes (migrated from SHA-256).
    # Set these in .env — no default credentials are embedded in source.
    admin_email: str = ""
    admin_password_hash: str = ""
    # ── Admin portal sign-in (locks /admin and /api/admin/*) ───────────
    # Customer organization admin. Managed accounts live in
    # the admin_users collection (Security page); a DB record with the same
    # email takes precedence over this env account.
    admin_panel_email: str = ""
    admin_panel_password_hash: str = ""
    # ── Super Admin portal sign-in (locks /super-admin) ────────────────
    # Platform owner / recovery super-admin for the admin panel.
    panel_admin_email: str = ""
    panel_admin_password_hash: str = ""
    # Secret signing the session cookie (any long random string; without it a
    # per-process random secret is used and sessions reset on restart)
    session_secret: str = ""
    # Session lifetime in days
    session_ttl_days: int = 7
    # Set true when serving over HTTPS so the cookie is only sent over TLS
    session_cookie_secure: bool = False

    # CORS — comma-separated allowed origins (empty = same-origin only)
    # For local dev: http://localhost:8000
    # For production: https://yourdomain.com
    allowed_origins: str = ""

    # URL-based social search — Apify actor IDs for non-Facebook platforms.
    # Change these if you have your own actors (or a cheaper/more updated one).
    # Leave one empty to disable that platform's URL search.
    instagram_actor_id: str = "apify/instagram-scraper"
    youtube_actor_id: str = "streamers/youtube-scraper"
    linkedin_actor_id: str = "harvestapi/linkedin-company"
    linkedin_posts_actor_id: str = "harvestapi/linkedin-company-posts"
    # Max comments collected per URL-search run (per-platform cap)
    max_comments_to_collect: int = 100

    # Security hardening
    # Disable API documentation in production (set to "true" to enable /docs)
    enable_api_docs: bool = False

    # ── Billing / Stripe (optional — mock provider used when unset) ───
    stripe_secret_key: str = ""
    stripe_publishable_key: str = ""
    stripe_webhook_secret: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
