# test_variant_store_v178.py ver 1
"""Per-platform persistence and curation invariants, including failed writes."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from sims.nolimits2 import ride_config
from sims.nolimits2 import variant_store as mod
from sims.nolimits2.variant_store import VariantStore, VariantStoreError, VariantConflictError


def document(key='platform_v2'):
    return {'schema_version': 1, 'platform_key': key, 'variants': [
        {'label':'Blackbird', 'park':'blackbird'},
        {'label':'Blackbird [Sine]', 'park':'blackbird', 'angular_cue':'sine'},
        {'label':'Blackbird [Gravity]', 'park':'blackbird', 'angular_cue':'gravity'},
        {'label':'GrandNational', 'park':'grandnational'},
    ]}


def writable(tmp_path, key='platform_v2', data=None):
    path = tmp_path / (key + '.json')
    path.write_text(json.dumps(data or document(key)), encoding='utf-8')
    return VariantStore.load(key, directory=tmp_path)


def test_preferred_hides_candidates_even_if_later_appended(tmp_path):
    s = writable(tmp_path)
    s.review('Blackbird [Sine]', 'preferred')
    assert s.visible_labels('blackbird') == ('Blackbird [Sine]',)
    assert s.entry('Blackbird')['status'] == 'rejected'
    assert s.entry('Blackbird [Gravity]')['status'] == 'rejected'
    s.add_candidate({'label':'Blackbird [New]', 'park':'blackbird'})
    assert s.visible_labels('blackbird') == ('Blackbird [Sine]',)
    assert s.player_labels('blackbird') == ('Blackbird [Sine]', 'Blackbird [New]')
    assert s.default_label('blackbird') == 'Blackbird [Sine]'


def test_missing_status_is_candidate_and_rejection_only_changes_one_entry(tmp_path):
    s = writable(tmp_path)
    before = s.entry('Blackbird [Sine]')
    s.review('Blackbird', 'rejected')
    assert 'Blackbird' not in s.visible_labels()
    assert s.entry('Blackbird [Sine]') == before
    assert s.default_label('blackbird') == 'Blackbird [Sine]'
    assert s.default_label('grandnational') == 'GrandNational'


def test_review_is_platform_local_and_persists_with_backup(tmp_path):
    a, b = writable(tmp_path), writable(tmp_path, 'chair_v3')
    old_a, old_b = a.path.read_bytes(), b.path.read_bytes()
    a.review('Blackbird [Sine]', 'preferred')
    assert b.path.read_bytes() == old_b
    assert a.path.with_suffix('.json.bak').read_bytes() == old_a
    reloaded = VariantStore.load('platform_v2', directory=tmp_path)
    assert reloaded.visible_labels('blackbird') == ('Blackbird [Sine]',)
    assert a.snapshot('Blackbird [Sine]')['platform_key'] == 'platform_v2'
    assert b.snapshot('Blackbird [Sine]')['platform_key'] == 'chair_v3'


def test_no_visible_variants_is_valid_and_no_default(tmp_path):
    s = writable(tmp_path)
    for label in s.labels():
        s.review(label, 'rejected')
    assert s.visible_labels() == ()
    assert s.default_label() is None
    s.validate_display(False)


def test_last_remaining_preview_does_not_mutate_anything(tmp_path):
    s = writable(tmp_path)
    old = s.path.read_bytes()
    preview = s.review_preview('GrandNational', 'rejected')
    assert preview.visible_labels('grandnational') == ()
    assert s.path.read_bytes() == old
    assert s.visible_labels('grandnational') == ('GrandNational',)


def test_duplicate_display_checked_only_after_filtering(tmp_path):
    s = writable(tmp_path)
    with pytest.raises(ValueError, match='Duplicate displayed'):
        s.validate_display(False)
    s.review('Blackbird [Sine]', 'preferred')
    s.validate_display(False)
    # Review-independent latent candidates are also suppressed for POC.
    s.add_candidate({'label':'Blackbird [Later]', 'park':'blackbird'})
    s.validate_display(False)


def test_failed_replacement_leaves_disk_memory_and_visible_list_unchanged(tmp_path, monkeypatch):
    s = writable(tmp_path)
    before, entries, visible = s.path.read_bytes(), s.entries(), s.visible_labels()
    replace = mod.os.replace
    def fail_active(src, dst):
        if Path(dst) == s.path:
            raise OSError('injected full disk / denied replace')
        return replace(src, dst)
    monkeypatch.setattr(mod.os, 'replace', fail_active)
    with pytest.raises(OSError, match='injected'):
        s.review('Blackbird', 'preferred')
    assert s.path.read_bytes() == before
    assert s.entries() == entries
    assert s.visible_labels() == visible
    assert not list(tmp_path.glob('*.tmp'))
    assert not list(tmp_path.glob('.*.lock'))


def test_failed_backup_does_not_commit(tmp_path, monkeypatch):
    s = writable(tmp_path)
    old = s.path.read_bytes()
    monkeypatch.setattr(mod, '_atomic_bytes', lambda *_: (_ for _ in ()).throw(OSError('backup blocked')))
    with pytest.raises(OSError):
        s.review('Blackbird', 'rejected')
    assert s.path.read_bytes() == old
    assert 'Blackbird' in s.visible_labels()


def test_stale_writer_is_rejected_instead_of_losing_another_apps_changes(tmp_path):
    a = writable(tmp_path)
    b = VariantStore.load('platform_v2', directory=tmp_path)
    a.review('Blackbird [Sine]', 'preferred')
    after = a.path.read_bytes()
    with pytest.raises(VariantConflictError, match='changed outside'):
        b.review('GrandNational', 'rejected')
    assert a.path.read_bytes() == after
    assert 'GrandNational' in b.visible_labels()


def test_lock_conflict_is_explicit(tmp_path):
    s = writable(tmp_path)
    lock = s.path.with_name('.' + s.path.name + '.lock')
    lock.write_text('12345')
    with pytest.raises(VariantConflictError, match='being saved'):
        s.review('Blackbird', 'rejected')
    assert lock.exists()  # Never remove another writer's lock.


def test_defaults_seed_only_once(tmp_path):
    s = VariantStore.load('chair_v3', directory=tmp_path)
    s.review('Blackbird', 'preferred')
    raw = s.path.read_bytes()
    assert VariantStore.load('chair_v3', directory=tmp_path).path.read_bytes() == raw
    assert not (tmp_path / 'platform_v2.json').exists()


@pytest.mark.parametrize('change', [
    lambda d: d.update(schema_version=True),
    lambda d: d.update(schema_version=2),
    lambda d: d.update(platform_key='chair_v3'),
    lambda d: d.update(variants={}),
    lambda d: d['variants'].append(deepcopy(d['variants'][0])),
    lambda d: d['variants'][0].update(status='accepted'),
    lambda d: d['variants'][0].update(platform_key='chair_v3'),
    lambda d: d['variants'][0].update(park='absent'),
    lambda d: d['variants'][0].update(label='Blackbird Sine'),
    lambda d: d['variants'][0].update(gain_scale_pct={'roll_gain':float('nan')}),
    lambda d: d['variants'][0].update(roll_washout='true'),
    lambda d: (d['variants'][0].update(status='preferred'), d['variants'][1].update(status='preferred')),
])
def test_invalid_catalogs_fail_before_use(change):
    d = document()
    change(d)
    with pytest.raises(VariantStoreError):
        VariantStore('platform_v2', d)


def test_duplicate_json_keys_are_not_silently_lost(tmp_path):
    p = tmp_path/'platform_v2.json'
    p.write_text('{"schema_version":1,"platform_key":"platform_v2","variants":[],"variants":[]}')
    with pytest.raises(VariantStoreError, match='Duplicate JSON key'):
        VariantStore.load('platform_v2', directory=tmp_path)


def test_adding_duplicate_label_fails_without_changing_json(tmp_path):
    s = writable(tmp_path)
    old = s.path.read_bytes()
    with pytest.raises(VariantStoreError):
        s.add_candidate({'label':'Blackbird', 'park':'blackbird'})
    assert s.path.read_bytes() == old


def test_old_working_file_never_silently_replaced_if_malformed(tmp_path):
    path=tmp_path/'platform_v2.json'
    path.write_text('{broken')
    with pytest.raises(VariantStoreError):
        VariantStore.load('platform_v2', directory=tmp_path)
    assert path.read_text() == '{broken'


def test_lock_cleanup_failure_does_not_turn_success_into_a_false_failed_save(tmp_path, monkeypatch, caplog):
    s=writable(tmp_path)
    original=Path.unlink
    def fail_lock(path, *args, **kwargs):
        if path.name.endswith('.lock'):
            raise PermissionError('injected lock cleanup failure')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', fail_lock)
    s.review('Blackbird [Sine]','preferred')
    assert s.visible_labels('blackbird') == ('Blackbird [Sine]',)
    assert VariantStore.load('platform_v2',directory=tmp_path).visible_labels('blackbird') == ('Blackbird [Sine]',)
    assert 'Cannot remove variant write lock' in caplog.text
    with pytest.raises(VariantConflictError):
        s.review('Blackbird [Sine]','rejected')
