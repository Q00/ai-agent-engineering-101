"""Background objective evidence and bounded context for the research agents.

Numerical supply alternatives are forecasts, never execution authorizations.
Shadow writes stay in memory; Main's negotiation/payment path never calls this.
"""
from collections import Counter, defaultdict
import copy
import json
import math
import time
from .state import identity

FOCUS = ('balanced', 'same_terms_price', 'acceptable_alternatives', 'mature_quality')
FACT_BYTES = 23000


class Shadow:
    def __init__(self, journal):
        self.journal, self.memory = journal, {}

    def __getattr__(self, name):
        return getattr(self.journal, name)

    def get(self, key, default=None):
        return self.memory[key] if key in self.memory else self.journal.get(key, default)

    def put(self, key, value):
        self.memory[key] = value


def observe_forum(journal, now):
    """Associate only returned server post IDs, never guess a post from its text."""
    deliveries = {r['post_id']: r for r in journal.events('forum_delivery', 2000)
                  if r.get('role') == 'buyer' and type(r.get('post_id')) is int
                  and r.get('information_kind') in FOCUS[1:] and r.get('status') in (200, 201)}
    for post in journal.snapshot('forum.json', []):
        if not isinstance(post, dict) or post.get('role') != 'buyer' or post.get('author') != 'seochanit':
            continue
        source = deliveries.get(post.get('id'))
        score = post.get('score')
        if not source or type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
            continue
        at = post.get('ts')
        if type(at) not in (int, float) or not 0 <= now-at <= 86400:
            continue
        row = {'post_id': post['id'], 'information_kind': source['information_kind'],
               'score': score, 'age_seconds': now-at, 'post_at': at,
               'likes': post.get('likes'), 'dislikes': post.get('dislikes')}
        journal.add('forum_objective_observation', row,
                    eid='forum-score:'+identity([post['id'], score]), role='buyer', ts=now)


