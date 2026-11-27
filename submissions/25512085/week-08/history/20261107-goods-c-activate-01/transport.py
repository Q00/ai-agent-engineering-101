"""Explicit one-command SSH transport; stores only sanitized helper output."""
import base64
import pathlib
import shutil
import subprocess
import sys
import zlib

HERE = pathlib.Path(__file__).resolve().parent
label = sys.argv[1]
code = (HERE / (label + '_remote.py')).read_bytes()
encoded = base64.b64encode(zlib.compress(code)).decode()
command = '/home/comalswjd/venv/bin/python -B -c "import base64,zlib;exec(zlib.decompress(base64.b64decode(\'' + encoded + '\')))"'
assert len(command) < 7500
with (HERE / (label + '-stderr.log')).open('xb') as err:
    result = subprocess.run([
        shutil.which('gcloud.cmd'), 'compute', 'ssh', 'comalswjd@market-agent',
        '--project=studious-plate-458511-a1', '--zone=us-central1-a', '--quiet',
        '--command=' + command,
    ], stdout=subprocess.PIPE, stderr=err, timeout=300)
with (HERE / (label + '-stdout.json')).open('xb') as out:
    out.write(result.stdout)
print(result.stdout.decode())
raise SystemExit(result.returncode)
