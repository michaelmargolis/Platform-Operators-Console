<!-- README.md ver 1 -->
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
