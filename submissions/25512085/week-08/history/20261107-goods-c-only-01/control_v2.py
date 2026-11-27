"""Revision02 reuses only this session's non-activation source-only control functions."""
import ast
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
REV = HERE/'revision02'
spec = importlib.util.spec_from_file_location('c_only_control', HERE/'control.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)
control.REMOTE = control.REMOTE.replace('c-only-runtime-01', 'c-only-runtime-02')


def prepare():
    REV.mkdir(exist_ok=False)
    def exclusive_write(name, data):
        with (REV/name).open('xb') as stream:
            stream.write(data)
    control.write = exclusive_write
    control.prepare()  # Fresh git source; uses preserved live-inspection source baseline.
    prior = (HERE/'seller.py').read_bytes()
    candidate = (REV/'seller.py').read_bytes()
    def refs(blob):
        return sorted({n.attr for n in ast.walk(ast.parse(blob)) if isinstance(n, ast.Attribute)
                       and isinstance(n.value, ast.Name) and n.value.id == 'cash_policy'})
    assert refs(prior) == refs(candidate), 'dependency_reference_drift'
    evidence = json.loads((HERE/'dependency-equivalence.json').read_text())
    evidence['revision02_cash_policy_references_equal'] = refs(candidate)
    exclusive_write('dependency-equivalence.json', json.dumps(evidence, indent=2).encode())
    exclusive_write('run_isolated.py', (HERE/'run_isolated.py').read_bytes())
    validator = (HERE/'validate_remote.py').read_text()
    # Preserve OLD=...runtime-01 as the reviewed original validator source. Only
    # its transformed target changes from revision01 to revision02.
    validator = validator.replace("'20261107-goods-c-only-runtime-01'", "'20261107-goods-c-only-runtime-02'")
    exclusive_write('validate_remote.py', validator.encode())
    exclusive_write('before.json', (HERE/'before.json').read_bytes())
    print('revision02_prepared_source_only')


if __name__ == '__main__':
    action = sys.argv[1]
    if action == 'prepare':
        prepare()
    else:
        control.OUT = REV
        if action == 'stage':
            control.stage()
        elif action == 'validate':
            print(control.ssh((REV/'validate_remote.py').read_text(), 'validate').decode())
        elif action == 'export':
            control.export()
        elif action == 'observe-final':
            print(control.ssh((HERE/'observe_final.py').read_text(), 'observe-final').decode())
        else:
            raise ValueError('Only prepare/stage/validate/export/observe-final allowed; no activation')
