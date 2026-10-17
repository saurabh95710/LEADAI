# Changelog

All notable changes to LeadAI are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added (UI pass 2026-10-16)
- **Org Admin → Assignment rules** (`/org-admin#assignment`): create rules that share new leads round-robin between chosen members with a contact time (SLA), see who gets which leads, delete rules. Explains how rules work.
- **Lead SLA in the customer app**: lead details show "Contact by … (4-hour SLA)" or "SLA missed — contact now"; the leads list marks overdue leads.
- **Public opt-out page** (`/opt-out`): people whose public comments were collected can remove their email or phone without an account (email link or SMS code). Linked from the footer, privacy page and contact page.
- Mistyped public addresses show the 404 page instead of the sign-in page.

### Changed (UI pass 2026-10-16)
- All portals: clearer plain-English wording, correct singular/plural ("1 lead", not "1 lead(s)"), exact token counts below 10,000 ("1,975 / 2,000" instead of "2K / 2K"), readable KPI labels (no more "ACTIVE SUBSCR…"), menu names that match page titles, sentence case, phone-width fixes.
- Sign-in page uses the green brand style like the rest of the product; primary buttons have readable contrast across the whole button.
- Closing the welcome guide no longer scrolls the page under the top bar.
- Partner Portal: plain labels, "Request payout" disabled when there is nothing to pay, real plan names.
- Super Admin: honest "Webhook delivery is coming soon" notice, help text on Partner Program settings, pricing preview no longer lists features twice.

