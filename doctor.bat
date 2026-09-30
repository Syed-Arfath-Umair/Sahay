@echo off
rem Readiness checklist: native ARM64, NPU runtime, voice, screen reading, local + cloud AI.
cd /d "%~dp0"
.venv\Scripts\python.exe -m sahay.doctor
pause
