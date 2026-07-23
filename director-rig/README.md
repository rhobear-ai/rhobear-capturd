# Director rig

The Director-mode film crew: turns a shot-list JSON into a rendered demo video
via the Captur'd MCP engine (`CAPTURD_REPO`, default `/opt/sunsponge-capture`).

- `scout.py` — plans a shot list for a URL (AI-assisted, falls back to a
  generic scroll tour).
- `film.py` — executes a shot list: record → enrich → stylize → zoom/hold/
  spotlight/overlay → export. See the [film.py crash fix](#2026-07-22-fix-keyerror-in-filmpys-adjusted)
  below and `tests/test_adjusted.py`.
- `paid_boot.py` — runtime patches on top of the frozen engine (typing
  playback, voice synthesis, template paths) applied before a shot runs.
  **Not new capability introduced by this PR** — it predates version control
  here and has been running live on every render job since before this PR
  (film.py boots it via `paid_boot.py` on every call). No unit tests exist
  for its individual patches, but it has been exercised end-to-end by both
  the `rhobear.ai` verification walkthroughs run against the live service
  for the `film.py` crash fix (job success confirmed with real MP4 output
  both times, before and after this homing PR).
- `finish.py` — paid-lane post-export finisher: aspect reframe, watermark,
  intro/outro title cards, music bed. Pure ffmpeg on the exported MP4, no
  engine changes.
- `revoice.py` — swaps the voiceover on an already-filmed demo without a
  refilm.
- `verify_frames.py` — pulls inspection frames from a batch of showcase
  exports for a manual verify pass.

**Live location:** the service (`service/app/main.py`, `service/render_worker.py`)
shells these by path via `CAPTURD_RIG_DIR` (default `/opt/capturd-rig` on
rhobear-vps). On the box, `/opt/capturd-rig` is a symlink into this directory
so there is exactly one copy under version control — edit here, not there.
This directory was previously ungoverned (never in any git repo); homed here
2026-07-22.

All Windows-only hardcoded paths (`C:\Users\...`, `D:\...`) that existed in
the pre-homed version have been removed in favor of `shutil.which()` /
`CAPTURD_REPO` / fontconfig resolution, since these scripts run on the Linux
box.

## 2026-07-22 fix: KeyError in film.py's adjusted()

`adjusted(act_i)` mapped a shot-list act index to a live engine step index via
`kept.index(act_to_engine[act_i])`. If an act's `demo.act` call never
completed (selector timeout under `skip_unresolved`) or its engine step got
trimmed as a stray SPA interaction, that act index has no live engine step —
and the old code raised `KeyError`/`ValueError`, crashing the whole render.
Content-dependent: reproduced on a rhobear.ai walkthrough (job `08a1b1ad8396`),
not on lab.html walkthroughs whose selectors always resolved.

Fixed: the mapping logic is now a top-level, testable `_adjusted()` that
returns `None` and logs a warning instead of raising. Every call site
(zoom/hold/spotlight/overlay) skips that one directive and keeps rendering
the rest of the shot. See `tests/test_adjusted.py`, which reproduces the
exact original crash inputs. Verified end-to-end too: two fresh
`https://rhobear.ai` walkthroughs through the live service (port 8099,
`/api/generate`) both completed with real MP4 output.
