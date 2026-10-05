# Changelog

All notable changes to LeadAI are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- **Partner / Reseller / Affiliate Program** (see `docs/PARTNERS.md`): public `/partners` application page; Super Admin Partner Management (applications, partners, commissions, payouts, rules, tiers, coupons, marketing assets, program settings); Partner Portal at `/partner` with permission-based modules; `partner` session scope; referral links and campaigns with signed first- and last-touch attribution; reseller onboarding through the existing demo flow; partner coupons applied at checkout; commissions from subscription confirmation and renewal; derived wallet with a ledger; payouts; read-only partner API keys; new platform permissions `partners.view` / `partners.manage`.

- **Partner commission engine v2**:
  - Commission lifecycle `pending → qualified → approved → payable → processing → paid`, plus `reversed`. Hybrid commissions, a cap per payment, and a qualification period per rule.
  - Proportional reversal on refund or chargeback; clawback of commissions that were already paid; reversal on cancellation during the qualification period.
  - Payout workflow with review, processing and failed states; payout schedule.
  - Coupon eligibility and commission basis; partner pricing; tier requirements, limits, permissions and automatic upgrade/downgrade.
  - Reseller onboarding links and a managed-customer limit.
  - Full referral funnel (checkout and payment stages) with conversion history.
  - Super Admin screens: customers & referrals (reassign, review), refunds & reversals, wallets, fraud review, partner pricing, and each partner's financial history.
- **Partner platform**:
  - Analytics reconciled with invoices (visitors, demos, revenue, commission by status, payouts, campaigns), with CSV export, a Super Admin analytics tab with partner and campaign filters, a leaderboard and a billing reconciliation check.
  - Fraud-flag review queue: flags hold commissions and never change them.
  - Marketing center with private file uploads, categories, per-partner visibility, email templates and download tracking.
  - Coupon and fraud notifications.
  - Partner API: an index endpoint, notifications readable with an API key, and key usage tracking.
- **Addresses:** `/` is now the public website (same as `/website`); the customer user panel is at **`/user`** (`/dashboard` still works); `/login` signs in to `/user`. Signed-in customers see "Open your dashboard" on the website. New `GET /api/auth/status` (always 200, no personal data).
- **All-portal audit** (every page of every portal opened in a real browser, light/dark and phone width):
  - Super Admin: "Free trials" (was "Demo Management") with a "Trial settings (days & tokens)" tab; new Subscriptions → Lifecycle automation, Payments → Invoices & refunds, Record renewal; drawers close on navigation; trial wording throughout.
  - Org Admin: keyword library tabs and duplicates fixed; Developer API keys screen (create / revoke); trial banners and dates; "Unlimited" limits; leads priority filter; comment qualification.
  - Customer app: open search no longer lost on reload; free-trial wording and expired-trial states; no made-up AI scores on unanalysed comments; viewers no longer hit forbidden calls; two-factor sign-in setup under Settings → Security.
  - Partner Portal & website: trial wording, notification links, coupon editing, theme switch on phones, pricing no longer shows a conflicting per-plan trial, contact topic preselect.
  - Platform console (/admin): global search, audit log, organization counts, subscription actions, trial organizations kept as trials when edited, Environment guard password can be set (Super Admin), staff sign-in goes to /admin, links to Super Admin → API keys, phone layout.
  - Backend: invoice / receipt downloads (`/api/billing/invoices/{number}/download|receipt`, own organization only); comment qualification in the Admin portal data API; developer API keys limited to documented scopes; full trial seats are a note, not an alarm; partner-onboarded customers get a correct set-your-password email; customer-facing "demo" wording is now "free trial"; shipped website copy updated once (admin edits kept).
- **Self-serve 3-day free trial** instead of demo requests waiting for approval:
  - Signing up on the website starts a free trial at once (3 days, the tokens set in Super Admin → demo settings, 500 by default) and signs the new owner straight in to their dashboard. The Super Admin gets an informational "New free trial" notice instead of an approval task.
  - The website, sign-up and login pages say "Start free 3-day trial" (CMS buttons labelled "Request a demo" are relabelled at runtime), and the dashboard counts down the free trial.
  - Super Admin → demo settings → "Self-serve free trial" (the old auto-approve switch) turns approval back on. Existing installs are switched to the 3-day self-serve trial once on startup; later changes there are kept.
