<!-- POC_DIGITAL_TWIN_UDP_HANDOVER_v2.md ver 1 -->
# POC Digital Twin UDP v2

Live POC and Ride Analyzer send complete UTF-8 JSON snapshots stamped `protocol: POC_TWIN` and `version: 2`. The destination, 20 Hz maximum cadence, sequence and timestamp fields use the existing UDP transport. The datagram limit is 1,400 bytes.

| Field | Meaning |
| --- | --- |
| `transform_convention` | `rider_xyz_rpy_v1` |
| `transform` | Six dimensionless motion cues in `[X, Y, Z, Roll, Pitch, Yaw]` order |
| `applied_transform_real` | Solved rider pose in mm and radians, or `null` when unavailable |
| `geometry_id` | SHA-256 of the shared reference model inputs, including ordered attachments, neutral-height and workspace geometry settings |
| `channel_to_pair` | Six entries indexed by valve channel. Each entry identifies the reference upper/lower attachment pair. |

X is forward, Y left, and Z up. Positive roll raises the left side, positive pitch lowers the nose, and positive yaw turns the nose left. Apply `Rz Ry Rx` to the reference pose and translate. The conversion to a receiver's scene axes belongs in the receiver. The earlier Twin v1 conventions are documented in [the v1 handover](POC_DIGITAL_TWIN_UDP_HANDOVER_v1.md).

Resolve `geometry_id` against the installed receiver model. Ground-Loading can appear as `wheelchair_platform` in live POC and `platform_v2` in replay. Both use the same geometry ID when they use the same reference model. Chair V3 appears as `chair_v3`. Both approved configurations use pair order `[0, 1, 2, 3, 4, 5]`, with right-side valve 0 on Ground-Loading and left-side valve 0 on Chair V3.

Keep `muscle_length_mm`, `contraction_pct` and `command_pressure_mbar` in packet channel order and use `channel_to_pair` to associate them with geometry. Pose estimates and commanded state are model outputs. A receiver should decline pose visualization for unknown protocol versions, convention IDs or geometry IDs. The physical commissioning and receiver update are separate from the sender's packet tests.
