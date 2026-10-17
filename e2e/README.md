# Edge end-to-end journeys

Real Microsoft Edge (Playwright), driven through the public website, the
customer app, Org Admin, Super Admin and the Partner Portal. Every journey
records a video and a Playwright trace; a failing one also leaves a
screenshot.

Nothing here touches real data or real services:

- **Database:** a dedicated `mongod` on port 27099, database `LeadAI_e2e`.
  `serve_fresh.py` drops it before each run and refuses any other port.
- **Apify:** only the network boundary is faked (`e2e/server.py`, the same
  approach as `tests/test_e2e_lifecycle.py`); every route, pipeline stage,
  filter and database write is the real code.
- **Gemini, email, SMS, payments:** no keys, so comments are scored by the
  keyword rules, mail waits in the outbox, and checkout is the mock provider.

The journeys are not part of `pytest` / CI (`testpaths = tests`): they need
Edge and the stack below.

## Run

```bash
pip install playwright && playwright install ffmpeg      # Edge itself is already installed on Windows

# 1. a throwaway mongod (any empty folder)
mongod --dbpath <empty folder> --port 27099 --bind_ip 127.0.0.1

# 2. a fresh LeadAI on it
python e2e/serve_fresh.py --port 8765

# 3. the journeys: headed, slow-mo 300 ms, 1440x900
python -m pytest e2e -p no:cacheprovider -v
E2E_HEADLESS=1 E2E_SLOWMO=0 python -m pytest e2e -p no:cacheprovider -q   # fast, for debugging
```

Restart step 2 before every run: the journeys create people and the login
lockout journey (G5) locks this machine's IP out of signing in for a while.

## Evidence

| What | Where |
|---|---|
| Video per person per journey | `reports/videos/<journey>/<person>.webm` |
| Playwright trace | `reports/traces/<journey>__<person>.zip` (`playwright show-trace <zip>`) |
| Screenshot of a failure | `reports/screenshots/<journey>__<person>.png` |

## Order

The journeys share the people they create and run in file order:
`test_journeys.py` (A website and sign-up, B customer app),
`test_journeys_2.py` (D Super Admin, then C Org Admin, which only opens once
D has confirmed the customer's payment, then B's exports, which the paid plan
allows), `test_journeys_3.py` (E partners, F billing, G cross-portal security).
