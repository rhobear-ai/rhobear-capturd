# director-rig/film.py

Executes a Director-mode shot list against the Captur'd MCP engine: record →
enrich → stylize → zoom/hold/spotlight/overlay → export. Lives on rhobear-vps
at `/opt/capturd-rig` (symlinked once the full rig lands in a follow-up PR);
shelled by the hosted service via `CAPTURD_RIG_DIR`.

## 2026-07-22 fix: KeyError in adjusted()

`adjusted()` mapped a shot-list act index to a live engine step index via
`kept.index(act_to_engine[act_i])`. If an act's `demo.act` call never
completed (selector timeout under `skip_unresolved`) or its engine step got
trimmed as a stray SPA interaction, that act index has no live engine step —
and the old code raised `KeyError`/`ValueError`, crashing the whole render.
Content-dependent: reproduced on a rhobear.ai walkthrough (job `08a1b1ad8396`),
not on lab.html walkthroughs whose selectors always resolved.

Fixed: the mapping logic is now a top-level, testable `_adjusted()` that
returns `None` and logs a warning instead of raising. Every call site
(zoom/hold/spotlight/overlay) skips that one directive and keeps rendering
the rest of the shot instead of failing the whole job. See
`tests/test_adjusted.py`, which reproduces the exact original crash inputs.

Verified end-to-end too: two fresh `https://rhobear.ai` walkthroughs through
the live service (port 8099, `/api/generate`) both completed with real MP4
output.
