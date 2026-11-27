"""Read-only Round 3 reconciliation. Schema: week-08.html STEP 2, market_kit.py ACP.
A Round 2 verified result NEVER proves settlement. Synthetic tests are not evidence.
"""
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import urllib.request


def collect(here, out, *, fetch=None, after=-500, max_pages=20):
    """Bounded read-only capture. Use --after at/before R3 activation for complete R3 coverage."""
    if fetch is None:
        def fetch(path):
            with urllib.request.urlopen('https://market.ourolabs.ai' + path, timeout=30) as r:
                return json.load(r)
    out.mkdir(parents=True, exist_ok=False)
    board = fetch('/api/board')
    (out/'board.json').write_text(json.dumps(board, ensure_ascii=False, indent=2), encoding='utf-8')
    unique, requests, caught_up = {}, [], False
    for i in range(max_pages):
        endpoint = '/api/ledger?after=' + str(after)
        page = fetch(endpoint)
        (out/f'ledger-page-{i:03d}.json').write_text(json.dumps(page, ensure_ascii=False), encoding='utf-8')
        requests.append({'path': endpoint, 'rows': len(page)})
        if not page:
            caught_up = True
            break
        for row in page:
            seq = row['seq']
            if seq in unique and unique[seq] != row:
                raise ValueError('conflicting ledger sequence')
            unique[seq] = row
        nxt = max(r['seq'] for r in page)
        if nxt <= after:
            raise ValueError('ledger cursor did not advance')
        after = nxt
    ledger = [unique[k] for k in sorted(unique)]
    (out/'ledger.json').write_text(json.dumps(ledger, ensure_ascii=False), encoding='utf-8')
    local, hashes, partial = {}, {}, {}
    for name in ('sales.jsonl', 'talks.jsonl', 'policy-events.jsonl'):
        data = (here/name).read_bytes() if (here/name).exists() else b''
        (out/name).write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
        local[name] = []
        partial[name] = 0
        for line in data.decode('utf-8').splitlines():
            try:
                row = json.loads(line)
                if not row.get('test'):
                    local[name].append(row)
            except ValueError:
                partial[name] += 1
    recon = reconcile(ledger, local['sales.jsonl'])
    failed = Counter()
    for row in ledger:
        d = row.get('data', {})
        if row.get('round') == 3 and d.get('buyer') == 'fair-furniture':
            failed.update(k for k, v in (d.get('checks') or {}).items() if v is False)
    seqs = sorted(unique)
    gaps = sum(b - a - 1 for a, b in zip(seqs, seqs[1:]))
    summary = {
        'captured_at': datetime.now().astimezone().isoformat(), 'current_round': board.get('current'),
        'round3_status': 'not_active' if board.get('current', 0) < 3 else 'active_observation_only',
        'homework_complete': False, 'target': {'buyer':20,'seller':20},
        'ledger': {'first_seq': seqs[0] if seqs else None, 'last_seq': seqs[-1] if seqs else None,
                   'rows':len(ledger),'caught_up':caught_up,'internal_gaps':gaps,'requests':requests,
                   'coverage':'bounded observation window, not entire ledger'},
        'schema_source':'week-08.html lines 635-641: settle/order,buyer,item,amount,checkout_hash; market_kit.py ACP lines 291-415',
        'schema_live_round3_validated': any(r.get('round') == 3 and r.get('kind') == 'settle' for r in ledger),
        'reconciliation':recon, 'pricing_observations':metrics(local['policy-events.jsonl'],local['talks.jsonl'],recon['matched']),
        'market_refusal_checks_for_buyer':dict(failed),'local_sha256':hashes,'partial_local_lines':partial,
        'comparison_warning':'No causal before/after or policy superiority claim. Legacy talks lack start denominators; round, model, traffic and ally changes confound historical comparisons.'}
    (out/'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2),encoding='utf-8')
    return summary



def metrics(events, talks, settled):
    out = {}
    terminal = {r['task']: r for r in talks if r.get('task') and not r.get('test')}
    unique = {r['task']: r for r in events if r.get('task') and not r.get('test')}
    for policy in ('baseline', 'adaptive'):
        out[policy] = {}
        for item in ('lamp', 'chair', 'bookshelf'):
            ee = [r for r in unique.values() if r.get('policy') == policy and r.get('item') == item]
            tasks = {r['task'] for r in ee}
            tt = [terminal[t] for t in tasks if t in terminal]
            ss = [r for r in settled if r['seller'] and r['task'] in tasks]
            n = len(ee)
            out[policy][item] = {
                'started': n, 'terminal_observed': len(tt), 'pending_or_abandoned': n - len(tt),
                'first_ask_mean': sum(r['first_ask'] for r in ee)/n if n else None,
                'negotiation_deals': sum(r['outcome'] == 'deal' for r in tt),
                'negotiation_success': sum(r['outcome'] == 'deal' for r in tt)/n if n else None,
                'settled': len(ss), 'settlement_success': len(ss)/n if n else None,
                'settled_average_price': sum(r['amount'] for r in ss)/len(ss) if ss else None,
                'settled_margin_sum': sum(r['margin'] for r in ss),
                'rejection_reasons': dict(Counter(r['outcome'] for r in tt if r['outcome'] != 'deal'))}
    return out


def reconcile(ledger, sales, nick='fair-furniture'):
    orders, results = {}, {}
    for row in ledger:
        if row.get('round') != 3:
            continue
        data = row.get('data', {})
        oid = data.get('order')
        if oid and row.get('kind') == 'order':
            orders.setdefault(oid, []).append(data)
        if oid and row.get('kind') == 'result':
            results.setdefault(oid, []).append(data)
    local = {r['task']: r for r in sales if r.get('task') and not r.get('test')}
    matched, reasons, seen = [], Counter(), set()
    for row in ledger:
        if row.get('kind') != 'settle' or row.get('round') != 3:
            continue
        d = row.get('data', {})
        oid = d.get('order')
        if not oid or oid in seen:
            reasons['missing_or_duplicate_order'] += 1
            continue
        seen.add(oid)
        oo, rr = orders.get(oid, []), results.get(oid, [])
        if len(oo) != 1 or len(rr) != 1:
            reasons['missing_or_ambiguous_exact_order_result'] += 1
            continue
        o, r = oo[0], rr[0]
        rec = r.get('receipt') or {}
        if (r.get('verified') is not True or not d.get('checkout_hash')
                or not (o.get('buyer') == r.get('buyer') == d.get('buyer'))
                or not (o.get('item') == rec.get('item') == d.get('item'))
                or type(d.get('amount')) is not int or d['amount'] != rec.get('price')
                or not rec.get('task')):
            reasons['settlement_order_result_mismatch'] += 1
            continue
        sale = local.get(rec['task'])
        seller = bool(sale and sale.get('item') == d['item'] and sale.get('price') == d['amount'])
        matched.append({'order': oid, 'seq': row.get('seq'), 'task': rec['task'], 'item': d['item'],
                        'amount': d['amount'], 'buyer': d['buyer'] == nick, 'seller': seller,
                        'policy': sale.get('policy', 'legacy-baseline') if seller else None,
                        'margin': d['amount'] - sale['reserve'] if seller else None})
    return {'buyer_settled': sum(r['buyer'] for r in matched),
            'seller_settled': sum(r['seller'] for r in matched), 'matched': matched,
            'unreconciled_reasons': dict(reasons),
            'scope': 'only exact order + verified R3 result + R3 settle matches in collected window; not lifetime totals'}
