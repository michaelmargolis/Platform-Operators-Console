#types.py  ver 8
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Sequence


class PlatformState(Enum):
    DEACTIVATED = auto()
    ACTIVATING = auto()
    ACTIVATED = auto()
    DEACTIVATING = auto()
    FAULT = auto()


class PlatformMotionState(Enum):
    # This is a commanded/timed state, NOT confirmation of physical support.
    PARKED = auto()
    UNPARKING = auto()
    UNPARKED = auto()
    PARKING = auto()


class ActivationControlMode(Enum):
    SWITCH = auto()
    TOUCH = auto()


class ExperienceState(Enum):
    DISCONNECTED = auto()
    READY = auto()
    RUNNING = auto()
    PAUSED = auto()
    RESETTING = auto()


@dataclass(frozen=True)
class SimStatus:
    """Generic simulator connection and operator status.

    The three connection flags are deliberately independent:
      pc_connected   - simulator PC/support host is reachable;
      sim_connected  - simulator control/API link is responding;
      data_connected - live simulator motion data is available.

    RESETTING can therefore legitimately report True, True, False.

    ready_to_start is simulator-defined and means the experience is in its
    safe operator waiting condition: e.g. an NL2 train stopped in station,
    SpaceCoaster native READY, or a freshly loaded X-Plane situation.
    """

    pc_connected: bool = False
    sim_connected: bool = False
    data_connected: bool = False
    text: str = "Not connected"
    entity_name: str = ""
    busy: bool = False
    ready_to_start: bool = False
    activity: str = ""


def compose_operator_status_text(
    status: SimStatus,
    platform_state: PlatformState,
    experience_state: ExperienceState,
    estop_recovery_active: bool = False,
    activation_instruction: str = "",
    parking_state: PlatformMotionState | None = None,
) -> str:
    """Compose the single operator-facing status message from state facts."""
    # Physical platform transitions are the operator's immediate action and
    # remain authoritative even if the simulator PC or simulator is offline.
    if platform_state is PlatformState.ACTIVATING:
        return "Activating platform"
    if platform_state is PlatformState.DEACTIVATING:
        return "Deactivating platform"

    if parking_state is PlatformMotionState.PARKING:
        return "Parking platform"
    if parking_state is PlatformMotionState.UNPARKING:
        return "Unparking platform"

    if activation_instruction:
        return activation_instruction

    if not status.pc_connected:
        return "Waiting for connection to simulator PC"
    if not status.sim_connected:
        return "Waiting for Sim"

    # Operations take priority over data availability because motion data can
    # legitimately disappear while a ride is loading or resetting.
    if status.activity == "loading":
        return "Loading…"
    if status.activity == "resetting" or status.busy:
        return "Resetting…"
    if estop_recovery_active:
        return "Resetting…"

    if not status.data_connected and not status.ready_to_start:
        return "Sim connected, waiting for data"

    if platform_state is not PlatformState.ACTIVATED:
        return "Waiting for activation"

    if status.ready_to_start and (parking_state is None or parking_state is PlatformMotionState.PARKED):
        return "Ready for dispatch"
    if experience_state is ExperienceState.RUNNING:
        return "Running"
    if experience_state is ExperienceState.PAUSED:
        return "Paused"
    if parking_state is PlatformMotionState.PARKED:
        return "Parked — waiting for Sim"

    return "Waiting for Sim"


@dataclass(frozen=True)
class SimSample:
    transform: Sequence[float] = field(default_factory=lambda: (0.0,) * 6)
    state: ExperienceState = ExperienceState.DISCONNECTED
    telemetry_valid: bool = False
    speed: float | None = None

    def __post_init__(self):
        if len(self.transform) != 6:
            raise ValueError("transform must contain six values: x, y, z, roll, pitch, yaw")


def should_latch_estop_on_deactivate(experience_state: ExperienceState) -> bool:
    """True only when deactivation interrupts an active simulator operation."""
    return experience_state in (
        ExperienceState.RUNNING,
        ExperienceState.PAUSED,
        ExperienceState.RESETTING,
    )
