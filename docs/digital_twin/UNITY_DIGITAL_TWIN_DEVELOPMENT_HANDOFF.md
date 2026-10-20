<!-- UNITY_DIGITAL_TWIN_DEVELOPMENT_HANDOFF.md ver 7 -->
# POC Unity Digital Twin Development Handoff

Prepared 12 September 2026

POC v121 introduced the live version-1 UDP snapshot protocol. Treat
`POC_DIGITAL_TWIN_UDP_HANDOVER_v1.md` as the authoritative wire-format and
receiver handover; the broader modelling guidance in this document remains
applicable.

## 1. Purpose and scope

Develop a Unity application that estimates and renders the physical pose of a
six-muscle Festo DMSP suspended motion platform from the pressure output produced
by the Platform Operators Console (POC).

The first useful implementation is a **quasi-static digital twin**. It should:

- receive or replay the six pressure commands;
- estimate six muscle lengths and one physically consistent rigid-platform pose;
- render the base, muscles, chair/platform and attachment geometry;
- show the POC commanded pose separately from the pressure-derived estimate;
- expose residuals, saturation and data age so the estimate is not mistaken for
  measured position;
- support recorded-data replay before live networking is added.

This is an engineering and diagnostic representation, not a safety system and
not a source of commands to the real platform. It must never send Festo commands.

## 2. Ground-truth inputs to provide to the new development thread

Supply:

1. the complete current POC source ZIP, not only a delta;
2. this handoff document;
3. the complete earlier Unity twin project, or a Unity package exported with
   dependencies;
4. any original CAD-derived model files used by that project;
5. at least one POC telemetry/pressure CSV capture;
6. photographs and confirmed dimensions of the commissioned chair/platform;
7. the selected platform configuration and its D-to-P CSV.

The POC source ZIP is authoritative for software behaviour. CAD and physical
measurements are authoritative for final geometry. Values marked unverified in
POC must not be promoted to verified twin parameters merely because they render
plausibly.

## 3. Current POC output chain

The live POC physical-output path is:

```text
sim telemetry
  -> washout, gains and intensity
  -> normalized xyzrpy
  -> real transform in mm and radians
  -> Stewart inverse kinematics
  -> six requested muscle lengths in mm
  -> load-selected D-to-P table with hysteresis branch
  -> six pressure commands in mbar
  -> Festo EasyIP UDP output
```

Relevant POC files:

| File | Purpose |
| --- | --- |
| `core/platform_controller.py` | Connects transforms, kinematics, D-to-P and Festo output |
| `core/poc_core.py` | Provides the current output snapshot used by the UI |
| `kinematics/kinematics_V3.py` | Current suspended-platform geometry and inverse kinematics |
| `kinematics/cfg_SuspendedChair1094.py` | Selected V2 chair geometry and muscle parameters |
| `kinematics/cfg_SuspendedChair.py` | Alternate V3 chair geometry retained for comparison |
| `kinematics/cfg_SuspendedPlatform.py` | Wheelchair/Falcon geometry and muscle parameters |
| `config/platforms.py` | Maps selectable platform IDs to configuration modules |
| `output/d_to_p.py` | Load interpolation and rising/falling table selection |
| `reference_data/pressure_tables/chair_DtoP.csv` | Original chair pressure tables |
| `output/wheelchair_DtoP.csv` | Wheelchair/Falcon pressure tables, if present in the supplied snapshot |
| `output/muscle_output.py` | Stores requested lengths and sent pressures |
| `output/festo_itf.py` | EasyIP UDP transport and optional actual-pressure polling |
| `output/fstlib/easyip.py` | Festo packet encoding |

`poc_config.json` identifies the startup platform through `selected_platform`.
Do not assume the chair geometry applies to every selectable platform.

## 4. Current geometry conventions

POC represents a pose as:

```text
[surge, sway, heave, roll, pitch, yaw]
```

Translations are millimetres and rotations are radians after normalized values
have been converted using the selected platform limits.

`kinematics_V3.py`:

- accepts either three left-side base coordinates and mirrors them, or six
  explicitly ordered coordinates as used by the 1094 configuration;
