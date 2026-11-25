"""Fault injection for source promotion; never touches the real running agent."""
import json
from pathlib import Path
import sys

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import release_v14 as release
import r3.lifecycle


def fixture(tmp_path, monkeypatch):
    stage = tmp_path / 'candidate'; stage.mkdir()
    (tmp_path / 'logs').mkdir(); (tmp_path / '.market').mkdir()
    names = ['one.py', 'two.py']
    for name in names:
        (tmp_path / name).write_text('old-' + name)
        (stage / name).write_text('new-' + name)
    (tmp_path / 'shop_key.pem').write_text('synthetic-identity-must-stay')
    (tmp_path / '.market/r3.sqlite3').write_bytes(b'synthetic-ledger-must-stay')
    entries = [{'path': name, 'baseline_sha256': release.sha(tmp_path / name),
                'candidate_sha256': release.sha(stage / name)} for name in names]
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'status': 'validated_candidate_not_deployed', 'files': entries}))
    monkeypatch.setattr(release, 'ROOT', tmp_path)
    monkeypatch.setattr(release, 'STAGE', stage)
    monkeypatch.setattr(release, 'FILES', names)
    monkeypatch.setattr(r3.lifecycle, 'market_active', lambda: False)
    return stage, manifest


def test_running_runtime_blocks_all_source_changes(tmp_path, monkeypatch):
    _, manifest = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(r3.lifecycle, 'market_active', lambda: True)
    with pytest.raises(RuntimeError, match='lease'):
        release.promote(manifest)
    assert (tmp_path / 'one.py').read_text() == 'old-one.py'


def test_candidate_tampering_cannot_be_promoted(tmp_path, monkeypatch):
    stage, manifest = fixture(tmp_path, monkeypatch)
    (stage / 'one.py').write_text('unvalidated-change')
    with pytest.raises(AssertionError, match='Candidate changed'):
        release.promote(manifest)
    assert (tmp_path / 'one.py').read_text() == 'old-one.py'


def test_partial_replace_failure_restores_prior_sources_and_keeps_identity(tmp_path, monkeypatch):
    _, manifest = fixture(tmp_path, monkeypatch)
    original = release.os.replace
    calls = []
    def replace(source, destination):
        calls.append(str(destination))
        if len(calls) == 2:
            raise PermissionError('synthetic deployment failure')
        original(source, destination)
    monkeypatch.setattr(release.os, 'replace', replace)
    with pytest.raises(PermissionError):
        release.promote(manifest)
    assert (tmp_path / 'one.py').read_text() == 'old-one.py'
    assert (tmp_path / 'two.py').read_text() == 'old-two.py'
    assert (tmp_path / 'shop_key.pem').read_text() == 'synthetic-identity-must-stay'
    assert (tmp_path / '.market/r3.sqlite3').read_bytes() == b'synthetic-ledger-must-stay'
    report = json.loads(next((tmp_path / 'logs').glob('v14-source-promotion-*.json')).read_text())
    assert report['status'] == 'source_replacement_failed_prior_files_restored'


def test_relative_manifest_and_successful_promotion_preserve_backup(tmp_path, monkeypatch):
    _, manifest = fixture(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    release.promote(Path(manifest.name))
    assert (tmp_path / 'one.py').read_text() == 'new-one.py'
    backup = next((tmp_path / '.market').glob('v14-release-*'))
    assert (backup / 'one.py').read_text() == 'old-one.py'
    assert not (backup / 'shop_key.pem').exists()
