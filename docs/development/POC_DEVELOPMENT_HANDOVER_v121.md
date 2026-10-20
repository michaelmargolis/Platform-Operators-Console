<!-- POC_DEVELOPMENT_HANDOVER_v121.md ver 4 -->
# POC development overview

## 1. Update discipline

Treat the latest code in this repository as the sole source ground truth. 

The current package and manifest revisions are recorded at the top of
`file_versions.txt`; they increment with each accepted update.

For every normal update:

- change only requested behaviour;
- compare source bytes/content, not only version comments;
- increment every changed tracked file and `file_versions.txt`;
- produce a delta ZIP containing only changed/new files;
- run `python3 version_check.py`, compile changed Python and run relevant tests;
- test the exact final ZIP before delivery and confirm its download is
  accessible.

Ordinary runtime deltas do not require rerunning `setup-console.sh`.

## 2. Creating a complete handover snapshot.

Run from the complete POC root:

```bash
python3 create_poc_handover_zip.py
```

The default output is:

```text
PlatformOperatorsConsole_full_handover.zip
```

The packager validates `file_versions.txt`, runs `version_check.py`, rejects
missing or unexpected zero-length files, excludes caches/captures/archives and
known stale staging paths, generates `PACKAGE_CONTENTS_SHA256.txt`, and tests
the finished ZIP. Versioned files are always mandatory.

The packager excludes old parallel trees and loose artifacts, including
`config/core`, `config/sims`, `config/tests`, `sims/from ai`, `ui/temp`, loose
root `config.py`/`sim.py`, Word working/lock files and diagnostic captures.

## 3. Target deployment

- Raspberry Pi 3B or later; current platform uses a Pi 3B+.
- Current Raspberry Pi OS 64-bit Desktop with Wayland/labwc.
- Live runtime: `/home/poc/Runtime`.
- Desktop autostart, not a system service.
- Full-screen application; Escape or Debug-tab Exit returns to the desktop only
  after the platform is fully deactivated.
- Private platform Ethernet: Pi `192.168.0.3`, SHS `192.168.0.4`, Festo
  `192.168.0.10`.
- USB Ethernet uses DHCP for development/maintenance access.

The project's 7-inch Touch Display 2 is portrait-native 720×1280 and used as
1280×720 landscape. Firmware autodetection intermittently failed after warm
reboot, leaving no DSI connector, backlight or panel-driver messages. The
explicit overlay passed 30 consecutive warm reboots:

```ini
dtoverlay=vc4-kms-dsi-ili9881-7inch
```

Fresh installation for this panel uses:

```bash
sudo ./raspi/setup-console.sh --display touch2-7
```

`auto` remains available for HDMI/firmware-detected displays and
`waveshare-10.1` remains available for the project Waveshare panel.

The Pi installer installs `python3-pyqtgraph`, which is required by the
Transform Viewer, and verifies that it imports before reporting success.
Transform Viewer is available only in the standard interface; it is suppressed
on the 800×480 compact screen.

## 4. Runtime architecture

```text
UI / GPIO controls
       |
     PocCore -------- Sim Host Service -------- simulator PC / USB fan
       |
       +------------> optional UDP digital twin
       |
PlatformController
       |
kinematics -> contraction-to-pressure mapping -> Festo UDP
```

`PocCore` owns action legality, state coordination, gain/washout and fan-power
calculation. Simulator adapters own simulator-specific telemetry and control.
`PlatformController` owns activation/deactivation, prop sequencing, kinematics
and physical output. Simulator modules never access Festo output directly.

Physical deactivation always has priority. There is no software
`virtual_output` gate.

## 5. Physical controls

Current BCM GPIO mapping:

| Function | BCM | Behaviour |
|---|---:|---|
| Dispatch | 18 | Pull-up; dispatch when active, simulator browse modifier when inactive |
| Pause | 15 | Pull-up, falling edge |
| VR reset | 27 | Pull-up, falling edge |
| Load | 23 | HIGH Normal, LOW Full |
| Activate | 24 | LOW Activate, HIGH Deactivate |
| Encoder A/B | 4/14 | Quadrature input |
| Encoder switch | 17 | Pull-up, falling edge |

While deactivated the encoder browses rides; holding Dispatch browses
simulators. While activated it changes intensity through 0, 10, ... 100%.
Startup deliberately refuses to activate if the physical Activate switch is
already ON.

Activation sources are exclusive. Each POC start defaults to physical Switch
mode; the operator may select Touch UI mode from Debug as a temporary fallback.

## 6. Platform geometry and motion output

`poc_config.json` defaults to `platform_v2`, registered in
`config/platforms.py` and implemented by `kinematics/cfg_SuspendedPlatform.py`.
Its operator name is Wheelchair Platform and its protocol name is
`wheelchair_platform`. The separately available `chair_v2` uses
`kinematics/cfg_SuspendedChair1094.py`.

The provisional geometry comes from `suspended chair.dxf`:

- six platform coordinates are used in clockwise order;
- platform attachment-point distance 0–1 is approximately 1094 mm;
- platform attachment Z is zero and base attachment Z is 900 mm;
- muscle length is 600–800 mm plus 200 mm fixed hardware;
- neutral platform height is calculated by `kinematics_V3.py`.

Physical measurement is still required to confirm which platform version was
built. Recent capture data showed substantially improved muscle dynamic range
with the 1094 configuration; subjective physical-platform testing remains in
progress.

Both chair configurations use `output/chair_DtoP_synthesized.csv`. Its four
load rows represent approximately 11, 22, 31 and 41 kg supported load per
muscle. The 11 kg increasing/decreasing rows preserve the measured 800 mm chair
curves. Higher-load rows apply the load-dependent differences from the 1000 mm
wheelchair measurements after mapping chair contraction to 1.25 times the
wheelchair contraction. Values are limited to 0–6000 mbar.

