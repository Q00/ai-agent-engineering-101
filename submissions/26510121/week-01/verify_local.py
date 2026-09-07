"""Offline checks only; does not call a model or use an API key."""
import ast
import pathlib
import subprocess
import sys
import tempfile
import os

root = pathlib.Path(__file__).resolve().parent
source = (root / 'first_agent.py').read_text(encoding='utf-8')
tree = ast.parse(source)
ast.parse((root / 'capture_run.py').read_text(encoding='utf-8'))
# Execute only the tool definition, avoiding API client imports and calls.
definition = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'write_note')
with tempfile.TemporaryDirectory(dir=root, prefix='offline-check-') as directory:
    namespace = {'Path': pathlib.Path, '__file__': str(pathlib.Path(directory) / 'agent.py')}
    exec(compile(ast.Module(body=[definition], type_ignores=[]), '<tool>', 'exec'), namespace)
    write = namespace['write_note']
    target = pathlib.Path(directory) / 'settlement.txt'
    assert write(' ').startswith('error:') and not target.exists()
    assert write('first').startswith('saved:')
    sample = '\uc815\uc0b0 77\u202f600\uc6d0'
    assert write(sample).startswith('saved:')
    assert target.read_text(encoding='utf-8') == 'first\n' + sample + '\n'
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    captured = pathlib.Path(directory) / 'capture.txt'
    with captured.open('wb') as output:
        subprocess.run([sys.executable, '-c', 'print(' + ascii(sample) + ')'],
                       env=env, stdout=output, check=True)
    assert captured.read_text(encoding='utf-8').strip() == sample
print('PASS: syntax, empty-input rejection, append preservation, Korean/U+202F UTF-8 file and capture round trip.')
print('Offline checks only. Model tool selection and actual model-driven saving remain untested.')
