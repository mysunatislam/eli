# Eli — one-time setup (Windows 10/11). Run from the repo root:  powershell -ExecutionPolicy Bypass -File scripts\install.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Write-Host "== Eli setup ==" -ForegroundColor Magenta

# Windows' 260-char path limit breaks pip in deep folders; warn early.
if ($root.Length -gt 60) { Write-Warning "Project path is long ($($root.Length) chars). If pip fails with 'filename too long', move the folder to something like C:\eli." }

# --- Python backend -----------------------------------------------------------------------
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { throw "Python 3.10+ not found. Install it from python.org or the Microsoft Store, then re-run." }
$ver = & python -c "import sys; print(sys.version_info >= (3,10))"
if ($ver -ne "True") { throw "Python 3.10 or newer is required." }

$backend = Join-Path $root "backend"
Push-Location $backend
if (-not (Test-Path ".venv")) { Write-Host "Creating virtual environment..."; python -m venv .venv }
& ".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
Write-Host "Installing Python packages (first run: a few minutes)..."
& ".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host "Created backend\.env — add your ANTHROPIC_API_KEY there for the full experience." -ForegroundColor Yellow }
Pop-Location

# --- Electron overlay -----------------------------------------------------------------------
$node = Get-Command npm -ErrorAction SilentlyContinue
if (-not $node) { throw "Node.js (npm) not found. Install Node 18+ from nodejs.org, then re-run." }
Push-Location (Join-Path $root "desktop")
Write-Host "Installing Electron..."
npm install --no-audit --no-fund
Pop-Location

Write-Host "`nDone. Start Eli with:  scripts\start.ps1   (or double-click start.bat)" -ForegroundColor Green
