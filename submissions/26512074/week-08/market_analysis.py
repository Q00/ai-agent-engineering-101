"""Public-ledger market summaries with disclosed inventory interests."""
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
from statistics import median
WINDOW=86400
INTERVAL=300

def summarize(ledger,catalog,inventory,now):
    current,previous=defaultdict(list),defaultdict(list)
    seen=set()
    for e in ledger:
        seq=e.get('seq')
        if e.get('kind')!='settle' or e.get('round', 3)!=3 or seq is None or seq in seen:
            continue
        seen.add(seq)
        d=e.get('data',{})
        sku,price,ts=d.get('sku') or d.get('item'),d.get('amount'),e.get('ts')
        if sku not in catalog or isinstance(price,bool) or not isinstance(price,(int,float)):
            continue
        if not math.isfinite(price) or price<0 or not isinstance(ts,(int,float)) or not math.isfinite(ts):
            continue
        age=now-ts
        if 0<=age<WINDOW:
            current[sku].append((price,seq))
        elif WINDOW<=age<2*WINDOW:
            previous[sku].append(price)
    held=Counter(u['sku'] for u in inventory.get('units',[]) if u.get('grade')!='expired'
                 and (u.get('expires_at') or math.inf)>now)
    rows=[]
    for sku,samples in sorted(current.items(),key=lambda p:(-len(p[1]),p[0]))[:5]:
        prices=[p for p,_ in samples]
        old=previous.get(sku,[])
        baseline=median(old) if len(old)>=3 else None
        rows.append({'sku':sku,'name':catalog[sku]['name'],'count':len(prices),
                     'median':median(prices),'low':min(prices),'high':max(prices),
                     'previous_count':len(old),'change_pct':round((median(prices)/baseline-1)*100,1)
                         if len(prices)>=3 and baseline else None,
                     'refs':[seq for _,seq in samples[:2]],'held':held[sku]})
    return rows

def report(rows):
    if not rows:
        return None
    text=['[시세 분석 | totato2203] 시장 전체에서 관측한 최근 24시간 거래 중 거래빈도 상위 5개 SKU의 정산 가격(credits).']
    for r in rows:
        trend=(f"; 이전 24시간 중앙값 대비 {r['change_pct']:+g}%" if r['change_pct'] is not None
               else '; 추세 판단 표본 부족')
        text.append(f"{r['sku']} {r['name'][:35]}: n={r['count']}, 중앙값 {r['median']:g}, "
                    f"범위 {r['low']:g}~{r['high']:g}{trend}. 이해관계 공개: 내 재고 {r['held']}개.")
    text.append('출처: 공개 ledger settle; 중복 제거, SKU별 집계. 등급·보증·잔여 수명 차이는 통제하지 않았습니다. '
                '수집 범위가 제한되어 전체 시장과 다를 수 있습니다. 원장과 다른 견적도 직접 비교해 주세요.')
    return {'post':'\n'.join(text),'as':'buyer','refs':[seq for r in rows for seq in r['refs']][:10]}

def queue_report(md,ledger,catalog,inventory,now,enabled=False):
    from round3 import read,write,lines
    md=Path(md)
    state=read(md/'analysis-state.json',{})
    rows=summarize(ledger,catalog,inventory,now)
    post=report(rows)
    if post is None:
        return False
    write(md/'analysis-preview.json',{'at':now,'rows':rows,'post':post})
    if not enabled or now-state.get('last_queued',-INTERVAL)<INTERVAL:
        return False
    if any('[시세 분석 | totato2203]' in p.get('post','') for p in lines(md/'outbox.jsonl')):
        return False
    digest=hashlib.sha256(post['post'].encode()).hexdigest()
    # Unchanged prices are still a valid periodic market update. Pending-queue
    # and interval checks prevent duplicate scheduling without stopping updates.
    with (md/'outbox.jsonl').open('a',encoding='utf-8') as out:
        out.write(json.dumps(post,ensure_ascii=False)+'\n')
    write(md/'analysis-state.json',{'last_queued':now,'last_digest':digest})
    return True
