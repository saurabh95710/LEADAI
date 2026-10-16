"""
Who provides each external API for an organization, and the organization's own keys.

LeadAI calls two paid APIs: Apify (scraping) and Google Gemini (AI analysis).
Per API, an organization either uses LeadAI's ("leadai", included in the plan
price) or brings its own key ("own", cheaper plan, it pays Apify / Google
directly). Any mix is allowed: own Apify + LeadAI Gemini, etc.

Organization document:
    api_coverage    {"apify": "leadai"|"own", "gemini": "leadai"|"own"}
    custom_api_keys {"apify":  {"ciphertext", "hint", "verified", "verified_at", "last_error", "updated_at"},
                     "gemini": {...}}

Rules enforced here:
- keys are encrypted at rest (app.services.secret_box) and NEVER returned,
  logged or audited in clear; ``public_config`` only carries masked hints
- an API set to "own" never falls back to LeadAI's key: a missing or rejected
  key stops the work with a clear reason (source "organization_missing") and
  the organization's admins are told
- the older draft fields (``api_mode``, plaintext ``custom_api_keys.apify_token``)
  are read transparently and rewritten encrypted by ``migrate_tenant_api_keys``
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

import httpx
from bson import ObjectId

from app.billing.plans import API_PROVIDERS, normalize_coverage
from app.db.models import utcnow
from app.db.mongo import get_async_db, get_sync_db
from app.services import secret_box

logger = logging.getLogger(__name__)

PROVIDER_NAMES = {"apify": "Apify", "gemini": "Google Gemini"}
_LEGACY_FIELDS = {"apify": "apify_token", "gemini": "gemini_api_key"}
_ALERT_EVERY = timedelta(hours=6)


def mask_key(secret: Optional[str]) -> str:
    """Masked form for display: first 4 and last 4 characters only."""
    if not secret:
        return ""
    secret = str(secret).strip()
    if len(secret) <= 10:
        return "••••••••"
    return f"{secret[:4]}…{secret[-4:]}"


def _oid(organization_id: Any):
    s = str(organization_id)
    return ObjectId(s) if ObjectId.is_valid(s) else s


def _check_provider(provider: str) -> str:
    if provider not in API_PROVIDERS:
        raise ValueError(f"Unknown API provider: {provider}")
    return provider


# ── reading the organization document ───────────────────────────────────────

def coverage_of(org: Optional[Dict[str, Any]]) -> Dict[str, str]:
    org = org or {}
    if isinstance(org.get("api_coverage"), dict):
        return normalize_coverage(org["api_coverage"])
    if org.get("api_mode") == "byok":   # older all-or-nothing switch
        keys = org.get("custom_api_keys") or {}
        return {api: ("own" if _entry(keys, api).get("configured") else "leadai") for api in API_PROVIDERS}
    return normalize_coverage(None)


def _entry(keys: Dict[str, Any], provider: str) -> Dict[str, Any]:
    """Normalized key entry; reads the old plaintext layout too."""
    e = keys.get(provider)
    if isinstance(e, dict):
        return {**e, "configured": bool(e.get("ciphertext"))}
    legacy = keys.get(_LEGACY_FIELDS[provider])
    if legacy:
        return {"legacy_plain": legacy, "hint": mask_key(legacy), "configured": True,
                "verified": bool(keys.get(f"{provider}_verified")), "last_error": None}
    return {"configured": False}


def _plain_key(org: Optional[Dict[str, Any]], provider: str) -> Optional[str]:
    e = _entry((org or {}).get("custom_api_keys") or {}, provider)
    if e.get("legacy_plain"):
        return str(e["legacy_plain"])
    return secret_box.decrypt(e.get("ciphertext"))


def iso_utc(value: Any) -> Any:
    """Mongo returns naive UTC datetimes; send them with their zone so browsers
    don't read them as local time."""
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()
    return value


