"""Unit tests for the Vertex HD TTS backend (no network, no gcloud)."""
import asyncio
import io
import wave

import pytest

from capturd.walk import tts_vertex
from capturd.walk.ai_pipeline import DemoAIError, _synthesize_one


# ---- voice routing ----------------------------------------------------------

def test_is_vertex_voice_bare_names():
    assert tts_vertex.is_vertex_voice("Kore")
    assert tts_vertex.is_vertex_voice("Charon")
    assert not tts_vertex.is_vertex_voice("en-US-AriaNeural")
    assert not tts_vertex.is_vertex_voice("")


def test_is_vertex_voice_prefixed():
    assert tts_vertex.is_vertex_voice("vertex:Charon")
    assert tts_vertex.is_vertex_voice("vertex:Kore:trailer")
    assert tts_vertex.is_vertex_voice("VERTEX:whatever")  # prefix wins


def test_parse_voice():
    assert tts_vertex.parse_voice("Kore") == ("Kore", tts_vertex.DEFAULT_STYLE)
    assert tts_vertex.parse_voice("vertex:Kore:trailer") == ("Kore", "trailer")
    assert tts_vertex.parse_voice("vertex:notreal:hero") == ("Charon", "hero")
    assert tts_vertex.parse_voice("") == ("Charon", tts_vertex.DEFAULT_STYLE)
    assert tts_vertex.parse_voice("Kore:notastyle") == ("Kore", tts_vertex.DEFAULT_STYLE)


# ---- word timings -----------------------------------------------------------

def test_approx_word_timings_covers_duration():
    words = tts_vertex._approx_word_timings("hello brave new world", 4000)
    assert [w.word for w in words] == ["hello", "brave", "new", "world"]
    assert words[0].tStartMs == 0
    assert words[-1].tEndMs == pytest.approx(4000, abs=2)
    for a, b in zip(words, words[1:]):  # contiguous, monotonic
        assert a.tEndMs == pytest.approx(b.tStartMs, abs=1)
        assert a.tStartMs < a.tEndMs


def test_approx_word_timings_empty():
    assert tts_vertex._approx_word_timings("", 1000) == []
    assert tts_vertex._approx_word_timings("hi", 0) == []


# ---- audio plumbing ---------------------------------------------------------

def test_pcm_to_wav_roundtrip():
    pcm = b"\x00\x01" * 2400  # 0.1 s at 24 kHz mono 16-bit
    blob = tts_vertex._pcm_to_wav(pcm, 24000)
    with wave.open(io.BytesIO(blob)) as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 24000
        assert w.readframes(w.getnframes()) == pcm


def test_pcm_to_wav_empty():
    blob = tts_vertex._pcm_to_wav(b"", 24000)
    with wave.open(io.BytesIO(blob)) as w:
        assert w.getnframes() == 0


def test_access_token_rejects_non_token_output(monkeypatch):
    def fake_run(cmd, **kw):
        class R:
            returncode = 0
            stdout = "ERROR: some gcloud message\nwith lines\n"
            stderr = ""
        return R()

    monkeypatch.setattr(tts_vertex, "_token_cache", ("", 0.0))
    monkeypatch.setattr(tts_vertex.shutil, "which", lambda n: "/usr/bin/gcloud")
    monkeypatch.setattr(tts_vertex.subprocess, "run", fake_run)
    with pytest.raises(tts_vertex.VertexTTSError, match="not an access token"):
        tts_vertex._access_token()


def test_parse_voice_unknown_warns(caplog):
    with caplog.at_level("WARNING", logger="capturd.walk.tts_vertex"):
        assert tts_vertex.parse_voice("NotAVoice")[0] == "Charon"
    assert "falling back to Charon" in caplog.text


def test_synthesize_empty_text_short_circuits():
    assert tts_vertex.synthesize("", "Kore") == (b"", [])
    assert tts_vertex.synthesize("   ", "Kore") == (b"", [])


# ---- token cache ------------------------------------------------------------

def test_access_token_cached(monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        class R:
            returncode = 0
            stdout = "tok-123\n"
            stderr = ""
        return R()

    monkeypatch.setattr(tts_vertex, "_token_cache", ("", 0.0))
    monkeypatch.setattr(tts_vertex.shutil, "which", lambda n: "/usr/bin/gcloud")
    monkeypatch.setattr(tts_vertex.subprocess, "run", fake_run)
    assert tts_vertex._access_token() == "tok-123"
    assert tts_vertex._access_token() == "tok-123"
    assert len(calls) == 1  # second call served from cache


# ---- pipeline error contract ------------------------------------------------

def test_synthesize_one_wraps_vertex_error(monkeypatch):
    def boom(text, voice):
        raise tts_vertex.VertexTTSError("no gcloud")

    monkeypatch.setattr(tts_vertex, "synthesize", boom)
    with pytest.raises(DemoAIError, match="Vertex TTS failed"):
        asyncio.run(_synthesize_one("hello", "vertex:Charon"))


def test_synthesize_one_empty_text_no_engine():
    # Empty text returns before any TTS engine (vertex or edge) is touched.
    assert asyncio.run(_synthesize_one("", "Kore")) == (b"", [])
