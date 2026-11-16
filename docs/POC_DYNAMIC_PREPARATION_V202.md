<!-- POC_DYNAMIC_PREPARATION_V202.md ver 1 -->
# Dynamic ride preparation and POC import - v202 engineering workflow

**Engineering release, not physically commissioned.** Supports Blackbird and
Mdx Logo Coaster on Chair V3 and Ground-Loading Platform. Installing code
alone does not add a selectable dynamic ride or edit any working catalogue.

## Install and prerequisites

Requires an existing complete checkout with prior updates applied through
POC v201 (which itself requires prior v200 updates). Close POC and Analyzer.
Extract **POC_v202_Dynamic_Ride_Workflow_ENGINEERING_delta.zip directly at the
root of the existing POC repository**, preserving the repository-relative paths.
No separate code ZIP is required for each platform. Keep existing working
`sims/nolimits2/ride_variants/<platform>.json` files and
`sims/nolimits2/telemetry_config.ini` intact. The release adds no profiles,
variant catalogue data, user local config or captures.

The accompanying **CHANGED_FILES.txt** and **VERIFICATION.md** are reference
only, not files to extract separately. The updated POC user manual DOCX is
included under `docs/` for human reference; no installation in POC is needed.

## Analyzer workflow (no command-line preparation)

1. Open **Study > Dynamic rides - prepare for POC...**. For a new study choose
   **Capture new study...**; otherwise select an existing completed dynamic
   source session. Sessions with fewer Avoid/Caution recordings and more
   Suggested/Good recordings sort first. Do not copy an isolated CSV: retain
   the complete source session and its timing/provenance companions.
2. Select the ride **Blackbird** first, then **Mdx Logo Coaster** for the second
   integration test. Select **Both platforms** or either individually.
3. Recording rows are sorted Suggested / Good / Caution / Unrated / Avoid and
   include explanatory tooltips. **Play selected recording** is optional.
   Analyzer automatically chooses the reference and independent validation
   runs from the entire study; selecting a playback CSV does not override it.
4. Select **Generate dynamic profiles**. Analyzer launches the existing optimiser
   as a managed child process and displays its progress. Cancelling cannot
   create a qualified finished package. Generated candidates are saved under
   `studies/nl2/dynamic_profiles/profiles_<timestamp>_<id>/`.
5. Open the generated HTML report. Select a strict or relaxed5 candidate, use
   **Review selected profile** and the usual Video/Plot/Overlay + Review and
   Validate tools, then select **Return to dynamic study** to continue.
   **Dismiss / restore** only hides or re-shows a research profile; it does not
   delete its evidence or installed POC variants.
6. When satisfied, select **Prepare for POC...** and provide a unique variant
   name following the existing ride label, such as
   `Blackbird [Dynamic A]`. Analyzer independently reads the complete
   recorded nominal and static fallback evidence, including the held-out
   recording. The required 5% margin is computed from the selected platform's
   configured muscle contraction stroke (e.g. 10 mm for 200 mm Chair V3 and
   12.5 mm for 250 mm Ground-Loading), while actual output remains in nominal
   bounds. Applicable floor clearance remains required. **Large commanded
   muscle steps are reported as ride-quality evidence, not automatically
   rejected for acceleration.** Offline validity is not passenger approval.
7. Analyzer writes an immutable ZIP to `exports/dynamic_candidates/` containing
   `dynamic_candidate.json`, and a repository-relative bundle directory with
   `profile.json`, `reference.npz`, `qualification.json`, `deployment.json`.
   On completion it displays the package and bundle path, copies the export
   **folder** path to clipboard and can open that folder. Copy the package ZIP
   to the target POC; do **not** copy the development PC's variant catalogue.

## POC import, selection and commissioning

1. Stop any physical motion: **deactivate and fully park** the target platform.
   Back up the platform's working ride catalogue before commissioning.
2. Transfer the exported candidate ZIP to a directory accessible on the POC
   device, e.g. `/home/poc/imports/`. Start POC with the intended physical
   platform selected. Open **Profile > Import dynamic candidate ZIP...**,
   select the transferred ZIP and confirm.
3. POC checks archive paths/sizes, the immutable profile and reference,
   SHA256 integrity, bound Analyzer qualification evidence, target platform,
   ride identity, valid fallback and a unique candidate label. Missing,
   malformed, corrupt, wrong-platform or unqualified bundles are rejected
   without changing the working catalogue. Compatible source-code byte
   hashes and NL2 build versions are provenance, not rejection gates.
   **There is no second kinematic optimiser/replay inside POC in v202.**
4. The import creates the runtime bundle and atomically adds one `candidate`
   entry to this physical platform's catalogue. No working catalogue is
   overwritten. **Restart POC** to reload candidate availability.
5. Existing preferred/candidate/rejected visibility rules still apply. If a
   preferred static candidate exists, the new candidate may be hidden until
   the commissioning review status is reconciled while parked. Do not delete
   a commissioned preferred variant merely to expose a new research candidate.
6. With physical output isolated, confirm NL2 dispatch and B+C tracking,
   multiplier values, fallback and the persistent dynamic error log. After
   the remaining physical verification gates, commission at conservative
   intensity on the intended platform and examine actual pneumatic response.
   Only then use Review mode to designate the candidate preferred.

## Ground-Loading Platform - operating minimum clearance

POC **Profile > Adjust floor clearance...** is visible only when Ground-Loading
Platform is selected. It adjusts the **minimum permitted underside-to-floor
clearance during active rides**, for static and dynamic rides alike. The default
is 15 mm and the deliberately bounded commissioning adjustment is **5-75 mm**.
Only change it while the platform is fully **deactivated and parked**. POC
requires confirmation, checks that the current ride neutral is feasible and
persists the result in `poc_config.json` under
`ground_loading_min_clearance_mm`. The adjustment affects the existing
runtime workspace limiter; it does **not** change CAD geometry, transform
origin, actuator lengths in the parked position or the parking behaviour.

Raise the setting to demand more clearance, or lower it when inspection of
physical geometry demonstrates adequate room. Before dispatch after any
change, inspect ground clearance across the intended platform movement and
confirm no unintended workspace limiting. Do not lower the setting merely to
avoid an undesirable software clamp without physically confirming clearance.
The current displayed setting is shown in the Profile tab. If an attempted
setting is outside 5-75 mm or invalid for the current neutral height, POC
rejects it and retains the previous value.

## Acceptance scope and limitations

- Exact Python source-file bytes and NL2 minor patch versions do **not**
  control profile selection; integrity and bounded physical processing do.
- The base ride processing, geometry and active runtime controls remain in
  POC; the stored profile shapes the existing motion and cannot generate
  signals from zero source motion. Configuration differences are logged.
- The existing per-20-Hz-tick workspace, final muscle-length limiting, floor
  clearance and E-STOP/simulator validity controls remain active.
- The offline report verifies measured recordings, not every possible future
  simulator trajectory, force/tension equilibrium, physical actuator tracking,
  ride realism or passenger comfort. Physical commissioning remains required.
- The Pi/Windows native UI and connected-hardware response are not tested by
  this offline release; see the verification report before installation.