def public_config(org: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Safe to send to a browser: never a key, only masked hints and status."""
    keys = (org or {}).get("custom_api_keys") or {}
    out: Dict[str, Any] = {"coverage": coverage_of(org), "keys": {}}
    for api in API_PROVIDERS:
        e = _entry(keys, api)
        unreadable = bool(e.get("ciphertext")) and secret_box.decrypt(e.get("ciphertext")) is None
        out["keys"][api] = {
            "name": PROVIDER_NAMES[api], "configured": bool(e.get("configured")) and not unreadable,
            "hint": e.get("hint") or "", "verified": bool(e.get("verified")) and not unreadable,
            "verified_at": iso_utc(e.get("verified_at")),
            "last_error": "The saved key can't be read any more. Enter it again." if unreadable else e.get("last_error"),
            "updated_at": iso_utc(e.get("updated_at")),
        }
    return out


def _org_sync(organization_id: Any) -> Optional[Dict[str, Any]]:
    if not organization_id:
        return None
    db = get_sync_db()
    if db is None:
        return None
    try:
        return db.organizations.find_one({"_id": _oid(organization_id)})
    except Exception as e:
        logger.warning("Error fetching organization %s: %s", organization_id, e)
        return None


def get_tenant_api_config_sync(organization_id: Optional[str]) -> Dict[str, Any]:
    return public_config(_org_sync(organization_id))


async def get_tenant_api_config_async(organization_id: Optional[str], db=None) -> Dict[str, Any]:
    db = db if db is not None else get_async_db()
    org = await db.organizations.find_one({"_id": _oid(organization_id)}) if (db is not None and organization_id) else None
    return public_config(org)


# ── resolving the key a call must use ───────────────────────────────────────

def platform_key(provider: str) -> str:
    _check_provider(provider)
    if provider == "apify":
        from app.admin.settings import get_apify_token
        return get_apify_token() or ""
    from app.admin.envvars import get_envvar_str
    from app.config import get_settings
    return get_envvar_str("GEMINI_API_KEY", get_settings().gemini_api_key) or ""


def resolve_api_key(organization_id: Optional[str], provider: str) -> Tuple[str, str]:
    """(key, source). source: "platform" (LeadAI pays), "organization" (the
    organization's own key) or "organization_missing" (the organization chose
    its own key but has none usable). Never falls back to LeadAI's key for an
    API the organization chose to provide itself."""
    _check_provider(provider)
    if not organization_id:
        return platform_key(provider), "platform"
    org = _org_sync(organization_id)
    if coverage_of(org)[provider] != "own":
        return platform_key(provider), "platform"
    key = _plain_key(org, provider)
    if key:
        return key, "organization"
    return "", "organization_missing"


def get_tenant_apify_token_sync(organization_id: Optional[str]) -> Tuple[str, str]:
    return resolve_api_key(organization_id, "apify")


def get_tenant_gemini_key_sync(organization_id: Optional[str]) -> Tuple[str, str]:
    return resolve_api_key(organization_id, "gemini")


def own_key_problem(organization_id: Optional[str], provider: str) -> Optional[Dict[str, str]]:
    """None when the API can be used; otherwise {code, message} for the user."""
    _, source = resolve_api_key(organization_id, provider)
    if source != "organization_missing":
        return None
    name = PROVIDER_NAMES[provider]
    return {"code": f"OWN_{provider.upper()}_KEY_MISSING",
            "message": (f"Your organization uses its own {name} key, but no working key is saved. "
                        f"An owner or admin can add it in Admin portal → API keys & plan, "
                        f"or switch {name} to LeadAI-provided.")}


def record_key_failure(organization_id: Optional[str], provider: str, code: str, message: str) -> None:
    """The organization's own key failed (missing, rejected, out of credit):
    remember the error and tell the organization's admins (at most every 6 h)."""
    if not organization_id:
        return
    _check_provider(provider)
    db = get_sync_db()
    if db is None:
        return
    try:
        now = utcnow()
        org = db.organizations.find_one({"_id": _oid(organization_id)}, {"custom_api_keys": 1, "api_key_alerts": 1})
        if not org:
            return
        db.organizations.update_one({"_id": org["_id"]}, {"$set": {
            f"custom_api_keys.{provider}.last_error": message[:300],
            f"custom_api_keys.{provider}.last_error_code": code}})
        last = ((org.get("api_key_alerts") or {}).get(provider))
        last = last.replace(tzinfo=now.tzinfo) if last is not None and last.tzinfo is None else last
        if last is None or now - last > _ALERT_EVERY:
            db.organizations.update_one({"_id": org["_id"]}, {"$set": {f"api_key_alerts.{provider}": now}})
            from app.events.notifications import notify_org_admins
            notify_org_admins(str(organization_id), "api_key_problem",
                              f"Your {PROVIDER_NAMES[provider]} key needs attention", message,
                              severity="danger", email=True, link="/org-admin#integrations")
    except Exception as e:  # alerting never breaks the caller
        logger.warning("Could not record %s key failure for %s: %s", provider, organization_id, e)


# ── changing keys and coverage ──────────────────────────────────────────────

def _validate_key_text(provider: str, key: str) -> str:
    key = (key or "").strip()
    if not key:
        raise ValueError(f"Enter your {PROVIDER_NAMES[provider]} key")
    if len(key) > 500 or any(c.isspace() for c in key):
        raise ValueError(f"That doesn't look like a {PROVIDER_NAMES[provider]} key")
    return key


async def save_key(organization_id: str, provider: str, key: str, *, actor_email: str, db=None,
                   verify: bool = True) -> Dict[str, Any]:
    """Encrypt and store the organization's key, then test it. Returns
    {"test": {...}, "config": public_config}."""
    _check_provider(provider)
    key = _validate_key_text(provider, key)
    db = db if db is not None else get_async_db()
    org = await db.organizations.find_one({"_id": _oid(organization_id)})
    if not org:
        raise ValueError("Organization not found")
    test = await (test_apify_token_connection(key) if provider == "apify" else test_gemini_key_connection(key)) \
        if verify else {"valid": False, "detail": "not tested"}
    now = utcnow()
    entry = {"ciphertext": secret_box.encrypt(key), "hint": mask_key(key), "verified": bool(test.get("valid")),
             "verified_at": now if test.get("valid") else None,
             "last_error": None if test.get("valid") else (test.get("detail") or "The key could not be verified"),
             "updated_at": now, "updated_by": actor_email}
    unset = {f"custom_api_keys.{_LEGACY_FIELDS[provider]}": "", f"custom_api_keys.{provider}_verified": "",
             f"custom_api_keys.{provider}_token_hint": "", f"custom_api_keys.{provider}_key_hint": ""}
    await db.organizations.update_one({"_id": org["_id"]}, {"$set": {f"custom_api_keys.{provider}": entry,
                                                                    "updated_at": now}, "$unset": unset})
    fresh = await db.organizations.find_one({"_id": org["_id"]})
    safe_test = {k: v for k, v in test.items() if k in ("valid", "detail", "message", "username", "models_count")}
    return {"test": safe_test, "config": public_config(fresh)}


async def verify_saved_key(organization_id: str, provider: str, *, db=None) -> Dict[str, Any]:
    """Test the stored key again (e.g. after topping up the provider account)."""
    _check_provider(provider)
    db = db if db is not None else get_async_db()
    org = await db.organizations.find_one({"_id": _oid(organization_id)})
    key = _plain_key(org, provider)
    if not key:
        return {"test": {"valid": False, "detail": "No key saved"}, "config": public_config(org)}
    test = await (test_apify_token_connection(key) if provider == "apify" else test_gemini_key_connection(key))
    now = utcnow()
    await db.organizations.update_one({"_id": org["_id"]}, {"$set": {
        f"custom_api_keys.{provider}.verified": bool(test.get("valid")),
        f"custom_api_keys.{provider}.verified_at": now if test.get("valid") else None,
        f"custom_api_keys.{provider}.last_error": None if test.get("valid") else test.get("detail")}})
    fresh = await db.organizations.find_one({"_id": org["_id"]})
    safe_test = {k: v for k, v in test.items() if k in ("valid", "detail", "message", "username", "models_count")}
    return {"test": safe_test, "config": public_config(fresh)}


async def remove_key(organization_id: str, provider: str, *, db=None) -> Dict[str, Any]:
    _check_provider(provider)
    db = db if db is not None else get_async_db()
    await db.organizations.update_one({"_id": _oid(organization_id)}, {"$unset": {
        f"custom_api_keys.{provider}": "", f"custom_api_keys.{_LEGACY_FIELDS[provider]}": "",
        f"custom_api_keys.{provider}_verified": ""}, "$set": {"updated_at": utcnow()}})
    return public_config(await db.organizations.find_one({"_id": _oid(organization_id)}))


async def set_coverage(organization_id: str, coverage: Dict[str, str], *, db=None) -> Dict[str, str]:
    """Store who provides each API (billing rules live in app.billing.api_coverage)."""
    cov = normalize_coverage(coverage)
    db = db if db is not None else get_async_db()
    await db.organizations.update_one({"_id": _oid(organization_id)},
                                      {"$set": {"api_coverage": cov, "updated_at": utcnow()},
                                       "$unset": {"api_mode": ""}})
    return cov


def migrate_tenant_api_keys(db) -> int:
    """One-time: older draft data -> per-API coverage + encrypted keys. Idempotent."""
    n = 0
    for org in db.organizations.find({"$or": [{"api_mode": {"$exists": True}},
                                              {"custom_api_keys.apify_token": {"$exists": True}},
                                              {"custom_api_keys.gemini_api_key": {"$exists": True}}]}):
        sets: Dict[str, Any] = {}
        unset: Dict[str, str] = {"api_mode": ""}
        keys = org.get("custom_api_keys") or {}
        for api in API_PROVIDERS:
            legacy = keys.get(_LEGACY_FIELDS[api])
            if legacy and not isinstance(keys.get(api), dict):
                sets[f"custom_api_keys.{api}"] = {
                    "ciphertext": secret_box.encrypt(str(legacy).strip()), "hint": mask_key(str(legacy)),
                    "verified": bool(keys.get(f"{api}_verified")), "verified_at": None, "last_error": None,
                    "updated_at": utcnow(), "updated_by": "migration"}
            for f in (_LEGACY_FIELDS[api], f"{api}_verified", f"{api}_token_hint", f"{api}_key_hint"):
                unset[f"custom_api_keys.{f}"] = ""
        if not isinstance(org.get("api_coverage"), dict):
            sets["api_coverage"] = coverage_of(org)
        db.organizations.update_one({"_id": org["_id"]}, {"$set": sets, "$unset": unset} if sets else {"$unset": unset})
        n += 1
    if n:
        logger.info("Migrated API key settings of %d organization(s)", n)
    return n


# ── testing a key against the provider ──────────────────────────────────────

async def test_apify_token_connection(token: str) -> Dict[str, Any]:
    """Test validity of an Apify API token against Apify User API."""
    if not token or not token.strip():
        return {"valid": False, "detail": "Apify token is empty"}
    token = token.strip()
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            resp = await client.get(
                "https://api.apify.com/v2/users/me",
                headers={"Authorization": f"Bearer {token}"},
            )
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            return {
                "valid": True,
                "username": data.get("username"),
                "message": f"Connected as {data.get('username') or 'your Apify account'}",
            }
        if resp.status_code in (401, 403):
            return {"valid": False, "status_code": resp.status_code,
                    "detail": "Apify rejected this token. Copy it again from Apify Console → Settings → Integrations."}
        return {"valid": False, "status_code": resp.status_code, "detail": f"Apify returned HTTP {resp.status_code}"}
    except Exception as e:
        logger.warning("Apify connection test failed: %s", type(e).__name__)
        return {"valid": False, "detail": "Couldn't reach Apify to test the token. Try again in a moment."}


async def test_gemini_key_connection(api_key: str) -> Dict[str, Any]:
    """Test validity of a Google Gemini API key."""
    if not api_key or not api_key.strip():
        return {"valid": False, "detail": "Gemini API key is empty"}
    api_key = api_key.strip()
    try:
        url = "https://generativelanguage.googleapis.com/v1beta/models"
        async with httpx.AsyncClient(timeout=12) as client:
            resp = await client.get(url, headers={"x-goog-api-key": api_key})
        if resp.status_code == 200:
            models = [m.get("name", "") for m in resp.json().get("models", [])]
            return {"valid": True, "models_count": len(models),
                    "message": f"Key works ({len(models)} models available)"}
        if resp.status_code in (400, 401, 403):
            err_msg = ""
            try:
                err_msg = resp.json().get("error", {}).get("message", "")
            except Exception:
                pass
            return {"valid": False, "status_code": resp.status_code,
                    "detail": err_msg or "Google rejected this key. Create one in Google AI Studio → API keys."}
        return {"valid": False, "status_code": resp.status_code, "detail": f"Gemini API returned HTTP {resp.status_code}"}
    except Exception as e:
        logger.warning("Gemini connection test failed: %s", type(e).__name__)
        return {"valid": False, "detail": "Couldn't reach Google to test the key. Try again in a moment."}
