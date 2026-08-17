# Admin Control Center — Implementation Report

A full admin panel for LeadAI: one app, one server, one Mongo. Every control
changes **real backend behavior** — no placeholder toggles. All data shown is
real data from the existing collections; the panel only adds three new
collections (`system_settings`, `admin_users`, `audit_logs`).

## Access

| URL | What |
|---|---|
| `/admin` | Admin SPA (single HTML+CSS+JS, no build step) |
| `/api/admin/*` | JSON API (48 endpoints), same auth as the user app |

Sign in with the existing admin account (env `admin_email` / password). The
env account always has `super_admin` role and can never be locked out.

## What was built

### Backend

**New modules**

| File | Purpose |
|---|---|
| `app/admin/settings.py` | Dynamic settings registry: `system_settings` collection (key → value) layered over `.env` defaults; sync/async/TTL-cached accessors; coercion to the registered type; `effective_limits()`, `get_apify_token()`, `is_platform_enabled()`, `sessions_epoch()`; every admin control reads through here at runtime |
| `app/admin/audit.py` | `audit_logs` writes (sync + async) with recursive secret redaction (`token`, `password`, `secret`, …) — secrets never reach the log |
| `app/auth/roles.py` | Three roles: `viewer` < `manager` < `super_admin`; `require_admin(min_role)` FastAPI dependency (plain callable); `current_admin` re-reads role from DB per request and enforces session timeout + session-epoch revocation |

**`app/api/routes/admin.py` — 48 endpoints**

- Dashboard (counts + Apify/maintenance status), Jobs (list/detail/retry/
  cancel/delete), Failed jobs, Leads (list/patch/bulk), Analytics
- Platforms (toggle enable + set actor IDs per kind), Apify (status/test/
  set/clear token), Actors (list/test), Usage (real `usageUsd` from run
  metadata)
- Limits, AI (incl. live Gemini test), Lead Scoring, Comment Intelligence,
  Features (all GET/PUT)
- Maintenance (GET/POST), Database (dbStats + collections + indexes), Logs
  (tail + download), CSV exports (`/api/admin/export/{scope}.csv`),
  Audit logs, Users (CRUD, super-admin only), Security (session timeout,
  login protection, audit toggle, revoke-all-sessions, change password),
  Health

**User-app enforcement (the controls are real)**

- `app/social/url_search.py`, `app/agent/search.py`, `app/api/routes/search.py`
  — platform-disabled scrapes rejected server-side; Apify token required;
  posts/comments clamped to `effective_limits()`; `features.url_search` and
  `features.exports` gates; min-comments read from settings; `scrape_info`
  (incl. `usageUsd`) persisted to `search_history`
- `app/connectors/apify_connector.py`, `app/social/scrapers.py` — Apify token
  and actor IDs read fresh from settings on every call
- `app/pipeline/comment_ai.py` — AI enabled/rule-fallback/model/temperature;
  lead-score weights configurable with the **original formula as defaults**;
  new `signal_lead_score()` + `derive_quality_from_score()`; per-signal
  detection toggles; `selling_intent` detection off by default
  (no behavior change out of the box)
- `app/auth/service.py` — multi-user login (admin_users collection), env
  account = recovery super-admin, login-protection flag, session `iat` claim
- `app/main.py` — maintenance gate (outermost): 503 for the user app, admin
  panel + `/api/admin` + `/health` + `/static` always open, any valid session
  passes
- `app/db/mongo.py` — indexes for `admin_users.email` (unique) and
  `audit_logs`

### Frontend

`app/static/admin.html` + `admin.css` + `admin.js` — premium dark SPA
reusing the `styles.css` design tokens: collapsible sidebar with every
section, skeleton loading, toast + confirmation modals, paged tables with
server-side filters, masked token hints (`••••1234` — the full token is never
returned by any API), viewer-mode hiding of write controls, 401 → auto
redirect to `/login`.

## Data model (new collections)

| Collection | Shape |
|---|---|
| `system_settings` | `{_id: "limits.max_posts_cap", value: 100, updated_at, updated_by}` |
| `admin_users` | `{email, name, password_hash (sha256), role, created_at, updated_at}` |
| `audit_logs` | `{action, category, user, role, ip, success, details, at}` |

## Verification

- `python -m pytest tests -q` → **59 passed** (43 existing + 16 new admin
  tests: settings defaults/coercion/DB-over-env, token masking, audit
  redaction, role ranking, scoring-formula defaults)
- Live smoke test against a running uvicorn server: all 48 `/api/admin/*`
  endpoints return 200 with the admin cookie, 401 without it; viewer role
  gets 403 on writes; maintenance on → anon user app 503 while admin panel
  stays up, message persisted, off → restored; settings PUT → GET round-trip
  verified; audit trail written for every action
- Startup now logs `MongoDB indexes verified successfully` (fixed an invalid
  `unique` spec on the implicit `_id` index)

## Limitations / notes

- Apify usage figures come only from `scrape_info.usageUsd` on runs stored
  after the connector change; older runs show as "not reported".
- `ci.detect_selling_intent` and `scoring.derive_quality` default **off** so
  existing lead filtering/scoring is unchanged until an admin opts in.
- Maintenance message is kept after maintenance is switched off (reused on
  the next enable).
## Environment variable management (Environment view)

`/admin` -> Environment: 19 real env vars, registry-driven
(`app/admin/envvars.py`). Three-layer resolution shown live on every row:
**DB override wins** (`env_overrides` collection, survives restarts) ->
real `.env` / process value -> documented default. Overrides never rewrite
the `.env` file.

- **Real values everywhere**: `GEMINI_API_KEY`, `APIFY_API_TOKEN`, admin
  credentials and session settings are read through `get_envvar()` at the
  moment they matter. Changing `ADMIN_PASSWORD_HASH`, `ADMIN_EMAIL`,
  `SESSION_TTL_DAYS`, `SESSION_COOKIE_SECURE`, `SESSION_SECRET`,
  `GEMINI_API_KEY`, `BUSINESS_DOMAIN` applies immediately (5s TTL cache);
  `MONGO_URI`, actor IDs, `MIN_COMMENTS`, `API_PORT` etc. are marked
  "needs restart" (consumers read them once at import).
- **Change admin password** card (super_admin): hashes server-side (sha256)
  and stores the override; verified end-to-end by logging in with the new
  password, then resetting.
- **Roles**: manager+ edits non-secret vars (403 on secrets); super_admin
  only for secret vars and the password endpoint. Secrets are never
  returned by the API � only masked hints. All changes audited
  (`env.set` / `env.delete` / `env.password.change`).
- `APIFY_API_TOKEN` is shown read-only with its real masked hint and is
  managed in the Apify view (single source of truth: `apify.token`).

Test suite is now **73 passed** (14 new envvar tests).
