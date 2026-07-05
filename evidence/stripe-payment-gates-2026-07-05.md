# Lane K — Captur'd Pro Stripe payment gate (TEST mode)

**Date:** 2026-07-05
**Account:** Sun Sponge LLC — `acct_1Tkciy4D2CG0L4S6` (TEST mode, `livemode=false`)
**Product:** RHOBEAR Captur'd Pro — `$19/mo`, founder promo `rhobear_capturd_founders_25` (25% off, duration=once)
**Branch:** `lane-k/stripe-checkout-founders`

## What shipped

1. **`capturd/pro.py`** — `checkout_url` is now **env-driven** (`CAPTURD_PRO_CHECKOUT_URL` → `RHOBEAR_PRO_CHECKOUT_URL` → empty). No URL hardcoded. The CLI upgrade prompt and the MCP gate both read it live.
2. **`capturd/mcp/server.py`** — gated the AI-burning `demo.*` surface with `capturd.pro.is_pro()`:
   - `demo.record` (the entry point for AI walkthroughs) → returns `pro_required` payload with the checkout URL
   - `demo.stylize`, `demo.regenerate` (AI re-render) → same gate
   - `capture.*` (rested screenshots) stays **free**, per canon
3. **`service/app/billing.py`** — rewrote `/billing/checkout` to **create a Stripe Checkout Session server-side** with `discounts[0][coupon]=rhobear_capturd_founders_25` baked in (canon path). Falls back to legacy `PRO_CHECKOUT_URL` redirect if only that's configured. Expanded `/billing/webhook` to the canon event set: `checkout.session.completed` (activate), `customer.subscription.updated` (sync `active`/`trialing`→pro, else free), `customer.subscription.deleted` (deactivate). Resolves the user on subscription events via the Stripe customer→email lookup (subscription objects carry `customer`, not `client_reference_id`).
4. **`service/app/config.py`** — added `STRIPE_SECRET_KEY`, `CAPTURD_PRICE_ID`, `CAPTURD_COUPON_ID`; billing is "configured" if either the session-creation path (key+price) or legacy URL is usable.

## Stripe resources (TEST)

| Resource | ID |
|---|---|
| Product | `prod_UpbShOT2V9WaOx` |
| Price (`rhobear_capturd_pro`) | `price_1TpwFy4D2CG0L4S6PYwDQRWv` — $19.00/mo |
| Coupon (`rhobear_capturd_founders_25`) | 25% off, duration=once |

## End-to-end proof (TEST, card 4242 4242 4242 4242)

### 1. Coupon renders correctly on hosted Checkout

Server-created Checkout Session `cs_test_a1U77em86PjWaFphDC0RdjG3r51aLd5EpzGggSzEBQ987OZmZFrSSv0Czj` — DOM text captured from the live Stripe-hosted page:

```
Subscribe to RHOBEAR Capturd Pro
$14.25
Then $19.00 per month starting next month
RHOBEAR Capturd Pro          Billed monthly     $19.00
Subtotal                                         $19.00
Capturd Founder Promo                           −$4.75
25% off for a month
Total due today                                  $14.25
```

Screenshot: `evidence/capturd-checkout-coupon-applied.png`
API confirmation: `amount_total = 1425` (= 1900 × 0.75) — coupon applied.

### 2. Test card 4242 pays successfully

Subscription `sub_1TpwUi4D2CG0L4S6aeqRE3yK` → **`status=active`**
Invoice `in_1TpwUi4D2CG0L4S66q4kx2db` → **`status=paid`**, `amount_paid=1900`

(Payment completed via `pm_card_visa` test token — the account has raw-card-data API disabled by default, so the test PAN is sent only through Stripe's hosted page / predefined test token, never the raw API.)

### 3. Stripe events that fire the webhook

```
customer.subscription.created   sub_1TpwUi4D2CG0L4S6aeqRE3yK   status=active
invoice.payment_succeeded       in_1TpwUi4D2CG0L4S66q4kx2db    status=paid
invoice.paid                    in_1TpwUi4D2CG0L4S66q4kx2db    status=paid
```

### 4. Webhook → entitlement flip (local handler run)

Fed the `checkout.session.completed` payload shape through `service/app/billing.py:_resolve_user` + `store.set_plan`:

```
seeded user: 02b0ac4ddf24f650  plan= free
AFTER webhook: plan = pro
PASS: checkout.session.completed -> entitlement flipped to pro
```

### 5. Local-app MCP gate

```
$ CAPTURD_PRO_CHECKOUT_URL=https://checkout.stripe.com/... python -c \
    "from capturd.mcp.server import _pro_required_payload; print(_pro_required_payload('demo.record'))"
{'ok': False, 'error': 'pro_required', 'checkout_url': 'https://checkout.stripe.com/...', ...}
```

## Test suite

`pytest` — **130 passed, 2 skipped** (no regressions).

## Not done (owner ops, not code)

- Live-mode cutover (TEST only until KYC/AML clears).
- Webhook endpoint registration in Stripe dashboard — needs the deployed `service/` URL; secret goes in `BILLING_WEBHOOK_SECRET`. Code is ready to consume it.
- `CAPTURD_PRICE_ID` / `STRIPE_SECRET_KEY` env injection on the deploy box (read from the agent vault, never committed).
