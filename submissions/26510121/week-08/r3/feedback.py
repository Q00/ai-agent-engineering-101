"""Expiring, evidence-bound operating policies and execution/outcome feedback.

Background roles measure the market; Main reads a small typed packet before
building candidates. Reviewed intents can select bounded operating responses.
They cannot change payment/quality/price caps or authorize a supply purchase.
"""
from collections import Counter
import json
import math
import statistics
import time
from .state import atomic_json, identity

REVISION = 'objective-feedback-v23-1'
FILE = 'operating-policy-v16.json'
LIMITS = {'shop': {'shop_demand_horizon_cycles': (1., None), 'shop_inventory_ceiling': (120, 240),
                    'shop_sku_trend_factors': None,'shop_public_demand_skus':None,'shop_public_demand_caps':None},
          'buyer': {'buyer_frontier_category_enabled': (False, True),
                    'buyer_frontier_min_orders':(4,12),'buyer_frontier_recheck_seconds':(60,300),
                    'buyer_forum_focus':None}}


def valid_patch(role, patch):
    if not isinstance(patch, dict) or set(patch)-LIMITS[role].keys():
        return False
    for key, value in patch.items():
        if key=='buyer_forum_focus':
            if value not in ('balanced','same_terms_price','acceptable_alternatives','mature_quality'):return False
        elif key=='shop_public_demand_caps':
            if not isinstance(value,dict) or len(value)>20 or any(not isinstance(k,str) or not k.startswith('G')
                or len(k)>64 or type(v) is not int or not 1<=v<=20 for k,v in value.items()):return False
        elif key=='shop_public_demand_skus':
            if not isinstance(value,list) or len(value)>20 or any(not isinstance(s,str) or not s.startswith('G') or len(s)>64 for s in value):return False
        elif key=='shop_sku_trend_factors':
            if (not isinstance(value,dict) or len(value)>20 or any(not isinstance(sku,str) or not sku.startswith('G') or len(sku)>64
                or type(factor) not in (int,float) or not math.isfinite(factor) or not .75<=factor<=1.5 for sku,factor in value.items())):return False
        elif key.endswith('_enabled'):
            if type(value) is not bool: return False
        elif (type(value) not in (int, float) or not math.isfinite(value) or value<LIMITS[role][key][0]
              or LIMITS[role][key][1] is not None and value>LIMITS[role][key][1]):
            return False
        elif key in {'shop_inventory_ceiling','buyer_frontier_min_orders','buyer_frontier_recheck_seconds'} and type(value) is not int:
            return False
    return True


def fresh(journal, now):
    rules = journal.get('market_rules', {})
    return bool(rules.get('compatible') and rules.get('buyer_compatible') and rules.get('sha256')
        and 0 <= now-rules.get('at', now) < 7200
        and -2 <= now-journal.get('board_checked_at', 0) < 180
        and -2 <= now-journal.get('public_feed_caught_up_at', 0) < 180)


