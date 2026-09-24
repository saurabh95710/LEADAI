"""
SaaS Invoice and Payment Transaction Records.

Maintains immutable invoice logs, payment receipts, and billing CSV export.
"""
import csv
import io
import logging
import secrets
from typing import Any, Dict, List, Optional
from bson import ObjectId

from app.db.models import utcnow
from app.db.mongo import get_async_db

logger = logging.getLogger(__name__)


def _clean_doc(d: Dict[str, Any]) -> Dict[str, Any]:
    if not d:
        return {}
    out = {}
    for k, v in d.items():
        if k == "_id":
            out["id"] = str(v)
        elif isinstance(v, ObjectId):
            out[k] = str(v)
        elif isinstance(v, dict):
            out[k] = _clean_doc(v)
        elif isinstance(v, list):
            out[k] = [_clean_doc(item) if isinstance(item, dict) else (str(item) if isinstance(item, ObjectId) else item) for item in v]
        else:
            out[k] = v
    return out


async def create_invoice_record(
    organization_id: str,
    subscription_id: Optional[str],
    amount: float,
    currency: str = "USD",
    plan_name: str = "Starter Plan",
    status: str = "paid",
    db=None,
) -> Dict[str, Any]:
    """Create an immutable invoice record and corresponding payment log."""
    if db is None:
        db = get_async_db()
    if db is None:
        return {}

    now = utcnow()
    inv_number = f"INV-{now.strftime('%Y%m')}-{secrets.token_hex(3).upper()}"
    s_org_id = str(organization_id)

    inv_doc = {
        "organization_id": s_org_id,
        "subscription_id": str(subscription_id) if subscription_id else None,
        "number": inv_number,
        "status": status,
        "description": f"Subscription to {plan_name}",
        "subtotal": amount,
        "discount": 0.0,
        "tax": 0.0,
        "total": amount,
        "currency": currency,
        "invoice_date": now,
        "paid_at": now if status == "paid" else None,
        "invoice_url": f"/api/billing/invoices/{inv_number}/download",
        "receipt_url": f"/api/billing/invoices/{inv_number}/receipt",
        "created_at": now,
    }

    res = await db.invoices.insert_one(inv_doc)
    inv_doc["id"] = str(res.inserted_id)

    # Record payment transaction
    if status == "paid" and amount > 0:
        try:
            await db.payments.insert_one({
                "organization_id": s_org_id,
                "invoice_id": inv_doc["id"],
                "provider_payment_id": f"pay_{secrets.token_hex(8)}",
                "amount": amount,
                "currency": currency,
                "status": "succeeded",
                "payment_method_type": "card",
                "created_at": now,
                "updated_at": now,
            })
        except Exception as e:
            logger.warning(f"Error logging payment record: {e}")

    return _clean_doc(inv_doc)


async def list_organization_invoices(
    organization_id: str,
    limit: int = 50,
    db=None,
) -> List[Dict[str, Any]]:
    """Retrieve billing invoice history for a tenant."""
    if db is None:
        db = get_async_db()
    if db is None:
        return []

    cursor = db.invoices.find({"organization_id": str(organization_id)}).sort("invoice_date", -1).limit(limit)
    out = []
    async for row in cursor:
        out.append(_clean_doc(row))
    return out


async def get_all_invoices_admin(
    status: Optional[str] = None,
    limit: int = 100,
    db=None,
) -> List[Dict[str, Any]]:
    """Super Admin view of all invoices across all tenants."""
    if db is None:
        db = get_async_db()
    if db is None:
        return []

    query = {}
    if status:
        query["status"] = status

    cursor = db.invoices.find(query).sort("invoice_date", -1).limit(limit)
    out = []
    async for row in cursor:
        doc = _clean_doc(row)
        # Attach org name
        try:
            org = await db.organizations.find_one({"_id": ObjectId(doc["organization_id"])})
            doc["organization_name"] = org.get("name") if org else "Unknown"
        except Exception:
            doc["organization_name"] = "Unknown"
        out.append(doc)
    return out


async def export_invoices_csv(db=None) -> str:
    """Generate CSV string of all platform invoices."""
    if db is None:
        db = get_async_db()
    if db is None:
        return "Invoice Number,Organization,Amount,Currency,Status,Date\n"

    invoices = await get_all_invoices_admin(limit=1000, db=db)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Invoice Number", "Organization", "Amount", "Currency", "Status", "Date"])

    for inv in invoices:
        writer.writerow([
            inv.get("number", ""),
            inv.get("organization_name", ""),
            inv.get("total", 0.0),
            inv.get("currency", "USD"),
            inv.get("status", ""),
            inv.get("invoice_date", ""),
        ])

    return output.getvalue()
