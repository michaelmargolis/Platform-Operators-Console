# Sim Player v0.2.7 - reusable ride reference videos

## What to do with each download

| File | Action |
|---|---|
| `Sim_Player_Task_Workspace_v0_2_7_delta_root_ready.zip` | Recommended for your installed v0.2.6. Close Sim Player and extract directly into your **existing POC repository root**, replacing the included `tools/sim_player/...` files. |
| `Sim_Player_Task_Workspace_v0_2_7_full.zip` | Alternative to the delta, not an additional step. Extract directly into the same POC root. Contains the complete player, not a complete POC installation. |
| `Sim_Player_Task_Workspace_v0_2_7_Guide.md` | Reference only; no installation. The same workspace guide is included at `tools/sim_player/WORKSPACE_GUIDE.md`. |
| `Sim_Player_Task_Workspace_v0_2_7_Verification.md` | Reference only; no installation. Included as `tools/sim_player/WORKSPACE_VERIFICATION.md`. |

**Existing dependencies, not instructions to reinstall:** your working POC,
profile-aware player, Phase 1 dynamic profiles and B+C v0.1.4. No new dependency.
Existing candidate archives, captures, video files and variant catalogues stay
where they are. No existing media reference or calibration is included in a
release ZIP, so extracting an update does not replace yours.

Start normally, from the existing repository root:

```bat
py -m tools.sim_player
```

## Register a ride once

1. Choose **Open > Ride reference video...** and select the ride.
2. Browse to the **video only**. Sim Player looks for a telemetry CSV with exactly
   the same base filename in the same directory: `ride.mp4` requires `ride.csv`.
   The Original telemetry CSV field is read-only and is populated automatically.
   If the matching CSV is absent, registration stops and reports the expected file.
3. The initial video offset is then read from an existing `_sim_player_sync.json`
   when present, otherwise from the existing capture timing metadata as an estimate.
   Positive offset means video time is ahead of CSV elapsed time.
4. Click **Import as this ride's reference video**. There is no separate
   "same video/telemetry run" checkbox: deterministic same-basename pairing is the
   registration rule. Import copies the files in a worker thread; originals are not
   moved, edited or deleted.
5. Open a raw capture, static candidate or dynamic profile as usual. The registered
   reference is used automatically.

The setup page no longer asks you to claim that an offset has been visually checked.
Newly estimated offsets remain explicitly **estimated**. After import, use the
**Calibration** tab to compare a visible video event with telemetry and tick
**I have checked this calibration against a visible video event** only when that
check has actually been made. Saving without the checkbox keeps the calibration
marked estimated. An existing saved visual calibration remains marked checked.

**No new video capture is needed just because you regenerate motion profiles or
record another telemetry repetition.** Videos without the same-basename original
telemetry cannot be registered automatically by this feature.

## Calibration

The mapping is:

`selected recording progress -> canonical CSV time -> calibrated video time`

The first link uses B+C XYZ/orientation matching. The second link must correspond
to the original video. It cannot be discovered from a video without observing a
recognisable event.

In the reference dialog's **Calibration** tab:

- One row `[CSV elapsed seconds, video timestamp seconds]` establishes a constant
  offset. For example, `[12.0, 13.5]` means video is 1.5 seconds ahead of CSV time.
- Multiple checked anchors define a piecewise-linear clock mapping. Both columns
  must increase. Use additional anchors only for measured recording-clock drift.
- **Add event anchor** inserts the original event's telemetry time and an estimated
  video time. Inspect the video and replace that estimate with the actual visible
  event timestamp. Event-file and telemetry-clock zeroes are aligned using the
  existing player logic; their raw elapsed columns are not assumed identical.
- **Save calibration** updates only the canonical media manifest. The visual-check checkbox controls whether the saved calibration is marked verified or estimated. It
  never changes input telemetry, profiles or live POC settings.

