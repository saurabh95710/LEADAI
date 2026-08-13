"""
Admin sign-in routes.

  POST /api/auth/login   email + password → session cookie
  GET  /api/auth/me      current signed-in user (401 when signed out)
  POST /api/auth/logout  clear the session cookie
"""
import logging

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from app.auth.service import (
    clear_session_cookie,
    login_allowed,
    login_denied_seconds,
    public_user,
    record_login_failure,
    reset_login_attempts,
    session_user,
    set_session_cookie,
    verify_admin_login,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response):
    ip = request.client.host if request.client else "unknown"
    if not login_allowed(ip):
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed attempts — try again in {login_denied_seconds(ip)}s",
        )
    user = verify_admin_login(body.email, body.password)
    if user is None:
        record_login_failure(ip)
        logger.warning(f"Failed login attempt for {body.email!r} from {ip}")
        raise HTTPException(status_code=401, detail="Invalid email or password")
    reset_login_attempts(ip)
    set_session_cookie(response, user)
    logger.info(f"Signed in: {user['email']} from {ip}")
    return {"success": True, "user": public_user(user)}


@router.get("/me")
async def me(request: Request):
    user = session_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    return {"success": True, "user": public_user(user)}


@router.post("/logout")
async def logout(response: Response):
    clear_session_cookie(response)
    return {"success": True}