## v0.2.10 UI refinement
Inspector section title buttons are now visually attached to their section frames; inspector dock titles use `Inspector : Review/Tune/Validate`.

## v0.2.9 UI refinement
Compact inspector title and adaptive ride/candidate summary.

# Sim Player Task Workspace v0.2.7

Offline POC capture, candidate and dynamic-profile review with one contextual
Inspector, Video / Plot / Overlay, and a reusable reference video for each ride.

## Install and run

With v0.2.6 already installed, close Sim Player and extract
**Sim_Player_Task_Workspace_v0_2_7_delta_root_ready.zip** directly into your
**existing POC repository root**, replacing the included `tools/sim_player/`
files. **Sim_Player_Task_Workspace_v0_2_7_full.zip** is an alternative complete
player package, not another required step. Neither ZIP is a complete POC checkout.
No need to reinstall existing POC, B+C v0.1.4, Phase 1 or candidate packages.

Run from that root:

```bat
py -m tools.sim_player
```

## v0.2.7 presentation/discoverability changes

- Shared plot palette now uses a brighter red for Pitch (`#ff4d4d`) and yellow
  for Yaw (`#ffe34f`). Plot, Overlay, muscle-series and dynamic-multiplier plots
  all use the same central axis palette.
- Review / Tune / Validate are grouped in a faint framed **Workflow** control so
  they read as one decision rather than three unrelated buttons.
- Each workflow button has a task-specific hover hint. Right-clicking the button
  opens a compact workflow guide: one purpose sentence followed by the high-level
  process expected of the user. The context menu is intentionally local: workflow
  help plus the full Workspace help, not panel/layout actions.
- The full information-icon / F1 tabbed help remains unchanged as the detailed
  reference. No new permanent instruction panel was added.
- v0.2.6 canonical lead-in wording and Windows Overlay composition fixes are
  retained unchanged. No motion, matching, timeline or physical-output logic changed.

Open > Ride reference video... asks for the video only. It requires a CSV with the
same base filename in the same directory (for example `ride.mp4` + `ride.csv`) and
populates the original telemetry field automatically. Missing companion telemetry is
reported immediately. The pair is then imported once. The player copies them, events and calibration into
`tools/sim_player/canonical_rides/<ride key>/`. Later compatible captures and
profiles reuse that video automatically. No source file is moved or edited.

See [CANONICAL_VIDEO_GUIDE.md](CANONICAL_VIDEO_GUIDE.md) for the exact registration,
calibration, identity checks, saved-study mapping and missing-coverage behaviour.
No video files or personal media manifests are distributed in the code ZIP.

## Existing workflows retained

- Open a raw capture, static optimiser candidates, or dynamic profiles.
- Review / Tune / Validate share the same playhead and loop. Video, Plot and a
  transparent lower-video Overlay remain alternative views, not split panes.
- Review any two available compatible candidates; A/B working copies retain edits.
- Inspect full anomaly durations, source/processed transform bars, and muscle
  lengths with red out-of-range extrema, fixed rulers and hover details.
- Static tuning supports base gains, neutral height, cue and offline Surge/Sway
  washout. Dynamic profiles remain read-only; their source/kinematic checks remain.
- Save static trials, experiments, notes and candidate/profile reviews through
  their existing explicit actions. A video change does not promote a variant.
- Optional Twin output remains user-controlled and never sends Festo commands.
- Help > Workspace help, the information icon and F1 open the same tabbed guide.
- Advanced keeps the original detailed panels; no controls are silently replaced.

Reference video changes presentation only. It does not alter playback's motion
clock, source samples, filters, kinematics, gain profiles or live POC files.

[Workspace guide](WORKSPACE_GUIDE.md) | [Panel restoration audit](WORKSPACE_PANEL_AUDIT.md)
| [Verification](WORKSPACE_VERIFICATION.md) | [Changed files](WORKSPACE_CHANGED_FILES.txt)
| [Release history](changelog.txt)

The older PROFILE_REVIEW_* and WORKSPACE_UX_V021 documents are release-specific
history. Where they describe missing shared media or older toolbar layouts,
this README and the current workspace/canonical guides supersede them.
