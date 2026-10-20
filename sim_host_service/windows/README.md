<!-- README.md ver 6 -->
# POC Sim Host Service — Windows setup

The Windows simulator PC runs SHS in the logged-in simulator-operator session. SHS must not run as a Windows service because it starts and controls interactive simulator applications.

## Addresses and ports

| Device or service | Address/port |
|---|---|
| POC Raspberry Pi | `192.168.0.3/24` |
| Windows simulator PC / SHS | `192.168.0.4/24` |
| Festo controller | `192.168.0.10` |
| SHS control | UDP `10040`, inbound from the POC |
| NoLimits telemetry | TCP `15151`, inbound from the POC |
| SpaceCoaster telemetry | UDP `10009`, outbound to the POC |

The private network has no gateway or DNS server. Leave those fields blank. `poc_config.json` must use `192.168.0.4` as the SHS/simulator address.

## Install Python 3.10 or later

Install 64-bit Python 3.10 or later. SHS does not require Python 2.7. Retain an
existing Python 2.7 installation only until any unrelated legacy tools that may
still depend on it have been identified and retired.

In the Python installer, include the Python Launcher (`py`). SHS invokes `py -3`, which selects an installed Python 3 runtime without requiring an exact minor version. Verify in Command Prompt:

```bat
py -3 --version
```

Install the serial package used by the USB fan controller:

```bat
py -3 -m pip install pyserial
```

## Install and test SHS

Copy the complete `PlatformOperatorsConsole` directory to a permanent local path on the Windows PC. Do not run SHS from a temporary ZIP/extraction directory.

Confirm these simulator launchers contain the correct local paths:

- `sim_host_service\sims\nolimits2.bat`
- `sim_host_service\sims\spacecoaster.bat`

Test SHS before changing Windows startup:

```bat
cd /d C:\path\to\PlatformOperatorsConsole
sim_host_service\windows\start_shs.cmd
```

The larger SHS dashboard reports the Console connection, active simulator,
simulator process state, Windows address, SpaceCoaster relay, current speed/fan
power, fan serial status and the most recent errors. Its prominent status banner
is intended to remain visible while the simulator is idle. For initial fan
setup, select the controller in the COM-port list and click **Open Port**. A
successfully opened port is saved to `sim_host_service.ini` and reopened on
later SHS starts.

The fan is forced to zero when POC stops sending current fan commands. Closing
the SHS window also sends zero before closing the serial port.

The log is `%LOCALAPPDATA%\POC\logs\shs.log`. On startup, a log larger than
10 MiB is moved to `shs.log.1`; only the current log and one previous log are
retained. With POC running, confirm that SHS receives the connection and starts
the configured simulator. Stop the manual test with `Ctrl+C` before installing
autostart.

## Configure Windows Firewall

Open PowerShell as Administrator, change to the copied project directory, and run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\sim_host_service\windows\configure_shs_firewall.ps1
```

This stages restricted rules for the private POC interface without changing the current firewall state. After testing, enable Windows Firewall and install/verify the same rules with:

```powershell
.\sim_host_service\windows\configure_shs_firewall.ps1 -EnableFirewall
```

## Replace the legacy Startup entry

While logged in as the simulator-operator account, run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\sim_host_service\windows\install_shs_startup.ps1
```

The script creates `POC Sim Host Service.lnk` in that account's Startup folder and lists all current Startup entries. Open the folder with `Win+R`, then `shell:startup`.
The shortcut and SHS window use the POC shield icon from
`sim_host_service\windows\poc_shs.ico`. Rerunning this idempotent installer
updates the existing shortcut rather than creating a second startup entry.

After the manual SHS test succeeds, move the old Python 2.7/heartbeat startup shortcut or script to a backup folder. Do not leave both startup systems enabled. Reboot and confirm:

1. Windows signs into the simulator-operator account.
2. `shs.log` records a new SHS start.
3. POC reports the PC connection.
4. Selecting NoLimits or SpaceCoaster starts the correct simulator.

To roll back, move the legacy entry back into `shell:startup` and remove `POC Sim Host Service.lnk`.
