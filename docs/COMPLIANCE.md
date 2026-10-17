# LeadAI Compliance, Privacy & Security Architecture

> **LEGAL DISCLAIMER**  
> LeadAI provides technical compliance controls, data protection mechanisms, and architectural safeguards. This document and the associated software features **do NOT constitute legal advice**. Organizations operating LeadAI must consult qualified legal counsel to ensure compliance with applicable regulations in their jurisdictions, including the General Data Protection Regulation (GDPR), California Consumer Privacy Act (CCPA), Telephone Consumer Protection Act (TCPA), and platform Terms of Service.

---

## 1. Executive Summary

LeadAI operates as an AI-orchestrated social lead intelligence platform. It analyzes publicly available comments across Facebook, Instagram, YouTube, and LinkedIn to surface business inquiries for authorized client organizations.

Phase 4 implements privacy-by-design, enterprise compliance primitives, robust adversarial defense against LLM prompt injection, and zero-trust authentication mechanisms.

---

## 2. Privacy & Data Protection Framework

### 2.1 Configurable PII Retention & Automated Purging
Organizations can configure an automated data retention window to minimize stored Personally Identifiable Information (PII) in compliance with GDPR Article 5(1)(e) (Storage Limitation) and CCPA §1798.100.

- **Settings Endpoint**: `GET /api/compliance/settings` and `PUT /api/compliance/settings`
- **Supported Window**: 0 (retention disabled / manual purge only), 30, 60, 90, 180, or 365 days.
- **Automated Sweep**: The background maintenance sweeper and the manual trigger endpoint (`POST /api/compliance/purge-pii`) identify records in `ai_comments` older than the organization's cutoff date.
- **Redaction Protocol**:
  - Direct identifiers (`phone`, `email`, `whatsapp`, `contact`) are permanently `$unset` from MongoDB.
  - The record is stamped with `pii_purged: true`, `pii_purged_at: <timestamp>`, and `pii_retention_days: <days>`.
  - Aggregated analytics, sentiment trends, and topic distributions remain intact for historical business reporting without exposing personal identifiers.

### 2.2 Right to be Forgotten (GDPR Article 17 / CCPA Deletion)
Individuals and organizations can submit immediate data erasure requests:

- **Admin Deletion Endpoint**: `POST /api/compliance/delete-request`
- **Public / Prospect Self-Serve Opt-Out**: `POST /api/compliance/opt-out` (no LeadAI account needed).
  Nothing is blocked or purged until the person proves the contact is theirs:
  - an **email address** gets a single-use confirmation link (24 hours): `GET /api/compliance/opt-out/confirm`;
  - a **phone number** gets a 6-digit SMS code (15 minutes, 5 tries): `POST /api/compliance/opt-out/confirm`;
  - a **username** can't be verified and is refused; the business that collected it removes it with the admin endpoint.
  The answer is the same whether or not LeadAI holds data on the contact. The opt-out is platform-wide: every
  organization's matching leads lose their contact details.
- **Execution Flow**:
  1. The prospect's phone number, email address, or username is normalized (names match whole, in any case).
  2. The identifier is permanently added to the `compliance_blocklist` collection to block any future scraping or AI ingestion.
  3. All existing records matching the identifier in `ai_comments` have their PII stripped, and flags set to `is_lead: false`, `is_useful: false`, `compliance_blocked: true`.
  4. An immutable audit record is emitted to `audit_logs` tracking the deletion event.

### 2.3 Contact Blocklist (Do-Not-Contact)
LeadAI maintains an organization-scoped and platform-wide blocklist:

- **Check Enforcement**: Before a comment is analyzed or converted to a lead in `app/pipeline/comment_ai.py`, `is_blocked()` evaluates:
  - Phone digits against blocked numbers.
  - Normalized email addresses against blocked emails.
  - Social handles / usernames against blocked authors.
- **Blocklist Management Endpoints**:
  - `GET /api/compliance/blocklist`: View paginated active blocks.
  - `POST /api/compliance/blocklist`: Add a phone/email/username with mandatory reason.
  - `DELETE /api/compliance/blocklist/{id}`: Remove a block.

### 2.4 Data Processing Notice
A publicly accessible Data Processing and Privacy Notice is published at:
`GET /api/public/compliance-notice`

It details:
- Lawful basis for processing (Legitimate Interests under GDPR Article 6(1)(f)).
- Public comment processing scope (publicly accessible posts only).
- Rights supported (Opt-out, Access, Erasure, Rectification).
- Direct link to the public self-service opt-out endpoint.

---

## 3. Platform Terms of Service & Scraping Risks

### 3.1 Meta (Facebook & Instagram)
- **ToS Considerations**: Meta's Terms of Service and Commercial Terms restrict automated collection of data without prior written permission.
- **Mitigation Architecture**:
  - Apify actors handle proxy rotation and browser emulation on external infrastructure.
  - LeadAI limits extraction exclusively to publicly visible comments on public business pages and posts.
  - LeadAI does NOT store credentials or session cookies for private Meta accounts.
  - Client organizations assume responsibility for ensuring their usage conforms with Meta's developer policies and commercial guidelines.