For an original pair not previously calibrated, opening that CSV normally with
**View > Use ride reference video** off allows the existing Advanced video-offset
controls to help establish the offset before import. They remain available for
ordinary capture-linked video. When canonical media is active, calibrate in the
reference dialog instead; otherwise the selected capture's different clock could
be applied a second time.

The setup operation pauses playback deliberately. Opening the tabbed Help does
not pause it. Saved reviews reopen against the currently registered ride media;
the review's candidate settings are not rewritten by a media change.

## What happens during review

- The existing selected-capture clock still drives playback, plots, loops, filters
  and optional Twin output. Reference video never drives or alters motion data.
- Video / Plot / Overlay use the same mapped video. No split layout is added.
  Overlay mirrors the already-decoded frame into a normal QWidget before drawing
  the selected plot, avoiding native QVideoWidget z-order problems on Windows.
- Video speed follows the local precomputed mapping; ordinary decoder seeking is
  still debounced. User seeks and loop restarts use the exact mapped target.
- A/B on the same source recording reuses the alignment. Changing gains, washout,
  neutral height or platform does not duplicate the reference video.
- Reference alignment is computed outside the playback tick and cached in memory.
  It is not continually re-run out of order when dragging the timeline.
- **View > Use ride reference video** checked enables shared-media reuse. Uncheck
  it to restore the source recording's own video if one exists. No files are removed.

### Saved dynamic studies

The supplied Phase 1 candidates already contain a checksum-bound reference
trajectory and recorded B+C progress. The player maps that reference trajectory
to the canonical one, then uses the recorded progress to look up video time.
This supports the existing candidate ZIP without requiring its original timing
session to be copied to the player PC.

That route is **indirect**: archived target progress is reused, not independently
recomputed from raw target XYZ. The status tooltip states this. Raw reconstructed
reviews can use their recorded matched progress instead of the archived slewed
lookup where that information is available. The motion/profile compatibility
check remains unchanged and is not weakened by media support.

## Uncertainty and incompatible data

- Known park-content, departure-station or viewed train/car/seat/view differences
  block reuse. Desktop media can accompany a compatible VR capture; VR mode and
  physical platform are not media-identity gates.
- Legacy CSVs may lack park hashes or seat identity. This limitation is reported;
  the original-pair confirmation and trajectory checks are then essential.
- At least 90% of moving trajectory samples must match to accept a mapping. This
  is a media-consistency screen, not a guarantee of visual frame accuracy.
- No global nearest-track search is introduced. Existing B+C local-window,
  orientation, stationary-clock and bounded recovery logic is reused unchanged.
- Stationary intervals are explicitly labelled **timing estimates**. Small
  stationary differences do not provide independent phase evidence.
- If the reference has a known usable first frame later than telemetry zero, the
  player reports **Reference video available - begins at mm:ss.s** and shows a
  **Video starts in x.x s** countdown. This is not reported as a failed match.
- Genuine missing/failed matches and gaps longer than 250 ms do not become
  apparently valid interpolated video. Later gaps are labelled **Reference video
  temporarily unavailable - telemetry alignment gap** while telemetry and plots
  continue. A reference video ending early is reported separately.
- Incompatible route coverage produces **New video capture required**, or asks
  you to select the correct original pair. A compatible original capture-linked
  video remains available as a fallback.

## Legend correction

The main Plot and Overlay views now have colour-matched line swatches and labels.
A single axis keeps its colour from the six-axis plot. Source-stage dashed lines
and solid processed lines are distinguished. A/B keeps solid active versus dashed
comparison curves. The optional six-lane gain plot uses the same axis palette.
Long legends wrap instead of silently losing their last entries.

## Scope and first check

No motion algorithms, washout calculations, kinematics, physical-output code,
profile data, candidate catalogues or B+C algorithms were changed.

Register your existing Mdx Logo Coaster video and its own capture, then open one
of the Mdx Logo dynamic candidates. Check a distinctive event near the start and
another near the end, switch A/B, and try a loop with Video and Overlay. Repeat
with Blackbird. A stable temporal mapping still needs this visual calibration
check; the automatic route tests cannot inspect the content of your actual video.