- **Super Admin → API keys: change LeadAI's keys and customers' keys in one place**:
  - Keys LeadAI provides (Apify, Gemini): replace (tested first, refused if the provider rejects it unless forced), test, or go back to the server environment's key (refused when there is none). Saved keys are now stored encrypted; the old Environment panel writes them encrypted too.
  - Customers' own keys: every organization on its own keys, with a "Needs attention" filter. Set or replace a key for an organization (its admins are notified), test, remove, or switch each API between LeadAI and its own key.
- **Every comment of a post, qualified or not**, on the post's comments screen:
  - A new filter shows qualified comments (matched the comment filter and went to AI), not-qualified ones (kept, never sent to AI) or both. Each comment carries a Qualified / Not qualified badge, and the summary line counts both.
  - When the platform reports more comments than were collected (e.g. 35 on the post, 1 collected because of "Max / post"), a **Collect all comments** button collects the rest on demand, up to the plan's per-post limit.
  - Collecting again never pays for AI twice: comments Gemini already analyzed (same text) are skipped.
- **LeadAI-provided or your own API keys, chosen per API** (`docs/API_COVERAGE.md`):
  - For Apify and Gemini separately, an organization uses LeadAI's (included in the price) or brings its own key (cheaper). Starter: $49 all included, $37 own Apify, $41 own Gemini, $29 both. Prices come from `price_for(plan, coverage, cycle)`, and the Super Admin sets each plan's per-API amounts.
  - Where it's chosen: the pricing page toggles, checkout (`api_coverage`), Org Admin → **API keys & plan**, and the Super Admin organization page (including a forced switch with a reason and removing compromised keys).
  - Other screens: a coverage filter, column and dashboard counts in the Super Admin subscriptions, and per-option prices and commissions in the partner sales kit.
  - Switching to your own key applies at once, and the lower price starts at renewal. Moving back to LeadAI-provided goes through checkout, unless the Super Admin grants it as a courtesy for the rest of the paid period. The renewal and the "next price" shown in the portals use the same calculation.
  - Keys are encrypted at rest (`API_KEY_ENCRYPTION_KEY`, new dependency `cryptography`), write-only and verified on save. No organization response in any portal carries a stored key, only a masked hint.
  - An own key that is missing or rejected never falls back to LeadAI's key: Apify searches stop with a clear message, Gemini uses rules, and admins are notified.
  - One organization's Gemini rate limit no longer pauses AI for everyone.
  - Platform tokens count only LeadAI-paid usage.
  - Replaces the uncommitted all-or-nothing BYOK draft. That draft returned customers' raw keys in an API response and stored them in plaintext; its data is migrated on startup.
- **New look: cream + light mint green** (every portal: website, sign-in, user dashboard, Org Admin, Super Admin, Partner Portal, staff console):
  - Light cream + mint is now the default; dark mode is deep forest with mint glows and cream text. The light/dark toggle and a person's saved choice still apply.
  - Colours come from `app/static/design/tokens.css`. The staff console's own palette, the dashboard's brand colours and the remaining hard-coded violets were moved to it.
  - Primary buttons are light mint with dark-green text. Text meets WCAG AA contrast in both themes.
  - Default brand colours (`branding.colors.*`, website `primary_color`) are now mint (`#2e9573`) and warm gold (`#d9a441`). Values an operator saved are kept.
- **Administrators can change other people's sign-in details** (`app/auth/credentials.py`):
  - Organization Admin: **Change email** and **Set password** on a member's page (`PATCH /api/org-admin/users/{id}/email`, `POST /api/org-admin/users/{id}/password`).
    - The existing rules apply: the owner is protected, only the owner manages admins, and you can't change your own account here.
    - Refused for accounts also used outside the organization (another organization, a partner, platform staff).
  - Super Admin: **Set password** for any user (`POST /api/super-admin/users/{id}/password`). The email change now also syncs partner records and resets email verification.
  - Super Admin: partners' email and password (`/api/super-admin/partners/{id}/email|password`).
  - Super Admin: a new **Admins › Platform staff** tab for staff-console accounts (`/api/super-admin/staff`).
  - Passwords are validated, stored hashed, and never emailed or shown again. The person is signed out everywhere and emailed a notice. Every change is audited.
  - **Temporary passwords**: an administrator-set password requires the person to choose their own at the next sign-in, in every portal (`must_change_password`, the `/change-password` page). Until then, the API answers `403 password_change_required`.
  - `POST /api/auth/password/change` now also works for platform staff accounts.
