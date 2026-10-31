<!-- README.md ver 5 -->
# sim_player - bundled with POC v182

The player is released as part of POC. Install the relevant POC delta in release order;
no separate player ZIP is published. Earlier standalone release notes below
remain as historical references only.

## v182 — Variant-specific ride-neutral height and Chair V3 limits

- The **Motion tuning** dock includes a **Ride neutral height (physical Z)**
  adjustment with a +/-100 mm slider (5 mm steps), fine numeric input, reset,
  and calculated platform/ride/effective neutral heights. Positive means
  raising the platform and shortening muscles. Slider release, Enter or focus
  loss triggers full offline reanalysis; the original video and CSV remain intact.
- The calculation uses the live POC backend and biases the real Heave pose
  **before** inverse kinematics and workspace limiting, without moving parked
  or propped positions. The raw replay option disables this adjustment.
- Variant switching, experiment snapshots, baseline comparison/restore and
  candidate JSON saving preserve the current adjustment; no existing candidate
  or INI file is overwritten. Status feedback separates short and long clipping
  and reports initial muscle lengths as well as floor clearance where applicable.
- Chair V3 applies the user-specified limits: 90/90/100 mm translational and
  10/8/9 degrees rotational. Chair V2 geometry/limits are unchanged.
- Automatic offset sweeps are deferred. See `docs/RIDE_NEUTRAL_HEIGHT_V182.md`.
  Real Windows Qt6 UI and physical-platform validation remain required.

## v180 — Kinematic event durations (30 October 2026)

- Kinematic timeline shows **orange horizontal bars** from the first to last
  anomalous frame in each event. Single-frame events use a short, selectable
  stub; displayed time values and actual event grouping are unchanged.
- Remove kinematic event vertical ticks/peak markers. The thin **white vertical
  playhead** remains; ride-event markers and captions remain unchanged.
- Hover anywhere along an orange bar for category, start/end, duration, peak
  timestamp and anomaly details. Click the bar to seek to its existing **peak**
  frame and open the corresponding anomaly panel event.
- Overlapping intervals are drawn long-first, short-last. When several bars
  overlap at one position, the shortest bar takes mouse interaction priority.
- No changes to anomaly detection, platform processing, video, POC control or
  active motion settings. Windows Qt6 visual QA remains necessary.

## v179: platform labels, comparable muscle ruler and Yaw diagnostics

- The last Platform dropdown choice is **V2 Chair** (`chair_v2`), rather than
  incorrectly repeating Chair V3. Both keys intentionally reference the same
  `cfg_SuspendedChair.py` kinematics module, but have distinct JSON catalogs.
- The Muscle lengths plot positions the valid **minimum** and **maximum** at
  the same horizontal coordinates on the 600–800 mm chair and 750–1000 mm MDX
  platform. An equal fraction of usable muscle travel occupies an equal
  fraction of the plot. The right-hand values stay in physical millimetres.
- 50 mm vertical guides remain linear within each platform's valid interval.
  Requested out-of-range extrema remain visible in the left/right 12% warning
  gutters; *only these warning gutters* are compressed to fit the requested
  extent of the whole recording. Actual millimetre excess is in labels/tooltips.
  The scale is fixed through playback and does not change when peaks advance.
- Red indicates the same requested out-of-range or final clamp as before.
  A greater number of separated *final-clipped timeline episodes* is not
  necessarily more clipping: fewer violating frames can form more episodes.
  For example Blackbird on chair_v3 gives 1,480 clipped frames / 2 episodes
  at 100% Yaw and 1,451 frames / 4 episodes at 0% Yaw, with otherwise 100%
  gains on the 26 October 2026 `Blackbird_repeat01_20261026_090300.csv` capture.
- No POC physical-platform logic, NL2 motion calculation, anomaly detection,
  video, Twin messages, source captures or JSON variant storage was changed.

## Platform-specific variants and direct saving

