"""Voice loop — push-to-talk + Whisper STT + TTS reply.

The whole point: you watch the agent walk the flow AND you talk to it about
what you see. "Top left is X, Y, Z — pan in on this before you click Buy Now."
The agent hears you (Whisper), understands (LLM through gateway), acts
(camera keyframes / next-click choice), and replies (TTS, ducked over the
site audio if any).

Modes:

* **Push-to-talk overlay.** Recording page has a mic button in the overlay
  badge; hold to speak, release to send. Transcript → agent prompt.
* **Workflow mode.** Agent watches the user click each step; between clicks
  the agent asks "what are you trying to illustrate?" via TTS; user answers
  by voice; agent extracts intent and writes it into the DemoSpec goal +
  per-step annotation.
* **Continuous listen (optional).** Streaming mic → Whisper streaming →
  agent hears real-time. Off by default; opt-in per session.

Tech:

* STT: ``faster-whisper`` (CT2 backend, ~5× openai-whisper, works offline
  with the small.en model — good enough for spoken direction).
* Audio input: ``sounddevice`` (portaudio) for cross-platform mic capture.
* TTS reply: reuse the AI pipeline's edge-tts pipe (same voice as narration
  so the agent sounds like the demo narrator).
"""

from __future__ import annotations

import asyncio
import io
import inspect
import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

logger = logging.getLogger(__name__)


class VoiceLoopError(RuntimeError):
    """Raised when the voice pipeline cannot start (mic, model, or model download)."""


@dataclass
class VoiceConfig:
    """Runtime config for the voice loop."""

    model: str = "small.en"          # faster-whisper model size
    # CPU is the portable default: 'auto' picks CUDA and hard-fails on any box
    # without the cuBLAS/cuDNN libs (most machines). Opt into 'cuda' explicitly.
    device: str = "cpu"              # 'cpu' | 'cuda'
    compute_type: str = "int8"       # CT2 quantization — int8 is fine on CPU
    input_device: int | None = None  # sounddevice input index (None = default)
    sample_rate: int = 16000
    workflow_mode: bool = False      # agent narrates + asks questions between clicks
    continuous: bool = False         # streaming mic instead of push-to-talk


# ---------------------------------------------------------------------------
# Mic button overlay JS — injected into the recorder page alongside the
# RECORDING badge. Shows a 🎤 button on the overlay; hold to talk.
# ---------------------------------------------------------------------------

MIC_BUTTON_CSS = """
#__demo-recorder-indicator__mic {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  border: none;
  background: rgba(151,183,196,0.12);
  color: #e8eef2;
  font-size: 13px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 120ms cubic-bezier(0.2,0,0,1), transform 120ms cubic-bezier(0.2,0,0,1);
  flex-shrink: 0;
}
#__demo-recorder-indicator__mic:hover {
  background: rgba(151,183,196,0.22);
}
#__demo-recorder-indicator__mic.active {
  background: #f2c230;
  color: #1a1206;
  transform: scale(1.15);
  box-shadow: 0 0 0 0 rgba(242,194,48,0.5);
  animation: __demoMicPulse 0.8s ease-out infinite;
}
@keyframes __demoMicPulse {
  0% { box-shadow: 0 0 0 0 rgba(242,194,48,0.5); }
  70% { box-shadow: 0 0 0 14px rgba(242,194,48,0); }
  100% { box-shadow: 0 0 0 0 rgba(242,194,48,0); }
}
"""

