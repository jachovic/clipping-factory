Stop-ScheduledTask -TaskName "ClippingFactory"
Get-Process factory -ErrorAction SilentlyContinue | Stop-Process
Write-Host "Clipping Factory stopped. Start again: Start-ScheduledTask -TaskName ClippingFactory"