- **Security**: the Super Admin user endpoints no longer return `totp_secret` (2FA seeds), and `strip_sensitive` removes it everywhere.
- **Partner tasks**:
  - The Super Admin assigns tasks to one partner or to every active partner, affiliate or reseller, with details, a due date, a priority and a link to a Partner Portal page.
  - Partners see them under *Tasks*, with a count in the menu and a dashboard banner. They start a task and submit it with a note.
  - The Super Admin approves it, sends it back with feedback, edits it or cancels it.
  - Notified both ways and audited (`partner.task.*`); readable with a partner API key.
  - New collection: `partner_tasks`.
- **Partners link in the website's top menu** (desktop and mobile), linking to the `/partners` application page. It's added once to existing sites at startup and is never re-added after an operator removes it.
- **Super Admin full access**:
  - Organization impersonation acts as Owner with every organization permission, ignoring the organization's role restrictions.
  - "View as partner" Partner Portal impersonation, fully attributed.
  - A platform-wide API keys & webhooks page with revoke, disable and enable.
  - The Super Admin can issue Customer API and Partner API keys for any organization or partner.
- **Partner selling and oversight**:
  - A Sell LeadAI kit: live plans, customer price, commission per plan, plan share links.
  - Deal registration with Super Admin review and claim protection.
  - A Super Admin partner activity log (sign-ins, every portal and API request, actions) with CSV export, partner session control, and per-partner activity, sessions, deals and audit tabs.
  - New partner permissions `sales.view` and `deals.manage`, granted to existing partners once.
- **Fixed**: partner screens showed UTC times as local time (several hours off in non-UTC timezones).
- **Billing refunds**: `POST /api/super-admin/invoices/{id}/refund` and `GET /api/super-admin/invoices`, plus provider refund/dispute webhooks. Invoices and payments now record full and partial refunds and chargebacks.

### Fixed
- TOTP two-factor authentication was never enforced at login (the flag was read from session claims). The login page now asks for the code.
- The public REST API (`/api/v1`) was unreachable: the auth gate required a session, and the handlers awaited a non-coroutine. The per-key rate limit is now enforced.
- Razorpay checkout overwrote the subscription amount with `plan.price_cents` (minor units, or 0 when unset), breaking payment verification.
- **CI pipeline**: every job except the dependency audit was failing.
  - Tests: plain `pytest` couldn't import `app` (fixed with `pythonpath = .` in `pytest.ini`).
  - Frontend check: it looked in `public/` and `static/`, which don't exist; it now checks `app/static`.
  - Lint: ruff's growing defaults reported 4,496 findings. `ruff.toml` now pins the rule set (`E4, E7, E9, F`), and all findings are fixed.
  - Type check: the 167 mypy errors are fixed (annotations, no behaviour changes).
  - Tool versions are pinned, and the GitHub actions are updated to Node 24.
