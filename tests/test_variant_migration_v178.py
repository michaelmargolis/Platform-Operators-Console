# test_variant_migration_v178.py ver 1
"""Migration is non-executing, explicit, platform-local and non-overwriting."""
import ast
from pathlib import Path
import pytest

from tools.migrate_nl2_variants import migrate, read_legacy_variants, main
from sims.nolimits2.variant_store import VariantStore, VariantConflictError
from tests.test_variant_store_v178 import document, writable


def legacy(tmp_path, content=None):
    path = tmp_path / 'old_ride_config.py'
    path.write_text(content or "RIDE_VARIANTS = ({'label': 'Blackbird [Custom]', 'park': 'blackbird', 'gain_scale_pct': {'roll_gain': 125}},)\n")
    return path


def test_migration_creates_independent_platform_files_preserves_tuning(tmp_path):
    source = legacy(tmp_path)
    original = source.read_bytes()
    paths = migrate(source, ['platform_v2', 'chair_v3'], directory=tmp_path/'catalogs')
    assert [p.name for p in paths] == ['platform_v2.json', 'chair_v3.json']
    a = VariantStore.load('platform_v2', directory=tmp_path/'catalogs')
    b = VariantStore.load('chair_v3', directory=tmp_path/'catalogs')
    assert a.snapshot('Blackbird [Custom]')['gain_scale_pct']['roll'] == 125
    a.review('Blackbird [Custom]', 'rejected')
    assert b.visible_labels() == ('Blackbird [Custom]',)
    assert source.read_bytes() == original


def test_migration_never_executes_python(tmp_path):
    source = legacy(tmp_path, "raise RuntimeError('must not execute')\nRIDE_VARIANTS = [{'label': 'Blackbird', 'park': 'blackbird'}]\n")
    assert read_legacy_variants(source)[0]['label'] == 'Blackbird'


@pytest.mark.parametrize('content', [
    "RIDE_VARIANTS = make_variants()\n",
    "RIDE_VARIANTS = []\nRIDE_VARIANTS = []\n",
    "PARKS = {}\n",
    "RIDE_VARIANTS = ['Blackbird']\n",
])
def test_ambiguous_or_programmatic_legacy_definition_requires_manual_conversion(tmp_path, content):
    with pytest.raises(ValueError):
        read_legacy_variants(legacy(tmp_path, content))


def test_migration_preflight_refuses_existing_without_partial_write(tmp_path):
    directory=tmp_path/'catalogs';directory.mkdir()
    b=writable(directory, 'chair_v3')
    before=b.path.read_bytes()
    with pytest.raises(ValueError,match='not be overwritten'):
        migrate(legacy(tmp_path), ['platform_v2','chair_v3'],directory=directory)
    assert not (directory/'platform_v2.json').exists()
    assert b.path.read_bytes()==before


def test_explicit_creation_never_overwrites(tmp_path):
    store=VariantStore.create('platform_v2',document(),directory=tmp_path)
    before=store.path.read_bytes()
    with pytest.raises(VariantConflictError):
        VariantStore.create('platform_v2',document(),directory=tmp_path)
    assert before==store.path.read_bytes()


def test_migration_cli_reports_error_and_success(tmp_path, capsys):
    source=legacy(tmp_path)
    args=[str(source),'--platform','platform_v2','--directory',str(tmp_path/'catalogs')]
    assert main(args)==0
    assert main(args)==1
    assert 'will not be overwritten' in capsys.readouterr().err


def test_invalid_startup_catalog_errors_are_caught_before_output():
    root=Path(__file__).resolve().parents[1]
    tree=ast.parse((root/'poc.py').read_text())
    main_node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
    assert any(isinstance(n,ast.Try) and 'core = PocCore(config)' in ast.unparse(n) and
               'POC Configuration Error' in ast.unparse(n) for n in main_node.body)
    core_text=(root/'core/poc_core.py').read_text()
    assert core_text.index('self._startup_variant_store.validate_display()') < core_text.index('platform_backend = SopPlatformBackend(')
