# capture.py ver 2
"""Batch capture using the existing POC recorder and NL2 adapter.

No core platform controller, Festo, SHS, Qt, video recorder or Twin sender is
constructed. All control commands go to the local NoLimits simulator only.
"""
from __future__ import annotations
import csv
from dataclasses import asdict
import json
import logging
import math
from pathlib import Path
import time

from tools import capture_nl2_rides as existing
from core.types import ExperienceState
from core.motion_convention import MOTION_CONVENTION
from sims.nolimits2 import ride_config
from sims.nolimits2.sim import Sim
from .common import ROOT, SCHEMA, machine_info, sha256, source_hashes, stamp, unique_id, write_json
from .protocol import TimingProtocol
from .clock import clock
from .presets import DEFERRED, resolve_plan, ride_timeout
from .system_info import VR_HEADSET_ON, vr_summary

TIMING_FIELDS = (
    "elapsed_s", "receive_monotonic_s", "process_monotonic_s", "request_id",
    "send_monotonic_s", "send_end_monotonic_s", "rtt_ms", "queue_delay_ms",
    "phase", "is_duplicate_frame", "state", "frame", "view_mode",
    "coaster_index", "coaster_style", "train", "car", "seat", "speed", "pos_x", "pos_y", "pos_z",
    "quat_x", "quat_y", "quat_z", "quat_w", "g_force_x", "g_force_y", "g_force_z",
)
NET_FIELDS = ("elapsed_s", "kind", "message", "request_id", "request_message",
              "send_monotonic_s", "receive_monotonic_s", "process_monotonic_s",
              "rtt_ms", "queue_delay_ms", "payload_hex")
POLL_FIELDS = ("elapsed_s", "lateness_ms", "work_ms", "skipped_ticks")
EVENT_FIELDS = (*existing.STATE_EVENT_COLUMNS, "event_monotonic_s", "observed_monotonic_s")


class StudySim(Sim):
    """Same adapter, with a fresh-clock station-setup pass for localhost.

    In v191-v193 Sim.service captures `now` before draining the receive queue.
    A reply processed in that drain has received_at > now, so the all-stations
    setup pass can reject every fresh reply on a fast, consistently responsive
    loopback connection. Re-evaluate setup after that drain with its own current
    clock. The manager retains its request correlation, rate limits and all
    interlocks. No production source is patched; no extra running-ride polling.
    """
    def service(self):
        super().service()
        if self.client.is_connected and self.state in (ExperienceState.READY, ExperienceState.RESETTING):
            self._service_station_modes()


def park_choices(spec="all"):
    """Stable capture-store indices; never enumerate per-platform variants."""
    hidden = set(getattr(ride_config, "OPERATOR_HIDDEN_PARKS", ("grandnational",)))
    entries = list(ride_config.PARKS.items())
    allowed = [(i, k, p) for i, (k, p) in enumerate(entries) if k not in hidden]
    if spec.casefold().strip() == "all":
        return [x for x in allowed if x[1] not in DEFERRED]
    display_numbers = {str(i): x[1] for i, x in enumerate(
        (x for x in allowed if x[1] not in DEFERRED), 1)}
    selected = []
    for token in spec.split(","):
        token = token.strip()
        choices = [x for j, x in enumerate(allowed, 1)
                   if token.casefold() in (x[1].casefold(), x[2]["name"].casefold())
                   or display_numbers.get(token) == x[1]]
        if len(choices) != 1:
            raise ValueError(f"Unknown/disabled ride {token!r}; use capture --list")
        if choices[0] not in selected:
            selected.append(choices[0])
    if not selected:
        raise ValueError("No rides selected")
    return selected


def resolve_paths(choices, parks_root=None):
    """Override the MDX root in memory only; never rewrite ride_config.py."""
    for _, key, park in choices:
        if parks_root is not None:
            old = Path(park["path"])
            try:
                relative = old.relative_to(Path(ride_config.BASE_RIDE_PATH))
            except ValueError:
                raise ValueError(f"Cannot relocate park path: {old}") from None
            park["path"] = str(Path(parks_root) / relative)
        path = Path(park["path"])
        if not path.is_file():
            raise FileNotFoundError(f"Missing local park for {key}: {path}. Use --parks-root if needed.")


