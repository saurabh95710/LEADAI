# LeadAI REST API Reference

LeadAI provides two tiers of RESTful APIs:
1. **Public REST API v1 (`/api/v1/*`)**: For external automation, Zapier, Make, and CRM integrations using scoped API keys.
2. **Internal SaaS Application API (`/api/*`, `/api/org-admin/*`, `/api/super-admin/*`)**: For the LeadAI web application, authenticated via HTTP-only secure session cookies.

---

## 1. Public REST API v1

### Authentication
Include your API key in the `X-API-Key` request header or `Authorization: Bearer <API_KEY>`.
API keys have prefixes:
- `lai_live_...` for production data.
- `lai_test_...` for test sandbox environments.

All API keys are SHA-256 hashed at rest and evaluated for per-org rate limits (default 60 requests per minute).

### Scopes
- `leads:read`: Query leads and export prospect details.
- `leads:write`: Ingest external leads or update lead statuses.
- `search:create`: Trigger URL searches and background scrapes.
- `search:read`: Check status and retrieve search run reports.
- `*`: Full programmatic access.

### Endpoints

#### `GET /api/v1/leads`
Retrieve leads for the organization.
- **Scope required**: `leads:read`
- **Query Parameters**:
  - `limit`: int (default 50, max 100)
  - `platform`: string (optional: `facebook`, `instagram`, `youtube`)
  - `priority`: string (optional: `high`, `medium`, `low`)
- **Response**:
```json
{
  "success": true,
  "total": 42,
  "leads": [
    {
      "id": "60c72b2f9b1d8b2bad7c9099",
      "platform": "youtube",
      "author_name": "Jane Doe",
      "author_url": "https://youtube.com/@janedoe",
      "text": "How much for the 3-bedroom unit in Bandra?",
      "phone": "+919876543210",
      "email": "jane@example.com",
      "lead_score": 85,
      "lead_quality": "hot",
      "intent": "pricing_inquiry",
      "created_at": "2026-10-02T10:00:00Z"
    }
  ]
}
```

#### `POST /api/v1/leads`
Ingest or sync an external lead into the organization's CRM pipeline.
- **Scope required**: `leads:write`
- **Request Body**:
```json
{
  "author_name": "Alex Smith",
  "text": "Looking for commercial office space in Bangalore",
  "platform": "youtube",
  "phone": "+919876500000",
  "email": "alex@example.com",
  "budget": "50L",
  "intent": "purchase_inquiry",
  "priority": "high"
}
```

#### `POST /api/v1/search`
Start a URL scraping and lead discovery job.
- **Scope required**: `search:create`
- **Request Body**:
```json
{
  "url": "https://youtube.com/@examplechannel",
  "max_posts": 20,
  "max_comments_per_post": 30
}
```

- `max_posts` and `max_comments_per_post` are optional (the platform defaults apply). A value above the plan's limit is refused (`402 PLAN_LIMIT`).
- The same rules as the app apply: the feature and platform switches, URL validation (`422`), plan limits, the monthly search quota and token charges (`402 TOKENS_EXHAUSTED` / `QUOTA_EXCEEDED`).
- The run appears in the organization's search history with `source: "api"`.

#### `GET /api/v1/search/{run_id}`
Check status and metrics of a search run.
- **Scope required**: `search:read` (keys with `search:create` can read too)

