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
import asyncio
import logging
import re
import secrets
from datetime import timedelta
from typing import Any, Dict, Optional

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
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
    totp_code: Optional[str] = None


class SignupRequest(BaseModel):
    email: str
    password: str
    name: Optional[str] = None
    organization_name: Optional[str] = None
    company: Optional[str] = None
    phone: Optional[str] = None
    message: Optional[str] = None
    industry: Optional[str] = None
    plan: Optional[str] = None             # plan chosen on the pricing page (interest only)
    accepted_terms: Optional[bool] = None  # Terms & Privacy consent from the form
    ref_code: Optional[str] = None         # partner referral code (?ref= on the signup page)


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


# ── Signup = free trial (or a demo request) ────────────────────────────────

@router.post("/signup")
async def signup(body: SignupRequest, request: Request, response: Response):
    """Register for LeadAI. With the self-serve free trial on (demo settings →
    auto-approve, the default) the trial starts at once and the visitor is
    signed in to the user panel. Off: a PENDING demo request — no session and no
    product access until a Super Admin approves it."""
    ip = _get_client_ip(request)
    if not _feature_enabled("features.demo_registration.enabled"):
        raise HTTPException(status_code=403, detail="New registrations are currently closed")
    if not _signup_allowed(ip):
        log_security_event("signup_rate_limited", "low", ip=ip, path=str(request.url.path))
        raise HTTPException(status_code=429, detail="Too many signup attempts. Please try again later.")
    from app.lifecycle.demo import create_demo_request
    company = (body.company or body.organization_name or "").strip()
    name = (body.name or body.email.split("@")[0]).strip()
    res = await asyncio.to_thread(create_demo_request, name=name, email=body.email, password=body.password,
                              company=company, phone=body.phone,
                              message=body.message, ip=ip, source="signup",
                              industry=body.industry, requested_plan=body.plan,
                              accepted_terms=bool(body.accepted_terms))
    # Partner attribution: signed referral cookie (from /r/{code}) or a typed code
    if res.get("organization_id"):
        from app.partners.constants import REF_COOKIE
        from app.partners.referrals import attribute_signup
        await asyncio.to_thread(
            attribute_signup, organization_id=res["organization_id"], user_id=None,
            email=body.email, company=company, phone=body.phone, ip=ip,
            cookie_value=request.cookies.get(REF_COOKIE), ref_code=body.ref_code)
    # Email verification token generation on signup (Phase 4)
    db = get_sync_db()
    if db is not None:
        token = secrets.token_urlsafe(32)
        now = utcnow()
        db["email_verifications"].insert_one({
            "email": body.email.strip().lower(),
            "token_hash": _hash_token(token),
            "demo_request_id": res.get("id"),
            "expires_at": now + timedelta(hours=24),
            "used_at": None,
            "created_at": now,
        })
        link = absolute_url(f"/verify-email?token={token}")
        await asyncio.to_thread(
            send_email,
            body.email.strip().lower(),
            "Verify your LeadAI email address",
            f"Hi {name},\n\nPlease verify your email address by clicking the link below:\n{link}\n\n"
            "Thank you for registering with LeadAI.",
            kind="email_verification",
        )

    if res.get("status") == "approved":
        # free trial started: sign the new owner straight in to the user panel
        user, _err = verify_saas_user_login(body.email.strip().lower(), body.password, scope="site")
        if user:
            meta = request_meta(request)
            tracked = create_tracked_session(user, ip=ip, user_agent=meta.get("user_agent"))
            set_session_cookie(response, tracked)
            await aaudit("auth.login", "auth", user=tracked, ip=ip, user_agent=meta.get("user_agent"),
                         details={"scope": "site", "via": "signup"})
            from app.lifecycle.config import get_demo_config
            cfg = get_demo_config()
            return {
                "success": True, "status": "approved", "demo_request_id": res["id"],
                "signed_in": True, "redirect": "/user",
                "trial": {"days": int(cfg.get("duration_days") or 0), "tokens": int(cfg.get("tokens") or 0)},
                "message": (f"Your {cfg.get('duration_days')}-day free trial has started with "
                            f"{cfg.get('tokens')} tokens."),
            }
    return {
        "success": True,
        "status": res["status"],
        "demo_request_id": res["id"],
        "signed_in": False,
        "message": ("Your demo request was received and is awaiting approval. "
                    "We'll email you as soon as your account is ready."
                    if res.get("status") == "pending" else "Your account is ready. Sign in to start."),
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
        # 1. Check env-var super-admin credentials first.
        user = verify_admin_login(email, body.password)
        if user is None:
            # 2. Check DB-backed platform admins (is_platform_admin flag).
            saas_user, err = verify_saas_user_login(email, body.password, scope="admin")
            if saas_user and saas_user.get("is_platform_admin"):
                user = saas_user
            else:
                # 3. Fall through: org-admin users reach the login page via
                #    /login?admin=1 which sends scope="admin".  They are NOT
                #    platform admins, so we re-try with scope="site" so they
                #    can access the org-admin portal normally.
                if err in (None, "Invalid email or password", "Not a platform account"):
                    site_user, site_err = verify_saas_user_login(email, body.password, scope="site")
                    if site_user:
                        user = site_user
                    elif site_err and site_err != "Invalid email or password":
                        auth_error = site_err
                elif err:
                    auth_error = err
    elif body.scope == "partner":
        # Partners / applicants: same users collection, partner-scope session
        from app.partners.auth import verify_partner_login
        user, err = verify_partner_login(email, body.password)
        if user is None and err and err != "Invalid email or password":
            auth_error = err
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
        "Not a partner account": (403, "not_a_partner",
                                  "This account is not a LeadAI partner. Apply at /partners."),
        "Partner application rejected": (403, "partner_rejected",
                                         "Your partner application was not approved."),
        "Partner suspended": (403, "partner_suspended", "Your partner account is suspended."),
    }
    if body.scope == "partner" and user is None:
        _partner_auth_event(email, "login", False, auth_error or "invalid_credentials", ip, user_agent)
    if user is None:
        if auth_error in blocking:
            status, code, msg = blocking[auth_error]
            await aaudit("auth.login", "auth", user=email, ip=ip, success=False,
                         user_agent=user_agent, details={"reason": code, "scope": body.scope})
            raise HTTPException(status_code=status, detail={"code": code, "message": msg})
        if body.scope == "admin":
            # operator diagnostics in the server log (e.g. Render → Logs);
            # the client only ever sees the generic message below
            from app.auth.superadmin import explain_login_failure
            logger.warning("Super Admin portal sign-in failed: %s", explain_login_failure(email))
        record_login_failure(ip)
        just_locked = record_account_failure(email)
        log_security_event("account_locked" if just_locked else "login_failed",
                           "high" if just_locked else "low", actor_email=email, ip=ip,
                           path=str(request.url.path), details={"scope": body.scope})
        await aaudit("auth.login", "auth", user=email, ip=ip, success=False,
                     user_agent=user_agent, details={"scope": body.scope})
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # RFC 6238 TOTP Two-Factor Authentication. The flag and secret live on the
    # users record (never in session claims), so read them from the database.
    totp_record = _totp_record(user)
    if totp_record and totp_record.get("totp_enabled"):
        totp_secret = totp_record.get("totp_secret")
        if not body.totp_code:
            if body.scope == "partner":
                _partner_auth_event(email, "2fa_required", True, None, ip, user_agent)
            return JSONResponse(
                {"success": False, "requires_2fa": True, "message": "Two-factor authentication code required"},
                status_code=200,
            )
        from app.auth.totp import verify_totp_code
        if not totp_secret or not verify_totp_code(totp_secret, body.totp_code):
            record_login_failure(ip)
            log_security_event("totp_failed", "medium", actor_email=email, ip=ip, path=str(request.url.path))
            if body.scope == "partner":
                _partner_auth_event(email, "login", False, "invalid_2fa_code", ip, user_agent)
            raise HTTPException(status_code=401, detail="Invalid two-factor authentication code")

    reset_login_attempts(ip)
    clear_account_failures(email)
    # an administrator set this password: the person must choose their own first
    from app.auth.credentials import must_change_password
    if must_change_password(user):
        user = {**user, "must_change_password": True}
    tracked_user = create_tracked_session(user, ip=ip, user_agent=user_agent)
    set_session_cookie(response, tracked_user)
    await aaudit("auth.login", "auth", user=tracked_user, ip=ip, user_agent=user_agent,
                 details={"scope": user.get("scope")})
    if user.get("scope") == "partner":
        _partner_auth_event(email, "login", True, None, ip, user_agent,
                            session_id=(tracked_user.get("session_id") or "")[:12])
    out: Dict[str, Any] = {"success": True, "user": public_user(tracked_user)}
    if tracked_user.get("must_change_password"):
        out.update({"must_change_password": True, "redirect": "/change-password"})
    return out


