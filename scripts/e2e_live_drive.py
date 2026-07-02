"""E2E: the LIVE-DRIVE rail — "talk to it as it records" over real MCP stdio.

Proves the owner's marquee feature: from any chat harness you open a live
session and drive it one instruction at a time — click this, type that,
caption it — and each instruction comes back as a frame you can show in
chat, then the whole thing renders to an MP4.

No LLM gateway needed to DRIVE (the harness is the brain); a local stub
gateway only serves the enrichment stages (annotations/summary/camera) so
the export is deterministic in CI. Headless here (visible=False) so it runs
without a display; the only difference on a real box is visible=True pops
the window on screen.

Usage:
    python scripts/e2e_live_drive.py [--out-dir DIR] [--keep]
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"

# Reuse the stub gateway + MCP stdio client from the agent-mode E2E.
sys.path.insert(0, str(REPO / "scripts"))
from e2e_mcp_stdio import McpStdioClient, StubGateway  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# The owner's instructions, as a chat harness would issue them.
DRIVE_SCRIPT = [
    {"action": "input", "selector": "#username", "value": "demo.user",
     "note": "Type a username for the new account."},
    {"action": "input", "selector": "#password", "value": "hunter2horse",
     "note": "Enter a password."},
    {"action": "click", "selector": "#signup-btn",
     "note": "Click Sign Up to create the account."},
    {"action": "click", "selector": "#confirm-btn",
     "note": "Confirm — this is the button that finishes signup."},
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    work = Path(args.out_dir) if args.out_dir else Path(tempfile.mkdtemp(prefix="capturd-live-"))
    work.mkdir(parents=True, exist_ok=True)
    print(f"[live] work dir: {work}")

    # fixture app
    fixture_port = _free_port()
    handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(  # noqa: E731
        *a, directory=str(FIXTURES), **kw)
    fixture_srv = http.server.ThreadingHTTPServer(("127.0.0.1", fixture_port), handler)
    threading.Thread(target=fixture_srv.serve_forever, daemon=True).start()
    app_url = f"http://127.0.0.1:{fixture_port}/signup-flow.html"
    print(f"[live] fixture app: {app_url}")

    env = dict(os.environ)
    env["CAPTURD_ROOT"] = str(work)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    # stub gateway (enrichment only)
    gw_port = _free_port()
    gw_srv = http.server.ThreadingHTTPServer(("127.0.0.1", gw_port), StubGateway)
    threading.Thread(target=gw_srv.serve_forever, daemon=True).start()
    env["RHOBEAR_GW_BASE_URL"] = f"http://127.0.0.1:{gw_port}/v1"
    env["RHOBEAR_GW_API_KEY"] = "e2e-stub-key"

    client = McpStdioClient([sys.executable, "-m", "capturd.mcp.server"], env=env, cwd=REPO)
    failures: list[str] = []
    frames_dir = work / "stream-frames"
    frames_dir.mkdir(exist_ok=True)
    try:
        client.request("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "live-harness", "version": "0.0.1"},
        })
        client.notify("notifications/initialized")
        names = sorted(t["name"] for t in client.request("tools/list", {}).get("tools", []))
        for needed in ("demo.act", "demo.narrate"):
            if needed not in names:
                failures.append(f"{needed} not registered")
        print(f"[live] tools present: demo.act={('demo.act' in names)} demo.narrate={('demo.narrate' in names)}")

        # open a live session (headless in CI; visible=True pops on screen)
        rec = client.call_tool("demo.record", {
            "url": app_url,
            "name": "Live signup walkthrough",
            "goal": "Create an account, narrated live.",
            "mode": "live",
            "visible": False,
            "viewport": {"width": 1280, "height": 800},
        })
        session_id = rec["sessionId"]
        print(f"[live] demo.record mode={rec.get('mode')} session={session_id}")
        if rec.get("mode") != "live":
            failures.append(f"expected live mode, got {rec.get('mode')}")

        import base64
        # drive it instruction by instruction, saving each streamed frame
        for i, step in enumerate(DRIVE_SCRIPT):
            res = client.call_tool("demo.act", {
                "session_id": session_id,
                "action": step["action"],
                "selector": step.get("selector", ""),
                "value": step.get("value", ""),
                "note": step.get("note", ""),
            })
            frame = res.get("frameBase64") or ""
            ok = bool(frame) and len(frame) > 1000
            if frame:
                (frames_dir / f"act_{i:02d}.jpg").write_bytes(base64.b64decode(frame))
            print(f"[live] act {i} {step['action']} {step.get('selector','')} "
                  f"-> stepIndex={res.get('stepIndex')} frame={'ok' if ok else 'MISSING'}")
            if not ok:
                failures.append(f"act {i} returned no frame")

        # stop + enrich + export
        stop = client.call_tool("demo.stop", {"session_id": session_id}, timeout=120)
        print(f"[live] demo.stop -> stepCount={stop.get('stepCount')} status={stop.get('status')}")
        demo_id = stop.get("demoId", session_id)
        if stop.get("stepCount", 0) != len(DRIVE_SCRIPT):
            failures.append(f"expected {len(DRIVE_SCRIPT)} steps, got {stop.get('stepCount')}")

        for _ in range(90):
            status = client.call_tool("demo.status", {"demo_id": demo_id})
            if status.get("status") in ("enriched", "failed"):
                break
            time.sleep(2)
        print(f"[live] demo.status -> {status.get('status')}")
        if status.get("status") != "enriched":
            failures.append(f"enrichment did not finish: {status}")

        exp = client.call_tool("demo.export", {"demo_id": demo_id, "format": "mp4"}, timeout=900)
        mp4 = Path(exp["path"])
        print(f"[live] demo.export mp4 -> {mp4}")
        if not mp4.is_file() or mp4.stat().st_size < 50_000:
            failures.append(f"mp4 missing/too small: {mp4}")

        # confirm the narration made it into the demo captions
        demo_json = json.loads((work / "demos" / demo_id / "demo.json").read_text(encoding="utf-8"))
        annotations = [s.get("annotation") for s in demo_json.get("steps", [])]
        print(f"[live] captions: {annotations}")
        if not any(a and "button" in a.lower() for a in annotations):
            failures.append("narration note did not land on any step")

        ffprobe = shutil.which("ffprobe")
        if ffprobe:
            probe = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries",
                 "stream=codec_type,codec_name:format=duration", "-of", "json", str(mp4)],
                capture_output=True, text=True,
            )
            info = json.loads(probe.stdout or "{}")
            codecs = {s["codec_type"]: s["codec_name"] for s in info.get("streams", [])}
            print(f"[live] ffprobe: {codecs} dur={info.get('format', {}).get('duration')}")
            if codecs.get("video") != "h264":
                failures.append(f"no h264 stream: {codecs}")

    finally:
        client.close()
        fixture_srv.shutdown()
        gw_srv.shutdown()

    if failures:
        print("\n[live] FAIL:")
        for f in failures:
            print("  -", f)
        print(f"\n--- server stderr ---\n{client.stderr_tail()}")
        return 1
    print("\n[live] PASS — drove the session turn-by-turn from chat, streamed frames, rendered MP4.")
    print(f"[live] artifacts in {work}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
