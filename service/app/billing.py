"""Billing — the buy button flow, built to the credential boundary.

Everything is coded. The ONLY things missing to take money (owner's credentials):
  * PRO_CHECKOUT_URL      — a Stripe Payment Link (or PayPal subscribe URL). Clicking
                            "Upgrade" redirects straight here → instant checkout.
  * BILLING_WEBHOOK_SECRET — verifies the "payment succeeded" callback so we flip the
                            user to Pro automatically.

With a Stripe Payment Link this is genuinely two values. We append the user's id as
`client_reference_id` so the webhook knows who paid. No Stripe SDK needed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from . import config, store
from .auth import require_user

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/checkout")
async def checkout(request: Request):
    user = require_user(request)
    if user["plan"] == "pro":
        return RedirectResponse("/?already=pro", status_code=303)
    if not config.status()["billing_configured"]:
        # honest — do NOT fake a broken checkout
        raise HTTPException(status_code=503,
                            detail="Checkout isn't live yet — owner payment credentials pending.")
    sep = "&" if "?" in config.PRO_CHECKOUT_URL else "?"
    url = (config.PRO_CHECKOUT_URL + sep
           + urllib.parse.urlencode({"client_reference_id": user["id"],
                                     "prefilled_email": user["email"]}))
    return RedirectResponse(url, status_code=303)


def _verify_stripe(raw: bytes, sig_header: str, secret: str) -> bool:
    # Stripe scheme: header "t=<ts>,v1=<hmac>"; signed_payload = "<ts>.<raw>"
    try:
        parts = dict(p.split("=", 1) for p in sig_header.split(","))
        ts, v1 = parts["t"], parts["v1"]
    except Exception:
        return False
    if abs(time.time() - int(ts)) > 60 * 5:
        return False
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + raw,
                   hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, v1)


@router.post("/webhook")
async def webhook(request: Request):
    raw = await request.body(max_size=1024 * 100)  # 100 KB max
    secret = config.BILLING_WEBHOOK_SECRET
    if not secret:
        raise HTTPException(status_code=503, detail="webhook secret not configured")
    sig = request.headers.get("stripe-signature", "")
    if not _verify_stripe(raw, sig, secret):
        raise HTTPException(status_code=400, detail="bad signature")

    event = json.loads(raw or b"{}")
    if event.get("type") not in ("checkout.session.completed",
                                 "customer.subscription.created",
                                 "invoice.paid"):
        return JSONResponse({"ok": True, "ignored": event.get("type")})

    obj = (event.get("data") or {}).get("object") or {}
    uid = obj.get("client_reference_id")
    email = obj.get("customer_email") or (obj.get("customer_details") or {}).get("email")
    user = None
    if uid:
        user = store.get_user(uid)
    if not user and email:
        user = store.get_user_by_email(email)
    if not user:
        return JSONResponse({"ok": True, "note": "no matching user"}, status_code=202)

    store.set_plan(user["id"], "pro")
    return JSONResponse({"ok": True, "upgraded": user["email"]})
