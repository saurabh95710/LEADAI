from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Google Gemini — the AI agent (intent parsing + comment analysis)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    # Business context
    business_domain: str = "general B2B services"

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

    # URL-based social search — Apify actor IDs for non-Facebook platforms.
    # Change these if you have your own actors (or a cheaper/more updated one).
    # Leave one empty to disable that platform's URL search.
    instagram_actor_id: str = "clockworks/instagram-scraper"
    youtube_actor_id: str = "streamers/youtube-scraper"
    linkedin_actor_id: str = "curious_coder/linkedin-data-scraper"
    # Max comments collected per URL-search run (per-platform cap)
    max_comments_to_collect: int = 100

    # Bright Data — alternative Facebook data source (Facebook Scraper API)
    # Get the key at https://brightdata.com/cp/setting/users
    brightdata_api_key: str = ""
    # Dataset IDs (defaults are the official Facebook Scraper API datasets)
    brightdata_dataset_pages: str = "gd_mf124a0511bauquyow"   # pages/profiles by URL
    brightdata_dataset_posts: str = "gd_lkaxegm826bjpoo9m5"   # page posts by URL
    brightdata_dataset_comments: str = "gd_lkay758p1eanlolqw8"  # comments by URL


@lru_cache
def get_settings() -> Settings:
    return Settings()
