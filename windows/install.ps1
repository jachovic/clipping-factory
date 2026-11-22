# Clipping Factory - one-time setup on Windows 10/11 (double-click windows\INSTALUJ.bat).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
Write-Host "== Clipping Factory: instalacja w $root ==" -ForegroundColor Cyan

function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
}
function Find-Python {
  # Prefer the py launcher; 'python' may be the Microsoft Store alias stub.
  foreach ($cand in @(@("py","-3.12"), @("py","-3"), @("python"))) {
    try {
      $exe = $cand[0]; $extra = @(); if ($cand.Length -gt 1) { $extra = $cand[1..($cand.Length-1)] }
      $v = & $exe @extra -c "import sys; print(sys.version_info[:2] >= (3,11))" 2>$null
      if ($v -eq "True") { return ,$cand }
    } catch {}
  }
  return $null
}

$py = Find-Python
if (-not $py) {
  Write-Host "Instaluje Pythona 3.12..."
  winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
  Refresh-Path
  $py = Find-Python
  if (-not $py) { throw "Python nie jest dostepny. Zamknij to okno i uruchom INSTALUJ.bat ponownie." }
}
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  Write-Host "Instaluje FFmpeg..."
  winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
  Refresh-Path
}

$exe = $py[0]; $pyArgs = @(); if ($py.Length -gt 1) { $pyArgs = $py[1..($py.Length-1)] }
if (-not (Test-Path .venv)) { & $exe @pyArgs -m venv .venv }
.\.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
.\.venv\Scripts\python.exe -m pip install -e . --quiet

if (-not (Test-Path .env)) {
  Copy-Item .env.example .env
  icacls .env /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null
}
$env:PYTHONUTF8 = "1"
[Environment]::SetEnvironmentVariable("PYTHONUTF8", "1", "User")
.\.venv\Scripts\factory.exe init

Write-Host "Pobieram model rozpoznawania mowy (~140 MB) i sprawdzam system..."
$env:HF_HUB_DISABLE_XET = "1"
.\.venv\Scripts\factory.exe doctor --download-model

# Autostart at logon + restart on failure
$action  = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $root `
             -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$root\windows\run-hidden.ps1`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$set     = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
             -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "ClippingFactory" -Action $action -Trigger $trigger -Settings $set -Force | Out-Null
Start-ScheduledTask -TaskName "ClippingFactory"

# Desktop shortcut to the dashboard
$lnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "Clipping Factory.url"
"[InternetShortcut]`r`nURL=http://127.0.0.1:8080/s/setup" | Set-Content -Path $lnk -Encoding ASCII

# Keep the PC awake while plugged in (factory runs 24/7)
powercfg /change standby-timeout-ac 0 | Out-Null

Write-Host ""
Write-Host "GOTOWE. Fabryka dziala w tle i startuje sama po zalogowaniu." -ForegroundColor Green
Write-Host "Otwieram strone Konfiguracji - wklej tam klucze (Crayo, Telegram)."
Start-Sleep -Seconds 8
Start-Process "http://127.0.0.1:8080/s/setup"
