#platform_controller.py  ver 19
from __future__ import annotations

import importlib
import logging
import math
from typing import Callable, Sequence

from core.platform_geometry import clearance_settings, configure_workspace
from core.ride_neutral import validated_ride_neutral_delta, real_heave_for_physical_height
from core.types import PlatformState, PlatformMotionState

log = logging.getLogger(__name__)


class SopPlatformBackend:
    """Physical output path using the same kinematics -> DtoP -> MuscleOutput calls as SOP.

    SOP's runtime path is:
        normalized transform -> Kinematics.norm_to_real()/muscle_lengths()
        -> MuscleOutput.set_muscle_lengths()
        -> DistanceToPressure.muscle_length_to_pressure()
        -> Festo.send_pressures()

    This backend deliberately reuses those modules rather than reimplementing them.
    """

    def __init__(
        self, config_module: str, festo_ip: str = "192.168.0.10",
        festo_interface=None,
    ):
        from kinematics.kinematics_V3 import Kinematics, PlatformParams
        from output.d_to_p import DistanceToPressure
        from output.muscle_output import MuscleOutput

        cfg_module = importlib.import_module(config_module)
        self.cfg = cfg_module.PlatformConfig()
        platform_coords = getattr(self.cfg, "platform_coords", None)
        if platform_coords is None:
            # Temporary compatibility with older external configs. New POC
            # configs use full XYZ platform coordinates.
            platform_coords = getattr(self.cfg, "platform_coords_xy", None)
        if not hasattr(self.cfg, "base_coords") or platform_coords is None:
            raise RuntimeError(
                f"{config_module} does not expose base_coords and platform_coords geometry"
            )

        self.k = Kinematics()
        # Only platforms explicitly specifying an axis convention override
        # the legacy default. The MDX CAD uses ROS rider-frame positive Roll.
        axis_flip_mask = getattr(self.cfg, "AXIS_FLIP_MASK", None)
        if axis_flip_mask is not None:
            self.k.set_axis_flip_mask(axis_flip_mask)
        params = PlatformParams(
            self.cfg.MUSCLE_MIN_LENGTH,
            self.cfg.MUSCLE_MAX_LENGTH,
            self.cfg.FIXED_HARDWARE_LENGTH,
            self.cfg.LIMITS_1DOF_TRANFORM,
        )
        clearance_offset, reserve = clearance_settings(self.cfg)
        self.k.set_geometry(
            self.cfg.base_coords,
            platform_coords,
            params,
            clearance_offset, reserve,
            neutral_height_offset_mm=getattr(self.cfg, "NEUTRAL_HEIGHT_OFFSET_MM", 0.0),
        )

        configure_workspace(self)

        self.dto_p = DistanceToPressure(
            self.cfg.MUSCLE_LENGTH_RANGE + 1,
            self.cfg.MUSCLE_MAX_LENGTH,
        )
        if not self.dto_p.load_data(self.cfg.MUSCLE_PRESSURE_MAPPING_FILE):
            raise RuntimeError(
                f"Unable to load muscle pressure mapping {self.cfg.MUSCLE_PRESSURE_MAPPING_FILE}"
            )

        payload_weights = [
            (w + self.cfg.UNLOADED_PLATFORM_WEIGHT) / 6.0
            for w in self.cfg.PAYLOAD_WEIGHTS
        ]
        self.dto_p.set_load(payload_weights[1])

        output_kwargs = {"has_piston": bool(getattr(self.cfg, "HAS_PISTON", False))}
        if festo_interface is not None:
            # Only Pose Sender passes an injected sink. Keep the existing
            # production constructor/recording-test call shape unchanged.
            output_kwargs["festo_interface"] = festo_interface
        self.muscle_output = MuscleOutput(
            self.dto_p.muscle_length_to_pressure,
            lambda seconds: None,
            festo_ip,
            self.cfg.MUSCLE_MAX_LENGTH,
            self.cfg.MUSCLE_LENGTH_RANGE,
            **output_kwargs,
        )
        self.last_transform = [0.0] * 6
        self.muscle_lengths = list(self.cfg.DEACTIVATED_MUSCLE_LENGTHS)
        self.payload_level = 1
        self.enabled = False
        self._transition_start = None
        self._transition_end = None
        self._transition_delta = None
        self._transition_step = 0
        self._transition_steps = 0
        self._sequence_phase = None
        self._wait_steps_remaining = 0
        self._pending_transition_end = None
        self.length_clip_count = 0
        self.last_length_clip = None

        self.has_piston = bool(getattr(self.cfg, "HAS_PISTON", False))
        self.piston_move_seconds = max(
            0.0, float(getattr(self.cfg, "PISTON_MOVE_SECONDS", 0.5))
        )

    @property
    def sent_pressures(self):
        return tuple(self.muscle_output.sent_pressures)

    @property
    def festo_ip(self):
        return self.muscle_output.get_festo_ip()

    def set_payload_level(self, level: int) -> None:
        level = max(0, min(2, int(level)))
        payload_weights = [
            (w + self.cfg.UNLOADED_PLATFORM_WEIGHT) / 6.0
            for w in self.cfg.PAYLOAD_WEIGHTS
        ]
        self.payload_level = level
        self.dto_p.set_load(payload_weights[level])
        if self.muscle_lengths:
            self._apply_lengths(self.muscle_lengths)

    def _apply_lengths(self, muscle_lengths) -> None:
        self.muscle_lengths = list(muscle_lengths)
        self.muscle_output.set_muscle_lengths(self.muscle_lengths)

    def _floor_clearance_for_real_transform(self, transform: Sequence[float]):
        """Return calculated underside-to-floor clearance in mm.

        The floor outline uses the same established rotation convention as the
        current inverse kinematics: neutral Z is embedded in each point before
        roll/pitch are applied. Yaw does not affect the resulting Z coordinate.
        """
        if not self._workspace_hull_xy:
            return None

        values = [float(value) for value in transform]
        translation_z = values[2] * float(self.k.FLIP_TRANSLATION[2])
        rpy = [
            values[index + 3] * float(self.k.FLIP_ROTATION[index])
            for index in range(3)
        ]
        rotation = self.k.calc_rotation(rpy)
        underside_z = self.k.PLATFORM_MID_HEIGHT + self._workspace_underside_z
        lowest_z = min(
            rotation[2, 0] * x
            + rotation[2, 1] * y
            + rotation[2, 2] * underside_z
            + translation_z
            for x, y in self._workspace_hull_xy
        )
        return float(lowest_z - self._workspace_floor_z)

    def _apply_floor_limit(self, transform: Sequence[float]):
        candidate = [float(value) for value in transform]
        clearance = self._floor_clearance_for_real_transform(candidate)
        if clearance is None:
            return candidate, False

        required = self._workspace_min_floor_clearance + self._workspace_hull_margin
        if clearance >= required:
            return candidate, False

        z_flip = float(self.k.FLIP_TRANSLATION[2])
        if abs(z_flip) < 1.0e-9:
            raise RuntimeError("Heave axis flip cannot be zero when floor limiting is enabled.")
        candidate[2] += (required - clearance) / z_flip
        return candidate, True

    def _actuator_lengths_for_real_transform(self, transform: Sequence[float]):
        _pose, actuator_lengths = self.k.inverse_kinematics(
            transform, return_lengths=True
        )
        return actuator_lengths

    def _actuator_bounds_ok(self, actuator_lengths, max_actuator_length):
        minimum = min(float(value) for value in actuator_lengths)
        maximum = max(float(value) for value in actuator_lengths)
        return (
            minimum >= float(self.cfg.MIN_ACTUATOR_LENGTH)
            and maximum <= float(max_actuator_length)
        )

    def _ride_neutral_real_transform(self):
        """Feasible motion anchor, expressed in IK input coordinates."""
        neutral = [0.0] * 6
        neutral[2] = real_heave_for_physical_height(
            getattr(self, "ride_neutral_height_delta_mm", 0.0),
            self.k.FLIP_TRANSLATION[2],
        )
        return neutral

    def real_with_ride_neutral_offset(self, transform: Sequence[float]):
        """Apply the ride's PHYSICAL vertical shift after gains/washout.

        The caller supplies IK real-transform coordinates; this adjustment is
        not used by fixed real-pose parking and prop-clearance operations.
        """
        requested = [float(value) for value in transform]
        requested[2] += self._ride_neutral_real_transform()[2]
        return requested

    def set_ride_neutral_height_delta_mm(self, delta_mm):
        """Validate neutral pose without moving hardware or modifying CAD.

        An infeasible *neutral* offset is rejected; extreme dynamic frames
        continue to use the configured workspace limiter or explicit clipping
        diagnostics. Do not silently shift an impossible neutral to a floor
        or muscle boundary.
        """
        delta = validated_ride_neutral_delta(delta_mm)
        if delta != 0.0:
            anchor = [0.0] * 6
            anchor[2] = real_heave_for_physical_height(delta, self.k.FLIP_TRANSLATION[2])
            # IK caches drive the digital twin. Checking a candidate offset is
            # not a physical move and must not leave a fictitious pose cached.
            old_pose = getattr(self.k, "cached_pose", None)
            try:
                actuator = self._actuator_lengths_for_real_transform(anchor)
            finally:
                if old_pose is not None:
                    self.k.cached_pose = old_pose
            maximum = float(self.cfg.MAX_ACTUATOR_LENGTH)
            if self.workspace_limit_mode == "reserve":
                maximum = float(getattr(self.cfg, "MAX_ACTIVE_ACTUATOR_LENGTH", maximum))
            if not self._actuator_bounds_ok(actuator, maximum):
                raise ValueError(
                    f"Ride neutral offset {delta:+g} mm is outside "
                    "the platform's allowable neutral muscle lengths")
            floor = self._floor_clearance_for_real_transform(anchor)
            if floor is not None and floor < (self._workspace_min_floor_clearance
                                                + self._workspace_hull_margin - 1e-7):
                raise ValueError(
                    f"Ride neutral offset {delta:+g} mm violates the minimum floor clearance")
        self.ride_neutral_height_delta_mm = delta

    def _limit_workspace(self, transform: Sequence[float]):
        requested = [float(value) for value in transform]
        self.workspace_limited = False
        self.workspace_scale = 1.0
        self.workspace_limit_reason = None
        self.workspace_floor_clearance = None
        self.workspace_actuator_min = None
        self.workspace_actuator_max = None

        if self.workspace_limit_mode == "off":
            return requested, None

        max_actuator_length = float(self.cfg.MAX_ACTUATOR_LENGTH)
        if self.workspace_limit_mode == "reserve":
            max_actuator_length = float(
                getattr(
                    self.cfg,
                    "MAX_ACTIVE_ACTUATOR_LENGTH",
                    self.cfg.MAX_ACTUATOR_LENGTH,
                )
            )

        candidate = requested
        floor_limited = False
        if self.workspace_limit_mode == "geometry":
            candidate, floor_limited = self._apply_floor_limit(candidate)

        actuator_lengths = self._actuator_lengths_for_real_transform(candidate)
        if self._actuator_bounds_ok(actuator_lengths, max_actuator_length):
            self.workspace_limited = floor_limited
            self.workspace_limit_reason = "FLOOR" if floor_limited else None
            self.workspace_floor_clearance = (
                self._floor_clearance_for_real_transform(candidate)
                if self.workspace_limit_mode == "geometry"
                else None
            )
            self.workspace_actuator_min = min(float(v) for v in actuator_lengths)
            self.workspace_actuator_max = max(float(v) for v in actuator_lengths)
            if floor_limited:
                self._record_workspace_limit()
            return candidate, actuator_lengths

        full_min = min(float(v) for v in actuator_lengths)
        full_max = max(float(v) for v in actuator_lengths)
        reasons = []
        if floor_limited:
            reasons.append("FLOOR")
        if full_min < float(self.cfg.MIN_ACTUATOR_LENGTH):
            reasons.append("SHORT")
        if full_max > max_actuator_length:
            reasons.append("LONG")

        # Only impossible poses pay the cost of iteration. Six bisection steps
        # give about 1.6% scale resolution and keep runtime cost bounded. Each
        # geometry-mode trial gets the direct heave floor correction first.
        low = 0.0
        high = 1.0
        anchor = self._ride_neutral_real_transform()
        best = list(anchor)
        if self.workspace_limit_mode == "geometry":
            best, _ = self._apply_floor_limit(best)
        best_lengths = self._actuator_lengths_for_real_transform(best)
        if not self._actuator_bounds_ok(best_lengths, max_actuator_length):
            raise RuntimeError("Neutral platform pose is outside configured actuator limits.")

        best_floor_limited = False
        for _ in range(self.workspace_limit_iterations):
            scale = (low + high) / 2.0
            trial = [center + scale * (value - center)
                     for center, value in zip(anchor, requested)]
            trial_floor_limited = False
            if self.workspace_limit_mode == "geometry":
                trial, trial_floor_limited = self._apply_floor_limit(trial)
            trial_lengths = self._actuator_lengths_for_real_transform(trial)
            if self._actuator_bounds_ok(trial_lengths, max_actuator_length):
                low = scale
                best = trial
                best_lengths = trial_lengths
                best_floor_limited = trial_floor_limited
            else:
                high = scale

        # Re-evaluate the selected result so cached kinematic pose matches the
        # lengths that will actually be sent.
        best_lengths = self._actuator_lengths_for_real_transform(best)
        self.workspace_limited = True
        self.workspace_scale = low
        if best_floor_limited and "FLOOR" not in reasons:
            reasons.insert(0, "FLOOR")
        self.workspace_limit_reason = "+".join(reasons) if reasons else "ACTUATOR"
        self.workspace_floor_clearance = (
            self._floor_clearance_for_real_transform(best)
            if self.workspace_limit_mode == "geometry"
            else None
        )
        self.workspace_actuator_min = min(float(v) for v in best_lengths)
        self.workspace_actuator_max = max(float(v) for v in best_lengths)
        self._record_workspace_limit()
        return best, best_lengths

    def _record_workspace_limit(self):
        self.workspace_limit_count += 1
        if self.workspace_limit_count == 1 or self.workspace_limit_count % 100 == 0:
            log.warning(
                "Workspace limit: mode=%s reason=%s scale=%.3f "
                "floor_mm=%s actuator_mm=%.1f..%.1f count=%d",
                self.workspace_limit_mode,
                self.workspace_limit_reason,
                self.workspace_scale,
                (
                    "n/a"
                    if self.workspace_floor_clearance is None
                    else f"{self.workspace_floor_clearance:.1f}"
                ),
                self.workspace_actuator_min
                if self.workspace_actuator_min is not None
                else float("nan"),
                self.workspace_actuator_max
                if self.workspace_actuator_max is not None
                else float("nan"),
                self.workspace_limit_count,
            )

    def _lengths_for_transform(self, transform: Sequence[float]):
        real_transform = self.real_with_ride_neutral_offset(self.k.norm_to_real(transform))
        applied_transform, actuator_lengths = self._limit_workspace(real_transform)
        self.last_applied_real_transform = list(applied_transform)
        return self._calculate_muscle_lengths(
            applied_transform, actuator_lengths=actuator_lengths
        )

    def _lengths_for_real_transform(self, transform: Sequence[float]):
        """Return muscle lengths for a transform already expressed in mm/radians."""
        return self._calculate_muscle_lengths(transform)

    def _calculate_muscle_lengths(
        self, transform: Sequence[float], actuator_lengths=None
    ):
        if actuator_lengths is None:
            _pose, actuator_lengths = self.k.inverse_kinematics(
                transform, return_lengths=True
            )
        raw_lengths = tuple(
            float(length) - float(self.cfg.FIXED_HARDWARE_LENGTH)
            for length in actuator_lengths
        )
        clipped = tuple(
            index
            for index, length in enumerate(raw_lengths)
            if length < self.cfg.MUSCLE_MIN_LENGTH
            or length > self.cfg.MUSCLE_MAX_LENGTH
        )
        self.last_length_clip = clipped or None
        if clipped:
            self.length_clip_count += 1
            if self.length_clip_count == 1 or self.length_clip_count % 100 == 0:
                log.warning(
                    "Kinematics muscle-length clipping: muscles=%s raw_mm=%s count=%d",
                    ",".join(str(index) for index in clipped),
                    ",".join(f"{length:.1f}" for length in raw_lengths),
                    self.length_clip_count,
                )
        lengths = list(self.k.muscle_lengths_from_lengths(actuator_lengths))
        self.k.cached_muscle_lengths = tuple(lengths)
        return lengths

    def _set_piston_disengaged(self, disengaged: bool) -> None:
        """Set the fail-safe prop actuator and transmit it with current pressures.

        Flag 1 extends the piston and disengages the prop. Flag 0 removes the
        command so the prop engages, including when control power is lost.
        """
        if not self.has_piston:
            return
        self.muscle_output.set_piston_flag(disengaged)
        self._apply_lengths(self.muscle_lengths)

    def _start_piston_wait(self, next_lengths) -> None:
        self._pending_transition_end = list(next_lengths)
        self._wait_steps_remaining = max(
            1, int(math.ceil(self.piston_move_seconds / 0.05))
        )

    def _start_transition(self, start_lengths, end_lengths) -> None:
        # Same transition rate and 50 ms stepping used by SOP start_transition().
        self._transition_start = list(start_lengths)
        self._transition_end = list(end_lengths)
        max_dist = max(abs(e - s) for s, e in zip(self._transition_start, self._transition_end))
        self._transition_steps = max(1, int(max_dist / (50 * 0.05)))
        self._transition_delta = [
            (e - s) / self._transition_steps
            for s, e in zip(self._transition_start, self._transition_end)
        ]
        self._transition_step = 0

    def begin_activate(self, target_transform: Sequence[float]) -> None:
        target_lengths = self._lengths_for_transform(target_transform)
        if self.has_piston:
            self._set_piston_disengaged(True)
            self._sequence_phase = "RELEASING_PROP"
            self._start_piston_wait(target_lengths)
        else:
            self._sequence_phase = "ACTIVATING"
            self._start_transition(self.cfg.DEACTIVATED_MUSCLE_LENGTHS, target_lengths)

    def begin_deactivate(self) -> None:
        final_lengths = list(self.cfg.DEACTIVATED_MUSCLE_LENGTHS)
        if not self.has_piston:
            self._sequence_phase = "LOWERING"
            self._start_transition(self.muscle_lengths, final_lengths)
            return

        contraction = float(
            getattr(
                self.cfg,
                "PROPPING_MUSCLE_CONTRACTION_MM",
                self.cfg.MUSCLE_MAX_LENGTH * 0.08,
            )
        )
        propping_length = self.cfg.MUSCLE_MAX_LENGTH - contraction
        needs_clearance = (
            self.muscle_lengths[0] > propping_length
            or self.muscle_lengths[5] > propping_length
        )

        if needs_clearance:
            clearance_transform = [
                0.0,
                0.0,
                float(getattr(self.cfg, "PROP_CLEARANCE_Z_MM", 0.0)),
                0.0,
                0.0,
                0.0,
            ]
            clearance_lengths = self._lengths_for_real_transform(clearance_transform)
            self._pending_transition_end = final_lengths
            self._sequence_phase = "CLEARING_PROP"
            self._start_transition(self.muscle_lengths, clearance_lengths)
        else:
            self._set_piston_disengaged(False)
            self._sequence_phase = "ENGAGING_PROP"
            self._start_piston_wait(final_lengths)

    def _service_length_transition(self) -> bool:
        """Service one 50 ms length-transition step."""
        if self._transition_start is None:
            return True
        if self._transition_step >= self._transition_steps:
            self._apply_lengths(self._transition_end)
            self._transition_start = None
            return True

        self.muscle_lengths = [
            s + self._transition_step * d
            for s, d in zip(self._transition_start, self._transition_delta)
        ]
        self._apply_lengths(self.muscle_lengths)
        self._transition_step += 1
        return False

    def service_transition(self) -> bool:
        if self._sequence_phase in ("RELEASING_PROP", "ENGAGING_PROP"):
            # EasyIP output is UDP and the piston has no feedback. Repeat the
            # fail-safe flag throughout its movement interval rather than
            # relying on the single packet sent when the phase began.
            self._apply_lengths(self.muscle_lengths)
            self._wait_steps_remaining -= 1
            if self._wait_steps_remaining <= 0:
                next_lengths = self._pending_transition_end
                self._pending_transition_end = None
                self._sequence_phase = (
                    "ACTIVATING"
                    if self._sequence_phase == "RELEASING_PROP"
                    else "LOWERING"
                )
                self._start_transition(self.muscle_lengths, next_lengths)
            return False

        if self._sequence_phase == "CLEARING_PROP":
            if self._service_length_transition():
                final_lengths = self._pending_transition_end
                self._set_piston_disengaged(False)
                self._sequence_phase = "ENGAGING_PROP"
                self._start_piston_wait(final_lengths)
            return False

        complete = self._service_length_transition()
        if complete:
            self._sequence_phase = None
        return complete

    @property
    def transition_progress(self) -> float:
        # Piston travel and clearance motion happen before the normal length
        # transition. Keep the operator activation indication at its starting
        # end until the activation/deactivation ramp itself begins.
        if self._sequence_phase in (
            "RELEASING_PROP",
            "CLEARING_PROP",
            "ENGAGING_PROP",
        ):
            return 0.0
        if self._transition_start is None:
            return 1.0
        if self._transition_steps <= 0:
            return 1.0
        # service_transition() applies the current step before incrementing
        # _transition_step. Report the step that was actually sent to the
        # platform, rather than leading the physical motion by one 50 ms tick.
        applied_step = max(0, self._transition_step - 1)
        return max(0.0, min(1.0, applied_step / self._transition_steps))

    def enable(self) -> None:
        self.enabled = True

    def disable(self) -> None:
        self.enabled = False

    def move(self, transform: Sequence[float]) -> None:
        self.last_transform = list(transform)
        muscle_lengths = self._lengths_for_transform(transform)
        if not all(x == y for x, y in zip(muscle_lengths, self.muscle_lengths)):
            self._apply_lengths(muscle_lengths)


