# test_candidate_review_v193.py ver 1
"""Headless browser inventory, A/B working copies, study resume and safe saves."""
from __future__ import annotations
import json
from pathlib import Path
from dataclasses import replace
import pytest

from tools.sim_player.tests.test_optimizer_v192 import capture_file
from tools.sim_player.optimizer import read_input, optimise_group, write_index
from tools.sim_player.optimization_search import SearchOptions
from tools.sim_player.candidate_review import (
    discover_run_dirs, load_run_index, shared_captures, prepare_session,
    reopen_session, checked_selection,
)
from tools.sim_player.recording import load_recording
from tools.sim_player.variant_presets import candidate_entry
from sims.nolimits2.variant_store import VariantStore


@pytest.fixture
def result_run(tmp_path):
    src = capture_file(tmp_path / "source", n=95, spike=True)
    run = tmp_path / "studies" / "optimizer" / "run_20261106_094500"
    out = run / "chair_v3" / "blackbird"
    report = optimise_group([read_input(src)], "chair_v3", out,
                            SearchOptions(evaluations=230, sample_frames=24, alternatives=1),
                            progress=lambda *_: None)
    assert len(report["candidates"]) == 4
    write_index(run, [report])
    return run


def test_all_four_candidates_available_for_any_ab_pair(result_run):
    root = result_run.parent
    assert discover_run_dirs(root) == (result_run,)
    result = load_run_index(result_run)
    assert not result.issues
    assert [(c.target, c.rank) for c in result.candidates] == [
        ("strict", 1), ("strict", 2), ("relaxed5", 1), ("relaxed5", 2)]
    source = shared_captures(result.candidates[0], result.candidates[1])[0].source_id
    both_strict = prepare_session(result.candidates[0], result.candidates[1], source, result.root)
    both_relaxed = prepare_session(result.candidates[2], result.candidates[3], source, result.root)
    mixed = prepare_session(result.candidates[1], result.candidates[2], source, result.root)
    assert list(both_strict.slots) == list(both_relaxed.slots) == list(mixed.slots) == ["A", "B"]
    one = prepare_session(result.candidates[2], None, source, result.root)
    assert list(one.slots) == ["A"]


def test_working_copies_independent_and_reset_keeps_original(result_run, tmp_path):
    index = load_run_index(result_run)
    choices = index.candidates
    digest = shared_captures(choices[0], choices[-1])[0].source_id
    study = prepare_session(choices[0], choices[-1], digest, index.root)
    a, b = study.slots["A"], study.slots["B"]
    assert not a.modified and not b.modified
    orig_a = load_recording(a.csv_path, default_standard_variant=True)
    a.incorporate(orig_a)
    modified = load_recording(a.csv_path, platform=a.choice.platform,
        variant_snapshot=a.snapshot, variant_label=a.snapshot["label"], cue_mode=a.mode,
        gain_percent=(100, 85, 110, 125, 100, 50), global_settings=a.globals)
    a.incorporate(modified)
    assert a.modified and not b.modified
    assert b.gains == tuple(b.source_bundle["relative_gains_percent"])
    assert study.to_dict()["slots"]["A"]["gains"] == [100, 85, 110, 125, 100, 50]
    dest = tmp_path / "review.json"
    study.active = "B"
    dest.write_text(json.dumps(study.to_dict()))
    reopened = reopen_session(dest)
    assert reopened.active == "B"
    assert reopened.slots["A"].pending_override[1] == (100, 85, 110, 125, 100, 50)
    assert reopened.slots["B"].pending_override[1] == b.gains
    a.restore_original()
    assert not a.modified
    assert a.recording is orig_a


def test_selected_csv_is_validated_not_just_trusted_by_index(result_run):
    choice = load_run_index(result_run).candidates[0]
    digest = choice.captures[0].source_id
    assert checked_selection(choice, None, digest)
    with pytest.raises(ValueError, match="index and candidate sidecar"):
        checked_selection(replace(choice, target="relaxed5"), None, digest)
    path = choice.captures[0].csv_path
    path.write_text(path.read_text() + "\n")
    with pytest.raises(ValueError, match="changed"):
        checked_selection(choice, None, digest)


def test_mismatched_sources_cannot_be_compared(result_run):
    index = load_run_index(result_run)
    a, b = index.candidates[:2]
    different = replace(b, captures=(replace(b.captures[0], source_id="a"*64),))
    assert not shared_captures(a, different)
    with pytest.raises(ValueError, match="share this capture"):
        checked_selection(a, different, a.captures[0].source_id)


