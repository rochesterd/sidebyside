# setup_net2860_helper.ps1
#
# Bootstraps .venv32/ -- a SEPARATE 32-bit Python environment for
# net2860_helper.py, the DirectShow capture helper for the older Vantage
# Plus BIO's NET GmbH KS722OUP camera. See DECISIONS.md's "Net2860Camera:
# 32-bit helper process for the older Vantage Plus BIO" entry for why this
# needs to be 32-bit: the vendor's DirectShow filter is only registered in
# Windows' WOW6432Node (32-bit) COM view, so it can't be driven from the
# project's normal (64-bit) .venv.
#
# Explicitly NOT part of setup.ps1 / the main dev bootstrap -- this
# hardware is not part of the prescribed instrument set in CLAUDE.md's
# Hardware table, so most dev machines will never need this script. Only
# run it if you're working on net2860_camera.py/net2860_helper.py against
# a real older-model Vantage Plus BIO.
#
# Safe to re-run: skips the download/extract if .venv32/python.exe already
# exists, and pip install is idempotent.

$ErrorActionPreference = "Stop"

function Fail($msg) {
    Write-Host $msg -ForegroundColor Red
    exit 1
}

$pythonVersion = "3.13.7"
$venvDir = ".venv32"
$venvPython = "$venvDir\python.exe"

if (Test-Path $venvPython) {
    Write-Host ".venv32 already exists, reusing it."
} else {
    Write-Host "Downloading Python $pythonVersion (32-bit embeddable)..."
    $zipUrl = "https://www.python.org/ftp/python/$pythonVersion/python-$pythonVersion-embed-win32.zip"
    $zipPath = "$env:TEMP\net2860_python32_embed.zip"
    try {
        Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath
    } catch {
        Fail "Failed to download $zipUrl - check network access, or that this exact patch version is still published."
    }

    Write-Host "Extracting to $venvDir..."
    Expand-Archive -Path $zipPath -DestinationPath $venvDir -Force
    Remove-Item $zipPath

    # Embeddable distributions ship with site-packages disabled by default
    # (the "._pth" file's "#import site" line is commented out) -- pip and
    # any installed packages would silently fail to import without this.
    $pthFile = Get-ChildItem $venvDir -Filter "python*._pth" | Select-Object -First 1
    if (-not $pthFile) { Fail "Could not find python*._pth in $venvDir after extraction." }
    (Get-Content $pthFile.FullName) -replace '^#import site$', 'import site' | Set-Content $pthFile.FullName

    Write-Host "Bootstrapping pip..."
    $getPipPath = "$env:TEMP\net2860_get_pip.py"
    try {
        Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPipPath
    } catch {
        Fail "Failed to download get-pip.py - check network access."
    }
    & $venvPython $getPipPath --no-warn-script-location
    if ($LASTEXITCODE -ne 0) { Fail "get-pip.py bootstrap failed." }
    Remove-Item $getPipPath
}

Write-Host "Installing dependencies from requirements-net2860.txt..."
& $venvPython -m pip install -r requirements-net2860.txt
if ($LASTEXITCODE -ne 0) { Fail "pip install -r requirements-net2860.txt failed." }

Write-Host ""
Write-Host "Done. net2860_camera.py's default python_exe path (.venv32\python.exe) is now valid." -ForegroundColor Green
