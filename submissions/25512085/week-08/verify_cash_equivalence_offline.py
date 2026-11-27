"""Reproduce the original cash-refactor AST and 2,160-result comparison.

Run: python -B submissions/25512085/week-08/verify_cash_equivalence_offline.py
Requires the baseline Git object 14acc74 locally; never fetches it. Git is used
only to read that source. Inputs below are synthetic, not market/state files.
Only a new exclusive log is written; no bytecode, key, market or state I/O.
"""
import ast
import contextlib
import copy
from datetime import datetime
from pathlib import Path
import subprocess
import sys
from types import ModuleType
from unittest.mock import patch


BASE = Path(__file__).resolve().parent
BASELINE = '14acc74'
INTERNAL = ('_cash_risk.py', '_cash_pricing.py', '_cash_demand.py', '_cash_forum.py')


class Normalize(ast.NodeTransformer):
    def visit_Attribute(self, node):
        if isinstance(node.value, ast.Name) and node.value.id == 'policy':
            return ast.copy_location(ast.Name(id=node.attr, ctx=node.ctx), node)
        return self.generic_visit(node)


def verify(source):
    original = ast.parse(source)
    old_functions = {n.name:n for n in original.body if isinstance(n, ast.FunctionDef)}
    moved = 0
    for name in INTERNAL:
        path = BASE / name
        text = path.read_text(encoding='utf-8')
        compile(text, str(path), 'exec')
        for fn in ast.parse(text).body:
            if not isinstance(fn, ast.FunctionDef):
                continue
            normalized = Normalize().visit(copy.deepcopy(fn))
            assert normalized.args.args.pop(0).arg == 'policy'
            assert ast.dump(normalized) == ast.dump(old_functions[fn.name]), fn.name
            print(f'Identical AST after dependency qualification only: {fn.name}')
            moved += 1
    assert moved == 10, moved
    import seller_cash_policy as new
    old = ModuleType('baseline_cash_policy')
    old.__file__ = str(BASE / 'seller_cash_policy.py')
    exec(compile(source, f'<baseline {BASELINE}>', 'exec'), old.__dict__)
    checks = 0
    now = 1800000000
    sku = 'G00000001'
    catalog = {sku:{'kind':'goods','list_price':100000,'category':'beds'}}
    with patch.object(old, 'config', return_value={'enabled':True}), \
            patch.object(new, 'config', return_value={'enabled':True}):
        for kind in ('goods', 'food'):
            for grade in ('A', 'B', 'C'):
                for age in (0, 60, 28799, 28800, 43200):
                    for cost in (500, 10000, 130000):
                        for hours in (0, 6, 72):
                            unit = {'sku':sku,'kind':kind,'grade':grade,'cost':cost,
                                    'supplied_at':now-age,'hours_left':72,'cert':True}
                            hub = {'demand_radar':{'sku_comparables':{sku:[
                                {'grade':grade,'ts':now-1,'amount':80000,'warranty_hours':hours}]*5}}}
                            for fn, args in (
                                ('quote',(unit,hub,catalog,now)),
                                ('profit_floor',(unit,hub,catalog,now)),
                                ('reference_price',(hub,sku,grade,now,kind,hours)),
                                ('refund_probability',(hub,sku,grade,catalog,now)),
                                ('procurement',(hub,sku,catalog[sku],{},20,catalog,now)),
                                ('forecast',(hub,sku,catalog[sku],catalog,now)),
                                ('spending_budget',(hub,{'balance':30000000},1500000,now)),
                                ('availability_plan',([unit],hub,catalog,now)),
                            ):
                                assert getattr(old,fn)(*copy.deepcopy(args)) == getattr(new,fn)(*copy.deepcopy(args)), (fn,kind,grade,age,cost,hours)
                                checks += 1
    assert checks == 2160, checks
    syntax_files = INTERNAL + ('seller_cash_policy.py', 'test_cash_policy_modules_offline.py',
                              'verify_cash_refactor_offline.py', 'test_verify_cash_refactor_offline.py',
                              'verify_cash_equivalence_offline.py')
    for name in syntax_files:
        path = BASE / name
        compile(path.read_text(encoding='utf-8'), str(path), 'exec')
    print(f'PASS: {moved} moved functions retain identical normalized AST; {checks} synthetic baseline/after results match.')
    print(f'PASS: syntax compile() for {len(syntax_files)} Python files, no bytecode writes.')


def main():
    # The sole subprocess is this fixed, local, read-only Git command.
    source = subprocess.check_output(
        ['git', 'show', f'{BASELINE}:submissions/25512085/week-08/seller_cash_policy.py'],
        cwd=BASE).decode('utf-8')
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    log = BASE / 'logs' / f'cash-refactor-equivalence-{stamp}.log'
    with log.open('x', encoding='utf-8') as output:
        def guard(event, args):
            if event in ('socket.connect', 'socket.getaddrinfo', 'subprocess.Popen'):
                raise AssertionError(f'external I/O forbidden: {event}')
            if event == 'open' and isinstance(args[0], (str, bytes)):
                path = Path(args[0]).resolve()
                mode, flags = args[1:3]
                if (mode is not None and any(c in mode for c in 'wax+')) or flags & 0x703:
                    raise AssertionError(f'file write forbidden: {path}')
                if path.is_relative_to(BASE) and (
                        '.market' in path.parts or path.suffix == '.pem'
                        or path.name in ('state_hub.json', 'seller_history.json')
                        or 'logs' in path.parts or 'history' in path.parts):
                    raise AssertionError(f'original data read forbidden: {path}')

        sys.addaudithook(guard)
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            print('Command: python -B verify_cash_equivalence_offline.py')
            print(f'Baseline: {BASELINE}; local git show only, no market execution')
            verify(source)
    print(log)


if __name__ == '__main__':
    main()
