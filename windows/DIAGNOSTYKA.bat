@echo off
cd /d "%~dp0\.."
set PYTHONUTF8=1
".venv\Scripts\factory.exe" doctor
echo.
echo Wynik zapisany tez w data\diagnostyka.txt
pause
