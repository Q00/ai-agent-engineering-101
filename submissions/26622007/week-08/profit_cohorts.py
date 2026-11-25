"""Read-only paid-sale cohorts. Never treat open warranties or missing costs as zero risk."""
import json
import os
import re
import time

import market_model


def read_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def read_rows(path):
    out = []
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    out.append(row)
    except OSError:
        pass
    return out


def summarize(rows, mine, negotiations, losses, now, start=0):
    """Join own settled seq -> unit -> paid memory cost. Refunds stay with sale cohorts.

    Recompute refunds from the cutoff ledger, not immutable loss_watch estimates made
    before the order-link fix. An order-bearing break must match that order; only old
    records without an order use the chronological fallback. No refund receipt is claimed.
    Maturity uses six hours even if a seller offered a shorter warranty.
    """
    costs, warranties = {}, {}
    for n in negotiations:
        if n.get('result') == 'deal' and n.get('unit'):
            warranties[n['unit']] = n.get('warranty')
            if isinstance(n.get('cost'), (int, float)):
                costs[n['unit']] = n['cost']
    visible = {r['seq']: r for r in rows if r.get('seq') is not None and (r.get('ts') or 0) <= now}
    orders = {r.get('order') for r in visible.values() if r.get('k') == 'settle' and r.get('order')}
    unmatched = [r for r in visible.values() if r.get('k') == 'broken' and r.get('order') and r['order'] not in orders]
    eligible = [r for r in visible.values() if r not in unmatched]
    refunds, unknown = {}, set()
    for broken, sale in market_model.link_broken(eligible):
        seq = sale['seq']
        if str(seq) not in mine:
            continue
        warranty = sale.get('warranty')
        if not isinstance(warranty, (int, float)):
            warranty = warranties.get(mine[str(seq)].get('unit'))
        if not isinstance(warranty, (int, float)):
            unknown.add(seq)
        elif 0 <= broken['ts'] - sale['ts'] <= warranty * 3600:
            refunds[seq] = sale.get('amount') or 0
    legacy = {}
    for loss in losses:
        match = re.search(r'ledger #(\d+)', loss.get('detail', ''))
        if match and loss.get('kind') == 'break_refund' and loss.get('ts', now + 1) <= now:
            legacy[int(match[1])] = loss.get('amount', 0)
    def empty():
        return dict(paid=0, revenue=0, known_cost=0, missing_cost=0, refunds=0,
                    refunded_sales=0, unknown_warranty=0, known_net=0, net=None, net_per_sale=None)
    groups, seen = {}, set()
    for r in sorted(rows, key=lambda x: x.get('ts') or 0):
        seq = r.get('seq')
        if r.get('k') != 'settle' or seq in seen or str(seq) not in mine or not start <= (r.get('ts') or 0) <= now:
            continue
        seen.add(seq)
        age = now - r['ts']
        sku = r.get('sku') or 'unknown'
        cohort = groups.setdefault(sku, {'all': empty(), 'mature_6h': empty(), 'open_6h': empty(), 'by_grade': {}})
        grade = cohort['by_grade'].setdefault(r.get('grade') or 'unknown', {'all': empty(), 'mature_6h': empty()})
        targets = [cohort['all'], cohort['mature_6h' if age >= 21600 else 'open_6h'], grade['all']]
        if age >= 21600:
            targets.append(grade['mature_6h'])
        cost = costs.get(mine[str(seq)].get('unit'))
        refund = refunds.get(seq, 0)
        for t in targets:
            t['paid'] += 1
            t['revenue'] += r.get('amount') or 0
            t['refunds'] += refund
            t['refunded_sales'] += seq in refunds
            t['unknown_warranty'] += seq in unknown
            if cost is None:
                t['missing_cost'] += 1
            else:
                t['known_cost'] += cost
                t['known_net'] += (r.get('amount') or 0) - cost - refund
    def finish(node):
        if 'paid' in node:
            if node['paid'] and not node['missing_cost'] and not node['unknown_warranty']:
                node['net'] = node['known_net']
                node['net_per_sale'] = round(node['net'] / node['paid'], 2)
        else:
            for v in node.values():
                if isinstance(v, dict):
                    finish(v)
    finish(groups)
    latest = max((r.get('ts') or 0 for r in rows if (r.get('ts') or 0) <= now), default=0)
    return {'as_of': now, 'start': start, 'maturity_hours': 6,
            'ledger_latest_ts': latest or None,
            'ledger_lag_seconds': round(now - latest) if latest else None,
            'refund_basis': 'cutoff ledger order-linked break within warranty; no-order fallback inferred; not a refund receipt',
            'refund_reconciliation': {'legacy_count': len(legacy), 'legacy_amount': sum(legacy.values()),
                                      'ledger_count': len(refunds), 'ledger_amount': sum(refunds.values()),
                                      'unmatched_order_breaks': len(unmatched),
                                      'changed_sale_seqs': sorted(k for k in set(legacy) | set(refunds)
                                                                  if legacy.get(k) != refunds.get(k))},
            'net_basis': 'paid revenue minus unit cost minus linked refunds; excludes unsold stock/storage',
            'skus': groups}


def build(root, now=None, start=0):
    md = os.path.join(root, '.market')
    return summarize(read_rows(os.path.join(md, 'ledger_rows.jsonl')),
                     read_json(os.path.join(md, 'loss_state.json'), {}).get('mine', {}),
                     read_json(os.path.join(root, 'memory', 'seller_memory_r3.json'), {}).get('negotiations', []),
                     read_rows(os.path.join(root, 'memory', 'agent', 'losses.jsonl')),
                     time.time() if now is None else now, start)
