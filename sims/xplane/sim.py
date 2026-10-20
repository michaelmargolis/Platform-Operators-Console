#sim.py  ver 10
from __future__ import annotations

import json
import logging
import time
from typing import Mapping

from core.sim_interface import SimInterface
from core.types import ExperienceState, SimSample, SimStatus
from sims.xplane import config
from sims.xplane.transport import XplaneTransport

log = logging.getLogger(__name__)


class Sim(SimInterface):
    """POC adapter for the current SOP X-Plane telemetry plugin.

    Aircraft validation, pilot-assist controls and the old flight-mode UI are
    not part of this adapter. POC always owns one of the three predefined SOP
    situations and loads it through the existing X-Plane FlightMode command.

    ``sim/time/paused`` is carried in the telemetry JSON by the companion
    PI_Mdx_telemetry_ui.py update and is authoritative for confirming load,
    RUNNING and PAUSED transitions.
    """

    name = "X-Plane"
    run_label = "Fly"
    pause_label = "Pause"
    experience_noun = "flight"
    service_period_ms = config.SERVICE_PERIOD_MS
    hot_switch = False
    fan_config = config.FAN_CONFIG

    def __init__(self, address: str = "127.0.0.1", transport=None, time_func=None):
        self.address = address
        self.transport = transport or XplaneTransport(
            address,
            config.TELEMETRY_EVT_PORT,
            config.TELEMETRY_CMD_PORT,
            config.HEARTBEAT_PORT,
        )
        self._time = time_func or time.monotonic

        self.state = ExperienceState.DISCONNECTED
        self._transform = (0.0,) * 6
        self._speed = None
        self._selected = config.DEFAULT_EXPERIENCE_INDEX
        self._experiences = tuple(label for label, _mode in config.EXPERIENCES)
        self._experience_modes = tuple(mode for _label, mode in config.EXPERIENCES)
        self._load_pending = False
        self._load_was_connected = False
        self._experience_loaded = False

        self._last_telemetry_time = None
        self._last_heartbeat_time = None
        self._last_initcoms_time = 0.0
        self._last_heartbeat_ping_time = 0.0
        self._last_plugin_ping_time = 0.0
        self._heartbeat_ok = False
        self._heartbeat_xplane_running = False
        self._started_by_poc = False
        self._paused = None
        self._paused_available = False
        self._status_text = "Not connected"
        self.last_error = ""

        # Debug-only state used to report changes rather than flood the log at
        # the 20 Hz telemetry rate.
        self._debug_last_pause = object()
        self._debug_pause_field_seen = False
        self._debug_last_missing_pause_log = 0.0
        self._debug_last_connection = None
        self._debug_last_state = self.state

    # ------------------------------------------------------------------
    # SimInterface
    # ------------------------------------------------------------------
    def connect(self) -> None:
        try:
            self.transport.open()
            self.state = ExperienceState.DISCONNECTED
            self._status_text = "Waiting for X-Plane telemetry"
            # Match SOP startup behavior: immediately ask the plugin to add this
            # controller to its telemetry target list.
            self._send_initcoms(force=True)
        except Exception as exc:
            self.last_error = str(exc)
            self.state = ExperienceState.DISCONNECTED
            self._status_text = f"X-Plane transport error: {exc}"
            log.exception("Unable to initialize X-Plane UDP transport")

    def disconnect(self) -> None:
        self.transport.close()
        self.state = ExperienceState.DISCONNECTED
        self._last_telemetry_time = None
        self._experience_loaded = False
        self._load_pending = False
        self._started_by_poc = False
        self._heartbeat_ok = False
        self._heartbeat_xplane_running = False
        self._status_text = "Disconnected"
        self._speed = None

    def read(self) -> SimSample:
        self._receive_telemetry()
        self._update_connection_state()
        return SimSample(
            transform=self._transform,
            state=self.state,
            telemetry_valid=self._data_connected(),
            speed=self._speed,
        )

    def service(self) -> None:
        now = self._time()
        self._receive_heartbeat(now)

        if now - self._last_heartbeat_ping_time >= config.HEARTBEAT_INTERVAL_S:
            try:
                self.transport.send_heartbeat_ping()
                self._last_heartbeat_ping_time = now
            except Exception as exc:
                self.last_error = str(exc)
                if config.XPLANE_DEBUG:
                    log.debug("X-Plane heartbeat send failed: %s", exc)

        # SOP sends InitComs while waiting for data. Repeating it is harmless
        # and allows recovery after the X-Plane plugin is restarted.
        if not self._data_connected():
            self._send_initcoms()
        elif now - self._last_plugin_ping_time >= 1.0:
            # PI_Mdx_telemetry_ui.py drops its controller after ~3 seconds
            # unless it receives the same telemetry-channel "ping" used by SOP.
            self._send_command("ping")
            self._last_plugin_ping_time = now

        self._update_connection_state()

    def run(self) -> None:
        if self.state not in (ExperienceState.READY, ExperienceState.PAUSED):
            if config.XPLANE_PAUSE_DEBUG:
                log.debug("X-Plane Fly ignored: state=%s", self.state.name)
            return
        self._started_by_poc = True
        if config.XPLANE_PAUSE_DEBUG:
            log.debug(
                "X-Plane Fly: state=%s paused_available=%s paused=%r -> TX Run",
                self.state.name, self._paused_available, self._paused,
            )
        self._send_command("Run")
        # Wait for paused=0 telemetry to confirm RUNNING.

    def pause(self) -> None:
        if self.state is ExperienceState.RUNNING:
            if config.XPLANE_PAUSE_DEBUG:
                log.debug(
                    "X-Plane Pause: state=RUNNING paused_available=%s paused=%r -> TX Pause",
                    self._paused_available, self._paused,
                )
            self._send_command("Pause")
        elif self.state is ExperienceState.PAUSED:
            if config.XPLANE_PAUSE_DEBUG:
                log.debug(
                    "X-Plane Pause toggle: state=PAUSED paused_available=%s paused=%r -> TX Run",
                    self._paused_available, self._paused,
                )
            self._send_command("Run")
        elif config.XPLANE_PAUSE_DEBUG:
            log.debug("X-Plane Pause ignored: state=%s", self.state.name)

    def prepare_for_activation(self) -> None:
        # Platform and simulator state machines remain independent. If this
        # predefined flight was actually started through POC, activation after
        # an abort reloads the selected experience, analogous to NL2 returning
        # its train to the station. If the flight was loaded but never started,
        # there is nothing to recover.
        if self._load_pending:
            return
        if self._started_by_poc and self._data_connected():
            self._begin_load(self._selected, reason="activation recovery")
            return
        if self._experience_loaded and self._data_connected():
            self.state = ExperienceState.READY
            self._status_text = f"{self._experiences[self._selected]} ready"
        elif not self._sim_connected():
            self.state = ExperienceState.DISCONNECTED

    def recover_from_estop(self) -> None:
        """Reload the selected predefined situation after an interrupted flight."""
        if not self._data_connected():
            raise RuntimeError("X-Plane telemetry must be connected for E-STOP recovery")
        if not self._load_pending:
            self._begin_load(self._selected, reason="E-STOP recovery")

    def is_ready_to_start(self) -> bool:
        return bool(
            self._data_connected()
            and self._experience_loaded
            and not self._load_pending
            and not self._started_by_poc
            and self.state is ExperienceState.READY
        )

    def reset_vr(self) -> None:
        # No X-Plane VR reset behavior is required in this POC phase.
        pass

    def get_experiences(self):
        return self._experiences

    def select_experience(self, index: int) -> None:
        if not 0 <= index < len(self._experiences):
            raise IndexError(index)
        if index == self._selected:
            return
        if not self._data_connected():
            raise RuntimeError("X-Plane telemetry must be connected before loading a flight")
        self._selected = index
        self._begin_load(index, reason="experience selection")

    def _begin_load(self, index: int, reason: str) -> None:
        """Load one of the predefined SOP situations and wait for paused data."""
        mode = self._experience_modes[index]
        # SOP pauses before sending FlightMode. Drain buffered telemetry first
        # so load completion cannot be confirmed by an old pre-load sample.
        self.transport.drain_telemetry()
        self._started_by_poc = False
        self._experience_loaded = False
        self._load_pending = True
        self._load_was_connected = self._sim_connected()
        self.state = ExperienceState.RESETTING
        self._status_text = f"Loading {self._experiences[index]}"
        self._send_command("Pause")
        self._send_command(f"FlightMode,{mode}")
        if config.XPLANE_LOAD_DEBUG:
            log.debug(
                "X-Plane load requested (%s): index=%d mode=%s label=%s",
                reason, index, mode, self._experiences[index],
            )

    def get_status(self) -> SimStatus:
        pc_connected = self._pc_connected()
        sim_connected = self._sim_connected()
        data_connected = self._data_connected()

        if not pc_connected:
            text = "Waiting for simulator PC"
        elif not sim_connected:
            text = "Waiting for X-Plane"
        elif not data_connected:
            text = "Waiting for X-Plane telemetry"
        else:
            text = self._status_text

        return SimStatus(
            pc_connected=pc_connected,
            sim_connected=sim_connected,
            data_connected=data_connected,
            text=text,
            entity_name=self._experiences[self._selected],
            busy=self._load_pending,
            ready_to_start=self.is_ready_to_start(),
            activity="loading" if self._load_pending else "",
        )

    # ------------------------------------------------------------------
    # Telemetry / state
    # ------------------------------------------------------------------
    def _receive_telemetry(self) -> None:
        messages = self.transport.drain_telemetry()
        if not messages:
            return

        latest = None
        for msg in messages:
            try:
                payload = json.loads(msg)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(payload, Mapping) and payload.get("header") == "xplane_telemetry":
                latest = payload

        if latest is None:
            return

        try:
            factors = config.GROUND_FACTORS if bool(latest.get("on_ground", 0)) else config.AIR_FACTORS
            self._transform = tuple(
                float(latest[key]) * float(factor)
                for key, factor in zip(config.TELEMETRY_KEYS, factors)
            )
        except (KeyError, TypeError, ValueError) as exc:
            self.last_error = f"Bad X-Plane telemetry: {exc}"
            log.warning(self.last_error)
            return

        try:
            self._speed = max(0.0, float(latest["speed"]))
        except (KeyError, TypeError, ValueError):
            # Retain motion compatibility with an older X-Plane plugin while
            # reporting no fan speed until the updated plugin is installed.
            self._speed = None

        now = self._time()
        self._last_telemetry_time = now
        if "paused" in latest:
            raw_paused = latest.get("paused")
            try:
                self._paused = bool(int(raw_paused))
                self._paused_available = True
                self._debug_pause_field_seen = True
                if config.XPLANE_PAUSE_DEBUG and self._paused != self._debug_last_pause:
                    log.debug(
                        "X-Plane RX pause: raw=%r paused=%s started_by_poc=%s state_before=%s",
                        raw_paused, self._paused, self._started_by_poc, self.state.name,
                    )
                    self._debug_last_pause = self._paused
            except (TypeError, ValueError):
                self._paused = None
                self._paused_available = False
                if config.XPLANE_PAUSE_DEBUG:
                    log.debug("X-Plane RX invalid paused value: %r", raw_paused)
        else:
            self._paused = None
            self._paused_available = False
            if config.XPLANE_PAUSE_DEBUG:
                if now - self._debug_last_missing_pause_log >= config.XPLANE_MISSING_PAUSE_LOG_INTERVAL_S:
                    log.debug(
                        "X-Plane RX telemetry has no paused field; fields=%s",
                        sorted(latest.keys()),
                    )
                    self._debug_last_missing_pause_log = now

        if not self._experience_loaded and not self._load_pending:
            # POC always starts X-Plane from a known predefined situation. The
            # first valid telemetry proves the plugin is available; use that
            # opportunity to load the configured default flight immediately.
            self._begin_load(self._selected, reason="initial connection")
            return

        if self._load_pending:
            # SituationLoader pauses a newly loaded situation before returning.
            # A fresh paused telemetry sample therefore confirms the load has
            # completed and is safe to present as READY.
            if self._paused_available and self._paused:
                self._load_pending = False
                self._load_was_connected = False
                self._experience_loaded = True
                self._started_by_poc = False
                self.state = ExperienceState.READY
                self._status_text = f"{self._experiences[self._selected]} ready"
                if config.XPLANE_LOAD_DEBUG:
                    log.debug("X-Plane load complete: %s", self._experiences[self._selected])
            else:
                self.state = ExperienceState.RESETTING
                self._status_text = f"Loading {self._experiences[self._selected]}"
        elif not self._started_by_poc:
            self.state = ExperienceState.READY
            self._status_text = f"{self._experiences[self._selected]} ready"
        elif self._paused_available:
            if self._paused:
                self.state = ExperienceState.PAUSED
                self._status_text = "X-Plane paused"
            else:
                self.state = ExperienceState.RUNNING
                self._status_text = "X-Plane running"
        else:
            # The current POC XPPython plugin always supplies paused. If it is
            # missing, keep the last logical state and expose the condition via
            # the existing pause diagnostics rather than guessing the state.
            self._status_text = f"X-Plane {self.state.name.lower()} (no pause dataref feedback)"

        if config.XPLANE_PAUSE_DEBUG and self.state is not self._debug_last_state:
            log.debug(
                "X-Plane state: %s -> %s (paused_available=%s paused=%r started_by_poc=%s)",
                self._debug_last_state.name, self.state.name,
                self._paused_available, self._paused, self._started_by_poc,
            )
            self._debug_last_state = self.state

        self.last_error = ""

    def _receive_heartbeat(self, now: float) -> None:
        messages = self.transport.drain_heartbeat()
        if messages:
            self._last_heartbeat_time = now
            self._heartbeat_ok = True
            self._heartbeat_xplane_running = any("xplane_running" in msg for msg in messages)

        if (
            self._last_heartbeat_time is None
            or now - self._last_heartbeat_time > config.HEARTBEAT_INTERVAL_S * 2.0
        ):
            self._heartbeat_ok = False
            self._heartbeat_xplane_running = False

    def _update_connection_state(self) -> None:
        now = self._time()
        pc_connected = self._pc_connected()
        data_connected = self._data_connected()
        sim_connected = self._heartbeat_xplane_running or data_connected
        current = (pc_connected, sim_connected, data_connected)

        if config.XPLANE_CONNECTION_DEBUG and current != self._debug_last_connection:
            telemetry_age = (
                None if self._last_telemetry_time is None
                else now - self._last_telemetry_time
            )
            log.debug(
                "X-Plane connection: PC=%s Sim=%s Data=%s telemetry_age=%s "
                "heartbeat_ok=%s heartbeat_xplane=%s",
                pc_connected, sim_connected, data_connected,
                "none" if telemetry_age is None else f"{telemetry_age:.3f}s",
                self._heartbeat_ok, self._heartbeat_xplane_running,
            )
            self._debug_last_connection = current

        if not sim_connected:
            # Situation loading can temporarily interrupt telemetry. If the
            # link was known-good when loading began, retain RESETTING instead
            # of interpreting that expected gap as a disconnect.
            if self._load_pending and self._load_was_connected:
                self.state = ExperienceState.RESETTING
                return
            if config.XPLANE_CONNECTION_DEBUG and self.state is not ExperienceState.DISCONNECTED:
                telemetry_age = (
                    None if self._last_telemetry_time is None
                    else now - self._last_telemetry_time
                )
                log.debug(
                    "X-Plane state -> DISCONNECTED: no valid sim link; telemetry_age=%s timeout=%.3fs",
                    "none" if telemetry_age is None else f"{telemetry_age:.3f}s",
                    config.TELEMETRY_TIMEOUT_S,
                )
            self.state = ExperienceState.DISCONNECTED
            self._debug_last_state = self.state
            return
        # If X-Plane is known alive through heartbeat but telemetry is absent,
        # keep the last logical state.  Data gating prevents Run/Pause and POC
        # motion until fresh telemetry returns.

    def _pc_connected(self) -> bool:
        return self._is_localhost(self.address) or self._heartbeat_ok

    def _sim_connected(self) -> bool:
        # On deployed remote systems heartbeat can distinguish PC from X-Plane.
        # During localhost development, fresh telemetry proves the datalink.
        # Preserve that proven link through the expected telemetry interruption
        # while a situation is loading.
        return (
            self._heartbeat_xplane_running
            or self._data_connected()
            or (self._load_pending and self._load_was_connected)
        )

    def _data_connected(self) -> bool:
        if self._last_telemetry_time is None:
            return False
        return self._time() - self._last_telemetry_time <= config.TELEMETRY_TIMEOUT_S

    @staticmethod
    def _is_localhost(address: str) -> bool:
        return str(address).strip().lower() in {"127.0.0.1", "localhost", "::1"}

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    def _send_initcoms(self, force: bool = False) -> None:
        now = self._time()
        if not force and now - self._last_initcoms_time < config.INIT_COMMS_INTERVAL_S:
            return
        try:
            self.transport.send_command("InitComs")
            self._last_initcoms_time = now
            if config.XPLANE_DEBUG:
                log.debug("X-Plane TX InitComs")
        except Exception as exc:
            self.last_error = str(exc)
            if config.XPLANE_DEBUG:
                log.debug("X-Plane InitComs send failed: %s", exc)

    def _send_command(self, command: str) -> None:
        try:
            self.transport.send_command(command)
            if config.XPLANE_DEBUG:
                log.debug("X-Plane TX %s", command)
        except Exception as exc:
            self.last_error = str(exc)
            log.warning("X-Plane command %s failed: %s", command, exc)
