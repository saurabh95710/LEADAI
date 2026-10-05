"""
SaaS Customer Billing, Plans & Invoices API.

Endpoints:
  - GET  /api/billing/plans                 public plan catalog (single source: plans collection)
  - GET  /api/billing/subscription          current subscription + any pending checkout
  - GET  /api/billing/usage                 usage vs plan limits (+ tokens)
  - POST /api/billing/checkout              choose plan -> PENDING_PAYMENT + provider checkout
  - GET  /api/billing/checkout/{session}    status of a checkout (polled by the status page)
  - POST /api/billing/checkout/{session}/mock-pay   dev mock provider "payment"
  - POST /api/billing/portal                provider billing portal link
  - POST /api/billing/cancel                schedule cancellation at period end
  - POST /api/billing/reactivate            undo a scheduled cancellation
  - GET  /api/billing/invoices              invoice history
  - POST /api/billing/webhook               signature-verified provider webhook

No endpoint here can make a subscription ACTIVE: payment verification moves
it to PENDING_ADMIN_CONFIRMATION and only a Super Admin confirms.
"""
import logging
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.admin.audit import request_meta
from app.auth.permissions import ORG_BILLING_MANAGE, ORG_BILLING_VIEW
from app.auth.tenant import TenantContext, get_org_context, require_org_permission
from app.billing.entitlements import EntitlementService
from app.billing.invoices import list_organization_invoices
from app.billing.plans import get_all_plans
from app.billing.provider import (
    WebhookSignatureError,
    complete_mock_checkout,
    get_billing_provider,
    mock_payments_enabled,
    process_billing_webhook,
)
from app.billing.subscriptions import (
    customer_view,
    cancel_subscription,
    get_organization_subscription,
    reactivate_subscription,
    start_checkout,
)
from app.db.mongo import get_async_db
from app.events.email import absolute_url

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/billing", tags=["billing"])


class CheckoutRequest(BaseModel):
    plan_slug: str
    billing_cycle: str = "monthly"
    # who provides each external API: {"apify": "leadai"|"own", "gemini": "leadai"|"own"}
    api_coverage: Optional[Dict[str, str]] = None
    api_mode: Optional[str] = None          # older all-or-nothing switch ("byok" = own for both)
    coupon_code: Optional[str] = None      # partner coupon (discount + attribution)


class MockPayRequest(BaseModel):
    succeed: bool = True



async def _log_foreign_checkout_probe(db, request: Request, ctx: TenantContext, session_id: str) -> None:
    """A checkout session of another organization is a cross-tenant probe."""
    other = await db.subscriptions.find_one(
        {"checkout_session_id": session_id, "organization_id": {"$ne": ctx.tenant_id}},
        {"_id": 1, "organization_id": 1})
    if other:
        from app.auth.tenant import report_out_of_scope
        report_out_of_scope(request, ctx, "subscriptions", other)

@router.get("/plans")
async def list_public_plans():
    """Public plans (the same documents the website, billing and usage use)."""
    db = get_async_db()
    from app.billing.plans import coverage_options, plan_api_pricing
    plans = [p for p in await get_all_plans(active_only=True, db=db) if p.get("is_public", True)]
    for p in plans:  # each plan's price for every choice of who provides Apify / Gemini
        pricing = plan_api_pricing(p)
        p["allows_own_keys"] = pricing["allows_own"]
        p["api_addons"] = pricing["addons"]
        p["coverage_options"] = coverage_options(p)
    return {"success": True, "plans": plans}


@router.get("/subscription")
async def get_tenant_subscription(ctx: TenantContext = Depends(require_org_permission(ORG_BILLING_VIEW))):
    db = get_async_db()
    sub = await get_organization_subscription(ctx.tenant_id, db=db)
    return {"success": True, "subscription": sub}


@router.get("/usage")
async def get_tenant_usage(ctx: TenantContext = Depends(get_org_context)):
    """Usage vs limits — every member may see their organization's meters."""
    db = get_async_db()
    summary = await EntitlementService.get_usage_summary(ctx.tenant_id, db=db)
    from app.lifecycle.demo import demo_status
    summary["demo"] = demo_status(ctx.tenant_id)
    return {"success": True, "usage": summary}