class PlatformController:
    """Logical operator activation, independently of physical parking motion.

    Startup assumes the platform is already parked, as in v148. There is no
    position/prop feedback: PARKED means a completed command, not verified pose.
    """

    def __init__(self, backend, transition_seconds: float = 1.0):
        self.backend = backend
        self.state = PlatformState.DEACTIVATED
        self.parking_state = PlatformMotionState.PARKED
        self.transition_seconds = max(0.05, float(transition_seconds))
        self._transition_elapsed = 0.0
        self._state_callback: Callable[[PlatformState], None] | None = None
        self.last_transition_error = None

    def set_state_callback(self, callback: Callable[[PlatformState], None]) -> None:
        self._state_callback = callback

    def _set_state(self, state: PlatformState) -> None:
        if state == self.state:
            return
        self.state = state
        log.info("Platform state -> %s", state.name)
        if self._state_callback:
            self._state_callback(state)

    def _set_parking_state(self, state: PlatformMotionState) -> None:
        if state != self.parking_state:
            log.info("Platform parking -> %s", state.name)
            self.parking_state = state
            if self._state_callback:
                self._state_callback(self.state)

    def request_activate(self, target_transform: Sequence[float] | None = None) -> bool:
        """Arm only; initial physical position is assumed PARKED."""
        if (self.state is not PlatformState.DEACTIVATED
                or self.parking_state is not PlatformMotionState.PARKED):
            return False
        self.backend.enable()
        self.last_transition_error = None
        self._set_state(PlatformState.ACTIVATED)
        return True

    def request_unpark(self, target_transform: Sequence[float]) -> bool:
        if (self.state is not PlatformState.ACTIVATED
                or self.parking_state is not PlatformMotionState.PARKED):
            return False
        if len(target_transform) != 6:
            raise ValueError("Starting transform must contain six values")
        self._transition_elapsed = 0.0
        try:
            self.backend.begin_activate(target_transform)
        except Exception as exc:
            # A partially transmitted command is not proof of a parked prop.
            self.last_transition_error = str(exc)
            self._set_state(PlatformState.FAULT)
            raise
        self._set_parking_state(PlatformMotionState.UNPARKING)
        return True

    def request_park(self) -> bool:
        """Park without changing logical activation; may interrupt UNPARKING."""
        if self.parking_state is PlatformMotionState.PARKED:
            return True
        if self.parking_state is PlatformMotionState.PARKING:
            return True
        self._transition_elapsed = 0.0
        try:
            self.backend.begin_deactivate()
        except Exception as exc:
            self.last_transition_error = str(exc)
            self._set_state(PlatformState.FAULT)
            raise
        self._set_parking_state(PlatformMotionState.PARKING)
        return True

    def request_deactivate(self) -> bool:
        if self.state is PlatformState.DEACTIVATED:
            return False
        if self.state is PlatformState.FAULT:
            return False
        # Cancel the ride before initiating physical park; a fresh activation
        # cannot bypass an unfinished PARKING transition.
        try:
            self.request_park()
        except Exception as exc:
            self.last_transition_error = str(exc)
            self._set_state(PlatformState.FAULT)
            raise
        self._set_state(PlatformState.DEACTIVATED)
        if self.parking_state is PlatformMotionState.PARKED:
            self.backend.disable()
        return True

    def service(self, dt: float) -> None:
        if self.state is PlatformState.FAULT:
            return
        if self.parking_state not in (
            PlatformMotionState.UNPARKING, PlatformMotionState.PARKING
        ):
            return
        try:
            service_transition = getattr(self.backend, "service_transition", None)
            if callable(service_transition):
                complete = bool(service_transition())
            else:
                self._transition_elapsed += dt
                complete = self._transition_elapsed >= self.transition_seconds
            if complete:
                if self.parking_state is PlatformMotionState.UNPARKING:
                    self._set_parking_state(PlatformMotionState.UNPARKED)
                else:
                    self._set_parking_state(PlatformMotionState.PARKED)
                    if self.state is PlatformState.DEACTIVATED:
                        self.backend.disable()
        except Exception as exc:
            self.last_transition_error = str(exc)
            log.exception("Platform %s failed", self.parking_state.name)
            self._set_state(PlatformState.FAULT)
            # Remain in fault state; do not infer a parked position.
            raise

    @property
    def activation_percent(self) -> int:
        # The Activate button reports the LOGICAL arm state, not a physical ramp.
        return 100 if self.state is PlatformState.ACTIVATED else 0

    @property
    def parked(self) -> bool:
        return self.parking_state is PlatformMotionState.PARKED

    def move(self, transform: Sequence[float]) -> None:
        if (self.state is PlatformState.ACTIVATED
                and self.parking_state is PlatformMotionState.UNPARKED):
            self.backend.move(transform)
