# CLEANUP.md — LeadAI refactor log

Pre-refactor snapshot: `C:\Users\saura\AppData\Local\Temp\opencode\leadai_backup` (app/ + scratch/ + .env + requirements.txt, 169 files).

## Keyword-search removal (2026-08-12, v2.1)

URL search is now the only entry point; keyword search and the Bright Data connector are gone.

- Deleted modules: `app/agent/intent.py` (keyword intent parsing), `app/connectors/brightdata_connector.py` (Bright Data API scraping).
- Removed from `app/agent/search.py`: `run_search`, `get_connector`, `_PROVIDERS`, `merge_page_details`, `collect_run_posts`, `parse_query` import, Bright Data exception handling. `collect_page_posts`/`collect_post_comments` now build `ApifyConnector()` directly.
- Removed from `app/connectors/apify_connector.py`: `scrape_facebook_pages` + `_keyword_variants` (keyword path); 3 FB actors remain.
- Removed routes: `POST /api/search` (keyword) and `POST /api/search/{run_id}/collect`.
- Removed config/env: `business_domain`, `BRIGHTDATA_API_KEY`, `BRIGHTDATA_DATASET_PAGES/POSTS/COMMENTS`. Added to `.env.example`: `INSTAGRAM_ACTOR_ID` (clockworks/instagram-scraper), `YOUTUBE_ACTOR_ID` (streamers/youtube-scraper), `LINKEDIN_ACTOR_ID` (curious_coder/linkedin-data-scraper).
- Frontend: keyword form, provider toggle, intent chips removed; URL search is the primary panel; `handleSearch`/`renderIntentChips` gone from `app.js`.
- `README.md` rewritten as URL-only product.
- Verified: pytest 34 passed, ruff clean, mypy 77 (baseline 85), integration check passed.
- Security: real `APIFY_API_TOKEN` had leaked into tracked `.env.example`; reverted to placeholder + `.gitignore` entry added. Rotate token if repo was shared.

## Deleted modules (dead code)

| Path | Why |
|---|---|
| `app/connectors/base.py` | Abstract-base indirection, no longer needed |
| `app/connectors/facebook.py` | Superseded by `apify_connector.py` |
| `app/connectors/facebook_explorer.py` | Only actor removed from the new connector |
| `app/connectors/facebook_explorer_light.py` | Duplicate explorer actor |
| `app/connectors/facebook_following.py` | Old whole-pipeline engine (`engine=browser|apify|auto`) |
| `app/connectors/google_maps.py` | Out of scope — Facebook only |
| `app/connectors/instagram.py` | Out of scope |
| `app/connectors/linkedin.py` | Out of scope |
| `app/connectors/twitter.py` | Out of scope |
| `app/connectors/website_analyzer.py` | Replaced by rule/AI extraction in comment_ai |
| `app/connectors/youtube.py` | Out of scope |
| `app/pipeline/ai_analysis.py` | Merged into `pipeline/comment_ai.py` |
| `app/pipeline/dedup.py` | Dedupe now handled by Mongo unique indexes + upserts |
| `app/pipeline/enrichment.py` | Merged into `agent/search.py` (`merge_page_details`) |
| `app/pipeline/scoring.py` | Merged into `pipeline/comment_ai.py` (lead_score) |
| `app/pipeline/tailored_agents.py` | Dead |
| `app/api/routes/leads.py` | Replaced by `/api/comments/{id}` lead detail |
| `app/api/routes/apify.py` | Actor list/debug endpoints, dev-only |
| `app/api/routes/facebook_pages.py` | Folded into `api/routes/search.py` |
| `app/api/routes/facebook_posts.py` | Folded into `api/routes/search.py` |
| `app/api/routes/facebook_comments.py` | Folded into `api/routes/search.py` |
| `app/api/routes/facebook_following.py` | Old engine + 24h following workflow |
| `app/tasks/` + `app/celery_app.py` | Celery background queue replaced by in-process `asyncio` background tasks |
| `app/utils/` | Dead helpers |

Also purged: all `__pycache__`, 34 legacy scratch tests (old flows/tools).

## Removed config / env keys

- `APIFY_ACTOR_BROWSER`, `APIFY_ACTOR_PAGES`, `APIFY_ACTOR_POSTS`, `APIFY_ACTOR_COMMENTS` — actor IDs now hardcoded in `apify_connector.py` (new/stable actor versions)
- `APIFY_ACTOR_FOLLOWING`, `FB_FOLLOWING_PROFILE_URL`, `FB_COOKIES` — following engine removed
- `GEMINI_*` temperature/max_tokens etc. — fixed in `comment_ai.py`
- `MIN_LEAD_SCORE` — lead filtering is now `is_lead` boolean from signals
- `BUSINESS_DOMAIN` — kept at v2, removed 2026-08-12 with keyword search

Remaining env keys (`.env`): `GEMINI_API_KEY`, `GEMINI_MODEL`, `MONGO_URI`, `MONGO_DB_NAME`, `APIFY_API_TOKEN`.

## Removed deps (requirements.txt)

`celery`, `redis`, `google-api-python-client`, `google-auth`, `beautifulsoup4`, `playwright`, `selenium`, `webdriver-manager`, `celery[redis]`, `python-multipart`, `pydantic-extra-types`, `email-validator`, `async-lru`, `orjson`, `python-dotenv` etc. — kept: `fastapi`, `uvicorn[standard]`, `pydantic`, `pydantic-settings`, `motor`, `pymongo`, `httpx`, `apify-client`.

## Collection model (5)

| Collection | Unique index | Purpose |
|---|---|---|
| `facebook_pages` | `facebook_url` | Discovered/enriched pages |
| `facebook_posts` | `post_url` | Posts of collected pages |
| `facebook_comments` | `comment_url` | Raw scraped comments |
| `ai_comments` | `comment_ref` | AI/rule lead analysis docs |
| `search_history` | `run_id` | Search runs + progress/status |

## Known external caveats (not code)

- Live Apify Facebook actors have returned 0 items recently (Facebook transient block) — retry pattern built in; tests use mocked connector.
- Live Gemini API currently returns HTTP 429 (rate limit) — comment analysis falls back to rule-only (`analyzed_by: "rules"`), which still surfaces phone/email/budget/requirement/urgency.
