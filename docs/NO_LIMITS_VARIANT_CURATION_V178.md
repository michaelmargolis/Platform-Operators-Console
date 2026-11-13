<!-- NO_LIMITS_VARIANT_CURATION_V178.md ver 1 -->
# POC v178: platform-specific ride variants and live review

## Release and installation

This is a POC release, not a separate sim_player release. The delta overlays
POC v177 and includes the complete player tree, based on standalone v20. All
future player changes are versioned and distributed as POC releases.

Stop POC and sim_player before installation. Back up the application, especially
any locally edited `sims/nolimits2/ride_config.py` and existing variant JSON files.
Extract `POC_v178_Platform_Variant_Curation_delta.zip` into the directory containing
`PlatformOperatorsConsole/`, allowing its files to merge. Do not replace the
whole repository with the delta. The package includes no working catalogs,
telemetry INI, `poc_config.json`, captures or unrelated actuator utilities.

Local edits to park paths/calibrations and SHOW_VARIANT_MODIFIERS must be merged
back into the new `ride_config.py`. The old RIDE_VARIANTS assignment does not
belong in that file anymore. Supplied defaults preserve the v177 release's
13 definitions for 10 parks, independently copied to each configured platform.
They are starting candidates, not physically validated preferred tunes.

### Preserve custom v176/v177 variants before the first launch

The optional migration tool reads a literal RIDE_VARIANTS list/tuple from your
backed-up Python file without importing or executing it. Run from the new POC root:

```powershell
py tools/migrate_nl2_variants.py "C:/backup/ride_config.py" --platform platform_v2 --platform chair_v3
```

On Linux replace `py` with `python3`. Omit a platform argument when its catalog
should not receive that tuning. Migration refuses to overwrite any existing
working JSON and does not alter the source backup. Custom programmatically
constructed Python variant lists require manual conversion to the documented
JSON format. Merge custom PARKS/calibration changes separately before migration.

## Files and responsibility

- `sims/nolimits2/config.py`: generic NoLimits2 settings.
- `sims/nolimits2/ride_config.py`: shared PARKS, paths, calibration, display flag,
  default startup park label and common validation; no variant definitions.
- `sims/nolimits2/ride_variants/defaults/<platform_key>.json`: shipped seed data.
- `sims/nolimits2/ride_variants/<platform_key>.json`: persistent working variants
  and reviews, read and written by POC and the player.
- `sims/nolimits2/variant_store.py`: shared non-Qt validation and atomic storage.

A working catalog is copied from its default only when it does not already
exist. Updates to defaults never overwrite an existing catalog. Back up working
JSON when transferring or reinstalling the application. A JSON file is not a
code patch and must not be deleted as part of a routine software update.

`POC_RIDE_VARIANTS_DIR` optionally chooses another writable persistent directory.
Both applications must use the same directory to share files on one computer.
Paths are resolved independently of the current working directory. The player's
Platform tooltip and save confirmation show the actual file location. On a normal
Pi deployment the default is under `/home/poc/Runtime/sims/nolimits2/ride_variants/`.
The `poc` user needs write permission; a failed write is reported without changing
reviews or the visible selection.

## JSON schema

```json
{
  "schema_version": 1,
  "platform_key": "platform_v2",
  "variants": [
    {"label": "Blackbird", "park": "blackbird", "status": "candidate"},
    {
      "label": "Blackbird [Sine]",
      "park": "blackbird",
      "angular_cue": "sine",
      "gain_scale_pct": {"roll_gain": 120},
      "status": "candidate"
    }
  ]
}
```

The file establishes the platform. Do not put platform_key in an individual
variant. The same label may exist independently in two platform catalogs. Labels
must be unique within one catalog (also case-insensitively). The exact park name
may be followed by one or more space-separated `[modifier]` groups. Optional
motion overrides retain their previous meaning: angular cue, relative six-axis
gains, Roll/Pitch washout overrides, heave mode and normalized-height amplitude.
Global gains/washout and physical geometry remain outside the variant.

JSON cannot contain Python comments, expressions, tuples or trailing commas.
Unknown fields, duplicate JSON keys, invalid values, a mismatched platform key,
or more than one preferred variant per park are explicit configuration errors.
A missing status means candidate. No additional multi-platform review state exists.

## POC visibility and preference

| Catalog state for a park | POC dropdown |
| --- | --- |
| No preferred variant | All candidates; no rejected entries |
| A preferred variant exists | That preferred variant only; candidates are hidden |
| Every variant rejected | No entry for that park |

Select as preferred marks the confirmed current variant preferred and every other
variant for that park rejected, in one file update. Other parks and platform
files are unchanged. Reject this variant changes only the current entry.

POC's physical platform is fixed for the session. A platform selection saved in
the existing configuration UI takes effect on restart, not while the application
is running. The recorder still enumerates physical parks once each, independently
of curation, so rejected or duplicated motion variants do not duplicate captures.

`SHOW_VARIANT_MODIFIERS=False` displays only park names. The startup duplicate
name check runs after preference/rejection filtering. Two visible candidates for
one park are an error in that display mode; rejected or hidden candidates are not.
Keep modifiers visible during initial curation.

## Touch and encoder review workflow

1. Test the ride normally, then deactivate and park the platform.
2. In Debug -> Controls check **Review mode**. It is unchecked at every startup
   and is not persisted. The **Review** button appears beside the ride dropdown.
3. Touch Review. The dialog identifies the confirmed loaded variant, not an
   unconfirmed highlighted entry. It offers **Select as preferred**, **Reject
   this variant**, and **Cancel**. Cancel is initially highlighted.