def test_atomic_two_candidate_catalogue_save_and_reject_duplicate(result_run, tmp_path):
    index = load_run_index(result_run)
    session = prepare_session(index.candidates[0], index.candidates[2],
                              index.candidates[0].captures[0].source_id, index.root)
    record = load_recording(session.slots["A"].csv_path, default_standard_variant=True)
    first = candidate_entry("Blackbird [Test A]", record)
    second = candidate_entry("Blackbird [Test B]", record)
    store = VariantStore.load("chair_v3", directory=tmp_path / "catalogue")
    assert store.add_candidates([first, second]) == (first["label"], second["label"])
    assert {"Blackbird [Test A]", "Blackbird [Test B]"}.issubset(set(store.player_labels("blackbird")))
    raw_before = store.path.read_bytes()
    with pytest.raises(ValueError):
        store.add_candidates([candidate_entry("Blackbird [Test C]", record), second])
    assert store.path.read_bytes() == raw_before


def test_original_optimiser_exports_remain_unmodified_after_review(result_run):
    index = load_run_index(result_run)
    choice = index.candidates[0]
    path = choice.captures[0].csv_path
    sidecar = path.with_name(path.stem + "_optimizer.json")
    before = path.read_bytes(), sidecar.read_bytes()
    s = prepare_session(choice, None, choice.captures[0].source_id, index.root)
    assert s.slots["A"].source_bundle["target"] == "strict"
    assert (path.read_bytes(), sidecar.read_bytes()) == before


def test_windows_style_index_relative_paths_and_traversal_rejection(result_run):
    import csv
    from tools.sim_player.candidate_review import _safe_candidate_path
    root = result_run
    index_path = root / "index.csv"
    with index_path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        fields = reader.fieldnames
    for row in rows:
        row["csv"] = row["csv"].replace("/", "\\")  # Native Windows pathlib output.
    with index_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    loaded = load_run_index(root)
    assert len(loaded.candidates) == 4
    assert not loaded.issues
    for name in ("..\\escape.csv", "C:\\Windows\\temp.csv", "\\\\host\\share\\a.csv", "../a.csv"):
        with pytest.raises(ValueError, match="relative"):
            _safe_candidate_path(root, name)


def test_real_ab_switch_logic_preserves_two_independent_working_edits(result_run):
    """Exercise actual mixin method bodies without requiring a Qt runtime."""
    import ast
    from types import SimpleNamespace
    source = Path(__file__).resolve().parents[1] / "candidate_workflow.py"
    tree = ast.parse(source.read_text())
    cls = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == "CandidateReviewMixin")
    # These methods have no direct Qt calls, only existing ReplayWindow methods.
    methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
    names = ("_activate_candidate", "_review_recording_applied", "reset_review_candidate")
    namespace = {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[methods[n] for n in names],
                                                 type_ignores=[])), str(source), "exec"), namespace)
    inventory = load_run_index(result_run)
    pair = (inventory.candidates[0], inventory.candidates[3])
    session = prepare_session(*pair, pair[0].captures[0].source_id, inventory.root)
    first = load_recording(session.slots["A"].csv_path, default_standard_variant=True)
    session.slots["A"].incorporate(first)

    class FakePlayer:
        def __init__(self):
            self._candidate_session = session
            self._candidate_switch_previous = None
            self.recording = first
            self.current_s = 2.75
            self._baseline_recording = first
            self.messages = []
            self._refresh_baseline_label = lambda: None
            self._update_tuning_feedback = lambda: None
            self._update_candidate_ui = lambda: None
            self._review_busy = lambda: False
            self._set_playing = lambda value: self.messages.append(("playing", value))
            for n in names:
                setattr(self, n, namespace[n].__get__(self))

        def _apply_reanalysis(self, recording):
            self.recording = recording
            self._review_recording_applied(recording)

        def _apply_experiment_values(self, cue, gains, *, variant_label, variant_snapshot, global_settings):
            calculated = load_recording(first.csv_path, platform="chair_v3",
                cue_mode=cue, gain_percent=gains, variant_label=variant_label,
                variant_snapshot=variant_snapshot, global_settings=global_settings)
            self._apply_reanalysis(calculated)

    player = FakePlayer()
    player._activate_candidate("B")
    assert player._candidate_session.active == "B"
    assert player.current_s == 2.75
    b_orig = player.recording
    snapshot = b_orig.variant_snapshot
    modified_gains = (110, 120, 95, 125, 110, 75)
    player._apply_experiment_values(b_orig.cue_mode, modified_gains,
                                    variant_label=b_orig.variant_label,
                                    variant_snapshot=snapshot,
                                    global_settings=b_orig.global_settings_snapshot)
    assert session.slots["B"].modified
    player._activate_candidate("A")
    assert player.recording is first
    assert player.current_s == 2.75
    assert not session.slots["A"].modified
    player._activate_candidate("B")
    assert player.recording.gain_percent == tuple(float(v) for v in modified_gains)
    player.reset_review_candidate()
    assert player.recording is b_orig
    assert not session.slots["B"].modified
