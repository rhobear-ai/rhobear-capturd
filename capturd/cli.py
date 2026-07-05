"""Captur'd top-level CLI dispatcher.

Routes:
  capturd shots ...  ->  capturd.shots.cli:main   (rested-state screenshots)
  capturd walk  ...  ->  capturd.walk.cli:main    (agent-made walkthroughs)
  capturd serve ...  ->  capturd.mcp.server:main  (MCP server, both surfaces)

Subcommand modules are imported lazily so `capturd shots` doesn't pay the walk
mode's import cost (fastmcp, openai, edge-tts).
"""

from __future__ import annotations

import sys


_HELP = """capturd - RHOBEAR Captur'd

  capturd shots ...    Rested-state bulk screenshots  [FREE]  (settled, animation-free, full-page)
  capturd walk  ...    Agent-made AI walkthroughs     [PRO]   (record / stop / list / export / edit)
  capturd serve        MCP server exposing capture.* and demo.* to any RHOBEAR agent
  capturd activate ..  Activate a Pro code / license (unlocks the AI walkthroughs)

  capturd --version    Show version
  capturd --help       Show this help

Screenshots are free. The AI walkthroughs (walk) are RHOBEAR Captur'd Pro, powered by our
own AI gateway - no bring-your-own-key. Unlock with: capturd activate <CODE>
"""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv or argv[0] in {"-h", "--help"}:
        sys.stdout.write(_HELP)
        return 0

    if argv[0] in {"-V", "--version"}:
        from capturd import __version__
        print(f"capturd {__version__}")
        return 0

    mode, rest = argv[0], argv[1:]

    if mode == "activate":
        from capturd.pro import PRO_CONFIG, activate
        if not rest:
            sys.stderr.write("usage: capturd activate <CODE-OR-LICENSE>\n")
            return 2
        if activate(rest[0]):
            print("RHOBEAR Captur'd Pro activated - AI walkthroughs unlocked.")
            return 0
        unset = not PRO_CONFIG.get("codes") and not PRO_CONFIG.get("pubkey_b64")
        sys.stderr.write("That code / license isn't recognized" + (" yet." if unset else ".") + "\n")
        return 1

    if mode == "shots":
        from capturd.shots.cli import main as shots_main
        return shots_main(rest)

    if mode == "walk":
        # PRO: the AI walkthroughs run on our own gateway AI (no BYOK) and are gated.
        from capturd.pro import require_pro
        if not require_pro("AI walkthroughs (capturd walk)"):
            return 1
        from capturd.walk.cli import main as walk_main
        return walk_main(rest)

    if mode == "serve":
        from capturd.mcp.server import main as serve_main
        return serve_main(rest)

    sys.stderr.write(f"error: unknown mode '{mode}'\n\n")
    sys.stdout.write(_HELP)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
