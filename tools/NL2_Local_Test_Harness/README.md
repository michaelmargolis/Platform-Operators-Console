<!-- README.md ver 2 -->
# NoLimits 2 Local Protocol Console

A **general-purpose TCP telemetry/control test harness**, distributed with POC
but run **independently** of its operator console, ride state machine, Qt UI,
SHS, Festo devices and physical GPIO. It supports all NoLimits 2 parks; the
flyer-seat buttons are only one of its station-control functions.

- **Default connection:** `127.0.0.1:15151` on the Windows PC running NL2.
- **Runtime:** Python 3.9+ and standard-library Tkinter; no pip packages.
- **Code sharing:** `harness_core.py` imports
  `sims.nolimits2.nl2_protocol.NL2Protocol` from the surrounding POC repository,
  rather than maintaining a second protocol implementation.
- **No autonomous control:** connection and automatic polling are read-only;
  manual mode, restraints, dispatch and arbitrary messages only send after an
  explicit button click. The control checkbox starts **off**.
- **No settings changes:** no writes to POC's normal configuration or variant
  catalogue. Logs are written inside the tool's `logs/` directory.

## Run on Windows

1. **Stop POC** and disable/disconnect the **physical motion platform**.
   Connect to a separate local NL2 simulation session. Using the same running
   NL2 instance as a motion controller can cause unexpected platform motion.
2. In NL2 enable **Telemetry Server** (Setup > Others or `--telemetry`),
   normally on port 15151. Load any park and start NL2 play mode.
3. From the POC directory, either double-click
   `tools/nl2_local_test_harness/run_harness.bat`, or run:

   ```powershell
   py -3 -m tools.nl2_local_test_harness
   ```

   Alternatively open `tools/nl2_local_test_harness` and run `py -3 harness.py`.
4. Click **Connect**. Version, viewed-train telemetry, nearest station and two
   chosen station indices are polled automatically. No control command is
   transmitted on connect.
5. To send a command, select **Target coaster** and **Target station**. Check
   **Enable control commands** and click a station button. Dispatch and other
   high-impact commands require an additional confirmation.
6. Examine the chronological TX/RX log, request IDs, raw station flags, and
   corresponding changes in the NL2 control-panel state. Logs are saved in
   `logs/nl2_harness_YYYYMMDD_HHMMSS.log`.

The two station watchers default to indices 0 and 1. If the park has one
station, set both watchers to 0. Click a station table row to select it as
the **control target**, or enter another station index manually. The nearest
station is not necessarily the station occupied by the **viewed train**.

## Controls and requests

| Function | Behaviour |
| --- | --- |
| Station display | Decodes 13 NL2 station status flags, showing age/staleness |
| Telemetry display | Viewed train/coaster, frame, speed, play/pause |
| Read-only commands | Version, telemetry, nearest station, coaster count |
| Manual mode | Explicit on (1) and off (0) |
| Flyer seats | **Lock seats (0)**; **Unlock seats (1)** using message 21 |
| Gates | Close (0), Open (1) |
| Harness | Close (0), Open (1) |
| Platform | Raise (0), Lower (1) |
| Dispatch | One request (message 17) to the selected coaster/station |
| Raw command | Editable client message ID and hex payload, explicitly sent |

All station controls take a selected coaster/station and, where relevant,
a one-byte control value. The **raw** command editor does *not* follow the
selected station automatically; encode the desired station in its payload.
Server-reply message IDs cannot be sent as requests. Unknown message IDs are
allowed only after confirmation. Malformed payloads may be rejected by NL2.

### Confirmed flyer-seat semantics (4 November 2026)

In an actual **Flying Beast / NL2 2.6.4.0** experiment, `SET_FLYER_CAR` (message
21) with **raw value 0** locked the seats; the coaster became dispatchable a
few seconds later. This corrects the earlier incorrect POC assumption that
raw 1 meant lock. The inverse, **value 1**, is labelled Unlock in the harness,
but unlocking has **not yet been independently validated live**. Do not
operate either button on an occupied/moving train without confirming its
position and the correct operating procedure.

