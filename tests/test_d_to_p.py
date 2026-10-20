#test_d_to_p.py  ver 1
from pathlib import Path

import numpy as np

from output.d_to_p import DistanceToPressure


ROOT = Path(__file__).resolve().parents[1]
CHAIR_TABLE = ROOT / "output" / "chair_DtoP_synthesized.csv"
WHEELCHAIR_TABLE = ROOT / "output" / "wheelchair_DtoP.csv"


def load_mapper(path, columns, max_length, load):
    mapper = DistanceToPressure(columns, max_length)
    assert mapper.load_data(path)
    mapper.set_load(load)
    return mapper


def test_production_tables_have_expected_loads_and_dimensions():
    chair = load_mapper(CHAIR_TABLE, 201, 800, 11)
    wheelchair = load_mapper(WHEELCHAIR_TABLE, 251, 1000, 13)

    assert tuple(chair.loads) == (11, 22, 31, 41)
    assert tuple(wheelchair.loads) == (13, 24, 33, 43)
    assert chair.all_d_to_p_up.shape == (4, 201)
    assert chair.all_d_to_p_down.shape == (4, 201)
    assert wheelchair.all_d_to_p_up.shape == (4, 251)
    assert wheelchair.all_d_to_p_down.shape == (4, 251)


def test_fractional_load_and_compression_are_interpolated_before_rounding():
    mapper = load_mapper(CHAIR_TABLE, 201, 800, 16.5)
    table = np.loadtxt(CHAIR_TABLE, delimiter=",", comments="#")

    pressures = mapper.muscle_length_to_pressure([699.5] * 6)
    load_midpoint_at_fractional_compression = np.mean(
        [
            np.interp(100.5, np.arange(201), table[0]),
            np.interp(100.5, np.arange(201), table[1]),
        ]
    )

    assert mapper.selected_load == 16.5
    assert pressures.tolist() == [round(load_midpoint_at_fractional_compression)] * 6


def test_gradual_reversal_accumulates_deadband_and_blends_branches():
    mapper = load_mapper(CHAIR_TABLE, 201, 800, 22)
    mapper.muscle_compression_to_pressure([100] * 6)
    mapper.muscle_compression_to_pressure([110] * 6)

    for compression in range(109, 104, -1):
        pressure = mapper.muscle_compression_to_pressure([compression] * 6)
        assert np.all(mapper.active_row == 0)
        assert pressure[0] == round(mapper.d_to_p_up[compression])

    pressure = mapper.muscle_compression_to_pressure([104] * 6)
    expected = round(
        0.8 * mapper.d_to_p_up[104]
        + 0.2 * mapper.d_to_p_down[104]
    )
    assert np.all(mapper.active_row == 0)
    assert pressure.tolist() == [expected] * 6

    mapper.muscle_compression_to_pressure([100] * 6)
    assert np.all(mapper.active_row == 1)


def test_sub_deadband_jitter_does_not_change_branch():
    mapper = load_mapper(CHAIR_TABLE, 201, 800, 22)
    mapper.muscle_compression_to_pressure([100] * 6)

    for compression in (104, 101, 103, 100, 102, 104):
        mapper.muscle_compression_to_pressure([compression] * 6)

    assert np.all(mapper.active_row == 0)


def test_each_muscle_tracks_direction_independently():
    mapper = load_mapper(CHAIR_TABLE, 201, 800, 22)
    mapper.muscle_compression_to_pressure([100] * 6)
    mapper.muscle_compression_to_pressure([110] * 6)

    mapper.muscle_compression_to_pressure([100, 110, 110, 110, 110, 110])
    assert mapper.active_row.tolist() == [1, 0, 0, 0, 0, 0]

    mapper.muscle_compression_to_pressure([100, 100, 110, 110, 110, 110])
    assert mapper.active_row.tolist() == [1, 1, 0, 0, 0, 0]
