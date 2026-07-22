# FEATURE-DIFF — Captur'd app rebuild to the FIREFLY pack (VISUAL lane)

Method: diff → purge the old at source → rebuild in the pack's likeness → eyes-verify.
Surface = `service/web/` only (index.html desktop `GET /`, m.html mobile `GET /m`, manifest, sw).
Wiring (`service/app/*.py`, render_worker, mcp_service) is the partner's lane (PR #27) — untouched.

## What the current product has (read from the real source)

| Feature / element | Wire contract (must keep) | Mock coverage |
|---|---|---|
| Signed-out hero + "Sign in with RHOBEAR" | `#signedOut #signinBtn` → auth.rhobear.ai/auth/dev | 77f8v1 step 1 (onboarding voice) |
| `rhobear_session` token exchange | `/auth/central` POST | — (invisible) |
| Account chip: plan pill, email, sign out | `#acct #planPill #acctEmail #signout` | 1jbdzd header (Pro pill, Sign out) |
| STEP 1 Product URL | `#url` | 1jbdzd / m2c8m7 STEP 1 |
| STEP 2 Brief the director (textarea + 3 hint chips + wave) | `#ask #brief .hintchip[data-hint] .wave` | 1jbdzd / m2c8m7 STEP 2 |
| STEP 3 Choose a shot — 6 demo types | `#tpls .tpl[data-tpl]` (saas-walkthrough, ux-showcase, feature-spotlight, tutorial-longform, social-teaser, login-flow) | 1jbdzd / m2c8m7 STEP 3 |
| STEP 4 Voice — 6 voices (5 HD + Aria classic) | `#voices .voice[data-voice]` (vertex:Charon/Kore/Aoede/Fenrir/Zephyr:…, "") | 1jbdzd / m2c8m7 STEP 4 |
| STEP 4 Aspect — 16:9 / 9:16 / 1:1 | `#aspect button[data-aspect]` | 1jbdzd / m2c8m7 STEP 4 |
| Film it (submit) | `#filmBtn` → POST `/api/generate` `{url,template,aspect,voice,brief}` | 1jbdzd (enabled) / m2c8m7 (disabled 40%) |
| Filming progress | `#prog #progText` + `poll()` on `/api/jobs/{id}` | bice8o (Filming overlay) |
| Result video | `#result #video` → `/api/jobs/{id}/video` | — |
| Your demos gallery | `#galgrid #galempty` ← `/api/jobs` | mxo8fj (desktop rows) / ln4kbp (mobile cards) |
| Plan meter + Upgrade to Pro | `#usageLabel #usageBar #billingNote #mcpNote #upgrade` → `/billing/checkout` | — (kept in-likeness) |
| First-run onboarding overlay (4 beats) | `#ctOnb .ctob-* [data-next] #ctobDone #ctobSkip` | 77f8v1 (mobile 4-step) informs it |
| Rho companion embed | `window.RHOBEAR_COMPANION` + companion-embed.js → `#rho-launch` | teal orb bottom-right in every mock |

## NO-MOCK SURVIVORS — KEEP, build in the system's likeness
- **Plan meter + Upgrade to Pro** — no picture, but it's the Stripe Pro wall. Kept, restyled to pack glass.
- **First-run onboarding overlay** (`#ctOnb`) — the mobile onboarding mock (77f8v1) is its design source; kept and re-toned to the pack (birch-word, #4B7AC8, teal eyebrows).
- **Signed-out sign-in card** — no dedicated desktop mock; built in-likeness from the onboarding voice.
- **MCP endpoint note** — Pro-only readout, kept.

## PURGE (old at source — ONE-VERSION / NO-OVERLAY)
- `service/web/assets/capturd-premium.css` + `capturd-bridge.js` — orphaned overlay pass, verified unreferenced (grep) → **deleted**.
- Self-hosted **Nacelle** `<link>` (dead — no `/assets/nacelle/` folder ships) → removed from index.html + m.html.
- Old accent `#4a9eff` / `#2e7fdd`, old ground `#080810`/`#111120` → replaced by pack tokens (`--capturd-accent #4B7AC8`, `--capturd-bg #0A0F14`).
- System-font stacks (`-apple-system`/New York/SF Mono) → Typekit `sbv5bcv` (rokkitt / lato / droid-sans-mono) + birch-std on the word "Captur'd".

## Seams NAMED for the wiring partner (PR #27)
- **Richer `/api/jobs` fields** — rows today carry only `job_id,status,detail,created_at,has_video`. The new demo rows render `url / template / voice / aspect / duration` **only when present** (`j.url`, `j.template`, `j.voice`, `j.aspect`, `j.duration_s`). Add them server-side to light up the row tags.
- **Re-film** button (`.demo__act[data-act="refilm"]`) — prefills the studio from the job's fields client-side and scrolls up; a true re-run endpoint would let it resubmit. Wire if desired.
- **Stop filming** — the bice8o mock shows a red "Stop filming"; there is **no cancel endpoint**, so it is intentionally NOT rendered (would be a fake control). Add `/api/jobs/{id}/cancel` to enable it.

## Marks (honest note)
Docroot ships only the **constellation bear** (`/assets/capturd-bear.png`) — the pack's *secondary* mark. The brand sheet (jua0po) also defines an **ink-roar head** (nav logo) and a **standing bear** (onboarding), but those mark files are not in the tree. Per "never draw/hue-rotate a bear," the constellation file is used at every bear slot via named `<img>` seams; drop the head/standing PNGs in and the nav/hero swap with no code change.
</content>
