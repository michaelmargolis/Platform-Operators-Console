#sim.py  ver 30
from __future__ import annotations

import logging
import math
import time
from typing import Optional

from core.sim_interface import SimInterface
from core.types import ExperienceState, SimSample, SimStatus
from sims.nolimits2 import config
from sims.nolimits2.nl2_protocol import NL2Protocol, NL2Telemetry

log = logging.getLogger(__name__)

# State-transition diagnostics are intentionally separate from the high-rate
# protocol trace in nl2_protocol.py.  These messages show why POC decides that
# a park reset is or is not required.
NL2_STATE_DEBUG = True


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

    def __init__(self, address="127.0.0.1", client: Optional[NL2Protocol] = None):
        self.address = address
        self.client = client or NL2Protocol(
            address,
            port=config.PORT,
            connect_timeout=config.CONNECT_TIMEOUT_S,
        )
        self.state = ExperienceState.DISCONNECTED
        self._selected = int(getattr(config, "DEFAULT_RIDE_INDEX", 0))
        self._rides = self._discover_rides()
        self._experiences = tuple(name for name, _path in self._rides)
        self._last_transform = (0.0,) * 6
        self._pause_target = None
        self._dispatch_pending = False
        self._dispatch_station_seen_at = 0.0
        self._reset_requested_at = 0.0
        self._initial_sync = False
        self._telemetry_valid = False
        self._leaving_station = False
        self._last_station_request = 0.0
        self._last_connect_attempt = 0.0
        self._previous_quaternion = None
        self._previous_quaternion_time = None
        self._filtered_yaw_rate = 0.0
        self._last_telemetry_frame = None
        self._lift_height = float(config.INITIAL_LIFT_HEIGHT_M)
        self._legacy_heave_enabled = (
            config.HEAVE_MODE == config.HEAVE_MODE_LEGACY_HEIGHT
        )
        self._connected_sync_started = False
        self._data_connected = False
        self._heartbeat_connected = False
        self._last_api_probe = 0.0
        self._manual_mode_requested = False
        self._park_reset_pending = False
        self._ride_load_pending = False
        self._ride_load_requested_at = 0.0
        self._ride_load_request_id = None
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
        # Startup is deterministic: always command the configured default park.
        # NL2 may have been left running any other park.  LOAD_PARK is requested
        # even when DEFAULT_RIDE_INDEX is zero and even when that same park is
        # already loaded.  Completion is based on NL2's request response, not on
        # a play-mode transition (there may be none for an already-loaded park).
        self._selected = int(getattr(config, "DEFAULT_RIDE_INDEX", 0))
        if not 0 <= self._selected < len(self._rides):
            self._selected = 0
        self._initial_sync = False
        self._begin_ride_load(self._selected, startup=True)

    def disconnect(self) -> None:
        self.client.disconnect()
        self.state = ExperienceState.DISCONNECTED
        self._telemetry_valid = False
        self._last_transform = (0.0,) * 6
        self._pause_target = None
        self._dispatch_pending = False
        self._dispatch_station_seen_at = 0.0
        self._reset_requested_at = 0.0
        self._initial_sync = False
        self._connected_sync_started = False
        self._data_connected = False
        self._manual_mode_requested = False
        self._park_reset_pending = False
        self._ride_load_pending = False
        self._ride_load_requested_at = 0.0
        self._ride_load_request_id = None
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
        self._update_state(telemetry)
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
        self._dispatch_station_seen_at = float(
            getattr(self.client, "station_status_received_at", 0.0) or 0.0
        )
        self._request_station_state()
        if NL2_STATE_DEBUG:
            log.debug("NL2 dispatch requested: waiting for station preparation")

    def cancel_pending_dispatch(self) -> None:
        # Do not allow an asynchronous station-preparation sequence to send
        # DISPATCH after the physical activation switch has gone OFF.
        self._dispatch_pending = False
        self._dispatch_station_seen_at = 0.0

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
        self._leaving_station = False
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
            and not self._dispatch_pending
            and not self._ride_load_pending
            and self._stopped_in_station()
            and self._manual_mode_is_set()
        )

    def reset_vr(self) -> None:
        self.client.recenter_vr()

    def _discover_rides(self):
        # The park files live on the Windows simulator PC, not on POC.  The
        # complete deployed list therefore comes from config.RIDES; never scan
        # the local POC filesystem.
        rides = tuple((str(name), str(park_path)) for name, park_path in getattr(config, "RIDES", ()))
        if not rides:
            return (("Current NoLimits2 ride", ""),)
        return rides

    def get_experiences(self):
        return self._experiences

    def select_experience(self, index: int) -> None:
        if not 0 <= index < len(self._experiences):
            raise IndexError(index)
        if index == self._selected:
            return
        if not self._sim_connected():
            raise RuntimeError("NoLimits2 must be connected before changing ride")
        self._begin_ride_load(index, startup=False)

    def _begin_ride_load(self, index: int, startup: bool = False) -> None:
        _name, park_path = self._rides[index]
        if not park_path:
            raise RuntimeError("No NL2 park path configured for this ride")

        self._selected = index
        self._pause_target = None
        self._dispatch_pending = False
        self._leaving_station = False
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
            text = f"Loading NoLimits2 ride: {self._experiences[self._selected]}"
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
        self._ride_load_pending = False
        self._ride_load_request_id = None
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

    def _update_state(self, telemetry: NL2Telemetry) -> None:
        in_station = self._train_in_station()
        stationary = abs(float(telemetry.speed)) < config.STATIONARY_SPEED_MPS

        if self._leaving_station and not in_station:
            self._leaving_station = False

        # The tested coaster implementation keeps NL2 in manual station mode.
        # This is essential: without bit_manual set, NL2 may auto-dispatch a
        # train at startup or immediately after RESET_PARK.  Never declare the
        # ride READY, and never send a pending park reset, until manual mode is
        # confirmed by station state.
        if self.client.station_status is not None:
            if not self._manual_mode_is_set():
                self._request_manual_mode()
                # Manual-mode setup alone is not a reset operation.  Keep a
                # freshly loaded ride in the stable READY/waiting condition;
                # actual activation/recovery reset paths explicitly enter
                # RESETTING via _start_park_reset().
                return
            if self._manual_mode_requested:
                if NL2_STATE_DEBUG:
                    log.debug("NL2 manual mode confirmed by station state")
                self._manual_mode_requested = False
            if self._park_reset_pending:
                self._send_park_reset()
                return

        # Initial connection only synchronizes with NL2. Connecting POC must
        # never alter the current ride. Park reset belongs exclusively to
        # prepare_for_activation(), where the operator has requested platform
        # activation/recovery.
        if self._initial_sync:
            if self.client.station_status is None:
                return
            self._initial_sync = False
            if NL2_STATE_DEBUG:
                status = int(self.client.station_status)
                log.debug(
                    "NL2 initial sync: speed=%.3f stationary=%s station=0x%08x "
                    "train_bit=%s current_train_bit=%s in_station=%s",
                    float(telemetry.speed),
                    stationary,
                    status,
                    bool(status & 0x0800),
                    bool(status & 0x1000),
                    in_station,
                )
            if stationary and in_station and self._manual_mode_is_set():
                self.state = ExperienceState.READY
                if NL2_STATE_DEBUG:
                    log.debug("NL2 initial sync: train confirmed stopped in station/manual -> READY")
            else:
                # We know NL2 is connected and have synchronized its current
                # condition, but do not disturb a ride already in progress.
                # Activation will decide whether RESET_PARK is required.
                self.state = ExperienceState.READY
                if NL2_STATE_DEBUG:
                    log.debug("NL2 initial sync: ride not ready in station -> no reset until activation")
            return

        if self.state is ExperienceState.RESETTING:
            # Waiting only for manual-mode confirmation is also represented as
            # RESETTING.  If no RESET_PARK has been sent and no reset is
            # pending, stationary + in-station + manual mode is sufficient to
            # become READY.
            if self._reset_requested_at <= 0.0 and not self._park_reset_pending:
                if stationary and in_station and self._manual_mode_is_set():
                    self.state = ExperienceState.READY
                    self._estop_recovery_pending = False
                return

            # A park reset can temporarily remove telemetry/control
            # availability.  Do not accept cached pre-reset station/telemetry
            # as completion; both observations must have arrived after the
            # reset command.
            station_time = float(getattr(self.client, "station_status_received_at", 0.0) or 0.0)
            if (
                self._reset_requested_at > 0.0
                and telemetry.received_at >= self._reset_requested_at
                and station_time >= self._reset_requested_at
                and stationary
                and in_station
                and self._manual_mode_is_set()
            ):
                self.state = ExperienceState.READY
                self._reset_requested_at = 0.0
                self._estop_recovery_pending = False
            return

        # Dispatch preparation is asynchronous. Remain READY until the actual
        # NL2 DISPATCH command has been sent; normal telemetry state processing
        # must not reinterpret the waiting state meanwhile.
        if self._dispatch_pending:
            return

        # Pause/unpause commands are asynchronous and telemetry is authoritative.
        # Keep the existing logical state until NL2 reports the requested state.
        if self._pause_target is not None:
            if telemetry.paused == self._pause_target:
                self.state = ExperienceState.PAUSED if self._pause_target else ExperienceState.RUNNING
                self._pause_target = None
            return

        if telemetry.paused:
            if self.state in (ExperienceState.RUNNING, ExperienceState.PAUSED):
                self.state = ExperienceState.PAUSED
            return

        if self.state is ExperienceState.PAUSED:
            self.state = ExperienceState.RUNNING

        if self.state is ExperienceState.RUNNING:
            if stationary and in_station and not self._leaving_station:
                self.state = ExperienceState.READY
            return

        # READY is an operator waiting state.  Match the proven early coaster
        # controller: telemetry must never promote READY to RUNNING.  RUNNING is
        # entered only by an explicit POC dispatch request (or by unpausing a
        # ride that POC already knows is PAUSED).  This prevents activation,
        # startup synchronization, or reset completion from starting a ride.
        if stationary and in_station:
            self.state = ExperienceState.READY

    def _service_dispatch_preparation(self) -> None:
        """Non-blocking equivalent of the old prepare_for_dispatch() loop."""
        if not self._dispatch_pending or not self._sim_connected():
            return

        status = self.client.station_status
        station_time = float(getattr(self.client, "station_status_received_at", 0.0) or 0.0)
        if status is None or station_time <= self._dispatch_station_seen_at:
            return

        self._dispatch_station_seen_at = station_time
        status = int(status)

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
                log.debug("NL2 dispatch preparation complete: sending DISPATCH")
            self._dispatch()
            self._dispatch_pending = False
            self.state = ExperienceState.RUNNING
            self._leaving_station = True
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
        self._reset_requested_at = 0.0
        self._park_reset_pending = True

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
        indices = self._station_indices()
        if indices is None:
            request_identity = getattr(self.client, "request_current_coaster_and_station", None)
            if callable(request_identity):
                request_identity()
            return
        self.client.request_station_state(*indices)

    def _set_manual_mode(self, enabled: bool) -> bool:
        indices = self._station_indices()
        if indices is None:
            self._request_station_state()
            return False
        self.client.set_manual_mode(bool(enabled), *indices)
        return True

    def _set_platform(self, lowered: bool) -> None:
        indices = self._station_indices()
        if indices is None:
            self._request_station_state()
            return
        self.client.set_platform(bool(lowered), *indices)

    def _dispatch(self) -> None:
        indices = self._station_indices()
        if indices is None:
            self._request_station_state()
            return
        self.client.dispatch(*indices)

    def _manual_mode_is_set(self) -> bool:
        status = self.client.station_status
        return status is not None and bool(int(status) & self.MANUAL_MODE_MASK)

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
        return self._train_in_station()

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
        # velocity for yaw. The established lift-height heave cue remains a
        # static configuration option for coaster operation.
        quaternion = self._normalize_quaternion(
            tm.quat_x, tm.quat_y, tm.quat_z, tm.quat_w
        )
        roll, pitch, _yaw = self._quaternion_to_rpy(*quaternion)
        roll = roll / math.pi
        pitch = -pitch * 0.6

        yaw_rate = self._yaw_rate_from_quaternion(quaternion, tm.received_at)
        yaw_rate *= float(config.YAW_RATE_GAIN)

        if self._legacy_heave_enabled:
            heave = self._legacy_height_z(float(tm.pos_y))
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
        return tuple(transform)

    def set_legacy_heave_enabled(self, enabled: bool) -> None:
        """Select the temporary NL2 heave comparison mode for this session."""
        self._legacy_heave_enabled = bool(enabled)

    def _legacy_height_z(self, height_m: float) -> float:
        ride_name = self._experiences[self._selected]
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