- applies the same three-or-six rule to platform XY coordinates;
- when mirroring, uses `coords + reversed(coords)` while negating Y;
- computes neutral platform Z rather than taking it directly from configuration;
- treats actuator length as muscle length plus fixed hardware length;
- uses a yaw-pitch-roll rotation matrix applied to platform anchor coordinates;
- calculates all six muscle lengths from the resulting platform anchor positions.

The available `chair_v2` configuration contains:

```text
MUSCLE_MAX_LENGTH       800 mm
MUSCLE_MIN_LENGTH       600 mm
FIXED_HARDWARE_LENGTH   200 mm
UPPER_ACTUATOR_Z_HEIGHT 900 mm    PROVISIONAL DXF VALUE
HAS_PISTON              true
```

The six base coordinates currently supplied, clockwise from the rightmost
negative-Y attachment, are:

```text
( 369.794016, -522.564892, 900)
( 231.048560, -561.432158, 900)
(-595.582491,  -69.202112, 900)
(-595.570652,   72.166062, 900)
( 231.045397,  570.005428, 900)
( 369.794016,  522.639951, 900)
```

The corresponding six moving-platform XY coordinates are:

```text
( 760.607579, -169.379240)
(-252.472559, -582.737035)
(-378.428521, -510.016326)
(-378.428521,  510.015971)
(-252.472559,  582.736680)
( 760.607579,  173.064104)
```

Do not manually retype these into multiple Unity components. Import them from a
versioned twin configuration file generated from or checked against the POC
configuration. Preserve muscle numbering exactly.

Unity uses metres and a Y-up scene convention. A provisional conversion is:

```text
Unity X = POC X / 1000
Unity Y = POC Z / 1000
Unity Z = +/- POC Y / 1000
```

The sign of Unity Z must be established with a one-axis validation test. Avoid
converting POC Euler angles directly during initial validation: transform the
six anchor positions or convert a tested rotation matrix/quaternion. This avoids
handedness and Euler-order mistakes.

## 5. What pressure can and cannot determine

A DMSP muscle has no unique pressure-to-length relationship. Length depends on:

- tensile load;
- pressure;
- contraction;
- whether it is inflating or exhausting;
- motion rate and pneumatic delay;
- rubber/fabric hysteresis;
- platform acceleration and payload centre of mass.

Therefore the Unity display is an **estimated pose**, not measured pose. Six
independent inverse table lookups can also produce six lengths that do not belong
to any possible rigid platform pose.

The existing D-to-P tables are still useful as the first estimator because they
contain separate increasing/decreasing branches and multiple load rows. Their
load semantics must be confirmed. The current POC backend calculates a nominal
per-muscle load as:

```text
(selected payload + unloaded platform weight) / 6
```

For the chair, the current table headings and configured load choices may not
select meaningfully different table rows. Treat this as a known POC calibration
issue, not as established physics for the twin.

## 6. Recommended first estimator

### 6.1 Invert the selected D-to-P surface

For each pressure, interpolate contraction from the appropriate pressure table.
Retain a per-muscle branch state based on pressure or estimated-length direction.
Handle plateaus such as repeated 6000 mbar values explicitly; an inverse over a
plateau is not unique and should return a bounded value plus a saturation flag.

### 6.2 Project the six estimates onto one valid platform pose

Solve for pose `q = [x, y, z, roll, pitch, yaw]` that minimises:

```text
sum(weight[i] * (geometryLength(i, q) - estimatedLength[i])^2)
```

Use:

- the previous solution as the next starting point;
- platform travel and angle bounds from configuration;
- a damped Gauss-Newton or Levenberg-Marquardt update;
- residual reporting rather than hiding a poor fit;
- a neutral-pose initial condition;
- no extrapolation beyond the valid muscle/table range.

The problem is only six variables, so a small deterministic C# solver is
sufficient. Unity does not supply a general nonlinear least-squares solver as a
standard component. Keep the solver independent of Unity scene objects so it can
be unit-tested using plain vectors and arrays.

### 6.3 Rendering cadence