def buyer_evidence(journal, now):
    from .buyer_scoring import formula,same_formula,MARGIN
    version=formula(journal)
    from .policy_learning import epoch
    diagnostic_start=max(now-3600,epoch(journal))
    orders = {r['order']: r for r in journal.events('buyer_order', 4000) if diagnostic_start<=r['_ts']<=now}
    results = {r['order']: r for r in journal.events('market_result', 4000) if r.get('order') in orders}
    purchases = {r['order']: r for r in journal.events('purchase', 10000)}
    canonical = {}
    for row in reversed(journal.events('outcome', 10000)):
        if row.get('canonical') and same_formula(row,version):
            canonical[row['order']] = row
    segments = {}
    for kind in ('food', 'goods'):
        closed = {o for o in results if orders[o].get('kind') == kind}
        paid = closed & purchases.keys()
        matured = [canonical[o]['saved_contribution'] for o in paid if o in canonical
                   and canonical[o].get('matured') and type(canonical[o].get('saved_contribution')) in (int,float)
                   and canonical[o].get('refund_status') != 'expected_by_warranty_rule']
        initial = [purchases[o]['initial_saved_contribution'] for o in paid
                   if same_formula(purchases[o],version) and type(purchases[o].get('initial_saved_contribution')) in (int,float)]
        durations = [max(0, results[o]['_ts']-orders[o]['_ts']) for o in closed]
        segments[kind] = {'closed': len(closed), 'paid': len(paid),
            'conversion': len(paid)/max(1,len(closed)), 'active_seconds': sum(durations),
            'initial_saved_paid_n':len(initial), 'initial_saved_paid_mean':sum(initial)/len(initial) if initial else None,
            'confirmed_mature_paid_n':len(matured),
            'confirmed_mature_saved_paid_mean':sum(matured)/len(matured) if matured else None,
            'quality_pending':len(paid)-len(matured)}
    # A 60-minute order cohort cannot contain six-hour goods outcomes. Supply
    # an older, explicitly aged cohort as well rather than reporting perpetual
    # zero maturity and conflating pending purchases with confirmed performance.
    mature_cohorts={}
    for kind in ('food','goods'):
        eligible=[p for p in purchases.values() if p.get('kind')==kind
                  and 21600 <= now-p.get('paid_at',p['_ts']) <= 172800]
        confirmed=[canonical[p['order']] for p in eligible if p['order'] in canonical
                   and canonical[p['order']].get('matured')
                   and canonical[p['order']].get('refund_status')!='expected_by_warranty_rule'
                   and type(canonical[p['order']].get('saved_contribution')) in (int,float)]
        exact_complete=len(confirmed)==len(eligible) and bool(eligible)
        mature_cohorts[kind]={'age_window_hours':[6,48],'eligible_paid':len(eligible),
            'confirmed_paid':len(confirmed),'pending':len(eligible)-len(confirmed),
            'sum_confirmed_saved_contribution':sum(r['saved_contribution'] for r in confirmed),
            'confirmed_saved_paid_mean':sum(r['saved_contribution'] for r in confirmed)/len(confirmed) if exact_complete else None,
            'known_subset_saved_mean':sum(r['saved_contribution'] for r in confirmed)/len(confirmed) if confirmed else None,
            'exact_reward_coverage':len(confirmed)/len(eligible) if eligible else None,
            'incomplete_subset_is_not_cohort_quality':not exact_complete}
    scored = {}
    for row in journal.events('forum_objective_observation', 3000):
        if row.get('post_id') not in scored and 0 <= now-row.get('post_at', 0) <= 86400:
            scored[row['post_id']] = row
    formats = {}
    for kind in FOCUS[1:]:
        rows = [r for r in scored.values() if r['information_kind'] == kind and now-r['post_at'] >= 600]
        formats[kind] = {'scored_posts_10min_old':len(rows),
            'mean_observed_score':sum(r['score'] for r in rows)/len(rows) if rows else None,
            'enough_for_descriptive_comparison':len(rows)>=5}
    board = journal.snapshot('board.json', {})
    own = next((r for r in board.get('buyers', []) if r.get('name') == 'seochanit'), {})
    failures = [r for r in journal.events('buyer_contract_result', 4000) if 0 <= now-r['_ts'] <= 3600 and same_formula(r,version)]
    return {'metric':journal.get('market_rules',{}).get('buyer_metric','unknown'),
        'score_formula':version,'acquisition_cost_observable':False,
        'decision_score_source':'public_range_and_shrunk_own_cost_proxy_scenarios' if version==MARGIN else 'list_discount',
        'cost_model':{k:journal.get('buyer-cost-scenarios:v22',{}).get(k) for k in ('at','id','groups','source')},
        'healthy_margin_share_exact_reward_pending':version==MARGIN,
        'objective_weights':{'saved_percentile':.5,'forum_raw':.3,'deals_percentile':.1,'posts_percentile':.1},
        'decision_contract':'joint expected ALL progress per execution second; compare purchase/counter/outside option',
        'saved_average_is_not_a_purchase_veto':True,
        'diagnostic_since':diagnostic_start,
        'current_saved':own.get('saved'), 'current_deals':own.get('deals'), 'current_forum':own.get('forum'),
        'forum_raw_all_gain_per_0_01':.003 if journal.get('market_rules',{}).get('buyer_forum_metric')=='raw' else None,
        'segments_60min':segments,'mature_quality_cohorts':mature_cohorts,
        'contract_failure_classes':dict(Counter(r.get('class','unknown') for r in failures)),
        'forum_formats':formats,
        'note':'Paid Saved contributions are normalized, not absolute surplus. Pending quality is not success. '
               'Post scores are sampled from newest 100 public posts, not causal format lift or the full forum aggregate.'}


def supply_evidence(journal, now):
    plans = [r for r in journal.events('shop_prepared_plan_used', 100) if 0 <= now-r['_ts'] <= 3600]
    states = [r for r in journal.events('supply_execution_status', 1000) if 0 <= now-r['_ts'] <= 3600]
    good = [r for r in states if r.get('status') == 'complete']
    rejections = [r for r in journal.events('stockout', 2000) if 0 <= now-r['_ts'] <= 3600]
    return {'plans':len(plans), 'planned_units':sum(b['qty'] for r in plans for b in r.get('buy',[])),
        'confirmed_acquired_units':sum(r.get('qty',0) for r in good), 'confirmed_acquired_lots':len(good),
        'statuses':dict(Counter(r.get('status') for r in states)),
        'stock_refusal_reasons':dict(Counter(r.get('reason','legacy_unclassified') for r in rejections)),
        'planned_decision_delay_seconds':[round(r['_ts']-r['window'][0],2) for r in plans[-4:]],
        'note':'POST success counts actual acquisition; prepared plans do not. Legacy refusal events mix quantity, eligibility and reservations.'}


