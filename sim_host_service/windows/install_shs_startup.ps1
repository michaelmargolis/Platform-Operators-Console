# install_shs_startup.ps1 ver 3
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$windowsDir = Split-Path -Parent $PSCommandPath
$shsRoot = (Resolve-Path (Join-Path $windowsDir '..\..')).Path
$startScript = Join-Path $windowsDir 'start_shs.cmd'
$iconPath = Join-Path $windowsDir 'poc_shs.ico'
$startupDir = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startupDir 'POC Sim Host Service.lnk'

if (-not (Test-Path $startScript -PathType Leaf)) {
    throw "SHS start script not found: $startScript"
}

try {
    $pythonVersion = (& py '-3' -c 'import platform; print(platform.python_version())' 2>&1).Trim()
    if ($LASTEXITCODE -ne 0 -or [version]$pythonVersion -lt [version]'3.10') {
        throw "Python 3.10 or later was not selected. Result: $pythonVersion"
    }
}
catch {
    throw 'Python 3.10 or later and the Python Launcher are required. Install a supported 64-bit Python 3 release, including the launcher, then rerun this script.'
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $startScript
$shortcut.WorkingDirectory = $shsRoot
$shortcut.Description = 'Start the Platform Operators Console Simulator Host Service'
$shortcut.IconLocation = "$iconPath,0"
$shortcut.WindowStyle = 7
$shortcut.Save()

Write-Host "Python $pythonVersion found."
Write-Host "Created startup shortcut: $shortcutPath"
Write-Host ''
Write-Host 'Current Startup-folder entries:'
Get-ChildItem -Path $startupDir | Select-Object Name, FullName | Format-Table -AutoSize
Write-Host 'After manually testing SHS, move the legacy Python/heartbeat startup shortcut out of this folder to prevent both systems starting.'
