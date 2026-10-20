#README.md  ver 16
# POC Raspberry Pi deployment

The concise technician procedure is maintained in
`docs/raspberry_pi/POC_Raspberry_Pi_Technician_Guide.md`.

## Supported starting point

- Raspberry Pi 3B or later.
- Current Raspberry Pi OS 64-bit with Desktop, installed as a fresh image.
- Initial display: 10-inch HDMI panel.
- Deployment displays: HDMI, Raspberry Pi Touch Display 2, or the project
  Waveshare 10.1-inch DSI panel.

The normal `auto` mode leaves detection to Raspberry Pi firmware and is suitable
for HDMI and displays that have proved reliable with firmware detection. Use
the explicit `touch2-7` mode for the project's 7-inch Raspberry Pi Touch
Display 2. Repeated testing found intermittent loss of DSI/backlight detection
after warm reboot with autodetection; the explicit ILI9881 overlay then passed
30 consecutive warm reboots. The Waveshare overlay is also enabled only when
explicitly selected.

## Install

For the initial full installation, copy or extract the complete tree directly
to the live runtime path:

```text
/home/poc/Runtime
```

After transfer, `/home/poc/Runtime/poc.py` must exist directly; do not leave an
extra `PlatformOperatorsConsole` directory level inside Runtime.

Open a terminal and run:

```bash
cd /home/poc/Runtime
chmod u+x raspi/*.sh
sudo ./raspi/setup-console.sh --display auto
```

For the 7-inch Raspberry Pi Touch Display 2 used by this project:

```bash
sudo ./raspi/setup-console.sh --display touch2-7
```

When installing the confirmed project Waveshare 10.1-inch DSI panel instead:

```bash
sudo ./raspi/setup-console.sh --display waveshare-10.1
```

The installer uses the desktop user that invoked `sudo`. If it is run directly
from a root shell, specify the existing desktop user with `--user NAME`.

The optional `--full-upgrade` switch applies all current Raspberry Pi OS
updates. It is intentionally not automatic, so installation cannot unexpectedly
replace the kernel or firmware immediately before a controlled test.

`/home/poc/Runtime` is the authoritative live application directory from the
initial upload onward. The installer can still be run from another complete
source tree and will copy it into Runtime for compatibility, but a separate
staging directory is not part of the normal installation or update procedure.

Reboot after installation. POC starts full screen after desktop login. When the
platform is fully deactivated, press Escape to close POC and return to the
desktop for maintenance. Exit is refused while the platform is active or
transitioning. POC is started by
the desktop autostart entry; it is not installed as a system service. The
installer also creates an executable **Platform Operators Console** launcher in
the desktop user's configured Desktop directory. In
`touch2-7` mode,
the same installer also creates the persistent labwc/`kanshi` landscape
profile, installs the prepared wallpaper and Plymouth theme, fixes Plymouth
`DeviceScale=1`, and rebuilds the initramfs. Setup also configures the Ethernet
profiles. If `eth0` has a different MAC from the packaged configuration, the
technician is prompted to accept the detected MAC; if Wi-Fi is enabled, the
technician is prompted to disable it. In a local session the changes activate
immediately. In an SSH session they are prepared without interrupting the live
connection and activate on reboot; a requested Wi-Fi shutdown is likewise
performed at the next boot. SSH detection checks both the session environment
and process ancestry, so it remains effective when `sudo` removes SSH variables.
The installer never reboots automatically. Rerunning setup preserves the deployed
`poc_config.json` and `raspi/network-config.conf`, so site-specific simulator
settings and an accepted Pi MAC are not reset from the original upload.

## Updating with a delta ZIP

Delta ZIP contents are relative to the runtime root so the directory structure
is easy to inspect and compare after extracting on Windows. Upload each changed
file with WinSCP to its matching path beneath `/home/poc/Runtime`. Transfer
shell scripts in binary mode so WinSCP does not change their LF line endings.

After uploading, run:

```bash
cd /home/poc/Runtime
chmod u+x raspi/*.sh
python3 version_check.py
```

The installer is not required for normal delta updates.

If a release explicitly requires `setup-console.sh` to be rerun, apply that
delta to `/home/poc/Runtime` and run the installer from there:

```bash
cd /home/poc/Runtime
sudo ./raspi/setup-console.sh --display touch2-7
```

