# install_shs_startup.ps1 ver 5
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$windowsDir = Split-Path -Parent $PSCommandPath
$shsRoot = (Resolve-Path (Join-Path $windowsDir '..\..')).Path
$startScript = Join-Path $windowsDir 'start_shs.cmd'
$taskName = 'POC Sim Host Service'
$startupDir = [Environment]::GetFolderPath('Startup')
$legacyShortcutPath = Join-Path $startupDir 'POC Sim Host Service.lnk'

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
Write-Host (" TASK  : {0} (run at logon, highest privileges)" -f $taskName)
Write-Host '============================================================'
Write-Host ''

$confirmation = Read-Host 'Install/update the elevated SHS logon task using this location? [y/N]'
if ($confirmation -notmatch '^(?i:y|yes)$') {
    Write-Host 'Installation cancelled. No startup configuration was changed.'
    exit 0
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$windowsPrincipal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $windowsPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'This installer must be run from PowerShell opened as Administrator. SHS needs an elevated logon task so it can restart Oculus OVRService before SpaceCoaster starts.'
}
$currentUser = $identity.Name

try {
    $pythonVersion = (& py '-3' -c 'import platform; print(platform.python_version())' 2>&1).Trim()
    if ($LASTEXITCODE -ne 0 -or [version]$pythonVersion -lt [version]'3.10') {
        throw "Python 3.10 or later was not selected. Result: $pythonVersion"
    }
}
catch {
    throw 'Python 3.10 or later and the Python Launcher are required. Install a supported 64-bit Python 3 release, including the launcher, then rerun this script.'
}

$cmdArguments = '/d /c ""{0}""' -f $startScript
$action = New-ScheduledTaskAction `
    -Execute $env:ComSpec `
    -Argument $cmdArguments `
    -WorkingDirectory $shsRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
$principal = New-ScheduledTaskPrincipal `
    -UserId $currentUser `
    -LogonType Interactive `
    -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description 'Start the Platform Operators Console Simulator Host Service in the interactive simulator-operator session.' `
    -Force | Out-Null

if (Test-Path $legacyShortcutPath -PathType Leaf) {
    Remove-Item -Force $legacyShortcutPath
    Write-Host "Removed superseded Startup shortcut: $legacyShortcutPath"
}

Write-Host "Python $pythonVersion found."
Write-Host "Installed/updated elevated logon task '$taskName' for $currentUser."
Write-Host "Task action: $env:ComSpec $cmdArguments"
Write-Host ''
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State | Format-Table -AutoSize
Write-Host 'The task will start SHS at the next logon. For an immediate test, stop any manually started SHS first, then run:'
Write-Host ("  Start-ScheduledTask -TaskName '{0}'" -f $taskName)
Write-Host 'Do not leave the old Python/heartbeat startup mechanism enabled.'
