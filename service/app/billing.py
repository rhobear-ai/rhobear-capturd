"""Billing — the buy button flow, built to the credential boundary.

Two checkout paths:
  * CANON (Lane K) — create a Stripe Checkout Session server-side so the founder
    coupon (``rhobear_capturd_founders_25``) auto-applies on the first invoice.
    Needs ``STRIPE_SECRET_KEY`` + ``CAPTURD_PRICE_ID``. This is what ships.
  * LEGACY — redirect to a static ``PRO_CHECKOUT_URL`` (Payment Link). Kept as a
    fallback so the service still works if only the static URL is configured.

Webhook handles the canon event set:
  * ``checkout.session.completed``        → activate Pro
  * ``customer.subscription.updated``     → sync plan from subscription.status
  * ``customer.subscription.deleted``     → drop to free

No Stripe SDK — ``httpx`` (already a service dep) talks to the REST API directly.
Keys are read from env (or the agent vault) via ``config``; none are hardcoded.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from . import config, store
from .auth import require_user

router = APIRouter(prefix="/billing", tags=["billing"])

_STRIPE_API = "https://api.stripe.com/v1"
# subscription.status values that grant Pro access (covers trial + active).
_PRO_STATUSES = {"active", "trialing"}


@router.get("/checkout")
async def checkout(request: Request):
    user = require_user(request)
    if user["plan"] == "pro":
        return RedirectResponse("/?already=pro", status_code=303)

    if config._BILLING_SESSION_READY:
        # CANON path: server-created Checkout Session with the founder coupon.
        session = await _create_checkout_session(
            user_id=user["id"], email=user["email"]
        )
        url = (session or {}).get("url")
        if url:
            return RedirectResponse(url, status_code=303)
        # Stripe returned no URL (rare) — fall through to legacy if available.

    if config.PRO_CHECKOUT_URL:
        sep = "&" if "?" in config.PRO_CHECKOUT_URL else "?"
        url = (config.PRO_CHECKOUT_URL + sep
               + urllib.parse.urlencode({"client_reference_id": user["id"],
                                         "prefilled_email": user["email"]}))
        return RedirectResponse(url, status_code=303)

    # honest — do NOT fake a broken checkout
    raise HTTPException(
        status_code=503,
        detail="Checkout isn't live yet — owner payment credentials pending.",
    )


async def _create_checkout_session(*, user_id: str, email: str) -> dict | None:
    """Create a Stripe Checkout Session for the Captur'd Pro price with the
    founder coupon baked in. Returns the parsed session dict or None on failure."""
    headers = {"Authorization": f"Bearer {config.STRIPE_SECRET_KEY}"}
    base = config.BASE_URL.rstrip("/")
    data = {
        "mode": "subscription",
        "line_items[0][price]": config.CAPTURD_PRICE_ID,
        "line_items[0][quantity]": "1",
        "discounts[0][coupon]": config.CAPTURD_COUPON_ID,
        "client_reference_id": user_id,
        "customer_email": email,
        "success_url": f"{base}/?billing=success",
        "cancel_url": f"{base}/?billing=canceled",
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(
                f"{_STRIPE_API}/checkout/sessions",
                headers=headers,
                data=data,
            )
        if r.status_code >= 300:
            # Log the body for ops; surface a clean 503 to the user.
            _last_error = f"stripe checkout create failed {r.status_code}: {r.text[:300]}"
            return None
        return r.json()
    except (httpx.HTTPError, ValueError):
        return None


def _verify_stripe(raw: bytes, sig_header: str, secret: str) -> bool:
    # Stripe scheme: header "t=<ts>,v1=<hmac>"; signed_payload = "<ts>.<raw>"
    try:
        parts = dict(p.split("=", 1) for p in sig_header.split(","))
        ts, v1 = parts["t"], parts["v1"]
    except Exception:
        return False
    if abs(time.time() - int(ts)) > 60 * 10:
        return False
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + raw,
                   hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, v1)


async def _customer_email(customer_id: str) -> str | None:
    """Resolve a Stripe customer id to an email (for subscription events, which
    carry customer but not client_reference_id)."""
    if not customer_id or not config.STRIPE_SECRET_KEY:
        return None
    headers = {"Authorization": f"Bearer {config.STRIPE_SECRET_KEY}"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"{_STRIPE_API}/customers/{customer_id}",
                                 headers=headers)
        if r.status_code < 300:
            return (r.json() or {}).get("email")
    except (httpx.HTTPError, ValueError):
        pass
    return None


def _resolve_user(*, obj: dict) -> dict | None:
    """Find the local user for a Stripe event object."""
    uid = obj.get("client_reference_id")
    email = (obj.get("customer_email")
             or (obj.get("customer_details") or {}).get("email"))
    user = store.get_user(uid) if uid else None
    if not user and email:
        user = store.get_user_by_email(email)
    return user


@router.post("/webhook")
async def webhook(request: Request):
    raw = await request.body()
    secret = config.BILLING_WEBHOOK_SECRET
    if not secret:
        raise HTTPException(status_code=503, detail="webhook secret not configured")
    sig = request.headers.get("stripe-signature", "")
    if not _verify_stripe(raw, sig, secret):
        raise HTTPException(status_code=400, detail="bad signature")

    event = json.loads(raw or b"{}")
    etype = event.get("type")
    obj = (event.get("data") or {}).get("object") or {}

    if etype == "checkout.session.completed":
        user = _resolve_user(obj=obj)
        if not user:
            return JSONResponse({"ok": True, "note": "no matching user"},
                                status_code=202)
        store.set_plan(user["id"], "pro")
        return JSONResponse({"ok": True, "upgraded": user["email"]})

    if etype == "customer.subscription.updated":
        # subscription objects carry customer (id), not client_reference_id.
        sub_status = obj.get("status")
        customer_id = obj.get("customer")
        email = await _customer_email(customer_id) if customer_id else None
        user = store.get_user_by_email(email) if email else None
        if not user:
            return JSONResponse({"ok": True, "note": "no matching user"},
                                status_code=202)
        plan = "pro" if sub_status in _PRO_STATUSES else "free"
        store.set_plan(user["id"], plan)
        return JSONResponse({"ok": True, "synced": plan,
                             "status": sub_status, "email": user["email"]})

    if etype == "customer.subscription.deleted":
        customer_id = obj.get("customer")
        email = await _customer_email(customer_id) if customer_id else None
        user = store.get_user_by_email(email) if email else None
        if not user:
            return JSONResponse({"ok": True, "note": "no matching user"},
                                status_code=202)
        store.set_plan(user["id"], "free")
        return JSONResponse({"ok": True, "deactivated": user["email"]})

    return JSONResponse({"ok": True, "ignored": etype})
