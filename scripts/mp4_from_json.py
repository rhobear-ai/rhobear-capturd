#!/usr/bin/env python3
"""Generate MP4 from an existing enriched demo.json.

Usage:
    python scripts/mp4_from_json.py <path_to_demo_enriched.json>
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mp4_gen")


async def main():
    spec_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if not spec_path or not spec_path.is_file():
        print("Usage: python scripts/mp4_from_json.py <path_to_demo_enriched.json>")
        sys.exit(1)

    out_dir = spec_path.parent

    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    viewer_path = out_dir / "viewer.html"

    from capturd.walk.viewer import render_viewer_to_file
    if not viewer_path.is_file():
        render_viewer_to_file(spec, viewer_path)
        logger.info("viewer.html → %s (%.1f KB)", viewer_path, viewer_path.stat().st_size / 1024)

    # ---- MP4 ----------------------------------------------------------------
    mp4_path = out_dir / "walkthrough.mp4"

    from playwright.async_api import async_playwright
    steps = spec.get("steps") or []
    if not steps:
        logger.error("No steps to screencast")
        return 1

    viewport = spec.get("viewport") or {"width": 1440, "height": 900}
    vp_w = viewport.get("width", 1440)
    vp_h = viewport.get("height", 900)

    per_step_ms = 4500
    total_ms = 2000 + len(steps) * per_step_ms + 2000
    total_s = total_ms / 1000.0

    logger.info("Viewport=%dx%d steps=%d est_duration=%.1fs", vp_w, vp_h, len(steps), total_s)

    frames_dir = out_dir / "frames"
    frames_dir.mkdir(exist_ok=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": vp_w, "height": vp_h})

        viewer_url = f"file://{viewer_path.absolute()}"
        await page.goto(viewer_url, wait_until="domcontentloaded")
        await asyncio.sleep(1.5)

        # Click play button
        try:
            await page.locator("#btn-play").click(timeout=3000)
            logger.info("Clicked play")
        except Exception:
            logger.info("Play button not found")

        fps = 8
        frame_interval = 1.0 / fps
        frame_idx = 0
        start = time.perf_counter()
        elapsed_log = 0

        while time.perf_counter() - start < total_s:
            frame_path = frames_dir / f"frame_{frame_idx:05d}.png"
            try:
                await page.screenshot(path=str(frame_path))
                frame_idx += 1
            except Exception as exc:
                logger.warning("Frame %d failed: %s", frame_idx, exc)

            await asyncio.sleep(frame_interval)
            elapsed = time.perf_counter() - start
            if elapsed - elapsed_log >= 5:
                logger.info("%.1fs / %.1fs (%d frames)", elapsed, total_s, frame_idx)
                elapsed_log = elapsed

        await browser.close()

    logger.info("Captured %d frames", frame_idx)
    if frame_idx == 0:
        logger.error("No frames — cannot produce MP4")
        return 1

    # Encode
    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", str(frames_dir / "frame_%05d.png"),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(mp4_path),
    ]
    logger.info("ffmpeg: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("ffmpeg failed:\n%s", result.stderr[-3000:])
        return 1

    for line in result.stderr.split("\n"):
        if "frame=" in line:
            logger.info("ffmpeg: %s", line.strip())

    size_kb = mp4_path.stat().st_size / 1024
    logger.info("walkthrough.mp4 → %s (%.1f KB)", mp4_path, size_kb)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
