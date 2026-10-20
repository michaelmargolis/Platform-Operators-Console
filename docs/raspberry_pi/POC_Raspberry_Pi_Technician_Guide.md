#POC_Raspberry_Pi_Technician_Guide.md  ver 10

# POC Raspberry Pi Technician Guide

## 1. Raspberry Pi environment

POC is supported on Raspberry Pi 3B or later using the current 64-bit Raspberry
Pi OS with Desktop. The standard desktop user is `poc` and the live application
is installed at:

```text
/home/poc/Runtime
```

After transfer, `/home/poc/Runtime/poc.py` must exist directly; do not leave an
extra `PlatformOperatorsConsole` directory level inside Runtime.

POC starts full screen after desktop login. When the platform is fully
deactivated, press **Escape** to close it and return to the desktop. Exit is
refused while the platform is active or transitioning. The startup entry calls
`raspi/start_console.sh`; the runtime log is:

```text
/home/poc/.local/state/poc/poc.log
```

POC is a desktop-autostart application, not a system service. Application code,
configuration and normal updates remain under `/home/poc/Runtime`; `/opt/poc`
is not the live runtime.

HDMI normally uses automatic display detection. The project's 7-inch Raspberry
Pi Touch Display 2 uses the explicit `touch2-7` installer mode; firmware
autodetection intermittently lost the DSI connector after warm reboot on the
test Pi 3B+. The explicit overlay passed 30 consecutive warm reboots. The
Waveshare 10.1-inch DSI panel has its own explicit installer option. At
800 x 480 or below, the compact interface provides Ride Control, Input Control,
Platform Output, Platform Selection and Debug; Transform Viewer is not
available at compact resolution. The standard interface is used at 1280 x 720
and 1280 x 800.

Relevant configuration files are:

- `poc_config.json`: selected simulator, SHS address and application options.
- `raspi/network-config.conf`: Pi Ethernet roles, built-in MAC and static IP.
- `file_versions.txt`: expected versions of all tracked runtime files.

GPIO controls are serviced by POC on the Pi. Keep the motion platform disabled
or otherwise made safe during installation and control testing.

## 2. Software installation and updating

### Fresh installation

Start with a fresh, current Raspberry Pi OS 64-bit Desktop image. Extract the
complete package on the maintenance PC and upload its complete contents to:

```text
/home/poc/Runtime
```

For the initial installation, run:

```bash
cd /home/poc/Runtime
chmod u+x raspi/*.sh
sudo ./raspi/setup-console.sh --display auto
```

Use this for the project's 7-inch Raspberry Pi Touch Display 2:

```bash
sudo ./raspi/setup-console.sh --display touch2-7
```

Use this instead only for the confirmed Waveshare 10.1-inch DSI panel:

```bash
sudo ./raspi/setup-console.sh --display waveshare-10.1
```

The installer adds the required packages and user groups, configures the live
tree in `/home/poc/Runtime`, enables desktop autologin, creates POC autostart and adds
an executable **Platform Operators Console** icon to the user's configured
Desktop directory. In
`touch2-7` mode it also installs the persistent landscape transform, wallpaper
and Plymouth splash. It configures both Ethernet roles and prompts only when a
different `eth0` MAC must be accepted or enabled Wi-Fi can be disabled. Local
setup activates the new profiles immediately; setup over SSH defers activation
and any requested Wi-Fi shutdown until reboot, so no locally attached keyboard
is required. SSH detection remains effective through `sudo` because it also
checks the process ancestry. The installer does not reboot automatically. The
installer can safely be rerun: it preserves the deployed
`poc_config.json` and `raspi/network-config.conf` and updates the same managed
profiles and configuration blocks. Reboot when installation completes.

`/home/poc/Runtime` is the authoritative live tree from the initial upload
onward. Although the installer retains compatibility with being run from a
different complete source tree, a separate staging directory is not required.
Apply subsequent delta ZIPs beneath Runtime. If a system-facing update
explicitly requires the installer, run
`/home/poc/Runtime/raspi/setup-console.sh` from the Runtime root.

