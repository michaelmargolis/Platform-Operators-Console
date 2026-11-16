<!-- POC_DYNAMIC_RUNTIME_V201.md ver 2 -->
# POC dynamic ride runtime - v201 engineering integration

**Historical v201 engineering procedure.** For v202 and later, the supported
preparation/deployment route is **Ride Analyzer > Study > Dynamic rides - prepare
for POC...**, then **POC > Profile > Import dynamic candidate ZIP...**.
See `docs/POC_DYNAMIC_PREPARATION_V202.md`; the command-line promotion and
source-file-hash gates below are superseded.

**Status: engineering integration, NOT physically commissioned. Do not place the
new dynamic candidates into normal passenger service.**

This update extends the established 20 Hz POC NoLimits 2 control path to apply
six trajectory-matched, post-washout gain multipliers. It uses the existing
B+C matcher and production kinematics. Static variants, other simulators,
platform geometry, Festo output and SHS transport are unchanged. Both Chair V3
(`chair_v3`) and Ground-Loading Platform (`platform_v2`) are supported.
Blackbird was qualified/replayed first, followed by Mdx Logo Coaster (`mdxlogo`).

## Installation - each deliverable

| Deliverable | Action |
|---|---|
| `POC_v201_Dynamic_Ride_Runtime_ENGINEERING_delta.zip` | **Code.** Requires an existing complete checkout with prior updates applied through **POC v200 / Ride Analyzer v0.2.16**. Close POC and Ride Analyzer. Extract this ZIP **directly into that repository root**, merging the supplied paths. Back up the working per-platform ride catalogues and `telemetry_config.ini` first. The ZIP contains **no** runtime dynamic profile, study, candidate catalogue, local calibration, or live hardware configuration. |
| `POC_v201_Dynamic_Ride_Runtime_CHANGED_FILES.txt` | Reference-only inventory, **no installation**. |
| `POC_v201_Dynamic_Ride_Runtime_VERIFICATION.md` | Test evidence and limitations, **no installation**. |
| `docs/POC_DYNAMIC_RUNTIME_V201.md` | This operator/engineering guide, installed by the ZIP. |

The existing working ride catalogues remain in
`sims/nolimits2/ride_variants/<platform>.json` and are **not** overwritten by
this update. Each platform loads its own catalogue and its own profiles.

## What changes for a static ride?

Nothing intentional. With a selected static variant, POC does not allocate a
matcher or invoke the multiplier stage. A frozen v200 baseline was used to
verify identical static 20 Hz transform-command output at 0%, 40%, 65% and
100% intensity for both NL2 and SpaceCoaster. The default maximum intensity
remains **100%**. The new read-only **Profile** tab also appears for static
rides and reports `STATIC`.

## Development-to-commissioning workflow

1. Capture a full compatible, repeated dynamic-source timing study using Ride
   Analyzer; preserve all session companions. The tested Extended deployment
   capture had five repeats per ride with VR running.
2. Generate fresh **strict** candidate profiles for the target platform/park:
   `py -m tools.sim_player.dynamic_optimizer SESSION --rides blackbird --platforms both`.
   Generate Mdx Logo candidates separately with `--rides mdxlogo`. Inspect
   `report.html`, `validation.json`, all large muscle steps, stress results,
   fallback, and source fingerprint before promoting anything. Do not use a
   pre-generated profile from an older checkout if its source hashes differ.
3. Review each generated profile and its static 100% baseline and fallback in
   Ride Analyzer, ideally with the original synchronized video.
4. **Promote explicitly, with POC stopped and the correct target checkout**:

   ```bat
   py -m tools.nl2_dynamic_profiles.promote "studies\nl2\dynamic_profiles\profiles_<run>\chair_v3\blackbird\strict" --label "Blackbird [Dynamic A]"
   ```

   For Ground-Loading Platform, substitute `platform_v2`. For Mdx Logo Coaster,
   substitute the `mdxlogo` directory and a unique label beginning
   `Mdx Logo Coaster`. The promoter verifies source/processing compatibility,
   an independent held-out run, robust +/-100 ms timing, production parity,
   strict nominal/fallback qualification and runtime hashes. It creates a
   small immutable profile/reference/deployment manifest under
   `sims/nolimits2/ride_profiles/<platform>/<park>/<id>/` and **only after
   successful verification** appends a uniquely labelled `candidate` to the
   existing platform catalogue. It never makes the candidate preferred.
