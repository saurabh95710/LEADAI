# Keyword search (discovery)

A user types one or more keywords (for example "villa" or "real estate Goa") and picks platforms. LeadAI then:
1. finds matching business profiles, pages, channels and companies;
2. checks them against quality filters;
3. collects posts and comments **only for the profiles that pass**.

The existing comment filter and Gemini lead scoring then run on those comments, exactly as for a URL search.

- **Customer app:** Search → **Keyword search** tab.
- **API:** `/api/keyword-search` (session) and `/api/v1/searches/keyword` (API key); see [API.md](API.md).

## How a search runs

Each stage runs only for the profiles that are still in, so you pay for deep scraping only on profiles worth it.

| Stage | What happens | Charged |
|---|---|---|
| 1. Discovery | The platform's search actor finds candidates for each keyword. The location and business type filters are sent with the search where the actor supports it (see [What is sent to Apify](#what-is-sent-to-apify)). | `discover` per keyword per platform |
| 2. Profile details | Followers, post count, bio, contacts, verified, business flag and last post date. **Profile filters run here**, and failures are dropped before any posts are fetched. | `enrich` per profile (free when discovery already returned the details or the profile is cached) |
| 3. Recent posts | The last N posts of each survivor; average and total comments and engagement rate are computed. **Post filters run here.** | `sample` per profile (free for Instagram up to 12 posts, which come with discovery, and for cached profiles) |
| 4. Deep scrape | Comments of the passing profiles' posts, within the per-search and per-plan limits, then the comment filter and Gemini / keyword-rule scoring. | `collect` per profile |

Details:
- **Candidates are deduplicated** by platform and profile id across all keywords. Every discovered address goes through the same validator as URL search, so personal profiles, groups, stories and reels are never accepted.
- **Every candidate is kept** with its status: `passed`, `rejected`, `needs_sampling` or `error`. Each carries the measured values and the exact reasons, e.g. `followers 800 < min 5000`, so users can see why a profile was dropped and tune the filters.
- **Results show the passing profiles by default.** The app opens the results on the **Passed** tab; rejected profiles are one click away ("28 profiles didn't match your filters — Show them") and the other tabs stay available. When a finished search has no passing profile, the app explains why instead: the most common rejection reasons with counts (e.g. "26 — location: 'Rajasthan' not found"), one-click fixes that prefill **Re-run with different filters** (e.g. "Use 'any' location match", "Lower minimum total posts"), and what was searched on Apify. The API's candidate list still returns every status unless `status` is given; `GET /{run_id}` adds `passed_count` and `rejection_summary`.
- **Failures stay contained.** A failed Apify call for one profile marks only that profile `error`; a failed discovery fails only that platform.
- **Recording and resuming:**
  - Each stage is recorded on the run.
  - After a crash, the worker resumes and skips finished stages.
  - Cancelling keeps the results so far.
  - **Continue** picks up what's left.
- **Cache:** profile details and recent posts are cached per profile (default 24 hours; Global Settings → Search limits → Keyword search). A cached profile isn't fetched or charged again.

## Filters

All filters are optional. Every filter that is set must pass (AND).

| Filter | Instagram | Facebook | YouTube | LinkedIn |
|---|---|---|---|---|
| Minimum / maximum followers | Yes | Yes | Yes | Yes |
| Minimum total posts | Yes | No | Yes | No |
| Minimum average comments per post | Yes | Yes | Yes | Yes |
| Minimum comments across analyzed posts | Yes | Yes | Yes | Yes |
| Minimum engagement rate (%) | Yes | Yes | Yes | Yes |
| Posted within the last N days | Yes | Yes | Yes | Yes |
| Business accounts only | Yes | Yes | No | Yes |
| Has contact info | Partial | Yes | Partial | Partial |
| Location contains | Partial | Partial | Partial | Yes |
| Business type contains | Partial (business category + bio) | Yes (page categories) | Partial (description only) | Yes (industries) |
| Exclude names / bios containing | Yes | Yes | Yes | Yes |
| Verified accounts only | Yes | Partial | Yes | Partial |

**Recent posts to analyze** (N, default 10) sets how many recent posts the post-level numbers use. It is capped by the plan.

**Location contains** takes places separated by commas (or semicolons), most specific first: `Kota, Rajasthan`. **Location must mention** (`location_match`) is `all` (default: every place must appear; also how runs saved before this option are read) or `any` (one place is enough). Each place is matched as whole words, ignoring case and punctuation, so `kota` matches "Kota, Rajasthan" and "KOTA-Rajasthan" but never "North Dakota" or "Kotak". It is looked for in the name, bio, address, city, country and category. A profile with no location data at all (no address, city, country or bio) is **unknown**, not rejected. Rejection reasons name the missing places: `location: 'Rajasthan' not found`.

**Business type contains** (`category_keyword`) takes alternatives separated by commas, e.g. `software, IT services`; one is enough. They are matched as whole words against the profile's category (Instagram business category, Facebook page categories, LinkedIn industries) and its bio and name. YouTube has no category, so only the channel description is used.

**Language** is no longer offered: no platform reports a profile's language. The API still accepts `language` for compatibility (it is reported as unsupported) and the estimate doesn't mention it.

## What is sent to Apify