class TrialRecorder:
    """Adapter observer: all wire samples plus original player-compatible CSV.

    One shared monotonic epoch for telemetry, protocol and state events. Setup
    is in the event/protocol logs; playable telemetry begins at pre-roll.
    """
    def __init__(self, directory, stem, metadata, args, clock=clock):
        self.directory, self.stem, self.meta = Path(directory), stem, metadata
        self.args, self.clock = args, clock
        self.zero = clock()
        self.phase = "prepare"
        self.last_frame = None
        self.last_raw_frame = None
        self.last_source = None
        self.last_raw = None
        self.first_motion_pending = []
        self.armed = False
        self.last_observation = None
        self.last_state = None
        self.samples = 0
        self.raw_samples = 0
        self.vr_on_samples = 0
        self.vr_off_samples = 0
        self.issues = set()
        self.marks = {}
        self.expected_view = None
        self.files = {}
        self.streams = []
        self.directory.mkdir(parents=True, exist_ok=True)
        self.writer = self._writer("capture", ".csv", existing.COLUMNS)
        self.timing = self._writer("timing", "_timing.csv", TIMING_FIELDS)
        self.net = self._writer("protocol", "_protocol.csv", NET_FIELDS)
        self.events = self._writer("events", "_state_events.csv", EVENT_FIELDS)
        self.polls = self._writer("polls", "_polls.csv", POLL_FIELDS)
        self.meta.update(schema=SCHEMA, tool_version="2.1.0", status="running", started_utc=stamp(),
                         epoch_monotonic_s=self.zero, files=self.files,
                         first_motion_threshold_mps=args.motion_threshold,
                         first_motion_confirmation_frames=3)
        self.metadata_path = self.directory / (stem + "_trial.json")
        # Existing Sim Player understands these two monotonic event anchors,
        # including when no OBS video was recorded. Both files use one epoch.
        sync = self.directory / (stem + "_video_sync.json")
        self.files["player_sync"] = sync.name
        write_json(sync, {"schema": SCHEMA, "video_recorded": False,
                          "note": "State-event alignment only; no video recorded by timing harness",
                          "events": {"state_event_elapsed_zero": {"monotonic_s": self.zero},
                                     "capture_elapsed_zero": {"monotonic_s": self.zero}}})
        self.persist()

    def _writer(self, key, suffix, fields):
        path = self.directory / (self.stem + suffix)
        stream = path.open("x", encoding="utf-8", newline="")
        self.streams.append(stream)
        self.files[key] = path.name
        w = csv.DictWriter(stream, fieldnames=fields)
        w.writeheader()
        return w

    def persist(self):
        self.meta.update(marks=self.marks, quality_issues=sorted(self.issues),
                         sample_count=self.samples, raw_reply_count=self.raw_samples,
                         observed_vr=vr_summary(self.vr_on_samples, self.vr_off_samples))
        write_json(self.metadata_path, self.meta)

    def mark(self, name, when=None):
        if name not in self.marks:
            self.marks[name] = (self.clock() if when is None else when) - self.zero

    def event(self, sim, name, when=None, details=None):
        observed = self.clock()
        when = observed if when is None else when
        obs = sim._station_observation()
        tm = sim.client.latest_telemetry
        self.events.writerow(dict(
            wall_time=stamp(), elapsed_s=f"{when-self.zero:.9f}", event=name,
            state=sim.state.name, speed_mps=tm.speed if tm else "",
            station="/".join(map(str, obs.identity)) if obs.identity else "",
            station_status=f"0x{obs.status:08x}" if obs.status is not None else "",
            view_train=f"{tm.coaster_index}/{tm.train}" if tm else "",
            details=json.dumps(details or {}, sort_keys=True), event_monotonic_s=when,
            observed_monotonic_s=observed))

    def arm(self, sim):
        self.armed = True
        self.phase = "preroll"
        self.mark("capture_armed")
        tm = sim.client.latest_telemetry
        self.expected_view = (tm.coaster_index, tm.train, tm.car, tm.seat, tm.view_mode) if tm else None
        obs = sim._station_observation()
        self.meta.update(view_identity=self.expected_view,
                         departure_station=obs.identity,
                         nl2_version=sim.client.version)
        self.event(sim, "timing_capture_armed", details={"view": self.expected_view})
        self.persist()

    def _packet(self, sim, r):
        def seconds(key):
            return r[key] / 1e9 if r.get(key) is not None else None
        tx, rx, proc = seconds("send_ns"), seconds("receive_ns"), seconds("process_ns")
        rtt = 1000*(rx-tx) if rx is not None and tx is not None else ""
        delay = 1000*(proc-rx) if rx is not None and proc is not None else ""
        msg = sim.client._MSG_NAMES.get(r["msg"], str(r["msg"]))
        self.net.writerow(dict(
            elapsed_s=(rx if rx is not None else tx)-self.zero,
            kind=r["kind"], message=msg, request_id=r["request_id"],
            request_message=sim.client._MSG_NAMES.get(r["request_msg"], r["request_msg"]),
            send_monotonic_s=tx, receive_monotonic_s=rx, process_monotonic_s=proc,
            rtt_ms=rtt, queue_delay_ms=delay, payload_hex=r["payload_hex"]))
        if r["kind"] == "tx" and r["msg"] == sim.client.N_MSG_DISPATCH and self.armed:
            if "dispatch_tx" not in self.marks:
                self.mark("dispatch_tx", tx)
                self.meta["dispatch_request_id"] = r["request_id"]
        if (r["kind"] == "rx" and r["request_id"] == self.meta.get("dispatch_request_id")
                and r["msg"] in (sim.client.N_MSG_OK, sim.client.N_MSG_ERROR)):
            self.mark("dispatch_reply", rx)
            if r["msg"] == sim.client.N_MSG_ERROR:
                self.issues.add("dispatch_rejected")
                self.meta["dispatch_error"] = bytes.fromhex(r["payload_hex"]).decode("utf-8", "replace")
        tm = r.get("telemetry")
        if tm is None or not self.armed:
            return
        self.last_raw = rx
        self.raw_samples += 1
        if tm.state & VR_HEADSET_ON:
            self.vr_on_samples += 1
        else:
            self.vr_off_samples += 1
        duplicate = tm.frame == self.last_raw_frame
        values = asdict(tm)
        row = {key: values.get(key, "") for key in TIMING_FIELDS}
        row.update(elapsed_s=rx-self.zero, receive_monotonic_s=rx,
                   process_monotonic_s=proc, send_monotonic_s=tx,
                   send_end_monotonic_s=seconds("send_end_ns"), request_id=r["request_id"],
                   rtt_ms=rtt, queue_delay_ms=delay, phase=self.phase,
                   is_duplicate_frame=int(duplicate))
        self.timing.writerow(row)
        if "dispatch_tx" not in self.marks or rx-self.zero < self.marks["dispatch_tx"]:
            self.last_raw_frame = tm.frame
            return
        if self.expected_view != (tm.coaster_index, tm.train, tm.car, tm.seat, tm.view_mode):
            self.issues.add("view_identity_changed")
        if tm.paused:
            self.issues.add("paused_during_run")
        if not tm.in_play_mode:
            self.issues.add("left_play_mode_during_run")
        if self.last_raw_frame is not None and tm.frame < self.last_raw_frame:
            # Accept ordinary 32-bit wrap, not a new simulation epoch/reset.
            if not (self.last_raw_frame > 0xF0000000 and tm.frame < 0x10000000):
                self.issues.add("frame_counter_reset_during_run")
        if not duplicate and "first_motion" not in self.marks:
            if (tm.in_play_mode and not tm.paused and abs(tm.speed) >= self.args.motion_threshold):
                if self.first_motion_pending and rx-self.first_motion_pending[-1] > .5:
                    self.first_motion_pending.clear()
                self.first_motion_pending.append(rx)
                if len(self.first_motion_pending) >= 3:
                    self.mark("first_motion", self.first_motion_pending[0])
                    self.mark("first_motion_confirmed", rx)
                    self.event(sim, "timing_first_motion", self.first_motion_pending[0],
                               {"confirmed_at": rx, "threshold_mps": self.args.motion_threshold})
            else:
                self.first_motion_pending.clear()
        self.last_raw_frame = tm.frame

    def observe(self, sim, tm):
        for packet in sim.client.drain_study_records():
            self._packet(sim, packet)
        for e in sim.consume_state_events():
            name = e["event"]
            when = e.get("time", self.clock())
            details = {k: v for k, v in e.items() if k not in ("time", "event")}
            self.event(sim, name, when, details)
            if name in ("park_reset_sent", "reset_view_station_confirmed", "view_train_dispatched",
                        "view_train_departed", "view_train_returned"):
                self.mark(name, when)
            if name == "view_train_returned":
                self.meta["arrival_station"] = e.get("station")
            if name == "auxiliary_train_dispatched":
                self.meta["auxiliary_dispatches"] = self.meta.get("auxiliary_dispatches", 0) + 1
        obs = sim._station_observation()
        observation = (obs.identity, obs.status, obs.valid)
        if observation != self.last_observation:
            self.event(sim, "station_changed", details={
                "valid": obs.valid, "manual": obs.manual, "occupied": obs.occupied,
                "view_train_here": obs.view_train_here, "can_dispatch": obs.can_dispatch})
            self.last_observation = observation
        if sim.state.name != self.last_state:
            self.event(sim, "observed_state")
            self.last_state = sim.state.name
        # Original CSV columns and original adapter normalization. Only the
        # newest sample has a corresponding transform, exactly as base.pump().
        if not (self.armed and tm is not None and tm.frame != self.last_frame
                and getattr(sim, "_telemetry_valid", False)):
            return
        self.last_frame = tm.frame
        row = dict(ride=self.meta["ride"], repeat=self.meta["repeat"],
                   wall_time=stamp(),
                   elapsed_s=self.clock()-self.zero, nl2_frame=tm.frame,
                   source_dt_s="" if self.last_source is None else tm.received_at-self.last_source,
                   nl2_receive_monotonic_s=tm.received_at, sim_state=sim.state.name,
                   valid=1, heave_mode=sim.heave_mode, transform_convention=MOTION_CONVENTION)
        self.last_source = tm.received_at
        row.update({k: getattr(tm, k) for k in existing.RAW_FIELDS})
        row.update({"norm_"+a: float(v) for a, v in zip(existing.AXES, sim._last_transform)})
        row.update(legacy_height_heave=sim._legacy_height_z(float(tm.pos_y)),
                   vertical_g_heave=existing.config.vertical_g_heave(tm.g_force_y))
        self.writer.writerow(row)
        self.samples += 1

    def finish(self, sim, status, error=""):
        self.meta.update(status=status, error=error, finished_utc=stamp(),
                         duration_observed_s=self.clock()-self.zero,
                         transport_overflow=sim.client.study_overflow)
        if sim.client.study_overflow:
            self.issues.add("transport_records_lost")
        for f in self.streams:
            f.close()
        self.meta["file_sha256"] = {k: sha256(self.directory/v) for k, v in self.files.items()}
        self.persist()


