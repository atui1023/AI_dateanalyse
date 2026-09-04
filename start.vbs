' AI Data Analysis launcher: start the server in a hidden window and open the browser.
' Double-click this file (or the desktop shortcut). No terminal window will appear.
Set ws = CreateObject("Wscript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

projDir = fso.GetParentFolderName(WScript.ScriptFullName)
ws.CurrentDirectory = projDir

' Start uvicorn hidden; logs go to server.log. If the server is already running, this process exits silently.
ws.Run "cmd /c .venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 > server.log 2>&1", 0, False

' Wait for the server to be ready, then open the browser.
WScript.Sleep 3000
ws.Run "http://127.0.0.1:8000/"
