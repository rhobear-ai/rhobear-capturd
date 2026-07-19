"""Command-line interface for `capturd shots` — rested-state screenshot batch.

Invoked either as ``capturd shots ...`` (via the top-level dispatcher) or as
the legacy ``sunsponge-capture ...`` alias (backwards compat for older scripts).
"""

from __future__ import annotations

import argparse
import sys
import time

from .capture import RestedCaptureError, RestedCaptureManager


def _split(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").replace("\n", ",").split(",") if item.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="capturd shots",
        description=(
            "Rested-state website screenshot batch - settled, animation-free, "
            "full-page shots across viewports and color schemes. "
            "(Also invokable as `sunsponge-capture` for backwards compat.)"
        ),
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--site", metavar="URL", help="Crawl a site and capture every discovered page")
    source.add_argument("--sitemap", metavar="URL", help="Capture every page listed in a sitemap.xml")
    source.add_argument("--local", metavar="PATH", help="Capture a local .html file, or a folder of them")
    source.add_argument("--urls", metavar="A,B,C", help="Comma- or newline-separated list of URLs")

    parser.add_argument("--viewports", default="desktop,tablet,mobile",
                        help="Any of desktop,tablet,mobile (default: all three)")
    parser.add_argument("--schemes", default="light,dark",
                        help="Any of light,dark (default: both)")
    parser.add_argument("--format", default="png", choices=["png", "jpeg"], help="Image format")
    parser.add_argument("--no-full-page", action="store_true",
                        help="Capture only the viewport instead of the full scrollable page")
    parser.add_argument("--out", metavar="DIR", help="Export folder (default: a ZIP in the app data dir)")
    parser.add_argument("--zip", action="store_true", help="Force a ZIP export even when --out is given")
    parser.add_argument("--depth", type=int, default=8, help="Crawl depth for --site (default: 8)")
    parser.add_argument("--max-pages", type=int, default=1000, help="Max pages to discover (default: 1000)")
    parser.add_argument("--workers", type=int, default=3, help="Concurrent capture workers (default: 3)")
    parser.add_argument("--cookie", metavar="NAME=VALUE;DOMAIN", action="append", default=[],
                        help="Send a cookie so signed-in pages can be captured, e.g. "
                             "--cookie 'session=abc123;app.example.com'. Repeatable. Without this "
                             "the batch can only shoot pages a logged-out visitor can reach.")
    parser.add_argument("--states", action="store_true",
                        help="Also capture every state a page can be put into: click each "
                             "interactive control (modals, dropdowns, tabs, accordions, "
                             "toggles), shoot what it opens, then restore. No LLM involved.")
    parser.add_argument("--states-limit", type=int, default=20, metavar="N",
                        help="Max controls to sweep per page (default: 20)")
    parser.add_argument("--state-wait-ms", type=int, default=900, metavar="MS",
                        help="Wait after each click before shooting (default: 900)")
    parser.add_argument("--settle-ms", type=int, default=600,
                        help="Settle wait per page, in ms, before the shot (default: 600)")
    return parser


def _parse_cookies(raw: list[str]) -> list[dict]:
    """`name=value;domain` -> the cookie dicts Playwright wants.

    Without this the batch can only ever capture what a logged-out visitor sees, which
    leaves every authenticated product — the actual app — impossible to screenshot.
    """
    out = []
    for item in raw or []:
        pair, _, domain = item.partition(";")
        name, _, value = pair.partition("=")
        name, value, domain = name.strip(), value.strip(), domain.strip()
        if not (name and value and domain):
            raise SystemExit(f"error: --cookie must look like NAME=VALUE;DOMAIN (got {item!r})")
        # A `secure` cookie is never sent over http, so marking one for localhost would
        # silently drop it and every local capture would come back logged out.
        local = domain.split(":")[0] in ("localhost", "127.0.0.1", "::1", "0.0.0.0")
        out.append({"name": name, "value": value, "domain": domain,
                    "path": "/", "secure": not local, "httpOnly": False})
    return out


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = {
        "urls": _split(args.urls) if args.urls else [],
        "sitemap_url": args.sitemap or "",
        "crawl": bool(args.site),
        "crawl_url": args.site or "",
        "local": bool(args.local),
        "local_path": args.local or "",
        "crawl_depth": args.depth,
        "max_pages": args.max_pages,
        "viewports": _split(args.viewports),
        "schemes": _split(args.schemes),
        "format": args.format,
        "full_page": not args.no_full_page,
        "export_mode": "folder" if (args.out and not args.zip) else "zip",
        "export_dir": args.out or None,
        "concurrency": args.workers,
        "wait_ms": args.settle_ms,
        "cookies": _parse_cookies(args.cookie),
        "states": args.states,
        "states_limit": args.states_limit,
        "state_wait_ms": args.state_wait_ms,
        "name": "sunsponge-captures",
    }

    manager = RestedCaptureManager()
    try:
        job = manager.start(payload)
    except RestedCaptureError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    job_id = job["job_id"]
    last = -1
    while True:
        job = manager.get(job_id)
        done, total = int(job.get("completed", 0)), int(job.get("total", 0))
        if done != last:
            print(f"\r{job.get('message', '')}  [{done}/{total}]", end="", flush=True)
            last = done
        if job.get("status") in {"done", "done_with_errors", "failed"}:
            break
        time.sleep(0.5)
    print()

    status = job.get("status")
    if status == "failed":
        print(f"failed: {job.get('message')}", file=sys.stderr)
        return 1

    total = int(job.get("total", 0))
    failed = int(job.get("failed", 0))
    print(f"done: {total - failed}/{total} captured" + (f"  ({failed} failed)" if failed else ""))
    if job.get("zip_path"):
        print(f"zip:  {job['zip_path']}")
    if job.get("output_dir") and job.get("output_dir") != job.get("zip_path"):
        print(f"out:  {job['output_dir']}")
    return 0 if status == "done" else 3


if __name__ == "__main__":
    raise SystemExit(main())
