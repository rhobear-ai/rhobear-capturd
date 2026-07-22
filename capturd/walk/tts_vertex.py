"""Vertex HD TTS backend — the house voice for Captur'd demos.

Same contract as the Edge TTS path in ai_pipeline (``(mp3_bytes, [WordTimestamp])``)
so the rest of the pipeline (voice-synced camera keyframes, captions, revoice)
doesn't know or care which engine spoke. Backend: the Vertex speech model
(``gemini-2.5-flash-preview-tts``) — the same engine behind Rho's HD voices,
billed against the house project's credit.

Voices are Vertex prebuilt names (Charon, Kore, Puck, …). Styles are ORIGINAL
characters (warm / hero / trailer / dj / butler) — never a real person's voice,
living or dead.

Auth: a bearer from gcloud ADC (``gcloud auth print-access-token``). Project
comes from CAPTURD_VERTEX_PROJECT (default: the RHOBEAR credit project).

Word timings: Vertex TTS returns no word boundaries, so we allocate the
measured audio duration across words proportionally to their length — close
enough for camera keyframes and captions at narration pace.
"""
from __future__ import annotations

import base64
import io
import logging
import os
import re
import shutil
import subprocess
import time
import wave

import httpx

log = logging.getLogger(__name__)

#: Vertex TTS reads at most this many characters per clip; longer annotation
#: text is truncated (and logged) — demo narration lines are far shorter.
MAX_CHARS = 900

VERTEX_PROJECT = os.environ.get("CAPTURD_VERTEX_PROJECT", "rhobear")
VERTEX_LOCATION = os.environ.get("CAPTURD_VERTEX_LOCATION", "us-central1")
TTS_MODEL = os.environ.get("CAPTURD_TTS_MODEL", "gemini-2.5-flash-preview-tts")

#: prebuilt Vertex voices we expose (allowlist — request input goes into a URL)
VERTEX_VOICES = {
    "Charon", "Puck", "Fenrir", "Orus", "Enceladus", "Iapetus",
    "Kore", "Leda", "Aoede", "Zephyr", "Callirrhoe", "Autonoe",
}

#: original character styles (vibe-legal; identity is not)
STYLES = {
    "": "",
    "warm": "Say this warmly and naturally, like a friendly product expert giving a demo",
    "hero": "Say this like a campy, earnest 1960s TV superhero — big warm delivery, completely deadpan",
    "trailer": "Say this like an epic movie-trailer announcer, low and dramatic",
    "dj": "Say this like a smooth late-night radio DJ, relaxed and unhurried",
    "butler": "Say this like an impeccably polite English butler",
}

DEFAULT_STYLE = os.environ.get("CAPTURD_TTS_STYLE", "warm")


class VertexTTSError(RuntimeError):
    """Raised when the Vertex TTS call cannot produce audio."""


def is_vertex_voice(voice: str) -> bool:
    """True when ``voice`` names this backend: bare prebuilt name or vertex:<name>[:style]."""
    if not voice:
        return False
    v = voice.split(":", 2)
    if v[0].lower() == "vertex":
        return True
    return voice in VERTEX_VOICES


def parse_voice(voice: str) -> tuple[str, str]:
    """``'vertex:Kore:trailer'`` / ``'Kore'`` → ``('Kore', 'trailer'|default)``."""
    parts = (voice or "").split(":")
    if parts and parts[0].lower() == "vertex":
        parts = parts[1:]
    if parts and parts[0] in VERTEX_VOICES:
        name = parts[0]
    else:
        if parts and parts[0]:
            log.warning("vertex tts: unknown voice %r — falling back to Charon", parts[0])
        name = "Charon"
    style = parts[1] if len(parts) > 1 and parts[1] in STYLES else DEFAULT_STYLE
    return name, style


#: (token, monotonic-expiry) — gcloud tokens live ~1h; refresh at 50 min so a
#: long walkthrough never spawns a gcloud subprocess per narration line.
_token_cache: tuple[str, float] = ("", 0.0)
_TOKEN_TTL_S = 50 * 60


