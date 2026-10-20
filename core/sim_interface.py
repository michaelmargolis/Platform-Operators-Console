#sim_interface.py  ver 6
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from core.types import SimSample, SimStatus


class SimInterface(ABC):
    """Small simulator contract used by POC.

    RESETTING is intentionally a weakly-connected state: a simulator may stop
    producing telemetry and may ignore normal control requests while reset is
    in progress. POC must not use motion telemetry until READY is reported.
    """

    name = "Simulator"
    run_label = "Run"
    pause_label = "Pause"
    experience_noun = "ride"
    service_period_ms = 0
    hot_switch = True
    supports_washout = True

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def read(self) -> SimSample: ...

    def service(self) -> None:
        pass

    @abstractmethod
    def run(self) -> None: ...

    @abstractmethod
    def pause(self) -> None: ...

    def prepare_for_activation(self) -> None:
        """Normal activation-time readiness check/preparation.

        This is used only when POC has no E-STOP recovery latched.  The
        physical platform state machine remains independent and does not wait
        for simulator preparation to complete.
        """
        pass

    def recover_from_estop(self) -> None:
        """Return an interrupted experience to its safe ready-to-start state.

        POC owns the E-STOP latch and calls this only on activation after an
        E-STOP.  Simulator adapters own the recovery mechanism (reload/reset
        park, return to lobby, reload situation, etc.).
        """
        self.prepare_for_activation()

    def starting_transform(self, sample: SimSample):
        """Current live normalized transform; None if not safely available.

        Recorded simulators may override to return their first recorded frame.
        This is the raw simulator pose BEFORE POC intensity/gain scaling.
        """
        if sample.telemetry_valid and sample.state.name == "READY":
            return tuple(float(v) for v in sample.transform)
        return None

    def cancel_pending_dispatch(self) -> None:
        """Optional cancellation before the simulator actually begins its run."""
        pass

    @abstractmethod
    def reset_vr(self) -> None: ...

    def get_experiences(self) -> Sequence[str]:
        return ("Default",)

    def select_experience(self, index: int) -> None:
        if index != 0:
            raise IndexError(index)

    @abstractmethod
    def get_status(self) -> SimStatus: ...
