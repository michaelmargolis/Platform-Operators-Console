#sim.py  ver 40
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
import math
import time
from typing import Optional

from core.sim_interface import SimInterface
from core.types import ExperienceState, SimSample, SimStatus
from sims.nolimits2 import config, ride_config, angular_cues
from sims.nolimits2.nl2_protocol import NL2Protocol, NL2Telemetry

log = logging.getLogger(__name__)

# State-transition diagnostics are intentionally separate from the high-rate
# protocol trace in nl2_protocol.py.  These messages show why POC decides that
# a park reset is or is not required.
NL2_STATE_DEBUG = True

# Station replies arrive at 4 Hz. Require a recent response for dispatch,
# departure and completion decisions, but allow a little scheduling margin.
STATION_OBSERVATION_MAX_AGE_S = 1.5
# Query the nearest station only when the ridden train stops after departing;
# normal 4 Hz station polling continues to target its pinned departure station.
ARRIVAL_NEAREST_POLL_S = 0.5


@dataclass(frozen=True)
class StationObservation:
    identity: tuple[int, int] | None
    status: int | None
    valid: bool
    received_at: float
    manual: bool = False
    occupied: bool = False
    view_train_here: bool = False
    other_train_here: bool = False
    can_dispatch: bool = False


class Sim(SimInterface):
    """Simple NoLimits2 adapter used to prove the POC simulator contract.

    The state behaviour follows the tested early coaster controller:
      * dispatch is accepted only from READY and immediately enters RUNNING;
      * pause toggles RUNNING/PAUSED;
      * a returning train becomes READY only after it has left the station;
      * reset enters RESETTING and returns to READY when NL2 is stationary in
        the station;
      * POC owns the E-STOP latch. Normal activation may conditionally reset
        the park; E-STOP recovery explicitly reloads the selected ride.
    """

    name = "NoLimits2 Coaster"
    run_label = "Dispatch"
    pause_label = "Pause"
    service_period_ms = config.SERVICE_PERIOD_MS
    hot_switch = True
    fan_config = config.FAN_CONFIG
    ui_tabs = ()

    MANUAL_MODE_MASK = 0x0002
    CAN_DISPATCH_MASK = 0x0004
    HARNESS_CAN_CLOSE_MASK = 0x0020
    PLATFORM_CAN_LOWER_MASK = 0x0100
    TRAIN_IN_STATION_MASK = 0x0800
    CURRENT_RIDE_TRAIN_MASK = 0x1000

    def __init__(self, address="127.0.0.1", client: Optional[NL2Protocol] = None,
                 startup_ride_index: Optional[int] = None):
        self.address = address
        self.client = client or NL2Protocol(
            address,
            port=config.PORT,
            connect_timeout=config.CONNECT_TIMEOUT_S,
        )
        self.state = ExperienceState.DISCONNECTED
        self._selected = int(getattr(config, "DEFAULT_RIDE_INDEX", 0))
        self._rides = self._discover_rides()
        self._park_keys = tuple(ride_config.park_key_for_variant(name) for name, _ in self._rides)
        # Optional initial selection for standalone study tools; normal POC
        # still loads config.DEFAULT_RIDE_INDEX when no override is supplied.
        if startup_ride_index is not None and not 0 <= startup_ride_index < len(self._rides):
            raise IndexError(startup_ride_index)
        self._startup_ride_index = startup_ride_index
        self._experiences = tuple(name for name, _path in self._rides)
        self._last_transform = (0.0,) * 6
        self._pause_target = None
        self._dispatch_pending = False
        self._dispatch_station_seen_at = 0.0
        self._reset_requested_at = 0.0
        self._initial_sync = False
        self._telemetry_valid = False
        # Ridden-train lifecycle is separate from station occupancy.
        self._view_train_departed = False
        self._departure_motion_seen = False
        self._ride_station = None  # pinned departure coaster/station
        self._ready_station = None  # confirmed arrival/next departure station
        self._arrival_probe_since = 0.0
        self._arrival_probe_last_request = 0.0
        self._ride_train = None  # (telemetry coaster index, train index)
        self._ride_dispatch_at = 0.0
        self._aux_dispatch_authorized = False
        self._aux_wait_for_station_clear = False
        self._aux_dispatch_count = 0
        self._state_events = deque(maxlen=256)
        self._last_station_request = 0.0
        self._last_connect_attempt = 0.0
        self._previous_quaternion = None
        self._previous_quaternion_time = None
        self._filtered_yaw_rate = 0.0
        self._last_telemetry_frame = None
        self._lift_height = float(config.INITIAL_LIFT_HEIGHT_M)
        self._heave_mode = config.HEAVE_MODE
        self._angular_cue_mode = angular_cues.EULER
        self._normalized_height_amplitude = None
        self._connected_sync_started = False
        self._data_connected = False
        self._heartbeat_connected = False
        self._last_api_probe = 0.0
        self._manual_mode_requested = False
        self._park_reset_pending = False
        self._ride_load_pending = False
        self._ride_load_requested_at = 0.0
        self._ride_load_request_id = None
        self._park_load_failed = False
        self._loaded_index = None  # Set only after the corresponding LOAD_PARK OK.
        self._loading_index = None
        self._queued_ride_index = None
        self._estop_recovery_pending = False

    def connect(self) -> None:
        self._last_connect_attempt = time.monotonic()
        self._connected_sync_started = False
        self._data_connected = False
        self.state = ExperienceState.DISCONNECTED
        if not self.client.connect():
            return

        # Match the proven coaster behavior: once TCP is connected, immediately
        # ask NL2 for a benign API response.  Do not block here waiting for the
        # reply; service() will process it and begin ride synchronization once
        # the simulator datalink has actually been proven alive.
        try:
            self.client.request_version()
            self._last_api_probe = time.monotonic()
        except ConnectionError:
            self.state = ExperienceState.DISCONNECTED

    def _begin_connected_sync(self) -> None:
        if self._connected_sync_started:
            return
        self._connected_sync_started = True
        # Startup is deterministic: normal POC loads the configured default.
        # A standalone recorder may explicitly select its first study ride so
        # it need not load the default and then immediately switch parks.
        # Subsequent rides still use the usual select_experience() path.
        # Completion is based on NL2's request response, not a play transition.
        self._clear_ride_tracking()
        self._selected = int(
            self._startup_ride_index if self._startup_ride_index is not None
            else getattr(config, "DEFAULT_RIDE_INDEX", 0)
        )
        if not 0 <= self._selected < len(self._rides):
            self._selected = 0
        self._initial_sync = False
        self._loaded_index = None
        self._queued_ride_index = None
        self._begin_ride_load(self._selected, startup=True)

    def disconnect(self) -> None:
        self.client.disconnect()
        self.state = ExperienceState.DISCONNECTED
        self._telemetry_valid = False
        self._last_transform = (0.0,) * 6
        self._pause_target = None
        self._dispatch_pending = False
        self._dispatch_station_seen_at = 0.0
        self._clear_ride_tracking()
        self._reset_requested_at = 0.0
        self._initial_sync = False
        self._connected_sync_started = False
        self._data_connected = False
        self._manual_mode_requested = False
        self._park_reset_pending = False
        self._ride_load_pending = False
        self._ride_load_requested_at = 0.0
        self._ride_load_request_id = None
        self._loading_index = None
        self._loaded_index = None
        self._queued_ride_index = None
        self._park_load_failed = False
        self._estop_recovery_pending = False
        self._reset_transform_tracking()

    def service(self) -> None:
        now = time.monotonic()
        if not self.client.is_connected:
            self._connected_sync_started = False
            self._data_connected = False
            self.state = ExperienceState.DISCONNECTED
            if now - self._last_connect_attempt >= config.RECONNECT_PERIOD_S:
                self.connect()
            return

        self.client.service()
        self._service_dispatch_preparation()

        # When normal motion data is unavailable, periodically use GET_VERSION
        # as a benign API health probe. VERSION/OK/ERROR responses all prove the
        # NL2 datalink is alive without requiring play mode.
        if not self._data_connected and now - self._last_api_probe >= config.API_PROBE_PERIOD_S:
            try:
                self.client.request_version()
                self._last_api_probe = now
            except ConnectionError:
                self._data_connected = False
                self.state = ExperienceState.DISCONNECTED
                return

        if not self._sim_connected(now):
            self._data_connected = False
            self.state = ExperienceState.DISCONNECTED
            return

        if not self._connected_sync_started:
            self._begin_connected_sync()

        # LOAD_PARK completion is an API response and must not depend on motion
        # telemetry being available.  A park loaded paused can legitimately be
        # between telemetry samples while still being fully loaded.
        self._service_ride_load_result()

        try:
            self.client.request_telemetry()
            if now - self._last_station_request >= config.STATION_POLL_PERIOD_S:
                self._request_station_state()
                if (self.state is ExperienceState.RUNNING
                        and self._view_train_departed and self._arrival_probe_since):
                    nearest_at = float(getattr(self.client, "current_station_received_at", 0.0) or 0.0)
                    candidate = self._station_indices()
                    if (candidate is not None and self._ride_train is not None
                            and candidate[0] == self._ride_train[0]
                            and nearest_at >= self._arrival_probe_since
                            and candidate != self._ride_station):
                        self.client.request_station_state(*candidate)
                self._last_station_request = now
        except ConnectionError:
            self.state = ExperienceState.DISCONNECTED
            self._data_connected = False
            self._telemetry_valid = False

    def read(self) -> SimSample:
        if not self._sim_connected():
            self.state = ExperienceState.DISCONNECTED
            self._data_connected = False
            self._telemetry_valid = False
            return SimSample(self._last_transform, self.state, False)

        # Also consume a completed park-load response here so direct/read-led
        # callers cannot leave the adapter stuck in RESETTING until telemetry
        # happens to arrive.
        self._service_ride_load_result()

        telemetry = self.client.latest_telemetry
        now = time.monotonic()
        if telemetry is None or now - telemetry.received_at > config.TELEMETRY_STALE_S:
            # During RESETTING absence of telemetry is explicitly permitted by
            # the POC contract.  For other connected states keep the logical
            # state but flag the data invalid.
            self._data_connected = False
            self._telemetry_valid = False
            self._reset_transform_tracking()
            return SimSample(self._last_transform, self.state, False)

        if not telemetry.in_play_mode:
            self._data_connected = False
            self._telemetry_valid = False
            self._reset_transform_tracking()
            return SimSample(self._last_transform, self.state, False)

        # The protocol retains its most recent telemetry. The NL2 frame number
        # identifies fresh simulator data; do not derive a second, zero-rate
        # yaw sample when read() sees the same cached packet again.
        if telemetry.frame != self._last_telemetry_frame:
            self._last_transform = self._transform_from_telemetry(telemetry)
            self._last_telemetry_frame = telemetry.frame
        self._data_connected = True
        self._telemetry_valid = True
        before_state = self.state
        self._update_state(telemetry)
        if self.state is not before_state:
            self._state_event("state", previous=before_state.name,
                              current=self.state.name,
                              frame=telemetry.frame,
                              speed=round(float(telemetry.speed), 3))
        return SimSample(
            self._last_transform,
            self.state,
            True,
            max(0.0, float(telemetry.speed)),
        )

    def run(self) -> None:
        if self.state is not ExperienceState.READY or self._dispatch_pending:
            return

        # Match the tested coaster dispatch sequence without blocking POC:
        # remain logically READY while the station is being prepared. RUNNING
        # begins only when the actual NL2 DISPATCH command is sent.
        self._pause_target = None
        self._dispatch_pending = True
        # Keep the last confirmed arrival station through dispatch preparation;
        # it will be pinned as the next departure station at DISPATCH.
        ready_station = self._ready_station
        self._clear_ride_tracking()
        self._ready_station = ready_station
        self._state_event("ride_dispatch_requested", selected=self._selected)
        # Compare against the actual intended departure station, not a
        # different station's latest global reply timestamp.
        self._dispatch_station_seen_at = self._station_observation().received_at
        self._request_station_state()
        if NL2_STATE_DEBUG:
            log.debug("NL2 dispatch requested: waiting for station preparation")

    def cancel_pending_dispatch(self) -> None:
        # Do not allow an asynchronous station-preparation sequence to send
        # DISPATCH after the physical activation switch has gone OFF.
        self._dispatch_pending = False
        self._dispatch_station_seen_at = 0.0
        self._aux_dispatch_authorized = False
        self._state_event("dispatch_authorization_revoked")

    def pause(self) -> None:
        # The tested coaster state machine treats telemetry as authoritative for
        # PAUSED/RUNNING.  Send the requested NL2 command but do not change the
        # logical state until telemetry confirms it.
        if self._dispatch_pending:
            if NL2_STATE_DEBUG:
                log.debug("NL2 pause ignored while dispatch preparation is pending")
            return
        if self.state is ExperienceState.RUNNING:
            self.client.set_pause(True)
            self._pause_target = True
        elif self.state is ExperienceState.PAUSED:
            self.client.set_pause(False)
            self._pause_target = False

    def prepare_for_activation(self) -> None:
        """Ensure NL2 is ready without coupling it to platform activation.

        POC calls this on every physical activation because NL2 may have been
        operated independently of POC.  A train confirmed stopped in station
        stays READY; anything else
        causes RESET_PARK(False) and RESETTING.
        """
        if not self._sim_connected():
            if NL2_STATE_DEBUG:
                log.debug(
                    "NL2 activation preparation: sim API not connected -> no park reset yet"
                )
            return

        stopped_in_station = self._stopped_in_station()
        if NL2_STATE_DEBUG:
            self._log_reset_decision("activation", stopped_in_station)
        if stopped_in_station:
            self._reset_requested_at = 0.0
            self._park_reset_pending = False
            if self._manual_mode_is_set():
                self.state = ExperienceState.READY
                if NL2_STATE_DEBUG:
                    log.debug(
                        "NL2 activation preparation: train stopped in station and manual -> no RESET_PARK"
                    )
            else:
                self.state = ExperienceState.RESETTING
                if NL2_STATE_DEBUG:
                    log.debug(
                        "NL2 activation preparation: train stopped in station but auto mode active -> set manual mode"
                    )
                self._request_manual_mode()
        else:
            if NL2_STATE_DEBUG:
                log.debug(
                    "NL2 activation preparation: train not confirmed stopped in station -> RESET_PARK"
                )
            self._start_park_reset()

        self._initial_sync = False
        self._pause_target = None
        self._dispatch_pending = False
        self._dispatch_station_seen_at = 0.0
        ready_station = self._ready_station if stopped_in_station else None
        self._clear_ride_tracking()
        self._ready_station = ready_station
        self._reset_transform_tracking()

    def recover_from_estop(self) -> None:
        """Reload the selected park and remain RESETTING until dispatch-ready."""
        if not self._sim_connected():
            raise RuntimeError("NoLimits2 is not connected for E-STOP recovery")
        self._begin_ride_load(self._selected, startup=False)
        self._estop_recovery_pending = True

    def is_ready_to_start(self) -> bool:
        """True only for the proven safe dispatch condition."""
        return bool(
            self.state is ExperienceState.READY
            and not self._park_load_failed
            and not self._dispatch_pending
            and not self._ride_load_pending
            and self._stopped_in_station()
            and self._manual_mode_is_set()
        )

    def reset_vr(self) -> None:
        self.client.recenter_vr()

    def _discover_rides(self):
        """Operator dropdown entries are variants; only physical parks load."""
        return tuple((label, ride_config.PARKS[ride_config.park_key_for_variant(label)]["path"])
                     for label in ride_config.labels())

    def park_name_for_experience(self, index):
        return ride_config.PARKS[self._park_keys[index]]["name"]

    def display_name_for_experience(self, index):
        """Hide bracketed modifiers in the POC UI only, not in identifiers."""
        return ride_config.display_label(self._experiences[index])

    def _same_park(self, a, b):
        return (a is not None and b is not None and
                self._rides[a][1].casefold() == self._rides[b][1].casefold())

    def _apply_loaded_variant(self, index):
        """Adopt only a confirmed variant; never reset park/station state."""
        value = ride_config.variant(self._experiences[index])
        self._angular_cue_mode = value["angular_cue"]
        self._heave_mode = value["heave_mode"]
        self._normalized_height_amplitude = value["normalized_height_amplitude"]
        self._lift_height = float(config.INITIAL_LIFT_HEIGHT_M)
        self._reset_transform_tracking()
        log.info("NL2 variant active: %s park=%s cue=%s heave=%s",
                 value["label"], value["park"], self._angular_cue_mode, self._heave_mode)

    def get_experiences(self):
        return self._experiences

    def select_experience(self, index: int) -> None:
        if not 0 <= index < len(self._experiences):
            raise IndexError(index)
        if not self._sim_connected():
            raise RuntimeError("NoLimits2 must be connected before changing ride")
        if self._ride_load_pending:
            self._selected = index
            # A variant of the park currently loading needs no second LOAD_PARK.
            self._queued_ride_index = (None if self._same_park(index, self._loading_index)
                                       else index)
            return
        if self._same_park(index, self._loaded_index):
            if index != self._loaded_index:
                self._selected = index
                self._loaded_index = index
                self._apply_loaded_variant(index)
            return
        self._begin_ride_load(index, startup=False)

    def _begin_ride_load(self, index: int, startup: bool = False) -> None:
        if self._ride_load_pending:
            raise RuntimeError("NoLimits2 park load already in progress")
        _name, park_path = self._rides[index]
        if not park_path:
            raise RuntimeError("No NL2 park path configured for this ride")

        self._selected = index
        self._pause_target = None
        self._dispatch_pending = False
        self._clear_ride_tracking()
        self._initial_sync = False
        # LOAD_PARK creates a new station/control context.  Any outstanding
        # manual-mode request belongs to the previous park and must not suppress
        # a fresh SET_MANUAL_MODE command after the new park is loaded.
        self._manual_mode_requested = False
        invalidate_station = getattr(self.client, "invalidate_current_station", None)
        if callable(invalidate_station):
            invalidate_station()
        self._park_reset_pending = False
        self._reset_requested_at = 0.0
        self._ride_load_pending = True
        self._park_load_failed = False
        self._loading_index = index
        self._ride_load_requested_at = time.monotonic()
        self._reset_transform_tracking()
        self._lift_height = float(config.INITIAL_LIFT_HEIGHT_M)
        self.state = ExperienceState.RESETTING
        if NL2_STATE_DEBUG:
            reason = "startup default" if startup else "operator selection"
            log.debug("NL2 loading ride %s (%s) from %s", self._experiences[index], reason, park_path)
        self._ride_load_request_id = self.client.load_park(park_path, start_paused=True)

    def get_status(self) -> SimStatus:
        pc_connected = self._pc_connected()
        sim_connected = self._sim_connected()
        data_connected = bool(sim_connected and self._data_connected)

        # PC connectivity is owned by SHS in PocCore.  Do not generate
        # heartbeat-specific text here because core may legitimately override
        # this adapter's legacy pc_connected value with the SHS heartbeat.
        if not self.client.is_connected:
            text = "Waiting for NoLimits2 TCP connection"
        elif not sim_connected:
            text = "NoLimits2 socket open; waiting for API response"
        elif self._ride_load_pending:
            loading = self._loading_index if self._loading_index is not None else self._selected
            text = f"Loading NoLimits2 ride: {self._experiences[loading]}"
        elif not data_connected:
            text = "NoLimits2 API connected; waiting for sim data"
        elif self._dispatch_pending:
            text = "Preparing NoLimits2 dispatch"
        elif self.state is ExperienceState.RESETTING:
            text = "Resetting NoLimits2 ride"
        elif self.is_ready_to_start():
            text = "Ready for dispatch"
        elif self.state is ExperienceState.READY:
            text = "NoLimits2 ride loaded; waiting for activation"
        else:
            text = self.state.name.replace("_", " ").title()
        if self.client.last_error:
            text = self.client.last_error
        return SimStatus(
            pc_connected=pc_connected,
            sim_connected=sim_connected,
            data_connected=data_connected,
            text=text,
            entity_name=self._experiences[self._selected],
            busy=bool(
                self.state is ExperienceState.RESETTING
                or self._ride_load_pending
                or self._park_reset_pending
                or self._reset_requested_at > 0.0
                or self._manual_mode_requested
                or self._dispatch_pending
            ),
            ready_to_start=self.is_ready_to_start(),
            activity=(
                "loading"
                if self._ride_load_pending
                else "resetting"
                if self.state is ExperienceState.RESETTING
                else ""
            ),
        )

    def _pc_connected(self) -> bool:
        # Until the legacy heartbeat service is integrated, localhost is the
        # only PC connection that can be positively established.
        address = str(self.address).strip().lower()
        if address == "localhost" or address == "::1" or address.startswith("127."):
            return True
        return bool(self._heartbeat_connected)

    def _sim_connected(self, now=None) -> bool:
        if not self.client.is_connected:
            return False
        response_is_fresh = getattr(self.client, "response_is_fresh", None)
        if callable(response_is_fresh):
            return bool(response_is_fresh(config.API_RESPONSE_STALE_S, now))
        # Test clients and legacy adapters may not expose response timestamps.
        return getattr(self.client, "version", None) is not None

    def _service_ride_load_result(self) -> bool:
        """Consume LOAD_PARK completion independently of telemetry availability."""
        if not self._ride_load_pending:
            return False

        request_result = getattr(self.client, "request_result", None)
        result = request_result(self._ride_load_request_id) if callable(request_result) else None
        if result is None:
            return False

        outcome, detail = result
        completed_index = self._loading_index
        self._ride_load_pending = False
        self._ride_load_request_id = None
        self._loading_index = None
        if outcome == "error":
            # An unsuccessful LOAD_PARK does not establish which park is open.
            self._loaded_index = None
            self._park_load_failed = True
        else:
            self._loaded_index = completed_index
            self._park_load_failed = False

        queued_index = self._queued_ride_index
        self._queued_ride_index = None
        if queued_index is not None and not self._same_park(queued_index, self._loaded_index):
            # An explicitly different park must follow the previous LOAD_PARK.
            self._begin_ride_load(queued_index, startup=False)
            return True
        if outcome != "error" and self._same_park(self._selected, completed_index):
            # Latest variant choice wins for a park that has just finished loading.
            self._loaded_index = self._selected
            self._apply_loaded_variant(self._loaded_index)

        if outcome == "error":
            if NL2_STATE_DEBUG:
                log.debug("NL2 LOAD_PARK failed: %s", detail)
            # The load operation has ended even on failure; leave the adapter in
            # its stable connected state and expose the protocol error via
            # client.last_error/status text rather than spinning indefinitely.
            self._estop_recovery_pending = False
            self.state = ExperienceState.READY
            return True

        # LOAD_PARK(start_paused=True) itself starts the requested park at its
        # initial paused state.  Do not RESET_PARK on startup.  Clear observations
        # that may belong to the previous park and request fresh state.
        self.client.latest_telemetry = None
        self.client.station_status = None
        self.client.station_status_received_at = 0.0
        # Also clear the per-station cache: a just-loaded park may reuse the
        # same (coaster, station) numbers as the previous one.
        invalidate_station = getattr(self.client, "invalidate_current_station", None)
        if callable(invalidate_station):
            invalidate_station()
        self._reset_requested_at = 0.0
        self._park_reset_pending = False
        # Normal ride loads may settle in READY while waiting for later operator
        # activation. E-STOP recovery is different: keep RESETTING until fresh
        # post-load station/telemetry proves the simulator is genuinely
        # dispatch-ready, so PocCore can safely clear the E-STOP latch.
        if self._estop_recovery_pending:
            # E-STOP recovery loads the park paused for deterministic startup,
            # then deliberately enters the same RESET_PARK(False) preparation
            # path used by normal activation.  Waiting for a train-in-station
            # flag while the freshly loaded park is still paused can deadlock:
            # NL2 may report only manual mode until the park is allowed to run.
            # _start_park_reset() waits for fresh play/manual state before
            # sending RESET_PARK(False), then the normal reset-completion logic
            # proves dispatch readiness.
            if NL2_STATE_DEBUG:
                log.debug(
                    "NL2 E-STOP recovery load acknowledged; entering normal park-reset preparation"
                )
            self._start_park_reset()
        else:
            self.state = ExperienceState.READY
            if NL2_STATE_DEBUG:
                log.debug(
                    "NL2 LOAD_PARK acknowledged; ride loaded, waiting for fresh park state/activation"
                )
            self.client.request_telemetry()
            self._request_station_state()
        return True

    def _state_event(self, event: str, **data) -> None:
        entry = {"time": time.monotonic(), "event": str(event), **data}
        self._state_events.append(entry)
        if NL2_STATE_DEBUG:
            log.info("NL2 ride-state %s: %s", event, data)

    def consume_state_events(self):
        """Drain low-rate, actionable state/dispatch events for diagnostics."""
        events = list(self._state_events)
        self._state_events.clear()
        return events

    def _clear_ride_tracking(self):
        self._view_train_departed = False
        self._departure_motion_seen = False
        self._ride_station = None
        self._ready_station = None
        self._arrival_probe_since = 0.0
        self._arrival_probe_last_request = 0.0
        self._ride_train = None
        self._ride_dispatch_at = 0.0
        self._aux_dispatch_authorized = False
        self._aux_wait_for_station_clear = False
        self._aux_dispatch_count = 0

    def _station_observation(self, now=None, identity=None):
        if now is None:
            now = time.monotonic()
        if identity is None:
            identity = self._ride_station or self._ready_station or self._station_indices()
        getter = getattr(self.client, "station_snapshot", None)
        if callable(getter):
            snapshot = getter(identity) if identity is not None else None
            status, received_at = snapshot if snapshot is not None else (None, 0.0)
            identity_matches = snapshot is not None
        else:
            # Legacy unit-test clients only carry one station observation.
            status = getattr(self.client, "station_status", None)
            received_at = float(getattr(self.client, "station_status_received_at", 0.0) or 0.0)
            actual_identity = getattr(self.client, "station_status_station", None)
            identity_matches = (identity is not None and
                (actual_identity == identity if hasattr(self.client, "station_status_station") else True))
        # Real NL2 responses always have a timestamp; test clients without
        # identity/timestamp metadata retain the earlier compatibility mode.
        timestamp_ok = (0 < received_at <= now
                        and now - received_at <= STATION_OBSERVATION_MAX_AGE_S)
        if not callable(getter) and not hasattr(self.client, "station_status_station") and received_at == 0:
            timestamp_ok = True
        valid = bool(identity_matches and status is not None and timestamp_ok)
        bits = int(status) if valid else 0
        occupied = bool(bits & self.TRAIN_IN_STATION_MASK)
        current = bool(occupied and bits & self.CURRENT_RIDE_TRAIN_MASK)
        return StationObservation(
            identity, bits if valid else None, valid, received_at,
            manual=bool(bits & self.MANUAL_MODE_MASK), occupied=occupied,
            view_train_here=current,
            other_train_here=bool(occupied and not current),
            can_dispatch=bool(bits & self.CAN_DISPATCH_MASK),
        )

    def _arrival_station_observation(self, now):
        """Return only a fresh, identified observation of the stopped view train.

        The nearest-station query is independent of pinned departure-station
        polling. The arrival response must follow this stationary episode and
        the station-state reply must follow the nearest-station response.
        """
        if not self._arrival_probe_since or self._ride_train is None:
            return None
        station_time = float(getattr(self.client, "current_station_received_at", 0.0) or 0.0)
        if (station_time < self._arrival_probe_since or station_time > now
                or now - station_time > STATION_OBSERVATION_MAX_AGE_S):
            return None
        identity = self._station_indices()
        if identity is None or identity[0] != self._ride_train[0]:
            return None
        obs = self._station_observation(now=now, identity=identity)
        if not (obs.valid and obs.view_train_here
                and obs.received_at >= station_time):
            return None
        return obs

    def _complete_view_train_return(self, obs, telemetry):
        departure = self._ride_station
        self._aux_dispatch_authorized = False
        self._manual_mode_requested = False
        self._ready_station = obs.identity
        self._ride_station = None
        self._arrival_probe_since = 0.0
        self._arrival_probe_last_request = 0.0
        self.state = ExperienceState.READY
        self._state_event("view_train_returned", station=obs.identity,
                          departure_station=departure, frame=telemetry.frame,
                          speed=float(telemetry.speed))
        # The next dispatch must use the station holding the returned train.
        # If it is not in manual mode, normal READY handling will request it.
        self._request_station_state()

    def _view_train_identity_matches(self, telemetry):
        if self._ride_train is None:
            return False
        return (int(telemetry.coaster_index), int(telemetry.train)) == self._ride_train

    def _consider_auxiliary_dispatch(self, obs, telemetry):
        if not (self._aux_dispatch_authorized
                and self._view_train_departed
                and self.state is ExperienceState.RUNNING
                and self._pause_target is None
                and not telemetry.paused
                and self._view_train_identity_matches(telemetry)
                and obs.valid and obs.manual):
            return
        if not obs.occupied:
            if self._aux_wait_for_station_clear:
                self._state_event("aux_station_cleared", station=obs.identity)
            self._aux_wait_for_station_clear = False
            return
        if (self._aux_wait_for_station_clear or not obs.other_train_here
                or not obs.can_dispatch or obs.identity != self._ride_station):
            return
        # The command targets the pinned departure station, NOT the nearest
        # station to the moving ride train. One send per observed occupancy;
        # require a fresh EMPTY station observation before another dispatch.
        self.client.dispatch(*self._ride_station)
        self._aux_wait_for_station_clear = True
        self._aux_dispatch_count += 1
        self._state_event("aux_dispatch_sent", station=self._ride_station,
                          status=f"0x{obs.status:08x}",
                          count=self._aux_dispatch_count)

    def _update_state(self, telemetry: NL2Telemetry) -> None:
        obs = self._station_observation()
        stationary = abs(float(telemetry.speed)) < config.STATIONARY_SPEED_MPS

        # Manual mode is required for explicit dispatch; do not let a cached
        # station report force a mode command to the wrong station.
        if obs.valid:
            if not obs.manual and self.state is not ExperienceState.RUNNING:
                self._request_manual_mode()
                return
            if obs.manual and self._manual_mode_requested:
                self._manual_mode_requested = False
            if self._park_reset_pending:
                self._send_park_reset()
                return

        if self._initial_sync:
            if not obs.valid:
                return
            self._initial_sync = False
            # READY is a waiting state. Only is_ready_to_start() grants
            # permission to dispatch the occupied (view) train.
            self.state = ExperienceState.READY
            self._state_event("initial_sync", view_train_here=obs.view_train_here,
                              stationary=stationary, status=f"0x{obs.status:08x}")
            return

        if self.state is ExperienceState.RESETTING:
            if self._reset_requested_at <= 0.0 and not self._park_reset_pending:
                if stationary and obs.valid and obs.view_train_here and obs.manual:
                    self.state = ExperienceState.READY
                    self._estop_recovery_pending = False
                return
            if (self._reset_requested_at > 0.0
                    and telemetry.received_at >= self._reset_requested_at
                    and obs.valid and obs.received_at >= self._reset_requested_at
                    and stationary and obs.view_train_here and obs.manual):
                self.state = ExperienceState.READY
                self._reset_requested_at = 0.0
                self._estop_recovery_pending = False
            return

        if self._dispatch_pending:
            return

        if self._pause_target is not None:
            if telemetry.paused == self._pause_target:
                self.state = (ExperienceState.PAUSED if self._pause_target
                              else ExperienceState.RUNNING)
                self._pause_target = None
            return

        if telemetry.paused:
            if self.state in (ExperienceState.RUNNING, ExperienceState.PAUSED):
                self.state = ExperienceState.PAUSED
            return

        if self.state is ExperienceState.PAUSED:
            self.state = ExperienceState.RUNNING

        if self.state is ExperienceState.RUNNING:
            # The ridden train is identified by telemetry, not by occupancy of
            # some station. Departure/auxiliary dispatch stay pinned to the
            # departure station even while checking a different arrival one.
            if not self._view_train_identity_matches(telemetry):
                return
            if not stationary:
                self._departure_motion_seen = True
                self._arrival_probe_since = 0.0
                self._arrival_probe_last_request = 0.0
            if (not self._view_train_departed and obs.valid
                    and self._departure_motion_seen
                    and obs.received_at > self._ride_dispatch_at
                    and not obs.view_train_here):
                self._view_train_departed = True
                self._state_event("view_train_departed", station=obs.identity,
                                  frame=telemetry.frame, train=self._ride_train)

            if self._view_train_departed and stationary:
                # Fast path: normal coaster returns to its departure station.
                if obs.valid and obs.view_train_here:
                    self._complete_view_train_return(obs, telemetry)
                    return
                # Multi-station coaster: request the station NEAREST THE VIEW
                # TRAIN, then require a separate identified STATION_STATE
                # response before declaring a completed ride.
                now = time.monotonic()
                if not self._arrival_probe_since:
                    self._arrival_probe_since = now
                    self._state_event("arrival_station_probe_started",
                                      departure_station=self._ride_station)
                if (not self._arrival_probe_last_request
                        or now - self._arrival_probe_last_request >= ARRIVAL_NEAREST_POLL_S):
                    request = getattr(self.client, "request_current_coaster_and_station", None)
                    if callable(request):
                        request()
                        self._arrival_probe_last_request = now
                arrived = self._arrival_station_observation(now)
                if arrived is not None:
                    self._complete_view_train_return(arrived, telemetry)
                    return

            self._consider_auxiliary_dispatch(obs, telemetry)
            return

        # READY is an operator waiting state, not a telemetry-driven RUNNING
        # transition. In particular, another train stopped in station never
        # changes the ridden train's lifecycle.

    def _service_dispatch_preparation(self) -> None:
        """Non-blocking equivalent of the old prepare_for_dispatch() loop."""
        if not self._dispatch_pending or not self._sim_connected():
            return

        obs = self._station_observation()
        if not obs.valid or obs.received_at <= self._dispatch_station_seen_at:
            return
        self._dispatch_station_seen_at = obs.received_at
        status = obs.status

        if not (status & self.MANUAL_MODE_MASK):
            self._request_manual_mode()
            self._request_station_state()
            return

        if status & self.PLATFORM_CAN_LOWER_MASK:
            if NL2_STATE_DEBUG:
                log.debug("NL2 dispatch preparation: lowering station platform")
            self._set_platform(True)
            self._request_station_state()
            return

        if status & self.HARNESS_CAN_CLOSE_MASK:
            # The tested coaster interface intentionally had no API command for
            # closing the harness; it waited for station state to change.
            if NL2_STATE_DEBUG:
                log.debug("NL2 dispatch preparation: waiting for harness to close")
            self._request_station_state()
            return

        if status & self.CAN_DISPATCH_MASK:
            if NL2_STATE_DEBUG:
                log.debug("NL2 dispatch preparation complete: verifying view train before DISPATCH")
            obs = self._station_observation()
            tm = self.client.latest_telemetry
            if not (obs.valid and obs.view_train_here and tm is not None
                    and tm.in_play_mode and obs.identity is not None
                    and int(tm.coaster_index) == obs.identity[0]):
                self._request_station_state()
                return
            self._ride_station = obs.identity
            self._ready_station = None
            self._ride_train = (int(tm.coaster_index), int(tm.train))
            self._view_train_departed = False
            self._departure_motion_seen = False
            self._aux_wait_for_station_clear = False
            # Authorise auxiliary departure only AFTER the primary command has
            # actually been transmitted. A socket error cannot arm that path.
            self._dispatch()
            self._ride_dispatch_at = time.monotonic()
            self._aux_dispatch_authorized = True
            self._dispatch_pending = False
            self.state = ExperienceState.RUNNING
            self._state_event("view_train_dispatched", station=self._ride_station,
                              train=self._ride_train, status=f"0x{obs.status:08x}")
            self._request_station_state()
            return

        if NL2_STATE_DEBUG:
            log.debug("NL2 dispatch preparation: station not yet dispatchable (0x%08x)", status)
        self._request_station_state()

    def _start_park_reset(self) -> None:
        # Old coaster code forced station manual mode before reset/dispatch
        # operations.  Do the same here so RESET_PARK(False) cannot enable an
        # automatic station dispatch.
        self.state = ExperienceState.RESETTING
        self._clear_ride_tracking()
        self._reset_requested_at = 0.0
        self._park_reset_pending = True
        self._state_event("park_reset_requested")

        telemetry = self.client.latest_telemetry
        if telemetry is not None and telemetry.in_play_mode:
            if self._manual_mode_is_set():
                self._send_park_reset()
                return
            self._request_manual_mode()

        # Ask for fresh observations promptly; service() will continue polling.
        self.client.request_telemetry()
        self._request_station_state()

    def _send_park_reset(self) -> None:
        if NL2_STATE_DEBUG:
            log.debug("NL2 manual mode confirmed; sending RESET_PARK start_paused=False")
        self.client.reset_park(False)
        self._state_event("park_reset_sent")
        invalidate_station = getattr(self.client, "invalidate_current_station", None)
        if callable(invalidate_station):
            invalidate_station()
        self._reset_requested_at = time.monotonic()
        self._park_reset_pending = False
        self.state = ExperienceState.RESETTING
        self.client.request_telemetry()
        self._request_station_state()

    def _station_indices(self):
        coaster = getattr(self.client, "current_coaster_index", None)
        station = getattr(self.client, "current_station_index", None)
        if coaster is None or station is None:
            return None
        return int(coaster), int(station)

    def _request_station_state(self) -> None:
        indices = self._ride_station or self._ready_station or self._station_indices()
        if indices is None:
            request_identity = getattr(self.client, "request_current_coaster_and_station", None)
            if callable(request_identity):
                request_identity()
            return
        self.client.request_station_state(*indices)

    def _set_manual_mode(self, enabled: bool) -> bool:
        indices = self._ride_station or self._ready_station or self._station_indices()
        if indices is None:
            self._request_station_state()
            return False
        self.client.set_manual_mode(bool(enabled), *indices)
        return True

    def _set_platform(self, lowered: bool) -> None:
        indices = self._ride_station or self._ready_station or self._station_indices()
        if indices is None:
            self._request_station_state()
            return
        self.client.set_platform(bool(lowered), *indices)

    def _dispatch(self) -> None:
        indices = self._ride_station or self._ready_station or self._station_indices()
        if indices is None:
            self._request_station_state()
            return
        self.client.dispatch(*indices)

    def _manual_mode_is_set(self) -> bool:
        return self._station_observation().manual

    def _request_manual_mode(self) -> None:
        if self._manual_mode_requested:
            return
        if NL2_STATE_DEBUG:
            status = self.client.station_status
            status_text = "None" if status is None else f"0x{int(status):08x}"
            log.debug(
                "NL2 station is not in manual mode (station=%s); sending SET_MANUAL_MODE True",
                status_text,
            )
        if self._set_manual_mode(True):
            self._manual_mode_requested = True
        self._request_station_state()

    def _stopped_in_station(self) -> bool:
        telemetry = self.client.latest_telemetry
        if telemetry is None or not telemetry.in_play_mode:
            return False
        if abs(float(telemetry.speed)) >= config.STATIONARY_SPEED_MPS:
            return False
        return self._station_observation().view_train_here

    def _log_reset_decision(self, source: str, stopped_in_station: bool) -> None:
        telemetry = self.client.latest_telemetry
        status = self.client.station_status
        speed = None if telemetry is None else float(telemetry.speed)
        play = None if telemetry is None else bool(telemetry.in_play_mode)
        if status is None:
            status_text = "None"
            train_bit = False
            current_train_bit = False
            in_station = False
        else:
            status_value = int(status)
            status_text = f"0x{status_value:08x}"
            train_bit = bool(status_value & 0x0800)
            current_train_bit = bool(status_value & 0x1000)
            in_station = self._train_in_station()
        log.debug(
            "NL2 %s reset check: state=%s play=%s speed=%s station=%s "
            "train_bit=%s current_train_bit=%s in_station=%s stopped_in_station=%s",
            source,
            self.state.name,
            play,
            "None" if speed is None else f"{speed:.3f}",
            status_text,
            train_bit,
            current_train_bit,
            in_station,
            stopped_in_station,
        )

    def _train_in_station(self) -> bool:
        status = self.client.station_status
        if status is None:
            return False
        return bool(int(status) & self.TRAIN_IN_STATION_MASK)

    def _transform_from_telemetry(self, tm: NL2Telemetry):
        # Convert NL2 telemetry to the same semantic inputs used by SOC:
        # accelerations for translation, attitude for roll/pitch, and angular
        # velocity for yaw. Heave is a session-selected cue; only calibrated
        # rides may use the track-relative Normalized Height option.
        quaternion = self._normalize_quaternion(
            tm.quat_x, tm.quat_y, tm.quat_z, tm.quat_w
        )
        roll, pitch, _yaw = self._quaternion_to_rpy(*quaternion)
        # NL2's signed bank angle is opposite the platform rider-frame Roll:
        # a right-side-down NL2 bank must become positive platform Roll.
        # The angular scales restore the legacy NL2 gain=0.6 response once
        # TELEMETRY_INTENSITY_FACTOR is applied below. Do not change the
        # physical Roll sign or the MDX-specific kinematic axis convention.
        roll = -roll / math.pi * config.ROLL_SCALE
        pitch = -pitch * 0.6 * config.PITCH_SCALE

        yaw_rate = self._yaw_rate_from_quaternion(quaternion, tm.received_at)
        yaw_rate *= float(config.YAW_RATE_GAIN)

        if self._heave_mode == config.HEAVE_MODE_LEGACY_HEIGHT:
            heave = self._legacy_height_z(float(tm.pos_y))
        elif self._heave_mode == config.HEAVE_MODE_NORMALIZED_HEIGHT:
            heave = self._normalized_height_z(float(tm.pos_y))
        else:
            heave = max(
                -1.0,
                min(
                    1.0,
                    (float(tm.g_force_y) - 1.0)
                    * config.TELEMETRY_INTENSITY_FACTOR,
                ),
            )
        surge = math.copysign(math.sqrt(abs(float(tm.g_force_z))), float(tm.g_force_z)) if tm.g_force_z else 0.0
        sway = math.copysign(math.sqrt(abs(float(tm.g_force_x))), float(tm.g_force_x)) if tm.g_force_x else 0.0

        data = (surge, sway, 0.0, roll, pitch, yaw_rate)
        transform = [
            max(-1.0, min(1.0, value * config.TELEMETRY_INTENSITY_FACTOR))
            for value in data
        ]
        transform[2] = heave
        if getattr(self, "_angular_cue_mode", angular_cues.EULER) != angular_cues.EULER:
            return angular_cues.map_angular_cue(
                tuple(transform), quaternion, self._angular_cue_mode,
                roll_gain=(float(config.TELEMETRY_INTENSITY_FACTOR)
                           * float(config.ROLL_SCALE) / math.pi),
                pitch_gain=(float(config.TELEMETRY_INTENSITY_FACTOR)
                            * float(config.PITCH_SCALE) * 0.6),
            )
        return tuple(transform)

    @property
    def heave_mode(self) -> str:
        return self._heave_mode

    @property
    def _legacy_heave_enabled(self) -> bool:
        """Compatibility for the v156 NL2 ride-study recorder."""
        return self._heave_mode == config.HEAVE_MODE_LEGACY_HEIGHT

    def has_normalized_height_profile(self, index: int | None = None) -> bool:
        """Whether this ride has calibrated track-height bounds."""
        if index is None:
            index = self._selected
        if not 0 <= index < len(self._experiences):
            return False
        profile = config.NORMALIZED_HEIGHT_PROFILES.get(self.park_name_for_experience(index))
        if not profile:
            return False
        try:
            low, high = float(profile["min_height_m"]), float(profile["max_height_m"])
            amplitude = float(profile.get("amplitude", config.NORMALIZED_HEIGHT_DEFAULT_AMPLITUDE))
        except (TypeError, ValueError, KeyError):
            return False
        return all(math.isfinite(x) for x in (low, high, amplitude)) and high > low and 0 <= amplitude <= 1

    def set_heave_mode(self, mode: str) -> None:
        """Select a session-only heave source without changing saved telemetry settings."""
        if mode not in (config.HEAVE_MODE_LEGACY_HEIGHT,
                        config.HEAVE_MODE_VERTICAL_G,
                        config.HEAVE_MODE_NORMALIZED_HEIGHT):
            raise ValueError(f"Unknown NL2 heave mode: {mode}")
        if mode == config.HEAVE_MODE_NORMALIZED_HEIGHT and not self.has_normalized_height_profile():
            raise ValueError(f"No calibrated height profile for {self._experiences[self._selected]}")
        self._heave_mode = mode

    def set_legacy_heave_enabled(self, enabled: bool) -> None:
        """Backward-compatible two-mode selector for existing clients/tests."""
        self.set_heave_mode(config.HEAVE_MODE_LEGACY_HEIGHT if enabled
                            else config.HEAVE_MODE_VERTICAL_G)

    def _normalized_height_z(self, height_m: float) -> float:
        """Invert ride-height fraction for the suspended-platform Z convention."""
        if not self.has_normalized_height_profile():
            raise ValueError(f"No calibrated height profile for {self._experiences[self._selected]}")
        profile = config.NORMALIZED_HEIGHT_PROFILES[self.park_name_for_experience(self._selected)]
        low, high = float(profile["min_height_m"]), float(profile["max_height_m"])
        amplitude = float(self._normalized_height_amplitude if self._normalized_height_amplitude is not None
                          else profile.get("amplitude", config.NORMALIZED_HEIGHT_DEFAULT_AMPLITUDE))
        fraction = max(0.0, min(1.0, (height_m - low) / (high - low)))
        return max(-1.0, min(1.0, amplitude * (1.0 - 2.0 * fraction)))

    def _legacy_height_z(self, height_m: float) -> float:
        ride_name = self.park_name_for_experience(self._selected)
        profile = config.LEGACY_HEIGHT_PROFILES.get(ride_name)
        if profile:
            bottom = float(profile["bottom_height_m"])
            top = float(profile["lift_top_height_m"])
            fraction = (float(height_m) - bottom) / max(0.001, top - bottom)
            fraction = max(0.0, min(1.0, fraction))
            z_bottom = float(profile["z_at_bottom"])
            z_top = float(profile["z_at_lift_top"])
            return z_bottom + fraction * (z_top - z_bottom)

        # Preserve the established dynamic mapping for rides without a profile.
        self._lift_height = max(self._lift_height, float(height_m))
        heave = ((float(height_m) * 2.0) / self._lift_height) - 1.0
        return max(
            -1.0,
            min(1.0, heave * config.TELEMETRY_INTENSITY_FACTOR),
        )

    @staticmethod
    def _quaternion_to_rpy(x: float, y: float, z: float, w: float):
        # NL2 uses X lateral, Y vertical and Z longitudinal. Decompose its
        # quaternion as yaw(Y), pitch(X), roll(Z), matching the established
        # POC roll/pitch directions while correcting the old component errors.
        roll = math.atan2(2.0 * (x * y + w * z), 1.0 - 2.0 * (x * x + z * z))
        pitch_sine = max(-1.0, min(1.0, 2.0 * (w * x - y * z)))
        pitch = math.asin(pitch_sine)
        yaw = math.atan2(2.0 * (x * z + w * y), 1.0 - 2.0 * (x * x + y * y))
        return roll, pitch, yaw

    @staticmethod
    def _normalize_quaternion(x: float, y: float, z: float, w: float):
        magnitude = math.sqrt(x * x + y * y + z * z + w * w)
        if magnitude < 1.0e-12:
            return 0.0, 0.0, 0.0, 1.0
        return x / magnitude, y / magnitude, z / magnitude, w / magnitude

    def _yaw_rate_from_quaternion(self, current, received_at: float) -> float:
        """Return local vertical-axis angular velocity in radians/second."""
        previous = self._previous_quaternion
        previous_time = self._previous_quaternion_time
        self._previous_quaternion = current
        self._previous_quaternion_time = float(received_at)
        if previous is None or previous_time is None:
            return 0.0

        dt = float(received_at) - previous_time
        if dt <= 0.0:
            return 0.0

        px, py, pz, pw = previous
        cx, cy, cz, cw = current
        # conjugate(previous) * current expresses the shortest rotation in the
        # vehicle's local coordinate frame. q and -q represent the same pose;
        # force a non-negative scalar component to avoid false full turns.
        dx = pw * cx - px * cw - py * cz + pz * cy
        dy = pw * cy + px * cz - py * cw - pz * cx
        dz = pw * cz - px * cy + py * cx - pz * cw
        dw = pw * cw + px * cx + py * cy + pz * cz
        if dw < 0.0:
            dx, dy, dz, dw = -dx, -dy, -dz, -dw

        vector_length = math.sqrt(dx * dx + dy * dy + dz * dz)
        if vector_length < 1.0e-12:
            raw_yaw_rate = 0.0
        else:
            angle = 2.0 * math.atan2(vector_length, max(0.0, min(1.0, dw)))
            raw_yaw_rate = (angle * dy / vector_length) / dt
        tau = max(0.0, float(config.YAW_RATE_LOW_PASS_TAU_S))
        alpha = 1.0 if tau == 0.0 else dt / (tau + dt)
        self._filtered_yaw_rate += alpha * (raw_yaw_rate - self._filtered_yaw_rate)
        return self._filtered_yaw_rate

    def _reset_transform_tracking(self) -> None:
        self._previous_quaternion = None
        self._previous_quaternion_time = None
        self._filtered_yaw_rate = 0.0
        self._last_telemetry_frame = None
