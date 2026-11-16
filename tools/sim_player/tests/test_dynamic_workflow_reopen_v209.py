# test_dynamic_workflow_reopen_v209.py ver 1
from pathlib import Path


def workspace_source():
    return (Path(__file__).parents[1] / 'dynamic_ride_workspace.py').read_text(encoding='utf-8')


def test_review_remains_available_after_review_stage():
    source = workspace_source()
    assert "self.review_again_btn = QPushButton('Review profile again')" in source
    assert "stage in ('prepare', 'export')" in source
    assert 'self.review_again_btn.clicked.connect(self.review_profile)' in source


def test_completed_generation_has_useful_reopen_summary():
    source = workspace_source()
    assert "analyzer_generation_output.txt" in source
    assert "Candidates for the current ride/platform selection:" in source
    assert "Detailed optimiser output was not retained for this older generated study." in source
    assert "noun = 'generation summary'" in source
