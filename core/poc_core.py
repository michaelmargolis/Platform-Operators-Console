#poc_core.py  ver 54
from __future__ import annotations

import logging
import math
import sys
import time
from collections import deque
from dataclasses import replace
from pathlib import Path
from PyQt5 import QtCore

from core.platform_controller import PlatformController, SopPlatformBackend
from core.digital_twin import DigitalTwinSender
from core.telemetry_config import TelemetryConfig
from core.sim_registry import create_sim, definition_for
from core.types import (
    ActivationControlMode,
    ExperienceState,
    PlatformState,
    PlatformMotionState,
    SimSample,
    SimStatus,
    compose_operator_status_text,
)
from controls.serial_remote import SerialRemote
from sim_host_service.client import SimHostClient

log = logging.getLogger(__name__)

# Temporary UI/operator-action tracing. Keep disabled for normal operation.
POC_UI_DEBUG = False

# Log the simulator adapter's original status text at core boundary.
# This is diagnostic only; operator text is composed centrally below.
RAW_SIM_STATUS_DEBUG = False


class PocCore(QtCore.QObject):
    simChanged = QtCore.pyqtSignal(str)
    simStatusChanged = QtCore.pyqtSignal(object)
    sampleUpdated = QtCore.pyqtSignal(object)
    experienceStateChanged = QtCore.pyqtSignal(object)
    platformStateChanged = QtCore.pyqtSignal(object)
    platformActivationChanged = QtCore.pyqtSignal(int)
    experiencesChanged = QtCore.pyqtSignal(list, int)
    message = QtCore.pyqtSignal(str)
    intensityChanged = QtCore.pyqtSignal(int)
    payloadChanged = QtCore.pyqtSignal(int)
    encoderRotated = QtCore.pyqtSignal(int)
    encoderPressed = QtCore.pyqtSignal()
    simulatorBrowseChanged = QtCore.pyqtSignal(bool)
    activateSwitchWarningChanged = QtCore.pyqtSignal(bool)
    activationControlModeChanged = QtCore.pyqtSignal(object)

    DATA_PERIOD_MS = 50
    PERFORMANCE_WINDOW_FRAMES = 200  # 10 seconds at 20 Hz
    FAN_SEND_PERIOD_S = 0.1

    def __init__(self, config_store, parent=None):
        super().__init__(parent)
        self.config_store = config_store

        # Always use the SOP kinematics/D->P/Festo output path. The Festo
        # transport is UDP and does not require a software output gate.
        from config.platforms import PLATFORMS
        self.platform_key = self.config_store.get("selected_platform", "platform_v2")
        platform_def = PLATFORMS[self.platform_key]
        self.platform_name = platform_def.get("platform_name", self.platform_key)
        platform_backend = SopPlatformBackend(
            platform_def["sop_config_module"],
            platform_def.get("festo_ip", "192.168.0.10"),
        )
        self.platform = PlatformController(backend=platform_backend)
        self.platform.set_state_callback(self._on_platform_state)
        self.sim = None
        self.sim_key = None
        self.experience_state = ExperienceState.DISCONNECTED
        self._estop_latched = False
        self._estop_recovery_active = False
        self._dispatch_unpark_pending = False
        self._ride_dispatched = False
        self._ride_has_run = False
        self._estop_running_pause_sent = False
        # Windows defaults to the touch controls; Linux retains the switch
        # default. A physical OFF observation arms an OFF-to-ON activation
        # only when switch mode has been selected.
        self.activation_control_mode = (
            ActivationControlMode.TOUCH
            if sys.platform == "win32"
            else ActivationControlMode.SWITCH
        )
        self._activation_switch_armed = False
        self._activation_request_result = (
            "Touch UI control selected; physical switch ignored"
            if self.activation_control_mode is ActivationControlMode.TOUCH
            else "waiting for physical switch state"
        )
        self._last_sample = SimSample()
        self.intensity_level = 2
        self.intensity_percent = 100
        self.payload_level = 1
        self.telemetry_config = None
        self._motion_transform = (0.0,) * 6
        self._last_fan_power = None
        self._last_fan_send_time = -1e9

        # Main 20 Hz callback timing; platform-independent (Windows and Pi).
        self.processing_percent = 0
        self.jitter_percent = 0
        self.processing_max_percent = 0
        self._last_data_tick_started_at = None
        self._processing_history = deque(maxlen=self.PERFORMANCE_WINDOW_FRAMES)

        host_cfg = self.config_store.get("sim_host_service", {})
        self.host_service_enabled = bool(host_cfg.get("enabled", True))
        self.host_service = SimHostClient(
            address=host_cfg.get("address", "192.168.0.4"),
            port=int(host_cfg.get("port", 10040)),
            heartbeat_interval_s=float(host_cfg.get("heartbeat_interval_s", 1.0)),
            heartbeat_timeout_s=float(host_cfg.get("heartbeat_timeout_s", 3.0)),
        )

        twin_cfg = self.config_store.get("digital_twin", {})
        if not isinstance(twin_cfg, dict):
            twin_cfg = {}
        self.digital_twin = DigitalTwinSender(
            address=twin_cfg.get("address", "-"),
            port=twin_cfg.get("port", 10020),
        )

        control_actions = {
            "activate": self.request_switch_activate,
            "activate_startup_invalid": self._activate_switch_startup_invalid,
            "deactivate": self.request_switch_deactivate,
            "dispatch": self.request_run,
            "dispatch_press": self.request_dispatch_press,
            "dispatch_release": self.request_dispatch_release,
            "pause": self.request_pause,
            "reset_vr": self.request_reset_vr,
            "intensity": self.request_remote_intensity,
            "payload": self.request_payload_level,
            "encoder_rotate": self.request_encoder_rotate,
            "encoder_press": self.request_encoder_press,
        }
        self.remote = self._create_physical_control_interface(control_actions)

        self.data_timer = QtCore.QTimer(self)
        self.data_timer.setTimerType(QtCore.Qt.PreciseTimer)
        self.data_timer.setInterval(self.DATA_PERIOD_MS)
        self.data_timer.timeout.connect(self._data_tick)

        self.service_timer = QtCore.QTimer(self)
        self.service_timer.setTimerType(QtCore.Qt.PreciseTimer)
        self.service_timer.timeout.connect(self._service_tick)


    @staticmethod
    def _is_raspberry_pi() -> bool:
        """Return True only on a Linux host positively identified as Raspberry Pi."""
        if not sys.platform.startswith("linux"):
            return False
        for model_path in (Path("/proc/device-tree/model"), Path("/sys/firmware/devicetree/base/model")):
            try:
                if "Raspberry Pi" in model_path.read_text(encoding="utf-8", errors="ignore"):
                    return True
            except OSError:
                continue
        return False

    def _create_physical_control_interface(self, actions):
        """Select GPIO controls on Raspberry Pi; serial remote everywhere else.

        The GPIO module is deliberately imported only inside the Raspberry-Pi
        branch, so Windows never imports or initializes RPi.GPIO code.
        """
        if self._is_raspberry_pi():
            from controls.physical_controls import LocalControlItf

            return LocalControlItf(actions, initial_intensity=self.intensity_percent // 10)
        return SerialRemote(actions)

    def start(self) -> None:
        selected_sim = self.config_store.get("selected_sim", "nolimits2")
        if self.host_service_enabled:
            self.host_service.set_startup_sim(selected_sim)
            self.host_service.start()
        self.load_sim(selected_sim, initial=True)
        self.remote.start()
        self._last_data_tick_started_at = None
        self.data_timer.start()

    def shutdown(self) -> None:
        self.data_timer.stop()
        self.service_timer.stop()
        self.remote.stop()
        if self.host_service_enabled:
            self.host_service.send_fan_power(0.0, 0)
        self.host_service.stop()
        self.digital_twin.close()
        if self.sim:
            self.sim.disconnect()

    def load_sim(self, key: str, initial: bool = False) -> bool:
        if not initial and (
            self.platform.state is not PlatformState.DEACTIVATED
            or not self.platform.parked
        ):
            raise RuntimeError("Simulator can only be changed while platform is deactivated")

        if not initial and self.host_service_enabled:
            if not self.host_service.select_sim(key):
                self.message.emit(self.host_service.last_error or "Simulator host switch request failed")
                return False

        if self.sim:
            self.service_timer.stop()
            self.sim.disconnect()

        # All simulators run on the PC hosting Sim Host Service, so SHS is the
        # single source of the simulator host address.
        self.sim = create_sim(key, self.host_service.address)
        # Optional simulator capability: SpaceCoaster uses the shared SHS client
        # for Windows-side mouse workarounds. Other simulators need no hook.
        set_host_service = getattr(self.sim, "set_host_service", None)
        if callable(set_host_service):
            set_host_service(self.host_service)
        self.sim_key = key
        telemetry_path = Path(__file__).resolve().parent.parent / "sims" / key / "telemetry_config.ini"
        self.telemetry_config = TelemetryConfig(telemetry_path)
        self._angular_washout_ride = None
        self._motion_transform = (0.0,) * 6
        self._last_fan_power = None
        self._last_fan_send_time = -1e9
        self._estop_latched = False
        self._estop_recovery_active = False
        self._dispatch_unpark_pending = False
        self._ride_dispatched = False
        self._ride_has_run = False
        self._estop_running_pause_sent = False
        self._set_experience_state(ExperienceState.DISCONNECTED)
        # NoLimits2 TCP connect() may block the Qt thread when SHS is offline.
        # Defer the initial attempt until a live SHS heartbeat is received.
        # With SHS disabled, preserve standalone/direct connection behaviour.
        if not (key == "nolimits2" and self.host_service_enabled
                and not self.host_service.connected):
            self.sim.connect()

        period = int(getattr(self.sim, "service_period_ms", 0) or 0)
        if period > 0:
            self.service_timer.start(period)
        else:
            self.service_timer.stop()

        experiences = list(self.sim.get_experiences())
        self.experiencesChanged.emit(experiences, 0)
        self.simChanged.emit(key)
        self.simStatusChanged.emit(self.get_sim_status())
        self.config_store.set("selected_sim", key)
        return True

    def simulator_hot_switch_supported(self, key: str) -> bool:
        # Requirement: if a sim cannot hot-switch, persist selection and request restart.
        candidate = create_sim(key, self.host_service.address)
        return bool(candidate.hot_switch)

    def persist_sim_for_restart(self, key: str) -> None:
        definition_for(key)  # validate
        self.config_store.set("selected_sim", key)

    def request_switch_activate(self) -> None:
        """Handle one debounced physical OFF-to-ON selector transition."""
        if self.activation_control_mode is not ActivationControlMode.SWITCH:
            self._activation_request_result = "physical ACTIVATE ignored—Touch UI mode"
            log.info(self._activation_request_result)
            return

        if not self._activation_switch_armed:
            self._activation_request_result = "rejected—physical switch OFF cycle required"
            self.message.emit("Return Activate switch to OFF, then ON")
            log.warning(self._activation_request_result)
            return

        # Each OFF-to-ON transition authorises one attempt only. A rejected
        # attempt must never be queued for automatic motion after a transition.
        self._activation_switch_armed = False
        if (self.platform.state is not PlatformState.DEACTIVATED
                or not self.platform.parked):
            self._activation_request_result = (
                f"rejected—platform was {self.platform.state.name}; OFF cycle required"
            )
            self.message.emit(
                "Wait until platform is inactive, then cycle Activate switch OFF and ON"
            )
            log.warning(self._activation_request_result)
            return

        self._begin_activation("physical switch")

    def request_switch_deactivate(self) -> None:
        """Handle physical selector OFF, arming its next deliberate ON edge."""
        if self.activation_control_mode is not ActivationControlMode.SWITCH:
            self._activation_request_result = "physical DEACTIVATE ignored—Touch UI mode"
            log.info(self._activation_request_result)
            return
        self._activation_switch_armed = True
        self._activation_request_result = "physical switch OFF—activation armed"
        self.activateSwitchWarningChanged.emit(False)
        self._begin_deactivation()

    def request_touch_activation_toggle(self) -> None:
        """Toggle activation from the UI only while Touch UI owns control."""
        if self.activation_control_mode is not ActivationControlMode.TOUCH:
            self._activation_request_result = "touch request ignored—Switch mode"
            self.message.emit("Activation is controlled by the physical switch")
            return
        if self.platform.state is PlatformState.DEACTIVATED:
            if not self.platform.parked:
                self.message.emit("Wait until parking is complete before activating")
                return
            self._begin_activation("Touch UI")
        elif self.platform.state is PlatformState.ACTIVATED:
            self._activation_request_result = "Touch UI deactivation accepted"
            self._begin_deactivation()
        else:
            self._activation_request_result = (
                f"touch request ignored—platform is {self.platform.state.name}"
            )
            self.message.emit("Wait for the platform transition to complete")

    def set_activation_control_mode(self, mode: ActivationControlMode) -> bool:
        """Select the sole live activation source without causing activation."""
        mode = ActivationControlMode(mode)
        if not self.platform.parked:
            self.message.emit("Wait for the platform to park before changing activation control")
            self.activationControlModeChanged.emit(self.activation_control_mode)
            return False
        if mode is self.activation_control_mode:
            return True

        self.activation_control_mode = mode
        if mode is ActivationControlMode.TOUCH:
            self._activation_request_result = "Touch UI control selected; physical switch ignored"
            self.activateSwitchWarningChanged.emit(False)
        else:
            level = getattr(self.remote, "activation_switch_level", None)
            self._activation_switch_armed = level == 1
            if level == 1:
                self._activation_request_result = "Switch control selected; switch OFF—activation armed"
                # Safe-direction reconciliation: an OFF selector may lower an
                # active platform, but an ON selector never raises one.
                self._begin_deactivation()
            else:
                self._activation_request_result = (
                    "Switch control selected; physical switch OFF cycle required"
                )
        log.info("Activation control mode -> %s", mode.name)
        self.activationControlModeChanged.emit(mode)
        self.simStatusChanged.emit(self.get_sim_status())
        return True

    def _begin_activation(self, source: str) -> None:
        if POC_UI_DEBUG:
            log.debug(
                "Activate request: platform=%s sim=%s estop_latched=%s",
                self.platform.state.name,
                self.experience_state.name,
                self._estop_latched,
            )
        # PlatformState and simulator state are independent. Physical
        # activation starts immediately; simulator recovery must never hold or
        # block the platform activation sequence.
        if self.platform.state is not PlatformState.DEACTIVATED:
            self._activation_request_result = (
                f"{source} activation rejected—platform is {self.platform.state.name}"
            )
            self.message.emit("Platform cannot activate from its current state")
            return

        try:
            accepted = self.platform.request_activate()
        except Exception as exc:
            self._activation_request_result = f"{source} activation failed—{exc}"
            log.exception("Platform activation failed")
            self.message.emit(f"Platform activation failed: {exc}")
            return
        if not accepted:
            self._activation_request_result = f"{source} activation rejected"
            self.message.emit("Platform cannot activate from its current state")
            return
        self._activation_request_result = f"{source} activation accepted—parked"

        # Recovery is simulator-specific. A verified READY station needs no
        # reloading for NL2/X-Plane; SpaceCoaster uses its lobby reset after an
        # interrupted ride, because its native state can otherwise be stale.
        try:
            ready = bool(self.sim.get_status().ready_to_start)
            if self._estop_latched:
                if ready and self.sim_key != "spacecoaster":
                    self._estop_latched = False
                    self._estop_recovery_active = False
                    log.info("E-STOP cleared: simulator already confirmed ready")
                else:
                    self.sim.recover_from_estop()
                    self._estop_recovery_active = True
            elif not ready:
                self.sim.prepare_for_activation()
            self._set_experience_state(self.sim.state)
        except Exception as exc:
            # Do not clear E-STOP on a failed simulator recovery.
            log.exception("Simulator preparation failed during activation")
            self.message.emit(f"Simulator preparation failed during activation: {exc}")

    def _activate_switch_startup_invalid(self) -> None:
        """Require an OFF edge before accepting physical activation after startup."""
        self._activation_switch_armed = False
        self._activation_request_result = "startup switch ON—physical switch OFF cycle required"
        log.warning("Physical Activate switch is ON at startup; waiting for OFF")
        self.activateSwitchWarningChanged.emit(True)

    def _begin_deactivation(self) -> None:
        # Deactivation at a confirmed station with the platform ALREADY parked
        # only disarms dispatch. Anywhere else (including mid-unpark) it aborts
        # the ride, pauses if RUNNING, and parks using the hardware-specific path.
        status = self.get_sim_status()
        idle_at_station = bool(
            status.ready_to_start
            and self.platform.parked
            and self.platform.state in (PlatformState.ACTIVATED, PlatformState.DEACTIVATED)
            and not self._dispatch_unpark_pending
        )
        was_dispatching = self._dispatch_unpark_pending or self._ride_dispatched
        self._dispatch_unpark_pending = False
        self._ride_dispatched = False
        self._ride_has_run = False
        # Revoke auxiliary-train dispatch permission immediately, including
        # during an asynchronous pause/E-stop transition. Other simulator
        # adapters either implement only pending-dispatch cancellation or no-op.
        cancel = getattr(self.sim, "cancel_pending_dispatch", None)
        if callable(cancel):
            cancel()
        if not idle_at_station and self.platform.state is PlatformState.ACTIVATED:
            self._estop_latched = True
            self._estop_recovery_active = False
            self._estop_running_pause_sent = False
            if self.experience_state is ExperienceState.RUNNING:
                try:
                    self.sim.pause()  # Never toggle PAUSED back to RUNNING.
                    self._estop_running_pause_sent = True
                except Exception as exc:
                    log.exception("Simulator pause failed during controlled abort")
                    self.message.emit(f"Simulator pause failed: {exc}")
        try:
            self.platform.request_deactivate()
        except Exception as exc:
            log.exception("Platform deactivation/parking failed")
            self.message.emit(f"Platform parking failed: {exc}")

    def _start_transform(self):
        get_start = getattr(self.sim, "starting_transform", None)
        if not callable(get_start):
            return None
        raw = get_start(self._last_sample)
        if raw is None or len(raw) != 6:
            return None
        try:
            if not all(math.isfinite(float(x)) for x in raw):
                return None
        except (TypeError, ValueError):
            return None
        # This is a target pose, not a new live motion sample: never run the
        # washout filter when preparing physical unparking.
        if self.telemetry_config is not None:
            scaled = self.telemetry_config.scale_without_washout(raw, self.intensity_percent)
        else:
            scaled = tuple(float(x) * self.intensity_percent / 100.0 for x in raw)
        return scaled if all(math.isfinite(float(x)) for x in scaled) else None

    @staticmethod
    def _sim_data_ready_for_dispatch(sim, status: SimStatus) -> bool:
        # SpaceCoaster's native READY has no live telemetry pacing; its
        # recorded profile supplies the starting transform instead.
        return bool(status.data_connected or (
            getattr(sim, "supports_recorded_start", False)
            and status.ready_to_start
        ))

    @property
    def ready_for_dispatch(self) -> bool:
        status = self.get_sim_status()
        return bool(
            self.platform.state is PlatformState.ACTIVATED
            and self.platform.parked
            and not self._dispatch_unpark_pending
            and not self._estop_latched
            and not self._estop_recovery_active
            and status.pc_connected and status.sim_connected
            and self._sim_data_ready_for_dispatch(self.sim, status)
            and status.ready_to_start
        )

    def request_run(self) -> None:
        if not self.ready_for_dispatch:
            self.message.emit("Platform must be activated, parked, and simulator ready for dispatch")
            return
        target = self._start_transform()
        if target is None:
            self.message.emit("No valid simulator starting transform; dispatch cancelled")
            return
        try:
            if not self.platform.request_unpark(target):
                self.message.emit("Unable to initiate unparking")
                return
        except Exception as exc:
            log.exception("Unable to unpark for dispatch")
            self.message.emit(f"Dispatch cancelled: {exc}")
            return
        self._dispatch_unpark_pending = True
        self._estop_running_pause_sent = False
        self._ride_has_run = False
        self._ride_dispatched = False

    def _advance_pending_dispatch(self) -> None:
        if not self._dispatch_unpark_pending:
            return
        if self.platform.parking_state is PlatformMotionState.UNPARKING:
            status = self.get_sim_status()
            if not (status.pc_connected and status.sim_connected):
                self._dispatch_unpark_pending = False
                self._estop_latched = True
                self.message.emit("Simulator connection lost during unpark; parking platform")
                self.platform.request_deactivate()
            return
        self._dispatch_unpark_pending = False
        if (self.platform.state is not PlatformState.ACTIVATED
                or self.platform.parking_state is not PlatformMotionState.UNPARKED
                or self._estop_latched):
            return
        status = self.get_sim_status()
        if not (status.pc_connected and status.sim_connected
                and self._sim_data_ready_for_dispatch(self.sim, status)
                and status.ready_to_start):
            self.message.emit("Simulator no longer ready; returning platform to park")
            self.platform.request_park()
            return
        try:
            self.sim.run()
            self._ride_dispatched = True
            self._set_experience_state(self.sim.state)
        except Exception as exc:
            log.exception("Simulator dispatch failed after unparking")
            self.message.emit(f"Simulator dispatch failed: {exc}; parking platform")
            self.platform.request_park()

    def _handle_normal_completion(self, sample: SimSample) -> None:
        if not self._ride_dispatched or self._estop_latched:
            return
        if sample.state is ExperienceState.RUNNING:
            self._ride_has_run = True
        completed = False
        if self.sim_key == "spacecoaster":
            completed = bool(self.sim.consume_normal_completion())
        elif self.sim_key == "nolimits2":
            completed = bool(
                self._ride_has_run
                and sample.state is ExperienceState.READY
                and self.get_sim_status().ready_to_start
            )
        # X-Plane has no ride-end event; it remains unparked until Deactivate.
        if not completed:
            return
        self._ride_dispatched = False
        self._ride_has_run = False
        try:
            self.platform.request_park()  # Keep logical ACTIVATED.
        except Exception as exc:
            log.exception("Automatic end-of-ride parking failed")
            self.message.emit(f"Automatic parking failed: {exc}")
            self.platform._set_state(PlatformState.FAULT)

    def request_pause(self) -> None:
        if (self.platform.state is not PlatformState.ACTIVATED
                or self.platform.parking_state is not PlatformMotionState.UNPARKED
                or self._estop_latched):
            self.message.emit("Pause unavailable while parked, parking, or deactivated")
            return
        status = self.get_sim_status()
        if not (status.pc_connected and status.sim_connected and status.data_connected):
            self.message.emit("Pause unavailable until simulator PC, simulator, and sim data are connected")
            return
        if self.experience_state in (ExperienceState.RUNNING, ExperienceState.PAUSED):
            self.sim.pause()

    def request_reset_vr(self) -> None:
        self.sim.reset_vr()

    def request_remote_intensity(self, value: int) -> None:
        """Apply the physical remote's 0..10 intensity value as 0..100%."""
        try:
            value = int(value)
        except (TypeError, ValueError):
            self.message.emit(f"Invalid remote intensity: {value}")
            return
        if not 0 <= value <= 10:
            self.message.emit(f"Remote intensity out of range: {value}")
            return
        self.request_intensity_percent(value * 10)

    def request_intensity_percent(self, percent: int) -> None:
        try:
            percent = int(percent)
        except (TypeError, ValueError):
            self.message.emit(f"Invalid intensity: {percent}")
            return
        if not 0 <= percent <= 100:
            self.message.emit(f"Intensity out of range: {percent}")
            return
        self.intensity_percent = percent
        self.intensity_level = percent // 10
        log.info("Intensity -> %d%%", self.intensity_percent)
        self.intensityChanged.emit(self.intensity_percent)

    def request_intensity_level(self, level: int) -> None:
        """Compatibility helper for legacy Static/Mild/Full requests."""
        percents = {0: 0, 1: 40, 2: 100}
        try:
            level = int(level)
        except (TypeError, ValueError):
            self.message.emit(f"Invalid intensity level: {level}")
            return
        if level not in percents:
            self.message.emit(f"Invalid intensity level: {level}")
            return
        self.request_intensity_percent(percents[level])

    def request_encoder_rotate(self, direction: int) -> None:
        """Forward one physical encoder detent to the Qt UI on the main thread."""
        try:
            direction = int(direction)
        except (TypeError, ValueError):
            return
        if direction == 0:
            return
        self.encoderRotated.emit(1 if direction > 0 else -1)

    def request_encoder_press(self) -> None:
        """Forward the physical encoder push switch to the Qt UI."""
        self.encoderPressed.emit()

    def request_dispatch_press(self) -> None:
        """Dispatch normally, or arm physical simulator browse when inactive."""
        if self.platform.state is PlatformState.DEACTIVATED:
            self.simulatorBrowseChanged.emit(True)
            return
        self.request_run()

    def request_dispatch_release(self) -> None:
        """End/cancel a physical simulator browse hold."""
        self.simulatorBrowseChanged.emit(False)

    def request_payload_level(self, level: int) -> None:
        try:
            level = int(level)
        except (TypeError, ValueError):
            self.message.emit(f"Invalid payload level: {level}")
            return
        if level not in (0, 1, 2):
            self.message.emit(f"Invalid payload level: {level}")
            return
        setter = getattr(self.platform.backend, "set_payload_level", None)
        if callable(setter):
            setter(level)
        self.payload_level = level
        log.info("Payload level -> %d", level)
        self.payloadChanged.emit(self.payload_level)

    def set_nl2_heave_mode(self, mode: str) -> bool:
        """Choose one NL2 heave source while deactivated; session-only."""
        if (self.sim_key != "nolimits2" or self.telemetry_config is None
                or self.platform.state is not PlatformState.DEACTIVATED):
            self.message.emit("Deactivate the platform before changing NL2 heave mode")
            return False
        setter = getattr(self.sim, "set_heave_mode", None)
        if not callable(setter):
            return False
        try:
            setter(mode)
        except ValueError as exc:
            self.message.emit(str(exc))
            return False
        # Vertical G alone uses the temporary classical high-pass Z filter.
        # Normalized and Legacy Height preserve sustained lift movement.
        from sims.nolimits2 import config as nl2_config
        self.telemetry_config.set_temporary_classical_z(
            mode == nl2_config.HEAVE_MODE_VERTICAL_G
        )
        self._motion_transform = (0.0,) * 6
        log.info("NL2 heave mode -> %s", mode)
        return True

    def set_nl2_legacy_heave(self, enabled: bool) -> None:
        """Compatibility with the former two-mode checkbox interface."""
        from sims.nolimits2 import config as nl2_config
        self.set_nl2_heave_mode(
            nl2_config.HEAVE_MODE_LEGACY_HEIGHT if enabled
            else nl2_config.HEAVE_MODE_VERTICAL_G
        )

    def get_output_snapshot(self) -> dict:
        backend = getattr(self.platform, "backend", None)
        cfg = getattr(backend, "cfg", None)
        lengths = tuple(float(v) for v in getattr(backend, "muscle_lengths", (0.0,) * 6))
        pressures = tuple(float(v) for v in getattr(backend, "sent_pressures", (0.0,) * 6))
        max_length = float(getattr(cfg, "MUSCLE_MAX_LENGTH", 1000.0)) if cfg is not None else 1000.0
        range_length = float(getattr(cfg, "MUSCLE_LENGTH_RANGE", 250.0)) if cfg is not None else 250.0
        contractions = tuple(
            max(0.0, min(100.0, (max_length - v) * 100.0 / max(1.0, range_length)))
            for v in lengths
        )
        dtop = getattr(backend, "dto_p", None)
        selected_load = getattr(dtop, "selected_load", 0.0)
        muscle_output = getattr(backend, "muscle_output", None)
        piston_disengaged = None
        if bool(getattr(backend, "has_piston", False)):
            piston_disengaged = bool(
                getattr(muscle_output, "activate_piston_flag", 0)
            )
        return {
            "festo_ip": getattr(backend, "festo_ip", ""),
            "muscle_lengths": lengths,
            "pressures": pressures,
            "contractions": contractions,
            "payload_level": self.payload_level,
            "dtop_load_kg_per_muscle": float(selected_load or 0.0),
            "piston_disengaged": piston_disengaged,
        }

    def set_digital_twin_enabled(self, enabled: bool) -> bool:
        """Session-only output switch; the configured destination is never edited."""
        if not enabled:
            self.digital_twin.close()
            return False
        if self.digital_twin.enabled:
            return True
        cfg = self.config_store.get("digital_twin", {})
        self.digital_twin = DigitalTwinSender(
            address=cfg.get("address", "-"), port=cfg.get("port", 10020)
        )
        return self.digital_twin.enabled

    def _send_digital_twin_snapshot(self, sample: SimSample, transform_applied: bool) -> None:
        """Send one complete best-effort snapshot; never affect control flow."""
        if not self.digital_twin.enabled:
            return
        try:
            output = self.get_output_snapshot()
            self.digital_twin.send_snapshot(
                platform=getattr(self, "platform_name", self.platform_key),
                platform_state=self.platform.state.name,
                parking_state=getattr(getattr(self.platform, "parking_state", None), "name", "UNKNOWN"),
                applied_transform_real=getattr(
                    getattr(self.platform, "backend", None), "last_applied_real_transform", None
                ),
                simulator_state=self.experience_state.name,
                telemetry_valid=sample.telemetry_valid,
                transform_applied=transform_applied,
                dtop_load_kg_per_muscle=output["dtop_load_kg_per_muscle"],
                transform=self._motion_transform,
                muscle_length_mm=output["muscle_lengths"],
                contraction_pct=output["contractions"],
                command_pressure_mbar=output["pressures"],
                piston_disengaged=output["piston_disengaged"],
            )
        except Exception:
            # Digital-twin diagnostics are never allowed to interrupt the
            # platform's 50 ms control cycle.
            log.exception("Unable to prepare digital twin UDP snapshot")

    def _sync_nl2_angular_washout(self) -> None:
        """Confirm variants before adopting gains/washout/heave filters.

        Nothing is written to the simulator-wide telemetry INI. Queued or
        failed park loads cannot leak their experimental motion settings.
        """
        if self.sim_key != "nolimits2" or self.telemetry_config is None:
            return
        from sims.nolimits2 import ride_config
        loaded = getattr(self.sim, "_loaded_index", None)
        pending = bool(getattr(self.sim, "_ride_load_pending", False))
        experiences = tuple(self.sim.get_experiences())
        if pending:
            return
        name = experiences[loaded] if isinstance(loaded, int) and 0 <= loaded < len(experiences) else None
        if name == self._angular_washout_ride:
            return
        variant = ride_config.variant(name) if name is not None else None
        roll = variant["roll_washout"] if variant else None
        pitch = variant["pitch_washout"] if variant else None
        gains = ([variant["gain_scale_pct"][axis] for axis in ride_config.AXES]
                 if variant is not None else [100] * 6)
        self.telemetry_config.set_variant_gain_scales(gains)
        self.telemetry_config.set_angular_washout_overrides(roll, pitch, reset=True)
        self.telemetry_config.set_temporary_classical_z(
            bool(variant and self.sim.heave_mode == "vertical_g"))
        self._angular_washout_ride = name
        self._motion_transform = (0.0,) * 6
        effective = self.telemetry_config.effective_washout()
        log.info("NL2 variant active: %s cue=%s gains=%s washout=%s Roll=%s Pitch=%s",
                 name or "unconfirmed", variant["angular_cue"] if variant else "euler",
                 gains, effective["type"], effective["roll_source"], effective["pitch_source"])

    def get_telemetry_config(self) -> dict:
        if self.telemetry_config is None:
            return {}
        return self.telemetry_config.snapshot()

    def apply_telemetry_config(self, values: dict) -> None:
        if self.telemetry_config is None:
            return
        self.telemetry_config.apply_values(values)

    def save_telemetry_config(self, values: dict) -> None:
        if self.telemetry_config is None:
            return
        self.telemetry_config.apply_values(values)
        self.telemetry_config.save()
        self.message.emit(f"Telemetry settings saved for {self.sim.name}")

    def cancel_telemetry_config(self) -> dict:
        if self.telemetry_config is None:
            return {}
        values = self.telemetry_config.load()
        self.message.emit(f"Telemetry settings restored for {self.sim.name}")
        return values

    def select_experience(self, index: int) -> bool:
        if self.platform.state is not PlatformState.DEACTIVATED or not self.platform.parked:
            self.message.emit("Deactivate and park the platform before changing ride/flight")
            return False
        previous_heave_mode = getattr(self.sim, "heave_mode", None)
        try:
            self.sim.select_experience(index)
        except Exception as exc:
            log.exception("Unable to select ride/flight")
            self.message.emit(str(exc))
            return False
        # Same-park variant changes are confirmed immediately by the NL2 adapter.
        # Synchronise gains/washout now, before another activation can occur.
        # Asynchronous park loads remain pending; _sync rejects them until ACK.
        self._sync_nl2_angular_washout()
        if (previous_heave_mode is not None
                and getattr(self.sim, "heave_mode", None) != previous_heave_mode
                and not getattr(self.sim, "_ride_load_pending", False)):
            self.message.emit(f"NL2 heave mode: {self.sim.heave_mode}")
        experiences = list(self.sim.get_experiences())
        self.experiencesChanged.emit(experiences, index)
        self._set_experience_state(self.sim.state)
        self.simStatusChanged.emit(self.get_sim_status())
        return True

    def _data_tick(self) -> None:
        """Time the complete main callback, including early returns/errors."""
        tick_start = time.perf_counter()
        period = self.DATA_PERIOD_MS / 1000.0
        if self._last_data_tick_started_at is not None:
            actual_interval = tick_start - self._last_data_tick_started_at
            self.jitter_percent = round(100.0 * abs(actual_interval - period) / period)
        self._last_data_tick_started_at = tick_start
        try:
            self._data_tick_body()
        finally:
            elapsed = time.perf_counter() - tick_start
            self.processing_percent = round(100.0 * elapsed / period)
            self._processing_history.append(elapsed)
            self.processing_max_percent = round(100.0 * max(self._processing_history) / period)

    def _data_tick_body(self) -> None:
        # Serial callbacks are intentionally serviced here so physical and UI
        # controls enter PocCore on the same Qt/main thread.
        self.remote.service()
        if self.host_service_enabled:
            self.host_service.service()
        try:
            self.platform.service(self.DATA_PERIOD_MS / 1000.0)
        except Exception as exc:
            self._dispatch_unpark_pending = False
            self.message.emit(f"Platform transition fault: {exc}")
        self.platformActivationChanged.emit(self.platform.activation_percent)
        sample = self.sim.read()
        self._last_sample = sample

        # Simulator-native state remains authoritative even during an E-STOP.
        # E-STOP is an orthogonal POC latch, not an ExperienceState.
        self._set_experience_state(sample.state)
        self._handle_normal_completion(sample)
        self._advance_pending_dispatch()

        # Safety catch for a late RUNNING/PAUSED event while the platform is no
        # longer fully active. This does not overwrite the native simulator
        # state; it only establishes the POC-owned E-STOP condition.
        unsafe_sim_motion = (
            sample.state in (ExperienceState.RUNNING, ExperienceState.PAUSED)
            and (self.platform.state is not PlatformState.ACTIVATED
                 or self.platform.parking_state is not PlatformMotionState.UNPARKED)
        )
        if unsafe_sim_motion:
            if not self._estop_latched:
                self._estop_latched = True
                self._estop_recovery_active = False
                self._estop_running_pause_sent = False
                self._dispatch_unpark_pending = False
                if self.platform.state is PlatformState.ACTIVATED:
                    # Unexpected simulator movement before/unrelated to
                    # confirmed unpark must not continue the unpark sequence.
                    try:
                        self.platform.request_deactivate()
                    except Exception as exc:
                        self.message.emit(f"Emergency parking failed: {exc}")
            if (sample.state is ExperienceState.RUNNING
                    and not self._estop_running_pause_sent):
                try:
                    self.sim.pause()
                    self._estop_running_pause_sent = True
                except Exception as exc:
                    log.exception("Simulator pause failed after unsafe RUNNING state")
                    self.message.emit(f"Simulator pause failed after unsafe RUNNING state: {exc}")

        # An E-STOP clears only after core has explicitly requested recovery and
        # the simulator subsequently reports its own ready-to-start condition.
        if self._estop_latched and self._estop_recovery_active:
            if (self.get_sim_status().ready_to_start
                    and self.platform.state is PlatformState.ACTIVATED
                    and self.platform.parked):
                self._estop_latched = False
                self._estop_recovery_active = False
                log.info("E-STOP recovery complete; simulator is ready to start")

        # The selected ride may change via an asynchronous LOAD_PARK result.
        # Sync once per confirmed change before any motion filtering occurs.
        self._sync_nl2_angular_washout()

        # RESETTING may legitimately have no valid telemetry. Motion is only
        # accepted while RUNNING and telemetry is explicitly valid.
        if sample.telemetry_valid and self.telemetry_config is not None:
            if bool(getattr(self.sim, "supports_washout", True)):
                self._motion_transform = self.telemetry_config.process(
                    sample.transform,
                    self.intensity_percent,
                    self.DATA_PERIOD_MS / 1000.0,
                )
            else:
                # SpaceCoaster's recorded CSV is already the final motion
                # profile. Apply gains/intensity only; never wash it out.
                self._motion_transform = self.telemetry_config.scale_without_washout(
                    sample.transform, self.intensity_percent
                )
        else:
            self._motion_transform = (0.0,) * 6

        transform_applied = (
            self.platform.state is PlatformState.ACTIVATED
            and self.platform.parking_state is PlatformMotionState.UNPARKED
            and not self._estop_latched
            and self.experience_state is ExperienceState.RUNNING
            and sample.telemetry_valid
        )
        if transform_applied:
            self.platform.move(self._motion_transform)

        self._update_fan(sample)
        self._send_digital_twin_snapshot(sample, transform_applied)

        self.sampleUpdated.emit(sample)
        self.simStatusChanged.emit(self.get_sim_status())

    @staticmethod
    def _calculate_fan_power(speed: float | None, fan_config: dict) -> int:
        if speed is None:
            return 0
        speed = max(0.0, float(speed))
        threshold_speed = float(fan_config.get("threshold_speed", 0.0))
        if speed <= threshold_speed:
            return 0

        table = tuple(fan_config.get("speed_power_table", ()))
        if len(table) < 2:
            return 0
        speed_at_100 = float(fan_config.get("speed_at_100", table[-1][0]))
        speed = min(speed, speed_at_100)

        shaped_power = float(table[-1][1])
        if speed <= float(table[0][0]):
            shaped_power = float(table[0][1])
        else:
            for (speed_0, power_0), (speed_1, power_1) in zip(table, table[1:]):
                speed_0 = float(speed_0)
                speed_1 = float(speed_1)
                if speed <= speed_1:
                    fraction = (speed - speed_0) / (speed_1 - speed_0)
                    shaped_power = float(power_0) + fraction * (float(power_1) - float(power_0))
                    break

        threshold_power = float(fan_config.get("threshold_power", 0.0))
        max_power = float(fan_config.get("max_power", 100.0))
        output = threshold_power + shaped_power * (max_power - threshold_power) / 100.0
        # The legacy Python 2 controller formatted the float with %d, which
        # truncated rather than rounded the final percentage.
        return max(0, min(100, int(output)))

    def _update_fan(self, sample: SimSample) -> None:
        if not self.host_service_enabled:
            return
        speed = max(0.0, float(sample.speed)) if sample.speed is not None else 0.0
        enabled = (
            self.platform.state is PlatformState.ACTIVATED
            and sample.state is ExperienceState.RUNNING
            and sample.telemetry_valid
        )
        fan_config = getattr(self.sim, "fan_config", {}) or {}
        power = self._calculate_fan_power(speed, fan_config) if enabled else 0
        now = time.monotonic()
        if power != self._last_fan_power or now - self._last_fan_send_time >= self.FAN_SEND_PERIOD_S:
            if self.host_service.send_fan_power(speed, power):
                self._last_fan_power = power
                self._last_fan_send_time = now

    def get_sim_status(self) -> SimStatus:
        """Return simulator facts plus one centrally composed operator message.

        Simulator adapters still own sim/data connection facts and retain their
        original ``text`` for diagnostics. SHS remains authoritative for PC
        presence. Only PocCore decides what is displayed to the operator.
        """
        status = self.sim.get_status() if self.sim else SimStatus()
        if RAW_SIM_STATUS_DEBUG:
            log.debug("Raw simulator status text: %s", status.text)
        if self.host_service_enabled:
            status = replace(status, pc_connected=bool(self.host_service.connected))
        activation_instruction = ""
        if (
            self.activation_control_mode is ActivationControlMode.SWITCH
            and not self._activation_switch_armed
            and self.platform.state is PlatformState.DEACTIVATED
        ):
            activation_instruction = "Return Activate switch to OFF, then ON"
        operator_text = compose_operator_status_text(
            status,
            self.platform.state,
            self.experience_state,
            estop_recovery_active=self._estop_recovery_active,
            activation_instruction=activation_instruction,
            parking_state=self.platform.parking_state,
        )
        return replace(status, text=operator_text)

    @property
    def estop_latched(self) -> bool:
        return bool(self._estop_latched)

    def get_debug_snapshot(self) -> dict:
        """Compact runtime diagnostics; omit values already visible in normal UI."""
        sample = self._last_sample
        backend = getattr(self.platform, "backend", None)
        sim_client = getattr(self.sim, "client", None)
        loaded_variant = None
        if self.sim_key == "nolimits2" and not getattr(self.sim, "_ride_load_pending", False):
            loaded_index = getattr(self.sim, "_loaded_index", None)
            experiences = tuple(self.sim.get_experiences())
            if isinstance(loaded_index, int) and 0 <= loaded_index < len(experiences):
                loaded_variant = experiences[loaded_index]
        return {
            "processing_percent": self.processing_percent,
            "jitter_percent": self.jitter_percent,
            "processing_max_percent": self.processing_max_percent,
            "platform_state": self.platform.state.name,
            "parking_state": self.platform.parking_state.name,
            "pending_dispatch": self._dispatch_unpark_pending,
            "ride_dispatched": self._ride_dispatched,
            "platform_transition_error": self.platform.last_transition_error,
            "platform_backend_enabled": getattr(backend, "enabled", None),
            "festo_ip": getattr(backend, "festo_ip", ""),
            "length_clip_count": getattr(backend, "length_clip_count", 0),
            "last_length_clip": getattr(backend, "last_length_clip", None),
            "experience_state": self.experience_state.name,
            "sim_native_state": getattr(getattr(self.sim, "state", None), "name", str(getattr(self.sim, "state", ""))),
            "nl2_variant": loaded_variant,
            "nl2_angular_cue": (getattr(self.sim, "_angular_cue_mode", "euler")
                                if loaded_variant is not None else None),
            "nl2_heave_mode": (getattr(self.sim, "heave_mode", None)
                              if loaded_variant is not None else None),
            "nl2_gain_scales_pct": (self.telemetry_config.variant_gain_scales
                                    if self.sim_key == "nolimits2" and
                                    self.telemetry_config is not None else None),
            "estop_latched": self._estop_latched,
            "estop_recovery_active": self._estop_recovery_active,
            "ready_to_start": self.get_sim_status().ready_to_start,
            # Adapter-specific recovery predicates are diagnostics only.  They
            # deliberately do not participate in core state decisions.
            "sim_estop_recovery_pending": getattr(self.sim, "_estop_recovery_pending", None),
            "sim_manual_mode_requested": getattr(self.sim, "_manual_mode_requested", None),
            "sim_station_status": getattr(sim_client, "station_status", None),
            "telemetry_valid": sample.telemetry_valid,
            "transform": tuple(float(v) for v in sample.transform),
            "motion_transform": tuple(float(v) for v in self._motion_transform),
            "sim_connect_attempts": getattr(getattr(self.sim, "client", None), "connect_attempts", 0),
            "sim_last_error": getattr(getattr(self.sim, "client", None), "last_error", ""),
            "remote_connected": self.remote.is_connected,
            "activation_control_mode": self.activation_control_mode.name,
            "activation_interlock": (
                "ARMED" if self._activation_switch_armed else "OFF_REQUIRED"
            ),
            "activation_request_result": self._activation_request_result,
            "activation_switch": getattr(
                self.remote, "activation_switch_debug", "unavailable"
            ),
            "remote_last_event": self.remote.last_event,
            "remote_last_error": self.remote.last_error,
            "shs_connected": self.host_service.connected if self.host_service_enabled else False,
            "shs_last_pong_age": self.host_service.last_pong_age if self.host_service_enabled else None,
            "shs_last_ack": self.host_service.last_ack if self.host_service_enabled else "",
            "shs_last_error": self.host_service.last_error if self.host_service_enabled else "",
        }

    def _service_tick(self) -> None:
        # Gate only NEW NoLimits2 TCP attempts: an already-connected simulator
        # must continue being serviced even if SHS temporarily goes offline.
        # SHS.service() runs independently in _data_tick_body() at 20 Hz.
        if (self.sim_key == "nolimits2" and self.host_service_enabled
                and not self.host_service.connected
                and not self.sim.client.is_connected):
            return
        self.sim.service()

    def _set_experience_state(self, state: ExperienceState) -> None:
        if state is self.experience_state:
            return
        self.experience_state = state
        self.experienceStateChanged.emit(state)

    def _on_platform_state(self, state: PlatformState) -> None:
        self.platformStateChanged.emit(state)
        self.simStatusChanged.emit(self.get_sim_status())
