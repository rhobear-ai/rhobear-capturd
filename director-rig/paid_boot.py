"""Paid-lane engine boot — Captur'd MCP server with Director quality patches IN MEMORY.

The OSS engine on disk stays frozen at v1 (THE WALL); this shim is the paid layer's
private overlay, applied at import time, zero repo changes, fully reversible.

Patches:
1. HUMAN TYPING — `demo.act` input actions type char-by-char (real key events, so the
   rrweb recording + export show someone actually typing) instead of batch `page.fill`.
   Knob: CAPTURD_TYPE_DELAY_MS (default 70; ~55-90 reads naturally human).
2. VOICE KNOB — CAPTURD_VOICE (any edge-tts voice, e.g. en-US-GuyNeural,
   en-GB-SoniaNeural; list with `edge-tts --list-voices`) overrides the voiceover voice
   EVERYWHERE: enrichment stage 3 and demo.edit(regenerate_voice=True). Unset -> engine
   default (en-US-AriaNeural).

Run exactly like the stock server (same stdio MCP protocol):
    python rig/paid_boot.py
from the sunsponge-capture repo dir (or with it on PYTHONPATH).
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Script mode puts THIS file's dir on sys.path, not the engine repo — add it.
# Override with CAPTURD_REPO if the repo ever moves. Same default as film.py.
_REPO = Path(os.environ.get("CAPTURD_REPO", "/opt/sunsponge-capture"))
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import capturd.walk.ai_pipeline as aip
import capturd.walk.coordinator as coord
import capturd.walk.recorder as rec

# ---- Patch 1: human typing on input actions --------------------------------

_orig_exec = rec.DemoRecorder._execute_agent_action


async def _exec_with_typing(self, action: str, selector: str, value: str | None) -> None:
    if action != "input":
        return await _orig_exec(self, action, selector, value)
    page = self._page
    assert page is not None
    await page.wait_for_selector(selector, state="visible", timeout=3000)
    # Focus click first (mirrors stock behavior: the overlay bridge records the
    # click so the step lands with a hotspot on the field). Click the LEFT edge
    # of the field, not the center — the hotspot anchors the camera, and the
    # typed words render from the left; centering there keeps them in frame on
    # wide inputs (owner: "the cursor has to be where the words show up").
    try:
        box = await page.locator(selector).first.bounding_box()
        if box and box.get("width"):
            pos = {"x": min(28.0, max(8.0, box["width"] * 0.08)),
                   "y": box["height"] / 2}
            await page.click(selector, position=pos, timeout=5000)
        else:
            await page.click(selector, timeout=5000)
    except Exception:
        try:
            await page.click(selector, timeout=5000)
        except Exception as exc:
            # Both click attempts failed -- typing below proceeds into an
            # unfocused field (empty-looking recording) with no other
            # signal that anything went wrong. Log it.
            print(f"[paid_boot] WARNING click failed for selector {selector!r}: {exc}")
    delay = int(os.environ.get("CAPTURD_TYPE_DELAY_MS", "70") or "70")
    text = value or ""
    await page.fill(selector, "", timeout=5000)  # clear, then really type
    await page.type(selector, text, delay=delay,
                    timeout=max(10000, delay * len(text) + 8000))
    await asyncio.sleep(0.3)


rec.DemoRecorder._execute_agent_action = _exec_with_typing

# ---- Patch 1b: record the FULL typed value on input steps -------------------
# With char-typing, the overlay bridge snapshots the step at the focus click —
# interaction.value captures only the first keystroke ("d"). The typing overlay
# (Patch 3) and any downstream consumer need the whole string.

_orig_act = rec.DemoRecorder.act


def _act_fix_value(self, action, *args, **kwargs):
    res = _orig_act(self, action, *args, **kwargs)
    try:
        value = kwargs.get("value", args[1] if len(args) > 1 else "")
        if action == "input" and value and self.spec.steps:
            self.spec.steps[-1].interaction.value = value
    except Exception:
        pass
    return res


rec.DemoRecorder.act = _act_fix_value

# ---- Patch 2: voiceover voice knob ------------------------------------------

_orig_synth = aip._synthesize_one


async def _synth_with_voice(text: str, voice: str = "en-US-AriaNeural"):
    v = (os.environ.get("CAPTURD_VOICE", "") or "").strip() or voice
    return await _orig_synth(text, voice=v)


aip._synthesize_one = _synth_with_voice
coord._synthesize_one = _synth_with_voice  # coordinator imported the symbol directly

# ---- Patch 3: synthetic typing animation in the EXPORT -----------------------
# The export renders one still per step + synthetic overlays (cursor/ripple/
# captions). Real keystrokes never show — and char-typed input can land AFTER
# the step's screenshot, leaving the field visibly empty. This injects a
# pure-function-of-t typing overlay into the viewer template: during an input
# step's window it types interaction.value into the field rect char-by-char
# (with caret), then holds the full line until the next step's screenshot
# (which contains the real typed text) fades in.

_TYPING_JS = r"""
(function () {
  var boot = function () {
    var exp = window.__demoExport;
    if (!exp || exp.__paidTypingWrapped) return;
    exp.__paidTypingWrapped = true;
    var DELAY = 70;
    var spec = null, wins = null, overlays = null;

    function build() {
      try {
        spec = JSON.parse(document.getElementById('demo-data').textContent);
      } catch (e) { spec = { steps: [] }; }
      var layer = document.getElementById('panzoom-layer');
      overlays = [];
      (spec.steps || []).forEach(function (s, i) {
        var it = s && s.interaction;
        // The overlay bridge records typing steps as type 'click' (the focus
        // click) — a non-empty value is the real "typing happened here" signal.
        if (!it || !it.value || !String(it.value).trim()) return;
        var r = it.target && it.target.boundingRect;
        if (!r || !isFinite(r.width) || r.width < 24) return;
        var el = document.createElement('div');
        var fs = Math.max(13, Math.min(17, Math.round(r.height * 0.3)));
        el.style.position = 'absolute';
        el.style.boxSizing = 'border-box';
        el.style.overflow = 'hidden';
        el.style.left = (r.x + 2) + 'px';
        el.style.top = (r.y + 2) + 'px';
        el.style.width = (r.width - 4) + 'px';
        el.style.height = (r.height - 4) + 'px';
        el.style.background = '#12121c';
        el.style.borderRadius = '8px';
        el.style.color = '#eceaf4';
        el.style.fontFamily = "ui-sans-serif, system-ui, 'Segoe UI', sans-serif";
        el.style.fontSize = fs + 'px';
        el.style.display = 'flex';
        el.style.alignItems = r.height > 84 ? 'flex-start' : 'center';
        el.style.padding = r.height > 84 ? '12px 14px' : '0 14px';
        el.style.whiteSpace = 'pre';
        el.style.zIndex = '5';
        el.style.visibility = 'hidden';
        layer.appendChild(el);
        overlays.push({ el: el, idx: i, text: String(it.value) });
      });
    }

    function render(t) {
      overlays.forEach(function (o) {
        var w = wins[o.idx];
        if (!w) { o.el.style.visibility = 'hidden'; return; }
        var start = (w.clickAt != null ? w.clickAt : w.tStart + 600) + 150;
        var nextW = wins[o.idx + 1];
        var end = nextW ? (nextW.fadeStart != null ? nextW.fadeStart : nextW.tStart + 600)
                        : (w.tEnd + 600);
        if (t < start || t >= end) { o.el.style.visibility = 'hidden'; return; }
        // Reveal may span past fadeStart — the overlay covers both stages of
        // this step, so typing keeps flowing while the screenshot crossfades.
        var revealEnd = Math.min(start + o.text.length * DELAY, end - 350);
        var frac = revealEnd > start
          ? Math.max(0, Math.min(1, (t - start) / (revealEnd - start))) : 1;
        var n = Math.round(frac * o.text.length);
        var caret = (frac < 1 && (Math.floor(t / 450) % 2 === 0)) ? '▏' : '';
        o.el.textContent = o.text.slice(0, n) + caret;
        o.el.style.visibility = 'visible';
      });
    }

    var origPrepare = exp.prepare;
    exp.prepare = function () {
      var args = arguments, self = this;
      return Promise.resolve(origPrepare.apply(self, args)).then(function (res) {
        wins = (res && res.stepWindows) || [];
        build();
        return res;
      });
    };

    var origSeek = exp.seek;
    exp.seek = function (tMs) {
      var out = origSeek.apply(this, arguments);
      if (overlays && wins) render(tMs);
      return out;
    };
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else { boot(); }
})();
"""

_orig_tpl_path = coord.viewer_template_path
_patched_tpl: Path | None = None


def _paid_template_path() -> Path:
    global _patched_tpl
    if _patched_tpl is not None and _patched_tpl.is_file():
        return _patched_tpl
    src = _orig_tpl_path().read_text(encoding="utf-8")
    inj = "<script>\n" + _TYPING_JS + "\n</script>\n</body>"
    html = inj.join(src.rsplit("</body>", 1))  # last </body> only
    import tempfile

    d = Path(tempfile.gettempdir()) / "capturd-paid"
    d.mkdir(parents=True, exist_ok=True)
    _patched_tpl = d / "viewer-paid.html"
    _patched_tpl.write_text(html, encoding="utf-8")
    return _patched_tpl


coord.viewer_template_path = _paid_template_path
import capturd.walk.viewer as _viz  # noqa: E402

_viz.default_template_path = _paid_template_path

# ---- Run the stock server ----------------------------------------------------

if __name__ == "__main__":
    from capturd.mcp.server import main

    sys.exit(main())
