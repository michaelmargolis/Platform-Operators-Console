<!-- README.md ver 4 -->
# Maintenance and diagnostic tools

This directory contains standalone utilities that are useful for commissioning
or diagnosis but are not imported by the POC runtime.

- `diagnostics/`: Raspberry Pi, SHS and SpaceCoaster test utilities.
- `spacecoaster_patch/`: source and compiled DLL for the patched SpaceCoaster
  telemetry/control integration.

The W5100S Festo emulator remains under `output/fstlib/firmware/` because it is
maintained alongside the EasyIP implementation it emulates.

## NoLimits2 ride-state monitor (v170)

`capture_nl2_rides.py` uses the same station/dispatch state machine as POC and
can show the event sequence needed to diagnose multi-train rides. The utility
**connects to and controls the real NL2 simulator**, but does not instantiate
POC or send any Festo/platform commands. Do not run it concurrently with POC.

From the repository root:

```bash
python tools/capture_nl2_rides.py --list
python tools/capture_nl2_rides.py --rides 9 --repeats 1 --state-monitor --host 192.168.0.4
```

`--rides` accepts the usual 1-based ride numbers or exact names. The monitor
loads the park, prepares manual station control, dispatches the ridden train,
then prints only state transitions and station-flag changes. It saves a
`*_state_events.csv` containing timestamps, events, station identity and flags.
Normal captures also save this sidecar in addition to the unchanged ride CSV.

Key events: `view_train_dispatched`, `view_train_departed`,
`aux_dispatch_sent`, `aux_station_cleared`, `view_train_returned` and `state`.
The monitor is **not** a passive observer: it issues NL2 park load and dispatch
commands. For passive observation of POC's own decisions, use the adapter's
`NL2_STATE_DEBUG` logging and avoid running a second NL2 controller.

Auxiliary train dispatch is generic for any multi-train park. It is authorised
only within a POC-dispatched ride, after the ridden train is confirmed to have
left its pinned departure station, and only when an unrelated train is present
and NL2 reports `CAN_DISPATCH`. A station-empty observation is required before
another auxiliary dispatch; no automatic retry is issued for a blocked train.

## Optional OBS video with NL2 ride captures

The same capture utility can record OBS video alongside each telemetry/state
capture. This is **Windows-only** and **opt-in**; no OBS import or connection
occurs for ordinary captures. It does not start POC, SHS or Festo output.

Prerequisites: OBS Studio running with **Tools > WebSocket Server Settings**
enabled (OBS 28+), the `obsws-python` package (`pip install obsws-python`),
and an existing OBS **Window Capture or Game Capture** source targeting NL2.
The source's *name* is arbitrary: pass its exact name using `--obs-source`.
The script dynamically updates its `window` setting to the loaded park's
render window (`NL3D_main_*` class); it searches all monitors and does not
hard-code the window GUID. Check the OBS preview on the first run; WebSocket
cannot verify image contents.

From the repository root, for example:

```powershell
$env:OBS_WEBSOCKET_PASSWORD = "<your OBS WebSocket password>"
python tools/capture_nl2_rides.py --rides Blackbird --repeats 1 --record-video --obs-source "NL2 Game Capture"
python tools/capture_nl2_rides.py --rides "Blackbird,Mdx Logo Coaster" --repeats 2 --record-video
```

The defaults are WebSocket host `127.0.0.1`, port `4455`, 0.35 seconds
pre-dispatch video lead-in and 0.5 seconds post-completion video tail.
Overrides: `--obs-host`, `--obs-port`, `--obs-password`, `--obs-source`,
`--obs-window-timeout`, `--obs-lead`, and `--obs-tail`. Prefer the environment
variable to exposing the password in process command-line arguments.

Before dispatching, the tool waits for the park to load and manual station
readiness, then sets the source window and OBS recording directory, starts
recording, and holds a short lead-in while NL2 service continues. It then
issues the normal NL2 dispatch. On return to READY it stops recording, gives
the video the **same stem** as the capture CSV, and saves a
`*_video_sync.json` with OBS start/stop request and acknowledgement clocks,
station-event clock zero, dispatch request and capture clock zero. Its video
extension is determined by OBS's actual output container (e.g. `.mp4` or
`.mkv`); the original OBS recording path is returned by StopRecord. The
previous OBS recording directory is restored when the capture session ends.

**Video-to-telemetry synchronisation is initially an estimate**. The JSON times
are recorded using Python's monotonic clock and UTC and include the approximate
telemetry-zero time after OBS StartRecord acknowledgement. An OBS WebSocket
acknowledgement is not the presentation timestamp (PTS) of the first encoded
video frame, so this is not a claim of frame-accurate calibration. The v230
canonical capture utility uses that estimate as the initial canonical timing
anchor because it starts OBS before telemetry zero; Ride Analyzer can still
apply a measured visual correction later. Do not assume the first video frame
equals the first CSV row.

If a ride errors or is interrupted, the recorder attempts to stop OBS and
writes an incomplete manifest. It refuses to take over an existing OBS
recording or overwrite an existing ride video. A source/OBS setup error before
dispatch prevents dispatch entirely. The original `--state-monitor` option
still works; with `--record-video` it writes a video and state-event CSV but
no raw telemetry CSV. Recording may affect NL2 graphics performance and should
be checked on the target Windows machine.

## Canonical NoLimits ride capture (v230)

`create_canonical_rides.py` is an occasional-use Windows utility for the SHS /
NoLimits PC. It reads the working Chair V3 and Ground-Loading Platform variant
catalogues, takes the union of their referenced physical parks, skips rides with
a valid canonical reference, captures each missing ride once with the existing
NL2/OBS recorder, and registers the matching telemetry/video pair directly into
`tools/sim_player/canonical_rides/`. It never starts POC or platform output.

Run NoLimits in desktop/non-VR mode, start OBS with WebSocket enabled, and close
other NL2 API clients. The utility refuses VR-active telemetry by default. After
station readiness it waits 5 seconds **before OBS starts** so the brief NL2 caption
is excluded, then uses a short video pre-roll before dispatch. The existing OBS
monotonic timing manifest supplies an automatic telemetry-zero -> video-time
anchor, avoiding the usual several-second missing-video lead at the start.

From the repository root:

```bat
py tools\create_canonical_rides.py --list
py tools\create_canonical_rides.py
```

Or double-click `tools\run_canonical_ride_capture.bat`. Original captures are
retained under `captures/canonical_source/`; valid canonical references are not
replaced unless `--replace` is supplied. See
`docs/CANONICAL_RIDE_CAPTURE_V230.md` for the complete workflow and options.
