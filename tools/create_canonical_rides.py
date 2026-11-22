# create_canonical_rides.py ver 1
"""Create one reusable canonical NoLimits2 video per unique ride.

This is an occasional local Windows/SHS-PC maintenance utility. It reads the
working Chair V3 and Ground-Loading Platform ride-variant catalogues, takes the
union of referenced physical parks, captures each missing ride once using the
existing NL2/OBS recorder stack, then registers the matching telemetry/video
pair directly in tools/sim_player/canonical_rides.

It never starts POC, SHS control traffic, Festo output, or platform hardware.
Close POC and any other client controlling the NL2 API before use.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
from types import SimpleNamespace
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_PLATFORM_KEYS = ("chair_v3", "platform_v2")
DEFAULT_OUTPUT_DIR = ROOT / "captures" / "canonical_source"
DEFAULT_CAPTION_DELAY_S = 5.0
DEFAULT_OBS_LEAD_S = 0.75
VR_ACTIVE_BIT = 0x08


@dataclass(frozen=True)
class RideSpec:
    park_key: str
    name: str
    path: str
    capture_index: int
    platforms: tuple[str, ...]


def unique_park_keys(entries_by_platform, park_order):
    """Return unique referenced park keys in configured PARKS order."""
    referenced = set()
    for entries in entries_by_platform.values():
        for entry in entries:
            park = entry.get("park") if isinstance(entry, dict) else None
            if isinstance(park, str) and park:
                referenced.add(park)
    return tuple(key for key in park_order if key in referenced)


def selected_park_keys(spec, rides):
    """Accept comma-separated park keys or exact ride names; preserve ride order."""
    if spec is None or not str(spec).strip() or str(spec).strip().casefold() == "all":
        return tuple(ride.park_key for ride in rides)
    chosen = []
    for raw in str(spec).split(","):
        value = raw.strip().casefold()
        matches = [ride.park_key for ride in rides
                   if value in (ride.park_key.casefold(), ride.name.casefold())]
        if len(matches) != 1:
            raise ValueError(f"Unrecognized ride {raw.strip()!r}; use --list")
        if matches[0] not in chosen:
            chosen.append(matches[0])
    return tuple(chosen)


def video_anchor_from_manifest(data):
    """Return [telemetry t=0, estimated video PTS] from local monotonic clocks."""
    value = data.get("approx_csv_zero_after_obs_ack_s") if isinstance(data, dict) else None
    try:
        offset = float(value)
    except (TypeError, ValueError):
        offset = float("nan")
    if not math.isfinite(offset):
        try:
            events = data["events"]
            capture_zero = float(events["capture_elapsed_zero"]["monotonic_s"])
            obs_ack = float(events["obs_start_ack"]["monotonic_s"])
            offset = capture_zero - obs_ack
        except (KeyError, TypeError, ValueError):
            raise RuntimeError(
                "OBS timing manifest has no usable telemetry-zero / recording-start clock anchor")
    if not math.isfinite(offset) or abs(offset) > 60.0:
        raise RuntimeError(f"Implausible automatic video offset {offset!r} seconds")
    return [[0.0, round(offset, 6)]]


def vr_active_from_state(state):
    try:
        return bool(int(state) & VR_ACTIVE_BIT)
    except (TypeError, ValueError):
        return False


def _catalog_rides(platform_keys=DEFAULT_PLATFORM_KEYS):
    from sims.nolimits2 import config, ride_config
    from sims.nolimits2.variant_store import VariantStore

    entries_by_platform = {}
    for platform in platform_keys:
        entries_by_platform[platform] = VariantStore.load_readonly(platform).entries()
    keys = unique_park_keys(entries_by_platform, tuple(ride_config.PARKS))

    capture_rides = tuple(config.RIDES)
    result = []
    for key in keys:
        if key not in ride_config.PARKS:
            raise RuntimeError(f"Variant catalogue refers to unknown park {key!r}")
        park = ride_config.PARKS[key]
        matches = [i for i, (name, path) in enumerate(capture_rides)
                   if str(path).casefold() == str(park["path"]).casefold()
                   or str(name).casefold() == str(park["name"]).casefold()]
        if len(matches) != 1:
            raise RuntimeError(
                f"Cannot map variant park {key!r} ({park['name']}) to exactly one NL2 capture ride")
        platforms = tuple(platform for platform in platform_keys
                          if any(entry.get("park") == key for entry in entries_by_platform[platform]))
        result.append(RideSpec(key, park["name"], park["path"], matches[0], platforms))
    return tuple(result), capture_rides


def _reference_status(park_key):
    from tools.sim_player.canonical_media import ROOT as CANONICAL_ROOT, MediaError, load_media

    pointer = CANONICAL_ROOT / park_key / "reference.json"
    if not pointer.is_file():
        return "missing", ""
    try:
        load_media(park_key)
    except MediaError as exc:
        return "invalid", str(exc)
    return "existing", str(pointer)


def _capture_video_path(csv_path):
    manifest_path = csv_path.with_name(csv_path.stem + "_video_sync.json")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Cannot read OBS timing manifest {manifest_path}: {exc}") from exc
    if data.get("status") != "completed":
        raise RuntimeError(f"OBS manifest is not complete: {manifest_path}")
    candidate = data.get("video_path") or data.get("video_filename")
    if not isinstance(candidate, str) or not candidate:
        raise RuntimeError(f"OBS manifest contains no video path: {manifest_path}")
    video = Path(candidate)
    if not video.is_absolute():
        video = csv_path.parent / video.name
    if not video.is_file():
        raise RuntimeError(f"Captured video does not exist: {video}")
    return video.resolve(), data, manifest_path


def _register_capture(ride, csv_path):
    from tools.sim_player.canonical_media import register_media

    video, manifest, manifest_path = _capture_video_path(csv_path)
    anchors = video_anchor_from_manifest(manifest)
    offset = anchors[0][1]
    print(f"  Automatic timing anchor: telemetry 0.000 s -> video {offset:.3f} s", flush=True)
    if offset < 0:
        print("  WARNING: video begins after telemetry zero; initial telemetry will have no video.",
              flush=True)

    def progress(message):
        print("  " + str(message), flush=True)

    media = register_media(
        ride.park_key,
        csv_path,
        video,
        anchors=anchors,
        calibrated=False,
        confirmed_pair=True,
        progress=progress,
    )
    print(f"  Canonical reference: {media.manifest_path}", flush=True)
    print(f"  Timing source: {manifest_path.name} (automatic estimate; visual spot-check recommended)",
          flush=True)
    return media


def _latest_telemetry(sim, capture_module, *, attempts=40, interval_s=0.05):
    tm = sim.client.latest_telemetry
    for _ in range(attempts):
        if tm is not None:
            return tm
        capture_module.pump(sim)
        tm = sim.client.latest_telemetry
        if tm is None:
            import time
            time.sleep(interval_s)
    return tm


def _capture_one_ride(ride, capture_rides, args, obs):
    from tools import capture_nl2_rides as capture
    from sims.nolimits2.sim import Sim
    import time

    sim = None
    for attempt in range(capture.BUSY_RETRIES + 1):
        sim = Sim(address=args.host, startup_ride_index=ride.capture_index, capture_mode=True)
        try:
            capture.connect_recorder(sim, args.prepare_timeout, 1.0 / args.hz)
            break
        except capture.ParkLoadBusyError:
            sim.disconnect()
            sim = None
            if attempt >= capture.BUSY_RETRIES:
                raise RuntimeError(
                    f"NL2 remained busy after {capture.BUSY_RETRIES + 1} startup attempts "
                    f"for {ride.name}") from None
            print(
                f"  NL2 still processing the previous park; reconnecting in "
                f"{capture.BUSY_RETRY_DELAY_S:g}s "
                f"({attempt + 1}/{capture.BUSY_RETRIES})...", flush=True)
            time.sleep(capture.BUSY_RETRY_DELAY_S)
    try:
        tm = _latest_telemetry(sim, capture)
        if tm is None or getattr(tm, "state", None) is None:
            raise RuntimeError(
                "No fresh NL2 telemetry state is available to verify desktop/non-VR mode")
        if vr_active_from_state(tm.state):
            message = (
                "NL2 reports VR active (telemetry state bit 0x08). Canonical capture is "
                "intended to run in desktop/non-VR mode.")
            if not args.allow_vr:
                raise RuntimeError(message + " Disable VR and rerun, or use --allow-vr deliberately.")
            print("WARNING: " + message, flush=True)

        capture_args = SimpleNamespace(
            host=args.host,
            hz=args.hz,
            prepare_timeout=args.prepare_timeout,
            max_duration=args.max_duration,
            output_dir=args.output_dir,
            state_monitor=False,
            _obs_recorder=obs,
            pre_record_delay=args.caption_delay,
        )
        return Path(capture.capture_one(sim, ride.capture_index, 1, capture_args, capture_rides))
    finally:
        if sim is not None:
            sim.disconnect()


def _print_inventory(rides):
    print("Canonical rides from Chair V3 + Ground-Loading Platform variant catalogues:")
    rows = []
    for ride in rides:
        status, detail = _reference_status(ride.park_key)
        platform_text = ", ".join(ride.platforms)
        rows.append((ride, status, detail))
        print(f"  {ride.park_key:18s} {ride.name:28s} {status:8s}  [{platform_text}]")
        if status == "invalid" and detail:
            print(f"      {detail}")
    return rows


def _confirm(count):
    response = input(f"Capture and register {count} canonical ride(s)? [Y/n] ").strip().casefold()
    return response in ("", "y", "yes")


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true",
                        help="List unique variant-referenced rides and canonical status, then exit")
    parser.add_argument("--rides", help="Comma-separated park keys/ride names, or all")
    parser.add_argument("--replace", action="store_true",
                        help="Capture selected rides even when a valid canonical reference already exists")
    parser.add_argument("--yes", action="store_true", help="Do not ask the final confirmation question")
    parser.add_argument("--allow-vr", action="store_true",
                        help="Allow capture when NL2 telemetry reports VR active (normally refused)")
    parser.add_argument("--host", default="127.0.0.1", help="Local NL2 API address")
    parser.add_argument("--hz", type=float, default=20.0, help="Telemetry polling frequency (default 20 Hz)")
    parser.add_argument("--prepare-timeout", type=float, default=120.0)
    parser.add_argument("--max-duration", type=float, default=600.0)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                        help="Retained original capture directory")
    parser.add_argument("--caption-delay", type=float, default=DEFAULT_CAPTION_DELAY_S,
                        help="Pause after station readiness and before OBS starts (default 5 s)")
    parser.add_argument("--obs-host", default="127.0.0.1")
    parser.add_argument("--obs-port", type=int, default=4455)
    parser.add_argument("--obs-password", default=None,
                        help="OBS WebSocket password (or OBS_WEBSOCKET_PASSWORD environment)")
    parser.add_argument("--obs-source", default="NL2 Game Capture",
                        help="Exact OBS Window/Game Capture source name")
    parser.add_argument("--obs-window-timeout", type=float, default=15.0)
    parser.add_argument("--obs-lead", type=float, default=DEFAULT_OBS_LEAD_S,
                        help="OBS pre-roll after recording starts, before dispatch (default 0.75 s)")
    parser.add_argument("--obs-tail", type=float, default=0.5)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.hz <= 0 or args.prepare_timeout <= 0 or args.max_duration <= 0:
        parser.error("hz and timeout values must be positive")
    if args.caption_delay < 0 or args.obs_lead < 0 or args.obs_tail < 0 or args.obs_window_timeout <= 0:
        parser.error("caption delay / OBS lead / tail must be nonnegative and window timeout positive")

    try:
        rides, capture_rides = _catalog_rides()
        requested = set(selected_park_keys(args.rides, rides))
    except (ValueError, RuntimeError, OSError) as exc:
        parser.error(str(exc))

    inventory = _print_inventory(rides)
    if args.list:
        return 0

    plan = []
    for ride, status, _detail in inventory:
        if ride.park_key not in requested:
            continue
        if status == "existing" and not args.replace:
            print(f"SKIP {ride.name}: canonical reference already exists", flush=True)
            continue
        plan.append(ride)

    if not plan:
        print("No canonical rides require capture.")
        return 0
    if sys.platform != "win32":
        parser.error("Live canonical capture is Windows-only (use --list on other systems)")

    args.output_dir = args.output_dir.expanduser().resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    print("\nBefore continuing:")
    print("  - NoLimits 2 must be running locally with its API enabled.")
    print("  - Use desktop/non-VR mode unless --allow-vr is intentional.")
    print("  - OBS must be running with WebSocket enabled and the configured capture source present.")
    print("  - Close POC and any other client controlling the NL2 API.")
    print(f"  - Original captures will be retained under: {args.output_dir}")
    print("  - Existing canonical references are skipped unless --replace is used.\n")
    print("Planned rides: " + ", ".join(ride.name for ride in plan))
    if not args.yes and not _confirm(len(plan)):
        print("Cancelled; nothing captured or changed.")
        return 0

    from tools.nl2_obs_recorder import OBSRideRecorder
    password = args.obs_password if args.obs_password is not None else os.environ.get(
        "OBS_WEBSOCKET_PASSWORD", "")
    obs = None
    successes = []
    failures = []
    try:
        obs = OBSRideRecorder(
            args.output_dir,
            host=args.obs_host,
            port=args.obs_port,
            password=password,
            source=args.obs_source,
            window_timeout=args.obs_window_timeout,
            tail_s=args.obs_tail,
            lead_s=args.obs_lead,
        )
        total = len(plan)
        for number, ride in enumerate(plan, 1):
            print(f"\n[{number}/{total}] {ride.name} ({ride.park_key})", flush=True)
            try:
                csv_path = _capture_one_ride(ride, capture_rides, args, obs)
                print(f"  Capture complete: {csv_path}", flush=True)
                _register_capture(ride, csv_path)
                successes.append(ride.name)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                failures.append((ride.name, str(exc)))
                print(f"ERROR: {ride.name}: {exc}", file=sys.stderr, flush=True)
                print("  Continuing with the next ride; captured evidence is retained.", flush=True)
    except KeyboardInterrupt:
        print("\nInterrupted by operator.", file=sys.stderr)
        return 130
    finally:
        if obs is not None:
            obs.close()

    print("\nCanonical capture summary")
    for name in successes:
        print(f"  CREATED  {name}")
    for name, error in failures:
        print(f"  FAILED   {name}: {error}")
    if failures:
        print("Successful canonical references remain registered; rerun later for failed rides.")
        return 1
    print("All planned canonical rides were captured and registered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
