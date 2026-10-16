"""Apify spend from LeadAI's own records (no network).

Primary source: ``apify_jobs`` — one row per actor run with
``usage_total_usd`` (float; older rows: numeric ``usage_usd``). Searches
whose runs were never recorded there (older data) fall back to the numeric
``search_history.scrape_info.usageTotalUsd`` / ``usageUsd``. A search is
counted from one source only, so partial coverage never double counts or
under-counts.
"""
from datetime import datetime
from typing import Any, Dict, Optional


def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def job_cost(job: Dict[str, Any]) -> Optional[float]:
    for key in ("usage_total_usd", "usage_usd"):
        n = _num(job.get(key))
        if n is not None:
            return n
    return None


def scrape_info_cost(info: Dict[str, Any]) -> Optional[float]:
    """Numeric cost of a search's ``scrape_info`` (objects are ignored)."""
    for key in ("usageTotalUsd", "usageUsd", "usage_usd"):
        n = _num((info or {}).get(key))
        if n is not None:
            return n
    return None


async def apify_spend(db, since: datetime) -> Dict[str, Any]:
    """{total, runs, runs_with_usage, per_actor: {actor: {runs, cost}}} since ``since``."""
    total = 0.0
    runs = with_usage = 0
    per_actor: Dict[str, Dict[str, Any]] = {}
    covered: set = set()

    def add(actor: str, cost: Optional[float]) -> None:
        nonlocal total, runs, with_usage
        entry = per_actor.setdefault(actor or "unknown", {"runs": 0, "cost": 0.0})
        entry["runs"] += 1
        runs += 1
        if cost is not None:
            entry["cost"] = round(entry["cost"] + cost, 6)
            total += cost
            with_usage += 1

    async for j in db.apify_jobs.find({"created_at": {"$gte": since}},
                                      {"actor_id": 1, "search_run_id": 1, "usage_total_usd": 1, "usage_usd": 1}):
        if j.get("search_run_id"):
            covered.add(str(j["search_run_id"]))
        add(str(j.get("actor_id") or "unknown"), job_cost(j))
    async for s in db.search_history.find({"created_at": {"$gte": since}, "scrape_info": {"$exists": True}},
                                          {"run_id": 1, "scrape_info": 1}):
        if s.get("run_id") and str(s["run_id"]) in covered:
            continue
        info = s.get("scrape_info") or {}
        add(str(info.get("actorId") or "unknown"), scrape_info_cost(info))
    return {"total": round(total, 4), "runs": runs, "runs_with_usage": with_usage, "per_actor": per_actor}
