"""One public board GET and <=4 MiB per live/rotated log, in-memory sanitization."""
import ast
import collections
import datetime
import hashlib
import json
from pathlib import Path
import re
import urllib.request

BASE = Path('/home/comalswjd/ai-agent-engineering-101/submissions/25512085/week-08')
LIMIT = 4 * 1024 * 1024
def utc(t=None):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat() if t is not None else datetime.datetime.now(datetime.timezone.utc).isoformat()
FIELDS = {'alive', 'deals', 'share', 'visits', 'stars', 'reviews', 'revenue', 'profit',
          'stock_units', 'stock_cost', 'book', 'bankruptcies', 'stars_rank', 'refunds',
          'broken', 'claims', 'honest_claims', 'all', 'saved', 'spent', 'value', 'orders',
          'total', 'student_shop', 'student_buyer', 'student', 'student_total', 'student_pct',
          'rank', 'student_rank', 'count', 'round', 'current', 'house', 'score'}
def numeric(row):
    if not isinstance(row, dict): return {}
    return {k: v for k, v in row.items() if k in FIELDS and (v is None or isinstance(v, (int, float, bool)))}
def own(row):
    if not isinstance(row, dict): return False
    vals = [row.get('name'), row.get('player')]
    for k in ('owner', 'shop', 'buyer'):
        v = row.get(k)
        if isinstance(v, dict): vals.extend([v.get('name'), v.get('login')])
    return any(isinstance(v, str) and v.lower() == 'mjforge' for v in vals)

result = {'observed_utc': utc(), 'read_policy': {'board_get_limit': 1, 'log_max_bytes_each': LIMIT,
          'raw_board_or_logs_exported': False, 'sampling': 'tail, not random; not full round'}}
try:
    with urllib.request.urlopen('https://market.ourolabs.ai/api/board', timeout=30) as response:
        board = json.load(response)
        status = response.status
    b = {'source': 'https://market.ourolabs.ai/api/board', 'http_status': status,
         'observed_utc': utc(), 'round': board.get('round'), 'current': board.get('current'),
         'top_level_schema_keys': sorted(board), 'own': {}}
    for section in ('shops', 'buyers', 'players'):
        rows = board.get(section, [])
        selected = [r for r in rows if own(r)]
        b['own'][section] = []
        for r in selected:
            rec = numeric(r)
            for nested in ('shop', 'buyer'):
                if isinstance(r.get(nested), dict): rec[nested] = numeric(r[nested])
            rec['schema_keys_without_values'] = sorted(r)
            b['own'][section].append(rec)
        b[section + '_rows'] = len(rows)
    students = [r for r in board.get('players', []) if r.get('student')]
    selected = [r for r in students if own(r)]
    b['student_players_denominator'] = len(students)
    if len(selected) == 1 and isinstance(selected[0].get('student_total'), (int, float)):
        score = selected[0]['student_total']
        valid = [r for r in students if isinstance(r.get('student_total'), (int, float))]
        b['student_rank_competition_desc_student_total'] = 1 + sum(r['student_total'] > score for r in valid)
        b['student_rank_score_denominator'] = len(valid)
        b['student_score_tie_count_including_self'] = sum(r['student_total'] == score for r in valid)
    b['timestamp_fields'] = {k: v for k, v in board.items() if k in ('timestamp', 'ts', 'as_of', 'updated_at', 'ends_at', 'round_end') and (isinstance(v, (int, float)) or isinstance(v, str) and re.fullmatch(r'[0-9TZ:+. -]{8,40}', v))}
    result['board'] = b
except Exception as e:
    result['board'] = {'error_type_only': type(e).__name__, 'get_attempts': 1, 'retried': False}

markers = {'seller_acp_settled_lines': '[acp] settled ', 'buyer_acp_paid_lines': '[acp] paid ',
           'settle_refused_lines': '[acp] settle refused:', 'checkout_refused_lines': '[acp] checkout refused:',
           'payment_not_completed_lines': '[acp] payment not completed:', 'payment_failed_lines': '[acp] payment failed:',
           'buyer_harness_warning_lines': '[Buyer Harness Warning]', 'gemini_fallback_warning_lines': '[Gemini Fallback Warning',
           'goods_nonpositive_saved_lines': 'nonpositive expected Saved', 'food_lifetime_precheck_lines': 'food-lifetime-precheck',
           'buyer_failure_telemetry_lines': '[buyer-failure]'}
