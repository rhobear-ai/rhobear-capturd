"""DemoForge AI enrichment pipeline (Phase 3).

Reads a raw ``DemoSpec`` JSON produced by the Phase 1 recorder and enriches it
with:

  1. Per-step natural-language annotations (vision LLM via screenshots)
  2. A 2-3 sentence flow summary (text LLM)
  3. Per-step voiceover audio (Edge TTS, no API key)
  4. Per-step cursor bezier paths (deterministic math)
  5. A pan/zoom animation timeline (text LLM → JSON)

All LLM calls go through the RHOBEAR Vertex Gateway, an OpenAI-compatible
proxy that fronts Google Vertex AI.

Auth
----
The gateway bearer token is read from the ``RHOBEAR_GW_API_KEY`` env var. It is
**never** logged, echoed, or persisted to disk by this module. A missing or
empty token raises ``DemoAIError`` with an actionable message. ``base_url`` is
overridable via ``RHOBEAR_GW_BASE_URL`` (default ``https://gw.rhobear.ai/v1``).

Model selection
---------------
The default model is ``gemini-2.5-flash`` for both vision and text. The brief
specified ``gemini-3.5-flash`` which is also available on the gateway but is a
reasoning model that consumes large numbers of internal reasoning tokens per
call — wasteful for short annotation/summary outputs. The constructor accepts
``model_vision`` and ``model_text`` to override.
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
from pathlib import Path
from typing import Any, Iterable

# OpenAI-compatible client. Imported lazily inside _build_client() so the
# module can still be imported on systems without the openai package.
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class DemoAIError(RuntimeError):
    """Raised when the AI pipeline cannot complete (config, network, parse)."""


# ---------------------------------------------------------------------------
# Env-var auth
# ---------------------------------------------------------------------------

_KEY_ENV = "RHOBEAR_GW_API_KEY"
_BASE_ENV = "RHOBEAR_GW_BASE_URL"
_DEFAULT_BASE_URL = "https://gw.rhobear.ai/v1"
_DEFAULT_MODEL = "gemini-2.5-flash"


def _load_key() -> str:
    """Read the gateway bearer token from env. Never logs the value."""
    token = os.environ.get(_KEY_ENV, "").strip()
    if not token:
        raise DemoAIError(
            f"{_KEY_ENV} is not set. Export the RHOBEAR gateway token in your "
            f"environment (e.g. in a .env file loaded before app startup) and "
            f"retry. Do NOT hardcode it in source."
        )
    return token


def _base_url() -> str:
    return os.environ.get(_BASE_ENV, _DEFAULT_BASE_URL).strip() or _DEFAULT_BASE_URL


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------


def _build_client():
    """Construct an async OpenAI-compatible client. Imports openai lazily."""
    try:
        from openai import AsyncOpenAI
    except ImportError as exc:  # pragma: no cover - import-time guard
        raise DemoAIError(
            "The 'openai' package is not installed. Run `pip install openai>=1.0`."
        ) from exc
    return AsyncOpenAI(base_url=_base_url(), api_key=_load_key())


async def _llm_text(
    client,
    *,
    model: str,
    prompt: str,
    max_tokens: int = 500,
) -> str:
    """Plain text completion. Returns the assistant message string."""
    resp = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0.4,
    )
    return (resp.choices[0].message.content or "").strip()


async def _llm_vision(
    client,
    *,
    model: str,
    prompt: str,
    image_b64: str,
    mime: str = "image/png",
    max_tokens: int = 500,
) -> str:
    """Vision completion given a base64-encoded image. Returns the reply string."""
    image_url = f"data:{mime};base64,{image_b64}"
    resp = await client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        }],
        max_tokens=max_tokens,
        temperature=0.4,
    )
    return (resp.choices[0].message.content or "").strip()


# ---------------------------------------------------------------------------
# Cursor path — pure math, deterministic
# ---------------------------------------------------------------------------


def _compute_cursor_path(
    prev_step: dict | None,
    curr_step: dict,
    *,
    duration_ms: int = 400,
    sample_count: int = 20,
) -> list[dict] | None:
    """Sample N points along a cubic-bezier arc from prev hotspot to current.

    Returns ``None`` for the very first step (no previous frame to fly from).
    """
    if not prev_step:
        return None
    prev = prev_step.get("interaction", {})
    curr = curr_step.get("interaction", {})
    pr = (prev.get("target") or {}).get("boundingRect")
    cr = (curr.get("target") or {}).get("boundingRect")
    ph = prev.get("hotspot") or {}
    ch = curr.get("hotspot") or {}
    if not (pr and cr and ph and ch):
        return None

    from_x = pr["x"] + (pr["width"]  * ph.get("xPct", 0) / 100)
    from_y = pr["y"] + (pr["height"] * ph.get("yPct", 0) / 100)
    to_x   = cr["x"] + (cr["width"]  * ch.get("xPct", 0) / 100)
    to_y   = cr["y"] + (cr["height"] * ch.get("yPct", 0) / 100)

    dx, dy = to_x - from_x, to_y - from_y
    length = max(1.0, (dx * dx + dy * dy) ** 0.5)
    offset = min(80.0, length * 0.35)
    # Perpendicular unit vector (rotate 90° CCW).
    px, py = -dy / length, dx / length

    cp1x = from_x + dx * 0.25 + px * offset
    cp1y = from_y + dy * 0.25 + py * offset
    cp2x = from_x + dx * 0.75 + px * offset
    cp2y = from_y + dy * 0.75 + py * offset

    points: list[dict] = []
    for i in range(sample_count):
        t = i / (sample_count - 1) if sample_count > 1 else 1.0
        u = 1.0 - t
        x = (
            u * u * u * from_x
            + 3 * u * u * t * cp1x
            + 3 * u * t * t * cp2x
            + t * t * t * to_x
        )
        y = (
            u * u * u * from_y
            + 3 * u * u * t * cp1y
            + 3 * u * t * t * cp2y
            + t * t * t * to_y
        )
        points.append({
            "x": round(x, 1),
            "y": round(y, 1),
            "t": round(t * duration_ms),
        })
    return points


# ---------------------------------------------------------------------------
# Animation timeline JSON extraction
# ---------------------------------------------------------------------------

_TIMELINE_ARRAY_RE = re.compile(r"\[\s*\{.*\}\s*\]", re.DOTALL)


def _extract_timeline_json(text: str) -> list[dict] | None:
    """Find the first JSON array of objects in the model's reply.

    Gemini occasionally wraps the array in prose or fences. We:
      1. Strip ```json ... ``` fences if present.
      2. Find the first '[' that begins a balanced array containing '{' entries.
      3. json.loads() and validate the shape.
    """
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", text).strip()

    # Fast path: the whole string parses as an array.
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass

    # Find a balanced [ ... ] that contains at least one {.
    start = cleaned.find("[")
    while start != -1:
        depth = 0
        for end in range(start, len(cleaned)):
            ch = cleaned[end]
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    candidate = cleaned[start:end + 1]
                    try:
                        parsed = json.loads(candidate)
                    except json.JSONDecodeError:
                        break
                    if isinstance(parsed, list):
                        return parsed
                    break
        start = cleaned.find("[", start + 1)
    return None


def _validate_timeline(entries: list[dict], step_count: int) -> list[dict]:
    """Drop/fix timeline entries that don't match the documented schema."""
    allowed_actions = {"zoomTo", "panTo", "zoomToFit", "reset", None}
    out: list[dict] = []
    for raw in entries:
        if not isinstance(raw, dict):
            continue
        try:
            idx = int(raw.get("stepIndex"))
        except (TypeError, ValueError):
            continue
        if idx < 0 or idx >= step_count:
            continue
        action = raw.get("action")
        if action not in allowed_actions:
            continue
        entry: dict[str, Any] = {"stepIndex": idx, "action": action}
        if action is not None:
            if raw.get("target"):
                entry["target"] = str(raw["target"])
            offset = raw.get("offset")
            if isinstance(offset, dict) and "x" in offset and "y" in offset:
                entry["offset"] = {"x": offset["x"], "y": offset["y"]}
            if action in ("zoomTo", "zoomToFit"):
                zoom = raw.get("zoomLevel")
                if isinstance(zoom, (int, float)):
                    entry["zoomLevel"] = max(1.0, min(2.0, float(zoom)))
            duration = raw.get("duration")
            if isinstance(duration, (int, float)):
                entry["duration"] = int(max(300, min(800, duration)))
            else:
                entry["duration"] = 500
        out.append(entry)
    out.sort(key=lambda e: e["stepIndex"])
    return out


