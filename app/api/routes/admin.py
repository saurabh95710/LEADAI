"""
Admin Control Center API.

Every endpoint under /api/admin is role-protected (viewer / manager /
super_admin) and re-reads the account's role from the database on each
request, so role changes apply immediately. Settings changes are written
through :mod:`app.admin.settings` and audited via :mod:`app.admin.audit`.

Read endpoints: GET (viewer+)
Operational writes: POST/PUT/DELETE for settings, jobs, actors (manager+)
User management & security: super_admin only.

Secrets policy: the Apify token and password hashes are never returned;
only masked hints (``apify.token.masked``) are exposed.
"""
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from app.admin import settings as s
from app.admin import audit as a
from app.admin import envvars as ev
from app.auth.roles import (require_env_unlocked, require_manager,
                            require_super, require_viewer)
from app.config import get_settings
from app.db.mongo import get_async_db
from app.db.models import utcnow

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])
settings = get_settings()


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Shared helpers
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def _db():
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return db


def _iso(value: Optional[str]) -> Optional[datetime]:
    """ISO date/time â†’ aware datetime (naive input is treated as UTC)."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def _date_filter(from_date: Optional[str], to_date: Optional[str]) -> dict:
    """$gte/$lte range on created_at from ISO date params (inclusive day)."""
    query: Dict[str, Any] = {}
    if from_date:
        start = _iso(from_date)
        if start is not None:
            query["$gte"] = start
    if to_date:
        end = _iso(to_date)
        if end is not None:
            query["$lte"] = end
    return {"created_at": query} if query else {}


def _pagination(offset: int, limit: int):
    return max(0, offset), min(max(1, limit), 200)


def _serialize_oid(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _serialize_oid(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize_oid(v) for v in value]
    return value


def _lead_statuses() -> List[str]:
    return ["new", "contacted", "qualified", "converted", "ignored"]


def _score_bucket(score: Optional[Any]) -> str:
    """Lead-score bucket label used by the analytics distribution."""
    if score is None:
        return "no score"
    try:
        score = float(score)
    except (TypeError, ValueError):
        return "no score"
    if score >= 80:
        return "80–100"
    if score >= 60:
        return "60–79"
    if score >= 40:
        return "40–59"
    if score >= 20:
        return "20–39"
    return "0–19"


def _contact_query() -> dict:
    """$or filter for docs carrying a real (non-empty) contact string."""
    return {"$or": [
        {"phone": {"$regex": r"\S"}},
        {"email": {"$regex": r"\S"}},
        {"whatsapp": {"$regex": r"\S"}},
    ]}


async def _count(db, coll: str, query: Optional[dict] = None) -> int:
    try:
        return await db[coll].count_documents(query or {})
    except Exception:
        return 0


async def _platform_stats(db, platform: str) -> Dict[str, Any]:
    return {
        "pages": await _count(db, "facebook_pages", {"platform": platform}),
        "posts": await _count(db, "facebook_posts", {"platform": platform}),
        "comments": await _count(db, "facebook_comments", {"platform": platform}),
        "leads": await _count(db, "ai_comments", {"platform": platform, "is_lead": True}),
    }


async def _actor_info(key: str) -> Dict[str, Any]:
    """Current value + where it comes from (DB override vs env default)."""
    value = await s.aget_setting(key)
    default = s.SETTING_DEFAULTS.get(key)
    doc = None
    try:
        db = get_async_db()
        if db is not None:
            doc = await db[s.COLLECTION].find_one({"_id": key},
                                                  {"updated_at": 1, "updated_by": 1})
    except Exception:
        pass
    return {
        "key": key,
        "value": value,
        "default": default,
        "overridden": value != default and doc is not None,
        "updated_at": (doc or {}).get("updated_at"),
        "updated_by": (doc or {}).get("updated_by"),
    }


def _day_range(days: int) -> tuple:
    """Start/end datetimes for the last N calendar days (UTC)."""
    now = datetime.now(timezone.utc)
    end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    start = (now - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return start, end


def _range_query(field: str, start: datetime, end: datetime,
                 extra: Optional[dict] = None) -> Dict[str, Any]:
    """Query for docs whose `field` (BSON date OR ISO string — legacy rows)
    falls inside [start, end], with optional extra conditions."""
    expr = {"$and": [
        {"$gte": [{"$convert": {"input": f"${field}", "to": "date",
                                "onError": None, "onNull": None}}, start]},
        {"$lte": [{"$convert": {"input": f"${field}", "to": "date",
                                "onError": None, "onNull": None}}, end]},
    ]}
    if extra:
        return {"$and": [{"$expr": expr}, extra]}
    return {"$expr": expr}


def _date_key(value: Any) -> datetime:
    """Best-effort datetime from either a BSON date or ISO string."""
    if isinstance(value, datetime):
        return value
    try:
        return _iso(str(value)) or datetime.now(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


async def _daily_counts(db, coll: str, field: str, start: datetime,
                        end: datetime, extra: Optional[dict] = None
                        ) -> List[Dict[str, Any]]:
    """Daily counts for `field` between start/end. Handles mixed string/date
    storage via $convert; never raises on schema drift."""
    converted = {"$convert": {"input": f"${field}", "to": "date",
                              "onError": None, "onNull": None}}
    match: Dict[str, Any] = {"$and": [
        {"$expr": {"$gte": [converted, start]}},
        {"$expr": {"$lte": [converted, end]}},
    ]}
    if extra:
        match["$and"].append(extra)
    pipeline = [
        {"$match": match},
        {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": converted}},
                    "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]
    try:
        return [{"date": d["_id"], "count": d["count"]}
                async for d in db[coll].aggregate(pipeline)]
    except Exception as e:
        logger.warning("daily_counts failed for %s.%s: %s", coll, field, e)
        return []


def _fill_daily(series: List[Dict[str, Any]], start: datetime, end: datetime
                ) -> Dict[str, int]:
    """Zero-filled {date: count} map for every day in [start, end]."""
    out: Dict[str, int] = {}
    day = start
    while day <= end:
        key = day.strftime("%Y-%m-%d")
        out[key] = 0
        day += timedelta(days=1)
    for item in series:
        out[item["date"]] = item.get("count", 0)
    return out


def _pct_change(current: int, previous: int) -> Optional[float]:
    if previous <= 0:
        return None
    return round((current - previous) / previous * 100, 1)


async def _derive_alerts(db, start: datetime, end: datetime) -> List[Dict[str, Any]]:
    """Data-driven alerts for the dashboard: every message comes from a real
    query against the database or configured settings."""
    alerts = []
    running = await _count(db, "search_history", {"status": "running"})
    if running:
        alerts.append({
            "id": "jobs-running", "severity": "info",
            "title": f"{running} job{'s' if running != 1 else ''} running",
            "message": "Active scrapes in progress.",
            "route": "#/jobs?status=running",
        })
    failed_window = await _count(db, "search_history", _range_query(
        "created_at", datetime.now(timezone.utc) - timedelta(days=1),
        datetime.now(timezone.utc), {"status": "error"}))
    if failed_window:
        alerts.append({
            "id": "recent-failures", "severity": "warn",
            "title": f"{failed_window} failed in the last 24h",
            "message": "Scrape runs ended in error. Review the failures.",
            "route": "#/failed",
        })
    if not s.get_apify_token():
        alerts.append({
            "id": "apify-missing", "severity": "warn",
            "title": "Apify token not configured",
            "message": "Scraping cannot start without an Apify token.",
            "route": "#/apify",
        })
    elif await s.aget_setting("apify.last_test_ok") is None:
        alerts.append({
            "id": "apify-untested", "severity": "info",
            "title": "Apify connection untested",
            "message": "Run a connection test to verify the token works.",
            "route": "#/apify",
        })
    if not ev.get_envvar_str("GEMINI_API_KEY", settings.gemini_api_key):
        alerts.append({
            "id": "gemini-missing", "severity": "warn",
            "title": "Gemini key not set",
            "message": "AI analysis falls back to rule-based scoring only.",
            "route": "#/ai",
        })
    new_leads = await _count(db, "ai_comments", _range_query(
        "analyzed_at", start, end, {"is_lead": True}))
    if new_leads:
        alerts.append({
            "id": "new-leads", "severity": "ok",
            "title": f"{new_leads} new lead{'s' if new_leads != 1 else ''} in range",
            "message": "Qualified contacts captured.",
            "route": "#/leads",
        })
    if await s.aget_setting("maintenance.enabled"):
        alerts.append({
            "id": "maintenance-on", "severity": "warn",
            "title": "Maintenance mode is active",
            "message": "Public API operations are disabled until turned off.",
            "route": "#/maintenance",
        })
    return alerts


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# DASHBOARD
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get("/dashboard", dependencies=[Depends(require_viewer)])
async def dashboard(days: int = Query(30, ge=1, le=365),
                    from_date: Optional[str] = Query(None),
                    to_date: Optional[str] = Query(None)):
    db = await _db()
    if from_date or to_date:
        start = _iso(from_date) or (datetime.now(timezone.utc) -
                                    timedelta(days=29)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        end = _iso(to_date) or datetime.now(timezone.utc)
        end = end.replace(hour=23, minute=59, second=59, microsecond=999999)
    else:
        start, end = _day_range(days)
    span = (end - start).days + 1
    prev_start = start - timedelta(days=span)
    prev_end = start - timedelta(days=1)

    # Daily series per collection (handles mixed string/date storage).
    daily = {}
    for key, (coll, field, extra) in {
        "searches": ("search_history", "created_at", None),
        "pages": ("facebook_pages", "collected_at", None),
        "posts": ("facebook_posts", "collected_at", None),
        "comments": ("facebook_comments", "created_at", None),
        "leads": ("ai_comments", "analyzed_at", {"is_lead": True}),
    }.items():
        counts = await _daily_counts(db, coll, field, start, end, extra)
        daily[key] = _fill_daily(counts, start, end)
    labels = [(start + timedelta(days=i)).strftime("%Y-%m-%d")
              for i in range(span)]
    series = lambda key: [daily[key][d] for d in labels]

    async def window_total(coll, field, s, e, extra=None):
        return sum(c["count"] for c in
                   await _daily_counts(db, coll, field, s, e, extra))

    def kpi(key, label, value, previous, change, key_series):
        return {"key": key, "label": label, "value": value,
                "previous": previous, "change": change,
                "series": key_series}

    searches_prev = await window_total("search_history", "created_at",
                                       prev_start, prev_end)
    searches_now = await window_total("search_history", "created_at",
                                      start, end)
    leads_prev = await window_total("ai_comments", "analyzed_at",
                                    prev_start, prev_end, {"is_lead": True})
    leads_now = await window_total("ai_comments", "analyzed_at",
                                   start, end, {"is_lead": True})
    kpis = [
        kpi("searches", "Searches", searches_now, searches_prev,
            _pct_change(searches_now, searches_prev), series("searches")),
        kpi("running", "Running",
            await _count(db, "search_history", {"status": "running"}),
            0, None, series("searches")),
        kpi("failed", "Failed",
            await _count(db, "search_history", {"status": "error"}),
            0, None, series("searches")),
        kpi("pages", "Pages",
            await _count(db, "facebook_pages"), 0, None, series("pages")),
        kpi("posts", "Posts",
            await _count(db, "facebook_posts"), 0, None, series("posts")),
        kpi("comments", "Comments",
            await _count(db, "facebook_comments"), 0, None,
            series("comments")),
        kpi("analyzed", "AI Analyzed",
            await _count(db, "ai_comments"), 0, None, series("leads")),
        kpi("leads", "Leads", leads_now, leads_prev,
            _pct_change(leads_now, leads_prev), series("leads")),
    ]

    leads_by_platform = []
    try:
        async for doc in db.ai_comments.aggregate([
            {"$match": _range_query("analyzed_at", start, end,
                                    {"is_lead": True})},
            {"$group": {"_id": "$platform", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ]):
            leads_by_platform.append(
                {"platform": doc["_id"] or "other", "count": doc.get("count", 0)})
    except Exception as e:
        logger.warning("dashboard leads_by_platform failed: %s", e)

    performance = {"success_rate": None, "avg_duration_s": None,
                   "leads": leads_now, "analyzed":
                   await _count(db, "ai_comments")}
    try:
        statuses = {}
        async for doc in db.search_history.aggregate([
            {"$match": _range_query("created_at", start, end)},
            {"$group": {"_id": "$status", "count": {"$sum": 1}}},
        ]):
            statuses[doc["_id"] or "unknown"] = doc.get("count", 0)
        total = sum(statuses.values())
        completed = statuses.get("completed", 0)
        performance["success_rate"] = round(completed / total * 100, 1) \
            if total else None
        async for doc in db.search_history.aggregate([
            {"$match": {"status": "completed",
                        "$expr": {"$and": [
                            {"$gte": [{"$convert": {"input": "$created_at",
                                                    "to": "date",
                                                    "onError": None,
                                                    "onNull": None}}, start]},
                            {"$lte": [{"$convert": {"input": "$created_at",
                                                    "to": "date",
                                                    "onError": None,
                                                    "onNull": None}}, end]}]}}},
            {"$project": {"dur": {"$subtract": [
                {"$convert": {"input": "$completed_at", "to": "date",
                              "onError": None, "onNull": None}},
                {"$convert": {"input": "$created_at", "to": "date",
                              "onError": None, "onNull": None}}]}}},
            {"$group": {"_id": None, "avg": {"$avg": "$dur"}}},
        ]):
            avg = doc.get("avg")
            performance["avg_duration_s"] = round(avg, 1) \
                if avg is not None else None
    except Exception as e:
        logger.warning("dashboard performance failed: %s", e)

    recent = []
    async for run in (db.search_history.find()
                      .sort("created_at", -1).limit(8)):
        recent.append(_serialize_oid(run))

    by_platform = []
    for platform in s.PLATFORMS:
        stats = await _platform_stats(db, platform)
        stats["platform"] = platform
        stats["enabled"] = await s.aget_setting(f"platform.{platform}.enabled")
        by_platform.append(stats)

    apify_token_hint = s.get_apify_token_hint()

    # Keyword Filter (Comment Scraping & Keyword Intelligence) card:
    # active rule + pipeline totals. Cost figures are never fabricated —
    # only comment counts are reported.
    comment_filter_block: Dict[str, Any] = {
        "active_rule": None,
        "active_rule_id": None,
        "totals": {"comments": 0, "filtered": 0, "matched": 0,
                   "not_matched": 0, "no_filter": 0, "coverage_pct": None},
    }
    try:
        from app.pipeline import comment_filter as cfilter
        active = await db[cfilter.RULES_COLLECTION].find_one({"active": True})
        if active:
            comment_filter_block["active_rule"] = _serialize_oid(active)
            comment_filter_block["active_rule_id"] = str(active["_id"])
        total = await _count(db, "facebook_comments")
        matched = await _count(
            db, "facebook_comments",
            {"keyword_filter_status": cfilter.STATUS_MATCHED})
        not_matched = await _count(
            db, "facebook_comments",
            {"keyword_filter_status": cfilter.STATUS_NOT_MATCHED})
        no_filter = await _count(
            db, "facebook_comments",
            {"keyword_filter_status": cfilter.STATUS_NO_FILTER})
        filtered = matched + not_matched + no_filter
        comment_filter_block["totals"] = {
            "comments": total, "filtered": filtered,
            "matched": matched, "not_matched": not_matched,
            "no_filter": no_filter,
            "coverage_pct": round(filtered / total * 100, 1) if total else None,
        }
    except Exception as e:
        logger.warning("dashboard comment_filter block failed: %s", e)

    return {
        "range": {
            "from": start.isoformat(), "to": end.isoformat(), "days": span,
            "label": f"{start.strftime('%b %d')} – {end.strftime('%b %d, %Y')}",
        },
        "kpis": kpis,
        "activity": {"labels": labels,
                     "searches": series("searches"),
                     "pages": series("pages"),
                     "posts": series("posts"),
                     "comments": series("comments"),
                     "leads": series("leads")},
        "leads_by_platform": leads_by_platform,
        "performance": performance,
        "alerts": await _derive_alerts(db, start, end),
        "counts": {
            "jobs_total": searches_now,
            "jobs_today": sum(series("searches")[-1:]),
            "jobs_running": kpis[1]["value"], "jobs_failed": kpis[2]["value"],
            "pages": kpis[3]["value"], "posts": kpis[4]["value"],
            "comments": kpis[5]["value"], "analyzed": kpis[6]["value"],
            "leads": leads_now, "leads_contact": await _count(
                db, "ai_comments", _contact_query()),
        },
        "platforms": by_platform,
        "recent_jobs": recent,
        "comment_filter": comment_filter_block,
        "status": {
            "database": True,
            "apify_token": bool(apify_token_hint),
            "apify_token_hint": apify_token_hint,
            "gemini_key": bool(ev.get_envvar_str("GEMINI_API_KEY", settings.gemini_api_key)),
            "maintenance": await s.aget_setting("maintenance.enabled"),
            "url_search_enabled": await s.aget_setting("features.url_search.enabled"),
        },
    }


@router.get("/alerts", dependencies=[Depends(require_viewer)])
async def alerts():
    """Notifications for the topbar bell: last-24h window."""
    db = await _db()
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=1)
    return {"alerts": await _derive_alerts(db, start, now),
            "checked_at": time.time()}


@router.get("/search", dependencies=[Depends(require_viewer)])
async def global_search(q: str = Query(..., min_length=2, max_length=80)):
    """Global admin search across jobs, leads, pages, posts, comments and
    platform names. Each group is capped at 5 results."""
    db = await _db()
    needle = re.escape(q)
    result: Dict[str, Any] = {"query": q}

    jobs = [doc async for doc in db.search_history.find(
        {"$or": [{"query": {"$regex": needle, "$options": "i"}},
                 {"run_id": {"$regex": needle, "$options": "i"}}]},
        {"run_id": 1, "query": 1, "status": 1}).sort("created_at", -1).limit(5)]
    result["jobs"] = _serialize_oid([{**j, "query": j.get("query") or ""}
                                     for j in jobs])

    leads = [doc async for doc in db.ai_comments.find(
        {"$or": [{"commenter_name": {"$regex": needle, "$options": "i"}},
                 {"comment_text": {"$regex": needle, "$options": "i"}},
                 {"phone": {"$regex": needle}},
                 {"email": {"$regex": needle, "$options": "i"}}]},
        {"_id": 1, "commenter_name": 1, "intent": 1, "lead_score": 1})
        .sort("lead_score", -1).limit(5)]
    result["leads"] = _serialize_oid(leads)

    pages = [doc async for doc in db.facebook_pages.find(
        {"$or": [{"page_name": {"$regex": needle, "$options": "i"}},
                 {"category": {"$regex": needle, "$options": "i"}},
                 {"city": {"$regex": needle, "$options": "i"}}]},
        {"page_name": 1, "category": 1, "city": 1}).limit(5)]
    result["pages"] = _serialize_oid(pages)

    posts = [doc async for doc in db.facebook_posts.find(
        {"$or": [{"caption": {"$regex": needle, "$options": "i"}},
                 {"page_name": {"$regex": needle, "$options": "i"}}]},
        {"caption": 1, "page_name": 1}).sort("published_date", -1).limit(5)]
    result["posts"] = _serialize_oid(posts)

    comments = [doc async for doc in db.ai_comments.find(
        {"$or": [{"comment_text": {"$regex": needle, "$options": "i"}},
                 {"author_name": {"$regex": needle, "$options": "i"}}]},
        {"comment_text": 1, "author_name": 1}).sort("lead_score", -1).limit(5)]
    result["comments"] = _serialize_oid(comments)

    result["platforms"] = [p for p in s.PLATFORMS if q.lower() in p]
    return {"results": result}


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# JOBS â€” list / detail / retry / cancel / delete
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get("/jobs", dependencies=[Depends(require_viewer)])
async def list_jobs(
    status: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
):
    db = await _db()
    query: Dict[str, Any] = {}
    if status and isinstance(status, str):
        query["status"] = status
    if platform and isinstance(platform, str):
        query["platform"] = platform
    if q and isinstance(q, str):
        query["$or"] = [{"query": {"$regex": re.escape(q), "$options": "i"}},
                        {"run_id": {"$regex": re.escape(q), "$options": "i"}}]
    query.update(_date_filter(from_date, to_date))
    offset, limit = _pagination(offset, limit)
    rows = [doc async for doc in
            db.search_history.find(query).sort("created_at", -1)
            .skip(offset).limit(limit)]
    return {
        "items": [_serialize_oid(r) for r in rows],
        "total": await _count(db, "search_history", query),
        "offset": offset, "limit": limit,
    }


@router.get("/jobs/{run_id}", dependencies=[Depends(require_viewer)])
async def job_detail(run_id: str):
    db = await _db()
    doc = await db.search_history.find_one({"run_id": run_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Search run not found")
    counts: Dict[str, Any] = {"pages": 0, "posts": 0, "comments": 0,
                              "analyzed": 0, "leads": 0}
    try:
        page_ids = [p["_id"] async for p in
                    db.facebook_pages.find({"search_run_id": run_id},
                                           {"_id": 1})]
        counts["pages"] = len(page_ids)
        post_query: Dict[str, Any] = {}
        if page_ids:
            post_query["$or"] = [
                {"page_ref": {"$in": [str(pid) for pid in page_ids]}},
                {"collection_run_id": run_id},
            ]
        else:
            post_query["collection_run_id"] = run_id
        post_ids = [p["_id"] async for p in
                    db.facebook_posts.find(post_query, {"_id": 1})]
        counts["posts"] = len(post_ids)
        comment_refs = []
        if post_ids:
            comment_ids = [c["_id"] async for c in
                           db.facebook_comments.find({"post_ref": {
                               "$in": [str(pid) for pid in post_ids]}}, {"_id": 1})]
            comment_refs = [str(cid) for cid in comment_ids]
            counts["comments"] = len(comment_refs)
        if comment_refs:
            counts["analyzed"] = await _count(
                db, "ai_comments", {"comment_ref": {"$in": comment_refs}})
            counts["leads"] = await _count(
                db, "ai_comments",
                {"comment_ref": {"$in": comment_refs}, "is_lead": True})
    except Exception as e:
        logger.warning("job_detail child counts failed for %s: %s", run_id, e)
    return {"job": _serialize_oid(doc), "counts": counts}


async def _retry_job(run_id: str, admin: dict) -> Dict[str, Any]:
    """Re-run a URL search with a fresh run_id, preserving the original URL
    and limits (admin-managed caps are re-applied)."""
    db = await _db()
    doc = await db.search_history.find_one({"run_id": run_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Search run not found")
    if not doc.get("url_search"):
        raise HTTPException(status_code=400,
                            detail="Only URL-based search runs can be retried")
    intent = doc.get("intent") or {}
    url = intent.get("canonical_url") or doc.get("query")
    if not url:
        raise HTTPException(status_code=400, detail="Run has no search URL")

    from app.social.url_search import UrlSearchThread
    from app.api.routes.search import _start

    lim = s.effective_limits()
    max_posts = min(int(intent.get("limit") or lim["max_posts_default"]),
                    lim["max_posts_cap"])
    max_comments = min(int(intent.get("max_comments_per_post")
                           or lim["max_comments_per_post_default"]),
                       lim["max_comments_per_post_cap"], lim["global_max_comments"])

    new_run_id = (f"URL{datetime.now().strftime('%Y%m%d%H%M%S')}"
                  f"{abs(hash(url + str(time.time()))) % 1000:03d}")
    await db.search_history.insert_one({
        "run_id": new_run_id, "query": url, "intent": {
            "keyword": url, "type": "url", "platform": intent.get("platform"),
            "canonical_url": url, "limit": max_posts,
            "max_comments_per_post": max_comments},
        "limit": max_posts, "provider": "apify", "url_search": True,
        "status": "running", "phase": "queued",
        "message": "Retried from admin panel",
        "pages_found": 0, "pages_stored": 0,
        "created_at": utcnow(), "completed_at": None,
        "retried_from": run_id,
    })
    _start(f"url_search:{new_run_id}",
           UrlSearchThread(new_run_id, url, max_posts, max_comments).run)
    return {"run_id": new_run_id, "status": "running", "retried_from": run_id}


@router.post("/jobs/{run_id}/retry")
async def retry_job(run_id: str, request: Request,
                    admin: dict = Depends(require_manager)):
    result = await _retry_job(run_id, admin)
    await a.aaudit("job.retry", "jobs", user=admin,
                   ip=request.client.host if request.client else None,
                   details={"run_id": run_id, "new_run_id": result["run_id"]})
    return result


@router.post("/jobs/{run_id}/cancel")
async def cancel_job(run_id: str, request: Request,
                     admin: dict = Depends(require_manager)):
    from app.api.routes.search import cancel_search_run
    db = await _db()
    run = await db.search_history.find_one({"run_id": run_id})
    if not run:
        raise HTTPException(status_code=404, detail="Search run not found")
    if run.get("status") != "running":
        raise HTTPException(status_code=400, detail="Run is not running")
    await cancel_search_run(run_id)
    await a.aaudit("job.cancel", "jobs", user=admin,
                   ip=request.client.host if request.client else None,
                   details={"run_id": run_id})
    return {"success": True, "message": "Cancellation requested"}


async def _delete_job(run_id: str) -> None:
    db = await _db()
    doc = await db.search_history.find_one({"run_id": run_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Search run not found")
    if doc.get("status") == "running":
        raise HTTPException(status_code=400, detail="Cancel the run before deleting it")
    # best-effort cascade: run â†’ pages â†’ posts â†’ comments â†’ ai_comments
    page_ids = [p["_id"] async for p in
                db.facebook_pages.find({"search_run_id": run_id}, {"_id": 1})]
    for pid in page_ids:
        post_ids = [p["_id"] async for p in
                    db.facebook_posts.find({"page_ref": str(pid)}, {"_id": 1})]
        for post_id in post_ids:
            comment_ids = [c["_id"] async for c in
                           db.facebook_comments.find({"post_ref": str(post_id)}, {"_id": 1})]
            await db.ai_comments.delete_many(
                {"comment_ref": {"$in": [str(c) for c in comment_ids]}})
            await db.facebook_comments.delete_many({"post_ref": str(post_id)})
        await db.facebook_posts.delete_many({"page_ref": str(pid)})
    await db.facebook_pages.delete_many({"search_run_id": run_id})
    await db.search_history.delete_one({"run_id": run_id})


@router.delete("/jobs/{run_id}", dependencies=[Depends(require_super)])
async def delete_job(run_id: str, request: Request):
    await _delete_job(run_id)
    await a.aaudit("job.delete", "jobs",
                   user=current_admin_for_audit(request),
                   ip=request.client.host if request.client else None,
                   details={"run_id": run_id})
    return {"success": True, "message": "Search run deleted"}


def current_admin_for_audit(request: Request) -> Optional[Dict[str, Any]]:
    """Best-effort admin claims for audit lines on dependency-gated routes."""
    try:
        from app.auth.roles import current_admin
        return current_admin(request)
    except Exception:
        return None


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# FAILED JOBS
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get("/failed-jobs", dependencies=[Depends(require_viewer)])
async def failed_jobs(offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=200)):
    db = await _db()
    query = {"status": "error"}
    offset, limit = _pagination(offset, limit)
    rows = [doc async for doc in
            db.search_history.find(query).sort("created_at", -1)
            .skip(offset).limit(limit)]
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    summary = {
        "total": await _count(db, "search_history", query),
        "today": await _count(db, "search_history",
                              _range_query("created_at", today_start, now, query)),
        "this_week": await _count(
            db, "search_history",
            _range_query("created_at", today_start - timedelta(days=6), now, query)),
        "top_error": None,
    }
    try:
        async for doc in db.search_history.aggregate([
            {"$match": query},
            {"$group": {"_id": {"$ifNull": ["$message", "unknown"]},
                        "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 1},
        ]):
            summary["top_error"] = {"message": str(doc["_id"])[:160],
                                    "count": doc.get("count", 0)}
    except Exception as e:
        logger.warning("failed-jobs top_error failed: %s", e)
    return {
        "items": [_serialize_oid(r) for r in rows],
        "total": await _count(db, "search_history", query),
        "summary": summary,
    }


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# LEADS â€” ai_comments management
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get("/leads", dependencies=[Depends(require_viewer)])
async def list_leads(
    platform: Optional[str] = Query(None),
    quality: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    only_leads: bool = Query(True),
    q: Optional[str] = Query(None),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
):
    db = await _db()
    query: Dict[str, Any] = {}
    if only_leads:
        query["is_lead"] = True
    if platform and isinstance(platform, str):
        query["platform"] = platform
    if quality and isinstance(quality, str):
        quality_map = {
            "high": ["high", "hot"],
            "hot": ["high", "hot"],
            "medium": ["medium", "warm"],
            "warm": ["medium", "warm"],
            "low": ["low", "cold"],
            "cold": ["low", "cold"],
        }
        targets = quality_map.get(str(quality).lower(), [quality])
        cond = {"$or": [{"lead_quality": {"$in": targets}}, {"priority": {"$in": targets}}]}
        if "$and" not in query:
            query["$and"] = []
        query["$and"].append(cond)
    if status and isinstance(status, str):
        query["lead_status"] = status
    if q and isinstance(q, str):
        q_cond = {"$or": [
            {"comment_text": {"$regex": re.escape(q), "$options": "i"}},
            {"commenter_name": {"$regex": re.escape(q), "$options": "i"}},
            {"phone": {"$regex": re.escape(q)}},
            {"email": {"$regex": re.escape(q), "$options": "i"}},
            {"page_name": {"$regex": re.escape(q), "$options": "i"}},
        ]}
        if "$and" not in query:
            query["$and"] = []
        query["$and"].append(q_cond)
    query.update(_date_filter(from_date, to_date))
    offset, limit = _pagination(offset, limit)
    rows = [doc async for doc in
            db.ai_comments.find(query).sort("lead_score", -1)
            .skip(offset).limit(limit)]
    summary = {"total": await _count(db, "ai_comments", {"is_lead": True}),
               "with_contact": 0, "by_platform": {}, "this_week": 0,
               "avg_score": None}
    try:
        summary["with_contact"] = await _count(
            db, "ai_comments", {"is_lead": True, **_contact_query()})
        now = datetime.now(timezone.utc)
        week_start = now - timedelta(days=7)
        summary["this_week"] = await _count(
            db, "ai_comments",
            _range_query("analyzed_at", week_start, now, {"is_lead": True}))
        async for doc in db.ai_comments.aggregate([
            {"$match": {"is_lead": True}},
            {"$group": {"_id": "$platform", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
        ]):
            summary["by_platform"][doc["_id"] or "other"] = doc.get("count", 0)
        async for doc in db.ai_comments.aggregate([
            {"$match": {"is_lead": True}},
            {"$group": {"_id": None, "avg": {"$avg": "$lead_score"}}},
        ]):
            avg = doc.get("avg")
            summary["avg_score"] = round(avg, 1) if avg is not None else None
    except Exception as e:
        logger.warning("leads summary failed: %s", e)
    return {
        "items": [_serialize_oid(r) for r in rows],
        "total": await _count(db, "ai_comments", query),
        "offset": offset, "limit": limit,
        "summary": summary,
    }


@router.get("/leads/{lead_id}", dependencies=[Depends(require_viewer)])
async def get_lead(lead_id: str):
    db = await _db()
    try:
        oid = ObjectId(lead_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid lead id")
    lead = await db.ai_comments.find_one({"_id": oid})
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return {"lead": _serialize_oid(lead)}


@router.patch("/leads/{lead_id}", dependencies=[Depends(require_manager)])
async def update_lead(lead_id: str, body: Dict[str, Any]):
    db = await _db()
    lead = await db.ai_comments.find_one({"_id": ObjectId(lead_id)})
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    status = body.get("status")
    if status is not None and status not in _lead_statuses():
        raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
    update: Dict[str, Any] = {"updated_at": utcnow()}
    if status is not None:
        update["lead_status"] = status
    for key in ("notes",):
        if key in body:
            update[key] = str(body[key])[:500]
    await db.ai_comments.update_one({"_id": lead["_id"]}, {"$set": update})
    await a.aaudit("lead.update", "leads",
                   details={"lead_id": lead_id, "status": status})
    return {"success": True, "lead": _serialize_oid({**lead, **update})}


@router.post("/leads/bulk", dependencies=[Depends(require_manager)])
async def bulk_leads(body: Dict[str, Any]):
    """Bulk action over a list of lead ids: {action, ids, value}."""
    db = await _db()
    ids = body.get("ids") or []
    if not ids:
        raise HTTPException(status_code=400, detail="No leads selected")
    oids = []
    for raw in ids:
        try:
            oids.append(ObjectId(raw))
        except Exception:
            raise HTTPException(status_code=400, detail=f"Invalid lead id: {raw}")
    action = body.get("action")
    query = {"_id": {"$in": oids}}
    if action == "set_status":
        status = body.get("value")
        if status not in _lead_statuses():
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
        result = await db.ai_comments.update_many(
            query, {"$set": {"lead_status": status, "updated_at": utcnow()}})
        count = result.modified_count
    elif action == "delete":
        result = await db.ai_comments.delete_many(query)
        count = result.deleted_count
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")
    await a.aaudit(f"lead.bulk.{action}", "leads",
                   details={"count": len(ids), "value": body.get("value")})
    return {"success": True, "affected": count}


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# ANALYTICS
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get("/analytics", dependencies=[Depends(require_viewer)])
async def analytics(
    days: Optional[int] = Query(None, ge=1, le=365),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
):
    db = await _db()
    if from_date or to_date:
        start = _iso(from_date) if from_date else None
        end = _iso(to_date) if to_date else None
        if end is not None:
            end = end.replace(hour=23, minute=59, second=59, microsecond=999999)
        since = start or datetime.now(timezone.utc) - timedelta(days=30)
    else:
        since = datetime.now(timezone.utc) - timedelta(days=days or 14)
        end = None
    range_filter: Dict[str, Any] = {}
    analyzed_range: Dict[str, Any] = {}
    if since:
        range_filter["$gte"] = since
    if end:
        range_filter["$lte"] = end
    created_match = {"created_at": range_filter} if range_filter else {}
    analyzed_match = {"analyzed_at": range_filter} if range_filter else {}

    jobs_series = []
    async for doc in db.search_history.aggregate([
        {"$match": created_match},
        {"$group": {"_id": {
            "$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
            "count": {"$sum": 1},
            "failed": {"$sum": {"$cond": [{"$eq": ["$status", "error"]}, 1, 0]}}}},
        {"$sort": {"_id": 1}},
    ]):
        jobs_series.append({
            "date": doc["_id"],
            "jobs": doc.get("count", 0),
            "failed": doc.get("failed", 0),
        })

    leads_series = []
    async for doc in db.ai_comments.aggregate([
        {"$match": {"is_lead": True, **analyzed_match}},
        {"$group": {"_id": {
            "$dateToString": {"format": "%Y-%m-%d", "date": "$analyzed_at"}},
            "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]):
        leads_series.append({"date": doc["_id"], "leads": doc.get("count", 0)})

    quality = {}
    async for doc in db.ai_comments.aggregate([
        {"$match": analyzed_match},
        {"$group": {"_id": "$lead_quality", "count": {"$sum": 1}}}]):
        quality[doc["_id"] or "none"] = doc["count"]

    leads_by_platform = {}
    async for doc in db.ai_comments.aggregate([
        {"$match": {"is_lead": True, **analyzed_match}},
        {"$group": {"_id": "$platform", "count": {"$sum": 1}}}]):
        leads_by_platform[doc["_id"] or "unknown"] = doc["count"]

    score_buckets = {}
    async for doc in db.ai_comments.aggregate([
        {"$match": {"is_lead": True, **analyzed_match}},
        {"$group": {"_id": "$lead_score", "count": {"$sum": 1}}}]):
        bucket = _score_bucket(doc["_id"])
        score_buckets[bucket] = score_buckets.get(bucket, 0) + doc["count"]

    top_pages = []
    async for doc in db.ai_comments.aggregate([
        {"$match": {"is_lead": True, **analyzed_match}},
        {"$group": {"_id": "$page_name", "leads": {"$sum": 1}}},
        {"$sort": {"leads": -1}},
        {"$limit": 10},
    ]):
        top_pages.append({"page": doc["_id"] or "unknown", "leads": doc["leads"]})

    statuses = {}
    async for doc in db.search_history.aggregate([
        {"$match": created_match},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}}]):
        statuses[doc["_id"] or "unknown"] = doc["count"]

    total_jobs = sum(statuses.values())
    completed_jobs = statuses.get("completed", 0)
    failed_jobs = statuses.get("error", 0)

    totals = {
        "jobs": total_jobs,
        "completed": completed_jobs,
        "failed": failed_jobs,
        "success_rate": round(completed_jobs / total_jobs * 100, 1)
        if total_jobs else None,
        "pages": await _count(db, "facebook_pages",
                              {"collected_at": range_filter} if range_filter else {}),
        "posts": await _count(db, "facebook_posts",
                              {"collected_at": range_filter} if range_filter else {}),
        "comments": await _count(db, "facebook_comments",
                                 {"created_at": range_filter} if range_filter else {}),
        "analyzed": await _count(db, "ai_comments", analyzed_match),
        "leads": await _count(db, "ai_comments",
                              {"is_lead": True, **analyzed_match}),
    }

    platform_perf = []
    for platform in s.PLATFORMS:
        plat_match = {"platform": platform, **created_match}
        runs = await _count(db, "search_history", plat_match)
        plat_completed = await _count(
            db, "search_history",
            {"platform": platform, "status": "completed", **created_match})
        plat_failed = await _count(
            db, "search_history",
            {"platform": platform, "status": "error", **created_match})
        plat_leads = await _count(
            db, "ai_comments",
            {"platform": platform, "is_lead": True, **analyzed_match})
        platform_perf.append({
            "platform": platform,
            "runs": runs,
            "completed": plat_completed,
            "failed": plat_failed,
            "success_rate": round(plat_completed / runs * 100, 1) if runs else None,
            "leads": plat_leads,
        })

    return {
        "range": {
            "from": since.isoformat() if since else None,
            "to": end.isoformat() if end else None,
            "days": (end - since).days if since and end else None,
        },
        "jobs_series": jobs_series,
        "leads_series": leads_series,
        "quality": quality,
        "score_distribution": score_buckets,
        "leads_by_platform": leads_by_platform,
        "top_pages": top_pages,
        "job_statuses": statuses,
        "totals": totals,
        "platform_perf": platform_perf,
        "lead_statuses": {st: await _count(db, "ai_comments",
                                           {"lead_status": st, **analyzed_match})
                          for st in _lead_statuses()},
    }


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# PLATFORMS
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get("/platforms", dependencies=[Depends(require_viewer)])
async def platforms():
    db = await _db()
    out = []
    for platform in s.PLATFORMS:
        stats = await _platform_stats(db, platform)
        actors = []
        if platform == "facebook":
            kinds = [("pages", "actor.facebook.pages"),
                     ("posts", "actor.facebook.posts"),
                     ("comments", "actor.facebook.comments")]
        elif platform == "linkedin":
            kinds = [("company", "actor.linkedin.company"),
                     ("posts", "actor.linkedin.posts")]
        else:
            kinds = [("main", f"actor.{platform}.main")]
        for kind, key in kinds:
            actors.append({**await _actor_info(key), "kind": kind})
        out.append({
            "platform": platform,
            "enabled": bool(await s.aget_setting(f"platform.{platform}.enabled")),
            "actors": actors,
            "stats": stats,
        })
    return {"platforms": out}


@router.post("/platforms/{platform}/toggle", dependencies=[Depends(require_manager)])
async def toggle_platform(platform: str):
    if platform not in s.PLATFORMS:
        raise HTTPException(status_code=400, detail=f"Unknown platform: {platform}")
    current = bool(await s.aget_setting(f"platform.{platform}.enabled"))
    await s.aset_setting(f"platform.{platform}.enabled", not current, by="admin")
    await a.aaudit("platform.toggle", "platforms",
                   details={"platform": platform, "enabled": not current})
    return {"success": True, "platform": platform, "enabled": not current}


@router.post("/platforms/{platform}/actor", dependencies=[Depends(require_manager)])
async def update_platform_actor(platform: str, body: Dict[str, Any]):
    if platform not in s.PLATFORMS:
        raise HTTPException(status_code=400, detail=f"Unknown platform: {platform}")
    key = s.platform_actor_key(platform, body.get("kind", "main"))
    actor_id = str(body.get("actor_id") or "").strip()
    if not actor_id:
        raise HTTPException(status_code=400, detail="actor_id is required")
    await s.aset_setting(key, actor_id, by="admin")
    await a.aaudit("platform.actor.update", "platforms",
                   details={"platform": platform, "key": key,
                            "actor_id": actor_id})
    return {"success": True, "key": key, "value": actor_id}


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# APIFY â€” token, connection test, usage
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def _apify_probe(actor_id: Optional[str] = None) -> Dict[str, Any]:
    """Real Apify API call: fetch actor metadata with the effective token."""
    token = s.get_apify_token()
    if not token:
        return {"ok": False, "error": "No Apify token configured"}
    try:
        from apify_client import ApifyClient
        client = ApifyClient(token)
        target = actor_id or "apify/facebook-pages-scraper"
        info = client.actor(target).get()
        if not info:
            return {"ok": False, "error": f"Actor {target} not found with this token"}
        return {"ok": True, "actor": target,
                "title": info.get("title") if isinstance(info, dict) else None}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


@router.get("/apify", dependencies=[Depends(require_viewer)])
async def apify_status():
    hint = s.get_apify_token_hint()
    return {
        "token_configured": bool(hint),
        "token_hint": hint,
        "env_token_configured": bool(settings.apify_api_token),
        "last_test_ok": await s.aget_setting("apify.last_test_ok"),
        "last_test_at": await s.aget_setting("apify.last_test_at"),
    }


@router.post("/apify/test", dependencies=[Depends(require_manager)])
async def apify_test():
    result = await _apify_probe()
    await s.aset_setting("apify.last_test_ok", result["ok"], by="admin")
    await s.aset_setting("apify.last_test_at", time.time(), by="admin")
    await a.aaudit("apify.test", "apify", success=result["ok"],
                   details={"ok": result["ok"]})
    return result


@router.post("/apify/token", dependencies=[Depends(require_manager)])
async def set_apify_token(body: Dict[str, Any]):
    token = str(body.get("token") or "").strip()
    if len(token) < 10:
        raise HTTPException(status_code=400, detail="Token looks too short")
    await s.aset_setting("apify.token", token, by="admin")
    result = await _apify_probe()
    await s.aset_setting("apify.last_test_ok", result["ok"], by="admin")
    await s.aset_setting("apify.last_test_at", time.time(), by="admin")
    await a.aaudit("apify.token.update", "apify", success=result["ok"],
                   details={"ok": result["ok"]})
    return {"success": True, "test": result}


@router.delete("/apify/token", dependencies=[Depends(require_super)])
async def clear_apify_token():
    await s.delete_setting("apify.token")
    await a.aaudit("apify.token.clear", "apify")
    return {"success": True, "message": "Token override removed (env token still applies)"}


# ── ENVIRONMENT VARIABLES ────────────────────────────────────────────────────
# Registry-backed env management: an override row wins, otherwise the real
# .env / process value applies, otherwise the documented default. Secrets are
# never returned — only masked hints. Restart-flagged vars take effect after
# the process restarts (their consumers read once at import).
#
# The whole section is additionally locked behind the guard password: every
# /api/admin/env* endpoint (except lock-status/unlock) requires a valid
# short-lived unlock cookie, on top of the normal admin session + role checks.


@router.get("/env/lock-status", dependencies=[Depends(require_viewer)])
async def env_lock_status(request: Request):
    from app.auth.service import ENV_UNLOCK_COOKIE, parse_env_unlock_value
    locked = not parse_env_unlock_value(request.cookies.get(ENV_UNLOCK_COOKIE))
    return {"locked": locked, "unlock_minutes": ev.GUARD_UNLOCK_MINUTES}


@router.post("/env/unlock", dependencies=[Depends(require_viewer)])
async def env_unlock(body: Dict[str, Any], response: Response,
                     admin: dict = Depends(require_viewer)):
    password = str(body.get("password") or "")
    if not ev.verify_guard_password(password):
        await a.aaudit("env.unlock.failed", "env", success=False)
        raise HTTPException(status_code=401, detail="Wrong password")
    from app.auth.service import set_env_unlock_cookie
    set_env_unlock_cookie(response)
    await a.aaudit("env.unlock", "env")
    return {"success": True, "unlock_minutes": ev.GUARD_UNLOCK_MINUTES}


@router.delete("/env/unlock", dependencies=[Depends(require_viewer)])
async def env_lock(response: Response):
    from app.auth.service import clear_env_unlock_cookie
    clear_env_unlock_cookie(response)
    await a.aaudit("env.lock", "env")
    return {"success": True, "message": "Environment panel locked"}


@router.get("/env", dependencies=[Depends(require_viewer),
                                  Depends(require_env_unlocked)])
async def env_list():
    return {"vars": ev.all_envvar_info()}


@router.put("/env/{name}", dependencies=[Depends(require_manager),
                                         Depends(require_env_unlocked)])
async def env_set(name: str, body: Dict[str, Any],
                  admin: dict = Depends(require_viewer)):
    if not ev.known_name(name):
        raise HTTPException(status_code=404, detail=f"Unknown env var: {name}")
    # Secrets are editable like everything else here: the guard password on
    # the whole section is the protection, so no extra role gate.
    value = body.get("value")
    if value is None or value == "":
        raise HTTPException(status_code=400, detail="value is required")
    ok = await ev.aset_envvar_override(name, value, by="admin")
    if not ok:
        raise HTTPException(status_code=503, detail="Database unavailable")
    await a.aaudit("env.set", "env", details={"name": name})
    return {"success": True, "entry": ev.envvar_info(name)}


@router.delete("/env/{name}", dependencies=[Depends(require_manager),
                                            Depends(require_env_unlocked)])
async def env_delete(name: str, admin: dict = Depends(require_viewer)):
    if not ev.known_name(name):
        raise HTTPException(status_code=404, detail=f"Unknown env var: {name}")
    if not ev.envvar_info(name)["overridden"]:
        return {"success": True, "message": "No override to remove"}
    ok = ev.delete_envvar_override(name)
    if not ok:
        raise HTTPException(status_code=503, detail="Database unavailable")
    await a.aaudit("env.delete", "env", details={"name": name})
    return {"success": True, "entry": ev.envvar_info(name)}


@router.post("/env/password", dependencies=[Depends(require_manager),
                                            Depends(require_env_unlocked)])
async def env_change_password(body: Dict[str, Any]):
    """Change the recovery admin password: hash it server-side, store as an
    override of ADMIN_PASSWORD_HASH. Takes effect on the next login."""
    new_password = str(body.get("new_password") or "")
    if len(new_password) < 8:
        raise HTTPException(status_code=400,
                            detail="Password must be at least 8 characters")
    import hashlib
    hashed = hashlib.sha256(new_password.encode("utf-8")).hexdigest()
    ok = await ev.aset_envvar_override("ADMIN_PASSWORD_HASH", hashed, by="admin")
    if not ok:
        raise HTTPException(status_code=503, detail="Database unavailable")
    await a.aaudit("env.password.change", "env",
                   details={"admin_password_hash": "••••"})
    return {"success": True, "masked": ev.envvar_info("ADMIN_PASSWORD_HASH")["masked"]}


@router.get("/usage", dependencies=[Depends(require_viewer)])
async def usage(days: int = Query(30, ge=1, le=365)):
    """Apify usage from REAL run metadata stored on search runs."""
    db = await _db()
    since = datetime.now(timezone.utc) - timedelta(days=days)
    per_actor = {}
    total_cost = 0.0
    runs_with_usage = 0
    cursor = db.search_history.find({
        "created_at": {"$gte": since},
        "scrape_info": {"$exists": True},
    })
    async for run in cursor:
        info = run.get("scrape_info") or {}
        actor = info.get("actorId") or "unknown"
        usage_usd = info.get("usageUsd")
        entry = per_actor.setdefault(actor, {"runs": 0, "cost": 0.0})
        entry["runs"] += 1
        if usage_usd is not None:
            try:
                cost = float(usage_usd)
                entry["cost"] += cost
                total_cost += cost
                runs_with_usage += 1
            except (TypeError, ValueError):
                pass
    actors = []
    for actor, entry in sorted(per_actor.items(),
                               key=lambda kv: kv[1]["cost"], reverse=True):
        actors.append({"actor": actor, **entry})
    return {
        "days": days,
        "actors": actors,
        "total_cost": round(total_cost, 4),
        "runs_with_usage": runs_with_usage,
        "note": ("Cost figures come from Apify run metadata (usageUsd). "
                 "Runs without usage data are excluded from cost totals."),
    }


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# ACTORS â€” list + test
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get("/actors", dependencies=[Depends(require_viewer)])
async def actors_list():
    keys = sorted(k for k in s.SETTING_DEFAULTS if k.startswith("actor."))
    items = []
    for key in keys:
        info = await _actor_info(key)
        platform = key.split(".")[1]
        items.append({
            "key": key, "platform": platform, "value": info["value"],
            "default": info["default"], "overridden": info["overridden"],
            "updated_at": info["updated_at"],
        })
    return {"actors": items}


@router.post("/actors/test", dependencies=[Depends(require_manager)])
async def actors_test(body: Dict[str, Any]):
    key = body.get("key")
    if key not in s.SETTING_DEFAULTS or not key.startswith("actor."):
        raise HTTPException(status_code=400, detail="Unknown actor key")
    actor_id = await s.aget_setting(key)
    result = await _apify_probe(str(actor_id))
    await a.aaudit("actor.test", "actors", success=result["ok"],
                   details={"key": key, "actor": actor_id})
    return {"key": key, "actor_id": actor_id, **result}


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# LIMITS + COST PROTECTION
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

_LIMITS_KEYS = [k for k in s.SETTING_DEFAULTS if k.startswith(("limits.", "cost."))]


@router.get("/limits", dependencies=[Depends(require_viewer)])
async def get_limits():
    db = await _db()
    usage = {
        "pages": await _count(db, "facebook_pages"),
        "posts": await _count(db, "facebook_posts"),
        "comments": await _count(db, "facebook_comments"),
        "leads": await _count(db, "ai_comments", {"is_lead": True}),
    }
    return {"settings": {k: await s.aget_setting(k) for k in _LIMITS_KEYS},
            "usage": usage}


@router.put("/limits", dependencies=[Depends(require_manager)])
async def put_limits(body: Dict[str, Any]):
    changed = []
    for key, value in body.items():
        if key not in _LIMITS_KEYS:
            continue
        await s.aset_setting(key, value, by="admin")
        changed.append(key)
    if not changed:
        raise HTTPException(status_code=400, detail="No valid limit keys")
    await a.aaudit("limits.update", "limits", details={"keys": changed})
    return {"success": True, "changed": changed}


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# AI / GEMINI
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

_AI_KEYS = [k for k in s.SETTING_DEFAULTS if k.startswith("ai.")]


@router.get("/ai", dependencies=[Depends(require_viewer)])
async def get_ai():
    db = await _db()
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    counts = {"analyzed": await _count(db, "ai_comments"),
              "this_month": await _count(
                  db, "ai_comments",
                  _range_query("analyzed_at", month_start, now)),
              "leads": await _count(db, "ai_comments", {"is_lead": True})}
    return {"settings": {k: await s.aget_setting(k) for k in _AI_KEYS},
            "gemini_key_configured": bool(ev.get_envvar_str("GEMINI_API_KEY", settings.gemini_api_key)),
            "counts": counts}


@router.put("/ai", dependencies=[Depends(require_manager)])
async def put_ai(body: Dict[str, Any]):
    changed = []
    for key, value in body.items():
        if key not in _AI_KEYS:
            continue
        await s.aset_setting(key, value, by="admin")
        changed.append(key)
    if not changed:
        raise HTTPException(status_code=400, detail="No valid ai keys")
    await a.aaudit("ai.update", "ai", details={"keys": changed})
    return {"success": True, "changed": changed}


@router.post("/ai/test", dependencies=[Depends(require_manager)])
async def ai_test(body: Dict[str, Any]):
    """Run the real analysis pipeline on a sample comment."""
    text = str(body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    from app.pipeline.comment_ai import analyze_comment_ai, comment_lead_score, \
        extract_display_signals, signal_lead_score
    analysis = analyze_comment_ai(text, body.get("author") or "Test User")
    return {
        "analyzed_by": analysis.get("analyzed_by"),
        "is_useful": analysis.get("is_useful"),
        "reason": analysis.get("reason"),
        "lead_quality": analysis.get("lead_quality"),
        "priority": analysis.get("priority"),
        "contact": analysis.get("contact"),
        "buyer": analysis.get("buyer"),
        "lead_score": comment_lead_score(analysis),
        "signal_score": signal_lead_score(analysis, text),
        "signals": extract_display_signals(text, analysis),
        "sample_text": text,
    }


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# LEAD SCORING
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

_SCORING_KEYS = [k for k in s.SETTING_DEFAULTS if k.startswith("scoring.")]


@router.get("/scoring", dependencies=[Depends(require_viewer)])
async def get_scoring():
    return {"settings": {k: await s.aget_setting(k) for k in _SCORING_KEYS}}


@router.put("/scoring", dependencies=[Depends(require_manager)])
async def put_scoring(body: Dict[str, Any]):
    changed = []
    for key, value in body.items():
        if key not in _SCORING_KEYS:
            continue
        await s.aset_setting(key, value, by="admin")
        changed.append(key)
    if not changed:
        raise HTTPException(status_code=400, detail="No valid scoring keys")
    await a.aaudit("scoring.update", "scoring", details={"keys": changed})
    return {"success": True, "changed": changed}


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# COMMENT INTELLIGENCE
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

_CI_KEYS = [k for k in s.SETTING_DEFAULTS if k.startswith("ci.")]


@router.get("/comment-intelligence", dependencies=[Depends(require_viewer)])
async def get_ci():
    return {"settings": {k: await s.aget_setting(k) for k in _CI_KEYS}}


@router.put("/comment-intelligence", dependencies=[Depends(require_manager)])
async def put_ci(body: Dict[str, Any]):
    changed = []
    for key, value in body.items():
        if key not in _CI_KEYS:
            continue
        await s.aset_setting(key, value, by="admin")
        changed.append(key)
    if not changed:
        raise HTTPException(status_code=400, detail="No valid ci keys")
    await a.aaudit("comment_intelligence.update", "comment_intelligence",
                   details={"keys": changed})
    return {"success": True, "changed": changed}


# ── Comment browser: real ai_comments with filters + summary ───────────────

@router.get("/comments", dependencies=[Depends(require_viewer)])
async def list_comments(
    platform: Optional[str] = Query(None),
    intent: Optional[str] = Query(None),
    quality: Optional[str] = Query(None),
    is_lead: Optional[bool] = Query(None),
    contact: bool = Query(False),
    min_confidence: Optional[float] = Query(None, ge=0, le=1),
    q: Optional[str] = Query(None),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=200),
):
    db = await _db()
    query: Dict[str, Any] = {}
    if platform and isinstance(platform, str):
        query["platform"] = platform
    if intent and isinstance(intent, str):
        query["intent"] = intent
    if quality and isinstance(quality, str):
        quality_map = {
            "high": ["high", "hot"],
            "hot": ["high", "hot"],
            "medium": ["medium", "warm"],
            "warm": ["medium", "warm"],
            "low": ["low", "cold"],
            "cold": ["low", "cold"],
        }
        targets = quality_map.get(str(quality).lower(), [quality])
        cond = {"$or": [{"lead_quality": {"$in": targets}}, {"priority": {"$in": targets}}]}
        if "$and" not in query:
            query["$and"] = []
        query["$and"].append(cond)
    if is_lead is not None and isinstance(is_lead, bool):
        query["is_lead"] = is_lead
    if contact is True:
        if "$and" not in query:
            query["$and"] = []
        query["$and"].append(_contact_query())
    if min_confidence is not None and isinstance(min_confidence, (int, float)):
        query["confidence"] = {"$gte": min_confidence}
    if q and isinstance(q, str):
        q_cond = {"$or": [
            {"comment_text": {"$regex": re.escape(q), "$options": "i"}},
            {"commenter_name": {"$regex": re.escape(q), "$options": "i"}},
            {"page_name": {"$regex": re.escape(q), "$options": "i"}},
            {"phone": {"$regex": re.escape(q)}},
            {"email": {"$regex": re.escape(q), "$options": "i"}},
        ]}
        if "$and" not in query:
            query["$and"] = []
        query["$and"].append(q_cond)
    query.update(_date_filter(from_date, to_date))
    offset, limit = _pagination(offset, limit)
    rows = [doc async for doc in
            db.ai_comments.find(query).sort("lead_score", -1)
            .skip(offset).limit(limit)]
    summary = {
        "total": await _count(db, "ai_comments"),
        "analyzed": await _count(db, "ai_comments", {"analyzed_at": {"$ne": None}}),
        "leads": await _count(db, "ai_comments", {"is_lead": True}),
        "contacts": await _count(db, "ai_comments", _contact_query()),
        "high_value": await _count(db, "ai_comments",
                                   {"is_lead": True, "lead_score": {"$gte": 80}}),
        "intents": {},
        "avg_confidence": None,
        "avg_score": None,
    }
    async for doc in db.ai_comments.aggregate([
        {"$group": {"_id": "$intent", "count": {"$sum": 1}}}]):
        summary["intents"][doc["_id"] or "unknown"] = doc["count"]
    async for doc in db.ai_comments.aggregate([
        {"$group": {"_id": None, "conf": {"$avg": "$confidence"},
                    "score": {"$avg": "$lead_score"}}}]):
        summary["avg_confidence"] = round(doc.get("conf") or 0, 3)
        summary["avg_score"] = round(doc.get("score") or 0, 1)
    return {
        "items": [_serialize_oid(r) for r in rows],
        "total": await _count(db, "ai_comments", query),
        "offset": offset, "limit": limit,
        "summary": summary,
    }


# ── Pages & posts browser (admin scopes) ───────────────────────────────────

@router.get("/pages", dependencies=[Depends(require_viewer)])
async def list_admin_pages(
    platform: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    contact: bool = Query(False),
    run_id: Optional[str] = Query(None),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=200),
):
    db = await _db()
    query: Dict[str, Any] = {}
    if run_id:
        query["search_run_id"] = run_id
    if platform:
        query["platform"] = platform
    if q:
        query["$or"] = [{"page_name": {"$regex": re.escape(q), "$options": "i"}},
                        {"category": {"$regex": re.escape(q), "$options": "i"}},
                        {"city": {"$regex": re.escape(q), "$options": "i"}}]
    if contact:
        query.update(_contact_query())
    if from_date or to_date:
        query["collected_at"] = _date_filter(from_date, to_date).get("created_at", {})
    offset, limit = _pagination(offset, limit)
    rows = [doc async for doc in
            db.facebook_pages.find(query).sort([("lead_score", -1), ("followers", -1)])
            .skip(offset).limit(limit)]
    items = []
    for r in rows:
        serialized = _serialize_oid(r)
        serialized["platform"] = r.get("platform") or _page_platform(r)
        items.append(serialized)
    return {
        "items": items,
        "total": await _count(db, "facebook_pages", query),
        "offset": offset, "limit": limit,
    }


def _page_platform(page: dict) -> str:
    source = page.get("source") or ""
    if "instagram" in source.lower():
        return "instagram"
    if "linkedin" in source.lower():
        return "linkedin"
    if "youtube" in source.lower():
        return "youtube"
    return "facebook"


@router.get("/posts", dependencies=[Depends(require_viewer)])
async def list_admin_posts(
    platform: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    run_id: Optional[str] = Query(None),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=200),
):
    db = await _db()
    query: Dict[str, Any] = {}
    if run_id:
        query["$or"] = [{"collection_run_id": run_id}, {"search_run_id": run_id}]
    if platform:
        query["platform"] = platform
    if q:
        query["$or"] = [{"caption": {"$regex": re.escape(q), "$options": "i"}},
                        {"description": {"$regex": re.escape(q), "$options": "i"}},
                        {"page_name": {"$regex": re.escape(q), "$options": "i"}}]
    if from_date or to_date:
        query["published_date"] = _date_filter(from_date, to_date).get("created_at", {})
    offset, limit = _pagination(offset, limit)
    rows = [doc async for doc in
            db.facebook_posts.find(query).sort("published_date", -1)
            .skip(offset).limit(limit)]
    items = []
    for r in rows:
        serialized = _serialize_oid(r)
        if serialized.get("total_comment_count") is None:
            serialized["total_comment_count"] = r.get("comments_count")
        serialized["platform"] = r.get("platform") or _page_platform(r)
        items.append(serialized)
    return {
        "items": items,
        "total": await _count(db, "facebook_posts", query),
        "offset": offset, "limit": limit,
    }


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# FEATURES
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

_FEATURES_KEYS = [k for k in s.SETTING_DEFAULTS
                  if k.startswith(("features.", "platform."))]


@router.get("/features", dependencies=[Depends(require_viewer)])
async def get_features():
    return {"settings": {k: await s.aget_setting(k) for k in _FEATURES_KEYS}}


@router.put("/features", dependencies=[Depends(require_manager)])
async def put_features(body: Dict[str, Any]):
    changed = []
    for key, value in body.items():
        if key not in _FEATURES_KEYS:
            continue
        await s.aset_setting(key, value, by="admin")
        changed.append(key)
    if not changed:
        raise HTTPException(status_code=400, detail="No valid feature keys")
    await a.aaudit("features.update", "features", details={"keys": changed})
    return {"success": True, "changed": changed}


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# MAINTENANCE
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get("/maintenance", dependencies=[Depends(require_viewer)])
async def get_maintenance():
    return {
        "enabled": await s.aget_setting("maintenance.enabled"),
        "message": await s.aget_setting("maintenance.message"),
    }


@router.post("/maintenance", dependencies=[Depends(require_manager)])
async def set_maintenance(body: Dict[str, Any]):
    enabled = bool(body.get("enabled"))
    await s.aset_setting("maintenance.enabled", enabled, by="admin")
    if body.get("message"):
        await s.aset_setting("maintenance.message", str(body["message"]), by="admin")
    await a.aaudit("maintenance.set", "maintenance",
                   details={"enabled": enabled})
    return {"success": True, "enabled": enabled,
            "message": await s.aget_setting("maintenance.message")}


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# DATABASE
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get("/database", dependencies=[Depends(require_viewer)])
async def database():
    db = await _db()
    db_stats = {}
    try:
        stats = await db.command("dbStats")
        db_stats = {k: stats.get(k) for k in
                    ("db", "collections", "objects", "dataSize", "storageSize",
                     "indexes", "indexSize", "avgObjSize")}
    except Exception as e:
        db_stats = {"error": str(e)[:200]}
    collections = []
    for name in ("search_history", "facebook_pages", "facebook_posts",
                 "facebook_comments", "ai_comments", "system_settings",
                 "admin_users", "audit_logs"):
        coll = db[name]
        count = 0
        size = None
        try:
            count = await coll.count_documents({})
        except Exception:
            pass
        try:
            stats = await db.command("collStats", name)
            size = stats.get("size")
        except Exception:
            pass
        indexes = []
        try:
            async for idx in coll.list_indexes():
                indexes.append({
                    "name": idx.get("name"),
                    "unique": bool(idx.get("unique")),
                    "keys": list((idx.get("key") or {}).keys()),
                })
        except Exception:
            pass
        collections.append({"name": name, "documents": count,
                            "size": size, "indexes": indexes})
    return {"db_stats": db_stats, "collections": collections}


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# LOGS
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _log_file_path() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))), "logs", "app.log")


def _tail_log(lines: int = 200, level: Optional[str] = None,
              q: Optional[str] = None) -> List[str]:
    path = _log_file_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            all_lines = fh.readlines()
    except Exception as e:
        return [f"[error] Cannot read log: {e}"]
    selected = all_lines[-max(lines, 1):]
    if level:
        level_upper = level.upper()
        selected = [ln for ln in selected
                    if f" {level_upper} " in ln or ln.startswith(level_upper)]
    if q:
        needle = q.lower()
        selected = [ln for ln in selected if needle in ln.lower()]
    return selected


@router.get("/logs", dependencies=[Depends(require_viewer)])
async def logs(
    lines: int = Query(200, ge=10, le=5000),
    level: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
):
    selected = _tail_log(lines, level, q)
    total = len(selected)
    page = selected[offset:offset + 300]
    return {"lines": page, "total": total, "offset": offset, "level": level, "q": q}


@router.get("/logs/download", dependencies=[Depends(require_manager)])
async def logs_download():
    path = _log_file_path()
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Log file not found")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Cannot read log: {e}")
    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="leadai.log"'})


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# EXPORTS (admin scope)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get("/export/{scope}.csv", dependencies=[Depends(require_manager)])
async def admin_export(
    scope: str,
    request: Request,
    platform: Optional[str] = Query(None),
    only_leads: bool = Query(True),
    q: Optional[str] = Query(None),
    from_date: Optional[str] = Query(None),
    to_date: Optional[str] = Query(None),
):
    from app.api.routes.search import (_csv_response, PAGES_CSV, POSTS_CSV,
                                       COMMENTS_CSV, _resolve_platform,
                                       _split_date_time)
    db = await _db()
    stamp = datetime.now().strftime("%Y%m%d")
    exported = 0
    if scope == "pages":
        query: Dict[str, Any] = {}
        if platform:
            query["platform"] = platform
        rows = [doc async for doc in
                db.facebook_pages.find(query).sort("followers", -1)]
        exported = len(rows)
        for row in rows:
            row["platform"] = _resolve_platform(row)
        response = _csv_response(rows, PAGES_CSV, f"admin_pages_{stamp}.csv")
    elif scope == "posts":
        query = {"platform": platform} if platform else {}
        rows = [doc async for doc in
                db.facebook_posts.find(query).sort("published_date", -1)]
        exported = len(rows)
        for row in rows:
            if row.get("total_comment_count") is None:
                row["total_comment_count"] = row.get("comments_count")
            row["platform"] = _resolve_platform(row)
        response = _csv_response(rows, POSTS_CSV, f"admin_posts_{stamp}.csv")
    elif scope == "leads":
        query: Dict[str, Any] = {"is_lead": only_leads}
        if platform:
            query["platform"] = platform
        if q:
            query["$or"] = [
                {"comment_text": {"$regex": re.escape(q), "$options": "i"}},
                {"commenter_name": {"$regex": re.escape(q), "$options": "i"}},
                {"phone": {"$regex": re.escape(q)}},
                {"email": {"$regex": re.escape(q), "$options": "i"}},
            ]
        query.update(_date_filter(from_date, to_date))
        rows = [doc async for doc in
                db.ai_comments.find(query).sort("lead_score", -1)]
        exported = len(rows)
        out = []
        for r in rows:
            date_part, time_part = _split_date_time(r.get("analyzed_at"))
            out.append({**r, "date": date_part, "time": time_part})
        response = _csv_response(out, COMMENTS_CSV, f"admin_leads_{stamp}.csv")
    elif scope == "jobs":
        query = {"platform": platform} if platform else {}
        if q:
            query["$or"] = [{"query": {"$regex": re.escape(q), "$options": "i"}},
                            {"run_id": {"$regex": re.escape(q), "$options": "i"}}]
        query.update(_date_filter(from_date, to_date))
        rows = [doc async for doc in
                db.search_history.find(query).sort("created_at", -1)]
        exported = len(rows)
        columns = ["run_id", "query", "platform", "status", "phase", "message",
                   "pages_found", "pages_stored", "created_at", "completed_at"]
        response = _csv_response(
            rows, columns, f"admin_jobs_{stamp}.csv")
    else:
        raise HTTPException(status_code=400, detail=f"Unknown scope: {scope}")
    await a.aaudit("export.csv", "exports",
                   user=current_admin_for_audit(request),
                   ip=request.client.host if request.client else None,
                   details={"scope": scope, "format": "csv", "rows": exported,
                            "platform": platform or None})
    return response


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# AUDIT LOGS
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get("/audit-logs", dependencies=[Depends(require_viewer)])
async def audit_logs(
    category: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    db = await _db()
    query: Dict[str, Any] = {}
    if category:
        query["category"] = category
    if q:
        query["$or"] = [
            {"action": {"$regex": re.escape(q), "$options": "i"}},
            {"user": {"$regex": re.escape(q), "$options": "i"}},
        ]
    offset, limit = _pagination(offset, limit)
    rows = [doc async for doc in
            db[a.COLLECTION].find(query).sort("at", -1)
            .skip(offset).limit(limit)]
    categories = [c["_id"] async for c in
                  db[a.COLLECTION].aggregate([
                      {"$group": {"_id": "$category"}}])]
    return {
        "items": [_serialize_oid(r) for r in rows],
        "total": await _count(db, a.COLLECTION, query),
        "categories": categories,
    }


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# USERS (super admin only)
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get("/users", dependencies=[Depends(require_super)])
async def list_users():
    db = await _db()
    rows = []
    async for doc in db["admin_users"].find(
            {}, {"password_hash": 0}).sort("email", 1):
        rows.append(_serialize_oid(doc))
    env_admin = {
        "email": ev.get_envvar_str("ADMIN_EMAIL", settings.admin_email),
        "name": "Admin",
        "role": "super_admin",
        "env_account": True,
        "enabled": True,
    }
    env_email = env_admin["email"]
    if not any(r["email"] == env_email for r in rows):
        rows.insert(0, env_admin)
    else:
        for r in rows:
            if r["email"] == env_email:
                r["env_account"] = True
    return {"users": rows}


@router.post("/users", dependencies=[Depends(require_super)])
async def create_user(body: Dict[str, Any]):
    import hashlib
    db = await _db()
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    role = body.get("role") or "viewer"
    if not re.match(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(status_code=400, detail="Invalid email")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if role not in ("viewer", "manager", "super_admin"):
        raise HTTPException(status_code=400, detail=f"Invalid role: {role}")
    existing = await db["admin_users"].find_one({"email": email})
    if existing:
        raise HTTPException(status_code=400, detail="User already exists")
    doc = {
        "email": email,
        "name": str(body.get("name") or email.split("@")[0]),
        "role": role,
        "enabled": bool(body.get("enabled", True)),
        "password_hash": hashlib.sha256(password.encode()).hexdigest(),
        "created_at": utcnow(),
        "last_login": None,
    }
    await db["admin_users"].insert_one(doc)
    await a.aaudit("user.create", "users",
                   details={"email": email, "role": role})
    return {"success": True, "user": _serialize_oid(doc)}


@router.patch("/users/{user_id}", dependencies=[Depends(require_super)])
async def update_user(user_id: str, body: Dict[str, Any]):
    import hashlib
    db = await _db()
    try:
        oid = ObjectId(user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user id")
    user = await db["admin_users"].find_one({"_id": oid})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    update: Dict[str, Any] = {}
    if "name" in body:
        update["name"] = str(body["name"]).strip()[:80]
    if "role" in body:
        role = body["role"]
        if role not in ("viewer", "manager", "super_admin"):
            raise HTTPException(status_code=400, detail=f"Invalid role: {role}")
        update["role"] = role
    if "enabled" in body:
        update["enabled"] = bool(body["enabled"])
    if body.get("password"):
        if len(str(body["password"])) < 8:
            raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
        update["password_hash"] = hashlib.sha256(
            str(body["password"]).encode()).hexdigest()
    if update:
        await db["admin_users"].update_one({"_id": oid},
                                           {"$set": {**update, "updated_at": utcnow()}})
    await a.aaudit("user.update", "users", details={"email": user["email"]})
    return {"success": True, "user": _serialize_oid({**user, **update})}


@router.delete("/users/{user_id}", dependencies=[Depends(require_super)])
async def delete_user(user_id: str):
    db = await _db()
    try:
        oid = ObjectId(user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid user id")
    user = await db["admin_users"].find_one({"_id": oid})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user["email"] == ev.get_envvar_str("ADMIN_EMAIL", settings.admin_email):
        raise HTTPException(status_code=400, detail="The environment admin account cannot be deleted")
    await db["admin_users"].delete_one({"_id": oid})
    await a.aaudit("user.delete", "users", details={"email": user["email"]})
    return {"success": True, "message": "User deleted"}


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# SECURITY
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

_SECURITY_KEYS = [k for k in s.SETTING_DEFAULTS if k.startswith("security.")]


@router.get("/security")
async def security(admin: dict = Depends(require_viewer)):
    return {
        "settings": {k: await s.aget_setting(k) for k in _SECURITY_KEYS},
        "me": admin,
        "session_timeout_hours_env": ev.get_envvar_int(
            "SESSION_TTL_DAYS", settings.session_ttl_days) * 24,
        "login_protection_active": _login_protection_active(),
    }


@router.put("/security")
async def put_security(body: Dict[str, Any],
                       admin: dict = Depends(require_super)):
    changed = []
    for key, value in body.items():
        if key not in _SECURITY_KEYS:
            continue
        if key in ("security.session_epoch",):
            continue
        await s.aset_setting(key, value, by="admin")
        changed.append(key)
    if not changed:
        raise HTTPException(status_code=400, detail="No valid security keys")
    await a.aaudit("security.update", "security", user=admin,
                   details={"keys": changed})
    return {"success": True, "changed": changed}


@router.post("/security/revoke-sessions")
async def revoke_sessions(admin: dict = Depends(require_super)):
    """Bump the session epoch: every existing cookie becomes invalid."""
    epoch = (await s.aget_setting("security.session_epoch") or 0) + 1
    await s.aset_setting("security.session_epoch", epoch, by="admin")
    await a.aaudit("security.revoke_sessions", "security", user=admin,
                   details={"epoch": epoch})
    return {"success": True, "message": "All sessions revoked (except yours after re-login)"}


@router.post("/security/change-password")
async def change_password(body: Dict[str, Any],
                          admin: dict = Depends(require_super)):
    """Change the signed-in account's password. For the env admin this
    creates a managed override record (the .env password stays valid as a
    recovery fallback)."""
    import hashlib
    password = str(body.get("password") or "")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    db = await _db()
    email = admin["email"]
    record = await db["admin_users"].find_one({"email": email})
    doc = {
        "email": email,
        "name": record.get("name") if record else admin["name"],
        "role": record.get("role") if record else "super_admin",
        "enabled": True,
        "password_hash": hashlib.sha256(password.encode()).hexdigest(),
        "updated_at": utcnow(),
    }
    if record:
        await db["admin_users"].update_one(
            {"_id": record["_id"]},
            {"$set": {"password_hash": doc["password_hash"],
                      "updated_at": utcnow()}})
    else:
        doc["created_at"] = utcnow()
        await db["admin_users"].insert_one(doc)
    await a.aaudit("security.change_password", "security", user=admin,
                   details={"email": email})
    return {"success": True,
            "message": "Password updated. Sign in again with the new password."}


def _login_protection_active() -> bool:
    try:
        return bool(s.get_setting("security.login_protection"))
    except Exception:
        return True


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# HEALTH
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@router.get("/health", dependencies=[Depends(require_viewer)])
async def health_check():
    db = get_async_db()
    checks = {}
    # MongoDB ping (with latency)
    mongo_latency_ms = None
    try:
        if db is not None:
            started = time.perf_counter()
            await db.command("ping")
            mongo_latency_ms = round((time.perf_counter() - started) * 1000, 1)
            checks["mongo"] = {"ok": True, "latency_ms": mongo_latency_ms}
        else:
            checks["mongo"] = {"ok": False, "error": "No database client"}
    except Exception as e:
        checks["mongo"] = {"ok": False, "error": str(e)[:200]}
    # Apify
    token = s.get_apify_token()
    checks["apify"] = {
        "ok": bool(token),
        "token_hint": s.get_apify_token_hint(),
        "last_test_ok": await s.aget_setting("apify.last_test_ok"),
    }
    # Gemini
    checks["gemini"] = {"ok": bool(ev.get_envvar_str("GEMINI_API_KEY", settings.gemini_api_key))}
    # Log file
    log_path = _log_file_path()
    checks["logs"] = {
        "ok": os.path.exists(log_path),
        "path": log_path,
    }
    checks["maintenance"] = {"enabled": await s.aget_setting("maintenance.enabled")}
    all_ok = all(v.get("ok", False) for k, v in checks.items()
                 if k in ("mongo", "apify"))
    return {"overall": "ok" if all_ok else "degraded",
            "checked_at": time.time(),
            "checks": checks}
