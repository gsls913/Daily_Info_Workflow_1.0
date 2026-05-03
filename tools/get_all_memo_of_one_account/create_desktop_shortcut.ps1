# Create desktop shortcut for Company Memos downloader
$WshShell = New-Object -ComObject WScript.Shell
$Desktop = [Environment]::GetFolderPath("Desktop")

$CurrentDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BatchFile = Join-Path $CurrentDir "run_get_company_memos.bat"

$Shortcut = $WshShell.CreateShortcut("$Desktop\Get Company Memos.lnk")
$Shortcut.TargetPath = $BatchFile
$Shortcut.WorkingDirectory = $CurrentDir
$Shortcut.IconLocation = "$env:SystemRoot\system32\shell32.dll,14"
$Shortcut.Description = "Get Company Meeting Memos"
$Shortcut.Save()

Write-Host "Desktop shortcut created successfully!"
Write-Host "Shortcut name: Get Company Memos"
