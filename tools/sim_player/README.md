<!-- README.md ver 12 -->
# MDX Ride Analyzer

Current workspace version: **v0.2.24**. Dynamic ride preparation is now a guided, state-driven workflow: the workspace shows Source / Generate / Review / Prepare / Export progress, recommends qualified Strict profiles first, presents one clear next action, and keeps source recordings, reports, regeneration, dismissal and optimiser logs as secondary controls.

**Current workspace:** v0.2.24

**MDX Ride Analyzer** is the offline ride-development and commissioning application for recorded NoLimits motion data.

The internal Python package remains `tools/sim_player` for compatibility with existing scripts, settings, saved reviews and data schemas.

Run it from the POC repository root:

```bat
py -m tools.sim_player
```

## Main workflows

The application is organised around three analysis workflows:

- **Review** - inspect motion, video/plots, muscle lengths, anomalies and candidate A/B differences.
- **Tune** - experiment offline with gains, ride neutral height, angular cue and translational washout.
- **Validate** - review calculated kinematic feasibility, synchronisation and provenance.

The main menu is:

**File / Open / Study / View / Advanced / Twin / Help**

## Dynamic studies and source recordings

Use **Study > Capture dynamic source...** to create the repeated NoLimits recordings used to generate and validate dynamic profiles.

A **dynamic source recording** is one measured ride run. A **dynamic study** is the complete capture session containing multiple dynamic source recordings together with the timing, lifecycle and provenance files required by profile generation.

The Ride Analyzer launches the existing `tools.nl2_timing_study` capture implementation in a child process; it does not duplicate or bypass the established NL2 loading, dispatch or recording logic. The human-facing workflow uses dynamic-study terminology because these recordings are being acquired as source evidence for dynamic variants.

By default Ride Analyzer dynamic studies are stored below:

```text
captures/dynamic_source/
```

The normal player-compatible telemetry CSV from a completed trial can be opened directly in Ride Analyzer. Keep the complete dynamic-study directory for profile generation; `_timing.csv`, `_trial.json` and the other companion files contain required synchronisation/provenance evidence and are not ordinary ride captures.

Dynamic-source capture does **not** use the currently selected motion Candidate. It records the simulator trajectory so the same source study can later support different base variants and profile-generation experiments.

For qualification, create dynamic source recordings on a **deployment PC with VR active**. VR-off capture may be useful for later testing, but it is not yet treated as an equivalent qualification condition. **Standard study** uses every current operator-available, non-hidden NoLimits ride with three measured recordings per ride; **Extended study** uses the same ride set with five measured recordings per ride. **Quick study** remains the short Blackbird/Fenrir/Tilt Coaster set. Scenic Run is retired from normal POC/Ride Analyzer selection and is not included.

The dialog shows an **Estimated capture time** calculated from typical ride durations and the requested recording count. Installed canonical ride trajectories are used when available; existing timing-study duration hints provide a fallback. The remaining estimate is refreshed at the start of each recording. Loading/reset/retry delays and optional uncertainty-extension recordings are not included.

The capture dialog provides **Test conditions (optional)** for descriptive provenance. It is prefilled with **Normal deployed configuration** plus the local CPU and physical GPU names when they can be detected; edit the text when the test configuration is unusual. VR state itself is recorded automatically by the underlying capture tool. The completed session also records the Ride Analyzer study scope (Current, Custom, Quick, Standard or Extended). Monitor count remains available only in the low-level timing-study CLI and is not part of the Ride Analyzer dynamic-source workflow.

Normal analysis/replay does not connect to live NoLimits, SHS or Festo. **Study > Capture dynamic source...** is the deliberate exception: it connects to the local NoLimits API for the duration of capture. The capture workflow does not record video.

## Prepare dynamic rides for POC (v0.2.24)

Use **Study > Dynamic rides - prepare for POC...**. This is the documented
operator-facing preparation route: **choose a complete study** (best-quality
studies first), select Blackbird or Mdx Logo Coaster, select one or both
platforms, and optionally play one of the recordings. Recordings are sorted
Suggested / Good / Caution / Unrated / Avoid; the selected individual CSV is
**not** the optimisation reference selection. The optimiser chooses reference,
fit and independent held-out recordings from the complete study itself.

The workspace is intentionally state-driven rather than a row of equally weighted
engineering buttons. A progress strip shows **Source / Generate / Review / Prepare /
Export** for the selected candidate, and the **Next step** panel presents one large
primary action. Source recordings are collapsed under **Source recordings
(optional)** because they are for inspection, not reference selection. Generation
progress is also collapsed after a successful run and expands automatically when
generation is running or fails.

