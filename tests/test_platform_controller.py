#test_platform_controller.py  ver 12
from core.platform_controller import PlatformController
from core.types import PlatformState, PlatformMotionState
from unittest.mock import patch


class RecordingBackend:
    def __init__(self):
        self.enabled = False
        self.activate_target = None
        self.deactivate_started = False
        self.transition_calls = 0
        self.moves = []

    def begin_activate(self, transform):
        self.activate_target = list(transform)
        self.transition_calls = 0

    def begin_deactivate(self):
        self.deactivate_started = True
        self.transition_calls = 0

    def service_transition(self):
        self.transition_calls += 1
        return self.transition_calls >= 2

    def enable(self):
        self.enabled = True

    def disable(self):
        self.enabled = False

    def move(self, transform):
        self.moves.append(tuple(transform))


class RecordingMuscleOutput:
    def __init__(self, d_to_p_func, sleep_func, festo_ip, max_length, length_range, has_piston=False):
        self._d_to_p = d_to_p_func
        self._festo_ip = festo_ip
        self.sent_pressures = [0] * 6
        self.has_piston = has_piston
        self.piston_flag = 0
        self.packets = []

    def set_muscle_lengths(self, lengths):
        self.sent_pressures = list(self._d_to_p(lengths))
        self.packets.append((tuple(self.sent_pressures), self.piston_flag))

    def get_festo_ip(self):
        return self._festo_ip

    def set_piston_flag(self, state):
        self.piston_flag = 1 if state else 0


def make_sop_backend():
    from core.platform_controller import SopPlatformBackend

    with patch("output.muscle_output.MuscleOutput", RecordingMuscleOutput):
        return SopPlatformBackend(
            "kinematics.cfg_SuspendedPlatformMdx",
            festo_ip="192.0.2.1",
        )


def make_chair_backend():
    from core.platform_controller import SopPlatformBackend

    with patch("output.muscle_output.MuscleOutput", RecordingMuscleOutput):
        return SopPlatformBackend(
            "kinematics.cfg_SuspendedChair",
            festo_ip="192.0.2.1",
        )


def test_physical_backend_transition_hooks_and_motion():
    backend = RecordingBackend()
    platform = PlatformController(backend=backend)
    target = [0.1, -0.2, 0.3, 0.01, -0.02, 0.03]

    assert platform.parked  # Startup assumes parked; never commands an unpark.
    assert platform.request_activate(target)
    assert platform.state is PlatformState.ACTIVATED
    assert platform.parked
    assert backend.activate_target is None
    assert backend.enabled

    assert platform.request_unpark(target)
    assert platform.parking_state is PlatformMotionState.UNPARKING
    assert backend.activate_target == target
    platform.service(0.05)
    platform.service(0.05)
    assert platform.parking_state is PlatformMotionState.UNPARKED

    platform.move(target)
    assert backend.moves == [tuple(target)]

    assert platform.request_park()  # Normal end: stay logically active.
    assert platform.state is PlatformState.ACTIVATED
    assert platform.parking_state is PlatformMotionState.PARKING
    platform.service(0.05)
    platform.service(0.05)
    assert platform.parked
    assert backend.enabled
    assert platform.request_deactivate()  # OFF while parked: logical only.
    assert platform.state is PlatformState.DEACTIVATED
    assert not backend.enabled


def test_deactivation_cancels_unparking_and_blocks_reactivation_until_parked():
    backend = RecordingBackend()
    platform = PlatformController(backend)
    assert platform.request_activate()
    assert platform.request_unpark([0.0] * 6)
    assert platform.request_deactivate()
    assert platform.state is PlatformState.DEACTIVATED
    assert platform.parking_state is PlatformMotionState.PARKING
    assert not platform.request_activate()  # OFF/ON during transition is inert.
    assert not platform.request_unpark([0.0] * 6)
    platform.service(0.05)
    platform.service(0.05)
    assert platform.parked
    assert platform.request_activate()

def test_sop_backend_computes_and_sends_festo_output():
    backend = make_sop_backend()
    assert backend.muscle_output is not None
    assert backend.festo_ip == "192.0.2.1"
    backend.begin_activate([0.0] * 6)
    for _ in range(500):
        if backend.service_transition():
            break
    assert any(float(p) > 0 for p in backend.sent_pressures)