The **Platform** dropdown is to the left of **Ride variant**. It lists configured
PLATFORM_NAME values and initially reads the saved POC platform. Its tooltip
identifies the platform key, geometry module and working variant JSON. Choosing a
platform recalculates the same capture, including muscle limits and applicable
floor geometry, without reloading video or changing POC's saved platform. Each
platform owns an independent catalog; no per-entry platform tagging is needed.

The selected platform's non-rejected variants appear for the recorded park. Its
preferred variant is the default; otherwise the bare-name standard or first
candidate is used. `<park name> raw` remains available for comparison. Candidates
can be researched here even when a preferred variant exists, but POC will show
only the preferred ride until that preference is manually reopened.

**File -> Save current settings as a ride variant...** replaces the Python
clipboard command. It asks for a new unique label and saves a candidate directly
to `sims/nolimits2/ride_variants/<platform_key>.json` (or POC_RIDE_VARIANTS_DIR).
The old entry is never overwritten. Select a named preset first; raw replay has
no complete live heave/calibration preset. Global telemetry INI, source CSV and
POC's platform choice are never modified by this operation.

Working catalogs are initialized only if absent, then preserved across updates.
After an external edit, switch away and back to the platform before saving again.
POC reads newly added/tuned candidates on restart. Development-PC and Pi files
are separate copies unless deliberately shared; transfer is explicit.

Experiment JSON v3 includes the platform key, geometry module and its source
SHA-256. Older experiment versions still load with a platform warning; changed
geometry produces a warning. The existing global-settings snapshot comparison
remains in place and does not overwrite the operational global INI.

Platforms with no/zero PLATFORM_CLEARANCE_OFFSET require no hull and display
floor clearance as N/A. MDX's existing floor model and neutral calculation remain
unchanged. No ride neutral-height bias is implemented in this release.

POC review is described in `docs/NO_LIMITS_VARIANT_CURATION_V178.md`.
**Operators should avoid rejecting every variant for a park without good reason.**
Rejected variants disappear from normal lists; there is no undo UI. The loaded
POC ride is intentionally retained until another selection. Review mode starts
off and uses a touch/encoder dialog whose initial choice is Cancel.

Run from the repository root with `py -m tools.sim_player` (Windows). Player uses
PyQt6; operational POC stays on PyQt5. Video decoding, marker defaults and dock
behavior are retained. Actual Windows/Pi GUI validation remains necessary.

## Historical standalone release notes

## v20 — Kinematic markers default; single hover instruction (29 October 2026)

- The Markers dropdown defaults to **Kinematic** when sim_player opens, including
  before a capture is loaded. **Ride events** remains selectable.
- The kinematic timeline retains every event marker and its hover details, but
  replaces truncated, overlapping per-event captions with one instruction:
  **Hover over an event for details**. The instruction starts at the first
  (earliest) kinematic event marker; no instruction is drawn if there are no events.
- Recorded ride-event captions, marker click/seek behaviour, anomaly detection,
  playback, processing and saved panel layout are unchanged.
- Complete replacement ZIP for `tools/sim_player/`; POC v177 is unchanged.
- GUI rendering requires Windows PyQt6 verification; headless tests do not
  visually inspect Qt text and clipping.

## v19 — Named park default and explicit raw replay (29 October 2026)

- **Requires POC v177** (`ride_config.py` display/format validation included).
  Replace the entire `tools/sim_player/` folder using `sim_player_v19.zip`.
- Opening a Blackbird capture selects **Blackbird** and immediately calculates
  its standard, park-calibrated configuration; it no longer starts with legacy
  offline height processing. A deployed park with only one `[modifier]`
  configuration selects that variant automatically when no bare-name entry exists.
- Rename the legacy playback entry **Blackbird raw** (or `<park name> raw` for
  another park). It replays the captured `norm_heave` as in v17, rather than
  applying a park-specific height calibration, and does not become a new
  `RIDE_VARIANTS` entry. Selecting it is an explicit research comparison.
- **sim_player always displays complete variant labels**, independent of the
  POC-only `SHOW_VARIANT_MODIFIERS` flag.
