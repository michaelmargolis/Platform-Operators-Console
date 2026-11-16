# test_dynamic_workflow_state_v207.py ver 1
from pathlib import Path
from types import SimpleNamespace

from tools.sim_player.dynamic_workflow_state import (
    candidate_is_qualified, candidate_sort_key, recommended_candidate_paths,
    workflow_stage,
)


def choice(platform, target, qualification='nominal_all_captures_pass', name='candidate'):
    return SimpleNamespace(
        path=Path(f'/profiles/{platform}/{target}/profile.json').resolve(),
        platform=platform,
        target=target,
        qualification=qualification,
        name=name,
    )


def test_recommend_strict_per_platform_when_qualified():
    rows = [
        choice('chair_v3', 'relaxed5'), choice('chair_v3', 'strict'),
        choice('platform_v2', 'relaxed5'), choice('platform_v2', 'strict'),
    ]
    rec = recommended_candidate_paths(rows)
    assert Path(rec['chair_v3']).parts[-3:] == ('chair_v3', 'strict', 'profile.json')
    assert Path(rec['platform_v2']).parts[-3:] == ('platform_v2', 'strict', 'profile.json')
    assert sorted(rows, key=candidate_sort_key)[0].target == 'strict'


def test_relaxed_is_recommended_only_when_strict_unavailable_or_dismissed():
    strict = choice('chair_v3', 'strict')
    relaxed = choice('chair_v3', 'relaxed5')
    failed = choice('platform_v2', 'strict', 'failed')
    relaxed_ground = choice('platform_v2', 'relaxed5')
    rec = recommended_candidate_paths(
        [strict, relaxed, failed, relaxed_ground], dismissed={str(strict.path)}
    )
    assert rec['chair_v3'] == str(relaxed.path)
    assert rec['platform_v2'] == str(relaxed_ground.path)
    assert candidate_is_qualified(failed) is False


def test_workflow_stage_advances_in_one_direction_for_selected_candidate():
    assert workflow_stage(has_study=False, generating=False, has_result=False,
                          has_candidate=False, reviewed=False, prepared=False) == 'source'
    assert workflow_stage(has_study=True, generating=False, has_result=False,
                          has_candidate=False, reviewed=False, prepared=False) == 'generate'
    assert workflow_stage(has_study=True, generating=True, has_result=False,
                          has_candidate=False, reviewed=False, prepared=False) == 'generating'
    assert workflow_stage(has_study=True, generating=False, has_result=True,
                          has_candidate=True, reviewed=False, prepared=False) == 'review'
    assert workflow_stage(has_study=True, generating=False, has_result=True,
                          has_candidate=True, reviewed=True, prepared=False) == 'prepare'
    assert workflow_stage(has_study=True, generating=False, has_result=True,
                          has_candidate=True, reviewed=True, prepared=True) == 'export'


def test_workspace_source_contract_is_guided_not_flat_button_grid():
    source = (Path(__file__).parents[1] / 'dynamic_ride_workspace.py').read_text(encoding='utf-8')
    assert "('1 Source', '2 Generate', '3 Review', '4 Prepare', '5 Export')" in source
    assert 'class WorkflowProgressStrip(QWidget):' in source
    assert 'self.progress_strip = WorkflowProgressStrip(self)' in source
    assert 'Current step:' in source
    assert 'Next action:' in source
    assert 'self.primary_btn = QPushButton()' in source
    assert 'self.primary_btn.setMaximumWidth(280)' in source
    assert 'self.source_toggle.setMaximumWidth(320)' in source
    assert 'Show generation details' in source
    assert 'Prepared package: none' not in source
    assert 'Recommended for this platform' in source
