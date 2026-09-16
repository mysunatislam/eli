# Make Eli start when you sign in to Windows.
param([switch]$Enable, [switch]$Disable, [switch]$Status)

$root = Split-Path -Parent $PSScriptRoot
$vbs = Join-Path $root "scripts\silent_start.vbs"
$regKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$regName = "app.eli.companion"
$installedExe = "E:\Eli\Eli.exe"

$startup = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup"
if (-not (Test-Path $startup)) { New-Item -ItemType Directory -Force -Path $startup | Out-Null }
$lnk = Join-Path $startup "Eli.lnk"

if ($Enable) {
  # Clean up any conflicting Run registry entry to avoid dual startup
  Remove-ItemProperty -Path $regKey -Name $regName -ErrorAction SilentlyContinue

  $shell = New-Object -ComObject WScript.Shell
  $s = $shell.CreateShortcut($lnk)
  if (Test-Path $installedExe) {
    $s.TargetPath = $installedExe
    $s.Arguments = ""
    $s.WorkingDirectory = "E:\Eli"
    $s.WindowStyle = 1
    $s.IconLocation = "$installedExe,0"
  } else {
    $s.TargetPath = "$env:WINDIR\System32\wscript.exe"
    $s.Arguments = "`"$vbs`""
    $s.WorkingDirectory = $root
    $s.WindowStyle = 7
    $devIcon = Join-Path $root "desktop\node_modules\electron\dist\electron.exe"
    if (Test-Path $devIcon) { $s.IconLocation = "$devIcon,0" }
  }
  $s.Description = "Eli AI companion"
  $s.Save()

  Write-Output "enabled: $lnk -> $($s.TargetPath)"
  exit 0
}

if ($Disable) {
  Remove-ItemProperty -Path $regKey -Name $regName -ErrorAction SilentlyContinue
  if (Test-Path $lnk) { Remove-Item $lnk -Force }
  Write-Output "disabled"
  exit 0
}

$regVal = (Get-ItemProperty -Path $regKey -Name $regName -ErrorAction SilentlyContinue).$regName
if ($regVal -or (Test-Path $lnk)) {
  Write-Output "enabled: $(if (Test-Path $lnk) { $lnk } else { $regVal })"
} else {
  Write-Output "disabled"
}
