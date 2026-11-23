"""Gradual capital expansion using paid evidence and equity drawdown; no model calls."""
from optimizer import read,atomic,append


def allocation(md,inventory,growth,policy,now):
    path=md/'adaptive-capital.json'
    equity=max(0,inventory.get('balance',0))+sum(max(0,u['cost']) for u in inventory.get('units',[]))
    state=read(path,{})
    if not state:
        state=dict(stage=0,baseline=max(1,equity),peak=equity,last_change=now,evidence_after=0)
    stage=state['stage'];metrics=growth.get('metrics',{})
    peak=max(state['peak'],equity);drawdown=1-equity/max(1,peak)
    fresh=metrics.get('latest_evidence_at',0)>state['evidence_after']
    healthy=(metrics.get('mature_sales',0)>=5 and metrics.get('known_cost_sales',0)==metrics.get('mature_sales')
             and (metrics.get('roi') or 0)>=.08 and metrics.get('net_after_refunds_storage',0)>0
             and metrics.get('refund_rate',1)<=.05 and metrics.get('defect_rate',1)<=.10
             and metrics.get('ledger_age_seconds',9999)<=180
             and metrics.get('median_turnover_hours') is not None and metrics['median_turnover_hours']<=2
             and metrics.get('stale_high_units',1)==0 and now>=growth.get('paused_until',0))
    reason='hold'
    bad=(drawdown>=.08 or metrics.get('mature_sales',0)>=3 and
         (metrics.get('net_after_refunds_storage',0)<0 or metrics.get('refund_rate',0)>.10 or metrics.get('stale_high_units',0)>=2))
    if bad and stage and now-state['last_change']>=3600:
        stage-=1;reason='reduce: drawdown, loss or stale inventory'
    elif healthy and fresh and stage<3 and now-state['last_change']>=3600 and equity>=state['baseline']*(1+.10*(stage+1)):
        stage+=1;reason='expand: equity growth and fresh profitable turnover'
    if stage!=state['stage']:
        state.update(last_change=now,evidence_after=metrics.get('latest_evidence_at',0))
    state.update(stage=stage,peak=peak,equity=equity,drawdown=drawdown,reason=reason,checked_at=now)
    atomic(path,state)
    if reason!='hold':append(md/'optimization/adaptive-capital-history.jsonl',state)
    effective=dict(policy)
    effective.update(inventory_equity_fraction=min(.90,policy['inventory_equity_fraction']+.02*stage),
                     normal_inventory_fraction=min(.80,policy['normal_inventory_fraction']+.02*stage),
                     stock_cover_hours=min(4,policy['stock_cover_hours']+.25*stage),
                     verified_restock_fraction=min(.95,policy['verified_restock_fraction']+.015*stage))
    return effective,state