Estimate at the incoming POC cadence, expected to be approximately 20 Hz. Render
at the display frame rate by interpolating between timestamped estimated poses.
Do not rerun table inversion and nonlinear fitting every rendered frame unless
profiling shows a reason to do so.

## 7. Later meaningful improvements

Implement only after the first estimator is working and validated:

1. Convert the table data into an approximate pressure-contraction-force surface.
2. Solve coupled static force and moment equilibrium including gravity, platform
   mass, payload and centre of mass.
3. Model pressure response with separate fill and exhaust time constants.
4. Add axial damping based on estimated muscle length velocity.
5. Use separate inflation/exhaust surfaces for simple hysteresis.
6. Fit per-muscle corrections from slow physical sweeps under known loads.

A full thermodynamic airflow and flexible-muscle model is not justified until
real measurements demonstrate that the quasi-static model is insufficient.

## 8. Recommended Unity project structure

```text
Assets/
  DigitalTwin/
    Art/
      ImportedLegacy/
      Materials/
    Configuration/
    Prefabs/
    Scenes/
    Scripts/
      Data/
      Estimation/
      Geometry/
      Networking/
      Rendering/
      UI/
    Tests/
      EditMode/
      PlayMode/
  LegacyTwin/
```

Suggested responsibilities:

| Component | Responsibility |
| --- | --- |
| `TwinDataReceiver` | Background UDP reception and thread-safe packet queue |
| `TwinReplaySource` | Timestamped CSV replay using the same sample interface |
| `TwinSample` | Versioned data contract containing pressures and reference values |
| `PlatformDefinition` | Anchors, muscle dimensions, limits, mass and load metadata |
| `DtoPInverseModel` | Bounded table inversion and hysteresis state |
| `PlatformGeometry` | Anchor transforms and length calculations |
| `QuasiStaticPoseSolver` | Rigid-pose least-squares solution |
| `TwinPosePresenter` | Interpolation and movement of visual objects |
| `MuscleVisual` | Length, orientation, colour and status of one muscle |
| `TwinDiagnostics` | Data age, residuals, clipping, branch and connection state |

Use assembly definitions to separate runtime code and tests if the project grows,
but do not add packages merely to create architecture.

## 9. Unity scene hierarchy

```text
DigitalTwinRoot
  BaseRoot
    BaseVisual
    BaseAnchor0 ... BaseAnchor5
  CommandedPlatformRoot
    CommandedVisual
    CommandedAnchor0 ... CommandedAnchor5
  EstimatedPlatformRoot
    EstimatedVisual
    EstimatedAnchor0 ... EstimatedAnchor5
  MuscleVisual0 ... MuscleVisual5
  DiagnosticsCanvas
  CameraRig
  Lighting
```

The commanded platform may be a transparent outline or ghost. The estimated
platform should be visually dominant. Provide a UI toggle for commanded,
estimated and overlay views.

For the quasi-static phase, move `EstimatedPlatformRoot` directly from the
solver. Do not add six Unity joints. A parallel Stewart mechanism is a closed
chain, while Unity articulations are tree structured; six generic joints can
fight each other and introduce solver-dependent jitter unrelated to the muscle
model.

## 10. Incorporating the earlier Unity twin assets

### 10.1 Known earlier design information

Earlier development notes describe:

- a simple platform model originating as a 3MF or similar CAD export;
- a moving platform object with six child anchors named approximately
  `Anchor0` through `Anchor5`;
- a rigid best-fit/Kabsch alignment of six local anchors to six target points;
- an earlier broadcast UDP interface on port 10020, now superseded by the
  versioned `POC_TWIN` snapshot protocol documented separately;
- message concepts named `request`, `distances` and `pose`;
- translations followed by three rotations, with rotations converted from
  radians to degrees and an earlier Z-sign inversion.

Those assets are not present in the current POC source snapshot. No Unity C# source or patched SpaceCoaster assembly is included in this
POC repository. Earlier SpaceCoaster patcher sources were supplied as separate
development artifacts, not as the previous Unity digital twin. Do not use
them as the basis for this application.

