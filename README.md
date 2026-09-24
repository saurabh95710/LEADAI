# LeadAI v2.5.0 — AI-Orchestrated Social Lead Intelligence Platform

An AI-powered **SaaS lead generation and management platform** that works from a **single social URL**: paste a Facebook page, Instagram profile, YouTube channel, or LinkedIn company link, and the platform fetches real data through **Apify** actors, drills into posts and comments, and uses a **rule-based + Google Gemini** pipeline to extract, qualify, and score high-intent leads — served through a FastAPI REST API with a browser dashboard, a full admin control center, and a public marketing website.

Paste **`https://www.facebook.com/somepage`** (or an Instagram / YouTube / LinkedIn profile) and the platform:

1. Detects + canonicalizes the URL (`app/social/url_detector.py`)
2. Fetches page details via the platform's Apify actor
3. Collects the latest posts and auto-analyzes each post for relevance
4. Collects comments (Facebook, Instagram & LinkedIn) from qualifying posts
5. AI-analyzes each comment for phone, email, WhatsApp, budget, requirement, urgency, and intent (buying / selling / rent / investment / other)
6. Scores leads 0–100 with a deterministic rank, manages lead lifecycle (new → contacted → qualified → follow-up → converted / lost), and lets you export pages, posts, and leads as CSV

The detected platform (facebook / instagram / youtube / linkedin) is propagated end-to-end — storage, API responses, UI labels, report page, and CSV exports — so an Instagram search always looks like Instagram and is **never** mislabeled as Facebook.

> **Stack:** FastAPI · MongoDB (Motor + PyMongo) · Apify actors · Google Gemini (optional — rule-based fallback) · Vanilla JS UI · Docker · bcrypt auth · Multi-tenant SaaS architecture

---

## Table of Contents

