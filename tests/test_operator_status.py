#test_operator_status.py  ver 4
from core.types import (
    ExperienceState,
    PlatformState,
    SimStatus,
    compose_operator_status_text,
    should_latch_estop_on_deactivate,
)


def text(
    status,
    platform=PlatformState.DEACTIVATED,
    experience=ExperienceState.READY,
    activation_instruction="",
):
    return compose_operator_status_text(
        status,
        platform,
        experience,
        activation_instruction=activation_instruction,
    )


def test_operator_status_priority_table():
    assert text(SimStatus()) == "Waiting for connection to simulator PC"
    assert text(SimStatus(pc_connected=True)) == "Waiting for Sim"
    assert text(SimStatus(pc_connected=True, sim_connected=True)) == "Sim connected, waiting for data"

    connected = dict(pc_connected=True, sim_connected=True, data_connected=True)
    assert text(SimStatus(**connected, busy=True, activity="loading")) == "Loading…"
    assert text(SimStatus(**connected, busy=True, activity="resetting")) == "Resetting…"
    assert text(SimStatus(**connected, ready_to_start=True)) == "Waiting for activation"
    assert text(
        SimStatus(**connected, ready_to_start=True),
        PlatformState.ACTIVATING,
    ) == "Activating platform"
    assert text(
        SimStatus(**connected, ready_to_start=True),
        PlatformState.ACTIVATED,
    ) == "Ready for dispatch"
    assert text(
        SimStatus(**connected),
        PlatformState.ACTIVATED,
        ExperienceState.RUNNING,
    ) == "Running"
    assert text(
        SimStatus(**connected),
        PlatformState.ACTIVATED,
        ExperienceState.PAUSED,
    ) == "Paused"


def test_deactivated_platform_never_reports_ready_for_dispatch():
    status = SimStatus(
        pc_connected=True,
        sim_connected=True,
        data_connected=True,
        ready_to_start=True,
        text="Ready for dispatch",
    )
    assert text(status, PlatformState.DEACTIVATED) == "Waiting for activation"
    assert text(status, PlatformState.DEACTIVATING) == "Deactivating platform"


def test_platform_transition_messages_override_sim_activity():
    connected = dict(pc_connected=True, sim_connected=True, data_connected=True)
    loading = SimStatus(**connected, busy=True, activity="loading")
    resetting = SimStatus(**connected, busy=True, activity="resetting")
    assert text(loading, PlatformState.ACTIVATING) == "Activating platform"
    assert text(resetting, PlatformState.DEACTIVATING) == "Deactivating platform"


def test_platform_transition_messages_override_disconnected_simulator():
    assert text(SimStatus(), PlatformState.ACTIVATING) == "Activating platform"
    assert text(SimStatus(), PlatformState.DEACTIVATING) == "Deactivating platform"


def test_activation_instruction_precedes_connection_waits_after_transition():
    assert text(
        SimStatus(),
        activation_instruction="Return Activate switch to OFF, then ON",
    ) == "Return Activate switch to OFF, then ON"


def test_estop_recovery_reports_resetting_not_waiting_for_sim():
    connected = SimStatus(pc_connected=True, sim_connected=True, data_connected=True)
    assert compose_operator_status_text(
        connected,
        PlatformState.ACTIVATED,
        ExperienceState.READY,
        estop_recovery_active=True,
    ) == "Resetting…"


def test_normal_ready_deactivation_does_not_latch_estop():
    assert not should_latch_estop_on_deactivate(ExperienceState.READY)
    assert not should_latch_estop_on_deactivate(ExperienceState.DISCONNECTED)
    assert should_latch_estop_on_deactivate(ExperienceState.RUNNING)
    assert should_latch_estop_on_deactivate(ExperienceState.PAUSED)
    assert should_latch_estop_on_deactivate(ExperienceState.RESETTING)
