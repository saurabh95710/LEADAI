"""
LeadAI Outbound Webhooks Service (Phase 6 Product Feature).

Delivers real-time tenant events to external endpoints with HMAC-SHA256 signature verification:
- Header: `X-LeadAI-Signature: sha256=<hex_hmac>`
- Header: `X-LeadAI-Event: <event_name>`
- Header: `X-LeadAI-Delivery: <delivery_id>`

Supported events:
- lead.created
- lead.status_changed
- lead.converted
- search.completed
"""
import hashlib
import hmac
import json
import logging
import uuid
from typing import Any

import httpx
from bson import ObjectId

from app.db.models import utcnow

logger = logging.getLogger(__name__)

COLL_OUTBOUND_WEBHOOKS = "outbound_webhooks"
COLL_DELIVERIES = "webhook_deliveries"


def create_outbound_webhook(
    db,
    *,
    organization_id: str,
    target_url: str,
    secret: str | None = None,
    events: list[str] | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    """Register an outbound webhook endpoint for an organization."""
    webhook_id = f"whk_{uuid.uuid4().hex[:12]}"
    whk_secret = secret or uuid.uuid4().hex
    now = utcnow()

    doc = {
        "_id": ObjectId(),
        "webhook_id": webhook_id,
        "organization_id": str(organization_id),
        "url": target_url,
        "secret": whk_secret,
        "events": events or ["lead.created", "lead.status_changed", "search.completed"],
        "is_active": True,
        "created_by": created_by,
        "created_at": now,
        "updated_at": now,
    }

    db[COLL_OUTBOUND_WEBHOOKS].insert_one(doc)
    doc["id"] = str(doc["_id"])
    logger.info("Registered outbound webhook %s for org %s -> %s", webhook_id, organization_id, target_url)
    return doc


def list_outbound_webhooks(db, organization_id: str) -> list[dict[str, Any]]:
    """List registered webhooks for an organization."""
    items = list(db[COLL_OUTBOUND_WEBHOOKS].find({"organization_id": str(organization_id)}).sort("created_at", -1))
    for it in items:
        it["id"] = str(it["_id"])
        # the signing secret is shown once, when the webhook is created; a
        # listing only ever carries a masked hint
        sec = it.pop("secret", "") or ""
        it["masked_secret"] = sec[:4] + "..." + sec[-4:] if len(sec) > 8 else "***"
    return items


def delete_outbound_webhook(db, webhook_id: str, organization_id: str) -> bool:
    """Delete an outbound webhook."""
    q: dict[str, Any] = {"organization_id": str(organization_id)}
    if ObjectId.is_valid(webhook_id):
        q["$or"] = [{"_id": ObjectId(webhook_id)}, {"webhook_id": webhook_id}]
    else:
        q["webhook_id"] = webhook_id

    res = db[COLL_OUTBOUND_WEBHOOKS].delete_one(q)
    return res.deleted_count > 0


def sign_payload(payload_bytes: bytes, secret: str) -> str:
    """Compute HMAC-SHA256 signature."""
    mac = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


async def dispatch_webhook_event(
    db,
    *,
    organization_id: str,
    event_name: str,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """Send an event payload to all matching registered webhooks for the tenant."""
    webhooks = list(db[COLL_OUTBOUND_WEBHOOKS].find({
        "organization_id": str(organization_id),
        "is_active": True,
        "events": event_name,
    }))

    if not webhooks:
        return []

    delivery_results = []
    now = utcnow()

    payload_data = {
        "event": event_name,
        "organization_id": organization_id,
        "timestamp": now.isoformat(),
        "data": payload,
    }
    body_bytes = json.dumps(payload_data, default=str).encode("utf-8")

    async with httpx.AsyncClient(timeout=10.0) as client:
        for whk in webhooks:
            delivery_id = f"del_{uuid.uuid4().hex[:12]}"
            sig = sign_payload(body_bytes, whk.get("secret", ""))

            headers = {
                "Content-Type": "application/json",
                "X-LeadAI-Event": event_name,
                "X-LeadAI-Delivery": delivery_id,
                "X-LeadAI-Signature": sig,
            }

            status_code = None
            success = False
            error = None

            try:
                resp = await client.post(whk["url"], content=body_bytes, headers=headers)
                status_code = resp.status_code
                success = 200 <= status_code < 300
            except Exception as e:  # noqa: BLE001
                error = str(e)
                logger.warning("Failed outbound webhook delivery %s to %s: %s", delivery_id, whk["url"], e)

            record = {
                "delivery_id": delivery_id,
                "webhook_id": whk.get("webhook_id"),
                "organization_id": str(organization_id),
                "event": event_name,
                "url": whk["url"],
                "status_code": status_code,
                "success": success,
                "error": error,
                "created_at": now,
            }
            if db is not None:
                db[COLL_DELIVERIES].insert_one(record)
            delivery_results.append(record)

    return delivery_results
