"""Auth — session cookies, Google OAuth (credential-ready), dev login.

Dev login lets the whole product work end-to-end NOW; the moment the owner drops
a Google client id/secret, real OAuth takes over with zero code change.
"""
from __future__ import annotations

import urllib.parse
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from . import config, store

router = APIRouter(prefix="/auth", tags=["auth"])
COOKIE = "capturd_session"


def current_user(request: Request) -> Optional[dict]:
    return store.user_for_session(request.cookies.get(COOKIE, ""))


def require_user(request: Request) -> dict:
    u = current_user(request)
    if not u:
        raise HTTPException(status_code=401, detail="sign in required")
    return u


def _set_session(resp: Response, uid: str) -> None:
    token = store.new_session(uid)
    secure = config.BASE_URL.startswith("https")
    resp.set_cookie(COOKIE, token, httponly=True, samesite="lax",
                    secure=secure, max_age=60 * 60 * 24 * 30)


# ---- dev login (works until real OAuth is wired) ----------------------------

@router.post("/dev-login")
async def dev_login(request: Request):
    if not config.DEV_LOGIN:
        raise HTTPException(status_code=403, detail="dev login disabled")
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    if "@" not in email:
        raise HTTPException(status_code=400, detail="valid email required")
    user = store.upsert_user(email)
    resp = Response(status_code=204)
    _set_session(resp, user["id"])
    return resp


# ---- Google OAuth (inert until GOOGLE_CLIENT_ID/SECRET are set) --------------

@router.get("/google/login")
async def google_login():
    if not config.status()["oauth_configured"]:
        raise HTTPException(status_code=503, detail="google sign-in not configured yet")
    params = {
        "client_id": config.GOOGLE_CLIENT_ID,
        "redirect_uri": f"{config.BASE_URL}/auth/google/callback",
        "response_type": "code",
        "scope": "openid email",
        "access_type": "online",
        "prompt": "select_account",
    }
    return RedirectResponse(
        "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params))


@router.get("/google/callback")
async def google_callback(code: str = ""):
    if not code:
        raise HTTPException(status_code=400, detail="missing code")
    async with httpx.AsyncClient(timeout=15) as cx:
        tok = await cx.post("https://oauth2.googleapis.com/token", data={
            "code": code, "client_id": config.GOOGLE_CLIENT_ID,
            "client_secret": config.GOOGLE_CLIENT_SECRET,
            "redirect_uri": f"{config.BASE_URL}/auth/google/callback",
            "grant_type": "authorization_code",
        })
        tok.raise_for_status()
        access = tok.json()["access_token"]
        info = await cx.get("https://www.googleapis.com/oauth2/v3/userinfo",
                            headers={"Authorization": f"Bearer {access}"})
        info.raise_for_status()
        email = info.json().get("email", "")
    if "@" not in email:
        raise HTTPException(status_code=400, detail="no email from google")
    user = store.upsert_user(email)
    resp = RedirectResponse("/", status_code=303)
    _set_session(resp, user["id"])
    return resp


@router.post("/logout")
async def logout(request: Request):
    store.drop_session(request.cookies.get(COOKIE, ""))
    resp = Response(status_code=204)
    resp.delete_cookie(COOKIE)
    return resp