Location and business type don't only filter the results afterwards — they also narrow the Apify search, so fewer unrelated profiles are found and paid for. Only inputs documented in each actor's input schema are used, and every profile is still checked by all the filters.

| Platform | Sent with the keyword search |
|---|---|
| Instagram | The search text becomes the keyword + the first (most specific) place, e.g. `developer job kota`. Instagram's account search matches names and usernames, so a longer phrase finds almost nothing; the business type is checked on each profile afterwards. |
| Facebook | `locations: ["kota, Rajasthan"]` — the location exactly as typed; the actor applies it to every search term. |
| LinkedIn | `locations: ["kota, Rajasthan"]`. Industry filtering needs LinkedIn's numeric industry ids, so the business type is checked afterwards only. |
| YouTube (video search) | `searchQueries: ["developer job kota software"]`: the keyword + the first place + the first business type. |
| YouTube (channel-search actor, when configured) | `searchTerms` built the same way, plus the actor's own `minSubscribers` / `maxSubscribers` / `minVideos` (from the follower and post filters), `verifiedOnly`, and `countryHint` when one of the places is a country (e.g. `India` → `IN`). |

At most one place (and, on YouTube, one business type) is added, a term already in the keyword isn't repeated, and the search text never grows past 60 characters (a term that doesn't fit is left out). Each run records what was sent per platform and keyword (`discovery_queries`); the app shows it as "Searched Apify for: 'developer job kota' (Instagram), 'developer job' near 'kota, Rajasthan' (Facebook)", and the cost estimate lists the same queries.

**Engagement rate** = average (likes + comments, plus shares where available) per analyzed post ÷ followers × 100.

**Missing data never passes or fails silently:**
- **Unsupported:** the platform can't provide the value at all, e.g. a Facebook page's total post count. The filter is skipped for that platform, and the UI, estimate and results say so.
- **Unknown:** the platform supports the value but this profile didn't return it.

Both are listed on the candidate. With **Strict** on, unsupported and unknown filters reject the profile instead.

**Validation:** negative, non-numeric or absurd values are refused (422 with the field). Limits above the plan are reduced to the plan's maximum, with a note.

**Re-apply filters:** this re-checks stored values without scraping and without charging. If looser filters let in profiles whose posts were never fetched, they become `needs_sampling`. **Continue** fetches them after showing the cost.

## Actors

The actor IDs live in Global Settings → Search limits → Keyword search, and changes are versioned like every setting.

| Platform | Discovery | Profile details | Recent posts |
|---|---|---|---|
| Instagram | `apify/instagram-search-scraper` (profile + latest ~12 posts in one call) | same | `apify/instagram-post-scraper` when N > 12 |
| Facebook | `apify/facebook-search-scraper` | existing pages actor | existing posts actor |
| YouTube | existing YouTube actor's video search, deduplicated to channels (an optional channel-search actor can be set) | `streamers/youtube-channel-scraper` | existing YouTube actor on the channel, newest first |
| LinkedIn | `harvestapi/linkedin-company-search` | same | existing company-posts actor |

**LinkedIn is switched off by default.** Its search actor is third party, and LinkedIn's terms forbid scraping. A Super Admin can switch it on in Global Settings.

**Own Apify key:** an organization that uses its own Apify key always uses it for every stage, and never falls back to LeadAI's key.

## Limits and cost

**Per plan** (keywords per search / candidates per search / recent posts per profile):

| Plan | Keywords | Candidates | Recent posts |
|---|---|---|---|
| Free trial | 1 | 20 | 10 |
| Starter | 3 | 100 | 20 |
| Pro | 5 | 250 | 30 |
| Business / Enterprise | 10 | 500 | 50 |

- The free plan has no keyword search.
- A plan or trial limit set to 0 switches keyword search off for it (`402 PLAN_LIMIT`). A missing limit means the Global Settings cap applies.
- Global Settings caps apply on top, and the lower value wins.
- Comments per post use the existing per-search limits.

**Tokens (defaults, editable in Super Admin → Tokens):**

| Action | Tokens |
|---|---|
| `discover` | 5 per keyword per platform |
| `enrich` | 1 per profile |
| `sample` | 1 per profile |
| Deep scrape | the existing `collect` price, per profile |

How charging works:
- Tokens are charged only for stages that actually run, and refunded if that stage's Apify call fails.
- Organizations on their own Apify key aren't charged for these actions.
- **Before starting**, the app (and the estimate endpoint) shows the worst-case tokens per stage and a rough Apify cost, and asks for confirmation (`confirm: true` in the API).
- An optional `max_tokens` budget stops the run when reached (`stopped_budget`). Running out of tokens stops it with `stopped_tokens`. Results so far are kept in both cases.
- When **Continue** is given `max_tokens`, the value is a budget for that continuation only.
- **Never charged twice:** each charge is recorded on its item before it is taken, so resuming after a crash never pays twice. Profiles with nothing to fetch aren't charged, and a stage whose fetches all failed is refunded and retried by Continue.

**Safety:**
- Rate limits per organization: 10 starts per minute, 60 estimates per minute.
- At most 5 searches running per organization when "Limit searches running at once" is on.
- Tenant isolation on every read: another organization's run returns 404 and is logged.
- Every start, re-apply, continue, cancel and preset change is audited.
