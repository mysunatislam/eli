# Make Eli start when you sign in to Windows (a shortcut in your Startup folder; visible in
# Task Manager > Startup as "Eli"), or remove it again.
#   scripts\autostart.ps1 -Enable
#   scripts\autostart.ps1 -Disable
#   scripts\autostart.ps1 -Status
param([switch]$Enable, [switch]$Disable, [switch]$Status)

$root = Split-Path -Parent $PSScriptRoot
$startup = [Environment]::GetFolderPath("Startup")
$lnk = Join-Path $startup "Eli.lnk"
$target = Join-Path $root "scripts\start.ps1"

if ($Enable) {
  $shell = New-Object -ComObject WScript.Shell
  $s = $shell.CreateShortcut($lnk)
  $s.TargetPath = "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe"
  $s.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$target`" -Hidden"
  $s.WorkingDirectory = $root
  $s.WindowStyle = 7
  $s.Description = "Eli AI companion"
  $icon = Join-Path $root "desktop\node_modules\electron\dist\electron.exe"
  if (Test-Path $icon) { $s.IconLocation = "$icon,0" }
  $s.Save()
  Write-Output "enabled: $lnk"
  exit 0
}
if ($Disable) {
  if (Test-Path $lnk) { Remove-Item $lnk -Force }
  Write-Output "disabled"
  exit 0
}
if (Test-Path $lnk) { Write-Output "enabled: $lnk" } else { Write-Output "disabled" }
