"""Durable, multi-process rate limits backed by MongoDB.

Every limiter is a named *bucket* (``login_ip``, ``signup_ip``, ...). Hits
are counted in small fixed slots — one document per ``{bucket, key,
window_start}`` in the ``rate_limits`` collection, bumped with an atomic
``$inc`` upsert — and a request is judged against the sum of the slots that
overlap the sliding window. Because the counters live in Mongo they survive
a restart and are shared by every server process. Each slot carries an
``expires_at`` date so a TTL index (see ``app.db.mongo.ensure_indexes``)
deletes old counters on its own.

Fail-safe: every hit is also mirrored into a small per-process in-memory
sliding window. When MongoDB is unreachable the limiter judges requests
against that local window instead — never "everyone unlimited", never
"everyone locked out" — and stops trying the database for a short
cool-down so a dead cluster does not add a connection timeout to every
login. ``clear()`` resets the in-memory side (the hook tests/conftest.py
calls); the database side of a test is already fresh per test.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

COLLECTION = "rate_limits"
_SLOTS_PER_WINDOW = 10
_DB_RETRY_AFTER_SEC = 30.0          # cool-down after a database failure
_MAX_MEMORY_KEYS = 10000             # cap on the in-memory fallback per bucket

_db_down_until = 0.0
_health_lock = threading.Lock()


def _now() -> float:
    return time.time()


def _mark_db_down(err: Exception) -> None:
    global _db_down_until
    with _health_lock:
        _db_down_until = _now() + _DB_RETRY_AFTER_SEC
    logger.warning("rate limits: MongoDB unavailable, using in-memory limits for %ss (%s)",
                   int(_DB_RETRY_AFTER_SEC), err)


def reset_db_health() -> None:
    """Forget a recorded database outage (tests, admin 'retry now')."""
    global _db_down_until
    with _health_lock:
        _db_down_until = 0.0


def _collection():
    """The Mongo collection, or None while the database is unavailable."""
    if _now() < _db_down_until:
        return None
    try:
        from app.db.mongo import get_sync_db
        db = get_sync_db()
        return db[COLLECTION] if db is not None else None
    except Exception as e:
        _mark_db_down(e)
        return None


class RateLimiter:
    """Sliding-window counter for one bucket. ``limit`` hits per ``window``
    seconds per key; ``slots`` controls the storage granularity."""

    def __init__(self, bucket: str, limit: int, window: int, slots: int = _SLOTS_PER_WINDOW):
        self.bucket = bucket
        self.limit = int(limit)
        self.window = int(window)
        self.slot = max(1, self.window // max(1, slots))
        self._memory: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    # ── in-memory mirror / fallback ────────────────────────────────────
    def _mem_prune(self, key: str, now: float) -> List[float]:
        stamps = [t for t in self._memory.get(key, []) if now - t < self.window]
        if stamps:
            self._memory[key] = stamps
        else:
            self._memory.pop(key, None)
        return stamps

    def _mem_hit(self, key: str, now: float) -> None:
        with self._lock:
            if key not in self._memory and len(self._memory) >= _MAX_MEMORY_KEYS:
                for k in list(self._memory):
                    self._mem_prune(k, now)
                if len(self._memory) >= _MAX_MEMORY_KEYS:  # evict the oldest half
                    oldest = sorted(self._memory, key=lambda k: self._memory[k][-1])
                    for k in oldest[:len(oldest) // 2]:
                        self._memory.pop(k, None)
            self._memory.setdefault(key, []).append(now)

    def _mem_unhit(self, key: str) -> None:
        with self._lock:
            stamps = self._memory.get(key)
            if stamps:
                stamps.pop()

    def _mem_state(self, key: str, now: float) -> tuple[int, Optional[float]]:
        with self._lock:
            stamps = self._mem_prune(key, now)
            return len(stamps), (stamps[-1] if stamps else None)

    # ── database side ─────────────────────────────────────────────────
    def _slot_start(self, now: float) -> int:
        return int(now // self.slot) * self.slot

    def _db_hit(self, coll, key: str, now: float, inc: int = 1) -> None:
        start = self._slot_start(now)
        coll.update_one(
            {"_id": f"{self.bucket}|{key}|{start}"},
            {"$inc": {"count": inc},
             "$max": {"last_at": now},
             "$setOnInsert": {
                 "bucket": self.bucket, "key": key, "window_start": start,
                 "expires_at": datetime.fromtimestamp(start + self.slot + self.window, tz=timezone.utc)}},
            upsert=True)

    def _db_state(self, coll, key: str, now: float) -> tuple[int, Optional[float]]:
        # every slot that overlaps (now - window, now] — conservative by at most one slot
        oldest = self._slot_start(now - self.window)
        total, last = 0, None
        for d in coll.find({"bucket": self.bucket, "key": key, "window_start": {"$gte": oldest}},
                           {"count": 1, "last_at": 1}):
            total += int(d.get("count") or 0)
            la = d.get("last_at")
            if isinstance(la, (int, float)) and (last is None or la > last):
                last = float(la)
        return max(0, total), last

    # ── public API ─────────────────────────────────────────────────────
    def state(self, key: str) -> tuple[int, Optional[float]]:
        """(hits in the current window, timestamp of the latest hit)."""
        now = _now()
        coll = _collection()
        if coll is not None:
            try:
                return self._db_state(coll, key, now)
            except Exception as e:
                _mark_db_down(e)
        return self._mem_state(key, now)

    def count(self, key: str) -> int:
        return self.state(key)[0]

    def hit(self, key: str) -> None:
        """Record one attempt."""
        now = _now()
        self._mem_hit(key, now)
        coll = _collection()
        if coll is not None:
            try:
                self._db_hit(coll, key, now)
            except Exception as e:
                _mark_db_down(e)

    def allowed(self, key: str, limit: Optional[int] = None) -> bool:
        return self.count(key) < (self.limit if limit is None else limit)

    def consume(self, key: str) -> bool:
        """Check-and-record: True (and counted) while under the limit,
        False (not counted) once it is reached. Atomic across processes:
        count first with ``$inc``, roll the hit back when it went over."""
        now = _now()
        coll = _collection()
        if coll is not None:
            try:
                self._db_hit(coll, key, now)
                total, _ = self._db_state(coll, key, now)
                if total > self.limit:
                    self._db_hit(coll, key, now, inc=-1)
                    return False
                self._mem_hit(key, now)
                return True
            except Exception as e:
                _mark_db_down(e)
        count, _ = self._mem_state(key, now)
        if count >= self.limit:
            return False
        self._mem_hit(key, now)
        return True

    def retry_after(self, key: str) -> int:
        """Seconds until the key is under the limit again, measured from
        the latest hit (0 = allowed)."""
        count, last = self.state(key)
        if count < self.limit or last is None:
            return 0
        return max(1, int(self.window - (_now() - last)) + 1)

    def reset(self, key: str) -> None:
        """Forget every hit of one key (e.g. after a successful login)."""
        with self._lock:
            self._memory.pop(key, None)
        coll = _collection()
        if coll is not None:
            try:
                coll.delete_many({"bucket": self.bucket, "key": key})
            except Exception as e:
                _mark_db_down(e)

    def clear(self) -> None:
        """Reset hook: drop the in-memory window (the database side of a
        test run is a fresh in-memory Mongo) and any recorded outage."""
        with self._lock:
            self._memory.clear()
        reset_db_health()

    # dict-ish helpers kept for older callers / debugging
    def __len__(self) -> int:
        return len(self._memory)

    def __contains__(self, key: object) -> bool:
        return key in self._memory
