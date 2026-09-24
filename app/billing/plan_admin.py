"""Single write path for the plan catalog (Super Admin only).

Both the Super Admin portal (/api/super-admin/plans) and the platform
console (/api/admin/plans) call these functions, so validation, cache
invalidation and auditing are identical everywhere:

  * prices must be non-negative numbers, currency a 3-letter code;
  * ``limits`` only accepts known keys (PLAN_LIMIT_KEYS) with whole,
    non-negative numbers and is MERGED on update (keys that are not sent
    keep their value — an editor that shows a subset can't wipe the rest);
  * ``features`` is stored as a list of keys; a {key: bool} dict is accepted
    and normalized;
  * archiving is refused while live or pending subscriptions use the plan.
"""
import re
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import HTTPException

from app.billing.plans import FEATURE_LABELS, PLAN_LIMIT_KEYS, invalidate_plan_cache
from app.db.models import utcnow

_LIVE_SUB_STATUSES = ["active", "trialing", "suspended", "pending_payment",
                      "payment_received", "pending_admin_confirmation"]
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-_]{1,40}$")


def _money(value: Any, field: str) -> float:
    try:
        v = round(float(value), 2)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail=f"{field} must be a number")
    if v < 0:
        raise HTTPException(status_code=422, detail=f"{field} cannot be negative")
    return v


def normalize_limits(limits: Dict[str, Any]) -> Dict[str, int]:
    clean: Dict[str, int] = {}
    for k, v in (limits or {}).items():
        if k not in PLAN_LIMIT_KEYS:
            raise HTTPException(status_code=422, detail=f"Unknown plan limit '{k}'")
        try:
            iv = int(v)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail=f"Limit '{k}' must be a whole number")
        if iv < 0:
            raise HTTPException(status_code=422, detail=f"Limit '{k}' cannot be negative")
        clean[k] = iv
    return clean


def normalize_features(features: Any) -> List[str]:
    if isinstance(features, dict):
        keys = [k for k, on in features.items() if on]
    elif isinstance(features, list):
        keys = [str(k) for k in features]
    else:
        raise HTTPException(status_code=422, detail="features must be a list")
    unknown = [k for k in keys if k not in FEATURE_LABELS]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown features: {', '.join(unknown)}")
    return list(dict.fromkeys(keys))


def _currency(value: Any) -> str:
    cur = str(value or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", cur):
        raise HTTPException(status_code=422, detail="currency must be a 3-letter ISO code")
    return cur


def _clean(doc: Dict[str, Any]) -> Dict[str, Any]:
    out = {("id" if k == "_id" else k): (str(v) if isinstance(v, ObjectId) else v)
           for k, v in doc.items()}
    return out


async def _find(db, plan_id: str) -> Dict[str, Any]:
    plan = None
    if ObjectId.is_valid(str(plan_id)):
        plan = await db.plans.find_one({"_id": ObjectId(str(plan_id))})
    if plan is None:
        plan = await db.plans.find_one({"slug": str(plan_id).strip().lower()})
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan


async def create_plan(db, body: Dict[str, Any], *, actor: Dict[str, Any]) -> Dict[str, Any]:
    from app.admin.audit import aaudit
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="Plan name is required")
    slug = str(body.get("slug") or name).strip().lower()
    slug = re.sub(r"[\s]+", "-", slug)
    if not _SLUG.match(slug):
        raise HTTPException(status_code=422, detail="Slug may use lowercase letters, digits, - and _")
    if await db.plans.find_one({"slug": slug}):
        raise HTTPException(status_code=409, detail="A plan with this slug already exists")
    status = body.get("status", "active")
    if status not in ("active", "inactive", "archived"):
        raise HTTPException(status_code=422, detail="status must be active, inactive or archived")
    now = utcnow()
    doc = {
        "name": name, "slug": slug,
        "description": str(body.get("description") or "").strip(),
        "status": status,
        "price_monthly": _money(body.get("price_monthly", 0), "price_monthly"),
        "price_yearly": _money(body.get("price_yearly", 0), "price_yearly"),
        "currency": _currency(body.get("currency") or "USD"),
        "trial_days": max(0, int(body.get("trial_days") or 0)),
        "features": normalize_features(body.get("features") or []),
        "limits": normalize_limits(body.get("limits") or {}),
        "display_order": int(body.get("display_order") or 99),
        "is_public": bool(body.get("is_public", True)),
        "is_default": bool(body.get("is_default", False)),
        "is_trial": bool(body.get("is_trial", False)),
        "created_at": now, "updated_at": now,
    }
    doc["_id"] = (await db.plans.insert_one(doc)).inserted_id
    invalidate_plan_cache()
    await aaudit("plan.created", "plans", user=actor, resource_type="plan", resource_id=slug,
                 details={"name": name, "price_monthly": doc["price_monthly"],
                          "limits": doc["limits"], "features": doc["features"]})
    return _clean(doc)


