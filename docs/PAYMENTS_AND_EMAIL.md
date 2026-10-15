# Payments and email: setup

Payment and mail-server credentials can be set in two places:

- **Super Admin → Global Settings** (recommended):
  - The **Payments** tab has each provider's keys and webhook secret, its webhook URL and events to tick, and **Check keys**.
  - The **Email & alerts** tab has the mail server, the outbox and **Send test email**.
  - Only the Super Admin can do this, and every change is audited.
  - Secrets are stored encrypted (Fernet, key from `API_KEY_ENCRYPTION_KEY`) and are never sent back to the browser.
  - A value saved here overrides `.env`.
- **The server's `.env`** (`~/leadai/.env` on the VPS) is used when nothing is saved in the app. It is read at start-up, so restart after editing it:

  ```
  cd ~/leadai && bash scripts/deploy_vps.sh
  ```

  Pushing any commit to `main` also restarts it. Real environment variables win over the file.

The root login (`SUPERADMIN_EMAIL`, `SUPERADMIN_PASSWORD`) and `SESSION_SECRET` can only be set in the environment, never in the app.

## Email (SMTP)

| Provider | Lines |
|---|---|
| Hostinger email | `SMTP_HOST=smtp.hostinger.com` · `SMTP_PORT=465` · `SMTP_SECURITY=ssl` · `SMTP_USER=you@yourdomain.com` · `SMTP_PASSWORD=<mailbox password>` |
| Gmail / Workspace | `SMTP_HOST=smtp.gmail.com` · `SMTP_PORT=587` · `SMTP_SECURITY=starttls` · `SMTP_USER=you@gmail.com` · `SMTP_PASSWORD=<16-letter app password>` |
| Zoho Mail (India) | `SMTP_HOST=smtp.zoho.in` · `SMTP_PORT=465` · `SMTP_SECURITY=ssl` |

Also set the following:

- `SMTP_FROM=YourBrand <you@yourdomain.com>`, using a mailbox the server lets you send from.
- `PUBLIC_BASE_URL=https://your-domain`. Without it, links in emails don't open.

Then use **Send test email**. If it fails, the page says why (wrong password, wrong port/security, server not reachable).

Emails that waited in the outbox while no server was set are **not** re-sent. Their one-time links (password reset, invitations) are never stored, so people simply ask again.

## Payments

Every verified payment still waits for a Super Admin to confirm it in Subscriptions before a plan starts.

### Razorpay (UPI, cards, net banking, wallets in INR)

1. Razorpay Dashboard → Account & Settings → **API keys**: generate a key. Enter it on the Payments tab, or put it in `.env`:
   ```
   RAZORPAY_KEY_ID=rzp_live_…
   RAZORPAY_KEY_SECRET=…
   ```
2. Razorpay Dashboard → **Webhooks** → Add new webhook:
   - **URL:** `<PUBLIC_BASE_URL>/api/billing/webhook/razorpay`
   - **Secret:** type one, then put it in `.env` as `RAZORPAY_WEBHOOK_SECRET=…`
   - **Events:** `payment.captured`, `payment.failed`, `order.paid`, `refund.processed`, `payment.dispute.created`
3. Leave Payment Capture on **Automatic** (the default).

### Stripe

1. Stripe Dashboard → Developers → **API keys**: put the secret key in `.env` as `STRIPE_SECRET_KEY=sk_live_…`.
2. Developers → **Webhooks** → Add endpoint:
   - **URL:** `<PUBLIC_BASE_URL>/api/billing/webhook/stripe`
   - **Events:** `checkout.session.completed`, `checkout.session.async_payment_failed`, `invoice.paid`, `invoice.payment_failed`, `charge.refunded`, `charge.dispute.created`, `customer.subscription.deleted`
   - **Signing secret:** put it in `.env` as `STRIPE_WEBHOOK_SECRET=whsec_…`

### Which provider takes payments

Global Settings → Payments → **Take payments with**:

- `auto` (default): Razorpay if its keys are set, otherwise Stripe, otherwise the test-only mock page.
- An explicit choice whose keys are missing falls back to `auto`, and the page says so.

Both webhook URLs keep working whichever provider is active, so renewals and refunds from the other provider are still recorded.

Use **Check keys** on the Payments tab after saving the keys (or after a restart when they are in `.env`). Test keys (`rzp_test_…`, `sk_test_…`) are labelled as such.
