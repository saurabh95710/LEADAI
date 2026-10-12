# LeadAI Administration & User Portals

LeadAI provides segregated portals designed for different personas with fail-closed security and role-based access control.

---

## Portals Summary Matrix

| Portal | URL | Who Can Access | API Prefix | Description |
|---|---|---|---|---|
| **Platform Console** | `/superadmin` | Super Admin & Platform Staff (`super_admin`, `operations_admin`, `billing_admin`, `support_admin`, `technical_admin`, `viewer`) | `/api/super-admin` | Global cross-tenant management, organization provisioning, billing plans, platform audit logs, and unit economics. |
| **Organization Admin Portal** | `/org-admin` | Customer Organization Owners & Admins (`owner`, `admin`) with confirmed subscription | `/api/org-admin` | Workspace team management, role delegation, lead assignment rules, outbound webhooks, public API keys, and organization audit trails. |
| **Workspace Dashboard** | `/user` (or `/dashboard`; `/` is the public website) | All verified organization members (`owner`, `admin`, `manager`, `member`, `viewer`) | `/api` | Daily operations: URL search execution, social posts & comments scraping, AI lead analysis, CRM pipeline, notes, and CSV/Excel exports. |
| **Public Portal & Landing** | `/`, `/website`, `/pricing`, `/login` | Public / Anonymous visitors | `/api/public` | Product marketing, pricing tiers, self-service organization signup, demo registration, and authentication entry point. |

---

## 1. Platform Console (`/superadmin`)

The Platform Console allows internal staff to oversee the entire SaaS ecosystem. Access requires a platform session with an assigned platform role.

**One portal for the whole platform team.** The old separate staff console at `/admin` is merged into `/superadmin`. `/admin` (and old links such as `/admin#/settings`) redirect here, and the old route names still open the matching page.

**Who sees what:** the menu shows each person what their console role allows. The server still checks every call: `/api/super-admin/*` is Super Admin only, and `/api/admin/*` keeps its own viewer / manager / Super Admin checks.

| Role | Sees |
|---|---|
| Super Admin | everything |
| Manager | **Operations** (overview, jobs and failed jobs, leads, platforms, pages, posts, comments, exports), **Lead engine** (Apify, actors, limits, usage & cost, lead scoring, keyword rules), **System** (database, logs) and **Settings** (Global Settings, Environment), plus read-only views of organizations, plans, subscriptions, invoices, analytics, AI, audit, security, health, feature flags and maintenance. Managers can change what their role allows. |
| Viewer | the same pages, read-only |

**Code:** `app/static/super-admin.js` (core) plus the modules `super-admin-ops.js`, `super-admin-engine.js` and `super-admin-settings.js`, all plugged in through `window.SAKit.register`.

### Key Capabilities:
- **Tenant Management**: Provision, suspend, activate, or archive organizations.
- **Subscription & Billing Plans**: Configure pricing tiers, quotas, token allocations, and features.
- **Feature Flags & Maintenance**: Toggle platform features globally or schedule maintenance windows with custom banner messaging.
- **Audit & Security**: Review system-wide audit logs and security incident alerts (cross-tenant attempts, forged tokens).
- **Unit Economics**: Live dashboard tracking token cost per AI action, Apify compute consumption per search, and gross margin per plan.
- **Global Settings** (`#/settings`): every platform-wide setting, in 12 tabs under four sections:
  - **Brand & website:** General, Branding, SEO & sharing.
  - **Leads engine:** AI engine, Lead scoring, Comment detection, Search limits, Platforms & features, Leads & downloads.
  - **Communication:** Email & alerts.
  - **Operations:** Security, Operations.

  Each tab opens with what it controls and links to related pages. A tab's number counts the settings changed from the default. Every listed setting takes effect. Settings that did nothing were retired (`RETIRED_KEYS` in `app/settings/registry.py`); their stored values are kept but no longer shown. Old tab links (`?tab=features`, `?tab=appearance`) open the tab those settings moved to.

### Access Guards:
- Backend dependency: `require_platform_role(role)` in `app/auth/tenant.py`.
- Non-platform sessions receive an immediate 403 Forbidden with security audit logging.

---

## 2. Organization Admin Portal (`/org-admin`)

Each customer organization has its own isolated admin portal. Access requires the caller to hold `owner` or `admin` role within their organization, and the organization must have an active subscription or unlocked admin portal flag.

### Key Capabilities:
- **Team Management**: Invite team members, change roles, suspend or remove members, and issue password reset links.
- **Granular RBAC**: Delegate granular permissions (e.g., `search.create`, `leads.manage`, `exports.create`) to custom roles.
- **Automated Lead Assignment Rules**: Configure rules (round-robin or criteria-based) with SLA response timers in hours.
- **Outbound Webhooks**: Register endpoints with custom HMAC-SHA256 secrets to receive real-time notifications on events (e.g. `lead.created`, `lead.qualified`).
- **Public API Keys**: Generate scoped API keys (`lai_live_...` or `lai_test_...`) for external CRM automation.
- **Audit Logs**: Filterable audit trail of all actions performed within the organization.

### Access Guards:
- Backend dependency: `require_portal(perm)` in `app/api/routes/org_admin.py`.
- Every query is strictly isolated with `organization_id: ctx.organization_id`.

---

## 3. Workspace Dashboard (`/user`, also `/dashboard`)

The main operational workspace where team members discover leads, review prospective buyers, and manage outreach.

### Key Capabilities:
- **Search Execution**: Submit target social URLs (Facebook, Instagram, YouTube) for on-demand or bulk scanning.
- **Scheduled Scans**: Set up recurring scans (e.g., hourly, daily) fetching only new posts and comments.
- **Lead Discovery**: View AI-classified comments categorized into Hot, Warm, Cold, and Intent types.
- **Contact Details Extraction**: Identify extracted phone numbers, email addresses, WhatsApp numbers, and buyer budgets.
- **Pipeline Workflow**: Update lead lifecycle statuses (`new` -> `contacted` -> `qualified` -> `converted`), assign leads to colleagues, and schedule follow-up reminders.
- **Data Export**: Export leads, posts, and pages to CSV or Excel XML format with formula injection sanitization.
