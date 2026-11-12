"""Offline-only snapshot validation. No application imports, network, or pyc writes."""
import ast
import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

OUT = Path(__file__).resolve().parent
REPO = OUT.parents[3]
def load(n): return json.loads((OUT / n).read_text(encoding='utf-8'))
manifest = load('runtime-manifest.json')
metrics = load('sanitizedmetrics.json')
verified = load('verification.json')
for name, row in manifest['sources'].items():
    if not row['exists']: continue
    assert hashlib.sha256((OUT / 'runtime-source' / name).read_bytes()).hexdigest() == row['sha256'], 'mirror hash mismatch'
    assert hashlib.sha256((REPO / name).read_bytes()).hexdigest() == row['local_sha256'], 'original source changed'
for row in manifest['local_existing_logs']:
    p = REPO / 'submissions/25512085/week-08/logs' / row['name']
    assert hashlib.sha256(p.read_bytes()).hexdigest() == row['sha256'], 'existing original log changed'

denied_json_keys = {'owner', 'login', 'avatar', 'html', 'card', 'order', 'order_id',
                    'budget', 'sig_buyer', 'sig_seller', 'checkout_jwt', 'authorization',
                    'cookie', 'password', 'private_key', 'api_key', 'token'}
def check_json(value):
    if isinstance(value, dict):
        assert not (set(value) & denied_json_keys), 'private/raw JSON field'
        for v in value.values(): check_json(v)
    elif isinstance(value, list):
        for v in value: check_json(v)
patterns = [r'-----BEGIN (?:\w+ )?PRIVATE KEY-----', r'\b(?:sk-proj-|ghp_|AIza)[A-Za-z0-9_-]{20,}', r'ya29\.[A-Za-z0-9_-]{20,}']
files = list(p for p in OUT.rglob('*') if p.is_file())
py_count = 0
for p in files:
    assert p.suffix.lower() not in ('.pem', '.key', '.env'), 'forbidden artifact suffix'
    text = p.read_text(encoding='utf-8')
    for pattern in patterns: assert not re.search(pattern, text), 'credential-like content'
    if p.suffix == '.json': check_json(json.loads(text))
    if p.suffix == '.py':
        ast.parse(text, filename=str(p)); py_count += 1

assert sum(r['bytes_read'] for r in metrics['logs']) == 8388608
assert sum(r['complete_lines_scanned'] for r in metrics['logs']) == 3086
for key, value in verified['log_counts_sum_NOT_BOARD_COUNTS'].items():
    assert sum(r['counts'][key] for r in metrics['logs']) == value
b = metrics['board']; s=b['own']['shops'][0]; y=b['own']['buyers'][0]; p=b['own']['players'][0]
assert b['round'] == b['current'] == 3
assert s['deals'] == 17732 and y['deals'] == 17954
assert abs(s['all'] + y['all'] - p['total']) < 1e-12
assert abs(p['student_shop'] + p['student_buyer'] - p['student_total']) < 1e-12
assert b['student_rank_competition_desc_student_total'] == 17 and b['student_players_denominator'] == 46
assert p['student_pct'] == .644
changed = subprocess.check_output(['git', 'diff', '--name-only', '5513728f30532c577dee48ba5f42edb86d1c5105'], cwd=REPO).decode().splitlines()
assert all(n.startswith('submissions/25512085/week08/history20261113-submission-alignment-01/') for n in changed), 'outside-scope tracked change'
summary = {'validated_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
           'offline_only': True, 'files_scanned': len(files), 'python_ast_files': py_count,
           'mirror_hashes_match_vm_manifest': True, 'original_local_source_and_log_hashes_preserved': True,
           'credential_pattern_and_structured_private_field_checks_passed': True,
           'privacy_check_limitation': 'Pattern/allowlist checks, not a universal proof; runtime raw data was never exported.',
           'board_arithmetic_and_bounded_log_count_invariants_passed': True,
           'tracked_changes_since_start_outside_authorized_evidence_directory': 0,
           'application_execution_or_network_used': False}
if '--record' in sys.argv:
    with (OUT / 'final-validation.json').open('x', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2); f.write('\n')
print(json.dumps(summary, ensure_ascii=False, indent=2))
