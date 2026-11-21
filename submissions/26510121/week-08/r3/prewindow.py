"""Prepare numerical stock plans before supply opens; revalidate at execution."""
from collections import Counter
import copy
import time
from .state import atomic_json, identity


def config_hash(config):
    # Remaining line budget is narrowed by the foreground on each invocation.
    return identity({k: v for k, v in config.items() if k != 'restock_max_lines'})


def service_fingerprint(journal,config,inv,now):
    if not config.get('service_contract_v26_enabled'):return None
    from .service_policy import context
    from .service_contracts import eligible
    from .economy import grade
    packet=context(journal,now);catalog=journal.snapshot('catalog.json',{})
    return identity([packet.get('generation'),sorted(
        (u['unit'],u['sku'],u.get('expires_at'),str(grade(u,catalog,now)),tuple(
            eligible(u,catalog,dict(b,valid=True,sku=u['sku']),now,config.get('checkout_buffer_seconds',30))
            for b in packet.get('shop_needs',{}).get(u['sku'],{}).get('buckets',[]))) for u in inv.get('units',[]))])


class PreviewJournal:
    def __init__(self, journal):
        self.journal = journal

    def __getattr__(self, name):
        return getattr(self.journal, name)

    def get(self, key, default=None):
        value = self.journal.get(key, default)
        if key == 'market_control' and isinstance(value, dict):
            return dict(value, pause_restock=False)  # Analysis only; execution retains every guard.
        return value


def prepare(journal, config, strategy, now=None):
    now = time.time() if now is None else now
    inv = journal.snapshot('inventory.json', {})
    window = inv.get('restock') or {}
    opens, closes = window.get('opens_at'), window.get('closes_at')
    rules = journal.get('market_rules', {})
    if (inv.get('round') != 3 or not rules.get('compatible') or not rules.get('sha256')
            or type(opens) not in (int, float) or type(closes) not in (int, float)
            or not opens - 310 <= now < closes - 20):
        return None
    old = journal.snapshot('shop-plan-v15.json', {})
    service_id=service_fingerprint(journal,config,inv,now)
    from .demand_policy import context as demand_context
    demand=demand_context(journal,config,now)
    demand_id=demand.get('id') if demand else None
    from .policy_learning import packet as learning_packet
    learning_at=learning_packet(journal,now).get('at') if config.get('book_growth_v23_enabled') else None
    phase = 'early' if now < opens - 90 else 'checked' if now < opens - 15 else 'ready'
    if (old.get('window') == [opens, closes] and old.get('phase') == phase
            and old.get('policy_hash') == config_hash(config) and old.get('demand_policy_id')==demand_id and old.get('service_policy_at')==learning_at
            and old.get('service_eligibility_id')==service_id and 0 <= now - old.get('at', 0) < 45):
        return old
    from .economy import restock_candidates, cash_capacity
    from .model import Chooser
    from restock import portfolios
    virtual = copy.deepcopy(inv)
    virtual['restock'] = {'open': True, 'opens_at': now - 1, 'closes_at': now + 180}
    virtual['units'] = [u for u in inv.get('units', []) if not u.get('expires_at') or u['expires_at'] > max(opens, now)]
    cfg = dict(config, restock_max_lines=min(10, config['restock_max_lines']))
    preview = PreviewJournal(journal)
    catalog = journal.snapshot('catalog.json', {})
    candidates = restock_candidates(virtual, catalog,
                                    journal.snapshot('demand.json', []), preview, cfg, now)
    if cfg.get('finalday_departments_enabled') and not candidates:
        approval=journal.get('company-procurement:v30',{})
        if approval.get('status')!='approved' or approval.get('snapshot_at')!=now:
            return None  # Unavailable evidence is not an economic wait decision.
    options = portfolios(candidates)
    chosen = Chooser(preview, cfg).choose('shop', options,
        {'shop_execution': True, 'operation': 'restock', 'preview': True,
         'cash_capacity': cash_capacity(virtual, journal, cfg, now)}, critical=False)
    # Keep only executable numbers and IDs, not copied transcripts or imagined grades.
    summaries = [{k: c.get(k) for k in ('id', 'buy', 'score', 'capital_commitment', 'expected_delta', 'objective_utility')}
                 for c in options]
    packet = {'schema': 1, 'at': now, 'expires_at': closes - 15, 'window': [opens, closes], 'phase': phase,
              'rules_hash': rules['sha256'], 'policy_hash': config_hash(config),'demand_policy_id':demand_id,'service_policy_at':learning_at, 'strategy_version': strategy['version'],
              'selected': chosen['id'], 'options': summaries,
              'service_eligibility_id':service_id,
              'held': dict(Counter(u['sku'] for u in virtual['units'])),
              'unit_commitments': {c['sku']: c['capital_commitment'] / c['qty'] for c in candidates},
              'catalog_hash': identity({c['sku']: catalog.get(c['sku']) for c in candidates}),
              'execution_allowed': False, 'note': 'Preview is not a supply authorization; fresh guards remain authoritative.'}
    packet['checksum'] = identity(packet)
    atomic_json(journal.md / 'shop-plan-v15.json', packet)
    journal.add('shop_plan_prepared', {k: v for k, v in packet.items() if k not in ('unit_commitments', 'held')}, role='shop')
    return packet


