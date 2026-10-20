<!-- POC_DIGITAL_TWIN_UDP_HANDOVER_v1.md ver 3 -->
# POC digital-twin UDP handover

## Purpose and scope

POC v121 introduced a one-way, best-effort UDP snapshot stream for a Unity or
other external digital twin; the protocol described here remains current. The
stream represents POC commands and calculated
state; it is not physical sensor feedback and must never be used as a safety
channel.

The twin receiver is intentionally independent of simulator-specific
telemetry. POC supplies the common processed transform and actuator values for
every supported simulator.

## Enabling the stream

The default configuration disables transmission:

```json
"digital_twin": {
  "address": "-",
  "port": 10020
}
```

Set `address` in `poc_config.json` to the digital-twin PC's IPv4 address. A
literal `"-"` means disabled. `port` defaults to UDP 10020 and can be changed
if required. Restart POC after editing the configuration.

The sender runs from POC's existing 50 ms data cycle, producing at most 20
datagrams per second. It is non-blocking, has no acknowledgements or retries,
and catches encoding and socket errors so a twin failure cannot interrupt
platform control.

## Wire format

Each UDP datagram is one complete UTF-8 JSON object. Whitespace is omitted and
the maximum encoded size is 1400 bytes so the packet remains below a normal
Ethernet MTU. JSON `NaN` and infinity are prohibited.

Example, expanded for readability:

```json
{
  "protocol": "POC_TWIN",
  "version": 1,
  "sequence": 18427,
  "timestamp_ms": 1789499212345,
  "platform": "wheelchair_platform",
  "platform_state": "ACTIVATED",
  "simulator_state": "RUNNING",
  "telemetry_valid": true,
  "transform_applied": true,
  "dtop_load_kg_per_muscle": 14.1666666667,
  "transform": [0.01234, -0.02145, 0.1042, 0.0021, -0.0184, 0.0],
  "muscle_length_mm": [731.0, 728.0, 742.0, 739.0, 725.0, 734.0],
  "contraction_pct": [34.5, 36.0, 29.0, 30.5, 37.5, 33.0],
  "command_pressure_mbar": [3210.0, 3375.0, 2780.0, 2910.0, 3480.0, 3095.0],
  "piston_disengaged": null
}
```

## Field semantics

| Field | Type and meaning |
|---|---|
| `protocol` | String, always `POC_TWIN`. Reject other datagrams. |
| `version` | Integer wire-schema version, currently 1. This is not the POC release number. |
| `sequence` | Unsigned 32-bit counter, starting at zero and wrapping after `4294967295`. It advances for each successfully handed-off datagram. |
| `timestamp_ms` | POC Unix time in milliseconds when the snapshot was encoded. Receiver arrival time should remain authoritative for stale-data detection. |
| `platform` | Configured platform/protocol name such as `wheelchair_platform` or `chair_v2`; select geometry using this exact value. |
| `platform_state` | `DEACTIVATED`, `ACTIVATING`, `ACTIVATED`, `DEACTIVATING` or `FAULT`. |
| `simulator_state` | POC common simulator state: `DISCONNECTED`, `READY`, `RUNNING`, `PAUSED` or `RESETTING`. |
| `telemetry_valid` | True when POC has valid current simulator telemetry. |
| `transform_applied` | True only when the platform is activated, the simulator is running and valid telemetry is being applied. |
| `dtop_load_kg_per_muscle` | Exact fractional supported load currently used by POC's D-to-P interpolation, in kg per muscle. It includes one sixth of the configured moving-platform tare. |
| `transform` | Six normalized, dimensionless POC kinematic inputs ordered `x, y, z, roll, pitch, yaw`, after gain, intensity and applicable washout/legacy processing. |
| `muscle_length_mm` | Six current commanded muscle lengths in millimetres, ordered muscles 0 through 5. |
| `contraction_pct` | Six contractions, where 0% is maximum muscle length and 100% is maximum configured contraction. |
| `command_pressure_mbar` | Six pressures commanded to the Festo interface in millibar. These are not measured pressures. |
| `piston_disengaged` | Boolean for platforms with a prop piston; `null` for platforms without one. True means the prop is commanded disengaged. |

All four six-element arrays use the POC platform configuration's muscle order
0 through 5. Do not reorder them in the transport layer.

## Interpreting simultaneous values

`transform` is the current processed simulator request. `muscle_length_mm` and
`command_pressure_mbar` are the current actuator commands. They deliberately
remain separate because they need not describe the same instantaneous pose:

- activation/deactivation ramps change muscle lengths progressively;
- prop release/engagement may precede muscle motion;
- processed simulator telemetry can remain available while not applied;
- a deactivated or paused platform can retain actuator values from a previous
  command.

For commanded-pose display, use `transform` only when `transform_applied` is
true. For the empirical pressure model and bounded forward solver, use
`command_pressure_mbar`, `dtop_load_kg_per_muscle`, prior receiver state and the
platform-specific model. `muscle_length_mm` is the POC-calculated reference
against which the estimated result can be compared.

## Unity receiver guidance

1. Bind a UDP receiver to the configured port on a background thread or async
   task. Never block Unity's main thread waiting for a packet.
2. Decode into a version-1 DTO with all fields above.
3. Validate protocol/version, finite numbers and exactly six elements in each
   actuator array.
4. Atomically replace a single latest-snapshot slot. Do not build a playback
   queue: delayed motion data is less useful than the newest state.
5. Apply the latest validated snapshot from `Update()` on Unity's main thread.
6. Use packet arrival time, not the remote Unix timestamp, to declare the
   connection stale after 250–500 ms.
7. On stale data, hold the last visual pose and show a visible `POC data stale`
   diagnostic. Do not extrapolate indefinitely.
8. Record missing, duplicate or out-of-order sequence values for diagnostics.
   UDP loss is acceptable; the next packet is a complete replacement.

Sequence wrap should be handled with unsigned modular comparison, or the
receiver may simply accept each newly arrived complete snapshot while logging
sequence discontinuities.

## Coordinate and model cautions

The transform is normalized POC input, not Unity world position or Euler
degrees. Use the matching POC platform limits and coordinate/sign conversion
before displaying a commanded pose. The platform name identifies the required
geometry, but the packet does not transfer attachment coordinates or D-to-P
tables.

The available `chair_v2` geometry is provisional and comes from the suspended
chair DXF. Physical coordinate measurement is still required. Keep the twin's
geometry/model revision explicit in its own configuration so a mismatch is
visible. The supplied POC default is `wheelchair_platform` (`platform_v2`).

## Acceptance tests for the receiver

- Disabled POC address produces no packets.
- Protocol/version mismatch is rejected cleanly.
- All four actuator arrays require exactly six finite values.
- Packet gaps and reordering do not produce queued or reverse-time motion.
- Stale indication appears within the selected timeout after POC stops.
- `platform_state` transitions remain visible while activating/deactivating.
- POC and twin agree on muscle 0–5 order, length, contraction and commanded
  pressure for captured packets.
- Paused and deactivated packets do not incorrectly apply `transform` as live
  motion.
- Stopping or disconnecting the twin has no observable effect on POC control.

## POC implementation locations

- `core/digital_twin.py`: protocol encoding, validation and non-blocking UDP.
- `core/poc_core.py`: snapshot assembly after the platform/fan work in each
  50 ms data cycle.
- `core/config_store.py` and `poc_config.json`: destination configuration.
- `tests/test_digital_twin.py`: schema, validation, error isolation and core
  field-mapping tests.
