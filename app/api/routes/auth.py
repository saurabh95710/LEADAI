"""
LeadAI Authentication & Session Routes.

  POST /api/auth/signup                 register = request a demo (NO access until approved)
  POST /api/auth/login                  sign in (per-IP throttle + per-account lockout)
  GET  /api/auth/me                     current user, re-resolved from the database
  POST /api/auth/switch-organization    switch active organization (membership verified)
  POST /api/auth/logout                 revoke session + clear cookie
  GET  /api/auth/sessions               my active sessions
  DELETE /api/auth/sessions/{id}        revoke one of my sessions ("all" = every other one)
  POST /api/auth/password/change        change password (current password required)
  POST /api/auth/password/forgot        email a single-use reset link (always 200)
  POST /api/auth/password/reset         set a new password with a reset token
"""
import hashlib
import logging
import re
import secrets
import time as _time
from datetime import timedelta
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from app.admin.audit import aaudit, audit, request_meta
from app.auth.crypto import hash_password
from app.auth.service import (
    _get_client_ip,
    _verify_and_migrate_password,
    account_locked_seconds,
    build_user_claims,
    clear_account_failures,
    clear_session_cookie,
    create_tracked_session,
    login_allowed,
    login_denied_seconds,
    public_user,
    record_account_failure,
    record_login_failure,
    reset_login_attempts,
    revoke_session,
    revoke_user_sessions,
    session_user,
    set_session_cookie,
    verify_admin_login,
    verify_saas_user_login,
)
from app.auth.rate_limit import RateLimiter
from app.db.models import utcnow
from app.db.mongo import get_sync_db
from app.events.email import absolute_url, send_email
from app.events.security import log_security_event

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

# ── Signup / reset rate limiting (per IP, MongoDB backed — durable and
# shared across processes; ``.clear()`` resets the in-memory fallback) ──────
_MAX_SIGNUP_ATTEMPTS = 5
_SIGNUP_WINDOW = 3600  # 1 hour
_MAX_RESET_ATTEMPTS = 5
_RESET_WINDOW = 3600  # 1 hour
_signup_attempts = RateLimiter("signup_ip", _MAX_SIGNUP_ATTEMPTS, _SIGNUP_WINDOW)
_reset_attempts = RateLimiter("password_reset_ip", _MAX_RESET_ATTEMPTS, _RESET_WINDOW)
_RESET_TOKEN_TTL_MIN = 60


def _signup_allowed(ip: str) -> bool:
    return _signup_attempts.consume(ip)


class LoginRequest(BaseModel):
    email: str
    password: str
    scope: str = "site"


class SignupRequest(BaseModel):
    email: str
    password: str
    name: Optional[str] = None
    organization_name: Optional[str] = None
    company: Optional[str] = None
    phone: Optional[str] = None
    message: Optional[str] = None


class SwitchOrgRequest(BaseModel):
    organization_id: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class ForgotRequest(BaseModel):
    email: str


class ResetRequest(BaseModel):
    token: str
    new_password: str


def _feature_enabled(key: str) -> bool:
    try:
        from app.admin.settings import get_setting
        val = get_setting(key)
        return True if val is None else bool(val)
    except Exception:
        return True


# ── Signup = demo request ───────────────────────────────────────────────────

@router.post("/signup")
async def signup(body: SignupRequest, request: Request):
    """Register for LeadAI. Creates a PENDING demo request — the visitor gets
    no session and no product access until a Super Admin approves it."""
    ip = _get_client_ip(request)
    if not _feature_enabled("features.demo_registration.enabled"):
        raise HTTPException(status_code=403, detail="New registrations are currently closed")
    if not _signup_allowed(ip):
        log_security_event("signup_rate_limited", "low", ip=ip, path=str(request.url.path))
        raise HTTPException(status_code=429, detail="Too many signup attempts. Please try again later.")
    from app.lifecycle.demo import create_demo_request
    company = (body.company or body.organization_name or "").strip()
    name = (body.name or body.email.split("@")[0]).strip()
    res = create_demo_request(name=name, email=body.email, password=body.password,
                              company=company, phone=body.phone,
                              message=body.message, ip=ip, source="signup")
    return {
        "success": True,
        "status": res["status"],
        "demo_request_id": res["id"],
        "message": ("Your demo request was received and is awaiting approval. "
                    "We'll email you as soon as your account is ready."),
    }


# ── Login ───────────────────────────────────────────────────────────────────

