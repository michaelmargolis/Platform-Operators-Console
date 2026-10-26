<!-- README.md ver 2 -->
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
