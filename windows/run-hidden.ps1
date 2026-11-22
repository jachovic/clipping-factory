# Runs the factory in the background and restarts it if it ever stops. Started by the ClippingFactory task.
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:HF_HUB_DISABLE_XET = "1"
New-Item -ItemType Directory -Force -Path "$root\data" | Out-Null
while ($true) {
  Add-Content -Path "$root\data\factory.log" -Value "=== start $(Get-Date -Format s) ===" -Encoding UTF8
  & "$root\.venv\Scripts\factory.exe" run --port 8080 *>> "$root\data\factory.log"
  Add-Content -Path "$root\data\factory.log" -Value "=== stopped (exit $LASTEXITCODE), restart in 30 s ===" -Encoding UTF8
  Start-Sleep -Seconds 30
}
