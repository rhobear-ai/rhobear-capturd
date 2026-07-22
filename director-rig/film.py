"""Director-mode film rig: executes one shot-list JSON against the Captur'd MCP engine.

Shot JSON:
{
  "name": "...", "start_url": "...", "prewait": 18, "style": "snappy",
  "steps": [{"action","selector","value","note","wait"}...],
  "zoom": [{"step":0,"target":"#email","level":2.2}],
  "hold": [{"step":2,"ms":900}],
  "spotlight": [{"step":0,"on":true,"target":"..."}],
  "overlay": [{"step":0,"text":"1 - Workspace","position":"top-left"}],
  "export": ["mp4"], "out": "D:/capturd-plans-showcase/login-flow"
}
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import sys
import time
from pathlib import Path

REPO = Path(os.environ.get("CAPTURD_REPO", "/opt/sunsponge-capture"))
sys.path.insert(0, str(REPO / "scripts"))
from e2e_mcp_stdio import McpStdioClient  # noqa: E402


def _adjusted(act_i: int, act_to_engine: dict, kept: list) -> int | None:
    """Map a shot-list act index to its live (post-trim) engine step index.

    Returns None (instead of raising) when the act has no live engine step:
    either its demo.act call never completed (selector timeout / skip_unresolved,
    so it's absent from act_to_engine), or its engine step was trimmed as a
    stray SPA interaction (present in act_to_engine but not in kept). Callers
    skip the directive rather than crashing the whole render -- content-
    dependent: only shows up on shot lists whose act numbering drifted from
    what actually ran (e.g. job 08a1b1ad8396, a rhobear.ai walkthrough).
    """
    eng_i = act_to_engine.get(act_i)
    if eng_i is None:
        print(f"[film] WARN adjusted: act={act_i} has no engine stepIndex "
              f"(step never completed) -- skipping directive")
        return None
    try:
        return kept.index(eng_i)
    except ValueError:
        print(f"[film] WARN adjusted: act={act_i} -> engine step={eng_i} "
              f"was trimmed as a stray -- skipping directive")
        return None


def main(shot_path: str) -> int:
    shot = json.loads(Path(shot_path).read_text(encoding="utf-8"))
    out = Path(shot["out"])
    out.mkdir(parents=True, exist_ok=True)
    frames = out / "act-frames"
    frames.mkdir(exist_ok=True)

    env = dict(os.environ)
    env["CAPTURD_ROOT"] = str(out / "work")
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("RHOBEAR_GW_API_KEY", None)  # keyless: deterministic enrichment + Edge-TTS
    # Director quality knobs (paid_boot patches honor these)
    if shot.get("voice"):
        env["CAPTURD_VOICE"] = shot["voice"]
    env["CAPTURD_TYPE_DELAY_MS"] = str(shot.get("type_delay_ms", 70))

    boot = Path(__file__).resolve().parent / "paid_boot.py"
    client = McpStdioClient([sys.executable, str(boot)], env=env, cwd=REPO)
    meta: dict = {"name": shot["name"], "steps": []}
    try:
        client.request("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "director", "version": "1"},
        })
        client.notify("notifications/initialized")

        rec = client.call_tool("demo.record", {
            "url": shot["start_url"], "name": shot["name"], "mode": "live",
            "visible": False, "voice": False,
            "viewport": shot.get("viewport") or {"width": 1440, "height": 900},
        }, timeout=120)
        sid = rec["sessionId"]
        print(f"[film] session {sid} on {shot['start_url']}")
        time.sleep(shot.get("prewait", 5))

        skip_unresolved = bool(shot.get("skip_unresolved"))
        for i, step in enumerate(shot["steps"]):
            res = None
            for attempt in range(4):  # SPA load variance: engine click waits 5s; retry to ~30s
                try:
                    res = client.call_tool("demo.act", {
                        "session_id": sid,
                        "action": step["action"],
                        "selector": step.get("selector", ""),
                        "value": step.get("value", ""),
                        "note": step.get("note", ""),
                    }, timeout=90)
                    break
                except RuntimeError as exc:
                    if "Timeout" not in str(exc) or attempt == 3:
                        # scouted shots: a stale/navigated-away selector drops the
                        # step instead of killing the whole render.
                        if skip_unresolved and "Timeout" in str(exc):
                            print(f"[film] act {i} skipped (selector unresolved after retries)")
                            res = None
                            break
                        raise
                    print(f"[film] act {i} wait-retry {attempt + 1} (selector not ready)")
                    time.sleep(8)
            if res is None:
                continue
            fb = res.get("frameBase64") or ""
            if fb:
                (frames / f"act_{i:02d}.jpg").write_bytes(base64.b64decode(fb))
            meta["steps"].append({"i": i, "act": step["action"],
                                  "sel": step.get("selector", ""),
                                  "stepIndex": res.get("stepIndex")})
            print(f"[film] act {i} {step['action']} -> stepIndex={res.get('stepIndex')} frame={'y' if fb else 'N'}")
            time.sleep(step.get("wait", 2))

        stop = client.call_tool("demo.stop", {"session_id": sid}, timeout=300)
        demo_id = stop.get("demoId", sid)
        meta["demoId"] = demo_id
        meta["stepCount"] = stop.get("stepCount")
        print(f"[film] stopped: {stop.get('stepCount')} steps, demo {demo_id}")

        status = {}
        for _ in range(120):
            status = client.call_tool("demo.status", {"demo_id": demo_id}, timeout=30)
            if status.get("status") in ("enriched", "failed"):
                break
            time.sleep(2)
        print(f"[film] enrichment: {status.get('status')}")
        if status.get("status") != "enriched":
            print("[film] FAIL enrichment", status)
            return 1

        # STEP AUDIT: the SPA can emit interactions I never scripted (rrweb
        # records them as extra steps) -> fallback annotation prints raw
        # selectors on camera. Trim strays; blank junk captions on my own
        # deliberately-silent steps.
        demo_json_path = out / "work" / "demos" / demo_id / "demo.json"
        djson = json.loads(demo_json_path.read_text(encoding="utf-8"))
        act_to_engine = {m["i"]: m["stepIndex"] for m in meta["steps"]
                         if m.get("stepIndex") is not None}
        mine = set(act_to_engine.values())
        junk_prefix = ("Clicked ", "Typed ", "Scrolled", "Interact", "Did ", "Navigated")
        n_steps = len(djson.get("steps", []))
        strays = sorted(i for i in range(n_steps) if i not in mine)
        kept = [i for i in range(n_steps) if i not in set(strays)]
        if strays:
            # trim keeps a RANGE [start,end] — to drop mid-list strays, push
            # them to the tail via reorder, then keep [0..len(kept)-1].
            client.call_tool("demo.reorder", {"demo_id": demo_id,
                                              "new_step_order": kept + strays}, timeout=30)
            client.call_tool("demo.trim", {"demo_id": demo_id, "start_step": 0,
                                           "end_step": len(kept) - 1}, timeout=30)
            print(f"[film] dropped {len(strays)} stray step(s) (unscripted SPA interactions): {strays}")

        def adjusted(act_i: int) -> int | None:
            return _adjusted(act_i, act_to_engine, kept)

        for m in meta["steps"]:
            note = shot["steps"][m["i"]].get("note", "")
            if note:
                continue
            eng = next((s for s in djson.get("steps", [])
                        if s["index"] == act_to_engine.get(m["i"])), None)
            ann = (eng or {}).get("annotation") or ""
            if ann.startswith(junk_prefix):
                step_idx = adjusted(m["i"])
                if step_idx is None:
                    continue
                client.call_tool("demo.edit", {"demo_id": demo_id,
                                               "step_index": step_idx,
                                               "annotation": ""}, timeout=60)
                print(f"[film] blanked junk caption on silent step act={m['i']}")

        # ORDER LAW: stylize first (regenerates timeline), then manual keyframes.
        # stylize is key-gated (LLM re-gen); keyless -> keep the deterministic
        # fallback timeline and approximate style via keyframe duration/easing.
        try:
            client.call_tool("demo.stylize", {"demo_id": demo_id,
                                              "style": shot.get("style", "snappy")}, timeout=120)
            print(f"[film] stylized: {shot.get('style')}")
        except RuntimeError as exc:
            print(f"[film] stylize skipped (keyless fallback timeline kept): {str(exc)[:80]}")
        for z in shot.get("zoom", []):
            step_idx = adjusted(z["step"])
            if step_idx is None:
                print(f"[film] skipped zoom on act={z['step']} (no live engine step)")
                continue
            client.call_tool("demo.zoom", {"demo_id": demo_id, "step_index": step_idx,
                                           "target": z["target"], "level": z["level"],
                                           "duration": z.get("duration", 500),
                                           "easing": z.get("easing", "ease-in-out")}, timeout=30)
        for h in shot.get("hold", []):
            step_idx = adjusted(h["step"])
            if step_idx is None:
                print(f"[film] skipped hold on act={h['step']} (no live engine step)")
                continue
            client.call_tool("demo.hold", {"demo_id": demo_id, "step_index": step_idx,
                                           "ms": h["ms"]}, timeout=30)
        for s in shot.get("spotlight", []):
            step_idx = adjusted(s["step"])
            if step_idx is None:
                print(f"[film] skipped spotlight on act={s['step']} (no live engine step)")
                continue
            client.call_tool("demo.spotlight", {"demo_id": demo_id,
                                                "step_index": step_idx,
                                                "on": s["on"], "target": s["target"]}, timeout=30)
        for o in shot.get("overlay", []):
            step_idx = adjusted(o["step"])
            if step_idx is None:
                print(f"[film] skipped overlay on act={o['step']} (no live engine step)")
                continue
            client.call_tool("demo.overlay", {"demo_id": demo_id,
                                              "step_index": step_idx,
                                              "text": o["text"], "position": o["position"]},
                             timeout=30)

        for fmt in shot.get("export", ["mp4"]):
            exp = client.call_tool("demo.export", {"demo_id": demo_id, "format": fmt}, timeout=900)
            src = Path(exp["path"])
            dst = out / f"{shot['name']}.{fmt}"
            shutil.copy2(src, dst)
            meta.setdefault("exports", []).append(str(dst))
            print(f"[film] export {fmt}: {dst} ({dst.stat().st_size} bytes)")

        (out / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    finally:
        client.close()
    print("[film] DONE", shot["name"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
