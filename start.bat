@echo off
cd /d %~dp0
start "AI Backend" /min "%~dp0start_backend.cmd"
timeout /t 8 /nobreak >nul
start "" http://127.0.0.1:8000/