- [1. Project Overview](#1-project-overview)
- [2. Key Features](#2-key-features)
- [3. Technology Stack](#3-technology-stack)
- [4. System Architecture](#4-system-architecture)
- [5. Folder Structure](#5-folder-structure)
- [6. End-to-End Workflow](#6-end-to-end-workflow)
- [7. UI Pages & Panels](#7-ui-pages--panels)
- [8. Admin Control Center](#8-admin-control-center)
- [9. Public Website](#9-public-website)
- [10. SaaS & Multi-Tenancy](#10-saas--multi-tenancy)
- [11. CMS (Content Management)](#11-cms-content-management)
- [12. Lead Data Model](#12-lead-data-model)
- [13. API Architecture](#13-api-architecture)
- [14. Apify Architecture](#14-apify-architecture)
- [15. AI Architecture](#15-ai-architecture)
- [16. Comment Filter Pipeline](#16-comment-filter-pipeline)
- [17. Lead Lifecycle](#17-lead-lifecycle)
- [18. Data Flow](#18-data-flow)
- [19. Error Handling](#19-error-handling)
- [20. Environment Variables](#20-environment-variables)
- [21. System Settings](#21-system-settings)
- [22. Installation](#22-installation)
- [23. Running the Project](#23-running-the-project)
- [24. Authentication & Authorization](#24-authentication--authorization)
- [25. Cost and Resource Usage](#25-cost-and-resource-usage)
- [26. Security](#26-security)
- [27. Performance](#27-performance)
- [28. Logging and Monitoring](#28-logging-and-monitoring)
- [29. Testing](#29-testing)
- [30. Troubleshooting](#30-troubleshooting)
- [31. Developer Guide](#31-developer-guide)
- [32. Git Workflow](#32-git-workflow)
- [33. Architecture Summary](#33-architecture-summary)

---

## 1. Project Overview

### The Problem

Businesses that sell high-value products (real estate, automobiles, interior design, wedding services, etc.) need a constant stream of qualified buyers. Social media is full of buying signals — business pages post offers and interested people comment with phone numbers, budgets, and urgent requirements. Manually reading thousands of comments is impossible.

### What LeadAI Does

LeadAI automates the entire funnel from **a single social URL**:

- **Understands the target** — any Facebook page, Instagram profile, YouTube channel, or LinkedIn company URL is detected, validated, and canonicalized (`app/social/url_detector.py`). Groups, single posts/videos, and malformed links are rejected with a clear error type.
- **Finds real data** — the URL is handed to the platform's Apify actor (`app/social/scrapers.py`). The agent never scrapes social media itself and never fabricates data — every stored value comes from real actor output.
- **Scores the page** — posts are collected and qualified: a post is *relevant* when its text mentions the target page's context, and *qualifying* when it also has at least `MIN_COMMENTS` (default 10) platform-reported comments. Pages get a deterministic `lead_score` from qualifying-post volume, comment volume, and posting recency/activity.
- **Finds leads in comments** — comments on qualifying posts are collected and passed through a two-stage pipeline: a free, offline rule stage (filters filler/spam, regex-extracts phone/email/whatsapp/budget/location/urgency) and an optional Gemini stage (rich extraction of contact info, person context, and buyer signals). Every lead gets a 0–100 `lead_score`, a priority (high/medium/low), a lead quality (hot/warm/cold), and a value list of `is_lead` display signals.
- **Manages lead lifecycle** — leads move through a state machine (new → contacted → qualified → follow-up → converted / lost / disqualified / archived) with notes, follow-ups, and full status history.
- **Presents and exports** — a live dashboard lets you drill page → posts → comments → lead cards, a standalone report page opens automatically per run, any level can be exported as CSV, and a full admin control center provides system-wide management.

### What Makes It Different from a Simple Scraper

- Deterministic, explainable **qualification logic** (relevance tokens + comment-count threshold) instead of dumping everything.
- **One provider, well integrated** — all scraping flows through `ApifyConnector` with classified errors and cancellation support.
- **AI analysis with a rule-based fallback** — the product works end-to-end even without a Gemini key.
- **Classified errors** — failures are labeled (`BLOCKED`, `ACTOR_FAILED`, `NO_RESULTS`, `ACCESS_DENIED`, ...) with run/dataset IDs, so "no results" is never mistaken for a block.
- **Cancellable background jobs** with live status persisted in MongoDB.
- **Full admin control center** with 25+ views for managing platforms, AI, scoring, users, security, and system settings.
- **Lead lifecycle management** with state machine, notes, follow-ups, and audit trail.

### Who It Is For

Sales teams, agencies, and lead-generation businesses that already know which pages/profiles matter and want qualified buyers/sellers from social-media conversations — especially around Indian markets.

---

## 2. Key Features

Status legend: ✅ Implemented · 🟡 Partially Implemented · ⬜ Planned

### Core Search & Pipeline

| Feature | Status | Location |
| --- | --- | --- |
| URL-based search: Facebook / Instagram / YouTube / LinkedIn | ✅ | `POST /api/url/search` → `app/social/` |
| URL detection + canonicalization (groups/posts/videos rejected) | ✅ | `app/social/url_detector.py` |
| Per-platform Apify actors (configurable actor IDs) | ✅ | `app/social/scrapers.py` + `app/config.py` |
| Graceful fallback page doc when details actor fails | ✅ | `_url_derived_page` in `app/social/url_search.py` |
| Platform propagation end-to-end (never mislabeled) | ✅ | `platform_from_url` / `_resolve_platform` + `platform` field everywhere |
| Configurable "Comments / Post" (1–500) | ✅ | UI setting synced with URL search (`memory.commentsPerPost`) |
| Comments loading progress bar (determinate + indeterminate) | ✅ | `showCommentsScrapeProgress` in `app.js` |
| Post qualification (relevant + ≥ MIN_COMMENTS) | ✅ | `_is_qualifying_post` in `app/agent/search.py` |
| Page lead ranking (qualifying posts, comments, activity) | ✅ | `_lead_score` in `app/agent/search.py` |
| Duplicate removal (unique indexes per run) | ✅ | MongoDB unique indexes |

### Data Collection

| Feature | Status | Location |
| --- | --- | --- |
| Facebook page detail extraction (followers, likes, category, about, phone, email, website, address, photos, verified) | ✅ | `apify/facebook-pages-scraper` + `map_page_item` |
| Facebook post collection (caption, images, videos, links, likes, comments, shares, date) | ✅ | `apify/facebook-posts-scraper` |
| Facebook comment collection (text, author, profile URL, date, reactions) | ✅ | `apify/facebook-comments-scraper` |
| Comments for Facebook, Instagram & LinkedIn; YouTube skips comments | ✅ | `app/social/scrapers.py` (`comments_supported`) |
| LinkedIn posts + comments (drill-down and URL search) | ✅ | `get_scraper(platform)` routes by platform |

### AI & Lead Analysis

| Feature | Status | Location |
| --- | --- | --- |
| Contact-info flagging (10-digit phone / email in comment) | ✅ | `has_contact_info` in `app/pipeline/comment_ai.py` |
| Rule-based comment screening (spam / filler / emoji / link-only) | ✅ | Stage 1 of `comment_ai.py` |
| Gemini comment analysis (contacts, budget, requirement, intent, urgency, quality) | ✅ (needs `GEMINI_API_KEY`) | Stage 2 of `comment_ai.py` |
| Buyer/seller/other intent classification | ✅ | Gemini `lead_type` + rule path |
| Lead quality (hot/warm/cold) and priority (high/medium/low) | ✅ | Gemini + `comment_lead_score` |
| Deterministic 0–100 lead score | ✅ | `comment_lead_score` in `comment_ai.py` |
| Keyword/comment filter pipeline (keyword/category/advanced rules) | ✅ | `app/pipeline/comment_filter.py` |
| Predefined business categories (real estate, automotive, etc.) | ✅ | `CATEGORIES` dict in `comment_filter.py` |
| Lead lifecycle state machine (new → contacted → qualified → ...) | ✅ | `app/pipeline/lead_lifecycle.py` |
| Notes and follow-ups on leads | ✅ | `POST /api/leads/{id}/notes` + `/follow-ups` |

### UI & Dashboard

| Feature | Status | Location |
| --- | --- | --- |
| Live dashboard (URL search → pages → posts → comments/leads) | ✅ | `app/static/index.html` + `app.js` |
| Standalone URL-search report page | ✅ | `/static/url_report.html` |
| Lead detail modal (full dossier with status, notes, follow-ups, history) | ✅ | `openLeadDetail` in `app.js` |
| Page filters (category / city / name / has-contact) | ✅ | `GET /api/pages` |
| Comments filter (all / leads / contact / hot / pricing / inquiry) | ✅ | `GET /api/posts/{id}/comments` |
| Search bar with debounced input | ✅ | `commentsSearchInput` in `app.js` |
| Quality filter dropdown | ✅ | `commentsQualityFilter` |
| Sort by options | ✅ | `commentsSortBy` |
| CSV export (pages / posts / leads) | ✅ | `GET /api/export/*.csv` |
| Search history with session stats | ✅ | `GET /api/search/history` |
| Manual deletion of individual searches | ✅ | `DELETE /api/search/{run_id}` + ✕ button |
| Run cancellation (aborts in-flight Apify run) | ✅ | `POST /api/search/{run_id}/cancel` |
| Live progress polling (UI progress bar + status fields) | ✅ | `app.js` `pollSearchRun` |
| localStorage workflow memory (survives refreshes) | ✅ | `memory` object in `app.js` |
| Toast notifications (success / error / warning / info) | ✅ | `toast()` in `app.js` |
| Ambient background orbs + grid overlay | ✅ | CSS animations in `styles.css` |
| Responsive layout | ✅ | CSS media queries |

### Authentication & Admin

| Feature | Status | Location |
| --- | --- | --- |
| Site login (admin email/password, bcrypt, session cookie) | ✅ | `/login` + `POST /api/auth/login` |
| Admin portal login (separate credential set) | ✅ | `/login?admin=1` |
| Role-based access control (viewer / manager / super_admin) | ✅ | `app/auth/roles.py` |
| Brute-force throttle (5 failed attempts → lockout) | ✅ | `app/auth/service.py` |
| Password visibility toggle on login | ✅ | `login.html` eye button |
| Remember Me (persists email in localStorage) | ✅ | `login.html` |
| Full admin control center (30+ views) | ✅ | `/admin` → `admin.html` + `admin.js` |
| Audit logging of admin actions | ✅ | `app/admin/audit.py` |
| Maintenance mode | ✅ | Toggle in admin → `maintenance.html` |
| Feature flags (URL search, exports, per-platform) | ✅ | Admin Features page |
| Global settings (branding, appearance, localization) | ✅ | Admin Global Settings page |
| Environment variable management (3-layer model) | ✅ | Admin Environment page |
| User management (CRUD with role assignment) | ✅ | Admin Users page |
| Session revocation | ✅ | Admin Security page |

### Admin Control Center Views

| Feature | Status | Location |
| --- | --- | --- |
| Dashboard with configurable KPI widgets, charts, alerts | ✅ | Admin Dashboard |
| Jobs management (filter, cancel, retry, delete) | ✅ | Admin Jobs |
| Failed jobs with retry/bulk retry | ✅ | Admin Failed Jobs |
| Leads management (filter, bulk actions, detail modal) | ✅ | Admin Leads |
| Analytics (date ranges, charts, platform performance) | ✅ | Admin Analytics |
| Pages & Posts tables | ✅ | Admin Pages / Posts |
| Platform management (enable/disable, actor IDs) | ✅ | Admin Platforms |
| Apify integration (token, test, usage) | ✅ | Admin Apify |
| Actor management (grid, test, links) | ✅ | Admin Actors |
| Usage & cost tracking | ✅ | Admin Usage & Cost |
| AI / Gemini settings + live test | ✅ | Admin AI |
| Lead scoring tuning (weights, signals, thresholds) | ✅ | Admin Lead Scoring |
| Comment intelligence (detection toggles, stats, feed) | ✅ | Admin Comment Intelligence |
| Keyword rules (CRUD, activate, reapply, filtered comments) | ✅ | Admin Keyword Rules |
| Scrape limits + cost protection | ✅ | Admin Limits |
| Database stats | ✅ | Admin Database |
| Log viewer (filter, autoscroll) | ✅ | Admin Logs |
| Health checks (all subsystems) | ✅ | Admin Health |
| CSV exports (jobs, pages, posts, leads, follow-ups, logs) | ✅ | Admin Exports |
| Security settings (session timeout, login protection) | ✅ | Admin Security |
| Audit log (immutable admin action feed) | ✅ | Admin Audit Log |
| Global settings editor (tabs, version history, import/export) | ✅ | Admin Global Settings |
| Plans catalog management | ✅ | Admin Plans |
| Subscriptions management | ✅ | Admin Subscriptions |
| Invoices management | ✅ | Admin Invoices |
| CMS pages (CRUD, draft/publish/restore/version-history) | ✅ | Admin CMS Pages |
| CMS FAQ (CRUD, reorder) | ✅ | Admin CMS FAQ |
| CMS testimonials (CRUD) | ✅ | Admin CMS Testimonials |
| CMS navigation (header/footer) | ✅ | Admin CMS Navigation |
| CMS website settings (branding, SEO, social links) | ✅ | Admin CMS Settings |
| CMS media (upload, list, delete) | ✅ | Admin CMS Media |
| CMS contact submissions (list, mark read) | ✅ | Admin CMS Contact |
| AI prompts (CRUD, version control, rollback) | ✅ | Admin AI Prompts |
| AI models (config, pricing, defaults) | ✅ | Admin AI Models |

### SaaS & Multi-Tenancy

| Feature | Status | Location |
| --- | --- | --- |
| Multi-tenant organizations with Default Org auto-provisioning | ✅ | `app/db/migration.py` + `app/db/saas_models.py` |
| Organization membership with roles (owner/admin/manager/member/viewer) | ✅ | `app/api/routes/organizations.py` |
| Team invitation system (token-hashed, expiring, accept/revoke) | ✅ | `app/billing/invitations.py` |
| Subscription plans (Free/Starter/Pro/Business/Enterprise) with feature flags | ✅ | `app/billing/plans.py` |
| Trial provisioning, upgrade/downgrade, cancellation, reactivation | ✅ | `app/billing/subscriptions.py` |
| Quota enforcement with atomic counters (searches, AI, team, exports) | ✅ | `app/billing/entitlements.py` + `app/billing/usage.py` |
| Usage tracking with cost calculation per AI model | ✅ | `app/pipeline/ai_usage_service.py` |
| Customer billing modal (plans, quotas, invoices, subscription) | ✅ | Billing modal in `index.html` |
| Workspace settings modal (name, timezone, currency, branding) | ✅ | Workspace modal in `index.html` |
| Team management modal (invite, roles, remove, pending) | ✅ | Team modal in `index.html` |

### Public Website & CMS

| Feature | Status | Location |
| --- | --- | --- |
| Public marketing website (hero, features, how-it-works, pricing, FAQ, CTA) | ✅ | `app/static/website.html` |
| Self-service signup page | ✅ | `app/static/signup.html` |
| Contact page with form (rate-limited, validated) | ✅ | `app/static/contact.html` |
| CMS pages (CRUD, draft/publish/restore/version-history) | ✅ | `app/api/routes/admin_cms.py` |
| FAQ management (CRUD, reorder) | ✅ | CMS Admin |
| Testimonials management | ✅ | CMS Admin |
| Website settings (branding, SEO, social links, footer) | ✅ | CMS Admin |
| Media upload/management | ✅ | CMS Admin |
| Contact submission management | ✅ | CMS Admin |
| Dynamic pricing cards (from DB plans) | ✅ | `website.html` fetches `/api/public/pricing` |
| Dynamic FAQ accordion (from DB) | ✅ | `website.html` fetches `/api/public/faq` |
| Runtime branding (colors, name, favicon via `config.js`) | ✅ | `app/static/config.js` + `/api/public/config` |

### Admin AI & Prompt Management

| Feature | Status | Location |
| --- | --- | --- |
| AI model registry (Gemini 2.5 Flash/Pro, configurable) | ✅ | `app/pipeline/ai_models_service.py` |
| Version-controlled prompt management (edit, rollback, variables) | ✅ | `app/pipeline/ai_prompt_service.py` |
| AI usage & cost tracking per request | ✅ | `app/pipeline/ai_usage_service.py` |
| Admin AI routes (prompts CRUD, model config, live test) | ✅ | `app/api/routes/admin_ai.py` |
| Admin Apify routes (actor management, job retry/bulk) | ✅ | `app/api/routes/admin_apify.py` |
| Admin Leads routes (search, dossier, notes, bulk actions) | ✅ | `app/api/routes/admin_leads.py` |
| Admin Analytics routes (SaaS overview, funnel, platform comparison) | ✅ | `app/api/routes/admin_analytics.py` |

### Not Implemented (Planned)

| Feature | Status |
| --- | --- |
| API rate limiting (provider 429s are handled locally) | ⬜ |
| Webhooks / background job queue (Celery/RQ) | ⬜ |
| Export to Excel / Sheets / CRM | ⬜ |
| More social platforms (Twitter/X, Pinterest) | ⬜ |
| Frontend tests / CI pipeline | ⬜ |

---

## 3. Technology Stack

| Layer | Technology | Purpose |
| --- | --- | --- |
| Frontend (User) | Vanilla HTML5 / CSS3 / JavaScript (no framework, no build step) | Dashboard: URL search, pages, posts, comments, lead cards, CSV export, workspace/team/billing modals |
| Frontend (Admin) | Vanilla HTML5 / CSS3 / JavaScript (no framework, no build step) | Admin control center: 30+ views, charts, settings, user management |
| Frontend (Public) | Vanilla HTML5 / CSS3 / JavaScript (design system: tokens.css, components.css) | Marketing website, signup, contact, FAQ, pricing |
| Backend | FastAPI + Uvicorn (Python 3.11+) | REST API, static hosting, background task orchestration |
| AI Model | Google Gemini `gemini-2.5-flash` (configurable) | Comment analysis |
| AI Access | REST `generativelanguage.googleapis.com/v1beta` via `httpx` | No SDK dependency |
| Scraping | Apify (`apify-client`) — 3 Facebook actors + configurable IG/YT/LI actors | All social data collection |
| Database | MongoDB 7 (Motor async + PyMongo sync) | Persistence of pages/posts/comments/analysis/history/settings/audit/organizations/subscriptions |
| Auth | bcrypt (cost 12) + session cookies + role-based access control | All routes protected |
| Multi-tenancy | Organization-scoped data, membership roles, invitation tokens | SaaS workspace isolation |
| CMS | MongoDB-backed content (pages, FAQ, testimonials, navigation, media) | Dynamic public website content |
| Configuration | `pydantic-settings` reading `.env` + MongoDB `system_settings` collection | All settings |
| Deployment | Docker + docker-compose (API + Mongo) | Containerized run |
| Code quality | Ruff (lint) + mypy (type checks) | Dev workflow |
| Testing | `pytest`-style acceptance tests + integration checks | `tests/` |

Key libraries (`requirements.txt`): `fastapi`, `uvicorn[standard]`, `pydantic`, `pydantic-settings`, `motor`, `pymongo`, `httpx`, `apify-client`, `python-multipart`, `bcrypt`.

---

## 4. System Architecture

```mermaid
flowchart TD
    U[User] --> FE[Dashboard<br/>app/static: index.html + app.js]
    U --> ADM[Admin Control Center<br/>app/static: admin.html + admin.js]
    U --> WEB[Public Website<br/>app/static: website.html / signup / contact]

    FE -->|"POST /api/url/search<br/>(social URL)"| API[FastAPI app.main:app]
    FE -->|"poll GET /api/search/{run_id}"| API
    FE -->|"GET pages, posts, comments, export"| API
    FE -->|"Workspace, Team, Billing"| API

    ADM -->|"GET/POST /api/admin/*"| API
    ADM -->|"POST /api/auth/login"| AUTH[Auth Router<br/>app/api/routes/auth.py]
    ADM -->|"CMS, AI, Apify, Leads, Analytics"| API

    WEB -->|"GET /api/public/*"| API
    WEB -->|"POST /api/public/contact"| API

    API --> DB[(MongoDB 'LeadAI'<br/>20+ collections)]
    API -->|in-process background thread| URS[URL Search Pipeline<br/>app/social/url_search.py]

    URS --> UDET[URL Detector<br/>app/social/url_detector.py]
    URS --> SCRAP[Platform Scrapers<br/>app/social/scrapers.py]
    SCRAP --> APIFY[ApifyConnector<br/>app/connectors/apify_connector.py]

    APIFY --> A2[facebook-pages-scraper]
    APIFY --> A3[facebook-posts-scraper]
    APIFY --> A4[facebook-comments-scraper]
    APIFY --> A5[Configurable actors<br/>instagram / youtube / linkedin]

    URS --> QT[Qualification + Scoring<br/>app/agent/search.py]
    QT --> CAAI[Comment AI Pipeline<br/>app/pipeline/comment_ai.py]
    CAAI -->|Stage 1: rule filter| CF[Comment Filter<br/>app/pipeline/comment_filter.py]
    CAAI -->|Stage 2: Gemini| GEMINI[Google Gemini API]
    CAAI -->|upsert analysis| DB

    URS -->|normalize + dedupe| DB

    API --> ADMIN[Admin Settings<br/>app/admin/settings.py]
    API --> AUDIT[Audit Logging<br/>app/admin/audit.py]
    API --> ENVVARS[Env Var Management<br/>app/admin/envvars.py]
    API --> LIFECYCLE[Lead Lifecycle<br/>app/pipeline/lead_lifecycle.py]

    API --> SAAS[SaaS Layer<br/>billing/ + organizations/ + usage/]
    SAAS --> DB
    API --> CMS[CMS Service<br/>app/cms/service.py]
    CMS --> DB
```

### Components

- **`app/main.py`** — FastAPI application (v2.5.0). Sets up configurable console + file logging, starts the Mongo index check at startup, reconciles stale running jobs, seeds default SaaS plans and CMS defaults, mounts the static frontend, and exposes `/health`, `/`, `/dashboard`, `/login`, `/admin`, `/website`, `/signup`, `/contact`, `/features`, `/pricing`, `/about`, `/faq`, `/privacy`, `/terms`. Includes security headers middleware, CSRF protection, auth gate (session validation per scope), and maintenance mode gate.

- **`app/log_parser.py`** — Structured log parser (364 lines). Normalizes raw log lines into structured dicts with level, module, source classification, event extraction, error type detection, structured field extraction, multiline stack trace grouping, and automatic secret redaction.

- **`app/api/routes/search.py`** — The entire product REST surface (15 `/api` routes + CSV exports). All long-running operations run as **in-process background tasks**; GET endpoints expose live status fields for polling. Includes per-user API rate limiting (30 req/min).

- **`app/api/routes/admin.py`** — The admin control center API (50+ endpoints). Every endpoint is role-protected.

- **`app/api/routes/admin_ai.py`** — Admin AI routes: prompt CRUD, model configuration, live AI test panel.

- **`app/api/routes/admin_apify.py`** — Admin Apify routes: actor management, job retry (max 3), bulk operations.

- **`app/api/routes/admin_leads.py`** — Admin Leads routes: search, dossier, notes, follow-ups, bulk actions, data quality dashboard.

- **`app/api/routes/admin_analytics.py`** — Admin Analytics routes: SaaS overview, lead funnel, platform comparison, category breakdown, plan analytics.

- **`app/api/routes/admin_cms.py`** — Admin CMS routes: pages (CRUD, draft/publish/restore/version-history), FAQ, testimonials, navigation, website settings, contact submissions, media upload.

- **`app/api/routes/auth.py`** — Login/logout/me endpoints for both site and admin scopes, plus signup endpoint.

- **`app/api/routes/comment_filters.py`** — Comment filter rules CRUD, activation, reapplication, and filtered comments feed.

- **`app/api/routes/settings.py`** — Global settings admin API + public `/api/public/config` for branding.

- **`app/api/routes/organizations.py`** — Organization & team management: workspace settings, member CRUD, invitation flow.

- **`app/api/routes/billing.py`** — SaaS billing: plans catalog, subscription management, usage tracking, checkout, invoices, webhooks.

- **`app/api/routes/public_website.py`** — Public CMS endpoints: theme, pricing, FAQ, contact form (rate-limited).

- **`app/social/url_search.py`** — The URL-search orchestrator. Runs in a daemon thread, writes live status to `search_history`, and drives page → posts → comments → AI analysis with cancellation checkpoints.

- **`app/social/url_detector.py`** — Platform detection + canonicalization for FB/IG/YT/LinkedIn, with classified `UrlError` (invalid / unsupported).

- **`app/social/scrapers.py`** — One `SocialMediaScraper` subclass per platform over `ApifyConnector`; normalizes platform-specific actor items into the shared document shapes. Also the router used by the drill-down collectors (`get_scraper(platform)`).

- **`app/agent/search.py`** — The shared lead-collection engine: `collect_page_posts`, `collect_post_comments`, normalizers (`map_page_item`, `map_post_item`, `map_comment_item`), relevance/qualification logic, page scoring (`_compute_page_stats`, `_lead_score`, `_activity_status`), and cancellation helpers.

- **`app/connectors/apify_connector.py`** — Thin wrapper over `apify-client` with classified error objects (`ScrapeError`), run timeouts, cancellation polling, exponential backoff retry (max 3), and request/response logging. Tracks active Apify runs for graceful shutdown abort.

- **`app/pipeline/comment_ai.py`** — The lead-analysis brain: Stage 1 rule filtering + regex extraction, Stage 2 Gemini structured JSON extraction, `comment_lead_score` (0–100), `extract_display_signals` / `is_lead`, and persistence into `ai_comments`.

- **`app/pipeline/comment_filter.py`** — Keyword/category matching pipeline with 4 modes (NO_FILTER, KEYWORD, CATEGORY, ADVANCED). 10+ predefined business categories (real estate, automotive, etc.) with curated keyword lists. Case-insensitive, Unicode-normalized, phrase/word-boundary matching.

- **`app/pipeline/lead_lifecycle.py`** — Lead state machine with 8 statuses, valid transitions, notes, follow-ups, and status history. Terminal states: converted, lost, disqualified, archived.

- **`app/auth/service.py`** — Session cookie management, bcrypt verification, SHA-256 migration for legacy hashes, brute-force throttle.

- **`app/auth/crypto.py`** — Password hashing (bcrypt cost 12) + SHA-256 legacy support.

- **`app/auth/roles.py`** — Role-based authorization (viewer < manager < super_admin). Checks DB `admin_users` collection on each request.

- **`app/auth/tenant.py`** — Multi-tenant context resolution: extracts organization_id from session, provides TenantContext for org-scoped queries.

- **`app/admin/settings.py`** — `system_settings` collection with TTL cache (5 min). `get_setting(key)`, `set_setting(key, value)`, maintenance mode, Apify token management.

- **`app/admin/audit.py`** — `audit_logs` collection. `audit(action, category, user, details, success)`. Secret redaction for sensitive keys.

- **`app/admin/envvars.py`** — `env_overrides` collection. Three-layer model: DB override → .env/Settings → documented default. `ENVVAR_REGISTRY` with kind/secret/restart flags.

- **`app/billing/plans.py`** — Plan tiers (Free/Starter/Professional/Business/Enterprise) with feature flags, limit definitions, default plan seeding, and caching.

- **`app/billing/subscriptions.py`** — Trial provisioning, plan upgrades/downgrades, cancellation scheduling, reactivation, period calculations.

- **`app/billing/usage.py`** — Atomic quota counters in `organization_usage`, immutable audit event logs in `usage_records`, calendar period tracking.

- **`app/billing/entitlements.py`** — Feature entitlement & quota enforcement. Raises `QUOTA_EXCEEDED` (HTTP 402) and `FEATURE_NOT_AVAILABLE` (HTTP 403) with upgrade guidance.

- **`app/billing/invoices.py`** — Invoice history per organization.

- **`app/billing/invitations.py`** — SHA-256 token-hashed invitations with configurable expiration, token validation, membership activation.

- **`app/billing/provider.py`** — Billing provider abstraction (Stripe-ready).

- **`app/cms/service.py`** — CMS CRUD, sanitization, draft/publish/restore/version-history, cache invalidation, audit logging.

- **`app/cms/models.py`** — CMS collection definitions: pages, FAQ, testimonials, navigation, media, settings, contact.

- **`app/db/saas_models.py`** — SaaS data models: organizations, memberships, invitations, subscriptions, usage, invoices, users with status/role enums.

- **`app/db/migration.py`** — Idempotent multi-tenant migration: ensures Default Organization, owner membership, backfills organization_id on existing documents.

- **`app/pipeline/ai_models_service.py`** — AI model registry: Gemini models with pricing, token limits, temperature, context limits.

- **`app/pipeline/ai_prompt_service.py`** — Version-controlled prompt management with rollback, mustache-style variable substitution, reproducibility tracking.

- **`app/pipeline/ai_usage_service.py`** — AI usage & cost tracking per request, aggregation metrics for billing and analytics.

- **`app/settings/registry.py`** — Settings schema registry: 30 admin view definitions, 8 dashboard widget definitions, font/theme options, validation rules (HEX_COLOR_RE, EMAIL_RE, URL_RE, JS_DANGER_RE).

- **`app/db/mongo.py`** — Motor async + PyMongo sync clients, DNS override for `mongodb+srv://`, `_create_index_safe()` wrapper for idempotent index creation (handles IndexKeySpecsConflict), `ensure_indexes()` creates all indexes at startup. Reconciles stale running jobs on startup.

- **`app/db/models.py`** — Pydantic models for all collections: `SearchHistory`, `FacebookPage`, `FacebookPost`, `FacebookComment`, `AICommentAnalysis`, plus admin models.

- **`app/static/index.html`** — Dashboard HTML shell with SaaS modals (workspace, team, billing, quota exceeded).

- **`app/static/app.js`** — Main dashboard SPA (1385 lines): URL search → poll run → pages → posts → comments → lead modal; workflow memory in `localStorage`; workspace/team/billing modal handlers.

- **`app/static/styles.css`** — Main UI design system (2432 lines).

- **`app/static/admin.html`** — Admin Control Center HTML shell (30+ views).

- **`app/static/admin.js`** — Admin SPA frontend (4725+ lines): 30+ views, sidebar, global search, notifications, health pill, structured log viewer with details modal.

- **`app/static/admin.css`** — Admin design system (2097 lines).

- **`app/static/login.html`** — Login page with two scopes (site / admin), password visibility toggle, Remember Me, Forgot Password info.

- **`app/static/signup.html`** — Self-service signup with first/last name, email, organization, password strength meter, terms consent.

- **`app/static/contact.html`** — Contact form with name, email, company, message, character counter, rate limiting, success state.

- **`app/static/website.html`** — Public marketing website: hero, platforms strip, features grid, how-it-works steps, dynamic pricing (from DB), dynamic FAQ (from DB), CTA banner, footer with dynamic links/copyright.

- **`app/static/url_report.html`** — Standalone report page for URL-search runs.

- **`app/static/config.js`** — Runtime branding/global settings provider (325 lines). Fetches `/api/public/config` and applies CSS vars, title, favicon, fonts dynamically.

- **`app/static/maintenance.html`** — Shown when maintenance mode is active.

- **`app/static/design/tokens.css`** — Design tokens: CSS custom properties for colors, spacing, typography, borders, shadows, transitions.

- **`app/static/design/components.css`** — Reusable component styles: buttons, cards, forms, badges, tables, alerts, modals.

- **`app/static/design/theme.js`** — Dynamic theme application from `/api/public/theme` (brand colors, name, logo, favicon).

---

## 5. Folder Structure

```text
lead_apify/
├── app/
│   ├── main.py                    # FastAPI app, security headers, auth/maintenance gates, /health, page routes
│   ├── config.py                  # pydantic-settings: all .env-driven settings
│   ├── log_parser.py              # Structured log parser: levels, modules, sources, secrets redaction, multiline grouping
│   ├── api/
│   │   ├── models.py              # Pydantic request/response models
│   │   └── routes/
│   │       ├── auth.py            # POST /api/auth/login, /logout, GET /me
│   │       ├── search.py          # All product REST endpoints + background task management
│   │       ├── admin.py           # Admin Control Center API (50+ endpoints)
│   │       ├── admin_ai.py        # Admin AI routes: prompts CRUD, models, live test
│   │       ├── admin_apify.py     # Admin Apify routes: actor management, job retry/bulk
│   │       ├── admin_leads.py     # Admin Leads routes: search, dossier, notes, follow-ups, bulk actions
│   │       ├── admin_analytics.py # Admin Analytics routes: SaaS overview, funnel, platform comparison
│   │       ├── admin_cms.py       # Admin CMS routes: pages, FAQ, testimonials, navigation, media
│   │       ├── comment_filters.py # Comment filter rules CRUD + user catalog
│   │       ├── settings.py        # Global settings admin API + public /api/public/config
│   │       ├── organizations.py   # Organization & team management (workspace, members, invitations)
│   │       ├── billing.py         # SaaS billing: plans, subscription, usage, checkout, invoices, webhooks
│   │       └── public_website.py  # Public CMS endpoints: theme, pricing, FAQ, contact form
│   ├── auth/
│   │   ├── service.py             # Session cookie management, bcrypt verification, brute-force throttle
│   │   ├── crypto.py              # Password hashing (bcrypt cost 12) + SHA-256 migration
│   │   ├── roles.py               # Role-based authorization (viewer / manager / super_admin)
│   │   └── tenant.py              # Multi-tenant context resolution (organization_id from session)
│   ├── agent/
│   │   └── search.py              # Lead engine: collect_page_posts, collect_post_comments,
│   │                              #   mappers, qualification + scoring, cancel helpers
│   ├── connectors/
│   │   └── apify_connector.py     # Thin wrapper over apify-client with classified errors, run timeouts, retry
│   ├── db/
│   │   ├── mongo.py               # Async/sync clients, DNS override, ensure_indexes(), stale job reconciliation
│   │   ├── models.py              # Pydantic models: pages, posts, comments, ai_comments, history
│   │   ├── saas_models.py         # SaaS models: organizations, memberships, invitations, subscriptions, usage, invoices, users
│   │   └── migration.py           # Idempotent multi-tenant migration & Default Organization backfill
│   ├── pipeline/
│   │   ├── comment_ai.py          # Rule filter → Gemini extraction → score → ai_comments
│   │   ├── comment_filter.py      # Keyword/category matching pipeline (4 modes, 10+ categories)
│   │   ├── lead_lifecycle.py      # Lead state machine with valid transitions, notes, follow-ups
│   │   ├── ai_models_service.py   # AI model registry: Gemini models, pricing, token limits
│   │   ├── ai_prompt_service.py   # Version-controlled prompt management with rollback, variable substitution
│   │   └── ai_usage_service.py    # AI usage & cost tracking per request, aggregation metrics
│   ├── social/
│   │   ├── url_detector.py        # Detect + canonicalize FB/IG/YT/LinkedIn URLs
│   │   ├── url_search.py          # URL-search background pipeline (page→posts→comments)
│   │   ├── scrapers.py            # Platform scraper classes over ApifyConnector
│   │   └── url_detector.py        # Platform detection + canonicalization
│   ├── admin/
│   │   ├── settings.py            # Admin settings (system_settings collection, TTL cache)
│   │   ├── audit.py               # Audit logging (audit_logs collection, secret redaction)
│   │   └── envvars.py             # Env var management (env_overrides collection, ENVVAR_REGISTRY)
│   ├── billing/
│   │   ├── plans.py               # Plan tiers (Free/Starter/Pro/Business/Enterprise), feature flags, limits
│   │   ├── subscriptions.py       # Trial provisioning, upgrades, cancellations, reactivations
│   │   ├── usage.py               # Atomic quota counters, usage records, calendar period tracking
│   │   ├── entitlements.py        # Feature entitlement & quota enforcement (QUOTA_EXCEEDED, FEATURE_NOT_AVAILABLE)
│   │   ├── invoices.py            # Invoice history per organization
│   │   ├── invitations.py         # SHA-256 token-hashed team invitations with expiration
│   │   └── provider.py            # Billing provider abstraction (Stripe-ready)
│   ├── cms/
│   │   ├── models.py              # CMS collections: pages, FAQ, testimonials, navigation, media, settings, contact
│   │   └── service.py             # CMS CRUD, sanitization, draft/publish/restore/version-history, cache
│   ├── settings/
│   │   └── registry.py            # Settings schema registry (30 admin view definitions, validation, font/theme options)
│   └── static/
│       ├── index.html             # Dashboard: URL search / pages / posts / comments + workspace/team/billing modals
│       ├── app.js                 # Polling, workflow memory, rendering, CSV exports (1385 lines)
│       ├── styles.css             # Main UI design system (2432 lines)
│       ├── admin.html             # Admin Control Center shell (30+ views, sidebar, global search)
│       ├── admin.js               # Admin SPA frontend (4725+ lines)
│       ├── admin.css              # Admin design system (2097 lines)
│       ├── login.html             # Login page (site + admin scope, password toggle, Remember Me)
│       ├── signup.html            # Self-service account creation (name, email, org, password, terms)
│       ├── contact.html           # Contact form (name, email, company, message, rate-limited)
│       ├── url_report.html        # Standalone report for URL-search runs
│       ├── website.html           # Public marketing website (hero, features, how-it-works, pricing, FAQ, CTA, footer)
│       ├── config.js              # Runtime branding/global settings provider (fetches /api/public/config)
│       ├── maintenance.html       # Maintenance mode page
│       ├── design/
│       │   ├── tokens.css         # Design tokens (colors, spacing, typography, borders, shadows)
│       │   ├── components.css     # Reusable component styles (buttons, cards, forms, badges, tables)
│       │   └── theme.js           # Dynamic theme application from API settings
│       └── media/                 # Uploaded media assets (CMS)
├── tests/
│   ├── test_auth.py               # Admin login, hashing, throttle, sessions
│   ├── test_admin_panel.py        # Admin panel tests
│   ├── test_url_search.py         # URL detection/canonicalization + error classification
│   ├── test_qualification.py      # Lead-qualification acceptance tests
│   ├── test_security.py           # Security tests
│   ├── test_comment_filter.py     # Comment filter pipeline tests
│   ├── test_ai_intelligence.py    # AI analysis tests
│   ├── test_scalability_production.py  # Scalability tests
│   ├── test_admin_panel_hardening.py   # Admin hardening tests
│   ├── test_admin_data.py         # Admin data tests
│   ├── test_settings.py           # Settings tests
│   ├── test_envvars.py            # Environment variable tests
│   ├── test_analytics_export.py   # Analytics export tests
│   ├── test_lead_lifecycle.py     # Lead lifecycle tests
│   ├── test_post_normalization.py # Post normalization tests
│   ├── test_http_endpoints.py     # HTTP/API endpoint tests (76 tests)
│   ├── test_log_parser.py         # Log parser tests (117 tests)
│   └── integration_check.py       # End-to-end pipeline check against scratch Mongo DB
├── scratch/                       # Dev-only diagnostic scripts (gitignored)
├── logs/                          # app.log (rotating, 10MB × 5, gitignored)
├── .env.example                   # Template for environment variables
├── .gitignore
├── docker-compose.yml             # api + mongo:7
├── Dockerfile                     # python:3.11-slim image
├── requirements.txt
└── README.md
```

---

## 6. End-to-End Workflow

### Step 1 — User Enters a Social URL

The dashboard's single search form accepts a **Facebook page, Instagram profile, YouTube channel, or LinkedIn company** URL plus a max-posts count (default 20), max-comments-per-post (default 30), and optional comment filter mode (all / preset / custom with keywords and categories).

### Step 2 — Request Reaches the Backend

`POST /api/url/search?url=&max_posts=&max_comments_per_post=&filter_mode=&preset=&include_keywords=&exclude_keywords=&categories=&match_mode=` — `url` 4–300 chars; detected and canonicalized by `detect_social_url()`; invalid/unsupported URLs return 422 with an `errorType` (`invalid` | `unsupported`). The endpoint immediately persists a `search_history` document (`status: running`, `phase: queued`), generates a `run_id` (prefixed `URL`), registers the background pipeline, and returns immediately. No request ever blocks the API.

### Step 3 — URL Validation and Platform Detection

`detect_social_url()` (`app/social/url_detector.py`) maps the host to a platform, verifies the URL shape against per-platform rules, and canonicalizes it (tracking params and trailing slashes dropped, path never rewritten, missing scheme auto-prefixed). Unsupported domains raise `UrlError(kind="unsupported")`; malformed profile URLs raise `kind="invalid"`.

### Step 4 — Page Details

The platform scraper fetches the profile via its Apify actor (`apify/facebook-pages-scraper`, `apify/instagram-scraper`, `streamers/youtube-scraper`, `harvestapi/linkedin-company` — all overridable in `.env`), normalized into the `facebook_pages` shape with a `platform` field. **Graceful fallback**: when the details actor fails, a minimal URL-derived page doc is still created (`_url_derived_page`) so posts/comments remain usable.

### Step 5 — Posts

Fetched (default 20, capped 1–100) and stored with per-page dedupe; live counts update on the page doc. Every post is flagged `is_relevant` (caption mentions the target context) and `is_qualifying` (relevant **and** platform-reported total comments ≥ `MIN_COMMENTS`).

### Step 6 — Comments (Facebook, Instagram & LinkedIn)

Only posts with `total_comment_count >= MIN_COMMENTS` are scraped — low-engagement posts are skipped (`comments_status: "skipped"`), saving Apify cost. The number of comments scraped per post is controlled by the **"Comments / Post"** setting in the UI (default 20, 1–500). Collected comments are deduped, flagged `has_contact` (10-digit phone or email), and each post's comments run through the keyword filter pipeline and then `analyze_comments_for_post` for the full AI treatment. YouTube posts are never comment-scraped.

### Step 7 — Keyword Filtering

Before AI analysis, each comment passes through the keyword filter pipeline (`app/pipeline/comment_filter.py`). The active rule determines if a comment is MATCHED (proceeds to AI), NOT_MATCHED (stored but skips AI), or NO_FILTER (no active rule, proceeds to AI). Modes: NO_FILTER (all pass), KEYWORD (any/all match), CATEGORY (preset keywords), ADVANCED (keyword groups). This saves AI costs by pre-filtering irrelevant comments.

### Step 8 — AI Analysis

Each MATCHED or NO_FILTER comment goes through the two-stage pipeline (`app/pipeline/comment_ai.py`): Stage 1 rule filtering + regex extraction (offline, free), Stage 2 Gemini structured JSON extraction (contact info, person context, buyer signals). Every lead gets a 0–100 `lead_score`, priority, quality, and `is_lead` flag.

### Step 9 — Page Scoring

`_compute_page_stats` aggregates the stored posts: qualifying counts, total comments on qualifying posts, latest post date → `activity_status` (active ≤ 90 days / recent ≤ 365 / inactive / unknown) and a deterministic `lead_score`.

### Step 10 — Finalize

A run that got neither details nor posts is marked `error` with the real reason; otherwise `completed`. Results are browsable through the normal dashboard (`GET /api/pages?run_id=`) and the dedicated report page (`GET /api/url/search/{run_id}/report` → `/static/url_report.html`), which opens automatically when the run finishes.

---

## 7. UI Pages & Panels

### 7A. Login Page (`/login` — `login.html`)

**Two-scope login** (site vs admin):

| Element | Description |
| --- | --- |
| **Logo badge** | Amber gradient square with ✦ icon, brand name "LeadAI", tagline "AI Lead Intelligence" |
| **Heading** | "Welcome Back" (site) / "Admin Portal" (admin) — driven by `AppConfig` |
| **Email field** | Input with email icon, live validation (green checkmark on valid format), placeholder "Admin@gmail.com" (site) / "Admin123@gmail.com" (admin) |
| **Password field** | Input with lock icon, **eye button** to toggle visibility (show/hide password) |
| **Remember Me** | Checkbox (amber gradient when checked), persists email in localStorage |
| **Forgot Password** | Link → shows info message "Contact your administrator" (no self-reset flow) |
| **Error banner** | Red banner with shake animation, shows validation/login errors |
| **Sign In button** | Amber gradient, arrow icon, loading spinner state, disabled during submission |
| **Footer** | "Protected by secure authentication · LeadAI © 2026" |
| **Ambient background** | 3 floating orbs (amber, violet, teal) + grid overlay, same as main site |

**Behavior**: POSTs to `/api/auth/login` with `{email, password, scope: "site"|"admin"}`. On success, redirects to `/` (site) or `/admin` (admin). 401 on failure shows error. Brute-force throttle with lockout indicator.

### 7B. Main Dashboard (`/` — `index.html` + `app.js`)

Single-page application with **4 view screens** navigated via breadcrumb, plus **3 SaaS modals**:

#### View 1: Search Screen

| Element | Description |
| --- | --- |
| **URL Search Input** | Text input for social media URL, hint text "Waiting for URL…" → "Ready to analyze — platform auto-detected" |
| **Max Posts input** | Number input (default 20, range 1–100) |
| **Comments Per Post input** | Number input (default 20, range 1–500), synced with comments view |
| **Filter Mode** | Radio buttons: All (no filter), Preset (select from categories/rules), Custom (manual keywords) |
| **Preset selector** | Dropdown with category presets and active admin rules |
| **Custom keywords** | Include keywords text input, Exclude keywords text input, Category checkboxes, Match mode dropdown (any/all) |
| **Analyze Profile button** | Amber gradient, triggers URL search, shows loading state "Analyzing Profile…" |
| **Cancel button** | Appears during search, cancels in-flight Apify run |
| **Platform chip** | Shows detected platform icon + name, canonical URL, running status |
| **Progress bar** | Determinate progress (5% → 20% → 55% → 75% → 100%) with phase label |
| **Analysis steps checklist** | 5 steps: URL validated, Platform detected, Page details, Posts, Comments — each gets done/active/error state |
| **Status badge** | Top-right badge: Idle / URL search / Viewing pages / Collecting posts / etc. |
| **Recent Searches section** | List of past searches with: icon, URL, platform label, page count, status badge (✓ Completed / ◌ Processing / ! Failed / – Cancelled), relative time, delete button (✕), "Open →" link |
| **Session stats strip** | Shows total searches, completed count, total pages |
| **Filter chips** | All / Facebook / Instagram / YouTube / LinkedIn |
| **View All button** | Toggles between showing 8 or 100 recent searches |

#### View 2: Pages Screen

| Element | Description |
| --- | --- |
| **Summary line** | "Found X page(s)/channel(s) · Y with qualifying high-intent posts" |
| **Category filter** | Dropdown populated from page categories |
| **Contact only checkbox** | Filters to pages with phone/email |
| **Export Pages CSV button** | Downloads pages as CSV |
| **Page cards** | Each card shows: |
| — Avatar | Profile picture or fallback initial |
| — Page name | With platform badge (Facebook/Instagram/YouTube/LinkedIn) and verified badge |
| — Platform link | "Open on Facebook ↗" (external link) |
| — About snippet | First 160 chars of about text |
| — Stats row | Followers/Subs, Likes, Posts Found, Qualifying Posts, Total Comments |
| — Contact chips | 📞 Phone (tel: link), ✉️ Email (mailto: link), 💬 WhatsApp (wa.me link), 🌐 Website (external link), 📍 Address, 🏷️ Category |
| — Action button | "🔍 Analyze Posts" / "📝 View X Posts (Y qualifying)" / "↻ Re-analyze Posts" / spinner "Analyzing posts…" |
| — Error row | If post collection failed |

#### View 3: Posts Screen

| Element | Description |
| --- | --- |
| **Page name header** | Shows which page's posts are displayed |
| **Summary line** | Total Posts, Relevant, Qualifying, Total Comments on Qualifying, Latest Post Date, qualifying threshold note |
| **Export Posts CSV button** | Downloads posts as CSV |
| **Post tiles** | Each tile shows: |
| — Thumbnail | Post image or video icon or text fallback |
| — Caption | Full post text (never truncated) |
| — Date | Published date |
| — Badges | ✓ Relevant / ✗ Low relevance, ★ Qualifying (≥ N comments) / <N comments |
| — External link | "View original post ↗" |
| — Stats row | Likes, Total Comments, Scraped & Analyzed, Shares |
| — Action button | "💬 Collect Comments (N available)" / "💬 View N Comments & Leads" / spinner "Collecting…" |
| — Error row | If comment collection failed |

#### View 4: Comments / Leads Screen

| Element | Description |
| --- | --- |
| **Post name header** | Shows which post's comments are displayed |
| **Filter pills** | All, Leads, Contact, Hot, Pricing, Inquiry — each with count badge |
| **Search input** | Debounced text search with clear button |
| **Quality filter** | Dropdown (hot/warm/cold) |
| **Sort by** | Dropdown (score/date/etc.) |
| **Comments Per Post** | Number input synced with search view |
| **Progress bar** | Determinate/indeterminate while comments are being scraped |
| **Summary line** | Active filter description, search match note, count of displayed/total |
| **Lead cards** | Each card shows: |
| — Avatar | First letter of commenter name |
| — Commenter name | With platform badge and "📞 Contact Ready" badge (if has contact) |
| — Date + platform link | "View on Facebook ↗" |
| — Score pill | 0–100 score with tooltip |
| — Priority badge | HIGH (red) / MEDIUM (yellow) / LOW (gray) |
| — Comment text | Full comment text |
| — AI Intelligence | 🤖 AI rationale/reason |
| — Contact chips | 📞 Phone, ✉️ Email, 💬 WhatsApp, 💰 Budget, 📋 Requirement, 📍 Location, 🎯 Intent, 💭 Sentiment |
| — "🔍 View Full Dossier" button | Opens lead detail modal |
| **Export Leads CSV button** | Downloads analyzed comments as CSV |

#### Lead Detail Modal

Full dossier popup with:

| Section | Content |
| --- | --- |
| **Title** | "Lead Intelligence Dossier — [Commenter Name]" |
| **Original Comment** | Full comment text with "Open original comment on [platform] ↗" link |
| **AI Rationale** | 🤖 AI Intelligence text + analyzed_by (rules/gemini) |
| **Contact Info** | Phone (tel: link), WhatsApp (wa.me link), Email (mailto: link), Website (external link) |
| **Buyer Signals** | Budget, Requirement/Inquiry, Location/City, Buying Intent badge, Urgency |
| **Platform & Quality** | Platform icon + name, Priority Level (🔴 High / 🟡 Medium / ⚪ Low), Lead Quality (🔥 Hot / ⚡ Warm / ❄️ Cold), Confidence Score (0–100%), Lead Score (0–100, large amber display) |
| **Lead Status** | Current status badge (colored), dropdown for valid transitions, "Update" button |
| **Notes** | List of existing notes (author, time, text), input + "Add" button |
| **Follow-ups** | List of follow-ups (title, status badge, due date, notes), input fields (title, datetime) + "Add" button |
| **Status History** | Timeline of status changes (from → to, time, changed_by, reason) |
| **Source Context** | Page name + link, Post caption + link, Commenter profile link |

#### SaaS Modals (Header Buttons)

| Modal | Trigger | Content |
| --- | --- | --- |
| **Workspace Settings** | 🏢 Workspace pill button | Workspace name, company website, timezone, currency, custom branding (primary/accent colors, brand display name) |
| **Team Management** | 👥 Team button | Invite form (email + role), active members table, pending invitations, role change/remove actions |
| **Billing & Subscriptions** | 💳 Billing button | Active subscription banner, monthly usage quota meters (searches/AI/team), available plans grid, invoices table |
| **Quota Exceeded** | Auto-triggered on limit | Warning modal with upgrade CTA |

### 7C. URL Report Page (`/static/url_report.html`)

Standalone report page opened automatically after URL search completion. Shows the full results of a search run with page details, post summary, and comment/lead data. Accessible via `/static/url_report.html?run_id=<URL-run-id>`.

### 7D. Maintenance Page (`/static/maintenance.html`)

Shown when maintenance mode is active. Displays configurable notice message from `maintenance.message` setting.

---

## 8. Admin Control Center

Full admin SPA at `/admin` (`admin.html` + `admin.js`, 4725+ lines) with **sidebar navigation** organized into sections:

### Main Section

| View | Description |
| --- | ---|
| **Dashboard** | Hero KPI (leads, searches, running, success rate), date range selector (7d/30d/90d/custom), KPI grid with sparklines, activity chart (dual area: leads gold + searches muted), leads by platform bars, alerts, system status, keyword filter summary, recent jobs table. Configurable widget visibility/ordering via Global Settings. |
| **Jobs** | Paginated table of all search runs. Filter by status (Running/Completed/Failed/Cancelled/Queued), platform, query, date range. Actions: Cancel (running), Retry (completed/failed), Delete (super_admin). Click opens job drawer with full report. |
| **Failed Jobs** | Hero with total failed count, today/this week, most common error. Table with retry button per row and "Retry All" bulk action. |
| **Leads** | Hero with total leads, contact count, avg score, per-platform counts. Paginated table with filters (platform, quality, status, text search). Bulk actions: set status, delete. Click opens lead detail modal. |
| **Analytics** | Date range selector (Today/7d/14d/30d/90d/custom). Charts: jobs per day (vbar), leads per day (area), job statuses, leads by platform, quality distribution, score distribution. Platform performance table. Top pages by leads. Lead pipeline status. |
| **Pages** | Paginated table of all collected pages. Filters: platform, name/category/city, contact info. Click opens page detail modal. |
| **Posts** | Paginated table of all collected posts. Filters: platform, caption/page. Click opens post detail modal. |

### Platforms & Data Sources Section

| View | Description |
| --- | --- |
| **Platforms** | Cards per platform (Facebook/Instagram/YouTube/LinkedIn) with enable/disable toggle, stats, actor management (view/edit/test/save actor IDs). |
| **Platform Detail** | Detailed view of one platform: stats, actor list, recent runs. |
| **Apify** | Connection status, token management (masked), live test button, usage this month with cost-by-actor breakdown. |
| **Actors** | Grid of all Apify actor cards with key, actor ID, platform badge, override indicator, test button, link to Apify. |
| **Usage & Cost** | 30-day usage aggregation: total cost, runs with usage, cost-by-actor bars and table. |
| **Environment** | Three-layer env var management: lock/unlock with password, grouped variables with source/override/secret badges, inline edit, reset to .env, guard password change. |

### AI & Lead Engine Section

| View | Description |
| --- | --- |
| **AI / Gemini** | Status (model, API key, analyzed count), settings (enable/disable AI, rule fallback, max calls/job, temperature), live test panel with sample comment analysis. |
| **Lead Scoring** | Three groups: Weights (confidence, priority, quality, phone/email, spam penalty), Signals (phone, email, budget, urgency, location, buying intent), Thresholds (hot_min, warm_min). Derive quality toggle. All auto-saved on input. |
| **Comment Intelligence** | Pipeline stats (total, analyzed, leads, contacts, high value, avg confidence), intent distribution bars. Detection settings toggles (phone, email, budget, location, urgency, buying/selling intent, emoji-only, spam, low value). Feed of recent analyzed comments with filters. |
| **Keyword Rules** | Tabbed view: Rules (table with activate/deactivate/edit/delete/reapply), Filtered Comments (status pills: All/Matched/Not Matched/No Filter, search), Top Keywords (bar chart). Rule editor modal: name, description, match mode (any/all/category/advanced), platform, category checkboxes, include/exclude keywords, keyword groups, detect contacts toggle. |

### Operations Section

| View | Description |
| --- | --- |
| **Limits** | Scrape limits (min_comments, max_posts_default/cap, comments_per_post_default/cap, global_max_comments) with auto-save. Cost protection toggles (stop_on_limit, warn_before_expensive). |
| **Database** | MongoDB connection stats (dbStats), collection table with document counts, sizes, indexes. |
| **Logs** | Structured log viewer with summary cards (level counts), search, level/source/module filters, severity badges, source badges, row-click details modal (all fields + stack trace + raw), copy JSON/raw/message, auto-refresh (10/30/60s), autoscroll with "Jump to latest", pagination, CSV export. |
| **Health** | Live health checks of every subsystem (database, Apify, Gemini, maintenance). Status dots with latency and error details. |
| **Exports** | CSV download buttons for: Jobs, Pages, Posts, Leads, Follow-ups, Application log. |

### Administration Section

| View | Description |
| --- | --- |
| **Users** | Table of admin accounts with role badges. Create/Edit/Delete users (super_admin only). Roles: viewer, manager, super_admin. Password change. |
| **Security** | Session timeout (hours), login protection toggle, audit logging toggle. Revoke all sessions (super_admin). Change own password. |
| **Features** | Feature switches: URL search enabled, exports enabled. Platform toggles (Facebook/Instagram/LinkedIn/YouTube). |
| **Maintenance** | Toggle maintenance mode, edit maintenance notice message. |
| **Audit Log** | Chronological feed of admin actions with user, category, action, timestamp, details. Immutable. |
| **Global Settings** | Tabbed settings editor with search. Tabs: General, Appearance, Branding (with live preview), Security, Localization, Features, AI, Defaults, System & History. Each setting has type-appropriate input (toggle, text, number, select, color picker, JSON textarea, file upload). Dirty tracking, save bar, version badge. System tab: version history with view/restore, export/import JSON, danger zone reset all. |

### SaaS & Billing Section

| View | Description |
| --- | --- |
| **Plans Catalog** | Manage subscription plans (Free/Starter/Pro/Business/Enterprise): name, price, features, limits, enable/disable. |
| **Subscriptions** | Tenant subscriptions: status, trial countdown, plan details, cancellation scheduling. |
| **Invoices** | Billing history: invoice number, date, amount, status. |

### CMS Section

| View | Description |
| --- | --- |
| **Pages** | CMS page management: list, create, edit, draft/publish, version history, restore. |
| **FAQ** | FAQ management: questions, answers, ordering, categories. |
| **Testimonials** | Customer testimonials: content, author, status. |
| **Navigation** | Header/footer nav items management. |
| **Media** | File upload and management. |
| **Contact Submissions** | Contact form submissions: list, mark read. |

### Shell Features

- **Collapsible sidebar** (persisted in localStorage, Ctrl+B shortcut)
- **Profile dropdown** (user name, avatar initial, logout)
- **Global search** (searches across jobs/leads/pages/posts/comments/platforms)
- **Bell notifications** (live alerts from `/api/admin/alerts`)
- **Health pill** (polls `/api/admin/health`)
- **Breadcrumb navigation**
- **Hash-based routing** (each view has a URL hash)

---

## 9. Public Website

### 9A. Marketing Website (`/website` — `website.html`)

Full public marketing website with sections served from a single HTML file:

| Section | Description |
| --- | --- |
| **Navigation** | Fixed top nav: logo, Features, How It Works, Pricing, FAQ, Contact links, Sign In + Start Free buttons, mobile hamburger menu |
| **Hero** | Gradient headline "Turn Social Conversations Into High-Intent Leads With AI", description, CTA buttons (Start Free, See How It Works), example lead card visual (score 94/100, intent, quality, contacts, urgency) |
| **Supported Platforms Strip** | Facebook, Instagram, YouTube, LinkedIn icons and labels |
| **Features Grid** | 9 feature cards: AI Lead Discovery, Intent Detection, Lead Scoring, Contact Extraction, Lead Lifecycle, Comment Filtering, Analytics, CSV Export, Team Collaboration |
| **How It Works** | 10-step flow: Enter URL → Detect Platform → Collect Data → Qualify Posts → Filter Comments → AI Analysis → Score Leads → Extract Contacts → Lead Intelligence → Sales Action |
| **Pricing** | Dynamic pricing cards fetched from `/api/public/pricing` (plans from DB), skeleton loading state |
| **FAQ** | Dynamic accordion fetched from `/api/public/faq` (FAQ items from DB), skeleton loading state |
| **CTA Banner** | "Start Discovering High-Intent Leads Today" with Start Free + Book a Demo buttons |
| **Footer** | Brand, Product links, Company links, Legal links, copyright (dynamic from settings), Sign In + Get Started buttons |

### 9B. Signup Page (`/signup` — `signup.html`)

Self-service account creation:

| Element | Description |
| --- | --- |
| **Form fields** | First Name, Last Name, Work Email, Organization Name, Password (with strength meter), Confirm Password |
| **Password strength** | 4-level meter (Too short → Weak → Fair → Good → Strong) with color coding |
| **Terms consent** | Checkbox linking to /terms and /privacy |
| **Error/success banners** | Inline validation errors, success redirect to dashboard |
| **Alternate link** | "Already have an account? Sign in" → /login |

### 9C. Contact Page (`/contact` — `contact.html`)

Contact form with validation and rate limiting:

| Element | Description |
| --- | --- |
| **Info panel** | "Let's Talk" heading, email, response time, global service note |
| **Form fields** | Full Name, Email, Company, Message (with character counter 0/2000) |
| **Validation** | Required fields, email format, message min 10 chars |
| **Rate limiting** | 1 submission per IP per 60 seconds |
| **Success state** | Form replaced with confirmation message + back link |

### 9D. Design System

Shared across public pages:

| File | Purpose |
| --- | --- |
| `design/tokens.css` | CSS custom properties: colors, spacing, typography, borders, shadows, transitions |
| `design/components.css` | Reusable components: buttons, cards, forms, badges, tables, alerts, modals |
| `design/theme.js` | Dynamic theme application from `/api/public/theme` (brand colors, name, logo, favicon) |

---

## 10. SaaS & Multi-Tenancy

### Multi-Tenant Architecture

| Component | Description | Location |
| --- | --- | --- |
| **Organizations** | Tenant entities with name, slug, status, plan, timezone, currency, settings | `app/db/saas_models.py` |
| **Memberships** | User-to-org relationships with roles (owner/admin/manager/member/viewer) | `app/db/saas_models.py` |
| **Invitations** | SHA-256 token-hashed, 7-day expiry, accept/revoke flow | `app/billing/invitations.py` |
| **Migration** | Idempotent backfill: Default Org + owner membership + `organization_id` on existing docs | `app/db/migration.py` |
| **Tenant Context** | Resolved from session, all customer data is org-scoped | `app/auth/tenant.py` |

### Subscription Plans

| Plan | Price | Searches | AI Analyses | Team Members | Key Features |
| --- | --- | --- | --- | --- | --- |
| **Free** | $0/mo | 50 | 500 | 1 | Basic features, all platforms |
| **Starter** | $49/mo | 200 | 2,000 | 5 | All platforms, CSV export |
| **Professional** | $149/mo | 500 | 5,000 | 10 | Priority support, advanced analytics |
| **Business** | $399/mo | 2,000 | 20,000 | 25 | Custom branding, API access |
| **Enterprise** | Custom | Unlimited | Unlimited | Unlimited | SLA, dedicated support, custom integrations |

### Usage & Quota System

| Metric | Counter Field | Enforcement |
| --- | --- | --- |
| `monthly_searches` | `searches_used` | Blocks search at limit |
| `monthly_posts` | `posts_used` | Informational |
| `monthly_comments` | `comments_used` | Informational |
| `monthly_ai_analyses` | `ai_used` | Blocks AI analysis at limit |
| `monthly_exports` | `exports_used` | Blocks CSV export at limit |

### API Routes

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/billing/plans` | Public plans catalog |
| GET | `/api/billing/subscription` | Tenant active subscription & trial |
| GET | `/api/billing/usage` | Real-time quota usage |
| POST | `/api/billing/checkout` | Plan upgrade checkout |
| POST | `/api/billing/cancel` | Schedule cancellation |
| GET | `/api/billing/invoices` | Invoice history |
| GET | `/api/organizations/current` | Workspace settings |
| PATCH | `/api/organizations/current` | Update workspace |
| GET | `/api/organizations/current/team` | Team members |
| POST | `/api/organizations/current/invitations` | Invite member |
| DELETE | `/api/organizations/current/invitations/{id}` | Revoke invite |
| PATCH | `/api/organizations/current/members/{id}` | Change role |
| DELETE | `/api/organizations/current/members/{id}` | Remove member |

---

## 11. CMS (Content Management)

### Managed Content Types

| Collection | CRUD | Draft/Publish | Version History | Description |
| --- | --- | --- | --- | --- |
| `cms_pages` | ✅ | ✅ | ✅ | Dynamic website pages with sections |
| `cms_faq` | ✅ | — | — | FAQ items with ordering |
| `cms_testimonials` | ✅ | ✅ | — | Customer testimonials |
| `cms_navigation` | ✅ | — | — | Header/footer nav items |
| `cms_settings` | ✅ | — | — | Branding, SEO, social links, footer |
| `cms_media` | ✅ | — | — | File uploads with metadata |
| `cms_contact` | ✅ | — | — | Contact form submissions |

### Admin CMS API Routes

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/admin/cms/pages` | List pages |
| POST | `/api/admin/cms/pages` | Create page |
| PUT | `/api/admin/cms/pages/{id}` | Update page |
| DELETE | `/api/admin/cms/pages/{id}` | Delete page |
| POST | `/api/admin/cms/pages/{id}/publish` | Publish draft |
| POST | `/api/admin/cms/pages/{id}/restore/{v}` | Restore version |
| GET | `/api/admin/cms/pages/{id}/history` | Version history |
| GET/POST/PUT/DELETE | `/api/admin/cms/faq` | FAQ CRUD |
| GET/POST/PUT/DELETE | `/api/admin/cms/testimonials` | Testimonial CRUD |
| GET/PUT | `/api/admin/cms/navigation` | Navigation management |
| GET/PUT | `/api/admin/cms/settings` | Website settings |
| GET/DELETE | `/api/admin/cms/media` | Media management |
| POST | `/api/admin/cms/media/upload` | Upload file |
| GET/PUT | `/api/admin/cms/contact` | Contact submissions |

### Public CMS API Routes

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/public/theme` | Public branding tokens |
| GET | `/api/public/config` | App config (name, colors, maintenance, features) |
| GET | `/api/public/pricing` | Plans catalog for pricing page |
| GET | `/api/public/faq` | Published FAQ items |
| GET | `/api/public/pages/{slug}` | Published page content |
| POST | `/api/public/contact` | Submit contact form (rate-limited) |

---

## 12. Lead Data Model

Twenty-plus MongoDB collections, all documents stored from **real actor output only** (absent values are `None`/omitted, never fabricated). Pydantic models live in `app/db/models.py` (core) and `app/db/saas_models.py` (SaaS).

### `search_history` — one doc per agent run

| Field | Type | Description |
| --- | --- | --- |
| `run_id` | str | Unique run ID (URL runs prefixed `URL`) |
| `query`, `intent` | str/dict | Raw URL + parsed `{keyword, type: url, platform, canonical_url, limit}` |
| `platform` | str | Detected platform |
| `status` | str | `running` \| `completed` \| `partial` \| `error` \| `cancelled` |
| `phase`, `message` | str | Live progress (queued/page/posts/comments/completed) |
| `error`, `error_meta` | str/dict | Structured failure (ScrapeError payload) |
| `pages_found`, `pages_stored` | int | Counts |
| `scrape_info` | dict | Last Apify call metadata |
| `created_at`, `completed_at`, `updated_at` | datetime | Timestamps |

### `facebook_pages` — one doc per real page

| Field | Type | Description |
| --- | --- | --- |
| `page_id` | str | Platform page ID |
| `page_name` | str | Page name |
| `facebook_url` | str | Page URL (unique per run) |
| `platform` | str | `facebook` \| `instagram` \| `youtube` \| `linkedin` |
| `category`, `about` | str | Category and intro text |
| `followers`, `likes` | int | Follower/like counts |
| `verified` | bool | Verified badge |
| `phone`, `email`, `whatsapp`, `website` | str | Contact info |
| `address`, `city`, `state`, `country` | str | Location |
| `profile_picture`, `cover_image` | str | Photo URLs |
| `posts_status`, `posts_count`, `posts_error` | mixed | Post-collection progress |
| `total_posts_found`, `relevant_posts_count`, `qualifying_posts_count` | int | Computed post analytics |
| `total_comments_on_qualifying_posts`, `latest_post_date` | mixed | Computed |
| `has_qualifying_posts`, `activity_status`, `lead_score` | mixed | Computed |
| `created_at`, `updated_at` | datetime | Timestamps |

### `facebook_posts` — one doc per post

| Field | Type | Description |
| --- | --- | --- |
| `post_id`, `post_url` | str | Post identity |
| `page_id`, `page_name`, `page_ref` | str | Parent page |
| `platform` | str | Platform of parent page |
| `caption` | str | Full post text |
| `images`, `videos`, `external_links` | list[str] | Media and links |
| `published_date` | str | Post date |
| `likes_count`, `shares_count` | int | Engagement |
| `total_comment_count` | int | Platform-reported total (never overwritten) |
| `scraped_comment_count` | int | Comments actually collected |
| `is_relevant`, `is_qualifying` | bool | Qualification flags |
| `comments_status`, `comments_error` | str/None | Comment-collection progress |
| `search_run_id`, `provider` | str | Provenance |

### `facebook_comments` — one doc per comment

| Field | Type | Description |
| --- | --- | --- |
| `comment_id`, `comment_url` | str | Comment identity |
| `author_name`, `author_profile_url` | str | Commenter info |
| `text` | str | Comment text |
| `published_date`, `reactions_count` | str/int | Metadata |
| `has_contact` | bool | Phone or email present |
| `post_id`, `post_url`, `page_id`, `platform` | str | Parent context |
| `keyword_filter_status` | str | `MATCHED` \| `NOT_MATCHED` \| `NO_FILTER` |
| `matched_keywords` | list[str] | Keywords that matched |
| `lead_score`, `lead_quality`, `priority`, `intent` | str/int | AI analysis fields |
| `phone`, `email`, `whatsapp`, `budget`, `requirement`, `location` | str | Extracted contacts |
| `lead_status` | str | Lifecycle status (new/contacted/qualified/etc.) |
| `notes` | list[dict] | Append-only notes |
| `follow_ups` | list[dict] | Follow-up items |
| `status_history` | list[dict] | Status change history |

### `ai_comments` — AI analysis of one comment

| Field | Type | Description |
| --- | --- | --- |
| `comment_ref`, `comment_id`, `comment_text`, `commenter_name` | str | Comment identity |
| `platform` | str | Platform of parent post |
| `post_ref`, `post_id`, `page_ref`, `page_name` | str | Parent context |
| `phone`, `email`, `whatsapp`, `website` | str | Extracted contacts |
| `budget`, `requirement`, `location` | str | Buyer signals |
| `intent` | str | `buying` \| `selling` \| `rent` \| `investment` \| `other` |
| `urgency` | str | Timeline signal |
| `priority` | str | `high` \| `medium` \| `low` |
| `lead_quality` | str | `hot` \| `warm` \| `cold` |
| `confidence` | float | 0–1 confidence |
| `lead_score` | int | 0–100 deterministic rank |
| `is_lead` | bool | Displayed as a lead candidate |
| `reason` | str | Why the comment was kept/dropped |
| `details` | dict | Full nested extraction |
| `analyzed_by` | str | `rules` \| `gemini` |
| `analyzed_at` | datetime | When analyzed |

### `admin_users` — admin accounts

| Field | Type | Description |
| --- | --- | --- |
| `email` | str | Unique email |
| `name` | str | Display name |
| `password` | str | bcrypt hash (cost 12) |
| `role` | str | `viewer` \| `manager` \| `super_admin` |
| `enabled` | bool | Account active |

### `system_settings` — key-value settings store

| Field | Type | Description |
| --- | --- | --- |
| `key` | str | Unique setting key (e.g. `ai.enabled`) |
| `value` | any | Setting value (typed) |
| `updated_at` | datetime | Last update (TTL index) |

### `env_overrides` — environment variable overrides

| Field | Type | Description |
| --- | --- | --- |
| `name` | str | Unique env var name |
| `value` | str | Override value |
| `updated_at` | datetime | Last update |

### `audit_logs` — admin action audit trail

| Field | Type | Description |
| --- | --- | --- |
| `action` | str | Action performed |
| `category` | str | Action category |
| `user` | str | User who performed the action |
| `details` | dict | Action details (secrets redacted) |
| `success` | bool | Whether the action succeeded |
| `at` | datetime | Timestamp |

### `keyword_filter_rules` — comment filter rules

| Field | Type | Description |
| --- | --- | --- |
| `name`, `description` | str | Rule identity |
| `match_mode` | str | `any` \| `all` \| `category` \| `advanced` |
| `include_keywords`, `exclude_keywords` | list[str] | Keywords |
| `categories` | list[str] | Category keys |
| `keyword_groups` | list[list[str]] | Advanced mode groups |
| `platforms` | list[str] | Platform filter |
| `detect_contacts` | bool | Contact detection |
| `active` | bool | Whether this is the active rule |

### `keyword_filter_results` — per-comment filter results

| Field | Type | Description |
| --- | --- | --- |
| `comment_id` | str | Comment reference |
| `rule_id` | str | Rule reference |
| `status` | str | `MATCHED` \| `NOT_MATCHED` \| `NO_FILTER` |
| `matched_keywords` | list[str] | Keywords that matched |

### `settings_history` — settings version history

| Field | Type | Description |
| --- | --- | --- |
| `version` | int | Version number |
| `changed` | dict | Changed keys and values |
| `changed_by` | str | User who made the change |
| `created_at` | datetime | Timestamp |

### `organizations` — tenant workspaces

| Field | Type | Description |
| --- | --- | --- |
| `name` | str | Organization name |
| `slug` | str | URL-friendly identifier (unique) |
| `status` | str | `active` \| `trial` \| `suspended` \| `disabled` \| `pending` \| `cancelled` \| `archived` |
| `plan_id` | str | Current subscription plan |
| `timezone` | str | Default timezone |
| `currency` | str | Billing currency |
| `settings` | dict | Workspace-specific settings |
| `metadata` | dict | System metadata |

### `organization_members` — user-to-org membership

| Field | Type | Description |
| --- | --- | --- |
| `organization_id` | ObjectId | Parent organization |
| `user_id` | ObjectId | Member user |
| `role` | str | `owner` \| `admin` \| `manager` \| `member` \| `viewer` |
| `status` | str | `active` \| `invited` \| `suspended` |
| `invited_by` | str | Who invited this member |
| `joined_at` | datetime | Join timestamp |

### `organization_invitations` — pending team invites

| Field | Type | Description |
| --- | --- | --- |
| `organization_id` | ObjectId | Target organization |
| `email` | str | Invitee email |
| `role` | str | Assigned role |
| `token_hash` | str | SHA-256 hash of invite token |
| `invited_by` | str | Inviter |
| `expires_at` | datetime | Token expiry (default 7 days) |
| `status` | str | `pending` \| `accepted` \| `expired` \| `revoked` |

### `subscriptions` — tenant subscriptions

| Field | Type | Description |
| --- | --- | --- |
| `organization_id` | ObjectId | Tenant |
| `plan_id` | str | Subscribed plan |
| `status` | str | `trialing` \| `active` \| `past_due` \| `cancelled` \| `expired` |
| `trial_start` / `trial_end` | datetime | Trial period |
| `current_period_start` / `current_period_end` | datetime | Billing period |
| `cancel_at` | datetime | Scheduled cancellation |

### `organization_usage` — aggregated quota counters

| Field | Type | Description |
| --- | --- | --- |
| `organization_id` | ObjectId | Tenant |
| `period` | str | `YYYY-MM` calendar month |
| `searches_used` / `posts_used` / `comments_used` / `ai_used` / `exports_used` | int | Atomic counters |

### `invoices` — billing history

| Field | Type | Description |
| --- | --- | --- |
| `organization_id` | ObjectId | Tenant |
| `invoice_number` | str | Unique invoice ID |
| `amount` | float | Amount in currency |
| `currency` | str | Currency code |
| `status` | str | `pending` \| `paid` \| `failed` \| `refunded` |
| `created_at` | datetime | Invoice date |

### `users` — customer users

| Field | Type | Description |
| --- | --- | --- |
| `email` | str | Unique email |
| `name` | str | Display name |
| `password` | str | bcrypt hash |
| `organization_id` | ObjectId | Primary organization |
| `role` | str | Organization role |
| `status` | str | `active` \| `inactive` \| `suspended` |

### `cms_pages` — CMS managed pages

| Field | Type | Description |
| --- | --- | --- |
| `title` | str | Page title |
| `slug` | str | URL slug |
| `content` | str | HTML content |
| `status` | str | `draft` \| `published` \| `archived` |
| `sections` | list | Page sections with ordering |
| `version` | int | Version number |
| `version_history` | list | Previous versions |

### `cms_faq` — FAQ items

| Field | Type | Description |
| --- | --- | --- |
| `question` | str | FAQ question |
| `answer` | str | HTML answer |
| `order` | int | Display ordering |
| `category` | str | Optional category |

### `cms_settings` — website settings

| Field | Type | Description |
| --- | --- | --- |
| `brand_name` | str | Brand display name |
| `tagline` | str | Brand tagline |
| `logo_url` / `favicon_url` | str | Brand assets |
| `primary_color` / `accent_color` | str | Brand colors |
| `seo_title` / `seo_description` | str | SEO metadata |
| `social_links` | dict | Social media URLs |
| `footer_copyright` | str | Footer text |

---

## 13. API Architecture

Interactive docs at `/docs` (Swagger UI) when `enable_api_docs=true`. All product routes are under `/api`, admin routes under `/api/admin`, public routes under `/api/public`.

### Auth Routes (`/api/auth/`)

| Method | Endpoint | Purpose | Auth |
| --- | --- | --- | --- |
| POST | `/api/auth/login` | Login with email/password. Body: `{email, password, scope}`. Sets session cookie. | None |
| POST | `/api/auth/signup` | Create new account. Body: `{name, email, password, organization_name}`. | None |
| POST | `/api/auth/logout` | Clear session cookie. | Session |
| GET | `/api/auth/me` | Return current user from session. | Session |

### Product Routes (`/api/`)

| Method | Endpoint | Purpose | Request | Response |
| --- | --- | --- | --- | --- |
| POST | `/api/url/search` | Start URL-based search | `url`, `max_posts`, `max_comments_per_post`, `filter_mode`, `preset`, `include_keywords`, `exclude_keywords`, `categories`, `match_mode` | `{run_id, status, platform, canonical_url}` |
| GET | `/api/url/search/{run_id}/report` | Full report bundle | — | `{page, posts, comments, status, platform, search_url}` |
| GET | `/api/search/history` | Recent runs | `limit` | `{searches, count}` |
| GET | `/api/search/{run_id}` | Run status + pages | — | `{success, items, count, scrape_info, search, pages}` |
| POST | `/api/search/{run_id}/cancel` | Cancel running search | — | `{run_id, status}` |
| DELETE | `/api/search/{run_id}` | Delete search + all data | — | `{run_id, deleted: {pages, posts, comments, leads}}` |
| GET | `/api/pages` | List/filter pages | `run_id`, `q`, `category`, `city`, `contact`, `offset`, `limit` | `{pages, total, offset, limit}` |
| GET | `/api/pages/{id}` | One page | — | page doc |
| POST | `/api/pages/{id}/posts` | Collect posts (background) | `max_posts` | `{status: running}` |
| GET | `/api/pages/{id}/posts` | Cached posts + status + stats | `offset`, `limit` | `{page, posts, total, qualifyingPosts, activityStatus, leadScore, ...}` |
| GET | `/api/posts/{id}` | One post | — | post doc |
| POST | `/api/posts/{id}/comments` | Collect comments + AI (background) | `max_comments` | `{status: running}` or `skipped` |
| GET | `/api/posts/{id}/comments` | Analyzed comments | `filter_type`, `q`, `quality`, `sort_by`, `only_leads`, `contact_only`, `limit` | `{post, comments, total, counts, ...}` |
| GET | `/api/comments/{id}` | Lead detail | — | merged doc (comment + AI + page + post) |
| PATCH | `/api/leads/{id}` | Update lead status | `{lead_status}` | `{success}` |
| POST | `/api/leads/{id}/notes` | Add note | `{text}` | `{success}` |
| POST | `/api/leads/{id}/follow-ups` | Add follow-up | `{title, due_at?, notes?}` | `{success}` |
| GET | `/api/comment-filters/catalog` | Filter catalog | — | `{categories, presets, custom_categories}` |
| GET | `/api/export/pages.csv` | Export pages CSV | `run_id` | CSV file |
| GET | `/api/export/posts.csv` | Export posts CSV | `page_id` | CSV file |
| GET | `/api/export/comments.csv` | Export leads CSV | `post_id`, `only_leads` | CSV file |

### Admin Routes (`/api/admin/`) — 50+ endpoints

| Method | Endpoint | Purpose | Role |
| --- | --- | --- | --- |
| GET | `/api/admin/dashboard` | Dashboard KPIs, charts, alerts | viewer+ |
| GET | `/api/admin/jobs` | Paginated jobs list | viewer+ |
| GET | `/api/admin/jobs/{run_id}` | Full job report | viewer+ |
| POST | `/api/admin/jobs/{run_id}/retry` | Retry failed job | manager+ |
| POST | `/api/admin/jobs/{run_id}/cancel` | Cancel running job | manager+ |
| DELETE | `/api/admin/jobs/{run_id}` | Delete job + data | super_admin |
| GET | `/api/admin/failed-jobs` | Failed jobs summary | viewer+ |
| GET | `/api/admin/leads` | Paginated leads | viewer+ |
| PATCH | `/api/admin/leads/{id}` | Update lead status | manager+ |
| POST | `/api/admin/leads/bulk` | Bulk lead actions | manager+ |
| GET | `/api/admin/analytics` | Aggregated analytics | viewer+ |
| GET | `/api/admin/platforms` | Platform list + stats | viewer+ |
| POST | `/api/admin/platforms/{p}/toggle` | Enable/disable platform | manager+ |
| POST | `/api/admin/platforms/{p}/actor` | Update actor ID | manager+ |
| GET | `/api/admin/apify` | Apify connection status | viewer+ |
| POST | `/api/admin/apify/test` | Live Apify test | manager+ |
| POST | `/api/admin/apify/token` | Save Apify token | manager+ |
| GET | `/api/admin/actors` | List all actors | viewer+ |
| POST | `/api/admin/actors/test` | Test an actor | manager+ |
| GET | `/api/admin/usage` | Usage aggregation | viewer+ |
| GET | `/api/admin/env` | List env vars | manager+ |
| POST | `/api/admin/env/unlock` | Unlock env editing | manager+ |
| PUT | `/api/admin/env/{key}` | Set env var override | manager+ |
| DELETE | `/api/admin/env/{key}` | Reset env var | manager+ |
| GET | `/api/admin/ai` | AI settings + stats | viewer+ |
| PUT | `/api/admin/ai` | Update AI settings | manager+ |
| POST | `/api/admin/ai/test` | Live AI test | manager+ |
| GET | `/api/admin/scoring` | Scoring settings | viewer+ |
| PUT | `/api/admin/scoring` | Update scoring | manager+ |
| GET | `/api/admin/comment-intelligence` | CI settings + stats | viewer+ |
| PUT | `/api/admin/comment-intelligence` | Update CI settings | manager+ |
| GET | `/api/admin/comments` | Comments feed | viewer+ |
| GET | `/api/admin/limits` | Limits settings | viewer+ |
| PUT | `/api/admin/limits` | Update limits | manager+ |
| GET | `/api/admin/database` | MongoDB stats | viewer+ |
| GET | `/api/admin/logs` | Structured log entries with filters (q, level, source, module), summary stats, pagination | viewer+ |
| GET | `/api/admin/logs/stats` | Log level summary statistics | viewer+ |
| GET | `/api/admin/logs/download` | Raw log file download | manager+ |
| GET | `/api/admin/health` | Health checks | viewer+ |
| GET | `/api/admin/alerts` | Active alerts | viewer+ |
| GET | `/api/admin/users` | List users | viewer+ |
| POST | `/api/admin/users` | Create user | super_admin |
| PATCH | `/api/admin/users/{id}` | Update user | super_admin |
| DELETE | `/api/admin/users/{id}` | Delete user | super_admin |
| GET | `/api/admin/security` | Security settings | viewer+ |
| PUT | `/api/admin/security` | Update security | manager+ |
| POST | `/api/admin/security/revoke-sessions` | Revoke all sessions | super_admin |
| GET | `/api/admin/features` | Feature flags | viewer+ |
| PUT | `/api/admin/features` | Update features | manager+ |
| GET | `/api/admin/maintenance` | Maintenance status | viewer+ |
| POST | `/api/admin/maintenance` | Toggle maintenance | manager+ |
| GET | `/api/admin/audit-logs` | Audit log entries | viewer+ |
| GET | `/api/admin/settings` | All global settings | viewer+ |
| PUT | `/api/admin/settings` | Save settings | manager+ |
| POST | `/api/admin/settings/reset` | Reset settings | manager+ |
| POST | `/api/admin/settings/upload` | Upload file (logo/favicon) | manager+ |
| GET | `/api/admin/settings/export` | Export settings JSON | viewer+ |
| POST | `/api/admin/settings/import` | Import settings JSON | manager+ |
| GET | `/api/admin/settings/history` | Version history | viewer+ |
| POST | `/api/admin/settings/history/{v}/restore` | Restore version | manager+ |
| GET | `/api/admin/search` | Global search | viewer+ |

### Organization Routes (`/api/organizations/`)

| Method | Endpoint | Purpose | Role |
| --- | --- | --- | --- |
| GET | `/api/organizations/current` | Get workspace settings | member+ |
| PATCH | `/api/organizations/current` | Update workspace settings | admin+ |
| GET | `/api/organizations/current/team` | List team members | member+ |
| POST | `/api/organizations/current/invitations` | Invite team member | admin+ |
| DELETE | `/api/organizations/current/invitations/{id}` | Revoke invitation | admin+ |
| PATCH | `/api/organizations/current/members/{id}` | Change member role | admin+ |
| DELETE | `/api/organizations/current/members/{id}` | Remove member | admin+ |
| GET | `/api/invitations/{token}` | Validate invitation token | None |
| POST | `/api/invitations/{token}/accept` | Accept invitation | None |

### Billing Routes (`/api/billing/`)

| Method | Endpoint | Purpose | Auth |
| --- | --- | --- | --- |
| GET | `/api/billing/plans` | Public plans catalog | None |
| GET | `/api/billing/subscription` | Active subscription & trial | Session |
| GET | `/api/billing/usage` | Real-time quota usage | Session |
| POST | `/api/billing/checkout` | Plan upgrade checkout | Session |
| POST | `/api/billing/cancel` | Schedule cancellation | Session |
| POST | `/api/billing/reactivate` | Reactivate subscription | Session |
| GET | `/api/billing/invoices` | Invoice history | Session |
| POST | `/api/billing/webhook` | Provider webhook handler | None |

### Admin AI Routes (`/api/admin/ai/`)

| Method | Endpoint | Purpose | Role |
| --- | --- | --- | --- |
| GET | `/api/admin/ai/prompts` | List AI prompts | viewer+ |
| GET | `/api/admin/ai/prompts/{id}` | Get prompt detail | viewer+ |
| POST | `/api/admin/ai/prompts` | Create prompt | manager+ |
| PUT | `/api/admin/ai/prompts/{id}` | Update prompt | manager+ |
| DELETE | `/api/admin/ai/prompts/{id}` | Delete prompt | super_admin |
| POST | `/api/admin/ai/prompts/{id}/rollback` | Rollback to previous version | manager+ |
| GET | `/api/admin/ai/models` | List AI models | viewer+ |
| PUT | `/api/admin/ai/models/{name}` | Update model config | manager+ |
| POST | `/api/admin/ai/test` | Live AI test | manager+ |

### Admin Apify Routes (`/api/admin/apify/`)

| Method | Endpoint | Purpose | Role |
| --- | --- | --- | --- |
| GET | `/api/admin/apify/jobs` | Paginated job list with filters | viewer+ |
| GET | `/api/admin/apify/jobs/{id}` | Job detail + report | viewer+ |
| POST | `/api/admin/apify/jobs/{id}/retry` | Retry failed job | manager+ |
| POST | `/api/admin/apify/jobs/{id}/cancel` | Cancel running job | manager+ |
| DELETE | `/api/admin/apify/jobs/{id}` | Delete job + data | super_admin |
| POST | `/api/admin/apify/jobs/bulk-retry` | Bulk retry failed jobs | manager+ |
| GET | `/api/admin/apify/actors` | List all actors with config | viewer+ |
| POST | `/api/admin/apify/actors/{key}/test` | Test specific actor | manager+ |

### Admin Leads Routes (`/api/admin/leads/`)

| Method | Endpoint | Purpose | Role |
| --- | --- | --- | --- |
| GET | `/api/admin/leads` | Paginated leads with filters | viewer+ |
| GET | `/api/admin/leads/{id}` | Lead full dossier | viewer+ |
| PATCH | `/api/admin/leads/{id}` | Update lead status | manager+ |
| POST | `/api/admin/leads/{id}/notes` | Add note to lead | manager+ |
| POST | `/api/admin/leads/{id}/follow-ups` | Add follow-up | manager+ |
| POST | `/api/admin/leads/bulk` | Bulk lead actions | manager+ |

### Admin Analytics Routes (`/api/admin/analytics/`)

| Method | Endpoint | Purpose | Role |
| --- | --- | --- | --- |
| GET | `/api/admin/analytics/overview` | SaaS overview metrics | viewer+ |
| GET | `/api/admin/analytics/lead-funnel` | Lead pipeline funnel | viewer+ |
| GET | `/api/admin/analytics/platforms` | Platform comparison | viewer+ |
| GET | `/api/admin/analytics/categories` | Category breakdown | viewer+ |
| GET | `/api/admin/analytics/plans` | Plan analytics | viewer+ |
| GET | `/api/admin/analytics/activity` | Daily activity charts | viewer+ |

### Admin CMS Routes (`/api/admin/cms/`)

| Method | Endpoint | Purpose | Role |
| --- | --- | --- | --- |
| GET/POST | `/api/admin/cms/pages` | List/Create pages | viewer+/manager+ |
| PUT/DELETE | `/api/admin/cms/pages/{id}` | Update/Delete page | manager+/super_admin |
| POST | `/api/admin/cms/pages/{id}/publish` | Publish draft | manager+ |
| POST | `/api/admin/cms/pages/{id}/restore/{v}` | Restore version | manager+ |
| GET | `/api/admin/cms/pages/{id}/history` | Version history | viewer+ |
| GET/POST/PUT/DELETE | `/api/admin/cms/faq` | FAQ CRUD | viewer+/manager+ |
| GET/POST/PUT/DELETE | `/api/admin/cms/testimonials` | Testimonial CRUD | viewer+/manager+ |
| GET/PUT | `/api/admin/cms/navigation` | Navigation management | manager+ |
| GET/PUT | `/api/admin/cms/settings` | Website settings | viewer+/manager+ |
| GET/POST/DELETE | `/api/admin/cms/media` | Media management | viewer+/manager+ |
| POST | `/api/admin/cms/media/upload` | Upload file | manager+ |
| GET/PUT | `/api/admin/cms/contact` | Contact submissions | viewer+/manager+ |

### Public Routes (`/api/public/`)

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/public/config` | Public-safe settings (branding, maintenance, features) |
| GET | `/api/public/theme` | Branding tokens (colors, name, logo, favicon) |
| GET | `/api/public/pricing` | Plans catalog for pricing page |
| GET | `/api/public/faq` | Published FAQ items |
| GET | `/api/public/pages/{slug}` | Published page content |
| POST | `/api/public/contact` | Submit contact form (rate-limited) |
| GET | `/health` | Health check (MongoDB ping, latency) |

---

## 14. Apify Architecture

```mermaid
flowchart TD
    R[Social URL input] --> B[Backend pipeline]
    B --> S["Actor selection (per platform)"]
    S --> I["Actor input (startUrls / resultsLimit / platform flags)"]
    I --> C["client.actor(id).call(run_input, run_timeout=8min)<br/>or .start() + poll + abort (cancellable)"]
    C --> D[Apify run + dataset]
    D --> E["Backend reads dataset (iterate_items)"]
    E --> F["Normalization (map_*_item)"]
    F --> G[AI analysis + qualification]
    G --> H[Lead results]
    E -->|"classified errors<br/>BLOCKED/ACTOR_FAILED/NO_RESULTS/..."| ERR[ScrapeError → API/UI]
```

### Facebook Actors (Hardcoded)

| Actor | Purpose | Input | Output (used fields) |
| --- | --- | --- | --- |
| `apify/facebook-pages-scraper` | Page details by URL | `{startUrls: [{url}]}` | verified, email, phone, whatsapp, website, about, photos, followers, category |
| `apify/facebook-posts-scraper` | Posts of a page | `{startUrls, resultsLimit, captionText: true}` | post url/id/text/media/dates/likes/comment count/shares |
| `apify/facebook-comments-scraper` | Comments of a post | `{startUrls, resultsLimit, includeNestedComments: true, viewOption: RANKED_UNFILTERED}` | comment id/url/text/author/date/reactions |

### Configurable URL-Search Actors

| Platform | Default Actor ID | Comments |
| --- | --- | --- |
| Instagram | `apify/instagram-scraper` | ✅ Supported |
| YouTube | `streamers/youtube-scraper` | ❌ Not collected |
| LinkedIn | `harvestapi/linkedin-company` + `harvestapi/linkedin-company-posts` | ✅ Supported |

### Run Lifecycle & Error Handling

- Every call wrapped with **8-minute timeout** (`_RUN_TIMEOUT_MIN`)
- **Cancellable runs**: `actor.start()` then poll every 5s; cancellation calls `run.abort()`
- Datasets read via `iterate_items()`; failures classified as `DATASET_ERROR`
- Errors classified: `BLOCKED`, `ACTOR_FAILED`, `ACTOR_TIMED_OUT`, `INVALID_INPUT`, `API_ERROR`, `ACCESS_DENIED`, `NETWORK_ERROR`, `NO_RESULTS`
- Billing hints produce user-facing `ApifyError` with billing link
- **Cost control**: `MIN_COMMENTS` gates expensive comment actor; posts below threshold never scraped
- **Graceful shutdown**: in-flight Apify runs aborted on server exit

---

## 15. AI Architecture

```mermaid
flowchart TD
    R["Raw lead data (comment text + post caption + author)"] --> CF["Comment Filter Pipeline<br/>app/pipeline/comment_filter.py<br/>keyword/category matching"]
    CF -->|"NOT_MATCHED"| SKIP["Stored but skipped"]
    CF -->|"MATCHED / NO_FILTER"| P["Stage 1 — Rule filter<br/>spam / emoji-only / link-only<br/>+ regex extraction (offline, free)"]
    P -->|"dropped"| OUT["No lead"]
    P -->|passes| PB["Prompt builder — COMMENT_SYSTEM_PROMPT<br/>Strict JSON schema, 'never invent values'"]
    PB --> M["Google Gemini — gemini-2.5-flash<br/>generateContent, 429 backoff + 10-min circuit breaker"]
    M -->|fail/rate-limited| FB["Fallback to rule result<br/>analyzed_by: rules"]
    M -->|structured JSON| V["Validation + normalization"]
    V --> S["comment_lead_score (0-100)"]
    S --> D["extract_display_signals → is_lead"]
    D --> DB[(ai_comments — upsert)]
```

- **Provider**: Google Gemini via direct REST, no SDK. Model default `gemini-2.5-flash` (`GEMINI_MODEL`).
- **Input**: JSON payload with `author`, `post_caption`, `comment_text` — one comment per call.
- **Output**: Strict JSON with `is_useful`, `lead_type`, `confidence_score`, `priority`, `lead_quality`, `sentiment`, `contact{}`, `person{}`, `buyer{}`.
- **Error handling**: 429 opens 10-minute circuit breaker; any exception falls back to Stage-1 rule result.
- **Rule path**: `rule_based_classify` + `_rule_extraction` (regex contact/budget/location/urgency/intent extraction).
- **Without GEMINI_API_KEY**: Everything still works on rules — only richer fields are missing.

---

## 16. Comment Filter Pipeline

**Location**: `app/pipeline/comment_filter.py` (860 lines)

The keyword filter is the **first layer** of the lead pipeline, running before AI analysis.

### Modes

| Mode | Behavior |
| --- | --- |
| **NO_FILTER** | No keywords, no categories: every comment processed (status `NO_FILTER`). Default. |
| **KEYWORD** | Match `include_keywords` (any/all mode); `exclude_keywords` always veto. |
| **CATEGORY** | Match predefined category keywords (real_estate, automotive, etc.). |
| **ADVANCED** | Keyword groups — must match one keyword in EVERY group. |

### Predefined Business Categories

| Key | Name | Icon |
| --- | --- | --- |
| `real_estate` | Real Estate | 🏠 |
| `automotive` | Automotive | 🚗 |
| `interior_design` | Interior Design | 🛋️ |
| `wedding` | Wedding Services | 💒 |
| `education` | Education | 📚 |
| `healthcare` | Healthcare | 🏥 |
| `technology` | Technology | 💻 |
| `finance` | Finance | 💰 |
| `travel` | Travel | ✈️ |
| `fashion` | Fashion | 👗 |

### Matching Semantics

- Case-insensitive (both sides normalized to lowercase)
- Unicode normalization (NFKC) + whitespace/punctuation normalization
- Phrase matching (multi-word keywords = normalized substring)
- Word-boundary matching for short keywords (<4 chars) so "car" never matches "career"
- Prefix matching for longer keywords so "price" matches "prices"
- Devanagari (Hindi) keywords match natively after normalization

---

## 17. Lead Lifecycle

**Location**: `app/pipeline/lead_lifecycle.py` (237 lines)

### State Machine

```
new → contacted → qualified → follow_up → converted
new → disqualified
new → lost
contacted → lost
qualified → lost
follow_up → lost
any → archived (admin only)
```

### Statuses

| Status | Label | Description |
| --- | --- | --- |
| `new` | New | Just identified as a lead |
| `contacted` | Contacted | Outreach initiated |
| `qualified` | Qualified | Meets quality criteria |
| `follow_up` | Follow-up | Scheduled for follow-up |
| `converted` | Converted | Successfully converted |
| `lost` | Lost | Lost opportunity |
| `disqualified` | Disqualified | Does not meet criteria |
| `archived` | Archived | Historical record |

### Features

- **Valid transitions** enforced per state (e.g., `archived` has no transitions out)
- **Status history** recorded on every change (from_status, to_status, changed_by, reason, timestamp)
- **Notes** append-only with author, text, and timestamp
- **Follow-ups** with title, due date, status (pending/completed/cancelled/overdue), and notes
- **Terminal states**: converted, lost, disqualified — only transition to archived

---

## 18. Data Flow

```mermaid
flowchart LR
    USER[USER] --> FE[FRONTEND app.js]
    USER --> ADM[ADMIN admin.js]
    FE -->|POST + poll| API[BACKEND API routes]
    ADM -->|GET/POST| API
    API -->|background task| AG[URL SEARCH PIPELINE url_search.py]
    AG --> PROV[PROVIDER — Apify actors]
    PROV --> RAW[RAW ACTOR DATA]
    RAW --> NORM[NORMALIZERS map_*_item]
    NORM --> DB[(MONGODB — 11 collections)]
    NORM --> CF[COMMENT FILTER comment_filter.py]
    CF --> AI[AI ANALYSIS comment_ai]
    AI --> QUAL[LEAD QUALIFICATION score / priority / is_lead]
    QUAL --> LC[LEAD LIFECYCLE lead_lifecycle.py]
    LC --> DB
    DB --> RESP[API RESPONSE — serialized docs]
    RESP --> FE
    RESP --> ADM
    FE --> USER
    ADM --> USER
```

---

## 19. Error Handling

The project treats failures as **data**, not just exceptions.

| Scenario | What Happens |
| --- | --- |
| Invalid URL / unsupported platform | `UrlError` → **422** `{errorType: invalid\|unsupported}` |
| Invalid ObjectId | **400** `Invalid id: ...` |
| Missing document | **404** `<collection> document not found` |
| Mongo unreachable | **503** `Database unavailable` |
| Missing `APIFY_API_TOKEN` | Startup warning; searches fail fast with clear message |
| Apify actor run failed | `ACTOR_FAILED` with `statusMessage`; run marked `error` |
| Apify run timed out (> 8 min) | `ACTOR_TIMED_OUT` — actor aborted |
| Apify API-level errors | 401 → invalid token; 403 → `ACCESS_DENIED`; 429 → rate-limited; 5xx → server error |
| Facebook blocking evidence | Only when run stats contain block/captcha tokens → `BLOCKED` |
| Empty results | `NO_RESULTS` — never treated as a block |
| Billing/credit exhaustion | User-facing `ApifyError` with billing link |
| Posts scrape returns nothing | Page `posts_status: empty` |
| Comments below threshold | Post `comments_status: skipped` — no API call made |
| Gemini failure / 429 | Circuit breaker (10 min), rule-based fallback |
| Page details actor fails | Graceful fallback: URL-derived page doc |
| Cancellation mid-run | `CANCELLED`; Apify run aborted |
| Stale "running" status (30 min) | UI offers Retry |
| Duplicate key on insert | Handled via unique indexes + upsert |
| Brute-force login attempt | Rate limit: 5 failures → lockout |
| Session expired | 401 → redirect to `/login` |
| Maintenance mode | 503 with custom message (admin panel still accessible) |

---

## 20. Environment Variables

All settings defined in `app/config.py` (`pydantic-settings`, `.env` file). Template: `.env.example`.

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `GEMINI_API_KEY` | No¹ | — | Google Gemini key for comment analysis |
| `GEMINI_MODEL` | No | `gemini-2.5-flash` | Gemini model ID |
| `MONGO_URI` | Yes | `mongodb://localhost:27017` | MongoDB connection string |
| `MONGO_DB_NAME` | Yes | `LeadAI` | Database name |
| `DNS_SERVERS` | No | `8.8.8.8,1.1.1.1` | DNS resolvers for SRV lookups |
| `APIFY_API_TOKEN` | Yes² | — | Apify API token |
| `MIN_COMMENTS` | No | `10` | Comment-count threshold (0 disables) |
| `INSTAGRAM_ACTOR_ID` | No | `apify/instagram-scraper` | Instagram actor |
| `YOUTUBE_ACTOR_ID` | No | `streamers/youtube-scraper` | YouTube actor |
| `LINKEDIN_ACTOR_ID` | No | `harvestapi/linkedin-company` | LinkedIn company actor |
| `LINKEDIN_POSTS_ACTOR_ID` | No | `harvestapi/linkedin-company-posts` | LinkedIn posts actor |
| `MAX_COMMENTS_TO_COLLECT` | No | `100` | Global comment cap per run |
| `SUPERADMIN_EMAIL` | **Yes** | — | Permanent Super Admin email (environment only; not editable in the app) |
| `SUPERADMIN_PASSWORD` | **Yes** | — | Permanent Super Admin password — plain (12+ chars) or a bcrypt hash |
| `TRUST_PROXY_HEADERS` | No | `false` | Set `true` behind Render / a load balancer (real client IP for login limits) |
| `PANEL_ADMIN_EMAIL` / `PANEL_ADMIN_PASSWORD_HASH` | No | — | Deprecated Super Admin names, used only when `SUPERADMIN_EMAIL` is unset |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD_HASH` | No | — | Deprecated site login; copied onto the database account at startup, then unused |
| `SESSION_SECRET` | Yes³ | — | Secret for session cookie signing |
| `SESSION_TTL_DAYS` | No | `7` | Session lifetime |
| `SESSION_COOKIE_SECURE` | No | `false` | Set `true` for HTTPS |
| `ALLOWED_ORIGINS` | No | — | CORS origins (comma-separated) |
| `ENABLE_API_DOCS` | No | `false` | Enable `/docs` Swagger UI |

¹ Required for Gemini enrichment. ² Required for scraping. ³ Required for stable sessions.

---

## 21. System Settings

Stored in MongoDB `system_settings` collection, configurable via Admin Control Center.

### Limits

| Key | Default | Description |
| --- | --- | --- |
| `limits.min_comments` | 10 | Minimum comments to qualify a post |
| `limits.max_posts_default` | 20 | Default post limit |
| `limits.max_posts_cap` | 100 | Hard post limit |
| `limits.max_comments_per_post_default` | 30 | Default comments per post |
| `limits.max_comments_per_post_cap` | 500 | Hard comments per post limit |
| `limits.global_max_comments` | 5000 | Total comment budget per query |
| `cost.stop_on_limit` | false | Abort when budget reached |
| `cost.warn_before_expensive` | true | Show cost warning |

### AI

| Key | Default | Description |
| --- | --- | --- |
| `ai.enabled` | true | Enable Gemini analysis |
| `ai.rule_fallback` | true | Fallback to rules if AI fails |
| `ai.max_calls_per_job` | 1000 | Gemini calls budget per run |
| `ai.temperature` | 0.3 | Model temperature |

### Lead Scoring

| Key | Default | Description |
| --- | --- | --- |
| `scoring.confidence_weight` | 30 | Confidence weight |
| `scoring.priority_weight` | 25 | Priority weight |
| `scoring.quality_weight` | 20 | Quality weight |
| `scoring.contact_phone` | 15 | Phone contact points |
| `scoring.contact_email` | 10 | Email contact points |
| `scoring.spam_penalty` | 20 | Spam penalty points |
| `scoring.hot_min` | 70 | Hot lead threshold |
| `scoring.warm_min` | 40 | Warm lead threshold |

### Comment Intelligence

| Key | Default | Description |
| --- | --- | --- |
| `ci.detect_phone` | true | Extract phone numbers |
| `ci.detect_email` | true | Extract emails |
| `ci.detect_budget` | true | Detect budget mentions |
| `ci.detect_location` | true | Detect locations |
| `ci.detect_urgency` | true | Detect urgency |
| `ci.detect_buying_intent` | true | Detect buying intent |
| `ci.detect_selling_intent` | false | Detect selling intent |
| `ci.ignore_emoji_only` | true | Skip emoji-only comments |
| `ci.ignore_spam` | true | Skip spam comments |
| `ci.ignore_low_value` | true | Skip low-value comments |

### Security

| Key | Default | Description |
| --- | --- | --- |
| `security.session_timeout_hours` | 24 | Session timeout |
| `security.login_protection` | true | Rate-limit logins |
| `security.audit_logging` | true | Record admin actions |

### Features & Platforms

| Key | Default | Description |
| --- | --- | --- |
| `features.url_search.enabled` | true | Allow URL search |
| `features.exports.enabled` | true | Allow CSV exports |
| `platform.facebook` | true | Facebook enabled |
| `platform.instagram` | true | Instagram enabled |
| `platform.youtube` | true | YouTube enabled |
| `platform.linkedin` | true | LinkedIn enabled |
| `maintenance.enabled` | false | Maintenance mode |
| `maintenance.message` | "" | Maintenance notice |

---

## 22. Installation

### Prerequisites

- **Python 3.11+**
- **MongoDB** — local install or Docker (`mongo:7`)
- **Apify account + token** (free tier: https://apify.com)
- **Google AI Studio key** (optional — https://aistudio.google.com)
- Docker + docker-compose (optional)

### Backend Installation

```bash
git clone https://github.com/saurabh95710/LEADAI.git
cd lead_apify

python -m venv .venv
# Windows: .venv\Scripts\activate    |    macOS/Linux: source .venv/bin/activate

pip install -r requirements.txt
```

### Environment Setup

```bash
cp .env.example .env
```

Edit `.env` with your real values. At minimum: `MONGO_URI`, `SUPERADMIN_EMAIL`, `SUPERADMIN_PASSWORD`, `SESSION_SECRET` (and `APIFY_API_TOKEN` to run searches). Admins and users are never configured here — they are database accounts created through demo signup and invitations.

```bash
# Generate bcrypt password hash
python -c "import bcrypt;print(bcrypt.hashpw(b'YourPassword', bcrypt.gensalt(12)).decode())"
```

### Database Setup

No manual schema creation needed — `ensure_indexes()` creates all indexes at startup using `_create_index_safe()` which handles IndexKeySpecsConflict by dropping and recreating conflicting indexes. The database is created automatically on first write.

### Frontend

Nothing to build — `app/static/` is served directly by the backend.

### Docker

```bash
docker compose up --build
```

Starts `leadai_api` (FastAPI on :8000) and `leadai_mongo` (MongoDB 7 on :27017 with named volume). `.env` is passed through `env_file`.

---

## 23. Running the Project

```bash
# Backend
uvicorn app.main:app --reload --port 8000
```

or with Docker:

```bash
docker compose up --build
```

URLs:

- Dashboard: [http://localhost:8000/](http://localhost:8000/)
- Admin: [http://localhost:8000/admin](http://localhost:8000/admin)
- Login: [http://localhost:8000/login](http://localhost:8000/login)
- Admin Login: [http://localhost:8000/login?admin=1](http://localhost:8000/login?admin=1)
- Signup: [http://localhost:8000/signup](http://localhost:8000/signup)
- Contact: [http://localhost:8000/contact](http://localhost:8000/contact)
- Website: [http://localhost:8000/website](http://localhost:8000/website)
- API docs: [http://localhost:8000/docs](http://localhost:8000/docs) (if enabled)
- Health: [http://localhost:8000/health](http://localhost:8000/health)
- URL-search report: `http://localhost:8000/static/url_report.html?run_id=<URL-run-id>`

---

## 24. Authentication & Authorization

### Two-Tier Auth

| Scope | Login URL | Credential Source | Access |
| --- | --- | --- | --- |
| **Site** (org admins, managers, users, viewers) | `/login` | Database accounts (`users` + `organization_members`), created by signup / invitation | User portal (`/`, `/api/*`) and Org Admin portal (`/org-admin`) |
| **Super Admin** | `/login?superadmin=1` | `SUPERADMIN_EMAIL` + `SUPERADMIN_PASSWORD` environment variables (permanent) | Super Admin portal (`/superadmin`) and platform console (`/admin`) |
| **Platform staff** | `/login?admin=1` | `admin_users` collection / `users.platform_role` (created by the Super Admin) | Platform console (`/admin`) by role |

### Public Pages (No Auth Required)

| Page | URL | Description |
| --- | --- | --- |
| Website | `/website` | Marketing landing page |
| Signup | `/signup` | Self-service account creation |
| Contact | `/contact` | Contact form |
| Features | `/features` | Alias for website |
| Pricing | `/pricing` | Alias for website |
| About | `/about` | Alias for website |
| FAQ | `/faq` | Alias for website |
| Privacy | `/privacy` | Alias for website |
| Terms | `/terms` | Alias for website |
| Health | `/health` | Health check endpoint |

### Roles

| Role | Permissions |
| --- | --- |
| **viewer** | Read-only access to all admin views |
| **manager** | Full access except user management, job/lead deletion, session revocation |
| **super_admin** | Full access including user CRUD, deletion, session revocation |

### Security Features

- bcrypt password hashing (cost 12) with automatic SHA-256 migration
- Session cookies (httpOnly, SameSite, configurable secure flag)
- Per-IP brute-force throttle (5 failed attempts → lockout)
- Session epoch for revocation (increment to invalidate all cookies)
- Environment variable lock/unlock with password
- Audit logging of all admin actions (toggleable)
- Security headers: CSP, HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy

---

## 25. Cost and Resource Usage

```text
Page Details    → Apify actor usage
    ↓
Post Scraping   → Apify actor usage
    ↓
Comment Scraping→ Apify actor usage
                 — gated by MIN_COMMENTS (only qualifying posts)
    ↓
Keyword Filter  → Free (offline, CPU only)
    ↓
AI Analysis     → Gemini API usage (per comment, only MATCHED/NO_FILTER)
```

- **Apify**: Actors consume credits/usage. Cost minimized: only qualifying posts are comment-scraped.
- **Gemini**: Billed per request/token — each analyzed comment is one call. Keyword filter saves cost by pre-filtering.
- **MongoDB**: No cost for local/docker usage.

---

## 26. Security

### Implemented

- Admin authentication (bcrypt, session cookies, brute-force throttle)
- Role-based access control (viewer / manager / super_admin)
- Security headers middleware (CSP, HSTS, X-Frame-Options, etc.)
- CSRF protection (Origin/Referer validation on state-changing requests)
- Input validation (Pydantic models, URL canonicalization, ObjectId validation)
- HTML escaping in frontend (`esc()` in `app.js` and `admin.js`)
- CORS (configurable origins, default same-origin only)
- Error messages never leak tokens or stack traces
- Audit logging of admin actions with secret redaction
- Environment variable lock/unlock with password protection
- **Log secret redaction** — API keys, tokens, passwords, MongoDB URIs, and bearer tokens are automatically redacted in all log output (`app/log_parser.py`)
- **Multi-tenant data isolation** — All customer data is organization-scoped
- **Invitation token hashing** — SHA-256 hashed tokens with expiration
- **Rate limiting** — Per-user API rate limiting (30 req/min), contact form rate limiting (1/min per IP)

### Not Implemented (Recommended Before Production)

- API rate limiting (provider 429s are handled)
- Secret rotation / managed secret storage
- Transport security (plain HTTP by default; use reverse proxy for TLS)
- PII controls (phone/email stored without encryption)
- Log retention / archival policy (recommended: delete logs > 30 days)

---

## 27. Performance

- **Async API layer** — Motor async DB calls; long work offloaded with `asyncio.to_thread` / daemon threads
- **Background jobs with live status** — In-process tasks keyed by run/page/post prevent duplicate work
- **Dedicated DB indexes** — Unique + lookup indexes on all 11 collections; idempotent `_create_index_safe()` wrapper handles IndexKeySpecsConflict on restart
- **Provider-side timeouts** — 8-minute Apify run timeout; httpx timeouts for Gemini
- **Scrape limits** — `resultsLimit` on every actor; posts capped at `max_posts`, comments capped per post
- **Cost-aware gating** — Only qualifying posts are comment-scraped
- **Caching of results** — Pages/posts/comments stored once and served from Mongo
- **Frontend polling** — 1.5s during runs, 2–4s for background jobs; stops when finished
- **API rate limiting** — 30 requests per minute per user (in-memory)
- **Configurable log levels** — Suppress noisy third-party loggers; control file/console verbosity via env vars

---

## 28. Logging and Monitoring

### Configuration

Three environment variables control log verbosity (default: `INFO` for all):

| Variable | Default | Effect |
|----------|---------|--------|
| `LOG_LEVEL` | `INFO` | Root logger level |
| `LOG_FILE_LEVEL` | `INFO` | File handler level |
| `LOG_CONSOLE_LEVEL` | `INFO` | Console handler level |

Third-party loggers (pymongo, motor, urllib3, httpcore) are **always forced to WARNING** regardless of configuration — this prevents DEBUG flood from database drivers.

### Handlers

| Handler | Format | Rotation |
|---------|--------|----------|
| **Console** | `HH:MM:SS LEVEL  name: message` | N/A |
| **File** (`logs/app.log`) | `HH:MM:SS LEVEL  name: message` | 10 MB × 5 files (RotatingFileHandler) |

### Structured Log Parser (`app/log_parser.py`)

Raw log lines are parsed into structured dicts with:

| Field | Description |
|-------|-------------|
| `timestamp` | `HH:MM:SS` |
| `level` | `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL` |
| `module` | Logger name (e.g. `app.pipeline.comment_ai`) |
| `source` | Category: Database, Apify, AI, Search, Authentication, System, API, Application |
| `event` | Concise event summary (first sentence, max 120 chars) |
| `message` | Full message with secrets redacted |
| `error_type` | Exception class if present |
| `run_id`, `request_id`, `job_id` | Correlation IDs |
| `endpoint`, `http_method`, `http_status` | HTTP context |
| `duration`, `collection`, `platform` | Additional structured fields |
| `stack_trace` | Grouped multiline tracebacks |
| `raw` | Original line with secrets redacted |

### Secret Redaction

Sensitive values are **automatically redacted** in all log output:

- API keys (`APIFY_API_TOKEN`, `GEMINI_API_KEY`)
- Passwords and tokens (`password=`, `token=`, `SESSION_SECRET`)
- MongoDB URIs (`mongodb://admin:***@host`)
- Bearer tokens (`Authorization: Bearer ***`)
- Dict keys matching `password`, `token`, `secret`, etc.

### Admin Log Viewer

The admin panel (`/admin` → Logs) provides a production-grade log console:

- **Structured table** with TIME, LEVEL, MODULE, MESSAGE, SOURCE columns
- **Severity badges**: CRITICAL (red), ERROR (red), WARNING (amber), INFO (blue), DEBUG (gray)
- **Source badges**: Database (blue), Apify (teal), AI (violet), Search (green), Authentication (amber)
- **Summary cards** with real-time level counts
- **Search** across all log fields
- **Filters**: level, source, module dropdowns
- **Details modal** on row click with all structured fields, full message, stack trace, raw log
- **Copy**: JSON, raw log, or message to clipboard
- **Auto-refresh**: Off / 10s / 30s / 60s intervals
- **Autoscroll** with "Jump to latest" bar
- **Pagination** with "Load more"
- **CSV export** of filtered logs

### Docker Log Rotation

```yaml
logging:
  driver: json-file
  options:
    max-size: "10m"
    max-file: "3"
```

---

## 29. Testing

```bash
# All tests (excluding scratch/)
python -m pytest --ignore=scratch -v

# Log parser tests only
python -m pytest tests/test_log_parser.py -v

# HTTP/API endpoint tests only
python -m pytest tests/test_http_endpoints.py -v

# Quick summary
python -m pytest --ignore=scratch -q
```

**880 tests** covering:

| Category | Count | Test File |
|----------|-------|-----------|
| URL detection + canonicalization | 56 | `test_url_search.py` |
| Lead qualification rules | 28 | `test_qualification.py` |
| Authentication + sessions | 32 | `test_auth.py` |
| Admin panel views | 45 | `test_admin_panel.py` |
| Security (CSRF, headers, injection) | 38 | `test_security.py` |
| Comment filter pipeline | 42 | `test_comment_filter.py` |
| AI intelligence analysis | 35 | `test_ai_intelligence.py` |
| Lead lifecycle state machine | 30 | `test_lead_lifecycle.py` |
| Post normalization | 25 | `test_post_normalization.py` |
| Settings CRUD | 28 | `test_settings.py` |
| Environment variables | 32 | `test_envvars.py` |
| Analytics export | 26 | `test_analytics_export.py` |
| Admin panel hardening | 40 | `test_admin_panel_hardening.py` |
| Admin data integrity | 35 | `test_admin_data.py` |
| Scalability + production readiness | 30 | `test_scalability_production.py` |
| HTTP/API endpoints (auth, roles, validation) | 76 | `test_http_endpoints.py` |
| **Log parser (structured parsing, redaction, multiline)** | **117** | **`test_log_parser.py`** |
| MongoDB index idempotency | 6 | `test_http_endpoints.py` |

---

## 30. Troubleshooting

| Issue | Solution |
| --- | --- |
| URL search returns no results | Check run's `error_meta` / logs; `BLOCKED` → wait and retry; `ACTOR_FAILED` → check Apify console |
| Page details fail but run works | Graceful fallback creates URL-derived page doc; `details_error` explains why |
| AI analysis fails | Without `GEMINI_API_KEY` runs on rules; 429 → circuit breaker pauses 10 min |
| Database connection fails | Check Mongo is running; verify `MONGO_URI` |
| Frontend cannot connect | Must be served from same origin as API (FastAPI static mount does this) |
| Runs stuck in "running" | Stale after 30 min; UI shows Retry button |
| Maintenance mode active | Admin panel still accessible; user app shows maintenance page |
| Logs flooded with DEBUG noise | Third-party loggers forced to WARNING; set `LOG_LEVEL=INFO` |
| Admin panel stuck on loading | Check browser console for JS errors; verify `/api/admin/logs` returns structured data |
| Index creation crash on restart | `_create_index_safe()` handles IndexKeySpecsConflict automatically |

---

## 31. Developer Guide

### Adding a New Platform

1. `app/social/url_detector.py` — add host aliases, canonicalizer, platform in `SUPPORTED_PLATFORMS`
2. `app/social/scrapers.py` — subclass `SocialMediaScraper`, register in `get_scraper()`
3. `app/config.py` — add `<PLATFORM>_ACTOR_ID` setting

### Adding a New Lead Field

1. Add to model in `app/db/models.py`
2. Extract in `app/pipeline/comment_ai.py`
3. Expose in `app/api/routes/search.py` (GET endpoint + CSV columns)
4. Render in `app/static/app.js` (card + modal)

### Adding a New API Endpoint

1. Add route in `app/api/routes/search.py` or `admin.py`
2. Use `_start(key, fn)` for background work
3. Use `get_async_db()` / `get_sync_db()` for DB access
4. Use `_serialize()` for responses

### Adding a New Admin View

1. Add view definition in `app/settings/registry.py` (`KNOWN_VIEWS`)
2. Add API endpoint in `app/api/routes/admin.py`
3. Add render function in `app/static/admin.js`
4. Add nav item in sidebar

### Adding a New CMS Content Type

1. Add collection definition in `app/cms/models.py`
2. Add CRUD service methods in `app/cms/service.py`
3. Add admin API routes in `app/api/routes/admin_cms.py`
4. Add public API route in `app/api/routes/public_website.py`
5. Add admin view in `app/static/admin.js`

### Adding a New SaaS Feature

1. Add data model in `app/db/saas_models.py`
2. Add service logic in `app/billing/` or new module
3. Add API routes in `app/api/routes/billing.py` or new router
4. Add admin management view in `app/static/admin.js`
5. Add entitlement check in `app/billing/entitlements.py`

---

## 32. Git Workflow

```bash
git checkout main && git pull origin main
git checkout -b feature/new-feature
git add . && git commit -m "Add new feature"
git push origin feature/new-feature
# Open pull request against main
```

Keep `.env` and `logs/` out of commits (gitignored).

---

## 33. Architecture Summary

```text
USER
  ↓
FRONTEND (dashboard — app/static/index.html + app.js)
ADMIN    (admin center — app/static/admin.html + admin.js)
WEBSITE  (public — app/static/website.html + signup.html + contact.html)
  ↓
DESIGN SYSTEM (tokens.css + components.css + theme.js)
  ↓
AUTH (app/auth/ — bcrypt, sessions, roles, tenant context)
  ↓
BACKEND (FastAPI — app/api/routes/)
  ├── Product Routes (search, pages, posts, comments, leads, exports)
  ├── Admin Routes (dashboard, jobs, leads, analytics, platforms, AI, settings, users)
  ├── CMS Routes (pages, FAQ, testimonials, navigation, media, contact)
  ├── Organization Routes (workspace, team, invitations)
  ├── Billing Routes (plans, subscription, usage, invoices, webhooks)
  └── Public Routes (config, theme, pricing, FAQ, contact)
  ↓
URL SEARCH PIPELINE (app/social/url_search.py — background thread)
  ↓
URL DETECTOR (platform detection + canonicalization)
  ↓
PLATFORM SCRAPER (app/social/scrapers.py)
  ↓
APIFY ACTORS (pages / posts / comments + configurable IG/YT/LI)
  ↓
LEAD ENGINE (app/agent/search.py — normalizers + qualification + scoring)
  ↓
KEYWORD FILTER (app/pipeline/comment_filter.py — 4 modes, 10+ categories)
  ↓
AI PIPELINE (app/pipeline/comment_ai.py: rules → Gemini)
  ├── AI Models Service (model registry, pricing, token limits)
  ├── AI Prompt Service (version-controlled prompts with rollback)
  └── AI Usage Service (cost tracking per request)
  ↓
LEAD LIFECYCLE (app/pipeline/lead_lifecycle.py — state machine, notes, follow-ups)
  ↓
SaaS LAYER
  ├── Organizations (multi-tenant workspaces)
  ├── Subscriptions (plans, trials, upgrades)
  ├── Usage Tracking (atomic quota counters)
  ├── Entitlements (feature flags, quota enforcement)
  ├── Invitations (team invites with token hashing)
  └── Invoices (billing history)
  ↓
CMS SERVICE (app/cms/service.py — pages, FAQ, testimonials, navigation, media)
  ↓
MONGODB (20+ collections)
  ↓
API RESPONSE (serialized docs, live status)
  ↓
FRONTEND (page → posts → comments/leads → lead modal → CSV export · report page · workspace/team/billing modals)
ADMIN    (dashboard → jobs → leads → analytics → platforms → AI → settings → users → CMS → plans → subscriptions)
WEBSITE  (hero → features → how-it-works → pricing → FAQ → CTA → footer)
  ↓
USER
```

LeadAI is a complete, working SaaS lead-intelligence platform: paste one social URL, and real data flows from the platform's Apify actors through keyword filtering, deterministic and AI-based lead qualification, lifecycle management, persistent storage with per-run deduplication, and a live dashboard that takes you from a single page to a scored, exportable list of buyers and sellers — all backed by a full admin control center, CMS-managed public website, multi-tenant SaaS infrastructure with subscription plans, team management, and usage-based billing.
