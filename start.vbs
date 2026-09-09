' AI Data Analysis launcher
Set ws = CreateObject("Wscript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
projDir = fso.GetParentFolderName(WScript.ScriptFullName)
ws.CurrentDirectory = projDir
ws.Run "cmd /c """ & projDir & "\start_backend.cmd""", 0, False
ws.Run "cmd /c """ & projDir & "\start_frontend.cmd""", 0, False
WScript.Sleep 5000
ws.Run "http://localhost:5173/?v=" & DateDiff("s", "1970-01-01 00:00:00", Now)
