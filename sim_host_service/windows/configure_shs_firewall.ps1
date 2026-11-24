# configure_shs_firewall.ps1 ver 2
[CmdletBinding()]
param(
    [string]$ShsAddress = '192.168.0.4',
    [string]$PocAddress = '192.168.0.3',
    [switch]$EnableFirewall
)

$ErrorActionPreference = 'Stop'
$ruleGroup = 'POC Simulator'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from an elevated PowerShell window (Run as administrator).'
}

# Locate the dedicated POC adapter without changing its network category.
# An isolated network without a gateway may be identified as Public at boot.
$addresses = @(Get-NetIPAddress -AddressFamily IPv4 -IPAddress $ShsAddress -ErrorAction SilentlyContinue |
    Where-Object { $_.PrefixOrigin -ne 'WellKnown' })
if ($addresses.Count -eq 0) {
    throw "No network adapter currently has SHS address $ShsAddress. Configure the POC adapter first."
}
if ($addresses.Count -gt 1) {
    throw "SHS address $ShsAddress is configured more than once. Resolve the duplicate address before installing firewall rules."
}
$address = $addresses[0]
$interfaceAlias = $address.InterfaceAlias
$profiles = @('Public', 'Private')

Get-NetFirewallRule -Group $ruleGroup -ErrorAction SilentlyContinue | Remove-NetFirewallRule

New-NetFirewallRule -DisplayName 'POC - SHS control UDP 10040' `
    -Group $ruleGroup -Direction Inbound -Action Allow -Profile $profiles -InterfaceAlias $interfaceAlias `
    -Protocol UDP -LocalAddress $ShsAddress -LocalPort 10040 -RemoteAddress $PocAddress | Out-Null

New-NetFirewallRule -DisplayName 'POC - NoLimits telemetry TCP 15151' `
    -Group $ruleGroup -Direction Inbound -Action Allow -Profile $profiles -InterfaceAlias $interfaceAlias `
    -Protocol TCP -LocalAddress $ShsAddress -LocalPort 15151 -RemoteAddress $PocAddress | Out-Null

New-NetFirewallRule -DisplayName 'POC - SpaceCoaster telemetry UDP 10009' `
    -Group $ruleGroup -Direction Outbound -Action Allow -Profile $profiles -InterfaceAlias $interfaceAlias `
    -Protocol UDP -LocalAddress $ShsAddress -RemoteAddress $PocAddress -RemotePort 10009 | Out-Null

if ($EnableFirewall) {
    Set-NetFirewallProfile -Profile Domain,Private,Public -Enabled True
    Write-Host 'Windows Firewall enabled for all profiles.'
}
else {
    Write-Host 'Rules installed. Firewall state was not changed.'
}

Write-Host "POC adapter: $interfaceAlias ($ShsAddress)"
Write-Host 'POC rules apply to Public and Private network profiles; network settings were not changed.'
Get-NetFirewallRule -Group $ruleGroup |
    Select-Object DisplayName, Direction, Action, Enabled, Profile |
    Format-Table -AutoSize
