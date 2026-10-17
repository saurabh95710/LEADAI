# LeadAI Known Limitations & Architectural Boundaries

This document details intentional architectural trade-offs, default fallbacks, and operational limits across the LeadAI platform.

---

## 1. Billing & Payment Gateway Defaults

### Mock Stripe Provider
- By default, when `BILLING_PROVIDER=stripe` is selected without live `STRIPE_SECRET_KEY` credentials in `.env`, the system runs the **Mock Stripe Billing Provider**.
- In mock mode:
  - Checkout sessions succeed immediately using test mock payment webhooks.
  - Subscriptions simulate payment confirmations without charging credit cards.
- **Production Resolution**: For real payment processing, set `BILLING_PROVIDER=razorpay` with real Razorpay credentials (supporting UPI and Indian cards) or set valid `STRIPE_SECRET_KEY` and `BILLING_WEBHOOK_SECRET` for Stripe.

---

## 2. In-Process & In-Memory Fallbacks

### Queue Worker Fallback
- When `REDIS_URL` is not provided in `.env`, LeadAI automatically falls back to an **in-memory thread pool** for background jobs.
- **Limitation**: In-memory jobs do not survive physical server restarts. If the application process terminates while a scrape is in progress, the job status remains `running` until marked failed by the supervisor.
- **Production Resolution**: Always configure a dedicated Redis instance (`REDIS_URL=redis://...`) in production environments.

### Rate Limiting Fallback
- When Redis is unavailable, API rate limiting operates in-process per application replica using sliding-window memory buffers.
- **Limitation**: Multi-node horizontal deployments without Redis will track rate limits separately per node.

---

## 3. Platform & Third-Party API Limits

### Social Media Scraping (Apify Actors)
- Social platforms (Meta, YouTube, LinkedIn) periodically update their web DOM and anti-bot challenges.
- Scraping runs rely on Apify actors. If an actor experiences upstream network blocks or platform layout changes, Apify may return empty datasets or actor run errors.
- LeadAI surfaces these upstream errors as `scrape_failed` with the exact Apify actor run link for administrative inspection.

### Keyword search: data varies by platform
- Each platform exposes different data, so some filters can't be checked everywhere ([KEYWORD_SEARCH.md](KEYWORD_SEARCH.md) has the full table):
  - **Facebook and LinkedIn** don't expose a total post count.
  - **YouTube** has no business-account flag.
  - **No platform** exposes language.
  - **Email** isn't available from Instagram, YouTube or LinkedIn; contact info there comes from bio text and links.

  Such filters are shown as "unsupported" and skipped, unless Strict is on.
- **Counts are snapshots** of what a logged-out visitor sees. Instagram can hide like counts, and pinned posts can make a profile look more recent than it is.
- **Discovery is capped:**
  - Instagram returns at most 250 profiles per keyword.
  - YouTube discovery searches videos and picks out their channels, so a keyword yields fewer channels than videos.
  - LinkedIn search returns at most about 1,000 companies per query.
- **Facebook:** discovery uses the search actor's default mode (search engine results). Some pages show limited data to logged-out visitors.
- **LinkedIn:** discovery relies on a third-party actor and is switched off by default; LinkedIn's terms forbid scraping.
- **Actor output:** some fields in the actors' output aren't documented (for example whether Facebook native search returns email and followers, and whether YouTube search items carry subscriber counts). Adapters treat a missing field as unknown and fetch details when needed. The first real runs on each platform should be checked.
- **Cost estimates are worst case** (every candidate passes). Apify prices change; the USD figure is a rough guide.

### AI Token & API Rate Limits
- Calls to Google Gemini are subject to per-minute request limits (RPM) and token limits (TPM) imposed by Google Cloud project quotas.
- For high-volume organizations running multiple simultaneous scans, batch classification jobs will wait for retry backoff if rate limits are exceeded.

### Concurrent Export Limits
- To prevent denial-of-service via massive spreadsheet generation, concurrent export generation is capped at 3 simultaneous exports per server worker (`_EXPORT_MAX_CONCURRENT = 3`).
- Additional export requests return HTTP `429 Too Many Requests` until an ongoing export completes.

## 4. Features that are stored but not run yet

### Outbound webhooks
Webhooks can be registered (`/api/org-admin/webhooks`) and payloads are signed
(`X-LeadAI-Signature: sha256=…`), but no event sends a delivery yet: nothing
calls `dispatch_webhook_event`. Before it is wired in, target URLs need
checking (https only, no private / loopback / link-local addresses, so a
webhook can't reach internal services), deliveries need to run off the request
path with retries, and the event list needs settling. There is no Org Admin
screen for webhooks.

### Scheduled scans
Scheduled scans can be created, listed and deleted (`/api/search/scheduled`),
but nothing runs them (`trigger_due_scheduled_scans` has no caller). When it
is wired in, each run must go through the same admission as a search started by
hand (`admit_url_search`: switches, plan caps, tokens, quota), on behalf of a
member who still belongs to the organization.

### AI playground
`POST /api/admin/ai/playground` answers 501: running a prompt against a model
from the registry is not implemented.