phrases = ['the checkout does not match the agreed receipt', 'above the mandate', 'not paying:',
           'cannot settle', 'declined-price-policy', 'food-lifetime-precheck', 'nonpositive expected Saved',
           'seller ask exceeds price limit', 'stockout', 'out of stock', 'timeout', 'timed out']
entries = []
for n in ('market-kit.log.1', 'market-kit.log'):
    p = BASE / 'logs' / n
    if not p.is_file():
        entries.append({'name': n, 'exists': False})
        continue
    with p.open('rb') as stream:
        before = __import__('os').fstat(stream.fileno())
        offset = max(0, before.st_size - LIMIT)
        stream.seek(offset)
        raw = stream.read(min(LIMIT, before.st_size))
        after = __import__('os').fstat(stream.fileno())
    lines = raw.splitlines()
    if offset and lines: lines = lines[1:]
    if raw and not raw.endswith(b'\n') and lines: lines = lines[:-1]
    texts = [s.decode('utf-8', errors='replace') for s in lines]
    counts = {k: sum(marker in s for s in texts) for k, marker in markers.items()}
    reasons = collections.Counter()
    seq = {'seller': [], 'buyer': []}
    snippets = []
    for line in texts:
        for phrase in phrases:
            if phrase.lower() in line.lower(): reasons[phrase] += 1
        if '[acp] settle refused:' in line:
            part = line.split('[acp] settle refused:', 1)[1].strip()
            try: checks = ast.literal_eval(part)
            except (ValueError, SyntaxError): checks = None
            if isinstance(checks, list) and all(isinstance(k, str) and re.fullmatch(r'[a-z_]{1,80}', k) for k in checks):
                for key in checks: reasons['settle_check:' + key] += 1
            else: reasons['settle_refusal_nonlist_or_withheld'] += 1
        role = 'seller' if '[acp] settled ' in line else 'buyer' if '[acp] paid ' in line else None
        if role:
            match = re.search(r'ledger #(\d+)', line)
            if match:
                seq[role].append(int(match.group(1)))
                # Exact discontiguous substrings, not full lines: never emit unit, URL, signatures, IDs or budget.
                snippets.append({'role': role, 'clock_only': line[:8] if re.match(r'^\d{2}:\d{2}:\d{2}', line) else None,
                                 'exact_substrings': ['[acp] settled' if role == 'seller' else '[acp] paid', match.group(0)]})
    clock = [s[:8] for s in texts if re.match(r'^\d{2}:\d{2}:\d{2}', s)]
    entries.append({'name': n, 'exists': True, 'bytes_at_open': before.st_size, 'mtime_utc_at_open': utc(before.st_mtime),
                    'inode': before.st_ino, 'offset': offset, 'bytes_read': len(raw), 'tail_sha256': hashlib.sha256(raw).hexdigest(),
                    'bytes_after': after.st_size, 'stable_during_read': before.st_size == after.st_size and before.st_mtime_ns == after.st_mtime_ns,
                    'complete_lines_scanned': len(texts), 'first_clock_without_date': clock[0] if clock else None,
                    'last_clock_without_date': clock[-1] if clock else None, 'counts': counts,
                    'reason_phrase_line_counts_nonexclusive': dict(reasons),
                    'settlement_sequences': {k: {'line_count_with_seq': len(v), 'unique_count': len(set(v)), 'min': min(v) if v else None, 'max': max(v) if v else None} for k, v in seq.items()},
                    'exact_substring_examples_NOT_FULL_LOG_LINES': snippets[-3:]})
result['logs'] = entries
result['coverage_limitations'] = ['Board cumulative counts and bounded log event counts have different denominators.',
                                'Log clocks have no calendar date; mtime is not the first event date.',
                                'A seller settlement and buyer payment can refer to one transaction; do not sum into unique trades.',
                                'Phrase counts overlap and include non-settlement refusals; not a disjoint rejection rate.',
                                'No model success payload was read; configured names do not prove successful model calls.']
print(json.dumps(result))
