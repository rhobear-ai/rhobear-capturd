"""scout.py — deterministic Director scout: read a live app, build a tight shot.

Opens the URL headless, reads the interactable-element digest (demo.look), and
derives a golden-path shot list — types into the real primary input, tours the
real nav, lands on the real CTA — with captions taken from the app's own text.
No LLM, no key: showcase-grade output from any URL, deterministically.

    from scout import build_shot
    shot = build_shot(url, template, name)   # -> film.py shot dict, or None

Returns None if the app can't be read (caller falls back to a generic tour).
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Optional

REPO = Path(os.environ.get("CAPTURD_REPO", "/opt/sunsponge-capture"))
sys.path.insert(0, str(REPO / "scripts"))
from e2e_mcp_stdio import McpStdioClient  # noqa: E402

_BOOT = Path(__file__).resolve().parent / "paid_boot.py"

TEMPLATE_STYLE = {
    "saas-walkthrough": "snappy", "ux-showcase": "cinematic",
    "tutorial-longform": "professional", "feature-spotlight": "smooth",
    "social-teaser": "snappy", "login-flow": "snappy",
}
TEMPLATE_PHRASE = {
    "saas-walkthrough": "Plan my product launch",
    "ux-showcase": "Design my workflow",
    "tutorial-longform": "Show me how this works",
    "feature-spotlight": "Make me a report",
    "social-teaser": "Try it now",
    "login-flow": "demo@example.com",
}
CTA_WORDS = ("get started", "start free", "sign up", "signup", "try ", "get demo",
             "book ", "buy ", "upgrade", "create", "launch", "join", "start",
             "get it", "begin", "continue")


def _digest(url: str, prewait: int = 16) -> list[dict]:
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.pop("RHOBEAR_GW_API_KEY", None)  # keyless: deterministic enrichment + Edge-TTS
    client = McpStdioClient([sys.executable, str(_BOOT)], env=env, cwd=REPO)
    try:
        client.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                       "clientInfo": {"name": "scout", "version": "1"}})
        client.notify("notifications/initialized")
        rec = client.call_tool("demo.record", {
            "url": url, "name": "scout", "mode": "live",
            "visible": False, "voice": False,
            "viewport": {"width": 1440, "height": 900},
        }, timeout=120)
        sid = rec["sessionId"]
        time.sleep(prewait)
        look = client.call_tool("demo.look", {"session_id": sid}, timeout=60)
        els = look.get("elements") or []
        try:
            stop = client.call_tool("demo.stop", {"session_id": sid}, timeout=120)
            client.call_tool("demo.delete", {"demo_id": stop.get("demoId", sid)}, timeout=30)
        except Exception:
            pass
        return els
    finally:
        client.close()


def _area(e: dict) -> int:
    r = e.get("rect") or {}
    return int(r.get("w", 0)) * int(r.get("h", 0))


def _pick(els: list[dict]):
    inputs = [e for e in els if e.get("tag") == "textarea"
              or (e.get("tag") == "input" and (e.get("type") or "") in ("", "text", "search", "email"))]
    inputs = [e for e in inputs if (e.get("rect") or {}).get("w", 0) >= 180]
    primary_input = max(inputs, key=lambda e: (e.get("rect") or {}).get("w", 0), default=None)

    def is_cta(e):
        t = (e.get("text") or e.get("label") or "").lower().strip()
        return t and len(t) <= 30 and any(w in t for w in CTA_WORDS)
    ctas = [e for e in els if (e.get("tag") in ("a", "button") or e.get("role") == "button") and is_cta(e)]
    primary_cta = sorted(ctas, key=lambda e: (e.get("tag") != "button", -_area(e)))[0] if ctas else None

    navs, seen = [], set()
    for e in els:
        t = (e.get("text") or "").strip()
        r = e.get("rect") or {}
        if (e.get("tag") == "a" and 0 < len(t) <= 18
                and (r.get("y", 999) < 130 or r.get("x", 999) < 260)
                and e is not primary_cta and t.lower() not in seen):
            seen.add(t.lower())
            navs.append(e)
    return primary_input, navs[:3], primary_cta


def build_shot(url: str, template: str, name: str) -> Optional[dict]:
    try:
        els = _digest(url)
    except Exception:
        return None
    if not els:
        return None
    primary_input, navs, primary_cta = _pick(els)

    style = TEMPLATE_STYLE.get(template, "snappy")
    phrase = TEMPLATE_PHRASE.get(template, "Show me around")
    steps, zoom, hold = [], [], []
    i = 0
    if primary_input:
        steps.append({"action": "input", "selector": primary_input["selector"],
                      "value": phrase, "note": "Just ask — in plain English", "wait": 2})
        zoom.append({"step": i, "target": primary_input["selector"], "level": 1.7, "duration": 600})
        hold.append({"step": i, "ms": 900})
        i += 1
    for nav in navs:
        steps.append({"action": "click", "selector": nav["selector"],
                      "note": nav["text"].strip(), "wait": 3})
        zoom.append({"step": i, "target": nav["selector"], "level": 2.1, "duration": 600})
        i += 1
    if primary_cta:
        steps.append({"action": "click", "selector": primary_cta["selector"],
                      "note": (primary_cta.get("text") or "Get started").strip()[:40], "wait": 3})
        zoom.append({"step": i, "target": primary_cta["selector"], "level": 2.4, "duration": 700})
        hold.append({"step": i, "ms": 1000})
        i += 1

    if len(steps) < 2:      # too little signal — let caller use the generic tour
        return None
    if not any(h["step"] == 0 for h in hold):
        hold.append({"step": 0, "ms": 800})

    return {
        "name": name, "start_url": url, "prewait": 16, "style": style,
        "steps": steps, "zoom": zoom, "hold": hold,
        "skip_unresolved": True,      # a stale selector drops that step, never aborts
        "export": ["mp4"],
    }


if __name__ == "__main__":
    import json
    print(json.dumps(build_shot(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "saas-walkthrough",
                                "scout test"), indent=1))
