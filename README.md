<!-- README.md ver 12 -->
# Platform Operators Console

The Platform Operators Console (POC) is the Raspberry Pi operator console for
the Middlesex motion platforms. It coordinates physical controls, simulator state,
telemetry processing, platform kinematics, Festo pressure output and
 the Windows Sim Host Service (SHS).

The live Raspberry Pi installation is `/home/poc/Runtime` and starts as a
desktop-autostart application. It is not installed as a system service.

## Supported simulators

- **NoLimits 2:** live TCP telemetry and ride control. Selection is permitted
  only while deactivated. Shared park paths/calibrations are in
  `sims/nolimits2/ride_config.py`; generic simulator settings remain in
  `sims/nolimits2/config.py`. Variants and review statuses live in the selected
  physical platform's JSON catalog.
- **SpaceCoaster:** Unity UDP state/control with recorded motion-cue CSV
  playback.  Motion CSV  playback deliberately bypasses washout.
- **X-Plane development adapter:** UDP plugin telemetry, situation loading and
  pause/run control are retained for future testing, but X-Plane is currently
  hidden from the deployed operator simulator selector.

All simulator adapters expose the same `SimInterface` contract. They do not
access kinematics or Festo output directly.

## Runtime flow

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

The physical platform and simulator state domains remain separate.
Deactivation always has priority over simulator recovery.

Make sure the platform physically safe before development or maintenance runs.

## Operator controls

- Start, Pause and Reset VR are available in the UI and through the configured
  Raspberry Pi GPIO controls. Activation uses one exclusive source: Linux
  defaults to the physical Switch, while Windows defaults to Touch UI. The
  Debug tab can change the activation source for the session.
- The encoder browses experiences while deactivated; holding Dispatch while
  turning it browses simulators. While activated, the encoder changes intensity
  in 0, 10, ... 100% steps.
- Simulator selection is permitted only while the platform is deactivated.
- Physical-platform selection is saved while deactivated and takes effect after
  restarting POC.
- POC refuses to exit until the platform is fully deactivated.
- A startup interlock prevents an already-active physical switch from causing
  automatic activation.

## Ride variant review (POC v178)

Each platform has its own persistent
`sims/nolimits2/ride_variants/<platform_key>.json`. It is seeded from `defaults/`
only if absent. Software updates must not replace working catalogs or their review
decisions. Back up local `ride_config.py` edits before installing v178; the
migration utility imports an old literal RIDE_VARIANTS list into new JSON files
without executing Python or overwriting existing catalogs.

For each park, POC displays its preferred variant only; when none is preferred,
it displays candidates. Rejected variants are hidden. Selecting a preferred
variant rejects every other variant for that park, only on the current platform.

Enable **Debug -> Controls -> Review mode** to show the **Review** button beside
the ride dropdown. Review mode starts off each session. With the platform
deactivated and parked, Review offers **Select as preferred**, **Reject this
variant**, and **Cancel**. Cancel is initially selected. Rotate the encoder to
cycle choices; press to choose, or touch a button. No keyboard or mouse is needed.

**Avoid rejecting all variants for a park unless you have good reason to remove
that park from the operator's ride list.** The dialog warns before the last
available variant is rejected. Undo/repair is deferred to manual JSON editing
with POC stopped. Rejection hides the entry but deliberately leaves its current
loaded settings and reactivation behavior unchanged until another selection;
it does not automatically load or activate a replacement.

The bundled player (`py -m tools.sim_player`) has an independent **Platform**
selector and **File -> Save current settings as a ride variant...**. New candidates
are saved directly to that platform's JSON. The player may inspect new candidates
while a preferred ride exists; those candidates stay hidden in POC. POC reads
added/tuned definitions on restart. PC and Pi catalogs need explicit transfer.

See [the v178 configuration, migration and operator guide](docs/NO_LIMITS_VARIANT_CURATION_V178.md)
for full workflow, file protection and verification instructions. All future
player updates are POC releases; separate sim_player version ZIPs end at v20.

