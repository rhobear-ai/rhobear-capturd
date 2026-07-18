"""Auth — identity comes from central auth (auth.rhobear.ai); this holds the local session.

Captur'd no longer carries its own login. A caller exchanges a `rhobear_session`
from central auth at POST /auth/central for an httponly Captur'd session cookie,
and the plan is taken from central auth's `entitled` decision rather than
re-derived here. Staff and agents sign in at https://auth.rhobear.ai/auth/dev.
"""
from __future__ import annotations

from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

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


# NOTE: the local /auth/dev-login and /auth/google/{login,callback} endpoints were
# removed on 2026-07-18. Identity now comes from central auth (auth.rhobear.ai)
# via /auth/central below — one login for every RHOBEAR product. Staff/agents use
# the central dev sign-in at https://auth.rhobear.ai/auth/dev.

# ---- central auth exchange (identity from auth.rhobear.ai) -----------------

@router.post("/central")
async def central_login(request: Request):
    """Exchange a rhobear_session token from central auth for a local session."""
    body = await request.json()
    token = (body.get("rhobear_session") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="rhobear_session required")

    # Validate the token against central auth
    try:
        async with httpx.AsyncClient(timeout=10) as cx:
            me_resp = await cx.get(
                f"{config.RHOBEAR_AUTH_BASE}/auth/me",
                headers={"Authorization": f"Bearer {token}"}
            )
            if me_resp.status_code != 200:
                raise HTTPException(status_code=401, detail="invalid session")
            me = me_resp.json()
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="could not reach central auth")

    email = (me.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="no email in central auth response")

    # Create/update local user from central auth identity
    user = store.upsert_user(email)

    # Plan comes from central auth's entitlement decision — never re-derived here.
    # `entitled` is already true for dev accounts (central auth sets it for plan='dev'),
    # for any paid plan, and for a live trial, so there is nothing to OR in. An extra
    # `or me.get("is_dev")` was redundant and read like a backdoor; dropped.
    # `me` comes from a server-to-server /auth/me call, so it is not user-controllable.
    entitled = bool(me.get("entitled", False))
    current_plan = "pro" if entitled else "free"
    if user["plan"] != current_plan:
        store.set_plan(user["id"], current_plan)
        user["plan"] = current_plan

    # Set local session cookie so subsequent requests use httponly auth
    local_token = store.new_session(user["id"])
    secure = config.BASE_URL.startswith("https")
    resp = JSONResponse({
        "signed_in": True,
        "email": user["email"],
        "plan": user["plan"],
        "entitled": entitled,
    })
    resp.set_cookie(COOKIE, local_token, httponly=True, samesite="lax",
                    secure=secure, max_age=60 * 60 * 24 * 30)
    return resp


@router.post("/logout")
async def logout(request: Request):
    store.drop_session(request.cookies.get(COOKIE, ""))
    resp = Response(status_code=204)
    resp.delete_cookie(COOKIE)
    return resp
