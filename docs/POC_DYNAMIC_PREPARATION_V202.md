<!-- POC_DYNAMIC_PREPARATION_V202.md ver 6 -->
# Dynamic ride preparation and POC import - current workflow

This document describes the current Analyzer-to-POC workflow, introduced in
v202 and updated through POC v224 / MDX Ride Analyzer v0.2.29.

## Terminology

- **Dynamic-source session**: one `session_*` folder created by the Analyzer
  capture workflow. It contains one or more rides, repeated recordings and the
  timing/provenance companions required for generation.
- **Dynamic-source recording**: one completed measured ride repeat within a
  session.
- **Capture**: the action/process that creates a dynamic-source session.
- **Generated profile set**: one optimiser output directory under
  `studies/nl2/dynamic_profiles/profiles_<timestamp>_<id>/`.
- **Candidate**: one platform/ride/qualification profile within a generated
  profile set.

The word **Study** remains the Analyzer menu category and is still used by some
older optimiser/timing internals, but a saved dynamic-source folder is called a
**session** in the current UI and documentation.

## Analyzer workflow - three-stage wizard

1. Open **Study > Dynamic rides - prepare for POC...**. The Source page is fixed
   to `captures/dynamic_source/`. Session lists direct `session_*` children
   only. Hover a row to see captured rides and their retained per-platform
   state: Partial, Generated, Reviewed or Exported. The normal workflow does
   not expose an alternate source-folder browser.
2. Select Session and Ride. Ride contains only rides with primary recordings in
   that session. Each Ride row also shows retained work, for example **Chair:
   Exported; Ground: Generated**, so already-processed rides can be skipped
   without opening them. Select **Chair V3**, **Ground-Loading Platform**, or both
   using the platform checkboxes. Beside each checkbox Analyzer shows the best
   retained state plus the generation time and generated-profile-set name; hover
   for the full profile-set path. Source recordings remain optional replay/inspection.
3. Analyzer searches retained completed results for the exact session, ride and
   selected platform combination. If one compatible completed profile set contains
   every selected platform, Source offers **Use existing profile(s) ->** and a
   separate **Generate new profiles ->** action. Otherwise the normal action is
   **Generate profiles ->**. Previous results are never silently treated as a new
   generation and changing platform selection re-evaluates the matching result.
4. Generate shows the live optimiser output continuously. **Abort generation**
   terminates the managed child process and incomplete output cannot be exported.
   Successful generation advances automatically to Review & Export. A failed
   regeneration does not delete an earlier complete result.
5. Review & Export contains one panel per selected physical platform. Analyzer
   recommends a qualified **Strict** candidate when available; **Relaxed 5%** is
   recommended only when no qualifying Strict candidate exists. Alternative and
   dismissed research candidates remain available through secondary controls.
6. **Review profile** opens the ordinary Analyzer Video / Plot / Overlay review.
   Use **Return to dynamic preparation** to return. That exact profile is then
   recorded as reviewed.
7. After review, edit the pre-filled **POC profile name** if required and choose
   **Export for POC**. Export performs the existing qualification/package checks
   and builds the immutable ZIP in `exports/dynamic_candidates/`. The default
   name advances from `Ride [Dynamic A]` to the next unused export label. A
   duplicate label for the same ride/platform already in the export folder is
   rejected. The persistent **Exported POC candidates** pane scans that folder,
   groups recognised candidate packages by physical platform and sorts rides/candidates
   alphabetically. Hover shows Strict/Relaxed 5% qualification and package
   details. Selecting a package is informational only and never changes Source;
   **Refresh** rescans manually, double-click/**Open selected folder** opens its
   directory, and successful export refreshes the pane automatically.
8. **Open export folder** is shown for an exported profile. The wizard can remain
   open while the other selected platform is reviewed/exported, or **Process
   another ride** returns to Source. Review/export state is persisted with the
   generated profile set.

## Capturing a new dynamic-source session

Use **Study > Capture dynamic source...** or **Capture new session...** from the
preparation workspace. The capture dialog calls the established NoLimits timing
capture code rather than duplicating its loading/dispatch logic.

The **Capture scope** choices are Current ride, Custom rides, Quick, Standard
and Extended. Quick uses the short Blackbird/Fenrir/Tilt Coaster set; Standard
uses the current operator-visible rides with three measured recordings each;
Extended uses the same set with five. New sessions are always created below
`captures/dynamic_source/`; the normal capture dialog does not expose an
alternate output-folder browser.

For qualification, acquire source sessions on the intended deployment-class PC
with VR active. The selected Ride Analyzer motion candidate is not applied to
capture; the simulator trajectory is recorded so the same source session can
support later profile-generation experiments.

## POC import, selection and commissioning

1. Stop physical motion: deactivate and fully park the target platform. Back up
   the platform's working ride catalogue before commissioning.
2. Transfer Analyzer-exported ZIPs into `exports/dynamic_candidates/` on the POC
   (or choose another folder from the import window). Open **Profile > Import
   dynamic candidate ZIP...**.
3. The chooser scans package manifests and shows only valid candidates for the
   POC's currently selected physical platform. Packages for the other platform,
   malformed/non-candidate ZIPs, and candidates already present in this platform
   catalogue are hidden and counted in the chooser summary. Already-imported
   detection uses both candidate label and deployment path, so renaming a ZIP
   does not make the same package appear new.
4. Confirm the selected package. POC re-runs archive integrity, platform/ride,
   qualification, fallback and naming checks before publishing anything. Import
   creates the runtime bundle and appends one `candidate` catalogue entry.
5. POC then performs the append-only live catalogue refresh introduced in v214.
   No full POC restart is normally required. If existing catalogue definitions
   changed externally, live refresh is refused and the imported candidate is
   picked up by a later normal NL2 reload/restart instead.
6. Existing preferred/candidate/rejected visibility rules still apply. Physical
   commissioning remains required before selecting a dynamic candidate as
   preferred.

## Ground-Loading Platform minimum clearance

**Profile > Adjust floor clearance...** changes the minimum permitted
underside-to-floor clearance during active rides. The default is 15 mm and the
commissioning adjustment is deliberately bounded to 5-75 mm. It applies to
static and dynamic rides and can only be changed while fully parked and
deactivated. It does not move the platform directly or change CAD geometry.
After any change, verify the actual physical clearance before dispatch.
