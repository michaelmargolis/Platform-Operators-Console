# test_v19_default_variant.py ver 2
"""v19: named park configuration is default, <park> raw is explicit legacy."""
from pathlib import Path
import pytest

from sims.nolimits2 import ride_config
from tools.sim_player.recording import load_recording
from tools.sim_player.variant_presets import (
    default_label_for_capture, raw_label_for_capture, choices_for_capture,
    dropdown_choices_for_capture,
)
from tools.sim_player.tests.test_recording import make_capture


def test_dropdown_names_and_default_are_park_specific():
    assert raw_label_for_capture('Blackbird') == 'Blackbird raw'
    assert raw_label_for_capture('GrandNational') == 'GrandNational raw'
    assert raw_label_for_capture('unknown ride') == 'unknown ride raw'
    assert default_label_for_capture('Blackbird') == 'Blackbird'
    assert default_label_for_capture('GrandNational') == 'GrandNational'
    assert default_label_for_capture('unknown ride') is None
    assert choices_for_capture('Blackbird')[0] == 'Blackbird'
    selected = dropdown_choices_for_capture('Blackbird')
    assert selected[:3] == (
        ('Blackbird', 'Blackbird'),
        ('Blackbird raw', None),
        ('Blackbird [Sine]', 'Blackbird [Sine]'),
    )


def test_standard_default_and_explicit_raw_are_reproducibly_distinct(tmp_path):
    path = make_capture(tmp_path, with_events=False, with_manifest=False, video=False)
    original = path.read_bytes()
    raw = load_recording(path)  # preserve v18 API behaviour for explicit research calls
    standard = load_recording(path, default_standard_variant=True)
    assert raw.variant_label is None
    assert raw.variant_snapshot is None
    assert standard.variant_label == 'Blackbird'
    assert standard.variant_snapshot == ride_config.variant('Blackbird')
    assert standard.cue_mode == 'euler'
    assert standard.gain_percent == (100.0,) * 6
    assert standard.frames[0].source_axes == raw.frames[0].source_axes
    assert standard.frames[0].input_axes[2] == pytest.approx(
        0.9 * (1.0 - 2.0*(7.5-5.9)/(41.0-5.9)))
    assert raw.frames[0].input_axes[2] == 0
    assert standard.frames[0].input_axes[2] != raw.frames[0].input_axes[2]
    assert path.read_bytes() == original


def test_initial_default_cannot_override_explicit_experiment(tmp_path):
    path = make_capture(tmp_path, with_events=False, with_manifest=False, video=False)
    with pytest.raises(ValueError, match='Cannot combine'):
        load_recording(path, variant_label='Blackbird', default_standard_variant=True)


def test_default_falls_back_to_one_approved_variant():
    from sims.nolimits2.variant_store import VariantStore
    store = VariantStore('platform_v2', {'schema_version':1, 'platform_key':'platform_v2',
        'variants':[{'label':'Blackbird [Sine]', 'park':'blackbird', 'status':'preferred'}]})
    assert default_label_for_capture('Blackbird', store) == 'Blackbird [Sine]'


def test_no_standard_uses_first_candidate_in_file_order():
    from sims.nolimits2.variant_store import VariantStore
    store = VariantStore('platform_v2', {'schema_version':1, 'platform_key':'platform_v2',
        'variants':[{'label':'Blackbird [Sine]', 'park':'blackbird'},
                    {'label':'Blackbird [Gravity]', 'park':'blackbird'}]})
    assert default_label_for_capture('Blackbird', store) == 'Blackbird [Sine]'


def test_player_actual_dropdown_method_selects_standard_and_raw_without_qt(tmp_path):
    """Test extracted Qt handler with a fake combo; no GUI process required."""
    import ast
    from types import SimpleNamespace
    from tools.sim_player.variant_presets import dropdown_choices_for_capture

    source = Path(__file__).resolve().parents[1] / 'app.py'
    tree = ast.parse(source.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ReplayWindow')
    func = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                and n.name == '_fill_variant_choices')
    namespace = {'dropdown_choices_for_capture': dropdown_choices_for_capture}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[func], type_ignores=[])),
                 str(source), 'exec'), namespace)

    class Combo:
        def __init__(self):
            self.items = []
            self.selected_index = None
        def blockSignals(self, flag):
            self.blocked = flag
        def clear(self):
            self.items.clear()
        def addItem(self, text, data):
            self.items.append((text, data))
        def findData(self, data):
            return next((i for i, value in enumerate(self.items) if value[1] == data), -1)
        def count(self):
            return len(self.items)
        def setCurrentIndex(self, index):
            self.selected_index = index
        def setEnabled(self, enabled):
            self.enabled = enabled

    def record(label):
        return SimpleNamespace(ride='Blackbird', variant_label=label,
                               variant_snapshot=ride_config.variant(label) if label else None,
                               frames=(1,))

    combo = Combo()
    copy_action = SimpleNamespace(setEnabled=lambda flag: setattr(copy_action, 'enabled', flag))
    from sims.nolimits2.variant_store import VariantStore
    neutral_panel = SimpleNamespace(set_neutral_enabled=lambda flag: setattr(neutral_panel, 'enabled', flag))
    player = SimpleNamespace(variant_box=combo, save_variant_action=copy_action,
                             tuning_panel=neutral_panel,
                             variant_store=VariantStore.load("platform_v2"), worker=None, loading=False)
    handler = namespace['_fill_variant_choices']
    handler(player, record('Blackbird'))
    assert combo.items[:3] == [
        ('Blackbird', 'Blackbird'),
        ('Blackbird raw', None),
        ('Blackbird [Sine]', 'Blackbird [Sine]'),
    ]
    assert combo.selected_index == 0
    assert copy_action.enabled is True
    handler(player, record(None))
    assert combo.selected_index == 1
    assert copy_action.enabled is False