@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response):
    meta = request_meta(request)
    ip = meta["ip"] or "unknown"
    user_agent = meta["user_agent"] or "unknown"
    email = (body.email or "").strip().lower()

    if not login_allowed(ip):
        log_security_event("login_rate_limited", "medium", actor_email=email, ip=ip,
                           path=str(request.url.path))
        raise HTTPException(status_code=429,
                            detail=f"Too many failed attempts — try again in {login_denied_seconds(ip)}s")
    locked = account_locked_seconds(email)
    if locked:
        raise HTTPException(status_code=423, detail={
            "code": "account_locked",
            "message": f"Account temporarily locked. Try again in {max(1, locked // 60)} minute(s).",
            "retry_after": locked})

    user = None
    auth_error = None
    if body.scope == "admin":
        user = verify_admin_login(email, body.password)
        if user is None:
            saas_user, err = verify_saas_user_login(email, body.password, scope="admin")
            if saas_user and saas_user.get("is_platform_admin"):
                user = saas_user
    else:
        # Admins and users are database accounts only (RBAC + invitations).
        saas_user, err = verify_saas_user_login(email, body.password, scope="site")
        if saas_user:
            user = saas_user
        elif err and err != "Invalid email or password":
            auth_error = err

    blocking = {
        "Demo pending approval": (403, "demo_pending",
                                  "Your demo request is awaiting approval. We'll email you when it's ready."),
        "Demo request was not approved": (403, "demo_rejected",
                                          "Your demo request was not approved."),
        "Account is suspended": (403, "account_suspended", "Your account is suspended."),
        "Organization is suspended": (403, "organization_suspended",
                                      "Your organization is suspended."),
        "No active organization membership": (403, "no_active_organization",
                                              "Your account is not part of an active organization."),
    }
    if user is None:
        if auth_error in blocking:
            status, code, msg = blocking[auth_error]
            await aaudit("auth.login", "auth", user=email, ip=ip, success=False,
                         user_agent=user_agent, details={"reason": code, "scope": body.scope})
            raise HTTPException(status_code=status, detail={"code": code, "message": msg})
        record_login_failure(ip)
        just_locked = record_account_failure(email)
        log_security_event("account_locked" if just_locked else "login_failed",
                           "high" if just_locked else "low", actor_email=email, ip=ip,
                           path=str(request.url.path), details={"scope": body.scope})
        await aaudit("auth.login", "auth", user=email, ip=ip, success=False,
                     user_agent=user_agent, details={"scope": body.scope})
        raise HTTPException(status_code=401, detail="Invalid email or password")

    reset_login_attempts(ip)
    clear_account_failures(email)
    tracked_user = create_tracked_session(user, ip=ip, user_agent=user_agent)
    set_session_cookie(response, tracked_user)
    await aaudit("auth.login", "auth", user=tracked_user, ip=ip, user_agent=user_agent,
                 details={"scope": user.get("scope")})
    return {"success": True, "user": public_user(tracked_user)}


# ── Organization switch ─────────────────────────────────────────────────────

@router.post("/switch-organization")
async def switch_organization(body: SwitchOrgRequest, request: Request, response: Response):
    """Switch the active organization. Only organizations the user is an
    ACTIVE member of (platform staff use impersonation instead)."""
    user = session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in required")
    if user.get("scope") != "site" or user.get("impersonated_by"):
        raise HTTPException(status_code=403, detail="Switching organizations is not available for this session")
    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    try:
        org_doc = db["organizations"].find_one({"_id": ObjectId(body.organization_id.strip())})
    except Exception:
        org_doc = None
    if not org_doc:
        raise HTTPException(status_code=404, detail="Organization not found")
    membership = db["organization_members"].find_one({
        "user_id": str(user.get("user_id", "")), "organization_id": str(org_doc["_id"]),
        "status": "active"})
    if not membership:
        from app.events.security import security_event_from_request
        security_event_from_request(request, "cross_tenant_access", "high",
                                    target_organization_id=str(org_doc["_id"]),
                                    details={"action": "switch_organization",
                                             "actor": user.get("email")})
        raise HTTPException(status_code=403, detail="You are not a member of this organization")
    if org_doc.get("status") not in ("active", "demo", "trial"):
        raise HTTPException(status_code=403, detail="Organization is not active")
    record = db["users"].find_one({"_id": ObjectId(str(user["user_id"]))})
    db["users"].update_one({"_id": record["_id"]},
                           {"$set": {"default_organization_id": str(org_doc["_id"])}})
    record["default_organization_id"] = str(org_doc["_id"])
    claims, err = build_user_claims(db, record)
    if not claims:
        raise HTTPException(status_code=403, detail=err or "Organization unavailable")
    if user.get("session_id"):
        revoke_session(user["session_id"], revoked_by="switch_organization")
    meta = request_meta(request)
    tracked = create_tracked_session(claims, ip=meta["ip"] or "unknown",
                                     user_agent=meta["user_agent"] or "unknown")
    set_session_cookie(response, tracked)
    await aaudit("auth.switch_organization", "auth", user=tracked, ip=meta["ip"],
                 organization_id=str(org_doc["_id"]))
    return {"success": True, "user": public_user(tracked)}


