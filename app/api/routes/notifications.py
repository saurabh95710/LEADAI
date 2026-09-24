"""In-app notifications for tenant users (User Portal and Admin Portal).

  GET  /api/notifications          own + (for org owners/admins) org notifications
  POST /api/notifications/read     mark one ({id}) or all as read
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.auth.tenant import TenantContext, get_org_context
from app.events.notifications import audience_query, list_for, mark_read

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


def _query(ctx: TenantContext):
    return audience_query(is_super_admin=False, user_id=ctx.user_id,
                          organization_id=ctx.organization_id, org_role=ctx.org_role)


class ReadBody(BaseModel):
    id: Optional[str] = None


@router.get("")
async def my_notifications(unread: bool = False, page: int = Query(1, ge=1),
                           limit: int = Query(30, ge=1, le=100),
                           ctx: TenantContext = Depends(get_org_context)):
    res = await list_for(_query(ctx), ctx.user_id, unread_only=unread, limit=limit,
                         skip=(page - 1) * limit)
    return {"success": True, **res}


@router.post("/read")
async def read_notifications(body: ReadBody, ctx: TenantContext = Depends(get_org_context)):
    n = await mark_read(_query(ctx), ctx.user_id, body.id)
    return {"success": True, "updated": n}
