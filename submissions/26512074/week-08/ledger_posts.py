"""One public-settlement evidence format for both forum roles."""
import json
import sqlite3
import time
from pathlib import Path
from contextlib import closing
from shop_profit import ownership_tags


def attach(md,body,role,refs=(),now=None,ledger=()):
    md=Path(md);now=time.time() if now is None else now
    path=md/'strategy.sqlite3'
    rows=[]
    if path.exists():
        with closing(sqlite3.connect('file:'+path.resolve().as_posix()+'?mode=ro',uri=True,timeout=5)) as c:
            rows=c.execute("select payload from market_events where kind='settle' and ts between ? and ? order by ts desc,seq desc limit 5000",(now-7200,now)).fetchall()
    if not rows:rows=[(json.dumps(e),) for e in sorted(ledger,key=lambda e:e.get('ts',0),reverse=True) if e.get('kind')=='settle' and 0<=now-e.get('ts',0)<7200]
    tags=ownership_tags();events=[];market=[];seen=set()
    for row in rows:
        e=json.loads(row[0]);d=e.get('data',{})
        if e.get('round',3)!=3 or not isinstance(e.get('seq'),int) or e['seq'] in seen:continue
        if not isinstance(d.get('amount'),(int,float)) or isinstance(d.get('amount'),bool) or d['amount']<=0:continue
        seen.add(e['seq']);market.append(e)
        if (d.get('shop') in tags if role=='shop' else d.get('buyer')=='totato2203-r3'):events.append(e)
        if len(events)>=3:break
    own=bool(events);events=(events or market)[:3]
    if not events:return None
    evidence=['【공개 정산 원장 · '+('우리 판매' if role=='shop' else '우리 구매' if own else '시장 거래')+'】']
    if not own:evidence[0]='【공개 정산 원장 · 시장 거래】'
    for e in events:
        d=e['data'];hours=d.get('warranty_hours') or 0
        evidence.append(f"#{e['seq']} | {d.get('sku') or d.get('item')} | {d['amount']:,.0f} credits | 등급 {d.get('grade') or '미공개'} | 보증 {hours:g}h")
    evidence.append('https://market.ourolabs.ai/api/ledger?after='+str(min(e['seq'] for e in events)-1))
    tail='\n'.join(evidence)
    room=max(0,2000-len(tail)-2)
    text=body.strip()
    if len(text)>room:text=text[:max(0,room-1)].rstrip()+'…'
    ids=[e['seq'] for e in events]+[r for r in refs if isinstance(r,int) and not isinstance(r,bool)]
    return {'as':role,'post':text+'\n\n'+tail,'refs':list(dict.fromkeys(ids))[:10]}
