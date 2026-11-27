"""Local syntax, parser-only AST delta, whitespace and final VM evidence checks."""
import ast
import hashlib
import json
from pathlib import Path
import py_compile
import subprocess
import sys

OUT = Path(__file__).resolve().parent
BASE = OUT.parents[1]
REPO = BASE.parents[2]
REL = 'submissions/25512085/week-08/'
label = sys.argv[1]
dest = OUT/'evidence'/('syntax-'+label)
dest.mkdir(parents=True, exist_ok=False)
paths = [BASE/'seller.py', BASE/'test_goods_terms_pilot_offline.py'] + list(OUT.glob('*.py'))
for path in paths:
    py_compile.compile(str(path), cfile=str(dest/(path.name+'c')), doraise=True)

def definitions(blob):
    return {node.name: node for node in ast.parse(blob).body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}

old = definitions(subprocess.check_output(['git', 'show', '72afb71:'+REL+'seller.py'], cwd=REPO))
new = definitions((BASE/'seller.py').read_bytes())
assert set(old) == set(new)
changed = [name for name in old if ast.dump(old[name]) != ast.dump(new[name])]
assert changed == ['goods_request_terms'], changed
files = [str(path.relative_to(REPO)).replace('\\', '/') for path in paths]
files += [REL+name for name in ('POLICY.md', 'GRADE_WARRANTY_POLICY.md')]
diff = subprocess.run(['git', 'diff', '72afb71', '--check', '--', *files], cwd=REPO,
                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
assert diff.returncode == 0, diff.stdout.decode()
report = dict(py_compile_files=len(paths), only_changed_definition=changed,
              pricing_and_other_definitions_identical=True, diff_check_exit=diff.returncode)
if label == 'final':
    manifest = json.loads((OUT/'manifest.json').read_text())
    observation = json.loads((OUT/'observe-final-stdout.log').read_text())
    assert observation['source_sha256'] == manifest['source_sha256']
    assert observation['candidate_sha256'] == manifest['after']
    before = json.loads((OUT/'before.json').read_text())
    for row in observation['processes']:
        prior = next(p for p in before['processes'] if p['role'] == row['role'])
        assert all(prior[key] == row[key] for key in ('pid', 'starttime', 'cwd'))
    for path, digest in manifest['after'].items():
        blob = subprocess.check_output(['git', 'show', manifest['source']+':'+path], cwd=REPO)
        assert hashlib.sha256(blob).hexdigest() == digest
        assert (REPO/path).read_bytes().replace(b'\r\n', b'\n') == blob
    tests = json.loads((OUT/'remote-evidence/remote-tests.json').read_text())
    assert tests['reports']['sandbox-focused']['success']
    assert tests['reports']['sandbox-all']['success']
    report.update(production_allowlist_unchanged=len(manifest['source_sha256']),
                  source_commit=manifest['source'], candidate_sha256=manifest['after'],
                  process_metadata_unchanged=True)
with (OUT/('verify-'+label+'.json')).open('x', encoding='utf-8') as stream:
    json.dump(report, stream, indent=2)
print(json.dumps(report))
