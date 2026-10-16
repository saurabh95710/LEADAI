"""
LeadAI Durable Job Queue & Worker Architecture (Phase 5).

Replaces non-durable in-process daemon threads with a persistent, recoverable
job queue backed by MongoDB (with optional Redis acceleration when configured).

Key guarantees:
- Durability: Jobs survive process restarts and server crashes.
- Concurrency & Lease Locking: Atomic `find_one_and_update` with heartbeat-based lease expiration.
- Auto-Recovery: Stale or crashed jobs (where lease expired) are automatically reclaimed or retried.
- Multi-Worker Cancellation: Instant cancellation propagation across processes.
- Unchanged API Contract: Keeps existing `/api/url/search`, `/api/search/{run_id}/cancel`,
  and progress polling intact.
"""
import asyncio
import logging
import os
import threading
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any, Optional

from app.db.models import utcnow
from app.db.mongo import get_sync_db

logger = logging.getLogger(__name__)

COLL_JOBS = "job_queue"
_TASK_HANDLERS: dict[str, Callable[..., Any]] = {}
_worker_instance: Optional["DurableQueueWorker"] = None

# Optional Redis connection cache
_redis_client: Any | None = None
_redis_checked: bool = False


def _get_redis() -> Any | None:
    """Return a Redis client if REDIS_URL is configured and redis package is installed."""
    global _redis_client, _redis_checked
    if _redis_checked:
        return _redis_client

    _redis_checked = True
    redis_url = os.environ.get("REDIS_URL")
    if redis_url:
        try:
            import redis
            _redis_client = redis.from_url(redis_url, decode_responses=True)
            _redis_client.ping()
            logger.info("Connected to Redis for durable queue signaling and caching: %s", redis_url.split("@")[-1])
        except Exception as e:  # noqa: BLE001
            logger.warning("Redis configured at %s but connection failed (%s); falling back to MongoDB", redis_url, e)
            _redis_client = None
    return _redis_client


# ── Registration & Handler Dispatch ──────────────────────────────────────────

def register_task_handler(task_name: str, handler: Callable[..., Any]) -> None:
    """Register a callable handler for a given task type."""
    _TASK_HANDLERS[task_name] = handler


# ── Core Queue Operations ────────────────────────────────────────────────────

def enqueue_job(
    task_name: str,
    payload: dict[str, Any],
    *,
    run_id: str | None = None,
    max_retries: int = 3,
    organization_id: str | None = None,
    created_by: str | None = None,
    user_id: str | None = None,
) -> str:
    """Enqueue a job for durable execution. Returns job_id."""
    db = get_sync_db()
    if db is None:
        raise RuntimeError("Database unavailable for job enqueueing")

    job_id = run_id or f"job_{uuid.uuid4().hex}"
    now = utcnow()

    doc = {
        "job_id": job_id,
        "run_id": run_id or job_id,
        "task_name": task_name,
        "payload": payload,
        "status": "queued",
        "retries": 0,
        "max_retries": max_retries,
        "organization_id": str(organization_id) if organization_id else None,
        "created_by": created_by,
        "user_id": str(user_id) if user_id else None,
        "locked_by": None,
        "locked_until": None,
        "heartbeat_at": None,
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "error": None,
        "updated_at": now,
    }

    db[COLL_JOBS].update_one(
        {"job_id": job_id},
        {"$setOnInsert": doc},
        upsert=True,
    )
    logger.info("Durable job %s (%s) enqueued for org %s", job_id, task_name, organization_id)
    return job_id


def claim_inline_job(
    task_name: str,
    payload: dict[str, Any],
    *,
    run_id: str,
    max_retries: int = 3,
    organization_id: str | None = None,
    created_by: str | None = None,
    user_id: str | None = None,
    lease_sec: int = 120,
) -> str | None:
    """Record a job that THIS process runs right now, already claimed.

    The job is stored as ``running`` with a lease owned by a fresh inline
    worker id, so the queue worker never picks it up while it runs (it only
    claims ``queued`` jobs or ``running`` ones whose lease expired). If this
    process dies, the lease lapses and the worker resumes the job.

    Returns the inline worker id when this process owns the job (keep the
    lease alive with :func:`keep_lease_alive`), or None when the job already
    existed — another process is running or has run it, so don't run it again.
    """
    db = get_sync_db()
    if db is None:
        raise RuntimeError("Database unavailable for job enqueueing")
    worker_id = f"inline_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    now = utcnow()
    doc = {
        "job_id": run_id, "run_id": run_id, "task_name": task_name, "payload": payload,
        "status": "running", "retries": 1, "max_retries": max_retries,
        "organization_id": str(organization_id) if organization_id else None,
        "created_by": created_by, "user_id": str(user_id) if user_id else None,
        "locked_by": worker_id, "locked_until": now + timedelta(seconds=lease_sec),
        "heartbeat_at": now, "created_at": now, "started_at": now, "completed_at": None,
        "error": None, "updated_at": now,
    }
    res = db[COLL_JOBS].update_one({"job_id": run_id}, {"$setOnInsert": doc}, upsert=True)
    if res.upserted_id is None:
        logger.warning("Job %s already exists — not running it a second time", run_id)
        return None
    logger.info("Job %s (%s) started inline by %s", run_id, task_name, worker_id)
    return worker_id


