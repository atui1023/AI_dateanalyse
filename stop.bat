@echo off
rem Stop the AI data analysis server (find process listening on port 8000)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8000 ^| findstr LISTENING') do taskkill /F /PID %%a
echo Server on port 8000 has been stopped.
timeout /t 2 >nul
