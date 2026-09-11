$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$desktop = [Environment]::GetFolderPath("Desktop")
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut((Join-Path $desktop "AI 数据分析.lnk"))
$link.TargetPath = Join-Path $project "start.vbs"
$link.WorkingDirectory = $project
$link.IconLocation = "$env:SystemRoot\System32\shell32.dll,13"
$link.Save()
Write-Host "桌面快捷方式已创建"