def _access_token() -> str:
    global _token_cache
    token, expiry = _token_cache
    if token and time.monotonic() < expiry:
        return token
    gcloud = shutil.which("gcloud") or shutil.which("gcloud.cmd")
    if not gcloud:
        raise VertexTTSError("gcloud not found — Vertex TTS needs ADC on this machine")
    out = subprocess.run(
        [gcloud, "auth", "print-access-token"],
        capture_output=True, text=True, timeout=30, shell=False,
    )
    if out.returncode != 0:
        raise VertexTTSError(f"gcloud token failed: {out.stderr.strip()[:200]}")
    token = out.stdout.strip()
    # Sanity: a real access token is one long unbroken string — never accept
    # multi-line/spaced output (e.g. a misrouted gcloud error message).
    if not token or any(c.isspace() for c in token):
        raise VertexTTSError("gcloud returned something that is not an access token")
    _token_cache = (token, time.monotonic() + _TOKEN_TTL_S)
    return token


def _pcm_to_wav(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _wav_to_mp3(wav: bytes) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise VertexTTSError("ffmpeg not found — needed to encode Vertex audio to MP3")
    out = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error",
         "-f", "wav", "-i", "pipe:0", "-codec:a", "libmp3lame", "-q:a", "3",
         "-f", "mp3", "pipe:1"],
        input=wav, capture_output=True, timeout=120,
    )
    if out.returncode != 0 or not out.stdout:
        raise VertexTTSError(f"ffmpeg mp3 encode failed: {out.stderr.decode(errors='replace')[:200]}")
    return out.stdout


def _approx_word_timings(text: str, total_ms: int):
    """Spread the clip duration across words, weighted by word length (+1 for
    the pause each word carries). Camera-keyframe-grade, not lip-sync-grade."""
    from capturd.walk.schema import WordTimestamp

    words = [w for w in re.split(r"\s+", text.strip()) if w]
    if not words or total_ms <= 0:
        return []
    weights = [len(w) + 1 for w in words]
    total_w = sum(weights)
    out, t = [], 0.0
    for w, wt in zip(words, weights):
        dur = total_ms * (wt / total_w)
        out.append(WordTimestamp(word=w, tStartMs=int(t), tEndMs=int(t + dur)))
        t += dur
    return out


def synthesize(text: str, voice: str = "Charon") -> tuple[bytes, list]:
    """Speak ``text`` with a Vertex prebuilt voice → ``(mp3_bytes, [WordTimestamp])``."""
    text = (text or "").strip()
    if not text:
        return b"", []
    name, style = parse_voice(voice)
    style_prefix = STYLES.get(style, "")
    if len(text) > MAX_CHARS:
        log.warning("vertex tts: truncating %d-char text to %d", len(text), MAX_CHARS)
    spoken = text[:MAX_CHARS]
    prompt = f"{style_prefix}: {spoken}" if style_prefix else spoken

    url = (f"https://{VERTEX_LOCATION}-aiplatform.googleapis.com/v1/projects/"
           f"{VERTEX_PROJECT}/locations/{VERTEX_LOCATION}/publishers/google/models/"
           f"{TTS_MODEL}:generateContent")
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": name}}},
        },
    }
    resp = httpx.post(
        url,
        headers={"Authorization": f"Bearer {_access_token()}",
                 "Content-Type": "application/json"},
        json=body,
        timeout=60.0,
    )
    if resp.status_code == 401:
        # Cached token revoked mid-session — refresh once and retry.
        global _token_cache
        _token_cache = ("", 0.0)
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {_access_token()}",
                     "Content-Type": "application/json"},
            json=body,
            timeout=60.0,
        )
    if resp.status_code != 200:
        raise VertexTTSError(f"vertex tts {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    inline = None
    for part in (data.get("candidates") or [{}])[0].get("content", {}).get("parts", []):
        if part.get("inlineData", {}).get("data"):
            inline = part["inlineData"]
            break
    if not inline:
        raise VertexTTSError("vertex tts returned no audio")

    pcm = base64.b64decode(inline["data"])
    m = re.search(r"rate=(\d+)", inline.get("mimeType", "") or "")
    rate = int(m.group(1)) if m else 24000
    total_ms = int(len(pcm) / 2 / rate * 1000)
    mp3 = _wav_to_mp3(_pcm_to_wav(pcm, rate))
    return mp3, _approx_word_timings(spoken, total_ms)


__all__ = [
    "DEFAULT_STYLE", "STYLES", "VERTEX_VOICES", "VertexTTSError",
    "is_vertex_voice", "parse_voice", "synthesize",
]
