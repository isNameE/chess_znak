@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Install dependencies first. See README.md.
  pause
  exit /b 1
)
.venv\Scripts\python.exe launch.py
if errorlevel 1 pause
