<!-- README.md ver 6 -->
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
- `cfg_SuspendedChair.py` — the 800 mm suspended chair. Its current geometry is
  provisional and will be replaced with CAD-derived values.

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

POC sends calculated pressures through the Festo UDP interface. Simulator code
must not import kinematics or Festo modules directly.

The suspended chair uses `output/chair_DtoP_synthesized.csv`. The 1000 mm MDX
and Falcon configurations use `output/wheelchair_DtoP.csv`.