# ── Me / logout ─────────────────────────────────────────────────────────────

@router.get("/me")
async def me(request: Request):
    """Current user, re-resolved from the database (roles are never stale)."""
    claims = session_user(request)
    if claims is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    from app.auth.tenant import resolve_tenant_context
    try:
        ctx = resolve_tenant_context(claims)
    except HTTPException as e:
        # e.g. demo pending, suspended, removed from organization
        raise e
    out = public_user(claims)
    out.update({
        "user_id": ctx.user_id, "name": ctx.name,
        "organization_id": ctx.organization_id,
        "organization_name": ctx.organization_name,
        "organization_slug": ctx.organization_slug,
        "organization_status": ctx.organization_status,
        "org_role": ctx.org_role,
        "role": ctx.platform_role or ctx.org_role,
        "platform_role": ctx.platform_role,
        "is_platform_admin": bool(ctx.platform_role) and not ctx.organization_id,
        "is_super_admin": ctx.is_super_admin,
        "permissions": ctx.permissions,
        "impersonation_expires_at": claims.get("impersonation_expires_at"),
    })
    if ctx.organization_id:
        db = get_sync_db()
        org = db.organizations.find_one({"_id": ObjectId(ctx.organization_id)}) if db is not None else None
        out["admin_portal_enabled"] = bool((org or {}).get("admin_portal_enabled")) or \
            (org or {}).get("status") == "active"
    return {"success": True, "user": out}


@router.post("/logout")
async def logout(request: Request, response: Response):
    user = session_user(request)
    if user and user.get("session_id"):
        revoke_session(user["session_id"], revoked_by="user_logout")
        await aaudit("auth.logout", "auth", user=user, **request_meta(request))
    clear_session_cookie(response)
    return {"success": True}


# ── Session management ──────────────────────────────────────────────────────

def _session_owner_key(user: dict) -> str:
    return str(user.get("user_id") or f"env:{user.get('email', '')}")


@router.get("/sessions")
async def my_sessions(request: Request):
    user = session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in required")
    db = get_sync_db()
    items = []
    for s in db["user_sessions"].find({"user_id": _session_owner_key(user), "revoked_at": None}).sort("created_at", -1).limit(50):
        items.append({
            "id": s["session_id"][:12],
            "current": s["session_id"] == user.get("session_id"),
            "user_agent": s.get("user_agent"),
            "created_at": s.get("created_at").isoformat() if s.get("created_at") else None,
            "expires_at": s.get("expires_at").isoformat() if s.get("expires_at") else None,
            "impersonated_by": s.get("impersonated_by"),
        })
    return {"success": True, "sessions": items}


@router.delete("/sessions/{short_id}")
async def revoke_my_session(short_id: str, request: Request):
    """Revoke one of my sessions by its short id, or 'others' for all but this one."""
    user = session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in required")
    db = get_sync_db()
    owner = _session_owner_key(user)
    now = utcnow()
    if short_id == "others":
        res = db["user_sessions"].update_many(
            {"user_id": owner, "revoked_at": None, "session_id": {"$ne": user.get("session_id")}},
            {"$set": {"revoked_at": now, "revoked_by": "user"}})
        count = res.modified_count
    else:
        if len(short_id) < 8:
            raise HTTPException(status_code=400, detail="Invalid session id")
        prefix = {"$regex": f"^{re.escape(short_id)}"}
        res = db["user_sessions"].update_one(
            {"user_id": owner, "revoked_at": None, "session_id": prefix},
            {"$set": {"revoked_at": now, "revoked_by": "user"}})
        count = res.modified_count
        if not count:
            other = db["user_sessions"].find_one({"user_id": {"$ne": owner}, "session_id": prefix},
                                                 {"organization_id": 1})
            if other:
                own_org = user.get("organization_id")
                same_org = bool(own_org) and other.get("organization_id") == own_org
                log_security_event("cross_user_access" if same_org else "cross_tenant_access",
                                   "medium" if same_org else "high",
                                   actor_email=user.get("email"), actor_user_id=user.get("user_id"),
                                   organization_id=own_org,
                                   target_organization_id=other.get("organization_id"),
                                   ip=request_meta(request)["ip"], path=str(request.url.path),
                                   method=request.method,
                                   details={"collection": "user_sessions",
                                            "resource_id": str(other["_id"])})
            raise HTTPException(status_code=404, detail="Session not found")
    await aaudit("auth.sessions_revoked", "auth", user=user, details={"count": count},
                 **request_meta(request))
    return {"success": True, "revoked": count}


