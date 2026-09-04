@echo off
rem Fallback launcher (shows a console window). For a windowless start, use start.vbs or the desktop shortcut.
cd /d %~dp0
echo Starting AI Data Analysis server...
echo URL: http://127.0.0.1:8000/
echo Close this window to stop the server.
echo.
start "" http://127.0.0.1:8000/
.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
pause
