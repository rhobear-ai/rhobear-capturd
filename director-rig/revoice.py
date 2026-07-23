"""Swap the voiceover voice on an ALREADY-FILMED demo — no refilm, free (edge-tts).

Re-synthesizes every step's voiceover in the new voice via demo.edit
(regenerate_voice), then re-exports. Works on any demo directory produced by
rig/film.py (or any CAPTURD_ROOT).

Usage (from the sunsponge-capture repo dir):
    python revoice.py <capturd_root> <demo_id> <voice> <out_dir> [format]
e.g.
    python revoice.py D:/capturd-plans-showcase/login-flow/work e89723ca9231 en-US-GuyNeural D:/capturd-plans-showcase/login-flow mp4

Voices: `edge-tts --list-voices` (hundreds, free). Good picks:
en-US-GuyNeural (male), en-US-AriaNeural (default female), en-US-JennyNeural,
en-GB-SoniaNeural, en-GB-RyanNeural, en-AU-NatashaNeural.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

REPO = Path(os.environ.get("CAPTURD_REPO", "/opt/sunsponge-capture"))
sys.path.insert(0, str(REPO / "scripts"))
from e2e_mcp_stdio import McpStdioClient  # noqa: E402


def main() -> int:
    root, demo_id, voice, out_dir = sys.argv[1:5]
    fmt = sys.argv[5] if len(sys.argv) > 5 else "mp4"
    demo_json = Path(root) / "demos" / demo_id / "demo.json"
    steps = json.loads(demo_json.read_text(encoding="utf-8")).get("steps", [])

    env = dict(os.environ)
    env["CAPTURD_ROOT"] = str(root)
    env["CAPTURD_VOICE"] = voice
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    boot = Path(__file__).resolve().parent / "paid_boot.py"
    client = McpStdioClient([sys.executable, str(boot)], env=env, cwd=REPO)
    try:
        client.request("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "revoice", "version": "1"},
        })
        client.notify("notifications/initialized")
        n = 0
        for s in steps:
            if (s.get("annotation") or "").strip():
                client.call_tool("demo.edit", {"demo_id": demo_id,
                                               "step_index": s["index"],
                                               "regenerate_voice": True}, timeout=120)
                n += 1
        print(f"[revoice] regenerated {n} voice clips as {voice}")
        exp = client.call_tool("demo.export", {"demo_id": demo_id, "format": fmt}, timeout=900)
        src = Path(exp["path"])
        dst = Path(out_dir) / src.name
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print(f"[revoice] export -> {dst} ({dst.stat().st_size} bytes)")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
