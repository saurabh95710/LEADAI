"""Throwaway LeadAI server for the Edge E2E journeys.

    python e2e/server.py --port 8765 --mongo mongodb://127.0.0.1:27099

- Database: LeadAI_e2e on a dedicated local mongod (never the .env cluster).
- Apify: only the network boundary (ApifyConnector._call_actor / _read_items)
  is replaced with realistic actor output, exactly like
  tests/test_e2e_lifecycle.py; every route, pipeline stage, filter, scoring
  and database write is the real code.
- Gemini: no key, so comments are scored by the keyword rules.
- Email / SMS / payments: no credentials, so mail waits in the outbox and
  checkout is the mock provider. Nothing leaves the machine.
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

E2E_SUPERADMIN = "e2e-root@leadai.test"
E2E_SUPERADMIN_PASSWORD = "E2e-SuperAdmin-Pass-2026"

NOW = datetime.now(timezone.utc)

# ── Facebook (link search) ───────────────────────────────────────────────────
PAGE_URL = "https://www.facebook.com/metrocarsdealer"
POST_URL = "https://www.facebook.com/metrocarsdealer/posts/555"
PAGE_ITEM = {"pageId": "1001", "title": "Metro Cars", "pageName": "metrocarsdealer",
             "facebookUrl": PAGE_URL, "pageUrl": PAGE_URL, "categories": ["Car dealership"],
             "followers": 12000, "likes": 11000, "intro": "Certified pre-owned cars in Pune",
             "phone": "+91 98765 43210", "email": "sales@metrocars.example"}
POST_ITEM = {"postId": "555", "url": POST_URL,
             "text": "Metro Cars: certified SUVs with easy EMI, visit Metro Cars today",
             "time": (NOW - timedelta(days=2)).isoformat(), "likes": 300, "comments": 6, "shares": 4,
             "pageName": "metrocarsdealer", "facebookUrl": PAGE_URL}
COMMENTS = [
    ("Ravi Kumar", "What is the price of the Creta? Please call me on 9876543210, I want to buy this month"),
    ("Anita Shah", "Interested! Is EMI available? My budget is 8 lakh. Email anita.shah@example.com"),
    ("Joe", "nice car"),
    ("Meera Iyer", "Do you have a test drive in Pune this weekend? I am ready to book"),
    ("Spam Bot", "🔥🔥🔥"),
    ("Link Guy", "https://spam.example/win"),
]


def _fb_comments(post_url):
    return [{"id": f"c{i}", "commentId": f"c{i}", "text": t, "profileName": a,
             "profileUrl": f"https://facebook.com/u{i}", "likesCount": i, "postUrl": post_url,
             "date": (NOW - timedelta(days=1, hours=i)).isoformat()}
            for i, (a, t) in enumerate(COMMENTS)]


# ── Instagram (keyword search) ───────────────────────────────────────────────
def _ig_posts(handle, n, comments, likes=80, days_ago=1):
    return [{"id": f"{handle}-p{i}", "shortCode": f"{handle}P{i}",
             "url": f"https://www.instagram.com/p/{handle}P{i}/",
             "commentsCount": comments, "likesCount": likes,
             "timestamp": (NOW - timedelta(days=days_ago + i)).isoformat()} for i in range(n)]


IG_PROFILES = {
    # passes every filter: big, active, business, has contacts, in Pune
    "punebakehouse": {"username": "punebakehouse", "fullName": "Pune Bake House", "followersCount": 18000,
                      "postsCount": 420, "biography": "Custom cakes in Pune. Call 9822012345",
                      "businessEmail": "orders@punebake.example", "isBusinessAccount": True,
                      "businessCategoryName": "Bakery", "verified": True, "city": "Pune",
                      "url": "https://www.instagram.com/punebakehouse",
                      "latestPosts": _ig_posts("punebakehouse", 12, comments=14)},
    # too few followers
    "tinycakes": {"username": "tinycakes", "fullName": "Tiny Cakes", "followersCount": 300,
                  "postsCount": 40, "biography": "Home baker", "isBusinessAccount": True,
                  "businessCategoryName": "Bakery", "city": "Pune",
                  "url": "https://www.instagram.com/tinycakes",
                  "latestPosts": _ig_posts("tinycakes", 12, comments=2)},
    # big but not a business account; no location data at all, not even a bio
    # (the bio counts as location data), so a location filter is "unknown"
    "cakelover": {"username": "cakelover", "fullName": "Cake Lover", "followersCount": 25000,
                  "postsCount": 900, "biography": "", "isBusinessAccount": False,
                  "url": "https://www.instagram.com/cakelover",
                  "latestPosts": _ig_posts("cakelover", 12, comments=30)},
    # inactive for months
    "oldbakery": {"username": "oldbakery", "fullName": "Old Bakery Mumbai", "followersCount": 9000,
                  "postsCount": 200, "biography": "Mumbai bakery", "isBusinessAccount": True,
                  "businessCategoryName": "Bakery", "city": "Mumbai",
                  "url": "https://www.instagram.com/oldbakery",
                  "latestPosts": _ig_posts("oldbakery", 12, comments=6, days_ago=200)},
}
FB_SEARCH = [{**PAGE_ITEM}, {"pageId": "2002", "title": "Pune Cakes Co", "pageUrl":
                             "https://www.facebook.com/punecakesco", "followers": 4000,
                             "categories": ["Bakery"], "intro": "Cakes, Pune", "address": "Pune"}]


def _ig_comments(post_url):
    return [{"id": f"{post_url}-c{i}", "text": t, "ownerUsername": a.replace(" ", "").lower(),
             "timestamp": (NOW - timedelta(hours=i)).isoformat()} for i, (a, t) in enumerate(COMMENTS)]


def fake_call_actor(self, actor_id, run_input, label, attempts=1, should_abort=None):
    return {"id": f"run-{label}", "status": "SUCCEEDED", "defaultDatasetId": label,
            "_input": run_input, "actId": actor_id}


def fake_read_items(self, run, *, actor_id=None, keyword=None):
    label = run["defaultDatasetId"]
    rin = run.get("_input") or {}
    if label in ("pages-details", "pages"):
        return [PAGE_ITEM]
    if label == "posts":
        return [POST_ITEM]
    if label == "comments":
        urls = [u.get("url") if isinstance(u, dict) else u for u in rin.get("startUrls") or []] or [POST_URL]
        return [c for u in urls for c in _fb_comments(u)]
    if label == "keyword-instagram-search":
        return list(IG_PROFILES.values())
    if label in ("keyword-instagram-posts", "instagram-posts"):
        user = (rin.get("username") or ["punebakehouse"])[0]
        return (IG_PROFILES.get(user) or IG_PROFILES["punebakehouse"])["latestPosts"][: int(rin.get("resultsLimit") or 12)]
    if label == "instagram-profile":
        return [IG_PROFILES["punebakehouse"]]
    if label == "instagram-comments":
        return _ig_comments((rin.get("directUrls") or [""])[0])
    if label == "keyword-facebook-search":
        return FB_SEARCH
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--mongo", default="mongodb://127.0.0.1:27099")
    args = ap.parse_args()
    base = f"http://127.0.0.1:{args.port}"
    os.environ.update({
        "MONGO_URI": args.mongo, "MONGO_DB_NAME": "LeadAI_e2e",
        "SUPERADMIN_EMAIL": E2E_SUPERADMIN, "SUPERADMIN_PASSWORD": E2E_SUPERADMIN_PASSWORD,
        "SESSION_SECRET": "e2e-session-secret-" + "x" * 40,
        "API_KEY_ENCRYPTION_KEY": "e2e-encryption-key-" + "y" * 40,
        "APIFY_API_TOKEN": "apify_e2e_fake_token", "GEMINI_API_KEY": "",
        "PUBLIC_BASE_URL": base, "SESSION_COOKIE_SECURE": "false", "TRUST_PROXY_HEADERS": "false",
        "ALLOWED_ORIGINS": base, "SMTP_HOST": "", "SMTP_USER": "", "SMTP_PASSWORD": "",
        "RAZORPAY_KEY_ID": "", "RAZORPAY_KEY_SECRET": "", "STRIPE_SECRET_KEY": "",
        "TWILIO_ACCOUNT_SID": "", "FAST2SMS_API_KEY": "", "REDIS_URL": "",
    })
    os.chdir(ROOT)
    sys.path.insert(0, ROOT)
    import uvicorn
    with patch("app.connectors.apify_connector.ApifyConnector._get_client", return_value=object()), \
            patch("app.connectors.apify_connector.ApifyConnector.has_token", return_value=True), \
            patch("app.connectors.apify_connector.ApifyConnector._call_actor", fake_call_actor), \
            patch("app.connectors.apify_connector.ApifyConnector._read_items", fake_read_items):
        from app.main import app
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
