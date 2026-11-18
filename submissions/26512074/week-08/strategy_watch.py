"""Finite live observation of rank, money, supply and validated settlements."""
import argparse
import json
import sqlite3
import time
from pathlib import Path
from contextlib import closing
from urllib.request import urlopen
from collections import Counter
from optimizer import atomic,read
from shop_profit import ownership_tags


def snapshot(md,start):
    now=time.time()
    with urlopen('https://market.ourolabs.ai/api/board?round=3',timeout=10) as response:board=json.load(response)
    seller=next(x for x in board['shops'] if x['name']=='totato2203-r3')
    buyer=next(x for x in board['buyers'] if x['name']=='totato2203-r3')
    players=sorted((x for x in board['players'] if x.get('student')),key=lambda x:-x['student_total'])
    player=next(x for x in players if x['player']=='totato2203')
    tags=ownership_tags();events=[]
    with closing(sqlite3.connect('file:'+(md/'strategy.sqlite3').resolve().as_posix()+'?mode=ro',uri=True,timeout=5)) as c:
        events=[json.loads(r[0]) for r in c.execute("select payload from market_events where ts>=? and kind in ('settle','supply','broken','refund','storage','expire')",(start,))]
        locked=c.execute('select coalesce(sum(amount),0) from funds where until>?',(now,)).fetchone()[0]
    sells=[e['data'] for e in events if e['kind']=='settle' and e['data'].get('shop') in tags]
    buys=[e['data'] for e in events if e['kind']=='settle' and e['data'].get('buyer')=='totato2203-r3']
    supplies=[e['data'] for e in events if e['kind']=='supply' and e['data'].get('shop') in tags]
    refunds={}
    for e in events:
        d=e['data']
        if e['kind'] in ('broken','refund') and d.get('shop') in tags:
            key=d.get('unit',d.get('order',str(e['seq'])))
            refunds[key]=max(refunds.get(key,0),d.get('refunded',0) if e['kind']=='broken' else d.get('amount',0))
    inv=read(md/'inventory.json',{});market=read(md/'strategy-market.json',{})
    own_posts=[p for p in read(md/'forum.json',[]) if p.get('author')=='totato2203-r3' and p.get('ts',0)>=start]
    return dict(at=now,elapsed_seconds=now-start,student_rank=players.index(player)+1,student_total=player['student_total'],
                shop_all=seller['all'],buyer_all=buyer['all'],book=seller['book'],revenue=seller['revenue'],stars=seller['stars_rank'],
                saved=buyer['saved'],forum=buyer['forum'],buyer_deals=buyer['deals'],buyer_posts=buyer['posts'],alive=seller.get('alive'),
                cash=inv.get('balance'),stock_cost=sum(u['cost'] for u in inv.get('units',[])),pending_supply=locked,
                recent_sales=len(sells),recent_revenue=sum(d['amount'] for d in sells),recent_buys=len(buys),
                buyer_grades=dict(Counter(d.get('grade') for d in buys)),supply_calls=len(supplies),supply_units=sum(d['qty'] for d in supplies),
                supply_spend=sum(d['total'] for d in supplies),refunds_posted=sum(refunds.values()),
                storage_posted=sum(e['data'].get('fee',0) for e in events if e['kind']=='storage' and e['data'].get('shop') in tags),
                expiry_posted=sum(e['data'].get('cost',0) for e in events if e['kind']=='expire' and e['data'].get('shop') in tags),
                recent_forum_posts=len(own_posts),post_likes=sum(p.get('likes',0) or 0 for p in own_posts),post_dislikes=sum(p.get('dislikes',0) or 0 for p in own_posts),
                market_age=now-market.get('at',0),strategy=market.get('version'),restock=inv.get('restock'),
                targets=read(md/'score-targets.json',{}).get('targets',{}))


def run(md,seconds,interval,output,resume=False):
    previous=read(output,{}) if resume else {}
    start=previous.get('start',time.time());baseline=previous.get('baseline');samples=previous.get('samples',0);errors=previous.get('errors',[])
    journal=output.with_suffix('.jsonl')
    while True:
        try:
            current=snapshot(md,start);baseline=baseline or current;samples+=1
            deltas={k:current[k]-baseline[k] for k in ['book','revenue','saved','forum','buyer_deals','buyer_posts','student_total']}
            result=dict(start=start,at=current['at'],samples=samples,baseline=baseline,current=current,deltas=deltas,errors=errors,
                        status='complete' if current['at']-start>=seconds else 'observing',warranty_maturity_limit='New six-hour warranty losses are not fully mature in this observation window.')
            with journal.open('a',encoding='utf-8') as f:f.write(json.dumps(current,ensure_ascii=False)+'\n')
            atomic(output,result)
            print(json.dumps(dict(elapsed=round(current['elapsed_seconds']),rank=current['student_rank'],deltas=deltas,sales=current['recent_sales'],buys=current['recent_buys'],supply_units=current['supply_units'],cash=current['cash'],alive=current['alive'],market_age=round(current['market_age'])),ensure_ascii=False),flush=True)
            if result['status']=='complete':return
        except Exception as exc:
            errors.append(dict(at=time.time(),error=type(exc).__name__+': '+str(exc)[:200]));print(json.dumps(errors[-1]),flush=True)
            if time.time()-start>seconds+60:return
        time.sleep(min(interval,max(1,start+seconds-time.time())))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--seconds',type=int,default=1800);p.add_argument('--interval',type=int,default=45)
    p.add_argument('--output',type=Path,default=Path('logs/coordination-observation.json'));p.add_argument('--resume',action='store_true');a=p.parse_args()
    run(Path('.market'),a.seconds,a.interval,a.output,a.resume)