@router.post("/checkout")
async def create_checkout(body: CheckoutRequest, request: Request,
                          ctx: TenantContext = Depends(require_org_permission(ORG_BILLING_MANAGE))):
    """Choose a plan. Creates a PENDING_PAYMENT subscription and a provider
    checkout. Nothing is activated here."""
    db = get_async_db()
    started = await start_checkout(ctx.tenant_id, body.plan_slug, body.billing_cycle,
                                   actor=ctx.audit_user(), db=db, coupon_code=body.coupon_code,
                                   api_coverage=body.api_coverage, api_mode=body.api_mode)
    sub = started["subscription"]
    provider = get_billing_provider()
    session = await provider.create_checkout_session(
        subscription=sub, plan=started["plan"], customer_email=ctx.email,
        success_url=absolute_url("/billing/status?session={CHECKOUT_SESSION_ID}"),
        cancel_url=absolute_url("/dashboard#billing"))
    return {"success": True, "checkout": {
        **session, "subscription_id": str(sub["_id"]), "status": "pending_payment",
        "plan_name": started["plan"]["name"], "plan_slug": started["plan"]["slug"],
        "billing_cycle": body.billing_cycle, "api_coverage": sub.get("api_coverage"),
        "amount": sub["amount"], "currency": sub.get("currency"),
        "coupon": customer_view(sub).get("coupon"),
        "message": "Complete the payment. Your plan activates after our team confirms it.",
    }}


@router.get("/checkout/{session_id}")
async def checkout_status(session_id: str, request: Request,
                          ctx: TenantContext = Depends(require_org_permission(ORG_BILLING_VIEW))):
    """Status for the payment status page. Read-only: it never activates."""
    db = get_async_db()
    sub = await db.subscriptions.find_one({"checkout_session_id": session_id,
                                           "organization_id": ctx.tenant_id})
    if not sub:
        await _log_foreign_checkout_probe(db, request, ctx, session_id)
        raise HTTPException(status_code=404, detail="Checkout session not found")
    payment = await db.payments.find_one({"subscription_id": str(sub["_id"])},
                                         sort=[("created_at", -1)])
    from app.billing.plans import get_plan_by_slug_or_id
    plan = await get_plan_by_slug_or_id(sub.get("plan_id"), db=db)
    return {"success": True, "checkout": {
        "session_id": session_id, "subscription": customer_view(sub),
        "status": sub["status"], "payment_status": (payment or {}).get("status"),
        "plan": {"name": (plan or {}).get("name"), "slug": sub.get("plan_id")},
        "mock": sub.get("provider") == "mock" and mock_payments_enabled(),
    }}


@router.post("/checkout/{session_id}/mock-pay")
async def mock_pay(session_id: str, body: MockPayRequest, request: Request,
                   ctx: TenantContext = Depends(require_org_permission(ORG_BILLING_MANAGE))):
    """Dev-only mock provider payment. Moves at most to PENDING_ADMIN_CONFIRMATION."""
    try:
        res = await complete_mock_checkout(session_id, ctx.tenant_id, succeed=body.succeed)
    except HTTPException as e:
        if e.status_code == 404:
            await _log_foreign_checkout_probe(get_async_db(), request, ctx, session_id)
        raise
    return {"success": True, **res}


@router.post("/portal")
async def create_portal(ctx: TenantContext = Depends(require_org_permission(ORG_BILLING_MANAGE))):
    provider = get_billing_provider()
    result = await provider.create_portal_session(organization_id=ctx.tenant_id)
    return {"success": True, "portal": result}


@router.post("/cancel")
async def cancel_tenant_subscription(request: Request,
                                     ctx: TenantContext = Depends(require_org_permission(ORG_BILLING_MANAGE))):
    """Schedule cancellation at the end of the current billing period."""
    if ctx.user_role != "owner":
        raise HTTPException(status_code=403, detail="Only the organization owner can cancel the subscription")
    db = get_async_db()
    sub = await cancel_subscription(ctx.tenant_id, at_period_end=True, db=db)
    from app.admin.audit import aaudit
    meta = request_meta(request)
    await aaudit("subscription.cancel_requested", "billing", user=ctx.audit_user(),
                 organization_id=ctx.tenant_id, resource_type="subscription",
                 resource_id=sub.get("id"), ip=meta["ip"], user_agent=meta["user_agent"])
    return {"success": True, "subscription": sub, "message": "Subscription will cancel at period end"}


