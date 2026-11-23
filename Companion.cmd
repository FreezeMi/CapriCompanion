@echo off
rem Starts the companion without a console window.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo The companion isn't set up yet. Run Install.cmd first.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m companion
