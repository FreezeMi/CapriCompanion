@echo off
rem One-time setup. Add --small for the faster 2B model.
cd /d "%~dp0"
py -3.13 -I install.py %*
if errorlevel 1 (
  echo.
  echo Setup didn't finish. Run Install.cmd again to resume.
)
pause
