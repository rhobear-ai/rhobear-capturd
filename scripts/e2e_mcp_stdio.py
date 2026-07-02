"""E2E: drive the Captur'd MCP server over real stdio, exactly like an external harness.

What this proves (the owner's actual use case):
    another agent, in another process, speaking MCP over stdio, can say
    "record a walkthrough of this app" and get back a Supademo-style MP4
    with zoom/pan camera, flying cursor, spotlight, captions and voiceover.

How it runs without a real LLM key:
    a local OpenAI-compatible stub gateway serves scripted agent actions,
    annotations, a summary, and a camera timeline. The MCP server subprocess
    is pointed at it via RHOBEAR_GW_BASE_URL. Everything else — recorder,
    enrichment, TTS, viewer render, frame capture, ffmpeg — is the real
    production path. Run against the real gateway by simply not passing
    --stub-gateway (requires RHOBEAR_GW_API_KEY).

Usage:
    python scripts/e2e_mcp_stdio.py [--out-dir DIR] [--keep]
"""

from __future__ import annotations

import argparse
import base64
import http.server
import json
import os
import re
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


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Stub OpenAI-compatible gateway — deterministic script for the signup flow
# ---------------------------------------------------------------------------

AGENT_SCRIPT = [
    {"action": "input", "selector": "#username", "value": "demo.user"},
    {"action": "input", "selector": "#password", "value": "correct-horse-battery"},
    {"action": "click", "selector": "#signup-btn"},
    {"action": "click", "selector": "#confirm-btn"},
    {"action": "done"},
]

ANNOTATIONS = {
    "#username": "Enter a username for the new account.",
    "#password": "Choose a secure password.",
    "#signup-btn": "Click Sign Up to create the account.",
    "#confirm-btn": "Confirm the account creation.",
}