- Exporting an experimental variant to clipboard requires the new strict
  space-separated `[modifier]` suffix syntax; it still never writes POC files.
- Direct use of `load_recording(path)` remains raw/legacy as before so existing
  code/tests are not silently changed. Only initial GUI capture loading
  explicitly requests the standard named variant.
- Unknown captured park names fall back to raw; ambiguous configurations with
  multiple variants but no standard bare-name default report an error.
- No changes to Qt decoder, saved panel layouts, UDP, Festo or original CSV.
  Windows GUI/video testing remains necessary.

## v18 — Shared NL2 ride variants (28 October 2026)

- **Requires POC v176** and its `sims/nolimits2/ride_config.py`,
  shared `angular_cues.py`, and v176 offline analyzer. Apply the POC v176 delta
  **before** replacing `tools/sim_player/` with `sim_player_v18.zip`.
- Ride variant dropdown only lists variants of the **captured physical park**.
  `As recorded (v17)` remains the default and leaves the earlier research
  replay untouched. Named variants use the park's fixed calibrated height
  range rather than the extrema observed in an incomplete CSV.
- Variants select Euler/Sine/Gravity, optional 0-200% relative six-axis gain
  multipliers, and the shared POC Roll/Pitch washout policy; Vertical-G mode
  uses the temporary classical-Z filter in the v176 shared analyzer.
- Named variant snapshots and the current global POC settings are saved in
  experiment JSON v2; v1 JSON remains readable. Experiments for deleted or
  renamed labels can use their saved variant snapshot. If the **current**
  global POC settings differ from the saved snapshot, the player **warns**;
  calculations still use current installed global settings. To exactly
  reproduce a past study, restore the recorded global configuration first.
- File → **Copy current settings as a ride variant…** copies a unique-label
  Python dictionary to the clipboard for manual review and addition to
  `RIDE_VARIANTS`; it never writes production config. This action requires
  a named preset, because As recorded may have no live calibrated heave.
- Selecting a preset recalculates offline and keeps the same CSV/video and
  playback position. It does **not** write `ride_config.py`, the operational
  telemetry INI or captured CSV. Selecting **As recorded** resets the
  experimental cue/gains to the current POC baseline.
- Player remains **PyQt6**, while live POC remains **PyQt5**. The existing
  video decoder, seek logic, Twin UDP output and physical-hardware isolation
  are unchanged. GUI/video behaviour still requires Windows testing.

## v17 — Fill available height; fixed playback controls (28 October 2026)

- Fix v16 startup with a large blank region beneath playback controls and a lower-right dock cut short above the status bar. Remove the artificial empty right-hand dock completely; the **last visible right-hand dock now fills to the bottom**.
- The video occupies all vertical space above the compact, **fixed-height playback/timeline controls**. The former vertical video/control splitter and its unused handle/drag/window-resize code are removed, rather than merely disabled. Resize the *window* to resize the video; Qt preserves the source video aspect ratio.
- Compact the upper visible right dock rows to their minimum practical heights once after startup. This also handles restored layouts with three separately stacked docks (Transform, Muscle lengths, Motion tuning), not only the default two-row tabbed layout. Manual dock resizing after startup remains supported.
- **Saved-layout migration:** restore the v16 spacer temporarily so Qt can reconstruct the old `QSettings` dock state, then remove it before the player is shown. Preserve existing window geometry and panel arrangements; discard obsolete video-splitter state. View → Restore default layout re-tabulates the lower panels and applies the compact heights.
- No changes to video backend/FFmpeg, ride calculations, physical POC files, kinematics, Twin output, or capture data.
- Validation in the standalone source environment: `compileall` passed; 101 tests passed, 16 skipped (PyQt6 unavailable), and 9 requiring the POC `core/` and `tools/analyze_nl2_csv.py` were not run. Windows GUI/video and saved-layout testing remain necessary.

## v16 — compact startup panel heights (28 October 2026)

