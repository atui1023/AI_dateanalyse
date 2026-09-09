@echo off
cd /d %~dp0
start "AI Backend" /min "%~dp0start_backend.cmd"
start "AI Frontend" /min "%~dp0start_frontend.cmd"
timeout /t 5 /nobreak >nul
start "" http://localhost:5173/
