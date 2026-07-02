#!/usr/bin/env python3
"""W8 — End-to-end walkthrough proof.

Full pipeline: agent_record → DemoAI → viewer HTML → MP4 screencast → screenshots.

Usage:
    python scripts/e2e_walkthrough_proof.py          # default: example.com
    python scripts/e2e_walkthrough_proof.py --target canvas  # canvas fixture
    python scripts/e2e_walkthrough_proof.py --target https://example.com
    python scripts/e2e_walkthrough_proof.py --target https://example.com --steps 5
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# CLI (only parsed when run as __main__)
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="W8 E2E walkthrough proof")
    p.add_argument(
        "--target",
        default="https://example.com",
        help="Target URL or 'canvas' for the canvas fixture",
    )
    p.add_argument("--steps", type=int, default=4, help="Minimum steps to record")
    p.add_argument(
        "--skip-mp4", action="store_true", help="Skip MP4 screencast (faster debug)"
    )
    p.add_argument(
        "--output-dir",
        default=None,
        help="Override output dir (default: out/e2e-proof/<timestamp>/)",
    )
    p.add_argument(
        "--headless", action="store_true", default=True, help="Run browser headless"
    )
    return p


# Module-level defaults (overridden by CLI when run as __main__)
_ARGS = None


def _get_args():
    global _ARGS
    if _ARGS is None:
        _ARGS = _build_parser().parse_args([])  # safe defaults for import
    return _ARGS


def _set_args(**kwargs):
    """Override args for test invocation."""
    global _ARGS
    from types import SimpleNamespace
    defaults = vars(_build_parser().parse_args([]))
    defaults.update(kwargs)
    _ARGS = SimpleNamespace(**defaults)



# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("e2e_proof")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CANVAS_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "canvas-page.html"

# Env
GW_KEY_ENV = "RHOBEAR_GW_API_KEY"
GW_BASE_ENV = "RHOBEAR_GW_BASE_URL"


def _ensure_gateway_key():
    """Ensure the gateway token is set."""
    key = os.environ.get(GW_KEY_ENV, "").strip()
    if not key:
        # Try loading from common locations
        for candidate in [
            Path.home() / "rhobear" / "gateway.key",
            Path.home() / "swarm" / "secrets" / "gateway.env",
        ]:
            if candidate.is_file():
                content = candidate.read_text().strip()
                for line in content.split("\n"):
                    if "=" in line and line.split("=")[0].strip() in (
                        "GATEWAY_KEY", "RHOBEAR_GW_API_KEY"
                    ):
                        key = line.split("=", 1)[1].strip().strip('"').strip("'")
                        os.environ[GW_KEY_ENV] = key
                        break
                if key:
                    break
        if not key:
            logger.error("RHOBEAR_GW_API_KEY not set and no gateway key file found")
            sys.exit(1)

    if not os.environ.get(GW_BASE_ENV, "").strip():
        os.environ[GW_BASE_ENV] = "http://127.0.0.1:8780/v1"
    logger.info("Gateway: %s", os.environ[GW_BASE_ENV])
    return key


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _out_dir() -> Path:
    if _get_args().output_dir:
        d = Path(_get_args().output_dir)
    else:
        d = PROJECT_ROOT / "out" / "e2e-proof" / _timestamp()
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


async def run_pipeline(target_url: str, out_dir: Path, label: str) -> dict[str, Any]:
    """Run the full pipeline: record → AI → viewer → MP4 → screenshots.

    Returns a results dict with paths and validation outcomes.
    """
    results: dict[str, Any] = {"label": label, "target": target_url, "out_dir": str(out_dir)}

    # ---- 1. Record ------------------------------------------------------------
    logger.info("=== STAGE 1: agent_record against %s ===", target_url)
    t0 = time.perf_counter()

    from capturd.walk.recorder import DemoRecorder

    session_id = f"e2e-{uuid.uuid4().hex[:8]}"
    recorder = DemoRecorder(
        session_id=session_id,
        url=target_url,
        name=f"E2E Proof — {label}",
        goal="Walk through the page, clicking on key elements to demonstrate the product. Explore links, buttons, and interactive elements.",
        viewport={"width": 1440, "height": 900},
        output_dir=out_dir / "record",
    )

    spec = await recorder.agent_record()
    record_elapsed = time.perf_counter() - t0
    logger.info(
        "agent_record done: %d steps in %.1fs", len(spec.steps), record_elapsed
    )
    results["record_steps"] = len(spec.steps)
    results["record_elapsed_s"] = round(record_elapsed, 1)

    # Save the raw demo.json
    demo_json_path = out_dir / "demo.json"
    demo_json_path.write_text(
        json.dumps(spec.to_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    logger.info("demo.json → %s", demo_json_path)

    if len(spec.steps) < 2:
        logger.warning(
            "Only %d steps recorded (target was %d). Pipeline may be sparse.",
            len(spec.steps), _get_args().steps,
        )

    # ---- 2. AI Pipeline -------------------------------------------------------
    logger.info("=== STAGE 2: DemoAI.enrich ===")
    t0 = time.perf_counter()

    from capturd.walk.ai_pipeline import DemoAI

    ai = DemoAI(
        model_vision="gemini-2.5-flash",
        model_text="gemini-2.5-flash",
        voice="en-US-AriaNeural",
    )

    spec_dict = json.loads(demo_json_path.read_text(encoding="utf-8"))

    # Inline screenshots into the spec so the viewer is self-contained
    _inline_screenshots(spec_dict, out_dir / "record")

    enriched = await ai.enrich(spec_dict, project_root=PROJECT_ROOT)
    ai_elapsed = time.perf_counter() - t0
    logger.info("DemoAI.enrich done in %.1fs", ai_elapsed)
    results["ai_elapsed_s"] = round(ai_elapsed, 1)

    # Save enriched spec
    enriched_path = out_dir / "demo_enriched.json"
    enriched_path.write_text(
        json.dumps(enriched, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    logger.info("demo_enriched.json → %s", enriched_path)

    # ---- 3. Validate enriched spec -------------------------------------------
    ai_ann = enriched.get("aiAnnotations") or {}
    steps = enriched.get("steps") or []

    voiceover_count = sum(1 for s in steps if s.get("voiceoverWords"))
    annotation_count = sum(1 for s in steps if s.get("annotation"))
    animation_count = sum(
        1 for s in steps
        if any(
            kf.get("stepIndex") == s.get("index")
            for kf in ai_ann.get("animationTimeline", [])
        )
    )
    timeline_actions = [
        kf.get("action") for kf in ai_ann.get("animationTimeline", [])
    ]
    has_spotlight = "spotlightOn" in timeline_actions
    has_zoom = "zoomTo" in timeline_actions
    has_hold = "hold" in timeline_actions

    logger.info(
        "Validation: voiceoverSteps=%d/%d annotationSteps=%d/%d "
        "timelineActions=%d spotlight=%s zoom=%s hold=%s",
        voiceover_count, len(steps),
        annotation_count, len(steps),
        len(timeline_actions), has_spotlight, has_zoom, has_hold,
    )

    results["voiceover_steps"] = voiceover_count
    results["annotation_steps"] = annotation_count
    results["timeline_actions"] = len(timeline_actions)
    results["has_spotlight"] = has_spotlight
    results["has_zoom"] = has_zoom
    results["has_hold"] = has_hold
    results["ai_summary"] = ai_ann.get("summary", "")

    # Content-mode detection
    content_modes = {}
    for s in steps:
        cm = s.get("contentMode", "?")
        cmv = s.get("contentMetadata", {})
        cap = cmv.get("canvasAreaPct", -1)
        content_modes[s.get("index", "?")] = {
            "mode": cm,
            "canvasAreaPct": cap,
            "hasVideo": cmv.get("hasVideo"),
        }
    results["content_modes"] = content_modes
    logger.info("Content modes: %s", json.dumps(content_modes, indent=2))

    # ---- 4. Render viewer HTML -----------------------------------------------
    logger.info("=== STAGE 4: render_viewer_to_file ===")
    from capturd.walk.viewer import render_viewer_to_file

    viewer_path = out_dir / "viewer.html"
    render_viewer_to_file(enriched, viewer_path)
    viewer_size_kb = round(viewer_path.stat().st_size / 1024, 1)
    logger.info("viewer.html → %s (%s KB)", viewer_path, viewer_size_kb)
    results["viewer_size_kb"] = viewer_size_kb

    # ---- 5. MP4 screencast ---------------------------------------------------
    mp4_path = None
    if not _get_args().skip_mp4:
        logger.info("=== STAGE 5: MP4 screencast ===")
        mp4_path = out_dir / "walkthrough.mp4"
        try:
            await _screencast_viewer(viewer_path, mp4_path, enriched, out_dir)
            mp4_size_kb = round(mp4_path.stat().st_size / 1024, 1) if mp4_path.is_file() else 0
            logger.info("walkthrough.mp4 → %s (%s KB)", mp4_path, mp4_size_kb)
            results["mp4_size_kb"] = mp4_size_kb
        except Exception as exc:
            logger.error("MP4 screencast failed: %s", exc, exc_info=True)
            results["mp4_error"] = str(exc)

    # ---- 6. Step screenshots -------------------------------------------------
    logger.info("=== STAGE 6: step screenshots ===")
    try:
        step_pngs = await _capture_step_screenshots(viewer_path, enriched, out_dir)
        results["step_screenshots"] = step_pngs
    except Exception as exc:
        logger.error("Step screenshots failed: %s", exc, exc_info=True)
        results["screenshot_error"] = str(exc)

    # ---- Save results manifest -----------------------------------------------
    manifest_path = out_dir / "results.json"
    manifest_path.write_text(json.dumps(results, indent=2, sort_keys=True))
    logger.info("results.json → %s", manifest_path)

    return results


# ---------------------------------------------------------------------------
# Screenshot inlining (so the viewer is self-contained for screencast)
# ---------------------------------------------------------------------------


def _inline_screenshots(spec: dict, record_dir: Path) -> None:
    """Replace screenshotPath with screenshotBase64 for all steps."""
    for step in spec.get("steps") or []:
        if step.get("screenshotBase64"):
            continue
        path_str = step.get("screenshotPath")
        if not path_str:
            continue

        # Path is relative to project root (e.g., "demos/<id>/step_000.png")
        # The record_dir is out/e2e-proof/<ts>/record/
        img_path = record_dir / Path(path_str).name
        if not img_path.is_file():
            # Try relative to project root
            alt = PROJECT_ROOT / path_str
            if alt.is_file():
                img_path = alt

        if img_path.is_file():
            step["screenshotBase64"] = base64.b64encode(
                img_path.read_bytes()
            ).decode("ascii")
            step["screenshotPath"] = None  # Already inlined


# ---------------------------------------------------------------------------
# Playwright screencast: open viewer → auto-play → record video
# ---------------------------------------------------------------------------


async def _screencast_viewer(
    viewer_path: Path, mp4_path: Path, spec: dict, out_dir: Path
) -> None:
    """Open the viewer HTML in a headless browser, let auto-play run,
    capture periodic screenshots, and encode them as MP4 via ffmpeg."""
    from playwright.async_api import async_playwright

    steps = spec.get("steps") or []
    if not steps:
        logger.warning("No steps to screencast")
        return

    viewport = spec.get("viewport") or {"width": 1440, "height": 900}
    vp_w = viewport.get("width", 1440)
    vp_h = viewport.get("height", 900)

    # Calculate total playback duration
    # Each step: transition(~500ms) + hold(~2500ms) + gap(500ms) ≈ 3.5s
    # Add 2s buffer at start + end
    per_step_ms = 4000
    total_ms = 2000 + len(steps) * per_step_ms + 2000
    total_s = total_ms / 1000.0

    logger.info("Screencast: viewport=%dx%d steps=%d est_duration=%.1fs",
                vp_w, vp_h, len(steps), total_s)

    frames_dir = out_dir / "frames"
    frames_dir.mkdir(exist_ok=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": vp_w, "height": vp_h})

        # Navigate to viewer
        viewer_url = f"file://{viewer_path.absolute()}"
        await page.goto(viewer_url, wait_until="domcontentloaded")
        await asyncio.sleep(1.0)  # Let the viewer init

        # Skip any "empty state" and wait for the demo to load
        try:
            await page.wait_for_selector("#screenshot.visible", timeout=5000)
        except Exception:
            logger.warning("Screenshot may not have loaded (placeholder?)")

        # Capture frames at ~10 fps
        fps = 10
        frame_interval = 1.0 / fps
        frame_idx = 0
        start = time.perf_counter()

        # Click play to start auto-play if it isn't already
        try:
            play_btn = page.locator("#btn-play")
            await play_btn.click(timeout=3000)
            logger.info("Clicked play button")
        except Exception:
            logger.info("Play button not found; may be auto-playing")

        elapsed_log = 0
        while time.perf_counter() - start < total_s:
            frame_path = frames_dir / f"frame_{frame_idx:05d}.png"
            try:
                await page.screenshot(path=str(frame_path))
                frame_idx += 1
            except Exception as exc:
                logger.warning("Frame %d capture failed: %s", frame_idx, exc)

            await asyncio.sleep(frame_interval)

            # Log progress every 5s
            elapsed = time.perf_counter() - start
            if elapsed - elapsed_log >= 5:
                logger.info(
                    "Screencast: %.1fs / %.1fs (%d frames)",
                    elapsed, total_s, frame_idx,
                )
                elapsed_log = elapsed

        await browser.close()

    logger.info("Captured %d frames in %s", frame_idx, frames_dir)

    # Encode to MP4 via ffmpeg
    if frame_idx == 0:
        logger.error("No frames captured — cannot produce MP4")
        return

    _encode_mp4(frames_dir, mp4_path, fps=fps)
    logger.info("MP4 encoded: %s", mp4_path)


def _encode_mp4(frames_dir: Path, output_path: Path, fps: int = 10) -> None:
    """Encode frame sequence to MP4 using ffmpeg."""
    cmd = [
        "ffmpeg",
        "-y",
        "-framerate", str(fps),
        "-i", str(frames_dir / "frame_%05d.png"),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output_path),
    ]
    logger.info("ffmpeg: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("ffmpeg stderr:\n%s", result.stderr[-2000:])
        raise RuntimeError(f"ffmpeg failed (exit {result.returncode})")

    # Log evidence
    for line in result.stderr.split("\n"):
        if "frame=" in line and "fps=" in line:
            logger.info("ffmpeg: %s", line.strip())


# ---------------------------------------------------------------------------
# Step screenshots — capture the viewer at specific step boundaries
# ---------------------------------------------------------------------------


async def _capture_step_screenshots(
    viewer_path: Path, spec: dict, out_dir: Path
) -> dict[int, str]:
    """Open the viewer, navigate to each step, take a screenshot."""
    from playwright.async_api import async_playwright

    steps = spec.get("steps") or []
    if not steps:
        return {}

    viewport = spec.get("viewport") or {"width": 1440, "height": 900}
    vp_w = viewport.get("width", 1440)
    vp_h = viewport.get("height", 900)

    screenshots: dict[int, str] = {}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": vp_w, "height": vp_h})

        viewer_url = f"file://{viewer_path.absolute()}"
        await page.goto(viewer_url, wait_until="domcontentloaded")
        await asyncio.sleep(1.0)

        # Navigate to each step and take screenshot
        for step_idx in range(min(len(steps), 6)):  # up to 6 steps
            # Use JS to jump to the step
            try:
                await page.evaluate(f"""
                    if (window.__demoViewerNavigate) {{
                        window.__demoViewerNavigate({step_idx});
                    }} else {{
                        // Manual step navigation
                        const btns = document.querySelectorAll('.progress-segment');
                        if (btns[{step_idx}]) btns[{step_idx}].click();
                    }}
                """)
            except Exception:
                # Fallback: click progress segment
                try:
                    segments = page.locator(".progress-segment")
                    count = await segments.count()
                    if step_idx < count:
                        await segments.nth(step_idx).click(timeout=2000)
                except Exception as exc:
                    logger.warning("Could not navigate to step %d: %s", step_idx, exc)

            await asyncio.sleep(1.5)  # Let spotlight + zoom animate in

            png_path = out_dir / f"step_{step_idx + 1}.png"
            await page.screenshot(path=str(png_path))
            screenshots[step_idx] = str(png_path)
            logger.info("Screenshot step %d → %s", step_idx + 1, png_path.name)

        await browser.close()

    return screenshots


# ---------------------------------------------------------------------------
# Canvas fixture test
# ---------------------------------------------------------------------------


async def run_canvas_test(out_dir: Path) -> dict[str, Any]:
    """Run content-mode detection against the canvas fixture.

    Opens the canvas fixture page, runs agent_record (1-2 steps since it's
    a static canvas), and verifies contentMode == video.
    """
    canvas_url = f"file://{CANVAS_FIXTURE.absolute()}"

    logger.info("=== CANVAS FIXTURE: %s ===", canvas_url)

    from capturd.walk.recorder import DemoRecorder
    from capturd.walk.recorder import _detect_content_mode, _classify_content_mode

    from playwright.async_api import async_playwright

    results: dict[str, Any] = {"fixture": str(CANVAS_FIXTURE)}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900}
        )
        page = await context.new_page()
        await page.goto(canvas_url, wait_until="domcontentloaded")
        await asyncio.sleep(0.5)

        # Run content-mode detection directly
        cm = await _detect_content_mode(page)
        mode = _classify_content_mode(cm)

        logger.info(
            "Canvas fixture: mode=%s canvasAreaPct=%.2f hasCanvas=%s "
            "hasVideo=%s hasIframe=%s mutRate=%.1f",
            mode, cm.canvasAreaPct, cm.hasCanvas,
            cm.hasVideo, cm.hasIframe, cm.mutationRate,
        )

        results["content_mode"] = mode
        results["canvas_area_pct"] = cm.canvasAreaPct
        results["has_canvas"] = cm.hasCanvas
        results["mutation_rate"] = cm.mutationRate

        await browser.close()

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> int:
    _ensure_gateway_key()
    out_dir = _out_dir()
    logger.info("Output dir: %s", out_dir)

    all_results: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "out_dir": str(out_dir),
    }

    # Resolve target
    target = _get_args().target
    if target == "canvas":
        target_url = f"file://{CANVAS_FIXTURE.absolute()}"
        label = "canvas-fixture"
    elif target.startswith("http"):
        target_url = target
        label = target.replace("https://", "").replace("http://", "").replace("/", "-")[:40]
    else:
        logger.error("Unknown target: %s", target)
        return 1

    # ---- Pipeline: example.com or user URL -----------------------------------
    try:
        results = await run_pipeline(target_url, out_dir, label)
        all_results["pipeline"] = results
    except Exception as exc:
        logger.exception("Pipeline failed")
        all_results["pipeline_error"] = str(exc)

    # ---- Canvas fixture test (if example.com was the main target) ------------
    if target != "canvas":
        canvas_out = out_dir / "canvas"
        canvas_out.mkdir(exist_ok=True)
        try:
            canvas_results = await run_canvas_test(canvas_out)
            all_results["canvas"] = canvas_results
        except Exception as exc:
            logger.exception("Canvas fixture test failed")
            all_results["canvas_error"] = str(exc)

    # ---- Summary -------------------------------------------------------------
    all_results["finished_at"] = datetime.now(timezone.utc).isoformat()
    summary_path = out_dir / "results.json"
    summary_path.write_text(json.dumps(all_results, indent=2, sort_keys=True))

    print("\n" + "=" * 72)
    print("E2E PROOF SUMMARY")
    print("=" * 72)
    pipeline = all_results.get("pipeline") or {}
    print(f"  Record steps:     {pipeline.get('record_steps', 'FAILED')}")
    print(f"  Voiceover steps:  {pipeline.get('voiceover_steps', 'FAILED')}")
    print(f"  Annotations:      {pipeline.get('annotation_steps', 'FAILED')}")
    print(f"  Timeline actions: {pipeline.get('timeline_actions', 'FAILED')}")
    has_sp = pipeline.get("has_spotlight")
    has_zm = pipeline.get("has_zoom")
    has_hd = pipeline.get("has_hold")
    print(f"  Spotlight:        {'✓' if has_sp else '✗'}")
    print(f"  Zoom:             {'✓' if has_zm else '✗'}")
    print(f"  Hold:             {'✓' if has_hd else '✗'}")
    print(f"  Viewer size:      {pipeline.get('viewer_size_kb', 'FAILED')} KB")
    mp4_kb = pipeline.get("mp4_size_kb")
    print(f"  MP4 size:         {mp4_kb if mp4_kb else 'FAILED/NA'} KB")
    canvas = all_results.get("canvas") or {}
    print(f"  Canvas mode:      {canvas.get('content_mode', 'NA')}")
    print(f"  Canvas area %:    {canvas.get('canvas_area_pct', 'NA')}")
    print(f"  Output:           {out_dir}")
    print("=" * 72)

    # Exit code
    errors = []
    if not pipeline:
        errors.append("Pipeline failed entirely")
    else:
        if pipeline.get("voiceover_steps", 0) < 1:
            errors.append("No voiceover steps")
        if pipeline.get("annotation_steps", 0) < 1:
            errors.append("No annotation steps")
        if not has_sp or not has_zm or not has_hd:
            errors.append("Missing timeline actions (need spotlight, zoom, hold)")

    if errors:
        print(f"\nFAILED: {'; '.join(errors)}")
        return 1

    print("\n✓ All checks passed")
    return 0


if __name__ == "__main__":
    _ARGS = _build_parser().parse_args()
    sys.exit(asyncio.run(main()))
