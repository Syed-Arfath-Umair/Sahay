@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
  echo Run setup.ps1 first:  powershell -ExecutionPolicy Bypass -File setup.ps1
  pause
  exit /b 1
)
rem pythonw = no console window; Sahay lives in the system tray.
start "" .venv\Scripts\pythonw.exe -m sahay
