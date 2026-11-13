<!-- NL2_MULTI_STATION_MANUAL_V189.md ver 1 -->
# POC v189 — NL2 multi-station manual-mode and auxiliary dispatch policy

## Evidence and objective

The operator's 5 November 2026 controlled harness test found Flying Beast can
only dispatch with BOTH station 0 and station 1 in manual mode. With station 1
in automatic mode, station 0 remains `0x00001C12` (not dispatchable); with both
manual it reaches `0x00001C16` after approximately 9 seconds (CAN_DISPATCH).
This reproduces the POC/harness discrepancy without requiring park edits.

The additional operating rule is **no routine dispatch of unwatched trains**.
Only an unwatched train physically reported inside the *selected train's pinned
departure station* may be dispatched, and then only if there is evidence the
selected train cannot return because it is stopped near that station.

## Implementation

- `sims/nolimits2/station_modes.py`: bounded non-blocking discovery of stations
  on the VIEWED coaster, starting at station index 0. A request-ID-correlated
  NL2 `STATION_STATE` proves a particular index exists; the first explicit
  invalid-station-index `ERROR` after station 0 terminates the list. Generic
  `ERROR` messages or missing replies are not treated
  as evidence of a nonexistent station. At most 16 stations are considered;
  uncertain discovery blocks dispatch rather than assuming all are known.
- For each confirmed station, POC sends `SET_MANUAL_MODE True` only if a recent
  identified station status reports AUTO. It reads back `MANUAL` from a fresh
  station response; an OK reply alone is insufficient. It requires verified
  manual mode on all discovered stations before RESET_PARK and before dispatch.
  RESET_PARK invalidates the discovery; POC repeats it after resetting.
- `sims/nolimits2/sim.py` reuses the existing safety gates (current viewed
  train present/stopped, fresh telemetry, pinned departure station, operator
  Start, flyer lock ACK and CAN_DISPATCH). The two-round viewed-station reset
  probe is used for every discovered multi-station coaster, not just a
  hard-coded list in `ride_config.py`.
- Existing one-station parks discover only station 0. The implementation does
  not edit or consult park files to determine the station count, and does not
  send manual-mode commands to other coasters in a multi-coaster park.

### Auxiliary train policy

POC never sends an auxiliary DISPATCH solely because an unwatched train is
`CAN_DISPATCH` at a station. It requires ALL of:

1. Operator-initiated primary DISPATCH already sent, authorisation not revoked.
2. Viewed train has moved and left its pinned departure station.
3. Viewed train currently stationary, with an ongoing station-approach probe
   lasting at least 3 seconds.
4. A fresh `GET_CURRENT_COASTER_AND_NEAREST_STATION` reply, obtained after the
   train stopped, identifies exactly the **pinned departure station**.
5. A fresh station reply shows **another train** occupying that station,
   station MANUAL and `CAN_DISPATCH` available.
6. No pending pause, and no auxiliary dispatch for this occupancy until an
   explicitly observed EMPTY station re-arms it.

This is cautious obstruction evidence, not proof of block occupancy: NL2's
telemetry API does not expose internal block ownership or obstruction causes.
No train at an unrelated station is dispatched. The operator may need to
intervene if these conditions cannot be confirmed.

## Field test (simulator first; physical platform isolated)

1. Overlay v189 on a COMPLETE, working POC v188 tree. Close the standalone NL2
   test harness; do not connect two controllers to the same NL2 server.
2. Disable motion output / isolate the physical platform. Load Flying Beast,
   activate and observe new `station_discovered`, `station_manual_requested`,
   `station_manual_confirmed`, and `station_discovery_complete` events.
3. Verify station (0,0) and station (0,1) are both MANUAL on NL2 control panel.
   Activate/reset the park once and confirm both are re-established as MANUAL.
4. With the VIEWED train at its confirmed station, press Start once. Confirm
   only one flyer lock command (raw 0), its correlated ACK, CAN_DISPATCH after
   approximately 9 seconds, then a single DISPATCH to the viewed train station.
5. Verify no auxiliary train at station 1 is dispatched by POC. With a multi-
   train ride circulating, verify an unwatched train at the departure station
   remains stopped while the viewed train is moving normally.
6. If the viewed train stops on approach to that SAME occupied station, POC may
   send one auxiliary dispatch only after the 3-second obstruction evidence.
   Confirm no further dispatch until an EMPTY-station observation.
7. Test a single-station park; ensure only station 0 is set manual, with no
   unexpected dispatches. Then test another two-station park if available.
8. If status reports `station_manual_setup_failed`, DO NOT bypass the gate;
   provide a debug log and the matching NL2 station replies for investigation.

## Known limitations

- The published NL2 telemetry API exposes no `GET_STATION_COUNT`. Discovery
  relies on contiguous zero-based station indices and an explicit invalid-index
  `ERROR` for the first nonexistent station. The protocol documentation does not promise which error
  is returned for an invalid station; this must be field-verified on the
  deployed NL2 version. A missing reply, unexpected valid index >=16, or
  failure to confirm MANUAL causes a safe halt (no dispatch).
- The API cannot tell exactly why a station cannot dispatch or which track
  block a train occupies. The cautious 3-second/nearest-station obstruction
  test may miss some genuine blockages; it intentionally avoids speculative
  automatic dispatch of unrelated trains.
- This update has been tested only with fake NL2 clients and offline Python
  tests. It has not been exercised against actual NL2 or physical actuators.