MIC_BUTTON_JS = r"""
(() => {
  const BADGE_ID = '__demo-recorder-indicator';

  function ensureMicButton() {
    // Only show mic button when voice bridge is wired up by Python.
    if (typeof window.__demoMicStart !== 'function') return;
    if (typeof window.__demoMicStop !== 'function') return;

    let btn = document.getElementById(BADGE_ID + '__mic');
    if (btn) return btn;

    btn = document.createElement('button');
    btn.id = BADGE_ID + '__mic';
    btn.textContent = '\uD83C\uDFA4';  // 🎤
    btn.title = 'Hold to talk';
    btn.setAttribute('aria-label', 'Push to talk');

    function handleTranscript(transcript) {
      // Belt-and-suspenders: recorder._append_voice also drives this HUD
      // state (covers workflow/continuous voice paths); doing it here too
      // means the button path updates with zero round-trip latency.
      if (transcript) {
        window.__demoHudSetHeard && window.__demoHudSetHeard(transcript);
        window.__demoHudSetState && window.__demoHudSetState('acting');
      } else {
        window.__demoHudSetState && window.__demoHudSetState('idle');
      }
    }

    btn.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      e.stopPropagation();
      btn.classList.add('active');
      window.__demoHudSetState && window.__demoHudSetState('listening');
      window.__demoMicStart();
    });

    btn.addEventListener('pointerup', async (e) => {
      e.preventDefault();
      e.stopPropagation();
      btn.classList.remove('active');
      window.__demoHudSetState && window.__demoHudSetState('transcribing');
      try {
        const transcript = await window.__demoMicStop();
        handleTranscript(transcript);
      } catch (_) { /* bridge may have been torn down */ }
    });

    // Also handle pointerleave (e.g. finger slips off the button).
    btn.addEventListener('pointerleave', async (e) => {
      if (!btn.classList.contains('active')) return;
      btn.classList.remove('active');
      window.__demoHudSetState && window.__demoHudSetState('transcribing');
      try {
        const transcript = await window.__demoMicStop();
        handleTranscript(transcript);
      } catch (_) {}
    });

    // Append into the HUD's row (beside the REC + voice-state pills).
    const row = document.getElementById(BADGE_ID + '__row') || document.getElementById(BADGE_ID);
    if (row) {
      btn.className = '__demo-hud__mic';
      row.appendChild(btn);
      // Inject the button stylesheet once.
      if (!document.getElementById(BADGE_ID + '__mic-styles')) {
        const style = document.createElement('style');
        style.id = BADGE_ID + '__mic-styles';
        style.textContent = `MIC_BUTTON_CSS_PLACEHOLDER`;
        (document.head || document.documentElement).appendChild(style);
      }
    }
    return btn;
  }

  // Retry until badge exists (badge is created by the main overlay script).
  function tryAttach() {
    const badge = document.getElementById(BADGE_ID);
    if (badge) {
      ensureMicButton();
      return true;
    }
    return false;
  }

  if (!tryAttach()) {
    const mo = new MutationObserver(() => {
      if (tryAttach()) mo.disconnect();
    });
    if (document.documentElement) {
      mo.observe(document.documentElement, { childList: true, subtree: true });
    }
    document.addEventListener('DOMContentLoaded', () => tryAttach(), { once: true });
  }
})();
""".replace("MIC_BUTTON_CSS_PLACEHOLDER", MIC_BUTTON_CSS.strip())


# ---------------------------------------------------------------------------
# VoiceLoop
# ---------------------------------------------------------------------------


