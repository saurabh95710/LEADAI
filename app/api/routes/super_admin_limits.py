"""API usage & limits monitor — Super Admin and platform staff API.

  Super Admin (/api/super-admin/api-limits — the auth gate keeps
  /api/super-admin/* for the Super Admin only):
    GET  /api/super-admin/api-limits            LeadAI's providers + every organization's
                                                own keys / tokens / plan (filter, paging) + settings
    POST /api/super-admin/api-limits/refresh    check now {scope: "platform" | "all" | <org id>}
                                                (rate-limited, audited)
    GET  /api/super-admin/api-limits/history    points for charts (?provider=&scope=&days=)

  Platform staff (/api/admin/api-limits — the staff console's role check):
    GET  /api/admin/api-limits                  same as above (viewer and up, read-only)
    GET  /api/admin/api-limits/history          (viewer and up)
    POST /api/admin/api-limits/refresh          (manager and up)

Responses never carry a key, token or provider password — only usage
numbers, levels and plain messages (see app/limits).
"""
import asyncio
from typing import Any, Dict, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.admin.audit import aaudit, request_meta
from app.auth.rate_limit import RateLimiter
from app.auth.roles import require_admin
from app.auth.tenant import TenantContext, require_platform_role

router = APIRouter(prefix="/api/super-admin/api-limits", tags=["super-admin-limits"])
staff_router = APIRouter(prefix="/api/admin/api-limits", tags=["admin-limits"])
SUPER = require_platform_role("super_admin")

LEVEL_PATTERN = "^(ok|warning|critical|exhausted|error|unknown|not_configured)$"
PROVIDER_PATTERN = "^(apify|gemini|email|sms|payments|tokens|plan)$"
SCOPE_PATTERN = "^(platform|org:[0-9a-fA-F]{24})$"

# manual "check now": a few per minute per person (each may call providers)
REFRESH_LIMIT, REFRESH_WINDOW = 5, 60
_refresh_limiter = RateLimiter("api_limits_refresh", REFRESH_LIMIT, REFRESH_WINDOW)


class RefreshBody(BaseModel):
    scope: Optional[str] = None     # "platform" (default) | "all" | an organization id


def _scope(body: Optional[RefreshBody]) -> str:
    scope = ((body.scope if body else None) or "platform").strip()
    if scope.startswith("org:"):
        scope = scope[4:]
    if scope in ("platform", "all"):
        return scope
    if not ObjectId.is_valid(scope):
        raise HTTPException(status_code=422, detail="scope must be platform, all or an organization id")
    return scope


def _rate(actor: str) -> None:
    key = (actor or "anonymous").lower()
    if not _refresh_limiter.consume(key):
        raise HTTPException(status_code=429, detail={
            "code": "RATE_LIMITED", "retry_after": _refresh_limiter.retry_after(key),
            "message": "Too many checks. Try again in a minute."})


async def _view(level: Optional[str], q: Optional[str], page: int, limit: int) -> Dict[str, Any]:
    from app.limits.views import super_admin_view
    return await asyncio.to_thread(super_admin_view, level=level, q=q, page=page, limit=limit)


async def _refresh(body: Optional[RefreshBody], request: Request, *, actor: str, user: Any) -> Dict[str, Any]:
    from app.limits.service import ChecksBusy, run_checks
    from app.limits.views import platform_statuses, run_info
    scope = _scope(body)
    _rate(actor)
    try:
        result = await asyncio.to_thread(run_checks, scope, True)
    except ChecksBusy:
        raise HTTPException(status_code=409, detail={
            "code": "CHECK_RUNNING", "message": "A full check is already running. Try again in a few minutes."})
    except ValueError:
        raise HTTPException(status_code=404, detail="Organization not found")
    meta = request_meta(request)
    await aaudit("api_limits.refreshed", "settings", user=user, ip=meta["ip"], user_agent=meta["user_agent"],
                 resource_type="api_limits", resource_id=scope,
                 details={"scope": scope, "organizations": result.get("organizations")})
    out: Dict[str, Any] = {"success": True, "scope": scope, **run_info()}
    if scope in ("platform", "all"):
        out["platform"] = await asyncio.to_thread(platform_statuses)
    if scope == "all":
        out["organizations_checked"] = result.get("organizations", 0)
    if scope not in ("platform", "all"):
        out["organization_id"] = scope
        out["statuses"] = result.get("statuses") or []
    return out


async def _history(provider: Optional[str], scope: str, days: int) -> Dict[str, Any]:
    from app.limits.store import history
    items = await asyncio.to_thread(history, provider, scope, days)
    return {"success": True, "provider": provider, "scope": scope, "days": days, "items": items}


# ── Super Admin ─────────────────────────────────────────────────────────────

@router.get("")
async def api_limits(level: Optional[str] = Query(None, pattern=LEVEL_PATTERN),
                     q: Optional[str] = Query(None, max_length=100),
                     page: int = Query(1, ge=1), limit: int = Query(25, ge=1, le=100),
                     ctx: TenantContext = Depends(SUPER)):
    """LeadAI's providers + organizations (worst level first)."""
    return await _view(level, q, page, limit)


@router.post("/refresh")
async def refresh_api_limits(request: Request, body: Optional[RefreshBody] = None,
                             ctx: TenantContext = Depends(SUPER)):
    return await _refresh(body, request, actor=ctx.email, user=ctx.audit_user())


@router.get("/history")
async def api_limits_history(provider: Optional[str] = Query(None, pattern=PROVIDER_PATTERN),
                             scope: str = Query("platform", pattern=SCOPE_PATTERN),
                             days: int = Query(7, ge=1, le=30),
                             ctx: TenantContext = Depends(SUPER)):
    return await _history(provider, scope, days)


# ── platform staff (viewer reads, manager refreshes) ────────────────────────

@staff_router.get("")
async def staff_api_limits(level: Optional[str] = Query(None, pattern=LEVEL_PATTERN),
                           q: Optional[str] = Query(None, max_length=100),
                           page: int = Query(1, ge=1), limit: int = Query(25, ge=1, le=100),
                           admin: Dict[str, Any] = Depends(require_admin("viewer"))):
    return await _view(level, q, page, limit)


@staff_router.get("/history")
async def staff_api_limits_history(provider: Optional[str] = Query(None, pattern=PROVIDER_PATTERN),
                                   scope: str = Query("platform", pattern=SCOPE_PATTERN),
                                   days: int = Query(7, ge=1, le=30),
                                   admin: Dict[str, Any] = Depends(require_admin("viewer"))):
    return await _history(provider, scope, days)


@staff_router.post("/refresh")
async def staff_refresh_api_limits(request: Request, body: Optional[RefreshBody] = None,
                                   admin: Dict[str, Any] = Depends(require_admin("manager"))):
    return await _refresh(body, request, actor=admin.get("email", ""), user=admin)