- Right-side Transform and tabbed Muscle Lengths/Angular Cues/Anomalies/Motion Tuning docks start at their minimum practical height. A non-interactive blank Qt dock slot absorbs excess right-column height; visible docks remain movable and resizable.
- Playback controls beneath the video use their layout's natural minimum height, fixed against resizing. Resize the main window to adjust video height; the former video/control divider is disabled.
- Startup compact sizing is re-applied after loading any previously saved Qt dock layout. Production POC and video decoding/seek logic are unchanged.

## v15: Muscle plot guides and optional Video Sync panel

- The Muscle Lengths plot uses more of its available width, placing labels
  closer to the edges. Subtle vertical guides indicate 50 mm intervals; stronger
  guides mark the valid muscle-length boundaries. Requested min/max extrema
  remain the same as v14, including out-of-range red warnings.
- The Video offset, Save sync offset, and OBS source information controls live
  in a **Video synchronisation** dock, hidden initially. Use **View → Video
  synchronisation** to reveal it, move/float it, and close it when finished.
  Video offset data and persistence remain unchanged.
- No production POC files, kinematics, Twin UDP packet formats, or video
  decoder code changed.

# sim_player v14 — Resizable player window

`sim_player` is an independent Windows research utility housed under
`tools/sim_player/` in the Platform Operators Console repository. **Installing
this release does not modify POC's production simulator, kinematics, SHS,
Festo, or PyQt5 files.** The operational POC remains PyQt5; only the player
uses PyQt6 and Qt Multimedia (FFmpeg backend).

## v14 window-resizing correction

The video is still resized using the horizontal divider, but releasing the
divider now **resizes the outer player window** by the corresponding amount,
instead of giving the playback-control area extra unused height. Drag up to
make the overall application shorter, or down to enlarge it. When the window
is maximized, the splitter only adjusts the internal allocation (Windows does
not permit resizing a maximized window).

The video stack and the Qt6 video widget now have a compact minimum-size policy,
so their native media size does not prevent resizing the **top or bottom edge**
of the player window either. The video maintains its source aspect ratio.
The controls retain their minimum usable height, as do the dockable panels.
The default window height is slightly smaller. Existing saved window sizes are
not overwritten; you can now resize them manually. The divider position is
still remembered across sessions.

The video decoder, motion processing, muscle extrema and Twin output are
unchanged. Only layout behaviour and related tests have been updated.

## v13 startup correction

The new vertical video splitter now creates both child widgets **before**
accessing its draggable handle. Qt returns `None` from `handle(1)` until the
second child exists; v12 crashed at startup while setting its tooltip.
No playback, video decoder, motion calculation or network behaviour changes.

## Install and launch

Extract the **complete** `sim_player_v14.zip` into the repository root, replacing
only `tools/sim_player/`. From the repository root:

```powershell
py -m pip install PyQt6
py -m tools.sim_player
```

To open a specific recording:

```powershell
py -m tools.sim_player "captures/nl2_ride_study/Blackbird_repeat01_20261026_110843.csv"
```

The analysis requires the existing POC Python dependencies (including NumPy)
and `tools/analyze_nl2_csv.py`, `core/telemetry_config.py` and platform
kinematics. The original capture CSV is read-only. It may automatically find
its matching video, state-event CSV and video timing metadata, where present.

## Video size and persistent muscle extremes (v12; startup fixed in v13)

The video now sits immediately above the playback controls in a **vertical Qt
splitter**. Drag the narrow horizontal divider underneath the image **up** to
shrink the video **and** main window, or down to enlarge both. The
source aspect ratio stays automatic. The default window/video allocation is
approximately 10% smaller than v11; existing saved window geometry is reduced
once on upgrading. Divider position is remembered separately in `QSettings`.
The Qt6 FFmpeg decoder and timeline seek policy have **not changed**.

The **Muscle lengths** dock still displays **final permitted** target lengths
as moving bars, but now also draws two vertical **requested-length extrema**
markers for each muscle over the portion of the ride examined so far:

