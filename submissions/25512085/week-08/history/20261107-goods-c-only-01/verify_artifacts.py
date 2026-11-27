"""Cross-check the final VM evidence and exact changed AST surface offline."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess

OUT = Path(__file__).resolve().parent
REPO = OUT.parents[4]
REL = 'submissions/25512085/week-08/'
revision = OUT/'revision02'
manifest = json.loads((revision/'manifest.json').read_text())
observation = json.loads((revision/'observe-final-stdout.log').read_text())
old_validator = (OUT.parent/'20261107-goods-terms-deploy-01/validate_remote.py').read_bytes().replace(b'\r\n', b'\n')
assert hashlib.sha256(old_validator).hexdigest() == observation['original_validator_sha256']
assert observation['source_sha256'] == manifest['source_sha256']
assert observation['candidate_sha256'] == manifest['after']
before = json.loads((OUT/'before.json').read_text())
for current in observation['processes']:
    old = next(row for row in before['processes'] if row['role'] == current['role'])
    assert all(old[key] == current[key] for key in ('role', 'pid', 'starttime', 'cwd'))

def definitions(blob):
    return {n.name: n for n in ast.parse(blob).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}

baseline = (OUT/'evidence/remote-baseline'/REL/'seller.py').read_bytes()
candidate = (revision/'seller.py').read_bytes()
old, new = definitions(baseline), definitions(candidate)
changed = [name for name in old if ast.dump(old[name]) != ast.dump(new[name])]
assert changed == ['_select_preferred_unit', 'Seller'], changed
assert set(new)-set(old) == {'goods_request_terms'}
for node in old['Seller'].body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name != 'execute':
        other = next(n for n in new['Seller'].body if getattr(n, 'name', None) == node.name)
        assert ast.dump(node) == ast.dump(other)
assert not any(word in candidate.decode() for word in ('pilot_A0', 'warranty_override', 'explicit_A72_choice', 'price_requested_unit'))
for path, digest in manifest['after'].items():
    committed = subprocess.check_output(['git', 'show', manifest['source']+':'+path], cwd=REPO)
    assert hashlib.sha256(committed).hexdigest() == digest
    assert (REPO/path).read_bytes().replace(b'\r\n', b'\n') == committed
report = dict(original_validator_hash_equal=True, production_allowlist_unchanged=56,
              final_candidate_matches_source_commit=manifest['source'], changed_definitions=changed,
              added_definitions=['goods_request_terms'], unchanged_Seller_methods=['__init__', 'cancel'],
              removed_A0_symbols=True, process_metadata_unchanged=True)
with (OUT/'artifact-review.json').open('x', encoding='utf-8') as stream:
    json.dump(report, stream, indent=2)
print(json.dumps(report))
