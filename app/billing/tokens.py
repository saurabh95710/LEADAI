"""Token ledger — allocated / used / remaining per organization.

Two collections:
  * ``token_balances``  one doc per organization:
        {organization_id, allocated, used, remaining, source ("demo"|"plan"),
         expires_at, notified_thresholds: [80, 100], updated_at}
  * ``token_ledger``    append-only entries:
        {organization_id, user_id, type (allocate|consume|refund|adjust|expire),
         amount, reason, reference, actor, balance_after, created_at}

Consumption is ATOMIC: a single conditional ``update_one`` requires
``remaining >= amount`` and not-expired, so concurrent requests can never
overdraw. Organizations without a balance document are not token-metered
(legacy customers keep working); every approved demo and every confirmed
paid plan gets one.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException

from app.db.models import utcnow
from app.db.mongo import get_sync_db

logger = logging.getLogger(__name__)

BALANCES = "token_balances"
LEDGER = "token_ledger"
_THRESHOLDS = (80, 100)


class TokensExhaustedException(HTTPException):
    def __init__(self, needed: int, remaining: int, expired: bool = False):
        code = "TOKENS_EXPIRED" if expired else "TOKENS_EXHAUSTED"
        msg = ("Your tokens have expired. Choose a plan to continue."
               if expired else
               f"Not enough tokens ({remaining} left, {needed} needed). Choose a plan to continue.")
        super().__init__(status_code=402, detail={
            "success": False, "code": code, "error": code, "metric": "tokens",
            "needed": needed, "remaining": remaining, "upgrade_available": True,
            "message": msg,
        })


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _ledger(db, org_id: str, etype: str, amount: int, *, user_id=None, reason="",
            reference=None, actor=None, balance_after=None) -> None:
    try:
        db[LEDGER].insert_one({
            "organization_id": org_id, "user_id": user_id, "type": etype,
            "amount": int(amount), "reason": reason, "reference": reference,
            "actor": actor, "balance_after": balance_after, "created_at": utcnow(),
        })
    except Exception as e:
        logger.warning("token ledger write failed: %s", e)


def get_balance(organization_id: str) -> Optional[Dict[str, Any]]:
    """{allocated, used, remaining, source, expires_at, expired} or None
    when the organization is not token-metered."""
    db = get_sync_db()
    if db is None or not organization_id:
        return None
    doc = db[BALANCES].find_one({"organization_id": str(organization_id)})
    if not doc:
        return None
    exp = _aware(doc.get("expires_at"))
    return {
        "allocated": int(doc.get("allocated", 0)),
        "used": int(doc.get("used", 0)),
        "remaining": int(doc.get("remaining", 0)),
        "source": doc.get("source"),
        "expires_at": exp.isoformat() if exp else None,
        "expired": bool(exp and exp < datetime.now(timezone.utc)),
    }


def allocate(organization_id: str, amount: int, *, source: str, actor: str,
             reason: str = "", expires_at: Optional[datetime] = None,
             reset: bool = False) -> Dict[str, Any]:
    """Grant tokens. ``reset=True`` replaces the balance (new plan period)."""
    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    org_id = str(organization_id)
    amount = int(amount)
    now = utcnow()
    if reset:
        db[BALANCES].update_one({"organization_id": org_id}, {"$set": {
            "allocated": amount, "used": 0, "remaining": amount, "source": source,
            "expires_at": expires_at, "notified_thresholds": [], "updated_at": now,
        }}, upsert=True)
    else:
        update: Dict[str, Any] = {"$inc": {"allocated": amount, "remaining": amount},
                                  "$set": {"source": source, "updated_at": now,
                                           "notified_thresholds": []},
                                  "$setOnInsert": {"used": 0}}
        if expires_at is not None:
            update["$set"]["expires_at"] = expires_at
        db[BALANCES].update_one({"organization_id": org_id}, update, upsert=True)
    bal = get_balance(org_id) or {}
    _ledger(db, org_id, "allocate", amount, reason=reason or source, actor=actor,
            balance_after=bal.get("remaining"))
    try:
        from app.admin.audit import audit
        audit("tokens.granted", "billing", user=actor, organization_id=org_id,
              resource_type="token_balance", resource_id=org_id,
              details={"amount": amount, "source": source, "reset": reset,
                       "expires_at": expires_at.isoformat() if expires_at else None})
    except Exception:
        pass
    return bal


def adjust(organization_id: str, delta: int, *, actor: str, reason: str) -> Dict[str, Any]:
    """Manual Super Admin adjustment (positive or negative)."""
    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    org_id = str(organization_id)
    delta = int(delta)
    q: Dict[str, Any] = {"organization_id": org_id}
    if delta < 0:
        q["remaining"] = {"$gte": -delta}
    res = db[BALANCES].update_one(q, {"$inc": {"allocated": delta, "remaining": delta},
                                      "$set": {"updated_at": utcnow()}})
    if res.matched_count == 0:
        raise HTTPException(status_code=400, detail="Adjustment would make the balance negative")
    bal = get_balance(org_id) or {}
    _ledger(db, org_id, "adjust", delta, reason=reason, actor=actor,
            balance_after=bal.get("remaining"))
    return bal


def consume(organization_id: str, amount: int, *, user_id: Optional[str] = None,
            reason: str = "", reference: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Atomically spend tokens. Raises TokensExhaustedException (402) when the
    balance is short or expired. Returns None when the org is not metered."""
    amount = int(amount)
    if amount <= 0 or not organization_id:
        return get_balance(organization_id) if organization_id else None
    db = get_sync_db()
    if db is None:
        return None
    org_id = str(organization_id)
    doc = db[BALANCES].find_one({"organization_id": org_id})
    if not doc:
        return None  # not token-metered
    exp = _aware(doc.get("expires_at"))
    if exp and exp < datetime.now(timezone.utc):
        raise TokensExhaustedException(amount, int(doc.get("remaining", 0)), expired=True)
    res = db[BALANCES].update_one(
        {"organization_id": org_id, "remaining": {"$gte": amount}},
        {"$inc": {"remaining": -amount, "used": amount}, "$set": {"updated_at": utcnow()}})
    if res.modified_count == 0:
        fresh = db[BALANCES].find_one({"organization_id": org_id}) or {}
        raise TokensExhaustedException(amount, int(fresh.get("remaining", 0)))
    bal = get_balance(org_id) or {}
    _ledger(db, org_id, "consume", amount, user_id=user_id, reason=reason,
            reference=reference, balance_after=bal.get("remaining"))
    _check_thresholds(db, org_id, bal)
    return bal


