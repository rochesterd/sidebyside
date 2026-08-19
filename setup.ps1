# setup.ps1
#
# Bootstraps the Python environment on a new machine: venv + dependencies,
# optionally the IDS peak Python bindings. Safe to re-run any time (e.g.
# after installing the IDS peak SDK, or to pick up a requirements.txt
# change) — it does not overwrite an existing venv, just reuses it.
#
# Does NOT touch config.json or camera role assignment; run settings.py
# for that after this finishes. See SETUP.md for the full procedure this
# script shortcuts, and DECISIONS.md's "setup.ps1" entry for why this
# doesn't conflict with settings.py staying a persistent, re-run-any-time
# tool rather than a one-shot installer.
#
# -DriveIds Yes|No skips the interactive prompt below — used by
# setup_wizard.py to drive this script non-interactively; a plain
# `.\setup.ps1` from a terminal still prompts as before.

param(
    [ValidateSet("Yes", "No", "")]
    [string]$DriveIds = ""
)

$ErrorActionPreference = "Stop"

function Fail($msg) {
    Write-Host $msg -ForegroundColor Red
    exit 1
}

# 1. Python version check
$pythonVersion = (python --version) 2>&1
if ($LASTEXITCODE -ne 0) {
    Fail "Python not found on PATH. Install Python 3.13 from python.org, then re-run this script."
}
if ($pythonVersion -notmatch "Python 3\.13") {
    Write-Host "Warning: found $pythonVersion - this project targets Python 3.13 specifically (see DECISIONS.md 'Python 3.13, not 3.11')." -ForegroundColor Yellow
}

# 2. Virtual environment
if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { Fail "python -m venv .venv failed." }
} else {
    Write-Host "Virtual environment already exists, reusing it."
}

$venvPython = ".\.venv\Scripts\python.exe"

# 3. Base dependencies
Write-Host "Installing dependencies from requirements.txt..."
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { Fail "pip install -r requirements.txt failed." }

Write-Host ""
Write-Host "Base install complete." -ForegroundColor Green
Write-Host "  python app.py   runs now, against SyntheticCamera if config.json is missing."
Write-Host ""

# 4. IDS peak bindings, only if this machine drives the real instrument cameras
if ($DriveIds -eq "") {
    $DriveIds = Read-Host "Will this machine drive the real IDS cameras (slit lamp / BIO)? [y/N]"
}
if ($DriveIds -match "^[Yy]") {
    Write-Host "Installing IDS peak Python bindings from requirements-ids.txt..."
    & $venvPython -m pip install -r requirements-ids.txt
    if ($LASTEXITCODE -ne 0) {
        Fail "pip install -r requirements-ids.txt failed. Check the pinned versions in that file still resolve on PyPI - see DECISIONS.md's 2026-08-12 entry on why they're pinned exactly."
    }

    Write-Host "Checking whether the IDS peak SDK runtime is installed..."
    & $venvPython -c "from ids_peak import ids_peak; ids_peak.Library.Initialize(); ids_peak.Library.Close()" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "IDS peak SDK runtime found - bindings loaded and initialized cleanly." -ForegroundColor Green
    } else {
        Write-Host ""
        Write-Host "IDS peak Python bindings are installed, but the SDK runtime itself was not found." -ForegroundColor Yellow
        Write-Host "Before real cameras will work:"
        Write-Host "  1. Install IDS peak - see SETUP.md Section 2 (Custom setup, enable the uEye Transport Layer)."
        Write-Host "  2. Driving the slit lamp specifically also needs SETUP.md Section 3 (uEye camera drivers)."
        Write-Host "  3. Re-run this script afterward, or just proceed straight to settings.py once installed."
    }
}

Write-Host ""
Write-Host "Next: run 'python settings.py' to detect cameras and assign roles (writes config.json)." -ForegroundColor Green
