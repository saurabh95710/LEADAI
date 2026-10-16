"""Storage of the latest status per (scope, provider) and a short history.

Collections:
  * ``api_limit_status``  _id = "<scope>:<provider>" (e.g. "platform:apify",
    "org:<id>:gemini"), ``kind: "status"``, the latest status; the document
    ``_id: "_meta"`` (``kind: "meta"``) holds the time of the last scheduled run.
  * ``api_limit_history`` one point per check {status_id, scope, provider,
    organization_id, level, metrics: [{key, used, limit, percent}], at};
    a TTL index on ``at`` keeps 30 days.
"""
import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

from app.db.mongo import get_sync_db
from app.limits.model import aware, now_utc, status_id

logger = logging.getLogger(__name__)

STATUS = "api_limit_status"
HISTORY = "api_limit_history"
ALERTS = "api_limit_alerts"
META_ID = "_meta"
HISTORY_DAYS = 30


def _db():
    return get_sync_db()


def public(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """A stored status without Mongo internals (safe for the APIs)."""
    if not doc:
        return None
    out = {k: v for k, v in doc.items() if k not in ("_id", "kind", "updated_at", "_hist")}
    for k in ("checked_at", "next_check_at"):
        d = aware(out.get(k))
        out[k] = d.isoformat() if d else out.get(k)
    block = out.get("block")
    if isinstance(block, dict):
        out["block"] = {k: (aware(v).isoformat() if k in ("until", "at") and aware(v) else v)
                        for k, v in block.items()}
    event = out.get("event")
    if isinstance(event, dict) and aware(event.get("at")):
        out["event"] = {**event, "at": aware(event.get("at")).isoformat()}
    return out


HISTORY_EVERY = timedelta(hours=1)
HISTORY_MIN_PERCENT_CHANGE = 1.0


def _history_due(prev_hist: Optional[Dict[str, Any]], level: Any, percents: Dict[str, Any]) -> bool:
    """A history point is written when the level changed, a percentage moved
    by >= 1 point, or an hour passed since the last point (caps the volume)."""
    if not isinstance(prev_hist, dict):
        return True
    at = aware(prev_hist.get("at"))
    if at is None or now_utc() - at >= HISTORY_EVERY or prev_hist.get("level") != level:
        return True
    old = prev_hist.get("percents") or {}
    for k, v in percents.items():
        o = old.get(k)
        if (v is None) != (o is None):
            return True
        if v is not None and abs(float(v) - float(o)) >= HISTORY_MIN_PERCENT_CHANGE:
            return True
    return False


def save_status(status: Dict[str, Any], *, history: bool = True) -> Dict[str, Any]:
    db = _db()
    if db is None:
        return status
    sid = status_id(status["scope"], status["provider"])
    now = now_utc()
    percents = {m.get("key"): m.get("percent") for m in status.get("metrics") or []}
    try:
        prev = db[STATUS].find_one({"_id": sid}, {"_hist": 1}) or {}
        hist = prev.get("_hist")
        write = history and _history_due(hist, status.get("level"), percents)
        if write:
            hist = {"at": now, "level": status.get("level"), "percents": percents}
        doc = {**status, "kind": "status", "updated_at": now, "_hist": hist}
        db[STATUS].replace_one({"_id": sid}, doc, upsert=True)
        if write:
            db[HISTORY].insert_one({
                "status_id": sid, "scope": status["scope"], "provider": status["provider"],
                "organization_id": status.get("organization_id"), "level": status.get("level"),
                "metrics": [{k: m.get(k) for k in ("key", "used", "limit", "percent")}
                            for m in status.get("metrics") or []],
                "at": now})
    except Exception as e:
        logger.warning("api limit status write failed: %s", e)
    return status


def get_status(scope: str, provider: str) -> Optional[Dict[str, Any]]:
    db = _db()
    if db is None:
        return None
    try:
        return db[STATUS].find_one({"_id": status_id(scope, provider)})
    except Exception:
        return None


def delete_status(scope: str, provider: str) -> None:
    db = _db()
    if db is None:
        return
    try:
        db[STATUS].delete_one({"_id": status_id(scope, provider)})
    except Exception:
        pass


def list_statuses(query: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    db = _db()
    if db is None:
        return []
    q = {"kind": "status", **(query or {})}
    try:
        return list(db[STATUS].find(q))
    except Exception:
        return []


def is_fresh(doc: Optional[Dict[str, Any]], minutes: int) -> bool:
    checked = aware((doc or {}).get("checked_at"))
    return bool(checked) and now_utc() - checked < timedelta(minutes=max(1, minutes)) - timedelta(seconds=5)


def last_run() -> Optional[Dict[str, Any]]:
    db = _db()
    if db is None:
        return None
    try:
        return db[STATUS].find_one({"_id": META_ID})
    except Exception:
        return None


def _ensure_meta(db) -> None:
    try:
        db[STATUS].update_one({"_id": META_ID}, {"$setOnInsert": {"kind": "meta"}}, upsert=True)
    except Exception:
        pass  # another worker created it at the same moment


def claim_scheduled_run(minutes: int) -> bool:
    """Atomically claim the next scheduled pass: True for exactly one caller
    once ``last_run_at`` is older than the interval (multi-worker safe)."""
    db = _db()
    if db is None:
        return False
    _ensure_meta(db)
    now = now_utc()
    cutoff = now - timedelta(minutes=minutes) + timedelta(seconds=30)
    try:
        res = db[STATUS].find_one_and_update(
            {"_id": META_ID, "$or": [{"last_run_at": {"$exists": False}}, {"last_run_at": None},
                                     {"last_run_at": {"$lte": cutoff}}]},
            {"$set": {"last_run_at": now, "kind": "meta"}})
        return res is not None
    except Exception as e:
        logger.debug("api limit run claim failed: %s", e)
        return False


def acquire_lease(seconds: int) -> bool:
    """One "all" pass at a time across workers (``_meta.lease_until``)."""
    db = _db()
    if db is None:
        return True
    _ensure_meta(db)
    now = now_utc()
    try:
        res = db[STATUS].find_one_and_update(
            {"_id": META_ID, "$or": [{"lease_until": {"$exists": False}}, {"lease_until": None},
                                     {"lease_until": {"$lte": now}}]},
            {"$set": {"lease_until": now + timedelta(seconds=seconds)}})
        return res is not None
    except Exception as e:
        logger.debug("api limit lease failed: %s", e)
        return False


def release_lease() -> None:
    db = _db()
    if db is None:
        return
    try:
        db[STATUS].update_one({"_id": META_ID}, {"$set": {"lease_until": None}})
    except Exception:
        pass


def mark_run(scope: str, counts: Dict[str, Any]) -> None:
    db = _db()
    if db is None:
        return
    try:
        db[STATUS].update_one({"_id": META_ID}, {"$set": {
            "kind": "meta", "last_run_at": now_utc(), "last_scope": scope, "counts": counts}}, upsert=True)
    except Exception as e:
        logger.debug("api limit meta write failed: %s", e)


def history(provider: Optional[str], scope: Optional[str], days: int) -> List[Dict[str, Any]]:
    db = _db()
    if db is None:
        return []
    q: Dict[str, Any] = {"at": {"$gte": now_utc() - timedelta(days=max(1, min(days, HISTORY_DAYS)))}}
    if provider:
        q["provider"] = provider
    if scope:
        q["scope"] = scope
    out = []
    try:
        # newest 5000 points, returned oldest first (chart order)
        rows = list(db[HISTORY].find(q).sort("at", -1).limit(5000))
        rows.reverse()
        for d in rows:
            out.append({"at": aware(d.get("at")).isoformat() if aware(d.get("at")) else None,
                        "scope": d.get("scope"), "provider": d.get("provider"),
                        "level": d.get("level"), "metrics": d.get("metrics") or []})
    except Exception as e:
        logger.debug("api limit history read failed: %s", e)
    return out
