<!-- README.md ver 17 -->
# Platform Operators Console

The Platform Operators Console (POC) is the Raspberry Pi operator console for
the Middlesex motion platforms. It coordinates physical controls, simulator state,
telemetry processing, platform kinematics, Festo pressure output and
 the Windows Sim Host Service (SHS).

The live Raspberry Pi installation is `/home/poc/Runtime` and starts as a
desktop-autostart application. It is not installed as a system service.

## v194 - Ground-Loading Platform display name

The human-facing name of the `platform_v2` physical platform is now
**Ground-Loading Platform** throughout POC, Sim Player and current documentation.
The stable `platform_v2` key, `wheelchair_platform` protocol identity, catalogue
filenames, geometry, limits and motion processing are unchanged.

## v193 - Sim Player optimiser candidate browser and independent A/B tuning

Use **Review candidates…** to browse the optimiser result runs by platform and
park. All available Strict and 5% candidates appear, and any two can be selected
as independent A/B working copies for the same capture. Change DOF gains,
angular cue and neutral offset subjectively, then explicitly save one or both
under new unique trial names in the selected platform's candidate catalogue.
Strict/relaxed remain provenance labels only. A/B review sessions can be saved
and reopened. No live ride, default/preferred variant, source capture, optimiser
CSV or NL2 control behaviour is modified. See
[Sim Player candidate review v193](docs/SIM_PLAYER_CANDIDATE_REVIEW_V193.md).

## v192 - Offline ride optimiser and self-describing player studies

Run `py -m tools.sim_player.optimizer "captures\nl2_ride_study" --output "studies\optimizer"`
from the repository root. It searches Chair V3 and Ground-Loading Platform independently for strict
and <=5%-stroke-request-excess candidates, prioritising Roll/Pitch/Heave, then
Yaw, then Surge/Sway. Full-turn captures can compare existing angular cues.
All exported finalists receive full-frame normal-player validation.

Open a generated CSV normally in Sim Player; keep its `_optimizer.json` beside
it. Platform, midpoint, cue, gains and archived global settings are restored
without double-processing, then can be manually tuned. The batch tool writes
only its output folder. Original captures, operational INI and live variant
catalogues remain unchanged. Grand National is hidden from normal POC selection
but retained for research/capture. See
[the optimiser guide](docs/SIM_PLAYER_OPTIMIZER_V192.md) for commands and limits.

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
Ground-Loading Platform. Its platform/protocol name remains `wheelchair_platform`
for wire compatibility, while its geometry comes from
`kinematics/cfg_SuspendedPlatformMdx.py`. The six fixed and moving
attachment points are CAD-derived XYZ coordinates using the current 84 mm CAD
reference-plane datum. Existing configurations containing the old `wheelchair`
key are migrated automatically.

The Ground-Loading Platform uses a 60 mm muscle-extension reserve to establish neutral
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
- **NL2 v189 station control:** POC discovers stations of the currently viewed
  coaster from request-ID-matched telemetry replies and verifies all discovered
  station modes are manual before reset completion or operator dispatch. This
  prevents an automatic second station from inhibiting Flying Beast dispatch
  without editing `.nl2park` files. Station indices must be zero-based and
  contiguous; an unknown/error/timeout blocks operator dispatch rather than
  guessing. POC does not normally dispatch unwatched trains. A guarded exception
  can clear an occupied departure station if the viewed train has actually
  departed, remains stopped nearby, and a fresh nearest-station reply identifies
  that same station. See `docs/NL2_MULTI_STATION_MANUAL_V189.md`.
- `tools/nl2_local_test_harness/` is a **localhost-only NL2 protocol console**
  with station-state views, explicit controls, raw request editing and response
  logs. Run `py -3 -m tools.nl2_local_test_harness` on the Windows NL2 PC
  with **POC stopped** and the physical motion platform disabled. It imports
  only the POC NL2 protocol module; no Qt or other POC runtime is involved.
  See its [README](tools/nl2_local_test_harness/README.md).
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
