"""Replay a bounded copy of real evidence. Never connect to market or API."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,json,sqlite3,time
from r3.state import ROOT,Journal,read_json,atomic_json,policy
from r3.evolution import update
from r3.outcomes import latest
from r3.economy import buyer_candidates
from r3.long_memory import contract_summary


def main(source):
    target=ROOT/'.market/replay';j=Journal(target);now=time.time()
    selected=['purchase','outcome','broken_news','ambiguous_break','public_broken','public_refund','shop_settled',
        'buyer_order','market_result','attempt','buyer_counter','quote_comparison','agreement','customer_visit',
        'customer_paid','customer_identity','buyer_execution','buyer_contract_result']
    with sqlite3.connect((source/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as original:
        original.execute('BEGIN')
        with j.connect() as c:
            for kind in selected:
                limit=20000 if kind.startswith('public_') else 10000
                rows=original.execute('SELECT id,kind,ts,role,data FROM events WHERE kind=? AND ts>=? ORDER BY ts DESC LIMIT ?',
                                      (kind,now-172800,limit)).fetchall()
                c.executemany('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?)',rows)
            for key in ('public_feed_caught_up_at','market_rules','adaptive:buyer','adaptive:shop'):
                row=original.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
                if row:c.execute('INSERT OR REPLACE INTO kv VALUES(?,?)',(key,row[0]))
    for name in ('catalog.json','inventory.json','board.json','registry.json'):
        atomic_json(target/name,read_json(source/'.market'/name,{}))
    j.put('framework-last-update',0);j.put('memory-refreshed-at',0)
    before=latest(j);started=time.monotonic();j.reconcile();reconcile_seconds=time.monotonic()-started
    after=latest(j);started=time.monotonic();plan=update(j,policy(),now);planning_seconds=time.monotonic()-started
    confirmed=sum(bool(r.get('canonical')) and r.get('refund_status')=='confirmed' for r in after.values())
    assert confirmed>=sum(bool(r.get('canonical')) and r.get('refund_status')=='confirmed' for r in before.values())
    assert plan['revision']=='system-v13-contract-frontier'
    order=read_json(source/'.market/order.json',{})
    shops=read_json(source/'.market/shops.json',[]);catalog=read_json(target/'catalog.json',{})
    started=time.monotonic();candidates=buyer_candidates(order,shops,catalog,j,policy()) if order.get('acceptable') else []
    candidate_seconds=time.monotonic()-started
    sample=next((u for u in read_json(target/'inventory.json',{}).get('units',[]) if u.get('kind')=='goods'),{})
    memory=contract_summary(j,sample,6) if sample else {}
    memory_groups=j.get('memory-contract-facts:v2',{}).get('groups',{})
    assert memory_groups,'Expected real settled contract memory to be materialized'
    result={'source':str(source),'replay_database':str(target/'r3.sqlite3'),'network_calls':0,
        'reconcile_seconds':reconcile_seconds,'planning_seconds':planning_seconds,'candidate_seconds':candidate_seconds,
        'canonical_confirmed_refund_orders':confirmed,'canonical_outcomes':sum(bool(r.get('canonical')) for r in after.values()),
        'plan':plan,'candidate_count':len(candidates),'memory_sample':memory,'memory_contract_groups':len(memory_groups),
        'checks':['read-only original database','working replay only','no model or market calls','hard guards unchanged']}
    path=source/'logs'/('system-upgrade-replay-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    atomic_json(path,result);print('SOURCE',path)
    print(json.dumps({k:v for k,v in result.items() if k!='plan'},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source-root',type=Path,required=True)
    main(parser.parse_args().source_root)
