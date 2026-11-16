#test_dynamic_clearance_v202.py ver 1
"""Commissioning floor-clearance setting, independent of hardware and live Qt."""
from pathlib import Path
from types import SimpleNamespace
import pytest

from core.config_store import ConfigStore
from core.platform_geometry import validate_operating_floor_clearance
from core.platform_controller import SopPlatformBackend


@pytest.mark.parametrize('value', (5,15,27.5,75))
def test_ground_loading_floor_setting_bounds_accept(value):
    assert validate_operating_floor_clearance(value) == value


@pytest.mark.parametrize('value', (-1,0,4.99,75.01,1e309,float('nan'),True,'15'))
def test_ground_loading_floor_setting_bounds_reject(value):
    with pytest.raises(ValueError):
        validate_operating_floor_clearance(value)


def test_floor_setting_does_not_change_geometry_or_park(tmp_path):
    backend = object.__new__(SopPlatformBackend)
    backend._workspace_hull_xy = ((0,0),(1,0),(1,1))
    backend.workspace_limit_mode = 'geometry'
    backend._workspace_min_floor_clearance=15.
    backend.ride_neutral_height_delta_mm=0.
    backend._workspace_floor_z=17.7
    backend._workspace_underside_z=-100.
    backend.set_ride_neutral_height_delta_mm=lambda x: None
    backend.set_minimum_operating_floor_clearance_mm(20.)
    assert backend._workspace_min_floor_clearance == 20.
    assert backend._workspace_floor_z == 17.7
    assert backend._workspace_underside_z == -100.
    def refused(_): raise ValueError('neutral invalid')
    backend.set_ride_neutral_height_delta_mm=refused
    with pytest.raises(ValueError, match='neutral invalid'):
        backend.set_minimum_operating_floor_clearance_mm(22.)
    assert backend._workspace_min_floor_clearance == 20.


def test_config_store_persists_adjustment(tmp_path):
    path=tmp_path/'poc_config.json'
    conf=ConfigStore(path)
    assert conf.get('ground_loading_min_clearance_mm') == 15.
    conf.set('ground_loading_min_clearance_mm',25.5)
    assert ConfigStore(path).get('ground_loading_min_clearance_mm') == 25.5
