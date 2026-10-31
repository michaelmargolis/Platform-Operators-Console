# test_ride_labels_v177.py ver 1
"""v177: stable variant IDs, strict modifiers, unambiguous deployed POC UI."""
from copy import deepcopy

import pytest

from sims.nolimits2 import ride_config
from sims.nolimits2.sim import Sim


def validate(variants, *, show=False):
    return ride_config.validate(variants=tuple(variants), default=variants[0]['label'],
                                show_variant_modifiers=show)


def test_full_ids_and_displayed_labels_do_not_change_park_selection(monkeypatch):
    sim = Sim()
    full_labels = sim.get_experiences()
    idx = full_labels.index('Blackbird [Sine]')
    assert sim.display_name_for_experience(idx) == 'Blackbird [Sine]'
    monkeypatch.setattr(ride_config, 'SHOW_VARIANT_MODIFIERS', False)
    assert sim.display_name_for_experience(idx) == 'Blackbird'
    assert sim.get_experiences() == full_labels
    assert sim.park_name_for_experience(idx) == 'Blackbird'
    assert ride_config.park_key_for_variant(full_labels[idx]) == 'blackbird'


def test_full_labels_distinct_when_modifiers_visible():
    both = [
        {'label': 'Blackbird', 'park': 'blackbird'},
        {'label': 'Blackbird [Sine]', 'park': 'blackbird'},
    ]
    assert len(validate(both, show=True)) == 2
    with pytest.raises(ValueError, match='Duplicate displayed experience name.*Blackbird'):
        validate(both, show=False)


def test_only_one_variant_per_park_can_hide_modifiers():
    sole = [
        {'label': 'Blackbird [Gravity]', 'park': 'blackbird', 'angular_cue': 'gravity'},
        {'label': 'GrandNational', 'park': 'grandnational'},
    ]
    assert len(validate(sole, show=False)) == 2
    assert ride_config.validate(variants=sole, default='Blackbird [Gravity]',
                                show_variant_modifiers=False)


def test_brackets_required_and_base_matches_park_name():
    valid = ('Blackbird', 'Blackbird [Sine]', 'Blackbird [Sine] [R120]')
    for name in valid:
        ride_config.validate_variant_label_format(name, 'blackbird')
    invalid = ('Blackbird Sine', 'Blackbird (Sine)', 'Blackbird [Sine] R120',
               'Blackbird [Sine][R120]', 'Blackbird [Sine',
               'Blackbird [Sine [R120]]', 'blackbird [Sine]', 'Blackbird []')
    for name in invalid:
        with pytest.raises(ValueError, match=r'\[modifiers\]'):
            ride_config.validate_variant_label_format(name, 'blackbird')


def test_duplicate_identical_labels_rejected_even_with_modifiers_visible():
    two = [{'label': 'Blackbird', 'park': 'blackbird'}] * 2
    with pytest.raises(ValueError, match='Duplicate operator variant label'):
        validate(two, show=True)


def test_show_modifiers_must_be_boolean():
    with pytest.raises(ValueError, match='must be True or False'):
        ride_config.validate(show_variant_modifiers='false')


def test_default_complete_v176_definitions_remain_valid():
    assert tuple(ride_config.validate()) == ride_config.labels()


def test_poc_experience_dropdown_uses_display_name_only(monkeypatch):
    """Exercise the actual Qt method body using a fake combo (Qt not installed)."""
    import ast
    from pathlib import Path
    from types import SimpleNamespace

    source = Path(__file__).resolve().parents[1] / 'ui' / 'main_window.py'
    tree = ast.parse(source.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'MainWindow')
    func = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                and n.name == '_on_experiences_changed')
    namespace = {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[func], type_ignores=[])),
                 str(source), 'exec'), namespace)

    class Combo:
        def __init__(self):
            self.items = []
            self.index = -1
        def clear(self):
            self.items = []
        def addItems(self, items):
            self.items = list(items)
        def setCurrentIndex(self, index):
            self.index = index

    sim = Sim()
    experiences = list(sim.get_experiences())
    idx = experiences.index('Blackbird [Sine]')
    combo = Combo()
    window = SimpleNamespace(_csv_stream=None, _csv_last_source_frame=None,
                             _updating_experience_combo=False,
                             combo_experience=combo, core=SimpleNamespace(sim=sim),
                             _load_ride_image=lambda: None,
                             _sync_nl2_heave_mode=lambda: None)
    monkeypatch.setattr(ride_config, 'SHOW_VARIANT_MODIFIERS', False)
    namespace['_on_experiences_changed'](window, experiences, idx)
    assert combo.items[idx] == 'Blackbird'
    assert combo.index == idx
    assert experiences[idx] == 'Blackbird [Sine]'
    assert window._confirmed_experience_index == idx
    assert window._updating_experience_combo is False
