# API usage & limits

LeadAI watches the limits of every outside service it uses, and the limits of each customer's plan. It warns the people affected before something stops working.

| Service | What is watched | Where the numbers come from |
|---|---|---|
| **Apify** (LeadAI's key and every customer's own key) | Monthly spend vs the account's limit, compute units. Memory and parallel runs are shown for information. Reset date from the billing cycle. | Apify's API (`/v2/users/me/limits`) |
| **Google Gemini** (LeadAI's key and customers' own keys) | Requests per day (the day resets at midnight Pacific time). Requests and input tokens per minute are shown for information only: short bursts are normal, so only Google's own "quota used up" reply blocks AI. | Counted by LeadAI against the limits you enter. Google offers no API for remaining quota. |
| **Email (SMTP)** | Emails sent in the last 24 hours vs the daily limit you enter. "Sending limit reached" replies from the mail server are detected (e.g. `550 5.4.5`). | Counted by LeadAI, limit from settings |
| **SMS** | Texts sent today vs the SMS daily limit, plus the provider balance (Twilio balance, Fast2SMS wallet and remaining SMS) | Provider API and LeadAI's count |
| **Payments** (Razorpay, Stripe) | Set-up status only. These providers have no usage quota, and they aren't polled. | Configuration |
| **LeadAI tokens and plan limits** (per organization) | Tokens left; monthly searches, posts, comments, AI analyses, exports | LeadAI |

Live errors are noticed immediately, without waiting for the next check:
- Apify "out of credit" (402) and limit errors.
- Gemini "quota used up" (429). A daily block lasts until midnight Pacific; a per-minute block lasts for the wait Google asks for.
- Gemini "no credit" (402) and "billing off".
- Mail-server sending-limit replies.
- Twilio "account suspended" and Fast2SMS "insufficient balance".

## Levels and notifications

| Level | When |
|---|---|
| OK | Below the warning threshold |
| Warning | At or above **Warn at** (default 80%) |
| Critical | At or above **Critical at** (default 95%) |
| Used up | The limit is reached, or the provider refuses for lack of credit or quota |
| Problem | The key was rejected or the provider can't be reached |

**Who is told:**

| Whose limit | Super Admin | Organization admins | Users |
|---|---|---|---|
| LeadAI's own Apify, Gemini, email or SMS | Every level, in-app. Email to `SUPERADMIN_EMAIL` at critical and above. | In-app at critical and above, only for organizations that rely on it (e.g. Apify on LeadAI's key). The message says what may pause, without LeadAI's internal numbers. | In-app when it's used up |
| An organization's own Apify or Gemini key | — | Every level in-app, plus email at critical and above | In-app when it's used up |
| An organization's tokens or plan limits | Existing token alerts | Warning, critical, used up | In-app when it's used up |

How often:
- Each level is announced **once per period**: the Apify billing month, the Gemini Pacific day, the day for email and SMS, or the plan period. It is announced again only if the level gets worse, or changes to a different problem.
- A problem that lasts (e.g. an SMS balance at 0 for a week) is announced again at most once a week.
- When things return to normal after a critical or used-up state, a "back to normal" notice follows. It is sent only once the service has been fine for two checks and at least 30 minutes, so a brief dip doesn't cause a burst of notices.
- **Live errors stay in force until they're cleared:** an Apify "out of credit" error lasts until the next successful run; a Gemini block lasts until its reset time or the next successful call.
- **Preferences:**
  - Organizations that switched off usage warnings still get "used up" notices, but not warning or critical ones.
  - Users always follow their own "usage warnings" preference.

## Where to see it

- **Super Admin → API limits** (`/superadmin#/api-limits`):
  - a card per service with meters, the reset time, where each number comes from, the last error and a 7-day history;
  - every organization's own keys, tokens and plan, worst first;
  - **Check now**.
- **Super Admin → Integrations** and **Health** show the same levels.
- **Org Admin → API usage & limits** (`/org-admin#api-limits`): the organization's own keys, tokens, plan limits, and whether LeadAI's services are working. A banner appears on the dashboard when something needs attention.
- **Customer app:** a Limits card on the Usage page (searches left, tokens left, AI available). The search page shows a banner when searches or AI may not run, with what to do (upgrade, contact your admin, or wait). The banner only warns: the search button stays usable, and the server itself refuses what really can't run (e.g. no tokens left).

## Settings

**Global Settings → Email & alerts → API limit alerts:**
- on/off;
- check interval (default 15 minutes, minimum 5);
- **Warn at %** and **Critical at %**;
- whether organization admins and users are notified, and whether admins get emails;
- the email daily limit;
- the minimum SMS balance.

**Global Settings → AI engine:** Gemini requests per minute, requests per day and input tokens per minute. Copy them from Google AI Studio → Rate limits; they depend on your Google project's tier.

A limit left at 0 isn't watched. The page says "Limit not set" and links to the setting.

**Typical email limits:**
- Gmail: 500/day.
- Google Workspace: 2,000/day.
- Hostinger: 1,000 or 3,000/day, depending on the plan; see hPanel → Mailboxes → View limits.
- Zoho: 50–500/hour.

## API

| Method | Path | Who |
|---|---|---|
| `GET` | `/api/super-admin/api-limits?level=&q=&page=&limit=` | Super Admin (platform staff: `/api/admin/api-limits`) |
| `POST` | `/api/super-admin/api-limits/refresh` `{scope: "platform" \| "all" \| <org id>}` | Super Admin (staff: manager and up). 5 per minute. |
| `GET` | `/api/super-admin/api-limits/history?provider=&scope=&days=` | Super Admin / staff |
| `GET` | `/api/org-admin/api-limits` · `POST …/refresh` (own keys; 3 per 5 minutes) | Organization owner / admin |
| `GET` | `/api/me/api-limits` | Any member (no provider details) |

Storage:
- `api_limit_status`: the latest status per service and scope.
- `api_limit_history`: one point per check, kept 30 days.
- `api_limit_alerts`: which notices were sent, kept 180 days.
