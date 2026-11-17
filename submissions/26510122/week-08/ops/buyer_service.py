"""Persistent buyer-only kit, frozen without life/world/roleplay dependencies."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
FILES = ('buyer_cohort.py','buyer_value.py','buyer_breakage.py','market_buyer_r3.py', 'buyer_r3.py', 'buyer_policy.py', 'buyer_memory.py','market_scoring.py','decision_audit.py',
         'buyer_identity.py', 'buyer_exploration.py', 'harness.py', 'context_compaction.py', 'review_r3.py','buyer_forum_memory.py','buyer_checkout_memory.py')
NAME = 'outseop-26510122'
STOP = False


def prepare(runtime, keys):
    runtime.mkdir(parents=True, exist_ok=True)
    release = runtime/'release'; release.mkdir(exist_ok=False)
    hashes = {}
    for name in FILES:
        shutil.copy2(ROOT/name, release/name)
        hashes[name] = hashlib.sha256((release/name).read_bytes()).hexdigest()
    for name in ('shop_key.pem', 'buyer_key.pem'):
        source = (keys/name).resolve()
        if not source.is_file(): raise FileNotFoundError(f'Missing existing market key: {name}')
        destination = runtime/name
        if not destination.exists(): destination.symlink_to(source)
        elif destination.resolve() != source: raise ValueError('Existing key source differs')
    (runtime/'logs').mkdir(exist_ok=True)
    python = str(ROOT/'.venv/bin/python')
    config = {'harness': 'Plain issued-order buyer; no roleplay or extra orders; forum worker is independent',
              'market_buyer': {'cmd': shlex.join([python, str(release/'market_buyer_r3.py'),
                               '--shops', '{shops}', '--order', '{order}', '--own-shop-name', NAME, '--epsilon', '0.1'])},
              'review': {'cmd': shlex.join([python, str(release/'review_r3.py'), '--deal', '{deal}'])}}
    (runtime/'agent.json').write_text(json.dumps(config, indent=2))
    commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    (runtime/'release.json').write_text(json.dumps({'source_commit': commit, 'sha256': hashes,
                                                   'mode': 'plain', 'prepared_at': time.time()}, indent=2))


def validate(runtime):
    manifest = json.loads((runtime/'release.json').read_text())
    if set(manifest['sha256']) != set(FILES) or manifest['mode'] != 'plain':
        raise ValueError('Unexpected plain buyer release files')
    for name, digest in manifest['sha256'].items():
        if hashlib.sha256((runtime/'release'/name).read_bytes()).hexdigest() != digest:
            raise ValueError('Frozen buyer release integrity changed')
    config = json.loads((runtime/'agent.json').read_text())
    if set(config) != {'harness', 'market_buyer', 'review'}: raise ValueError('Unexpected buyer hooks')
    for name, entry in (('market_buyer', 'market_buyer_r3.py'), ('review', 'review_r3.py')):
        tokens = shlex.split(config[name]['cmd'])
        if tokens[:2] != [str(ROOT/'.venv/bin/python'), str(runtime/'release'/entry)]:
            raise ValueError('Unexpected buyer command')
        if any(token.startswith('--life') for token in tokens): raise ValueError('Roleplay is disabled')
    return manifest


def run(args):
    global STOP
    def finish(*_):
        global STOP
        STOP = True
    signal.signal(signal.SIGTERM, finish); signal.signal(signal.SIGINT, finish)
    manifest = validate(args.runtime)
    env = os.environ.copy()
    env['MARKET_OWNER'] = args.owner_file.read_text().strip()
    env['PYTHONUNBUFFERED'] = '1'
    env['PATH'] = f"{Path.home()/'.local/bin'}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:{env.get('PATH', '')}"
    with (args.runtime/'service.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while not STOP:
            proc = None
            try:
                with (args.runtime/'logs/kit-console.log').open('a') as output:
                    proc = subprocess.Popen([str(ROOT/'.venv/bin/python'), str(ROOT.parents[2]/'scripts/market_kit.py'),
                        'join', '--dir', str(args.runtime), '--market', 'https://market.ourolabs.ai',
                        '--name', NAME, '--buyer-only'], cwd=args.runtime, env=env,
                        stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
                    (args.runtime/'running.json').write_text(json.dumps({
                        'supervisor_pid': os.getpid(), 'kit_pid': proc.pid, 'source_commit': manifest['source_commit'],
                        'mode': 'plain', 'roleplay_enabled': False, 'started_at': time.time()}, indent=2))
                    while not STOP and proc.poll() is None: time.sleep(1)
            finally:
                if proc is not None and proc.poll() is None:
                    try: os.killpg(proc.pid, signal.SIGTERM)
                    except ProcessLookupError: pass
                    try: proc.wait(timeout=12)
                    except subprocess.TimeoutExpired:
                        try: os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError: pass
                        proc.wait(timeout=5)
            for _ in range(10):
                if STOP: break
                time.sleep(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', type=Path, default=ROOT/'state/buyer-runtime')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--keys-dir', type=Path)
    parser.add_argument('--owner-file', type=Path, default=Path.home()/'.config/ourolabs-market/owner-code')
    args = parser.parse_args(); args.runtime = args.runtime.resolve()
    if args.prepare_only:
        if not args.keys_dir: parser.error('--keys-dir required')
        prepare(args.runtime, args.keys_dir)
    else: run(args)
