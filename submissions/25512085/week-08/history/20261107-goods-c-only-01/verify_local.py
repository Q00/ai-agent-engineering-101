"""Syntax, exact price-logic AST and limited source/doc whitespace verification."""
import ast
import json
from pathlib import Path
import py_compile
import subprocess
import sys

OUT = Path(__file__).resolve().parent
BASE = OUT.parents[1]
REPO = BASE.parents[2]
label = sys.argv[1] if len(sys.argv) > 1 else 'final'
dest = OUT/('evidence/syntax-'+label)
dest.mkdir(parents=True, exist_ok=False)
paths = [BASE/'seller.py', BASE/'test_goods_terms_pilot_offline.py',
         OUT/'check.py', OUT/'run_isolated.py', OUT/'control.py',
         OUT/'validate_remote.py', OUT/'verify_local.py', OUT/'control_v2.py', OUT/'observe_final.py',
         OUT/'verify_artifacts.py']
for path in paths:
    py_compile.compile(str(path), cfile=str(dest/(path.name+'c')), doraise=True)
old = subprocess.check_output(['git', 'show', '92729a6^:submissions/25512085/week-08/seller.py'], cwd=REPO)
def pricing(blob):
    return next(n for n in ast.parse(blob).body if isinstance(n, ast.FunctionDef)
                and n.name == 'compute_unit_pricing_and_terms')
assert ast.dump(pricing(old)) == ast.dump(pricing((BASE/'seller.py').read_bytes()))
files = [str(p.relative_to(REPO)).replace('\\', '/') for p in paths]
files += ['submissions/25512085/week-08/'+n for n in ('POLICY.md', 'GRADE_WARRANTY_POLICY.md')]
result = subprocess.run(['git', 'diff', '01f4be7', '--check', '--', *files], cwd=REPO,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
report = dict(py_compile_files=len(paths), pricing_AST_equal=True,
              diff_check_exit=result.returncode, diff_output=result.stdout.decode())
with (OUT/('verify-local-'+label+'.json')).open('x', encoding='utf-8') as stream:
    json.dump(report, stream, indent=2)
print(json.dumps(report))
assert result.returncode == 0
