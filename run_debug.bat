@echo off
rem Same as run.bat but keeps a console open so you can see errors.
cd /d "%~dp0"
.venv\Scripts\python.exe -m sahay
pause
