# Sim Player - Profile Review v0.1.0

## What to do with each file

| File | Action |
|---|---|
| **Sim_Player_Profile_Review_v0_1_0.zip** | Close Sim Player. Extract directly into your **existing POC repository root**, for example `C:\github\Platform-Operators-Console\`. Merge/replace its `tools/sim_player/` files. The ZIP has complete repository-relative paths; do not extract into `tools` itself or delete the existing player folder. |
| **NL2_MdxLogo_Blackbird_Dynamic_Candidates_root_ready.zip** (previously supplied) | **Only if not already extracted:** extract directly into the same POC root. Results belong under `studies/nl2/dynamic_profiles/MdxLogo_Blackbird_Phase1/`. The corrected `NL2_MdxLogo_Blackbird_Dynamic_Candidates_v0_1_0.zip` contains the same root-ready paths; use either, not both. |
| **Sim_Player_Profile_Review_v0_1_0_Guide.md** | Reference only; no installation. Also included as `tools/sim_player/PROFILE_REVIEW_GUIDE.md`. |
| **Sim_Player_Profile_Review_v0_1_0_Verification.md** | Reference only; no installation. Also included as `tools/sim_player/PROFILE_REVIEW_VERIFICATION.md`. |

**Existing prerequisites, not instructions to reinstall:** complete POC v193,
Timing Study v2.1 / B+C v0.1.4, and Dynamic Profiles Phase 1 v0.1.0. Continue
using the existing Python environment with NumPy, PyQt6 and Qt6 Multimedia.
The update adds no Python dependency. It does not replace the matcher, profile
engine, kinematics, controller, capture tools, variants, recordings or studies.

## First review

Run on the **development Windows PC** (or SHS PC while not controlling a ride).
NoLimits and the physical platform are not needed. From the existing POC root:

```bat
py -m tools.sim_player
```

1. Choose **Review dynamic profiles...** at the top, or
   **File > Review dynamic motion profiles...**.
2. The default results root is `studies/nl2/dynamic_profiles`. Select
   **Chair V3** or **MDX suspended**, then **mdxlogo** or **blackbird**.
3. Select any generated profile for **A**. All candidates are listed together,
   not split into mutually exclusive Strict and 5% modes.
4. For **B**, select another profile, **Static 100% baseline of A**, **Static
   fallback of A**, or **No B**. Select the same source recording for both.
5. Leave **Recalculate from original timing session** unchecked for the first
   review. Choose **Open for review**.
6. Play, seek or loop the difficult sections. Switch A/B without moving the
   playhead or resetting the loop. **Inspect largest muscle-command step** jumps
   to the largest change in the active recording.

An alternative opens a particular profile against its own static baseline:

```bat
py -m tools.sim_player "studies\nl2\dynamic_profiles\MdxLogo_Blackbird_Phase1\chair_v3\mdxlogo\strict\profile.json"
```

Open a results directory from the command line to display the browser:

```bat
py -m tools.sim_player "studies\nl2\dynamic_profiles\MdxLogo_Blackbird_Phase1"
```

## What the comparisons mean

| Selection | Applied motion |
|---|---|
| Dynamic profile | Profile's six post-washout multipliers, including recorded/replayed fallback transitions. |
| Static 100% baseline | Constant multiplier 1 on all six axes, using A's **same fixed cue, midpoint, base gains and washout**. It is not necessarily the previously preferred live variant. |
| Static fallback | Constant six fallback multipliers stored in A's profile, with the same fixed settings. |
| Another profile | That profile's own complete frozen settings and gain curves, on the same capture/platform. Different cue or midpoint is shown rather than silently forced to match A. |

The gain graph uses **solid lines for the active side** and dashed lines for
its comparison. All six axes have their own lane. 100% means multiplier 1,
not 100% of a platform angle or a replacement for the original pre-washout gain.
Click the graph to seek. Base pre-washout gains and all fixed settings remain
part of the profile. A multiplier cannot produce motion from a zero source.

## What is displayed

The existing transform bars, muscle bars, extrema, anomaly panel, timeline and
loop controls are retained. The new **Dynamic profile - gains and sync** dock
adds the six applied multipliers, source curve values, ride time, matched
reference lookup and synchronisation state. Reference lookup and the player's
elapsed time are different: pre-roll is retained and the **First sustained
movement** marker identifies ride time zero.

Whole-recording metrics include pre-roll unless explicitly labelled otherwise.
The primary-axis p95 comparison excludes pre-roll and describes normalized
requested motion, not measured physical motion. Existing analysis warnings
remain visible; research qualification is not a safety approval.

Production muscle-length **rounding is distinguished from limiting**. A rounded
permitted length can differ from the requested float without being clipping.
New event categories identify lost sync/reacquisition, lookup lag over 250 ms,
fallback/blend use and a commanded length outside its configured range. Original
kinematic and large-step markers remain available.

The initial candidates still contain sharp source transitions, especially
Blackbird's late braking. Use the largest-step jump and existing anomaly tools;
feasible lengths do not imply achievable pneumatic speed or smooth motion.

## Saved study versus raw-session reconstruction

**Saved study (default):** needs only the supplied candidate folder containing
`profile.json`, `reference.npz` and its `<capture>_dynamic_diagnostics.csv` files.
It consumes the exported **post-washout** values and B+C progress once, applies
the selected comparison, recalculates production kinematics and verifies the
dynamic output against the saved trace. It does not treat diagnostic CSVs as
raw telemetry or filter them a second time.

The original Phase 1 diagnostics do not contain the separate raw match phase,
position residual or observation age. Those fields are shown as unavailable,
not invented or reported as zero. A saved-study parity check is not a fresh
verification of the raw timing file's digest.

**Raw reconstruction (optional):** tick **Recalculate from original timing
session** and choose the extracted timing-session folder (or its parent).
There must be one matching `<capture>_trial.json`, plus its original companions.
The trial must be complete and match the profile's park, platform-compatible
source identity, NL2 version, viewed train/seat/station, VR state and timing-file
SHA-256. A duplicate trial in several sessions requires choosing a narrower
folder. ZIP paths are not accepted as session directories.

The complete recording is then processed in order at its recorded 20 Hz service
schedule using the installed parser, telemetry conversion, washout, B+C matcher,
profile fallback, kinematics, pressure calculation and null command transport.
The player subsequently seeks cached frames: a seek does not reset filters or
advance the matcher out of order. Cancellation is available while loading; a
failed/cancelled new review retains the previous view.

Raw reconstruction additionally exposes position residual and **match phase
minus actual profile lookup**. A small position residual alone does not prove
phase accuracy. Low-information stationary-time progression is identified, not
reported as a precise spatial measurement.

## Video and Visualizer

The timing-study captures supplied for these profiles contain **no video**.
The central gain chart is therefore shown automatically. This is not a missing
video error. The usual muscle and transform panels work normally.

Raw reconstruction can use an accompanying video and synchronization sidecar
from the **same capture**, where available. It never borrows another repeat's
video. **Gains / video** switches the central view; offset editing is disabled
inside profile review. Calibrate the original recording using ordinary Open
capture first when needed.

The existing **File > Settings** Visualizer control remains available. It sends
only the displayed calculated Twin pose/lengths and validity flag, never Festo
commands. Review mode does not change the configured Visualizer address or turn
Twin output on automatically.

## Save and resume a review

**Save review...** creates a new research JSON, normally under:

```text
studies/nl2/profile_reviews/
```

It saves selected profiles/capture, source mode, active A/B side, playhead and
loop, with integrity hashes and relative file references where possible. Reopen
via **File > Open saved dynamic review...**, or pass that JSON to the player.
It is not a self-contained copy: keep the original candidate folders and, for
raw mode, the original session. Moving the common parent keeps relative paths
working. Existing review files are not overwritten.

Dynamic review is intentionally **read-only** in this release. Existing static
gain, cue, midpoint, platform, experiment and live-variant save controls are
locked while a profile is open. Switch back to **Open capture** or the original
**Review candidates** workflow to use ordinary static manual tuning. There is
no profile-knot editor, profile regeneration or live dynamic-variant promotion.

## Compatibility and boundaries

Profile/reference checksums and relevant processing-source fingerprints are
checked before review. CRLF/LF line-ending-only changes are accepted. A genuine
processing or geometry difference requires profile regeneration; it is not
silently hidden under an old Strict/5% classification.

There are no live NL2, SHS or Festo connections. Production POC remains unchanged.
The saved numerical comparison has been checked on all supplied profiles; actual
Windows Qt/video interaction still needs evaluation. Anticipatory cues, actuator
lag compensation, live fallback approval and physical validation are outside
this update.
