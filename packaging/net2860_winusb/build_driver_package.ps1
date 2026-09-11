# build_driver_package.ps1
#
# Builds and self-signs the WinUSB driver package that binds the legacy BIO
# camera (see reflex_net2860.inf). Produces three files the clinic
# installer ships:
#
#   reflex_net2860.inf   the package itself (no binaries -- see the INF)
#   reflex_net2860.cat   its catalogue, signed
#   reflex_net2860.cer   the public certificate, to be trusted at install
#
# The private key never leaves this machine; only the .cer ships.
#
# WHY SELF-SIGNED IS ENOUGH HERE: this package contains no kernel binaries,
# only Include/Needs references to the inbox winusb.inf, so Kernel Mode Code
# Signing -- the gate that demands an EV certificate and a Partner Center
# submission -- never applies. What remains is PnP *package* signing, which
# any certificate the machine trusts satisfies. See DECISIONS.md's
# 2026-09-09 entries for the full reasoning.
#
# The trade being made: the installer must add this certificate to the
# machine's Trusted Root and Trusted Publishers stores. That is a narrow,
# revocable grant ("trust driver packages from this publisher"), and is not
# the same thing as machine-wide test-signing, which DECISIONS.md rejected
# for the eMPIA driver. A commercial OV code-signing certificate would avoid
# the root install entirely, at a few hundred dollars a year.
#
# Re-runnable: reuses an existing certificate with the same subject rather
# than minting a new one, so a rebuild does not invalidate installs.
#
# THE SIGNING KEY IS DELIBERATELY NOT BACKED UP (decided 2026-09-10). It
# lives only in this user's certificate store on this machine. Lose it and
# the next build signs as a *different* publisher; nothing bricks, because
# the installer ships and trusts the new .cer alongside the driver, so a
# normal reinstall self-heals. What is given up is a **driver-only** update:
# a one-file fix becomes "re-run the full installer everywhere". Judged an
# acceptable trade rather than keeping a private key indefinitely or buying
# a commercial OV certificate. To preserve it after all:
#
#   $c = Get-ChildItem Cert:\CurrentUser\My |
#        Where-Object { $_.Subject -eq "CN=NECO Reflex driver signing" }
#   Export-PfxCertificate -Cert $c -FilePath signing-key.pfx `
#                         -Password (Read-Host -AsSecureString)
#
# Import it on a new build machine with Import-PfxCertificate before running
# this script, and it will be reused rather than replaced.

[CmdletBinding()]
param(
    [string]$CertSubject = "CN=NECO Reflex driver signing",
    # Left empty and resolved in the body: $PSScriptRoot is not reliably
    # this script's directory while parameter defaults are being bound --
    # with a PowerShell profile loaded it resolves to the profile's folder,
    # which is where the .cer silently ended up the first time.
    [string]$OutDir = "",
    # Also install into THIS machine (trust the cert, stage the driver).
    # Needs elevation. Intended for a dev/test box, not the build step.
    [switch]$Install
)

$ErrorActionPreference = "Stop"

if (-not $OutDir) { $OutDir = $PSScriptRoot }

function Fail($msg) { Write-Host $msg -ForegroundColor Red; exit 1 }

# --- locate the SDK/WDK tools -------------------------------------------
# Inf2Cat is the only supported way to produce a driver-package catalogue.
# PowerShell's New-FileCatalog is NOT a substitute: it emits a generic
# Authenticode catalogue without the per-OS attributes PnP expects.
function Find-Tool($name) {
    $roots = @("${env:ProgramFiles(x86)}\Windows Kits", "$env:ProgramFiles\Windows Kits")
    foreach ($r in $roots) {
        if (Test-Path $r) {
            $hit = Get-ChildItem $r -Recurse -Filter $name -ErrorAction SilentlyContinue |
                   Sort-Object FullName -Descending | Select-Object -First 1
            if ($hit) { return $hit.FullName }
        }
    }
    return $null
}

