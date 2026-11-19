#test_dynamic_candidate_import_v202.py ver 2
"""No-hardware exporter/importer and qualification-envelope acceptance tests."""
from __future__ import annotations
import json
from pathlib import Path
import shutil
from zipfile import ZipFile, ZIP_DEFLATED

import pytest

from core.dynamic_profiles import DynamicDeployment, DynamicProfileError
from core.dynamic_qualification import QualificationError, verify_evidence
from core.dynamic_import import import_candidate
from tools.nl2_dynamic_profiles.package import prepare_bundle
from tools.nl2_sync.profile import DynamicProfile
from sims.nolimits2.variant_store import VariantStore
from sims.nolimits2 import ride_config
from tests.test_dynamic_runtime_v201 import _fixture


@pytest.mark.parametrize('platform', ('chair_v3', 'platform_v2'))
@pytest.mark.parametrize('park', ('blackbird', 'mdxlogo', 'fenrir'))
def test_analyzer_package_poc_import_roundtrip(tmp_path, platform, park):
    root, source, catalog, globals = _fixture(tmp_path, platform, park)
    title = ride_config.PARKS[park]['name'] + ' [D202]'
    export, rel, summary = prepare_bundle(source, title, export_dir=tmp_path / 'exports', token='aabbccddeeff')
    assert export.is_file()
    assert summary['source_recordings'] == 2
    assert summary['status'] == 'offline_qualified_not_physically_commissioned'
    with ZipFile(export) as z:
        names = z.namelist()
        assert len(names) == 5
        assert 'dynamic_candidate.json' in names
        assert 'sims/nolimits2/ride_profiles/' + platform + '/' + park + '/aabbccddeeff/qualification.json' in names
    label, installed_rel = import_candidate(export, platform_key=platform, root=root, catalog_dir=catalog)
    assert (label, installed_rel) == (title, rel)
    store = VariantStore.load(platform, directory=catalog)
    snap = store.snapshot(title)
    dep = DynamicDeployment.load(label=title, relative=rel, platform=platform, park=park,
           live_variant=snap, global_settings=globals, root=root)
    assert dep.profile.identity['platform'] == platform
    with pytest.raises(DynamicProfileError, match='already exists'):
        import_candidate(export, platform_key=platform, root=root, catalog_dir=catalog)


def _rewrite_zip(original, replacement_path, mutate):
    with ZipFile(original) as z:
        contents={i.filename: z.read(i.filename) for i in z.infolist()}
    mutate(contents)
    with ZipFile(replacement_path, 'w', compression=ZIP_DEFLATED) as z:
        for name, raw in contents.items():
            z.writestr(name,raw)


@pytest.mark.parametrize('kind', ('profile_corrupt','qualification_missing','manifest_tampered','zip_traversal'))
def test_bad_package_cannot_change_catalogue(tmp_path,kind):
    root, source, catalog, _ = _fixture(tmp_path, 'chair_v3', 'blackbird')
    package, rel, _ = prepare_bundle(source, 'Blackbird [Qual]', export_dir=tmp_path / 'exports',token='aabbccddeeff')
    old = (catalog/'chair_v3.json').read_bytes()
    bad = tmp_path/'bad.zip'
    def change(data):
        if kind == 'profile_corrupt':
            key = next(k for k in data if k.endswith('/profile.json'))
            data[key] = b'bad-profile'
        elif kind == 'qualification_missing':
            data.pop(next(k for k in data if k.endswith('/qualification.json')))
        elif kind == 'manifest_tampered':
            data['dynamic_candidate.json'] = b'{}'
        else:
            data['../../inject.py'] = b'bad'
    _rewrite_zip(package,bad,change)
    with pytest.raises((DynamicProfileError,ValueError)):
        import_candidate(bad,platform_key='chair_v3',root=root,catalog_dir=catalog)
    assert (catalog/'chair_v3.json').read_bytes() == old
    assert not (root/'sims/nolimits2/ride_profiles').exists()


def test_wrong_platform_does_not_mutate(tmp_path):
    root, source, catalog, _ = _fixture(tmp_path, 'chair_v3', 'blackbird')
    package, _, _ = prepare_bundle(source,'Blackbird [Wrong]',export_dir=tmp_path / 'exports',token='aabbccddeeff')
    original=(catalog/'chair_v3.json').read_bytes()
    with pytest.raises(DynamicProfileError,match='another physical platform'):
        import_candidate(package,platform_key='platform_v2',root=root,catalog_dir=catalog)
    assert (catalog/'chair_v3.json').read_bytes()==original


def test_five_percent_limit_and_floor_requirements(tmp_path):
    root, source, cat, _ = _fixture(tmp_path,'platform_v2','blackbird')
    profile=DynamicProfile.load(source/'profile.json')
    data=json.loads((source/'validation.json').read_text())
    # Nominal strict records must not exceed their declared range.
    data['nominal'][0]['maximum_requested_excess_mm']=1
    with pytest.raises(QualificationError, match='5% envelope'):
        verify_evidence(profile,data)
    data=json.loads((source/'validation.json').read_text())
    data['nominal'][0]['minimum_requested_floor_mm']=5
    with pytest.raises(QualificationError, match='floor-clearance'):
        verify_evidence(profile,data)


def test_relaxed_still_within_five_percent(tmp_path):
    _, source, _, _ = _fixture(tmp_path, 'chair_v3', 'mdxlogo')
    data=json.loads((source/'validation.json').read_text())
    obj=json.loads((source/'profile.json').read_text())
    obj['metadata']['target']='relaxed5'
    from tools.nl2_sync.profile import DynamicProfile
    profile=DynamicProfile.load(source/'profile.json')
    profile.metadata['target']='relaxed5'
    data['nominal'][0]['maximum_requested_excess_mm']=9
    data['nominal'][0]['allowance_mm']=10
    assert verify_evidence(profile,data)['maximum_requested_excess_mm']==9
    data['nominal'][0]['maximum_requested_excess_mm']=10.01
    with pytest.raises(QualificationError, match='5% envelope'):
        verify_evidence(profile,data)