### Fixed (QA run 2026-10-16, see reports/QA_REPORT.md)
- **Revoke all sessions did nothing** (Super Admin → Security): the epoch was a counter compared with cookie times in seconds. It is now the revocation time, and every tracked session in every portal is revoked.
- **Bulk search skipped every rule**: `POST /api/search/bulk` queued Apify runs without the search switches, plan caps, tokens or monthly quota. Each URL is now admitted like a single search.
- **Public opt-out**: it purged every organization's matching leads at once and any signed-in customer could call it. A prospect now opts out without an account, confirmed by an email link or SMS code; usernames are refused.
- **Assignment rules were never applied**: rules now assign new leads round-robin, stamp an SLA, accept only active members, and the background sweep flags missed SLAs and overdue follow-ups.
- **Environment guard password** could be guessed without limit: 5 wrong tries per account lock it for 15 minutes.
- Security: 2FA can no longer be replaced with only a session; webhook listings no longer return the signing secret; AI model audit entries can no longer name someone else; partner records no longer store the Super Admin's session object; a partner editing a coupon no longer undoes the Super Admin's commission decision; verification emails are only sent to existing unverified accounts.
- Broken flows: `GET /api/search/scheduled` (shadowed, and now scoped to the member); Apify control center wrote actor keys the scrapers never read; AI models created through the API could not be read; `DELETE /api/admin/apify/token`, AI model list and category rename answered 500; admin lead PATCH lost the status when a note was sent; legacy env overrides could not be removed; the keyword library ignored custom industries; the emailed verification link had no page (`/verify-email`); right-to-be-forgotten by name missed capitalised names.
- Validation: negative trial extensions and credit grants are refused; admin lead search combines text and contact filters with AND; notes on a missing lead answer 404.

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
- **API usage & limits monitor** (`docs/API_LIMITS.md`):
  - **What's watched:**
    - Apify (monthly spend and compute units from Apify's API) for LeadAI's key and every customer's own key.
    - Gemini requests per minute and per day, and input tokens per minute, counted against limits copied from AI Studio.
    - Emails per day against a daily limit; mail-server "limit reached" replies are detected.
    - SMS daily count, Twilio balance, and Fast2SMS wallet and remaining SMS.
    - Payment set-up.
    - Each organization's tokens and plan limits.
  - **Live errors are noticed at once:** Apify 402, Gemini 429/402, SMTP limit replies, Twilio suspension, Fast2SMS low balance.
  - **Levels:** warning (80%), critical (95%) and used up, with a "back to normal" notice. Each is announced once per period.
  - **Who is told:**
    - Super Admins: every level, plus email at critical.
    - Organization admins: their own keys and plan, and LeadAI services they depend on.
    - Users: when something they need is used up.

    Personal and organization notification preferences are respected. The plan near-limit notices, which existed but were never sent, are now sent.
  - **New screens:** Super Admin → API limits (cards with meters, reset time, history, organizations worst-first, Check now); SMS on Integrations; limits on Health; Org Admin → API usage & limits with a dashboard banner; a Limits card and blocked-search banner in the customer app.
  - **Settings:** Global Settings → Email & alerts → API limit alerts, plus the Gemini limits on the AI engine tab.
  - **Fixed:**
    - The Super Admin Integrations page always showed $0 Apify cost (it read a field that doesn't exist).
    - The AI overview's "last 24 hours" range showed 30 days.
- **Keyword search** (`docs/KEYWORD_SEARCH.md`): type keywords, pick platforms, and LeadAI finds matching business profiles, pages, channels and companies.
  - **Stages:** discovery → profile details → recent posts → comments. Profiles failing the filters are dropped before any money is spent on their posts or comments.
  - **Filters:**
    - Followers (min / max), total posts, average and total comments over the last N posts, engagement rate, active within N days.
    - Business only, has contact info, location, verified, excluded words.
    - Missing data shows as "unsupported" or "unknown", never as a silent pass or fail; Strict mode rejects instead.
  - **Results:** every candidate is stored with its measured values and the exact rejection reasons. Filters can be re-applied without scraping, and **Continue** fetches newly passing profiles.
  - **Cost:**
    - Tokens are charged per stage actually run (`discover`, `enrich`, `sample`, then `collect`), with a cost estimate and confirmation before starting.
    - Optional token budget; graceful stop when tokens run out.
    - 24-hour profile cache; own Apify keys never fall back to LeadAI's.
  - **Limits:** per plan and per free trial (keywords, candidates, recent posts). Global Settings → Search limits → Keyword search holds the switches, caps, default filters, cache time and actor IDs. LinkedIn is off by default.
  - **APIs:** `/api/keyword-search` (app) and `/api/v1/searches/keyword` (API key).
  - **Customer app:** a Keyword search tab with filter presets and a Guide step.
  - **Filters narrow the Apify search and results show only matches:** the location (places separated by commas, `all` / `any` must match, whole-word so "kota" never matches "Dakota") and the new **Business type contains** filter are sent with the search (Facebook / LinkedIn `locations`, Instagram / YouTube search text, YouTube channel-search limits) and recorded as `discovery_queries`; results open on the passing profiles, with a rejection summary and one-click filter fixes when none pass; Language is hidden (no platform measures it).
- **Fixed: every URL search ran twice on the server.** The request thread ran it and the queue worker picked up the same job a second later, so each search called Apify twice. The job is now recorded as already taken by the thread, and the worker only resumes it if that process dies.
- **Fixed: `POST /api/v1/search` skipped every check.** API searches now go through the same rules as the app: feature and platform switches, URL validation, plan limits, monthly quota and token charges. They also appear in search history.
- **Payments with Razorpay and Stripe, and a working mail server setup** (`docs/PAYMENTS_AND_EMAIL.md`):
  - **Razorpay is complete:**
    - Checkout creates a real Razorpay order and opens Razorpay's payment window (UPI, cards, net banking, wallets).
    - The payment is checked by its signature, and its webhook (`/api/billing/webhook/razorpay`) records payments, failures, refunds and disputes once each.
    - Stripe has its own webhook address (`/api/billing/webhook/stripe`), and both keep working whichever provider is active.
  - **Super Admin → Global Settings → Payments** chooses the provider (auto, Razorpay or Stripe) and shows each provider's keys, webhook URL and events to tick, the `.env` lines to add, and a **Check keys** button. Keys stay in the server's `.env` only.
  - **Email & alerts** now has a Mail server panel:
    - It shows what is set and missing, the outbox and the last error.
    - It offers ready-made `.env` lines for Hostinger, Gmail, Zoho and Brevo, and a **Send test email** button.
    - Email works over SSL (port 465, e.g. Hostinger) as well as STARTTLS (587).
    - Failures say what to fix (wrong password, wrong port, server unreachable).
  - **Fix:** values written only in `.env` were ignored: `PUBLIC_BASE_URL`, `SMTP_*`, `TRUST_PROXY_HEADERS`, `BILLING_WEBHOOK_SECRET`, the SMS settings and the new Razorpay keys. `.env` is now read at start-up (real environment variables still win).
- **Global Settings reorganized, and every setting on it now works:**
  - **Layout:**
    - The tabs are grouped under four headings: Brand & website, Leads engine, Communication and Operations.
    - Each tab opens with what it controls and links to related pages (Messaging, Feature Flags, AI Management…).
    - Groups have a one-line explanation, and short fields sit two to a row.
    - A number on each tab shows how many settings differ from the default.
    - Lead scoring and comment detection have their own tabs, and every setting has a plain description of what it really does.
  - **Settings that did nothing now work:**
    - **Email:** the sender name, reply-to address and footer line apply to every email, and emails use the product name instead of "LeadAI".
    - **Alerts and emails:** the alert-bell switch and the "search failed" / "new leads" email switches work.
    - **Downloads:** row limit, leads-only default and the AI-reason column.
    - **Leads:** "require a phone or email" and the starting status.
    - **New organizations:** the default timezone and currency apply to them.
    - **Default colour theme:** applies to visitors who haven't picked one.
    - **Colours:** success, warning, danger, info and accent apply everywhere.
    - **Footers:** help and legal links appear in the sign-in and app footers.
    - **Large searches:** a warning appears before an unusually large search.
    - **Website sign-up switch:** the website shows "Sign-ups are closed", and partner sign-ups respect it too.
    - **Minimum Facebook comments:** applies without a restart.
  - **Removed:** 30 settings that had no effect, duplicates and leftovers of the old `/admin` console; their saved values are kept. The two AI on/off switches are now one; a saved "off" is carried over.
  - **Corrected labels:** "Stop on limit" actually limits searches running at once, and "Minimum comments" applies to Facebook posts.
  - **Elsewhere:**
    - Brand colours and font now also apply on the Super Admin, Admin and Partner portals.
    - The user app's tab title shows your product name.
    - Website colours set in Website / CMS apply.
    - The misleading "Audit logging" switch is replaced by "Always on".
    - A page link saved as an image is never sent to browsers.
- **Global Settings fixes:** an image setting saved as a page address (e.g. `…/superadmin#/settings?tab=branding`) is flagged on the Branding tab instead of showing a broken logo, every image field has a **Clear** button, previews show only real images, and such a value is refused when typed (as on the server). A bad value you didn't touch never blocks saving other changes. On start, stored page-address logos and stray id-named setting entries are removed once, so the built-in defaults apply.
- **Brand changes reach the website:** Super Admin → Global Settings (General: name, tagline; Branding: logo, favicon, colours) is now the one source for the public website too: header, page titles, favicon and colours (light and dark), as well as the apps and sign-in pages. Website settings keep those fields only as fallbacks and say where to change them. Saved changes show on the next page load (no 5-minute browser copy, website data revalidates). Image settings refuse page links, and garbled text from an earlier wrong-encoding save ("â€”") is repaired once.
- **One platform portal: the old `/admin` console is merged into `/superadmin`.** Every feature of both is kept and each duplicate appears once:
  - New pages: Operations (overview, jobs + failed jobs with retry, leads with notes, follow-ups, priority, assignment and bulk actions, platforms, pages, posts, comments, exports), Lead engine (Apify, actors, limits, usage & cost, lead scoring, keyword rules with custom categories), System (database, logs with statistics) and Settings (Global Settings with version history, import/export and live brand preview; Environment with the guard lock).
  - Merged into the existing pages: plans, subscriptions (extend trial, grant credits), invoices, organizations (set plan, grant feature, impersonate a member), Security Center (staff accounts, security settings), feature flags, health checks, maintenance, analytics (leads & scraping tab), AI management (runtime settings, live test, analysis), audit.
  - Platform staff (managers, viewers) use the same portal; the menu shows what their role allows, and everyone can change their own password from the profile menu. `/admin` and old `/admin#/…` links redirect to the matching page.
  - Fixes found on the way: run filters for comments, leads and posts, the follow-ups CSV, export audit details, the log module parser, a wrong guard password no longer signs you out, and the Apify statistics no longer fail on databases without `$convert`.
- **Messaging gateway: email + SMS** (`docs/MESSAGING.md`):
  - SMS through Twilio, Fast2SMS (India) or a webhook to any other provider, set up in Super Admin → Messaging → Gateway (secrets stored encrypted), with a test send, an SMS outbox and daily spending limits (total and per number; reset codes have their own allowance).
  - Automatic messages: a welcome text on sign-up and a "new sign-in" alert by email and SMS (at most every 10 minutes per person); each can be switched off.
  - Forgotten password by phone: a 6-digit SMS code (10 minutes, 5 tries), next to the emailed link, on the reset page ("Email me a link" / "Text me a code").
  - The Super Admin can message anyone (people, whole organizations, all organization admins, all users, all partners) and organization admins can message their own team (Team → Message team), by email and/or SMS, with delivery tracking per person.
- **Interactive guides in every portal** (`docs/INTERACTIVE_GUIDES.md`): customer app, Org Admin, Super Admin and Partner Portal each have a welcome guide (starts once, on a person's first visit) and page guides, opened from the **? Guide** button. One shared engine (`app/static/design/tour.js` / `tour.css`): highlights each part of the screen, opens the right page, works with the keyboard and screen readers, light/dark and phones.
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
