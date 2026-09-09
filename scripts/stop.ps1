# Stops Eli: the backend (python run.py in backend\) and the overlay (electron started from desktop\).
$root = Split-Path -Parent $PSScriptRoot
$backend = (Join-Path $root "backend").ToLower()
$desktop = (Join-Path $root "desktop").ToLower()
$n = 0
Get-CimInstance Win32_Process | Where-Object {
  ($_.Name -like "python*" -and $_.CommandLine -like "*run.py*" -and ($_.ExecutablePath -and $_.ExecutablePath.ToLower().StartsWith($backend))) -or
  ($_.Name -eq "electron.exe" -and $_.ExecutablePath -and $_.ExecutablePath.ToLower().StartsWith($desktop))
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $n++ }
Write-Output "stopped $n Eli process(es)"
