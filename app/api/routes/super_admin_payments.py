"""Super Admin → Payments: which provider takes payments and whether its keys work.

  GET  /api/super-admin/payments/setup   provider choice, keys configured (yes/no,
                                         test/live, 4-char key id hint), webhook
                                         URLs + events to tick in each dashboard
  POST /api/super-admin/payments/check   {provider}: ask Razorpay / Stripe whether
                                         the keys in the server environment work

The keys themselves live only in the server .env (locked env vars): nothing
here can change them and no response contains a secret. The provider is
chosen in Global Settings → Payments (setting ``billing.provider``).
"""
import time
from collections import deque
from typing import Deque, List, Literal, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Request

from app.admin.audit import aaudit, request_meta
from app.auth.tenant import TenantContext, require_platform_role
from app.billing.provider import check_provider_keys, payments_status, reset_billing_provider
from app.admin import envvars
from app.admin.settings import set_setting, clear_settings_cache

router = APIRouter(prefix="/api/super-admin/payments", tags=["super-admin-payments"])
SUPER = require_platform_role("super_admin")

# key checks call the provider's API: a few per minute is plenty
_CHECK_LIMIT, _CHECK_WINDOW_SEC = 10, 60.0
_checks: Deque[float] = deque()


class KeyCheckRequest(BaseModel):
    provider: Literal["razorpay", "stripe"]


class PaymentSettingsUpdate(BaseModel):
    razorpay_key_id: Optional[str] = None
    razorpay_key_secret: Optional[str] = None
    razorpay_webhook_secret: Optional[str] = None
    stripe_secret_key: Optional[str] = None
    stripe_publishable_key: Optional[str] = None
    stripe_webhook_secret: Optional[str] = None
    billing_provider: Optional[str] = None
    clear_secrets: List[str] = []


_PAYMENT_ENV_MAP = {
    "razorpay_key_id": "RAZORPAY_KEY_ID",
    "razorpay_key_secret": "RAZORPAY_KEY_SECRET",
    "razorpay_webhook_secret": "RAZORPAY_WEBHOOK_SECRET",
    "stripe_secret_key": "STRIPE_SECRET_KEY",
    "stripe_publishable_key": "STRIPE_PUBLISHABLE_KEY",
    "stripe_webhook_secret": "STRIPE_WEBHOOK_SECRET",
}

_PAYMENT_SECRETS = {
    "razorpay_key_secret": "RAZORPAY_KEY_SECRET",
    "razorpay_webhook_secret": "RAZORPAY_WEBHOOK_SECRET",
    "stripe_secret_key": "STRIPE_SECRET_KEY",
    "stripe_webhook_secret": "STRIPE_WEBHOOK_SECRET",
}


@router.get("/setup")
async def payments_setup(ctx: TenantContext = Depends(SUPER)):
    return {"success": True, "payments": payments_status()}


@router.put("/setup")
async def update_payments_setup(body: PaymentSettingsUpdate, request: Request,
                                ctx: TenantContext = Depends(SUPER)):
    data = body.model_dump(exclude_none=True)
    clear = data.pop("clear_secrets", []) or []
    by_user = ctx.email or "super_admin"
    changed = []

    # Update active provider setting if specified
    if "billing_provider" in data and data["billing_provider"]:
        prov = str(data["billing_provider"]).strip().lower()
        if prov in ("auto", "razorpay", "stripe"):
            set_setting("billing.provider", prov, by=by_user)
            changed.append(f"billing.provider={prov}")

    for field, env_name in _PAYMENT_ENV_MAP.items():
        if field not in data:
            continue
        val = data[field]
        if field in _PAYMENT_SECRETS and not str(val).strip():
            # Blank secret provided: keep existing
            continue
        val_str = str(val).strip() if val is not None else ""
        ok = await envvars.aset_envvar_override(env_name, val_str, by=by_user)
        if not ok:
            raise HTTPException(status_code=503, detail=f"Couldn't save {field}. Try again.")
        envvars._CACHE.pop(env_name, None)
        changed.append(field)

    for field in clear:
        if field in _PAYMENT_SECRETS:
            env_name = _PAYMENT_SECRETS[field]
            envvars.delete_envvar_override(env_name)
            envvars._CACHE.pop(env_name, None)
            changed.append(f"{field} (cleared)")

    envvars._CACHE.clear()
    clear_settings_cache()
    reset_billing_provider()

    meta = request_meta(request)
    await aaudit("payments.settings_updated", "billing", user=ctx.audit_user(),
                 ip=meta["ip"], user_agent=meta["user_agent"],
                 details={"changed": changed})

    return {"success": True, "payments": payments_status()}


@router.post("/check")
async def payments_check(body: KeyCheckRequest, request: Request,
                         ctx: TenantContext = Depends(SUPER)):
    now = time.monotonic()
    while _checks and now - _checks[0] > _CHECK_WINDOW_SEC:
        _checks.popleft()
    if len(_checks) >= _CHECK_LIMIT:
        raise HTTPException(status_code=429, detail="Too many key checks. Try again in a minute.")
    _checks.append(now)
    result = await check_provider_keys(body.provider)
    meta = request_meta(request)
    await aaudit("payments.keys_checked", "billing", user=ctx.audit_user(), success=bool(result["ok"]),
                 ip=meta["ip"], user_agent=meta["user_agent"], resource_type="payment_provider",
                 resource_id=body.provider, details={"mode": result.get("mode"),
                                                     "message": result.get("message")})
    return {"success": True, "provider": body.provider, **result}