Treat the old messages, sign inversion and Kabsch behaviour above as legacy
interface information. Do not copy the old receiver; implement the versioned
protocol in `POC_DIGITAL_TWIN_UDP_HANDOVER_v1.md`.

### 10.2 Preferred import method

If the earlier Unity project opens successfully in its original Unity version:

1. make an untouched archival copy;
2. open it and identify the platform prefab, meshes, materials, textures, anchor
   objects, camera setup and any muscle visualisation;
3. export only those assets as a Unity package with dependencies;
4. import that package into `Assets/LegacyTwin/` in the new project;
5. duplicate reusable assets into `Assets/DigitalTwin/` before modifying them.

If files must be copied directly, copy each asset together with its `.meta` file
so Unity GUID references are preserved. Copying only a prefab or scene without
its referenced meshes, materials and textures will produce missing references.

Do not initially import the entire old `Library`, `Temp`, `Logs` or build-output
directories. Retain the old `Packages/manifest.json`, `Packages/packages-lock.json`
and `ProjectSettings/ProjectVersion.txt` separately for dependency diagnosis.

### 10.3 Asset audit

Classify every earlier asset as follows:

| Reuse directly | Reuse after validation | Replace/quarantine |
| --- | --- | --- |
| Meshes and textures | Platform prefab hierarchy | Old UDP receiver |
| Materials that compile | Anchor empty objects | Direct-transform scripts |
| Base/chair visual components | Muscle cylinders/lines | Old kinematics constants |
| Camera composition | UI layout | Port-10020 assumptions |
| Lighting/environment | Kabsch alignment code | Obsolete packages/input code |

The visual mesh is not geometry ground truth. Anchor positions must be compared
numerically with current POC/CAD coordinates. Keep the imported model as a child
of a clean root transform with:

```text
root position = (0,0,0)
root rotation = identity
root scale    = (1,1,1)
```

Correct model orientation and scale in a dedicated parent object or re-export,
not by scattering compensating rotations through solver code.

### 10.4 Anchor migration

For each imported `Anchor0...Anchor5`:

1. record its old local coordinate;
2. confirm whether it represents the base or moving attachment;
3. confirm muscle numbering from the old scripts and visuals;
4. compare it against the generated POC coordinate;
5. replace or reposition it from the approved `PlatformDefinition`;
6. run neutral and single-axis tests before accepting it.

If the earlier Kabsch solver is clean and tested, it may be retained as a
diagnostic method for recovering a rigid transform from six target points. It
must not replace the new pressure-to-length estimator or conceal inconsistent
length estimates. Report its fit residual.

## 11. Data interface to Unity

### 11.1 Do not rely on passive capture of Festo traffic

Festo output is unicast UDP to the controller, normally `192.168.0.10`. A Unity
application on another PC will not reliably receive those packets without a
network mirror, proxy or explicit duplicate transmission.

Recommended development order:

1. CSV replay in Unity;
2. a dedicated POC-to-twin UDP telemetry stream;
3. optional actual-pressure input if Festo feedback is proven reliable.

The WIZnet Festo emulator can validate EasyIP packets but should not become the
production twin transport.

### 11.2 Implemented versioned packet

Use the implemented `POC_TWIN` version-1 JSON packet exactly as specified in
`POC_DIGITAL_TWIN_UDP_HANDOVER_v1.md`. That document is authoritative for field
names, units and validity semantics. Do not implement the earlier proposed
field names such as `platform_id`, `source_time_s`, `payload_level` or
`pressure_actual_mbar`.

Use `command_pressure_mbar` as the estimator input. The supplied muscle lengths
and transform are reference channels used to validate and compare the estimate;
they must not silently override the pressure-derived pose. Reject packets with
the wrong protocol/version or wrong number of channels, and display stale-data
status rather than continuing indefinitely with an apparently live twin.

The receiver thread should only parse/enqueue immutable samples. Unity objects
must be updated on the main thread.

## 12. Visual diagnostics

At minimum display:

- connection/replay state and sample age;
- selected platform and payload level;
- six commanded pressures;
- six estimated muscle lengths;
- six commanded muscle lengths when supplied;
- estimated xyzrpy;
- solver RMS and maximum length residual;
- table branch for each muscle;
- pressure and contraction saturation;
- invalid configuration or non-convergence.