### Applying a delta update

Delta ZIP paths are relative to `/home/poc/Runtime`. The normal Windows/WinSCP
procedure is:

1. Extract the ZIP on Windows and review differences if required.
2. Upload every supplied file to the matching path below
   `/home/poc/Runtime`, preserving the directory structure.
3. Preserve local values in `poc_config.json` when an update does not explicitly
   replace them.
4. On the Pi, restore permissions and validate the complete runtime:

```bash
cd /home/poc/Runtime
chmod u+x raspi/*.sh
find raspi/assets -type d -exec chmod 755 {} \; 2>/dev/null || true
find raspi/assets -type f -exec chmod 644 {} \; 2>/dev/null || true
python3 version_check.py
```

`VERSION CHECK OK` must be reported before testing. Do not rerun the installer
for a normal delta update. Exit and restart POC after Python-only changes.
Reboot only when the release changes display, networking, OS/session settings,
or another system-facing component that explicitly requires it.

Only releases that explicitly change Pi system configuration require setup to
be rerun. In that case:

```bash
cd /home/poc/Runtime
sudo ./raspi/setup-console.sh --display touch2-7
```

After an older installation has been switched from `/opt/poc`, confirm the
desktop autostart entry with:

```bash
grep '^Exec=' /home/poc/.config/autostart/poc.desktop
```

It must report `/home/poc/Runtime/raspi/start_console.sh`.

For a manual application test:

```bash
cd /home/poc/Runtime
python3 poc.py
```

The installer creates the maintenance desktop launcher automatically, normally
at `/home/poc/Desktop/POC.desktop`. Its important entries are:

```ini
Exec=/home/poc/Runtime/raspi/start_console.sh
Path=/home/poc/Runtime
Icon=/home/poc/Runtime/images/POC.png
```

If repairing it manually, make both the launcher and target executable:

```bash
cd /home/poc/Runtime
chmod u+x raspi/install-desktop-launcher.sh raspi/start_console.sh
sudo ./raspi/install-desktop-launcher.sh --user poc
```

## 3. Connecting and configuring the network

POC uses two separate Ethernet networks. They must use different IP subnets.

| Interface | Purpose | Configuration |
| --- | --- | --- |
| `eth0` | Private platform network | `192.168.0.3/24`, no gateway or DNS |
| `eth1` | Development/maintenance network | DHCP, supplies the default route |

The private network normally contains:

- POC Raspberry Pi: `192.168.0.3`
- SHS simulator PC: `192.168.0.4`
- Festo controller: `192.168.0.10`

The SHS address is configurable in `poc_config.json`. The built-in Pi Ethernet
MAC, static address and interface roles are configured in
`raspi/network-config.conf`.

Before installation, connect the USB Ethernet adapter if it is available.
Fresh setup runs network configuration automatically. From a local session it
activates the profiles immediately. From an SSH session it writes the profiles
without interrupting the live connection and they activate on the next boot.
The standalone maintenance command remains:

```bash
cd /home/poc/Runtime
chmod u+x raspi/configure-network.sh
sudo ./raspi/configure-network.sh
```

The script verifies that `eth0` is Ethernet. If its MAC differs from the stored
value, it displays the detected address and asks whether this is the built-in
platform adapter. Pressing Enter accepts it and updates `network-config.conf`;
declining makes no network changes. It then assigns the static profile to
`eth0` and an unrestricted DHCP profile to `eth1`. The DHCP profile works with
a replacement USB Ethernet adapter because it is not bound to an adapter MAC.
Absence of `eth1` is allowed; its DHCP profile activates when an adapter is
connected later.

