"""Preserve previous evidence and isolate official-test memory from market memory."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]


def main(phase='lab1'):
    out = ROOT / 'logs' / (phase + '-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    out.mkdir(parents=True)
    previous = out / 'previous-evidence'
    files = [ROOT / 'test-report.json', ROOT / 'logs/market-test.log', ROOT / 'logs/seller-under-test.log']
    files += list((ROOT / 'logs').glob('buyer-under-test-*.log'))
    for source in files:
        if source.is_file():
            target = previous / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    sources = list(ROOT.glob('*.py')) + [ROOT / 'agent.json', REPO / 'scripts/market_test_week07.py']
    manifest = {str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    (out / 'source-hashes.json').write_text(json.dumps(manifest, indent=2) + '\n')
    env = dict(os.environ, MARKET_MEMORY_PATH=str(out / 'test-memory.sqlite3'))
    def run(name, command):
        with (out / name).open('w') as log:
            proc = subprocess.Popen(command, cwd=REPO, env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True)
            for line in proc.stdout:
                log.write(line)
                log.flush()
                print(line, end='', flush=True)
            return proc.wait()
    print(phase.upper() + '_EVIDENCE=' + str(out), flush=True)
    offline = run('offline-tests.log', [sys.executable, '-m', 'unittest', 'discover', '-s', str(ROOT), '-p', 'test_*.py', '-v'])
    if offline:
        return offline
    status = run('market-test-console.log', [sys.executable, str(REPO / 'scripts/market_test_week07.py'), str(ROOT)])
    db = out / 'test-memory.sqlite3'
    if db.exists():
        with sqlite3.connect(str(db)) as conn:
            conn.row_factory = sqlite3.Row
            records = [dict(row) for row in conn.execute('SELECT * FROM trades ORDER BY time')]
        (out / 'test-memory-export.json').write_text(json.dumps(records, indent=2, ensure_ascii=False) + '\n')
    (out / 'exit-status.json').write_text(json.dumps(dict(offline=offline, official_market_test=status)) + '\n')
    return status


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=('lab1', 'lab2'), default='lab1')
    sys.exit(main(parser.parse_args().phase))
