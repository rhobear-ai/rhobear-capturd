"""DemoForge — Phase 1 recorder.

Headful Playwright session that captures user clicks + screenshots and produces
a DemoSpec JSON ready for the AI annotation pipeline.

Reuses SunSponge's Playwright infrastructure (browser channel fallback for
Windows) but does not modify ``capture_service.py``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from capturd.walk.schema import (
    BoundingRect,
    ContentMetadata,
    ContentMode,
    DemoSpec,
    DemoStep,
    Hotspot,
    Interaction,
)
from capturd.walk.voice import MIC_BUTTON_JS, VoiceConfig, VoiceLoop, VoiceLoopError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# JS overlay — injected on every page load via page.add_init_script()
# ---------------------------------------------------------------------------

# The script is plain JavaScript; we ship it as a string constant so it lands
# in the page's main world on every navigation. It:
#   * draws a fixed ● RECORDING badge bottom-right (not click-captured)
#   * listens for clicks in capture phase
#   * builds a CSS selector, bounding rect, and percentage hotspot
#   * pushes the payload to Python via window.recordClick (expose_function)
#   * mirrors step count back into the badge via window.__demoRecorderStepCount
#
# Notes on the selector builder: it prefers IDs, then classes (max 2),
# then :nth-of-type for sibling disambiguation — same heuristic as
# journey-trace's getCssSelector() (see research-findings.md).
OVERLAY_JS = r"""
(() => {
  if (window.__demoRecorderInstalled) return;
  window.__demoRecorderInstalled = true;
  window.__demoRecorderStepCount = 0;

  const BADGE_ID = '__demo-recorder-indicator';

  function ensureBadge() {
    let badge = document.getElementById(BADGE_ID);
    if (badge) return badge;
    badge = document.createElement('div');
    badge.id = BADGE_ID;
    badge.style.cssText = [
      'position: fixed',
      'right: 16px',
      'bottom: 16px',
      'z-index: 2147483647',
      'padding: 8px 14px',
      'border-radius: 999px',
      'background: rgba(20, 20, 24, 0.92)',
      'color: #fff',
      'font: 600 13px/1.2 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif',
      'box-shadow: 0 4px 18px rgba(0,0,0,0.35)',
      'display: flex',
      'align-items: center',
      'gap: 8px',
      'pointer-events: auto',
      'user-select: none',
      'letter-spacing: 0.04em',
    ].join(';');
    const dot = document.createElement('span');
    dot.style.cssText = [
      'width: 10px',
      'height: 10px',
      'border-radius: 50%',
      'background: #ff3b30',
      'box-shadow: 0 0 0 0 rgba(255,59,48,0.6)',
      'animation: __demoRecPulse 1.4s ease-out infinite',
    ].join(';');
    const label = document.createElement('span');
    label.id = BADGE_ID + '__label';
    label.textContent = 'RECORDING · 0 steps';
    badge.appendChild(dot);
    badge.appendChild(label);
    const style = document.createElement('style');
    style.textContent = '@keyframes __demoRecPulse { 0% { box-shadow: 0 0 0 0 rgba(255,59,48,0.6); } 70% { box-shadow: 0 0 0 12px rgba(255,59,48,0); } 100% { box-shadow: 0 0 0 0 rgba(255,59,48,0); } }';
    (document.head || document.documentElement).appendChild(style);
    (document.body || document.documentElement).appendChild(badge);
    return badge;
  }

  function setStepCount(n) {
    window.__demoRecorderStepCount = n;
    const label = document.getElementById(BADGE_ID + '__label');
    if (label) label.textContent = 'RECORDING · ' + n + ' step' + (n === 1 ? '' : 's');
  }

  function buildSelector(el) {
    if (!el || el.nodeType !== 1) return '';
    if (el.id) return '#' + (window.CSS && CSS.escape ? CSS.escape(el.id) : el.id);
    if (el === document.body) return 'body';
    const parts = [];
    let current = el;
    while (current && current !== document.body) {
      if (!current.tagName) break;
      let sel = current.tagName.toLowerCase();
      if (current.id) {
        parts.unshift('#' + (window.CSS && CSS.escape ? CSS.escape(current.id) : current.id));
        break;
      }
      if (current.className && typeof current.className === 'string') {
        const classes = current.className.trim().split(/\s+/).slice(0, 2)
          .filter(Boolean)
          .map(c => '.' + (window.CSS && CSS.escape ? CSS.escape(c) : c))
          .join('');
        if (classes) sel += classes;
      }
      const parent = current.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(c => c.tagName === current.tagName);
        if (siblings.length > 1) {
          const idx = siblings.indexOf(current) + 1;
          sel += ':nth-of-type(' + idx + ')';
        }
      }
      parts.unshift(sel);
      current = current.parentElement;
    }
    return parts.join(' > ');
  }

  function isBadge(target) {
    if (!target || !target.closest) return false;
    return !!target.closest('#' + BADGE_ID);
  }

  function describeClick(event) {
    const el = event.target;
    if (!el || el.nodeType !== 1) return null;
    const rect = el.getBoundingClientRect();
    const clientX = event.clientX;
    const clientY = event.clientY;
    let xPct = 0, yPct = 0;
    if (rect.width > 0) xPct = ((clientX - rect.left) / rect.width) * 100;
    if (rect.height > 0) yPct = ((clientY - rect.top) / rect.height) * 100;
    xPct = Math.max(0, Math.min(100, +xPct.toFixed(2)));
    yPct = Math.max(0, Math.min(100, +yPct.toFixed(2)));

    let text = '';
    if (el.innerText) text = el.innerText.trim().replace(/\s+/g, ' ');
    else if (el.textContent) text = el.textContent.trim().replace(/\s+/g, ' ');
    if (text.length > 80) text = text.slice(0, 77) + '...';

    return {
      type: 'click',
      target: {
        selector: buildSelector(el),
        tagName: el.tagName.toLowerCase(),
        text: text || undefined,
        boundingRect: {
          x: +rect.x.toFixed(2),
          y: +rect.y.toFixed(2),
          width: +rect.width.toFixed(2),
          height: +rect.height.toFixed(2),
        },
      },
      hotspot: { xPct, yPct },
      value: undefined,
    };
  }

  function handleClick(event) {
    if (isBadge(event.target)) return;
    if (event.button !== undefined && event.button !== 0) return; // left button only
    const payload = describeClick(event);
    if (!payload) return;
    payload.pageUrl = location.href;
    payload.pageTitle = document.title;
    payload.timestamp = Date.now();
    if (typeof window.recordClick === 'function') {
      try { window.recordClick(payload); } catch (_) { /* bridge gone */ }
    }
    // Always also stash for any late-attaching consumer.
    window.__demoRecorderLastClick = payload;
  }

  // Defer until DOM exists; otherwise retry on first mutation.
  function attach() {
    if (!document.body) {
      // body not ready yet — try again on next tick
      return false;
    }
    ensureBadge();
    document.addEventListener('click', handleClick, true);
    return true;
  }

  if (!attach()) {
    const mo = new MutationObserver(() => {
      if (attach()) mo.disconnect();
    });
    if (document.documentElement) {
      mo.observe(document.documentElement, { childList: true, subtree: true });
    }
    // Also try once DOMContentLoaded fires
    document.addEventListener('DOMContentLoaded', () => attach(), { once: true });
  }

  // Expose step-count updater for the Python side.
  window.__demoRecorderSetStepCount = setStepCount;
})();
"""


# ---------------------------------------------------------------------------
# Browser launch — headful, with the same Windows channel fallback the capture
# service uses. We deliberately don't import _launch_browser from
# capture_service because that one is hardcoded to headless=True.
# ---------------------------------------------------------------------------

async def _launch_demo_browser(playwright: Any, headless: bool = False) -> Any:
    """Launch a Chromium browser, falling back to system Chrome channel.

    On Linux/Windows where Playwright-managed Chromium may not be installed,
    falls back to the system Chrome channel. On Windows also tries Edge.
    """
    launch_args: list[str] = []
    attempts: list[dict[str, Any]] = [
        {"headless": headless, "args": launch_args}
    ]
    # Fallback: system Chrome channel (available on most Linux + Win).
    attempts.append(
        {"headless": headless, "channel": "chrome", "args": launch_args}
    )
    if os.name == "nt":
        attempts.append(
            {"headless": headless, "channel": "msedge", "args": launch_args}
        )
    last_error: Exception | None = None
    for kwargs in attempts:
        try:
            return await playwright.chromium.launch(**kwargs)
        except Exception as exc:  # pragma: no cover - environment-specific
            last_error = exc
            logger.warning("demo launch attempt failed (%s): %s", kwargs, exc)
    raise RuntimeError(
        f"unable to launch a browser for demo recording: {last_error}"
    )


# ---------------------------------------------------------------------------
# Content-mode detection (W1)
# ---------------------------------------------------------------------------


async def _detect_content_mode(page) -> ContentMetadata:
    """Probe the current page for canvas / video / iframe / mutation rate.

    Returns a ContentMetadata dataclass with raw signals. Threshold
    classification is done by ``_classify_content_mode()``.
    """
    try:
        signals = await page.evaluate("""() => {
            const vw = window.innerWidth;
            const vh = window.innerHeight;
            const vpArea = Math.max(1, vw * vh);

            // Largest <canvas> area / viewport area.
            let maxCanvasArea = 0;
            const canvases = document.querySelectorAll('canvas');
            canvases.forEach(c => {
                const r = c.getBoundingClientRect();
                const w = Math.max(0, Math.min(r.width, vw) - Math.max(0, -r.x));
                const h = Math.max(0, Math.min(r.height, vh) - Math.max(0, -r.y));
                if (r.width > 0 && r.height > 0) {
                    maxCanvasArea = Math.max(maxCanvasArea, r.width * r.height);
                }
            });
            const canvasAreaPct = (maxCanvasArea / vpArea) * 100;

            const hasVideo = document.querySelectorAll('video').length > 0;
            const hasIframe = document.querySelectorAll('iframe').length > 0;

            return {
                canvasAreaPct: +canvasAreaPct.toFixed(2),
                hasCanvas: canvases.length > 0,
                hasVideo: hasVideo,
                hasIframe: hasIframe,
                mutationRate: 0  // populated below
            };
        }""")
    except Exception as exc:
        logger.warning("content-mode probe failed: %s", exc)
        return ContentMetadata()

    # Mutation rate: install a MutationObserver for ~500ms, count mutations.
    try:
        mut_count = await page.evaluate("""() => {
            return new Promise((resolve, reject) => {
                const body = document.body;
                if (!body) { resolve(0); return; }
                let count = 0;
                const obs = new MutationObserver(() => { count++; });
                obs.observe(body, { childList: true, subtree: true, attributes: true });
                const started = performance.now();
                const timer = setInterval(() => {
                    const elapsed = performance.now() - started;
                    if (elapsed >= 400) {
                        clearInterval(timer);
                        obs.disconnect();
                        const rate = (count / elapsed) * 1000;
                        resolve(+rate.toFixed(2));
                    }
                }, 50);
                // Failsafe: resolve after 1s.
                setTimeout(() => {
                    clearInterval(timer);
                    obs.disconnect();
                    const elapsed = performance.now() - started;
                    const rate = (count / Math.max(1, elapsed)) * 1000;
                    resolve(+rate.toFixed(2));
                }, 1000);
            });
        }""")
    except Exception:
        mut_count = 0.0

    return ContentMetadata(
        hasCanvas=signals.get("hasCanvas", False),
        canvasAreaPct=signals.get("canvasAreaPct", 0.0),
        hasVideo=signals.get("hasVideo", False),
        hasIframe=signals.get("hasIframe", False),
        mutationRate=float(mut_count),
    )


def _classify_content_mode(cm: ContentMetadata) -> str:
    """Classify ContentMetadata into a ContentMode string.

    Thresholds:
      - canvasAreaPct >= 30  → VIDEO (likely a canvas app / game)
      - 0 < canvasAreaPct < 30 and has DOM chrome → HYBRID
      - else → DOM
    """
    if cm.canvasAreaPct >= 30:
        return ContentMode.VIDEO.value
    if cm.canvasAreaPct > 0:
        return ContentMode.HYBRID.value
    return ContentMode.DOM.value


# ---------------------------------------------------------------------------
# Agent reply parser (W1)
# ---------------------------------------------------------------------------

_AGENT_REPLY_JSON_RE = re.compile(r"\{[^}]*\}", re.DOTALL)


def _parse_agent_reply(reply: str) -> tuple[str | None, str | None, str | None]:
    """Parse the LLM's JSON reply into (action, selector, value).

    Returns (None, None, None) if parsing fails.
    """
    if not reply:
        return None, None, None

    # Strip markdown fences.
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", reply).strip()

    # Try direct parse.
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to find a JSON object in the text.
        m = _AGENT_REPLY_JSON_RE.search(cleaned)
        if not m:
            return None, None, None
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None, None, None

    if not isinstance(obj, dict):
        return None, None, None

    action = obj.get("action", "").strip().lower()
    if action not in ("click", "input", "navigate", "done"):
        return None, None, None

    selector = (obj.get("selector") or "").strip()
    value = (obj.get("value") or "").strip() if obj.get("value") else None

    return action, selector, value


# ---------------------------------------------------------------------------
# DemoRecorder — one instance per recording session
# ---------------------------------------------------------------------------


class DemoRecorderError(RuntimeError):
    """Raised when a recording session fails or is in the wrong state."""


class DemoRecorder:
    """Headful Playwright recorder. Produces a DemoSpec + PNGs per click.

    Modes (v1 target):

    * **Human-clicks mode** (current — ported from prev PR #17). User drives
      the browser; overlay JS captures clicks + hotspots. Works.
    * **Agent-driven mode** — TODO(W1). LLM step-picker chooses each next
      click from the flow goal + current DOM. This is the "prompt in,
      walkthrough out" premise. Owner-critical gap flagged in the previous
      build: the browser opened with no record button because there was no
      agent entrypoint. W1 fixes exactly that.
    * **Workflow (voice-dialog) mode** — TODO(W7). Human clicks; between
      clicks the agent asks "what are you illustrating?" via TTS
      (:mod:`capturd.walk.voice`) and extracts intent from the spoken reply.
      The voice loop primitives (push-to-talk, reply) are implemented in W6;
      W7 composes them into the dialog loop.

    Content-mode detection — W1. At each step, probe:
      - hasCanvas + canvasAreaPct (largest <canvas> area / viewport area)
      - hasVideo (<video> element present)
      - hasIframe (embedded iframe — YouTube etc.)
      - mutationRate (rrweb mutations/sec over a ~500ms window)
    Emit as :class:`capturd.walk.schema.ContentMetadata`. Pipeline picks
    DOM / video / hybrid based on canvas area threshold (~30%).
    """

    POLL_INTERVAL_S = 0.1

    def __init__(
        self,
        *,
        session_id: str,
        url: str,
        name: str,
        goal: str,
        viewport: dict[str, int] | None = None,
        output_dir: Path | None = None,
        workflow_mode: bool = False,
    ) -> None:
        self.session_id = session_id
        self.url = url
        self.name = name
        self.goal = goal
        self.viewport = viewport or {"width": 1440, "height": 900}
        self.output_dir = output_dir or (Path.cwd() / "demos" / session_id)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.workflow_mode = workflow_mode

        self.spec = DemoSpec(
            id=session_id,
            name=name,
            goal=goal,
            createdAt=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            viewport=dict(self.viewport),
            startUrl=url,
        )

        self._click_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._capture_task: asyncio.Task | None = None
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None
        self._started_at_ms: int | None = None
        # threading.Event so we can signal stop from any thread (the API path
        # calls stop() from uvicorn's worker thread, not the recorder's loop).
        self._stopped = threading.Event()
        # Set by the owning thread when the session has fully torn down —
        # agent mode finishes on its own; demo.stop waits on this.
        self.finished = threading.Event()
        self._last_url: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        # Voice — enabled when payload["voice"] is True.
        self.voice_loop: VoiceLoop | None = None
        self._voice_transcripts: list[str] = []  # accumulated transcripts

    # ----- lifecycle ---------------------------------------------------------

    # ----- agent-driven recording (W1) ---------------------------------------

    async def agent_record(self) -> DemoSpec:
        """Agent-driven recording: LLM picks each next action until done.

        Flow per turn:
        1. Screenshot + visible DOM text excerpt
        2. LLM call via RHOBEAR Vertex Gateway
        3. Execute action (click / input / navigate / done)
        4. Loop until done or max 30 steps

        The overlay JS capture loop runs in the background; clicks initiated
        by the agent via page.click() are captured by the overlay bridge and
        recorded as DemoStep entries with full selectors and hotspots.
        """
        from capturd.walk.ai_pipeline import _build_client, _llm_vision

        if self._capture_task is not None:
            raise DemoRecorderError("recorder already started")

        # ---- Launch browser + capture loop (same as start()) ----------------
        from playwright.async_api import async_playwright

        self._loop = asyncio.get_running_loop()
        self._playwright = await async_playwright().start()
        self._browser = await _launch_demo_browser(self._playwright, headless=True)
        self._context = await self._browser.new_context(
            viewport={"width": self.viewport["width"], "height": self.viewport["height"]},
            device_scale_factor=1,
            locale="en-US",
        )
        self._page = await self._context.new_page()
        await self._page.expose_function("recordClick", self._on_click)
        await self._page.add_init_script(OVERLAY_JS)
        await self._page.goto(self.url, wait_until="domcontentloaded")
        self._last_url = self._page.url
        self._started_at_ms = int(time.time() * 1000)
        self._capture_task = asyncio.create_task(
            self._capture_loop(), name=f"demo-cap-{self.session_id}"
        )
        logger.info("agent recorder launched: session=%s url=%s", self.session_id, self.url)

        # Wait a tick so the overlay installs and first paints settle.
        await asyncio.sleep(0.3)

        # ---- Agent loop -----------------------------------------------------
        client = _build_client()
        model = "gemini-2.5-flash"
        step_history: list[dict[str, Any]] = []
        previous_dom_hash: int | None = None

        max_steps = 30
        for turn in range(max_steps):
            assert self._page is not None
            if self._stopped.is_set():
                logger.info("agent recording stopped externally at turn %d", turn)
                break

            # ---- 1. Screenshot + DOM ----------------------------------------
            try:
                png_bytes = await self._page.screenshot(full_page=False, type="png")
            except Exception as exc:
                logger.warning("agent step %d: screenshot failed: %s", turn, exc)
                png_bytes = b""
            screenshot_b64 = (
                base64.b64encode(png_bytes).decode("ascii") if png_bytes else ""
            )

            # Extract visible viewport outerHTML (truncated for token budget).
            try:
                dom_snippet = await self._page.evaluate("""() => {
                    const body = document.body;
                    if (!body) return '<body missing>';
                    const clone = body.cloneNode(true);
                    clone.querySelectorAll('script, style, noscript, link, meta, svg')
                        .forEach(e => e.remove());
                    let html = clone.outerHTML || body.outerHTML || '';
                    return html.length > 5000 ? html.slice(0, 5000) + '...' : html;
                }""")
            except Exception:
                dom_snippet = "(DOM unavailable)"

            # ---- 2. Content-mode detection ----------------------------------
            cm = await _detect_content_mode(self._page)
            content_mode = _classify_content_mode(cm)

            # ---- 3. LLM call ------------------------------------------------
            history_text = ""
            for h in step_history:
                history_text += (
                    f"  Step {h['index']}: {h['type']} on {h.get('selector', '?')}"
                    f"{' = ' + h['value'] if h.get('value') else ''}\n"
                )

            prompt = (
                f"You are an agent driving a browser to record a product demo "
                f"walkthrough.\n\n"
                f"Demo name: {self.name}\n"
                f"Demo goal: {self.goal}\n"
                f"Current URL: {self._safe_url_sync()}\n"
                f"Page title: {self._safe_title_sync()}\n"
                f"Content mode: {content_mode} (canvas={cm.canvasAreaPct:.1f}%, "
                f"video={cm.hasVideo}, iframe={cm.hasIframe}, "
                f"mutRate={cm.mutationRate:.1f}/s)\n\n"
                f"Steps taken so far ({len(step_history)}/30):\n{history_text}\n"
                f"Relevant DOM excerpt (outerHTML, truncated):\n"
                f"```html\n{dom_snippet}\n```\n\n"
                f"Decide the NEXT action to advance the demo toward the goal. "
                f"Output valid JSON only (no prose, no fences):\n"
                f'{{"action": "click"|"input"|"navigate"|"done", '
                f'"selector": "CSS selector", '
                f'"value": "text for input or URL for navigate"}}\n\n'
                f"Rules:\n"
                f"- Use precise, stable CSS selectors (prefer #id, then .class, "
                f"then tag[name='...']).\n"
                f"- For input fields: action=input, selector=the field, "
                f"value=the text to type.\n"
                f"- For navigation: action=navigate, value=the full URL.\n"
                f"- If the flow is complete, return action=done.\n"
                f"- If you hit a dead end or error page, return action=done.\n"
                f"- Do NOT repeat the same action on the same selector more "
                f"than twice consecutively.\n"
                f"- If the same DOM appears 3 times in a row, return action=done.\n"
            )

            try:
                reply = await _llm_vision(
                    client,
                    model=model,
                    prompt=prompt,
                    image_b64=screenshot_b64,
                    max_tokens=300,
                )
            except Exception as exc:
                logger.warning("agent step %d: LLM call failed: %s", turn, exc)
                break

            # ---- 4. Parse LLM response -------------------------------------
            action, selector, value = _parse_agent_reply(reply)
            logger.info(
                "agent step %d: action=%s selector=%s value=%s",
                turn, action, selector, value,
            )

            if action == "done":
                logger.info("agent finished after %d steps", len(step_history))
                break

            if not action or not selector:
                logger.warning(
                    "agent step %d: unparseable reply: %s", turn, reply
                )
                step_history.append(
                    {"index": turn, "type": "unparseable", "selector": "?"}
                )
                continue

            # ---- 5. Execute action ------------------------------------------
            try:
                await self._execute_agent_action(action, selector, value)
            except Exception as exc:
                logger.warning("agent step %d: action failed: %s", turn, exc)
                step_history.append({
                    "index": turn, "type": action, "selector": selector,
                    "value": value, "error": str(exc),
                })
                continue

            # ---- 6. Wait for the capture loop to process the click ----------
            await asyncio.sleep(0.5)

            # ---- 7. Emit DemoStep with content metadata ---------------------
            step_index = len(self.spec.steps) - 1
            if step_index >= 0:
                step = self.spec.steps[step_index]
                step.contentMode = content_mode
                step.contentMetadata = asdict(cm)

            # ---- 8. Detect stuck loop ---------------------------------------
            try:
                current_dom_hash = await self._page.evaluate(
                    "() => document.body ? document.body.outerHTML.length : 0"
                )
            except Exception:
                current_dom_hash = None

            if current_dom_hash == previous_dom_hash:
                self._stuck_same_dom_count = (
                    getattr(self, "_stuck_same_dom_count", 0) + 1
                )
                if self._stuck_same_dom_count >= 3:
                    logger.info("agent: same DOM 3x in a row — finishing")
                    break
            else:
                self._stuck_same_dom_count = 0
            previous_dom_hash = current_dom_hash

            step_history.append({
                "index": turn, "type": action, "selector": selector, "value": value,
            })

        # ---- Teardown -------------------------------------------------------
        self._stopped.set()
        try:
            await asyncio.wait_for(self._capture_task, timeout=5.0)
        except asyncio.TimeoutError:
            self._capture_task.cancel()
        await self._teardown_browser()
        self._write_outputs()
        logger.info(
            "agent_record complete: session=%s steps=%d",
            self.session_id, len(self.spec.steps),
        )
        return self.spec

    async def _execute_agent_action(
        self, action: str, selector: str, value: str | None
    ) -> None:
        """Execute a single agent-chosen action via Playwright."""
        assert self._page is not None

        if action == "click":
            # Ensure element is visible before clicking.
            try:
                await self._page.wait_for_selector(selector, state="visible", timeout=3000)
            except Exception:
                pass  # Element might be present but offscreen; try anyway.
            await self._page.click(selector, timeout=5000)
            await asyncio.sleep(0.3)  # Let the page react.

        elif action == "input":
            await self._page.wait_for_selector(selector, state="visible", timeout=3000)
            # Click to focus first — the overlay bridge records the click, so
            # typing into a field shows up as a real step (hotspot on the
            # field) instead of silently mutating the page.
            try:
                await self._page.click(selector, timeout=5000)
            except Exception:
                pass  # field may be focus-only; fill still works
            await self._page.fill(selector, value or "", timeout=5000)
            await asyncio.sleep(0.3)

        elif action == "navigate":
            if value and (value.startswith("http://") or value.startswith("https://")):
                await self._page.goto(value, wait_until="domcontentloaded", timeout=15000)
                self._last_url = self._page.url
                await asyncio.sleep(0.5)

    def _safe_url_sync(self) -> str:
        """Get current URL without awaiting (for prompt building)."""
        try:
            return self._page.url if self._page else self._last_url or ""
        except Exception:
            return self._last_url or ""

    def _safe_title_sync(self) -> str:
        """Get current title (sync — may be stale)."""
        # Best-effort: for the LLM prompt, a slightly stale title is fine.
        try:
            if self._page:
                # We can't await here; use last known.
                return getattr(self, "_last_title", "")
        except Exception:
            pass
        return ""

    # ----- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Launch the browser, navigate, and start capturing."""
        from playwright.async_api import async_playwright

        if self._capture_task is not None:
            raise DemoRecorderError("recorder already started")

        if self.workflow_mode and self.voice_loop is None:
            raise DemoRecorderError(
                "workflow mode requires a VoiceLoop — "
                "pass one via DemoManager.start(payload['voice']=True)"
            )

        self._loop = asyncio.get_running_loop()
        self._playwright = await async_playwright().start()
        self._browser = await _launch_demo_browser(self._playwright)
        self._context = await self._browser.new_context(
            viewport={"width": self.viewport["width"], "height": self.viewport["height"]},
            device_scale_factor=1,
            locale="en-US",
        )
        self._page = await self._context.new_page()

        # Bridge: JS calls window.recordClick(payload) → Python callback.
        await self._page.expose_function("recordClick", self._on_click)

        # Voice push-to-talk bridges (if voice_loop is active).
        if self.voice_loop is not None:
            await self._page.expose_function("__demoMicStart", self.voice_loop._js_mic_start)
            await self._page.expose_function("__demoMicStop", self.voice_loop._js_mic_stop)
            # Callback: JS hands transcript back to Python after mic release.
            await self._page.evaluate("""
                window.__demoOnVoiceTranscript = (text) => {
                    window.__demoRecorderLastVoiceTranscript = text;
                    // Also relay through recordClick shape so the recorder
                    // picks it up as a userDirection on the next step.
                    if (typeof window.recordClick === 'function') {
                        window.recordClick({
                            type: 'voice',
                            transcript: text,
                            pageUrl: location.href,
                            pageTitle: document.title,
                            timestamp: Date.now(),
                        });
                    }
                };
            """)

        # Init script re-installs overlay on every navigation.
        await self._page.add_init_script(OVERLAY_JS + "\n" + MIC_BUTTON_JS)

        await self._page.goto(self.url, wait_until="domcontentloaded")
        self._last_url = self._page.url
        self._started_at_ms = int(time.time() * 1000)

        # Start voice loop if enabled.
        if self.voice_loop is not None:
            await self.voice_loop.start(
                on_utterance=lambda text: self._voice_transcripts.append(text),
                page=self._page,
            )

        self._capture_task = asyncio.create_task(self._capture_loop(), name=f"demo-cap-{self.session_id}")
        logger.info("demo recorder started: session=%s url=%s voice=%s",
                     self.session_id, self.url, self.voice_loop is not None)

    def stop(self) -> DemoSpec:
        """Stop the recorder from any thread/loop and return the persisted spec.

        Safe to call from the FastAPI worker thread: we hand the actual
        teardown off to the recorder's own loop via run_coroutine_threadsafe.
        The loop is stopped from the calling thread AFTER the future resolves
        — calling loop.stop() from inside _stop_async would kill the loop
        before future.result() receives its callback.
        """
        if self._loop is None or self._capture_task is None:
            raise DemoRecorderError("recorder was never started")
        self._stopped.set()
        future = asyncio.run_coroutine_threadsafe(self._stop_async(), self._loop)
        result = future.result(timeout=30.0)
        # Stop the parked loop now that we have the result.
        self._loop.call_soon_threadsafe(self._loop.stop)
        return result

    async def _stop_async(self) -> DemoSpec:
        """Actual teardown — runs inside the recorder's event loop."""
        assert self._capture_task is not None
        try:
            await asyncio.wait_for(self._capture_task, timeout=5.0)
        except asyncio.TimeoutError:
            self._capture_task.cancel()
        if self.voice_loop is not None:
            try:
                await self.voice_loop.stop()
            except Exception as exc:
                logger.warning("error stopping voice loop: %s", exc)
        try:
            if self._context:
                await self._context.close()
        except Exception:
            pass
        try:
            if self._browser:
                await self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        await self._teardown_browser()
        self._write_outputs()
        logger.info(
            "demo recorder stopped: session=%s steps=%d",
            self.session_id, len(self.spec.steps),
        )
        return self.spec

    async def _teardown_browser(self) -> None:
        """Close browser context, browser, and playwright. Idempotent."""
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    # ----- click bridge ------------------------------------------------------

    async def _on_click(self, payload: dict) -> None:
        """Called by the page when a click is captured. Non-blocking enqueue."""
        # Bridge runs in the Playwright async loop — same loop as our task.
        await self._click_queue.put(payload)

    # ----- workflow-mode dialog loop -----------------------------------------

    async def _prompt_step_intent(self, step: DemoStep) -> None:
        """Ask the user what they're illustrating after a click (workflow mode).

        1. Compose a short question from the step's interaction data.
        2. Speak the question via TTS (voice_loop.reply).
        3. Capture the user's spoken answer (voice_loop.push_to_talk, 8s window).
        4. Persist ``userIntent`` on the step and append to ``annotation``.

        Gracefully handles empty transcripts (user says nothing).
        """
        if self.voice_loop is None:
            return  # defensive — caller guards this

        target = step.interaction.target
        target_desc = target.get("text") or target.get("selector", "element")
        # Keep under 15 words — TTS latency matters.
        # Truncate target_desc if it's very long.
        if len(target_desc) > 40:
            target_desc = target_desc[:37] + "..."
        question = f"You clicked {target_desc}. What are you illustrating here?"

        # 1. Speak the question.
        try:
            await self.voice_loop.reply(question)
        except Exception as exc:
            logger.warning("workflow TTS reply failed: %s", exc)
            # Continue anyway — the overlay shows the question text.

        # 2. Listen for answer (8s window).
        try:
            transcript = await self.voice_loop.push_to_talk(duration_ms=8000)
        except Exception as exc:
            logger.warning("workflow push_to_talk failed: %s", exc)
            transcript = ""

        # 3. Persist.
        if transcript and transcript.strip():
            step.userIntent = transcript.strip()
            step.annotation = (step.annotation or "") + f" [user intent: {transcript.strip()}]"

    # ----- capture loop ------------------------------------------------------

    async def _capture_loop(self) -> None:
        assert self._page is not None
        while not self._stopped.is_set():
            try:
                payload = await asyncio.wait_for(
                    self._click_queue.get(), timeout=self.POLL_INTERVAL_S
                )
            except asyncio.TimeoutError:
                continue

            step_index = len(self.spec.steps)
            timestamp_ms = int(time.time() * 1000) - (self._started_at_ms or 0)
            payload_type = payload.get("type", "click")
            is_voice = payload_type == "voice"

            # Screenshot AFTER the event so we capture the post-event state.
            # The RECORDING badge is UI for the person driving, not part of
            # the product being demoed — hide it around the capture.
            try:
                await self._page.evaluate(
                    "() => { const b = document.getElementById('__demo-recorder-indicator');"
                    " if (b) b.style.visibility = 'hidden'; }"
                )
            except Exception:
                pass
            try:
                png_bytes = await self._page.screenshot(full_page=False, type="png")
            except Exception as exc:
                logger.warning("screenshot failed on step %d: %s", step_index, exc)
                png_bytes = b""
            try:
                await self._page.evaluate(
                    "() => { const b = document.getElementById('__demo-recorder-indicator');"
                    " if (b) b.style.visibility = 'visible'; }"
                )
            except Exception:
                pass

            shot_filename = f"step_{step_index:03d}.png"
            shot_path = self.output_dir / shot_filename
            if png_bytes:
                shot_path.write_bytes(png_bytes)

            if is_voice:
                # Voice transcript — stored as userDirection on a marker step.
                transcript = payload.get("transcript", "")
                step = DemoStep(
                    index=step_index,
                    timestamp=timestamp_ms,
                    pageUrl=payload.get("pageUrl") or self._safe_url(),
                    pageTitle=payload.get("pageTitle") or (await self._safe_title()),
                    interaction=Interaction(
                        type="voice",
                        target={},
                        hotspot={"xPct": 0, "yPct": 0},
                        value=transcript,
                    ),
                    userDirection=transcript,
                    screenshotBase64=None,
                    screenshotPath=str(shot_path.relative_to(self.output_dir.parent))
                    if png_bytes
                    else None,
                    screenshotError=None if png_bytes else "screenshot capture failed",
                )
            else:
                step = DemoStep(
                    index=step_index,
                    timestamp=timestamp_ms,
                    pageUrl=payload.get("pageUrl") or self._safe_url(),
                    pageTitle=payload.get("pageTitle") or (await self._safe_title()),
                    interaction=Interaction(
                        type=payload_type,
                        target=payload.get("target", {}),
                        hotspot=payload.get("hotspot", {"xPct": 0, "yPct": 0}),
                        value=payload.get("value"),
                    ),
                    screenshotBase64=None,
                    screenshotPath=str(shot_path.relative_to(self.output_dir.parent))
                    if png_bytes
                    else None,
                    screenshotError=None if png_bytes else "screenshot capture failed",
                )
            self.spec.steps.append(step)
            self._last_url = step.pageUrl

            # Workflow mode: after each user click, prompt for intent.
            if self.workflow_mode and not is_voice and self.voice_loop is not None:
                try:
                    await self._prompt_step_intent(step)
                except Exception as exc:
                    logger.warning("workflow intent prompt failed: %s", exc)

            # Update the on-page badge step count.
            try:
                await self._page.evaluate(
                    "(n) => { if (window.__demoRecorderSetStepCount) window.__demoRecorderSetStepCount(n); }",
                    len(self.spec.steps),
                )
            except Exception:
                # Page might have navigated away mid-call; safe to ignore.
                pass

    async def _safe_url(self) -> str:
        try:
            return self._page.url if self._page else ""
        except Exception:
            return ""

    async def _safe_title(self) -> str:
        try:
            return await self._page.title() if self._page else ""
        except Exception:
            return ""

    # ----- output ------------------------------------------------------------

    def _write_outputs(self) -> None:
        """Persist demo.json next to the screenshots."""
        out_path = self.output_dir / "demo.json"
        out_path.write_text(
            json.dumps(self.spec.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        logger.info("wrote %s (%d steps)", out_path, len(self.spec.steps))

    # ----- public read-only handle -------------------------------------------

    def get_spec(self) -> DemoSpec:
        """Return the current spec snapshot. Safe to call while recording."""
        return self.spec


# ---------------------------------------------------------------------------
# DemoManager — in-process registry of active recordings (used by app.py)
# ---------------------------------------------------------------------------


class DemoManager:
    """Tracks active DemoRecorder sessions so the API can find them by id."""

    def __init__(self, output_root: Path | None = None) -> None:
        self.output_root = output_root or (Path.cwd() / "demos")
        self.output_root.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, DemoRecorder] = {}
        self._lock = threading.Lock()
        self._voice_config: VoiceConfig | None = None  # set by start() when voice=True

    def new_session_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def start(self, payload: dict[str, Any]) -> tuple[Any, str, str]:
        """Create a DemoRecorder and return (recorder, session_id, mode).

        mode is 'human' (default — headful, user drives) or 'agent' (LLM picks
        each next click via agent_record). The caller uses this to decide
        whether to call ``recorder.start()`` or ``recorder.agent_record()``.
        """
        url = (payload.get("url") or "").strip()
        name = (payload.get("name") or "Untitled demo").strip()
        goal = (payload.get("goal") or "").strip()
        mode = payload.get("mode", "human")
        if not url:
            raise DemoRecorderError("url is required")
        if not (url.startswith("http://") or url.startswith("https://")):
            raise DemoRecorderError(f"unsupported url scheme: {url}")

        viewport = payload.get("viewport") or {"width": 1440, "height": 900}
        session_id = payload.get("sessionId") or self.new_session_id()
        out_dir = self.output_root / session_id

        voice_enabled = bool(payload.get("voice"))
        workflow_enabled = bool(payload.get("workflow"))

        if workflow_enabled and not voice_enabled:
            raise DemoRecorderError(
                "workflow mode requires voice — pass voice=True alongside workflow=True"
            )

        recorder = DemoRecorder(
            session_id=session_id,
            url=url,
            name=name,
            goal=goal,
            viewport=viewport,
            output_dir=out_dir,
            workflow_mode=workflow_enabled,
        )
        if voice_enabled:
            try:
                voice_cfg = VoiceConfig(
                    workflow_mode=workflow_enabled,
                )
                recorder.voice_loop = VoiceLoop(config=voice_cfg)
            except VoiceLoopError:
                logger.warning("voice mode requested but voice extras not installed")
        with self._lock:
            self._sessions[session_id] = recorder
        return recorder, session_id, mode

    def get(self, session_id: str) -> DemoRecorder:
        with self._lock:
            rec = self._sessions.get(session_id)
        if rec is None:
            raise DemoRecorderError(f"unknown session: {session_id}")
        return rec

    def discard(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "sessionId": sid,
                    "url": r.url,
                    "name": r.name,
                    "stepCount": len(r.spec.steps),
                }
                for sid, r in self._sessions.items()
            ]


# ---------------------------------------------------------------------------
# Helper for synchronous callers (FastAPI threadpool)
# ---------------------------------------------------------------------------


def run_async(coro: Any) -> Any:
    """Run an async coroutine to completion from sync code."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're inside an async context; create a new loop in a thread.
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                return ex.submit(asyncio.run, coro).result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


__all__ = [
    "BoundingRect",
    "ContentMetadata",
    "ContentMode",
    "DemoManager",
    "DemoRecorder",
    "DemoRecorderError",
    "DemoSpec",
    "DemoStep",
    "Hotspot",
    "Interaction",
    "OVERLAY_JS",
    "_detect_content_mode",
    "_classify_content_mode",
    "_parse_agent_reply",
    "VoiceConfig",
    "VoiceLoop",
    "VoiceLoopError",
    "run_async",
]