"""W3 voice-sync tests — real edge-tts WordBoundary → WordTimestamp pipeline.

These are NOT mocked. The whole point is that edge-tts returns real
per-word timestamps the camera keyframe system will anchor to.
"""

from __future__ import annotations

import asyncio

import pytest


# ---------------------------------------------------------------------------
# Direct _synthesize_one contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesize_one_returns_tuple_with_word_timestamps() -> None:
    """_synthesize_one must return (bytes, [WordTimestamp]) from a real TTS call."""
    from capturd.walk.ai_pipeline import _synthesize_one

    result = await _synthesize_one(
        "The quick brown fox jumps over the lazy dog",
        voice="en-US-AriaNeural",
    )

    # 1. Returns tuple.
    assert isinstance(result, tuple), f"expected tuple, got {type(result)}"
    assert len(result) == 2, f"expected (bytes, list), got len={len(result)}"
    audio, words = result

    # 2. Audio bytes non-empty.
    assert isinstance(audio, bytes), f"audio must be bytes, got {type(audio)}"
    assert len(audio) > 0, "audio bytes must be non-empty"

    # 3. Word timestamps list has 9 entries (one per word).
    assert isinstance(words, list), f"words must be list, got {type(words)}"
    assert len(words) == 9, (
        f"expected 9 words for 'The quick brown fox jumps over the lazy dog', "
        f"got {len(words)}: {[w.word for w in words]}"
    )

    # 4. Timestamps are monotonically increasing.
    prev_end = 0
    for i, wt in enumerate(words):
        assert wt.tEndMs > wt.tStartMs, (
            f"word [{i}] '{wt.word}': tEndMs={wt.tEndMs} must be > "
            f"tStartMs={wt.tStartMs}"
        )
        assert wt.tStartMs >= prev_end, (
            f"word [{i}] '{wt.word}': tStartMs={wt.tStartMs} must be >= "
            f"previous.tEndMs={prev_end}"
        )
        prev_end = wt.tEndMs


@pytest.mark.asyncio
async def test_synthesize_one_empty_text() -> None:
    """Empty / whitespace text returns empty bytes and empty word list."""
    from capturd.walk.ai_pipeline import _synthesize_one

    audio, words = await _synthesize_one("", voice="en-US-AriaNeural")
    assert audio == b""
    assert words == []

    audio2, words2 = await _synthesize_one("   ", voice="en-US-AriaNeural")
    assert audio2 == b""
    assert words2 == []


# ---------------------------------------------------------------------------
# Pipeline round-trip: DemoAI.enrich populates voiceoverWords
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_roundtrip_populates_voiceover_words() -> None:
    """DemoAI._synthesize_voiceover on a 1-step fixture stores voiceoverWords.

    We call _synthesize_voiceover directly (skipping LLM stages that need the
    RHOBEAR GW API key) since the step already has a pre-seeded annotation.
    """
    from capturd.walk.ai_pipeline import DemoAI

    spec = {
        "version": 1,
        "id": "w3-roundtrip",
        "name": "voice-sync fixture",
        "goal": "verify W3 pipeline field",
        "startUrl": "https://example.com",
        "steps": [
            {
                "index": 0,
                "timestamp": 0,
                "pageUrl": "https://example.com",
                "pageTitle": "Example Step",
                "interaction": {
                    "type": "click",
                    "target": {
                        "selector": "#buy-now",
                        "tagName": "button",
                        "text": "Buy Now",
                        "boundingRect": {
                            "x": 100, "y": 200, "width": 120, "height": 40,
                        },
                    },
                    "hotspot": {"xPct": 50.0, "yPct": 50.0},
                },
                # Pre-seeded annotation so we don't need the vision LLM.
                "annotation": "Clicked the Buy Now button to begin checkout.",
                "screenshotPath": "",
            }
        ],
    }

    ai = DemoAI()
    # Only run the voiceover stage — LLM stages need the GW API key.
    await ai._synthesize_voiceover(spec)

    steps = spec.get("steps", [])
    assert isinstance(steps, list)
    assert len(steps) == 1

    step0 = steps[0]
    words = step0.get("voiceoverWords")
    assert words is not None, "voiceoverWords must be populated"
    assert isinstance(words, list), (
        f"voiceoverWords must be a list of dicts, got {type(words)}"
    )
    assert len(words) > 0, "voiceoverWords must be non-empty"

    # Every entry must be a dict with word / tStartMs / tEndMs keys.
    for i, entry in enumerate(words):
        assert isinstance(entry, dict), (
            f"voiceoverWords[{i}] must be a dict, got {type(entry)}"
        )
        assert "word" in entry, f"voiceoverWords[{i}] missing 'word' key"
        assert "tStartMs" in entry, f"voiceoverWords[{i}] missing 'tStartMs' key"
        assert "tEndMs" in entry, f"voiceoverWords[{i}] missing 'tEndMs' key"
        assert isinstance(entry["word"], str)
        assert isinstance(entry["tStartMs"], int)
        assert isinstance(entry["tEndMs"], int)
        assert entry["tEndMs"] > entry["tStartMs"], (
            f"voiceoverWords[{i}] '{entry['word']}': tEndMs must be > tStartMs"
        )

    # Verify voiceoverBase64 is still populated (backwards compat).
    assert step0.get("voiceoverBase64"), "voiceoverBase64 must still be populated"
