@echo off
setlocal
cd /d "%~dp0"

rem Replace the Vite process currently listening on the app frontend port.
for /f "tokens=5" %%P in ('netstat -ano -p tcp ^| findstr /R /C:"127.0.0.1:5173 .*LISTENING"') do (
  taskkill /F /PID %%P >nul 2>&1
)

cd frontend
npm run dev -- --host 127.0.0.1 --strictPort > ..\frontend.log 2>&1
