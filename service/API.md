# Captur'd Hosted Service — API contract (L5)

The HTTP surface the dashboard calls. `render_worker.py` already implements the render +
cost-cap core; this is the thin transport + gating that wraps it. **None of this is money-in**
— billing is a separate gate (G4), added last.

## Endpoints

### `POST /api/generate`
Start a generation. **Auth required** (session cookie from Google OAuth, G1). **Billing gate:**
Free tier → 1 lifetime generation; Pro → unlimited (checked here, enforced by the cost cap
regardless).
```json
{ "url": "https://app.example.com", "template": "saas-walkthrough",
  "aspect": "9:16", "brand": "#4f8cff", "intro": "My App", "voice": "en-US-GuyNeural" }
```
→ `202 { "job_id": "ab12cd34ef56", "status": "queued" }`
Server maps `{url, template}` → a shot list (Director brain), builds a `JobSpec` with the
caller's plan cap, enqueues `run_job`.

### `GET /api/jobs/:id`
Poll status. → `{ "job_id", "status": "queued|running|done|failed|capped", "detail" }`.
`capped` = hit the cost cap (over-budget or ran too long) — surfaced honestly, not a silent fail.

### `GET /api/jobs/:id/video`
Stream/redirect to the finished MP4 (CDN URL once storage is wired). 404 until `status==done`.

### `GET /api/me`
Current user + plan + usage (generations used / remaining). Powers the dashboard meters.

## Gating order (per request)
1. **Auth** — valid session, else 401.
2. **Plan/usage** — Free over its 1-gen limit → 402 "upgrade" (billing gate, G4).
3. **Cost cap** — `enforce_precheck` + wall-time timeout in `run_job` (always on, even for Pro).
4. **Render** — `run_job` → film.py rig → finish.py → store → return.

## What exists vs pending
- ✅ **Render + cost cap** — `render_worker.py`, tested.
- ⏳ **Transport** — FastAPI app wrapping the above (small).
- ⏳ **Auth** — Google OAuth (needs G1 client).
- ⏳ **Billing gate** — Stripe check at step 2 (needs G4 — LAST).
- ⏳ **Storage/CDN** — push finished MP4s, return signed URLs.
- ⏳ **Auto shot-list** — Director brain maps `{url, template}` → shot (plugs into `POST /generate`).