* **Blue**: shortest requested length reached so far.
* **Amber**: longest requested length reached so far.
* **Red**: either requested extreme exceeded the configured working range
  (750–1,000 mm with the default platform), including demands that POC's
  workspace limiter later corrected.

Each row also shows the current permitted length and the accumulated requested
minimum/maximum in millimetres; hovering over the panel shows precise figures.
The scale includes the **whole recorded experiment's possible requested range**
and stays fixed during playback, so an existing extrema line never moves back
toward the centre because the display automatically rescaled. The horizontal
rail section within the configured working range remains identifiable.

Extrema **only advance outward**. Forward seeks include intermediate recorded
samples; backward seeks cannot retract them. The markers disappear when the
user deliberately **restarts playback** from the beginning (or restarts the
single-play A/B range), opens another ride, or changes the calculated cue/gains.
Automatic repeated A/B loops **retain** extrema until the user explicitly
restarts. Immediately after reset the markers are blank until playback advances.
No original recordings are modified.

## Motion Tuning (v11)

Open **View → Motion tuning · relative gains** (or **Panels**) to change the
six independent experimental gains for Surge, Sway, Heave, Roll, Pitch, Yaw.
They default to **100%**, meaning *the current saved POC axis gain is used*;
0–200% multiplies that existing gain, **not** an absolute degree/mm amount.
The sliders adjust the input to the existing production `TelemetryConfig`
processing before POC's washout, workspace limiting and kinematics. The
operational POC gain/washout settings, other POC source files and the capture
CSV are never modified. Slider values are whole percentages; adjustment via
keyboard is possible in addition to dragging.

Reanalysis occurs when a slider is released (or after a short keyboard
input pause). It runs in the existing background analysis worker, pauses
playback and retains the current video frame/time. Once complete, all motion
bars, modeled clearances, anomaly events, angular-cue comparisons and
Visualizer UDP output update using the new calculated ride. The video decoder
is **not** reopened or sought as a result of gain changes.

The Motion Tuning panel shows full-ride metrics:

* Maximum **final commanded muscle-length change per sample**, compared with
  the adjustable anomaly-panel warning (50 mm by default).
* Minimum **applied modeled floor clearance**, compared with the adjustable
  warning threshold (30 mm by default), when floor calculations are available.
* Counts of out-of-range **requested** muscle frames, workspace interventions
  and final-output clamp frames, plus the largest requested excess.
* Changes in peak step, workspace interventions and minimum clearance relative
  to the experiment's **baseline**.

The initial baseline is the ride at its original current settings. **Set
baseline** designates the currently calculated experiment as the comparison;
**Restore baseline** returns to that mode and those gains. **Reset 100%**
resets the six gains while leaving the chosen angular cue unchanged.

**Save experiment** and **Load experiment** use a separate research JSON file
containing the cue mode, six relative gains and two anomaly warning
thresholds. It does not overwrite the ride CSV or any live POC configuration.
An experiment inherits the *currently saved* POC gain/washout configuration
at time of replay; this first release does not yet embed a complete snapshot
of that external configuration, so it is not a fully standalone reproducible
processing archive.

### Red muscle-length warnings

The **Muscle lengths** panel continues to show the **final permitted** muscle
lengths (the six targets sent to the Digital Twin). If a particular muscle's
**unconstrained requested length** falls outside its configured range, that
muscle's bar and value turn **red**, *even if the workspace limiter corrected
it*. A final-output length clamp is also shown red. At sufficient panel width
both the permitted length and the offending requested length appear together;
hover for exact request/permitted values. Green means there is no requested
range violation or final clamp for that muscle.

These are geometric demand warnings, not measurements of physical muscle
movement. A red request does not necessarily mean the **permitted command** is
out of range; the Anomalies panel distinguishes each stage. For a diagnostic
example, raising Blackbird's Roll gain to 150% on the 26 October 2026 capture
produces 13 workspace-corrected out-of-range requests (offline model).

## Kinematic anomalies (v10)