Recommended muscle colours:

```text
normal          neutral/green
near limit      amber
saturated       red
stale/invalid   grey
```

Draw base and platform axes, neutral-plane references and numbered anchor
gizmos. Provide a fixed engineering camera in addition to any orbit camera.

## 13. Validation plan

### Phase A: asset and coordinate validation

1. Render the fixed base and neutral moving platform.
2. Confirm scale using a known physical dimension.
3. Confirm all twelve anchors and muscle numbering.
4. Compare Unity neutral muscle lengths against POC values.
5. Apply positive surge, sway, heave, roll, pitch and yaw individually.
6. Confirm signs visually and numerically.

### Phase B: deterministic estimator tests

1. Generate pressures from known POC muscle lengths using the original D-to-P
   code or captured reference cases.
2. Invert them in Unity.
3. Recover a valid pose.
4. Compare estimated and original lengths/pose.
5. Test table endpoints, plateaus, branch changes and impossible combinations.
6. Confirm repeated runs produce identical results.

### Phase C: recorded ride replay

Replay the same timestamped capture through every estimator revision. Graph or
log:

- commanded versus estimated pose;
- commanded versus estimated muscle length;
- solver residual;
- percentage of time at each limit;
- discontinuities and non-convergence.

### Phase D: physical calibration

With appropriate platform safety controls:

1. test slow neutral/heave/pitch/roll poses under known loads;
2. measure actual settled pose or individual lengths;
3. repeat paths in both directions;
4. identify pressure delay with small step tests;
5. fit corrections without changing the POC control path;
6. reserve a separate dataset to test rather than fit the model.

## 14. Acceptance criteria for the first useful release

- Imports and displays the earlier platform visual assets without missing
  references.
- Uses current POC geometry from one explicit versioned definition.
- Replays a captured six-pressure stream at the correct timestamps.
- Produces one bounded rigid-platform pose rather than six disconnected lengths.
- Preserves muscle numbering 0 through 5.
- Shows commanded and estimated platform poses separately.
- Reports stale input, saturation, poor residual and solver failure visibly.
- Passes neutral and all six single-axis coordinate tests.
- Contains edit-mode tests for table inversion and geometry length calculation.
- Does not send any network message capable of controlling the real platform.

## 15. Decisions deliberately deferred

- Final pressure-contraction-force model.
- Whether actual Festo pressure feedback is sufficiently reliable.
- Permanent calibration instrumentation.
- Dynamic rigid-body simulation.
- Detailed pneumatic flow and temperature modelling.
- Validate the implemented POC_TWIN version-1 UDP receiver against live packets.
- Support for multiple simultaneous platform types in one running scene.

## 16. Primary technical references

- Festo, *Fluidic Muscle DMSP*: force/contraction operating ranges and the
  pressure-adjustable spring behaviour:
  <https://ftp.festo.com/Public/PNEUMATIC/SOFTWARE_SERVICE/Documentation/2026/TR/DMSP_EN.PDF>
- Unity `ArticulationBody`: articulations are reduced-coordinate parent-child
  trees, relevant to the decision not to represent the six-muscle closed chain
  as one articulation:
  <https://docs.unity3d.com/6000.7/Documentation/ScriptReference/ArticulationBody.html>
- Unity `Physics.Simulate`: useful later for deterministic stepped dynamic
  replay:
  <https://docs.unity3d.com/6000.1/Documentation/ScriptReference/Physics.Simulate.html>

## 17. Immediate first task for the next thread

Inspect the supplied earlier Unity project and produce an inventory containing:

- Unity editor version and render pipeline;
- scenes and prefabs;
- platform/base meshes and their source units;
- materials and missing shader dependencies;
- exact anchor object names and local coordinates;
- muscle numbering;
- receiver and pose scripts;
- message formats and ports;
- packages that are actually required;
- which assets can be migrated without modification.

Do not write the new estimator until the legacy asset inventory and coordinate
comparison are complete.