def test_mdx_backend_uses_latest_cad_xyz_and_reserve_neutral_height():
    backend = make_sop_backend()
    assert backend.k.base_coords.shape == (6, 3)
    assert backend.k.platform_local_coords.shape == (6, 3)
    assert backend.cfg.CAD_Z_REFERENCE_OFFSET == 84.0
    assert backend.k.BASE_MAX_Z == 1083.0
    assert list(backend.k.platform_local_coords[:, 2]) == [
        -8.0, -2.0, -3.0, -2.0, -2.0, -8.0
    ]
    assert abs(backend.k.PLATFORM_LEVEL_Z_MIN - 70.1835225) < 1.0e-5
    assert abs(backend.k.PLATFORM_LEVEL_Z_MAX - 284.3457990) < 1.0e-5
    assert abs(backend.k.PLATFORM_MID_HEIGHT - 177.2646608) < 1.0e-5
    assert backend.cfg.MUSCLE_EXTENSION_RESERVE == 60.0
    assert backend.cfg.MAX_ACTIVE_ACTUATOR_LENGTH == 1140.0


def test_mdx_geometry_limiter_raises_heave_to_protect_floor_without_fixed_reserve():
    backend = make_sop_backend()
    requested = backend.k.norm_to_real([0, 0, 1, 1, 1, 0])
    raw_clearance = backend._floor_clearance_for_real_transform(requested)
    assert raw_clearance < 0.0

    applied, actuator_lengths = backend._limit_workspace(requested)

    assert backend.workspace_limited
    assert backend.workspace_limit_reason == "FLOOR"
    assert backend.workspace_scale == 1.0
    assert backend.workspace_floor_clearance >= (
        backend.cfg.MIN_FLOOR_CLEARANCE + backend.cfg.FLOOR_HULL_APPROX_MARGIN - 1.0e-6
    )
    assert applied[3] == requested[3]
    assert applied[4] == requested[4]
    assert applied[2] < requested[2]
    assert min(actuator_lengths) >= backend.cfg.MIN_ACTUATOR_LENGTH
    assert max(actuator_lengths) <= backend.cfg.MAX_ACTUATOR_LENGTH
    # Geometry mode may safely use extension beyond the simple 60 mm reserve.
    assert max(actuator_lengths) > backend.cfg.MAX_ACTIVE_ACTUATOR_LENGTH


def test_mdx_geometry_limiter_scales_only_when_actuator_limits_require_it():
    backend = make_sop_backend()
    requested = backend.k.norm_to_real([1, 1, 1, 1, 1, 1])

    applied, actuator_lengths = backend._limit_workspace(requested)

    assert backend.workspace_limited
    assert backend.workspace_scale < 1.0
    assert "LONG" in backend.workspace_limit_reason
    assert min(actuator_lengths) >= backend.cfg.MIN_ACTUATOR_LENGTH
    assert max(actuator_lengths) <= backend.cfg.MAX_ACTUATOR_LENGTH
    assert backend.workspace_floor_clearance >= backend.cfg.MIN_FLOOR_CLEARANCE
    assert applied != list(requested)


def test_mdx_reserve_mode_is_single_config_fallback():
    backend = make_sop_backend()
    backend.workspace_limit_mode = "reserve"
    requested = backend.k.norm_to_real([1, 1, 1, 1, 1, 1])

    _applied, actuator_lengths = backend._limit_workspace(requested)

    assert backend.workspace_limited
    assert backend.workspace_scale < 1.0
    assert min(actuator_lengths) >= backend.cfg.MIN_ACTUATOR_LENGTH
    assert max(actuator_lengths) <= backend.cfg.MAX_ACTIVE_ACTUATOR_LENGTH


def test_chair_backend_uses_fractional_total_load_per_muscle():
    backend = make_chair_backend()
    expected_normal = (
        backend.cfg.PAYLOAD_WEIGHTS[1] + backend.cfg.UNLOADED_PLATFORM_WEIGHT
    ) / 6.0
    assert abs(backend.dto_p.selected_load - expected_normal) < 1.0e-9

    backend.set_payload_level(2)
    expected_full = (
        backend.cfg.PAYLOAD_WEIGHTS[2] + backend.cfg.UNLOADED_PLATFORM_WEIGHT
    ) / 6.0
    assert abs(backend.dto_p.selected_load - expected_full) < 1.0e-9


def test_activation_percent_reflects_logical_arm_not_physical_transition():
    backend = RecordingBackend()
    platform = PlatformController(backend)
    assert platform.activation_percent == 0
    assert platform.request_activate()
    assert platform.activation_percent == 100
    assert platform.request_unpark([0.0] * 6)
    assert platform.activation_percent == 100
    assert platform.request_deactivate()
    assert platform.activation_percent == 0
    assert platform.parking_state is PlatformMotionState.PARKING


