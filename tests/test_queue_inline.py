"""
A search started from the app runs once: the queue worker must not pick up
the job the request thread is already running (that paid Apify twice), but
must still resume it if the process running it dies.

Run:  python -m pytest tests/test_queue_inline.py -q -p no:cacheprovider
"""
from datetime import timedelta
from unittest.mock import patch

from app.db.models import utcnow
from app.db.mongo import get_sync_db
from app.queue import service as q


def _payload(run_id):
    return {"run_id": run_id, "url": "https://www.facebook.com/acme", "max_posts": 5}


def test_worker_never_claims_a_job_running_inline():
    owner = q.claim_inline_job("url_search", _payload("URL_inline_1"), run_id="URL_inline_1")
    assert owner and owner.startswith("inline_")
    job = get_sync_db()[q.COLL_JOBS].find_one({"job_id": "URL_inline_1"})
    assert job["status"] == "running" and job["locked_by"] == owner
    assert q.claim_job("worker_test") is None


def test_the_same_run_is_never_started_twice():
    assert q.claim_inline_job("url_search", _payload("URL_inline_2"), run_id="URL_inline_2")
    assert q.claim_inline_job("url_search", _payload("URL_inline_2"), run_id="URL_inline_2") is None


def test_a_crashed_inline_run_is_resumed_by_the_worker():
    owner = q.claim_inline_job("url_search", _payload("URL_inline_3"), run_id="URL_inline_3")
    get_sync_db()[q.COLL_JOBS].update_one({"job_id": "URL_inline_3"},
                                          {"$set": {"locked_until": utcnow() - timedelta(seconds=5)}})
    claimed = q.claim_job("worker_test")
    assert claimed and claimed["job_id"] == "URL_inline_3" and claimed["locked_by"] == "worker_test"
    assert owner != "worker_test"


def test_lease_is_renewed_while_the_run_lasts():
    owner = q.claim_inline_job("url_search", _payload("URL_inline_4"), run_id="URL_inline_4", lease_sec=1)
    before = get_sync_db()[q.COLL_JOBS].find_one({"job_id": "URL_inline_4"})["locked_until"]
    import time
    with q.keep_lease_alive("URL_inline_4", owner, every_sec=0.05, lease_sec=120):
        time.sleep(0.3)
    after = get_sync_db()[q.COLL_JOBS].find_one({"job_id": "URL_inline_4"})["locked_until"]
    assert after > before + timedelta(seconds=60)


def test_url_search_thread_runs_the_search_exactly_once():
    from app.social.url_search import UrlSearchThread
    calls = []
    with patch("app.social.url_search.run_url_search", side_effect=lambda *a, **k: calls.append(a) or {"status": "ok"}):
        UrlSearchThread("URL_inline_5", "https://www.facebook.com/acme", 5).run()
        UrlSearchThread("URL_inline_5", "https://www.facebook.com/acme", 5).run()   # duplicate start
    assert len(calls) == 1
    job = get_sync_db()[q.COLL_JOBS].find_one({"job_id": "URL_inline_5"})
    assert job["status"] == "completed"
    assert q.claim_job("worker_test") is None
