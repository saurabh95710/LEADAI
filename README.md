# LeadAI — AI-Powered Social Lead Intelligence SaaS

[![CI](https://github.com/saurabh95710/LEADAI/actions/workflows/ci.yml/badge.svg)](https://github.com/saurabh95710/LEADAI/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com)
[![MongoDB](https://img.shields.io/badge/MongoDB-6.0+-47A248.svg)](https://www.mongodb.com)

**LeadAI** is a multi-tenant B2B SaaS platform:

1. It collects posts and comments from Facebook Pages, Instagram business profiles, YouTube channels and LinkedIn company pages using **Apify** actors.
2. It reads every comment with **Google Gemini** to find people who want to buy, and picks out their phone, email and budget.
3. It hands the qualified leads to your sales team: assignment rules, follow-ups, SLA timers, exports and outbound webhooks.

The platform includes:
- **Five portals:** the public website, the customer app, Org Admin, Super Admin and the Partner Portal.
- **Payments:** Razorpay and Stripe, with every payment confirmed by a Super Admin.
- **Messaging:** email over SMTP, SMS through Twilio, Fast2SMS or any webhook, plus a durable outbox.
- **Partner and reseller program:** with a commission ledger.
- **Global Settings:** one page where every platform setting is versioned and can be rolled back.

---

## Architecture

```
          Public website (/) · Customer app (/user) · Org Admin (/org-admin)
          Super Admin (/superadmin) · Partner Portal (/partner) · REST API (/api/v1)
                                        |
                                   HTTPS (Cloudflare)
                                        |
                     +------------------v-------------------+
                     |  FastAPI app (uvicorn)               |
                     |  - session scopes per portal         |
                     |  - tenant isolation, roles           |
                     |  - CSRF, CSP, HSTS, rate limits      |
                     +---------+--------------------+-------+
                               |                    |
                +--------------v-------+    +-------v--------------+
                | MongoDB 6+           |    | Job queue            |
                | (Motor async + sync) |    | (Redis, in-memory    |
                | tenant data, outbox, |    |  fallback)           |
                | encrypted secrets    |    +-------+--------------+
                +----------------------+            |
                                       +------------v-----------------+
                                       | Background workers           |
                                       | - Apify scraping             |
                                       | - Gemini analysis + rules    |
                                       | - lead assignment & SLAs     |
                                       | - email / SMS outbox         |
                                       | - billing & trial lifecycle  |
                                       +------------------------------+
```

---

## Portals

Each portal has its own session scope: a customer session can't open an admin portal, and the reverse.

| Portal | Address | Who | Main features |
|---|---|---|---|
| **Website** | `/`, `/pricing`, `/contact`, `/signup`, `/partners` | Anyone | Marketing pages managed from the CMS, and self-serve sign-up. A new customer gets a **free trial at once** (default 3 days, limited tokens; set in Super Admin → Free trials). |
| **Customer app** | `/user` (also `/dashboard`) | Organization members | Search by page or post link, see every post and comment, AI lead scoring, lead pipeline (status, notes, follow-ups), CSV / Excel downloads. |
| **Org Admin** | `/org-admin` | Organization owners and admins | Team members and invitations, roles and permissions, lead assignment rules, webhooks, API keys, billing and invoices. |
| **Super Admin** | `/superadmin` (sign in at `/login?superadmin=1`) | The platform owner (`SUPERADMIN_EMAIL`) and platform staff (manager, viewer) | Organizations, users, plans and payments, free trials, messaging, AI management, the scraping engine, **Global Settings**, Environment, health, audit log. |
| **Partner Portal** | `/partner` (sign in at `/login?partner=1`) | Approved affiliates and resellers | Referral links (`/r/{code}`), deal registration, commissions, wallet and payouts. |

Every portal has a **Guide** button with interactive walkthroughs ([docs/INTERACTIVE_GUIDES.md](docs/INTERACTIVE_GUIDES.md)). The old `/admin` console is merged into `/superadmin`; old links redirect.

---

## How it works

### Collecting
- Facebook, Instagram, YouTube and LinkedIn links are recognised, cleaned of tracking parameters and checked. Personal profiles, groups, stories and reels are refused.
- Scraping uses LeadAI's Apify token or the customer's own key (bring your own key, stored encrypted). A customer key never falls back to the platform key.
- Limits per search (posts, comments per post) and per plan are enforced on the server.

### Finding leads
- Gemini reads each comment for buying intent, budget, urgency, location and contact details, and the result becomes a 0–100 lead score.
- When Gemini is switched off, fails or reaches its per-search call limit, keyword rules score the comment instead.
- Emoji-only, link-only and very short comments are skipped before any AI call.
- How comments are scored and what is detected are set in Global Settings → Lead scoring / Comment detection.

### Working the leads
- Assignment rules (round-robin or rule-based) give each lead an owner and an SLA due time. A background sweep flags breached SLAs and overdue follow-ups.
- Leads sent in through the public API (`/api/v1`) are matched against existing ones by phone, email and author, and duplicates are merged.

### Billing
- **Razorpay** (UPI, cards, net banking, wallets) and **Stripe**. Global Settings → Payments picks which one takes new payments: auto, Razorpay or Stripe.
- With no keys set, a test-only mock checkout page is used and no real money is collected.
- Webhooks are verified by signature and processed once. Each provider has its own address, so renewals and refunds keep working after you switch provider:
  - `/api/billing/webhook/razorpay`
  - `/api/billing/webhook/stripe`
- A verified payment never starts a plan by itself: a Super Admin confirms it in Subscriptions.

### Messaging
- Email goes over SMTP (SSL on port 465 or STARTTLS on 587). SMS goes through Twilio, Fast2SMS or any webhook.
- Automatic messages:
  - welcome email (and optional SMS) on sign-up;
  - optional sign-in alerts;
  - password reset by email link or SMS code.
- Without a mail server, emails wait in the outbox (Super Admin → Messaging). One-time links (password reset, invitations) are never stored in the outbox, so waiting emails are never re-sent later.

### Partners
- First- or last-touch attribution with configurable windows; fixed, percentage and hybrid commissions; payouts reviewed by a Super Admin.
- Refunds and chargebacks reverse commissions in proportion.

### Security
- Root credentials (`SUPERADMIN_EMAIL`, `SUPERADMIN_PASSWORD`, `SESSION_SECRET`) come from the environment only and can never be changed from the app.
- Payment, email and SMS credentials:
  - can be entered by the Super Admin in the app, Super Admin only and audited;
  - are stored encrypted with Fernet (AES-128-CBC + HMAC-SHA256) using `API_KEY_ENCRYPTION_KEY`;
  - override `.env`, and are never sent back to the browser.
- The Environment page (server variables) sits behind a separate **guard password**, stored as a bcrypt hash.
- Every admin action is written to the audit log, and audit logging can't be switched off.

---

## Global Settings

Super Admin → **Global Settings** (`/superadmin#/settings`) holds every platform-wide setting. Every save is versioned and can be rolled back, and the settings can be exported and imported as JSON. Changes apply within seconds without a restart.

| Section | Tabs |
|---|---|
| Brand & website | General (name, company, support, footer links, regional defaults) · Branding (logos, colours, font, theme, sign-in page) · SEO & sharing |
| Leads engine | AI engine · Lead scoring · Comment detection · Search limits · Platforms & features · Leads & downloads |
| Communication | Email & alerts (mail server setup with a test button, automatic emails, sender name, reply-to, footer) |
| Operations | Payments (provider, keys, webhook URLs, key check) · Security · Operations (maintenance mode, automation) |

The product name, logo and colours set here are used everywhere: the website, the sign-in page, every app and every email.

---

## Quick start

### 1. Prerequisites
- Python 3.11+
- MongoDB 6.0+ (local or Atlas)
- Redis 7+ (optional; an in-memory queue is used without it)
- An Apify API token and a Google Gemini API key

### 2. Install
```bash
git clone https://github.com/saurabh95710/LEADAI.git
cd LEADAI
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# Linux / macOS:
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure
```bash
cp .env.example .env
```

The minimum `.env`:
```ini
MONGO_URI=mongodb://localhost:27017
MONGO_DB_NAME=LeadAI
SESSION_SECRET=<python -c "import secrets; print(secrets.token_hex(32))">
SUPERADMIN_EMAIL=admin@yourdomain.com
SUPERADMIN_PASSWORD=<a strong password>
API_KEY_ENCRYPTION_KEY=<a long random passphrase; encrypts stored keys — see the note below>

GEMINI_API_KEY=<your Gemini key>
APIFY_API_TOKEN=<your Apify token>

PUBLIC_BASE_URL=http://localhost:8000   # your https:// address in production; used in emailed links
SESSION_COOKIE_SECURE=false             # true in production (HTTPS)
TRUST_PROXY_HEADERS=false               # true behind your own proxy / Cloudflare
```

`.env` is read at start-up, and real environment variables win over it.

> **`API_KEY_ENCRYPTION_KEY`:** set it once, before keys are stored, and never change it. Without it, the key is derived from `SESSION_SECRET`. Setting or changing it later means every stored key (customer Apify / Gemini keys, payment, email and SMS credentials) must be entered again.

Payment and email credentials can go in `.env` too:
- **Email:** `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`
- **Payments:** `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`

The easier route is Super Admin → Global Settings → **Payments** and **Email & alerts**: enter the credentials there and test them on the spot. See [docs/PAYMENTS_AND_EMAIL.md](docs/PAYMENTS_AND_EMAIL.md) and the comments in [.env.example](.env.example).

### 4. Run
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Then open:
- Website: http://localhost:8000/
- Customer app: http://localhost:8000/user
- Org Admin: http://localhost:8000/org-admin
- Super Admin: http://localhost:8000/superadmin (sign in at `/login?superadmin=1` with `SUPERADMIN_EMAIL` / `SUPERADMIN_PASSWORD`)
- Partner Portal: http://localhost:8000/partner

### 5. Docker
```bash
docker compose up -d --build   # app on :8000, MongoDB 7 on :27017
```

---

## Deployment

Pushing to `main` runs CI (`.github/workflows/ci.yml`: ruff, mypy and the test suite against MongoDB and Redis). When it passes, CI deploys that exact commit to the VPS: it pulls it into `~/leadai` and runs `scripts/deploy_vps.sh`, which installs dependencies, restarts the app and waits for `/health`.

The server's `.env` lives in `~/leadai/.env` and is not touched by deploys. To restart after editing it, run `cd ~/leadai && bash scripts/deploy_vps.sh`.

Static files are versioned (`?v=` on every script and stylesheet), so browsers pick up a deploy immediately. More in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

---

## Tests and checks

```bash
pytest -q -p no:cacheprovider                        # the whole suite (~12 minutes)
pytest tests/test_razorpay_billing.py -q             # one area
ruff check app tests scripts                         # lint
mypy app --ignore-missing-imports --no-strict-optional
python scripts/smoke_test.py --in-memory             # end-to-end smoke test, throwaway database
```

Tests use an in-memory MongoDB (mongomock). Values that exist only in `.env`, such as the mail server and payment keys, are not loaded during tests, so a test never sends real email or charges a card.

---

## Scripts

| Script | Purpose |
|---|---|
| `scripts/smoke_test.py --in-memory` | End-to-end check of the main subsystems against a throwaway database. |
| `scripts/backup_mongodb.py --output-dir backups/` | Compressed, checksummed backup (`--retention-days`, `--collections`). |
| `scripts/restore_mongodb.py --backup-dir <dir> --dry-run` | Verify a backup; add `--confirm` to restore. |
| `scripts/migrate_social_collections.py --verify` | Social-collection migration (`--dry-run` / `--apply`). |
| `scripts/rollback_social_collections.py` | Undo that migration. |
| `scripts/cleanup_legacy_collections.py` | Tidy legacy collections (dry run by default; `--apply`). |
| `scripts/deploy_vps.sh` | Run on the server by CI: install, restart, health check. |

---

## Documentation

- [Admin portals](docs/ADMIN_PORTALS.md): roles, permissions and every portal page
- [Payments and email setup](docs/PAYMENTS_AND_EMAIL.md): Razorpay, Stripe, SMTP
- [Messaging](docs/MESSAGING.md): email, SMS, automatic messages, password reset by SMS
- [Interactive guides](docs/INTERACTIVE_GUIDES.md): the in-app walkthroughs
- [API coverage and own keys](docs/API_COVERAGE.md): platform vs customer Apify / Gemini keys
- [Architecture](docs/ARCHITECTURE.md) · [REST API](docs/API.md) · [Deployment](docs/DEPLOYMENT.md)
- [Security](docs/SECURITY.md) · [Compliance (GDPR / CCPA)](docs/COMPLIANCE.md) · [Disaster recovery](docs/DISASTER_RECOVERY.md)
- [Partner program](docs/PARTNERS.md) · [Unit economics](docs/UNIT_ECONOMICS.md) · [Known limitations](docs/KNOWN_LIMITATIONS.md)
- [Changelog](CHANGELOG.md) · [Contributing](CONTRIBUTING.md)

---

## License

Released under the [MIT License](LICENSE). Copyright © 2026 LeadAI.
