"""Read-only paired policy replay; never equates quotes with completed sales."""
from collections import Counter
from datetime import datetime,timezone
import json
import sqlite3
import time
from r3.state import ROOT,read_json
from r3.meta import rank_gain,rank_progress
from r3.model import threshold_delta


def main():
    stop=time.time();start=1794886809
    board=read_json(ROOT/'.market/board.json',{})
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        query='SELECT ts,data FROM events WHERE kind=? AND ts>=? AND ts<? ORDER BY ts'
        predictions=[dict(json.loads(raw),event_at=ts) for ts,raw in db.execute(query,('prediction',start,stop))]
        actual={r['decision']:r['chosen'] for _,raw in db.execute(query,('shop_fast_execution',start,stop))
            for r in [json.loads(raw)]}
        reputation=db.execute('SELECT value FROM kv WHERE key=?',('shop-reputation:v24',)).fetchone()
        reputation=json.loads(reputation[0]) if reputation else {}
    cases={}
    for p in predictions:
        if p.get('role')!='shop' or p.get('operation')=='restock' or not p.get('task'):continue
        if not any(c.get('action')=='hold' for c in p.get('candidates',[])):continue
        cases[p['task']]=p  # Last decision per task avoids counting repeated turns as independent orders.
    summary=Counter();examples=[];rows=[]
    for p in cases.values():
        candidates=p['candidates']
        def value(c,fixed):
            d=c.get('expected_delta') or {}
            exact=threshold_delta('shop',c,d) if fixed else d
            return ((rank_gain(board,'shop','seochanit',exact) or 0)+
                    (rank_progress(board,'shop','seochanit',d) or 0),c.get('score') or 0)
        old=max(candidates,key=lambda c:value(c,False));new=max(candidates,key=lambda c:value(c,True))
        observed=actual.get(p['decision'])
        summary['tasks']+=1;summary['actual_hold']+=observed=='hold'
        summary['paired_old_hold']+=old.get('action')=='hold'
        summary['paired_new_hold']+=new.get('action')=='hold'
        reopened=(old.get('action')=='hold' and new.get('action')!='hold' and
                  (new.get('expected_delta') or {}).get('book',0)>0)
        summary['reopened_profitable_quote_tasks']+=reopened
        summary['observed_hold_reopened']+=reopened and observed=='hold'
        row={'event_at':p['event_at'],'actual':observed,'old':old['id'],'new':new['id'],
            'old_value':value(old,False)[0],'new_value':value(new,True)[0],
            'new_expected_delta':new.get('expected_delta')}
        rows.append(row)
        if reopened and len(examples)<5:examples.append({'decision_input':p,'comparison':row})
    out={'at':stop,'window_start':start,'query':query,'board':board,
        'reputation_summary':reputation.get('summary'),'summary':dict(summary),'examples':examples,'cases':rows,
        'limits':['Both policies are replayed against the same frozen current board, not the exact board of every historical decision.',
            'Compact predictions lack their full rating evidence; current reputation packets explicitly state the official mapping is not exact.',
            'Reopened quotes are opportunities, not actual accepted orders or realized profit.',
            'No LLM calls, market payments, supplier calls or runtime policy writes were made.']}
    path=ROOT/'logs'/('v34-rank-replay-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf8').write(json.dumps(out,ensure_ascii=False,indent=2))
    print('SOURCE',path);print(json.dumps(out['summary']))


if __name__=='__main__':main()
