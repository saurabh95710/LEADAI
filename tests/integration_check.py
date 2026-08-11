"""
End-to-end pipeline check (no Apify cost): runs the REAL collect_page_posts /
collect_post_comments / collect_run_posts against a scratch Mongo DB with a
stubbed connector, then verifies the spec's business rules end-to-end.
"""
import os
import sys

os.environ["MONGO_URI"] = "mongodb://localhost:27017"
os.environ["MONGO_DB_NAME"] = "LeadAI_qual_test"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bson import ObjectId
from app.agent import search as agent
from app.db.mongo import get_sync_db
from app.db.models import utcnow

db = get_sync_db()
db.facebook_pages.delete_many({})
db.facebook_posts.delete_many({})
db.facebook_comments.delete_many({})

PAGE = {
    "page_name": "Shyam Property Dealer",
    "page_id": "1000",
    "facebook_url": "https://www.facebook.com/shyamprop",
    "category": "Real Estate",
    "followers": 2500,
    "likes": 400,
    "search_run_id": "qual_run_1",
    "search_keyword": "property dealers in kota",
    "city": "Kota",
    "state": "Rajasthan",
    "source_type": "page",
    "provider": "apify",
    "posts_status": "not_started",
    "posts_count": 0,
    "created_at": utcnow(),
    "updated_at": utcnow(),
}
page_id = str(db.facebook_pages.insert_one(PAGE).inserted_id)


def fake_posts(*args, **kwargs):
    items = []
    for i in range(30):
        items.append({
            "id": f"fbid{i}",
            "url": f"https://www.facebook.com/shyamprop/posts/{i}",
            "text": f"2 BHK flat in Kota listing #{i}" if i < 15 else "Happy Diwali wishes",
            "date": "2026-07-01T10:00:00Z" if i < 15 else "2026-01-01T10:00:00Z",
            "likesCount": 50 + i,
            "commentsCount": 20 if i < 5 else (12 if 5 <= i < 10 else 3),
            "sharesCount": 7,
        })
    return items


def fake_comments(*args, **kwargs):
    return [{
        "id": f"cid{k}",
        "postUrl": "https://www.facebook.com/shyamprop/posts/0",
        "url": f"https://www.facebook.com/shyamprop/posts/0?comment_id=cid{k}",
        "profileName": f"Buyer {k}",
        "profileUrl": f"https://www.facebook.com/profile.php?id={1000 + k}",
        "text": "interested, call me please",
        "date": "2026-07-02T10:00:00Z",
    } for k in range(37)]


class StubConnector:
    def scrape_facebook_posts(self, urls, posts_per_page=20):
        return fake_posts()

    def scrape_facebook_comments(self, urls, comments_per_post=200):
        return fake_comments()


agent.get_connector = lambda provider=None: StubConnector()

# the AI analysis step is a separate pipeline; stub it for this pipeline test
import app.pipeline.comment_ai as comment_ai
comment_ai.analyze_comments_for_post = lambda post_id: None


def check(label, cond):
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        sys.exit(1)


r = agent.collect_page_posts(page_id, 30)
page = db.facebook_pages.find_one({"_id": ObjectId(page_id)})
check("posts status completed", page["posts_status"] == "completed")
check("total_posts_found == 30", page["total_posts_found"] == 30)
check("relevant_posts_count == 15", page["relevant_posts_count"] == 15)
check("qualifying_posts_count == 10", page["qualifying_posts_count"] == 10)
check("comments_on_qualifying == 10*12+20*5... ",
      page["total_comments_on_qualifying_posts"] == 5 * 20 + 5 * 12)
check("has_qualifying_posts", page["has_qualifying_posts"] is True)
check("activity active", page["activity_status"] == "active")
check("lead_score > 0", page["lead_score"] > 0)

posts = list(db.facebook_posts.find({"page_ref": page_id}))
qual = [p for p in posts if p["is_qualifying"]]
low = [p for p in posts if not p["is_qualifying"]]
check("10 qualifying / 20 not", len(qual) == 10 and len(low) == 20)
check("irrelevant posts never qualify", all(p["is_relevant"] is False for p in low[15:]))
check("all posts stored (no 100-threshold deletion)", len(posts) == 30)

# comments: scrape the TOP qualifying post (37 comments on post #0? no — 20)
top = sorted(qual, key=lambda p: p["total_comment_count"], reverse=True)[0]
r2 = agent.collect_post_comments(str(top["_id"]), 200)
post_after = db.facebook_posts.find_one({"_id": top["_id"]})
check("comments completed", post_after["comments_status"] == "completed")
check("scraped_comment_count == 37", post_after["scraped_comment_count"] == 37)
check("total_comment_count untouched (20)", post_after["total_comment_count"] == 20)
check("comments_count alias untouched (20)", post_after["comments_count"] == 20)
check("37 comments stored", db.facebook_comments.count_documents({"post_ref": str(top["_id"])}) == 37)

# non-qualifying post → scrape is refused
low_post = sorted(low, key=lambda p: p["total_comment_count"] or 0)[-1]
r3 = agent.collect_post_comments(str(low_post["_id"]), 200)
check("low-comment post skipped", r3["status"] == "skipped" or "Not scraped" in r3["message"])
low_after = db.facebook_posts.find_one({"_id": low_post["_id"]})
check("skipped persisted", low_after["comments_status"] == "skipped")
check("no comments stored for skipped post",
      db.facebook_comments.count_documents({"post_ref": str(low_post["_id"])}) == 0)

# collect_run_posts auto-comments: only qualifying posts get scraped (the
# manually-scraped post occupies one slot; completed posts are skipped)
before = {p["_id"] for p in db.facebook_posts.find({"page_ref": page_id, "scraped_comment_count": {"$gt": 0}})}
r4 = agent.collect_run_posts("qual_run_1", max_posts=30, auto_comments=3)
scraped_posts = list(db.facebook_posts.find({"page_ref": page_id, "scraped_comment_count": {"$gt": 0}}))
newly = [p for p in scraped_posts if p["_id"] not in before]
check("auto-comments ran on exactly 2 new posts", len(newly) == 2)
check("scraped posts are qualifying", all(p["is_qualifying"] for p in scraped_posts))
check("run finished", r4["status"] == "completed")

db.facebook_pages.delete_many({})
db.facebook_posts.delete_many({})
db.facebook_comments.delete_many({})
print("INTEGRATION CHECK PASSED — all business rules hold end-to-end")
