# Captur'd hosted service — deploy + go-live

The service is **built, running, and proven end-to-end** (auth → generate → render → deliver →
plan gate → billing → Pro unlock, all verified locally including the money-in path with stand-in
credentials). This is the turn-key runbook to put it live.

## Run it locally (works right now)
```
cd D:\capturd-service
python -m uvicorn app.main:app --host 127.0.0.1 --port 8099
# open http://127.0.0.1:8099  → sign in with any email (dev login) → film a demo
```

## Go-live checklist — the ONLY things left are owner-gated

| Step | Who | What |
|---|---|---|
| **Payment link** | **Owner** | Create a Stripe Payment Link (or PayPal subscribe URL) for Pro $19/mo → set `PRO_CHECKOUT_URL`. This is the buy button. |
| **Webhook secret** | **Owner** | Stripe → webhook to `https://<domain>/billing/webhook` for `checkout.session.completed` → set `BILLING_WEBHOOK_SECRET`. Flips payers to Pro automatically. |
| **Google OAuth** | **Owner** | OAuth client (redirect `https://<domain>/auth/google/callback`) → `GOOGLE_CLIENT_ID/SECRET`. (Dev login covers everything until then.) |
| **Domain (G2)** | **Owner** | Point `capturd.rhobear.ai` (or chosen host) at the deploy box; set `CAPTURD_BASE_URL`. |
| **Deploy target** | **Owner call** | Dedicated box **recommended** — render jobs peg CPU for minutes and would compete with the other live products on rhobear-vps. Don't co-locate on the shared prod box without accepting that risk. |

Everything else is done. Drop the four env values, point the domain, and it takes money.

## Deploy (dedicated box or container)

**Docker (simplest):**
```
# place the sunsponge-capture repo at ./engine, then:
docker build -t capturd-service .
docker run -d --env-file .env -p 8099:8099 -v capturd-data:/data capturd-service
```

**systemd + Caddy (VPS):**
1. `/opt/capturd-service` = this dir; `/opt/sunsponge-capture` = the engine repo.
2. `python -m venv .venv && .venv/bin/pip install -r requirements.txt` and
   `pip install "/opt/sunsponge-capture[voice]" && python -m playwright install --with-deps chromium`; ensure `ffmpeg` on PATH.
3. Copy `.env.example` → `.env`, fill the owner values.
4. `cp deploy/capturd-service.service /etc/systemd/system/ && systemctl enable --now capturd-service`.
5. Append `deploy/Caddyfile.snippet` to the Caddyfile, `systemctl reload caddy`.

## Verify live (like a user, not curl)
Open the domain → sign in → film a demo → watch it render → confirm the video plays → hit the
free cap → click Upgrade → confirm it lands on the real checkout → pay (test mode) → confirm
auto-upgrade to Pro. Only call it shipped when you've watched that happen.
