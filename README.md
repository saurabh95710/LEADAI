# LeadAI - AI-Powered Social Lead Generation SaaS

[![CI](https://github.com/saurabh95710/LEADAI/actions/workflows/ci.yml/badge.svg)](https://github.com/saurabh95710/LEADAI/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com)
[![MongoDB](https://img.shields.io/badge/MongoDB-6.0+-47A248.svg)](https://www.mongodb.com)

**LeadAI** is an enterprise-grade multi-tenant B2B SaaS platform that monitors social media (Facebook, Instagram, YouTube, and LinkedIn), extracts comments from target posts and channels, and uses **Google Gemini AI** to identify high-intent buyer inquiries, extract contact information, and automate lead delivery to your CRM.

---

## Architecture Overview

```
                      +---------------------------------------+
                      |   Client Web Browser / Public API     |
                      +-------------------+-------------------+
                                          |
                                    HTTPS / WSS
                                          |
                      +-------------------v-------------------+
                      |         FastAPI Backend Cluster       |
                      |  - Session / API Key Authentication   |
                      |  - Multi-tenant Isolation Gate        |
                      |  - Rate Limiter (Redis / In-memory)   |
                      +---------+-------------------+---------+
                                |                   |
               +----------------v-----+       +-----v----------------+
               | MongoDB (Async/Sync) |       |  Redis Job Queue     |
               | - Multi-tenant DB    |       |  (Durable Background |
               | - Compound Indexes   |       |   Task Supervisor)   |
               +----------------------+       +-----+----------------+
                                                    |
                                      +-------------v----------------+
                                      |   Async Worker Pool          |
                                      |   - Apify Scraping Actors    |
                                      |   - Gemini AI Pipeline       |
                                      |   - Lead Deduplication       |
                                      |   - Webhooks & CRM Sync      |
                                      +------------------------------+
```

---

## Key Features

- **Multi-Platform Social Listening**: Scrapes posts, videos, and comments from Facebook Pages, Instagram, YouTube channels, and LinkedIn company pages (each non-Facebook platform is enabled by setting its Apify actor ID).
- **AI Intent & Lead Classification**: Powered by Google Gemini to identify buyer intent (`purchase_inquiry`, `pricing_inquiry`), budget, location, and contact details (phone, email, WhatsApp).
- **Strict Multi-Tenancy**: Built-in tenant isolation with fail-closed RBAC, scoped queries, and cross-tenant tamper protection.
- **Account Security**: bcrypt passwords, per-IP throttling and account lockout, revocable sessions with idle timeout, and TOTP two-factor sign-in.
- **Recurring & Scheduled Scans**: Automatically scan target channels and social profiles on an hourly or daily basis.
- **Cross-Run Lead Deduplication**: Merges duplicate prospect contacts across multiple runs and platforms.
- **Lead Assignment & SLA Timers**: Automated round-robin or criteria-based lead routing with SLA response countdowns.
- **CRM Integrations & Webhooks**: Real-time HMAC-SHA256 signed webhooks, HubSpot and Zoho CRM connectors, and formula-safe Excel (XML spreadsheet) / CSV exports.
- **Developer API**: Scoped Public REST API v1 (`/api/v1/leads`, `/api/v1/search`) with SHA-256 hashed API keys and rate limiting.
- **Billing**: Razorpay (UPI, NetBanking, Cards), Stripe, or a mock provider for development. Payments are verified by signed webhooks, and a subscription becomes active only after Super Admin confirmation. Plan tokens, renewals, and full or partial refunds and chargebacks are recorded on invoices.
- **Unit Economics Engine**: Live token costs, scraping compute metrics, and margin tracking per tier.
- **LeadAI-provided or your own API keys**: For Apify and for Gemini separately, a customer either uses LeadAI's (included in the price) or brings its own key (cheaper; it pays Apify / Google directly). Any mix; the price follows the choice. Keys are encrypted and never shown again. A customer's failing key never falls back to LeadAI's. See [API coverage](docs/API_COVERAGE.md).
- **Partner / Reseller / Affiliate Program**: Public application with Super Admin review, and a dedicated Partner Portal (`/partner`).
  - Referral and campaign links with first- or last-touch attribution.
  - Reseller customer onboarding (direct or through onboarding links).
  - Configurable commissions (percentage, fixed or hybrid; one-time or recurring; caps; per plan, tier or partner), reversed automatically on refunds, chargebacks and cancellations.
  - A ledger-based wallet and a reviewed payout workflow.
  - Coupons, partner pricing and automatic tiers.
  - Analytics reconciled with invoices, a marketing center, a fraud review queue, and read-only partner API keys. See [Partner Program](#partner-program).

---

## Administration & User Portals

| Portal | URL | Who Can Access | API Prefix | Description |
|---|---|---|---|---|
| **Super Admin Portal** | `/superadmin` (sign in at `/login?superadmin=1`) | The Super Admin (`SUPERADMIN_EMAIL`) — holds **every** permission | `/api/super-admin` | Sign in as any organization (Owner rights) or partner, every API key and webhook, organizations, users, demos, plans, subscriptions, payments and refunds, tokens, AI, website CMS, audit, security, **Partners & Resellers** management, and the sign-in email and password of any organization owner, admin, user, partner or staff account. |
| **Platform Staff Console** | `/admin` (sign in at `/login?admin=1`) | Platform staff (`operations_admin`, `billing_admin`, `support_admin`, …) | `/api/admin` | Operational console with role-based platform permissions. |
| **Organization Admin Portal** | `/org-admin` | Customer Org Owners & Admins (`owner`, `admin`) with confirmed subscription | `/api/org-admin` | Member management (including members' sign-in email and password), RBAC, assignment rules, webhooks, API keys, org audit logs. |
| **Workspace Dashboard** | `/user` (or `/dashboard`; `/` is the public website) | All verified organization members (`owner`, `admin`, `manager`, `member`, `viewer`) | `/api` | Scrape execution, social lead pipeline, CRM views, note taking, follow-up reminders. |
| **Partner Portal** | `/partner` (sign in at `/login?partner=1`) | Approved affiliates and resellers; applicants see only their application status | `/api/partner/v1` | Dashboard, tasks from LeadAI, analytics, referrals, customers, campaigns, coupons, marketing center, commissions, wallet, payouts, notifications, API keys. |
| **Public Website** | `/`, `/website`, `/pricing`, `/contact`, `/request-demo`, `/partners` | Public / Anonymous | `/api/public` | Product marketing, pricing, demo requests, contact, and the Partner Program application. |

Each portal has its own session scope. A partner session can't reach organization or Super Admin APIs, and a customer session can't reach the Partner Portal. Blocked attempts are logged.

For detailed portal documentation, see [docs/ADMIN_PORTALS.md](docs/ADMIN_PORTALS.md).

---

## Partner Program

Partners are ordinary user accounts that sign in with a partner session. The customers they bring are ordinary organizations, and they go through the normal demo → subscription → payment flow. There is no second customer, billing or payment system.

```
Website /partners → application → Super Admin approval → Partner Portal
     → referral link /r/{code} (or reseller onboarding) → demo request → demo approval
     → checkout → verified payment → Super Admin confirmation → commission (linked to the invoice)
     → qualified → approved → payable → payout (review → approve → processing → paid)
```

- **Attribution**: a signed cookie records the first-touch and last-touch partner within a configurable window (7, 30, 60, 90 days or custom). Each organization belongs to one partner. Self-referrals, duplicate conversions, conflicting attribution and tampered cookies are blocked or flagged.
- **Money**: wallet balances (pending, available, processing, paid, reversed) are derived from commission records and the ledger, so partners can't edit them. Refunds and chargebacks recorded on an invoice, by a Super Admin or a provider webhook, reverse the related commission in proportion. A commission that was already paid becomes a clawback against the next payout.
- **Fraud review**: suspicious activity opens a flag that *holds* the related commissions without changing them, until a Super Admin dismisses it, confirms it, or invalidates the referral.
- **Selling**: the **Sell LeadAI** page shows the live plans with each partner's customer price, their commission per plan, and plan-specific share links. Partners register **deals** (prospects), which the Super Admin approves to protect the partner's claim.
- **Tasks**: the Super Admin assigns tasks to one partner or to all of them, with details, a due date and a priority. The partner does the work and submits a note on what they did. The Super Admin then approves the task or sends it back with feedback.
- **Oversight**: the Super Admin sees an **activity log** of everything partners do (sign-ins, every portal and API request, every action) with filters and CSV export, can see and end partner sessions, and has a full audit trail per partner.
- **Super Admin → Partners & Resellers** has these screens: applications, analytics, activity log, partners, tasks, deals, customers and referrals, commissions, refunds and reversals, wallets, payouts, fraud review, commission rules, tiers, partner pricing, coupons, marketing assets, and program settings.

Full details: [docs/PARTNERS.md](docs/PARTNERS.md).

---

## Quick Start

### 1. Prerequisites
- **Python**: 3.11+ (recommended 3.12)
- **MongoDB**: 6.0+ (local instance or MongoDB Atlas)
- **Redis**: 7.0+ (optional; set `REDIS_URL` and `pip install redis` for the durable queue and distributed rate limits, otherwise an in-memory fallback is used)
- **Apify API Token**: [https://apify.com](https://apify.com)
- **Google Gemini API Key**: [https://aistudio.google.com](https://aistudio.google.com)

### 2. Installation
```bash
# Clone repository
git clone https://github.com/saurabh95710/LEADAI.git
cd LEADAI

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure Environment
```bash
cp .env.example .env
```
Edit `.env` with at least the required values (see [`.env.example`](.env.example) for every option):
```ini
# Required
GEMINI_API_KEY=your_gemini_api_key
APIFY_API_TOKEN=your_apify_token
MONGO_URI=mongodb://localhost:27017
MONGO_DB_NAME=LeadAI
SUPERADMIN_EMAIL=you@example.com
SUPERADMIN_PASSWORD=a-long-unique-password   # or a bcrypt hash ($2b$...)
SESSION_SECRET=   # python -c "import secrets;print(secrets.token_urlsafe(48))"

# Production
PUBLIC_BASE_URL=https://app.yourdomain.com   # absolute links in emails and partner referral links
SESSION_COOKIE_SECURE=true                   # when served over HTTPS
TRUST_PROXY_HEADERS=true                     # behind a reverse proxy / load balancer

# Optional
# REDIS_URL=redis://localhost:6379/0
# INSTAGRAM_ACTOR_ID / YOUTUBE_ACTOR_ID / LINKEDIN_ACTOR_ID  (empty = platform disabled)
# SMTP_*            (email verification, invitations, notifications)
# Billing: Stripe (STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET) or Razorpay (BILLING_PROVIDER=razorpay,
#   RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, RAZORPAY_WEBHOOK_SECRET); otherwise the mock provider is used
#   (MOCK_PAYMENTS_ENABLED, BILLING_WEBHOOK_SECRET). Provider webhooks: POST /api/billing/webhook
# STORAGE_BACKEND=local | s3 | cloudinary   (media and partner marketing files; local files of the
#   marketing center are stored privately in data/partner_assets/)
```

> Only the Super Admin is configured through the environment. Organization admins and members are database accounts created via signup and invitation links.

### 4. Run Locally
```bash
# Start the web server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
Open your browser at [http://localhost:8000](http://localhost:8000).

| Sign in as | URL |
|---|---|
| Super Admin | `/login?superadmin=1` with the `SUPERADMIN_*` credentials |
| Customer (user dashboard) | `/login` — accounts come from approved demo requests and invitations |
| Organization owner / admin | `/login?admin=1`, which opens `/org-admin` |
| Partner | `/login?partner=1` — apply first at `/partners` |

The durable job queue worker starts in-process with the web server; no separate worker command is needed.

#### Or with Docker Compose
```bash
docker compose up -d --build   # API on :8000, MongoDB 7 bound to 127.0.0.1:27017
```
Set `MONGO_URI=mongodb://mongo:27017` in `.env` when running inside Compose.

### 5. Run Tests
```bash
# Run unit and integration test suite
pytest

# Lint and type-check (same as CI)
ruff check app tests scripts
mypy app --ignore-missing-imports --no-strict-optional

# End-to-end smoke test across all major subsystems (safe: in-memory database)
python scripts/smoke_test.py --in-memory
```

> **Note:** `pytest` always runs against an in-memory MongoDB. `scripts/smoke_test.py` uses the database configured in `.env` unless you pass `--in-memory`, so without that flag point it at a test database, never production.

**Current status:** 1491 automated tests pass. These cover every portal, cross-portal isolation, the partner lifecycle from referral through payout and refund, billing, and RBAC.

---

## Operational Scripts

| Script | Purpose |
|---|---|
| `scripts/backup_mongodb.py` | Gzip-compressed, checksummed backup (`--output-dir`, `--retention-days`, `--collections`). |
| `scripts/restore_mongodb.py` | Restore from a backup (`--backup-dir`; `--dry-run` to verify, `--confirm` to write). |
| `scripts/migrate_social_collections.py` | Migrate to platform-neutral social collections (`--dry-run` / `--apply` / `--verify`). |
| `scripts/rollback_social_collections.py` | Roll back the social collections migration. |
| `scripts/cleanup_legacy_collections.py` | Consolidate legacy collections (dry run by default; `--apply`). |
| `scripts/smoke_test.py` | Smoke test of health, dedup, scans, SLA, webhooks, CRM, API v1, billing, unit economics (uses the `.env` database — see the note above). |

See [docs/DISASTER_RECOVERY.md](docs/DISASTER_RECOVERY.md) for the full backup and restore runbook.

---

## Detailed Documentation

Comprehensive documentation is organized in the [`docs/`](docs/) directory:

- [System Architecture](docs/ARCHITECTURE.md) - High-level topology, queue service, and AI pipeline.
- [REST API Reference](docs/API.md) - Public API v1 and internal endpoints.
- [Portals Guide](docs/ADMIN_PORTALS.md) - Super Admin, Org Admin, and User Dashboard.
- [Production Deployment](docs/DEPLOYMENT.md) - Docker, Nginx, environment variables, and scaling.
- [Security Controls](docs/SECURITY.md) - RBAC, session management, tamper protection, and injection defense.
- [Compliance & Data Privacy](docs/COMPLIANCE.md) - PII retention, data subject deletion, and blocklists.
- [Partner Program](docs/PARTNERS.md) - Partner applications, attribution, commissions, payouts and security model.
- [Unit Economics](docs/UNIT_ECONOMICS.md) - Token costs, compute expenses, and pricing margins.
- [API Coverage](docs/API_COVERAGE.md) - LeadAI-provided vs own Apify / Gemini keys, pricing, switching rules and key security.
- [Disaster Recovery](docs/DISASTER_RECOVERY.md) - MongoDB backup, restore runbook, and failover steps.
- [Performance Report](docs/PERFORMANCE_REPORT.md) - Load time benchmarks and caching optimizations.
- [Performance Baseline](docs/PERFORMANCE_BASELINE.md) - Pre-optimization measurements.
- [Data Model Consolidation](docs/DATA_MODEL_CONSOLIDATION.md) - Collection layout and legacy migration notes.
- [Product Roadmap](docs/ROADMAP.md) - Feature implementation statuses and upcoming milestones.
- [Known Limitations](docs/KNOWN_LIMITATIONS.md) - Architecture boundaries, defaults, and platform limits.
- [Upgrade Notes](docs/UPGRADE_NOTES.md) - Migration guides and breaking changes.

---

## Contributing & License

- **Contributing**: Please review [CONTRIBUTING.md](CONTRIBUTING.md) for code standards and pull request workflows.
- **Changelog**: Detailed release notes are tracked in [CHANGELOG.md](CHANGELOG.md).
- **License**: Released under the [MIT License](LICENSE).