5. Restart POC, review the read-only **Profile** tab and persistent dynamic
   error count. Dynamic candidate loading validates the bundle, code/geometry/
   input fingerprints, platform, park key, fixed gains/cue/heave/washout and
   global settings. Unverifiable profiles are unavailable; an invalid
   preferred dynamic profile does **not** silently expose another candidate.
6. At dispatch POC checks NL2 version, the currently viewed train/car/seat,
   the expected departure station and confirmed base processing. It will not
   dispatch when these checks fail. During the ride the matcher tracks source
   XYZ and quaternion; temporary alignment loss blends towards the separately
   validated static fallback. Serious/uncompensated faults latch out the
   dynamic stage for the current ride and append to `logs/dynamic_errors.log`.
   The normal POC E-STOP, stale simulator telemetry and kinematics remain
   authoritative.
7. **Physical commissioning is mandatory** before selecting the candidate as
   `preferred`. Start at conservative intensity and assess real measured
   platform response, muscle velocity/pressure, force/tension feasibility,
   command-versus-actual displacement, comfort and fallback transitions.
   Requalify if the motion processing, relevant geometry, platform configuration
   or source ride changes. Only use `--max-intensity 150` deliberately for
   commissioning tests outside the original offline 100% qualification.

## Important hard limitations / release gates

- **Not physically safety-qualified.** Recorded muscle-length command steps
  exceed the typical pneumatic response used in earlier engineering estimates.
  Do not assume a mathematically feasible 20 Hz command is dynamically
  trackable. The update deliberately does **not** introduce undocumented
  smoothing or alter current platform limits.
- **Remote .nl2park file hash**: the research profile stores the qualified park
  SHA256 and POC confirms the park identity/version and viewed train/seat, but
  **this first integration does not independently read/hash the current NL2
  park file on the remote Windows PC**. Verify the exact park file on the
  deployment host before commissioning; automated SHS-side attestation is a
  remaining hard requirement before unattended/normal production approval.
- PyQt5 hardware/touch screen rendering, real NL2 TCP + SHS/Festo integration,
  physical frame-time scheduling, an intentionally mismatched park test and
  live loss-of-tracking behaviour still need Windows/Pi/rig smoke tests.
  Offline no-network replay is not a replacement for those tests.
- 250 ms profile-lag and 2 s sustained-loss latch are initial engineering
  thresholds, not proven comfort/safety tolerances. Actual physical validation
  may require narrower limits and new profile qualification.
- Unity multiplier/no-network parity alone does not validate non-unity force,
  tension, pneumatic latency or rider experience. No anticipatory absolute
  cues or pneumatic-lag feedforward have been added.
- Use the updated *full checkout* for profile generation. The promoter will
  reject profiles built against different frozen processing source hashes.
- A profile is part of the chosen candidate, **not** a separate normal-operator
  toggle. Dynamic settings cannot be edited in POC while its candidate is
  selected. Existing candidate/preferred/rejected rules still apply.

## Diagnostics and controls

Read-only **Profile** (between Platform and Debug) shows variant, base cue,
heave, fixed gains, ride neutral, current dynamic gains, profile phase, phase
lag, match basis, fallback state, loss count and any runtime dynamic fault.
**Debug** adds the persistent dynamic-error count. If errors exist at startup,
POC shows a closable information dialog. The append-only log is never cleared,
archived or rotated automatically. An operator/admin must review entries and
remove the file manually if appropriate.

Default intensity is 0..100%. The normal touch slider and physical encoder use
10% increments. A deliberate commissioning launch with
`python poc.py --max-intensity 150` extends both to 150%; it does not change
runtime hard kinematic limits, dynamic multipliers or offline qualification.

### Validation recommended before testing with connected pneumatic hardware

From the POC root on the development computer:

```bat
py -m pytest -q tests/test_dynamic_runtime_v201.py tests/test_dynamic_core_equivalence_v201.py
py -m pytest -q tests/test_ride_variants_v176.py tools/nl2_sync_benchmark/tests/test_matcher.py tools/nl2_dynamic_profiles/tests
py version_check.py
```

The historical archive reconstruction used for developing v201 had **four
pre-existing v165 pose-sender manifest mismatches** because that one historical
delta was unavailable. Those files were **not modified in v201**. The actual
current GitHub checkout should not inherit the reconstruction gap; review any
manifest mismatch before enabling dynamic candidates on hardware.
