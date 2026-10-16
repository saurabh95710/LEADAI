# API coverage: LeadAI-provided or bring your own keys

LeadAI calls two paid external APIs:

- **Apify**, to scrape pages, posts and comments.
- **Google Gemini**, to classify comments into leads.

For **each** API, an organization chooses who provides it:

| Choice | Apify | Gemini | Starter price (monthly / yearly) |
|---|---|---|---|
| All included | LeadAI | LeadAI | $49 / $490 |
| Own Apify key | customer's key | LeadAI | $37 / $370 |
| Own Gemini key | LeadAI | customer's key | $41 / $410 |
| Bring both keys | customer's key | customer's key | $29 / $290 |

With its own key, the customer pays Apify or Google directly, and LeadAI's price is lower.

## Pricing

- **Plan price:** `price_monthly` / `price_yearly` is the **all-included** price.
- **Add-ons:** `api_addons[api][cycle]` is the amount taken off when the customer brings that API's key.
- **Allowing own keys:** `allows_byok` controls whether a plan allows own keys at all. The Free plan doesn't.
- **One pricing function:** `app/billing/plans.py: price_for(plan, coverage, cycle)` computes every price that is shown or charged: the pricing page, checkout, renewal and the partner sales kit.
- **Editing:** the Super Admin edits the add-ons in the plan editor. Together they can't exceed the plan price.

Default add-ons per month, with yearly = ×10:

| Plan | Apify | Gemini |
|---|---|---|
| Starter | $12 | $8 |
| Pro | $36 | $24 |
| Business | $96 | $64 |
| Enterprise | $240 | $160 |

## Where it is chosen

- **Pricing page:** toggles per API, on `/pricing`.
- **Checkout:** `POST /api/billing/checkout` with `api_coverage`. The subscription keeps a snapshot (`subscriptions.api_coverage`). On confirmation, the organization's coverage becomes that snapshot.
- **Org Admin → API keys & plan** (`/org-admin#integrations`): add, replace, test and remove keys, and switch each API. Owners and admins only.
- **Super Admin → organization → API keys & plan:** view the organization's coverage and switch it, with a reason. A forced switch ("courtesy") applies without payment and is audited, but can't move an API to "own" unless the organization has saved a key. The Super Admin can also remove a compromised key; a reason is required and is sent to the organization's admins.

## Changing it later

| Situation | What happens |
|---|---|
| No paid subscription (demo, trial, free) | Applies at once |
| Switch an API to your own key (a verified key is required) | Your key is used at once; the lower price starts at the next renewal |
| Switch an API back to LeadAI-provided, which the subscription didn't pay for | Costs more, so checkout at the new price; it applies once the payment is confirmed |
| Super Admin courtesy switch to LeadAI-provided | Applies at once without payment for the rest of the paid period; the renewal charges the new price |

**Renewal** (`app/billing/api_coverage.py: renewal_amount`) charges for the coverage in use at that moment. It adds or takes off only the list-price difference from the stored amount, so partner and coupon discounts are kept. The "next price" shown in the portals uses the same calculation.

**Payment providers:** with a real provider subscription (Stripe / Razorpay), the provider's recurring price must also be updated. The app records the new amount; the provider-side plan isn't changed automatically.

## Keys

- **Stored encrypted:** keys are encrypted at rest with Fernet (`app/services/secret_box.py`). The encryption key comes from `API_KEY_ENCRYPTION_KEY`; if that is unset, from `SESSION_SECRET`; and in development only, from a key generated and stored once in the database.
- **Changing the encryption key** makes saved customer keys unreadable. Customers then have to enter them again.
- **Write-only:** no API response, log, audit entry or email ever contains a key. Responses carry only a masked hint (`apif…7890`) and status.
- **Verification:** keys are tested with the provider when saved. A key that fails the test is saved but marked unverified.

## Super Admin → API keys

One page (`/super-admin#/provider-keys`) for both kinds of keys.

**Keys LeadAI provides.** Every organization on LeadAI-provided Apify or Gemini runs on these.

- **Replace:** the new key is tested with the provider first; a key that fails is refused unless the Super Admin ticks "Save anyway". A reason is required and audited.
- **Storage:** a key saved here overrides the server environment (`APIFY_API_TOKEN` / `GEMINI_API_KEY`) and is stored encrypted. If it can no longer be decrypted (the encryption key changed), the environment value is used and an error is logged.
- **Use the server key:** removes the saved key so the environment's applies again. Refused when the environment has none, because every customer on LeadAI-provided would stop.
- **Gemini:** replacing or successfully testing the key closes LeadAI's shared Gemini circuit breaker, so AI resumes at once.
- The page shows where each key comes from, its last test and how many organizations run on it.

**Customers' own keys.** A list of organizations using their own keys, with a "Needs attention" filter (an API on "own" without a working key). Per API the Super Admin can set or replace the key (for example one the customer sent to support), test it, remove it, or switch the API between LeadAI and the customer's own key. Setting a key notifies the organization's admins; the key is encrypted and never shown again.

## At run time

- **One resolver:** `resolve_api_key(org_id, "apify" | "gemini")` (`app/services/tenant_api_keys.py`) is used by the scraper, URL search, background jobs and the AI step.
- **No fallback:** an API set to "own" **never falls back to LeadAI's key.** What happens instead depends on the API:
  - **Apify:** a missing, rejected (401/403) or out-of-credit (402) key stops the search with a clear message.
  - **Gemini:** comments are analysed with the free rule-based pass.
  - **Both:** the organization's admins are notified, at most once every 6 hours, and the error is shown on the API keys page.
- **Per-organization pauses:** a customer's rate-limited or rejected Gemini key pauses AI for that organization only. LeadAI's shared circuit breaker only guards LeadAI's own key.
- **Platform tokens** count only what LeadAI pays for. A search is 60% Apify and 40% Gemini; collecting posts is 100% Apify. The share that runs on the customer's own keys is not charged.
- **Keyword search** ([KEYWORD_SEARCH.md](KEYWORD_SEARCH.md)): every stage (discovery, profile details, recent posts, comments) uses the organization's own Apify key when its coverage is "own", and never LeadAI's.
  - A missing or rejected own key stops the search before anything runs.
  - The `discover`, `enrich` and `sample` token actions are 100% Apify, so they cost an own-key organization 0 tokens.

## Endpoints

| Method + path | Who |
|---|---|
| `GET /api/public/pricing`: `coverage_options`, `api_addons`, `allows_own_keys` per plan | public |
| `GET /api/billing/plans`: the same three fields per plan | public |
| `GET /api/org-admin/integrations/api-keys` | org owner/admin (members read-only) |
| `PUT /api/org-admin/integrations/api-keys/{apify\|gemini}` `{key}` · `POST …/{provider}/test` · `DELETE …/{provider}` | org owner/admin |
| `PUT /api/org-admin/integrations/api-coverage` `{apify?, gemini?}` | org owner/admin |
| `GET` / `PUT /api/super-admin/organizations/{id}/api-coverage` · `PUT` / `DELETE …/api-keys/{provider}` · `POST …/api-keys/{provider}/test` | Super Admin |
| `GET /api/super-admin/provider-keys?show=own\|problems\|all&q=` · `PUT` / `DELETE /api/super-admin/provider-keys/platform/{provider}` · `POST …/platform/{provider}/test` | Super Admin |
| `GET /api/super-admin/subscriptions?coverage=…` · `dashboard.api_coverage` | Super Admin |
| `GET /api/partner/v1/sales`: `coverage_options` with customer price and commission | partner |