# ---------------------------------------------------------------------------
# Screenshot loading
# ---------------------------------------------------------------------------


def _load_screenshot_b64(spec: dict, step: dict, project_root: Path) -> str | None:
    """Return base64 PNG bytes for a step, or None if no image is available."""
    b64 = step.get("screenshotBase64")
    if b64:
        return b64
    rel = step.get("screenshotPath")
    if not rel:
        return None
    candidates: list[Path] = []
    p = Path(rel)
    if p.is_absolute() and p.is_file():
        candidates.append(p)
    else:
        # Resolve relative to the demo file's parent (where demo.json lives).
        demo_id = spec.get("id", "")
        if demo_id:
            candidates.append(project_root / "demos" / demo_id / rel)
            candidates.append(project_root / "demos" / demo_id / p.name)
        candidates.append(project_root / rel)
    for cand in candidates:
        try:
            if cand.is_file():
                return base64.b64encode(cand.read_bytes()).decode("ascii")
        except OSError:
            continue
    return None


# ---------------------------------------------------------------------------
# Edge TTS
# ---------------------------------------------------------------------------


async def _synthesize_one(
    text: str, voice: str = "en-US-AriaNeural"
) -> tuple[bytes, list]:
    """Synthesize one annotation to MP3 bytes + per-word timestamps via Edge TTS.

    edge-tts streams chunks of two types:
      * ``{"type": "audio", "data": <bytes>}`` — audio bytes
      * ``{"type": "WordBoundary", "offset": <100ns>, "duration": <100ns>,
        "text": <word>}`` — word timing events

    We collect both, convert 100ns ticks to ms (offset / 10000), and return
    ``(audio_bytes, [WordTimestamp, ...])`` so callers can populate
    ``step.voiceoverWords`` for voice-synced camera keyframes.

    Reference: :class:`capturd.walk.schema.WordTimestamp`.
    """
    # Lazy import so the module doesn't break at import time.
    from capturd.walk.schema import WordTimestamp

    try:
        import edge_tts
    except ImportError as exc:  # pragma: no cover - import-time guard
        raise DemoAIError(
            "The 'edge-tts' package is not installed. Run `pip install edge-tts>=6.0`."
        ) from exc
    if not text or not text.strip():
        return b"", []
    com = edge_tts.Communicate(text.strip(), voice, boundary="WordBoundary")
    import io
    sink = io.BytesIO()
    word_ts: list[WordTimestamp] = []
    async for chunk in com.stream():
        if chunk.get("type") == "audio" and chunk.get("data"):
            sink.write(chunk["data"])
        elif chunk.get("type") == "WordBoundary":
            # offset and duration are in 100-nanosecond ticks (HNS).
            # Convert to milliseconds: 1 ms = 10_000 HNS.
            offset_ms = int(chunk["offset"]) // 10000
            dur_ms = int(chunk["duration"]) // 10000
            w = chunk.get("text", "")
            if w:
                word_ts.append(WordTimestamp(
                    word=w,
                    tStartMs=offset_ms,
                    tEndMs=offset_ms + dur_ms,
                ))
    return sink.getvalue(), word_ts