The wheelchair platform continues to use its measured
`output/wheelchair_DtoP.csv` rows for 13, 24, 33 and 43 kg individual-muscle
loads. For both platforms, POC calculates approximate load per muscle as total
payload plus platform tare divided by six and retains the fractional result
during load interpolation.

D-to-P lookup retains fractional contraction and pressure values until final
millibar rounding. Each muscle independently detects accumulated reversal from
its last peak or trough, keeps the current curve through a 5 mm deadband, then
blends to the opposite curve over the next 5 mm. This avoids a pressure step at
the branch boundary and allows gradual 1 mm reversals to change branch.

## 7. NoLimits 2

NL2 uses live TCP telemetry on port 15151. The temporary, session-only
**Legacy lift-height Z** checkbox compares:

- checked: ride-height-proportional legacy Z, including visual lift motion;
- unchecked: SOC-style vertical-G heave with temporary Z washout.

Mdx Logo Coaster is the current comparison ride. Its legacy profile maps the
captured bottom/lift heights to normalized Z values chosen to reduce balanced
clipping with the provisional 1094 geometry.

Yaw is derived from quaternion change and low-pass filtered with a 0.12-second
time constant before the existing yaw gain.

## 8. SpaceCoaster

SpaceCoaster uses Unity UDP state/control and prerecorded CSV motion cues.
Motion playback advances only while running, freezes while paused, rewinds for
recovery and deliberately bypasses washout.

A separately patched SpaceCoaster Unity `Assembly-CSharp.dll` sends live WAGON
position. The patched simulator DLL and its build inputs are **not included**
in this repository; retain and verify them separately before attempting to
rebuild or commission the SpaceCoaster integration. POC derives fan speed from position delta/time, rejects isolated
outliers with a short median stage, averages approximately one second and
applies `position_speed_scale` before the simulator fan curve. Current physical
testing reports that fan speed synchronizes well with the visuals.

## 9. Fan control

Each simulator owns `FAN_CONFIG`, grouping:

- `speed_at_100`;
- `threshold_speed` and `threshold_power`;
- `max_power`;
- the interpolated `speed_power_table`;
- any simulator-specific speed filtering/scaling parameters.

POC sends speed and fan power to SHS only through the common host-service
protocol. Power is forced to zero unless the platform is activated and the
simulator is running with valid telemetry. SHS owns the USB serial connection,
saves the selected COM port when opened and sends `power,<percent>\n` to the fan
controller. SHS also forces zero when commands become stale or it closes.

## 10. UI and capture

- Standard UI supports 1280×720 and 1280×800; compact UI supports 800×480.
- Compact UI provides Ride Control, Input Control, Platform Output, Platform
  Selection and Debug; Transform Viewer is standard-interface only.
- Status message is 22-point bold; intensity value is 20-point bold.
- Status captions are 14-point bold and Sim/Data is displayed on two lines.
- Run, Pause and Activate controls use the larger requested font.
- Platform Output displays simulator speed between Transform from Sim and CSV
  Out.
- Front and side simulator views use the available label travel so ±100%
  reaches the corresponding edge; top-view scaling remains unchanged.
- CSV capture includes raw simulator values, speed, transforms, muscle lengths,
  contraction percentages and pressures.

## 11. Digital-twin UDP output

POC v121 introduced the optional, one-way UDP snapshot stream for an external
digital twin. The current `poc_config.json` default keeps it disabled:

```json
"digital_twin": {
  "address": "-",
  "port": 10020
}
```

`"address": "-"` disables transmission. When enabled with a valid IPv4
address, `core/digital_twin.py` sends a compact versioned JSON snapshot during
each 50 ms POC data cycle. Each packet contains platform/simulator state,
validity flags, the exact fractional D-to-P load per muscle, processed
normalized transform, six muscle lengths, six contraction percentages, six
commanded pressures and optional piston state.

The sender is non-blocking and best-effort. Encoding or network errors are
caught and cannot interrupt platform control. The values are calculated or
commanded state, not physical sensor feedback. The full version-1 wire schema,
units, transition semantics and Unity receiver guidance are in
`../digital_twin/POC_DIGITAL_TWIN_UDP_HANDOVER_v1.md`.

## 12. Windows Sim Host Service

SHS runs in the logged-in Windows simulator-operator session. It must not run
as a Windows service because it starts and controls interactive simulator
applications. Startup scripts accept Python 3.10 or later.

The SHS window shows POC/simulator status, selected fan COM port, simulator
speed, fan power, last POC message and latest errors. See
`sim_host_service/windows/README.md` for installation and firewall instructions.

## 13. Validation and immediate work

Before accepting any update:

```bash
python3 version_check.py
python3 -m compileall -q .
python3 -m pytest -q
```

Immediate remaining work:

1. Complete subjective comparison of legacy height Z against SOC heave.
2. Confirm the physical platform attachment coordinates and selected geometry.
3. Validate synthesized chair D-to-P behaviour with physical-platform captures.
4. Physically verify prop polarity, travel time and clearance pose.
5. Continue simulator/platform commissioning without changing safety or state
   semantics without explicit approval.
6. Implement and validate the Unity receiver against
   `../digital_twin/POC_DIGITAL_TWIN_UDP_HANDOVER_v1.md`.

## 13. Safety constraints

- An already-ON Activate switch must never initiate motion at application
  startup.
- Deactivation must always override simulator state and motion requests.
- Festo output is live whenever the application output path runs.
- Keep personnel clear and use the hardware safety system for commissioning.
- Treat the prop as unverified until polarity and engagement have been checked
  on the physical platform.
