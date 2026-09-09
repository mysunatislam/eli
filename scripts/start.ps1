# Starts the Eli backend and the desktop overlay.
#   scripts\start.ps1            backend in a minimised console + overlay (what start.bat does)
#   scripts\start.ps1 -Hidden    no consoles at all (used by the Windows startup entry; logs in backend\data\server.log)
param([switch]$Hidden)

$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$desktop = Join-Path $root "desktop"
$py = Join-Path $backend ".venv\Scripts\python.exe"
$electron = Join-Path $desktop "node_modules\electron\dist\electron.exe"
if (-not (Test-Path $py)) { Write-Host "Run scripts\install.ps1 first (backend\.venv is missing)." -ForegroundColor Red; exit 1 }
if (-not (Test-Path $electron)) { Write-Host "Run scripts\install.ps1 first (desktop\node_modules is missing)." -ForegroundColor Red; exit 1 }

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
  if ($Hidden) {
    Start-Process -FilePath $py -ArgumentList "run.py" -WorkingDirectory $backend -WindowStyle Hidden `
      -RedirectStandardOutput (Join-Path $logDir "server.log") -RedirectStandardError (Join-Path $logDir "server.err.log")
  } else {
    Start-Process -FilePath $py -ArgumentList "run.py" -WorkingDirectory $backend -WindowStyle Minimized
  }
  $ok = $false
  for ($i = 0; $i -lt 60; $i++) { if (Backend-Up) { $ok = $true; break }; Start-Sleep -Milliseconds 500 }
  if (-not $ok) { Write-Host "Backend did not come up on port $port. See backend\data\server.log." -ForegroundColor Yellow }
}

# Overlay (a second launch only brings the existing heart forward: single-instance)
$env:ELI_BACKEND_URL = "ws://127.0.0.1:$port/ws/desktop"
$env:ELI_BACKEND_HTTP = "http://127.0.0.1:$port"
Start-Process -FilePath $electron -ArgumentList "." -WorkingDirectory $desktop -WindowStyle Hidden
if (-not $Hidden) { Write-Host "Eli is starting: look for the heart at the bottom-right. Ctrl+Shift+E opens the panel." -ForegroundColor Green }