After generation, Analyzer prefers a qualified **Strict** candidate for each
platform. **Relaxed 5%** is recommended only when no qualifying Strict candidate
is available. Alternative and dismissed candidates remain selectable for
engineering work. **View generation report**, **Regenerate profiles...** and
**Dismiss candidate** are secondary actions rather than competing next steps.

If a candidate has already been reviewed, reopening the workspace keeps that
review state and advances the primary action to **Prepare for POC...**. A
secondary **Review profile again** action remains available so the engineer can
reopen the selected profile at any time without moving the workflow backwards.

**Show generation summary** is useful after the generation process has ended or
a workspace has been reopened. It shows the generated-study path, report path and
candidate/review/preparation state. New generations also retain optimiser stdout
in `analyzer_generation_output.txt` inside the generated study; older studies
without that file still show the derived summary and point to the HTML report.

Choose **Review selected profile** to open normal Analyzer playback. When you use
**Return to dynamic study**, that candidate is remembered as reviewed and the
primary action advances to **Prepare for POC...**. Preparation validates Analyzer's
nominal + independent-heldout + static-fallback evidence against the platform's
fixed 5% requested muscle-range envelope and applicable floor clearance, then
builds one immutable ZIP in `exports/dynamic_candidates/`. The workspace then
shows the prepared package and changes the primary action to **Open package
folder**; **Copy folder path** is available alongside it. A large muscle-length
step remains a ride-quality metric, not an automatic rejection. Qualification is
not physical commissioning.

Transfer the ZIP to the target POC and use **Profile > Import dynamic candidate
ZIP...**, while deactivated and fully parked. POC verifies the package and
Analyzer qualification report, adds only the named candidate to the selected
platform's catalogue, and requests a restart. **Do not overwrite the entire
working POC catalogue.** POC may warn about changed code revision, NL2
version or frozen settings instead of rejecting a complete, qualified bundle.
Malformed, missing, corrupt, wrong-platform or wrong-ride packages are still
rejected; POC retains its per-frame physical workspace/output protection.

For Ground-Loading Platform, **Profile > Adjust floor clearance...** changes
the persistent minimum operating underside-to-floor clearance between 5 mm and
75 mm (default 15 mm). This applies to static and dynamic rides, only while
fully parked/deactivated, and does not move the platform directly or alter CAD
geometry or parking. Verify actual physical clearance after any adjustment.
Refer to `docs/POC_DYNAMIC_PREPARATION_V202.md` and the updated POC manual.

### Static and dynamic candidate analysis

- Static optimiser results can be opened as independent A/B working copies and tuned without modifying the source study.
- Dynamic profiles can be compared with another profile, their unity-gain static baseline or independently validated fallback.
- Dynamic multiplier curves are still read-only in Analyzer. To change them, regenerate a candidate from the source study.
- Creating/exporting a candidate does not make it preferred or physically commissioned in POC.

## Reference video

Use **Open > Ride reference video...** to register one reusable reference video for a ride. The video is trajectory-mapped to compatible captures and candidates. Imported ride media remain under `tools/sim_player/canonical_rides/<ride key>/` and are not committed to the code repository.

## Output and safety boundary

Optional **Twin** output sends replay poses to the Visualizer with zero command pressures. Ride Analyzer does not use Twin output as a substitute for POC commissioning.

Offline kinematic checks do not establish comfort, pneumatic response, tension-only feasibility or physical safety. Candidates intended for operation on a physical platform still require the normal POC commissioning and review process.

## Help

**Help > Workspace help**, the information icon and **F1** open the same tabbed help window. Right-click **Review**, **Tune** or **Validate** for a concise workflow guide.


### Reopen a dynamic-source recording
Use **Open > Open dynamic source...** to browse completed studies. The picker starts at `captures/dynamic_source` and displays only primary recording CSVs whose names end in `_timing_YYYYMMDDTHHMMSS_<capture-id>.csv`; companion CSVs such as polls, protocol, state events and timing diagnostics are hidden. Hover a recording for a non-blocking suitability hint: **Suggested** (clean representative selected by the study analysis), **Good**, **Caution**, **Avoid**, or **Unrated**. The tooltip explains the evidence used. These are engineering source-selection hints, not physical qualification or deployment approval.
