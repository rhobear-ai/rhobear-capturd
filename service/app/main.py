"""Captur'd hosted service — the shippable app.

Full lifecycle: auth → plan/usage gate → cost cap → render → deliver. Serves its
own frontend. Reuses render_worker (cost cap) + the Director rig. Everything works
end-to-end today except the owner's payment credentials (see billing.py / config.py).
"""
from __future__ import annotations

import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

APP_DIR = Path(__file__).resolve().parent
SERVICE_DIR = APP_DIR.parent
sys.path.insert(0, str(SERVICE_DIR))

from app import auth, billing, config, store            # noqa: E402
from app.auth import current_user, require_user          # noqa: E402
from render_worker import CostCap, JobSpec, run_job       # noqa: E402

# the Director scout lives in the rig; add it to path
RIG = Path(config._env("CAPTURD_RIG", r"C:\Users\slang\.claude\skills\capturd-autopilot\rig"))
if RIG.is_dir():
    sys.path.insert(0, str(RIG))
try:
    from scout import build_shot                          # noqa: E402
except Exception:
    build_shot = None

WEB = SERVICE_DIR / "web"

# template -> camera style (the look; AI-scout tightens the shot later)
TEMPLATE_STYLE = {
    "saas-walkthrough": "snappy", "ux-showcase": "cinematic",
    "tutorial-longform": "professional", "feature-spotlight": "smooth",
    "social-teaser": "snappy", "login-flow": "snappy",
}


def shot_from_template(url: str, template: str, name: str) -> dict:
    """A universal, always-works scroll-tour shot for any URL. The Director
    brain (scout→plan) replaces this with a tight per-app shot later; this
    guarantees a real branded video today."""
    style = TEMPLATE_STYLE.get(template, "snappy")
    return {
        "name": name, "start_url": url, "prewait": 16, "style": style,
        "steps": [
            {"action": "scroll", "value": "down", "note": "A look at the product", "wait": 2},
            {"action": "scroll", "value": "down", "note": "", "wait": 2},
            {"action": "scroll", "value": "top", "note": "Back to the top", "wait": 2},
        ],
        "export": ["mp4"],
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init()
    config.ensure_dirs()
    yield


app = FastAPI(title="Captur'd", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(billing.router)


@app.get("/healthz")
async def healthz():
    return {"ok": True, **config.status()}


@app.get("/api/me")
async def me(request: Request):
    u = current_user(request)
    if not u:
        return {"signed_in": False, "config": config.status(),
                "pro_price": config.PRO_PRICE}
    gens = store.usage_count(u["id"], "generation")
    return {
        "signed_in": True, "email": u["email"], "plan": u["plan"],
        "usage": {"generations": gens,
                  "free_limit": config.FREE_GENERATION_LIMIT,
                  "remaining": (None if u["plan"] == "pro"
                                else max(0, config.FREE_GENERATION_LIMIT - gens))},
        "mcp_endpoint": (f"{config.BASE_URL}/mcp/{u['id']}" if u["plan"] == "pro" else None),
        "config": config.status(), "pro_price": config.PRO_PRICE,
    }


def _run_generation(job_id: str, uid: str, url: str, template: str,
                    name: str, spec_kwargs: dict) -> None:
    store.record_job(job_id, uid, "walk", "running")
    # Director scout → a tight per-app shot; fall back to the generic tour.
    shot = None
    if build_shot is not None:
        try:
            shot = build_shot(url, template, name)
        except Exception:
            shot = None
    if not shot:
        shot = shot_from_template(url, template, name)
    spec = JobSpec(shot=shot, job_id=job_id, **spec_kwargs)
    res = run_job(spec, config.JOBS_DIR)
    store.record_job(job_id, uid, "walk", res.status, res.output, res.detail)
    if res.status == "done":
        store.add_usage(uid, "generation", 1)


@app.post("/api/generate")
async def generate(request: Request, background: BackgroundTasks):
    user = require_user(request)
    body = await request.json()
    url = (body.get("url") or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="a valid http(s) url is required")
    template = body.get("template") or "saas-walkthrough"

    # plan / usage gate (canon: Free = 1 generation)
    if user["plan"] != "pro":
        used = store.usage_count(user["id"], "generation")
        if used >= config.FREE_GENERATION_LIMIT:
            raise HTTPException(
                status_code=402,
                detail=f"Free plan is {config.FREE_GENERATION_LIMIT} generation. "
                       f"Upgrade to Pro ({config.PRO_PRICE}/mo) for unlimited.")

    import uuid
    job_id = uuid.uuid4().hex[:12]
    name = body.get("name") or f"{template} demo"
    # scout needs headroom on top of the film render
    cap = CostCap(max_seconds=int(body.get("max_seconds", 360)))
    spec_kwargs = {
        "aspect": body.get("aspect", "16:9"),
        "brand": body.get("brand", "#4f8cff"),
        "intro": body.get("intro", ""),
        "outro": body.get("outro", ""),
        "voice": body.get("voice", ""),
        "cap": cap,
    }
    store.record_job(job_id, user["id"], "walk", "queued")
    background.add_task(_run_generation, job_id, user["id"], url, template, name, spec_kwargs)
    return JSONResponse({"job_id": job_id, "status": "queued"}, status_code=202)


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str, request: Request):
    user = require_user(request)
    job = store.get_job(job_id)
    if not job or job["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="job not found")
    return {"job_id": job_id, "status": job["status"], "detail": job["detail"],
            "has_video": bool(job["output"])}


@app.get("/api/jobs/{job_id}/video")
async def job_video(job_id: str, request: Request):
    user = require_user(request)
    job = store.get_job(job_id)
    if not job or job["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="job not found")
    if job["status"] != "done" or not job["output"]:
        raise HTTPException(status_code=409, detail=f"job is {job['status']}")
    p = Path(job["output"])
    if not p.is_file():
        raise HTTPException(status_code=410, detail="video no longer available")
    return FileResponse(str(p), media_type="video/mp4", filename=f"{job_id}.mp4")


@app.get("/", response_class=HTMLResponse)
async def index():
    idx = WEB / "index.html"
    if idx.is_file():
        return HTMLResponse(idx.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Captur'd</h1><p>frontend missing</p>")


if (WEB / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=str(WEB / "assets")), name="assets")
