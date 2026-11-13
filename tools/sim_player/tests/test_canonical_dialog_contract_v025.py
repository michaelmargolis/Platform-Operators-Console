"""Static contract tests for the Qt dialog when Qt is unavailable in CI."""
from pathlib import Path

DIALOG = Path(__file__).resolve().parents[1] / 'canonical_dialog.py'


def source():
    return DIALOG.read_text(encoding='utf-8')


def test_registration_has_one_file_browser_and_it_is_video():
    s = source()
    assert 'def _browse_video' in s
    assert 'Select reference video' in s
    assert 'def _browse(self,kind)' not in s
    assert "'Original telemetry CSV',self.csv,'csv'" not in s


def test_original_csv_is_read_only_and_automatic():
    s = source()
    assert 'self.csv.setReadOnly(True)' in s
    assert 'companion_telemetry(Path(self.video.text()))' in s
    assert 'Automatically matched telemetry:' in s


def test_setup_does_not_show_old_pair_confirmation_or_offset_checkbox():
    s = source()
    assert 'self.confirm=' not in s
    assert 'self.verified=' not in s
    assert 'This video and telemetry are the same original run' not in s
    assert 'I have checked this offset against a visible event' not in s


def test_visual_verification_belongs_to_calibration_tab_only():
    s = source()
    assert 'I have checked this calibration against a visible video event' in s
    assert 'self.calibration_verified.hide()' in s
    assert 'calibrated=self.calibration_verified.isChecked()' in s
