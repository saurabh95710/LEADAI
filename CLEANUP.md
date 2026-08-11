# CLEANUP.md — LeadAI v2 refactor (2026-08-05)

Pre-refactor snapshot: `C:\Users\saura\AppData\Local\Temp\opencode\leadai_backup` (app/ + scratch/ + .env + requirements.txt, 169 files).

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
- `BUSINESS_DOMAIN` kept (used for relevance hints)

Remaining env keys (`.env`): `GEMINI_API_KEY`, `GEMINI_MODEL`, `BUSINESS_DOMAIN`, `MONGO_URI`, `MONGO_DB_NAME`, `APIFY_API_TOKEN`, `BRIGHTDATA_API_KEY` (+ optional `BRIGHTDATA_DATASET_PAGES/POSTS/COMMENTS`).

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
