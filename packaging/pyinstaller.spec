# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for capturd.exe — single-file Windows bundle.

Build with:
    pyinstaller --clean packaging/pyinstaller.spec

Prereqs (run once before building):
    python -m playwright install chromium
    python -m faster_whisper  # downloads small.en model into HF cache
    # ffmpeg static build -> packaging/ffmpeg/ffmpeg.exe + ffprobe.exe
    # (see packaging/README.md)
"""

import os
import sys
import glob
from pathlib import Path

import PyInstaller.utils.hooks as hooks

# ── paths ───────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent   # repo root
SPEC_DIR = Path(__file__).resolve().parent       # packaging/

# ── hidden imports ──────────────────────────────────────────────────────────
_hiddenimports = [
    # Playwright internals
    'playwright._impl._browser_type',
    'playwright._impl._api_structures',
    'playwright._impl._connection',
    'playwright._impl._transport',
    'playwright._impl._local_utils',
    'playwright._impl._object_factory',
    'playwright.async_api',
    'playwright.sync_api',
    # faster-whisper / ctranslate2 stack
    'faster_whisper',
    'ctranslate2',
    'numpy',
    'tokenizers',
    'tiktoken',
    'tiktoken_ext',
    'tiktoken_ext.openai_public',
    # edge-tts
    'edge_tts',
    'certifi',
    # httpx + httpcore
    'httpx',
    'httpcore',
    'h2',
    'hpack',
    # fastmcp / starlette / uvicorn
    'uvicorn',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'starlette',
    'sse_starlette',
    'anyio',
    'sniffio',
    # voice input
    'sounddevice',
    'soundfile',
    # misc stdlib + data
    'yaml',
    'asyncio',
    'concurrent.futures',
]

# Collect all submodules that PyInstaller might miss
_hiddenimports += hooks.collect_submodules('capturd')
_hiddenimports += hooks.collect_submodules('fastmcp')
_hiddenimports += hooks.collect_submodules('playwright')

# ── data files ──────────────────────────────────────────────────────────────
_datas = []

# 1. viewer.html template (capturd walk needs this at runtime)
viewer_src = ROOT / 'capturd' / 'walk' / 'templates' / 'viewer.html'
if viewer_src.is_file():
    _datas.append((str(viewer_src), 'capturd/walk/templates'))

# 2. faster-whisper small.en model — locate in HF cache
def _find_whisper_model():
    model_dir_env = os.environ.get('WHISPER_MODEL_DIR', '').strip()
    if model_dir_env:
        d = Path(model_dir_env)
        if d.is_dir():
            return d
    for hf_root in (Path.home() / '.cache' / 'huggingface' / 'hub',
                    Path.home() / '.cache' / 'huggingface_hub'):
        patterns = [
            hf_root / 'models--Systran--faster-whisper-small.en' / 'snapshots' / '*',
            hf_root / 'models--guillaumekln--faster-whisper-small.en' / 'snapshots' / '*',
        ]
        for pat in patterns:
            matches = sorted(glob.glob(str(pat)))
            if matches:
                return Path(matches[-1])
    return None

_model_dir = _find_whisper_model()
if _model_dir:
    dest_base = 'faster_whisper_models/small.en'
    for root_dir, _dirs, files in os.walk(str(_model_dir)):
        for fname in files:
            fpath = Path(root_dir) / fname
            rel = fpath.relative_to(_model_dir)
            _datas.append((str(fpath), str(Path(dest_base) / rel.parent)))
    print(f"[pyinstaller.spec] Bundled whisper model from {_model_dir}", file=sys.stderr)
else:
    print("[pyinstaller.spec] WARNING: faster-whisper model not found — voice mode disabled.", file=sys.stderr)

# ── binaries (Playwright Chromium + ffmpeg) ─────────────────────────────────
_binaries = []

# 3. Playwright Chromium browser
def _find_playwright_browsers():
    candidates = []
    local_appdata = os.environ.get('LOCALAPPDATA', '')
    if local_appdata:
        candidates.append(Path(local_appdata) / 'ms-playwright')
    for cache_env in ('XDG_CACHE_HOME',):
        val = os.environ.get(cache_env, '')
        if val:
            candidates.append(Path(val) / 'ms-playwright')
    candidates.append(Path.home() / '.cache' / 'ms-playwright')

    for base in candidates:
        chromium_matches = sorted(glob.glob(str(base / 'chromium-*')))
        if chromium_matches:
            latest = Path(chromium_matches[-1])
            chrome_exe = latest / ('chrome.exe' if sys.platform == 'win32' else 'chrome')
            if chrome_exe.is_file():
                yield latest  # Return the dir; caller walks it
                break

for browser_dir in _find_playwright_browsers():
    dest_prefix = f'playwright_browsers/{browser_dir.name}'
    for root_dir, _dirs, files in os.walk(str(browser_dir)):
        for fname in files:
            fpath = Path(root_dir) / fname
            rel = fpath.relative_to(browser_dir)
            _binaries.append((str(fpath), str(Path(dest_prefix) / rel)))
    print(f"[pyinstaller.spec] Bundled Playwright browsers from {browser_dir}", file=sys.stderr)

# 4. ffmpeg static builds
_ffmpeg_dir = SPEC_DIR / 'ffmpeg'
for name in ('ffmpeg.exe', 'ffprobe.exe'):
    candidate = _ffmpeg_dir / name
    if candidate.is_file():
        _binaries.append((str(candidate), name))
    else:
        print(f"[pyinstaller.spec] WARNING: {name} not found at {candidate} — media features disabled.", file=sys.stderr)

# ── icon ────────────────────────────────────────────────────────────────────
_icon_path = SPEC_DIR / 'capturd.ico'
if not _icon_path.is_file():
    logo_src = ROOT / 'assets' / 'sunsponge-logo-wide.png'
    if logo_src.is_file():
        try:
            from PIL import Image
            img = Image.open(logo_src)
            sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
            img.save(str(_icon_path), format='ICO', sizes=sizes)
            print(f"[pyinstaller.spec] Generated icon: {_icon_path}", file=sys.stderr)
        except ImportError:
            print("[pyinstaller.spec] WARNING: Pillow not installed — no .ico generated.", file=sys.stderr)
    else:
        print(f"[pyinstaller.spec] WARNING: {logo_src} not found — no icon.", file=sys.stderr)

# ── assemble ────────────────────────────────────────────────────────────────
block_cipher = None

a = Analysis(
    [str(ROOT / 'capturd' / 'cli.py')],
    pathex=[str(ROOT)],
    binaries=_binaries,
    datas=_datas,
    hiddenimports=_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='capturd',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(_icon_path) if _icon_path.is_file() else None,
)
