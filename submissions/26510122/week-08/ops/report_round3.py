"""Capture bounded, read-only Round 3 report evidence; never touch a live DB."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import urllib.request

KST = timezone(timedelta(hours=9))
SOURCE = 'logs/buyer-events.jsonl'
WANTED = {'settled', 'no_candidate', 'negotiation_skipped', 'negotiation',
          'checkout_candidate_held', 'buyer_policy_selection', 'buyer_forum_ack'}


def public_json(url):
    with urllib.request.urlopen(url, timeout=25) as response:
        return json.load(response)


def buyer_evidence(root):
    path = root / 'state/buyer-runtime/logs/buyer-events.jsonl'
    db = root / 'state/buyer-runtime/state/buyer.sqlite3'
    with path.open('rb') as source, closing(sqlite3.connect(
            db.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)) as conn:
        stat = os.fstat(source.fileno())
        conn.execute('BEGIN')
        cursor = conn.execute('SELECT device,inode,generation,offset FROM sources WHERE source=?', (SOURCE,)).fetchone()
        if not cursor or cursor[:2] != (stat.st_dev, stat.st_ino) or not 0 <= cursor[3] <= stat.st_size:
            raise ValueError('Current source and SQLite prefix do not match')
        generation, offset = cursor[2:]
        last = conn.execute('SELECT byte_offset,payload FROM events WHERE source=? AND generation=? AND byte_offset<? ORDER BY byte_offset DESC LIMIT 1', (SOURCE, generation, offset)).fetchone()
        if last:
            source.seek(last[0])
            if json.loads(source.readline()) != json.loads(last[1]):
                raise ValueError('Indexed prefix boundary changed')
        args = (SOURCE, generation, offset, *sorted(WANTED))
        sql = 'SELECT byte_offset,payload FROM events WHERE source=? AND generation=? AND byte_offset<? AND event IN (' + ','.join('?' for _ in WANTED) + ') ORDER BY byte_offset'
        rows = [(off, json.loads(payload)) for off, payload in conn.execute(sql, args)]
        source.seek(offset)
        while source.tell() < stat.st_size:
            off = source.tell()
            raw = source.readline()
            if source.tell() > stat.st_size or not raw.endswith(b'\n'):
                break
            event = json.loads(raw)
            if event.get('event') in WANTED:
                rows.append((off, event))
    counts = Counter(e['event'] for _, e in rows)
    outcomes = Counter(e.get('result', 'unknown') for _, e in rows if e['event'] == 'negotiation')
    failed = Counter((e.get('reason') or e.get('terminal_reason') or e.get('receipt_problem') or 'unspecified') for _, e in rows if e['event'] == 'negotiation' and e.get('result') != 'receipt_pending')
    payments = {e['ledger']: e for _, e in rows if e['event'] == 'settled' and type(e.get('ledger')) is int}
    selected = []
    for kind in ['settled', 'no_candidate', 'negotiation_skipped', 'negotiation']:
        choices = [(off, e) for off, e in rows if e['event'] == kind]
        if kind == 'negotiation':
            choices = [(off, e) for off, e in choices if e.get('result') != 'receipt_pending']
        for off, event in choices[-2:]:
            selected.append({'source': str(path.relative_to(root)), 'byte_offset': off,
                             'event': {k: v for k, v in event.items() if k in {
                                 'at','event','order','shop_name','sku','price','ledger','result','reason',
                                 'terminal_reason','receipt_problem','source_commit','model','policy_version'}}})
    return {'source': str(path.relative_to(root)), 'generation': generation,
            'indexed_prefix_bytes': offset, 'captured_source_bytes': stat.st_size,
            'prefix_boundary_verified': True, 'current_generation_only': True,
            'event_counts': dict(counts), 'negotiation_outcomes': dict(outcomes),
            'unsuccessful_attempt_reasons': dict(failed.most_common()),
            'unique_settled_ledgers': len(payments),
            'first_settled_at': min(e['at'] for e in payments.values()),
            'last_settled_at': max(e['at'] for e in payments.values()),
            'examples': selected,
            'scope': 'Current verified file generation plus complete captured raw tail. Attempts are not unique failed orders; local callbacks may lag the board.'}


def kit_evidence(root, output):
    counts = Counter()
    files = []
    selected = []
    patterns = [
        ('buyer_paid', '[acp] paid '), ('seller_settled', '[acp] settled '),
        ('grade_refusal', '[acp] not paying: grade '),
        ('shelf_life_refusal', 'h of shelf life left, the policy asks for'),
        ('payment_failed', '[acp] payment not completed:'),
        ('result_refused', '[market] result refused:'),
        ('order_failed', '[market] order failed:')]
    for role in ['buyer', 'seller']:
        for name in ['market-kit.log.1', 'market-kit.log']:
            path = root / 'state' / (role + '-runtime') / 'logs' / name
            if not path.exists():
                continue
            examples = defaultdict(list)
            digest = hashlib.sha256()
            with path.open('rb') as source:
                stat = os.fstat(source.fileno())
                limit = stat.st_size
                line_number = 0
                captured = 0
                while source.tell() < limit:
                    raw = source.readline()
                    if source.tell() > limit or not raw.endswith(b'\n'):
                        break
                    digest.update(raw)
                    captured = source.tell()
                    line_number += 1
                    line = raw.decode('utf-8', errors='replace').rstrip('\n')
                    if not re.match(r'^\d\d:\d\d:\d\d \[(acp|market)\]', line):
                        continue
                    for category, needle in patterns:
                        if needle in line:
                            counts[role + ':' + category] += 1
                            if len(examples[category]) < 2:
                                if re.search(r'Bearer |sk-[A-Za-z0-9]|eyJ[A-Za-z0-9_-]{20}', line):
                                    raise ValueError('Unexpected credential-like text in selected public protocol line')
                                examples[category].append({'role': role, 'category': category,
                                    'source': str(path.relative_to(root)), 'source_line': line_number,
                                    'text': line})
            files.append({'source': str(path.relative_to(root)), 'device': stat.st_dev,
                          'inode': stat.st_ino, 'snapshot_size': limit, 'complete_line_prefix_bytes': captured,
                          'complete_line_prefix_sha256': digest.hexdigest(), 'lines': line_number})
            for examples_for_category in examples.values():
                selected.extend(examples_for_category)
    lines = ['# Verbatim protocol excerpts; not the full round log.',
             '# Original live logs are untouched. Counts cover only the captured retained kit files.',
             '# See snapshot.json for source paths, prefix sizes and hashes.']
    for row in selected:
        lines.append('# ' + row['source'] + ':' + str(row['source_line']) + ' [' + row['category'] + ']')
        row['excerpt_line'] = len(lines) + 1
        lines.append(row['text'])
    (output / 'market-kit.log').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return {'counts': dict(counts), 'files': files, 'examples': selected,
            'scope': 'Retained .log/.log.1 complete-line prefixes only, not cumulative round refusal counts. Multiple lines may describe one failed payment.'}


def seller_evidence(root):
    path = root/'state/seller-runtime/logs/shop-events.jsonl'
    wanted = {'request_seen','opening','requirements_unmet','no_stock','rejected',
              'negotiation_expired','receipt_signed','settled','checkout_unpaid',
              'settled_unit_blocked','selling_price_guard','stale_inventory'}
    counts = Counter()
    examples = {}
    paid = set()
    digest = hashlib.sha256()
    first = None
    last = None
    with path.open('rb') as source:
        stat = os.fstat(source.fileno())
        complete = 0
        while source.tell() < stat.st_size:
            offset = source.tell()
            raw = source.readline()
            if source.tell() > stat.st_size or not raw.endswith(b'\n'):
                break
            digest.update(raw)
            complete = source.tell()
            event = json.loads(raw)
            first = first or event.get('at')
            last = event.get('at') or last
            kind = event.get('event')
            if kind not in wanted:
                continue
            counts[kind] += 1
            if kind == 'settled' and type(event.get('ledger')) is int:
                paid.add(event['ledger'])
            if kind not in examples:
                examples[kind] = {'source': str(path.relative_to(root)), 'byte_offset': offset,
                    'event': {k:v for k,v in event.items() if k in {
                        'at','event','task_id','sku','unit','price','cost','ledger','reason',
                        'source_commit','price_floor','near_expiry_loss_allowed'}}}
    return {'source': str(path.relative_to(root)), 'snapshot_size':stat.st_size,
            'complete_line_prefix_bytes':complete,'complete_line_prefix_sha256':digest.hexdigest(),
            'first_event_at':first,'last_event_at':last,'event_counts':dict(counts),
            'unique_settled_ledgers':len(paid),'examples':list(examples.values()),
            'scope':'Complete captured append-only shop-events prefix. Event occurrences can overlap for one task; not disjoint rejected orders.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    root = Path.cwd()
    output = args.output.resolve()
    own_submission = root.resolve()
    if not output.is_relative_to(own_submission):
        raise ValueError('Output must stay in this submission directory')
    output.mkdir(parents=True, exist_ok=False)
    started = datetime.now(KST).isoformat()
    board = public_json('https://market.ourolabs.ai/api/board?round=3')
    board_at = datetime.now(KST).isoformat()
    after_close = datetime.fromisoformat(board_at) >= datetime(2026, 11, 17, 23, 59, tzinfo=KST)
    owner = 'outseop'
    def owned(row):
        return str((row.get('owner') or {}).get('login', '')).lower() == owner
    player = next(p for p in board['players'] if owned(p))
    students = [p for p in board['players'] if p.get('student')]
    class_rank = 1 + sum(p['student_total'] > player['student_total'] for p in students)
    public = {'url': 'https://market.ourolabs.ai/api/board?round=3', 'observed_at': board_at,
              'round': board['round'], 'player': player,
              'players_class_rank': class_rank, 'players_class_count': len(students),
              'players_class_tied': sum(p['student_total'] == player['student_total'] for p in students),
              'rank_method': '1 + count of student_total strictly greater; ties share a rank',
              'players_overall_rank': 1 + sum(p['total'] > player['total'] for p in board['players'])}
    for key in ['shops', 'buyers']:
        row = next(p for p in board[key] if owned(p))
        metric = row['all']
        public[key] = {'row': row, 'rank': 1 + sum(p.get('all', 0) > metric for p in board[key]), 'count': len(board[key])}
    buyer = buyer_evidence(root)
    kit = kit_evidence(root, output)
    seller = seller_evidence(root)
    baseline_paths = {
        'buyer_week10': 'state/buyer-runtime/state/growth-baseline-BUYERSAVED20261110A.json',
        'buyer_week10_hour': 'state/buyer-runtime/state/growth-result-BUYERSAVED20261110A.json',
        'seller_week10': 'state/seller-runtime/state/growth-baseline-FIRSTBUYER20261110A.json'}
    baselines = {}
    for key, name in baseline_paths.items():
        path = root / name
        baselines[key] = {'source': name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                          'data': json.loads(path.read_text())}
    hour_path = root / 'state/buyer-runtime/state/buyer-cohort-hour-20261117/verified.json'
    hour = json.loads(hour_path.read_text())
    h = hour['hourly_comparison']
    clean_hour = {key: h[key] for key in ['windows','duration_seconds','fixed_roster_count','saved','changes_vs_clean_before','per_combo','breakage_notices','method']}
    clean_hour['metrics'] = {label: {k: v for k, v in row.items() if k != 'rows'} for label, row in h['metrics'].items()}
    clean_hour['all_preservation_checks_passed'] = hour['all_checks_pass']
    clean_hour['source_sha256'] = hashlib.sha256(hour_path.read_bytes()).hexdigest()
    running = {}
    for role in ['buyer', 'seller']:
        data = json.loads((root / 'state' / (role+'-runtime') / 'running.json').read_text())
        running[role] = {k: v for k, v in data.items() if k.endswith('_pid') or k in ['source_commit','started_at','url']}
    report = {'schema': 'round3-report-evidence-v1', 'started_at': started,
              'completed_at': datetime.now(KST).isoformat(), 'is_round_final': after_close,
              'official': public, 'buyer_current_generation': buyer, 'retained_kit_logs': kit,
              'seller_captured_prefix': seller,
              'week10_baselines': baselines, 'fixed_hour_experiment': clean_hour,
              'runtime': running,
              'limits': [('Observed after the published Round3 close; use round3 board values.' if after_close else
                          'Observed before 2026-11-17 23:59 KST. Final Class rank is not yet known.'),
                         'Official cumulative board numbers and local captured log counts have different update times.',
                         'Week10 Saved predates retroactive patch3.6; do not interpret its difference as a policy gain.']}
    (output/'snapshot.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    with (output/'buyer-event-excerpts.jsonl').open('w', encoding='utf-8') as file:
        for row in buyer['examples']:
            file.write(json.dumps(row, ensure_ascii=False)+'\n')
    with (output/'seller-event-excerpts.jsonl').open('w', encoding='utf-8') as file:
        for row in seller['examples']:
            file.write(json.dumps(row, ensure_ascii=False)+'\n')
    print(json.dumps({'output': str(output), 'official': public, 'buyer_events': buyer['event_counts'],
                      'negotiation_outcomes': buyer['negotiation_outcomes'],
                      'top_reasons': list(buyer['unsuccessful_attempt_reasons'].items())[:10],
                      'kit_counts': kit['counts'], 'seller_counts': seller['event_counts']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
