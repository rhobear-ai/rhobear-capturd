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


class DemoAlreadyRunning(DemoForgeError):
    """Raised when an enrichment is already in flight for this demo_id."""


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

    def start_recording(self, payload: dict[str, Any]) -> tuple[Any, str]:
        return self.demo_manager.start(payload)

    def get_recorder(self, session_id: str) -> Any:
        return self.demo_manager.get(session_id)

    def discard_recorder(self, session_id: str) -> None:
        self.demo_manager.discard(session_id)

    def list_sessions(self) -> list[dict[str, Any]]:
        return self.demo_manager.list_sessions()

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
    # Export
    # ------------------------------------------------------------------

    def export_demo(self, demo_id: str, *, fmt: str = "html") -> Path:
        """Render ``demo_id`` as a self-contained HTML viewer file.

        Screenshots are inlined as base64 data URIs so the resulting
        ``export.html`` has zero external dependencies — open it from
        ``file://`` and it works.
        """
        if fmt != "html":
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
        out_path = self.demo_dir(demo_id) / "export.html"
        # Write bytes directly with LF line endings — the template's
        # regex matches LF, and we want the export to be portable across
        # platforms. ``write_text`` would translate to CRLF on Windows.
        out_path.write_bytes(exported.encode("utf-8"))
        return out_path

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
    "DemoAlreadyRunning",
    "DemoCorrupt",
    "DemoForge",
    "DemoForgeError",
    "DemoNotFound",
    "DemoSummary",
    "demos_root",
    "project_root",
    "viewer_template_path",
]