## Platform configuration

`poc_config.json` selects the simulator, physical platform and SHS address. Its
`digital_twin` group optionally sends complete 20 Hz POC output snapshots to a
configured IPv4 address; `"address": "-"` disables this output.
Physical platforms are registered in `config/platforms.py`.

The supplied `poc_config.json` explicitly selects `platform_v2`, which is the
MDX Suspended Platform. Its platform/protocol name remains `wheelchair_platform`
for wire compatibility, while its geometry comes from
`kinematics/cfg_SuspendedPlatformMdx.py`. The six fixed and moving
attachment points are CAD-derived XYZ coordinates using the current 84 mm CAD
reference-plane datum. Existing configurations containing the old `wheelchair`
key are migrated automatically.

The MDX platform uses a 60 mm muscle-extension reserve to establish neutral
height, then normally runs with `WORKSPACE_LIMIT_MODE="geometry"`: a precomputed
moving-platform underside hull enforces 15 mm minimum floor clearance while the
physical actuator envelope remains 950..1200 mm. This avoids imposing the 60 mm
reserve on safe poses. `"reserve"` is the simple fallback mode (950..1140 mm),
and `"off"` disables workspace limiting for diagnostics.

The `Falcon` selection uses `kinematics/cfg_SuspendedChairFalcon.py`; it is a
separate 1000 mm-muscle geometry. The 800 mm chair uses the single
`kinematics/cfg_SuspendedChair.py` geometry. The legacy `chair_v2` selector key
is retained for saved-config compatibility but maps to this same chair config;
there is no separate 1094 geometry file. Chair coordinates remain provisional
until the forthcoming CAD update.

The digital twin protocol includes platform/simulator state, the processed normalized
transform, fractional D-to-P load per muscle, muscle lengths, contractions,
commanded pressures and piston state. See
`docs/digital_twin/POC_DIGITAL_TWIN_UDP_HANDOVER_v1.md` for the wire schema and
Unity receiver requirements.

## Fan control

Each simulator supplies `FAN_CONFIG`, containing its speed threshold, minimum
power, maximum power and speed-to-power shaping table. POC calculates fan power
only while the platform is activated and the simulator is running with valid
telemetry. POC sends simulator speed and commanded fan power to SHS; SHS sends
`power,<percent>` over USB serial and forces zero when commands become stale or
SHS closes.

## Running and validation

Python 3.10 or later:

```bash
python3 -m pip install -r requirements.txt
python3 version_check.py
python3 -m pytest -q
python3 poc.py
```

For Raspberry Pi installation, networking, display setup and normal delta
updates, see `raspi/README.md` and
`docs/raspberry_pi/POC_Raspberry_Pi_Technician_Guide.md`.

The 800 x 480 compact interface provides Ride Control, Input Control, Platform
Output, Platform Selection and Debug. Transform Viewer is available only on
the standard interface because its plots are not useful at compact resolution.

For Windows SHS installation and fan COM-port setup, see
`sim_host_service/windows/README.md`.

## Repository layout

- Runtime Python is grouped under `core/`, `controls/`, `kinematics/`,
  `output/`, `sims/` and `ui/`.
- `sim_host_service/` contains the Windows SHS runtime and startup scripts.
- `raspi/` contains Raspberry Pi installation and appliance configuration.
- `docs/` contains architecture, handover and technician documentation.
- `tools/` contains standalone commissioning and diagnostic utilities.
- `reference_data/` preserves calibration/source data that is not loaded by
  the runtime.

## Update discipline

- Treat the latest accepted complete handover ZIP as the sole source ground
  truth.
- Normal update ZIPs contain changed/new POC files at application-relative
  paths. Player updates are POC releases; v178 includes the complete player tree.
- Increment every changed tracked file and update `file_versions.txt`.
- Run `version_check.py`, compile changed Python and run relevant tests before
  packaging.
- Ordinary runtime updates do not require rerunning `setup-console.sh`.
