# Clipping Factory - one-time setup on Windows 10/11.
# Run in PowerShell from the project folder:   powershell -ExecutionPolicy Bypass -File windows\install.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Need($cmd, $wingetId) {
  if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
    Write-Host "Installing $wingetId ..."
    winget install --id $wingetId -e --accept-source-agreements --accept-package-agreements
  }
}
Need python "Python.Python.3.12"
Need ffmpeg "Gyan.FFmpeg"
# refresh PATH for this session after winget installs
$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")

if (-not (Test-Path .venv)) { python -m venv .venv }
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e .

if (-not (Test-Path .env)) {
  Copy-Item .env.example .env
  # private to the current user only
  icacls .env /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null
  Write-Host "Created .env - add your keys later (Telegram, Crayo)."
}
.\.venv\Scripts\factory init

# Autostart at logon, restart on failure
$action  = New-ScheduledTaskAction -Execute "$root\windows\start.bat" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$set     = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
             -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "ClippingFactory" -Action $action -Trigger $trigger -Settings $set -Force | Out-Null
Start-ScheduledTask -TaskName "ClippingFactory"

Write-Host ""
Write-Host "Running. Dashboard: http://127.0.0.1:8080   Logs: data\factory.log"
Write-Host "Keep the PC awake: Settings > System > Power > Sleep = Never (when plugged in)."
