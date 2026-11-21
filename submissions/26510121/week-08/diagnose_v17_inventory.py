"""Read-only inventory audit and explicitly unexecuted numerical scenarios.

No network, model calls, journal writes, registrations or trading. Freeze inputs
before analysis so the live agents can continue operating independently.
"""
from collections import Counter, defaultdict
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
import sys
import time

from autonomous_status import ReadJournal
from r3.state import ROOT, read_json, policy
from r3.economy import cash_capacity, restock_candidates
from r3.shop_growth import inventory_limit, target_stock
from r3.adaptive import effective

KST = timezone(timedelta(hours=9))
KINDS = ['shop_settled', 'stockout', 'customer_outcome', 'customer_visit',
         'shop_prepared_plan_used', 'restock_decision', 'restock_blocked',
         'supply_blocked', 'supply_execution_status', 'market_research_published',
         'market_research_deferred', 'market_control', 'market_control_error']


def kst(at):
    return datetime.fromtimestamp(at, KST).isoformat()


class FrozenJournal(ReadJournal):
    def __init__(self, connection, cutoff, files):
        super().__init__(connection, cutoff)
        self.files = files

    def snapshot(self, name, default=None):
        return copy.deepcopy(self.files.get(name, default))


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    now = time.time()
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    files, file_times = {}, {}
    for name in ['runtime.json', 'inventory.json', 'board.json', 'catalog.json',
                 'market_stock_counts.json', 'market-control.json', 'operating-policy-v16.json',
                 'market-research-v15.json', 'shop-plan-v15.json', 'shop-health.json']:
        path = ROOT/'.market'/name
        file_times[name] = path.stat().st_mtime
        files[name] = read_json(path, {})
    start = files['runtime.json']['started_at']
    inv = files['inventory.json']
    end = min(now, file_times['inventory.json'])
    lo = start-(end-start)
    catalog = files['catalog.json']
    cfg = policy()
    board_source_path = Path(sys.argv[1]) if len(sys.argv)>1 else None
    board_source = read_json(ROOT/board_source_path) if board_source_path else None
    baseline_path = 'logs/autonomous-deploy-before-20261111T104125973451Z.json'
    baseline = read_json(ROOT/baseline_path)
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro', uri=True) as db:
        db.execute('BEGIN')
        j = FrozenJournal(db, end, files)
        raw = {kind: [dict(json.loads(data), _ts=at, _event_id=eid) for eid, at, data in db.execute(
            'SELECT id,ts,data FROM events WHERE kind=? AND ts>=? AND ts<=? ORDER BY ts,rowid',
            (kind, lo, end))] for kind in KINDS}
        kv = {key: j.get(key) for key in ['merchant_stock_timeline', 'merchant_context_v5',
              'board_checked_at', 'market_control', 'cycle-market-analytics', 'market_rules',
              'research-last-result', 'cycle-meta-state', 'adaptive:shop', 'feedback-last-policy']}
        capacity = cash_capacity(inv, j, cfg, end)
        effective_cfg, strategy = effective(j, 'shop', base=cfg)
        # Numerical scenarios force a hypothetical open window and omit only the
        # visibility pause, never change live state or affordability calculations.
        virtual = copy.deepcopy(inv)
        virtual['restock'] = {'open': True, 'opens_at': end-1, 'closes_at': end+180}
        control = dict(j.get('market_control', {}), pause_restock=False)
        j.memory['market_control'] = control
        scenarios = []
        for cycles, cap in [(1, 6), (2, 6), (3, 6), (3, 20)]:
            scenario = dict(effective_cfg, shop_demand_horizon_cycles=cycles,
                shop_public_demand_caps={s: cap for s in effective_cfg.get('shop_public_demand_skus', [])})
            rows = restock_candidates(virtual, catalog, [], j, scenario, end)
            scenarios.append({'NOT_EXECUTED': True, 'cover_cycles': cycles, 'public_cap': cap,
                'lines': len(rows), 'units': sum(r['qty'] for r in rows),
                'capital_commitment': sum(r['capital_commitment'] for r in rows),
                'book_scenario': sum(r['expected_delta']['book'] for r in rows),
                'rows': [{'sku': r['sku'], 'name': catalog[r['sku']]['name'], 'qty': r['qty'],
                    'rate': r['evidence']['arrival_rate_scenario'],
                    'own_positive_paid_available': r['evidence']['growth'].get('positive_paid_while_available', 0),
                    'own_paid_last_hour': r['evidence']['own_sales_last_hour'],
                    'held': sum(u['sku']==r['sku'] for u in inv['units']),
                    'capital': r['capital_commitment'], 'book_scenario': r['expected_delta']['book']}
                    for r in rows]})
    def events(kind, after=start, before=end):
        return [r for r in raw[kind] if after<=r['_ts']<=before]
    sales = {r['unit']: r for r in events('shop_settled')}
    timeline = kv['merchant_stock_timeline']
    initial = max((r for r in timeline if r['at']<=start), key=lambda r:r['at'], default=None)
    console_path = ROOT/'logs'/(files['runtime.json']['launch_id']+'.txt')
    console_text = console_path.read_text(encoding='utf-8', errors='replace')
    supplies, refused = [], []
    day = datetime.fromtimestamp(start, KST).date().isoformat()
    for line in console_text.splitlines():
        m = re.match(r'^(\d\d:\d\d:\d\d) \[supply\] (\d+) x (.*?) for (\d+), balance (\d+)', line)
        if m:
            at = datetime.fromisoformat(day+'T'+m[1]).replace(tzinfo=KST).timestamp()
            if start<=at<=end:
                supplies.append({'at':at, 'time_kst':m[1], 'qty':int(m[2]), 'name':m[3],
                                 'cost':int(m[4]), 'balance_after':int(m[5])})
        elif '[supply]' in line and 'refused:' in line:
            refused.append(line)
    complete = [r for r in events('supply_execution_status') if r['status']=='complete']
    # A successful POST is journaled before the kit refreshes inventory and
    # prints the confirmation. Join the ordered successes one-to-one rather
    # than dropping slow refreshes with an arbitrary two-second tolerance.
    assert len(supplies)==len(complete), 'Console and successful POST counts differ'
    for row, match in zip(supplies, complete):
        assert row['qty']==match['qty']
        assert row['name']==catalog[match['sku']]['name']
        assert -1<=row['at']-match['finished_at']<=30
        row['sku'], row['window'] = match['sku'], match['window']
    acquired = sum(r['qty'] for r in supplies)
    plan_rows = events('shop_prepared_plan_used')
    windows = []
    for row in plan_rows:
        got = [s for s in supplies if s.get('window')==row['window'][0]]
        windows.append({'opens_kst':kst(row['window'][0]), 'decision_kst':kst(row['_ts']),
            'planned_lines':len(row['buy']), 'planned_units':sum(b['qty'] for b in row['buy']),
            'acquired_lines':len(got), 'acquired_units':sum(s['qty'] for s in got),
            'actual_cost':sum(s['cost'] for s in got),
            'planned_qty_histogram':dict(Counter(b['qty'] for b in row['buy']))})
    stats = {}
    for label, a, b in [('before_equal_duration',lo,start), ('v17',start,end)]:
        paid = {r['unit']:r for r in events('shop_settled',a,b)}
        stats[label] = {'start_kst':kst(a), 'end_kst':kst(b), 'hours':(b-a)/3600,
            'sales':len(paid), 'sales_per_hour':len(paid)/((b-a)/3600),
            'revenue':sum(r['price'] for r in paid.values()),
            'gross_margin':sum(r['price']-r['cost'] for r in paid.values()),
            'below_cost_sales':sum(r['price']<r['cost'] for r in paid.values()),
            'by_kind':dict(Counter(catalog.get(r['sku'],{}).get('kind') for r in paid.values())),
            'raw_stockout_events':len(events('stockout',a,b)),
            'distinct_stockout_tasks':len({r['task'] for r in events('customer_outcome',a,b) if r.get('outcome')=='stockout'}),
            'restock_block_reasons':dict(Counter(r['reason'] for r in events('restock_blocked',a,b))),
            'supply_status':dict(Counter(r['status'] for r in events('supply_execution_status',a,b)))}
    counts = Counter(u['sku'] for u in inv['units'])
    merchant = kv['merchant_context_v5'].get('skus', {})
    sku_rows = []
    for sku in set(counts)|{r['sku'] for r in sales.values()}:
        g = merchant.get(sku,{})
        n = g.get('positive_paid_while_available',0)
        rate = g.get('paid_arrival_rate_scenario',0) if n>=3 else g.get('arrival_rate_scenario',0)
        rate *= effective_cfg.get('shop_sku_trend_factors',{}).get(sku,1)
        qualified = sku in effective_cfg.get('shop_public_demand_skus',[])
        sku_rows.append({'sku':sku, 'name':catalog.get(sku,{}).get('name'), 'kind':catalog.get(sku,{}).get('kind'),
            'held':counts[sku], 'sold':sum(r['sku']==sku for r in sales.values()),
            'positive_paid_available_6h':n, 'rate':rate,
            'target_1cycle':target_stock(rate,n,dict(effective_cfg,shop_demand_horizon_cycles=1),qualified),
            'target_3cycles':target_stock(rate,n,dict(effective_cfg,shop_demand_horizon_cycles=3),qualified),
            'public_qualified':qualified, 'exposure_hours':g.get('available_hours_observed')})
    policy_history = [{'at_kst':kst(r['_ts']), 'operating_policy':r.get('operating_policy'),
                       'intents':r.get('operating_intents'), 'summary':r.get('summary')}
                      for r in events('market_research_published')]
    peer_comparison = []
    if board_source:
        board = board_source['responses']['/api/board']['data']
        board_end = board_source['responses']['/api/board']['finished_at']
        old = {r['name']:r for r in baseline['board']['shops']}
        top = {r['name'] for r in sorted(board['shops'],key=lambda r:r.get('all') or 0,reverse=True)[:5]}|{'seochanit'}
        for r in board['shops']:
            if r['name'] not in top or r['name'] not in old:continue
            prior = old[r['name']]
            peer_comparison.append({'name':r['name'], 'stock_before':prior.get('stock_units'),
                'stock_after':r.get('stock_units'), 'alive':r.get('alive'), 'all_before':prior.get('all'),
                'all_after':r.get('all'), 'book_delta':r['book']-prior['book'],
                'deals_delta':r['deals']-prior['deals'], 'revenue_delta':r['revenue']-prior['revenue'],
                'book_per_hour':(r['book']-prior['book'])/((board_end-baseline['at'])/3600)})
    result = {'at_kst':kst(now), 'runtime_start_kst':kst(start), 'inventory_cutoff_kst':kst(end),
        'sources':{'baseline':baseline_path, 'public_board':str(board_source_path) if board_source_path else None,
                   'console':str(console_path.relative_to(ROOT)), 'database':'.market/r3.sqlite3'},
        'file_times':{n:kst(t) for n,t in file_times.items()}, 'periods':stats,
        'inventory':{'units':len(inv['units']), 'skus':len(counts), 'cost':sum(u['cost'] for u in inv['units']),
            'by_kind':dict(Counter(u['kind'] for u in inv['units'])), 'balance':inv['balance'],
            'capacity':capacity, 'ceiling':inventory_limit(cfg), 'initial_timeline_at_kst':kst(initial['at']) if initial else None,
            'initial_timeline_units':sum(initial['counts'].values()) if initial else None,
            'actual_supply_units':acquired, 'actual_supply_cost':sum(r['cost'] for r in supplies),
            'sold':len(sales), 'accounting_residual':sum(initial['counts'].values())+acquired-len(sales)-len(inv['units']) if initial else None},
        'supply_windows':windows, 'actual_supplies':supplies, 'supply_refusals':refused,
        'policy_history':policy_history, 'effective_shop_patch':{k:v for k,v in effective_cfg.items() if k.startswith('shop_')},
        'research_deferrals':dict(Counter(r['reason'] for r in events('market_research_deferred'))),
        'stockout_skus':[{'sku':s,'name':catalog.get(s,{}).get('name'),'events':n,'held':counts[s],
                         'kind':catalog.get(s,{}).get('kind')}
                        for s,n in Counter(r.get('sku') for r in events('stockout')).most_common(12)],
        'stock_and_targets':sorted(sku_rows,key=lambda r:r['sold'],reverse=True),
        'peers_since_baseline':peer_comparison, 'numerical_scenarios':scenarios,
        'limitations':['Scenarios are unexecuted model forecasts, not proof of extra sales or causal gains.',
            'Public board and local inventory have separately recorded observation times.',
            'Raw stockout events include repeated negotiation turns; distinct tasks are also reported.',
            'Inventory residual includes timing, expiry/disposal and other changes; no waste attribution without evidence.',
            'Baseline board precedes runtime start; local before/after periods use the exact runtime start.',
            'Gross margins precede future refunds and storage.']}
    source_path = ROOT/'logs'/('v17-stock-inputs-'+stamp+'.json')
    source_path.write_text(json.dumps({'at':now,'files':files,'file_times':file_times,'kv':kv,'events':raw,
        'config':cfg,'baseline':baseline,'console':console_text},ensure_ascii=False,indent=2),encoding='utf-8')
    result['frozen_inputs'] = str(source_path.relative_to(ROOT))
    path = ROOT/'logs'/('v17-stock-diagnosis-'+stamp+'.json')
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('RESULT',path)
    print(json.dumps({k:result[k] for k in ['at_kst','runtime_start_kst','inventory','periods','supply_windows',
                                          'research_deferrals','peers_since_baseline']},ensure_ascii=False,indent=2))
    print('SCENARIOS',json.dumps([{k:v for k,v in r.items() if k!='rows'} for r in scenarios]))
    print('TARGETS',json.dumps(result['stock_and_targets'][:10],ensure_ascii=False))


if __name__=='__main__':
    main()
