# Capturd MSIX Packaging

Build a self-contained `.msix` installer so users can double-click to install
**Captur'd by Sun Sponge** on Windows 10+.

## Prerequisites

| Requirement | Notes |
|---|---|
| Windows 10+ (x64) | Build host must be Windows. CI uses `windows-latest`. |
| Python 3.10+ | 3.11 recommended. |
| Windows 10 SDK | Provides `makeappx.exe` and `signtool.exe`. [Download here](https://developer.microsoft.com/en-us/windows/downloads/windows-sdk/). During install, select "Windows SDK for Desktop Apps". |
| PyInstaller | `pip install pyinstaller` |
| ffmpeg static build | Download `ffmpeg-release-essentials.zip` from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip), extract `ffmpeg.exe` and `ffprobe.exe` into `packaging/ffmpeg/`. |
| Playwright Chromium | `python -m playwright install chromium` |
| faster-whisper model | `python -c "from faster_whisper import WhisperModel; WhisperModel('small.en')"` (downloads ~500MB into HF cache) |
| Disk space | ~2 GB free (build artifacts + bundled deps) |

## Quick start

```powershell
# 1. Install Python deps
pip install -e ".[voice,dev]"
pip install pyinstaller
pip install pillow       # for .ico generation at build time

# 2. Download runtime deps
python -m playwright install chromium
python -c "from faster_whisper import WhisperModel; WhisperModel('small.en')"

# 3. Fetch ffmpeg
#    Download https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip
#    Extract ffmpeg.exe and ffprobe.exe into packaging/ffmpeg/

# 4. Build
.\packaging\build-msix.ps1
```

## Build script

`packaging/build-msix.ps1` does four things:

1. **PyInstaller** — runs `pyinstaller packaging/pyinstaller.spec` to produce `dist/capturd.exe`.
2. **Stage** — copies the executable, `AppxManifest.xml`, and `assets/` into `msix-staging/`.
3. **Pack** — invokes `makeappx.exe pack` to produce `dist/Capturd-0.2.0.msix`.
4. **Sign** (optional) — if `CAPTURD_MSIX_CERT` is set, signs the MSIX with `signtool.exe`.

### Options

```powershell
.\packaging\build-msix.ps1 -SkipPyInstaller   # Skip the PyInstaller step (iterate on manifest/signing)
.\packaging\build-msix.ps1 -Sign               # Force interactive signing prompt
.\packaging\build-msix.ps1 -OutputDir "out"    # Custom output directory
```

### Environment variables

| Variable | Purpose |
|---|---|
| `CAPTURD_MSIX_CERT` | Path to `.pfx` code-signing certificate. If not set, produces unsigned MSIX. |
| `WHISPER_MODEL_DIR` | Override path to the `faster-whisper` small.en model directory (used by PyInstaller spec). |

## Output

```
dist/Capturd-0.2.0.msix
```

## Installing (sideload)

```powershell
# Developer mode must be enabled (Settings → Update & Security → For developers)
# Run as Administrator:
Add-AppxPackage -Path .\dist\Capturd-0.2.0.msix
```

To validate the installed package:

```powershell
Get-AppxPackageManifest -Package (Get-AppxPackage -Name "SunSpongeLLC.Capturd")
```

## What's bundled

| Asset | Source |
|---|---|
| `capturd.exe` | PyInstaller one-file build — all Python code + deps in one binary. |
| `viewer.html` | `capturd/walk/templates/viewer.html` — shipped inside the binary. |
| Playwright Chromium | Bundled from the local `ms-playwright` cache. |
| faster-whisper `small.en` | Bundled from HF cache (~500 MB). Enables voice mode offline. |
| `ffmpeg.exe` / `ffprobe.exe` | Static builds from gyan.dev, bundled inside the binary. |
| App icons | `assets/sunsponge-logo-wide.png` and `assets/sunsponge-mark.png`. |

## Capabilities declared

| Capability | Reason |
|---|---|
| `runFullTrust` | Playwright launches a real Chromium subprocess; audio devices need unrestricted access. |
| `internetClient` | Fetches web pages for screenshots, TTS, and MCP server. |
| `microphone` | Push-to-talk voice input for walk mode. |

## Store submission

This MSIX is built for **direct installation** (sideload / GitHub Releases).
Microsoft Store submission requires:

- A Windows Store developer account.
- The app to pass Windows App Certification Kit (WACK) tests.
- Separate Store-specific manifest entries.

That process is **out of scope for this packaging** and will be handled when
SunSponge LLC registers for the Store program.

## Follow-up work

- **`capturd-lite.exe`** — a second PyInstaller spec without the ~500 MB
  faster-whisper model. Users who don't need voice mode get a much smaller
  download. This is tracked separately; the current spec always bundles voice.

## CI

See `.github/workflows/build-msix.yml`. Triggers on pushes to `main`, `feat/*`,
and tags `v*`. Uploads the MSIX as a workflow artifact.
