#sim.py  ver 19
from __future__ import annotations

import csv
import logging
import math
import queue
import socket
import threading
import time
from collections import deque
from enum import IntEnum
from pathlib import Path
from statistics import median
from typing import Optional

from core.sim_interface import SimInterface
from core.types import ExperienceState, SimSample, SimStatus
from sims.spacecoaster import config
from sims.spacecoaster.control import SpaceCoasterControl

log = logging.getLogger(__name__)


class NativeState(IntEnum):
    INITIALIZING = 0
    WAITING = 1
    READY = 2
    RUNNING = 3
    COMPLETED = 4


_STATE_NAMES = ("Initializing", "Waiting", "Ready", "Running", "Completed")


class Sim(SimInterface):
    """POC adapter for the legacy Unity SpaceCoaster interface.

    The state sequencing, delays, mouse clicks, and CSV-motion triggering are
    intentionally kept equivalent to runtime/SimpleSims/spacecoaster.py.
    The Unity state and xyzrpy streams are known to be unreliable; they are not
    "corrected" here. xyzrpy/posrpy packets pace replay of the recorded CSV
    motion. The position form also provides a low-cost live fan-speed cue.
    """

    name = "SpaceCoaster"
    run_label = "Start Ride"
    pause_label = "Pause"
    service_period_ms = config.SERVICE_PERIOD_MS
    hot_switch = True
    supports_washout = False
    supports_recorded_start = True
    fan_config = config.FAN_CONFIG
    ui_tabs = ()

    def __init__(
        self,
        address="127.0.0.1",
        control: Optional[SpaceCoasterControl] = None,
        sleep_func=time.sleep,
        port: int = config.PORT,
    ):
        self.address = address
        self.port = int(port)
        self.control = control or SpaceCoasterControl()
        self._sleep = sleep_func

        self.state = ExperienceState.DISCONNECTED
        self.native_state = -1
        self._status_text = "Waiting for SpaceCoaster"
        self._selected = 0
        self._experiences = ("SpaceCoaster",)

        self._telemetry = []
        self._levels = (0.0,) * 6
        self._fan_speed = 0.0
        self._fan_speed_time = 0.0
        self._position_speed_position = None
        self._position_speed_time = 0.0
        self._position_speed_raw = deque(
            maxlen=max(1, int(self.fan_config.get("position_speed_median_samples", 1)))
        )
        self._position_speed_average = deque()
        self._position_speed_average_total = 0.0
        self.frame_number = int(config.START_FRAME)
        self.start_frame = int(config.START_FRAME)
        self.start_time = 0.0
        self._telemetry_loaded = self._read_telemetry()
        self._normal_completion_event = False

        self._xyzrpy_q = queue.Queue()
        self._cmd_q = queue.Queue()
        self._stop_event = threading.Event()
        self._listener_thread = None
        self._socket = None
        self._listener_ready = threading.Event()

        # Because Unity's message stream is known to be unreliable, connection
        # is latched after the first valid SpaceCoaster datagram.  Do not use a
        # freshness timeout here; the deployed Windows heartbeat will later own
        # PC reachability while this flag represents the Unity datalink seen.
        self._sim_connected = False
        self.last_message_time = 0.0
        self._last_motion_data_time = None
        self.last_error = ""
        self._unknown_udp_counts = {}
        self._ready_due_at = None
        self._startup_sync_due_at = None
        self._startup_sync_complete = False

    def set_host_service(self, host_service) -> None:
        """Attach the shared POC Sim Host Service client for Windows-side controls."""
        self.control.set_host_service(host_service)

    # ------------------------------------------------------------------
    # SimInterface
    # ------------------------------------------------------------------
    def connect(self) -> None:
        if self._listener_thread and self._listener_thread.is_alive():
            return

        self._stop_event.clear()
        self._listener_ready.clear()
        self._reset_fan_speed_tracking()
        self.native_state = -1
        self._ready_due_at = None
        # Do not force a lobby reset here. connect() is also used for a fresh
        # NL2 -> SpaceCoaster launch, whose normal Unity startup sequence must
        # be allowed to reach READY without an injected recovery command. SHS
        # owns restart recovery because it knows whether Unity was already
        # running before the POC startup request.
        self._startup_sync_due_at = None
        self._startup_sync_complete = False
        self._normal_completion_event = False
        self._listener_thread = threading.Thread(
            target=self._listener_loop,
            name="SpaceCoasterUDP",
            daemon=True,
        )
        self._listener_thread.start()
        self._status_text = "Waiting for SpaceCoaster"
        self.state = ExperienceState.DISCONNECTED

    def disconnect(self) -> None:
        self._stop_event.set()
        sock = self._socket
        self._socket = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        self._sim_connected = False
        self._last_motion_data_time = None
        self._reset_fan_speed_tracking()
        self._ready_due_at = None
        self._startup_sync_due_at = None
        self._startup_sync_complete = False
        self.state = ExperienceState.DISCONNECTED
        self._status_text = "Disconnected"

    def read(self) -> SimSample:
        # Preserve current SpaceCoaster behavior: service is driven from read().
        self.service()
        telemetry_valid = (
            self._sim_connected
            and self._telemetry_loaded
            and self.state is ExperienceState.RUNNING
            and self._motion_data_fresh()
        )
        return SimSample(
            self._levels,
            self.state,
            telemetry_valid,
            self._fresh_fan_speed() if telemetry_valid else None,
        )

    def service(self) -> None:
        # The current interface processes every pending command/state message
        # before processing at most one telemetry frame.
        try:
            while True:
                try:
                    msg = self._cmd_q.get_nowait()
                except queue.Empty:
                    break
                self._process_command_msg(msg)

            self._service_startup_sync()
            self._service_delayed_ready()

            if (
                not self._xyzrpy_q.empty()
                and self.native_state == NativeState.RUNNING
                and self.state is ExperienceState.RUNNING
            ):
                if self.frame_number < len(self._telemetry):
                    frame = self.frame_number
                    self._levels = tuple(self._telemetry[frame])
                    self.frame_number += 1

                    # Preserve the established motion behavior: packet arrival
                    # remains the pacing trigger for the next recorded frame.
                    self._drain_xyzrpy_queue()
        except Exception:
            log.exception("SpaceCoaster service error")

    def starting_transform(self, sample: SimSample):
        # Live transform wins when valid; READY generally has no live pacing
        # telemetry, so use the first configured recorded transform instead.
        current = super().starting_transform(sample)
        if current is not None:
            return current
        if not self._telemetry_loaded or not (0 <= self.start_frame < len(self._telemetry)):
            return None
        return tuple(self._telemetry[self.start_frame])

    def consume_normal_completion(self) -> bool:
        event = self._normal_completion_event
        self._normal_completion_event = False
        return event

    def run(self) -> None:
        self._normal_completion_event = False
        # POC remains READY until Unity sends its READY -> RUNNING state event.
        # Discard pacing packets accumulated while READY/paused so recorded
        # telemetry always starts from a fresh Unity pacing event.
        self._drain_xyzrpy_queue()
        self.control.run()
        self.frame_number = self.start_frame
        self._reset_fan_speed_tracking()

    def pause(self) -> None:
        # The POC pause button is a pause/resume toggle. SpaceCoaster's v8 UDP
        # interface exposes explicit commands, so select the semantic command
        # from the current POC state rather than sending a toggle.
        if self.state is ExperienceState.RUNNING:
            if self.control.pause():
                self._reset_fan_speed_tracking()
                self.state = ExperienceState.PAUSED
                self._status_text = "SpaceCoaster paused"
        elif self.state is ExperienceState.PAUSED:
            if self.control.resume():
                self._reset_fan_speed_tracking()
                self.state = ExperienceState.RUNNING
                self._status_text = "SpaceCoaster running"

    def prepare_for_activation(self) -> None:
        # A normal activation while already ready requires no Unity action.
        pass

    def recover_from_estop(self) -> None:
        """Return an interrupted ride to MenuMSU lobby for a fresh start."""
        # The CSV is the final SpaceCoaster motion profile. Rewind it and
        # discard stale pacing packets before asking Unity to return to lobby.
        self.frame_number = self.start_frame
        self._drain_xyzrpy_queue()
        self._reset_fan_speed_tracking()
        if not self.control.lobby():
            raise RuntimeError("Unable to queue SpaceCoaster lobby recovery command")
        self.state = ExperienceState.RESETTING
        self._status_text = "Returning SpaceCoaster to lobby"

    def is_ready_to_start(self) -> bool:
        return bool(
            self._sim_connected
            and self.native_state == NativeState.READY
            and self.state is ExperienceState.READY
        )

    def reset_vr(self) -> None:
        self.control.reset_vr()

    def get_experiences(self):
        return self._experiences

    def select_experience(self, index: int) -> None:
        if index != 0:
            raise IndexError(index)
        self._selected = 0

    def get_status(self) -> SimStatus:
        pc_connected = self._is_localhost(self.address)
        sim_connected = self._sim_connected

        # Motion comes from the local CSV, but Unity telemetry packets pace its
        # frames. State/config traffic proves the simulator link only; it must
        # not keep the motion-data indicator green after pacing has stopped.
        data_connected = (
            sim_connected and self._telemetry_loaded and self._motion_data_fresh()
        )

        return SimStatus(
            pc_connected=pc_connected,
            sim_connected=sim_connected,
            data_connected=data_connected,
            text=self._status_text,
            entity_name=self._experiences[0],
            busy=self.state is ExperienceState.RESETTING,
            ready_to_start=self.is_ready_to_start(),
            activity="resetting" if self.state is ExperienceState.RESETTING else "",
        )

    # ------------------------------------------------------------------
    # Legacy Unity message/state behavior
    # ------------------------------------------------------------------
    def _process_command_msg(self, msg: str) -> None:
        msg = msg.rstrip()
        fields = msg.split(",")
        if len(fields) < 2:
            return

        kind = fields[0]
        value = fields[1]

        if kind == "command":
            if config.SPACECOASTER_DEBUG:
                log.debug("SpaceCoaster command {%s}", value)
            self._legacy_command(value)
        elif kind == "config":
            # The current Unity interface contains a config branch but no
            # working process_config_msg implementation. Preserve that fact by
            # accepting/logging the packet and taking no action.
            if config.SPACECOASTER_DEBUG:
                log.debug("SpaceCoaster config {%s} ignored (legacy behavior)", value)
        elif kind == "state":
            try:
                state_value = int(value)
            except (ValueError, TypeError):
                log.warning("Bad SpaceCoaster state message: %r", msg)
                return

            # v8 diagnostic emitted when Unity has accepted the lobby command
            # and has started entering MenuMSU. It is not a stable native state.
            if state_value == 99:
                if config.SPACECOASTER_DEBUG:
                    log.debug("SpaceCoaster entering MenuMSU lobby (state,99)")
                return

            try:
                new_state = NativeState(state_value)
            except ValueError:
                log.warning("Bad SpaceCoaster state message: %r", msg)
                return
            self._startup_sync_due_at = None
            self._startup_sync_complete = True
            self._process_native_state(new_state)

    def _process_native_state(self, new_state: NativeState) -> None:
        old_state = self.native_state
        if new_state != old_state:
            self._reset_fan_speed_tracking()

        # WAITING is the MenuMSU lobby state. Unity can report WAITING before
        # the lobby is quite ready to accept a control command, so schedule the
        # ready command rather than sending it from inside the state handler.
        if new_state == NativeState.WAITING:
            self._status_text = "SpaceCoaster waiting in lobby"
            self._ready_due_at = time.monotonic() + config.SPACECOASTER_READY_DELAY_S

        elif old_state == -1 and new_state == NativeState.INITIALIZING:
            if config.SPACECOASTER_DEBUG:
                log.debug("SpaceCoaster initial state message")

        elif old_state == NativeState.WAITING and new_state == NativeState.READY:
            self._status_text = "SpaceCoaster ready for dispatch"
            # READY is the start-of-ride condition. Do not allow lobby/paused
            # pacing packets to carry into the next recorded telemetry run.
            self.frame_number = self.start_frame
            self._drain_xyzrpy_queue()

        elif old_state == NativeState.READY and new_state == NativeState.RUNNING:
            self._status_text = "SpaceCoaster starting run"
            self.start_time = time.time()
            self.frame_number = self.start_frame

        elif old_state == NativeState.RUNNING and new_state == NativeState.COMPLETED:
            self._normal_completion_event = True
            self._status_text = "SpaceCoaster run completed"
            self.control.lobby()

        else:
            if config.SPACECOASTER_DEBUG:
                old_name = self._native_state_name(old_state)
                log.debug(
                    "Ignoring out of sequence SpaceCoaster transition %s -> %s",
                    old_name,
                    _STATE_NAMES[int(new_state)],
                )

        # Preserve the established behavior: even an out-of-sequence state
        # event becomes the new native state after its action is ignored.
        self.native_state = new_state
        self.state = self._experience_state_for(new_state)

        if config.SPACECOASTER_DEBUG:
            log.debug(
                "SpaceCoaster native state %s -> %s; POC state=%s",
                self._native_state_name(old_state),
                _STATE_NAMES[int(new_state)],
                self.state.name,
            )


    def _service_delayed_ready(self) -> None:
        """Send ready once, after Unity's WAITING lobby state has settled."""
        due = self._ready_due_at
        if due is None or time.monotonic() < due:
            return
        self._ready_due_at = None
        if self.native_state != NativeState.WAITING:
            return
        self.control.ready()

    def _service_startup_sync(self) -> None:
        """Return Unity to its lobby once if reconnect receives no state."""
        due = self._startup_sync_due_at
        if due is None or time.monotonic() < due:
            return
        self._startup_sync_due_at = None
        if self._startup_sync_complete or self.native_state != -1:
            return
        if self.control.lobby():
            self._startup_sync_complete = True
            self.state = ExperienceState.RESETTING
            self._status_text = "Synchronizing SpaceCoaster state"
            log.info("SpaceCoaster state unknown after reconnect; returning to lobby once")
        else:
            # The Unity endpoint may not yet be available in SHS. Retry at the
            # same low rate until a state arrives or the command can be queued.
            self._startup_sync_due_at = (
                time.monotonic() + config.SPACECOASTER_STARTUP_SYNC_TIMEOUT_S
            )

    def _experience_state_for(self, native_state: NativeState) -> ExperienceState:
        if native_state == NativeState.READY:
            return ExperienceState.READY
        if native_state == NativeState.RUNNING:
            return ExperienceState.RUNNING
        # Initializing, waiting and completed are all intermediate Unity states
        # in which the legacy code is progressing its automatic click sequence.
        return ExperienceState.RESETTING

    def _legacy_command(self, cmd: str) -> None:
        # In the current SimpleSims implementation command() only forwards when
        # an external cmd_func was supplied by begin(). POC does not supply one.
        if config.SPACECOASTER_DEBUG:
            log.debug("SpaceCoaster legacy command ignored: %s", cmd)

    # ------------------------------------------------------------------
    # UDP input and recorded telemetry
    # ------------------------------------------------------------------
    def _listener_loop(self) -> None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(config.SOCKET_TIMEOUT_S)
            sock.bind(("", self.port))
            self._socket = sock
            self._listener_ready.set()
            log.info("SpaceCoaster UDP listening on port %d", self.port)
        except Exception as exc:
            self.last_error = str(exc)
            self._listener_ready.set()
            log.exception("SpaceCoaster listener init error")
            return

        while not self._stop_event.is_set():
            try:
                raw, _peer = sock.recvfrom(config.MAX_MSG_LEN)
            except socket.timeout:
                continue
            except OSError:
                if self._stop_event.is_set():
                    break
                log.exception("SpaceCoaster UDP receive error")
                continue

            try:
                msg = raw.decode("utf-8")
            except UnicodeDecodeError:
                self._debug_unknown_datagram(raw, _peer, "binary")
                continue
            if not self._handle_datagram(msg):
                self._debug_unknown_datagram(raw, _peer, "text")

    def _handle_datagram(self, msg: str) -> bool:
        if msg.startswith(("xyzrpy", "posrpy")):
            self._mark_sim_seen()
            self._last_motion_data_time = time.monotonic()
            if msg.startswith("posrpy"):
                self._update_position_speed(msg)
            else:
                self._update_fan_speed(msg)
            if (
                self.native_state == -1
                and not self._startup_sync_complete
                and self._startup_sync_due_at is None
            ):
                self._startup_sync_due_at = (
                    time.monotonic() + config.SPACECOASTER_STARTUP_SYNC_TIMEOUT_S
                )
            self._xyzrpy_q.put((time.time(), msg))
            return True
        if msg.startswith(("command", "config", "state")):
            self._mark_sim_seen()
            self._cmd_q.put(msg)
            return True
        return False

    def _debug_unknown_datagram(self, raw: bytes, peer, encoding: str) -> None:
        if not (config.SPACECOASTER_UDP_DEBUG and config.SPACECOASTER_UDP_DEBUG_UNKNOWN):
            return
        signature = (len(raw), raw[:24])
        count = self._unknown_udp_counts.get(signature, 0) + 1
        self._unknown_udp_counts[signature] = count
        # Log the first few examples, then only occasional repeats so an
        # unrelated high-rate datagram cannot overwhelm the POC log.
        if count > 3 and count % 100 != 0:
            return
        text = raw[:120].decode("utf-8", errors="backslashreplace")
        text = text.replace("\r", "\\r").replace("\n", "\\n")
        log.debug(
            "SpaceCoaster unknown UDP %s from %s len=%d count=%d prefix=%r hex=%s",
            encoding, peer, len(raw), count, text, raw[:32].hex(" "),
        )

    def _mark_sim_seen(self) -> None:
        self._sim_connected = True
        self.last_message_time = time.time()

    def _motion_data_fresh(self) -> bool:
        if self._last_motion_data_time is None:
            return False
        return (
            time.monotonic() - self._last_motion_data_time
            <= config.MOTION_DATA_TIMEOUT_S
        )

    def _update_fan_speed(self, msg: str) -> None:
        """Read spline speed, falling back for the earlier six-value packet."""
        fields = msg.rstrip().split(",")
        if len(fields) < 4:
            return
        try:
            if len(fields) > 7 and fields[7].strip():
                speed = float(fields[7])
            else:
                velocity = tuple(float(value) for value in fields[1:4])
                speed = math.hypot(*velocity)
        except (TypeError, ValueError):
            return
        if not math.isfinite(speed):
            return
        self._fan_speed = max(0.0, speed)
        self._fan_speed_time = time.monotonic()

    def _update_position_speed(self, msg: str) -> None:
        """Derive the fan cue from successive SpaceCoaster positions."""
        fields = msg.rstrip().split(",")
        if len(fields) < 4:
            return
        try:
            position = tuple(float(value) for value in fields[1:4])
        except (TypeError, ValueError):
            return
        if not all(math.isfinite(value) for value in position):
            return

        now = time.monotonic()
        previous_position = self._position_speed_position
        previous_time = self._position_speed_time
        if previous_position is None:
            self._position_speed_position = position
            self._position_speed_time = now
            return
        if position == previous_position:
            return

        self._position_speed_position = position
        self._position_speed_time = now
        elapsed = now - previous_time
        if elapsed <= 0.0 or elapsed > config.FAN_SPEED_TIMEOUT_S:
            self._clear_position_speed_filter()
            self._fan_speed = 0.0
            self._fan_speed_time = now
            return

        scale = float(self.fan_config.get("position_speed_scale", 1.0))
        speed = math.dist(position, previous_position) * scale / elapsed
        if not math.isfinite(speed):
            self._clear_position_speed_filter()
            self._fan_speed = 0.0
            self._fan_speed_time = now
            return
        self._fan_speed = self._smooth_position_speed(max(0.0, speed), now)
        self._fan_speed_time = now

    def _smooth_position_speed(self, speed: float, now: float) -> float:
        """Reject isolated samples with a median, then average over time."""
        self._position_speed_raw.append(speed)
        filtered = float(median(self._position_speed_raw))
        self._position_speed_average.append((now, filtered))
        self._position_speed_average_total += filtered

        duration = max(
            0.0,
            float(self.fan_config.get("position_speed_average_s", 0.0)),
        )
        while (
            self._position_speed_average
            and now - self._position_speed_average[0][0] > duration
        ):
            _sample_time, old_value = self._position_speed_average.popleft()
            self._position_speed_average_total -= old_value
        return self._position_speed_average_total / len(self._position_speed_average)

    def _clear_position_speed_filter(self) -> None:
        self._position_speed_raw.clear()
        self._position_speed_average.clear()
        self._position_speed_average_total = 0.0

    def _reset_fan_speed_tracking(self) -> None:
        self._fan_speed = 0.0
        self._fan_speed_time = 0.0
        self._position_speed_position = None
        self._position_speed_time = 0.0
        self._clear_position_speed_filter()

    def _fresh_fan_speed(self) -> float:
        if self._fan_speed_time <= 0.0:
            return 0.0
        age = time.monotonic() - self._fan_speed_time
        if age > config.FAN_SPEED_TIMEOUT_S:
            return 0.0
        return self._fan_speed

    def _drain_xyzrpy_queue(self) -> None:
        while True:
            try:
                self._xyzrpy_q.get_nowait()
            except queue.Empty:
                return

    def _read_telemetry(self) -> bool:
        telemetry_path = Path(__file__).resolve().with_name("spacecoaster_telemetry.csv")
        rows = []
        try:
            with telemetry_path.open("r", newline="", encoding="utf-8") as csvfile:
                reader = csv.reader(csvfile, delimiter=",")
                for row in reader:
                    if not row or row[0].lstrip().startswith("#") or len(row) < 6:
                        continue
                    data = [float(value) for value in row[:6]]
                    normalized = [
                        self._scale(
                            value,
                            -config.MAX_VALUES[index],
                            config.MAX_VALUES[index],
                            -1.0,
                            1.0,
                        )
                        for index, value in enumerate(data)
                    ]
                    rows.append(tuple(normalized))
        except Exception as exc:
            self.last_error = str(exc)
            log.exception("Error reading SpaceCoaster telemetry file")
            self._telemetry = []
            return False

        self._telemetry = rows
        log.info("Read %d SpaceCoaster telemetry frames", len(rows))
        return bool(rows)

    @staticmethod
    def _scale(value: float, from_min: float, from_max: float, to_min: float, to_max: float) -> float:
        if value > from_max:
            return to_max
        if value < from_min:
            return to_min
        if from_max == from_min:
            return to_min
        return ((value - from_min) / (from_max - from_min)) * (to_max - to_min) + to_min

    @staticmethod
    def _is_localhost(address: str) -> bool:
        return str(address).strip().lower() in {"127.0.0.1", "localhost", "::1"}

    @staticmethod
    def _native_state_name(state) -> str:
        if state == -1:
            return "Unset"
        try:
            return _STATE_NAMES[int(state)]
        except (ValueError, TypeError, IndexError):
            return str(state)