If NetworkManager reports Wi-Fi enabled, the script asks whether to disable it
for appliance operation. Pressing Enter accepts the recommended setting;
answering no leaves Wi-Fi enabled. During an SSH/deferred installation,
accepting the prompt installs a root-owned one-shot service that disables the
Wi-Fi radio at the next boot instead of terminating the current session. Wi-Fi
therefore remains available until the technician manually reboots. If `eth1`
is absent, connect the USB Ethernet adapter before rebooting or choose to leave
Wi-Fi enabled.

For a standalone deferred update over SSH, use:

```bash
sudo ./raspi/configure-network.sh --defer-activation
```

Verify the result:

```bash
nmcli -f DEVICE,TYPE,STATE,CONNECTION device
ip -br address
ip route
```

There should be a `192.168.0.0/24` route through `eth0` and only one default
route, through `eth1`. Test the private devices as appropriate:

```bash
ping -c 3 192.168.0.4
ping -c 3 192.168.0.10
```

If another Pi is installed, accept its detected `eth0` MAC when prompted. If
the configured interface is later missing or its MAC changes unexpectedly, POC
does not start and displays **Network adapters need configuring**.

The previous NetworkManager profiles are retained but their autoconnect is
disabled. List all profiles for diagnosis or recovery with:

```bash
nmcli connection show
```

If POC does not see SHS but a diagnostic client does, first verify that the
running console and the test both use the live runtime:

```bash
pid="$(pgrep -f 'python3 .*poc.py' | head -n 1)"
readlink -f "/proc/$pid/cwd"
grep -A6 '"sim_host_service"' /home/poc/Runtime/poc_config.json
```

The working directory must be `/home/poc/Runtime` and the normal SHS address is
`192.168.0.4` on UDP port `10040`.

## 4. Touch Display 2 maintenance

The Touch Display 2 is portrait-native (`720 x 1280`). POC uses it in landscape
at `1280 x 720`. The installer writes `/home/poc/.config/kanshi/config` with a
`DSI-1` transform of `90`; labwc applies this to both the desktop and touch
coordinates. Use `wlr-randr` to confirm the connector and current transform.
Do not add `video=DSI-1:720x1280@60,rotate=...` to the kernel command line for
this desktop installation. The first existing `kanshi` configuration is backed
up as `config.before-poc`, and the appliance then uses one authoritative POC
profile so manually commissioned and installer-created profiles cannot clash.

The 7-inch panel must be installed with `--display touch2-7`. This writes:

```ini
dtoverlay=vc4-kms-dsi-ili9881-7inch
```

and removes `display_auto_detect` entries. If a previously undetected display
remains black after applying this setting, shut down cleanly, remove PoE power
for at least 20 seconds, then start again before testing warm reboots.

The wallpaper is a normal 1280 x 720 landscape PNG. The Plymouth Touch 2 image
is a 720 x 1280 PNG made by rotating the complete landscape artwork 90 degrees
counter-clockwise. A `touch2-7` setup installs both automatically. To reinstall
only the display assets, run:

```bash
cd /home/poc/Runtime
chmod u+x raspi/install-display-assets.sh
sudo ./raspi/install-display-assets.sh --user poc
sudo reboot
```

Plymouth sees the native 720 x 1280 canvas even though the mounted panel is
landscape. The POC theme draws the pre-rotated image at native size without
scaling and sets `DeviceScale=1` in `/etc/plymouth/plymouthd.conf`. This is the
confirmed configuration; changing the image dimensions, enabling adaptive
theme scaling or adding kernel rotation can reintroduce cropping or rotation.

To disable the on-screen keyboard, run `sudo raspi-config`, then select
**Display Options > Onscreen Keyboard > Always Off** and reboot.

If Touch Display 2 input produces repeated `Goodix-TS` I/O errors in `dmesg`,
the kernel has detected the controller but cannot communicate reliably. Treat
that as a display cable, connector, power or kernel/driver investigation; it is
not fixed by changing POC UI code.

## 5. Platform operation

General POC and motion-platform operating instructions will be added after
platform commissioning and are outside the Raspberry Pi installation scope of
this revision.
