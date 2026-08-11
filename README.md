# LeadAI v2 — Facebook Lead Intelligence Platform

Discovers Facebook business pages via Apify, collects their posts and comments, and uses an AI/rule pipeline to extract high-intent leads (phone, email, WhatsApp, budget, requirement, urgency) with a 0–100 lead score — served through a FastAPI REST API with a browser dashboard.

> **Stack:** FastAPI · MongoDB (motor) · Apify actors · Google Gemini (optional) · vanilla JS UI · Docker

---

## What it does

1. **Search** — natural-language query ("property dealers in jaipur") → AI intent parser extracts keyword / city / state / category → Apify `facebook-search-scraper` finds pages, enriched by `facebook-pages-scraper` (phone/email/website/about, never overwriting existing data).
2. **Posts** — pick a page → Apify `facebook-posts-scraper` collects post URLs, text, images, stats.
3. **Comments + AI analysis** — pick a post → Apify `facebook-comments-scraper` collects comments → `comment_ai` pipeline scores each comment:
   - **Stage 1 (rules, no API key needed):** filters spam/link-only/gratitude filler; regex-extracts phone, email, WhatsApp, website, budget, requirement, city, urgency, buying intent.
   - **Stage 2 (Gemini, optional):** richer intent/contact extraction with 429 backoff. Without a key everything still works on rules (`analyzed_by: "rules"`).
   - **lead_score** = confidence·50 + priority(≤20) + lead quality(≤20) + contact(≤10) − spam(≤20); `is_lead` = has ≥1 display signal.
4. **Leads** — filter/sort by score, drill into any comment for full context (raw comment + post + page), export pages/posts/comments as CSV.

All long-running scrapes run as background tasks (`asyncio`) with status polling — no Celery/Redis.

---

## API

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | App + Mongo status |
| POST | `/api/search?query=...&limit=...` | Start search (returns `run_id`, intent) |
| GET | `/api/search/{run_id}` | Poll search status + found pages |
| GET | `/api/search/history` | Previous runs |
| GET | `/api/pages?run_id=&q=&category=&city=&contact=` | List/filter pages |
| GET | `/api/pages/{id}` | Page detail (+ progress) |
| POST | `/api/pages/{id}/posts?max_posts=` | Collect posts (background) |
| GET | `/api/pages/{id}/posts` | List posts + progress |
| GET | `/api/posts/{id}` | Post detail |
| POST | `/api/posts/{id}/comments?max_comments=` | Collect + AI-analyze comments (background) |
| GET | `/api/posts/{id}/comments?only_leads=` | List comments/leads (leads only by default) |
| GET | `/api/comments/{id}` | Lead detail with comment/post/page context |
| GET | `/api/export/pages.csv?run_id=` | CSV export |
| GET | `/api/export/posts.csv?page_id=` | CSV export |
| GET | `/api/export/comments.csv?post_id=` | CSV export |

---

## Data model (5 collections)

| Collection | Unique index | Notes |
|---|---|---|
| `facebook_pages` | `facebook_url` | dedupe by URL; `posts_status`/`comments_status` track pipeline progress |
| `facebook_posts` | `post_url` | `page_ref` → pages |
| `facebook_comments` | `comment_url` | raw scraped comments |
| `ai_comments` | `comment_ref` | analysis docs; `is_lead`, `lead_score`, extracted contact/intent; `comment_ref`→comments, `post_ref`→posts, `page_ref`→pages |
| `search_history` | `run_id` | run status/phases/counts |

---

## Getting started

```bash
pip install -r requirements.txt
# or: docker compose up -d   (api + mongo)

cp .env.example .env    # if you have one; otherwise edit .env
python -m app.main      # runs uvicorn on :8000
```

Requirements: MongoDB running locally (default `mongodb://localhost:27017`), `.env` keys:

| Key | Required | Default |
|---|---|---|
| `MONGO_URI` | yes | `mongodb://localhost:27017` |
| `MONGO_DB_NAME` | yes | `LeadAI` |
| `APIFY_API_TOKEN` | yes (for live Apify scrapes) | — |
| `BRIGHTDATA_API_KEY` | no (for live Bright Data scrapes) | — |
| `BRIGHTDATA_DATASET_PAGES` | no | `gd_mf124a0511bauquyow` |
| `BRIGHTDATA_DATASET_POSTS` | no | `gd_lkaxegm826bjpoo9m5` |
| `BRIGHTDATA_DATASET_COMMENTS` | no | `gd_lkay758p1eanlolqw8` |
| `GEMINI_API_KEY` | no (rule-only mode) | — |
| `GEMINI_MODEL` | no | `gemini-2.5-flash` |
| `BUSINESS_DOMAIN` | no | `general B2B services` |

The UI runs at `http://localhost:8000/dashboard`.

### Data providers — Apify or Bright Data

Every search asks **which provider to use** (toggle in the search box; the choice
is remembered per search run and reused for that run's posts/comments):

- **Apify** — `APIFY_API_TOKEN` from https://apify.com/account/integrations.
  Pages via `facebook-search-scraper`, details via `facebook-pages-scraper`,
  posts/comments via the paid posts/comments actors.
- **Bright Data** — `BRIGHTDATA_API_KEY` from https://brightdata.com/cp/setting/users.
  Uses the official Facebook Scraper API: the **Discover API** finds pages
  (`site:facebook.com <keyword> <city>`), then the pages/profiles dataset
  (`gd_mf124a0511bauquyow`) enriches phone/email, the posts dataset
  (`gd_lkaxegm826bjpoo9m5`) and comments dataset (`gd_lkay758p1eanlolqw8`)
  collect posts/comments. All output is normalized to the same internal shape.

---

## Tests

```bash
python scratch\test_intent.py
python scratch\test_comment_ai.py
python scratch\test_search_flow.py
python scratch\test_api.py
```

Tests use a **real local MongoDB** (`LeadAI_test`) and a **mocked Apify connector** (`FakeApifyConnector`), so no live API keys or network access are needed. `test_api.py` runs the full HTTP surface via `httpx.ASGITransport` in a single event loop, including background-task polling.

---

## Structure

```
app/
  main.py                 FastAPI app (v2)
  config.py               pydantic-settings (.env)
  db/models.py            Pydantic models (5 collections)
  db/mongo.py             sync + async clients, ensure_indexes
  connectors/apify_connector.py       Apify: 4 actors, retries, keyword variants
  connectors/brightdata_connector.py  Bright Data: Discover + 3 datasets, same interface
  agent/intent.py         natural-language → keyword/city/state/category
  agent/search.py         run_search / collect_page_posts / collect_post_comments
  pipeline/comment_ai.py  rule + Gemini scoring → ai_comments
  api/routes/search.py    all endpoints + background tasks + CSV export
  static/                 dashboard UI (index.html, app.js, styles.css)
scratch/                  test suites (mocked Apify, real Mongo)
```

## Notes / caveats

- Apify Facebook actors may transiently return 0 items (Facebook blocking) — the connector retries per actor and the UI surfaces an "empty" status; simply retry later.
- Posts/comments actors are **paid (pay-per-event)** — if the Apify account runs out of credits, collection stops and the UI shows a clear billing error with the upgrade link (search stays free).
- Gemini calls failing (e.g. 429) never break the pipeline — analysis falls back to rules and the failure is logged.
- See `CLEANUP.md` for what was removed in the v2 refactor (backup path included).
