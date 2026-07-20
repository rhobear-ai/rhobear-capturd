"""Video export — deterministic frame rendering over the viewer's ``__demoExport``.

The viewer template compiles the whole demo (camera, cursor, crossfades,
spotlight, captions) into pure functions of time. This module drives that
renderer frame by frame in headless Chromium and encodes the result with
ffmpeg — every frame lands exactly where the timeline says, no wall-clock
jitter, any fps.

Voiceover audio (per-step Edge-TTS MP3s embedded in the spec) is muxed onto
the video at the offsets the compiled plan reports (``audioPlan``).

ffmpeg discovery: PATH first, then the ``imageio-ffmpeg`` wheel if installed.
No RHOBEAR credentials or founder identity anywhere — sellable by default.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_FPS = 30
GIF_FPS = 12
GIF_WIDTH = 960


class DemoExportError(RuntimeError):
    """Raised when the video export cannot complete."""


def find_ffmpeg() -> str:
    """Locate an ffmpeg executable. PATH first, then the imageio-ffmpeg wheel."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - any failure means "not available"
        pass
    raise DemoExportError(
        "ffmpeg not found. Install it (winget install Gyan.FFmpeg / "
        "apt install ffmpeg) or `pip install imageio-ffmpeg`."
    )


# ---------------------------------------------------------------------------
# Frame capture
# ---------------------------------------------------------------------------


async def _capture_frames(
    viewer_html: Path,
    viewport: dict[str, int],
    frames_dir: Path,
    fps: int,
) -> tuple[int, float, list[dict[str, Any]]]:
    """Drive ``__demoExport`` seek-by-seek and screenshot every frame.

    Returns ``(frame_count, duration_s, audio_plan)``.
    """
    from playwright.async_api import async_playwright

    vw = int(viewport.get("width", 1440))
    vh = int(viewport.get("height", 900))

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page(
            viewport={"width": vw, "height": vh},
            device_scale_factor=1,
        )
        await page.goto(viewer_html.resolve().as_uri(), wait_until="domcontentloaded")
        await page.wait_for_function(
            "() => window.__demoExport && window.__demoViewer && window.__demoViewer.STATE.spec",
            timeout=15000,
        )
        plan = await page.evaluate("() => window.__demoExport.prepare()")
        total_ms = float(plan["totalMs"])
        audio_plan = list(plan.get("audioPlan") or [])
        n_frames = max(1, int(total_ms / 1000.0 * fps))
        logger.info(
            "export: %d frames @ %dfps (%.1fs of video, %d voiceover clips)",
            n_frames, fps, total_ms / 1000.0, len(audio_plan),
        )

        for i in range(n_frames):
            t = i * 1000.0 / fps
            await page.evaluate("(t) => window.__demoExport.seek(t)", t)
            png = await page.screenshot(type="png")
            (frames_dir / f"frame_{i:06d}.png").write_bytes(png)
            if i and i % (fps * 5) == 0:
                logger.info("export: %d/%d frames", i, n_frames)

        await browser.close()
    return n_frames, total_ms / 1000.0, audio_plan


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------


_FFMPEG_TIMEOUT = 600  # max seconds for any single ffmpeg call


