# test_nl2_normalized_height_v157.py ver 1
"""Phase 2 NL2 heave mapping: calibrated ride height, per-ride amplitudes, safety."""
import math

import pytest

from sims.nolimits2 import config
from sims.nolimits2.sim import Sim
from tests.test_nolimits2_adapter import FakeNL2Client, telemetry


EXPECTED_AMPLITUDES = {
    "Mdx Logo Coaster": .80,
    "Blackbird": .90,
    "Fenrir": .70,
    "Forest Mountain Park": .90,
    "GrandNational": .90,
    "Hybris": .90,
    "Wilderness Park": .90,
}


def _at_ride(sim, name):
    sim._selected = sim.get_experiences().index(name)


def test_initial_heave_mode_and_unchanged_legacy_and_vertical_g():
    sim = Sim(client=FakeNL2Client())
    assert sim.heave_mode == config.HEAVE_MODE_LEGACY_HEIGHT
    assert sim._legacy_heave_enabled is True  # v156 recorder compatibility
    assert sim._transform_from_telemetry(telemetry(pos_y=7.4, gy=1.5))[2] == -0.5
    sim.set_heave_mode(config.HEAVE_MODE_VERTICAL_G)
    assert sim._legacy_heave_enabled is False
    assert math.isclose(sim._transform_from_telemetry(telemetry(pos_y=7.4, gy=1.5))[2], .2)
    sim.set_legacy_heave_enabled(True)  # older clients still supported
    assert sim.heave_mode == config.HEAVE_MODE_LEGACY_HEIGHT
    assert sim._transform_from_telemetry(telemetry(pos_y=7.4, gy=1.5))[2] == -0.5


@pytest.mark.parametrize("ride,amplitude", EXPECTED_AMPLITUDES.items())
def test_per_ride_amplitude_endpoints_midpoint_and_clamping(ride, amplitude):
    sim = Sim(client=FakeNL2Client())
    _at_ride(sim, ride)
    assert sim.has_normalized_height_profile()
    profile = config.NORMALIZED_HEIGHT_PROFILES[ride]
    low = profile['min_height_m']
    high = profile['max_height_m']
    assert low < high
    sim.set_heave_mode(config.HEAVE_MODE_NORMALIZED_HEIGHT)
    # Inverted Stewart platform: rising coaster height means NEGATIVE heave
    # and increasing muscle contraction, not extension.
    for height, expected in ((low, amplitude), (high, -amplitude),
                             ((low + high) / 2, 0.0), (low - 100, amplitude),
                             (high + 100, -amplitude)):
        result = sim._transform_from_telemetry(telemetry(pos_y=height, gy=2.0))
        assert math.isclose(result[2], expected, abs_tol=1.0e-10)
        assert len(result) == 6
    assert amplitude <= .90


@pytest.mark.parametrize('ride', ['Flying Beast', 'Scenic Run', 'Tilt Coaster'])
def test_unrecorded_and_incomplete_rides_reject_normalized_height(ride):
    sim = Sim(client=FakeNL2Client())
    _at_ride(sim, ride)
    assert not sim.has_normalized_height_profile()
    with pytest.raises(ValueError, match='No calibrated height profile'):
        sim.set_heave_mode(config.HEAVE_MODE_NORMALIZED_HEIGHT)
    assert sim.heave_mode == config.HEAVE_MODE_LEGACY_HEIGHT


def test_ride_switch_retains_normalized_mode_with_profile_and_reverts_when_missing():
    client = FakeNL2Client()
    sim = Sim(client=client)
    sim.set_heave_mode(config.HEAVE_MODE_NORMALIZED_HEIGHT)
    sim._begin_ride_load(sim.get_experiences().index('Blackbird'))
    assert sim.heave_mode == config.HEAVE_MODE_NORMALIZED_HEIGHT
    assert sim._transform_from_telemetry(telemetry(pos_y=41.0))[2] == -.90
    sim._begin_ride_load(sim.get_experiences().index('Scenic Run'))
    assert sim.heave_mode == config.HEAVE_MODE_LEGACY_HEIGHT
    assert not sim.has_normalized_height_profile()


def test_invalid_mode_and_malformed_profile_are_rejected(monkeypatch):
    sim = Sim(client=FakeNL2Client())
    with pytest.raises(ValueError, match='Unknown NL2 heave mode'):
        sim.set_heave_mode('other')
    _at_ride(sim, 'Blackbird')
    broken = dict(config.NORMALIZED_HEIGHT_PROFILES)
    broken['Blackbird'] = {'min_height_m': 30, 'max_height_m': 10, 'amplitude': .9}
    monkeypatch.setattr(config, 'NORMALIZED_HEIGHT_PROFILES', broken)
    assert not sim.has_normalized_height_profile()
    with pytest.raises(ValueError, match='No calibrated'):
        sim.set_heave_mode(config.HEAVE_MODE_NORMALIZED_HEIGHT)


def test_gui_has_single_three_mode_selector_for_both_qt_layouts():
    # This structural check runs without PyQt5; interactive Qt behaviour must
    # still be verified on the target PC/Pi.
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "ui/main_window.py").read_text()
    assert 'combo_nl2_heave_mode = QtWidgets.QComboBox()' in source
    assert 'self.combo_nl2_heave_mode.currentIndexChanged.connect(self._nl2_heave_mode_changed)' in source
    assert 'self.core.set_nl2_heave_mode(mode)' in source
    assert 'self._sync_nl2_heave_mode()' in source
    assert 'chk_nl2_legacy_heave' not in source
    from xml.etree import ElementTree as ET
    for name in ('poc_main.ui', 'poc_main_800x480.ui'):
        root = ET.parse(Path(__file__).resolve().parents[1] / 'ui' / name).getroot()
        assert root.find('.//layout[@name="telemetryButtonsLayout"]') is not None


def test_mdx_muscles_shorten_as_normalized_coaster_height_increases():
    from kinematics.cfg_SuspendedPlatformMdx import PlatformConfig
    from kinematics.kinematics_V3 import Kinematics, PlatformParams
    cfg = PlatformConfig()
    kinematics = Kinematics()
    params = PlatformParams(cfg.MUSCLE_MIN_LENGTH, cfg.MUSCLE_MAX_LENGTH,
                            cfg.FIXED_HARDWARE_LENGTH, cfg.LIMITS_1DOF_TRANFORM)
    coords = getattr(cfg, 'platform_coords', None)
    if coords is None:
        coords = cfg.platform_coords_xy
    kinematics.set_geometry(cfg.base_coords, coords, params,
                            getattr(cfg, 'PLATFORM_CLEARANCE_OFFSET', 50),
                            getattr(cfg, 'MUSCLE_EXTENSION_RESERVE', None))
    sim = Sim(client=FakeNL2Client())
    sim.set_heave_mode(config.HEAVE_MODE_NORMALIZED_HEIGHT)

    def average_length(height):
        norm = sim._transform_from_telemetry(
            telemetry(pos_y=height, gx=0, gy=1, gz=0)
        )
        real = kinematics.norm_to_real(norm)
        _, actuators = kinematics.inverse_kinematics(real, return_lengths=True)
        return sum(float(x) - cfg.FIXED_HARDWARE_LENGTH for x in actuators)/6

    bottom = average_length(3.2)
    top = average_length(32.7)
    assert 890 < bottom < 910
    assert 780 < top < 800
    assert top < bottom  # rising NL2 ride -> contracting suspended muscles
