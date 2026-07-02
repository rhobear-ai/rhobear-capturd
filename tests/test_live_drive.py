"""Live-drive contract tests — the 'talk to it as it records' surface.

Fast, browser-free checks that the wiring is correct. The full end-to-end
(real browser, real MP4) lives in scripts/e2e_live_drive.py.
"""

from __future__ import annotations

import asyncio

import pytest

from capturd.mcp.server import _build_server
from capturd.walk.recorder import DemoManager, DemoRecorder, DemoRecorderError


def _tool_names() -> set[str]:
    server = _build_server()
    tools = asyncio.run(server._list_tools())
    return {t.name for t in tools}


def test_live_drive_tools_registered():
    names = _tool_names()
    assert {"demo.act", "demo.narrate"} <= names
    # the original surface is still there
    assert {"demo.record", "demo.stop", "demo.export"} <= names


def test_start_rejects_unknown_mode(tmp_path):
    mgr = DemoManager(output_root=tmp_path)
    with pytest.raises(DemoRecorderError):
        mgr.start({"url": "https://example.com", "name": "x", "mode": "bogus"})


def test_live_mode_defaults_visible(tmp_path):
    mgr = DemoManager(output_root=tmp_path)
    recorder, _sid, mode = mgr.start(
        {"url": "https://example.com", "name": "x", "mode": "live"}
    )
    assert mode == "live"
    assert recorder.headful is True  # live sessions show by default


def test_agent_mode_defaults_hidden(tmp_path):
    mgr = DemoManager(output_root=tmp_path)
    recorder, _sid, _mode = mgr.start(
        {"url": "https://example.com", "name": "x", "mode": "agent"}
    )
    assert recorder.headful is False


def test_visible_override(tmp_path):
    mgr = DemoManager(output_root=tmp_path)
    recorder, _sid, _mode = mgr.start(
        {"url": "https://example.com", "name": "x", "mode": "agent", "visible": True}
    )
    assert recorder.headful is True


def test_act_before_start_raises(tmp_path):
    rec = DemoRecorder(session_id="s", url="https://example.com", name="x", goal="",
                       output_dir=tmp_path / "s")
    with pytest.raises(DemoRecorderError):
        rec.act("click", "#btn")


def test_narrate_without_steps_raises(tmp_path):
    rec = DemoRecorder(session_id="s", url="https://example.com", name="x", goal="",
                       output_dir=tmp_path / "s")
    with pytest.raises(DemoRecorderError):
        rec.narrate("hello")