def refund(organization_id: str, amount: int, *, reason: str,
           reference: Optional[str] = None) -> None:
    """Give tokens back (e.g. a search that failed before doing any work)."""
    amount = int(amount)
    db = get_sync_db()
    if db is None or amount <= 0:
        return
    org_id = str(organization_id)
    res = db[BALANCES].update_one({"organization_id": org_id, "used": {"$gte": amount}},
                                  {"$inc": {"remaining": amount, "used": -amount}})
    if res.modified_count:
        bal = get_balance(org_id) or {}
        _ledger(db, org_id, "refund", amount, reason=reason, reference=reference,
                balance_after=bal.get("remaining"))


def _check_thresholds(db, org_id: str, bal: Dict[str, Any]) -> None:
    allocated = bal.get("allocated") or 0
    if allocated <= 0:
        return
    pct = bal.get("used", 0) * 100 / allocated
    for t in _THRESHOLDS:
        if pct < t:
            continue
        res = db[BALANCES].update_one(
            {"organization_id": org_id, "notified_thresholds": {"$ne": t}},
            {"$addToSet": {"notified_thresholds": t}})
        if not res.modified_count:
            continue
        try:
            from app.events.notifications import notify_org_admins, notify_super_admins
            title = f"Token usage reached {t}%"
            msg = f"{bal.get('used')}/{allocated} tokens used, {bal.get('remaining')} remaining."
            notify_org_admins(org_id, "usage_threshold", title, msg,
                              severity="warning" if t < 100 else "danger",
                              link="/org-admin#subscription")
            notify_super_admins("high_token_usage", title, f"Organization {org_id}: {msg}",
                                severity="warning", data={"organization_id": org_id})
        except Exception:
            pass
