"""render_worker.py — the hosted service's core render job runner (L5 seed).

This is the piece the paid cloud service is built on: accept a generation job,
enforce a hard cost cap, run the Director rig to produce a video, optionally
brand/reframe it, and return a result. It is deliberately transport-agnostic —
an HTTP layer (FastAPI) and a queue wrap this later; the render logic and the
COST CAP live here and are testable now.

What's real today:
  * JobSpec validation + a hard cost cap (max wall-seconds + max steps) — the
    single most important safety rail so a runaway can't drain the render budget.
  * run_job() shells the proven film.py rig with a timeout, then optional
    finish.py (aspect/brand), returns a JobResult with status + output path.

What plugs in later (documented, not faked):
  * Auto shot-list generation from {url, template} via the Director brain
    (scout → plan) — today run_job takes an explicit shot JSON like film.py does.
  * HTTP API (see API.md), auth (Google OAuth), billing gate (Stripe), and the
    dashboard wiring. None of those are money-in and none are built here yet.

CLI:  python render_worker.py <job.json>
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path

# The Director rig (film.py / finish.py) and the engine repo are configurable
# via env vars so the same code works on any machine. The service's main.py
# shares the CAPTURD_RIG env var; ENGINE_REPO has its own variable.
SERVICE_DIR = Path(__file__).resolve().parent
_RIG_ENV = os.environ.get("CAPTURD_RIG", "").strip()
_ENGINE_ENV = os.environ.get("CAPTURD_ENGINE_REPO", "").strip()
RIG = Path(_RIG_ENV) if _RIG_ENV else (SERVICE_DIR / ".." / "rig").resolve()
ENGINE_REPO = Path(_ENGINE_ENV) if _ENGINE_ENV else SERVICE_DIR.resolve()

# ---- cost cap (the load-bearing safety rail) --------------------------------

DEFAULT_MAX_SECONDS = 300      # a single generation may never exceed this wall time
DEFAULT_MAX_STEPS = 16         # shot lists over this are rejected before any render
HARD_CEIL_SECONDS = 600        # absolute ceiling regardless of per-plan overrides


@dataclass
class CostCap:
    max_seconds: int = DEFAULT_MAX_SECONDS
    max_steps: int = DEFAULT_MAX_STEPS

    def clamp(self) -> "CostCap":
        return CostCap(min(self.max_seconds, HARD_CEIL_SECONDS),
                       min(self.max_steps, DEFAULT_MAX_STEPS))


class CapExceeded(Exception):
    pass


def enforce_precheck(shot: dict, cap: CostCap) -> None:
    """Reject a job BEFORE spending any render compute. Pure + unit-testable."""
    steps = shot.get("steps") or []
    if len(steps) > cap.max_steps:
        raise CapExceeded(f"{len(steps)} steps exceeds cap of {cap.max_steps}")
    if cap.max_seconds > HARD_CEIL_SECONDS:
        raise CapExceeded(f"max_seconds {cap.max_seconds} exceeds hard ceiling {HARD_CEIL_SECONDS}")


# ---- job model --------------------------------------------------------------

@dataclass
class JobSpec:
    shot: dict                              # a film.py shot list (url/steps/zoom/...)
    aspect: str = "16:9"                    # 16:9 | 9:16 | 1:1  (finish.py)
    brand: str = "#0d1017"
    intro: str = ""
    outro: str = ""
    voice: str = ""                         # edge-tts voice id, "" = default
    cap: CostCap = field(default_factory=CostCap)
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


@dataclass
class JobResult:
    job_id: str
    status: str                             # queued|running|done|failed|capped
    output: str = ""
    seconds: float = 0.0
    detail: str = ""

    def as_json(self) -> str:
        return json.dumps(asdict(self), indent=1)


# ---- runner -----------------------------------------------------------------

def run_job(spec: JobSpec, out_dir: Path) -> JobResult:
    cap = spec.cap.clamp()
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        enforce_precheck(spec.shot, cap)
    except CapExceeded as exc:
        return JobResult(spec.job_id, "capped", detail=str(exc))

    # persist the shot for film.py, injecting per-job knobs
    shot = dict(spec.shot)
    shot["out"] = str(out_dir / spec.job_id)
    if spec.voice:
        shot["voice"] = spec.voice
    shot_path = out_dir / f"{spec.job_id}.job.json"
    shot_path.write_text(json.dumps(shot, indent=1), encoding="utf-8")

    t0 = time.perf_counter()
    try:
        # HARD wall-time cap on the whole render — the runaway killer.
        subprocess.run(
            [sys.executable, str(RIG / "film.py"), str(shot_path)],
            cwd=str(ENGINE_REPO), timeout=cap.max_seconds,
            capture_output=True, text=True, check=True,
        )
    except subprocess.TimeoutExpired:
        return JobResult(spec.job_id, "capped", seconds=time.perf_counter() - t0,
                         detail=f"render exceeded {cap.max_seconds}s cap — killed")
    except subprocess.CalledProcessError as exc:
        return JobResult(spec.job_id, "failed", seconds=time.perf_counter() - t0,
                         detail=(exc.stderr or "")[-500:])

    # locate the exported mp4
    mp4s = list((out_dir / spec.job_id).glob("*.mp4"))
    if not mp4s:
        return JobResult(spec.job_id, "failed", seconds=time.perf_counter() - t0,
                         detail="no mp4 produced")
    final = mp4s[0]

    # optional brand/aspect finish (post-process, WALL-safe)
    if spec.aspect != "16:9" or spec.intro or spec.outro:
        branded = out_dir / spec.job_id / f"{spec.job_id}-final.mp4"
        args = [sys.executable, str(RIG / "finish.py"), str(final), str(branded),
                "--aspect", spec.aspect, "--brand", spec.brand]
        if spec.intro:
            args += ["--intro", spec.intro]
        if spec.outro:
            args += ["--outro", spec.outro]
        try:
            subprocess.run(args, cwd=str(ENGINE_REPO), timeout=180,
                           capture_output=True, text=True, check=True)
            final = branded
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
            # finish failed — still deliver the unbranded render, note it
            return JobResult(spec.job_id, "done", output=str(final),
                             seconds=time.perf_counter() - t0,
                             detail=f"delivered unbranded (finish failed: {exc})")

    return JobResult(spec.job_id, "done", output=str(final),
                     seconds=time.perf_counter() - t0)


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    cap = CostCap(**payload.pop("cap", {})) if "cap" in payload else CostCap()
    spec = JobSpec(cap=cap, **payload)
    res = run_job(spec, Path(r"D:\capturd-service\jobs"))
    print(res.as_json())
    return 0 if res.status == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
