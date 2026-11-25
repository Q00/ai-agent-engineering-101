"""Dry-run or apply uniquely proven historical failures; retain all raw logs."""
import argparse
import copy
import fcntl
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from market_memory import record
from procurement_legacy import discover
from replenishment_plan import save
from supply_reconciliation import pending_quantity,resolve_responses


def main():
    p=argparse.ArgumentParser();p.add_argument('--runtime',type=Path,required=True)
    p.add_argument('--console',type=Path,required=True);p.add_argument('--apply',action='store_true');a=p.parse_args()
    state_path=a.runtime/'state/inventory-portfolio.json';log=a.runtime/'logs/shop-events.jsonl'
    # Large historical logs must never hold the live execution lock.
    state=json.loads(state_path.read_text());original=copy.deepcopy(state)
    history=[]
    with log.open(errors='replace') as source:
        for line in source:
            try:e=json.loads(line)
            except ValueError:continue
            if e.get('event') in ('portfolio_exploration_intent','procurement_supply_result'):history.append(e)
    with a.console.open(errors='replace') as source:evidence=discover(state['intents'],history,source)
    if a.apply:
        lock=state_path.with_suffix('.lock').open('a')
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        current=json.loads(state_path.read_text())
        # IDs added by discovery are deterministic annotations, never new decisions.
        for before,annotated in zip(original['intents'],state['intents']):
            if before.get('intent_id') or not annotated.get('intent_id'):continue
            matches=[i for i in current['intents'] if all(i.get(k)==before.get(k) for k in ('sku','at','window','qty'))]
            if len(matches)==1:matches[0]['intent_id']=annotated['intent_id']
        state=current
    try:
        before=sum(pending_quantity(i)*i['cost_ceiling'] for i in state['intents'])
        known={e.get('request_id') for e in history if e.get('event')=='procurement_supply_result'}
        changes=resolve_responses(state['intents'],history+evidence)
        after=sum(pending_quantity(i)*i['cost_ceiling'] for i in state['intents'])
        if a.apply:
            for e in evidence:
                if e['request_id'] not in known:record(path=log,**e)
            save(state_path,state)
            for c in changes:record('portfolio_intent_reconciled',path=log,**c)
        print(json.dumps({'applied':a.apply,'matched_evidence':len(evidence),
            'released_cost_ceiling':before-after,'pending_cost_ceiling':after,
            'released_units':sum(c['released_units'] for c in changes)},ensure_ascii=False))
    finally:
        if a.apply:lock.close()


if __name__=='__main__':main()
