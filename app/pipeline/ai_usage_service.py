"""
AI Usage & Cost Tracking Service.

Tracks token consumption, latency, and estimated costs per AI request for SaaS analytics and billing:
  - Granular logging into `ai_requests`
  - Real-time cost calculation using registered model rates
  - Aggregation metrics for the Super Admin AI Overview dashboard
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from app.db.mongo import get_sync_db
from app.pipeline.ai_models_service import get_model_by_name

logger = logging.getLogger(__name__)


def calculate_estimated_cost(model_name: str, tokens_in: int, tokens_out: int) -> float:
    """Calculates estimated cost in USD based on input and output tokens."""
    model = get_model_by_name(model_name)
    if model:
        in_rate = model.get("cost_input_per_1k", 0.0001) / 1000.0
        out_rate = model.get("cost_output_per_1k", 0.0004) / 1000.0
    else:
        in_rate = 0.0001 / 1000.0
        out_rate = 0.0004 / 1000.0
    return round((tokens_in * in_rate) + (tokens_out * out_rate), 6)


def log_ai_request(
    organization_id: Optional[str] = None,
    user_id: Optional[str] = None,
    search_id: Optional[str] = None,
    run_id: Optional[str] = None,
    provider: str = "gemini",
    model: str = "gemini-2.5-flash",
    prompt_key: Optional[str] = None,
    prompt_version: Optional[int] = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    latency_ms: float = 0.0,
    status: str = "success",
    error_type: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    """Persists an AI request log with computed token cost."""
    try:
        db = get_sync_db()
        if db is None:
            return

        total_tokens = tokens_in + tokens_out
        cost = calculate_estimated_cost(model, tokens_in, tokens_out)

        doc = {
            "organization_id": organization_id,
            "user_id": user_id,
            "search_id": search_id,
            "run_id": run_id,
            "provider": provider,
            "model": model,
            "prompt_key": prompt_key,
            "prompt_version": prompt_version,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "total_tokens": total_tokens,
            "latency_ms": round(latency_ms, 2),
            "estimated_cost": cost,
            "status": status,
            "error_type": error_type,
            "error_message": error_message,
            "created_at": datetime.now(timezone.utc),
        }
        db.ai_requests.insert_one(doc)
    except Exception as e:
        logger.warning(f"Failed to log AI request: {e}")


def _parse_time_range(time_range: str) -> datetime:
    now = datetime.now(timezone.utc)
    if time_range == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif time_range == "yesterday":
        return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    elif time_range == "7d":
        return now - timedelta(days=7)
    elif time_range == "30d":
        return now - timedelta(days=30)
    elif time_range == "90d":
        return now - timedelta(days=90)
    elif time_range == "this_month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return now - timedelta(days=30)


def get_ai_overview_metrics(time_range: str = "30d", organization_id: Optional[str] = None) -> Dict[str, Any]:
    """Computes real-time KPI metrics and usage breakdown for Super Admin AI Control Center."""
    db = get_sync_db()
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    range_start = _parse_time_range(time_range)

    empty_res = {
        "requests_today": 0,
        "requests_this_month": 0,
        "total_requests": 0,
        "successful_requests": 0,
        "failed_requests": 0,
        "fallback_requests": 0,
        "avg_latency_ms": 0.0,
        "total_tokens": 0,
        "avg_tokens": 0,
        "estimated_cost_usd": 0.0,
        "ai_leads_generated": 0,
        "ai_comments_analyzed": 0,
        "requests_over_time": [],
        "cost_over_time": [],
        "usage_by_model": [],
        "usage_by_org": [],
    }

    if db is None:
        return empty_res

    try:
        base_match = {}
        if organization_id:
            base_match["organization_id"] = organization_id

        # Requests today
        today_match = {**base_match, "created_at": {"$gte": today_start}}
        requests_today = db.ai_requests.count_documents(today_match)

        # Requests this month
        month_match = {**base_match, "created_at": {"$gte": month_start}}
        requests_this_month = db.ai_requests.count_documents(month_match)

        # Range aggregation
        range_match = {**base_match, "created_at": {"$gte": range_start}}
        pipeline = [
            {"$match": range_match},
            {
                "$group": {
                    "_id": None,
                    "total": {"$sum": 1},
                    "success": {"$sum": {"$cond": [{"$eq": ["$status", "success"]}, 1, 0]}},
                    "failure": {"$sum": {"$cond": [{"$eq": ["$status", "failure"]}, 1, 0]}},
                    "fallback": {"$sum": {"$cond": [{"$eq": ["$status", "fallback"]}, 1, 0]}},
                    "total_latency": {"$sum": "$latency_ms"},
                    "total_tokens": {"$sum": "$total_tokens"},
                    "total_cost": {"$sum": "$estimated_cost"},
                }
            }
        ]
        agg_res = list(db.ai_requests.aggregate(pipeline))
        if agg_res:
            row = agg_res[0]
            total_reqs = row.get("total", 0)
            success_reqs = row.get("success", 0)
            failure_reqs = row.get("failure", 0)
            fallback_reqs = row.get("fallback", 0)
            avg_lat = round(row.get("total_latency", 0) / max(total_reqs, 1), 2)
            tot_tokens = row.get("total_tokens", 0)
            avg_tokens = int(tot_tokens / max(total_reqs, 1))
            est_cost = round(row.get("total_cost", 0.0), 4)
        else:
            total_reqs = success_reqs = failure_reqs = fallback_reqs = avg_tokens = tot_tokens = 0
            avg_lat = est_cost = 0.0

        # AI comments analyzed and leads generated from ai_comments
        comments_match = {"analyzed_at": {"$gte": range_start}}
        if organization_id:
            comments_match["organization_id"] = organization_id
        comments_analyzed = db.ai_comments.count_documents(comments_match)
        leads_generated = db.ai_comments.count_documents({**comments_match, "is_lead": True})

        # Requests over time (grouped by day)
        trend_pipeline = [
            {"$match": range_match},
            {
                "$group": {
                    "_id": {
                        "$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}
                    },
                    "count": {"$sum": 1},
                    "success": {"$sum": {"$cond": [{"$eq": ["$status", "success"]}, 1, 0]}},
                    "cost": {"$sum": "$estimated_cost"},
                    "avg_latency": {"$avg": "$latency_ms"}
                }
            },
            {"$sort": {"_id": 1}}
        ]
        trend_res = list(db.ai_requests.aggregate(trend_pipeline))
        requests_over_time = [
            {"date": t["_id"], "requests": t["count"], "success": t["success"], "cost": round(t["cost"], 4), "latency": round(t.get("avg_latency") or 0, 1)}
            for t in trend_res
        ]

        # Usage by model
        model_pipeline = [
            {"$match": range_match},
            {
                "$group": {
                    "_id": "$model",
                    "count": {"$sum": 1},
                    "tokens": {"$sum": "$total_tokens"},
                    "cost": {"$sum": "$estimated_cost"}
                }
            },
            {"$sort": {"count": -1}}
        ]
        model_res = list(db.ai_requests.aggregate(model_pipeline))
        usage_by_model = [
            {"model": m["_id"] or "unknown", "requests": m["count"], "tokens": m["tokens"], "cost": round(m["cost"], 4)}
            for m in model_res
        ]

        # Usage by organization
        org_pipeline = [
            {"$match": range_match},
            {
                "$group": {
                    "_id": "$organization_id",
                    "count": {"$sum": 1},
                    "tokens": {"$sum": "$total_tokens"},
                    "cost": {"$sum": "$estimated_cost"}
                }
            },
            {"$sort": {"count": -1}},
            {"$limit": 10}
        ]
        org_res = list(db.ai_requests.aggregate(org_pipeline))
        usage_by_org = []
        for o in org_res:
            org_id = o["_id"]
            org_name = "Global / Default"
            if org_id:
                org_doc = db.organizations.find_one({"_id": org_id}) or db.organizations.find_one({"slug": org_id})
                if org_doc:
                    org_name = org_doc.get("name", org_id)
                else:
                    org_name = org_id
            usage_by_org.append({
                "organization_id": org_id or "global",
                "organization_name": org_name,
                "requests": o["count"],
                "tokens": o["tokens"],
                "cost": round(o["cost"], 4)
            })

        return {
            "requests_today": requests_today,
            "requests_this_month": requests_this_month,
            "total_requests": total_reqs,
            "successful_requests": success_reqs,
            "failed_requests": failure_reqs,
            "fallback_requests": fallback_reqs,
            "avg_latency_ms": avg_lat,
            "total_tokens": tot_tokens,
            "avg_tokens": avg_tokens,
            "estimated_cost_usd": est_cost,
            "ai_leads_generated": leads_generated,
            "ai_comments_analyzed": comments_analyzed,
            "requests_over_time": requests_over_time,
            "cost_over_time": [{"date": t["date"], "cost": t["cost"]} for t in requests_over_time],
            "usage_by_model": usage_by_model,
            "usage_by_org": usage_by_org,
        }
    except Exception as e:
        logger.error(f"Error computing AI overview metrics: {e}")
        return empty_res
