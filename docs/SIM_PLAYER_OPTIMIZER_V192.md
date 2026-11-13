<!-- SIM_PLAYER_OPTIMIZER_V192.md ver 1 -->
# POC v192: offline ride optimiser

## Install and run

Overlay this delta onto a **complete v191 POC installation**, preserving the
`PlatformOperatorsConsole/` folder structure. Close POC and Sim Player before
replacing files. Existing working ride-variant JSON files, telemetry INI files,
platform geometry and park files are not included or replaced.

Run from the repository root, using the Python environment used for POC's tools:

```bat
py -m tools.sim_player.optimizer "captures\nl2_ride_study" --output "studies\optimizer"
```

The optimiser is a command-line batch tool, not a new Sim Player menu action. It
requires the existing POC Python/numpy dependencies but does not require Qt or
video decoding to run. Use `python` instead of `py` on Linux or in a selected
virtual environment. It never opens an NL2, Festo or Twin network connection.

Validate inputs and see coverage without running a search or writing outputs:

```bat
py -m tools.sim_player.optimizer "captures\nl2_ride_study" --list
```

A focused first run:

```bat
py -m tools.sim_player.optimizer "captures\nl2_ride_study" --only blackbird --output "studies\optimizer"
```

Both `chair_v3` and `platform_v2` are selected by default. Each input can be a
file or a folder; folders are searched recursively. Multiple input paths are
accepted. State-event CSVs, unrelated CSVs and previously generated optimiser
CSVs are not treated as new captures. Exact duplicate capture contents are
ignored. The CSV's ride field, not a guessed filename, determines its park.
Unknown rides, bad times, missing necessary fields and nonfinite data are
reported explicitly. Missing supported rides are listed, never fabricated.

## Search scope and objectives

The algorithm is deterministic, multi-start and coarse-to-fine, not exhaustive
and not a proof of a global optimum. Defaults are 900 approximate unique trial
parameters per ride/platform, a coarse 20 mm midpoint sweep, gain refinements of
25/10/5 percentage points and midpoint refinements of 20/10/5 mm. The supported
midpoint offset interval is -100 to +100 mm, reduced to valid neutral geometry.
Positive offset raises the physical platform. Relative gains span 0 to 200%.
The configured global master gain, global axis gains, washout settings, selected
variant's heave mode/calibration and angular washout overrides remain fixed.
A globally disabled axis therefore cannot be restored by a relative gain.

Roll, Pitch and Heave form the first objective tier. Yaw is second; Surge/Sway
are third. The primary tier balances active axes using a mean/geometric-mean
combination; axes with negligible source motion do not force an artificial cue.
Motion score combines DC-removed RMS and 5th-to-95th percentile span in fractions
of each platform's single-axis range. A constant heave bias cannot win the score.
Lower tiers cannot trade away more than one 0.005-normalised-range primary score
bin. Scores refer to **requested pose after normalised-axis saturation**, not
measured platform response. Saturation, clipping, velocity and step diagnostics
are reported; force, acceleration and comfort are not certified or optimised.

Whole recordings are filtered at their original sample intervals before trial
screening. The search uses the existing homogeneous linear telemetry filters to
reuse preprocessed traces; no new motion algorithm is introduced. Numerical
parity with ordinary player replay is covered by tests for disabled, classical
and legacy filtering and all three cue modes. A screening set begins with
uniformly spaced frames and axis extrema/neighbours. Full-trace checks add
missed worst-case frames. Every exported finalist is checked on **every frame**,
independently recalculated through `load_recording`, then exported and reopened
through the player's ordinary initial-open path to verify identical requested
muscle lengths. Screening alone can never qualify an export.

The existing current/preferred variant supplies the baseline; compatible other
saved variants are additional seeds. No live catalogue is seeded or updated by
the optimiser. An invalid existing catalogue is an error rather than silently
being replaced. If every variant for a park is rejected, that job reports it.

By default, all recordings of a park contribute equally to the motion score and
must pass the same candidate's limits. Each recording still gets its own output
CSV. Use `--per-capture` for independently tuned settings for each recording.

## Angular cues

Ordinary non-inverting recordings keep their current angular cue. The default
`--cue-policy auto` compares existing Euler, Sine and Gravity cues only when the
quaternion series indicates an approximately complete Roll or Pitch turn: a
continuous projected-gravity angular span of at least 330 degrees plus inverted
samples. Yaw-only turns do not trigger a comparison. This is a conservative
heuristic, not an infallible detector of complex compound rotations.

For a known full-turn recording that the detector cannot classify, use
`--cue-search-rides blackbird fenrir` with the appropriate park keys.
`--cue-policy current` disables comparisons; `--cue-policy all` explicitly
requests them for all selected rides. Sine/Gravity comparisons require a complete
valid quaternion series. Missing quaternions are reported, not silently invented.
Gravity is evaluated as a joint Roll/Pitch mapping, not as Roll alone.

## The two targets

| Target | Requested muscle lengths | Workspace/floor requirements |
|---|---|---|
| `strict` | No excursion outside actual physical limits | No workspace reduction or final clamp; no floor violation |
| `relaxed5` | Maximum excursion at any frame/muscle <=5% of usable stroke | Workspace reduction/clamping may occur; no floor violation |

