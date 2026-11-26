# configure_shs_network.ps1 ver 1
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$shsAddress = '192.168.0.4'
$prefixLength = 24
$subnetMask = '255.255.255.0'

function Show-Profiles($profiles) {
    $profiles | Select-Object InterfaceAlias, InterfaceIndex, Name, NetworkCategory, IPv4Connectivity |
        Format-Table -AutoSize | Out-Host
}

function Get-IPv4State([uint32]$index) {
    $ipInterface = Get-NetIPInterface -InterfaceIndex $index -AddressFamily IPv4
    [pscustomobject]@{
        Dhcp = $ipInterface.Dhcp
        Addresses = @(Get-NetIPAddress -AddressFamily IPv4 |
            Where-Object { $_.InterfaceIndex -eq $index })
        DefaultRoutes = @(Get-NetRoute -AddressFamily IPv4 |
            Where-Object { $_.InterfaceIndex -eq $index -and $_.DestinationPrefix -eq '0.0.0.0/0' })
        Dns = @(Get-DnsClientServerAddress -InterfaceIndex $index -AddressFamily IPv4 |
            ForEach-Object { $_.ServerAddresses } | Where-Object { $_ })
    }
}

function Test-StaticIPv4($state) {
    return ($state.Dhcp -eq 'Disabled' -and
        $state.Addresses.Count -eq 1 -and
        $state.Addresses[0].IPAddress -eq $shsAddress -and
        $state.Addresses[0].PrefixLength -eq $prefixLength -and
        $state.DefaultRoutes.Count -eq 0)
}

function Test-IPv4Ready($state) {
    return ((Test-StaticIPv4 $state) -and
        $state.Addresses[0].AddressState -eq 'Preferred' -and
        $state.Dns.Count -eq 0)
}

function Show-IPv4State($state) {
    $addresses = @($state.Addresses | ForEach-Object {
        '{0}/{1} ({2})' -f $_.IPAddress, $_.PrefixLength, $_.AddressState
    }) -join ', '
    $gateways = @($state.DefaultRoutes | ForEach-Object { $_.NextHop }) -join ', '
    $dns = $state.Dns -join ', '
    Write-Host (" DHCP    : {0}" -f $state.Dhcp)
    Write-Host (" IPv4    : {0}" -f $addresses)
    Write-Host (" Gateway : {0}" -f $gateways)
    Write-Host (" DNS     : {0}" -f $dns)
}

function Get-OnlyProfile {
    $profiles = @(Get-NetConnectionProfile)
    if ($profiles.Count -eq 0) {
        throw 'No active network connection profile was found. Connect the POC platform Ethernet network and retry.'
    }
    if ($profiles.Count -gt 1) {
        Show-Profiles $profiles
        throw 'More than one active network connection profile was found. Disconnect all external Ethernet and Wi-Fi connections (and any active VPN), leaving only the POC platform Ethernet connection, then retry.'
    }
    if ($profiles[0].NetworkCategory -notin @('Public', 'Private')) {
        Show-Profiles $profiles
        throw 'Only Public or Private network profiles are supported. A domain-authenticated network must not be changed.'
    }
    return $profiles[0]
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from an elevated PowerShell window (Run as administrator).'
}

Write-Host ''
Write-Host 'POC SIMULATOR PC NETWORK SETUP'
Write-Host 'Run locally, not over a remote network session.'
Write-Host 'Leave only the dedicated POC platform Ethernet connection active.'
Write-Host ''

$profile = Get-OnlyProfile
$index = $profile.InterfaceIndex
$adapter = Get-NetAdapter -InterfaceIndex $index
if ($adapter.Status -ne 'Up') {
    throw 'The selected network adapter is not connected. Check the POC Ethernet connection and retry.'
}
$otherAddress = @(Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -eq $shsAddress -and $_.InterfaceIndex -ne $index })
if ($otherAddress.Count -gt 0) {
    throw "IPv4 address $shsAddress is already configured on another interface. Resolve the duplicate local address before retrying."
}

