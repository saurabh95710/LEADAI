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

    # Admin sign-in — the only account. The password is stored as a sha256
    # hash, never as plaintext. Generate a new hash for a custom password:
    #   python -c "import hashlib;print(hashlib.sha256(b'YourPass').hexdigest())"
    admin_email: str = "admin@gmail.com"
    admin_password_hash: str = "a36aef5a11c4073fbe60314fc9df530a9d5f986533594d1f5190742ff9e0e408"
    # Secret signing the session cookie (any long random string; without it a
    # per-process random secret is used and sessions reset on restart)
    session_secret: str = ""
    # Session lifetime in days
    session_ttl_days: int = 7
    # Set true when serving over HTTPS so the cookie is only sent over TLS
    session_cookie_secure: bool = False

    # URL-based social search — Apify actor IDs for non-Facebook platforms.
    # Change these if you have your own actors (or a cheaper/more updated one).
    # Leave one empty to disable that platform's URL search.
    instagram_actor_id: str = "apify/instagram-scraper"
    youtube_actor_id: str = "streamers/youtube-scraper"
    linkedin_actor_id: str = "harvestapi/linkedin-company"
    linkedin_posts_actor_id: str = "harvestapi/linkedin-company-posts"
    # Max comments collected per URL-search run (per-platform cap)
    max_comments_to_collect: int = 100


@lru_cache
def get_settings() -> Settings:
    return Settings()
