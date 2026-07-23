"""finish.py — paid-lane post-export finisher (WALL-safe: pure ffmpeg on the MP4).

Takes a rendered Captur'd MP4 and applies brand/social polish the frozen engine
doesn't do — all as a post-process, zero engine changes:

  * aspect  — reframe to 9:16 / 1:1 / 16:9 (blurred-fill background, no crop of
              the content; the landscape capture is centered on a filled canvas)
  * watermark — overlay a logo PNG in a chosen corner
  * intro / outro — prepend/append a title card (solid brand color + text)
  * music — mix a music bed under the existing voiceover (ducked)

Usage:
    python finish.py <in.mp4> <out.mp4> --aspect 9:16 \
        --watermark logo.png --wm-corner br \
        --intro "RHOBEAR Plans" --outro "Start free today" \
        --brand "#4f8cff" --music bed.mp3 --music-db -18

Every flag is optional; with none it's a straight copy. Font defaults to the
system sans-serif family (via fontconfig); override with --font <path> to a
real font file.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
if not FFMPEG or not FFPROBE:
    sys.exit("finish.py: ffmpeg/ffprobe not found on PATH -- install them or add to PATH")

ASPECTS = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}
CORNERS = {
    "tl": "x=40:y=40", "tr": "x=W-w-40:y=40",
    "bl": "x=40:y=H-h-40", "br": "x=W-w-40:y=H-h-40",
}


def _run(args: list[str]) -> None:
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{r.stderr[-1500:]}")


def _probe_dur(path: Path) -> float:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def _ff_font(path: str) -> str:
    # ffmpeg filter escaping: backslashes -> forward, escape the drive colon.
    return path.replace("\\", "/").replace(":", "\\:")


def _ff_text(text: str) -> str:
    # drawtext text= is single-quoted; escape ffmpeg's filter-syntax special
    # chars (\ : ' %) so a title containing any of them can't break out of
    # the quoted value or start a new filter clause.
    escaped = (text.replace("\\", "\\\\")
                    .replace(":", "\\:")
                    .replace("%", "\\%")
                    .replace("'", r"'\''"))
    return escaped


# ffmpeg treats a leading "scheme:" as a protocol (concat:, http:, pipe:,
# tcp:, data:, subfile:, ...), not a literal filename -- but ONLY when it's
# at the very start of the string with no leading path separator; ffmpeg
# never re-parses a colon that appears later in an already-a-path string as
# a new protocol boundary. So this only rejects bare scheme-looking values
# (protocol prefixes, or a filename that's genuinely ambiguous to ffmpeg
# too) -- a real filename containing a colon mid-name (e.g.
# "logo_v2:final.png") is unaffected, and is_file()+resolve() below is the
# actual safety net regardless (the resolved absolute path we return always
# starts with "/", which ffmpeg never treats as a protocol scheme).
_PROTOCOL_PREFIX_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:")


def _safe_input_path(raw: str, label: str) -> str:
    if _PROTOCOL_PREFIX_RE.match(raw):
        sys.exit(f"finish.py: --{label} {raw!r} looks like a protocol prefix "
                  f"-- rejected (ffmpeg would interpret this as a protocol, "
                  f"not a file path; use an absolute or ./-relative path)")
    p = Path(raw)
    if not p.is_file():
        sys.exit(f"finish.py: --{label} {raw!r} is not a real file")
    return str(p.resolve())


def _title_card(text: str, wh: tuple[int, int], brand: str, font: str | None,
                seconds: float, out: Path) -> None:
    w, h = wh
    fs = max(40, int(h * 0.06))
    # --font is a file path if given and it exists; otherwise fall back to a
    # fontconfig family name so this works without a platform-specific
    # hardcoded path (drawtext resolves `font=` via libfontconfig).
    font_clause = f"fontfile='{_ff_font(font)}'" if font and Path(font).is_file() else "font='sans-serif'"
    draw = (f"drawtext={font_clause}:text='{_ff_text(text)}':"
            f"fontcolor=white:fontsize={fs}:x=(w-text_w)/2:y=(h-text_h)/2")
    _run([FFMPEG, "-y", "-f", "lavfi", "-i",
          f"color=c={brand}:s={w}x{h}:d={seconds}:r=30",
          "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo:d={seconds}",
          "-vf", draw, "-c:v", "libx264", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-shortest", str(out)])


def _reframe_filter(wh: tuple[int, int]) -> str:
    w, h = wh
    # Blurred fill background + centered, aspect-preserved foreground. No crop.
    return (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},boxblur=luma_radius=40:luma_power=1,eq=brightness=-0.2[bg];"
            f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v]")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("infile")
    ap.add_argument("outfile")
    ap.add_argument("--aspect", choices=list(ASPECTS), default="16:9")
    ap.add_argument("--watermark", default="")
    ap.add_argument("--wm-corner", choices=list(CORNERS), default="br")
    ap.add_argument("--wm-scale", type=float, default=0.16, help="logo width as frac of canvas W")
    ap.add_argument("--intro", default="")
    ap.add_argument("--outro", default="")
    ap.add_argument("--brand", default="#0d1017")
    ap.add_argument("--card-secs", type=float, default=1.6)
    ap.add_argument("--music", default="")
    ap.add_argument("--music-db", type=float, default=-18.0)
    ap.add_argument("--font", default=None,
                    help="path to a font file; omit to use the system sans-serif family")
    args = ap.parse_args()

    infile = _safe_input_path(args.infile, "infile")
    out = Path(args.outfile)
    out.parent.mkdir(parents=True, exist_ok=True)
    wh = ASPECTS[args.aspect]
    watermark = _safe_input_path(args.watermark, "watermark") if args.watermark else ""
    music = _safe_input_path(args.music, "music") if args.music else ""
    tmp = Path(tempfile.mkdtemp(prefix="capturd-finish-"))
    try:
        # 1) reframe (+ optional watermark) into the target canvas
        body = tmp / "body.mp4"
        inputs = ["-i", infile]
        if watermark:
            inputs += ["-i", watermark]
            wm_w = int(wh[0] * args.wm_scale)
            fc = (_reframe_filter(wh)
                  + f";[1:v]scale={wm_w}:-1[wm];[v][wm]overlay={CORNERS[args.wm_corner]}[vo]")
            vmap = "[vo]"
        else:
            fc = _reframe_filter(wh)
            vmap = "[v]"
        _run([FFMPEG, "-y", *inputs, "-filter_complex", fc,
              "-map", vmap, "-map", "0:a?", "-c:v", "libx264", "-pix_fmt", "yuv420p",
              "-c:a", "aac", "-r", "30", str(body)])

        # 2) music bed (duck under existing audio)
        staged = body
        if music:
            with_music = tmp / "music.mp4"
            _run([FFMPEG, "-y", "-i", str(body), "-i", music,
                  "-filter_complex",
                  f"[1:a]volume={args.music_db}dB[m];[0:a][m]amix=inputs=2:duration=first:dropout_transition=3[a]",
                  "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", str(with_music)])
            staged = with_music

        # 3) intro/outro title cards → concat
        parts = []
        if args.intro:
            p = tmp / "intro.mp4"; _title_card(args.intro, wh, args.brand, args.font, args.card_secs, p); parts.append(p)
        parts.append(staged)
        if args.outro:
            p = tmp / "outro.mp4"; _title_card(args.outro, wh, args.brand, args.font, args.card_secs, p); parts.append(p)

        if len(parts) == 1:
            shutil.copy2(staged, out)
        else:
            # re-encode-concat (safe across the title cards) via concat filter
            fi = []
            for p in parts:
                fi += ["-i", str(p)]
            n = len(parts)
            streams = "".join(f"[{i}:v][{i}:a]" for i in range(n))
            _run([FFMPEG, "-y", *fi, "-filter_complex",
                  f"{streams}concat=n={n}:v=1:a=1[v][a]",
                  "-map", "[v]", "-map", "[a]", "-c:v", "libx264",
                  "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)])

        dur = _probe_dur(out)
        print(f"[finish] {out} — {args.aspect} {wh[0]}x{wh[1]}, {dur:.1f}s"
              + (", watermark" if args.watermark else "")
              + (", music" if args.music else "")
              + (f", intro/outro" if (args.intro or args.outro) else ""))
    finally:
        # Cleanup failure doesn't mean the render failed -- log it loudly
        # (so leaked temp dirs on the VPS get noticed) but don't tell the
        # caller the video itself failed when it didn't.
        try:
            shutil.rmtree(tmp)
        except OSError as exc:
            print(f"[finish] ERROR cleanup failed for {tmp}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
