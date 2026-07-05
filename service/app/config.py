"""Config + the credential boundary.

Everything the service needs is here. The OWNER-GATED credentials are read from
env (or the agent vault) and each has an honest "configured?" flag so the app
degrades gracefully and tells the truth instead of faking a broken flow.

The ONLY hard stops for going live (owner's words: "you can't install something
if my PayPal credentials aren't in there"):
  * PRO_CHECKOUT_URL      — the buy button target (Stripe Payment Link / PayPal subscribe URL)
  * BILLING_WEBHOOK_SECRET — verifies the "payment succeeded" callback that flips a user to Pro
  * GOOGLE_CLIENT_ID/SECRET — real Google sign-in (dev-login covers everything until then)

Drop them in the environment (or D:\rhobear-agent-vault\ files) and the flow is live.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

VAULT = Path(r"D:\rhobear-agent-vault")


def _env(name: str, default: str = "") -> str:
    v = os.environ.get(name, "").strip()
    if v:
        return v
    # optional vault fallback: a file named after the var (lowercased) holds the value
    f = VAULT / f"{name.lower()}.txt"
    if f.is_file():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line
    return default


# ---- service basics ----------------------------------------------------------
BASE_URL = _env("CAPTURD_BASE_URL", "http://127.0.0.1:8099")
DATA_DIR = Path(_env("CAPTURD_DATA_DIR", r"D:\capturd-service\data"))
JOBS_DIR = Path(_env("CAPTURD_JOBS_DIR", r"D:\capturd-service\data\jobs"))
DB_PATH = DATA_DIR / "capturd.sqlite3"
SESSION_SECRET = _env("CAPTURD_SESSION_SECRET") or secrets.token_hex(32)

# ---- plan limits (canon: Free = 1 generation + 20 shots) ---------------------
FREE_GENERATION_LIMIT = int(_env("CAPTURD_FREE_GEN_LIMIT", "1"))
FREE_SHOT_LIMIT = int(_env("CAPTURD_FREE_SHOT_LIMIT", "20"))
PRO_PRICE = _env("CAPTURD_PRO_PRICE", "$19")

# ---- OWNER-GATED credentials (honest flags) ----------------------------------
GOOGLE_CLIENT_ID = _env("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = _env("GOOGLE_CLIENT_SECRET")

# Billing — two paths. The CANON path (Lane K) creates a Stripe Checkout Session
# server-side so the founder coupon auto-applies. The legacy path redirects to a
# static PRO_CHECKOUT_URL (Payment Link). Session-creation wins when configured.
STRIPE_SECRET_KEY = _env("STRIPE_SECRET_KEY")             # test: sk_test_...  (NEVER commit)
CAPTURD_PRICE_ID = _env("CAPTURD_PRICE_ID")               # price for lookup_key rhobear_capturd_pro
CAPTURD_COUPON_ID = _env("CAPTURD_COUPON_ID", "rhobear_capturd_founders_25")
PRO_CHECKOUT_URL = _env("PRO_CHECKOUT_URL")               # legacy: static buy link
BILLING_WEBHOOK_SECRET = _env("BILLING_WEBHOOK_SECRET")

GW_API_KEY = _env("RHOBEAR_GW_API_KEY")               # optional: agent self-drive
DEV_LOGIN = _env("CAPTURD_DEV_LOGIN", "1") == "1"     # allow dev login until OAuth is wired

# Billing is "configured" if EITHER checkout path is usable. The session-creation
# path (canon) needs the secret key + price id; legacy needs a static URL.
_BILLING_SESSION_READY = bool(STRIPE_SECRET_KEY and CAPTURD_PRICE_ID)
_BILLING_LEGACY_READY = bool(PRO_CHECKOUT_URL)

# Enterprise self-host: this whole service IS the enterprise edition when run on
# the customer's own box. No separate build — same code, self-hosted.
EDITION = _env("CAPTURD_EDITION", "hosted")           # hosted | enterprise


def status() -> dict:
    """Honest readiness — what's wired vs what waits on the owner's credentials."""
    return {
        "oauth_configured": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),
        "billing_configured": bool(_BILLING_SESSION_READY or _BILLING_LEGACY_READY),
        "billing_session_creation": _BILLING_SESSION_READY,
        "webhook_configured": bool(BILLING_WEBHOOK_SECRET),
        "gateway_configured": bool(GW_API_KEY),
        "dev_login": DEV_LOGIN,
        "edition": EDITION,
    }


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
