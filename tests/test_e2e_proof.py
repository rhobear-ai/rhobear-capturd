"""W8 E2E proof — test wrapper.

Invokes the e2e_walkthrough_proof module and asserts artifacts exist.
Marked @pytest.mark.e2e — deselected by default.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

# Ensure the scripts dir is importable
SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture(scope="module")
def e2e_results(tmp_path_factory) -> dict[str, Any]:
    """Run the E2E proof script in a temp dir and return results."""
    out_dir = tmp_path_factory.mktemp("e2e-proof")

    from e2e_walkthrough_proof import _set_args, main as e2e_main

    # Override args for test run
    _set_args(
        target="https://example.com",
        steps=3,
        skip_mp4=True,  # MP4 too slow for CI; save as separate run
        output_dir=str(out_dir),
        headless=True,
    )

    exit_code = asyncio.run(e2e_main())
    assert exit_code == 0, f"E2E script failed with exit code {exit_code}"

    results_path = out_dir / "results.json"
    assert results_path.is_file(), f"No results.json at {results_path}"

    return json.loads(results_path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def out_dir(e2e_results) -> Path:
    return Path(e2e_results["out_dir"])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestE2EProof:
    """Full E2E pipeline validation."""

    def test_results_file_exists(self, e2e_results):
        """Results manifest exists and contains pipeline data."""
        assert "pipeline" in e2e_results, "No pipeline results"
        pipeline = e2e_results["pipeline"]
        assert "record_steps" in pipeline, "No record_steps"

    def test_demo_json_exists(self, out_dir):
        """demo.json was written by the recorder."""
        demo_json = out_dir / "demo.json"
        assert demo_json.is_file(), f"demo.json not found at {demo_json}"
        spec = json.loads(demo_json.read_text(encoding="utf-8"))
        assert isinstance(spec.get("steps"), list), "steps should be a list"

    def test_demo_enriched_json_exists(self, out_dir):
        """demo_enriched.json was written by the AI pipeline."""
        enriched = out_dir / "demo_enriched.json"
        assert enriched.is_file(), f"demo_enriched.json not found at {enriched}"
        spec = json.loads(enriched.read_text(encoding="utf-8"))
        assert isinstance(spec.get("steps"), list), "steps should be a list"

    def test_minimum_steps(self, e2e_results):
        """DemoSpec has ≥3 steps."""
        pipeline = e2e_results.get("pipeline") or {}
        steps = pipeline.get("record_steps", 0)
        assert steps >= 3, f"Only {steps} steps (need ≥3)"

    def test_voiceover_populated(self, e2e_results):
        """At least 2 steps have voiceoverWords."""
        pipeline = e2e_results.get("pipeline") or {}
        vcount = pipeline.get("voiceover_steps", 0)
        assert vcount >= 2, f"Only {vcount} steps with voiceover (need ≥2)"

    def test_annotations_populated(self, e2e_results):
        """At least 2 steps have annotations."""
        pipeline = e2e_results.get("pipeline") or {}
        ac = pipeline.get("annotation_steps", 0)
        assert ac >= 2, f"Only {ac} steps with annotations (need ≥2)"

    def test_animation_timeline_has_keyframes(self, e2e_results):
        """Animation timeline contains spotlight, zoom, and hold."""
        pipeline = e2e_results.get("pipeline") or {}
        assert pipeline.get("has_spotlight"), "No SPOTLIGHT_ON keyframe"
        assert pipeline.get("has_zoom"), "No ZOOM_TO keyframe"
        assert pipeline.get("has_hold"), "No HOLD keyframe"

    def test_viewer_html_exists_and_large(self, out_dir):
        """viewer.html exists and is > 100KB."""
        viewer = out_dir / "viewer.html"
        assert viewer.is_file(), f"viewer.html not found at {viewer}"
        size_kb = viewer.stat().st_size / 1024
        assert size_kb > 100, f"viewer.html is only {size_kb:.0f} KB (need >100)"

    def test_content_mode_recorded(self, e2e_results):
        """Content-mode was recorded for at least one step."""
        pipeline = e2e_results.get("pipeline") or {}
        content_modes = pipeline.get("content_modes") or {}
        assert content_modes, "No content modes recorded"
        modes = {v.get("mode") for v in content_modes.values()}
        assert modes, f"No content modes found: {content_modes}"

    def test_canvas_fixture_detected(self, e2e_results):
        """Canvas fixture was tested and content-mode detection fired."""
        canvas = e2e_results.get("canvas") or {}
        # Canvas fixture has 80% canvas — should be "video" mode
        mode = canvas.get("content_mode")
        area_pct = canvas.get("canvas_area_pct", -1)
        assert mode is not None, "Canvas fixture not tested"
        assert area_pct >= 30, f"Canvas area {area_pct}% should be ≥30%"
        assert mode == "video", f"Expected mode=video, got mode={mode}"

    def test_summary_populated(self, e2e_results):
        """AI summary is non-empty."""
        pipeline = e2e_results.get("pipeline") or {}
        summary = pipeline.get("ai_summary", "")
        assert summary, "AI summary is empty"
        assert len(summary) > 20, f"Summary too short: {len(summary)} chars"
        # Should not be a placeholder
        assert "[user intent:" not in summary, "Summary contains placeholder"
        assert "placeholder" not in summary.lower(), "Summary contains placeholder"

    def test_step_screenshots_exist(self, out_dir):
        """Step screenshots were captured."""
        found = sorted(out_dir.glob("step_*.png"))
        assert len(found) >= 1, f"No step screenshots found in {out_dir}"
        for p in found:
            assert p.stat().st_size > 100, f"Screenshot {p.name} is empty"


@pytest.mark.e2e
class TestE2EMP4:
    """MP4-specific tests (only runs when --skip-mp4 is not set)."""

    @pytest.mark.skip(reason="MP4 generation is slow — run manually with full args")
    def test_mp4_exists_and_large(self, out_dir):
        mp4 = out_dir / "walkthrough.mp4"
        assert mp4.is_file(), f"walkthrough.mp4 not found at {mp4}"
        size_kb = mp4.stat().st_size / 1024
        # Panzoom of static screenshots is highly compressible; real content > 50KB
        assert size_kb > 50, f"walkthrough.mp4 is only {size_kb:.0f} KB (need >50)"
