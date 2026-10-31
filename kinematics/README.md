<!-- README.md ver 8 -->
# Kinematics

`kinematics_V3.py` implements the suspended-platform geometry used by the POC
`SopPlatformBackend`. Platform configs now provide fixed attachment XYZ and
moving-platform attachment XYZ coordinates. Three-point configs may still be
mirrored; six-point configs preserve measured asymmetry. Legacy XY moving points
are accepted temporarily and are treated as Z=0.

The intended configuration modules are:

- `cfg_SuspendedPlatformMdx.py` — current MDX suspended platform, 1000 mm muscles.
  Its six attachment pairs come from the current CAD export. CAD Z is shifted
  down by 84 mm so the nominal moving-attachment reference plane is local Z=0.
  The platform underside is local Z=-80, placing it 4 mm above the floor at the
  level/full-extension CAD position.
- `cfg_SuspendedChairFalcon.py` — Falcon geometry, also using 1000 mm muscles.
- `cfg_SuspendedChair.py` — the legacy `chair_v2` 800 mm suspended chair.
- `cfg_SuspendedChairV3.py` — the active `chair_v3` 800 mm chair, inheriting
  the same CAD attachment points but applying a separately adjustable neutral
  height bias (+100 mm for v181 tests). This changes **only** chair_v3, not
  chair_v2 or any 1000 mm platform.

`cfg_SuspendedChair1094.py` and `cfg_SuspendedPlatform.py` are obsolete names and
are no longer selected by POC. Existing `chair_v2` saved selector values are
kept compatible by mapping that selector to `cfg_SuspendedChair.py`.

The neutral reference-plane height is calculated automatically. Legacy configs
retain their clearance-offset sweep. The MDX platform instead uses
`MUSCLE_EXTENSION_RESERVE=60`: the lower level-platform boundary is calculated
from a 940 mm active muscle length (1140 mm total actuator), the upper boundary
from the 950 mm minimum total actuator length, and their midpoint is neutral.

MDX runtime motion normally uses `WORKSPACE_LIMIT_MODE="geometry"`. A compact
precomputed convex hull of the moving-platform underside is checked against a
15 mm floor-clearance requirement. Heave is corrected directly when floor
clearance alone is limiting. Physical actuator limits remain 950..1200 mm; only
poses that still exceed them use up to six bisection steps toward neutral. For
rapid fallback during platform testing, change the mode to `"reserve"` to enforce
950..1140 mm without the floor hull, or to `"off"` for the previous no-workspace-
limiter behaviour. The DXF is not parsed at runtime.

For `chair_v3` only, the normal legacy midpoint (216.5 mm Z) is raised by
`NEUTRAL_HEIGHT_OFFSET_MM = 100.0` to 316.5 mm. This moves the low-station
Blackbird pose to approximately 18% of the level-platform travel above its
lowest physically feasible height, with 763 mm longest muscle rather than
849 mm at the recorded opening frame. The Chair's physical range remains
600..800 mm. This is a **test tuning value**, not an assertion that every
recorded motion is feasible: workspace limiting remains off and final length
clamps still occur in around 13% of recorded frames. Other platforms retain
neutral-height offset 0. To change the trial value, edit
`kinematics/cfg_SuspendedChairV3.py`, `PlatformConfig.NEUTRAL_HEIGHT_OFFSET_MM`,
then restart the app. Never change `PLATFORM_CLEARANCE_OFFSET` to tune this
height: zero correctly means no floor collision on Chair V3.

POC sends calculated pressures through the Festo UDP interface. Simulator code
must not import kinematics or Festo modules directly.

The suspended chair uses `output/chair_DtoP_synthesized.csv`. The 1000 mm MDX
and Falcon configurations use `output/wheelchair_DtoP.csv`.

## v182: Chair V3 1-DOF limits and ride-only neutral adjustment

`cfg_SuspendedChairV3.py` sets `LIMITS_1DOF_TRANFORM` **inside**
`PlatformConfig.__init__` to `(90, 90, 100, radians(10), radians(8),
radians(9))`. The inherited Chair V2 configuration stores this attribute
on the instance; a class-level override alone would not change live output.
The separate v181 V3 platform baseline adjustment remains +100 mm.

The optional `neutral_height_delta_mm` in a platform-specific NL2 variant
adds a **ride-only physical vertical translation** after normalized
transform processing and before inverse kinematics. Zero leaves the current
platform midpoint unchanged. Parking and propping positions are not shifted.
Positive raises the platform; negative lowers it. A +/-100 mm experimental
range is additionally checked against feasible neutral actuator lengths and
floor clearance, and does not guarantee all dynamic poses are feasible.
See `docs/RIDE_NEUTRAL_HEIGHT_V182.md`.
