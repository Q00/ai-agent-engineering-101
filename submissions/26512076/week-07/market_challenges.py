"""Collect local evidence. Never calls market APIs or manufactures market results."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import shutil

from market_support import ROOT, read_events, now


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(value, ensure_ascii=True, indent=2))


def snapshot(a):
    rows = [r for r in read_events(ROOT / 'logs/challenge-1/seller-events.jsonl', 0)
            if r.get('phase') == a.phase and r.get('run_id') == a.run_id
            and (not a.since or r.get('time', '') >= a.since)
            and (not a.until or r.get('time', '') <= a.until)]
    # Task IDs deduplicate terminal events, not prices; count a sale at most once.
    started = {r['task'] for r in rows if r.get('event') == 'task_started'}
    sales = {r['task']: r for r in rows if r.get('event') == 'sale'}
    ended = {r['task']: r for r in rows if r.get('event') in ('sale','reject','failed')}
    quotes = [r for r in rows if r.get('event') == 'quote' and r.get('opening')]
    mean = lambda xs: sum(xs) / len(xs) if xs else None
    result = {'recorded_at': now(), 'phase': a.phase, 'run_id': a.run_id,
              'since': a.since, 'until': a.until,
              'local': {'started_tasks': len(started), 'terminal_tasks': len(ended),
                        'completed_sales': len(sales),
                        'average_signed_receipt_price': mean([r['price'] for r in sales.values()]),
                        'average_opening_ask': mean([r['price'] for r in quotes]),
                        'completed_sales_per_started_task': len(sales) / len(started) if started else None},
              'market': {'share': None, 'note': 'Unknown until comparable dashboard evidence is supplied.'}}
    if a.market_sales is not None:
        if a.own_market_sales is None or a.market_average is None or a.market_source is None:
            raise SystemExit('Market share needs --own-market-sales, --market-average and --market-source')
        if not 0 <= a.own_market_sales <= a.market_sales or a.market_sales <= 0:
            raise SystemExit('Require 0 <= own-market-sales <= market-sales and market-sales > 0')
        import math
        if not math.isfinite(a.market_average) or a.market_average < 0:
            raise SystemExit('Market average must be a finite nonnegative number')
        if not a.market_source.is_file():
            raise SystemExit('Dashboard evidence file does not exist')
        evidence = ROOT / 'logs/challenge-1' / (a.phase + '-dashboard' + a.market_source.suffix)
        if evidence.resolve() != a.market_source.resolve():
            shutil.copy2(a.market_source, evidence)
        result['market'] = {'own_verified_sales': a.own_market_sales,
                            'all_verified_sales': a.market_sales,
                            'share': a.own_market_sales / a.market_sales,
                            'own_average_transaction_price': a.market_average,
                            'source': str(evidence.relative_to(ROOT)),
                            'note': 'Operator-transcribed dashboard figures for the same time window; not independently verified.'}
    elif any(v is not None for v in (a.own_market_sales, a.market_average, a.market_source)):
        raise SystemExit('Supply all four market arguments together, or omit them all')
    write(ROOT / 'logs/challenge-1' / (a.phase + '-summary.json'), result)


def feedback(a):
    data = json.loads(a.input.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or data.get('name') != a.name or not isinstance(data.get('reviews'), list):
        raise SystemExit('Input must be {"name": YOUR_REGISTRY_NAME, "reviews": [actual dashboard reviews]}')
    result = {'name': data['name'], 'reviews': data['reviews'], 'captured_at': now(),
              'source': str(a.input.name)}
    # Preserve a copy of supplied review evidence. No generated reviews.
    write(ROOT / 'shop_feedback.json', result)
    write(ROOT / 'logs/challenge-1' / ('reviews-' + str(__import__('time').time_ns()) + '.json'), result)


def status(a):
    path = ROOT / 'logs/market-kit.log'
    counts = {}; current_round = None
    lines = path.read_text(encoding='utf-8-sig', errors='replace').splitlines() if path.exists() else []
    evidence = []
    for n, line in enumerate(lines, 1):
        order = re.search(r'\[order .*round (\d+)', line)
        if order:
            current_round = int(order[1])
        if '[market] ledger #' in line:
            stat = counts.setdefault(str(current_round), Counter())
            stat['ledger_lines'] += 1
            stat['reported_receipts'] += int(': no deal' not in line)
            stat['verified'] += int('verified True' in line)
            if current_round == 2 and 'verified True' in line:
                evidence.append({'line': n, 'text': line})
    report = json.loads((ROOT / 'test-report.json').read_text(encoding='utf-8-sig')) if (ROOT / 'test-report.json').exists() else {}
    write(ROOT / 'logs/market-status.json', {
        'recorded_at': now(), 'historical_market_test_passed': report.get('passed'),
        'market_test_runs': len(report.get('results', [])), 'buyer_activity_by_round': counts,
        'round2_verified_ledger_evidence': evidence,
        'live_registration_and_github_owner': 'Not checked by this offline script',
        'challenge2_events': dict(Counter(r.get('event') for r in read_events(
            ROOT / 'logs/challenge-2/avoidance.jsonl', 0)))})


def compare(a):
    before = json.loads((ROOT / 'logs/challenge-1/before-summary.json').read_text(encoding='utf-8'))
    after = json.loads((ROOT / 'logs/challenge-1/after-summary.json').read_text(encoding='utf-8'))
    has_market = before['market'].get('share') is not None and after['market'].get('share') is not None
    write(ROOT / 'logs/challenge-1/comparison.json', {
        'before': before, 'after': after, 'market_comparison_available': has_market,
        'note': 'Local conversion is not market share. Different windows/traffic can confound causal claims; no improvement is assumed.'})


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    s = sub.add_parser('snapshot')
    s.add_argument('--phase', choices=['before','after'], required=True)
    s.add_argument('--run-id', default='live-market')
    s.add_argument('--since', help='UTC ISO timestamp, same format as event log')
    s.add_argument('--until', help='UTC ISO timestamp, same format as event log')
    s.add_argument('--market-sales', type=int)
    s.add_argument('--own-market-sales', type=int)
    s.add_argument('--market-average', type=float)
    s.add_argument('--market-source', type=Path)
    s.set_defaults(func=snapshot)
    f = sub.add_parser('feedback')
    f.add_argument('--input', type=Path, required=True)
    f.add_argument('--name', default='26512076')
    f.set_defaults(func=feedback)
    sub.add_parser('status').set_defaults(func=status)
    sub.add_parser('compare').set_defaults(func=compare)
    args = p.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
