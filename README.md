<p align="center">
  <img src="assets/sunsponge-logo-wide.png" alt="SunSponge Capture" width="420">
</p>

<h1 align="center">SunSponge Capture</h1>

<p align="center">
  <b>Rested-state website screenshots, in bulk.</b><br>
  Point it at a site, a sitemap, a list of URLs, or a folder of HTML — get clean, settled,
  full-page shots across every viewport and color scheme.
</p>

<p align="center">
  <a href="LICENSE"><img alt="MIT" src="https://img.shields.io/badge/license-MIT-7dffd5"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-f5b942">
  <img alt="Playwright" src="https://img.shields.io/badge/engine-Playwright-b794f4">
</p>

---

## What it does

Most screenshot tools fire too early — mid-animation, before fonts load, while a carousel is sliding —
and you get junk. SunSponge Capture takes **rested** shots: it disables animations and transitions,
waits for fonts/layout/network to settle, pauses video, and scrolls to top, so **every capture is the
same clean steady state**. Then it does it for **every page × every viewport × every color scheme** in
one run.

- 🕸️ **Four ways in** — crawl a whole **site**, expand a **sitemap.xml**, paste a **list of URLs**, or
  point at a **local HTML** file/folder.
- 📱 **Responsive matrix** — desktop (1440×1000), tablet (834×1112), mobile (390×844), in **light and
  dark**, full-page.
- 🧘 **Rested state** — animations killed, fonts awaited, motion stopped — deterministic, diff-friendly shots.
- 🧭 **Smart discovery** — same-site crawl with depth limit, sitemap + `robots.txt` sniffing, tracking-param
  stripping, asset/`mailto:`/fragment filtering.
- 📦 **Clean output** — numbered `NNN-site-viewport-scheme.png`, a `manifest.json`, exported as a ZIP or folder.
- ⚡ **Concurrent** — parallel workers, contexts reused per (site, viewport, scheme), retries on flaky loads.

> In acceptance runs it captured a **22-page site at 132/132 targets (0 missed)** and a **24-screen
> local build**, end to end, unattended.

## Install

```bash
git clone https://github.com/deariencampbell1-sys/sunsponge-capture
cd sunsponge-capture
pip install -e .
python -m playwright install chromium     # one-time browser download
```

> A PyPI release (so `pip install sunsponge-capture` just works) is on the way — for now, install from source above.

## Usage

```bash
# Crawl a whole site, every viewport + scheme, into a folder
sunsponge-capture --site https://example.com --out ./shots

# Just a few URLs, mobile dark only, as JPEG
sunsponge-capture --urls "https://example.com,https://example.com/pricing" \
  --viewports mobile --schemes dark --format jpeg

# A local build before you deploy
sunsponge-capture --local ./dist --viewports desktop,mobile

# A sitemap, capped at 50 pages
sunsponge-capture --sitemap https://example.com/sitemap.xml --max-pages 50
```

Run `sunsponge-capture --help` for every flag (depth, workers, settle time, full-page toggle, …).

### As a library

```python
from sunsponge_capture import RestedCaptureManager

mgr = RestedCaptureManager()
job = mgr.start({"crawl": True, "crawl_url": "https://example.com",
                 "viewports": ["desktop", "mobile"], "schemes": ["light", "dark"]})
print(job["job_id"])
```

## Requirements

- Python **3.10+**
- **Playwright** + Chromium (`python -m playwright install chromium`). On Windows it will also fall back
  to a system Edge/Chrome channel if available.

## Development

```bash
pip install -e ".[dev]"
pytest            # unit tests run without a browser
```

## License

[MIT](LICENSE) — free for anyone, forever. Use it, fork it, ship it, rebrand it. Don't like the name?
Take it off; it's yours.

<p align="center"><sub>Made by SunSponge LLC · a thank-you to the open source we all build on.</sub></p>