def consume(journal, config, strategy, inv, now=None):
    try:
        return _consume(journal, config, strategy, inv, now)
    except (KeyError,TypeError,ValueError,OverflowError):
        return None


def _consume(journal, config, strategy, inv, now=None):
    now = time.time() if now is None else now
    from .economy import supply_open, cash_capacity
    packet = journal.snapshot('shop-plan-v15.json', {})
    if packet.get('checksum') != identity({k:v for k,v in packet.items() if k!='checksum'}):
        return None
    window = inv.get('restock') or {}
    from .demand_policy import context as demand_context
    demand=demand_context(journal,config,now)
    demand_id=demand.get('id') if demand else None
    from .policy_learning import packet as learning_packet
    learning_at=learning_packet(journal,now).get('at') if config.get('book_growth_v23_enabled') else None
    if (not supply_open(inv, now) or packet.get('schema') != 1
            or packet.get('window') != [window.get('opens_at'), window.get('closes_at')]
            or packet.get('rules_hash') != journal.get('market_rules', {}).get('sha256')
            or packet.get('policy_hash') != config_hash(config)
            or packet.get('demand_policy_id')!=demand_id
            or packet.get('service_policy_at')!=learning_at
            or packet.get('strategy_version') != strategy['version']
            or packet.get('service_eligibility_id')!=service_fingerprint(journal,config,inv,now)
            or not 0 <= now - packet.get('at', 0) <= 90 or now >= packet.get('expires_at', 0)):
        return None
    catalog = journal.snapshot('catalog.json', {})
    if packet.get('catalog_hash') != identity({s: catalog.get(s) for s in packet.get('unit_commitments', {})}):
        return None
    option = next((c for c in packet.get('options', []) if c['id'] == packet.get('selected')), None)
    if option is None:
        return None
    counts = Counter(u['sku'] for u in inv.get('units', []) if not u.get('expires_at') or u['expires_at'] > now)
    from .shop_growth import stock_caps_enabled
    lines, committed = [], 0
    for row in option['buy'][:config['restock_max_lines']]:
        sku = row['sku']
        if (type(row.get('qty')) is not int or not 1<=row['qty']<=20
                or sku not in catalog or type(packet['unit_commitments'].get(sku)) not in (int,float)
                or not __import__('math').isfinite(packet['unit_commitments'][sku]) or packet['unit_commitments'][sku]<=0):
            return None
        held_room=max(0,config['restock_max_qty']-counts[sku]) if stock_caps_enabled(config) else 20
        qty = min(row['qty'], max(0, row['qty'] - max(0, counts[sku] - packet['held'].get(sku, 0))),held_room)
        if qty:
            lines.append({'sku': sku, 'qty': qty})
            committed += packet['unit_commitments'][sku] * qty
    if committed > cash_capacity(inv, journal, config, now)['spendable']:
        return None
    journal.add('shop_prepared_plan_used', {'window': packet['window'], 'plan_at': packet['at'],
                'selected': option['id'], 'buy': lines, 'capital_commitment': committed}, role='shop')
    return {'buy': lines}
