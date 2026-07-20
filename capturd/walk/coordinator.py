"""DemoForge coordinator — shared by app.py and demo_mcp.py.

This module is intentionally thin. The real work lives in:

* ``demo_engine.DemoManager`` — recording sessions + on-disk persistence
* ``demo_ai.DemoAI`` — vision / text / TTS pipeline
* ``demo_ai.DemoEnrichManager`` — async job tracker for the AI pipeline

``DemoForge`` here is the glue: it ties a recorded demo on disk to its
current enrichment status, owns the export step (which isn't part of the
AI pipeline), and provides a single ``edit``/``delete`` API so the MCP
server and the HTTP API stay in sync.

Hard rules:

* All file I/O is rooted at ``<project>/demos/`` — never write elsewhere.
* ``demo_id`` is treated as untrusted input; we strip ``..``, ``/``, and
  ``\\`` to guarantee the path stays inside ``demos/``.
* Exports are written next to the demo JSON so the viewer's relative
  ``screenshotPath`` resolution keeps working.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from capturd.walk.ai_pipeline import (
    DemoAI,
    DemoAIError,
    DemoEnrichManager,
    _build_client,
    _load_screenshot_b64,
    _llm_vision,
    _synthesize_one,
)
from capturd.walk.recorder import DemoManager, run_async

logger = logging.getLogger(__name__)


PROJECT_ROOT_DEFAULT = Path(__file__).resolve().parents[2]
DEMOS_DIR_NAME = "demos"


def project_root() -> Path:
    """Resolve the project root, honoring ``CAPTURD_ROOT`` (or legacy ``SUNSPONGE_ROOT``)."""
    override = (os.environ.get("CAPTURD_ROOT", "") or os.environ.get("SUNSPONGE_ROOT", "")).strip()
    if override:
        return Path(override).expanduser().resolve()
    return PROJECT_ROOT_DEFAULT


def demos_root() -> Path:
    root = project_root() / DEMOS_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def viewer_template_path() -> Path:
    # Template is co-located inside the package so pip-installed users work too.
    return Path(__file__).resolve().parent / "templates" / "viewer.html"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class DemoForgeError(Exception):
    """Base error for DemoForge operations."""


class DemoNotFound(DemoForgeError):
    pass


class DemoCorrupt(DemoForgeError):
    pass



# ---------------------------------------------------------------------------
# DemoForge — the coordinator
# ---------------------------------------------------------------------------


@dataclass
class DemoSummary:
    """Lightweight row for ``demo.list`` responses."""

    demo_id: str
    name: str = ""
    step_count: int = 0
    status: str = "recorded"  # recorded | enriching | enriched | failed
    created_at: str = ""
    error: str | None = None
    has_voiceover: bool = False


_SCRIPT_BLOCK_RE = re.compile(
    r'<script id="demo-data"[^>]*>\s*\n.*?\n\s*</script>',
    re.DOTALL,
)


class DemoForge:
    """Single entry-point used by both the HTTP API and the MCP server."""

    def __init__(
        self,
        *,
        demos_dir: Path | None = None,
        viewer_template: Path | None = None,
        enrich_manager: DemoEnrichManager | None = None,
    ) -> None:
        self.demos_dir = Path(demos_dir) if demos_dir else demos_root()
        self.demos_dir.mkdir(parents=True, exist_ok=True)
        self.viewer_template = Path(viewer_template) if viewer_template else viewer_template_path()
        self.demo_manager = DemoManager(output_root=self.demos_dir)
        self.enrich_manager = enrich_manager or DemoEnrichManager(output_root=self.demos_dir)
        self._lock = threading.Lock()
        # Map demoId → currently-running enrich jobId. Updated by the
        # MCP ``demo.stop`` → enrichment path so we can short-circuit
        # duplicate ``demo.status`` polls and idempotent re-submits.
        self._demo_to_job: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Recording (Phase 1 — delegating to DemoManager)
    # ------------------------------------------------------------------

    def start_recording(self, payload: dict[str, Any]) -> tuple[Any, str, str]:
        return self.demo_manager.start(payload)

    def discard_recorder(self, session_id: str) -> None:
        self.demo_manager.discard(session_id)

    # ------------------------------------------------------------------
    # Disk-backed reads
    # ------------------------------------------------------------------

    def _safe_demo_id(self, raw: str) -> str:
        if not raw or "\x00" in raw:
            raise DemoNotFound(f"invalid demo id: {raw!r}")
        cleaned = raw.strip()
        if ".." in cleaned or "/" in cleaned or "\\" in cleaned:
            raise DemoNotFound(f"invalid demo id: {raw!r}")
        if not cleaned:
            raise DemoNotFound("demo id is empty")
        return cleaned

    def demo_dir(self, demo_id: str) -> Path:
        safe = self._safe_demo_id(demo_id)
        return self.demos_dir / safe

    def demo_json_path(self, demo_id: str) -> Path:
        return self.demo_dir(demo_id) / "demo.json"

    def load_spec(self, demo_id: str) -> dict[str, Any]:
        """Read demo.json from disk. Raises ``DemoNotFound`` / ``DemoCorrupt``."""
        path = self.demo_json_path(demo_id)
        if not path.is_file():
            raise DemoNotFound(f"demo not found: {demo_id}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DemoCorrupt(f"demo.json is not valid JSON: {exc}") from exc

    def save_spec(self, demo_id: str, data: dict[str, Any]) -> Path:
        path = self.demo_json_path(demo_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        return path

    def list_demos(self) -> list[DemoSummary]:
        out: list[DemoSummary] = []
        if not self.demos_dir.is_dir():
            return out
        for entry in sorted(self.demos_dir.iterdir(), key=lambda p: p.name, reverse=True):
            if not entry.is_dir():
                continue
            demo_json = entry / "demo.json"
            if not demo_json.is_file():
                continue
            try:
                data = json.loads(demo_json.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            steps = data.get("steps") or []
            annotations = data.get("aiAnnotations") or {}
            enriched = bool(annotations.get("summary"))
            out.append(
                DemoSummary(
                    demo_id=entry.name,
                    name=data.get("name") or "",
                    step_count=len(steps),
                    status="enriched" if enriched else "recorded",
                    created_at=data.get("createdAt") or "",
                    has_voiceover=any((s.get("voiceoverBase64") for s in steps)),
                )
            )
        return out

    def delete_demo(self, demo_id: str) -> bool:
        d = self.demo_dir(demo_id)
        if not d.is_dir():
            return False
        # Defense in depth — the safe-id check above already guarantees this,
        # but never trust user input reaching a shutil.rmtree call.
        try:
            d.resolve().relative_to(self.demos_dir.resolve())
        except ValueError as exc:
            raise DemoNotFound(f"refusing to delete outside demos/: {demo_id}") from exc
        shutil.rmtree(d)
        with self._lock:
            self._demo_to_job.pop(demo_id, None)
        return True

    # ------------------------------------------------------------------
    # Enrichment (Phase 3)
    # ------------------------------------------------------------------

    def is_enriching(self, demo_id: str) -> bool:
        with self._lock:
            job_id = self._demo_to_job.get(demo_id)
        if not job_id:
            return False
        try:
            job = self.enrich_manager.get_status(job_id)
        except KeyError:
            return False
        return job.get("status") in {"pending", "running"}

    def _resolve_demo_job(self, demo_id: str) -> dict[str, Any] | None:
        """Return the most recent job for ``demo_id`` or ``None``."""
        with self._lock:
            job_id = self._demo_to_job.get(demo_id)
        if job_id:
            try:
                return self.enrich_manager.get_status(job_id)
            except KeyError:
                pass
        # Fallback: scan all jobs (in case the mapping was lost on restart).
        for job in self.enrich_manager.list_jobs():
            if job.get("demoId") == demo_id:
                return job
        return None

    def enrich_demo(self, demo_id: str) -> dict[str, Any]:
        """Kick off (or reuse) the enrichment pipeline for ``demo_id``.

        Returns the job descriptor. If an enrichment is already in flight
        for the same demo, the existing job is returned (idempotent).
        """
        safe = self._safe_demo_id(demo_id)
        with self._lock:
            existing = self._demo_to_job.get(safe)
        if existing:
            try:
                job = self.enrich_manager.get_status(existing)
                if job.get("status") in {"pending", "running"}:
                    return job
            except KeyError:
                pass
        job_id = self.enrich_manager.submit(safe)
        with self._lock:
            self._demo_to_job[safe] = job_id
        return self.enrich_manager.get_status(job_id)

    def get_status(self, demo_id: str) -> dict[str, Any]:
        """Return a uniform status payload for a demo (across job or on-disk)."""
        safe = self._safe_demo_id(demo_id)
        job = self._resolve_demo_job(safe)
        if job is not None:
            steps = self._safe_step_count(safe)
            status_map = {"pending": "enriching", "running": "enriching", "done": "enriched", "failed": "failed"}
            status = status_map.get(job.get("status", ""), "enriching")
            return {
                "demoId": safe,
                "status": status,
                "stepsCompleted": steps if status == "enriched" else 0,
                "totalSteps": steps,
                "jobId": job.get("jobId"),
                "error": job.get("error"),
                "elapsedS": job.get("elapsedS"),
            }
        try:
            data = self.load_spec(safe)
        except DemoNotFound:
            raise
        steps = data.get("steps") or []
        annotations = data.get("aiAnnotations") or {}
        if annotations.get("summary"):
            return {
                "demoId": safe,
                "status": "enriched",
                "stepsCompleted": len(steps),
                "totalSteps": len(steps),
            }
        return {
            "demoId": safe,
            "status": "recorded",
            "stepsCompleted": 0,
            "totalSteps": len(steps),
        }

    def _safe_step_count(self, demo_id: str) -> int:
        try:
            data = self.load_spec(demo_id)
        except DemoForgeError:
            return 0
        return len(data.get("steps") or [])

    # ------------------------------------------------------------------
    # Edit
    # ------------------------------------------------------------------

    async def edit_step(
        self,
        demo_id: str,
        step_index: int,
        *,
        annotation: str | None = None,
        regenerate_voice: bool = False,
    ) -> dict[str, Any]:
        """Edit a step's annotation and optionally re-synthesize its voiceover.

        Returns the updated step (as dict) on success.
        """
        data = self.load_spec(demo_id)
        steps = data.get("steps") or []
        if step_index < 0 or step_index >= len(steps):
            raise DemoForgeError(f"stepIndex {step_index} out of range (have {len(steps)} steps)")
        step = steps[step_index]
        if annotation is not None:
            step["annotation"] = annotation.strip()
        if regenerate_voice:
            text = (step.get("annotation") or "").strip()
            if not text:
                raise DemoForgeError("cannot regenerate voiceover for empty annotation")
            audio, words = await _synthesize_one(text, voice="en-US-AriaNeural")
            if audio:
                step["voiceoverBase64"] = base64.b64encode(audio).decode("ascii")
            if words:
                step["voiceoverWords"] = [asdict(wt) for wt in words]
        self.save_spec(demo_id, data)
        return step

    # ------------------------------------------------------------------
    # Camera timeline helpers (W4 — MCP surface)
    # ------------------------------------------------------------------

    def append_animation_keyframe(
        self,
        demo_id: str,
        step_index: int,
        action: str,
        *,
        target: str | None = None,
        zoom_level: float | None = None,
        duration: int = 500,
        easing: str = "ease-in-out",
    ) -> dict[str, Any]:
        """Append an AnimationKeyframe to the demo's aiAnnotations.animationTimeline."""
        data = self.load_spec(demo_id)
        ann = data.setdefault("aiAnnotations", {})
        timeline = ann.setdefault("animationTimeline", [])
        kf: dict[str, Any] = {
            "stepIndex": step_index,
            "action": action,
            "duration": duration,
            "easing": easing,
        }
        if target is not None:
            kf["target"] = target
        if zoom_level is not None:
            kf["zoomLevel"] = zoom_level
        timeline.append(kf)
        self.save_spec(demo_id, data)
        return kf

    def set_step_overlay(
        self,
        demo_id: str,
        step_index: int,
        text: str,
        position: str = "center",
        style: str = "callout",
    ) -> dict[str, Any]:
        """Set a text callout overlay on a step (free dict key)."""
        data = self.load_spec(demo_id)
        steps = data.get("steps") or []
        if step_index < 0 or step_index >= len(steps):
            raise DemoForgeError(f"stepIndex {step_index} out of range (have {len(steps)} steps)")
        steps[step_index]["overlay"] = {
            "text": text,
            "position": position,
            "style": style,
        }
        self.save_spec(demo_id, data)
        return steps[step_index]["overlay"]

    def reorder_steps(self, demo_id: str, new_order: list[int]) -> dict[str, Any]:
        """Reorder steps in-place and rewrite each step.index to match position."""
        data = self.load_spec(demo_id)
        steps = data.get("steps") or []
        n = len(steps)
        if sorted(new_order) != list(range(n)):
            raise DemoForgeError(
                f"newStepOrder must be a permutation of 0..{n - 1}, got {new_order}"
            )
        reordered = [steps[i] for i in new_order]
        for idx, step in enumerate(reordered):
            step["index"] = idx
        data["steps"] = reordered
        self.save_spec(demo_id, data)
        return {"stepCount": n, "order": new_order}

    def trim_steps(self, demo_id: str, start: int, end: int) -> dict[str, Any]:
        """Keep only steps in [start, end] inclusive and re-index."""
        data = self.load_spec(demo_id)
        steps = data.get("steps") or []
        n = len(steps)
        if start < 0 or end >= n or start > end:
            raise DemoForgeError(
                f"trim range [{start}, {end}] invalid for {n} steps"
            )
        trimmed = steps[start : end + 1]
        for idx, step in enumerate(trimmed):
            step["index"] = idx
        data["steps"] = trimmed
        self.save_spec(demo_id, data)
        return {"originalCount": n, "newCount": len(trimmed), "kept": [start, end]}

    def add_branch(self, demo_id: str, at_step: int, alt_path: list[dict[str, Any]]) -> dict[str, Any]:
        """Record an alternate path from at_step under step.branches."""
        data = self.load_spec(demo_id)
        steps = data.get("steps") or []
        if at_step < 0 or at_step >= len(steps):
            raise DemoForgeError(f"atStep {at_step} out of range (have {len(steps)} steps)")
        branches = steps[at_step].setdefault("branches", [])
        branches.append(alt_path)
        self.save_spec(demo_id, data)
        return {"atStep": at_step, "branchCount": len(branches)}

    # ------------------------------------------------------------------
    # Stylize (W4)
    # ------------------------------------------------------------------

    async def stylize_demo(self, demo_id: str, style: str) -> dict[str, Any]:
        """Update aiAnnotations.style and re-run animation timeline generation."""
        data = self.load_spec(demo_id)
        ann = data.setdefault("aiAnnotations", {})
        ann["style"] = style
        self.save_spec(demo_id, data)

        # Re-run the animation timeline generator with the new style.
        ai = self.enrich_manager.ai
        client = _build_client()
        await ai._generate_animation_timeline(client, data)
        self.save_spec(demo_id, data)
        return {"style": style, "demoId": demo_id}

    # ------------------------------------------------------------------
    # Regenerate (W4)
    # ------------------------------------------------------------------

    async def regenerate_step(
        self,
        demo_id: str,
        step_index: int,
        aspects: list[str],
    ) -> dict[str, Any]:
        """Re-run specific AI-pipeline stages for one step.

        Aspects:
          - "narration" — re-annotate the step via vision LLM
          - "voice"     — re-synthesize voiceover audio for the step
          - "cursor"    — recompute cursor paths for the full spec
          - "zoom"      — regenerate the full animation timeline
        """
        data = self.load_spec(demo_id)
        steps = data.get("steps") or []
        if step_index < 0 or step_index >= len(steps):
            raise DemoForgeError(f"stepIndex {step_index} out of range (have {len(steps)} steps)")
        valid = {"narration", "voice", "cursor", "zoom"}
        unknown = set(aspects) - valid
        if unknown:
            raise DemoForgeError(f"unknown aspects: {sorted(unknown)}; valid: {sorted(valid)}")

        ai = self.enrich_manager.ai
        proj = self.demos_dir.parent
        results: list[str] = []

        if "narration" in aspects:
            client_v = _build_client()
            step = steps[step_index]
            img = _load_screenshot_b64(data, step, proj)
            if img:
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
                try:
                    text = await _llm_vision(
                        client_v,
                        model=ai.model_vision,
                        prompt=prompt,
                        image_b64=img,
                        max_tokens=500,
                    )
                    first = text.strip().split(".")[0].strip() + "." if text.strip() else ""
                    if first:
                        step["annotation"] = first
                        results.append("narration")
                except Exception as exc:
                    logger.warning("regenerate narration failed for step %d: %s", step_index, exc)
            else:
                logger.warning("regenerate narration: no screenshot for step %d", step_index)

        if "voice" in aspects:
            text = (steps[step_index].get("annotation") or "").strip()
            if text:
                audio, words = await _synthesize_one(text, voice=ai.voice)
                if audio:
                    steps[step_index]["voiceoverBase64"] = base64.b64encode(audio).decode("ascii")
                    if words:
                        steps[step_index]["voiceoverWords"] = [asdict(wt) for wt in words]
                    results.append("voice")

        if "cursor" in aspects:
            ai._compute_cursor_paths(data)
            results.append("cursor")

        if "zoom" in aspects:
            client_z = _build_client()
            await ai._generate_animation_timeline(client_z, data)
            results.append("zoom")

        self.save_spec(demo_id, data)
        return {"regenerated": results, "demoId": demo_id, "stepIndex": step_index}

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_demo(self, demo_id: str, *, fmt: str = "html") -> Path:
        """Render ``demo_id`` as an HTML viewer, MP4 video, or GIF.

        ``html`` — self-contained viewer file. Screenshots are inlined as
        base64 data URIs so ``export.html`` has zero external dependencies.

        ``mp4`` / ``gif`` — the deterministic export renderer inside the
        viewer is driven frame-by-frame in headless Chromium and encoded by
        ffmpeg; voiceover audio is muxed at the timeline offsets.
        """
        fmt = (fmt or "html").lower()
        if fmt not in {"html", "mp4", "gif"}:
            raise DemoForgeError(f"unsupported export format: {fmt!r}")
        if not self.viewer_template.is_file():
            raise DemoForgeError(f"viewer template not found: {self.viewer_template}")
        data = self.load_spec(demo_id)
        self._inline_screenshots(data, demo_id)
        template = self.viewer_template.read_text(encoding="utf-8")
        if not _SCRIPT_BLOCK_RE.search(template):
            raise DemoForgeError("viewer template missing <script id=\"demo-data\"> block")
        spec_json = json.dumps(data, indent=2)
        new_block = f'<script id="demo-data" type="application/json">\n{spec_json}\n  </script>'
        # IMPORTANT: pass the replacement through a lambda so ``re.sub`` does
        # NOT interpret backslash escapes (``\1``, ``\g<name>``, etc.) inside
        # the JSON. Without this the spec's screenShotPath values get mangled.
        exported = _SCRIPT_BLOCK_RE.sub(lambda _m: new_block, template, count=1)
        html_path = self.demo_dir(demo_id) / "export.html"
        # Write bytes directly with LF line endings — the template's
        # regex matches LF, and we want the export to be portable across
        # platforms. ``write_text`` would translate to CRLF on Windows.
        html_path.write_bytes(exported.encode("utf-8"))
        if fmt == "html":
            return html_path

        from capturd.walk.exporter import DemoExportError, export_video

        out_path = self.demo_dir(demo_id) / f"walkthrough.{fmt}"
        try:
            return export_video(data, html_path, out_path, fmt=fmt)
        except DemoExportError as exc:
            raise DemoForgeError(str(exc)) from exc

    def _inline_screenshots(self, data: dict[str, Any], demo_id: str) -> None:
        """Mutate ``data`` so each step has ``screenshotBase64`` populated."""
        d = self.demo_dir(demo_id)
        steps = data.get("steps") or []
        for step in steps:
            if step.get("screenshotBase64"):
                continue
            rel = step.get("screenshotPath")
            if not rel:
                continue
            # Recorder stores relative paths like ``demos/{id}/step_NNN.png``.
            # Resolve both that and the basename inside our demo_dir.
            candidates: list[Path] = []
            try:
                if (d / Path(rel).name).is_file():
                    candidates.append(d / Path(rel).name)
            except (OSError, ValueError):
                pass
            p_rel = Path(rel)
            if p_rel.is_absolute() and p_rel.is_file():
                candidates.append(p_rel)
            for cand in candidates:
                try:
                    step["screenshotBase64"] = base64.b64encode(cand.read_bytes()).decode("ascii")
                    break
                except OSError:
                    continue


__all__ = [
    "DEMOS_DIR_NAME",
    "DemoCorrupt",
    "DemoForge",
    "DemoForgeError",
    "DemoNotFound",
    "DemoSummary",
    "demos_root",
    "project_root",
    "viewer_template_path",
]