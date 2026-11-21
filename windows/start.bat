@echo off
cd /d "%~dp0\.."
if not exist data mkdir data
set HF_HUB_DISABLE_XET=1
".venv\Scripts\factory.exe" run --port 8080 >> data\factory.log 2>&1