The single timeline marker line has a **Markers** dropdown near its right end:
**Ride events** (recorded NL2 dispatch/return markers) or **Kinematic** (computed
motion anomalies). Only one category is displayed at a time; the existing
playhead and A/B markers remain independent. Tension-feasibility events can
be added as a later category when a tension solver is implemented.

A new **Kinematic anomalies** dock is available via **View** or **Panels**,
and is tabbed with the Muscle Lengths and Angular Cues docks. The dock lists
**grouped anomaly intervals**; it does not create one table row per offending
telemetry frame. Clicking a row seeks the video, data and Twin to the peak
sample. Hovering over a timeline marker displays its details; clicking a
kinematic marker also selects the corresponding table row.

**Examine event** (or double-clicking a row) sets the existing A/B range to
the entire anomaly plus **one second before and after**, clipping at ride
boundaries. It selects Loop range, seeks to A and starts playing with the
existing one-second loop-hold delay. A/B controls and the Qt6 video seek
backend are otherwise unchanged.

The detector reads the existing POC offline analyser's *per-frame results*;
there is no additional kinematics solver or independent workspace logic.
Categories:

| Anomaly | Test / interpretation |
|---|---|
| Requested outside range | **Unconstrained** muscle-length request below configured minimum or above configured maximum; may be fully corrected by workspace limiting |
| Workspace limited | POC adjusted the requested pose to stay within configured geometric/workspace constraints; reported independently of requested-length violation |
| Final length clipped | Final output required additional clamping after workspace processing; distinct from a corrected request |
| Large muscle step | Difference in any **final permitted** muscle length from the preceding recorded sample exceeds threshold, default **50 mm** |
| Low applied clearance | Modeled **applied** floor clearance below the warning threshold, default **30 mm** |
| Requested floor violation | Unconstrained requested pose below POC's configured floor limit (even if subsequently corrected) |

Both numeric warning thresholds are editable directly above the anomaly table
and are **session-only**. Changing a threshold or angular-cue mode recomputes
the anomaly list for the currently selected processed recording; it never
recalculates kinematics unnecessarily, writes to the captured CSV or changes
operational POC configuration. Missing floor-clearance measurements do not
produce fabricated floor warnings. The muscle-step threshold is **per
recorded sample**, not an actuator velocity or acceleration safety rating.

These are predicted command/geometry anomalies, not measurements of physical
platform response. No tension-only feasibility analysis is performed yet.

## A/B playback range and loop modes (v9)

The existing playback slider has two draggable markers: **red A** (start) and
**amber B** (end). Both default to the recording boundaries. The range is
highlighted, and the markers are subdued in Full ride mode. Hover over a
marker for its exact timestamp; dragging A or B does not seek the video,
change the telemetry frame or transmit to the Digital Twin.

There are three modes in the compact dropdown beside **Mark** and **Duration**:

* **Full ride** (default): ignore A/B when playing; the markers remain visible
  for editing or later use.
* **Play range**: playback starts at A if outside the range, stops at B and
  holds there. The next Play restarts from A.
* **Loop range**: playback starts at A if outside the range and repeats A→B.
  When B is reached, the video, graphs and Visualizer hold on B for the
  selected **Delay**, then the video/telemetry/Twin seek to A and resume.

The delay dropdown is enabled only in Loop range mode and offers **0, 0.5,
1 (default) or 2 seconds**. The delay is non-blocking, so the UI remains
responsive. `LOOP HOLD` appears during the pause and a brief `LOOP RESTART`
message appears when playback resumes. No repeated decoder seek or play/pause
workaround is used: at most one seek to B when holding and one to A at restart.
At zero delay, the player seeks directly to A without pausing the decoder.

* To select a short region, move to the desired moment, choose Duration
  (default **2 seconds**) and press **Mark**. A/B are centred on that time,
  clipped to the recording bounds; the mode automatically becomes Loop range.
* Drag either marker to fine-tune the selected interval. Markers cannot cross
  and the minimum interval is 0.05 s (or the full duration of a shorter file).
