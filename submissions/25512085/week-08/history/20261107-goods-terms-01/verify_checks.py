"""Syntax in the synthetic snapshot, and separate source/raw-log whitespace checks."""
from pathlib import Path
import py_compile
import subprocess
import sys

OUT = Path(__file__).resolve().parent
BASE = OUT.parents[1]
REPO = BASE.parents[2]

if __name__ == '__main__':
    label = sys.argv[1] if len(sys.argv) > 1 else 'final05'
    compiled = OUT/('syntax-'+label)
    compiled.mkdir(exist_ok=False)
    tree = OUT/'evidence'/label/'submissions/25512085/week-08'
    with (OUT/(label+'-syntax-diff.log')).open('x', encoding='utf-8') as log:
        def report(text):
            print(text)
            log.write(text+'\n')
        for name in ('seller.py', 'test_goods_terms_pilot_offline.py'):
            py_compile.compile(str(tree/name), cfile=str(compiled/(name+'c')), doraise=True)
            report('py_compile '+name+': PASS (isolated snapshot/output)')
        files = ['seller.py', 'test_goods_terms_pilot_offline.py', 'POLICY.md', 'GRADE_WARRANTY_POLICY.md',
                 'history/20261107-goods-terms-01/check.py', 'history/20261107-goods-terms-01/run_isolated.py',
                 'history/20261107-goods-terms-01/verify_checks.py']
        command = ['git', 'diff', '92729a6^', '--check', '--']+[str(BASE/name) for name in files]
        p = subprocess.run(command, cwd=REPO, text=True, capture_output=True)
        report('git diff 92729a6^ --check -- <changed source/docs/runner>: exit '+str(p.returncode))
        report(p.stdout or '(no whitespace errors)')
        if p.returncode:
            raise SystemExit(p.returncode)
        p = subprocess.run(['git', 'diff', '92729a6^', '--check'], cwd=REPO, text=True, capture_output=True)
        report('git diff 92729a6^ --check (including immutable raw logs): exit '+str(p.returncode))
        report(p.stdout or '(no whitespace errors)')
