$WshShell = New-Object -ComObject WScript.Shell
$DesktopPath = [Environment]::GetFolderPath('Desktop')
$ShortcutPath = Join-Path $DesktopPath "Investment_Collector.lnk"
$CurrentDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BatchFile = Join-Path $CurrentDir "start_workflow.bat"
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $BatchFile
$Shortcut.WorkingDirectory = $CurrentDir
$Shortcut.Description = "Investment Information Collector"
$Shortcut.Save()
Write-Host "Desktop shortcut created successfully: $ShortcutPath"