Show-Profiles @($profile)
Write-Host (" Adapter : {0}" -f $adapter.InterfaceDescription)
Write-Host ''
Write-Host 'Current IPv4 configuration:'
$state = Get-IPv4State $index
Show-IPv4State $state

Write-Host ''
Write-Host 'Required POC configuration:'
Write-Host ' Profile : Private'
Write-Host ' DHCP    : Disabled'
Write-Host (" IPv4    : {0}/{1}" -f $shsAddress, $prefixLength)
Write-Host (" Mask    : {0}" -f $subnetMask)
Write-Host ' Gateway : none'
Write-Host ' DNS     : none (IPv4)'
Write-Host ''

if ($profile.NetworkCategory -eq 'Private' -and (Test-IPv4Ready $state)) {
    Write-Host 'The selected adapter already has the required POC configuration. No changes needed.'
    return
}

$confirmation = Read-Host 'Is this the dedicated POC Ethernet adapter, and may its IPv4 settings and network category be changed as shown? [y/N]'
if ($confirmation -notmatch '^(?i:y|yes)$') {
    Write-Host 'Configuration cancelled. No network settings were changed.'
    return
}

# Check again in case an external connection was established during the prompt.
$profile = Get-OnlyProfile
if ($profile.InterfaceIndex -ne $index) {
    throw 'The selected network connection changed during confirmation. No settings were changed. Retry.'
}
$state = Get-IPv4State $index

try {
    # netsh replaces existing IPv4 addresses/default gateways and persists the
    # new static configuration. Target only the confirmed interface index.
    $addressChanged = -not (Test-StaticIPv4 $state)
    if ($addressChanged) {
        & netsh.exe interface ipv4 set address "name=$index" source=static "address=$shsAddress" "mask=$subnetMask" gateway=none store=persistent
        if ($LASTEXITCODE -ne 0) {
            throw "Setting static IPv4 failed (netsh exit code $LASTEXITCODE)."
        }
    }
    if ($addressChanged -or $state.Dns.Count -gt 0) {
        & netsh.exe interface ipv4 set dnsservers "name=$index" source=static address=none validate=no
        if ($LASTEXITCODE -ne 0) {
            throw "Clearing IPv4 DNS failed (netsh exit code $LASTEXITCODE)."
        }
    }

    # Windows may temporarily drop/re-identify the profile when IPv4 changes.
    # Wait for the same sole profile and a usable address before setting Private.
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        $profiles = @(Get-NetConnectionProfile)
        if ($profiles.Count -gt 1) {
            Show-Profiles $profiles
            throw 'An additional network connection appeared during configuration. Disconnect it and retry.'
        }
        if ($profiles.Count -eq 1) {
            $profile = $profiles[0]
            if ($profile.InterfaceIndex -ne $index -or $profile.NetworkCategory -notin @('Public', 'Private')) {
                throw 'The network connection changed unexpectedly during configuration.'
            }
            $state = Get-IPv4State $index
            if (Test-IPv4Ready $state) {
                $ready = $true
                break
            }
        }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) {
        throw 'The required IPv4 configuration and a usable connection profile could not be verified. Check for an IP address conflict or disconnected Ethernet cable.'
    }

    if ($profile.NetworkCategory -eq 'Public') {
        Set-NetConnectionProfile -InterfaceIndex $index -NetworkCategory Private
    }

    $profile = Get-OnlyProfile
    $state = Get-IPv4State $index
    if ($profile.InterfaceIndex -ne $index -or $profile.NetworkCategory -ne 'Private' -or
        -not (Test-IPv4Ready $state)) {
        throw 'Final network configuration verification failed.'
    }
}
catch {
    throw "POC network setup did not complete: $($_.Exception.Message) Some network settings may already have changed; inspect the adapter and rerun this script locally."
}

Write-Host ''
Write-Host 'POC network configuration verified.'
Show-Profiles @($profile)
Show-IPv4State $state
Write-Host 'Firewall, Network Discovery, File Sharing and IPv6 settings were not changed.'