def _partner_auth_event(email: str, action: str, success: bool, reason: Optional[str], ip: Optional[str],
                        user_agent: Optional[str], session_id: Optional[str] = None) -> None:
    """Partner sign-in activity for the Super Admin (only for real partner /
    applicant accounts; unknown emails are covered by security events)."""
    try:
        db = get_sync_db()
        u = db["users"].find_one({"email": (email or "").lower()}, {"_id": 1}) if db is not None else None
        if not u:
            return
        from app.partners import constants as PK
        uid = str(u["_id"])
        p = db[PK.PARTNERS].find_one({"user_id": uid}, {"_id": 1})
        if not p and not db[PK.APPLICATIONS].find_one({"user_id": uid}, {"_id": 1}):
            return
        from app.partners.activity import record
        record("auth", action, partner_id=str(p["_id"]) if p else None, user_id=uid, email=email, ip=ip,
               user_agent=user_agent, success=success,
               details={k: v for k, v in (("reason", reason), ("session", session_id)) if v})
    except Exception:
        pass


def _totp_record(user: dict) -> Optional[dict]:
    db = get_sync_db()
    if db is None:
        return None
    try:
        if user.get("user_id") and ObjectId.is_valid(str(user["user_id"])):
            return db["users"].find_one({"_id": ObjectId(str(user["user_id"]))},
                                        {"totp_enabled": 1, "totp_secret": 1})
        return db["users"].find_one({"email": (user.get("email") or "").lower()},
                                    {"totp_enabled": 1, "totp_secret": 1})
    except Exception:
        return None


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