For coaster 0, station 0, payload format is big-endian signed int32 coaster,
big-endian signed int32 station, and one byte for state:

| Message | Payload (hex) |
| --- | --- |
| Read station state (14) | `00 00 00 00 00 00 00 00` |
| Flyer LOCK (21) | `00 00 00 00 00 00 00 00 00` |
| Flyer UNLOCK (21) | `00 00 00 00 00 00 00 00 01` |
| DISPATCH (17) | `00 00 00 00 00 00 00 00` |

The official NL2 specification labels message 21's raw flag as *off/on*, not
*lock/unlock*. The mapping above is grounded in operator testing; use the
observed station status and NL2 visual state as confirmation. An API `OK`
means a request was accepted, **not** that seats moved or a train departed.

## Tests

Run from the harness folder:

```powershell
py -3 -m unittest discover -s tests -v
```

Or use `run_tests.bat`. Tests start a fake NL2 TCP server on localhost and
verify framing, request/result correlation, status decode, read-only startup,
command bytes and production-protocol equivalence. They require neither NL2 nor
physical equipment. The GUI also has been smoke-tested against that fake server.

No code from third-party telemetry projects is included. This harness uses
POC's own `nl2_protocol.py` **without copying it**.

Official protocol reference:
https://nolimitscoaster.com/nolimits2/help/pages/telemetry.html

## Dispatch readiness investigation (harness diagnostic update)

**Goal:** determine whether a long delay after flyer-seat lock is associated
with station-interlock availability, another train, or the control sequence.
This is separate from the POC runtime and never dispatches automatically.

1. **Stop POC and keep the physical platform isolated.** Load Flying Beast
   in a local NL2 session. Enable NL2's telemetry server and connect the harness.
2. Select coaster **0**, watch station **0** and station **1**, and check that
   the viewed train is in the intended station. Do not infer train count from
   station count.
3. Click **Start capture**. Both station states and the viewed train's
   telemetry are now polled at approximately 4 Hz, with request IDs recorded.
   Change snapshots, raw status flags, telemetry samples and command replies
   are preserved in a timestamped CSV.
4. If necessary, explicitly enable control commands and set **Manual mode On**
   for the appropriate station. Allow baseline observations before changing
   anything. Click **Lock seats (0)** once, only when safe and in station.
   Observe how long NL2's panel and API take to report dispatch availability.
5. Click **NL2 panel READY**, **NL2 panel NOT READY**, or **Other train moves**
   when you see those external events. They are *operator observations*, not
   facts reported by the telemetry API. Add notes for other events.
6. When `CAN_DISPATCH` is true, use the existing explicit Dispatch control
   if desired and appropriate. The harness will NOT dispatch on readiness.
7. Let the capture continue until readiness has appeared/disappeared more
   than once or the train has completed the relevant station sequence.
   Click **Stop and summarise** to save two files under `logs/`:
   `nl2_dispatch_...csv` with full observations and `..._summary.txt` with
   relative timing of lock, first observed flyer availability, dispatch
   readiness and human markers. Share both files for analysis.

**Controlled comparison:** repeat an equivalent test with one train instead
of multiple trains, if the park's configuration permits, without changing
other station settings. If single-train readiness occurs quickly but
multi-train readiness is delayed until another train moves, a block conflict
becomes a strong hypothesis. It still needs corroboration from NL2's visible
train locations or other data.

**What the API cannot tell us:** it does not provide per-block occupancy,
train positions for every train simultaneously, internal safety interlock
reasons, or reliable physical seat-lock sensor readings. Only the *viewed*
train's telemetry is available; station `CAN_*` bits describe command
availability. The harness therefore cannot guarantee to identify the
internal cause merely from status bits. Avoid interpreting `OK` as proof
that locking completed. Do not automatically clear interlocks or retry
control commands.
