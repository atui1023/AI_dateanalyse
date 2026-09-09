@echo off
setlocal
cd /d "%~dp0"

rem Replace only the process currently listening on the app port.
for /f "tokens=5" %%P in ('netstat -ano -p tcp ^| findstr /R /C:"127.0.0.1:8000 .*LISTENING"') do (
  taskkill /F /PID %%P >nul 2>&1
)

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
".venv\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8000 > server.log 2>&1
