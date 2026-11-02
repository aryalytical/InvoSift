@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\run-app.ps1"
if errorlevel 1 (
  echo.
  echo InvoSift could not start. Read the error above, fix the reported issue, and run RUN-APP.bat again.
  pause
)
