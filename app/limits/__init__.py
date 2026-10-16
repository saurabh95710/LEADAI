"""API usage & limits monitor.

Shows the usage and limits of every third-party API LeadAI uses (Apify,
Google Gemini, SMTP email, SMS via Twilio / Fast2SMS / webhook, payments via
Razorpay / Stripe) and LeadAI's own token & plan meters, and notifies the
people affected when a limit is near or reached.

  model.py     status / metric shapes, levels, thresholds, Pacific-time day
  checkers.py  one checker per provider (provider API, counted, configured)
  store.py     api_limit_status (latest) + api_limit_history (30-day TTL)
  alerts.py    api_limit_alerts dedupe + who gets which notification
  service.py   run_checks / run_scheduled + live hooks (record_*_error)
  views.py     response shapes of the Super Admin / org admin / user APIs
"""