def record_trial(sim, index, park_key, park, repeat, warmup, folder, args, machine, hashes, park_hash):
    stem = f"{existing.safe_name(park['name'])}_repeat{repeat:02d}_{unique_id('timing')}"
    meta = dict(ride=park["name"], park_key=park_key, repeat=repeat, warmup=warmup,
                transform_convention=MOTION_CONVENTION,
                machine=machine, source_hashes=hashes, park_sha256=park_hash,
                park_path=park["path"], hz=args.hz, conditions=args.conditions,
                reset_each_run=True, protocol_port=args.port,
                ride_timeout_s=getattr(args, "_trial_timeout", None) or args.max_duration or 600.)
    recorder = TrialRecorder(folder, stem, meta, args)
    sim._capture_state_monitor = recorder
    status, error = "incomplete", ""
    try:
        existing.select_ride_for_capture(sim, index, args.prepare_timeout, 1/args.hz)
        # Force a reproducible reset for EVERY attempt (including the first).
        # The production adapter manages manual-mode setup before/after reset.
        sim._start_park_reset()
        existing.await_condition(sim, lambda s, *_: s.is_ready_to_start(),
                                 args.prepare_timeout, "post-reset viewed-train readiness", 1/args.hz)
        def before_dispatch():
            recorder.arm(sim)
            existing.wait_between_load_attempts(sim, args.pre_roll, 1/args.hz)
            recorder.phase = "dispatching"
            recorder.mark("start_request")
            recorder.event(sim, "timing_start_requested")
        existing.start_ride(sim, index, args.prepare_timeout, 1/args.hz,
                            before_dispatch=before_dispatch)
        recorder.phase = "riding"
        period, deadline = 1/args.hz, clock()
        last_flush = deadline
        while True:
            before = clock()
            lateness = max(0.0, before-deadline)
            existing.pump(sim)
            after = clock()
            if not sim.client.is_connected:
                raise ConnectionError("NL2 disconnected during recording")
            elapsed_dispatch = after-recorder.zero-recorder.marks.get("dispatch_tx", 0.0)
            if "dispatch_rejected" in recorder.issues:
                raise RuntimeError("NL2 rejected the dispatch command")
            if "view_train_departed" not in recorder.marks and elapsed_dispatch > args.depart_timeout:
                raise TimeoutError("No confirmed viewed-train departure")
            if elapsed_dispatch > recorder.meta["ride_timeout_s"]:
                raise TimeoutError("Ride did not return within max-duration")
            if recorder.last_raw is not None and after-recorder.last_raw > args.telemetry_timeout:
                raise TimeoutError("Telemetry replies stopped")
            returned = recorder.marks.get("view_train_returned")
            if returned is not None:
                recorder.phase = "tail"
                if after-recorder.zero >= returned+args.tail:
                    if not all(k in recorder.marks for k in ("dispatch_tx", "first_motion", "view_train_departed")):
                        raise RuntimeError("Arrival without a complete dispatch/motion/departure sequence")
                    status = "complete"
                    break
            deadline += period
            skipped = max(0, math.floor((after-deadline)/period)+1) if after > deadline else 0
            deadline += skipped*period
            recorder.polls.writerow(dict(elapsed_s=after-recorder.zero,
                                         lateness_ms=lateness*1000, work_ms=(after-before)*1000,
                                         skipped_ticks=skipped))
            if after-last_flush >= 2:
                for f in recorder.streams:
                    f.flush()
                last_flush = after
            time.sleep(max(0.0, deadline-clock()))
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
        try:
            obs=sim._station_observation()
            recorder.meta["failure_diagnostics"]={
                "state":sim.state.name,"station":obs.identity,"station_status":obs.status,
                "station_valid":obs.valid,"view_train_here":obs.view_train_here,
                "last_protocol_error":sim.client.last_error,
                "station_setup_error":str(getattr(sim._station_modes,"error","")),
                "latest_frame":getattr(sim.client.latest_telemetry,"frame",None)}
            recorder.event(sim,"timing_attempt_failed",details=recorder.meta["failure_diagnostics"])
        except Exception:
            pass
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
    finally:
        # Never leave a pending authorization after cancellation/failure.
        try:
            sim.cancel_pending_dispatch()
            recorder.observe(sim, sim.client.latest_telemetry)
        except Exception:
            pass
        sim._capture_state_monitor = None
        recorder.finish(sim, status, error)
    print(f"{status.upper()}: {park['name']} repeat {repeat}: {recorder.samples} player samples, "
          f"{recorder.raw_samples} wire replies" + (f"; {error}" if error else ""), flush=True)
    return recorder.metadata_path, status


