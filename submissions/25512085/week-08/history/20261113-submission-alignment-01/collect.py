"""Exclusive local evidence writer; approved existing gcloud SSH transport only."""
import base64
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zlib

OUT = Path(__file__).resolve().parent
REPO = OUT.parents[4]
def save(name, obj):
    p = OUT / name
    p.parent.mkdir(parents=True, exist_ok=True)
    blob = obj if isinstance(obj, bytes) else (json.dumps(obj, ensure_ascii=False, indent=2) + '\n').encode()
    with p.open('xb') as f: f.write(blob)
def git(*a):
    return subprocess.check_output(['git', *a], cwd=REPO).decode().strip()
def ssh(script):
    payload = base64.b64encode(zlib.compress(script.encode())).decode()
    cmd = '/home/comalswjd/venv/bin/python -B -c "import base64,zlib;exec(zlib.decompress(base64.b64decode(\'' + payload + '\')))"'
    assert len(cmd) < 7500
    p = subprocess.run([shutil.which('gcloud.cmd'), 'compute', 'ssh', 'comalswjd@market-agent', '--project=studious-plate-458511-a1', '--zone=us-central1-a', '--quiet', '--command=' + cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=240)
    if p.returncode:
        save('transport-failure.json', {'returncode': p.returncode, 'stderr_not_saved_for_privacy': True})
        raise RuntimeError('SSH failed; raw stderr withheld')
    return json.loads(p.stdout)

if __name__ == '__main__':
    mode = sys.argv[1]
    if mode == 'source':
        result = ssh((OUT / 'inspect_runtime.py').read_text())
        for name, rec in result['sources'].items():
            if not rec['exists']: continue
            blob = base64.b64decode(rec.pop('source_base64'))
            assert hashlib.sha256(blob).hexdigest() == rec['sha256']
            save('runtime-source/' + name, blob)
            local = REPO / name
            rec['local_exists'] = local.is_file()
            if local.is_file():
                rec['local_sha256'] = hashlib.sha256(local.read_bytes()).hexdigest()
                rec['matches_local'] = rec['local_sha256'] == rec['sha256']
        result['local'] = {'head': git('rev-parse', 'HEAD'), 'tracked_dirty': git('diff', 'HEAD', '--name-only').splitlines(), 'untracked_present': bool(git('ls-files', '--others', '--exclude-standard'))}
        result['local_existing_logs'] = []
        for n in ('market-kit.log', 'market-kit.log.1'):
            p = REPO / 'submissions/25512085/week-08/logs' / n
            if p.is_file():
                s = p.stat()
                result['local_existing_logs'].append({'name': n, 'bytes': s.st_size, 'mtime_utc': datetime.datetime.fromtimestamp(s.st_mtime, datetime.timezone.utc).isoformat(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()})
        save('runtime-manifest.json', result)
        print(json.dumps({'vm_head': result['head'], 'processes': result['processes'], 'agent_safe': result['agent_commands_models_only'], 'dirty_allowlisted': result['dirty_allowlisted'], 'logs_stat': result['logs_stat_only'], 'source_files': len(result['sources'])}, ensure_ascii=False, indent=2))
    elif mode == 'metrics':
        result = ssh((OUT / 'inspect_metrics.py').read_text())
        save('sanitizedmetrics.json', result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else: raise SystemExit('source or metrics only')
