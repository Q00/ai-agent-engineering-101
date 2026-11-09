"""Read-only profiling/replay of live Buyer inputs; no RFQs, payments or model calls."""
from collections import Counter
from datetime import datetime,timezone
import cProfile
import io
import json
import pstats
import sqlite3
import time
from autonomous_status import ReadJournal
from r3.state import ROOT,Journal,policy,read_json,atomic_json
from r3.economy import buyer_candidates


class Probe(ReadJournal):
    def __init__(self,c,now):
        super().__init__(c,now);self.md=ROOT/'.market';self.added=Counter();self.reads=Counter()
    def add(self,kind,data,**kwargs):self.added[kind]+=1;return False
    def events(self,kind,limit=500):
        self.reads[kind]+=1;return super().events(kind,limit)


def main():
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');now=time.time()
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as c:
        c.execute('BEGIN');j=Probe(c,now);prof=cProfile.Profile();prof.enable()
        start=time.perf_counter();Journal.reconcile(j,now);reconcile=time.perf_counter()-start
        order=read_json(ROOT/'.market/order.json',{});shops=read_json(ROOT/'.market/shops.json',[])
        start=time.perf_counter();rows=buyer_candidates(order,shops,j.snapshot('catalog.json',{}),j,policy())
        ranking=time.perf_counter()-start;prof.disable();out=io.StringIO()
        pstats.Stats(prof,stream=out).sort_stats('cumulative').print_stats(25)
        epoch=j.get('buyer_revision_epoch');orders=j.events('buyer_order',1000)
        results={r['order']:r for r in j.events('market_result',1000)}
        predictions=j.events('prediction',5000);by_order={}
        for r in predictions:
            ctx=r.get('context',{});oid=(ctx.get('order') or {}).get('order')
            if r.get('role')=='buyer' and ctx.get('operation')=='offer_selection' and oid:
                by_order[oid]=min(by_order.get(oid,r['_ts']),r['_ts'])
        timings=[]
        for r in orders:
            oid=r['order']
            if r['_ts']<epoch or oid not in results:continue
            end=results[oid]['_ts'];begin=by_order.get(oid)
            timings.append({'order':oid,'kind':r.get('kind'),'verified':bool(results[oid].get('verified')),
                'total_seconds':end-r['_ts'],'before_first_choice_seconds':begin-r['_ts'] if begin else None,
                'after_first_choice_seconds':end-begin if begin else None})
        info={'at':now,'order_kind':order.get('kind'),'candidates':len(rows),'ranking_seconds':ranking,
            'reconcile_seconds_without_writes':reconcile,'attempted_insert_counts':dict(j.added),
            'event_read_counts':dict(j.reads),'runtime':read_json(ROOT/'.market/runtime.json',{}),
            'timings':timings,'profile':out.getvalue(),
            'limits':['Read-only reconcile omits actual duplicate INSERT and connection costs.',
                      'Order timings include live peer, settlement and market-result latency; no causal attribution.']}
    path=ROOT/'logs'/f'buyer-v12-profile-{stamp}.json';atomic_json(path,info)
    print('SOURCE',path);print(json.dumps({k:v for k,v in info.items() if k not in ['timings','profile']},ensure_ascii=True))
    print(out.getvalue())


if __name__=='__main__':main()