#### `POST /api/v1/searches/keyword`
Start a keyword (discovery) search: find profiles for keywords, filter them, and collect comments only from those that pass. See [KEYWORD_SEARCH.md](KEYWORD_SEARCH.md).
- **Scope required**: `search:create`
- **Request Body**:
```json
{
  "keywords": ["villa", "real estate Goa"],
  "platforms": ["instagram", "facebook"],
  "filters": {
    "min_followers": 5000,
    "recent_posts_to_analyze": 10,
    "min_avg_comments": 3,
    "active_within_days": 60,
    "has_contact_info": true,
    "location_keyword": "Kota, Rajasthan",
    "location_match": "all",
    "category_keyword": "software, IT services",
    "exclude_keywords": ["jobs"]
  },
  "strict": false,
  "limits": {"max_candidates": 100, "recent_posts": 10, "comments_per_post": 30},
  "confirm": true,
  "max_tokens": 400
}
```
- Every filter is optional:
  - numbers: `min_followers`, `max_followers`, `min_posts`, `recent_posts_to_analyze`, `min_avg_comments`, `min_total_comments`, `min_engagement_rate` (%), `active_within_days`;
  - true/false: `business_account_only`, `has_contact_info`, `verified_only`;
  - text: `location_keyword` (places separated by commas, most specific first), `location_match` (`all` — default, every place — or `any`), `category_keyword` (business types separated by commas; one must match);
  - list: `exclude_keywords`;
  - `language` is still accepted for compatibility but no platform can measure it (always unsupported).

  Location and business type are also sent to the Apify search where the actor supports it (Facebook / LinkedIn `locations`, Instagram / YouTube search text, the YouTube channel-search actor's subscriber / video / verified / country inputs); see [KEYWORD_SEARCH.md](KEYWORD_SEARCH.md#what-is-sent-to-apify).

  Unknown keys and invalid values return `422` with the field. Limits above the plan are lowered to the plan's maximum.
- `confirm: true` is required; without it the response is `409 CONFIRM_REQUIRED` with the cost estimate. `max_tokens` is an optional hard budget.
- Response: `{run_id, status: "running", estimate, message}`. Run ids start with `KW_API_`.
- Errors:
  - `402`: plan, quota or tokens.
  - `403`: feature or platform switched off, or not in the plan.
  - `422`: invalid filters or request.
  - `429`: rate limit, or too many searches running.

#### `GET /api/v1/searches/keyword/{run_id}`
Status of a keyword search.
- **Scope required**: `search:read` or `search:create`
- Response:
  - `status`: `running`, `completed`, `error` or `cancelled`.
  - `phase`: `discovery`, `enrichment`, `sampling`, `deep_scrape`, `completed`, `stopped_tokens`, `stopped_budget`, and so on.
  - `stages{...}`: each stage's state.
  - `stage_counts{discovered, enriched, sampled, passed, rejected, needs_sampling, errors, comments_scraped, leads}`.
  - `tokens_charged{...}` and `platform_errors{...}`.
  - `discovery_queries[{platform, keyword, query_text, locations[], extra_inputs{}}]`: what each keyword search sent to Apify.
  - `passed_count` and `rejection_summary[{filter, label, count, outcome, message}]`: the most common reasons profiles were rejected, e.g. `{"filter": "location_keyword", "count": 26, "message": "location: 'Rajasthan' not found"}` (`outcome` is `unknown` for values strict mode rejected because they were missing).

#### `GET /api/v1/searches/keyword/{run_id}/candidates`
Every profile found, with why it passed or was rejected. Without `status` every status is returned; the app lists `status=passed` by default.
- **Scope required**: `search:read` or `search:create`
- Query: `status` (`passed`, `rejected`, `needs_sampling`, `error`, `pending`), `platform`, `sort` (`followers`, `engagement`, `avg_comments`, `newest`), `page`, `page_size` (up to 100).
- Each item has:
  - `status`, `metrics{followers, posts_count, avg_comments, total_comments, engagement_rate, last_post_at, ...}`;
  - `reasons[{filter, outcome: pass|fail|unsupported|unknown, message, value, threshold}]`, e.g. `"followers 800 < min 5000"`;
  - `unsupported[]` and `unknown[]`;
  - `profile` (name, URL, bio, contacts).

#### `POST /api/v1/searches/keyword/{run_id}/reapply`
Re-check the stored profiles with different filters, without scraping or charging.
- **Scope required**: `search:create`
- Body: `{filters, strict}`.
- Response: `{counts, changed, continue_cost, notes}`. Profiles that now pass but whose posts were never fetched become `needs_sampling`; fetch them with `POST /api/keyword-search/{run_id}/continue` from the app.

---

## 2. Workspace Product API (`/api/*`)

Requires an authenticated tenant session cookie.

### Search & Scraping
| Method | Endpoint | Description | Permission |
|---|---|---|---|
| `POST` | `/api/url/search` | Launch a single URL-based scraping run | `search.create` |
| `POST` | `/api/search/bulk` | Start a search for each URL of a batch (up to 50). Each URL passes the same checks as a single search (switches, plan caps, tokens, monthly quota, searches running); refused URLs are listed with the reason | `search.create` |
| `POST` | `/api/search/scheduled` | Create a recurring scheduled scan | `search.create` |
| `GET` | `/api/search/scheduled` | List scheduled scans: the whole organization's for owners / admins, a member's own otherwise. Scheduled scans are stored but not run yet (see KNOWN_LIMITATIONS.md) | `search.view` |
| `DELETE` | `/api/search/scheduled/{id}` | Cancel and delete a scheduled scan | `search.create` |
| `GET` | `/api/search/history` | List recent search runs for the workspace | `search.view` |
| `GET` | `/api/search/{run_id}` | Poll status, progress, and discovered pages | `search.view` |
| `POST` | `/api/search/{run_id}/cancel`| Request immediate cancellation of a running search | `search.cancel` |
| `DELETE` | `/api/search/{run_id}` | Delete a search run and all associated data | `search.cancel` |

### Keyword search ([KEYWORD_SEARCH.md](KEYWORD_SEARCH.md))
Keyword runs also appear in `/api/search/history`, and the pages that pass are browsable with the normal pages, posts and comments endpoints.

| Method | Endpoint | Description | Permission |
|---|---|---|---|
| `GET` | `/api/keyword-search/capabilities` | Filters, what each platform supports, defaults, this organization's caps and token prices | `search.view` |
| `POST` | `/api/keyword-search/estimate` | Worst-case tokens per stage and rough Apify cost for a request | `search.create` |
| `POST` | `/api/keyword-search` | Start (`confirm: true` required) | `search.create` |
| `GET` | `/api/keyword-search/{run_id}` | Status, stages, counts, tokens charged, `discovery_queries` (what was sent to Apify), `passed_count`, `rejection_summary` | `search.view` |
| `GET` | `/api/keyword-search/{run_id}/candidates` | Candidates with reasons and metrics (filter, sort, paginate) | `search.view` |
| `POST` | `/api/keyword-search/{run_id}/reapply` | Re-check with new filters (no scraping, free) | `search.create` |
| `POST` | `/api/keyword-search/{run_id}/continue` | Fetch the profiles that now need posts (`confirm: true`; charged) | `search.create` |
| `POST` | `/api/keyword-search/{run_id}/cancel` | Stop; results so far are kept | `search.cancel` |
| `GET` / `POST` / `DELETE` | `/api/keyword-search/presets[/{id}]` | Saved filter presets (own and shared) | `search.view` / `search.create` |

### Leads & Comments
| Method | Endpoint | Description | Permission |
|---|---|---|---|
| `GET` | `/api/posts/{id}/comments` | Get analyzed comments and leads for a post | `search.view` |
| `GET` | `/api/comments/{id}` | Get lead detail view with AI context | `leads.view` |
| `PATCH` | `/api/leads/{id}` | Update status (`new`, `contacted`, `qualified`, etc.) | `leads.manage` |
| `POST` | `/api/leads/{id}/notes` | Add timestamped team note to lead | `leads.manage` |
| `POST` | `/api/leads/{id}/follow-ups` | Schedule a follow-up reminder | `leads.manage` |
| `POST` | `/api/leads/assign` | Assign lead to an organization member | `leads.assign` |

### Exports
| Method | Endpoint | Description | Permission |
|---|---|---|---|
| `GET` | `/api/export/{scope}.csv` | Export pages, posts, or comments as CSV | `exports.create` |
| `GET` | `/api/leads/export/xlsx` | Export leads as sanitized Excel spreadsheet XML | `exports.create` |

---

### API usage & limits ([API_LIMITS.md](API_LIMITS.md))
| Method | Endpoint | Description | Permission |
|---|---|---|---|
| `GET` | `/api/me/api-limits` | Tokens, plan limits, whether searches and AI are available, issues with what to do | any member |
| `GET` | `/api/org-admin/api-limits` | Own keys' limits, tokens, plan, LeadAI services status | org owner / admin |
| `POST` | `/api/org-admin/api-limits/refresh` | Re-check own keys (3 per 5 min) | org owner / admin |
| `GET` | `/api/super-admin/api-limits` | Every service's limits + organizations worst-first | Super Admin |
| `POST` | `/api/super-admin/api-limits/refresh` | Check now: `{scope: platform \| all \| <org id>}` (5/min) | Super Admin |
| `GET` | `/api/super-admin/api-limits/history` | Up to 30 days of checks per service | Super Admin |

## 3. Organization Admin API (`/api/org-admin/*`)

Requires tenant `owner` or `admin` role with active subscription.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/org-admin/overview` | Organization dashboard stats and activity |
| `GET` | `/api/org-admin/users` | List members with roles, statuses, and usage |
| `POST` | `/api/org-admin/users/{id}/reset-access` | Generate one-time password reset link |
| `GET` | `/api/org-admin/roles` | Configurable role matrix and delegable permissions |
| `POST` | `/api/org-admin/assignment-rules` | Create automated lead assignment rule with SLA |
| `GET` | `/api/org-admin/assignment-rules` | List lead assignment rules |
| `DELETE` | `/api/org-admin/assignment-rules/{id}` | Delete assignment rule |
| `POST` | `/api/org-admin/webhooks` | Register outbound webhook endpoint (`url`, `secret`, `events`). Deliveries are not sent yet (see KNOWN_LIMITATIONS.md) |
| `GET` | `/api/org-admin/webhooks` | List registered webhooks (the signing secret only as a masked hint) |
| `DELETE` | `/api/org-admin/webhooks/{id}` | Remove webhook |
| `POST` | `/api/org-admin/api-keys` | Generate new public API key |
| `GET` | `/api/org-admin/api-keys` | List active API keys |
| `DELETE` | `/api/org-admin/api-keys/{id}` | Revoke API key |
| `GET` | `/api/org-admin/audit-logs` | Filterable organization audit trail |

---

## 4. Super Admin Platform API (`/api/super-admin/*`)

Requires internal platform role (`super_admin`, `operations_admin`, etc.).

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/super-admin/overview` | Platform-wide metrics, active orgs, revenue |
| `GET` | `/api/super-admin/organizations` | List all customer organizations |
| `PATCH` | `/api/super-admin/organizations/{id}/status` | Activate, suspend, or archive organization |
| `GET` | `/api/super-admin/plans` | List and edit subscription plan catalog |
| `GET` | `/api/super-admin/unit-economics` | Real-time token and compute margin economics report |
| `GET` | `/api/super-admin/audit-logs` | Platform-wide master audit logs |
