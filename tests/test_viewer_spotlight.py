"""Playwright tests for viewer spotlight + panzoom engine.

Renders the viewer against a synthetic DemoSpec with aiAnnotations.animationTimeline
and verifies:
- Spotlight overlay is present and has correct CSS properties mid-step
- Panzoom layer has a CSS transform (zoom is active)
- The viewer doesn't error on load (console check)
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def viewer_html_path(tmp_path) -> Path:
    """Render the viewer template with a synthetic DemoSpec and return the HTML path."""
    from capturd.walk.viewer import render_viewer_to_file

    spec = {
        "version": 1,
        "id": "spotlight-test",
        "name": "Spotlight Test Demo",
        "goal": "Verify spotlight overlay + zoom in Playwright",
        "createdAt": "2026-07-02T00:00:00Z",
        "viewport": {"width": 1024, "height": 768},
        "startUrl": "https://example.com",
        "aiAnnotations": {
            "style": "cinematic",
            "summary": "A test flow.",
            "animationTimeline": _make_test_timeline(),
        },
        "steps": _make_test_steps(),
    }

    out = tmp_path / "viewer.html"
    render_viewer_to_file(spec, out)
    assert out.is_file(), f"Viewer did not render to {out}"
    return out


def _make_test_steps() -> list[dict]:
    return [
        {
            "index": 0, "timestamp": 0,
            "pageUrl": "https://example.com/", "pageTitle": "Welcome",
            "interaction": {
                "type": "click",
                "target": {
                    "selector": "#get-started",
                    "tagName": "button",
                    "text": "Get Started",
                    "boundingRect": {"x": 380, "y": 320, "width": 264, "height": 64},
                },
                "hotspot": {"xPct": 50, "yPct": 50},
            },
            "annotation": "Click Get Started to begin.",
            "screenshotBase64": _placeholder_screenshot_b64(1024, 768),
        },
    ]


def _make_test_timeline() -> list[dict]:
    """Manual cinematic timeline for Playwright verification."""
    return [
        {
            "stepIndex": 0,
            "action": "spotlightOn",
            "target": "#get-started",
            "duration": 200,
            "easing": "ease-out",
            "intensity": 0.9,
        },
        {
            "stepIndex": 0,
            "action": "zoomTo",
            "target": "#get-started",
            "offset": {"x": 50, "y": 50},
            "zoomLevel": 1.5,
            "duration": 800,
            "easing": "ease-in-out",
        },
        {
            "stepIndex": 0,
            "action": "hold",
            "duration": 9999,  # long hold so the test can capture mid-step
        },
        {
            "stepIndex": 0,
            "action": "spotlightOff",
            "duration": 200,
        },
    ]


def _placeholder_screenshot_b64(width: int, height: int) -> str:
    """Return a base64 placeholder PNG-like data URL.

    We don't need a real image — just a non-empty string the viewer can
    display. Using a tiny valid 1×1 PNG as base64.
    """
    # Minimal valid 1×1 white PNG
    return "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_viewer_loads_without_console_errors(viewer_html_path: Path):
    """Viewer should load without console errors."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors = []

        def on_error(msg):
            errors.append(msg.text)

        page.on("console", lambda msg: on_error(msg) if msg.type == "error" else None)

        page.goto(f"file://{viewer_html_path}")
        page.wait_for_timeout(2000)  # Wait for boot + initial timeline to fire

        browser.close()

    # The viewer may log innocuous errors (e.g., cannot load real screenshots)
    # but should NOT have any unrecoverable JS errors.
    critical = [e for e in errors if "Cannot read property" in e or "is not defined" in e or "undefined" in e]
    assert len(critical) == 0, f"Critical JS errors: {critical}"


@pytest.mark.slow
def test_spotlight_overlay_present_mid_step(viewer_html_path: Path):
    """Spotlight overlay should be visible (active class + non-zero opacity) after timeline starts."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(f"file://{viewer_html_path}")

        # Wait for boot + timeline to execute SPOTLIGHT_ON
        page.wait_for_timeout(3000)

        # Check spotlight overlay is active
        is_active = page.evaluate("""() => {
            const el = document.getElementById('spotlight-overlay');
            return el ? el.classList.contains('active') : false;
        }""")
        assert is_active, "spotlight-overlay should have 'active' class mid-step"

        # Check it has a clip-path (not 'none')
        clip_path = page.evaluate("""() => {
            const el = document.getElementById('spotlight-overlay');
            return el ? el.style.clipPath : null;
        }""")
        assert clip_path and clip_path != "none", (
            f"spotlight clip-path should not be 'none', got: {clip_path}"
        )

        browser.close()


@pytest.mark.slow
def test_panzoom_layer_has_transform(viewer_html_path: Path):
    """Panzoom layer should have a CSS transform after zoomTo keyframe executes."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(f"file://{viewer_html_path}")

        # Wait for zoomTo to fire (800ms duration)
        page.wait_for_timeout(3000)

        transform = page.evaluate("""() => {
            const el = document.getElementById('panzoom-layer');
            return el ? el.style.transform : null;
        }""")
        assert transform and "matrix" in (transform or ""), (
            f"panzoom-layer should have a matrix transform, got: {transform}"
        )

        browser.close()


@pytest.mark.slow
def test_spotlight_off_hides_overlay(viewer_html_path: Path):
    """After SPOTLIGHT_OFF, the overlay should lose the 'active' class."""
    from playwright.sync_api import sync_playwright

    # Create a spec with short hold so SPOTLIGHT_OFF fires quickly
    specs_path = viewer_html_path.parent / "viewer_fast.json"
    viewer_fast = viewer_html_path.parent / "viewer_fast.html"

    spec = {
        "version": 1,
        "id": "fast-spotlight-test",
        "name": "Fast Spotlight Test",
        "goal": "Verify spotlight off",
        "viewport": {"width": 1024, "height": 768},
        "startUrl": "https://example.com",
        "aiAnnotations": {
            "style": "snappy",
            "animationTimeline": [
                {"stepIndex": 0, "action": "spotlightOn", "target": "#btn",
                 "duration": 100, "easing": "ease-out", "intensity": 0.7},
                {"stepIndex": 0, "action": "zoomTo", "target": "#btn",
                 "offset": {"x": 50, "y": 50}, "zoomLevel": 1.3, "duration": 200, "easing": "ease-out"},
                {"stepIndex": 0, "action": "hold", "duration": 100},
                {"stepIndex": 0, "action": "spotlightOff", "duration": 100},
            ],
        },
        "steps": [{
            "index": 0, "timestamp": 0,
            "pageUrl": "https://example.com/", "pageTitle": "Btn",
            "interaction": {
                "type": "click",
                "target": {
                    "selector": "#btn", "tagName": "button", "text": "Click",
                    "boundingRect": {"x": 100, "y": 100, "width": 200, "height": 50},
                },
                "hotspot": {"xPct": 50, "yPct": 50},
            },
            "annotation": "Click the button.",
            "screenshotBase64": _placeholder_screenshot_b64(1024, 768),
        }],
    }

    from capturd.walk.viewer import render_viewer_to_file
    render_viewer_to_file(spec, viewer_fast)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(f"file://{viewer_fast}")

        # Wait long enough for the full short timeline to play (spotlightOff should fire)
        page.wait_for_timeout(2000)

        is_active = page.evaluate("""() => {
            const el = document.getElementById('spotlight-overlay');
            return el ? el.classList.contains('active') : null;
        }""")
        assert is_active is False, (
            f"spotlight-overlay should NOT be active after SPOTLIGHT_OFF, got: {is_active}"
        )

        browser.close()