4. Rotate the encoder to cycle through the three choices; press it to activate
   the highlighted choice. Touching a button also activates it. No keyboard or
   mouse is needed. Cancel writes nothing.

Reviews are enabled only while deactivated and parked, with a confirmed loaded
variant and no park load pending. A state change closes an open review dialog
without saving. The core checks these conditions again at commit time.

**Avoid rejecting every variant for a park unless there is a good reason to
remove that park from the operator's available rides.** The dialog warns before
rejection would leave no available variant for that park. This warning does not
forbid the operator's deliberate decision. Repairing mistaken review decisions
is a manual JSON edit with POC stopped; no undo/restore dialog is supplied.

### What happens to a rejected current variant?

It immediately disappears from the dropdown. Its already-loaded park, cue settings
and train/station state are not changed, and no replacement park is loaded.
The experience heading explicitly identifies the variant as still loaded. It can
still be reactivated until a different variant/park is explicitly selected, as
agreed. This is deliberately not an activation interlock or an automatic motion
change. On restart it is not offered or selected again. Review cannot be used to
re-accept an already rejected loaded variant.

If all variants are rejected at a fresh startup, POC has no selection and does not
load or reset an arbitrary park. Manual restoration of a candidate is needed.

## Player workflow

Launch from the POC root:

```powershell
py -m tools.sim_player
```

The **Platform** selector appears before **Ride variant** and lists PLATFORM_NAME
from all entries in `config/platforms.py`. It initially follows the saved POC
platform but never changes that saved configuration. The tooltip includes the key,
configuration module and active JSON path; legacy keys sharing a physical module
still have independent catalogs.

Changing platform validates/loads its catalog and recalculates the capture using
that platform's kinematics, actuator limits and applicable floor geometry. The
video, playback position and immutable raw telemetry remain in place. A failed
recalculation restores the last successful platform selection. Analysis caches
include platform identity and the platform-config source fingerprint.

The player shows non-rejected variants for the recorded park from the selected
platform file only. It also retains `<park name> raw`. It defaults to the preferred
variant, otherwise the bare-name standard candidate, otherwise the first candidate.
There is no diagnostic for variants in other platform files.

The player is a research tool: newly added candidates remain available there even
when a preferred variant exists; POC still hides them. To reopen live curation,
manually change the preferred entry back to candidate while POC is stopped, and
restore any previously rejected entries you want to test. Automatic undo or
reopening acceptance is deferred.

### Save a candidate

Select a named configuration, tune cue/gains, then choose **File -> Save current
settings as a ride variant...**. Enter a new unique bracketed label. The operation
adds a candidate to the selected platform's working JSON. It does not accept it,
overwrite another entry, write Python, modify global telemetry settings or change
POC's current platform. Raw replay cannot be exported as a live candidate until a
named heave/calibration preset has been selected.

POC loads definitions at startup and changes only review status during the session.
Restart it to see variants added or retuned by the player. A running POC cannot
silently overwrite an externally edited catalog: the save detects changed file
bytes and reports a conflict. Stop/restart POC, or switch away and back to the
platform in the player, before retrying. Close applications before manually editing
or transferring files. Development PC and Pi files are separate unless deliberately
shared; copying a curated file between them is an explicit operator/developer step.

Experiment JSON v3 records platform key, config module and that module's SHA-256,
plus the existing variant/global snapshots. v1/v2 remain readable with a platform
warning. Changed geometry fingerprints trigger a warning, not a claim that an old
experiment is identical. This is a source-file fingerprint, not a complete hardware
or dependency calibration certificate. Current installed global settings remain
in use; older global snapshots produce the existing mismatch warning.

## Persistence and failures

Before commit, the store validates the proposed document, checks that the on-disk
catalog is unchanged, saves the prior bytes to `<platform>.json.bak`, then replaces
the active file atomically. No in-memory review/dropdown change occurs on failed
validation, backup or replacement. A per-file lock prevents concurrent writers.
After a crashed process, remove a stale `.<platform>.json.lock` only with both
applications stopped. A backup is the previous revision, not an undo history.

## Floor geometry

A zero or missing PLATFORM_CLEARANCE_OFFSET means no floor-clearance model is
required. No hull is borrowed from another platform; the player reports N/A.
The full physical muscle maximum remains available, while applicable actuator
limits remain enforced. MUSCLE_MAX_ACTIVE_LENGTH equals MUSCLE_MAX_LENGTH
(an absolute length, not the contraction-range width).

MDX now explicitly declares its prior 50 mm clearance-offset fallback. Its existing
60 mm extension-reserve neutral calculation, 28-point hull and geometry limits are
preserved. Live POC and offline analysis use the same setup helper. This release
does not add the earlier proposed ride-specific neutral-height bias or sensors.

## Validation before physical operation

Regression tests exercise catalog validation, platform independence, candidate
filtering, preference/rejection, file conflicts/failures, stable NL2 selection,
same-park no-reload, retained rejected settings, last-choice warnings, encoder
navigation logic, real offline kinematics on both active platforms, migration and
legacy experiments. Test fixtures use isolated temporary catalogs.

Actual Pi touchscreen/encoder dialog rendering, Windows Qt video/layout, live NL2,
SHS and physical motion require local verification. Do not interpret headless
regression success as physical commissioning. Initially verify with physical output
disconnected or the existing nonphysical test setup; do not change production
motion safeguards for this test. The accompanying release verification report lists
exact executed tests and limitations.