@router.get("/status")
async def session_status(request: Request):
    """Signed in, and to which portal? Always 200 (the public website asks, so
    signed-out visitors don't get an error). Carries no personal data."""
    user = await asyncio.to_thread(session_user, request)
    scope = (user or {}).get("scope")
    home = {"site": "/user", "admin": "/admin", "partner": "/partner"}.get(scope or "")
    if scope == "admin" and (user or {}).get("platform_role") == "super_admin":
        home = "/superadmin"
    return {"signed_in": bool(user), "scope": scope, "home": home}


@router.get("/me")
def me(request: Request):
    """Current user, re-resolved from the database (roles are never stale).
    Plain ``def``: its database reads are sync, so FastAPI runs it in the
    threadpool instead of blocking the event loop."""
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
        # the business context every search of this organization is analysed with
        from app.pipeline.business_context import build_context
        biz = build_context(org or {}, db)
        out["industry"] = {"key": biz["industry_key"], "name": biz["industry_name"]}
    return {"success": True, "user": out}


@router.post("/logout")
async def logout(request: Request, response: Response):
    user = session_user(request)
    if user and user.get("session_id"):
        revoke_session(user["session_id"], revoked_by="user_logout")
        meta = request_meta(request)
        await aaudit("auth.logout", "auth", user=user, ip=meta["ip"], user_agent=meta["user_agent"])
        if user.get("scope") == "partner":
            _partner_auth_event(user.get("email") or "", "logout", True, None, meta["ip"], meta["user_agent"])
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
    meta = request_meta(request)
    await aaudit("auth.sessions_revoked", "auth", user=user, details={"count": count},
                 ip=meta["ip"], user_agent=meta["user_agent"])
    return {"success": True, "revoked": count}