# ── Passwords ───────────────────────────────────────────────────────────────

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@router.post("/password/change")
async def change_password(body: ChangePasswordRequest, request: Request, response: Response):
    user = session_user(request)
    if not user or not user.get("user_id"):
        raise HTTPException(status_code=401, detail="Sign in required")
    from app.lifecycle.demo import validate_password
    validate_password(body.new_password)
    db = get_sync_db()
    record = db["users"].find_one({"_id": ObjectId(str(user["user_id"]))})
    if not record:
        raise HTTPException(status_code=404, detail="Account not found")
    ok, _ = _verify_and_migrate_password(body.current_password, record.get("password_hash") or "")
    if not ok:
        log_security_event("password_change_failed", "medium", actor_email=user.get("email"),
                           actor_user_id=str(user["user_id"]), ip=request_meta(request)["ip"])
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    db["users"].update_one({"_id": record["_id"]}, {"$set": {
        "password_hash": hash_password(body.new_password), "password_changed_at": utcnow(),
        "updated_at": utcnow()}})
    # sign out every other session
    db["user_sessions"].update_many(
        {"user_id": str(record["_id"]), "revoked_at": None,
         "session_id": {"$ne": user.get("session_id")}},
        {"$set": {"revoked_at": utcnow(), "revoked_by": "password_change"}})
    await aaudit("auth.password_changed", "auth", user=user, **request_meta(request))
    return {"success": True, "message": "Password updated. Other sessions were signed out."}


@router.post("/password/forgot")
async def forgot_password(body: ForgotRequest, request: Request):
    """Always answers 200 so it cannot be used to discover accounts."""
    ip = _get_client_ip(request)
    email = (body.email or "").strip().lower()
    generic = {"success": True,
               "message": "If an account exists for that email, a reset link is on its way."}
    if not _reset_attempts.consume(ip):
        log_security_event("password_reset_rate_limited", "low", actor_email=email, ip=ip)
        return generic
    db = get_sync_db()
    if db is None or not email:
        return generic
    record = db["users"].find_one({"email": email})
    if not record or record.get("status") not in ("active",):
        return generic
    token = secrets.token_urlsafe(32)
    now = utcnow()
    db["password_resets"].update_many({"user_id": str(record["_id"]), "used_at": None},
                                      {"$set": {"used_at": now, "invalidated": True}})
    db["password_resets"].insert_one({
        "user_id": str(record["_id"]), "token_hash": _hash_token(token),
        "expires_at": now + timedelta(minutes=_RESET_TOKEN_TTL_MIN), "used_at": None,
        "requested_ip": ip, "created_at": now})
    link = absolute_url(f"/reset-password?token={token}")
    send_email(email, "Reset your LeadAI password",
               f"Hi {record.get('name') or ''},\n\nUse this link to choose a new password "
               f"(valid for {_RESET_TOKEN_TTL_MIN} minutes, single use):\n{link}\n\n"
               "If you didn't ask for this, you can ignore this email.", kind="password_reset")
    log_security_event("password_reset_requested", "low", actor_email=email,
                       actor_user_id=str(record["_id"]), ip=ip)
    audit("auth.password_reset_requested", "auth", user=email, ip=ip,
          resource_type="user", resource_id=str(record["_id"]))
    return generic


@router.post("/password/reset")
async def reset_password(body: ResetRequest, request: Request):
    from app.lifecycle.demo import validate_password
    validate_password(body.new_password)
    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    now = utcnow()
    doc = db["password_resets"].find_one_and_update(
        {"token_hash": _hash_token(body.token.strip()), "used_at": None,
         "expires_at": {"$gt": now}},
        {"$set": {"used_at": now}})
    if not doc:
        raise HTTPException(status_code=400, detail="This reset link is invalid or has expired.")
    uid = ObjectId(doc["user_id"])
    db["users"].update_one({"_id": uid}, {"$set": {
        "password_hash": hash_password(body.new_password), "password_changed_at": now,
        "updated_at": now}})
    revoke_user_sessions(doc["user_id"], revoked_by="password_reset")
    record = db["users"].find_one({"_id": uid}, {"email": 1})
    clear_account_failures((record or {}).get("email", ""))
    ip = _get_client_ip(request)
    log_security_event("password_reset_completed", "low", actor_email=(record or {}).get("email"),
                       actor_user_id=doc["user_id"], ip=ip)
    await aaudit("auth.password_reset", "auth", user=(record or {}).get("email"), ip=ip,
                 resource_type="user", resource_id=doc["user_id"])
    return {"success": True, "message": "Password updated. You can now sign in."}
