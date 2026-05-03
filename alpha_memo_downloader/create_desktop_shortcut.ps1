# Create desktop shortcut for AlphaPai downloader
$WshShell = New-Object -ComObject WScript.Shell
$Desktop = [Environment]::GetFolderPath("Desktop")

# Get current directory (alpha_memo_downloader folder)
$CurrentDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BatchFile = Join-Path $CurrentDir "run_alphapai.bat"

# Create shortcut
$Shortcut = $WshShell.CreateShortcut("$Desktop\AlphaPai Meeting Downloader.lnk")
$Shortcut.TargetPath = $BatchFile
$Shortcut.WorkingDirectory = $CurrentDir
$Shortcut.IconLocation = "$env:SystemRoot\system32\shell32.dll,14"
$Shortcut.Description = "AlphaPai Meeting Minutes Downloader"
$Shortcut.Save()

Write-Host "Desktop shortcut created successfully!" -ForegroundColor Green
Write-Host "Shortcut name: AlphaPai Meeting Downloader" -ForegroundColor Yellow
Write-Host "Location: Desktop" -ForegroundColor Yellow
