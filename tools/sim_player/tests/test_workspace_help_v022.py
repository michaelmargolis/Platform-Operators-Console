"""Help content contracts; these tests need no Qt installation."""
import ast
from pathlib import Path

from tools.sim_player.workspace_help_content import help_topics

PLAYER = Path(__file__).resolve().parents[1]


def test_original_six_topics_are_retained_with_current_limits_tab():
    topics = help_topics()
    assert tuple(t.title for t in topics) == (
        'Workflows', 'Markers and playback', 'Gains versus profiles',
        'Reference video', 'Washout', 'Limits and safety')
    assert len({t.key for t in topics}) == len(topics)


def test_all_pages_have_substantial_self_contained_content():
    for topic in help_topics():
        assert '<h2>' in topic.html and '</h2>' in topic.html
        assert len(topic.html.split()) > 80
        assert '<script' not in topic.html.lower()


def test_current_player_help_does_not_present_the_shell_as_working_code():
    text = '\n'.join(t.html for t in help_topics())
    assert 'Play only changes' not in text
    assert 'UI prototype only' not in text
    assert 'read-only in this release' in text


def test_reference_video_workflow_and_uncertainty_are_documented():
    text = {t.key: t.html for t in help_topics()}['video']
    assert 'Open &gt; Ride reference video' in text
    assert 'tools/sim_player/canonical_rides/' in text
    assert 'New video capture required' in text
    assert 'timing estimates' in text
    assert 'not a new verification' in text
    assert 'original telemetry CSV' in text


def test_all_three_help_entry_points_documented():
    text = {t.key: t.html for t in help_topics()}['workflows']
    assert 'Help &gt; Workspace help' in text
    assert 'information icon' in text
    assert 'F1' in text


def test_any_pair_and_independent_range_markers_remain_explained():
    content = {t.key: t.html for t in help_topics()}
    assert 'including two' in content['gains']
    assert 'strict or two relaxed' in content['gains']
    assert 'different controls' in content['playback']
    assert 'no split Video + Plot' in content['playback']


def test_washout_editing_limits_are_explicit():
    text = {t.key: t.html for t in help_topics()}['washout']
    assert 'numeric values' in text
    assert 'whole capture' in text
    assert 'independently' in text
    assert 'linked editing' in text and 'not present' in text
    assert 'not a filter-only response' in text
    assert 'physical platform' in text


def test_help_content_has_no_processing_or_output_imports():
    module = ast.parse((PLAYER / 'workspace_help_content.py').read_text())
    allowed = {'__future__', 'dataclasses'}
    for node in ast.walk(module):
        if isinstance(node, ast.ImportFrom):
            assert node.module in allowed
        elif isinstance(node, ast.Import):
            assert all(n.name in allowed for n in node.names)


def test_help_window_is_modeless_and_keeps_content_scrollable():
    tree = ast.parse((PLAYER / 'workspace_help.py').read_text())
    calls = [node.func.attr for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]
    assert 'exec' not in calls
    assert 'setOpenExternalLinks' in calls
    assert 'setOpenLinks' in calls
    assert 'setUsesScrollButtons' in calls
    assert 'setHtml' in calls