@router.post("/reactivate")
async def reactivate_tenant_subscription(request: Request,
                                         ctx: TenantContext = Depends(require_org_permission(ORG_BILLING_MANAGE))):
    """Undo a scheduled cancellation (does NOT re-activate a lapsed subscription)."""
    db = get_async_db()
    sub = await reactivate_subscription(ctx.tenant_id, db=db)
    from app.admin.audit import aaudit
    meta = request_meta(request)
    await aaudit("subscription.cancel_undone", "billing", user=ctx.audit_user(),
                 organization_id=ctx.tenant_id, resource_type="subscription",
                 resource_id=sub.get("id"), ip=meta["ip"], user_agent=meta["user_agent"])
    return {"success": True, "subscription": sub, "message": "Scheduled cancellation removed"}


@router.get("/invoices")
async def get_tenant_invoices(ctx: TenantContext = Depends(require_org_permission(ORG_BILLING_VIEW))):
    db = get_async_db()
    invoices = await list_organization_invoices(ctx.tenant_id, db=db)
    return {"success": True, "invoices": invoices}


async def _own_invoice(ctx: TenantContext, number: str, request: Request):
    db = get_async_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    inv = await db.invoices.find_one({"number": number, "organization_id": str(ctx.tenant_id)})
    if not inv:  # another organization's invoice looks exactly like a missing one (and is logged)
        other = await db.invoices.find_one({"number": number}, {"_id": 1, "organization_id": 1})
        if other:
            from app.auth.tenant import report_out_of_scope
            report_out_of_scope(request, ctx, "invoices", other)
        raise HTTPException(status_code=404, detail="Invoice not found")
    from bson import ObjectId
    org = await db.organizations.find_one({"_id": ObjectId(str(ctx.tenant_id))}, {"name": 1}) \
        if ObjectId.is_valid(str(ctx.tenant_id)) else None
    return inv, org or {}


@router.get("/invoices/{number}/download")
async def download_invoice(number: str, request: Request,
                           ctx: TenantContext = Depends(require_org_permission(ORG_BILLING_VIEW))):
    """The invoice as a printable page (Print → Save as PDF)."""
    from fastapi.responses import HTMLResponse
    from app.billing.invoices import render_invoice_html
    inv, org = await _own_invoice(ctx, number, request)
    return HTMLResponse(render_invoice_html(inv, org), headers={
        "Content-Disposition": f'inline; filename="{inv["number"]}.html"', "Cache-Control": "no-store"})


@router.get("/invoices/{number}/receipt")
async def download_receipt(number: str, request: Request,
                          ctx: TenantContext = Depends(require_org_permission(ORG_BILLING_VIEW))):
    from fastapi.responses import HTMLResponse
    from app.billing.invoices import render_invoice_html
    inv, org = await _own_invoice(ctx, number, request)
    if inv.get("status") != "paid":
        raise HTTPException(status_code=409, detail="This invoice isn't paid yet, so there's no receipt")
    return HTMLResponse(render_invoice_html(inv, org, receipt=True), headers={
        "Content-Disposition": f'inline; filename="{inv["number"]}-receipt.html"', "Cache-Control": "no-store"})


@router.post("/webhook")
async def billing_webhook(request: Request):
    """Signature-verified provider webhook (public, no session)."""
    body = await request.body()
    provider = get_billing_provider()
    headers = {k.lower(): v for k, v in request.headers.items()}
    try:
        event = provider.verify_webhook(body, headers)
    except WebhookSignatureError as e:
        from app.events.security import security_event_from_request
        security_event_from_request(request, "invalid_webhook_signature", "high",
                                    details={"provider": provider.name, "reason": e.detail})
        raise
    return await process_billing_webhook(event, provider_name=provider.name)