def update(journal, config, now=None, force=False):
    if not config.get('objective_feedback_enabled'): return None
    now = time.time() if now is None else now
    previous = journal.snapshot(FILE, {})
    if not force and 0 <= now-previous.get('at', 0) < 45: return previous
    board = journal.snapshot('board.json', {})
    market = journal.get('cycle-market-analytics', {})
    rules = journal.get('market_rules', {})
    own = next((r for r in board.get('shops', []) if r.get('name') == 'seochanit'), {})
    with journal.connect() as db:
        def rows(kind, lo=now-3600):
            return [dict(json.loads(raw), _ts=at) for at, raw in db.execute(
                'SELECT ts,data FROM events WHERE kind=? AND ts>=? AND ts<=? ORDER BY ts,rowid', (kind, lo, now))]
        sales = list({r['unit']: r for r in rows('shop_settled')}.values())
        blocks = rows('restock_blocked')
        shortages = rows('stockout')
        from .policy_learning import epoch
        orders = {r['order']: r for r in rows('buyer_order') if r['_ts']>=epoch(journal)}
        results = {r['order']: r for r in rows('market_result') if r.get('order') in orders}
        purchases = {r['order']: r for r in rows('purchase')}
        attempts = rows('attempt')
        quotes = rows('quote_comparison')
        canonical_latest={r['order']:r for r in rows('outcome') if r.get('canonical')}
        mature_outcomes = {oid:r for oid,r in canonical_latest.items() if r.get('matured')
                           and r.get('refund_status')!='expected_by_warranty_rule'}
        mature_policy_rows = {}
        for oid in mature_outcomes:
            frozen = db.execute('SELECT value FROM kv WHERE key=?', ('feedback-order:'+oid,)).fetchone()
            if frozen: mature_policy_rows[oid] = json.loads(frozen[0])
        choices = rows('feedback_main_choice')
        prior_rows = db.execute('SELECT ts,data FROM events WHERE kind=? AND ts<=? AND ts>=? ORDER BY ts DESC LIMIT 1',
                                ('feedback_market_observation', now-1200, now-3900)).fetchall()
    segments = {}
    for kind in ('food', 'goods'):
        closed = {oid for oid in results if orders[oid].get('kind') == kind}
        segments[kind] = {'closed': len(closed), 'paid': len(closed & purchases.keys()),
                         'conversion': len(closed & purchases.keys())/max(1, len(closed)),
                         'quotes':sum(orders.get(r.get('order'),{}).get('kind')==kind for r in quotes),
                         'above_cap':sum(orders.get(r.get('order'),{}).get('kind')==kind and r.get('ask',0)>r.get('assessment',{}).get('cap',float('inf')) for r in quotes)}
    peers = {}
    for role in ('shops', 'buyers'):
        peers[role] = [{k: r.get(k) for k in ('name', 'all', 'book', 'revenue', 'deals', 'saved', 'forum', 'stock_units', 'alive')}
                      for r in board.get(role, []) if type(r.get('all')) in (int, float)]
    from .buyer_scoring import formula,same_formula
    observation = {'at': now, 'score_formula':formula(journal), 'board_at': journal.get('board_checked_at'), 'peers': peers}
    trends = {}
    if prior_rows:
        old = json.loads(prior_rows[0][1]); hours = (now-prior_rows[0][0])/3600
        for role in peers:
            if role=='buyers' and not same_formula(old,formula(journal)):continue
            baseline = {r['name']: r for r in old['peers'][role]}
            compared = []
            for row in peers[role]:
                p = baseline.get(row['name'])
                if not p: continue
                compared.append({'name': row['name'], **{f+'_per_hour': (row[f]-p[f])/hours
                    for f in ('book', 'deals', 'saved') if type(row.get(f)) in (int, float) and type(p.get(f)) in (int, float)}})
            metric = 'book_per_hour' if role == 'shops' else 'saved_per_hour'
            top = sorted(compared, key=lambda r: r.get(metric, 0), reverse=True)[:5]
            ours = [r for r in compared if r['name'] == 'seochanit']
            trends[role] = top + [r for r in ours if r not in top]
    windows = market.get('windows', {})
    recent = windows.get('20', {}).get('trades', 0)
    preceding = max(0, windows.get('60', {}).get('trades', 0)-recent)
    shift_ratio = (recent+10)/(preceding/2+10)
    regime = 'unverified' if not fresh(journal, now) else ('shock' if recent+preceding >= 60 and (shift_ratio > 2 or shift_ratio < .5) else 'normal')
    inv = journal.snapshot('inventory.json', {})
    cap = journal.get('market_control', {}).get('cash_capacity', {})
    positive = sum(r['price']-r['cost'] for r in sales)
    live_peer_stock = [r['stock_units'] for r in peers['shops'] if r['name'] != 'seochanit'
                       and r.get('alive') is True and type(r.get('stock_units')) is int and r['stock_units'] > 0]
    median_stock = statistics.median(live_peer_stock) if live_peer_stock else None
    issued = journal.get('research-last-result', {})
    reviewed = (not config.get('llm_disabled') and (issued.get('operating_actions_reviewed') is True or issued.get('trial_review_approved') is True)
                and issued.get('rules_hash') == rules.get('sha256')
                and 0 <= now-issued.get('at', 0) < 1200 and now < issued.get('expires_at', 0))
    intents = issued.get('operating_intents', []) if reviewed else []
    from .research import valid_operating
    selected=issued.get('operating_policy',{})
    agent_selected=config.get('agent_policy_selection_enabled') and reviewed and valid_operating(selected)
    patches = {'shop': {}, 'buyer': {}}
    reasons = []
    evidence_ok = fresh(journal, now) and 0 <= now-market.get('at', 0) < 180 and board.get('round') == 3
    if evidence_ok and positive > 0 and len(sales) >= 5 and (len(blocks)>=2 or len(shortages) >= 3) and cap.get('spendable', 0) > 1000000:
        # Supply failures justify a larger coverage horizon only for SKUs with
        # repeat own paid demand. Candidate economics still reject bad units.
        from .limits import capital_caps
        cycles = min(3., 1.25 + min(1.75, len(blocks)/4)) if capital_caps(config) else 1.25+len(blocks)/4
        if agent_selected:cycles=selected['cover_cycles']
        if regime == 'shock' and capital_caps(config): cycles = min(cycles, 1.5)
        if 'stock_coverage' in intents:
            patches['shop']['shop_demand_horizon_cycles'] = cycles
            if config.get('shop_inventory_caps_enabled',True) and len(inv.get('units', [])) >= .8*config.get('shop_inventory_ceiling', 180):
                patches['shop']['shop_inventory_ceiling'] = min(240, config.get('shop_inventory_ceiling', 180)+30)
        else: reasons.append('stock_coverage_review_pending')
    if evidence_ok and segments['goods']['closed'] >= 20 and segments['goods']['conversion'] < .3:
        if 'buyer_price_gap' in intents:
            patches['buyer']['buyer_frontier_category_enabled'] = True
            if agent_selected:
                patches['buyer'].update(buyer_frontier_min_orders=selected['frontier_min_orders'],
                    buyer_frontier_recheck_seconds=selected['recheck_seconds'])
        else: reasons.append('buyer_frontier_review_pending')
    if config.get('objective_decision_enabled') and evidence_ok and 'buyer_information' in intents:
        focus=issued.get('forum_focus')
        if isinstance(focus,str) and valid_patch('buyer',{'buyer_forum_focus':focus}):
            patches['buyer']['buyer_forum_focus']=focus
    if evidence_ok and 'stock_coverage' in intents:
        factors={}
        qualified=[]
        caps={}
        for sku in market.get('top_skus',[])[:20]:
            n=sku.get('recent60',0);current=sku.get('recent20',0)
            if n>=12:
                ratio=(current+5)/(max(0,n-current)/2+5)
                factors[sku['sku']]=round(max(.75,min(1.5,1+(ratio-1)*.5))*20)/20
            if n>=20 and current>=10 and sku.get('buyers20',0)>=3:
                qualified.append(sku['sku'])
                if agent_selected and config.get('shop_inventory_caps_enabled',True):
                    caps[sku['sku']]=min(selected['initial_batch_limit'],20 if current>=50 and sku.get('buyers20',0)>=5 else 6)
        if factors and not config.get('book_growth_v23_enabled'):patches['shop']['shop_sku_trend_factors']=factors
        if qualified:patches['shop']['shop_public_demand_skus']=sorted(qualified)
        if caps:patches['shop']['shop_public_demand_caps']=caps
    if not evidence_ok: reasons.append('facts_stale_or_rules_unverified')
    if journal.get('market_control', {}).get('pause_restock'): reasons.append('fresh_supply_guards_still_required')
    # This semantic ID is stable when the policy is unchanged. Merely observing
    # another minute must not invalidate every prepared supply plan.
    policy_id = identity([REVISION, rules.get('sha256'), regime, patches])
    choice_counts = Counter(r.get('policy_id') or 'unattributed' for r in choices)
    linked_orders = {r.get('order'): r.get('policy_id') or 'unattributed' for r in choices if r.get('order')}
    linked_tasks = {r['task']: r.get('policy_id') for r in choices if r.get('task') and not r.get('preview')}
    outcomes = {}
    for oid, pid in linked_orders.items():
        if oid not in results: continue
        out = outcomes.setdefault(pid, {'closed_orders': 0, 'paid': 0})
        out['closed_orders'] += 1; out['paid'] += oid in purchases
    shop_outcomes = {}
    for sale in sales:
        pid = linked_tasks.get(sale.get('task'))
        if pid is None: continue
        out = shop_outcomes.setdefault(pid, {'paid': 0, 'gross_revenue': 0, 'gross_margin': 0})
        out['paid'] += 1; out['gross_revenue'] += sale['price']; out['gross_margin'] += sale['price']-sale['cost']
    canonical = {}
    from .buyer_scoring import formula,same_formula
    score_version=formula(journal)
    for oid,frozen in mature_policy_rows.items():
        if not same_formula(frozen,score_version) or not same_formula(mature_outcomes[oid],score_version):continue
        value=mature_outcomes[oid].get('saved_contribution')
        if type(value) not in (int,float):continue
        row=canonical.setdefault(frozen['id'], {'mature_paid':0,'broken':0,'sum_final_saved_contribution':0,'saved_delta_at_assignment_sum':0})
        row['mature_paid']+=1;row['broken']+=bool(mature_outcomes[oid].get('broken'))
        row['sum_final_saved_contribution']+=value
        if type(frozen.get('saved_at_assignment')) in (int,float):
            row['saved_delta_at_assignment_sum']+=(value-frozen['saved_at_assignment'])/(frozen.get('deals_at_assignment',0)+101)
    from .meta import rank_targets
    scoring_board=dict(board,buyer_metric=rules.get('buyer_metric','saved'),buyer_forum_metric=rules.get('buyer_forum_metric','raw'))
    packet = {'schema': 1, 'revision': REVISION, 'id': policy_id, 'at': now, 'expires_at': now+150,
        'rules_hash': rules.get('sha256'), 'status': 'active' if evidence_ok else 'deferred', 'regime': regime,
        'patches': patches, 'reasons': reasons, 'objective': 'maximize official Shop ALL + Buyer ALL',
        'facts': {'external_20min_vs_prior40_rate_ratio': shift_ratio, 'external_trades_20min': recent,
            'stock_units': len(inv.get('units', [])), 'median_online_peer_stock': median_stock,
            'own_paid_sales_60min': len(sales), 'own_gross_margin_60min': positive,
            'restock_block_reasons': dict(Counter(r.get('reason') for r in blocks)), 'stockouts_60min': len(shortages),
            'buyer_segments_60min': segments, 'technical_attempts_60min': sum(r.get('kind') in ('ReadTimeout','ConnectTimeout','ConnectError') for r in attempts)},
        'peer_trends': trends, 'research_at': issued.get('at'), 'reviewed_intents': intents,
        'policy_authority':'agent_selected' if agent_selected else 'bounded_baseline',
        'source_times':{'board':journal.get('board_checked_at'),'feed':journal.get('public_feed_caught_up_at'),'analytics':market.get('at')},
        'objective_targets':{'shop':rank_targets(scoring_board,'shop'),'buyer':rank_targets(scoring_board,'buyer')},
        'execution_feedback': {'main_choices_by_policy': dict(choice_counts), 'buyer_closed_results_by_policy': outcomes,
            'buyer_canonical_mature_results_by_policy':canonical,
            'shop_paid_results_by_policy': shop_outcomes, 'shop_unlinked_paid': len(sales)-sum(r['paid'] for r in shop_outcomes.values()),
            'note': 'Observed results, not randomized causal improvement. Gross margins precede future refunds/storage; no maturity claim.'},
        'mature_promotion': journal.get('framework-plan', {}).get('evaluation', {}),
        'constraints': ['no_price_cap_change', 'full_refund_reserve', 'live_supply_checks', 'no_unverified_profile_promotion']}
    packet['checksum'] = identity(packet)
    atomic_json(journal.md/FILE, packet)
    journal.add('feedback_policy_created', packet)
    if evidence_ok: journal.add('feedback_market_observation', observation)
    journal.put('feedback-last-policy', packet)
    return packet


