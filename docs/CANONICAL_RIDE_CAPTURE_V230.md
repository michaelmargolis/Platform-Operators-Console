<!-- CANONICAL_RIDE_CAPTURE_V230.md ver 2 -->
# Local canonical NoLimits ride capture

POC v230 adds an occasional-use Windows utility for creating the Ride Analyzer's reusable canonical ride videos directly on the SHS / NoLimits PC. It does not extend the SHS network protocol and does not start POC, Festo, Twin or physical-platform output.

## Purpose

The utility creates **one canonical reference per physical NoLimits ride**, not one per motion platform or variant. It reads the working `chair_v3` and `platform_v2` ride-variant catalogues, takes the union of every referenced `park`, and captures each physical park at most once. Multiple static/dynamic variants and the same park appearing on both platforms therefore do not duplicate video capture.

A valid existing canonical reference is skipped by default. An invalid/missing reference is captured. `--replace` deliberately captures an existing reference again; the canonical library retains the previous immutable media directory and moves only `reference.json` to the new import.

## Before capture

1. Use the complete POC checkout on the SHS / NoLimits Windows PC.
2. Start NoLimits 2 with its telemetry/control API enabled on localhost port 15151.
3. Run NoLimits in normal **desktop/non-VR** mode. The utility checks NL2 telemetry state bit `0x08` and refuses live capture when VR is active unless `--allow-vr` is deliberately supplied. It does not turn VR on or off itself.
4. Start OBS Studio with WebSocket Server enabled and the existing `NL2 Game Capture` source (or supply another source name with `--obs-source`). The optional `obsws-python` package used by the existing NL2 OBS recorder must be installed.
5. Close POC and every other program controlling the NL2 API. SHS itself may remain running; the utility does not use or modify the SHS command protocol.

If OBS uses a WebSocket password, prefer setting `OBS_WEBSOCKET_PASSWORD` in the shell rather than putting it on the command line.


## Configure NoLimits 2 and OBS for canonical capture

Canonical capture is intended to produce a clear engineering reference video without creating unnecessarily large files. The recommended standard setup is **1280 x 720 at 30 fps**, with NL2 running in desktop/non-VR mode.

### NoLimits 2

Before starting the batch:

1. Run NL2 in **desktop/non-VR** mode. The capture utility checks the NL2 VR-state bit and refuses capture while VR is active unless `--allow-vr` is explicitly supplied.
2. Set the NL2 render/window size to approximately **1280 x 720**. This is large enough for Ride Analyzer review while substantially reducing file size and decoding load compared with 1920 x 1080.
3. Keep the NL2 window visible and in the same normal foreground/window state throughout the batch. Do not minimize it during one ride and restore it for another.
4. Use the intended ride view/train/seat consistently. Canonical video is shared across platforms and motion variants, so there is no reason to create separate Chair V3 and Ground-Loading Platform videos.
5. Avoid changing graphics, view, park or station controls manually once the batch has started.

The NL2 window does not have to fill the physical monitor; the important requirement is that the OBS source captures the intended NL2 image consistently.

### OBS Studio

Recommended OBS video settings:

```text
Base (Canvas) Resolution:   1280 x 720
Output (Scaled) Resolution: 1280 x 720
Common FPS Value:           30
Encoder:                    H.264 hardware encoder when available
Rate control:               quality-based / constant-quality mode
Recording source:           NL2 window/game capture only
```

Thirty frames per second is sufficient for Ride Analyzer: telemetry is recorded at approximately 20 Hz, so 60 fps normally increases storage and encode/decode load without providing much additional engineering value. Keep OBS output at the same 1280 x 720 resolution as NL2 where practical so no extra scaling is required.

Use a moderate/high quality setting rather than an unnecessarily high fixed bitrate. Exact encoder names and quality scales vary by GPU/OBS version; the goal is a clean 720p reference image without visually obvious compression artifacts. Audio is not required by Ride Analyzer and may be disabled unless it is useful as an additional ride reference.

The existing capture code controls **recording start/stop** through OBS WebSocket. OBS WebSocket also exposes `GetVideoSettings` and `SetVideoSettings`; the latter can set FPS and canvas/output resolution, including `fpsNumerator`/`fpsDenominator`. However, OBS refuses video-setting changes while an output is active. The canonical capture utility therefore deliberately **does not modify the OBS profile's FPS or resolution**. Configure these once in OBS before the batch and leave them unchanged during capture.