class keep_lease_alive:
    """``with keep_lease_alive(job_id, worker_id):`` renews the job's lease
    every ``every_sec`` seconds while the block runs."""

    def __init__(self, job_id: str, worker_id: str, every_sec: float = 30, lease_sec: int = 120):
        self.job_id, self.worker_id = job_id, worker_id
        self.every_sec, self.lease_sec = every_sec, lease_sec
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "keep_lease_alive":
        def _beat():
            while not self._stop.wait(self.every_sec):
                try:
                    heartbeat_job(self.job_id, self.worker_id, self.lease_sec)
                except Exception:  # noqa: BLE001 — a missed beat only shortens the lease
                    logger.debug("Lease renewal failed for %s", self.job_id)
        self._thread = threading.Thread(target=_beat, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._stop.set()


def claim_job(worker_id: str, lock_duration_sec: int = 120) -> dict[str, Any] | None:
    """Atomically claim the next queued or recovered crashed job using lease locking."""
    db = get_sync_db()
    if db is None:
        return None

    now = utcnow()
    lease_until = now + timedelta(seconds=lock_duration_sec)

    # Claim next queued job OR a stalled/orphaned running job whose lease expired
    query = {
        "$or": [
            {"status": "queued"},
            {
                "status": "running",
                "locked_until": {"$lt": now},
                "$expr": {"$lt": ["$retries", "$max_retries"]},
            },
        ]
    }

    update = {
        "$set": {
            "status": "running",
            "locked_by": worker_id,
            "locked_until": lease_until,
            "started_at": now,
            "heartbeat_at": now,
            "updated_at": now,
        },
        "$inc": {"retries": 1},
    }

    # Find oldest job first (FIFO)
    claimed = db[COLL_JOBS].find_one_and_update(
        query,
        update,
        sort=[("created_at", 1)],
        return_document=True,
    )
    return claimed


def heartbeat_job(job_id: str, worker_id: str, extend_sec: int = 120) -> bool:
    """Extend the lease of a currently running job to prevent orphan reclamation."""
    db = get_sync_db()
    if db is None:
        return False

    now = utcnow()
    lease_until = now + timedelta(seconds=extend_sec)

    res = db[COLL_JOBS].update_one(
        {"job_id": job_id, "locked_by": worker_id, "status": "running"},
        {"$set": {"locked_until": lease_until, "heartbeat_at": now, "updated_at": now}},
    )
    return res.modified_count > 0


def complete_job(job_id: str, result: dict[str, Any] | None = None) -> bool:
    """Mark a job as successfully completed."""
    db = get_sync_db()
    if db is None:
        return False

    now = utcnow()
    res = db[COLL_JOBS].update_one(
        {"job_id": job_id},
        {
            "$set": {
                "status": "completed",
                "completed_at": now,
                "result": result,
                "updated_at": now,
            },
            "$unset": {"locked_by": "", "locked_until": ""},
        },
    )
    logger.info("Durable job %s marked as completed", job_id)
    return res.modified_count > 0


def fail_job(job_id: str, error: str, can_retry: bool = True) -> bool:
    """Record job failure. Re-queues if retries are remaining, otherwise marks failed."""
    db = get_sync_db()
    if db is None:
        return False

    now = utcnow()
    job = db[COLL_JOBS].find_one({"job_id": job_id})
    if not job:
        return False

    retries = job.get("retries", 1)
    max_retries = job.get("max_retries", 3)

    if can_retry and retries < max_retries:
        new_status = "queued"
        logger.warning("Job %s failed (%s) — re-queuing (attempt %d/%d)", job_id, error, retries + 1, max_retries)
    else:
        new_status = "failed"
        logger.error("Job %s permanently failed after %d attempts: %s", job_id, retries, error)

    res = db[COLL_JOBS].update_one(
        {"job_id": job_id},
        {
            "$set": {
                "status": new_status,
                "error": str(error),
                "updated_at": now,
            },
            "$unset": {"locked_by": "", "locked_until": ""},
        },
    )
    return res.modified_count > 0


def cancel_job(run_id: str, cancelled_by: str | None = None) -> bool:
    """Request immediate cancellation of a job across all workers."""
    db = get_sync_db()
    now = utcnow()

    # 1. Set in Redis if available for sub-millisecond multi-worker notification
    r = _get_redis()
    if r:
        try:
            r.set(f"cancel:{run_id}", "1", ex=3600)
        except Exception as e:  # noqa: BLE001
            logger.debug("Failed to set Redis cancel flag: %s", e)

    # 2. Update search_history
    if db is not None:
        db.search_history.update_one(
            {"run_id": run_id},
            {
                "$set": {
                    "cancel_requested": True,
                    "status": "cancelled",
                    "phase": "cancelled",
                    "message": "Search cancelled by user",
                    "cancelled_at": now,
                    "updated_at": now,
                }
            },
        )
        # 3. Update job_queue
        db[COLL_JOBS].update_one(
            {"$or": [{"run_id": run_id}, {"job_id": run_id}]},
            {
                "$set": {
                    "status": "cancelled",
                    "cancelled_by": cancelled_by or "user",
                    "completed_at": now,
                    "updated_at": now,
                },
                "$unset": {"locked_by": "", "locked_until": ""},
            },
        )
    logger.info("Cancellation signaled for run %s", run_id)
    return True


def is_job_cancelled(run_id: str) -> bool:
    """Check if cancellation has been requested for a run (checks Redis then Mongo)."""
    # 1. Fast Redis check
    r = _get_redis()
    if r:
        try:
            if r.get(f"cancel:{run_id}") == "1":
                return True
        except Exception as e:  # noqa: BLE001
            logger.debug("Failed to read Redis cancel flag: %s", e)

    # 2. Durable Mongo check
    db = get_sync_db()
    if db is None:
        return False

    doc = db.search_history.find_one({"run_id": run_id}, {"cancel_requested": 1, "status": 1})
    if doc and (doc.get("cancel_requested") or doc.get("status") == "cancelled"):
        return True

    job = db[COLL_JOBS].find_one({"run_id": run_id}, {"status": 1})
    return bool(job and job.get("status") == "cancelled")


def recover_crashed_jobs(max_lease_sec: int = 180) -> int:
    """Reset jobs that were left running by dead workers back to queued or failed."""
    db = get_sync_db()
    if db is None:
        return 0

    now = utcnow()
    stale_cutoff = now - timedelta(seconds=max_lease_sec)

    # 1. Jobs with remaining retries -> re-queue
    requeued = db[COLL_JOBS].update_many(
        {
            "status": "running",
            "locked_until": {"$lt": stale_cutoff},
            "$expr": {"$lt": ["$retries", "$max_retries"]},
        },
        {
            "$set": {"status": "queued", "error": "Recovered from server restart", "updated_at": now},
            "$unset": {"locked_by": "", "locked_until": ""},
        },
    )

    # 2. Jobs with exhausted retries -> fail
    failed = db[COLL_JOBS].update_many(
        {
            "status": "running",
            "locked_until": {"$lt": stale_cutoff},
            "$expr": {"$gte": ["$retries", "$max_retries"]},
        },
        {
            "$set": {"status": "failed", "error": "Worker lease expired; retries exhausted", "updated_at": now},
            "$unset": {"locked_by": "", "locked_until": ""},
        },
    )

    total = requeued.modified_count + failed.modified_count
    if total > 0:
        logger.info("Recovered %d crashed/orphaned jobs (%d requeued, %d marked failed)",
                    total, requeued.modified_count, failed.modified_count)
    return total


def get_queue_stats() -> dict[str, Any]:
    """Return counts of jobs by status."""
    db = get_sync_db()
    if db is None:
        return {"error": "Database unavailable"}

    pipeline = [
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]
    counts = {doc["_id"]: doc["count"] for doc in db[COLL_JOBS].aggregate(pipeline)}
    return {
        "queued": counts.get("queued", 0),
        "running": counts.get("running", 0),
        "completed": counts.get("completed", 0),
        "failed": counts.get("failed", 0),
        "cancelled": counts.get("cancelled", 0),
        "total": sum(counts.values()),
    }


# ── Durable Worker Implementation ────────────────────────────────────────────

class DurableQueueWorker:
    """Continuous worker process that claims and executes jobs from COLL_JOBS."""

    def __init__(self, worker_id: str | None = None, poll_interval_sec: float = 1.0):
        self.worker_id = worker_id or f"worker_{os.getpid()}_{uuid.uuid4().hex[:8]}"
        self.poll_interval = poll_interval_sec
        self.running = False
        self._task: asyncio.Task | None = None
        self._current_job_id: str | None = None

    async def start(self) -> None:
        """Start the worker event loop."""
        self.running = True
        logger.info("Starting DurableQueueWorker %s", self.worker_id)
        # Recover stale jobs from previous unclean shutdowns
        await asyncio.to_thread(recover_crashed_jobs)
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Gracefully stop the worker."""
        self.running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("DurableQueueWorker %s stopped", self.worker_id)

    async def _run_loop(self) -> None:
        while self.running:
            try:
                job = await asyncio.to_thread(claim_job, self.worker_id, 120)
                if job:
                    await self._process_job(job)
                else:
                    await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in DurableQueueWorker loop")
                await asyncio.sleep(self.poll_interval)

    async def _process_job(self, job: dict[str, Any]) -> None:
        job_id = job["job_id"]
        run_id = job.get("run_id") or job_id
        task_name = job["task_name"]
        payload = job.get("payload", {})
        self._current_job_id = job_id

        logger.info("Worker %s executing job %s (%s)", self.worker_id, job_id, task_name)

        # Check early cancellation
        if is_job_cancelled(run_id):
            logger.info("Job %s was cancelled before execution started", job_id)
            await asyncio.to_thread(complete_job, job_id, {"status": "cancelled"})
            return

        # Start background lease renewer
        stop_heartbeat = threading.Event()

        def _heartbeat_worker():
            while not stop_heartbeat.wait(30):
                heartbeat_job(job_id, self.worker_id, 120)

        hb_thread = threading.Thread(target=_heartbeat_worker, daemon=True)
        hb_thread.start()

        handler = _TASK_HANDLERS.get(task_name)
        if not handler:
            stop_heartbeat.set()
            err = f"No handler registered for task type '{task_name}'"
            logger.error(err)
            await asyncio.to_thread(fail_job, job_id, err, can_retry=False)
            return

        try:
            # Execute handler in worker thread pool
            result = await asyncio.to_thread(handler, **payload)
            stop_heartbeat.set()
            await asyncio.to_thread(complete_job, job_id, result if isinstance(result, dict) else None)
        except Exception as e:
            stop_heartbeat.set()
            logger.exception("Error executing task %s for job %s", task_name, job_id)
            await asyncio.to_thread(fail_job, job_id, str(e), can_retry=True)
        finally:
            self._current_job_id = None


def start_queue_worker() -> DurableQueueWorker:
    """Start the singleton durable worker."""
    global _worker_instance
    if _worker_instance is None or not _worker_instance.running:
        _worker_instance = DurableQueueWorker()
        asyncio.create_task(_worker_instance.start())
    return _worker_instance


def stop_queue_worker() -> None:
    """Stop the singleton durable worker if running."""
    global _worker_instance
    if _worker_instance and _worker_instance.running:
        asyncio.create_task(_worker_instance.stop())
        _worker_instance = None


def _handle_url_search(**payload) -> Any:
    from app.social.url_search import run_url_search

    return run_url_search(
        run_id=payload["run_id"],
        initial_url=payload.get("url") or payload.get("initial_url"),
        max_posts=payload.get("max_posts", 10),
        max_comments_per_post=payload.get("max_comments_per_post", 30),
        organization_id=payload.get("organization_id"),
        created_by=payload.get("created_by"),
        user_id=payload.get("user_id"),
    )


register_task_handler("url_search", _handle_url_search)


def _handle_keyword_search(**payload) -> Any:
    """Resume a keyword search whose process died (stages already done are skipped)."""
    from app.discovery.pipeline import run_keyword_search

    return run_keyword_search(payload["run_id"])


register_task_handler("keyword_search", _handle_keyword_search)