* **Reset range** restores A=0, B=recording end and selects Full ride.
* The playback range and delay are not written to the capture or to POC.
  Switching angular-cue mode preserves A/B; opening another capture resets
  A/B and Full ride mode (the chosen delay remains session-only).

The range controls are visually separated from the **Panels** dropdown so the
latter remains distinct from playback-range settings.

## File → Settings and Visualizer status

**File → Settings…** replaces the former Options menu item. A prominent
**Visualizer output** dropdown explicitly shows either:

* `Off — no UDP output` (default on a fresh installation)
* `On — send replay to Visualizer`

The IP address and port fields activate only when output is On. Defaults are
`127.0.0.1` and UDP `10020`. The menu choice, address and port are remembered
through Qt `QSettings`, independent of POC's own configuration.

The main window always shows **TWIN OFF**, **TWIN ENABLED**, **TWIN HOLD**,
**TWIN SENDING**, or **TWIN ERROR**, as appropriate. UDP is connectionless;
`TWIN SENDING` does **not** confirm receiver delivery. The Visualizer should
be configured for **Twin Length** mode; all six transmitted pressure fields
are zero. No physical hardware is addressed.

## Experimental angular-cue comparison

The **Angular cue (experimental)** selector is a separate *session-only*
research control located underneath video synchronization. Its three modes:

| Mode | Angular cue before gain/washout processing |
|---|---|
| Euler (current POC) | **Exact recorded** normalized Roll/Pitch; the production baseline |
| Sinusoidal Roll | Replace Roll with a bounded sine of the recorded quaternion's Euler Roll; leave Pitch unchanged |
| Quaternion gravity vector | Replace Roll and Pitch with scaled lateral and longitudinal components of the gravity/up vector expressed in the coaster's local coordinates |

The existing v167 NL2 nominal angular scales are obtained from
`sims/nolimits2/config.py` (not copied to a second tuning table). Using the
current calibration, the idealised formulas for a pure rotation are:

* Euler Roll: `-0.6 * angle_rad / pi` (with `angle_rad` wrapped into ±pi).
* Sine Roll: `-(0.6 / pi) * sin(angle_rad)`.
* Gravity Roll: `-(0.6 / pi) * local_up_x`.
* Gravity Pitch: `+0.6 * local_up_z`; with pure Pitch angle `p`, this is
  `-0.6 * sin(p)`.

These candidates **preserve the established small-angle sign and slope**.
They are deliberately **not** gain-matched at large banking angles: the
maximum pure-Roll magnitude of sine/gravity is approximately 19% normalized,
versus the ±60% wrapped Euler value near inversion. This is an important
research trade-off, not a hidden automatic gain correction. The sine
candidate retains Euler's sensitivity to pitch/gimbal singularities; the
gravity-vector candidate does not. Gravity alone is not a full inertial
motion-cueing algorithm and does not model centrifugal accelerations.

Selecting a new mode:

1. Pauses playback and holds the current video time.
2. Recomputes the *entire* ride, including filter history, with the same
   production `TelemetryConfig`, kinematics, workspace and floor-limiting code.
3. Updates the processed 6DOF bars, muscle bars, analysis panel and, if
   enabled, sends the recalculated pose to the Digital Twin immediately.
4. Caches a bounded number of results per (cue mode, six gain values) for instant subsequent switching.

**It does not reopen or seek the video**, alter video calibration, change
operational settings, or overwrite the capture. The production POC still uses
its existing Euler Roll conversion. Alternative modes require quaternion
fields (`quat_x`, `quat_y`, `quat_z`, `quat_w`) in the capture; missing or
invalid fields cause a clear analysis error without replacing the previous
valid result.

The Transform panel's **Show original NL2 CSV transform** option always shows
the untouched six normalized axes from the recording, independent of the
selected experiment. Processed display shows the selected cue **after**
normal gains, ride-specific washout and intensity.

## Angular cues comparative analysis panel

