"""DemoForge MCP server — exposes the demo + capture pipeline as 19 MCP tools.

Demo tools (``demo.*`` namespace):

* ``demo.record``     — open a headful browser and start capturing clicks
* ``demo.stop``       — close the browser, persist the spec, kick off AI enrichment
* ``demo.status``     — check pipeline progress for a demo
* ``demo.list``       — enumerate every recorded demo on disk
* ``demo.edit``       — rewrite a step's annotation and optionally re-synthesize voiceover
* ``demo.delete``     — remove a demo and its files
* ``demo.export``     — render a standalone HTML viewer
* ``demo.zoom``       — append a ZOOM_TO camera keyframe
* ``demo.pan``        — append a PAN_TO camera keyframe
* ``demo.hold``       — append a HOLD camera keyframe
* ``demo.spotlight``  — append SPOTLIGHT_ON or SPOTLIGHT_OFF
* ``demo.overlay``    — add a text callout overlay to a step
* ``demo.reorder``    — reorder steps and rewrite indexes
* ``demo.trim``       — remove steps outside a range
* ``demo.branch``     — record an alternate path from a step
* ``demo.stylize``    — change camera style and re-run timeline generation
* ``demo.regenerate`` — re-run AI pipeline stages for a step

Capture tools (``capture.*`` namespace):

* ``capture.crawl``   — crawl a site and capture rested-state stills
* ``capture.rested``  — capture rested-state stills for a list of URLs

Transport: stdio JSON-RPC 2.0 (the FastMCP default). The server is launched
via ``python -m capturd.mcp.server``.

Hard constraints (from phase4-brief.md):

* Reuse :class:`DemoForge` from :mod:`capturd.walk.coordinator`. No duplication.
* ``demo.stop`` must be idempotent — calling it twice returns the demo's
  current status instead of starting a second enrichment.
* ``demo.edit`` returns the updated step as ``{ok, step}``.
* ``demo.export`` writes a single self-contained HTML file (no external
  assets); screenshots are inlined as base64.
* All tool errors are surfaced as plain strings (no stack traces).

Run with::

    python -m capturd.mcp.server

Or, from inside pi, attach it as an MCP server via the standard
``fastmcp`` stdio transport.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from capturd.walk.recorder import DemoRecorderError
from capturd.walk.coordinator import (
    DemoForge,
    DemoForgeError,
    DemoNotFound,
    DEMOS_DIR_NAME,
    demos_root,
)

logger = logging.getLogger("capturd.mcp.server")


# ---------------------------------------------------------------------------
# Server bootstrap
# ---------------------------------------------------------------------------

_INSTRUCTIONS = (
    "DemoForge MCP server. Use the demo.* tools to record product flows, "
    "enrich them with AI (annotations, voiceover, animation timeline), and "
    "export the result as a standalone HTML viewer. Recording tools open a "
    "headful Playwright window — make sure the host has a graphical session "
    "(or a virtual display) when calling demo.record. Pipeline runs "
    "asynchronously; poll demo.status until 'enriched' before exporting."
)


def _build_server(forge: DemoForge | None = None) -> FastMCP:
    """Construct the MCP server with all 7 tools wired up."""
    forge = forge if forge is not None else DemoForge()
    mcp = FastMCP(
        name="DemoForge",
        instructions=_INSTRUCTIONS,
        version="0.1.0",
    )

    # ---- demo.record ------------------------------------------------------

    # sessionId → {"recorder", "thread", "mode"} for demo.stop.
    _live_sessions: dict[str, dict[str, Any]] = {}

    @mcp.tool(
        name="demo.record",
        description=(
            "Start recording a product demo. mode='agent' (the headless "
            "'prompt in, demo out' flow): an LLM drives the browser toward "
            "`goal`, clicking through the product on its own; the session "
            "finishes by itself — poll demo.stop with the returned sessionId "
            "to persist + enrich. Requires an OpenAI-compatible gateway "
            "(RHOBEAR_GW_API_KEY / RHOBEAR_GW_BASE_URL env on the host). "
            "mode='human' opens a headful browser window on the host for a "
            "person to click through, then call demo.stop."
        ),
        timeout=60.0,
    )
    async def demo_record(
        url: str,
        name: str,
        goal: str = "",
        mode: str = "agent",
        viewport: dict[str, int] | None = None,
        workflow: bool = False,
        voice: bool = False,
    ) -> dict[str, Any]:
        if not url:
            raise ValueError("url is required")
        if not name:
            raise ValueError("name is required")
        if mode not in ("agent", "human"):
            raise ValueError(f"mode must be 'agent' or 'human', got {mode!r}")
        if mode == "agent" and not goal:
            raise ValueError("agent mode needs a goal — tell the agent what flow to demonstrate")
        try:
            recorder, session_id, mode = forge.start_recording(
                {
                    "url": url,
                    "name": name,
                    "goal": goal,
                    "mode": mode,
                    "viewport": viewport or {"width": 1440, "height": 900},
                    "workflow": workflow,
                    "voice": voice or workflow,
                }
            )
        except DemoRecorderError as exc:
            raise ValueError(str(exc)) from exc

        if mode == "agent":
            # Agent mode drives itself to completion in its own loop; the
            # thread wrapper marks the recorder finished either way so
            # demo.stop never hangs on a crashed run.
            def _spawn_agent() -> None:
                try:
                    asyncio.run(recorder.agent_record())
                except Exception:
                    logger.exception("agent recorder crashed for %s", session_id)
                finally:
                    recorder.finished.set()

            thread = threading.Thread(
                target=_spawn_agent,
                name=f"demo-agent-{session_id}",
                daemon=True,
            )
        else:
            # Human mode: the recorder needs a live event loop for the whole
            # session. Park the loop with run_forever(); recorder.stop()
            # (called by demo.stop) schedules teardown onto it and then
            # stops it — never close the loop out from under the browser.
            def _spawn_human() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(recorder.start())
                    loop.run_forever()
                except Exception:
                    logger.exception("recorder thread crashed for %s", session_id)
                finally:
                    recorder.finished.set()
                    try:
                        loop.close()
                    except Exception:
                        pass

            thread = threading.Thread(
                target=_spawn_human,
                name=f"demo-recorder-{session_id}",
                daemon=True,
            )
        thread.start()
        _live_sessions[session_id] = {
            "recorder": recorder,
            "thread": thread,
            "mode": mode,
        }
        return {
            "sessionId": session_id,
            "mode": mode,
            "message": (
                "Agent recording started — it will click through the flow on "
                "its own. Call demo.stop with this sessionId to wait for it "
                "to finish and start AI enrichment."
                if mode == "agent"
                else "Recording started. Interact with the browser window, "
                     "then call demo.stop with this sessionId."
            ),
        }

    # ---- demo.stop --------------------------------------------------------

    @mcp.tool(
        name="demo.stop",
        description=(
            "Stop a recording session, persist the DemoSpec to disk, and "
            "kick off the AI enrichment pipeline. For agent-mode sessions "
            "this waits for the agent to finish its click-through first. "
            "Returns the demoId and the initial pipeline status. Idempotent "
            "— calling demo.stop twice on the same sessionId returns the "
            "current status without restarting enrichment."
        ),
        timeout=360.0,
    )
    async def demo_stop(session_id: str) -> dict[str, Any]:
        if not session_id:
            raise ValueError("sessionId is required")

        live = _live_sessions.get(session_id)
        if live is None:
            # Idempotent path: session already stopped and discarded — if the
            # demo made it to disk, report its current status instead of
            # erroring on the retry.
            try:
                status = forge.get_status(session_id)
            except (DemoNotFound, DemoForgeError):
                raise ValueError(f"unknown recording session: {session_id}")
            return status

        recorder = live["recorder"]
        mode = live["mode"]

        if mode == "agent":
            # The agent session drives itself to completion and writes
            # demo.json on its own — demo.stop just waits for it (off the
            # event loop so other MCP calls stay responsive). Never signal
            # _stopped here: that would abort the click-through mid-flow.
            def _await_agent() -> None:
                live["thread"].join(timeout=300.0)

            await asyncio.to_thread(_await_agent)
            if live["thread"].is_alive():
                raise RuntimeError(
                    "agent recording did not finish within 300s — "
                    "poll demo.stop again"
                )
            spec = recorder.get_spec()
        else:
            # Human mode: bridge into the recorder's parked loop.
            try:
                spec = await asyncio.to_thread(recorder.stop)
            except DemoRecorderError as exc:
                raise ValueError(str(exc)) from exc
            except Exception as exc:  # pragma: no cover - defensive
                forge.discard_recorder(session_id)
                _live_sessions.pop(session_id, None)
                raise RuntimeError(f"failed to stop recorder: {exc}") from exc

        step_count = len(spec.steps)
        forge.discard_recorder(session_id)
        _live_sessions.pop(session_id, None)

        if step_count == 0:
            return {
                "demoId": spec.id,
                "stepCount": 0,
                "status": "failed",
                "error": (
                    "recording captured zero steps — for agent mode check "
                    "that the gateway env (RHOBEAR_GW_API_KEY) is set on the "
                    "MCP host and see the server log for the agent trace"
                ),
            }

        # Kick off enrichment (returns immediately; runs in a daemon thread).
        try:
            job = forge.enrich_demo(spec.id)
        except DemoForgeError as exc:
            raise RuntimeError(f"failed to start enrichment: {exc}") from exc

        return {
            "demoId": spec.id,
            "stepCount": step_count,
            "status": _enrich_status_to_spec(job.get("status")),
            "jobId": job.get("jobId"),
            "summary": _summary_hint(spec),
        }

    # ---- demo.status ------------------------------------------------------

    @mcp.tool(
        name="demo.status",
        description=(
            "Check enrichment progress for a demo. Returns the current "
            "status (recorded / enriching / enriched / failed) and "
            "step counts. Use the demoId returned by demo.stop."
        ),
        timeout=15.0,
    )
    async def demo_status(demo_id: str) -> dict[str, Any]:
        if not demo_id:
            raise ValueError("demoId is required")
        try:
            return forge.get_status(demo_id)
        except DemoNotFound:
            raise ValueError(f"demo not found: {demo_id}")

    # ---- demo.list --------------------------------------------------------

    @mcp.tool(
        name="demo.list",
        description=(
            "List every recorded demo on disk. Each entry includes "
            "demoId, name, stepCount, status, and createdAt. Use this to "
            "discover demos before exporting or editing."
        ),
        timeout=15.0,
    )
    async def demo_list() -> dict[str, Any]:
        summaries = forge.list_demos()
        return {
            "demos": [
                {
                    "demoId": s.demo_id,
                    "name": s.name,
                    "stepCount": s.step_count,
                    "status": s.status,
                    "createdAt": s.created_at,
                    "hasVoiceover": s.has_voiceover,
                }
                for s in summaries
            ],
            "count": len(summaries),
        }

    # ---- demo.edit --------------------------------------------------------

    @mcp.tool(
        name="demo.edit",
        description=(
            "Edit a step's annotation and optionally regenerate its "
            "voiceover audio. Pass only the fields you want to change — "
            "omitting annotation keeps the existing text, omitting "
            "regenerateVoice leaves the audio untouched. Returns the "
            "updated step."
        ),
        timeout=60.0,
    )
    async def demo_edit(
        demo_id: str,
        step_index: int,
        annotation: str | None = None,
        regenerate_voice: bool = False,
    ) -> dict[str, Any]:
        if not demo_id:
            raise ValueError("demoId is required")
        if not isinstance(step_index, int) or step_index < 0:
            raise ValueError("stepIndex must be a non-negative integer")
        if annotation is None and not regenerate_voice:
            raise ValueError("nothing to edit — provide annotation or set regenerateVoice=true")
        try:
            step = await forge.edit_step(
                demo_id,
                step_index,
                annotation=annotation,
                regenerate_voice=regenerate_voice,
            )
        except DemoNotFound:
            raise ValueError(f"demo not found: {demo_id}")
        except DemoForgeError as exc:
            raise ValueError(str(exc))
        return {"ok": True, "step": step}

    # ---- demo.delete ------------------------------------------------------

    @mcp.tool(
        name="demo.delete",
        description=(
            "Delete a demo and all its files from disk. Returns {ok: bool}. "
            "Cannot be undone — make sure the demo is exported first if "
            "you might need it again."
        ),
        timeout=15.0,
    )
    async def demo_delete(demo_id: str) -> dict[str, Any]:
        if not demo_id:
            raise ValueError("demoId is required")
        try:
            ok = forge.delete_demo(demo_id)
        except DemoNotFound as exc:
            raise ValueError(str(exc))
        if not ok:
            raise ValueError(f"demo not found: {demo_id}")
        return {"ok": True, "demoId": demo_id}

    # ---- demo.export ------------------------------------------------------

    @mcp.tool(
        name="demo.export",
        description=(
            "Export a demo. format='html' renders a self-contained "
            "interactive viewer (screenshots inlined as base64 — open from "
            "file:// anywhere). format='mp4' renders the full Supademo-style "
            "walkthrough VIDEO — smooth zoom/pan camera, flying cursor, "
            "spotlight, captions, voiceover audio track — via the "
            "deterministic frame renderer + ffmpeg. format='gif' is the "
            "same video as an animated GIF. Video rendering takes roughly "
            "2-4x the demo duration; the call blocks until the file is "
            "written and returns its absolute path."
        ),
        timeout=900.0,
    )
    async def demo_export(demo_id: str, format: str = "html") -> dict[str, Any]:
        if not demo_id:
            raise ValueError("demoId is required")
        fmt = (format or "html").lower()
        if fmt not in {"html", "mp4", "gif"}:
            raise ValueError(f"format must be one of html, mp4, gif — got {format!r}")
        try:
            out_path = await asyncio.to_thread(forge.export_demo, demo_id, fmt=fmt)
        except DemoNotFound:
            raise ValueError(f"demo not found: {demo_id}")
        except DemoForgeError as exc:
            raise ValueError(str(exc))
        return {
            "path": str(out_path.resolve()),
            "demoId": demo_id,
            "format": fmt,
        }

    # ---- W4: demo.zoom ---------------------------------------------------

    @mcp.tool(
        name="demo.zoom",
        description=(
            "Append a ZOOM_TO camera keyframe to the demo's animation timeline. "
            "The viewer uses this to zoom into a target element at the given step."
        ),
        timeout=15.0,
    )
    async def demo_zoom(
        demo_id: str,
        step_index: int,
        target: str,
        level: float = 1.5,
        duration: int = 500,
        easing: str = "ease-in-out",
    ) -> dict[str, Any]:
        if not demo_id:
            raise ValueError("demoId is required")
        if not isinstance(step_index, int) or step_index < 0:
            raise ValueError("stepIndex must be a non-negative integer")
        try:
            kf = forge.append_animation_keyframe(
                demo_id, step_index, "zoomTo",
                target=target, zoom_level=level,
                duration=duration, easing=easing,
            )
        except DemoNotFound:
            raise ValueError(f"demo not found: {demo_id}")
        return {"ok": True, "keyframe": kf}

    # ---- W4: demo.pan ----------------------------------------------------

    @mcp.tool(
        name="demo.pan",
        description=(
            "Append a PAN_TO camera keyframe to move the view from one "
            "selector to another at the given step."
        ),
        timeout=15.0,
    )
    async def demo_pan(
        demo_id: str,
        step_index: int,
        from_selector: str,
        to_selector: str,
        duration: int = 500,
    ) -> dict[str, Any]:
        if not demo_id:
            raise ValueError("demoId is required")
        if not isinstance(step_index, int) or step_index < 0:
            raise ValueError("stepIndex must be a non-negative integer")
        try:
            kf = forge.append_animation_keyframe(
                demo_id, step_index, "panTo",
                target=to_selector, duration=duration,
            )
        except DemoNotFound:
            raise ValueError(f"demo not found: {demo_id}")
        return {"ok": True, "keyframe": kf, "from": from_selector, "to": to_selector}

    # ---- W4: demo.hold ---------------------------------------------------

    @mcp.tool(
        name="demo.hold",
        description=(
            "Append a HOLD camera keyframe — the camera stays still for "
            "the specified number of milliseconds at this step."
        ),
        timeout=15.0,
    )
    async def demo_hold(
        demo_id: str,
        step_index: int,
        ms: int,
    ) -> dict[str, Any]:
        if not demo_id:
            raise ValueError("demoId is required")
        if not isinstance(step_index, int) or step_index < 0:
            raise ValueError("stepIndex must be a non-negative integer")
        if not isinstance(ms, int) or ms <= 0:
            raise ValueError("ms must be a positive integer")
        try:
            kf = forge.append_animation_keyframe(
                demo_id, step_index, "hold", duration=ms,
            )
        except DemoNotFound:
            raise ValueError(f"demo not found: {demo_id}")
        return {"ok": True, "keyframe": kf}

    # ---- W4: demo.spotlight ----------------------------------------------

    @mcp.tool(
        name="demo.spotlight",
        description=(
            "Add a SPOTLIGHT_ON or SPOTLIGHT_OFF camera keyframe. When on, "
            "the viewer dims everything except the target element."
        ),
        timeout=15.0,
    )
    async def demo_spotlight(
        demo_id: str,
        step_index: int,
        on: bool,
        target: str,
    ) -> dict[str, Any]:
        if not demo_id:
            raise ValueError("demoId is required")
        if not isinstance(step_index, int) or step_index < 0:
            raise ValueError("stepIndex must be a non-negative integer")
        action = "spotlightOn" if on else "spotlightOff"
        try:
            kf = forge.append_animation_keyframe(
                demo_id, step_index, action, target=target,
            )
        except DemoNotFound:
            raise ValueError(f"demo not found: {demo_id}")
        return {"ok": True, "keyframe": kf}

    # ---- W4: demo.overlay ------------------------------------------------

    @mcp.tool(
        name="demo.overlay",
        description=(
            "Add a text callout overlay to a step. The viewer renders this "
            "as a positioned annotation on top of the screenshot."
        ),
        timeout=15.0,
    )
    async def demo_overlay(
        demo_id: str,
        step_index: int,
        text: str,
        position: str = "center",
        style: str = "callout",
    ) -> dict[str, Any]:
        if not demo_id:
            raise ValueError("demoId is required")
        if not isinstance(step_index, int) or step_index < 0:
            raise ValueError("stepIndex must be a non-negative integer")
        if not text or not text.strip():
            raise ValueError("text is required")
        valid_positions = {"top-left", "top-right", "bottom-left", "bottom-right", "center"}
        if position not in valid_positions:
            raise ValueError(f"position must be one of {sorted(valid_positions)}")
        try:
            overlay = forge.set_step_overlay(demo_id, step_index, text.strip(), position, style)
        except DemoNotFound:
            raise ValueError(f"demo not found: {demo_id}")
        except DemoForgeError as exc:
            raise ValueError(str(exc))
        return {"ok": True, "overlay": overlay}

    # ---- W4: demo.reorder ------------------------------------------------

    @mcp.tool(
        name="demo.reorder",
        description=(
            "Reorder demo steps. newStepOrder must be a permutation of "
            "0..N-1. Step indexes are rewritten to match their new position."
        ),
        timeout=15.0,
    )
    async def demo_reorder(
        demo_id: str,
        new_step_order: list[int],
    ) -> dict[str, Any]:
        if not demo_id:
            raise ValueError("demoId is required")
        if not isinstance(new_step_order, list) or not new_step_order:
            raise ValueError("newStepOrder must be a non-empty list of integers")
        try:
            result = forge.reorder_steps(demo_id, new_step_order)
        except DemoNotFound:
            raise ValueError(f"demo not found: {demo_id}")
        except DemoForgeError as exc:
            raise ValueError(str(exc))
        return {"ok": True, **result}

    # ---- W4: demo.trim ---------------------------------------------------

    @mcp.tool(
        name="demo.trim",
        description=(
            "Remove steps outside the range [startStep, endStep] inclusive. "
            "Remaining steps are re-indexed from 0."
        ),
        timeout=15.0,
    )
    async def demo_trim(
        demo_id: str,
        start_step: int,
        end_step: int,
    ) -> dict[str, Any]:
        if not demo_id:
            raise ValueError("demoId is required")
        if not isinstance(start_step, int) or start_step < 0:
            raise ValueError("startStep must be a non-negative integer")
        if not isinstance(end_step, int) or end_step < 0:
            raise ValueError("endStep must be a non-negative integer")
        try:
            result = forge.trim_steps(demo_id, start_step, end_step)
        except DemoNotFound:
            raise ValueError(f"demo not found: {demo_id}")
        except DemoForgeError as exc:
            raise ValueError(str(exc))
        return {"ok": True, **result}

    # ---- W4: demo.branch -------------------------------------------------

    @mcp.tool(
        name="demo.branch",
        description=(
            "Record an alternate path branching from atStep. altPath is a "
            "list of DemoStep-shaped dicts representing the branched flow."
        ),
        timeout=15.0,
    )
    async def demo_branch(
        demo_id: str,
        at_step: int,
        alt_path: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not demo_id:
            raise ValueError("demoId is required")
        if not isinstance(at_step, int) or at_step < 0:
            raise ValueError("atStep must be a non-negative integer")
        if not isinstance(alt_path, list):
            raise ValueError("altPath must be a list")
        try:
            result = forge.add_branch(demo_id, at_step, alt_path)
        except DemoNotFound:
            raise ValueError(f"demo not found: {demo_id}")
        except DemoForgeError as exc:
            raise ValueError(str(exc))
        return {"ok": True, **result}

    # ---- W4: demo.stylize ------------------------------------------------

    @mcp.tool(
        name="demo.stylize",
        description=(
            "Change the demo's camera style and regenerate the animation "
            "timeline. Valid styles: snappy, smooth, professional, cinematic."
        ),
        timeout=60.0,
    )
    async def demo_stylize(
        demo_id: str,
        style: str,
    ) -> dict[str, Any]:
        if not demo_id:
            raise ValueError("demoId is required")
        valid = {"snappy", "smooth", "professional", "cinematic"}
        if style not in valid:
            raise ValueError(f"style must be one of {sorted(valid)}, got {style!r}")
        try:
            result = await forge.stylize_demo(demo_id, style)
        except DemoNotFound:
            raise ValueError(f"demo not found: {demo_id}")
        except DemoForgeError as exc:
            raise ValueError(str(exc))
        return {"ok": True, **result}

    # ---- W4: demo.regenerate ---------------------------------------------

    @mcp.tool(
        name="demo.regenerate",
        description=(
            "Re-run AI pipeline stages for a single step. aspects can include "
            "any subset of: narration (vision annotation), voice (TTS audio), "
            "cursor (bezier path), zoom (animation timeline)."
        ),
        timeout=120.0,
    )
    async def demo_regenerate(
        demo_id: str,
        step_index: int,
        aspects: list[str],
    ) -> dict[str, Any]:
        if not demo_id:
            raise ValueError("demoId is required")
        if not isinstance(step_index, int) or step_index < 0:
            raise ValueError("stepIndex must be a non-negative integer")
        if not isinstance(aspects, list) or not aspects:
            raise ValueError("aspects must be a non-empty list")
        try:
            result = await forge.regenerate_step(demo_id, step_index, aspects)
        except DemoNotFound:
            raise ValueError(f"demo not found: {demo_id}")
        except DemoForgeError as exc:
            raise ValueError(str(exc))
        return {"ok": True, **result}

    # ---- W4: capture.crawl -----------------------------------------------

    @mcp.tool(
        name="capture.crawl",
        description=(
            "Crawl a website and capture rested-state stills across viewports "
            "and color schemes. Returns jobId for tracking progress."
        ),
        timeout=30.0,
    )
    async def capture_crawl(
        url: str,
        viewports: list[str] | None = None,
        schemes: list[str] | None = None,
        format: str = "png",
        out_dir: str = "",
    ) -> dict[str, Any]:
        if not url:
            raise ValueError("url is required")
        try:
            from capturd.shots.capture import RestedCaptureManager
        except ImportError as exc:
            raise RuntimeError(f"capture module not available: {exc}") from exc

        payload: dict[str, Any] = {
            "crawl_url": url,
            "crawl": True,
            "viewports": viewports or ["desktop", "mobile"],
            "schemes": schemes or ["light", "dark"],
            "format": format or "png",
            "name": url,
        }
        if out_dir:
            payload["export_dir"] = out_dir
            payload["export_mode"] = "folder"

        manager = RestedCaptureManager()
        job = manager.start(payload)
        return {
            "job_id": job["job_id"],
            "output_dir": job.get("output_dir") or job.get("work_dir", ""),
            "count": job["total"],
            "status": job["status"],
        }

    # ---- W4: capture.rested ----------------------------------------------

    @mcp.tool(
        name="capture.rested",
        description=(
            "Capture rested-state stills for a list of URLs (no crawl). "
            "Returns jobId for tracking progress."
        ),
        timeout=30.0,
    )
    async def capture_rested(
        urls: list[str],
        viewports: list[str] | None = None,
        schemes: list[str] | None = None,
        format: str = "png",
        out_dir: str = "",
    ) -> dict[str, Any]:
        if not urls:
            raise ValueError("urls is required and must be non-empty")
        try:
            from capturd.shots.capture import RestedCaptureManager
        except ImportError as exc:
            raise RuntimeError(f"capture module not available: {exc}") from exc

        payload: dict[str, Any] = {
            "urls": urls,
            "viewports": viewports or ["desktop", "mobile"],
            "schemes": schemes or ["light", "dark"],
            "format": format or "png",
            "name": urls[0] if urls else "capture",
        }
        if out_dir:
            payload["export_dir"] = out_dir
            payload["export_mode"] = "folder"

        manager = RestedCaptureManager()
        job = manager.start(payload)
        return {
            "job_id": job["job_id"],
            "output_dir": job.get("output_dir") or job.get("work_dir", ""),
            "count": job["total"],
            "status": job["status"],
        }

    # Stash the forge on the server so tests / integration helpers can grab
    # it without rebuilding the server from scratch.
    mcp.state = {  # type: ignore[attr-defined]
        "forge": forge,
    }
    return mcp


def _enrich_status_to_spec(internal: str | None) -> str:
    """Translate ``DemoEnrichManager`` job statuses to the brief's vocabulary."""
    if internal in {"pending", "running"}:
        return "enriching"
    if internal == "done":
        return "enriched"
    if internal == "failed":
        return "failed"
    return "recorded"


def _summary_hint(spec: Any) -> str:
    """Build a placeholder summary line for the demo.stop response."""
    if spec.goal:
        return f"AI pipeline started; will summarise as: {spec.goal}"
    return "AI pipeline started; will summarise the recorded flow."


# ---------------------------------------------------------------------------
# Module entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Run the MCP server on stdio. argv is accepted for CLI-dispatcher compat and ignored."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    server = _build_server()
    # stdio transport is the MCP default for desktop agents; the brief
    # explicitly calls this out ("MCP server must use stdio transport").
    server.run(transport="stdio")
    return 0


__all__ = [
    "DEMOS_DIR_NAME",
    "_build_server",
    "demos_root",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())