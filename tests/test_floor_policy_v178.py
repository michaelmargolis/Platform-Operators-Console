# test_floor_policy_v178.py ver 1
from types import SimpleNamespace
import pytest
from core.platform_geometry import configure_workspace, clearance_settings
from tools.analyze_nl2_csv import make_backend, analyze


@pytest.mark.parametrize('offset',[None,0])
def test_no_floor_requires_no_hull_and_keeps_full_length(offset):
    cfg=SimpleNamespace(MUSCLE_MAX_LENGTH=800,MAX_ACTUATOR_LENGTH=1000,
                        MUSCLE_MAX_ACTIVE_LENGTH=750,WORKSPACE_LIMIT_MODE='geometry')
    if offset is not None:cfg.PLATFORM_CLEARANCE_OFFSET=offset
    backend=SimpleNamespace(cfg=cfg)
    configure_workspace(backend)
    assert backend._workspace_hull_xy==()
    assert backend.workspace_floor_clearance is None
    assert cfg.MUSCLE_MAX_ACTIVE_LENGTH==800
    assert cfg.MAX_ACTIVE_ACTUATOR_LENGTH==1000
    assert clearance_settings(cfg)==(0,None)


def test_mdx_explicit_floor_keeps_existing_geometry_and_neutral():
    b=make_backend('platform_v2')
    assert b.cfg.PLATFORM_CLEARANCE_OFFSET>0
    assert len(b._workspace_hull_xy)==28
    assert b.k.PLATFORM_MID_HEIGHT==pytest.approx(177.26466075505908)
    assert b.cfg.MUSCLE_MAX_ACTIVE_LENGTH==940


def test_chair_analysis_clearance_is_not_applicable_not_zero():
    rows=[(i*.05,12.,(0,0,.1,0,.1,0),None) for i in range(6)]
    frames,stats=analyze(rows,'chair_v3','Recorded heave',80,100,False)
    assert all(f['applied_floor'] is None and f['requested_floor'] is None for f in frames)
    b=make_backend('chair_v3')
    assert b.cfg.MUSCLE_MAX_ACTIVE_LENGTH==b.cfg.MUSCLE_MAX_LENGTH