def shop_alternatives(journal, config, now):
    from .economy import restock_candidates, cash_capacity, remaining
    from .adaptive import effective
    from .meta import rank_progress
    from .shop_growth import inventory_limit
    shadow = Shadow(journal)
    inv = copy.deepcopy(journal.snapshot('inventory.json', {}))
    cfg, _ = effective(shadow, 'shop', base=config)
    control = shadow.get('market_control', {})
    shadow.put('market_control', dict(control, pause_restock=False))
    virtual = dict(inv, restock={'open':True,'opens_at':now-1,'closes_at':now+180})
    catalog = shadow.snapshot('catalog.json', {})
    cap = cash_capacity(inv, shadow, cfg, now)
    board = journal.snapshot('board.json', {})
    if not -2 <= now-journal.get('board_checked_at',0) < 180:
        board = {}
    rows = []
    cycles_grid=[1,1.5,2,3]
    if config.get('shop_internal_capital_caps_enabled') is False:
        # Logarithmic alternatives reach the finite forecast boundary without
        # turning each background observation into an unbounded grid search.
        from .finance import planning_hours
        end=max(1,planning_hours(cfg,now)*3600/max(1,cfg.get('supply_cycle_seconds',1200)))
        cycles_grid=[1.]
        while cycles_grid[-1]*2<end:cycles_grid.append(cycles_grid[-1]*2)
        cycles_grid=sorted(set(cycles_grid+[end,max(1,cfg.get('shop_demand_horizon_cycles',1))]))
    for cycles in cycles_grid:
        policy = dict(cfg, shop_demand_horizon_cycles=cycles, restock_max_lines=min(10,cfg['restock_max_lines']))
        candidates = restock_candidates(virtual, catalog, [], shadow, policy, now)
        delta = {k:sum(c['expected_delta'].get(k,0) for c in candidates) for k in ('book','revenue')}
        stress = 0
        for c in candidates:
            e = c['evidence']
            upper = (.6 if e['kind']=='food' else .69)*e['list_price']*e['supply_surge_factor']
            for m in e.get('marginal_units',[]):
                probability = m['probability']
                storage = upper*.01*m['holding']/24 if e['kind']=='goods' else 0
                expiry = (1-probability)*upper*.25 if e['kind']=='food' else 0
                stress += m['revenue']-probability*upper-storage-expiry
        rows.append({'cover_cycles':cycles,'lines':len(candidates),'units':sum(c['qty'] for c in candidates),
            'capital_commitment':sum(c['capital_commitment'] for c in candidates),
            'expected_book_scenario':delta['book'],'expected_revenue_scenario':delta['revenue'],
            'rank_progress_surrogate':rank_progress(board,'shop','seochanit',delta),
            'upper_mean_cost_book_stress':stress,
            'examples':[{'sku':c['sku'],'qty':c['qty'],'own_positive_paid_available':c['evidence']['growth'].get('positive_paid_while_available',0),
                'arrival_rate_scenario':c['evidence']['arrival_rate_scenario']} for c in candidates[:4]]})
    units = [u for u in inv.get('units',[]) if remaining(u,now)>0]
    unreserved = None
    with journal.connect() as db:
        reserved = {r[0] for r in db.execute('SELECT unit FROM reservations WHERE until>?',(now,))}
    unreserved = sum(u.get('unit') not in reserved for u in units)
    from .shop_growth import demand_diagnostics
    diagnostic=demand_diagnostics(dict(inv,units=units),shadow.get('merchant_context_v5',{}),cfg)
    if config.get('shop_adaptive_demand_enabled'):
        from .demand_policy import context as demand_context
        learned=demand_context(journal,config,now)
        diagnostic['calibration_summary']=(learned or {}).get('summary',{'status':'no_fresh_calibration_neutral_prior'})
    return {'at':now, 'rules_hash':journal.get('market_rules',{}).get('sha256'),
        'physical_live_units':len(units),'unreserved_live_units':unreserved,'inventory_ceiling':inventory_limit(cfg),
        'food_under_2h_units':sum(u.get('kind')=='food' and remaining(u,now)<2 for u in units),
        'cash':inv.get('balance'), 'spendable':cap['spendable'],
        'refund_liability':cap['refund_liability'],'execution_guard_reasons':control.get('reasons',[]),
        'execution_permitted':False, 'alternatives':rows,'demand_diagnostics':diagnostic,
        'note':'Shared-data forecasts, not measured lift. Cash converts to inventory; acquisition alone adds no Book. '
               'Stars effect unmodeled; retain validated quality and full refund guards. '
               'Select repeat-demand depth by marginal Book/rank, cash and supply delay; raw peer quantities are not targets.'}


