# capture_nl2_rides.py  ver 5
"""Capture repeatable NoLimits2 rides without launching POC or platform outputs.

Run ``python tools/capture_nl2_rides.py --list`` to see configured rides.
Run ``python tools/capture_nl2_rides.py`` for interactive ride/repeat selection.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.types import ExperienceState
from sims.nolimits2 import config
from sims.nolimits2.sim import Sim

AXES = ("surge", "sway", "heave", "roll", "pitch", "yaw")
RAW_FIELDS = (
    "state", "frame", "speed", "pos_x", "pos_y", "pos_z",
    "quat_x", "quat_y", "quat_z", "quat_w",
    "g_force_x", "g_force_y", "g_force_z",
)
# NL2 can briefly reject a second LOAD_PARK while a previous park is settling.
# Retry only that specific transient response; all other errors remain fatal.
BUSY_RETRIES = 5
BUSY_RETRY_DELAY_S = 2.0


class ParkLoadBusyError(RuntimeError):
    """NL2 is still processing a previous park operation."""


COLUMNS = (
    "ride", "repeat", "wall_time", "elapsed_s", "nl2_frame", "source_dt_s",
    "nl2_receive_monotonic_s", "sim_state", "valid", "heave_mode",
    *RAW_FIELDS, *("norm_" + x for x in AXES),
    "legacy_height_heave", "vertical_g_heave",
)


STATE_EVENT_COLUMNS = (
    "wall_time", "elapsed_s", "event", "state", "speed_mps", "station",
    "station_status", "view_train", "details",
)


class RideStateMonitor:
    """Low-rate transition log, no Festo/platform control and no polling thread."""

    def __init__(self, stream, ride_name):
        self.writer = csv.DictWriter(stream, fieldnames=STATE_EVENT_COLUMNS)
        self.writer.writeheader()
        self.start = time.monotonic()
        self.ride_name = ride_name
        self.last_key = None
        self.last_state = None

    def _log(self, sim, event, tm=None, details=""):
        obs = sim._station_observation()
        elapsed = time.monotonic() - self.start
        state = sim.state.name
        speed = "" if tm is None else f"{float(tm.speed):.3f}"
        station = "" if obs.identity is None else f"{obs.identity[0]}/{obs.identity[1]}"
        status = "" if obs.status is None else f"0x{obs.status:08x}"
        view_train = "" if tm is None else f"{tm.coaster_index}/{tm.train}"
        self.writer.writerow(dict(
            wall_time=datetime.now().isoformat(timespec="milliseconds"),
            elapsed_s=f"{elapsed:.3f}", event=event, state=state,
            speed_mps=speed, station=station, station_status=status,
            view_train=view_train, details=details,
        ))
        print(f"[STATE {elapsed:7.2f}s] {self.ride_name}: {event}: "
              f"state={state} speed={speed or '?'} station={station or '?'} "
              f"flags={status or '?'} {details}", flush=True)

    def observe(self, sim, tm):
        # Events are pushed by the NL2 adapter at dispatch/reset/departure
        # transitions, so they are not lost if the final state is unchanged.
        for event in sim.consume_state_events():
            details = {k: v for k, v in event.items() if k not in ("time", "event")}
            self._log(sim, event["event"], tm, json.dumps(details, sort_keys=True))
        obs = sim._station_observation()
        station_key = (
            obs.identity, obs.status if obs.valid else None, obs.valid,
            obs.occupied, obs.view_train_here, obs.can_dispatch,
        )
        if station_key != self.last_key:
            self.last_key = station_key
            self._log(sim, "station_changed", tm,
                      f"valid={obs.valid} occupied={obs.occupied} "
                      f"view_train={obs.view_train_here} "
                      f"other_train={obs.other_train_here} "
                      f"can_dispatch={obs.can_dispatch} manual={obs.manual}")
        if sim.state != self.last_state:
            self.last_state = sim.state
            self._log(sim, "observed_state", tm)


def selected_indices(spec: str, rides: tuple[tuple[str, str], ...]) -> list[int]:
    """Accept 1-based ride indices, exact ride names, or 'all'."""
    if not spec.strip():
        raise ValueError("No rides selected")
    if spec.strip().lower() == "all":
        return list(range(len(rides)))
    result = []
    for item in spec.split(","):
        item = item.strip()
        matches = ([int(item) - 1] if item.isdigit()
                   else [i for i, (name, _) in enumerate(rides) if name.lower() == item.lower()])
        if len(matches) != 1 or not 0 <= matches[0] < len(rides):
            raise ValueError(f"Unrecognized ride {item!r}; use --list")
        if matches[0] not in result:
            result.append(matches[0])
    return result


def safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_") or "ride"


def pump(sim: Sim, last_frame=None):
    """Process NL2 protocol and return one fresh adapter sample if available."""
    sim.service()
    sample = sim.read()
    tm = sim.client.latest_telemetry
    observer = getattr(sim, "_capture_state_monitor", None)
    if observer is not None:
        observer.observe(sim, tm)
    if not sample.telemetry_valid or tm is None or tm.frame == last_frame:
        return sample, None
    return sample, tm


def await_condition(sim, predicate, timeout_s, label, interval_s):
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        sample, tm = pump(sim)
        if predicate(sim, sample, tm):
            return
        time.sleep(interval_s)
    raise TimeoutError(f"Timed out waiting for {label} ({timeout_s:g}s)")



def wait_for_park_load(sim, request_id: int, ride_name: str, park_path: str,
                       timeout_s: float, interval_s: float):
    """Require the actual response to this LOAD_PARK, not merely READY state."""
    if request_id is None:
        raise RuntimeError(f"No LOAD_PARK request ID for {ride_name}: {park_path}")
    print(f"NL2 LOAD_PARK request {request_id}: {ride_name}", flush=True)
    print(f"  Configured park path: {park_path}", flush=True)
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        # service() may consume the response and clear _ride_load_request_id,
        # but NL2Protocol.request_result() retains it under the original ID.
        pump(sim)
        result = sim.client.request_result(request_id)
        if result is not None:
            outcome, detail = result
            if outcome == "error":
                message = f"NL2 rejected LOAD_PARK for {ride_name} ({park_path}): {detail}"
                if "processing another operation" in str(detail).casefold():
                    raise ParkLoadBusyError(message)
                raise RuntimeError(message)
            if outcome != "ok":
                raise RuntimeError(f"Unexpected LOAD_PARK response {result!r} for {ride_name}")
            if not sim._ride_load_pending and sim.state is ExperienceState.READY:
                print(f"NL2 LOAD_PARK acknowledged OK: {ride_name}", flush=True)
                return
        time.sleep(interval_s)
    raise TimeoutError(
        f"No confirmed successful LOAD_PARK for {ride_name} ({park_path}) "
        f"within {timeout_s:g}s"
    )


def wait_between_load_attempts(sim: Sim, seconds: float, interval_s: float):
    """Keep servicing NL2 while a previous park operation settles."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        pump(sim)
        time.sleep(min(interval_s, max(0.0, end - time.monotonic())))


