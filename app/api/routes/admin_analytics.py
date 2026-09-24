"""
LeadAI Admin — Analytics & Business Intelligence Routes

Aggregations for SaaS overview, lead funnel, platform comparison,
category breakdown, plan analytics, and performance metrics.
All endpoints support date range filtering (today/7d/30d/custom).
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.roles import require_viewer
from app.db.mongo import get_async_db

router = APIRouter(prefix="/api/admin/analytics", tags=["admin_analytics"])
logger = logging.getLogger(__name__)


async def _db():
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return db


def _day_range(days: int):
    now = datetime.now(timezone.utc)
    end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, end


def _parse_date(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _convert(field: str):
    return {"$convert": {"input": f"${field}", "to": "date", "onError": None, "onNull": None}}


def _range_expr(field: str, start: datetime, end: datetime) -> dict:
    c = _convert(field)
    return {"$and": [{"$gte": [c, start]}, {"$lte": [c, end]}]}


async def _count(db, coll: str, q: dict = None) -> int:
    try:
        return await db[coll].count_documents(q or {})
    except Exception:
        return 0


# ── Overview ─────────────────────────────────────────────────────────────────

@router.get("/overview", dependencies=[Depends(require_viewer)])
async def analytics_overview(
    days: int = Query(30, ge=1, le=365),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
):
    db = await _db()
    start = _parse_date(from_date) or _day_range(days)[0]
    end   = _parse_date(to_date)   or _day_range(days)[1]
    span = (end - start).days + 1
    prev_start = start - timedelta(days=span)
    prev_end   = start - timedelta(seconds=1)

    async def count_in(coll, field, s, e, extra=None):
        match: Dict[str, Any] = {"$expr": _range_expr(field, s, e)}
        if extra:
            match.update(extra)
        return await _count(db, coll, match)

    leads_now  = await count_in("ai_comments", "analyzed_at", start, end, {"is_lead": True})
    leads_prev = await count_in("ai_comments", "analyzed_at", prev_start, prev_end, {"is_lead": True})
    jobs_now   = await count_in("search_history", "created_at", start, end)
    jobs_prev  = await count_in("search_history", "created_at", prev_start, prev_end)
    comments_now = await count_in("ai_comments", "analyzed_at", start, end)

    converted = await _count(db, "ai_comments", {"lifecycle_status": "converted"})
    total_leads_all = await _count(db, "ai_comments", {"is_lead": True})
    conversion_rate = round(converted / total_leads_all * 100, 1) if total_leads_all else None

    def pct_change(n, p):
        return round((n - p) / p * 100, 1) if p > 0 else None

    return {
        "range": {"from": start.isoformat(), "to": end.isoformat(), "days": span},
        "kpis": {
            "leads":      {"value": leads_now,  "prev": leads_prev,  "change": pct_change(leads_now, leads_prev)},
            "jobs":       {"value": jobs_now,   "prev": jobs_prev,   "change": pct_change(jobs_now, jobs_prev)},
            "analyzed":   {"value": comments_now},
            "conversion": {"value": conversion_rate, "unit": "%"},
        },
        "totals": {
            "total_leads":    total_leads_all,
            "total_jobs":     await _count(db, "search_history"),
            "total_comments": await _count(db, "ai_comments"),
            "total_pages":    await _count(db, "facebook_pages"),
            "total_posts":    await _count(db, "facebook_posts"),
        }
    }


# ── Lead Funnel ───────────────────────────────────────────────────────────────

@router.get("/funnel", dependencies=[Depends(require_viewer)])
async def lead_funnel():
    """Lead lifecycle funnel from discovered to converted."""
    db = await _db()
    statuses = ["new", "contacted", "qualified", "follow_up", "converted", "lost", "disqualified", "archived"]
    funnel = []
    total_leads = await _count(db, "ai_comments", {"is_lead": True})
    discovered  = await _count(db, "ai_comments")

    funnel.append({"stage": "Discovered", "count": discovered, "pct": 100})
    funnel.append({"stage": "Leads", "count": total_leads, "pct": round(total_leads / discovered * 100, 1) if discovered else 0})

    for st in statuses:
        n = await _count(db, "ai_comments", {"lifecycle_status": st})
        funnel.append({
            "stage": st.replace("_", " ").title(),
            "count": n,
            "pct": round(n / total_leads * 100, 1) if total_leads else 0,
        })

    return {"funnel": funnel}


# ── Platform Comparison ───────────────────────────────────────────────────────

@router.get("/platforms", dependencies=[Depends(require_viewer)])
async def platform_comparison(days: int = Query(30, ge=1, le=365)):
    db = await _db()
    start, end = _day_range(days)

    pipeline = [
        {"$match": {"$expr": _range_expr("analyzed_at", start, end), "is_lead": True}},
        {"$group": {"_id": "$platform",
                    "leads": {"$sum": 1},
                    "avg_score": {"$avg": "$lead_score"},
                    "with_contact": {"$sum": {"$cond": [{"$or": [
                        {"$regexMatch": {"input": {"$ifNull": ["$phone", ""]},    "regex": r"\S"}},
                        {"$regexMatch": {"input": {"$ifNull": ["$email", ""]},    "regex": r"\S"}},
                        {"$regexMatch": {"input": {"$ifNull": ["$whatsapp", ""]}, "regex": r"\S"}},
                    ]}, 1, 0]}}}},
        {"$sort": {"leads": -1}},
    ]
    rows = [doc async for doc in db.ai_comments.aggregate(pipeline)]
    return {"days": days, "platforms": [{
        "platform":     r["_id"] or "unknown",
        "leads":        r["leads"],
        "avg_score":    round(r["avg_score"] or 0, 1),
        "with_contact": r["with_contact"],
    } for r in rows]}


# ── Category Breakdown ────────────────────────────────────────────────────────

@router.get("/categories", dependencies=[Depends(require_viewer)])
async def category_breakdown(days: int = Query(30, ge=1, le=365)):
    db = await _db()
    start, end = _day_range(days)
    pipeline = [
        {"$match": {"$expr": _range_expr("created_at", start, end)}},
        {"$group": {"_id": "$category", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 20},
    ]
    rows = [doc async for doc in db.facebook_pages.aggregate(pipeline)]
    return {"days": days, "categories": [{"category": r["_id"] or "unknown", "count": r["count"]} for r in rows]}


# ── Lead Score Distribution ───────────────────────────────────────────────────

@router.get("/score-distribution", dependencies=[Depends(require_viewer)])
async def score_distribution():
    db = await _db()
    buckets = [
        ("0–19",   {"$gte": 0,  "$lt": 20}),
        ("20–39",  {"$gte": 20, "$lt": 40}),
        ("40–59",  {"$gte": 40, "$lt": 60}),
        ("60–79",  {"$gte": 60, "$lt": 80}),
        ("80–100", {"$gte": 80, "$lte": 100}),
    ]
    result = []
    for label, score_q in buckets:
        n = await _count(db, "ai_comments", {"is_lead": True, "lead_score": score_q})
        result.append({"bucket": label, "count": n})
    return {"distribution": result}


# ── Performance (job success/failure rates) ───────────────────────────────────

@router.get("/performance", dependencies=[Depends(require_viewer)])
async def performance_analytics(days: int = Query(30, ge=1, le=365)):
    db = await _db()
    start, end = _day_range(days)
    match = {"$expr": _range_expr("created_at", start, end)}

    pipeline = [
        {"$match": match},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]
    status_counts: Dict[str, int] = {}
    async for doc in db.search_history.aggregate(pipeline):
        status_counts[doc["_id"] or "unknown"] = doc["count"]

    total = sum(status_counts.values())
    completed = status_counts.get("completed", 0)

    # Average duration (ms) for completed jobs
    avg_duration_ms = None
    try:
        dur_pipeline = [
            {"$match": {"status": "completed", **match}},
            {"$project": {"dur": {"$subtract": [_convert("completed_at"), _convert("created_at")]}}},
            {"$group": {"_id": None, "avg": {"$avg": "$dur"}}},
        ]
        async for doc in db.search_history.aggregate(dur_pipeline):
            avg_duration_ms = doc.get("avg")
    except Exception as e:
        logger.warning("performance avg_duration failed: %s", e)

    return {
        "days": days,
        "total_jobs": total,
        "success_rate": round(completed / total * 100, 1) if total else None,
        "avg_duration_s": round(avg_duration_ms / 1000, 1) if avg_duration_ms else None,
        "status_breakdown": status_counts,
    }


# ── Daily Activity (time-series) ──────────────────────────────────────────────

@router.get("/activity", dependencies=[Depends(require_viewer)])
async def daily_activity(days: int = Query(30, ge=1, le=90)):
    db = await _db()
    start, end = _day_range(days)

    async def daily(coll, field, extra=None):
        c = _convert(field)
        match: Any = {"$and": [{"$expr": {"$gte": [c, start]}}, {"$expr": {"$lte": [c, end]}}]}
        if extra:
            match["$and"].append(extra)
        pipeline = [
            {"$match": match},
            {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": c}}, "count": {"$sum": 1}}},
            {"$sort": {"_id": 1}},
        ]
        try:
            return {d["_id"]: d["count"] async for d in db[coll].aggregate(pipeline)}
        except Exception:
            return {}

    labels = [(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range((end - start).days + 1)]
    jobs_map    = await daily("search_history", "created_at")
    leads_map   = await daily("ai_comments", "analyzed_at", {"is_lead": True})
    comments_map= await daily("ai_comments", "analyzed_at")

    return {
        "labels": labels,
        "jobs":     [jobs_map.get(d, 0)     for d in labels],
        "leads":    [leads_map.get(d, 0)    for d in labels],
        "comments": [comments_map.get(d, 0) for d in labels],
    }


# ── Plan Analytics ────────────────────────────────────────────────────────────

@router.get("/plans", dependencies=[Depends(require_viewer)])
async def plan_analytics():
    """Subscription plan distribution across organizations."""
    db = await _db()
    try:
        pipeline = [
            {"$group": {"_id": "$plan_id", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ]
        rows = [doc async for doc in db.subscriptions.aggregate(pipeline)]
        return {"plans": [{"plan_id": r["_id"] or "none", "count": r["count"]} for r in rows]}
    except Exception as e:
        logger.warning("plan_analytics failed: %s", e)
        return {"plans": []}
