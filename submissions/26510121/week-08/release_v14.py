"""Checkpoint validated code, then promote only while the runtime lease is free.

Never backs up or replaces credentials, accounts, logs or the market database.
Economic rule proposals remain shadow; this release changes execution structure.
"""
import argparse
from datetime import datetime, timezone
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time

ROOT = Path(__file__).resolve().parent
STAGE = ROOT / '.market/v14-stage-20261111'
FILES = ['r3/decision_view.py', 'r3/meta.py', 'r3/economy.py', 'r3/forum.py', 'r3/buyer_scoring.py',
    'r3/buyer_routes.py', 'r3/buyer_execution.py', 'r3/adapter.py', 'r3/autonomy.py',
    'r3/worker_bridge.py', 'r3/buyer_worker.py', 'r3/state.py', 'r3/quote_market.py',
    'r3/model.py', 'seller_r3.py', 'market_buyer.py', 'policy.json', 'tests/test_v14_runtime.py']


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def checkpoint():
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    tests = STAGE / 'logs/v14-full-final-tests-01.txt'
    text = tests.read_text(encoding='utf-16')
    passed = re.search(r'(\d+) passed', text)
    if not passed or int(passed[1]) < 295 or re.search(r'\d+ failed|\d+ errors', text):
        raise RuntimeError('Full regression evidence is missing or failed')
    if any((STAGE / name).stat().st_mtime > tests.stat().st_ctime for name in FILES):
        raise RuntimeError('Candidate changed after the full regression started')
    parity_path = sorted((ROOT / 'logs').glob('v14-parity-summary-*.json'))[-1]
    replay_path = sorted((ROOT / 'logs').glob('v14-replay-verification-*.json'))[-1]
    parity = json.loads(parity_path.read_text())
    replay = json.loads(replay_path.read_text())
    assert parity['all_outputs_identical'] and len(parity['pairs']) >= 8
    assert parity['total_candidate_seconds'] <= .8 * parity['total_baseline_seconds']
    assert replay['confirmed_refunds_preserved'] and replay['inventory_file_unchanged']
    assert replay['all_outputs_identical'] and not replay['background_errors'] and replay['background_ticks'] > 0
    assert replay['facts_published'] and replay['role_count'] == 12 and replay['new_trade_rules_activated'] == 0
    patch = []
    entries = []
    for name in FILES:
        original, candidate = ROOT / name, STAGE / name
        before = original.read_text(encoding='utf-8-sig').splitlines(keepends=True) if original.exists() else []
        after = candidate.read_text(encoding='utf-8-sig').splitlines(keepends=True)
        patch.extend(difflib.unified_diff(before, after, fromfile='a/' + name, tofile='b/' + name))
        entries.append({'path': name, 'baseline_sha256': sha(original), 'candidate_sha256': sha(candidate)})
    patch_path = ROOT / 'logs' / ('v14-runtime-candidate-' + stamp + '.patch')
    patch_path.write_text(''.join(patch), encoding='utf-8')
    for source in sorted((STAGE / 'logs').glob('v14-*.txt')):
        target = ROOT / 'logs' / ('stage-' + source.name)
        if not target.exists():
            shutil.copyfile(source, target)
    report = {'at': datetime.now(timezone.utc).isoformat(), 'scope': 'v14 structural execution and analytical skill composition',
        'status': 'validated_candidate_not_deployed', 'files': entries, 'full_tests_passed': int(passed[1]),
        'parity': str(parity_path.relative_to(ROOT)), 'replay': str(replay_path.relative_to(ROOT)),
        'patch': str(patch_path.relative_to(ROOT)), 'new_economic_trade_rules_enabled': False,
        'universal_superiority_or_first_place_proven': False}
    path = ROOT / 'logs' / ('v14-release-candidate-' + stamp + '.json')
    write(path, report); print(path)


def promote(manifest):
    from r3.lifecycle import market_active
    if market_active():
        raise RuntimeError('Runtime still owns the lease; drain before source replacement')
    report = json.loads(manifest.read_text(encoding='utf-8'))
    if report.get('status') != 'validated_candidate_not_deployed':
        raise RuntimeError('Not a validated candidate manifest')
    if [row['path'] for row in report['files']] != FILES:
        raise RuntimeError('Unexpected release file set')
    for row in report['files']:
        assert sha(ROOT / row['path']) == row['baseline_sha256'], 'Baseline changed'
        assert sha(STAGE / row['path']) == row['candidate_sha256'], 'Candidate changed'
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = ROOT / '.market' / ('v14-release-' + stamp)
    backup.mkdir()
    record = {'at': datetime.now(timezone.utc).isoformat(), 'candidate_manifest': str(manifest.relative_to(ROOT)),
              'backup': str(backup.relative_to(ROOT)), 'files': report['files'], 'status': 'replacing_sources'}
    for row in report['files']:
        path = ROOT / row['path']
        if path.is_file():
            target = backup / row['path']; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    try:
        for row in report['files']:
            path = ROOT / row['path']
            assert path.resolve().is_relative_to(ROOT.resolve())
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + '.v14-' + stamp + '.tmp')
            temporary.write_bytes((STAGE / row['path']).read_bytes())
            os.replace(temporary, path)
        assert all(sha(ROOT / r['path']) == r['candidate_sha256'] for r in report['files'])
        record['status'] = 'sources_promoted_runtime_not_started'
    except Exception:
        for row in report['files']:
            old = backup / row['path']
            if old.is_file():
                shutil.copyfile(old, ROOT / row['path'])
        record['status'] = 'source_replacement_failed_prior_files_restored'
        raise
    finally:
        path = ROOT / 'logs' / ('v14-source-promotion-' + stamp + '.json')
        write(path, record); print(path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--promote', type=Path)
    args = parser.parse_args()
    if args.promote:
        promote(args.promote)
    else:
        checkpoint()