A dockable **Angular cues · comparative analysis** panel is tabbed with the
Muscle Lengths panel (bottom-right) to avoid occupying additional main-window
space. Show/hide it from **View** or **Panels**, resize, detach, or use
**View → Restore default layout**. Switch to each mode once to populate its
row; uncalculated modes show a dash.

Columns report:

* **Peak step** — maximum absolute change in any of the six commanded muscle
  lengths between two consecutive telemetry samples (in mm).
* **>10 mm** — sample-to-sample transitions with a muscle step above 10 mm.
* **Limited** — number of samples requiring POC geometric/workspace limiting.
* **Min floor** — minimum modeled platform-to-floor clearance when available.

The summary also shows peak absolute processed Roll/Pitch. These are
**theoretical commanded-motion values**. They are not actual muscle speeds,
measured physical floor clearance, proof of positive pneumatic muscle tension,
or evidence of occupied-platform safety. A tension-only feasibility solver
remains a future, separate analysis stage using the preserved applied pose
and geometry data.

## Video, timing and existing panels

The video continues using the **unchanged v6 Qt6/FFmpeg backend**. The source
aspect ratio is preserved automatically. Video frames, timeline, event markers,
frame stepping, video calibration and seek behaviour remain as in v6.

Capture file association, recorded irregular timestamps, raw telemetry,
requested/applied poses, workspace limiting, six muscle-length targets and
zero-pressure Twin UDP packets all retain the existing interfaces. This
release does not create another kinematics or packet implementation.

## Architecture

```
tools/sim_player/
  app.py                Qt6 playback, background analysis and Twin coordination
  player_ui.py          Menus, layout, shared controls, dock registration
  panels.py             Transform, muscle, one event line, draggable A/B, comparison
  tuning.py             Headless experimental gain transforms and summary metrics
  tuning_panel.py       Dockable six-axis slider and baseline/experiment controls
  anomaly_panel.py      Docked anomaly table, threshold inputs, Examine action
  anomaly_detection.py  Qt-free grouped anomalies from existing POC frame results
  loop_range.py         Qt-independent A/B markers and three range modes
  options_dialog.py     Settings dialog (visible Visualizer On/Off choice)
  settings.py           Persistent user preferences (not motion tuning)
  angular_cues.py       New Qt-free experimental NL2 quaternion cue mappings
  metrics.py            New Qt-free whole-ride diagnostic measurements
  recording.py          Original CSV and preprocessed POC analysis adapter
  twin_output.py        Existing POC_TWIN sender adapter
  video_backend.py      Existing Qt6/FFmpeg decoder; unchanged
  video_sync.py         Existing calibrated timeline/seek policy; unchanged
  tests/                Headless and optional Qt6 regression tests
```

The experimental mapping sits **before** production telemetry gains and
washout. `recording.py` retains the original CSV axes separately from the
substituted pre-filter cue and the post-filter pose. Per-frame
`PlaybackFrame.analysis` retains workspace-limited applied real poses and
muscle lengths for future diagnostics and tension-force research.

## Checks

From the POC repository root:

```powershell
py -m pytest -q tools/sim_player/tests
```

The anomaly tests cover request-vs-final clipping, grouped intervals,
large permitted-length steps, floor data availability, threshold changes,
A/B Examine boundaries, and real Blackbird Euler/Sine/Gravity comparisons.

The range tests cover default boundaries, centered Mark, end clipping,
marker non-crossing, off-range Play and timer wrap. An optional Qt6 GUI test
checks that marker dragging does not change the playhead or emit seeks.

The numerical tests cover complete 360-degree rotations, ±180-degree angle
wrapping, combined Roll/Pitch, gravity cue yaw invariance, Euler baseline
reproduction, real POC analyser invocation, and zero-pressure Twin packets.
The optional Qt6 GUI tests verify menus, docks and visibly selectable
Visualizer settings on a machine with PyQt6 installed. Live Windows Qt6
GUI/video/Visualizer operation must still be verified on the user's PC.