# ── Passwords ───────────────────────────────────────────────────────────────

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@router.post("/password/change")
async def change_password(body: ChangePasswordRequest, request: Request, response: Response):
    """Change your own password (current password required). Works for every
    account kind; clears an administrator's "must change password" flag."""
    user = session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in required")
    from app.auth.credentials import own_account
    from app.auth.superadmin import is_superadmin_email
    if is_superadmin_email(user.get("email")) and not user.get("user_id"):
        raise HTTPException(status_code=409, detail=(
            "The Super Admin password is set by SUPERADMIN_PASSWORD in the hosting environment "
            "and cannot be changed in the app."))
    from app.lifecycle.demo import validate_password
    validate_password(body.new_password)
    db = get_sync_db()
    coll, record = own_account(user, db)
    if not record:
        raise HTTPException(status_code=404, detail="Account not found")
    ok, _ = _verify_and_migrate_password(body.current_password, record.get("password_hash") or "")
    if not ok:
        log_security_event("password_change_failed", "medium", actor_email=user.get("email"),
                           actor_user_id=str(user.get("user_id") or ""), ip=request_meta(request)["ip"])
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if body.new_password == body.current_password:
        raise HTTPException(status_code=422, detail="Choose a password different from the current one")
    db[coll].update_one({"_id": record["_id"]}, {"$set": {
        "password_hash": hash_password(body.new_password), "password_changed_at": utcnow(),
        "must_change_password": False, "updated_at": utcnow()}})
    # sign out every other session
    owner = str(record["_id"]) if coll == "users" else f"env:{(record.get('email') or '').lower()}"
    db["user_sessions"].update_many(
        {"user_id": owner, "revoked_at": None, "session_id": {"$ne": user.get("session_id")}},
        {"$set": {"revoked_at": utcnow(), "revoked_by": "password_change"}})
    if user.get("must_change_password"):
        # this session stays signed in, now without the restriction
        user.pop("must_change_password", None)
        set_session_cookie(response, user)
    meta = request_meta(request)
    await aaudit("auth.password_changed", "auth", user=user, ip=meta["ip"], user_agent=meta["user_agent"])
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
    await asyncio.to_thread(send_email, email, "Reset your LeadAI password",
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
        "must_change_password": False,          # they chose this password themselves
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


# ── Two-Factor Authentication (RFC 6238 TOTP) ────────────────────────────────


class TotpVerifyRequest(BaseModel):
    secret: str
    code: str


class TotpDisableRequest(BaseModel):
    code: Optional[str] = None
    password: Optional[str] = None


@router.get("/2fa/status")
async def get_2fa_status(request: Request):
    """Check whether two-factor authentication is enabled for the current user."""
    user = session_user(request)
    if not user or not user.get("user_id"):
        raise HTTPException(status_code=401, detail="Sign in required")
    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    doc = db["users"].find_one({"_id": ObjectId(str(user["user_id"]))})
    return {"totp_enabled": bool(doc and doc.get("totp_enabled"))}


@router.post("/2fa/setup")
async def setup_2fa(request: Request):
    """Generate a new RFC 6238 TOTP secret and QR code URI."""
    user = session_user(request)
    if not user or not user.get("user_id"):
        raise HTTPException(status_code=401, detail="Sign in required")
    from app.auth.totp import generate_totp_secret, get_totp_uri
    secret = generate_totp_secret()
    email = user.get("email") or "user"
    uri = get_totp_uri(secret, email=email)
    return {"secret": secret, "otpauth_url": uri}


@router.post("/2fa/enable")
async def enable_2fa(body: TotpVerifyRequest, request: Request):
    """Verify submitted code against secret and activate 2FA."""
    user = session_user(request)
    if not user or not user.get("user_id"):
        raise HTTPException(status_code=401, detail="Sign in required")
    from app.auth.totp import verify_totp_code
    if not verify_totp_code(body.secret, body.code):
        raise HTTPException(status_code=400, detail="Invalid verification code. Please check your authenticator app.")

    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    now = utcnow()
    db["users"].update_one(
        {"_id": ObjectId(str(user["user_id"]))},
        {"$set": {"totp_enabled": True, "totp_secret": body.secret, "totp_enabled_at": now, "updated_at": now}}
    )
    meta = request_meta(request)
    await aaudit("auth.2fa_enabled", "auth", user=user, ip=meta["ip"], user_agent=meta["user_agent"])
    return {"success": True, "message": "Two-factor authentication successfully enabled."}


@router.post("/2fa/disable")
async def disable_2fa(body: TotpDisableRequest, request: Request):
    """Disable 2FA (requires current password or valid TOTP code)."""
    user = session_user(request)
    if not user or not user.get("user_id"):
        raise HTTPException(status_code=401, detail="Sign in required")
    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    record = db["users"].find_one({"_id": ObjectId(str(user["user_id"]))})
    if not record:
        raise HTTPException(status_code=404, detail="Account not found")

    authenticated = False
    if body.password:
        ok, _ = _verify_and_migrate_password(body.password, record.get("password_hash") or "")
        if ok:
            authenticated = True
    if not authenticated and body.code and record.get("totp_secret"):
        from app.auth.totp import verify_totp_code
        if verify_totp_code(record["totp_secret"], body.code):
            authenticated = True

    if not authenticated:
        raise HTTPException(status_code=400, detail="Valid password or TOTP code required to disable 2FA")

    now = utcnow()
    db["users"].update_one(
        {"_id": record["_id"]},
        {"$set": {"totp_enabled": False, "updated_at": now}, "$unset": {"totp_secret": "", "totp_enabled_at": ""}}
    )
    meta = request_meta(request)
    await aaudit("auth.2fa_disabled", "auth", user=user, ip=meta["ip"], user_agent=meta["user_agent"])
    return {"success": True, "message": "Two-factor authentication disabled."}


# ── Email Verification ───────────────────────────────────────────────────────


class VerifyEmailRequest(BaseModel):
    token: str


class ResendVerificationRequest(BaseModel):
    email: Optional[str] = None


_email_verify_limiter = RateLimiter("verify_email_ip", 5, 3600)


@router.post("/verify-email")
async def verify_email(body: VerifyEmailRequest, request: Request):
    """Verify an email address using a single-use token."""
    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    now = utcnow()
    token_hash = _hash_token(body.token.strip())
    doc = db["email_verifications"].find_one_and_update(
        {"token_hash": token_hash, "used_at": None, "expires_at": {"$gt": now}},
        {"$set": {"used_at": now}}
    )
    if not doc:
        raise HTTPException(status_code=400, detail="This verification link is invalid or has expired.")

    uid = ObjectId(str(doc["user_id"])) if doc.get("user_id") else None
    email = doc.get("email")

    if uid:
        db["users"].update_one({"_id": uid}, {"$set": {"email_verified": True, "email_verified_at": now, "updated_at": now}})
    elif email:
        db["users"].update_many({"email": email.lower()}, {"$set": {"email_verified": True, "email_verified_at": now, "updated_at": now}})

    await aaudit("auth.email_verified", "auth", user=email or str(uid), ip=_get_client_ip(request), details={"user_id": str(uid)})
    return {"success": True, "message": "Email address successfully verified."}


@router.post("/verify-email/resend")
async def resend_email_verification(body: ResendVerificationRequest, request: Request):
    """Send or re-send an email verification link."""
    ip = _get_client_ip(request)
    if not _email_verify_limiter.consume(ip):
        raise HTTPException(status_code=429, detail="Too many verification requests. Please try again later.")

    user = session_user(request)
    target_email = (body.email or (user.get("email") if user else "") or "").strip().lower()
    if not target_email:
        raise HTTPException(status_code=400, detail="Email is required")

    db = get_sync_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    record = db["users"].find_one({"email": target_email})
    user_id_str = str(record["_id"]) if record else None

    now = utcnow()
    db["email_verifications"].update_many(
        {"$or": [{"email": target_email}, {"user_id": user_id_str}], "used_at": None},
        {"$set": {"used_at": now, "invalidated": True}}
    )

    token = secrets.token_urlsafe(32)
    db["email_verifications"].insert_one({
        "user_id": user_id_str,
        "email": target_email,
        "token_hash": _hash_token(token),
        "expires_at": now + timedelta(hours=24),
        "used_at": None,
        "requested_ip": ip,
        "created_at": now,
    })

    link = absolute_url(f"/verify-email?token={token}")
    await asyncio.to_thread(
        send_email,
        target_email,
        "Verify your LeadAI email address",
        f"Hi,\n\nPlease verify your email address by clicking the link below (valid for 24 hours):\n{link}\n\n"
        "If you did not request this, you can safely ignore this email.",
        kind="email_verification",
    )
    return {"success": True, "message": "Verification link sent to your email."}