$inf2cat = Find-Tool "Inf2Cat.exe"
$signtool = Find-Tool "signtool.exe"
if (-not $inf2cat -or -not $signtool) {
    Fail @"
Missing driver-signing tools.

  Inf2Cat.exe : $(if ($inf2cat) { $inf2cat } else { 'NOT FOUND' })   (Windows WDK)
  signtool.exe: $(if ($signtool) { $signtool } else { 'NOT FOUND' })   (Windows SDK)

They come from two different kits, and installing only the SDK is the easy
mistake -- it gets you signtool and leaves Inf2Cat missing:

  SDK   tick only "Windows SDK Signing Tools for Desktop Apps" (a few MB;
        the rest of that installer's 3.6 GB is irrelevant here)
  WDK   winget install Microsoft.WindowsWDK.10.0.<version>

The WDK version must match the installed SDK version, not the OS build.

Build-machine requirement only -- clinic machines need nothing beyond the
three output files.
"@
}
Write-Host "Inf2Cat : $inf2cat"
Write-Host "SignTool: $signtool"

# --- certificate ---------------------------------------------------------
$cert = Get-ChildItem Cert:\CurrentUser\My |
        Where-Object { $_.Subject -eq $CertSubject -and $_.NotAfter -gt (Get-Date) } |
        Sort-Object NotAfter -Descending | Select-Object -First 1

if ($cert) {
    Write-Host "Reusing certificate $($cert.Thumbprint) (expires $($cert.NotAfter.ToString('yyyy-MM-dd')))"
} else {
    Write-Host "Creating a new self-signed code-signing certificate..."
    $cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject $CertSubject `
        -CertStoreLocation Cert:\CurrentUser\My -KeyUsage DigitalSignature `
        -KeyExportPolicy Exportable -NotAfter (Get-Date).AddYears(10) `
        -HashAlgorithm SHA256
    Write-Host "Created $($cert.Thumbprint)"
}

# --- catalogue -----------------------------------------------------------
$infName = "reflex_net2860.inf"
if (-not (Test-Path (Join-Path $PSScriptRoot $infName))) { Fail "$infName not found beside this script." }

Write-Host "Generating catalogue..."
& $inf2cat /driver:"$PSScriptRoot" /os:10_X64 /verbose
if ($LASTEXITCODE -ne 0) { Fail "Inf2Cat failed ($LASTEXITCODE). An INF syntax error is the usual cause." }

$cat = Join-Path $PSScriptRoot "reflex_net2860.cat"
if (-not (Test-Path $cat)) { Fail "Inf2Cat reported success but produced no .cat" }

Write-Host "Signing catalogue..."
& $signtool sign /fd SHA256 /sha1 $cert.Thumbprint /t http://timestamp.digicert.com $cat
if ($LASTEXITCODE -ne 0) { Fail "signtool failed ($LASTEXITCODE)" }

# Timestamped deliberately: without one the package stops validating the day
# the certificate expires. That is precisely how NET GmbH's 2011 driver
# still loads today -- see DECISIONS.md.

# --- verify the catalogue actually covers the INF ------------------------
# The catalogue hashes the INF's *bytes*, so anything that rewrites that
# file after this point invalidates it while leaving both files present and
# apparently fine. A line-ending conversion on git checkout is the one that
# actually bit (2026-09-10): the installer compiled, shipped, and failed on
# a clinic machine with pnputil -536870325 (0xE000024B,
# SPAPI_E_FILE_HASH_NOT_IN_CATALOG). .gitattributes now pins the INF's
# working-tree form; this is the check that would have caught it anyway, at
# build time, where a failure costs a rebuild instead of a site visit.
Write-Host "Verifying the catalogue covers the INF..."
$infPath = Join-Path $PSScriptRoot $infName
# $ErrorActionPreference is "Stop" for this script, which turns signtool's
# stderr into a *terminating* NativeCommandError the moment 2>&1 merges it
# into the pipeline -- killing the script here, before the exit-code branch
# below that exists precisely to tolerate an untrusted chain. Relaxed just
# around this call so a failed verify stays data to inspect, not a crash.
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$verifyOutput = & $signtool verify /pa /v /c $cat $infPath 2>&1 | Out-String
$verifyExit = $LASTEXITCODE
$ErrorActionPreference = $prevEap
if ($verifyOutput -match "not found in the specified catalog") {
    Write-Host $verifyOutput
    Fail @"
The signed catalogue does not cover $infName.

The two files are out of step: the catalogue was hashed against different
bytes than the INF now on disk. Anything that rewrote the INF -- an edit, or
a git checkout converting its line endings -- does this.

Check the INF is the one you mean, then re-run this script so the catalogue
is regenerated against it.
"@
}
if ($verifyExit -ne 0) {
    # The catalogue covers the INF (checked above); what failed is the trust
    # chain, because this machine has not been asked to trust the signing
    # certificate. Expected on a build machine that has never run -Install --
    # the clinic installer trusts the shipped .cer on the target. Not a
    # reason to fail the build.
    Write-Host "Catalogue covers the INF. (This machine does not trust the signing certificate itself -- normal on a build-only machine.)" -ForegroundColor Yellow
} else {
    Write-Host "Verified: the catalogue covers the INF." -ForegroundColor Green
}

$cer = Join-Path $OutDir "reflex_net2860.cer"
Export-Certificate -Cert $cert -FilePath $cer -Force | Out-Null
Write-Host "Exported public certificate -> $cer"

Write-Host ""
Write-Host "Package ready:" -ForegroundColor Green
Get-ChildItem $PSScriptRoot -Include *.inf, *.cat, *.cer -Recurse |
    ForEach-Object { Write-Host ("  {0,-32} {1,8} bytes" -f $_.Name, $_.Length) }

# --- optional local install ---------------------------------------------
if ($Install) {
    $admin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
             ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $admin) { Fail "-Install needs an elevated shell." }

    Write-Host ""
    Write-Host "Trusting the certificate (Root + TrustedPublisher)..."
    certutil -addstore -f Root $cer | Out-Null
    certutil -addstore -f TrustedPublisher $cer | Out-Null

    Write-Host "Staging the driver..."
    pnputil /add-driver (Join-Path $PSScriptRoot $infName) /install
    if ($LASTEXITCODE -ne 0) { Fail "pnputil failed ($LASTEXITCODE)" }

    Write-Host ""
    Write-Host "Installed. Verify with:" -ForegroundColor Green
    Write-Host "  python -c `"import winusb; print(winusb.find_by_vid_pid(0x20F1,0x0004))`""
}
