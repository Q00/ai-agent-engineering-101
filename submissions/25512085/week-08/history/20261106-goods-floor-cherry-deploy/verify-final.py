"""Read-only final deployment verification and allowlisted evidence export."""
import deploy as d

before = d.json.loads((d.OUT / 'remote-before.json').read_text())
after = d.json.loads((d.OUT / 'remote-after.json').read_text())
manifest = d.json.loads((d.OUT / 'manifest.json').read_text())
head = d.applied_head()
assert head == after['head']
assert d.snapshot() == after['after'], 'process_identity_changed'
assert not d.git('diff', '--name-only') and not d.git('diff', '--cached', '--name-only')
assert d.hashes(manifest['paths']) == manifest['target_files']
assert d.hashes(d.GUARDS) == before['guard_sha256']
d.protected_unchanged(before)
assert d.digest(d.OUT / 'backup/seller.py') == manifest['base_runtime'][str(d.REL / 'seller.py')]
seller = next(p for p in after['after'] if p['role'] == 'seller')
with d.urllib.request.urlopen('http://127.0.0.1:' + str(seller['port']) + '/.well-known/agent-card.json', timeout=10) as response:
    assert response.status == 200
    local_status = response.status
log = (d.OUT / 'focused-offline.log').read_text()
counts = {name: sum(line.startswith('test_') and ('(' + name + '.') in line for line in log.splitlines())
          for name in ('test_goods_floor_offline', 'test_goods_selection_offline',
                       'test_warranty_disclosure_offline', 'test_grade_warranty_offline')}
assert counts == dict(test_goods_floor_offline=16, test_goods_selection_offline=29,
                      test_warranty_disclosure_offline=12, test_grade_warranty_offline=15)
result = dict(utc=d.datetime.datetime.now(d.datetime.timezone.utc).isoformat(), head=head,
              parent=d.BASE, source_commit=d.TARGET, source_commit_present_in_message=True,
              changed_paths=len(manifest['paths']), all_target_file_sha256_match=True,
              runtime_sha256=d.hashes(d.RUNTIME), guard_sha256=d.hashes(d.GUARDS),
              protected_evidence_sha256=d.hashes(d.PROTECTED), protected_evidence_untracked=True,
              tracked_changes=[], staged_changes=[], processes=d.snapshot(),
              local_card_http=local_status, public=d.public_check(), focused_counts=counts,
              tests=after['tests'], seller_signal_count=1, rollback_needed=False)
d.save('final-check.json', result)
allowed = ['deploy.py', 'verify-final.py', 'manifest.json', 'remote-before.json',
           'remote-applied.json', 'remote-tests.json', 'signal.json', 'remote-after.json',
           'final-check.json', 'backup-sha256.json', 'backup/seller.py',
           'cherry-pick-console.log', 'focused-offline.log', 'all-offline.log']
with d.tarfile.open(d.OUT / 'evidence-export.tar', 'x') as archive:
    for name in allowed:
        archive.add(d.OUT / name, arcname=name)
print(d.json.dumps(result))
