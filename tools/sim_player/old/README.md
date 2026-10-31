# sim_player v5 — Qt 6 / FFmpeg video and telemetry playback

A standalone **offline research tool** for replaying NoLimits2 video and
recorded 20 Hz telemetry. This release is **not a POC update**. Install it as
`tools/sim_player/` inside the Platform Operators Console repository.

## Why v4 uses Qt 6

The Windows PyQt5 Multimedia backend displayed damaged H.264 pictures after
pausing, repeated seeking and slider dragging. The v3 workaround (briefly
playing after each paused seek) made matters worse. A **separate Qt 6/FFmpeg
seek test passed on the user's Windows PC**, including paused seeking,
backward seeking and slider dragging.

**Only `sim_player` uses PyQt6.** The POC GUI and all other POC applications
remain on PyQt5, completely unchanged. Do not migrate their imports or
uninstall PyQt5. Both bindings can coexist in an environment, provided each
application uses only one binding within its process.

## Install / run on Windows

Python 3.10+ and the existing POC dependencies (`numpy`, etc.) are required.
Install PyQt6 if it is not already available in the Python environment used
to launch the player:

```powershell
python -m pip install PyQt6
```

Extract the complete `sim_player_v5.zip` into the **POC repository root**,
overwriting `tools/sim_player/`. No other repository path is included.

From the POC repository root:

```powershell
python -m tools.sim_player
```

To open a particular ride:

```powershell
python -m tools.sim_player "captures/nl2_ride_study/Blackbird_repeat01_20261026_134740.csv"
```

Use **File → Open capture** or Ctrl+O to select another telemetry CSV.
The file picker hides `_state_events.csv` files. The associated MP4/MKV,
events and timing metadata are located automatically when available.

## What is unchanged

* Common timeline, Play/Pause, ±5 s, recorded-sample stepping and 0.25–2× speed.
* Clickable ride-state markers and **resizable / floating / closable** 6DOF
  and six-muscle-length dock panels. View → Show all / Restore default layout.
* Raw vs. processed 6DOF display, recorded timestamp sampling, nonuniform
  sample intervals and numeric data loading without a video.
* Reuse of **existing POC offline analysis functions** for the six modeled
  muscle lengths, workspace limiting, and applied/requested poses. No copy
  of the kinematics or washout implementation was introduced.
* Video offset calibration stored in a separate `_sim_player_sync.json` file;
  the original telemetry CSV and capture video are never modified.
* No NL2 live connection, Festo, SHS, platform activation or Twin UDP output.

## Qt 6 video operation (introduced in v4)

The Qt 6 multimedia implementation is isolated in `video_backend.py`:

* Uses PyQt6 QMediaPlayer + QVideoWidget with the **FFmpeg** backend.
* On opening a file, the decoder runs **once** to render its first actual
  frame, then pauses. It seeks to the initial video offset while paused.
* A Play-after-Pause resumes normal playback **without forcing a new seek**.
* Explicit timeline seeks—including while paused—use `setPosition()` directly.
  There are **no more play/pause pulses to force a frame refresh**.
* During slider dragging, seeks are limited to about **8 per second**;
  releasing the slider always sends the selected final position. Telemetry
  values update on every slider movement.
* During normal playback, only *sustained large* video-clock drift triggers
  a correction; small differences do not force decoder resets.
* The backend exposes **decoded frame presentation timestamps** from
  `QVideoSink` for future synchronization diagnostics.

This is a migration of the complete `sim_player` GUI to PyQt6, because
**Qt5 and Qt6 widgets cannot safely be mixed in one Python process**.
The production POC remains unmodified and retains PyQt5.

## v5 Qt 6 API compatibility fixes

* Corrected File → Open (Ctrl+O) to use
  `QKeySequence(QKeySequence.StandardKey.Open)`, not the removed Qt 5-style `QKeySequence.Open`.
* Replaced `QMouseEvent.x()` with `QMouseEvent.position().x()` in the ride
  marker, timeline click and slider drag handlers. These Qt 5-style
  event methods may be unavailable in PyQt6.
* Audited all remaining Qt classes/enums and multimedia calls in `app.py`
  and `video_backend.py`. No changes to the video backend or numeric replay
  are required for these API corrections.
* Extended the Qt 6 GUI smoke tests to exercise the Open shortcut and actual
  mouse interactions. These tests require PyQt6 at runtime; when it is not
  available, the static API regression test still runs.

## Limitations / interpretation

* Video-to-telemetry synchronization still requires calibrating the OBS
  start-time estimate. The target remains within 2–3 video frames; it has
  not yet been independently measured for all captures.
* H.264 decoding is handled by Qt6/FFmpeg, which worked in the prior
  Windows seek prototype. The integrated GUI still needs a Windows test.
* Six muscle lengths are **modeled commands**, not measured physical travel.
  The default analysis uses `platform_v2`, recorded heave, 100% intensity,
  and currently saved POC gains/washout. These may not equal the settings
  actually active during the original capture.
* Raw captures and all sidecar files are left unchanged.
* Strip charts, Digital Twin UDP replay, configurable motion algorithms,
  physical actuator-response studies and the tension feasibility solver
  remain future development work. Each is supported by the existing
  per-frame `analysis` data kept in `recording.py`.

## Architecture

```
tools/sim_player/
  __main__.py              launch the offline player
  app.py                   PyQt6 controls, docks, timeline, video coordination
  video_backend.py         Qt6 / FFmpeg video decoder and displayed-frame timestamps
  video_sync.py            Qt-independent seek/drift and CSV filename policy
  recording.py             Qt-independent file and POC analysis adapter
  tests/                   numerical, timeline, integration and optional Qt6 smoke tests
```

Both `recording.py` and `video_sync.py` remain independent of Qt and physical
outputs. Later tension-feasibility processing can use `PlaybackFrame.analysis`
without modifying the video backend.

## Tests

From the POC repository root:

```powershell
python -m pytest -q tools/sim_player/tests
```

Headless tests cover Qt6 API compatibility, recording association, nonuniform timestamps, modeled
muscle calculation, new pause/seek policy, continuous dragging and no extra
play/pause decoder pulses. The optional Qt6 GUI smoke test runs when PyQt6 is
installed; actual Windows video rendering requires a user-side test.
