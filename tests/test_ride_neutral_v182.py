# test_ride_neutral_v182.py ver 1
"""v182 ride-height bounds, physical signs, limiter anchor and live POC seam."""
from __future__ import annotations

import ast
import logging
import math
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from core.ride_neutral import (
    MAX_RIDE_NEUTRAL_DELTA_MM, validated_ride_neutral_delta,
    real_heave_for_physical_height,
)
from core.platform_controller import SopPlatformBackend
from sims.nolimits2 import ride_config
from sims.nolimits2.variant_store import VariantStore
from tools.analyze_nl2_csv import make_backend


def test_chair_v3_limits_implemented_as_instance_override():
    cfg=make_backend('chair_v3').cfg
    assert cfg.LIMITS_1DOF_TRANFORM[:3]==(90,90,100)
    assert cfg.LIMITS_1DOF_TRANFORM[3:]==pytest.approx([math.radians(x) for x in (10,8,9)])
    legacy=make_backend('chair_v2').cfg
    assert legacy.LIMITS_1DOF_TRANFORM[3:]==pytest.approx(
        [math.radians(x) for x in (12,10,12)])
    assert cfg.NEUTRAL_HEIGHT_OFFSET_MM==100


@pytest.mark.parametrize('value', [True, None, '12', float('nan'), float('inf'), -101, 101])
def test_schema_rejects_invalid_ride_neutral(value):
    with pytest.raises(ValueError,match='neutral_height_delta_mm|ride neutral'):
        ride_config.validate(variants=[{'label':'Blackbird','park':'blackbird',
                                         'neutral_height_delta_mm':value}])


@pytest.mark.parametrize('delta', [-100,-18.5,0,20,100])
def test_snapshot_resolves_optional_value(delta):
    entry={'label':'Blackbird','park':'blackbird', 'neutral_height_delta_mm':delta}
    ride_config.validate(variants=[entry])
    assert ride_config.resolve_variant(entry)['neutral_height_delta_mm']==delta
    entry.pop('neutral_height_delta_mm')
    assert ride_config.resolve_variant(entry)['neutral_height_delta_mm']==0.0


def test_normalization_and_heave_sign():
    assert MAX_RIDE_NEUTRAL_DELTA_MM==100
    assert validated_ride_neutral_delta(-14.5)==-14.5
    assert real_heave_for_physical_height(10,-1)==-10
    assert real_heave_for_physical_height(-10,-1)==10
    with pytest.raises(ValueError):real_heave_for_physical_height(10,0)


@pytest.mark.parametrize('platform', ('chair_v3','chair_v2','platform_v2'))
def test_offset_direction_and_zero_preserve_current_transform(platform):
    b=make_backend(platform)
    norm=(0,0,.25,.04,-.08,.03)
    original=b.k.norm_to_real(norm)
    baseline=b.real_with_ride_neutral_offset(original)
    assert baseline==pytest.approx(original)
    b.set_ride_neutral_height_delta_mm(10)
    higher=b.real_with_ride_neutral_offset(original)
    assert higher[:2]==pytest.approx(baseline[:2])
    assert higher[2]==pytest.approx(baseline[2]-10)
    higher_lengths=b._actuator_lengths_for_real_transform(higher)
    b.set_ride_neutral_height_delta_mm(-10)
    lower=b.real_with_ride_neutral_offset(original)
    lower_lengths=b._actuator_lengths_for_real_transform(lower)
    assert lower[2]==pytest.approx(baseline[2]+10)
    assert all(float(h)<float(l) for h,l in zip(higher_lengths,lower_lengths))
    b.set_ride_neutral_height_delta_mm(0)
    assert b.real_with_ride_neutral_offset(original)==pytest.approx(baseline)


def test_setting_height_validates_anchor_and_does_not_mutate_ik_cache():
    b=make_backend('chair_v2')
    b.k.inverse_kinematics([10,0,0,0,0,0])
    prior=b.k.cached_pose.copy()
    b.set_ride_neutral_height_delta_mm(20)
    assert np.array_equal(prior,b.k.cached_pose)
    with pytest.raises(ValueError,match='outside'):
        b.set_ride_neutral_height_delta_mm(-100)
    assert b.ride_neutral_height_delta_mm==20
    assert np.array_equal(prior,b.k.cached_pose)


def test_non_ride_real_pose_remains_unbiased_during_parking():
    b=make_backend('chair_v3')
    pose=[0,0,-30,0,0,0]
    original=b.k.muscle_lengths(pose)
    b.set_ride_neutral_height_delta_mm(-45)
    # Parking and prop clearance always use already-real transforms.
    direct=b._lengths_for_real_transform(pose)
    assert direct==pytest.approx(original)
    activated=b.real_with_ride_neutral_offset(pose)
    assert activated[2]==pytest.approx(15)
    assert b.k.muscle_lengths(activated)!=pytest.approx(original)


