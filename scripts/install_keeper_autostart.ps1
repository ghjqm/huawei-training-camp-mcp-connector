# Register or remove a Windows logon shortcut that starts the session keeper
# minimized. Usage:
#   powershell -NoProfile -File scripts\install_keeper_autostart.ps1
#   powershell -NoProfile -File scripts\install_keeper_autostart.ps1 -Remove
param([switch]$Remove)

$startup = [Environment]::GetFolderPath('Startup')
$link = Join-Path $startup 'HuaweiTrainingCampSessionKeeper.lnk'

if ($Remove) {
    Remove-Item -LiteralPath $link -ErrorAction SilentlyContinue
    Write-Host '已移除开机自启快捷方式。'
    exit 0
}

$launcher = Join-Path $PSScriptRoot 'start_session_keeper.cmd'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($link)
$shortcut.TargetPath = $launcher
$shortcut.Arguments = '--minimized'
$shortcut.WorkingDirectory = Split-Path $PSScriptRoot -Parent
$shortcut.WindowStyle = 7   # minimized console window
$shortcut.Description = 'Huawei Training Camp MCP session keeper'
$shortcut.Save()
Write-Host "已创建开机自启快捷方式：$link"