def connect_recorder(sim: Sim, timeout_s: float, interval_s: float):
    """Connect once; verify the adapter's normal default-park startup load."""
    sim.connect()
    await_condition(
        sim,
        lambda s, *_: s._sim_connected() and s._ride_load_pending
        and s._ride_load_request_id is not None,
        timeout_s,
        "NL2 connection and initial park request",
        interval_s,
    )
    index = sim._selected
    ride_name, park_path = sim._rides[index]
    print(f"NL2 adapter startup (first selected study park): {ride_name}", flush=True)
    print("  Loaded via the normal POC park-loading sequence.", flush=True)
    wait_for_park_load(sim, sim._ride_load_request_id, ride_name, park_path,
                       timeout_s, interval_s)


def select_ride_for_capture(sim: Sim, target: int, timeout_s: float, interval_s: float):
    """Use the regular adapter ride change, retrying only NL2's transient busy response."""
    if sim._selected == target:
        return
    ride_name, park_path = sim._rides[target]
    print(f"Selecting ride index {target}: {ride_name}", flush=True)
    for attempt in range(BUSY_RETRIES + 1):
        if attempt == 0:
            sim.select_experience(target)
        else:
            # After a rejected LOAD_PARK the adapter already records target as
            # _selected. select_experience(target) would now be a no-op, so
            # reissue through the same internal method it normally calls.
            sim._begin_ride_load(target, startup=False)
        try:
            wait_for_park_load(sim, sim._ride_load_request_id, ride_name,
                               park_path, timeout_s, interval_s)
            return
        except ParkLoadBusyError:
            if attempt >= BUSY_RETRIES:
                raise RuntimeError(
                    f"NL2 remained busy after {BUSY_RETRIES + 1} LOAD_PARK "
                    f"attempts for {ride_name}"
                ) from None
            print(
                f"NL2 busy loading {ride_name}; retrying in "
                f"{BUSY_RETRY_DELAY_S:g}s ({attempt + 1}/{BUSY_RETRIES})",
                flush=True,
            )
            wait_between_load_attempts(sim, BUSY_RETRY_DELAY_S, interval_s)


