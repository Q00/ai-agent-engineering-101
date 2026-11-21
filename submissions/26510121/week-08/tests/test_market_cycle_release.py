"""Source promotion must refuse live leases and restore partial replacement."""
import hashlib
import json
from pathlib import Path
import pytest
import release_market_cycle as release


def setup_release(tmp_path,monkeypatch):
    root=tmp_path/'workspace';stage=root/'.market/stage';stage.mkdir(parents=True);(root/'logs').mkdir()
    names=['one.py','two.py']
    for name in names:(root/name).write_text('old');(stage/name).write_text('new')
    monkeypatch.setattr(release,'ROOT',root);monkeypatch.setattr(release,'STAGE',stage);monkeypatch.setattr(release,'FILES',names)
    manifest={'status':'verified_not_promoted','files':[{'path':name,
        'baseline_sha256':release.sha(root/name),'candidate_sha256':release.sha(stage/name)} for name in names]}
    path=root/'logs/manifest.json';path.write_text(json.dumps(manifest))
    monkeypatch.setattr('r3.lifecycle.market_active',lambda:False)
    return root,stage,path


def test_live_runtime_lease_blocks_any_source_write(tmp_path,monkeypatch):
    root,stage,path=setup_release(tmp_path,monkeypatch)
    monkeypatch.setattr('r3.lifecycle.market_active',lambda:True)
    with pytest.raises(RuntimeError,match='drain'):release.promote(path)
    assert (root/'one.py').read_text()=='old'


def test_candidate_tamper_is_rejected_before_replacement(tmp_path,monkeypatch):
    root,stage,path=setup_release(tmp_path,monkeypatch);(stage/'one.py').write_text('tampered')
    with pytest.raises(AssertionError,match='Candidate changed'):release.promote(path)
    assert (root/'one.py').read_text()=='old'


def test_partial_source_replacement_restores_old_files_without_touching_state(tmp_path,monkeypatch):
    root,stage,path=setup_release(tmp_path,monkeypatch)
    (root/'.market/ledger').write_text('preserved');(root/'shop_key.pem').write_text('synthetic fixture only')
    original=release.os.replace;calls=[]
    def replace(a,b):
        calls.append(b)
        if len(calls)==2:raise PermissionError('synthetic transient lock')
        return original(a,b)
    monkeypatch.setattr(release.os,'replace',replace)
    with pytest.raises(PermissionError):release.promote(path)
    assert all((root/name).read_text()=='old' for name in release.FILES)
    assert (root/'.market/ledger').read_text()=='preserved'
    assert (root/'shop_key.pem').read_text()=='synthetic fixture only'


def test_success_has_source_backup_and_exact_hash_match(tmp_path,monkeypatch):
    root,stage,path=setup_release(tmp_path,monkeypatch);release.promote(path)
    assert all(release.sha(root/name)==release.sha(stage/name) for name in release.FILES)
    backup=next((root/'.market').glob('v15-backup-*'))
    assert (backup/'one.py').read_text()=='old'
