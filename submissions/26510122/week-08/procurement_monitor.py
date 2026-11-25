"""Durable reconciliation and stage alerts, independent of supply/model calls."""
import fcntl
import json
from pathlib import Path
from inventory_observation import timestamp
from market_memory import record
from replenishment_plan import save
from supply_reconciliation import RECONCILIATION_EVENTS, reconcile, pending_quantity

HEALTH=Path('state/procurement-health.json')
MONITOR_EVENTS = RECONCILIATION_EVENTS | {
    'portfolio_prepared_reused', 'portfolio_execution', 'procurement_protocol_received',
    'procurement_request_started', 'procurement_batch_finished',
}


def health(state,ready,history,now):
    alerts=[]
    pending=[i for i in state.get('intents',[]) if pending_quantity(i)]
    for i in pending:
        if now-i['at']>600:
            alerts.append({'key':f"pending:{i.get('intent_id') or str(i['at'])+i['sku']}",
                'stage':'arrival_unconfirmed','sku':i['sku'],'units':pending_quantity(i),
                'age_seconds':round(now-i['at'])})
    claims={e['window']:timestamp(e.get('at')) for e in history
            if e.get('event')=='portfolio_prepared_reused' and e.get('window')
            and timestamp(e.get('at')) is not None and now-timestamp(e['at'])<21600}
    if ready.get('status')=='claimed' and ready.get('claimed_at'):
        claims[ready['window']]=ready['claimed_at']
    for window,claimed in claims.items():
        current=[e for e in history if e.get('window')==window]
        kinds={e.get('event') for e in current}
        if now-claimed<=30:continue
        if 'portfolio_execution' not in kinds:stage='claimed_without_execution'
        elif any(e.get('event')=='portfolio_execution' and e.get('emitted_units',0)>0
                 and e.get('transport_observation')=='supply-http-v1' for e in current) and 'procurement_protocol_received' not in kinds:
            stage='emitted_without_kit_receipt'
        elif 'procurement_protocol_received' in kinds and 'procurement_request_started' not in kinds and 'procurement_batch_finished' not in kinds:
            stage='kit_received_without_request'
        else:stage=None
        if stage:alerts.append({'key':f'{stage}:{window}','stage':stage,'age_seconds':round(now-claimed)})
    requests={e.get('request_id'):e for e in history if e.get('event')=='procurement_request_started'}
    results={e.get('request_id') for e in history if e.get('event')=='procurement_supply_result'}
    for identity,e in requests.items():
        at=timestamp(e.get('at'))
        if identity and identity not in results and at is not None and now-at>30:
            alerts.append({'key':f'response:{identity}','stage':'request_without_response','sku':e.get('sku')})
    return {'at':now,'pending_units':sum(pending_quantity(i) for i in pending),
        'pending_cost_ceiling':sum(pending_quantity(i)*i['cost_ceiling'] for i in pending),
        'alerts':alerts,'policy':'Only explicit rejected/not-sent evidence releases reservations; age alone never releases them.'}


def maintain(inventory,history,now,*,state_path,ready_path):
    state_path.parent.mkdir(parents=True,exist_ok=True)
    with state_path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        state=json.loads(state_path.read_text()) if state_path.exists() else {'version':1,'windows':{},'intents':[]}
        if state.get('version')!=1 or not isinstance(state.get('intents'),list):raise ValueError('invalid_portfolio_state')
        changes=reconcile(state['intents'],inventory,history,now)
        if changes:
            save(state_path,state)
            for change in changes:record('portfolio_intent_reconciled',**change)
        try:ready=json.loads(ready_path.read_text())
        except (OSError,ValueError):ready={}
        result=health(state,ready,history,now)
        try:old=json.loads(HEALTH.read_text())
        except (OSError,ValueError):old={}
        known={a['key'] for a in old.get('alerts',[])}
        for alert in result['alerts']:
            if alert['key'] not in known:record('procurement_health_warning',**alert)
        save(HEALTH,result)
        return result