def build(journal, config, now):
    started = time.monotonic()
    result = {'shop':shop_alternatives(journal,config,now), 'buyer':buyer_evidence(journal,now),
              'supply_execution_60min':supply_evidence(journal,now)}
    result['at'] = now
    result['id'] = identity(result)
    journal.put('objective-decision-context', result)
    journal.add('objective_alternatives_prepared', {'id':result['id'],'at':now,
        'seconds':time.monotonic()-started, 'shop_alternatives':result['shop']['alternatives'],
        'buyer_segments':result['buyer']['segments_60min'], 'rules_hash':result['shop']['rules_hash']})
    return result


def _bounded(value, depth=0):
    if depth>12:return 'omitted_deep_context'
    if isinstance(value,dict):return {str(k)[:100]:_bounded(v,depth+1) for k,v in list(value.items())[:40]}
    if isinstance(value,list):return [_bounded(v,depth+1) for v in value[:20]]
    if isinstance(value,str):return value[:500]
    if type(value) is float and not math.isfinite(value):return None
    return value


def curate(facts):
    """Keep the diagnosis, bounded alternatives and totals; never duplicate cohorts."""
    source_bytes = len(json.dumps(facts,ensure_ascii=False).encode())
    row = copy.deepcopy(facts)
    meta = row.get('meta') or {}
    row['meta'] = {k:meta.get(k) for k in ('at','issues','source_evaluation_at','skills_executed','research_at',
        'next_actions','promotion_authority','analytical_roles')}
    op = meta.get('operating_policy') or {}
    row['meta']['operating_policy'] = {k:op.get(k) for k in ('id','at','status','regime','reasons')}
    plan = row.get('supply_plan') or {}
    row['supply_plan'] = {k:plan.get(k) for k in ('window','phase','selected')}
    row['supply_plan']['options'] = [{k:c.get(k) for k in ('id','score','capital_commitment','expected_delta','objective_utility')}
                                   for c in plan.get('options') or []]
    row = _bounded(row)
    reductions = []
    def size():return len(json.dumps(row,ensure_ascii=False).encode())
    if size()>FACT_BYTES:
        market = row.get('market') or {}
        market['top_skus'] = (market.get('top_skus') or [])[:8]
        market['buyer_preferences'] = (market.get('buyer_preferences') or [])[:3]
        for name, peers in (market.get('competitors') or {}).items():
            market['competitors'][name] = peers[:3]
        reductions.append('market_examples')
    if size()>FACT_BYTES:
        feedback = row.get('operating_feedback') or {}
        for key in ('peer_trends','patches'):
            feedback.pop(key,None)
        row['profile_dispatch'] = {k:v for k,v in (row.get('profile_dispatch') or {}).items() if k!='catalog'}
        reductions.append('redundant_patches_and_profile_catalog')
    if size()>FACT_BYTES:
        # Preserve official targets, current parameters, objective alternatives,
        # guards and evidence IDs. These optional examples stay in the journal.
        for key in ('market','framework','meta','supply_plan'):
            if size()<=FACT_BYTES:break
            row.pop(key,None);reductions.append(key)
    if size()>FACT_BYTES:
        raise ValueError('essential_objective_context_exceeds_budget')
    return row, {'source_bytes':source_bytes, 'fact_bytes':size(), 'budget_bytes':FACT_BYTES,
                 'reductions':reductions, 'duplicated_meta_feedback_removed':bool(op.get('execution_feedback'))}
