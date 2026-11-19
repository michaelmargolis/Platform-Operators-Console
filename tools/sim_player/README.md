<!-- README.md ver 17 -->
# MDX Ride Analyzer

Current workspace version: **v0.2.28**. Dynamic ride preparation is a three-stage wizard: Source / Generate / Review & Export. Source sessions and Ride rows show retained generation/review/export state, selected platforms show the prior profile-set date/name, and a compatible existing result is an explicit **Use existing profile(s)** choice alongside **Generate new profiles**. Generation owns its live progress/abort controls, and reviewed platform profiles can be named and exported directly for POC.

**Current workspace:** v0.2.28

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

## Dynamic-source sessions and recordings

Use **Study > Capture dynamic source...** to create the repeated NoLimits recordings used to generate and validate dynamic profiles.

A **dynamic-source recording** is one measured ride run. A **dynamic-source session** is one `session_*` folder containing repeated recordings together with the timing, lifecycle and provenance files required by profile generation. In the UI and current documentation, **capture** refers to the act of acquiring a session; it is not the name of the saved data object.

The Ride Analyzer launches the existing `tools.nl2_timing_study` capture implementation in a child process; it does not duplicate or bypass the established NL2 loading, dispatch or recording logic. The human-facing workflow uses **session** for one `session_*` capture directory and **recording** for one completed ride repeat. The word **study** is reserved for optimiser/research artifacts and the Study menu category.

By default Ride Analyzer dynamic-source sessions are stored below:

```text
captures/dynamic_source/
```

The normal player-compatible telemetry CSV from a completed trial can be opened directly in Ride Analyzer. Keep the complete dynamic-source session directory for profile generation; `_timing.csv`, `_trial.json` and the other companion files contain required synchronisation/provenance evidence and are not standalone playback recordings.

Dynamic-source capture does **not** use the currently selected motion Candidate. It records the simulator trajectory so the same source session can later support different base variants and profile-generation experiments.

For qualification, create dynamic source recordings on a **deployment PC with VR active**. VR-off capture may be useful for later testing, but it is not yet treated as an equivalent qualification condition. **Standard** capture scope uses every current operator-available, non-hidden NoLimits ride with three measured recordings per ride; **Extended** uses the same ride set with five measured recordings per ride. **Quick** remains the short Blackbird/Fenrir/Tilt Coaster set. Scenic Run is retired from normal POC/Ride Analyzer selection and is not included.

The dialog shows an **Estimated capture time** calculated from typical ride durations and the requested recording count. Installed canonical ride trajectories are used when available; existing timing-study duration hints provide a fallback. The remaining estimate is refreshed at the start of each recording. Loading/reset/retry delays and optional uncertainty-extension recordings are not included.

The capture dialog provides **Test conditions (optional)** for descriptive provenance. It is prefilled with **Normal deployed configuration** plus the local CPU and physical GPU names when they can be detected; edit the text when the test configuration is unusual. VR state itself is recorded automatically by the underlying capture tool. The completed session also records the Ride Analyzer capture scope (Current, Custom, Quick, Standard or Extended). Monitor count remains available only in the low-level timing-study CLI and is not part of the Ride Analyzer dynamic-source workflow.

Normal analysis/replay does not connect to live NoLimits, SHS or Festo. **Study > Capture dynamic source...** is the deliberate exception: it connects to the local NoLimits API for the duration of capture. The capture workflow does not record video.

## Prepare dynamic rides for POC (v0.2.28)

Use **Study > Dynamic rides - prepare for POC...**. The normal workflow is a
three-page wizard rather than one all-at-once control surface:

1. **Source** starts at `captures/dynamic_source`. The Session dropdown lists
   direct `session_*` folders only. Its row/tooltip shows captured rides plus
   retained Partial / Generated / Reviewed / Exported state derived from
   `studies/nl2/dynamic_profiles`. Selecting a session rebuilds Ride from data
   actually present in that session. Choose **Chair V3**, **Ground-Loading
   Platform**, or both with checkboxes. Existing completed work is resumed when
   possible; the wizard does not require regeneration merely because another
   session/ride was used most recently. Source recording playback remains an
   optional inspection tool and never chooses the optimiser reference.
2. **Generate** runs the existing offline optimiser as a managed process. The
   live optimiser output is always visible on this page and **Abort generation**
   stops the child process. Incomplete runs cannot be exported. On success the
   wizard moves automatically to Review & Export. If a previous complete result
   exists, a failed regeneration does not delete it.
3. **Review & Export** shows one section for each selected physical platform.
   Analyzer still recommends qualified **Strict** first and **Relaxed 5%** only
   when no qualifying Strict candidate is available. **Review profile** opens
   normal Video / Plot / Overlay playback. After **Return to dynamic
   preparation**, that exact profile is marked reviewed, its POC profile name
   becomes editable, and **Export for POC** is enabled. Export performs the same
   qualification checks as the former Prepare step and writes one immutable ZIP
   under `exports/dynamic_candidates/`. Existing generation report, regeneration,
   alternative-candidate and dismiss/restore controls remain secondary
   engineering actions.

Export naming defaults to the first unused `Ride [Dynamic X]` label found in
the Analyzer export folder. A duplicate platform/ride label already present in
that folder is rejected before export. Export is engineering qualification, not
physical commissioning.

On POC, **Profile > Import dynamic candidate ZIP...** opens at
`exports/dynamic_candidates`, shows only valid packages for the POC's currently
selected physical platform, and hides candidates already present in that
platform catalogue. Filtering uses the package manifest (platform, candidate
label and deployment path), not the ZIP filename. Import remains available only
while fully deactivated and parked. A successful append-only import refreshes
the NoLimits ride catalogue live; a normal POC restart is not required.

For Ground-Loading Platform, **Profile > Adjust floor clearance...** changes
the persistent minimum operating underside-to-floor clearance between 5 mm and
75 mm (default 15 mm). This applies to static and dynamic rides, only while
fully parked/deactivated, and does not move the platform directly or alter CAD
geometry or parking. Verify actual physical clearance after any adjustment.
Refer to `docs/POC_DYNAMIC_PREPARATION_V202.md` for the complete current path.

### Static and dynamic candidate analysis

- Static optimiser results can be opened as independent A/B working copies and tuned without modifying the source optimiser data.
- Dynamic profiles can be compared with another profile, their unity-gain static baseline or independently validated fallback.
- Dynamic multiplier curves are still read-only in Analyzer. To change them, regenerate a candidate from the source session.
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
