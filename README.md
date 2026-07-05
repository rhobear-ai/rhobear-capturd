<p align="center">
  <img src="assets/capturd-logo-wide.png" alt="RHOBEAR Captur'd" width="420">
</p>

<h1 align="center">RHOBEAR Captur'd</h1>

<p align="center">
  <b>Post-work product capture. Stills and walkthroughs, made by an agent, not a human.</b><br>
  You built the thing. Now show it. Point Captur'd at a URL and get either every rested-state
  screenshot of it or a full agent-narrated walkthrough video — no clicking through, no manual
  editing, no SaaS bill.
</p>

<p align="center">
  <a href="LICENSE"><img alt="MIT" src="https://img.shields.io/badge/license-MIT-7dffd5"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-f5b942">
  <img alt="Playwright" src="https://img.shields.io/badge/engine-Playwright-b794f4">
  <img alt="MCP" src="https://img.shields.io/badge/agent--surface-MCP-6ea8fe">
</p>

---

## Two modes, one lane

### `capturd shots` — rested-state screenshots, in bulk
The original RHOBEAR Captur'd, unchanged. Most screenshot tools fire too early — mid-animation,
before fonts load, while a carousel is sliding — and you get junk. Captur'd takes **rested** shots:
animations disabled, fonts awaited, motion stopped, scroll reset, then every page × every viewport ×
every color scheme, in one run.

- 🕸️ **Four ways in** — crawl a **site**, expand a **sitemap.xml**, paste **URLs**, point at **local HTML**.
- 📱 **Responsive matrix** — desktop (1440×1000), tablet (834×1112), mobile (390×844), in **light and dark**.
- 🧘 **Deterministic** — same input, same pixels. Diff-friendly.
- 📦 **Clean output** — `NNN-site-viewport-scheme.png` + `manifest.json`, ZIP or folder.

### `capturd walk` — agent-made interactive walkthroughs
The new mode. Prompt in, walkthrough out. Your agent (Plans, social manager, support, pi) says
"make me a UX-flow video of this site" and Captur'd walks the site itself and produces:

- **Interactive HTML viewer** (self-contained, zero deps, embeddable anywhere), plus
- **MP4 / GIF export** for social & sales,
- with **semantic zoom** (camera anchors to DOM elements, not pixels — survives your site's font/layout changes),
- **voice-synced camera** (TTS word timestamps align keyframes — camera lands on the noun),
- **spotlight mode** (rest of the page dims and blurs; attention is forced),
- **content-mode auto-detection** — canvas / WebGL / Three.js / HTML5 games fall back to video-mode automatically instead of silently breaking like every SaaS demo tool does.

Nothing else in the space is agent-made. Supademo / Arcade / Storylane all assume a human clicks
through. Screen Studio has the cinematography but no interactivity and no agent surface. Captur'd
does both, and it's yours, offline, MIT.

### `capturd serve` — MCP surface for both modes
One MCP server exposes both modes to any RHOBEAR agent:

```
capture.crawl   capture.rested                            # stills
demo.record     demo.stop        demo.export              # walk basics
demo.zoom       demo.pan         demo.hold                # camera
demo.spotlight  demo.overlay                              # composition
demo.reorder    demo.trim        demo.branch              # structure
demo.stylize    demo.regenerate                           # restyle
```

Editing is the API. The agent IS the editor. No GUI editor as v1.

## Install

```bash
git clone https://github.com/rhobear-ai/rhobear-capturd
cd sunsponge-capture
pip install -e .
python -m playwright install chromium
```

## Usage

```bash
# Stills — crawl a whole site, every viewport + scheme, into a folder
capturd shots --site https://example.com --out ./shots

# Stills — a few URLs, mobile dark only, as JPEG
capturd shots --urls "https://example.com,https://example.com/pricing" \
  --viewports mobile --schemes dark --format jpeg

# Walkthrough — agent walks the site itself (headful Playwright + LLM step-picker)
capturd walk record --url https://example.com --goal "sign up for a free account" --name "onboarding"
capturd walk export --demo-id <id> --format mp4

# MCP server — expose everything to your RHOBEAR agents
capturd serve
```

Legacy CLI (`sunsponge-capture ...`) still works as an alias for `capturd shots ...` so existing
scripts don't break.

### As a library

```python
from capturd import RestedCaptureManager      # stills
from capturd.walk.coordinator import DemoForge  # walkthroughs
from capturd.walk.schema import DemoSpec       # the contract

mgr = RestedCaptureManager()
job = mgr.start({"crawl": True, "crawl_url": "https://example.com",
                 "viewports": ["desktop", "mobile"], "schemes": ["light", "dark"]})
```

## Requirements

- Python **3.10+**
- **Playwright** + Chromium (`python -m playwright install chromium`).
- For `walk` mode: a RHOBEAR Vertex Gateway token (`RHOBEAR_GW_API_KEY`) — walkthrough narration
  and camera choreography go through the family gateway. No API keys checked into source.
- For `walk` MP4 export: `ffmpeg` on PATH.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

[MIT](LICENSE) — free for anyone, forever. Fork it, ship it, rebrand it. Don't like the name?
Take it off; it's yours.

<p align="center"><sub>Made by SunSponge LLC · a thank-you to the open source we all build on.</sub></p>
