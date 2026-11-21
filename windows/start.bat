@echo off
cd /d "%~dp0\.."
if not exist data mkdir data
".venv\Scripts\factory.exe" run --port 8080 >> data\factory.log 2>&1
