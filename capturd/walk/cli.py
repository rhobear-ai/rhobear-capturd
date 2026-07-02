"""`capturd walk` CLI — thin wrapper over the same coordinator the MCP surface uses.

Subcommands: record / stop / status / list / export / edit / delete.
Both the human CLI and the agent MCP surface land on the same code path
(``capturd.walk.coordinator.DemoForge``) — no duplicate business logic.

W1 (recorder-as-agent-entrypoint), W2 (zoom pipeline), W3 (voice-sync), W4
(expanded MCP surface) will fill in the actual implementations. This file
is the argparse skeleton the workers hang their work on.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="capturd walk",
        description="Agent-made interactive product walkthroughs.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("record", help="Start a walkthrough recording session")
    p.add_argument("--url", required=True, help="URL to walk")
    p.add_argument("--name", required=True, help="Demo name")
    p.add_argument("--goal", required=True, help="What the flow demonstrates")
    p.add_argument("--agent", action="store_true",
                   help="Agent-driven (LLM picks each next click). Default: headful, human clicks.")
    p.add_argument("--voice", action="store_true",
                   help="Enable push-to-talk voice input (mic button on overlay).")
    p.add_argument("--workflow", action="store_true",
                   help="Workflow mode: agent asks 'what are you illustrating?' after each click. "
                        "Automatically enables --voice.")
    p.add_argument("--viewport", default="1440x900", help="Recording viewport (WxH)")

    p = sub.add_parser("stop", help="Stop a recording and kick off AI enrichment")
    p.add_argument("--session-id", required=True)

    p = sub.add_parser("status", help="Check enrichment progress for a demo")
    p.add_argument("--demo-id", required=True)

    p = sub.add_parser("list", help="List recorded demos")
    p.add_argument("--json", action="store_true", help="Machine-readable output")

    p = sub.add_parser("export", help="Render a demo as viewer HTML / MP4 / GIF")
    p.add_argument("--demo-id", required=True)
    p.add_argument("--format", choices=["html", "mp4", "gif"], default="html")
    p.add_argument("--out", help="Output path (default: alongside the demo JSON)")

    p = sub.add_parser("edit", help="Edit a step's annotation / voiceover / camera")
    p.add_argument("--demo-id", required=True)
    p.add_argument("--step", type=int, required=True)
    p.add_argument("--annotation", help="Rewrite the annotation")
    p.add_argument("--regenerate-voice", action="store_true")

    p = sub.add_parser("delete", help="Delete a demo and its files")
    p.add_argument("--demo-id", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    dispatch = {
        "record": _cmd_record,
        "stop": _cmd_stop,
        "status": _cmd_status,
        "list": _cmd_list,
        "export": _cmd_export,
        "edit": _cmd_edit,
        "delete": _cmd_delete,
    }
    return dispatch[args.cmd](args)


def _forge():
    from capturd.walk.coordinator import DemoForge

    return DemoForge()


def _cmd_stop(args: argparse.Namespace) -> int:
    """Kick off enrichment for a recorded session (its demo.json on disk)."""
    from capturd.walk.coordinator import DemoForgeError

    forge = _forge()
    try:
        job = forge.enrich_demo(args.session_id)
    except DemoForgeError as exc:
        sys.stderr.write(f"stop failed: {exc}\n")
        return 1
    sys.stdout.write(json.dumps(job, indent=2) + "\n")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    from capturd.walk.coordinator import DemoForgeError

    try:
        status = _forge().get_status(args.demo_id)
    except DemoForgeError as exc:
        sys.stderr.write(f"status failed: {exc}\n")
        return 1
    sys.stdout.write(json.dumps(status, indent=2) + "\n")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    demos = _forge().list_demos()
    if args.json:
        sys.stdout.write(json.dumps([d.__dict__ for d in demos], indent=2) + "\n")
        return 0
    if not demos:
        sys.stdout.write("no demos recorded yet\n")
        return 0
    for d in demos:
        voice = " voice" if d.has_voiceover else ""
        sys.stdout.write(
            f"{d.demo_id}  {d.status:9s} {d.step_count:3d} steps{voice}  "
            f"{d.name}\n"
        )
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    from capturd.walk.coordinator import DemoForgeError

    try:
        out = _forge().export_demo(args.demo_id, fmt=args.format)
    except DemoForgeError as exc:
        sys.stderr.write(f"export failed: {exc}\n")
        return 1
    if args.out:
        import shutil
        dest = args.out
        shutil.copyfile(out, dest)
        out = dest
    sys.stdout.write(json.dumps({"path": str(out), "format": args.format}, indent=2) + "\n")
    return 0


def _cmd_edit(args: argparse.Namespace) -> int:
    import asyncio

    from capturd.walk.coordinator import DemoForgeError

    if args.annotation is None and not args.regenerate_voice:
        sys.stderr.write("nothing to edit — pass --annotation and/or --regenerate-voice\n")
        return 1
    try:
        step = asyncio.run(_forge().edit_step(
            args.demo_id,
            args.step,
            annotation=args.annotation,
            regenerate_voice=args.regenerate_voice,
        ))
    except DemoForgeError as exc:
        sys.stderr.write(f"edit failed: {exc}\n")
        return 1
    slim = {k: v for k, v in step.items() if k not in ("screenshotBase64", "voiceoverBase64")}
    sys.stdout.write(json.dumps({"ok": True, "step": slim}, indent=2) + "\n")
    return 0


def _cmd_delete(args: argparse.Namespace) -> int:
    from capturd.walk.coordinator import DemoForgeError

    try:
        ok = _forge().delete_demo(args.demo_id)
    except DemoForgeError as exc:
        sys.stderr.write(f"delete failed: {exc}\n")
        return 1
    if not ok:
        sys.stderr.write(f"demo not found: {args.demo_id}\n")
        return 1
    sys.stdout.write(json.dumps({"ok": True, "demoId": args.demo_id}, indent=2) + "\n")
    return 0


def _cmd_record(args: argparse.Namespace) -> int:
    """Dispatch `capturd walk record` — agent or human mode."""
    import asyncio

    from capturd.walk.recorder import DemoManager, DemoRecorderError

    parts = args.viewport.split("x")
    viewport = {"width": 1440, "height": 900}
    if len(parts) == 2:
        try:
            viewport = {"width": int(parts[0]), "height": int(parts[1])}
        except ValueError:
            sys.stderr.write(f"invalid viewport: {args.viewport}\n")
            return 1

    payload: dict[str, Any] = {
        "url": args.url,
        "name": args.name,
        "goal": args.goal,
        "viewport": viewport,
        "mode": "agent" if args.agent else "human",
        "voice": args.voice or args.workflow,
        "workflow": args.workflow,
    }

    mgr = DemoManager()

    try:
        recorder, session_id, mode = mgr.start(payload)
    except DemoRecorderError as exc:
        sys.stderr.write(f"record failed: {exc}\n")
        return 1

    if mode == "agent":
        sys.stderr.write(f"Recording (agent mode) session={session_id}...\n")
        try:
            spec = asyncio.run(recorder.agent_record())
        except DemoRecorderError as exc:
            sys.stderr.write(f"agent record failed: {exc}\n")
            return 1
        sys.stdout.write(json.dumps({
            "sessionId": session_id,
            "mode": "agent",
            "steps": len(spec.steps),
        }, indent=2) + "\n")
        sys.stderr.write(
            f"Done. {len(spec.steps)} steps recorded → "
            f"demos/{session_id}/demo.json\n"
        )
    else:
        sys.stderr.write(
            f"Recording (human mode) session={session_id}.\n"
            f"Open the browser and click through the flow. "
            f"Send SIGINT or call `capturd walk stop --session-id {session_id}` to finish.\n"
        )
        try:
            asyncio.run(recorder.start())
        except DemoRecorderError as exc:
            sys.stderr.write(f"human record failed: {exc}\n")
            return 1
        # Human mode: the recorder loop runs in background. The user stops
        # it from another terminal. We keep the event loop alive.
        try:
            asyncio.get_event_loop().run_forever()
        except KeyboardInterrupt:
            spec = recorder.stop()
            sys.stdout.write(json.dumps({
                "sessionId": session_id,
                "mode": "human",
                "steps": len(spec.steps),
            }, indent=2) + "\n")
            sys.stderr.write(
                f"Done. {len(spec.steps)} steps recorded → "
                f"demos/{session_id}/demo.json\n"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