With the current configurations, Chair V3 uses 600-800 mm (10 mm relaxed
allowance) and MDX uses 750-1000 mm (12.5 mm allowance). These thresholds are
calculated from the selected configuration, not hardcoded into the search.
The allowance is the **size of the worst excursion**, not a percentage of the
ride's duration. Final commanded muscle lengths remain within physical limits.
MDX requested floor clearance must satisfy the existing 15 mm minimum plus
0.5 mm model margin. There is no floor allowance for the relaxed target.

A default run retains the best found candidate plus one meaningfully different
alternative for each target, where found: up to four settings per ride/platform.
Different cue choices are retained preferentially among qualified alternatives.
A strict solution may also be the best relaxed solution; the optimiser does not
add errors just to make the two targets different. No all-zero ride is presented
as an optimised result. An unqualified or unavailable target is reported as
partial instead of being given a misleading pass label.

## Open, review and tune results

Every run creates a new timestamped directory. It contains `index.csv`, `run.json`
and per-platform/per-park folders. For example:

```text
run_<timestamp>/
  index.csv
  run.json
  chair_v3/blackbird/
    result.json
    candidate_variants.json
    strict_01/
      <original-stem>_<hash>.csv
      <original-stem>_<hash>_optimizer.json
      <original-stem>_<hash>_experiment.json
      ... optional video sync/calibration and state-event companions
    strict_02/
    relaxed5_01/
    relaxed5_02/
```

Start the normal player with `py -m tools.sim_player`. Use **File > Open** on a
candidate CSV listed in `index.csv`. Keep the CSV and `_optimizer.json` together.
The player automatically selects the candidate's platform, cue, six gains,
midpoint and archived global settings. It presents the archived candidate label
and recalculates anomalies using the normal player implementation.

The study CSV preserves all original input telemetry columns and adds `opt_*`
columns documenting processing, requests, permitted muscle lengths and anomaly
flags. Those audit columns are **not** fed back as telemetry. Gains, cues and
washout therefore are not applied twice. You can use the ordinary gain and
midpoint controls, loop regions and kinematic anomaly display for manual tuning.
Changing controls does not reapply the candidate defaults. Save a research
experiment to retain manual changes. Reopening the original candidate CSV
restores the optimiser's original candidate; load your saved experiment to
restore subsequent manual changes.

The companion includes source/output hashes, geometry/processing fingerprints
and a global-settings snapshot. Missing or modified CSV companions fail clearly.
If installed motion/geometry source changes, the player recalculates and warns
that the previous qualification no longer applies. Global settings remain frozen
for replay and manual tuning; neither the batch runner nor experiment loading
writes POC's operational INI.

Existing calibrated video offsets and state-event files are carried over.
Videos are linked by absolute path, not duplicated; video availability is not
required for motion analysis. Moving the studies to another PC may require
locating the original videos again. An uncalibrated OBS estimate is not relabelled
as a user calibration.

`candidate_variants.json` is a review/export document, **not** a replacement for
the live per-platform catalogue. After review, the player's existing **Save
current settings as a ride variant...** command can explicitly append a uniquely
named candidate. It refuses promotion if archived global settings differ from
the live INI, because a ride variant cannot store that global difference.
Align/re-evaluate those settings deliberately; the tool does not change them.
A park with a preferred variant will still hide additional candidates in normal
POC selection until that preference is deliberately edited.

## Reports and controls

`index.csv` provides paths and a compact comparison; `result.json` contains the
baseline, scores, actual parameters, qualification metrics and provenance.
Metrics include maximum excess, affected frames/duration, longest excursion,
workspace/clamp counts, floor clearance, peak muscle steps and velocity
statistics. These support review; durations are not an extra optimisation limit.

Optional controls:

| Option | Effect |
|---|---|
| `--platforms chair_v3` | Only Chair V3 (use `platform_v2` for MDX) |
| `--evaluations 1800` | Increase the approximate search budget |
| `--alternatives 0` | Best found setting only for each target |
| `--alternatives 2` | Up to two extra settings per target |
| `--neutral-min -50 --neutral-max 50` | Narrow the midpoint search |
| `--sample-frames 256` | Denser initial screening, still full-frame finalists |
| `--per-capture` | Independent settings per recording |
| `--include-grandnational` | Explicit offline research only |

Progress is printed per job/stage; no unsupported duration estimate is made.
Ctrl+C preserves completed candidates and marks the run interrupted. Results
are not resumed automatically. A subsequent run makes another directory.
A partial job/run has a nonzero process exit code and records its reason.

## Grand National and deployment boundary

Grand National is hidden from normal operator ride selection as previously
requested. Its configuration, variants, capture access and Sim Player research
access remain. The optimiser excludes it by default. Existing NL2 station-mode,
reset, dispatch and auxiliary-train logic are unchanged.

These are modelled kinematic candidates for review, not a physical-platform
safety approval. Tension-only force feasibility, measured muscle response,
dynamic load, rider comfort and real-world calibration remain outside this
optimiser. No live motion algorithm, actuator limit or pressure mapping is changed.
