# Eli — one-time setup (Windows 10/11). Run from the repo root:  powershell -ExecutionPolicy Bypass -File scripts\install.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Write-Host "== Eli setup ==" -ForegroundColor Magenta

# Windows' 260-char path limit breaks pip in deep folders; warn early.
if ($root.Length -gt 60) { Write-Warning "Project path is long ($($root.Length) chars). If pip fails with 'filename too long', move the folder to something like C:\eli." }

# --- Python backend -----------------------------------------------------------------------
$pyCmd = "python"
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $pyCmd = "py"
    } else {
        $discoveredPy = Get-ChildItem -Path "$env:LocalAppData\Programs\Python\Python3*", "$env:ProgramFiles\Python3*", "C:\Python3*" -Filter "python.exe" -Recurse -Depth 2 -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
        if ($discoveredPy -and (Test-Path $discoveredPy)) {
            $pyCmd = $discoveredPy
        } else {
            Write-Host "Python 3.10+ was not found on your system." -ForegroundColor Red
            Write-Host "To install Python automatically via winget, run: winget install Python.Python.3.12" -ForegroundColor Cyan
            Write-Host "Or download Python from https://www.python.org/downloads/ (ensure 'Add python.exe to PATH' is checked)." -ForegroundColor Yellow
            throw "Python 3.10+ not found. Install it and re-run."
        }
    }
}
$ver = & $pyCmd -c "import sys; print(sys.version_info >= (3,10))"
if ($ver -ne "True") { throw "Python 3.10 or newer is required." }

$backend = Join-Path $root "backend"
Push-Location $backend
if (-not (Test-Path ".venv")) { Write-Host "Creating virtual environment..."; & $pyCmd -m venv .venv }
& ".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
Write-Host "Installing Python packages (first run: a few minutes)..."
& ".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
$badDll = Join-Path $backend ".venv\Lib\site-packages\winrt\msvcp140.dll"
if (Test-Path $badDll) { Remove-Item $badDll -Force }
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host "Created backend\.env - add your GEMINI_API_KEY or ANTHROPIC_API_KEY there." -ForegroundColor Yellow }
Pop-Location

# --- Electron overlay -----------------------------------------------------------------------
$node = Get-Command npm -ErrorAction SilentlyContinue
if (-not $node) { throw "Node.js (npm) not found. Install Node 18+ from nodejs.org, then re-run." }
Push-Location (Join-Path $root "desktop")
Write-Host "Installing Electron..."
npm install --no-audit --no-fund
Pop-Location

Write-Host "Done. Start Eli with: scripts\start.ps1 (or double-click start.bat)" -ForegroundColor Green
