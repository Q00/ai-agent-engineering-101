"""Revision03 source-only candidate. No production replacement/restart action."""
import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
OLD = HERE.parent/'20261107-goods-c-only-01'
spec = importlib.util.spec_from_file_location('reviewed_c_only_control', OLD/'control.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)
control.OUT = HERE
control.REMOTE = control.REMOTE.replace('c-only-runtime-01', 'c-only-runtime-03')

if __name__ == '__main__':
    action = sys.argv[1]
    if action in ('inspect', 'prepare', 'dependencies', 'stage', 'export'):
        getattr(control, action)()
    elif action == 'validate':
        print(control.ssh((HERE/'validate_remote.py').read_text(), 'validate').decode())
    elif action == 'observe-final':
        code = (OLD/'observe_final.py').read_text().replace('c-only-runtime-02', 'c-only-runtime-03')
        print(control.ssh(code, 'observe-final').decode())
    else:
        raise ValueError('No activation/restart action exists')