class VoiceLoop:
    """Push-to-talk voice loop backed by faster-whisper + sounddevice.

    Usage::

        loop = VoiceLoop(config=VoiceConfig(workflow_mode=True))
        await loop.start(on_utterance=lambda text: recorder.inject_direction(text))
        ...
        await loop.stop()

    The ``on_utterance`` callback receives clean transcripts; the loop handles
    VAD, mic gating, and Whisper decoding off the main thread.
    """

    def __init__(self, config: VoiceConfig | None = None) -> None:
        self.config = config or VoiceConfig()
        self._model: Any = None           # faster_whisper.WhisperModel — lazy
        self._stream: Any = None           # sounddevice.InputStream
        self._running = False
        self._capturing = False
        self._buffer: list[np.ndarray] = []
        self._buffer_lock = threading.Lock()
        self._on_utterance: Callable[[str], Any] | None = None
        self._page: Any = None             # Playwright Page for audio ducking

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(
        self,
        on_utterance: Callable[[str], Any],
        page: Any = None,
    ) -> None:
        """Open the mic stream and begin listening for push-to-talk triggers.

        Args:
            on_utterance: Called with the transcribed text after each
                push-to-talk release.
            page: Optional Playwright Page for TTS audio ducking.
        """
        try:
            import sounddevice as sd
        except ImportError:
            raise VoiceLoopError(
                "voice mode requires: pip install \"capturd[voice]\""
            ) from None

        self._on_utterance = on_utterance
        self._page = page
        self._running = True

        try:
            self._stream = sd.InputStream(
                samplerate=self.config.sample_rate,
                channels=1,
                dtype="int16",
                device=self.config.input_device,
                callback=self._audio_callback,
            )
            self._stream.start()
        except sd.PortAudioError as exc:
            raise VoiceLoopError(
                f"Cannot open microphone: {exc}. "
                f"Check that a mic is connected and not in use by another app."
            ) from exc
        except Exception as exc:
            raise VoiceLoopError(f"Failed to start audio stream: {exc}") from exc

        logger.info(
            "voice loop started: model=%s device=%s sample_rate=%d",
            self.config.model, self.config.device, self.config.sample_rate,
        )

    async def stop(self) -> None:
        """Close the mic stream and release resources."""
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as exc:
                logger.warning("error closing audio stream: %s", exc)
            finally:
                self._stream = None
        self._capturing = False
        self._buffer.clear()
        logger.info("voice loop stopped")

    # ------------------------------------------------------------------
    # Push-to-talk
    # ------------------------------------------------------------------

    async def push_to_talk(self, duration_ms: int | None = None) -> str | None:
        """Start capturing from the mic.

        If ``duration_ms`` is given, capture for that many milliseconds,
        transcribe, and return the result. Otherwise return immediately;
        caller must call :meth:`stop_push_to_talk` to stop and transcribe.

        Returns:
            The transcript string, or ``None`` when called without
            ``duration_ms`` (async fire-and-forget start).
        """
        if not self._running:
            raise VoiceLoopError("voice loop is not running — call start() first")

        self._capturing = True
        with self._buffer_lock:
            self._buffer.clear()

        if duration_ms is not None:
            await asyncio.sleep(duration_ms / 1000.0)
            return await self.stop_push_to_talk()
        return None

    async def stop_push_to_talk(self) -> str:
        """Stop capturing, transcribe the buffered audio, and return the transcript.

        Safe to call even when not capturing (returns empty string).
        """
        return await self._stop_capture_and_transcribe()

    # ------------------------------------------------------------------
    # TTS reply
    # ------------------------------------------------------------------

    async def reply(self, text: str) -> None:
        """Speak ``text`` via Edge TTS, ducking page audio while speaking.

        Uses the same TTS pipeline as the demo narration so the agent
        sounds like the demo narrator.
        """
        if not text or not text.strip():
            return

        from capturd.walk.ai_pipeline import _synthesize_one

        mp3_bytes, _words = await _synthesize_one(text.strip())
        if not mp3_bytes:
            return

        # Decode MP3 → PCM via miniaudio.
        try:
            import miniaudio
        except ImportError:
            raise VoiceLoopError(
                "TTS playback requires miniaudio: pip install \"capturd[voice]\""
            ) from None

        decoded = miniaudio.mp3_read_f32(mp3_bytes)
        sample_rate = decoded.sample_rate
        audio_float = np.array(decoded.samples, dtype=np.float32)
        if decoded.nchannels == 2:
            # Interleaved stereo → reshape to (n_frames, 2) for sounddevice.
            audio_float = audio_float.reshape(-1, 2)
        else:
            audio_float = audio_float.reshape(-1, 1)

        # Duck page audio while speaking.
        await self._duck_page_audio()

        try:
            import sounddevice as sd

            sd.play(audio_float, samplerate=sample_rate)
            sd.wait()
        finally:
            # Always restore page audio, even if playback fails.
            await self._unduck_page_audio()

    # ------------------------------------------------------------------
    # Internal: audio callback (runs on PortAudio thread)
    # ------------------------------------------------------------------

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time: Any,
        status: Any,
    ) -> None:
        """sounddevice callback — accumulate audio when capturing."""
        if self._capturing and self._running:
            with self._buffer_lock:
                self._buffer.append(indata.copy())

    # ------------------------------------------------------------------
    # Internal: model loading (lazy)
    # ------------------------------------------------------------------

    def _load_model(self) -> None:
        """Lazy-load the Whisper model on first transcription request.

        Model download (first run) can take 5–15s; we log progress so
        the user doesn't think the tool is stuck.
        """
        if self._model is not None:
            return

        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise VoiceLoopError(
                "voice mode requires: pip install \"capturd[voice]\""
            ) from None

        logger.info(
            "loading faster-whisper model '%s' (device=%s, compute_type=%s) — "
            "first run may download from HuggingFace (5–15s)...",
            self.config.model, self.config.device, self.config.compute_type,
        )
        try:
            self._model = WhisperModel(
                self.config.model,
                device=self.config.device,
                compute_type=self.config.compute_type,
            )
        except Exception as exc:
            raise VoiceLoopError(
                f"Failed to load Whisper model '{self.config.model}': {exc}"
            ) from exc

        logger.info("whisper model '%s' loaded", self.config.model)

    # ------------------------------------------------------------------
    # Internal: transcription
    # ------------------------------------------------------------------

    def _transcribe(self, audio: np.ndarray) -> str:
        """Transcribe a numpy audio array (int16, mono, 16kHz) to text.

        Public for testing — feed a WAV file's samples directly.
        """
        self._load_model()
        # faster-whisper expects float32 in [-1.0, 1.0]
        if audio.dtype == np.int16:
            audio_float = audio.astype(np.float32) / 32768.0
        elif audio.dtype == np.float32:
            audio_float = audio
        else:
            audio_float = audio.astype(np.float32)
            if audio_float.max() > 1.0:
                audio_float /= 32768.0

        if audio_float.ndim > 1:
            audio_float = audio_float.mean(axis=1)  # mono mixdown

        segments, _info = self._model.transcribe(
            audio_float,
            beam_size=5,
            vad_filter=True,
        )
        return " ".join(s.text.strip() for s in segments if s.text.strip())

    async def _stop_capture_and_transcribe(self) -> str:
        """Stop capturing, transcribe buffered audio, fire callback, return text."""
        if not self._capturing:
            return ""
        self._capturing = False

        with self._buffer_lock:
            if not self._buffer:
                return ""
            audio = np.concatenate(self._buffer, axis=0)
            self._buffer.clear()

        transcript = self._transcribe(audio)

        if transcript and self._on_utterance is not None:
            result = self._on_utterance(transcript)
            if inspect.isawaitable(result):
                await result

        return transcript

    # ------------------------------------------------------------------
    # Internal: page audio ducking
    # ------------------------------------------------------------------

    async def _duck_page_audio(self) -> None:
        """Reduce page <video>/<audio> volume by 15dB (~0.18×)."""
        if self._page is None:
            return
        try:
            await self._page.evaluate("""
                document.querySelectorAll('video, audio').forEach(el => {
                    if (el.dataset.__demoOrigVolume === undefined) {
                        el.dataset.__demoOrigVolume = String(el.volume);
                    }
                    el.volume = Math.max(0, el.volume * 0.18);
                });
            """)
        except Exception as exc:
            logger.debug("audio duck failed: %s", exc)

    async def _unduck_page_audio(self) -> None:
        """Restore page <video>/<audio> volume to pre-duck levels."""
        if self._page is None:
            return
        try:
            await self._page.evaluate("""
                document.querySelectorAll('video, audio').forEach(el => {
                    if (el.dataset.__demoOrigVolume !== undefined) {
                        el.volume = parseFloat(el.dataset.__demoOrigVolume);
                        delete el.dataset.__demoOrigVolume;
                    }
                });
            """)
        except Exception as exc:
            logger.debug("audio unduck failed: %s", exc)

    # ------------------------------------------------------------------
    # JS-exposed bridge functions (called from page via expose_function)
    # ------------------------------------------------------------------

    async def _js_mic_start(self) -> None:
        """Exposed as window.__demoMicStart — start push-to-talk from JS."""
        if not self._running:
            return
        self._capturing = True
        with self._buffer_lock:
            self._buffer.clear()

    async def _js_mic_stop(self) -> str:
        """Exposed as window.__demoMicStop — stop push-to-talk, return transcript."""
        return await self._stop_capture_and_transcribe()


__all__ = [
    "MIC_BUTTON_CSS",
    "MIC_BUTTON_JS",
    "VoiceConfig",
    "VoiceLoop",
    "VoiceLoopError",
]
