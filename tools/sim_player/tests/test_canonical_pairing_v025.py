"""Reference-video registration contract added in Task Workspace v0.2.5."""
from pathlib import Path
import pytest

from tools.sim_player.canonical_media import companion_telemetry
from tools.sim_player.media_trajectory import MediaError


def test_video_selects_same_basename_csv(tmp_path):
    video = tmp_path / 'Blackbird_repeat01.mp4'
    csv = tmp_path / 'Blackbird_repeat01.csv'
    video.write_bytes(b'video')
    csv.write_text('elapsed_s\n0\n', encoding='utf-8')
    assert companion_telemetry(video) == csv.resolve()


def test_missing_same_basename_csv_is_explicit_error(tmp_path):
    video = tmp_path / 'MdxLogo_reference.mp4'
    video.write_bytes(b'video')
    (tmp_path / 'different.csv').write_text('elapsed_s\n0\n', encoding='utf-8')
    with pytest.raises(MediaError, match=r'Expected MdxLogo_reference\.csv in the same folder'):
        companion_telemetry(video)


def test_video_suffix_does_not_change_required_csv_basename(tmp_path):
    video = tmp_path / 'Scenic_Run.webm'
    csv = tmp_path / 'Scenic_Run.csv'
    video.write_bytes(b'video')
    csv.write_text('elapsed_s\n0\n', encoding='utf-8')
    assert companion_telemetry(video).name == 'Scenic_Run.csv'


def test_non_video_path_rejected(tmp_path):
    text = tmp_path / 'ride.txt'
    text.write_text('x')
    with pytest.raises(MediaError, match='Select an existing'):
        companion_telemetry(text)