Do not maintain a parallel staging copy as another application tree.

When changing an existing installation from `/opt/poc` for the first time,
upload the complete current tree to `/home/poc/Runtime`, then run:

```bash
cd /home/poc/Runtime
chmod u+x raspi/*.sh
./raspi/switch-to-runtime.sh
python3 version_check.py
```

The switch changes only the desktop autostart entry. It does not delete or
modify `/opt/poc`, which can remain temporarily as a fallback.

Confirm the live launcher after switching:

```bash
grep '^Exec=' ~/.config/autostart/poc.desktop
```

It must point to `/home/poc/Runtime/raspi/start_console.sh`.

## Platform and development networks

POC uses two simultaneous NetworkManager Ethernet profiles:

- the built-in Ethernet port is `192.168.0.3/24` on the private platform
  network, with no gateway or DNS;
- the configured development interface (default `eth1`) uses DHCP and supplies
  the default route whenever it is connected.

The static profile is bound to both the configured `eth0` interface and the
built-in adapter MAC address in `raspi/network-config.conf`. On a different Pi,
the script reports the detected `eth0` MAC and asks whether to store it as the
built-in platform adapter. Declining leaves the network unchanged. Subsequent
startup checks continue to enforce the stored MAC.

The DHCP profile is bound to the `DEVELOPMENT_INTERFACE` in
`raspi/network-config.conf` (default `eth1`) but deliberately has no MAC
restriction. A replacement USB Ethernet adapter can therefore use it whenever
Linux presents it under that configured interface name. The private and
development networks must use different IPv4 subnets.

`setup-console.sh` invokes network configuration automatically. A local setup
activates the profiles immediately. When setup detects SSH, it uses deferred
activation: the profile definitions are written without disturbing the current
connection and take effect on reboot. The standalone maintenance command is:

```bash
cd /home/poc/Runtime
chmod u+x raspi/configure-network.sh
sudo ./raspi/configure-network.sh
```

The script disables autoconnect on the superseded NetworkManager profiles but
does not delete them. If `eth1` is absent, the static platform profile is still
activated and the unrestricted DHCP profile is retained for the next USB
Ethernet adapter. If Wi-Fi is enabled, the script asks whether to disable it;
pressing Enter accepts the recommended appliance setting. Choosing no leaves
Wi-Fi unchanged. In deferred mode, accepting the prompt installs a root-owned
one-shot service that switches the Wi-Fi radio off at the next boot. Wi-Fi
remains active until that reboot. If `eth1` is absent, the script prints a
warning to connect the USB Ethernet adapter before rebooting and disabling
Wi-Fi.

To prepare changes manually without interrupting an SSH connection:

```bash
sudo ./raspi/configure-network.sh --defer-activation
```

At desktop startup, `network_check.py` verifies that the configured platform
interface has the configured built-in MAC address. On a mismatch, POC does not
start and a graphical **Network adapters need configuring** message is shown.
The absence of `eth1` is not an error.

Check the result with:

```bash
nmcli -f DEVICE,TYPE,STATE,CONNECTION device
ip -br address
ip route
```

The default SHS address remains configurable in `poc_config.json`; the supplied
deployment default is `192.168.0.4`. The Festo controller remains
`192.168.0.10`.

## Original 800 x 480 Touch Display

POC automatically loads the compact layout when the physical
screen geometry is 800 x 480 or smaller. The normal layout remains in use at
1280 x 720 and 1280 x 800. The compact interface provides Ride Control, Input
Control, Platform Output, Platform Selection and Debug. Transform Viewer is
not available on the compact screen because its plots are not useful at that
resolution.

For development, selection can be forced independently of screen size:

```bash
POC_COMPACT_UI=1 python3 poc.py --fullscreen
```

Use `POC_COMPACT_UI=0` to force the standard layout.

The original 800 x 480 official Touch Display should use `--display auto`. If it shows
the firmware rainbow screen and then goes dark, check detection before forcing
an overlay:

```bash
kmsprint -m
grep -nE '^\[|display_auto_detect|dtoverlay|ignore_lcd|disable_touchscreen' /boot/firmware/config.txt
for connector in /sys/class/drm/card*-DSI-*; do
    echo "$connector: $(cat "$connector/status" 2>/dev/null)"
done
sudo dmesg | grep -iE 'dsi|touch|panel|drm|vc4'
```

