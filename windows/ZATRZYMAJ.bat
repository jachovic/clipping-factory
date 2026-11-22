@echo off
echo Zatrzymuje Clipping Factory...
powershell -NoProfile -Command "Stop-ScheduledTask -TaskName ClippingFactory; Get-Process factory -ErrorAction SilentlyContinue | Stop-Process -Force"
echo Zatrzymane. Ponowne uruchomienie: URUCHOM.bat
pause