# ---------------------------------------------------------------------------
# DemoAI — main pipeline class
# ---------------------------------------------------------------------------


class DemoAI:
    """Enriches a DemoSpec with AI-generated fields. Async throughout."""

    VISION_CONCURRENCY = 3
    TTS_CONCURRENCY = 2
    DEFAULT_VOICE = "en-US-AriaNeural"

    def __init__(
        self,
        *,
        model_vision: str = _DEFAULT_MODEL,
        model_text: str = _DEFAULT_MODEL,
        voice: str = DEFAULT_VOICE,
        max_tokens_vision: int = 500,
        max_tokens_text: int = 600,
    ) -> None:
        self.model_vision = model_vision
        self.model_text = model_text
        self.voice = voice
        self.max_tokens_vision = max_tokens_vision
        self.max_tokens_text = max_tokens_text

    # ---- public -----------------------------------------------------------

    async def enrich(
        self,
        spec: dict,
        *,
        project_root: Path | None = None,
        progress: Any | None = None,
    ) -> dict:
        """Run the full 5-stage pipeline. Returns the enriched spec (mutated copy)."""
        if not isinstance(spec, dict):
            raise DemoAIError("spec must be a dict")
        steps = spec.get("steps") or []
        if not isinstance(steps, list):
            raise DemoAIError("spec.steps must be a list")
        spec = json.loads(json.dumps(spec))  # deep copy so callers don't mutate
        steps = spec["steps"]
        if not steps:
            ann = _ai_annotations(spec)
            ann["summary"] = ""
            ann["animationTimeline"] = []
            return spec

        client = _build_client()
        proj = Path(project_root) if project_root else Path.cwd()

        t_total = time.perf_counter()

        # Stage 1 — vision annotations
        t1 = time.perf_counter()
        await self._annotate_steps(client, spec, proj)
        self._report(progress, "annotate_steps", time.perf_counter() - t1, len(steps))

        # Stage 4 — cursor paths (deterministic; can run before/after the LLMs)
        t4 = time.perf_counter()
        self._compute_cursor_paths(spec)
        self._report(progress, "cursor_paths", time.perf_counter() - t4, len(steps))

        # Stage 2 — flow summary
        t2 = time.perf_counter()
        await self._generate_summary(client, spec)
        self._report(progress, "summary", time.perf_counter() - t2, 1)

        # Stage 3 — voiceover
        t3 = time.perf_counter()
        await self._synthesize_voiceover(spec)
        self._report(progress, "voiceover", time.perf_counter() - t3, len(steps))

        # Stage 5 — animation timeline
        t5 = time.perf_counter()
        await self._generate_animation_timeline(client, spec)
        self._report(progress, "animation_timeline", time.perf_counter() - t5, len(steps))

        _ai_annotations(spec)["generatedAt"] = _utc_now_iso()
        logger.info(
            "DemoAI.enrich: %d steps enriched in %.1fs",
            len(steps), time.perf_counter() - t_total,
        )
        return spec

    # ---- stage 1: vision ---------------------------------------------------

    async def _annotate_steps(self, client, spec: dict, project_root: Path) -> None:
        steps: list[dict] = spec["steps"]
        sem = asyncio.Semaphore(self.VISION_CONCURRENCY)

        async def one(idx: int, step: dict) -> None:
            if step.get("annotation"):
                return  # already populated — skip
            img = _load_screenshot_b64(spec, step, project_root)
            if not img:
                logger.warning("step %d: no screenshot available, skipping vision", idx)
                return
            target = (step.get("interaction") or {}).get("target") or {}
            hotspot = (step.get("interaction") or {}).get("hotspot") or {}
            prompt = (
                "You are analyzing a screenshot from a product demo recording.\n\n"
                f"Page: {step.get('pageUrl', '')}\n"
                f"Page title: {step.get('pageTitle', '')}\n"
                f"User clicked: {target.get('selector', '?')} "
                f"({target.get('tagName', '?')}, text: \"{target.get('text', '')}\")\n"
                f"Click position: {round(hotspot.get('xPct', 0), 1)}%, "
                f"{round(hotspot.get('yPct', 0), 1)}% of element bounding box\n\n"
                "Describe in ONE sentence (max 18 words) what the user did, "
                "from their perspective. Be specific — name the button/field/text "
                "they clicked. Output ONLY the sentence, no other text."
            )
            async with sem:
                try:
                    text = await _llm_vision(
                        client,
                        model=self.model_vision,
                        prompt=prompt,
                        image_b64=img,
                        max_tokens=self.max_tokens_vision,
                    )
                except Exception as exc:
                    logger.warning("vision failed for step %d: %s", idx, exc)
                    return
            sentence = _first_sentence(text)
            if sentence:
                step["annotation"] = sentence

        await asyncio.gather(*(one(i, s) for i, s in enumerate(steps)))

    # ---- stage 2: summary --------------------------------------------------

    async def _generate_summary(self, client, spec: dict) -> None:
        steps: list[dict] = spec["steps"]
        bullets = []
        for s in steps:
            ann = s.get("annotation") or _fallback_annotation(s)
            bullets.append(f"Step {s.get('index', 0) + 1}: {ann}")
        prompt = (
            "You are writing a product demo narration.\n\n"
            f"Demo: \"{spec.get('name', '')}\"\n"
            f"Goal: \"{spec.get('goal', '')}\"\n"
            f"Start page: {spec.get('startUrl', '')}\n\n"
            "These are the user actions, in order:\n"
            + "\n".join(bullets)
            + "\n\nWrite a 2-3 sentence summary of what this demo flow accomplishes. "
            "Use present tense. Be concise. Output ONLY the summary."
        )
        try:
            text = await _llm_text(
                client,
                model=self.model_text,
                prompt=prompt,
                max_tokens=self.max_tokens_text,
            )
        except Exception as exc:
            logger.warning("summary LLM failed: %s", exc)
            text = ""
        _ai_annotations(spec)["summary"] = text.strip()

    # ---- stage 3: voiceover ------------------------------------------------

    async def _synthesize_voiceover(self, spec: dict) -> None:
        steps: list[dict] = spec["steps"]
        sem = asyncio.Semaphore(self.TTS_CONCURRENCY)

        async def one(idx: int, step: dict) -> None:
            if step.get("voiceoverBase64"):
                return
            text = step.get("annotation") or _fallback_annotation(step)
            if not text:
                return
            async with sem:
                try:
                    audio, words = await _synthesize_one(text, voice=self.voice)
                except Exception as exc:
                    logger.warning("tts failed for step %d: %s", idx, exc)
                    return
            if audio:
                step["voiceoverBase64"] = base64.b64encode(audio).decode("ascii")
            if words:
                step["voiceoverWords"] = [asdict(wt) for wt in words]

        await asyncio.gather(*(one(i, s) for i, s in enumerate(steps)))

    # ---- stage 4: cursor paths --------------------------------------------

    def _compute_cursor_paths(self, spec: dict) -> None:
        steps: list[dict] = spec["steps"]
        prev: dict | None = None
        for s in steps:
            s["cursorPath"] = _compute_cursor_path(prev, s)
            prev = s

    # ---- stage 5: animation timeline --------------------------------------

    async def _generate_animation_timeline(self, client, spec: dict) -> None:
        """Direct the camera. LLM writes an AnimationKeyframe timeline; viewer executes it.

        TODO(W2) — dominance features to add (spine has the contracts; wire them):

        * **Semantic zoom** — anchor keyframes to ``ZoomTarget.selector``
          (element-anchored), not raw pixel coords. Schema type ready.
        * **Spotlight action** — emit ``CameraAction.SPOTLIGHT_ON/OFF`` around
          the focus element so the viewer dims + blurs everything else.
        * **Voice-sync alignment** — read ``step.voiceoverWords`` (W3 output)
          and set ``AnimationKeyframe.tStartMs`` to the word-arrival offset so
          the camera lands on the noun ("click **Buy Now**") in sync with the
          narrator's utterance.
        * **Style tokens** — `snappy` / `smooth` / `professional` / `cinematic`
          change easing curves, hold durations, and spotlight intensity. The
          LLM prompt should switch tone by ``AIAnnotations.style``.
        * **Adaptive per viewport** — smaller zoom on mobile (viewport already
          zoomed); harder zoom on desktop for small targets.

        Reference: :class:`capturd.walk.schema.AnimationKeyframe`,
        :class:`capturd.walk.schema.CameraAction`, and Screen Studio's
        camera-timeline shape for polish inspiration (they don't have
        semantic anchoring — that's ours).
        """
        steps: list[dict] = spec["steps"]
        step_descriptions = []
        for s in steps:
            t = (s.get("interaction") or {}).get("target") or {}
            r = t.get("boundingRect") or {}
            h = (s.get("interaction") or {}).get("hotspot") or {}
            ann = s.get("annotation") or _fallback_annotation(s)
            step_descriptions.append(
                f"step {s.get('index', 0)}: "
                f"selector=\"{t.get('selector', '')}\" "
                f"tag={t.get('tagName', '')} "
                f"text=\"{t.get('text', '')}\" "
                f"rect=({r.get('x', 0)},{r.get('y', 0)},{r.get('width', 0)},{r.get('height', 0)}) "
                f"hotspot=({round(h.get('xPct', 0), 1)},{round(h.get('yPct', 0), 1)}) "
                f"annotation=\"{ann}\""
            )
        viewport = spec.get("viewport") or {"width": 1440, "height": 900}
        prompt = (
            "You are directing a camera for a product demo. Respond with valid "
            "JSON only — do NOT include any reasoning, commentary, or markdown "
            "fences. Begin your reply with '[' and end with ']'.\n\n"
            f"Viewport: {viewport.get('width', 1440)}x{viewport.get('height', 900)}\n"
            f"Steps ({len(steps)} total):\n" + "\n".join(step_descriptions) + "\n\n"
            "For each step, decide what camera action to take. Options:\n"
            '- "zoomTo": zoom into a specific element to highlight it\n'
            '- "panTo": pan the view to center a specific element\n'
            '- "zoomToFit": fit the current focused element comfortably in view\n'
            '- "reset": return to full-page view\n'
            '- null: no camera change\n\n'
            "Output a JSON array. Each entry must have: stepIndex (int), action "
            "(string or null), target (CSS selector, string), offset "
            "{x, y} (hotspot percentages, numbers), zoomLevel (1.0-2.0, only "
            "for zoomTo/zoomToFit), duration (ms, 300-800).\n\n"
            "Example:\n"
            '[{"stepIndex":0,"action":"zoomTo","target":"#get-started",'
            '"offset":{"x":50,"y":50},"zoomLevel":1.5,"duration":600}]\n\n'
            "Output ONLY the JSON array, no other text."
        )
        # Retry up to 3x. The reasoning model occasionally burns its token
        # budget on internal thinking and emits a truncated JSON blob. A second
        # attempt usually succeeds because the warmup cost is paid.
        text = ""
        for attempt in range(3):
            try:
                text = await _llm_text(
                    client,
                    model=self.model_text,
                    prompt=prompt,
                    max_tokens=max(self.max_tokens_text, 1500),
                )
            except Exception as exc:
                logger.warning("timeline LLM failed (attempt %d): %s", attempt + 1, exc)
                continue
            parsed = _extract_timeline_json(text)
            if parsed:
                break
            logger.warning(
                "timeline parse failed (attempt %d) — reply was %d chars; retrying",
                attempt + 1, len(text),
            )
        parsed = _extract_timeline_json(text) if text else None
        timeline = _validate_timeline(parsed or [], len(steps))
        _ai_annotations(spec)["animationTimeline"] = timeline

    # ---- misc --------------------------------------------------------------

    @staticmethod
    def _report(progress: Any | None, stage: str, elapsed_s: float, n: int) -> None:
        if progress is None:
            return
        try:
            progress(stage=stage, elapsed_s=elapsed_s, items=n)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# DemoEnrichManager — thread-pool job tracker used by app.py