No Waveshare overlay should be enabled when using an official Touch Display.

## Logs and manual launch

The runtime log is:

```text
~/.local/state/poc/poc.log
```

At startup, a log larger than 10 MiB is moved to `poc.log.1`; only the current
log and one previous log are retained.

To start the appliance manually:

```bash
~/Runtime/raspi/start_console.sh
```

For a normal development window:

```bash
cd ~/Runtime
python3 poc.py
```

## Display notes

The Raspberry Pi Touch Display 2 has a portrait-native panel. The
`touch2-7` installer writes a persistent `kanshi` profile that applies
`transform 90` to `DSI-1`; labwc then presents a 1280 x 720 desktop and maps
touch input to the rotated display. Do not add a `video=...rotate=...` argument
to `/boot/firmware/cmdline.txt`: testing on the current Pi OS kernel showed
that it did not rotate the KMS/Plymouth canvas and is unnecessary for the
desktop. The POC UI requires at least 1100 x 700 pixels, so the 1280 x 720
landscape desktop meets its minimum.

Configure the project's 7-inch Touch Display 2 explicitly:

```bash
sudo ./raspi/setup-console.sh --display touch2-7
```

This removes active `display_auto_detect` entries managed or inherited by the
installer and writes `dtoverlay=vc4-kms-dsi-ili9881-7inch` in the single POC
boot-configuration block. A full power removal may be required once if the
display controller is already stuck following an unsuccessful warm reboot.

The full `touch2-7` installation installs the prepared desktop wallpaper and
Plymouth theme automatically. To reinstall only those assets after replacing
the PNG files, run:

```bash
cd ~/Runtime
chmod u+x raspi/install-display-assets.sh
sudo ./raspi/install-display-assets.sh --user poc
sudo reboot
```

Required assets are the normal 1280 x 720 wallpaper and the 720 x 1280
`poc_splash_touch2_boot.png`, which contains the complete landscape artwork
rotated 90 degrees counter-clockwise. Plymouth continues to see the native
720 x 1280 boot canvas, so the theme draws that image at `(0,0)` without
scaling. `/etc/plymouth/plymouthd.conf` sets `DeviceScale=1`; this prevents the
Touch Display 2 DPI from enlarging and cropping the image. This combination is
confirmed to produce an upright, correctly scaled splash.

The Touch Display 2 is portrait-native. On the current labwc desktop, check its
active geometry and transform with:

```bash
XDG_RUNTIME_DIR=/run/user/$(id -u) WAYLAND_DISPLAY=wayland-0 wlr-randr
```

The expected output includes `Transform: 90`. The persistent setting is in:

```text
/home/poc/.config/kanshi/config
```

Because POC is a dedicated appliance, `touch2-7` mode makes this the single
authoritative `kanshi` profile. On first installation it preserves any existing
file as `config.before-poc`; this also cleanly replaces a profile created during
manual display commissioning.

If the on-screen keyboard remains enabled after using Control Centre, disable
it with `sudo raspi-config` under **Display Options > Onscreen Keyboard > Always
Off**, then reboot.

Do not select the Waveshare mode for a different Waveshare model or revision
without first confirming its required device-tree overlay.

## Maintenance desktop launcher

The full installer creates or refreshes the desktop shortcut automatically. It
uses absolute runtime paths:

```ini
Exec=/home/poc/Runtime/raspi/start_console.sh
Path=/home/poc/Runtime
Icon=/home/poc/Runtime/images/POC.png
```

The launcher is executable and can be used to restart POC after exiting while
the platform is fully deactivated. Its normal location for the `poc` user is
`/home/poc/Desktop/POC.desktop`; the installer honours a different Desktop
directory configured through `xdg-user-dir`.

To create or repair only the launcher without rerunning the full installer:

```bash
cd /home/poc/Runtime
chmod u+x raspi/install-desktop-launcher.sh raspi/start_console.sh
sudo ./raspi/install-desktop-launcher.sh --user poc
```

## Safe GPIO bring-up

Keep motion output disabled or otherwise made safe for initial testing. Run:

```bash
cd ~/Runtime
python3 version_check.py
python3 -m pytest -q tests/test_physical_controls.py
```

Then verify each BCM input and its idle/active level using the pin map in the
physical-controls handoff. Confirm LOAD startup state and encoder direction
before enabling platform output.