async def update_plan(db, plan_id: str, body: Dict[str, Any], *,
                      actor: Dict[str, Any]) -> Dict[str, Any]:
    from app.admin.audit import aaudit
    plan = await _find(db, plan_id)
    updates: Dict[str, Any] = {}
    if body.get("name") is not None:
        if not str(body["name"]).strip():
            raise HTTPException(status_code=422, detail="Plan name cannot be empty")
        updates["name"] = str(body["name"]).strip()
    if body.get("description") is not None:
        updates["description"] = str(body["description"]).strip()
    for key in ("price_monthly", "price_yearly"):
        if body.get(key) is not None:
            updates[key] = _money(body[key], key)
    if body.get("currency") is not None:
        updates["currency"] = _currency(body["currency"])
    if body.get("trial_days") is not None:
        updates["trial_days"] = max(0, int(body["trial_days"]))
    if body.get("features") is not None:
        updates["features"] = normalize_features(body["features"])
    if body.get("limits") is not None:
        for k, v in normalize_limits(body["limits"]).items():
            updates[f"limits.{k}"] = v          # merge, never wipe other keys
    if body.get("status") is not None:
        if body["status"] not in ("active", "inactive", "archived"):
            raise HTTPException(status_code=422, detail="status must be active, inactive or archived")
        if body["status"] != "active":
            await _assert_unused(db, plan)
        updates["status"] = body["status"]
    for key in ("display_order",):
        if body.get(key) is not None:
            updates[key] = int(body[key])
    for key in ("is_public", "is_default", "is_trial"):
        if body.get(key) is not None:
            updates[key] = bool(body[key])
    if not updates:
        return _clean(plan)
    updates["updated_at"] = utcnow()
    await db.plans.update_one({"_id": plan["_id"]}, {"$set": updates})
    invalidate_plan_cache()
    before = {k: plan.get(k.split(".")[0]) if "." not in k else (plan.get("limits") or {}).get(k.split(".", 1)[1])
              for k in updates if k != "updated_at"}
    await aaudit("plan.updated", "plans", user=actor, resource_type="plan",
                 resource_id=plan.get("slug"),
                 details={"before": before,
                          "after": {k: v for k, v in updates.items() if k != "updated_at"}})
    return _clean(await db.plans.find_one({"_id": plan["_id"]}))


async def _assert_unused(db, plan: Dict[str, Any]) -> None:
    n = await db.subscriptions.count_documents({
        "plan_id": {"$in": [plan.get("slug"), str(plan["_id"])]},
        "status": {"$in": _LIVE_SUB_STATUSES}})
    if n:
        raise HTTPException(status_code=409,
                            detail=f"{n} live or pending subscription(s) use this plan")


async def archive_plan(db, plan_id: str, *, actor: Dict[str, Any]) -> None:
    from app.admin.audit import aaudit
    plan = await _find(db, plan_id)
    await _assert_unused(db, plan)
    await db.plans.update_one({"_id": plan["_id"]},
                              {"$set": {"status": "archived", "updated_at": utcnow()}})
    invalidate_plan_cache()
    await aaudit("plan.archived", "plans", user=actor, resource_type="plan",
                 resource_id=plan.get("slug"), details={"name": plan.get("name")})


def plan_schema() -> Dict[str, Any]:
    """What an editor needs to render every field of a plan."""
    return {"limit_keys": list(PLAN_LIMIT_KEYS), "features": FEATURE_LABELS}
