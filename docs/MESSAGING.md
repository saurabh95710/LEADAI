# Messaging: email + SMS gateway

LeadAI sends email and SMS through one gateway.

- **Sign-up:** a welcome email, plus a welcome text when there's a phone number.
- **Sign-in:** a "new sign-in to your account" alert by email and SMS.
- **Forgotten password:** a reset link by email, or a 6-digit code by SMS.
- **Admin messages:** the Super Admin can message anyone; an organization's owners and admins can message their own team.

Code: `app/messaging/` (`phone.py`, `sms.py`, `service.py`). Email: `app/events/email.py`.

## Setting it up (Super Admin → Messaging → Gateway)

### Email

Set in the **server environment**, not in the app, so nobody can redirect password-reset emails from a screen:

```
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=…
SMTP_PASSWORD=…
SMTP_FROM=LeadAI <no-reply@yourdomain.com>
```

Without `SMTP_HOST`, emails stay in the outbox (Super Admin → Notifications → Email outbox) and nothing is delivered.

### SMS

Chosen on the Gateway tab. Settings can also come from the environment. Secrets are stored encrypted and never shown again.

| Provider | Settings | Notes |
|---|---|---|
| `twilio` | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM` (a number, or a Messaging Service SID starting with `MG`) | Worldwide |
| `fast2sms` | `FAST2SMS_API_KEY` | India only ("Quick SMS" route) |
| `webhook` | `SMS_WEBHOOK_URL` (https), optional `SMS_WEBHOOK_TOKEN` (sent as `Authorization: Bearer …`) | Any other provider. LeadAI POSTs `{"to": "+91…", "message": "…", "kind": "…"}` |
| *(empty)* | — | Nothing is sent; texts stay `queued` in the SMS outbox |

**India:** commercial SMS needs a DLT-registered sender ID and message templates with your provider. Register the welcome, sign-in alert and reset-code texts, or use the webhook to reach a provider's DLT template API.

**Other settings:**
- `SMS_DEFAULT_COUNTRY_CODE` (default `+91`) is used for numbers typed without one.
- Phone numbers are stored in E.164 form as `users.phone_e164`. Existing users are converted on startup.

**Spending guards:**
- `SMS_DAILY_LIMIT` (default 500) caps texts in any 24 hours.
- `SMS_PER_NUMBER_DAILY_LIMIT` (default 10) caps texts to one number in 24 hours. This stops "SMS pumping" abuse of the public sign-up and reset forms.
- Test sends skip the limits.

Every text is recorded in `sms_outbox` with its status: `sent`, `queued`, `failed`, `invalid` or `limited`. Reset codes are masked in the stored copy.

## Automatic messages (Super Admin → Messaging → Automatic messages)

| Message | Default | When |
|---|---|---|
| Welcome email | always | On sign-up: the free-trial / demo email. |
| Welcome text | on | On sign-up, when there's a phone number. Also when a partner creates a customer, and when a demo is approved. |
| Login alert by email | on | On every sign-in to any portal; at most once per `login_alert_every_minutes` (default 10) per person. Never during a support (impersonation) session. |
| Login alert by SMS | on | The same, by text. |

## Password reset by SMS

1. `POST /api/auth/password/forgot {"phone": "…"}` always answers 200, so it can't be used to find accounts.
2. If exactly one active account uses that number, it gets a 6-digit code. The code is valid for 10 minutes and only its hash is stored.
3. If several accounts share the number, the text says to reset by email instead, and no code is created.
4. `POST /api/auth/password/reset {"phone": "…", "code": "123456", "new_password": "…"}` checks the code. Five wrong codes and that code stops working.
5. On success, every session of that account is signed out.

The emailed link (`{"email": …}` then `{"token": …}`) still works as before.

## Sending messages

**Super Admin** (`/api/super-admin/messages`):
- **Recipients:** chosen people (`user_ids`), whole organizations (`organization_ids`), or a group: `org_admins` (every owner and admin), `all_users` or `partners`. At most 5000 people per message.
- **Channels:** email, SMS, or both. Email needs a subject. An optional shorter `sms_body` is used for the text.

**Organization owners and admins** (`/api/org-admin/messages`):
- Only their own organization's members. Anyone else in `user_ids` is dropped.
- At most 20 messages a day per organization.

**Delivery tracking:**
- Each message is recorded in `message_campaigns`, with counts per channel: sent, queued, failed, skipped (no address) and other.
- Every email and text it sends is tagged with the message's id, so delivery can be followed person by person (`GET …/messages/{id}`).
- Sending runs in the background and is audited (`messaging.message_sent`, `messaging.team_message_sent`).