def test_piston_platform_releases_prop_only_on_dispatch_unpark():
    backend = make_chair_backend()
    platform = PlatformController(backend=backend)

    assert platform.request_activate()
    assert backend.muscle_output.piston_flag == 0
    assert platform.request_unpark([0.0] * 6)
    assert backend.muscle_output.piston_flag == 1
    assert backend._sequence_phase == "RELEASING_PROP"

    for _ in range(9):
        platform.service(0.05)
        assert backend._sequence_phase == "RELEASING_PROP"
    platform.service(0.05)
    assert backend._sequence_phase == "ACTIVATING"
    release_packets = [packet for packet in backend.muscle_output.packets if packet[1] == 1]
    assert len(release_packets) >= 10

def test_piston_chair_returns_to_prop_supported_park_after_unpark():
    backend = make_chair_backend()
    platform = PlatformController(backend)
    assert platform.request_activate()
    assert platform.request_unpark([0.0] * 6)
    for _ in range(450):
        platform.service(0.05)
        if platform.parking_state is PlatformMotionState.UNPARKED:
            break
    assert platform.parking_state is PlatformMotionState.UNPARKED
    assert backend.muscle_output.piston_flag == 1
    assert platform.request_park()
    for _ in range(500):
        platform.service(0.05)
        if platform.parked:
            break
    assert platform.parked
    assert platform.state is PlatformState.ACTIVATED
    assert backend.muscle_output.piston_flag == 0
    assert list(backend.muscle_lengths) == list(backend.cfg.DEACTIVATED_MUSCLE_LENGTHS)


def test_piston_platform_repeats_engage_command_during_wait():
    backend = make_chair_backend()
    threshold = (
        backend.cfg.MUSCLE_MAX_LENGTH
        - backend.cfg.PROPPING_MUSCLE_CONTRACTION_MM
    )
    backend.muscle_lengths = [threshold] * 6

    backend.begin_deactivate()
    initial_packets = len(backend.muscle_output.packets)
    for _ in range(5):
        backend.service_transition()

    assert len(backend.muscle_output.packets) == initial_packets + 5
    assert all(flag == 0 for _pressures, flag in backend.muscle_output.packets[-5:])


def test_piston_platform_clears_low_front_before_engaging_prop():
    backend = make_chair_backend()
    backend.muscle_lengths = [backend.cfg.MUSCLE_MAX_LENGTH] * 6
    backend._lengths_for_real_transform = lambda transform: [700.0] * 6

    backend.begin_deactivate()
    assert backend._sequence_phase == "CLEARING_PROP"
    assert backend.muscle_output.piston_flag == 0

    for _ in range(200):
        backend.service_transition()
        if backend._sequence_phase == "ENGAGING_PROP":
            break

    assert backend._sequence_phase == "ENGAGING_PROP"
    assert backend.muscle_output.piston_flag == 0


def test_piston_platform_skips_clearance_when_both_front_muscles_are_short_enough():
    backend = make_chair_backend()
    threshold = (
        backend.cfg.MUSCLE_MAX_LENGTH
        - backend.cfg.PROPPING_MUSCLE_CONTRACTION_MM
    )
    backend.muscle_lengths = [threshold] * 6

    backend.begin_deactivate()

    assert backend._sequence_phase == "ENGAGING_PROP"
    assert backend.muscle_output.piston_flag == 0


def test_muscle_output_appends_piston_flag_only_for_piston_platforms():
    from output.muscle_output import MuscleOutput

    class RecordingFesto:
        def __init__(self, address):
            self.address = address
            self.packets = []

        def send_pressures(self, pressures):
            self.packets.append(list(pressures))

    with patch("output.muscle_output.festo_itf.Festo", RecordingFesto):
        piston_output = MuscleOutput(lambda lengths: lengths, lambda seconds: None, has_piston=True)
        piston_output.set_piston_flag(True)
        piston_output.send_pressures([100, 200, 300, 400, 500, 600])
        assert piston_output.festo.packets[-1] == [100, 200, 300, 400, 500, 600, 1]

        standard_output = MuscleOutput(lambda lengths: lengths, lambda seconds: None)
        standard_output.send_pressures([100, 200, 300, 400, 500, 600])
        assert standard_output.festo.packets[-1] == [100, 200, 300, 400, 500, 600]


def test_sop_backend_counts_out_of_range_kinematic_lengths_without_changing_limits():
    backend = make_sop_backend()

    lengths = backend._lengths_for_real_transform([0, 0, 5000, 0, 0, 0])

    assert backend.length_clip_count == 1
    assert backend.last_length_clip
    assert all(length <= backend.cfg.MUSCLE_MAX_LENGTH for length in lengths)