# ---------------------------------------------------------------------------


class DemoEnrichManager:
    """Tracks in-flight enrich jobs and writes results back to ``demos/{id}/demo.json``.

    The async pipeline runs in a background daemon thread (mirrors the
    RestedCaptureManager pattern). The HTTP layer can poll the status via
    :meth:`get_status` and read the final spec via :meth:`read_spec`.
    """

    def __init__(self, output_root: Path | None = None, ai: DemoAI | None = None) -> None:
        self.output_root = output_root or (Path.cwd() / "demos")
        self.output_root.mkdir(parents=True, exist_ok=True)
        self._ai = ai or DemoAI()
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    @property
    def ai(self) -> DemoAI:
        return self._ai

    # ---- job lifecycle ----------------------------------------------------

    def submit(self, demo_id: str) -> str:
        """Kick off enrichment for an existing demoId. Returns a jobId."""
        if not demo_id:
            raise DemoAIError("demoId is required")
        demo_path = self._demo_path(demo_id)
        if not demo_path.is_file():
            raise DemoAIError(f"no demo.json at {demo_path}")
        job_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._jobs[job_id] = {
                "jobId": job_id,
                "demoId": demo_id,
                "status": "pending",
                "startedAt": _utc_now_iso(),
                "finishedAt": None,
                "elapsedS": None,
                "error": None,
            }
        t = threading.Thread(
            target=self._run_job,
            args=(job_id, demo_id, demo_path),
            name=f"demo-enrich-{job_id}",
            daemon=True,
        )
        t.start()
        return job_id

    def get_status(self, job_id: str) -> dict:
        with self._lock:
            job = self._jobs.get(job_id)
        if not job:
            raise KeyError(job_id)
        return dict(job)

    def list_jobs(self) -> list[dict]:
        with self._lock:
            return [dict(j) for j in self._jobs.values()]

    def read_spec(self, demo_id: str) -> dict:
        demo_path = self._demo_path(demo_id)
        if not demo_path.is_file():
            raise FileNotFoundError(str(demo_path))
        return json.loads(demo_path.read_text(encoding="utf-8"))

    # ---- internals --------------------------------------------------------

    def _demo_path(self, demo_id: str) -> Path:
        return self.output_root / demo_id / "demo.json"

    def _run_job(self, job_id: str, demo_id: str, demo_path: Path) -> None:
        self._set_status(job_id, status="running")
        t0 = time.perf_counter()
        try:
            spec = json.loads(demo_path.read_text(encoding="utf-8"))
        except Exception as exc:
            self._set_status(job_id, status="failed", error=f"read failed: {exc}",
                             elapsed_s=time.perf_counter() - t0)
            return
        try:
            enriched = asyncio.run(
                self._ai.enrich(spec, project_root=self.output_root.parent)
            )
        except DemoAIError as exc:
            self._set_status(job_id, status="failed", error=str(exc),
                             elapsed_s=time.perf_counter() - t0)
            return
        except Exception as exc:  # noqa: BLE001 — surface any pipeline failure
            logger.exception("enrich job %s crashed", job_id)
            self._set_status(job_id, status="failed", error=f"pipeline error: {exc}",
                             elapsed_s=time.perf_counter() - t0)
            return
        # Atomic-ish write: write to .tmp then rename.
        tmp = demo_path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(enriched, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(tmp, demo_path)
        except Exception as exc:
            self._set_status(job_id, status="failed", error=f"write failed: {exc}",
                             elapsed_s=time.perf_counter() - t0)
            return
        self._set_status(
            job_id, status="done",
            elapsed_s=time.perf_counter() - t0,
            finished_at=True,
        )

    def _set_status(self, job_id: str, *, status: str, error: str | None = None,
                    elapsed_s: float | None = None, finished_at: bool = False) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job["status"] = status
            if error is not None:
                job["error"] = error
            if elapsed_s is not None:
                job["elapsedS"] = round(elapsed_s, 2)
            if finished_at:
                job["finishedAt"] = _utc_now_iso()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _first_sentence(text: str) -> str:
    text = (text or "").strip().strip('"').strip("'")
    if not text:
        return ""
    # Split on the first sentence terminator.
    m = re.search(r"[.!?](?:\s|$)", text)
    if m:
        return text[: m.end()].strip().rstrip(".!?")
    return text


def _fallback_annotation(step: dict) -> str:
    target = (step.get("interaction") or {}).get("target") or {}
    sel = target.get("selector") or target.get("tagName") or "element"
    text = (target.get("text") or "").strip()
    if text:
        return f"Clicked {sel} ({text})."
    return f"Clicked {sel}."


def _ai_annotations(spec: dict) -> dict:
    """Return the spec's aiAnnotations dict, creating it if missing or null."""
    ann = spec.get("aiAnnotations")
    if not isinstance(ann, dict):
        ann = {}
        spec["aiAnnotations"] = ann
    return ann


__all__ = [
    "DemoAI",
    "DemoAIError",
    "DemoEnrichManager",
]