Also check before capture:

- **Tools -> WebSocket Server Settings**: WebSocket server enabled;
- the configured source (normally `NL2 Game Capture`) shows the NL2 image in the OBS preview;
- no unwanted desktop, notification or diagnostic overlays are visible in that source;
- the recording directory has enough free disk space for the complete batch;
- OBS is not already recording or streaming when the utility is started.

If using a WebSocket password, set `OBS_WEBSOCKET_PASSWORD` in the shell as described below.

## Run

From the repository root:

```bat
py tools\create_canonical_rides.py --list
py tools\create_canonical_rides.py
```

Or double-click:

```text
tools\run_canonical_ride_capture.bat
```

The normal invocation lists the unique rides and whether each canonical reference is `existing`, `missing` or `invalid`, then asks once before starting the missing/invalid set.

Useful explicit operations:

```bat
py tools\create_canonical_rides.py --rides blackbird,fenrir
py tools\create_canonical_rides.py --rides "Flying Beast" --replace
```

`--replace` is required to recapture a valid existing canonical reference. `--yes` suppresses the final confirmation for an intentional unattended run.

## Per-ride sequence

For each ride, the utility creates a fresh local NL2 adapter connection and uses the existing production ride loading/reset/station/dispatch state machine. This includes current multi-station handling and Flying Beast seat locking; no separate dispatch implementation is introduced.

The sequence is:

1. load and confirm the selected park;
2. prepare the viewed train/station until the existing adapter reports ready;
3. **wait 5 seconds while continuing to service NL2**, so the brief NoLimits help/park caption can clear;
4. start OBS and confirm recording is active;
5. hold the normal short OBS pre-roll (default 0.75 s) while NL2 remains serviced;
6. dispatch using the existing NL2 adapter;
7. set telemetry elapsed time zero after dispatch confirmation and record the complete ride at approximately 20 Hz;
8. stop OBS after return to READY, preserving the existing short video tail;
9. register the exact telemetry/video pair into the canonical library.

Original captures are retained under:

```text
captures/canonical_source/
```

Canonical media is stored using the established Ride Analyzer structure:

```text
tools/sim_player/canonical_rides/<park_key>/
  reference.json
  media_<timestamp>_<id>/
    video.<obs-container>
    trajectory.npz
    import_manifest.json
    source/
      <capture>.csv
      <capture>_state_events.csv
      <capture>_video_sync.json
```


## Moving canonical media to another Analyzer PC

The utility writes the canonical library into the POC checkout on the SHS / NoLimits PC. If Ride Analyzer is normally run from a different computer, copy each complete generated ride directory under `tools/sim_player/canonical_rides/<park_key>/` to the corresponding `canonical_rides` directory on that computer. Preserve `reference.json` and the complete referenced `media_*` directory; do not copy only the video file.

## Automatic video/telemetry alignment

The existing OBS recorder records local monotonic timestamps for OBS StartRecord acknowledgement and telemetry elapsed-time zero. The canonical utility writes the corresponding single calibration anchor automatically, for example:

```text
telemetry 0.000 s -> video 1.420 s
```

Starting OBS **before** telemetry zero gives the canonical video a small pre-roll instead of the previous situation where video could begin several seconds after telemetry. Ride Analyzer then applies that positive offset automatically; the MP4/MKV is not physically trimmed.

This is still an OBS/WebSocket-clock estimate, not a measurement of the presentation timestamp of the first encoded video frame. The import is therefore marked as automatically estimated rather than user-verified. A brief visual spot-check of a recognizable early event is recommended after a new batch. Existing Ride Analyzer calibration controls remain available if a measured correction is needed.

## Failure/retry behaviour

A failed ride is reported and the utility continues to the next ride. Successful canonical references already created remain registered. Incomplete capture/video evidence remains in `captures/canonical_source` for diagnosis and is not registered as canonical media. Rerun later for the failed ride; existing successful rides will then be skipped automatically.

The utility never deletes old canonical media, ride captures, variant catalogues or OBS files outside the capture directory.
