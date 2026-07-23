"""Extract inspection frames from each showcase export for the template verify pass."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CAPTURD_SHOWCASE_DIR", "capturd-plans-showcase"))
FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
if not FFMPEG:
    sys.exit("verify_frames.py: ffmpeg not found on PATH -- install it or add to PATH")


def probe(mp4: Path) -> dict:
    if not FFPROBE:
        return {}
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries",
         "stream=codec_type,codec_name:format=duration,size", "-of", "json", str(mp4)],
        capture_output=True, text=True).stdout
    return json.loads(out or "{}")


def main() -> int:
    for d in sorted(ROOT.iterdir()):
        if not d.is_dir() or d.name in ("shots", "scout", "scout2"):
            continue
        mp4s = list(d.glob("*.mp4"))
        if not mp4s:
            print(f"[verify] {d.name}: NO MP4")
            continue
        mp4 = mp4s[0]
        info = probe(mp4)
        dur = float(info.get("format", {}).get("duration", 0) or 0)
        codecs = {s["codec_type"]: s["codec_name"] for s in info.get("streams", [])}
        print(f"[verify] {d.name}: {mp4.name} dur={dur:.1f}s codecs={codecs} "
              f"size={mp4.stat().st_size//1024}KB")
        fdir = d / "verify-frames"
        fdir.mkdir(exist_ok=True)
        # sample: first frame, 25/50/75%, last-ish
        for pct in (0.0, 0.25, 0.5, 0.75, 0.96):
            t = max(0.0, dur * pct)
            outp = fdir / f"t{int(pct*100):03d}.jpg"
            subprocess.run([FFMPEG, "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(mp4),
                            "-frames:v", "1", "-q:v", "3", str(outp)], check=False)
        print(f"[verify]   frames -> {fdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
