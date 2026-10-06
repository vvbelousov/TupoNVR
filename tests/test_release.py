"""Publication guards and workflow structure; no registry/account access."""
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('release_check', ROOT / 'scripts/release_check.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def settings(**overrides):
    return dict(tag='v0.1.0', prerelease=False, version='0.1.0', username='example',
                image='example/tuponvr', platforms='linux/amd64', license_id='MIT', has_license=True,
                **overrides)


def test_release_stable_and_prerelease():
    assert release.validate(**settings()) == {'version': '0.1.0', 'stable': 'true', 'platforms': 'linux/amd64', 'license': 'MIT'}
    values = settings()
    values.update(tag='v0.1.0-rc.1', version='0.1.0-rc.1', prerelease=True,
                  platforms='linux/amd64,linux/arm64')
    assert release.validate(**values)['stable'] == 'false'


def test_old_releases_and_prereleases_do_not_move_stable_aliases():
    assert release.eligible_aliases(False, '42', 42)
    assert not release.eligible_aliases(False, '41', 42)
    assert not release.eligible_aliases(True, '42', 42)


@pytest.mark.parametrize('changes', [
    {'tag': 'v0.2.0'}, {'tag': 'v0.01.0', 'version': '0.01.0'},
    {'tag': 'v0.1.0-rc.1', 'version': '0.1.0-rc.1'}, {'prerelease': True},
    {'tag': 'v0.1.0-01', 'version': '0.1.0-01', 'prerelease': True},
    {'has_license': False}, {'license_id': ''}, {'username': ''},
    {'image': 'example/tuponvr:latest'}, {'image': 'example/TupoNVR'},
    {'platforms': 'linux/amd64,linux/arm/v7'},
])
def test_release_rejects_unsafe_or_inconsistent_metadata(changes):
    values = settings()
    values.update(changes)
    with pytest.raises(ValueError):
        release.validate(**values)


def test_workflows_separate_ci_and_protected_publication():
    ci = yaml.load((ROOT / '.github/workflows/ci.yml').read_text(), Loader=yaml.BaseLoader)
    publish = yaml.load((ROOT / '.github/workflows/release.yml').read_text(), Loader=yaml.BaseLoader)
    assert ci['on']['push']['branches'] == ['main'] and 'pull_request' in ci['on']
    assert ci['permissions'] == publish['permissions'] == {'contents': 'read'}
    assert set(publish['on']) == {'release'}
    assert publish['on']['release']['types'] == ['published']
    assert publish['jobs']['publish']['environment'] == 'dockerhub'
    assert set(publish['jobs']['publish']['needs']) == {'verify', 'checks'}
    assert 'DOCKERHUB_TOKEN' not in (ROOT / '.github/workflows/ci.yml').read_text()
    matrix = ci['jobs']['docker']['strategy']['matrix']['include']
    assert matrix == [{'arch': 'amd64', 'runner': 'ubuntu-24.04'}, {'arch': 'arm64', 'runner': 'ubuntu-24.04-arm'}]
    for step in ci['jobs']['docker']['steps']:
        if step.get('uses', '').startswith('docker/build-push-action@'):
            assert step['with']['push'] == 'false'
    steps = publish['jobs']['publish']['steps']
    build = next(step['with'] for step in steps if step.get('uses', '').startswith('docker/build-push-action@'))
    meta = next(step['with'] for step in steps if step.get('uses', '').startswith('docker/metadata-action@'))
    assert build['sbom'] == 'true' and build['provenance'] == 'mode=max'
    assert meta['flavor'] == 'latest=false'
    assert 'pattern={{major}}\n' not in meta['tags']
    assert "outputs.aliases == 'true'" in meta['tags']


def test_application_version_matches_manifest():
    version_spec = importlib.util.spec_from_file_location('application_version', ROOT / 'backend/version.py')
    version = importlib.util.module_from_spec(version_spec)
    version_spec.loader.exec_module(version)
    manifest = json.loads((ROOT / 'frontend/package.json').read_text())
    lock = json.loads((ROOT / 'frontend/package-lock.json').read_text())
    assert version.VERSION == manifest['version'] == lock['version'] == lock['packages']['']['version']
    assert manifest['license'] == lock['packages']['']['license'] == 'Apache-2.0'


def test_community_yaml_parses():
    for path in (ROOT / '.github').rglob('*.yml'):
        assert isinstance(yaml.load(path.read_text(), Loader=yaml.BaseLoader), dict), path
