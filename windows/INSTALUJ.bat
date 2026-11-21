@echo off
echo Clipping Factory - instalacja (moze potrwac kilka minut)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
echo.
pause
