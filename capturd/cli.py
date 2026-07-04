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


_HELP = """capturd - Captur'd by Sun Sponge

  capturd shots ...    Rested-state bulk screenshots (settled, animation-free, full-page)
  capturd walk  ...    Agent-made interactive walkthroughs (record / stop / list / export / edit)
  capturd serve        MCP server exposing capture.* and demo.* to any RHOBEAR agent

  capturd --version    Show version
  capturd --help       Show this help
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

    if mode == "shots":
        from capturd.shots.cli import main as shots_main
        return shots_main(rest)

    if mode == "walk":
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