- **Bugs found by the new checks**:
  - Searching a direct YouTube video URL crashed (`utcnow` was not imported in `app/social/scrapers.py`).
  - Shutdown never aborted in-flight Apify runs (it called a method that doesn't exist on the apify-client run collection).
  - `PUT /api/admin/apify/actors/{platform}` always failed with a wrong keyword argument.
  - `DELETE /api/search-presets/{id}` always failed: it called a missing permission check. Org owners and admins (`settings.manage`) and the creator can now delete.
  - `/api/admin/ai/playground` crashed on a missing import. It now returns `501 Not Implemented`.

### Performance
- **Slow admin panel on hosted MongoDB**: every request made many sequential database round trips, and some blocked the event loop, so all requests waited.
  - The session check runs once per request instead of twice, and the `last_active_at` write happens at most once a minute.
  - System settings reads are cached for 5 seconds, and writes clear the cache.
  - The usage summary resolves the plan once and fetches every bonus credit in one query.
  - `/api/auth/me`, the `/org-admin` page, `context` and `business-summary` no longer run sync database calls on the event loop.
  - `/api/org-admin/overview` went from 101 to 54 database operations and `context` from 40 to 18, with none blocking the event loop.
- **Request timing**: a `Server-Timing` header on every response, plus a `slow request` log line above `SLOW_REQUEST_MS` (default 1000 ms).

## [2.0.0] - 2026-10-02

### Added
- **Phase 7: Comprehensive Documentation Architecture**:
  - Restructured monolithic `README.md` into modular documentation in `docs/`.
  - Added `docs/ARCHITECTURE.md`, `docs/API.md`, `docs/ADMIN_PORTALS.md`, `docs/DEPLOYMENT.md`, `docs/SECURITY.md`, `docs/ROADMAP.md`, `docs/KNOWN_LIMITATIONS.md`, and `docs/UPGRADE_NOTES.md`.
  - Added `LICENSE` (MIT License) and `CONTRIBUTING.md`.

## [1.6.0] - 2026-10-02

### Added
- **Phase 6: Enterprise Product Features**:
  - YouTube comment scraping and video lead extraction pipeline.
  - Recurring / scheduled search scans (`scheduled_scans`) with interval hours and automated execution.
  - Bulk URL submission endpoint for multi-URL concurrent processing.
  - Cross-platform & cross-run lead deduplication engine (`app/pipeline/deduplication.py`) matching phone, email, and social handles.
  - Automated lead assignment rules with SLA timers, round-robin allocation, and reminder alerts.
  - Outbound webhook subsystem (`outbound_webhooks`) with HMAC-SHA256 signature verification.
  - CRM connectors: HubSpot, Zoho CRM, Google Sheets, and Excel (.xlsx) streaming export with formula injection sanitization.
  - Public REST API v1 (`/api/v1/leads`, `/api/v1/search`) authenticated via SHA-256 hashed API keys with per-org scopes and rate limiting.
  - Razorpay billing provider with UPI checkout, webhook verification, and INR pricing plans.
  - Unit-economics reporting: Gemini token costs, Apify compute costs, and per-tier margin tracking (`docs/UNIT_ECONOMICS.md` and `GET /api/super-admin/unit-economics`).

## [1.5.0] - 2026-10-01

### Added
- **Phase 5: Reliability & Scalability Infrastructure**:
  - Durable job queue abstraction (`app/queue/service.py`) supporting Redis RQ/Arq and MongoDB fallbacks.
  - Pluggable media storage service (`app/storage/service.py`) supporting Local filesystem, AWS S3, and Cloudinary.
  - Automated MongoDB backup and restore tools (`scripts/backup_mongodb.py`, `scripts/restore_mongodb.py`).
  - Disaster recovery runbook (`docs/DISASTER_RECOVERY.md`).
  - GitHub Actions CI workflow for automated linting, type-checking, and test execution.

## [1.4.0] - 2026-09-30

### Added
- **Phase 4: Compliance & Security Hardening**:
  - Prompt-injection defense: delimited untrusted-data boundaries and strict JSON schema validation.
  - GDPR/CCPA compliance engine (`docs/COMPLIANCE.md`): PII auto-purge retention policies, contact blocklists, and data deletion requests.
  - Sentry error tracking integration and correlation request IDs on every API request.
  - Docker container hardening with non-root security context and healthchecks.

## [1.3.0] - 2026-09-30

### Added
- **Phase 3: Data Model Consolidation**:
  - Industry-agnostic buyer intent classification (`purchase_inquiry`, `pricing_inquiry`, `partnership`, etc.).
  - Relaxed lead lifecycle state machine with reason-tracking for reopened or disqualified leads.
  - Platform-neutral collection taxonomy (`social_pages`, `social_posts`, `social_comments`).

## [1.2.0] - 2026-09-29

### Fixed
- **Phase 2: Single Source of Truth & Audit**:
  - Synchronized scoring thresholds (Hot >= 80, Warm 50-79, Cold < 50).
  - Reconciled session lifetime and token storage mechanisms.
  - Created `docs/README_AUDIT.md`.

## [1.1.0] - 2026-09-29

### Added
- **Phase 1: Performance & Caching**:
  - GZip/Brotli compression middleware.
  - Compound MongoDB index optimization for hot queries.
  - Static asset caching and frontend asset bundling.
  - Re-measured performance gains documented in `docs/PERFORMANCE_REPORT.md`.

## [1.0.0] - 2026-09-29

### Initial Release
- Baseline multi-tenant social listening application with Apify scraping and Gemini AI analysis.