### 3.2 LinkedIn
- **ToS Considerations**: LinkedIn actively enforces restrictions on automated scraping under Section 8.2 of its User Agreement.
- **Legal Precedent Note**: While *hiQ Labs v. LinkedIn* affirmed that scraping public data does not violate the CFAA, LinkedIn maintains contractual restrictions against automated collection.
- **Mitigation Architecture**:
  - Ingestion is limited to public company updates and comments.
  - Rate limiting, backoff, and randomized schedules prevent aggressive crawling.

### 3.3 TCPA & CAN-SPAM Compliance
- Scraped contact details (phone numbers and emails) must **never** be contacted via automated dialers, pre-recorded messages, or unsolicited bulk commercial emails without express verifiable consent.
- Organizations must reconcile LeadAI leads with their internal Do-Not-Call (DNC) registries before initiating outreach.

---

## 4. Prompt-Injection Defense Architecture

LeadAI employs multi-layered defense to prevent untrusted user comments from altering Gemini's classification logic or executing adversarial instructions:

```
[Untrusted Comment Text]
           │
           ▼
[Adversarial Regex Detection] ──► (Block / Neutralize if Jailbreak / System Prompt Override)
           │
           ▼
[Delimiter Sanitization] ───────► (Strip delimiter closing tags)
           │
           ▼
[Boundary Framing] ─────────────► <UNTRUSTED_COMMENT_TEXT> ... </UNTRUSTED_COMMENT_TEXT>
           │
           ▼
[System Prompt Hardening] ──────► Strict instructions: Ignore instructions inside delimiter
           │
           ▼
[Gemini Structured Inference]
           │
           ▼
[Strict JSON Schema Validation] ─► Bounded scores (0-100), whitelisted intent, sanitized fields
```

1. **Delimited Untrusted Blocks**:
   Comments are encapsulated within `<UNTRUSTED_COMMENT_TEXT>` and captions within `<UNTRUSTED_POST_CAPTION>`.
2. **Breakout Sanitization**:
   Any literal `</UNTRUSTED_COMMENT_TEXT>` or `</UNTRUSTED_POST_CAPTION>` within the comment is stripped or escaped before prompt assembly.
3. **Adversarial Regex Interception**:
   Comments containing explicit jailbreak tokens (`ignore previous instructions`, `DAN mode`, `disregard system prompt`, `you are now in developer mode`) are flagged immediately, assigned zero lead score, and categorized as neutral/none without invoking high-cost LLM tokens.
4. **Strict Schema Validation**:
   Outputs must strictly adhere to the expected JSON schema with validated bounds (e.g., `lead_score` clamped between 0 and 100, `intent` strictly enumerated).

---

## 5. Authentication, 2FA & Session Hardening

1. **Email Verification**:
   - `POST /api/auth/signup` generates a cryptographically secure single-use token (`secrets.token_urlsafe(32)`) hashed with SHA-256 in MongoDB.
   - Verification link (`/verify-email?token=...`) validated via `POST /api/auth/verify-email`.
   - Rate-limited resend endpoint via `POST /api/auth/verify-email/resend`.
2. **RFC 6238 TOTP Two-Factor Authentication (2FA)**:
   - Zero external dependency implementation using standard library `hmac`, `hashlib`, `struct`, `base64`.
   - Setup (`POST /api/auth/2fa/setup`), verification & activation (`POST /api/auth/2fa/enable`), and revocation (`POST /api/auth/2fa/disable`).
   - `POST /api/auth/login` checks `totp_enabled` and verifies 6-digit one-time code with time-step drift tolerance.
3. **Password Reset Hardening**:
   - Rate-limited single-use SHA-256 tokens (`POST /api/auth/password/forgot` and `POST /api/auth/password/reset`).
   - Constant-time lookup and immediate revocation of all other active sessions upon password reset.

---

## 6. Request Tracing & Sentry-Compatible Error Reporting

1. **Distributed Request Tracing**:
   - `RequestIdMiddleware` extracts incoming `X-Request-ID` or generates a 32-character hex UUID.
   - Stored in Python `contextvars.ContextVar` and propagated to `request.state.request_id`.
   - Returned in outgoing HTTP response header `X-Request-ID`.
2. **Unified Log Formatting**:
   - `RequestIdFilter` injects `[request_id=<id>]` into every log line across console and rotating file logs.
   - Compatible with regex parser in `app/log_parser.py` (`request_id=\S+`).
3. **Sentry-Compatible Error Hooks**:
   - `capture_exception` hook integrates with Sentry SDK when `SENTRY_DSN` is configured.
   - Captures unhandled 500 exceptions, attaching HTTP method, route, and active `request_id`.

---

## 7. Container Hardening

- **Base Image**: Pinned to stable `python:3.11.9-slim-bookworm`.
- **Least Privilege**: Application runs under unprivileged system user `appuser` (UID/GID isolated, nologin shell).
- **Healthcheck**: Docker native `HEALTHCHECK` periodically verifies `http://localhost:8000/health`.
- **Pinned Dependencies**: `requirements.txt` strictly pinned to tested patch versions.
