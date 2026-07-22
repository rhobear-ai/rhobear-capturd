"""RHOBEAR Captur'd — Pro entitlement for the LOCAL app.

Product model (owner canon):
  * FREE  = ``capturd shots`` — rested-state screenshots of your finished/mapped site
            in all its states. Always free, local, offline, no account, no AI.
  * PRO   = ``capturd walk`` — agent-made AI walkthroughs (the Supademo-killer: semantic
            zoom, live cursor, voice-sync). The AI is RHOBEAR's own, piped through our
            gateway (``gw.rhobear.ai``) — there is NO bring-your-own-key. You pay us; we
            supply the AI. Same paid feature ships in the hosted web service.

Unlock is a launch CODE or a signed LICENSE, stored in ``~/.capturd/license`` (or the
``RHOBEAR_CAPTURD_LICENSE`` env var). A paid unlock is what authorizes the gateway AI.

─────────────────────────────────────────────────────────────────────────────
OWNER — the only things to fill before this sells (nothing here moves money or
unlocks the AI until you do):
  * PRO_CONFIG['checkout_url'] : your Stripe/PayPal buy link (shown on the upgrade prompt).
  * PRO_CONFIG['codes']        : launch codes you email on purchase.
  * PRO_CONFIG['pubkey_b64']   : Ed25519 public key (raw 32B, base64) to verify signed licenses.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path

# Checkout URL is env-driven so no secret/URL is hardcoded in the repo.
# Precedence: CAPTURD_PRO_CHECKOUT_URL > RHOBEAR_PRO_CHECKOUT_URL > "".
# At launch this is a Stripe Payment Link (durable) or a server-created
# Checkout Session URL (test mode). The CLI upgrade prompt + MCP demo.*
# gate both surface this URL.
def _resolve_checkout_url() -> str:
    return ((os.environ.get("CAPTURD_PRO_CHECKOUT_URL") or "").strip()
            or (os.environ.get("RHOBEAR_PRO_CHECKOUT_URL") or "").strip())


PRO_CONFIG = {
    "product_name": "RHOBEAR Captur'd Pro",
    "price_label": "$19 / mo",
    "codes": [],          # <-- OWNER: ["CAPTURD-LAUNCH-2026", ...]
    "pubkey_b64": "",     # <-- OWNER: Ed25519 public key (raw 32 bytes, base64) for signed licenses
}


def checkout_url() -> str:
    """Stripe Checkout URL (env-driven). Empty string = not configured yet."""
    return _resolve_checkout_url()


_LICENSE_ENV = "RHOBEAR_CAPTURD_LICENSE"
_LICENSE_FILE = Path.home() / ".capturd" / "license"


def _stored_license() -> str:
    v = (os.environ.get(_LICENSE_ENV) or "").strip()
    if v:
        return v
    try:
        return _LICENSE_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _code_ok(v: str) -> bool:
    codes = [c.upper() for c in (PRO_CONFIG.get("codes") or [])]
    return bool(codes) and v.upper() in codes


def _b64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _signed_license_ok(v: str) -> bool:
    """Ed25519-signed ``<payload_b64>.<sig_b64>``; payload may carry an ``exp`` epoch."""
    pub = PRO_CONFIG.get("pubkey_b64") or ""
    if not pub or "." not in v:
        return False
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        payload_b64, sig_b64 = v.split(".", 1)
        key = Ed25519PublicKey.from_public_bytes(_b64(pub))
        key.verify(_b64(sig_b64), _b64(payload_b64))
        payload = json.loads(_b64(payload_b64).decode("utf-8"))
        if payload.get("exp") and time.time() > float(payload["exp"]):
            return False
        return True
    except Exception:
        return False


def is_pro() -> bool:
    v = _stored_license()
    return bool(v) and (_code_ok(v) or _signed_license_ok(v))


def activate(value: str) -> bool:
    """Persist a valid code/license so future ``walk`` runs are unlocked. Returns validity."""
    v = (value or "").strip()
    if not (_code_ok(v) or _signed_license_ok(v)):
        return False
    try:
        _LICENSE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LICENSE_FILE.write_text(v, encoding="utf-8")
    except OSError:
        pass
    return True


def require_pro(feature: str = "AI walkthroughs") -> bool:
    """True if unlocked; else print the upgrade prompt to stderr and return False."""
    if is_pro():
        return True
    url = checkout_url() or "(checkout link coming soon)"
    sys.stderr.write(
        f"\n  {feature} are a {PRO_CONFIG['product_name']} feature ({PRO_CONFIG['price_label']}).\n"
        f"  Screenshots are free:            capturd shots ...\n"
        f"  Unlock the AI walkthroughs:      {url}\n"
        f"  Already bought? Activate a code: capturd activate <CODE>\n\n"
    )
    return False