def test_geometry_limiter_interpolates_about_biased_neutral():
    b=make_backend('platform_v2')
    b.set_ride_neutral_height_delta_mm(-20)
    input_real=b.k.norm_to_real((1,1,0,1,1,1))
    request=b.real_with_ride_neutral_offset(input_real)
    applied, actuator=b._limit_workspace(request)
    assert b.workspace_limited and 0<b.workspace_scale<1
    # The request's zero dynamic Heave stays at the biased neutral even though
    # the other axes have to be scaled. Scaling toward original zero would lose it.
    anchor=b._ride_neutral_real_transform()
    assert applied[2]==pytest.approx(anchor[2])
    assert applied[0]==pytest.approx(anchor[0]+b.workspace_scale*(request[0]-anchor[0]))
    assert all(b.cfg.MIN_ACTUATOR_LENGTH-1e-5 <= x <= b.cfg.MAX_ACTUATOR_LENGTH+1e-5
               for x in actuator)


def test_mdx_floor_check_still_applies_to_height_biased_pose():
    b=make_backend('platform_v2')
    b.set_ride_neutral_height_delta_mm(-20)
    input_real=b.k.norm_to_real((0,0,1,1,1,0))
    request=b.real_with_ride_neutral_offset(input_real)
    applied,_=b._limit_workspace(request)
    required=b._workspace_min_floor_clearance+b._workspace_hull_margin
    assert b._floor_clearance_for_real_transform(applied)>=required-1e-5


def test_poc_variant_sync_applies_only_confirmed_height_and_ignores_pending():
    # Real POC method, extracted because PyQt5 is unavailable in headless tests.
    path=Path(__file__).resolve().parents[1]/'core/poc_core.py'
    tree=ast.parse(path.read_text())
    cls=next(x for x in tree.body if isinstance(x,ast.ClassDef) and x.name=='PocCore')
    fn=next(x for x in cls.body if isinstance(x,ast.FunctionDef)
            and x.name=='_sync_nl2_angular_washout')
    namespace={'log':logging.getLogger('neutral-seam')}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),str(path),'exec'),namespace)
    from core.telemetry_config import TelemetryConfig
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as temp:
        backend=SimpleNamespace(ride_neutral_height_delta_mm=0.0)
        backend.set_ride_neutral_height_delta_mm=lambda value:setattr(backend,'ride_neutral_height_delta_mm',value)
        variant=ride_config.resolve_variant({'label':'Blackbird','park':'blackbird',
                                              'neutral_height_delta_mm':-18})
        sim=SimpleNamespace(_ride_load_pending=True,loaded_variant_label='Blackbird',
                             loaded_variant_snapshot=lambda:variant,heave_mode='normalized_height')
        core=SimpleNamespace(sim_key='nolimits2',sim=sim,platform=SimpleNamespace(backend=backend),
            telemetry_config=TelemetryConfig(Path(temp)/'values.ini'),
            _angular_washout_ride=None,_motion_transform=(0,)*6)
        method=namespace['_sync_nl2_angular_washout']
        method(core)
        assert backend.ride_neutral_height_delta_mm==0
        sim._ride_load_pending=False
        method(core)
        assert backend.ride_neutral_height_delta_mm==-18
        assert core._angular_washout_ride=='Blackbird'
        assert core._motion_transform==(0,)*6
        sim.loaded_variant_label='GrandNational'
        sim.loaded_variant_snapshot=lambda:ride_config.resolve_variant({'label':'GrandNational','park':'grandnational'})
        method(core)
        assert backend.ride_neutral_height_delta_mm==0


def test_independent_platform_catalogs_and_atomically_saved_candidate(tmp_path):
    a=VariantStore.load('chair_v3',directory=tmp_path/'a')
    b=VariantStore.load('platform_v2',directory=tmp_path/'b')
    a.add_candidate({'label':'Blackbird [Neutral -15mm]', 'park':'blackbird',
                     'neutral_height_delta_mm':-15})
    assert a.entry('Blackbird [Neutral -15mm]')['status']=='candidate'
    assert a.snapshot('Blackbird [Neutral -15mm]')['neutral_height_delta_mm']==-15
    assert 'Blackbird [Neutral -15mm]' not in b.labels()
    assert VariantStore.load('chair_v3',directory=tmp_path/'a').entry('Blackbird [Neutral -15mm]')\
        ['neutral_height_delta_mm']==-15
