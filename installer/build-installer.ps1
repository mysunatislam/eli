# Builds an installable Windows app for Eli: a self-contained portable Python runtime (with every
# backend dependency already installed) plus the Electron overlay, packaged as one NSIS installer.
# A laptop that runs the installer needs nothing pre-installed - no Python, no Node, no Git.
#
# Run from the repo root or anywhere:
#   powershell -ExecutionPolicy Bypass -File installer\build-installer.ps1
#   powershell -ExecutionPolicy Bypass -File installer\build-installer.ps1 -Clean   (rebuild the portable Python from scratch)
#
# Output: desktop\dist\Eli-Setup-<version>.exe
# Needs: internet access on THIS machine (downloads embeddable Python + pip installs requirements).
param(
  [string]$PythonVersion = "3.12.10",
  [switch]$Clean
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$desktop = Join-Path $root "desktop"
$backend = Join-Path $root "backend"
$buildRes = Join-Path $desktop "build-resources"
$pyDir = Join-Path $buildRes "python"
$backendOut = Join-Path $buildRes "backend"
$pyExe = Join-Path $pyDir "python.exe"

Write-Host "== Eli installer build ==" -ForegroundColor Cyan
Write-Host "root: $root"

# ---------- 1. portable Python runtime ----------------------------------------------------------------
if ($Clean -and (Test-Path $pyDir)) { Remove-Item $pyDir -Recurse -Force }

if (Test-Path $pyExe) {
  Write-Host "Portable Python already built at $pyDir - reusing it (pass -Clean to rebuild from scratch)." -ForegroundColor Yellow
  Write-Host "Refreshing backend requirements (fast if nothing changed) ..." -ForegroundColor Cyan
  & $pyExe -m pip install --no-warn-script-location -r (Join-Path $backend "requirements.txt")
  if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
} else {
  New-Item -ItemType Directory -Force $pyDir | Out-Null
  $embedUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
  $embedZip = Join-Path $env:TEMP "eli-python-embed.zip"
  Write-Host "Downloading embeddable Python $PythonVersion ..." -ForegroundColor Cyan
  Invoke-WebRequest -UseBasicParsing -Uri $embedUrl -OutFile $embedZip
  Expand-Archive -Path $embedZip -DestinationPath $pyDir -Force
  Remove-Item $embedZip -Force

  $pth = Get-ChildItem $pyDir -Filter "python*._pth" | Select-Object -First 1
  $pthLines = Get-Content $pth.FullName
  $pthLines = $pthLines -replace '^#\s*import site$', 'import site'
  if ($pthLines -notcontains 'Lib\site-packages') { $pthLines += 'Lib\site-packages' }
  Set-Content -Path $pth.FullName -Value $pthLines -Encoding ASCII

  $getPip = Join-Path $env:TEMP "eli-get-pip.py"
  Write-Host "Bootstrapping pip ..." -ForegroundColor Cyan
  Invoke-WebRequest -UseBasicParsing -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPip
  & $pyExe $getPip --no-warn-script-location
  if ($LASTEXITCODE -ne 0) { throw "get-pip failed" }
  Remove-Item $getPip -Force

  Write-Host "Installing setuptools/wheel (some deps still ship as sdists) ..." -ForegroundColor Cyan
  & $pyExe -m pip install --no-warn-script-location --upgrade pip setuptools wheel
  if ($LASTEXITCODE -ne 0) { throw "pip bootstrap of setuptools/wheel failed" }

  Write-Host "Installing backend requirements into the portable runtime (this takes a few minutes) ..." -ForegroundColor Cyan
  & $pyExe -m pip install --no-warn-script-location -r (Join-Path $backend "requirements.txt")
  if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
}

# pywin32 normally needs a postinstall step (pywin32_postinstall.py -install) that registers its DLLs
# system-wide - that needs admin rights and we don't want it. The portable fix: copy pythoncomXX.dll /
# pywintypesXX.dll next to python.exe, which Windows' DLL search already checks (no registration needed).
$pywinDlls = Join-Path $pyDir "Lib\site-packages\pywin32_system32"
if (Test-Path $pywinDlls) {
  Copy-Item (Join-Path $pywinDlls "*.dll") $pyDir -Force
  Write-Host "Copied pywin32 runtime DLLs next to python.exe (no admin postinstall needed)." -ForegroundColor Green
}

Get-ChildItem (Join-Path $pyDir "Lib\site-packages") -Directory -Filter "__pycache__" -Recurse -ErrorAction SilentlyContinue |
  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

$pySizeMB = (Get-ChildItem $pyDir -Recurse | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("Portable runtime size: {0:N0} MB" -f $pySizeMB) -ForegroundColor Green

# ---------- 2. backend source (no venv, no local data, no tests/dev assets) ----------------------------
if (Test-Path $backendOut) { Remove-Item $backendOut -Recurse -Force }
New-Item -ItemType Directory -Force $backendOut | Out-Null
$excludeDirs = @(".venv", "data", "__pycache__", "tests", ".pytest_cache")
robocopy $backend $backendOut /E /XD $excludeDirs /XF ".env" /NFL /NDL /NJH /NJS /NC /NS | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy failed copying backend source (code $LASTEXITCODE)" }
Get-ChildItem $backendOut -Directory -Filter "__pycache__" -Recurse -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
$backendSizeKB = (Get-ChildItem $backendOut -Recurse | Measure-Object Length -Sum).Sum / 1KB
Write-Host ("Backend source size: {0:N0} KB" -f $backendSizeKB) -ForegroundColor Green

# ---------- 3. Electron + electron-builder, then package the installer ---------------------------------
Push-Location $desktop
try {
  if (-not (Test-Path "node_modules\electron-builder")) {
    Write-Host "Installing desktop dependencies (npm install) ..." -ForegroundColor Cyan
    npm install --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
  }
  Write-Host "Building the NSIS installer ..." -ForegroundColor Cyan
  npx electron-builder --win nsis
  if ($LASTEXITCODE -ne 0) { throw "electron-builder failed" }
} finally {
  Pop-Location
}

$out = Get-ChildItem (Join-Path $desktop "dist") -Filter "Eli-Setup-*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($out) {
  $exeSizeMB = $out.Length / 1MB
  Write-Host ("`nDone: {0}  ({1:N0} MB)" -f $out.FullName, $exeSizeMB) -ForegroundColor Green
  Write-Host "Copy this one file to another Windows PC and run it. No Python, Node, or Git needed there." -ForegroundColor Green
} else {
  throw "Build finished but no installer .exe was found in desktop\dist"
}