def _run_ffmpeg(args: list[str]) -> None:
    # -nostdin + DEVNULL: ffmpeg must never touch our stdin — when this runs
    # inside the MCP server, stdin is the JSON-RPC stdio channel and an
    # inherited handle makes ffmpeg block forever waiting for console input.
    try:
        result = subprocess.run(
            [args[0], "-nostdin", *args[1:]],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=_FFMPEG_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        # A hung ffmpeg hitting the timeout raises TimeoutExpired, which none of
        # the callers (export_video → coordinator.export_demo) catch — it would
        # crash the export instead of surfacing a graceful error. Convert it.
        raise DemoExportError(
            f"ffmpeg timed out after {_FFMPEG_TIMEOUT}s"
        ) from exc
    if result.returncode != 0:
        raise DemoExportError(f"ffmpeg failed:\n{result.stderr[-3000:]}")


def _encode_mp4(
    ffmpeg: str,
    frames_dir: Path,
    fps: int,
    duration_s: float,
    out_path: Path,
    audio_clips: list[tuple[Path, int]],
) -> None:
    """Encode PNG frames to H.264, muxing voiceover clips at their offsets."""
    video_args = [
        ffmpeg, "-y",
        "-framerate", str(fps),
        "-i", str(frames_dir / "frame_%06d.png"),
    ]
    if audio_clips:
        filters = []
        mix_inputs = []
        for k, (_path, at_ms) in enumerate(audio_clips):
            video_args += ["-i", str(_path)]
            delay = max(0, int(at_ms))
            filters.append(f"[{k + 1}:a]adelay={delay}|{delay}[a{k}]")
            mix_inputs.append(f"[a{k}]")
        filters.append(
            "".join(mix_inputs)
            + f"amix=inputs={len(audio_clips)}:normalize=0:duration=longest[aout]"
        )
        video_args += [
            "-filter_complex", ";".join(filters),
            "-map", "0:v", "-map", "[aout]",
            "-c:a", "aac", "-b:a", "160k",
        ]
    video_args += [
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-t", f"{duration_s:.3f}",
        str(out_path),
    ]
    _run_ffmpeg(video_args)


def _encode_gif(ffmpeg: str, frames_dir: Path, fps: int, out_path: Path) -> None:
    """Two-pass palette GIF from the captured frames."""
    with tempfile.TemporaryDirectory(prefix="capturd-gif-") as td:
        palette = Path(td) / "palette.png"
        _run_ffmpeg([
            ffmpeg, "-y",
            "-framerate", str(fps),
            "-i", str(frames_dir / "frame_%06d.png"),
            "-vf", f"fps={GIF_FPS},scale={GIF_WIDTH}:-1:flags=lanczos,palettegen",
            str(palette),
        ])
        _run_ffmpeg([
            ffmpeg, "-y",
            "-framerate", str(fps),
            "-i", str(frames_dir / "frame_%06d.png"),
            "-i", str(palette),
            "-lavfi", f"fps={GIF_FPS},scale={GIF_WIDTH}:-1:flags=lanczos[x];[x][1:v]paletteuse",
            str(out_path),
        ])


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def export_video(
    spec: dict[str, Any],
    viewer_html: Path,
    out_path: Path,
    *,
    fmt: str = "mp4",
    fps: int = DEFAULT_FPS,
) -> Path:
    """Render ``viewer_html`` (a fully inlined export) to MP4 or GIF.

    ``spec`` must be the same enriched DemoSpec the viewer HTML embeds — the
    per-step ``voiceoverBase64`` clips are pulled from it for the audio track.
    """
    fmt = (fmt or "mp4").lower()
    if fmt not in {"mp4", "gif"}:
        raise DemoExportError(f"unsupported video format: {fmt!r}")
    if not viewer_html.is_file():
        raise DemoExportError(f"viewer html not found: {viewer_html}")
    if not (spec.get("steps") or []):
        raise DemoExportError("demo has no steps — record a flow before exporting video")

    ffmpeg = find_ffmpeg()
    viewport = spec.get("viewport") or {"width": 1440, "height": 900}

    with tempfile.TemporaryDirectory(prefix="capturd-frames-") as td:
        frames_dir = Path(td)
        from capturd.walk.recorder import run_async

        n_frames, duration_s, audio_plan = run_async(
            _capture_frames(viewer_html, viewport, frames_dir, fps)
        )
        if n_frames == 0:
            raise DemoExportError("no frames captured")

        if fmt == "gif":
            _encode_gif(ffmpeg, frames_dir, fps, out_path)
        else:
            audio_clips: list[tuple[Path, int]] = []
            steps = spec.get("steps") or []
            for entry in audio_plan:
                idx = entry.get("stepIndex")
                at_ms = entry.get("atMs", 0)
                if not isinstance(idx, int) or idx < 0 or idx >= len(steps):
                    continue
                b64 = steps[idx].get("voiceoverBase64")
                if not b64:
                    continue
                clip = frames_dir / f"voice_{idx:03d}.mp3"
                try:
                    clip.write_bytes(base64.b64decode(b64))
                except (ValueError, OSError) as exc:
                    logger.warning("skipping voiceover for step %d: %s", idx, exc)
                    continue
                audio_clips.append((clip, at_ms))
            _encode_mp4(ffmpeg, frames_dir, fps, duration_s, out_path, audio_clips)

    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise DemoExportError(f"export produced no output at {out_path}")
    logger.info("export complete: %s (%.1f KB)", out_path, out_path.stat().st_size / 1024)
    return out_path


__all__ = [
    "DEFAULT_FPS",
    "DemoExportError",
    "export_video",
    "find_ffmpeg",
]
