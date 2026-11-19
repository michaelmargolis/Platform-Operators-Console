# install_shs_startup.ps1 ver 4
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

$drive = Split-Path -Qualifier $shsRoot
if ([string]::IsNullOrWhiteSpace($drive)) {
    $driveDisplay = '(no drive letter - network/UNC path)'
    $rootDisplay = $shsRoot
}
else {
    $driveDisplay = $drive
    $rootDisplay = $shsRoot.Substring($drive.Length)
    if ([string]::IsNullOrWhiteSpace($rootDisplay)) {
        $rootDisplay = '\'
    }
}

Write-Host ''
Write-Host '============================================================'
Write-Host ' SHS STARTUP INSTALL LOCATION'
Write-Host '============================================================'
Write-Host (" DRIVE : {0}" -f $driveDisplay) -ForegroundColor Yellow
Write-Host (" ROOT  : {0}" -f $rootDisplay) -ForegroundColor Yellow
Write-Host '------------------------------------------------------------'
Write-Host (" FULL  : {0}" -f $shsRoot) -ForegroundColor Cyan
Write-Host (" TARGET: {0}" -f $startScript)
Write-Host (" STARTUP SHORTCUT: {0}" -f $shortcutPath)
Write-Host '============================================================'
Write-Host ''

$confirmation = Read-Host 'Install/update the SHS startup shortcut using this location? [y/N]'
if ($confirmation -notmatch '^(?i:y|yes)$') {
    Write-Host 'Installation cancelled. No startup shortcut was changed.'
    exit 0
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
