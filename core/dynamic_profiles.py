#dynamic_profiles.py ver 3
"""Fail-closed NoLimits dynamic-ride bundle loading and 20 Hz runtime matching.

No imports of Qt, NL2 transport, Festo, or hardware. All disk I/O and SHA256
work occur at startup/load, never in the 20 Hz processing loop. The default
static POC path does not instantiate this module's matcher.

A dynamic profile is a POST-WASHOUT multiplier, not a substitute motion script.
An approved runtime bundle is required; research directories cannot be loaded
through a variant catalogue entry.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import hashlib
import json
import logging
import math
import os
import time

from tools.nl2_sync.matcher import Matcher, Settings
from tools.nl2_sync.profile import DynamicProfile, ProfilePlayback, load_reference, digest_json

LOG = logging.getLogger(__name__)
SCHEMA = "poc.nl2.dynamic-deployment/2"
RELATIVE_BUNDLE_ROOT = PurePosixPath("ride_profiles")
ROOT = Path(__file__).resolve().parent.parent
NL2_DIRECTORY = ROOT / "sims" / "nolimits2"
DYNAMIC_ERROR_FILE = ROOT / "logs" / "dynamic_errors.log"
AXES = ("surge", "sway", "heave", "roll", "pitch", "yaw")
# Fail closed after a sustained inability to apply a trustworthy dynamic phase.
# This is an initial engineering policy, NOT a validated physical tolerance.
DEFAULT_FAULT_AFTER_S = 2.0
DEFAULT_MAX_PROFILE_LAG_S = 0.250


class DynamicProfileError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_dict(path: Path) -> dict:
    try:
        raw = path.read_bytes()
        if len(raw) > 1_000_000:
            raise DynamicProfileError(f"Oversized manifest: {path}")
        data = json.loads(raw.decode("utf-8-sig"),
                          parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
        if not isinstance(data, dict):
            raise ValueError("not an object")
        return data
    except (UnicodeError, ValueError, OSError) as exc:
        raise DynamicProfileError(f"Invalid deployment JSON {path}: {exc}") from exc


def validate_bundle_relpath(relative: str, platform_key: str | None = None) -> PurePosixPath:
    """A catalogue may reference only a deployment manifest within ride_profiles/."""
    if not isinstance(relative, str) or "\\" in relative or not relative.strip():
        raise DynamicProfileError("Dynamic profile path must be a nonempty POSIX relative path")
    path = PurePosixPath(relative)
    if (path.is_absolute() or any(part in ("", ".", "..") for part in path.parts)
            or len(path.parts) != 5 or path.parts[0] != "ride_profiles"
            or path.parts[-1] != "deployment.json"):
        raise DynamicProfileError("Expected ride_profiles/<platform>/<park>/<bundle>/deployment.json")
    if platform_key is not None and path.parts[1] != platform_key:
        raise DynamicProfileError("Dynamic profile path belongs to the wrong physical platform")
    if any(not p.replace("-", "").replace("_", "").isalnum() for p in path.parts[1:-1]):
        raise DynamicProfileError("Unsafe dynamic profile path segment")
    return path


def _within_directory(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False


def _checked_file(directory: Path, name: str) -> Path:
    if name not in ("profile.json", "reference.npz", "qualification.json", "deployment.json"):
        raise DynamicProfileError("Unexpected runtime bundle file name: " + str(name))
    path = directory / name
    if path.is_symlink() or not path.is_file() or not _within_directory(path, directory):
        raise DynamicProfileError(f"Unsafe or missing runtime file: {path}")
    return path


def _same_scalar(a, b) -> bool:
    if isinstance(a, (int, float)) and not isinstance(a, bool) and isinstance(b, (int, float)) and not isinstance(b, bool):
        return math.isfinite(float(a)) and math.isfinite(float(b)) and abs(float(a) - float(b)) <= 1e-9
    return a == b


class DynamicRequalificationRequired(DynamicProfileError):
    """The motion convention or geometry differs from the current runtime."""


def require_canonical_identity(identity, platform: str) -> None:
    """Validate the convention and geometry on a profile or a package manifest."""
    from core.motion_convention import MOTION_CONVENTION
    from core.platform_geometry import geometry_id_for_platform

    if not isinstance(identity, dict) or identity.get("transform_convention") != MOTION_CONVENTION:
        raise DynamicRequalificationRequired(
            "Profile predates canonical motion or uses another convention; requalification required"
        )
    try:
        expected = geometry_id_for_platform(platform)
    except KeyError as exc:
        raise DynamicProfileError("Profile platform is unsupported in this release") from exc
    if identity.get("geometry_id") != expected:
        raise DynamicRequalificationRequired("Profile geometry identity differs; requalification required")


def require_canonical_profile(profile: DynamicProfile) -> None:
    """Require both profile identity and processing snapshot to match the new model."""
    platform = profile.identity["platform"]
    require_canonical_identity(profile.identity, platform)
    require_canonical_identity(profile.metadata.get("processing"), platform)


def _check_frozen_settings(profile: DynamicProfile, live_variant: dict,
                           global_settings: dict, root: Path, platform: str, park: str):
    """Check processing schema, report meaningful differences as warnings.

    Exact Python source bytes and NL2 patch revisions are provenance, not a
    kinematic acceptance gate. The installed platform continues to enforce
    workspace, floor and output limits at every service tick.
    """
    context = profile.metadata.get("processing")
    if not isinstance(context, dict):
        raise DynamicProfileError("Profile lacks processing context")
    if context.get("platform") != platform or profile.identity.get("park") != park:
        raise DynamicProfileError("Profile platform/park is incompatible")
    frozen = context.get("base_variant")
    if not isinstance(frozen, dict) or frozen.get("park") != park:
        raise DynamicProfileError("Profile has malformed base variant")
    if not isinstance(context.get("matcher"), dict):
        raise DynamicProfileError("Missing matcher settings")
    try:
        Settings(**context["matcher"])
    except (ValueError, TypeError) as exc:
        raise DynamicProfileError(f"Malformed matcher settings: {exc}") from exc
    if not _same_scalar(context.get("tick_s"), .05):
        raise DynamicProfileError("Unsupported 20 Hz processing format")
    warnings = []
    for key in ("angular_cue", "heave_mode", "neutral_height_delta_mm",
                "roll_washout", "pitch_washout", "normalized_height_amplitude"):
        if not _same_scalar(frozen.get(key), live_variant.get(key)):
            warnings.append("Frozen ride setting differs: " + key)
    if frozen.get('gain_scale_pct') != live_variant.get('gain_scale_pct'):
        warnings.append('Frozen gains differ from the current catalogue')
    if isinstance(context.get('telemetry'), dict) and context['telemetry'] != global_settings:
        warnings.append('Global telemetry/washout differs from study')
    # Preserve the source hashes as provenance. A repository with changed line
    # endings or a compatible implementation update is not automatically invalid.
    hashes = context.get('source_hashes')
    if hashes is not None and not isinstance(hashes, dict):
        raise DynamicProfileError('Malformed source provenance')
    for reason in warnings:
        LOG.warning("Dynamic profile %s/%s: %s", platform, park, reason)
    return tuple(warnings)


@dataclass(frozen=True)
class DynamicDeployment:
    label: str
    platform: str
    park: str
    manifest_path: Path
    profile: DynamicProfile
    reference: object
    manifest: dict
    global_settings: dict

    @classmethod
    def load(cls, *, label: str, relative: str, platform: str, park: str,
             live_variant: dict, global_settings: dict, root: Path = ROOT):
        rel = validate_bundle_relpath(relative, platform)
        if rel.parts[2] != park:
            raise DynamicProfileError("Dynamic bundle path belongs to another ride")
        base = root / "sims" / "nolimits2"
        manifest_path = base.joinpath(*rel.parts)
        bundle = manifest_path.parent
        if not _within_directory(bundle, base) or bundle.is_symlink():
            raise DynamicProfileError("Dynamic bundle escapes runtime directory")
        _checked_file(bundle, "deployment.json")
        d = _json_dict(manifest_path)
        if d.get("schema") != SCHEMA or d.get("platform") != platform or d.get("park") != park:
            raise DynamicProfileError("Deployment schema, park or platform mismatch")
        if d.get("file_names") != ["profile.json", "reference.npz", "qualification.json"]:
            raise DynamicProfileError("Unsupported runtime bundle file list")
        sha = d.get("sha256")
        if not isinstance(sha, dict) or set(sha) != {"profile.json", "reference.npz", "qualification.json"}:
            raise DynamicProfileError("Missing deployment SHA256 entries")
        for name, expected in sha.items():
            p = _checked_file(bundle, name)
            if not isinstance(expected, str) or sha256_file(p) != expected:
                raise DynamicProfileError(f"Dynamic runtime bundle checksum mismatch: {name}")
        profile_path = bundle / "profile.json"
        try:
            profile = DynamicProfile.load(profile_path)
            reference = load_reference(profile_path, profile)
        except (ValueError, KeyError, OSError) as exc:
            raise DynamicProfileError(f"Invalid dynamic profile/reference: {exc}") from exc
        if profile.identity.get("platform") != platform or profile.identity.get("park") != park:
            raise DynamicProfileError("Profile identity is for a different park or platform")
        require_canonical_profile(profile)
        if not math.isclose(float(profile.time[-1]), float(reference.time[-1]), abs_tol=1.0):
            raise DynamicProfileError("Profile and reference trajectory durations disagree")
        if d.get("profile_identity") != profile.identity:
            raise DynamicProfileError("Deployment/profile identity differs")
        if d.get("profile_content_sha256") != digest_json(profile.to_dict()):
            raise DynamicProfileError("Deployment/profile content identity differs")
        from core.dynamic_qualification import load_qualification
        try:
            load_qualification(_checked_file(bundle, "qualification.json"), profile)
        except (ValueError, OSError) as exc:
            raise DynamicProfileError(f"Invalid Analyzer qualification report: {exc}") from exc
        _check_frozen_settings(profile, live_variant, global_settings, root, platform, park)
        return cls(label, platform, park, manifest_path, profile, reference, d, dict(global_settings))

    def preflight(self, *, variant: dict, telemetry_globals: dict, nl2_version: str | None,
                  telemetry=None, departure_station=None) -> None:
        """Semantic identity gate; advisory study differences do not block a ride.

        A wrong viewed train/seat will be handled by runtime matcher fallback,
        not by silently applying a gain curve from unrelated progress.
        """
        if variant.get('park') != self.park:
            raise DynamicProfileError('Selected NL2 park does not match dynamic profile')
        expected = self.profile.identity.get('nl2_version')
        if nl2_version and expected and str(nl2_version) != str(expected):
            LOG.warning('Dynamic ride NL2 version differs: source=%s live=%s', expected, nl2_version)
        frozen = self.profile.metadata['processing'].get('base_variant', {})
        if variant.get('gain_scale_pct') != frozen.get('gain_scale_pct'):
            LOG.warning('Dynamic ride base gains differ from qualified study')
        if telemetry_globals != self.global_settings:
            LOG.warning('Dynamic ride telemetry globals changed since startup')
        expected_view = self.profile.identity.get('view_identity')
        if expected_view is not None and telemetry is not None:
            actual = [int(telemetry.coaster_index), int(telemetry.train),
                      int(telemetry.car), int(telemetry.seat), int(telemetry.view_mode)]
            if actual != list(expected_view):
                LOG.warning('Dynamic ride viewed train/seat differs; matcher fallback may be necessary')
        expected_station = self.profile.identity.get('departure_station')
        if expected_station is not None and departure_station is not None:
            if list(departure_station) != list(expected_station):
                LOG.warning('Dynamic ride departure station differs from study')

    def new_runtime(self):
        return DynamicRideRuntime(self)


class DynamicErrorLog:
    """Append-only errors, including previous sessions; never rotated or cleared."""
    def __init__(self, path: Path = DYNAMIC_ERROR_FILE):
        self.path = Path(path)
        self.count = 0
        try:
            if self.path.exists():
                with self.path.open("rb") as fp:
                    self.count = sum(bool(line.strip()) for line in fp)
        except OSError as exc:
            LOG.error("Unable to read dynamic error log %s: %s", self.path, exc)

    def append(self, event: str, *, label: str = "", platform: str = "", park: str = "",
               reason: str = "") -> None:
        entry = {"utc": datetime.now(timezone.utc).isoformat(), "event": str(event),
                 "label": str(label), "platform": str(platform), "park": str(park),
                 "reason": str(reason).replace("\n", " ")[:1200]}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")
                fp.flush()
                os.fsync(fp.fileno())
            self.count += 1
        except OSError as exc:
            # Failure to log must never create new motion commands.
            LOG.error("Unable to append dynamic error log %s: %s", self.path, exc)


class DynamicRideRuntime:
    """Stateful 20 Hz dynamic multiplier stage, only for one confirmed ride."""
    def __init__(self, deployment: DynamicDeployment, *, logger=None,
                 fault_after_s=DEFAULT_FAULT_AFTER_S,
                 max_profile_lag_s=DEFAULT_MAX_PROFILE_LAG_S):
        self.bundle = deployment
        self.error_log = logger
        self.fault_after_s = float(fault_after_s)
        self.max_profile_lag_s = float(max_profile_lag_s)
        if not (0.5 <= self.fault_after_s <= 30.0 and 0.05 <= self.max_profile_lag_s <= 2.0):
            raise ValueError("Invalid dynamic fallback policy")
        self.cycle_active = False
        self.latch_reason = ""
        self.status = "READY"
        self.match = None
        self.match_basis = "--"
        self.current_gains = tuple(float(x) for x in self.bundle.profile.fallback)
        self.fallback = "active"
        self.untrusted_start = None
        self.last_wall = None
        self.active_seconds = 0.0
        self.loss_count = 0
        self.last_age = None
        self.matcher = None
        self.playback = None

    def start_cycle(self):
        if self.latch_reason:
            return
        context = self.bundle.profile.metadata["processing"]
        self.matcher = Matcher(self.bundle.reference, Settings(**context["matcher"]))
        meta = self.bundle.profile.metadata
        self.playback = ProfilePlayback(
            self.bundle.profile,
            transition_s=float(meta.get("fallback_transition_s", .25)),
            max_age_s=float(meta.get("max_observation_age_s", .25)))
        self.cycle_active = True
        self.status = "REACQUIRING"
        self.match_basis = "--"
        self.untrusted_start = None
        self.last_wall = None
        self.active_seconds = 0.0
        self.current_gains = tuple(float(x) for x in self.bundle.profile.fallback)
        self.fallback = "active"

    def stop_cycle(self):
        # A hard runtime fault stays latched through this ride only, and is
        # retained in the persistent log for later inspection.
        self.latch_reason = ""
        self.match = None
        self.match_basis = "--"
        self.last_age = None
        self.matcher = None
        self.playback = None
        self.cycle_active = False
        self.last_wall = None
        self.untrusted_start = None
        self.status = "READY"
        self.current_gains = tuple(float(x) for x in self.bundle.profile.fallback)
        self.fallback = "active"

    def suspend(self):
        # The simulator pause may last minutes; paused real time must not be
        # misinterpreted as progress along the reference.
        self.last_wall = None
        if self.cycle_active and not self.latch_reason:
            self.status = "PAUSED"

    def latch(self, reason):
        if self.latch_reason:
            return
        self.latch_reason = str(reason)
        self.status = "DYNAMIC DISABLED"
        self.fallback = "latched"
        # The following ticks blend to the validated static fallback rather
        # than imposing a discontinuous multiplier jump on this tick.
        if self.error_log is not None:
            self.error_log.append("runtime_dynamic_disabled", label=self.bundle.label,
                                  platform=self.bundle.platform, park=self.bundle.park,
                                  reason=self.latch_reason)

    def tick(self, telemetry, *, now: float | None = None):
        """Advance matching once; never touch the physical platform."""
        if not self.cycle_active:
            self.start_cycle()
        if now is None:
            now = time.monotonic()
        if not math.isfinite(now):
            self.latch("Nonfinite POC monotonic time")
            return self.current_gains
        if self.last_wall is None:
            dt = 0.0
        else:
            dt = max(0.0, now - self.last_wall)
        self.last_wall = now
        self.active_seconds += dt
        if self.latch_reason:
            if self.playback is not None:
                try:
                    self.current_gains = self.playback.update(
                        None, "lost", self.active_seconds,
                        self.last_age if self.last_age is not None else 1e6)
                    self.fallback = ("active" if self.playback.blend <= 1e-6 else "blending")
                except ValueError:
                    self.current_gains = tuple(float(x) for x in self.bundle.profile.fallback)
                    self.fallback = "active"
            return self.current_gains
        try:
            if telemetry is None:
                # A transient absence of the latest pose is a matcher trust loss,
                # not an immediate permanent dynamic fault. Fade toward the
                # validated static fallback and allow bounded reacquisition.
                self.match_basis = "missing"
                self.last_age = None
                self.current_gains = tuple(float(x) for x in self.playback.update(
                    None, "profile_untrusted", self.active_seconds, 1e6))
                self.fallback = ("active" if self.playback.blend <= 1e-6 else "blending")
                if self.untrusted_start is None:
                    self.untrusted_start = self.active_seconds
                    self.loss_count += 1
                elif self.active_seconds - self.untrusted_start >= self.fault_after_s:
                    self.latch(f"Trustworthy profile phase unavailable for {self.fault_after_s:g} s")
                if not self.latch_reason:
                    self.status = "FALLBACK"
                return self.current_gains
            expected_view = self.bundle.profile.identity.get("view_identity")
            if expected_view is not None:
                observed_view = [int(telemetry.coaster_index), int(telemetry.train),
                                 int(telemetry.car), int(telemetry.seat),
                                 int(telemetry.view_mode)]
                if observed_view != list(expected_view):
                    self.latch("Live NL2 viewed train/car/seat differs from qualified profile")
                    return self.current_gains
            received_at = float(telemetry.received_at)
            if not math.isfinite(received_at) or received_at > now + .05:
                raise ValueError("Nonfinite or future-dated NL2 observation")
            tm_age = max(0.0, now - received_at)
            self.last_age = tm_age
            match = self.matcher.update(
                (float(telemetry.pos_x), float(telemetry.pos_y), float(telemetry.pos_z)),
                (float(telemetry.quat_x), float(telemetry.quat_y),
                 float(telemetry.quat_z), float(telemetry.quat_w)),
                self.active_seconds, frame=int(telemetry.frame),
                speed=float(telemetry.speed))
            self.match = match
            self.match_basis = match.status
            lag = abs(match.phase_s - match.profile_phase_s)
            usable = (match.confidence in ("matched", "low")
                      or (match.status == "stale" and tm_age <= self.playback.max_age_s))
            usable = usable and lag <= self.max_profile_lag_s and tm_age <= self.playback.max_age_s
            # A successful reacquisition carries a 'reacquired_' prefix;
            # ProfilePlayback accepts the underlying matched basis.
            state = (match.status.removeprefix("reacquired_")
                     if usable else "profile_untrusted")
            self.current_gains = tuple(float(x) for x in self.playback.update(
                match.profile_phase_s, state, self.active_seconds, tm_age))
            self.fallback = ("inactive" if self.playback.blend >= 1.0 - 1e-6 else
                             "active" if self.playback.blend <= 1e-6 else "blending")
            if usable:
                self.untrusted_start = None
            elif self.untrusted_start is None:
                self.untrusted_start = self.active_seconds
                self.loss_count += 1
            elif self.active_seconds - self.untrusted_start >= self.fault_after_s:
                self.latch(f"Trustworthy profile phase unavailable for {self.fault_after_s:g} s")
            if not self.latch_reason:
                self.status = ("TRACKING" if self.fallback == "inactive" else
                               "REACQUIRING" if usable and self.fallback == "blending" else "FALLBACK")
            return self.current_gains
        except Exception as exc:
            # A dynamic-only internal fault cannot escape into the main 20 Hz
            # callback and bypass the separately validated static fallback.
            self.latch(f"Dynamic matcher error: {exc}")
            return self.current_gains

    def multiply(self, post_washout):
        try:
            if len(post_washout) != 6:
                raise ValueError("Six post-washout components required")
            result = tuple(float(a) * float(b) for a, b in zip(post_washout, self.current_gains))
            if not all(math.isfinite(v) for v in result):
                raise ValueError("Nonfinite dynamic motion request")
            return result
        except Exception as exc:
            self.latch("Invalid post-washout motion: " + str(exc))
            # Do not fabricate a zero/neutral motion command: on a suspended
            # platform an uncommanded return to neutral could be a large step.
            # The caller must withhold this frame's physical move command.
            raise DynamicProfileError("Invalid dynamic request; physical move withheld") from exc

    def snapshot(self):
        match = self.match
        return {
            "profile_name": str(self.bundle.profile.metadata.get("name", self.bundle.label)),
            "motion_profile": "Dynamic", "state": self.status,
            "match_basis": self.match_basis, "fallback": self.fallback,
            "gain_percent": [round(x * 100., 1) for x in self.current_gains],
            "profile_phase_s": match.profile_phase_s if match else None,
            "match_phase_s": match.phase_s if match else None,
            "profile_lag_s": (match.phase_s - match.profile_phase_s) if match else None,
            "observation_age_s": self.last_age,
            "loss_count": self.loss_count, "fault": self.latch_reason,
        }


def audit_dynamic_store(store, globals_snapshot: dict, *, root: Path = ROOT,
                        error_log: DynamicErrorLog | None = None) -> dict[str, DynamicDeployment]:
    """Preload/verify all bundles at startup; hide invalid dynamic variants.

    An invalid preferred variant suppresses its park, not replacement with an
    unrelated static candidate. Only selected platform's catalogue is examined.
    """
    valid = {}
    invalid = {}
    for entry in store.entries():
        rel = entry.get("dynamic_profile")
        if rel is None:
            continue
        try:
            resolved = store.snapshot(entry["label"])
            valid[entry["label"]] = DynamicDeployment.load(
                label=entry["label"], relative=rel,
                platform=store.platform_key, park=entry["park"],
                live_variant=resolved, global_settings=globals_snapshot, root=root)
        except Exception as exc:
            invalid[entry["label"]] = str(exc)
            LOG.error("Dynamic candidate rejected: %s: %s", entry["label"], exc)
            if error_log is not None:
                error_log.append("invalid_dynamic_candidate", label=entry["label"],
                                 platform=store.platform_key, park=entry.get("park", ""),
                                 reason=str(exc))
    store.set_dynamic_invalid(invalid)
    return valid
