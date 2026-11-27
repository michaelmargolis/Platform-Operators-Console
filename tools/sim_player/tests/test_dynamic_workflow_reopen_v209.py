# test_dynamic_workflow_reopen_v209.py ver 3
from pathlib import Path


def workspace_source():
    return (Path(__file__).parents[1] / 'dynamic_ride_workspace.py').read_text(encoding='utf-8')


def test_review_remains_available_after_review_stage():
    source = workspace_source()
    assert "review_btn = QPushButton('Review profile')" in source
    assert 'lambda _checked=False, key=platform_key: self._review_profile_for_platform(key)' in source
    assert "panel['review'].setEnabled(not dismissed)" in source
    assert "can_export = (reviewed and not dismissed" in source


def test_completed_generation_has_useful_reopen_summary():
    source = workspace_source()
    assert "analyzer_generation_output.txt" in source
    assert "Candidates for the current ride/platform selection:" in source
    assert "Detailed optimiser output was not retained for this older generated profile set." in source
    assert "self.log.setPlainText(self._generation_summary_text())" in source
