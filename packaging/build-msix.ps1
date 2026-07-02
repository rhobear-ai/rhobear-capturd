<#
.SYNOPSIS
    Build a self-contained Capturd MSIX package from source.

.DESCRIPTION
    1. Runs PyInstaller via the spec at packaging/pyinstaller.spec.
    2. Stages output + AppxManifest.xml + assets into msix-staging/.
    3. Packs into dist/Capturd-0.2.0.msix via makeappx.exe.
    4. Optionally signs if CAPTURD_MSIX_CERT env var is set.

.PARAMETER SkipPyInstaller
    Skip the PyInstaller step (useful when iterating on manifest/signing).

.PARAMETER OutputDir
    Override the output directory (default: dist/).

.PARAMETER Sign
    Force signing even if CAPTURD_MSIX_CERT is not set (will prompt for .pfx path).

.EXAMPLE
    .\packaging\build-msix.ps1

.EXAMPLE
    .\packaging\build-msix.ps1 -SkipPyInstaller
#>

[CmdletBinding()]
param(
    [switch]$SkipPyInstaller,
    [string]$OutputDir = "dist",
    [switch]$Sign
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$StagingDir = Join-Path $RepoRoot "msix-staging"
$DistDir = Join-Path $RepoRoot $OutputDir
$Version = "0.2.0"
$MsixName = "Capturd-$Version.msix"
$MsixPath = Join-Path $DistDir $MsixName

Write-Host "=== Capturd MSIX Builder v$Version ===" -ForegroundColor Cyan
Write-Host "  Repo root : $RepoRoot"
Write-Host "  Staging   : $StagingDir"
Write-Host "  Output    : $MsixPath"
Write-Host ""

# ────────────────────────────────────────────────────────────────────────────
# Step 1: PyInstaller
# ────────────────────────────────────────────────────────────────────────────
if (-not $SkipPyInstaller) {
    Write-Host "[1/4] Running PyInstaller..." -ForegroundColor Yellow

    $SpecFile = Join-Path $RepoRoot "packaging" "pyinstaller.spec"
    if (-not (Test-Path $SpecFile)) {
        Write-Error "Spec file not found: $SpecFile"
        exit 1
    }

    # Clean previous build output
    $pyiArgs = @(
        "packaging/pyinstaller.spec",
        "--clean",
        "--distpath", $DistDir,
        "--workpath", (Join-Path $RepoRoot "build" "pyinstaller"),
        "--noconfirm"
    )

    Push-Location $RepoRoot
    try {
        $pyiResult = & pyinstaller @pyiArgs 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Error "PyInstaller failed with exit code $LASTEXITCODE"
            Write-Host $pyiResult
            exit 1
        }
        Write-Host "  PyInstaller done." -ForegroundColor Green
    }
    finally {
        Pop-Location
    }
}
else {
    Write-Host "[1/4] Skipping PyInstaller (--SkipPyInstaller)" -ForegroundColor DarkYellow
}

# ────────────────────────────────────────────────────────────────────────────
# Step 2: Stage into msix-staging/
# ────────────────────────────────────────────────────────────────────────────
Write-Host "[2/4] Staging MSIX contents..." -ForegroundColor Yellow

# Clean + recreate staging dir
if (Test-Path $StagingDir) {
    Remove-Item -Recurse -Force $StagingDir
}
New-Item -ItemType Directory -Force -Path $StagingDir | Out-Null

# Copy the PyInstaller dist directory contents
$pyiDist = Join-Path $DistDir "capturd"
if (-not (Test-Path $pyiDist)) {
    # Onefile mode: capturd.exe is directly in dist/
    $capturdExe = Join-Path $DistDir "capturd.exe"
    if (Test-Path $capturdExe) {
        Copy-Item $capturdExe $StagingDir
        Write-Host "  Copied capturd.exe (one-file build)" -ForegroundColor Green
    }
    else {
        Write-Error "Neither dist/capturd/ nor dist/capturd.exe found. Run PyInstaller first."
        exit 1
    }
}
else {
    # Directory mode: copy everything
    Copy-Item -Recurse "$pyiDist\*" $StagingDir
    Write-Host "  Copied dist/capturd/ contents" -ForegroundColor Green
}

# Copy AppxManifest.xml
$manifestSrc = Join-Path $RepoRoot "packaging" "msix" "AppxManifest.xml"
if (-not (Test-Path $manifestSrc)) {
    Write-Error "AppxManifest.xml not found: $manifestSrc"
    exit 1
}
Copy-Item $manifestSrc (Join-Path $StagingDir "AppxManifest.xml")
Write-Host "  Copied AppxManifest.xml" -ForegroundColor Green