def capture(args):
    args = resolve_plan(args)
    choices = park_choices(args.rides)
    if args.list:
        for number, (_, key, park) in enumerate(park_choices(), 1):
            print(f"{number:2d}  {key:16s} {park['name']}")
        print("Default presets exclude Grand National and Scenic Run. --rides scenicrun enables the latter explicitly.")
        return 0
    resolve_paths(choices, args.parks_root)
    args.output = Path(args.output).resolve()
    session_dir = args.output / unique_id("session")
    session_dir.mkdir(parents=True, exist_ok=False)
    machine = machine_info(args.conditions)
    from tools.nl2_sync.host import host_details
    machine["hardware"] = host_details()
    machine["declared_monitors"] = args.monitors
    hashes = source_hashes()
    parks_hash = {k: sha256(p["path"]) for _, k, p in choices}
    session = dict(schema=SCHEMA, started_utc=stamp(), machine=machine, source_hashes=hashes,
                   transform_convention=MOTION_CONVENTION,
                   observed_vr=vr_summary(0, 0), selected_parks=[k for _, k, _ in choices], repeats=args.repeats,
                   warmups=args.warmups, hz=args.hz, trials=[], status="running",
                   reset_each_run=True, video_recording=False, tool_version="2.1.0", preset=args.preset, attempt_errors=[], skipped=[], extensions=[], arguments=vars(args).copy())
    session["arguments"] = {k: str(v) if isinstance(v, Path) else v
                            for k, v in session["arguments"].items() if k != "func"}
    manifest = session_dir / "session.json"
    write_json(manifest, session)
    log = logging.FileHandler(session_dir/"adapter.log", encoding="utf-8")
    log.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    rootlog = logging.getLogger()
    oldlevel = rootlog.level
    rootlog.setLevel(logging.DEBUG if args.debug else logging.INFO)
    rootlog.addHandler(log)
    sim = None
    failures = 0
    interrupted = False
    def connect(index):
        for attempt in range(args.load_retries + 1):
            client = TimingProtocol(port=args.port)
            candidate = StudySim(address="127.0.0.1", client=client,
                            startup_ride_index=index, capture_mode=True)
            try:
                existing.connect_recorder(candidate, args.prepare_timeout, 1/args.hz)
            except existing.ParkLoadBusyError as exc:
                candidate.disconnect()
                session["attempt_errors"].append(dict(stage="connect_busy", attempt=attempt+1,
                                                        when=stamp(), error=str(exc)))
                write_json(manifest,session)
                if attempt >= args.load_retries:
                    raise
                wait=args.retry_delay*(attempt+1)
                print(f"NL2 is busy; retry {attempt+1}/{args.load_retries} after {wait:g}s",flush=True)
                time.sleep(wait)
                continue
            except BaseException:
                candidate.disconnect()
                raise
            client.drain_study_records()
            candidate.consume_state_events()
            return candidate
        raise RuntimeError("Connection retry budget exhausted")

    def should_extend(paths):
        from .analysis import read_trial, compare
        from .quality import choose_reference
        traces=[read_trial(p) for p in paths]
        good=[t for t in traces if not t.issues]
        if len(good)<3:return "fewer than three qualified completed trials"
        ref=choose_reference(good)
        for t in good:
            if t.summary["gaps_above_threshold"]:return "observed telemetry gap"
            if t is ref:continue
            c,_,_=compare(ref,t)
            if not c.get("sufficient_coverage") or c.get("motion_clock_max_abs_s",0)>.2:
                return "coverage or phase error requires review"
        return ""
    print(f"Timing study: {machine['label']}; NL2 127.0.0.1:{args.port}; output {session_dir}")
    print(f"{len(choices)} parks x {args.repeats} measured runs; {args.warmups} warmups per park.")
    print("Close POC and other NL2 clients. No platform/SHS/Festo/Twin output. Ctrl+C stops.")
    try:
        for index, key, park in choices:
            consecutive_failures=0
            measured_paths=[];durations=[]
            target=args.warmups+args.repeats
            extended=False
            number=0
            while number < target:
                number+=1
                warmup=number<=args.warmups
                rep=number if warmup else number-args.warmups
                print(f"\n{'WARMUP' if warmup else 'MEASURED'} {park['name']} {rep}",flush=True)
                args._trial_timeout=ride_timeout(key,args,durations)
                print(f"  Ride return timeout: {args._trial_timeout:.0f}s",flush=True)
                try:
                    if sim is None:
                        sim=connect(index)
                    trial,status=record_trial(sim,index,key,park,rep,warmup,
                                              session_dir/key,args,machine,hashes,parks_hash[key])
                    session["trials"].append(str(trial.relative_to(session_dir)))
                    if not warmup:measured_paths.append(trial)
                    if status=="complete":
                        m=json.loads(trial.read_text(encoding="utf-8"))["marks"]
                        durations.append(m["view_train_returned"]-m["dispatch_tx"])
                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    status="incomplete"
                    session["attempt_errors"].append(dict(park=key,repeat=rep,stage="connect_or_record",
                        when=stamp(),error=f"{type(exc).__name__}: {exc}"))
                    print(f"ATTEMPT FAILED: {park['name']}: {type(exc).__name__}: {exc}",flush=True)
                write_json(manifest,session)
                if status!="complete":
                    failures+=1;consecutive_failures+=1
                    if sim is not None:sim.disconnect()
                    sim=None
                    if args.fail_fast:raise RuntimeError("Stopped after failed trial (--fail-fast)")
                    if consecutive_failures>=args.max_park_failures:
                        session["skipped"].append(dict(park=key,remaining=target-number,
                            reason="consecutive failure limit; proceeding to next park"))
                        break
                    time.sleep(args.retry_delay)
                else:
                    consecutive_failures=0
                    if args.between_runs:existing.wait_between_load_attempts(sim,args.between_runs,1/args.hz)
                if number==target and args.extend_uncertain and not extended and args.repeats<5:
                    extended=True
                    reason=should_extend(measured_paths)
                    if reason:
                        extra=min(2,5-args.repeats)
                        target+=extra
                        session["extensions"].append(dict(park=key,extra_runs=extra,reason=reason))
                        print(f"  Adding {extra} measured runs: {reason}",flush=True)
                        write_json(manifest,session)
    except KeyboardInterrupt:
        interrupted = True
        session["error"] = "Interrupted by operator"
        print("Stopped. Partial capture is retained and excluded from timing conclusions.")
    except Exception as exc:
        failures += 1
        session["error"] = f"{type(exc).__name__}: {exc}"
        print("STUDY ERROR:", session["error"])
    finally:
        if sim is not None:
            try:
                sim.cancel_pending_dispatch()
            finally:
                sim.disconnect()
        session.update(status="interrupted" if interrupted else "finished", failures=failures,
                       finished_utc=stamp())
        # Include a Ctrl+C trial whose normal return path was interrupted.
        session["trials"] = [str(p.relative_to(session_dir)) for p in sorted(session_dir.rglob("*_trial.json"))]
        vr_on = vr_off = 0
        for relative in session["trials"]:
            try:
                status = json.loads((session_dir / relative).read_text(encoding="utf-8")).get("observed_vr", {})
                vr_on += int(status.get("headset_on_samples", 0))
                vr_off += int(status.get("headset_off_samples", 0))
            except (OSError, ValueError, TypeError):
                pass
        session["observed_vr"] = vr_summary(vr_on, vr_off)
        print(f"Observed NL2 VR headset flag: {session['observed_vr']['state']} "
              f"({vr_on} on, {vr_off} off telemetry samples)", flush=True)
        write_json(manifest, session)
        rootlog.removeHandler(log)
        rootlog.setLevel(oldlevel)
        log.close()
    if not args.no_report and session["trials"]:
        from .analysis import analyse_paths
        try:
            report_dir = analyse_paths([session_dir], session_dir/"report", reference_machine=machine["host"])
            print("Report:", report_dir/"report.html")
        except Exception as exc:
            print(f"Analysis failed; recordings retained for re-analysis: {exc}")
            failures += 1
    return 130 if interrupted else (1 if failures else 0)