def start_ride(sim: Sim, target: int, timeout_s: float, interval_s: float):
    # The caller keeps one Sim instance/connection across rides and repeats.
    # This mirrors POC ride selection instead of forcing a default-park load
    # every time another CSV is recorded.
    select_ride_for_capture(sim, target, timeout_s, interval_s)
    # The normal POC adapter owns station preparation, manual mode and dispatch.
    sim.prepare_for_activation()
    await_condition(sim, lambda s, *_: s.is_ready_to_start(),
                    timeout_s, "manual-mode station readiness", interval_s)
    sim.run()
    await_condition(sim, lambda s, *_: s.state is ExperienceState.RUNNING
                    and not s._dispatch_pending,
                    timeout_s, "dispatch", interval_s)

def capture_one(sim: Sim, ride_index: int, repeat: int, args, rides):
    ride_name, _ = rides[ride_index]
    stem = f"{safe_name(ride_name)}_repeat{repeat:02d}_{datetime.now():%Y%m%d_%H%M%S}"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    path = args.output_dir / f"{stem}.csv"
    event_path = args.output_dir / f"{stem}_state_events.csv"
    monitor_only = bool(getattr(args, "state_monitor", False))
    sample_count = 0
    last_frame = None
    last_source_time = None
    started_at = None
    left_station = False
    period_s = 1.0 / args.hz

    with event_path.open("w", newline="", encoding="utf-8") as events_stream:
        sim._capture_state_monitor = RideStateMonitor(events_stream, ride_name)
        try:
            print(f"Preparing {ride_name}, repeat {repeat}; NL2 at {args.host}:{config.PORT}", flush=True)
            start_ride(sim, ride_index, args.prepare_timeout, period_s)
            print("Dispatched; monitoring ride state..." if monitor_only
                  else "Dispatched; recording...", flush=True)
            started_at = time.monotonic()
            next_poll = started_at

            # State-only mode avoids writing high-rate telemetry CSV; both
            # modes retain a timestamped _state_events.csv beside the output.
            from contextlib import nullcontext
            frame_stream = (nullcontext(None) if monitor_only else
                            path.open("w", newline="", encoding="utf-8"))
            with frame_stream as stream:
                writer = None if monitor_only else csv.DictWriter(stream, fieldnames=COLUMNS)
                if writer is not None:
                    writer.writeheader()
                while True:
                    now = time.monotonic()
                    if now - started_at > args.max_duration:
                        raise TimeoutError(
                            f"Ride did not return to station after {args.max_duration:g}s")
                    sample, tm = pump(sim, last_frame)
                    if tm is not None:
                        last_frame = tm.frame
                        sample_count += 1
                        left_station = left_station or sim._view_train_departed
                        if writer is not None:
                            elapsed = time.monotonic() - started_at
                            source_dt = ("" if last_source_time is None
                                         else tm.received_at - last_source_time)
                            last_source_time = tm.received_at
                            legacy = sim._legacy_height_z(float(tm.pos_y))
                            vertical = max(-1.0, min(1.0,
                                (float(tm.g_force_y) - 1.0)
                                * config.TELEMETRY_INTENSITY_FACTOR))
                            row = dict(
                                ride=ride_name, repeat=repeat,
                                wall_time=datetime.now().isoformat(timespec="milliseconds"),
                                elapsed_s=f"{elapsed:.4f}", nl2_frame=tm.frame,
                                source_dt_s=source_dt,
                                nl2_receive_monotonic_s=tm.received_at,
                                sim_state=sample.state.name,
                                valid=int(sample.telemetry_valid),
                                heave_mode="legacy_height" if sim._legacy_heave_enabled else "vertical_g",
                            )
                            row.update({key: getattr(tm, key) for key in RAW_FIELDS})
                            row.update({f"norm_{axis}": float(value)
                                        for axis, value in zip(AXES, sample.transform)})
                            row.update(legacy_height_heave=legacy,
                                       vertical_g_heave=vertical)
                            writer.writerow(row)
                    if left_station and sim.state is ExperienceState.READY and sim.is_ready_to_start():
                        print(f"Completed: {event_path if monitor_only else path} "
                              f"({sample_count} frames processed)", flush=True)
                        return event_path if monitor_only else path
                    next_poll += period_s
                    time.sleep(max(0.0, next_poll - time.monotonic()))
        finally:
            # Never retain a reference to an output stream after closing it.
            sim._capture_state_monitor = None
            if started_at is not None:
                print(f"State events: {event_path}", flush=True)
                if not monitor_only and sample_count:
                    print(f"Saved {sample_count} frames to {path}", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="List configured NL2 rides")
    parser.add_argument("--rides", help="Comma-separated ride numbers/names, or all")
    parser.add_argument("--repeats", type=int, default=None, help="Number of runs per ride")
    parser.add_argument("--host", default="127.0.0.1", help="NL2 PC address")
    parser.add_argument("--hz", type=float, default=20.0, help="Polling frequency (default: 20 Hz)")
    parser.add_argument("--prepare-timeout", type=float, default=90.0)
    parser.add_argument("--max-duration", type=float, default=480.0)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "captures" / "nl2_ride_study")
    parser.add_argument(
        "--state-monitor", action="store_true",
        help="Select/dispatch a ride and print/save ride-state changes only (no telemetry CSV)",
    )
    args = parser.parse_args(argv)
    rides = tuple(config.RIDES)
    if args.list:
        for i, (name, _) in enumerate(rides, 1):
            print(f"{i:2d}: {name}")
        return 0
    for i, (name, _) in enumerate(rides, 1):
        print(f"{i:2d}: {name}")
    spec = args.rides if args.rides is not None else input("Select rides (numbers separated by comma, or all): ")
    repeats = args.repeats if args.repeats is not None else int(input("Repeats per ride [3]: ") or "3")
    if repeats < 1 or args.hz <= 0 or args.prepare_timeout <= 0 or args.max_duration <= 0:
        parser.error("repeats, hz and timeout values must be positive")
    try:
        indices = selected_indices(spec, rides)
    except ValueError as exc:
        parser.error(str(exc))
    print("This tool controls NL2 park loading and dispatch, but never starts POC/Festo outputs.")
    print("Selected study rides: " + ", ".join(rides[index][0] for index in indices), flush=True)
    # Load the first requested park directly at connection.  This overrides
    # neither the normal POC default nor later multi-ride selection.
    sim = Sim(address=args.host, startup_ride_index=indices[0])
    try:
        connect_recorder(sim, args.prepare_timeout, 1.0 / args.hz)
        for index in indices:
            for repeat in range(1, repeats + 1):
                try:
                    capture_one(sim, index, repeat, args, rides)
                except (TimeoutError, ConnectionError, OSError, RuntimeError) as exc:
                    print(f"ERROR: {rides[index][0]} repeat {repeat}: {exc}", file=sys.stderr)
                    return 1
    except (TimeoutError, ConnectionError, OSError, RuntimeError) as exc:
        print(f"ERROR: NL2 initial connection: {exc}", file=sys.stderr)
        return 1
    finally:
        sim.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
