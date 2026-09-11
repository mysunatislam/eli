# Starts the Eli backend and the desktop overlay.
#   scripts\start.ps1            backend in a minimised console + overlay (what start.bat does)
#   scripts\start.ps1 -Hidden    no consoles at all (used by the Windows startup entry; logs in backend\data\server.log)
param([switch]$Hidden)

$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$desktop = Join-Path $root "desktop"
$electronInstalled = "E:\Eli\Eli.exe"
$electronDev = Join-Path $desktop "node_modules\electron\dist\electron.exe"

if (Test-Path $electronInstalled) {
  # Installed packaged app manages its own bundled Python backend and lifecycle
  Start-Process -FilePath $electronInstalled -WorkingDirectory "E:\Eli"
  if (-not $Hidden) { Write-Host "Eli is starting: look for the heart at the bottom-right. Ctrl+Shift+E opens the panel." -ForegroundColor Green }
  exit 0
}

# Development mode
$py = Join-Path $backend ".venv\Scripts\python.exe"
$electron = $electronDev
$electronArgs = @(".")
$electronCwd = $desktop

if (-not (Test-Path $py)) { Write-Host "Run scripts\install.ps1 first (backend\.venv is missing)." -ForegroundColor Red; exit 1 }
if (-not (Test-Path $electron)) { Write-Host "Neither Eli.exe nor desktop\node_modules\electron was found." -ForegroundColor Red; exit 1 }

$port = 8790
$envFile = Join-Path $backend ".env"
if (Test-Path $envFile) { $m = Select-String -Path $envFile -Pattern "^ELI_PORT=(\d+)"; if ($m) { $port = [int]$m.Matches[0].Groups[1].Value } }

function Backend-Up {
  try { $r = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$port/health" -TimeoutSec 1; return ($r.StatusCode -eq 200) } catch { return $false }
}

# Backend (skip if one is already answering, e.g. you ran start.bat twice)
if (-not (Backend-Up)) {
  $logDir = Join-Path $backend "data"
  New-Item -ItemType Directory -Force $logDir | Out-Null
  $winStyle = if ($Hidden) { "Hidden" } else { "Minimized" }
  Start-Process -FilePath $py -ArgumentList "run.py" -WorkingDirectory $backend -WindowStyle $winStyle
  $ok = $false
  for ($i = 0; $i -lt 60; $i++) { if (Backend-Up) { $ok = $true; break }; Start-Sleep -Milliseconds 500 }
  if (-not $ok) { Write-Host "Backend did not come up on port $port. See backend\data\server.log." -ForegroundColor Yellow }
}

# Overlay (a second launch only brings the existing heart forward: single-instance).
# Its stdout/stderr always go to backend\data\electron.log so window failures are diagnosable.
$env:ELI_BACKEND_URL = "ws://127.0.0.1:$port/ws/desktop"
$env:ELI_BACKEND_HTTP = "http://127.0.0.1:$port"
$logDir = Join-Path $backend "data"
New-Item -ItemType Directory -Force $logDir | Out-Null
Start-Process -FilePath $electron -ArgumentList "." -WorkingDirectory $desktop -WindowStyle Hidden `
  -RedirectStandardOutput (Join-Path $logDir "electron.log") -RedirectStandardError (Join-Path $logDir "electron.err.log")
if (-not $Hidden) { Write-Host "Eli is starting: look for the heart at the bottom-right. Ctrl+Shift+E opens the panel." -ForegroundColor Green }