# Copy assets into staging subdir
$assetsSrc = Join-Path $RepoRoot "assets"
$assetsDst = Join-Path $StagingDir "assets"
if (Test-Path $assetsSrc) {
    New-Item -ItemType Directory -Force -Path $assetsDst | Out-Null
    Copy-Item -Recurse "$assetsSrc\*" $assetsDst
    Write-Host "  Copied assets/" -ForegroundColor Green
}
else {
    Write-Warning "assets/ directory not found — splash/logo tiles will be missing"
}

Write-Host "  Staging complete: $StagingDir" -ForegroundColor Green

# ────────────────────────────────────────────────────────────────────────────
# Step 3: Pack with makeappx.exe
# ────────────────────────────────────────────────────────────────────────────
Write-Host "[3/4] Packing MSIX with makeappx.exe..." -ForegroundColor Yellow

# Locate makeappx.exe — Windows SDK
$makeappx = $null
$sdkPaths = @(
    "${env:ProgramFiles(x86)}\Windows Kits\10\bin\10.0.22621.0\x64\makeappx.exe",
    "${env:ProgramFiles(x86)}\Windows Kits\10\bin\10.0.22000.0\x64\makeappx.exe",
    "${env:ProgramFiles(x86)}\Windows Kits\10\bin\10.0.19041.0\x64\makeappx.exe",
    "${env:ProgramFiles(x86)}\Windows Kits\10\bin\10.0.18362.0\x64\makeappx.exe",
    "${env:ProgramFiles(x86)}\Windows Kits\10\bin\10.0.17763.0\x64\makeappx.exe"
)

# Also try glob for any SDK version
$sdkBinRoot = "${env:ProgramFiles(x86)}\Windows Kits\10\bin"
if (Test-Path $sdkBinRoot) {
    $allVersions = Get-ChildItem -Path $sdkBinRoot -Directory | Sort-Object Name -Descending
    foreach ($v in $allVersions) {
        $candidate = Join-Path $v.FullName "x64\makeappx.exe"
        if (Test-Path $candidate) {
            $sdkPaths += $candidate
        }
    }
}

foreach ($p in $sdkPaths) {
    if (Test-Path $p) {
        $makeappx = $p
        break
    }
}

if (-not $makeappx) {
    Write-Error @"
makeappx.exe not found. Install the Windows 10 SDK:

  1. Download from https://developer.microsoft.com/en-us/windows/downloads/windows-sdk/
  2. During install, select "Windows SDK for Desktop Apps" (includes makeappx.exe)
  3. Re-run this script.
"@
    exit 1
}

Write-Host "  Using makeappx: $makeappx"

# Ensure dist dir exists
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null

# Pack
$packArgs = @(
    "pack",
    "/d", $StagingDir,
    "/p", $MsixPath,
    "/overwrite"
)

& $makeappx @packArgs
if ($LASTEXITCODE -ne 0) {
    Write-Error "makeappx.exe pack failed with exit code $LASTEXITCODE"
    exit 1
}

Write-Host "  Packed: $MsixPath" -ForegroundColor Green

# ────────────────────────────────────────────────────────────────────────────
# Step 4: Sign (optional)
# ────────────────────────────────────────────────────────────────────────────
Write-Host "[4/4] Code signing..." -ForegroundColor Yellow

$certPath = $env:CAPTURD_MSIX_CERT

if ($Sign -and -not $certPath) {
    Write-Host "  CAPTURD_MSIX_CERT not set. Prompting for .pfx path..." -ForegroundColor DarkYellow
    $certPath = Read-Host "Path to .pfx signing certificate (or Enter to skip)"
}

if ($certPath -and (Test-Path $certPath)) {
    Write-Host "  Signing with $certPath ..."
    & signtool.exe sign /fd SHA256 /a /f "$certPath" /v "$MsixPath"
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  Signed: $MsixPath" -ForegroundColor Green
    }
    else {
        Write-Warning "signtool.exe exited with code $LASTEXITCODE — MSIX is unsigned"
    }
}
elseif ($certPath) {
    Write-Warning "Signing certificate not found at '$certPath' — MSIX is unsigned"
}
else {
    Write-Warning "No signing certificate provided (set CAPTURD_MSIX_CERT or use -Sign)."
    Write-Warning "The MSIX is UNSIGNED and will require developer-mode sideload."
}

# ────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== Build complete ===" -ForegroundColor Cyan
Write-Host "  MSIX : $MsixPath"
Write-Host "  Size : $((Get-Item $MsixPath).Length / 1MB) MB"
Write-Host ""
Write-Host "To install (PowerShell as Administrator):"
Write-Host "  Add-AppxPackage -Path '$MsixPath'"
Write-Host ""
Write-Host "To validate the manifest:"
Write-Host "  Get-AppxPackageManifest -Package (Get-AppxPackage -Name 'SunSpongeLLC.Capturd')"
