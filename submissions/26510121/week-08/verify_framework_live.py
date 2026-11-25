"""Read-only evidence that the deployed framework is actually being consumed."""
from collections import Counter
from datetime import datetime,timezone
import json,sqlite3,time
from r3.state import ROOT,read_json,atomic_json,policy
from autonomous_status import ReadJournal


def main():
    now=time.time();runtime=read_json(ROOT/'.market/runtime.json',{});start=runtime.get('started_at',now)
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as c:
        c.execute('BEGIN');j=ReadJournal(c,now)
        def events(kind):return [dict(json.loads(d),_ts=t) for t,d in c.execute(
            'SELECT ts,data FROM events WHERE kind=? AND ts>=? AND ts<=?',(kind,start,now))]
        assigned=events('framework_order');plans=j.get('framework-plan',{});memory=j.get('memory-contract-facts:v2',{})
        reviews=events('shop_review_observed');outcomes=events('outcome')
        table=c.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='memory_episodes'").fetchone()[0]
        result={'checked_at':datetime.now(timezone.utc).isoformat(),'runtime':runtime,
            'buyer_revision':j.get('buyer_strategy_revision'),'shop_revision':j.get('shop_strategy_revision'),
            'framework_revision':plans.get('revision'),'plan_at':plans.get('at'),
            'arena':plans.get('arena'),'assigned_orders':len(assigned),
            'profiles':dict(Counter(r['profile'] for r in assigned)),'arms':dict(Counter(r['arm'] for r in assigned)),
            'buyer_purchases':len(events('purchase')),'shop_sales':len(events('shop_settled')),
            'contract_feedback':len(events('buyer_contract_result')),'contract_pauses':len(events('buyer_contract_pause')),
            'canonical_outcomes':sum(bool(r.get('canonical')) for r in outcomes),
            'canonical_confirmed_refunds':sum(bool(r.get('canonical')) and r.get('refund_status')=='confirmed' for r in outcomes),
            'contract_memory_groups':len(memory.get('groups',{})),
            'memory_episodes':c.execute('SELECT count(*) FROM memory_episodes').fetchone()[0] if table else 0,
            'review_observations':len(reviews),'verified_reviewer_observations':sum(bool(r.get('identity_known')) for r in reviews),
            'rating_mode':plans.get('shop_rating',{}).get('mode'),
            'control':j.get('market_control',{}).get('status'),
            'runtime_errors':{kind:len(events(kind)) for kind in ('strategy_error','market_control_error','merchant_context_error','forum_error')}}
    result['activated']=bool(result['framework_revision']=='system-v13-contract-frontier' and
        result['buyer_revision']==policy()['buyer_strategy_revision'] and result['shop_revision']==policy()['shop_strategy_revision'] and
        result['assigned_orders']>0 and result['contract_memory_groups']>0 and result['plan_at']>=start)
    path=ROOT/'logs'/('framework-live-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    atomic_json(path,result);print('SOURCE',path);print(json.dumps(result,ensure_ascii=False))
    return result['activated']


if __name__=='__main__':raise SystemExit(0 if main() else 1)