def get(journal, config, now=None):
    if not config.get('objective_feedback_enabled'): return None
    now = time.time() if now is None else now
    from .decision_view import fact
    row = fact(journal, 'operating-policy-v16', lambda: journal.snapshot(FILE, {}))
    if (row.get('schema') != 1 or row.get('revision') != REVISION or row.get('status') != 'active'
            or row.get('checksum') != identity({k:v for k,v in row.items() if k != 'checksum'})
            or not 0 <= now-row.get('at', 0) < 150 or now >= row.get('expires_at', 0)
            or row.get('rules_hash') != journal.get('market_rules', {}).get('sha256')
            or not fresh(journal, now) or any(not valid_patch(role, row.get('patches', {}).get(role)) for role in LIMITS)):
        return None
    return row


def apply(journal, config, role, order=None, now=None):
    now = time.time() if now is None else now
    if not config.get('objective_feedback_enabled'): return config
    key = 'feedback-order:'+str(order) if order else None
    frozen = journal.get(key) if key else None
    row = frozen if frozen and frozen.get('rules_hash') == journal.get('market_rules', {}).get('sha256') else get(journal, config, now)
    if row is None: return config
    if key and (frozen is None or frozen.get('rules_hash')!=row.get('rules_hash')):
        # Freeze one small decision policy for all checkout-recovery calls on
        # the same order; the actual economic and payment guards stay fresh.
        row = {k:row[k] for k in ('id','rules_hash','patches','at','regime')}
        buyer=next((r for r in journal.snapshot('board.json',{}).get('buyers',[]) if r.get('name')=='seochanit'),{})
        from .buyer_scoring import formula
        row.update(saved_at_assignment=buyer.get('saved'),deals_at_assignment=buyer.get('deals',0),score_formula=formula(journal))
        journal.put(key, row)
    patch = row.get('patches', {}).get(role, {})
    if not valid_patch(role, patch): return config
    return dict(config, **patch, _feedback_policy_id=row['id'])


def context(journal, config):
    row = get(journal, config)
    if not row: return {'status': 'no_fresh_operating_policy', 'applied_policy_id': config.get('_feedback_policy_id')}
    return {'id': row['id'], 'applied_policy_id': config.get('_feedback_policy_id'), 'regime': row['regime'],
            'patches': row['patches'], 'reasons': row['reasons'], 'research_at': row['research_at'],
            'objective': row['objective']}


def record_choice(journal, config, role, decision, selected, context):
    if not config.get('objective_feedback_enabled'): return
    try:
        row = {'decision': decision, 'policy_id': config.get('_feedback_policy_id'), 'role': role,
            'order': (context.get('order') or {}).get('order'), 'task': context.get('task'),
            'operation': context.get('operation', 'quote' if role=='shop' else None),
            'chosen': selected['id'], 'expected_delta': selected.get('expected_delta'),
            'objective_utility': selected.get('objective_utility'), 'preview': bool(context.get('preview')),
            'reason': 'selected_by_official_objective_surrogate_after_operating_policy'}
        if context.get('_feedback_policy_id'):row['policy_id']=context['_feedback_policy_id']
        journal.add('feedback_main_choice', row, role=role)
    except Exception:
        pass  # Telemetry cannot change the selected trade.
