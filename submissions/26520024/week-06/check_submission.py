"""Run the unchanged course checker on a byte-identical source/log export.

The course checker recursively enumerates ignored runtime caches before filtering
them. Exclude only generated runtimes/caches here, as a clean checkout does.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
EXCLUDED = {'.venv', '.runtime', '.uv-cache', '__pycache__'}


def check():
    with tempfile.TemporaryDirectory(prefix='week06-submission-check-') as temp:
        destination = Path(temp) / 'week-06'
        destination.mkdir()
        hashes = {}
        for directory, folders, files in os.walk(ROOT):
            folders[:] = [name for name in folders if name not in EXCLUDED]
            for name in files:
                source = Path(directory) / name
                if name.endswith('.pyc') or name == '.run.lock':
                    continue
                relative = source.relative_to(ROOT)
                data = source.read_bytes()
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                digest = hashlib.sha256(data).hexdigest()
                assert hashlib.sha256(target.read_bytes()).hexdigest() == digest
                hashes[str(relative)] = digest
        print(json.dumps(dict(exported_files=hashes, excluded=sorted(EXCLUDED)), indent=2), flush=True)
        command = [sys.executable, str(ROOT.parents[2] / 'scripts/check_week06.py'), str(destination)]
        print('UNCHANGED COURSE CHECKER: ' + ' '.join(command), flush=True)
        return subprocess.run(command).returncode


if __name__ == '__main__':
    sys.exit(check())
