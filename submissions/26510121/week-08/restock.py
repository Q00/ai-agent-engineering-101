"""A standard v4 restock hook; only queues forum text through the journal."""
import argparse
import json
from r3.state import Journal
from r3.economy import restock_candidates, supply_open, cash_capacity
import time
from r3.model import Chooser
from r3.adaptive import effective


def portfolios(candidates):
    result = []
    breadth = [dict(c, qty=1, max_cost=c['max_cost']/c['qty'],
                    capital_commitment=c['capital_commitment']/c['qty'],score=c['score']/c['qty'],
                    expected_delta={k:v/c['qty'] for k,v in c['expected_delta'].items()}) for c in candidates]
    for name, lines in [('diversified', candidates), ('breadth', breadth), ('cautious', candidates[:1])]:
        result.append({'id': name, 'buy': [{'sku': c['sku'], 'qty': c['qty']} for c in lines],
            'score': sum(c['score'] for c in lines), 'max_cost': sum(c['max_cost'] for c in lines),
            'capital_commitment': sum(c['capital_commitment'] for c in lines),
            'expected_delta': {k: sum(c['expected_delta'].get(k, 0) for c in lines) for k in ('book','profit','revenue')},
            'evidence': [c['evidence'] for c in lines]})
    result.append({'id': 'wait', 'buy': [], 'score': 0, 'max_cost': 0, 'capital_commitment': 0,
        'expected_delta': {'book': 0, 'profit': 0, 'revenue': 0}, 'evidence': 'keep cash; no attractive inventory'})
    return result


def plan(journal, chooser=None):
    cfg, strategy = effective(journal, 'shop')
    journal.reconcile()
    inv = journal.snapshot("inventory.json", {})
    if inv.get("round", 0) < 3:
        return {"buy": []}
    if not supply_open(inv):
        return {'buy':[], 'bankrupt':inv.get('balance',0)<inv.get('bankrupt_line',0)
                and not inv.get('units') and inv.get('bankruptcies_left',0)>0}
    if cfg.get('market_control_enabled'):
        own=next((row for row in journal.snapshot('board.json',{}).get('shops',[])
                  if row.get('name')=='seochanit'),{})
        if own.get('alive') is False:
            journal.add('restock_blocked',{'reason':'market_shop_not_discoverable'})
            return {'buy':[]}
        diagnosis = journal.get('market_control', {})
        if diagnosis.get('pause_restock',True) or not 0<=time.time()-diagnosis.get('at',0)<900:
            # Re-evaluate recovered startup/transport conditions inside the
            # short supply window rather than waiting for the 5-minute tick.
            from r3.control import update
            diagnosis=update(journal,cfg,force=True)
        if diagnosis.get('pause_restock', True) or not 0 <= time.time()-diagnosis.get('at',0)<900:
            journal.add('restock_blocked', {'reason':'market_control_defensive_or_stale'})
            return {'buy': []}
    window=inv.get('restock') or {}
    remaining=window.get('closes_at',time.time()+180)-time.time()
    decision_seconds=0 if cfg.get('shop_fast_execution_enabled') else 2*cfg['model_timeout_seconds']
    line_budget=min(cfg['restock_max_lines'],max(1,int((remaining-decision_seconds-15)/12)))
    # Allocate inside the executable line budget. Truncating afterwards strands
    # capital that should have funded depth in the remaining legal supply calls.
    cfg=dict(cfg,restock_max_lines=line_budget)
    if cfg.get('market_cycle_enabled'):
        from r3.prewindow import consume
        prepared = consume(journal, cfg, strategy, inv)
        if prepared is not None:
            return prepared
    candidates = restock_candidates(inv, journal.snapshot("catalog.json", {}),
                                    journal.snapshot("demand.json", []), journal, cfg)
    if not candidates:
        return {"buy": [], "bankrupt": inv.get("balance", 0) < inv.get("bankrupt_line", 0)
                and not inv.get("units") and inv.get("bankruptcies_left", 0) > 0}
    # The actor chooses among transparent portfolio variants; every line has a conservative affordability bound.
    options = portfolios(candidates)
    selected = (chooser or Chooser(journal, cfg)).choose("shop", options,
               {"balance": inv["balance"], "stock_units": len(inv.get("units", [])), "strategy": strategy,
                'shop_execution':True,'operation':'restock',
                'supply_window': inv.get('restock'), 'cash_capacity': cash_capacity(inv, journal, cfg)},
               deadline=(inv.get('restock') or {}).get('closes_at', time.time() + 120) - 15,
               critical=options[0]["max_cost"] > cfg["critic_cash_fraction"] * inv["balance"])
    current = journal.snapshot('inventory.json', {})
    if not supply_open(current) or cash_capacity(current, journal, cfg)['spendable'] < sum(
            c['capital_commitment']/c['qty']*b['qty'] for c in candidates for b in selected['buy'] if b['sku']==c['sku']):
        return {'buy': []}
    journal.add('restock_decision', {'strategy_version': strategy['version'], 'buy': selected['buy'],
                'policy':cfg.get('shop_strategy_revision'),'line_budget':line_budget,
                'capital_commitment':selected['capital_commitment'],
                'spendable':cash_capacity(inv,journal,cfg)['spendable'],
                'expected_delta':selected['expected_delta']}, role='shop')
    return {"buy": selected["buy"]}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--state", required=True)
    args = p.parse_args()
    print(json.dumps(plan(Journal(args.state)), ensure_ascii=False))
