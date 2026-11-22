@echo off
echo Uruchamiam Clipping Factory w tle...
powershell -NoProfile -Command "Start-ScheduledTask -TaskName ClippingFactory"
timeout /t 8 >nul
start "" http://127.0.0.1:8080/s/setup