class StubGateway(http.server.BaseHTTPRequestHandler):
    agent_turn = 0
    lock = threading.Lock()

    def log_message(self, *args):  # silence request logging
        pass

    def _texts(self, body: dict) -> str:
        out = []
        for m in body.get("messages", []):
            c = m.get("content")
            if isinstance(c, str):
                out.append(c)
            elif isinstance(c, list):
                for part in c:
                    if isinstance(part, dict) and part.get("type") == "text":
                        out.append(part.get("text", ""))
        return "\n".join(out)

    def _reply(self, text: str) -> None:
        payload = {
            "id": "stub-1", "object": "chat.completion", "created": 0,
            "model": "stub",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        prompt = self._texts(body)

        if "Decide the NEXT action" in prompt:
            with StubGateway.lock:
                i = StubGateway.agent_turn
                StubGateway.agent_turn += 1
            step = AGENT_SCRIPT[i] if i < len(AGENT_SCRIPT) else {"action": "done"}
            return self._reply(json.dumps(step))

        if "Describe in ONE sentence" in prompt:
            m = re.search(r"User clicked: (\S+)", prompt)
            sel = m.group(1) if m else ""
            return self._reply(ANNOTATIONS.get(sel, "Interact with the highlighted element."))

        if "Write a 2-3 sentence summary" in prompt:
            return self._reply(
                "This demo walks through creating a new account: the user "
                "enters a username and password, submits the sign-up form, "
                "and confirms the account. The flow ends on the welcome screen."
            )

        if "directing a cinematic camera" in prompt:
            m = re.search(r"Steps \((\d+) total\)", prompt)
            n = int(m.group(1)) if m else 4
            kfs = []
            for idx in range(n):
                kfs += [
                    {"stepIndex": idx, "action": "spotlightOn", "target": "body",
                     "duration": 200, "intensity": 0.75},
                    {"stepIndex": idx, "action": "zoomTo", "target": "body",
                     "offset": {"x": 50, "y": 50}, "zoomLevel": 1.8,
                     "duration": 650, "easing": "ease-out"},
                    {"stepIndex": idx, "action": "hold", "duration": 2400},
                    {"stepIndex": idx, "action": "spotlightOff", "duration": 200},
                ]
            return self._reply(json.dumps(kfs))

        return self._reply("OK.")


# ---------------------------------------------------------------------------
# Minimal MCP stdio client (newline-delimited JSON-RPC 2.0)
# ---------------------------------------------------------------------------


class McpStdioClient:
    def __init__(self, cmd: list[str], env: dict[str, str], cwd: Path) -> None:
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            cwd=str(cwd),
            env=env,
        )
        self._id = 0
        self._stderr_lines: list[str] = []
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self._stderr_lines.append(line.rstrip())

    def stderr_tail(self, n: int = 40) -> str:
        return "\n".join(self._stderr_lines[-n:])

    def _send(self, obj: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def notify(self, method: str, params: dict | None = None) -> None:
        msg: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)

    def request(self, method: str, params: dict | None = None, timeout: float = 120.0) -> dict:
        self._id += 1
        rid = self._id
        msg: dict = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)

        deadline = time.time() + timeout
        assert self.proc.stdout is not None
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError(
                    f"MCP server closed stdout during {method}.\n"
                    f"--- server stderr ---\n{self.stderr_tail()}"
                )
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("id") == rid:
                if "error" in obj:
                    raise RuntimeError(f"{method} failed: {obj['error']}")
                return obj.get("result", {})
            # anything else: server notification/log — ignore
        raise TimeoutError(f"no response to {method} within {timeout}s")

    def call_tool(self, name: str, arguments: dict, timeout: float = 120.0) -> dict:
        result = self.request(
            "tools/call", {"name": name, "arguments": arguments}, timeout=timeout
        )
        if result.get("isError"):
            raise RuntimeError(f"tool {name} error: {result}")
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            return structured
        for item in result.get("content", []):
            if item.get("type") == "text":
                try:
                    return json.loads(item["text"])
                except json.JSONDecodeError:
                    return {"text": item["text"]}
        return result

    def close(self) -> None:
        try:
            self.proc.terminate()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="", help="working dir (default: temp)")
    ap.add_argument("--keep", action="store_true", help="keep the working dir")
    ap.add_argument("--real-gateway", action="store_true",
                    help="use the real gateway from env instead of the stub")
    args = ap.parse_args()

    work = Path(args.out_dir) if args.out_dir else Path(tempfile.mkdtemp(prefix="capturd-e2e-"))
    work.mkdir(parents=True, exist_ok=True)
    print(f"[e2e] work dir: {work}")

    # ---- 1. fixture app over HTTP -------------------------------------
    fixture_port = _free_port()
    fixture_handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(  # noqa: E731
        *a, directory=str(FIXTURES), **kw)
    fixture_srv = http.server.ThreadingHTTPServer(("127.0.0.1", fixture_port), fixture_handler)
    threading.Thread(target=fixture_srv.serve_forever, daemon=True).start()
    app_url = f"http://127.0.0.1:{fixture_port}/signup-flow.html"
    print(f"[e2e] fixture app: {app_url}")

    env = dict(os.environ)
    env["CAPTURD_ROOT"] = str(work)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    # ---- 2. stub gateway ----------------------------------------------
    gw_srv = None
    if not args.real_gateway:
        gw_port = _free_port()
        gw_srv = http.server.ThreadingHTTPServer(("127.0.0.1", gw_port), StubGateway)
        threading.Thread(target=gw_srv.serve_forever, daemon=True).start()
        env["RHOBEAR_GW_BASE_URL"] = f"http://127.0.0.1:{gw_port}/v1"
        env["RHOBEAR_GW_API_KEY"] = "e2e-stub-key"
        print(f"[e2e] stub gateway: http://127.0.0.1:{gw_port}/v1")

    # ---- 3. MCP server over stdio --------------------------------------
    client = McpStdioClient(
        [sys.executable, "-m", "capturd.mcp.server"], env=env, cwd=REPO
    )
    failures: list[str] = []
    try:
        init = client.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "e2e-harness", "version": "0.0.1"},
        })
        print(f"[e2e] initialized: {init.get('serverInfo', {})}")
        client.notify("notifications/initialized")

        tools = client.request("tools/list", {})
        names = sorted(t["name"] for t in tools.get("tools", []))
        print(f"[e2e] {len(names)} tools: {', '.join(names)}")

        # -- record (agent mode) ----------------------------------------
        rec = client.call_tool("demo.record", {
            "url": app_url,
            "name": "Signup walkthrough",
            "goal": "Create a new account: fill username and password, sign up, confirm.",
            "mode": "agent",
            "viewport": {"width": 1280, "height": 800},
        })
        session_id = rec["sessionId"]
        print(f"[e2e] demo.record -> session {session_id} ({rec.get('mode')})")

        # -- stop: waits for the agent to finish, kicks enrichment -------
        stop = client.call_tool("demo.stop", {"session_id": session_id}, timeout=360)
        print(f"[e2e] demo.stop -> {stop}")
        demo_id = stop.get("demoId", session_id)
        if stop.get("stepCount", 0) < 3:
            failures.append(f"expected >=3 recorded steps, got {stop.get('stepCount')}")

        # -- poll status until enriched ----------------------------------
        status = {}
        for _ in range(120):
            status = client.call_tool("demo.status", {"demo_id": demo_id})
            if status.get("status") in ("enriched", "failed"):
                break
            time.sleep(2)
        print(f"[e2e] demo.status -> {status}")
        if status.get("status") != "enriched":
            failures.append(f"enrichment did not complete: {status}")

        # -- idempotent second stop --------------------------------------
        stop2 = client.call_tool("demo.stop", {"session_id": session_id})
        print(f"[e2e] demo.stop (2nd, idempotent) -> status={stop2.get('status')}")

        # -- export mp4 ---------------------------------------------------
        t0 = time.time()
        exp = client.call_tool(
            "demo.export", {"demo_id": demo_id, "format": "mp4"}, timeout=900
        )
        mp4 = Path(exp["path"])
        print(f"[e2e] demo.export mp4 -> {mp4} in {time.time() - t0:.0f}s")
        if not mp4.is_file() or mp4.stat().st_size < 50_000:
            failures.append(f"mp4 missing or too small: {mp4}")

        # -- export html too ---------------------------------------------
        exp_html = client.call_tool("demo.export", {"demo_id": demo_id, "format": "html"})
        html_path = Path(exp_html["path"])
        if not html_path.is_file():
            failures.append("html export missing")
        print(f"[e2e] demo.export html -> {html_path}")

        # -- probe the video ----------------------------------------------
        ffprobe = shutil.which("ffprobe")
        if ffprobe:
            probe = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries",
                 "stream=codec_type,codec_name:format=duration",
                 "-of", "json", str(mp4)],
                capture_output=True, text=True,
            )
            info = json.loads(probe.stdout or "{}")
            codecs = {s["codec_type"]: s["codec_name"] for s in info.get("streams", [])}
            duration = float(info.get("format", {}).get("duration", 0))
            print(f"[e2e] ffprobe: streams={codecs} duration={duration:.1f}s")
            if codecs.get("video") != "h264":
                failures.append(f"no h264 video stream: {codecs}")
            if "audio" not in codecs:
                failures.append(f"no audio stream (voiceover missing): {codecs}")
            if duration < 8:
                failures.append(f"video too short: {duration:.1f}s")

        # -- dump inspection frames ---------------------------------------
        ffmpeg = shutil.which("ffmpeg")
        frames_out = work / "inspect"
        if ffmpeg and mp4.is_file():
            frames_out.mkdir(exist_ok=True)
            dur = duration if ffprobe else 15
            for ts in [0.5, dur * 0.22, dur * 0.4, dur * 0.6, dur * 0.8, dur - 0.5]:
                out = frames_out / f"t{ts:05.1f}s.png"
                subprocess.run(
                    [ffmpeg, "-y", "-ss", f"{ts:.2f}", "-i", str(mp4),
                     "-frames:v", "1", str(out)],
                    capture_output=True,
                )
            print(f"[e2e] inspection frames -> {frames_out}")

    finally:
        client.close()
        fixture_srv.shutdown()
        if gw_srv:
            gw_srv.shutdown()

    if failures:
        print("\n[e2e] FAIL:")
        for f in failures:
            print(f"  - {f}")
        print(f"\n--- server stderr tail ---\n{client.stderr_tail()}")
        return 1
    print("\n[e2e] PASS — agent-driven MCP walkthrough produced a real MP4.")
    print(f"[e2e] artifacts in {work}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
