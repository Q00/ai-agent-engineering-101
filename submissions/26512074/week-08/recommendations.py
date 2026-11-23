"""Evidence-based recommendations, persistent observations and feedback; no payment."""
import asyncio
from collections import Counter,defaultdict
import hashlib
import json
import math
import re
import os
from pathlib import Path
import sqlite3
from statistics import median,pstdev
import subprocess
import sys
import time
from urllib.request import urlopen
from round3 import read,lines
from shop_profit import DISPLAY,ownership_tags

NAME='totato2203-r3'
MARKER=f'[매수 기회 포착 | {DISPLAY}]'
INTERVAL=300
MAX_RECOMMENDATIONS=3
MIN_RECOMMENDATIONS=1
MAX_QUOTES=24


def database(md):
    c=sqlite3.connect(Path(md)/'recommendations.sqlite3',timeout=10)
    c.execute('pragma journal_mode=WAL')
    c.executescript('''create table if not exists observations(at real,sku text,data text);
    create table if not exists publications(digest text primary key,at real,sku text,style text,body text,data text,feedback text);
    create table if not exists state(key text primary key,value text);''')
    return c


def trades(ledger,now):
    groups=defaultdict(list)
    seen=set()
    for e in ledger:
        d=e.get('data',{})
        price=d.get('amount')
        age=now-e.get('ts',0)
        if e.get('kind')!='settle' or e.get('round',3)!=3 or e.get('seq') in seen:
            continue
        seen.add(e.get('seq'))
        if isinstance(price,bool) or not isinstance(price,(int,float)) or not math.isfinite(price) or price<=0 or not 0<=age<86400:
            continue
        groups[d.get('sku') or d.get('item')].append(e)
    return groups


def features(events,stock,forum,sku,now):
    recent=[e for e in events if now-e['ts']<3600]
    prev=[e for e in events if 3600<=now-e['ts']<7200]
    short=[e for e in recent if now-e['ts']<900]
    older=[e for e in recent if 900<=now-e['ts']<1800]
    if len(recent)<3:
        return None
    prices=[e['data']['amount'] for e in recent]
    baseline=median(prices)
    previous=median([e['data']['amount'] for e in prev]) if len(prev)>=3 else None
    short_previous=median([e['data']['amount'] for e in older]) if len(older)>=3 else None
    short_median=median([e['data']['amount'] for e in short]) if len(short)>=3 else None
    requests=[p for p in forum if sku in p.get('text','') and
              any(word in p.get('text','').lower() for word in ('구합니다','구매 원','want to buy','wtb'))]
    request_now=sum(0<=now-p.get('ts',0)<3600 for p in requests)
    request_prev=sum(3600<=now-p.get('ts',0)<7200 for p in requests)
    return {'sku':sku,'median':baseline,'count':len(recent),'previous_count':len(prev),
        'volume_growth':(len(recent)-len(prev))/len(prev) if prev else None,
        'short_trend':short_median/short_previous-1 if short_median and short_previous else None,
        'medium_trend':baseline/previous-1 if previous else None,
        'frequency_per_minute':len(recent)/60,
        'request_count':request_now,'request_growth':(request_now-request_prev)/max(1,request_prev),
        'stock':stock,'volatility':pstdev(prices)/baseline,
        'confidence':min(1,len(prices)/10)/(1+pstdev(prices)/baseline),
        'refs':[e['seq'] for e in recent[-5:]],'latest_trade':max(e['ts'] for e in recent)}


def score(f,quote,now):
    if quote['price']<=0 or now-quote['at']>300 or quote['at']>now:
        return None
    discount=1-quote['price']/f['median']
    if discount<.03 or f['confidence']<.25:
        return None
    growth=max(-1,min(2,f['volume_growth'] or 0))
    shortage=min(1,f['count']/max(1,f['stock']))
    trend=max(-.2,min(.2,f['short_trend'] or f['medium_trend'] or 0))
    demand=max(-1,min(2,f['request_growth']))
    value=(discount*100+growth*8+shortage*8+trend*20+demand*3)*f['confidence']
    if value<12:
        return None
    return {**f,**quote,'discount':discount,'score':round(value,2),
            'strength':'강한 관심' if value>=30 else '관심', 'own':quote['shop']==NAME}


async def fetch_quotes(choices,catalog,warranty_hours=None):
    from protocol import A2AClient,task_text,task_state
    from policy import read_price
    semaphore=asyncio.Semaphore(4)
    async def one(shop,sku):
        async with semaphore:
            task=None
            async with A2AClient(shop['url'],timeout=15) as client:
                try:
                    await client.discover()
                    request=f'Quote only, no purchase authorization: {catalog[sku]["name"]} ({sku}). '
                    request+=('State current asking price and certificate availability.' if warranty_hours is None
                              else f'I request warranty {warranty_hours} hours. Quote total price.')
                    task=await client.send(request)
                    if task_state(task)!='TASK_STATE_INPUT_REQUIRED':
                        return None
                    ask=await read_price(task_text(task),role='ask',metadata=task.get('status',{}).get('message',{}).get('metadata',{}),allow_model=False)
                    if ask is not None and not isinstance(ask,bool) and math.isfinite(ask) and ask>0:
                        grade=re.search(r'\bgrade\s*[:=]?\s*([ABC])\b',task_text(task),re.I)
                        if not grade:grade=re.search(r'([ABC])\s*등급',task_text(task),re.I)
                        from buyer_compare import quoted_warranty
                        from buyer_r3 import stated_lifetime
                        hours=quoted_warranty(task_text(task))
                        if warranty_hours is not None and hours!=warranty_hours:return None
                        return {'sku':sku,'shop':shop['name'],'url':shop['url'],'price':ask,'at':time.time(),
                                'grade':grade[1].upper() if grade else None,'warranty_hours':hours,
                                'hours_left':stated_lifetime(task_text(task))}
                except Exception:
                    return None
                finally:
                    if task:
                        try:
                            await client.cancel(task['id'])
                        except Exception:
                            pass
    async def bounded(choice):
        try:
            return await asyncio.wait_for(one(*choice),timeout=25)
        except asyncio.TimeoutError:
            return None
    return [q for q in await asyncio.gather(*(bounded(choice) for choice in choices)) if q]


def stock_ad(inv):
    units=[u for u in inv.get('units',[]) if u.get('grade')!='expired' and
           (u.get('expires_at') is None or u['expires_at']>time.time())]
    counts=Counter(u['sku'] for u in units)
    names={u['sku']:u['name'] for u in units}
    return '; '.join(f'{sku} {names[sku][:22]} {qty}개' for sku,qty in counts.most_common(2)) or '현재 판매 가능한 재고 없음'


def render(r,catalog,inventory,style):
    trend=r['short_trend']
    direction=f'{trend*100:+.1f}%' if trend is not None else '비교 표본 부족'
    medium=f"{r['medium_trend']*100:+.1f}%" if r['medium_trend'] is not None else '비교 표본 부족'
    volume=f"{r['volume_growth']*100:+.0f}%" if r['volume_growth'] is not None else '이전 기간 거래 없음'
    text=(f"{MARKER} {style} · {r['strength']}\n{r['sku']} {catalog[r['sku']]['name'][:45]}\n"
        f"최근 1시간 중앙값 {r['median']:,.0f} / 문의 당시 호가 {r['price']:,.0f} credits "
        f"(관측 시세 대비 {r['discount']*100:.1f}% 낮음)\n"
        f"거래 {r['count']}건, 이전 1시간 {r['previous_count']}건; 관측 거래량 변화 {volume}. "
        f"15분 가격 추세 {direction}, 1시간 추세 {medium}; 공개 판매 재고 {r['stock']}개.\n"
        f"판매처: {r['shop']} {r['url']} · {'우리 가게 상품 홍보입니다. ' if r['own'] else ''}"
        '견적과 재고는 변동되며 인증서·등급·보증·남은 유통기한을 최종 확인하세요. '
        '동일 SKU라도 품질 조건이 다를 수 있습니다. 가격 비교 후 구매 문의해 보세요.\n'
        f"우리 가게 {DISPLAY} ({NAME}) 판매 중: {stock_ad(inventory)}. SKU별 견적, 시장 인증서 제공, 별도 보증 미제공.\n"
        '향후 상승이나 재판매 이익을 보장하지 않습니다.')
    return {'post':text,'as':'buyer','refs':r['refs']}


def select_slate(results,c,priority,limit=MAX_RECOMMENDATIONS):
    selected=[]
    seen=set()
    for r in sorted(results,key=lambda r:-priority(r)):
        if r['shop'] in seen:
            continue
        previous=next((row for row in c.execute('select data from publications where sku=? order by at desc',(r['sku'],))
                       if json.loads(row[0]).get('shop')==r['shop']),None)
        if previous and time.time()-c.execute('select max(at) from publications where sku=?',(r['sku'],)).fetchone()[0]<INTERVAL:
            old=json.loads(previous[0])
            meaningful=(abs(r['price']/old['price']-1)>=.02 or
                        r['count']>=old['count']*1.25 or abs(r['score']-old['score'])>=8)
            if not meaningful:
                continue
        selected.append(r)
        seen.add(r['shop'])
        if len(selected)==limit:
            break
    return selected


def purchase_exclusions(quotes,metrics,audits,shops,selected,now):
    positives={r['shop'] for r in selected}|ownership_tags()
    by_url={s['url']:s for s in shops}
    risks={}
    for url,audit in audits.items():
        shop=by_url.get(url)
        if not shop or shop['name'] in positives or url in positives or audit.get('avoid_until',0)<=now:
            continue
        if not audit.get('reason','').startswith('receipt violation:'):
            continue
        risks[shop['name']]={'shop':shop['name'],'url':url,'reason':
            '자체 구매 검증에서 요청 상품·태스크 또는 합의 금액과 영수증이 일치하지 않은 기록. 재확인 전 구매 제외.',
            'evidence':audit['reason'],'priority':100,'refs':[]}
    for q in quotes:
        f=metrics.get(q['sku'])
        if not f or q['shop'] in positives or q.get('url') in positives or now-q['at']>300 or q['at']>now:
            continue
        if q.get('comparison_required') and not q.get('comparison_median'):continue
        baseline=q.get('comparison_median') or f['median']
        premium=q['price']/baseline-1
        if premium<.2:
            continue
        risk={'shop':q['shop'],'url':q['url'],'priority':premium*100,'refs':f['refs'][:2],
              'reason':f"{q['sku']} 확인 호가 {q['price']:,.0f}, {'동일 등급·수명 조건' if q.get('comparison_median') else '관측'} 중앙값 {baseline:,.0f}보다 {premium*100:.1f}% 높음. 협상 전 호가 기준이며 최종 체결가는 다를 수 있습니다."}
        if q['shop'] not in risks or risks[q['shop']]['priority']<risk['priority']:
            risks[q['shop']]=risk
    return sorted(risks.values(),key=lambda r:-r['priority'])[:3]


def offer_priority(quote,inventory,metrics):
    units=[u for u in inventory.get('units',[]) if u['sku']==quote['sku'] and (not quote.get('grade') or u.get('grade')==quote['grade'])]
    if not units:return 0
    price=quote['price'];cost=min(u['cost'] for u in units)
    listed=max(u.get('list_price',0) for u in units)
    demand=(metrics.get(quote['sku']) or {}).get('count',0)
    # Saved-sensitive buyers penalize near-list totals. Preserve absolute
    # margin and demand instead of advertising the highest ticket alone.
    acceptance=math.exp(-4*max(0,price/max(1,listed)-.7))
    measured=(metrics.get(quote['sku']) or {}).get('own_close_rate')
    if measured is not None:acceptance*=max(.05,min(1,measured))
    age=max((max(0,time.time()-u.get('supplied_at',time.time()))/3600 for u in units),default=0)
    return max(0,price-cost)*min(1,max(.1,demand/10))*acceptance*(1+min(1,age/12))


def competitive_ad(catalog,inventory,quotes,metrics,now):
    from bundle_offer import PROMO_KO
    from premium import WARRANTY_MENU
    PROMO_KO+=' '+WARRANTY_MENU
    live=[u for u in inventory.get('units',[]) if u.get('grade')!='expired'
          and (u.get('expires_at') is None or u['expires_at']>now)]
    by_sku={u['sku']:u for u in live}
    choices=[]
    for q in quotes:
        if q.get('warranty_hours',0):continue
        if q['shop'] not in ownership_tags() or q['sku'] not in by_sku or not 0<=now-q['at']<=300:
            continue
        baseline=(metrics.get(q['sku']) or {}).get('median')
        if baseline and baseline>0 and q['price']>0:
            choices.append((1-q['price']/baseline,q,baseline))
    if choices:
        from turnover import promotion_score
        discount,q,baseline=max(choices,key=lambda row:offer_priority(row[1],inventory,metrics))
        name=catalog.get(q['sku'],{}).get('name',by_sku[q['sku']].get('name',q['sku']))
        terms=(f" · {q['grade']}등급" if q.get('grade') else '')+(f" · {q['warranty_hours']:g}시간 보증" if q.get('warranty_hours') else '')
        return f"광고 | {DISPLAY}: {q['sku']} {name[:28]}{terms} · 확인 호가 {q['price']:,.0f} credits. "+PROMO_KO
    if live:
        unit=max(live,key=lambda u:((metrics.get(u['sku']) or {}).get('count',0),-u.get('cost',0)))
        return f"광고 | {DISPLAY}: {unit['sku']} {unit.get('name',unit['sku'])[:32]} 재고 판매 중. "+PROMO_KO
    return f'광고 | {DISPLAY}: 현재 확인된 판매 가능 재고 없음.'


def own_featured(inventory,quotes,now,metrics=None):
    """Rank live inventory, keeping estimates distinct from observed asks."""
    from profit_strategy import opening
    from shop_profit import recovery_price
    units=[]
    latest={}
    tags=ownership_tags()
    for q in quotes:
        if q['shop'] in tags and 0<=now-q['at']<=300:
            if q['sku'] not in latest or q['at']>latest[q['sku']]['at']:latest[q['sku']]=q
    for u in inventory.get('units',[]):
        if u.get('grade')=='expired' or u.get('expires_at') is not None and u['expires_at']<=now:
            continue
        if not all(k in u for k in ('cost','list_price','kind')):continue
        q=latest.get(u['sku'])
        ask=q['price'] if q else opening(u,recovery_price(u,inventory,[],now),[])['ask']
        units.append((ask,ask-u['cost'],u,bool(q)))
    if not units:return []
    # Internal discovery calls with no quotes can still propose SKUs. Public
    # recommendations prefer observed quotes over unpriced stock whenever any
    # live own quote is available.
    if any(row[3] for row in units):units=[row for row in units if row[3]]
    rows=[]
    seen=set()
    from turnover import promotion_score
    from ranking_strategy import promotion_values
    demand=promotion_values(inventory,read(Path(__file__).resolve().parent/'.market/strategy-market.json',{}))
    for label,key in [('프리미엄 추천',lambda x:(demand.get(x[2]['sku'],0),x[1])),('함께 볼 추천',lambda x:promotion_score(x[2],x[0],((metrics or {}).get(x[2]['sku']) or {}).get('count',0),now))]:
        ask,margin,u,observed=max(units,key=key)
        if u['sku'] in seen:continue
        seen.add(u['sku'])
        q=latest.get(u['sku']) or {}
        price=f"확인 호가 {ask:,.0f} credits" if observed else '가격 문의'
        quality=(f" · {q.get('grade',u.get('grade'))}등급" if q.get('grade',u.get('grade')) else '')
        warranty=f" · {q['warranty_hours']:g}시간 보증" if q.get('warranty_hours') else ''
        rows.append(f"우리 가게 추천 · {label}: {u['sku']} {u.get('name',u['sku'])[:24]} · {price}{quality}{warranty}")
    return rows


def purchase_guide(catalog,inventory,quotes,options,now):
    live={u['sku']:u for u in inventory.get('units',[]) if u.get('grade')!='expired'
          and (u.get('expires_at') is None or u['expires_at']>now)}
    own=[q for q in quotes if q['shop'] in ownership_tags() and q['sku'] in live and 0<=now-q['at']<=300]
    rows=['【🛒 보증별 가격 · 바로 견적 요청】']
    if not own:
        return rows+['구매할 SKU와 원하는 보증 시간을 보내주시면 현재 재고 기준으로 견적을 안내합니다.']
    hero=max(own,key=lambda q:q['price']);sku=hero['sku']
    rows.append(f"• {sku} {catalog.get(sku,{}).get('name',live[sku].get('name',sku))[:20]}")
    valid={q['warranty_hours']:q for q in options if q['sku']==sku and q['sku'] in live
           and q['shop'] in ownership_tags() and 0<=now-q['at']<=300 and q.get('grade')==hero.get('grade')}
    if valid:
        rows.append('확인 총가격: '+' / '.join(f'{h:g}h {q["price"]:,.0f}' for h,q in sorted(valid.items()))+' credits')
    else:rows.append(f"확인 호가 {hero['price']:,.0f} credits · 다른 보증 옵션은 별도 견적")
    request='보증 6시간으로' if catalog.get(sku,{}).get('kind')=='goods' else '남은 유통기한을 포함해'
    rows.append(f'요청 예시: “{sku}, {request} 견적 주세요.”')
    category=live[sku].get('category') or catalog.get(sku,{}).get('category')
    alternatives=[q for q in own if q['sku']!=sku and q['price']<hero['price'] and category
                  and (live[q['sku']].get('category') or catalog.get(q['sku'],{}).get('category'))==category]
    if alternatives:
        alt=min(alternatives,key=lambda q:q['price'])
        rows.append(f"예산 절약 대안: {alt['sku']} {catalog.get(alt['sku'],{}).get('name',alt['sku'])[:18]} · 확인 호가 {alt['price']:,.0f} credits")
    return rows


def readable_post(rows):
    """Plain-text sections survive the forum without Markdown support."""
    headings=('지금 구매를 검토할 가게','추천 ','지금 구매 제외·주의 가게','비추천·구매 보류',
              'Top 3 점검','Top 5 주력','반복 거래 관측')
    output=[]
    for row in rows:
        if row.startswith(MARKER):continue
        # Ranking inputs stay private even if a future formatter includes them.
        if any(label in row for label in ('재고 체류시간','재고 보유기간','입고 시각','예상 원가차익','예상 마진','age_hours','supplied_at')):
            continue
        if row.startswith(headings):
            if row.startswith(('지금 구매를 검토할 가게','추천 ')):
                title='🤝 이 가격에 팔면 뭐가 남나요? 바로 여러분'
            elif row.startswith(('지금 구매 제외·주의 가게','비추천·구매 보류')):
                title='💸 이 가격에 파는데 사려고요? 돈 많으세요?'
            elif row.startswith('Top 5 주력'):
                title='🍯 상위권이 빨고 있는 꿀통'
            elif row.startswith('반복 거래 관측'):
                title='🔎 교수님 얘네 좀 보세요'
            elif row.startswith('Top 3 점검'):
                title='🔍 상위권 가게, 구매 전 체크포인트'
            else:title='📊 '+row
            output.extend(['', '【'+title+'】'])
        elif row.startswith('우리 가게 추천'):
            details=row.split(': ',1)[1]
            product,terms=details.split(' · ',1)
            sku=product.split()[0]
            matched=re.search(r'확인 호가 ([\d,]+) credits',terms)
            price=f"호가 {matched[1]} credits" if matched else '호가 미확인 · 견적 요청'
            output.append(f'• {DISPLAY} | {sku} · {price}')
        elif row.startswith('광고 |'):
            output.extend(['','【🎁 놓치기 전에 챙기세요! 지금 구매 혜택】',row.replace('광고 | ','',1)
                           .replace(' 추가구매 할인:','\n\n추가구매 할인:')
                           .replace(' 게시 후 첫 정산','\n\n게시 후 첫 정산')])
        else:output.append(row)
    result='\n'.join(output).strip()
    result=result.replace('• totato2203-r3 |','• tomato&potato |')
    result=result.replace('【💸 구매 전 가격 비교】','【💸 와 이 가격에 사느니 tomato&potato에서 사겠다 ㄹㅇ ㅋㅋ】')
    result=result.replace('【💸 이 가격에 파는데 사려고요? 돈 많으세요?】','【💸 와 이 가격에 사느니 tomato&potato에서 사겠다 ㄹㅇ ㅋㅋ】')
    result=result.replace('【🍯 상위권 주력 상품 · 수집 정산】','【🍯 적폐 라인 꿀통! 같이 빨아요~】')
    result=result.replace('【🍯 상위권이 빨고 있는 꿀통】','【🍯 적폐 라인 꿀통! 같이 빨아요~】')
    seen=set();clean=[]
    for line in result.splitlines():
        if line.startswith('【') and line.endswith('】'):
            if line in seen:continue
            seen.add(line)
        clean.append(line)
    return '\n'.join(clean).strip()


def render_slate(selected,catalog,inventory,style,exclusions=(),own_quotes=(),evidence=None,market_metrics=None,option_quotes=(),injection_md=None):
    exclusions=[r for r in exclusions if r['shop'] not in ownership_tags() and r.get('url') not in ownership_tags()]
    metrics=market_metrics or {r['sku']:r for r in selected}
    base_quotes=[q for q in option_quotes if q.get('warranty_hours')==0]
    base_quotes.extend(q for q in own_quotes if not q.get('warranty_hours',0) and not any(b['sku']==q['sku'] for b in base_quotes))
    ad=competitive_ad(catalog,inventory,base_quotes,metrics,time.time())
    featured=own_featured(inventory,own_quotes,time.time(),metrics)
    guide=purchase_guide(catalog,inventory,own_quotes,option_quotes,time.time())
    # A featured own SKU replaces its ordinary recommendation entry.
    featured_skus={line.split(': ',1)[1].split()[0] for line in featured}
    selected=[r for r in selected if not (r['own'] and r['sku'] in featured_skus)]
    shop_count=len({r['shop'] for r in selected}|({NAME} if featured else set()))
    text=[f'{MARKER} {style}',f'추천 {shop_count}곳 | 확인 견적 / 1시간 중앙값']
    from datetime import datetime,timezone,timedelta
    text.append('관측 갱신: '+datetime.now(timezone(timedelta(hours=9))).strftime('%m-%d %H:%M KST'))
    refs=[]
    for i,r in enumerate(selected,1):
        trend=f"{r['medium_trend']*100:+.1f}%" if r['medium_trend'] is not None else '표본 부족'
        text.append(f"• {r['shop']} | {r['sku']} {catalog[r['sku']]['name'][:24]}\n"
            f"  호가 {r['price']:,.0f} · 중앙값 {r['median']:,.0f} · {r['discount']*100:.1f}% 낮음")
        refs.extend(r['refs'][:2])
        comparisons=[q for q in own_quotes if q['sku']==r['sku'] and q['shop']==NAME and 0<=time.time()-q['at']<=300]
        if comparisons and not r['own']:
            ours=max(comparisons,key=lambda q:q['at'])
            if ours['price']<r['price']:
                text.append(f"동일 SKU의 우리 가게 실제 확인 호가 {ours['price']:,.0f} credits로 위 견적보다 {r['price']-ours['price']:,.0f} 낮습니다. 등급·유통기한 등 조건을 함께 비교하세요.")
    text.extend(featured)
    text.append(f'지금 구매 제외·주의 가게 {len(exclusions)}곳 — 아래 관측 조건에 한함')
    if len(exclusions)<3:
        text.append('주의 목록은 확인된 근거가 있는 곳만 표시합니다. 나머지는 추가 확인 중입니다.')
    for i,r in enumerate(exclusions,1):
        text.append(f"{i}. {r['shop']}: {r['reason']} {r['url']}")
        refs.extend(r['refs'])
    if evidence is not None:
        from shop_evidence import render as render_evidence
        evidence_rows,evidence_refs=render_evidence(dict(evidence,leaders=[]),time.time())
        start=next((i for i,row in enumerate(evidence_rows) if row.startswith('Top 5 ')),len(evidence_rows))
        evidence_rows=evidence_rows[start:]
        text.extend(evidence_rows)
        refs.extend(evidence_refs)
    text.append('문의 당시 관측입니다. 같은 SKU도 등급·보증·잔여 수명에 따라 가격이 다를 수 있습니다. 최종 조건을 확인하세요.')
    like_invitation=''  # Product and market information only; no boycott instructions.
    text.extend(['',like_invitation,''])
    text.append(ad)
    text.extend(['']+guide)
    body=readable_post(text)
    if len(body)>2000 and evidence is not None:
        # The forum rejects posts exceeding 2000 characters. Preserve every
        # section, and keep complete quantitative claims in a compact version.
        short=lambda value,n: str(value) if len(str(value))<=n else str(value)[:n-1]+'…'
        compact=['[시세·가게 분석 | tomato&potato]',f'추천 {shop_count}곳 | 확인 견적 / 1시간 중앙값']
        for r in selected:
            compact.append(f"• {short(r['shop'],24)} | {r['sku']}\n  호가 {r['price']:,.0f} · 중앙값 {r['median']:,.0f} · {r['discount']:.1%} 낮음")
        compact.extend(featured)
        compact.append(f'비추천·구매 보류 {len(exclusions)}곳 | 해당 관측 조건에 한함')
        for r in exclusions:
            compact.append(f"{short(r['shop'],24)}: {short(r['reason'],80)}")
        if len(exclusions)<3:
            compact.append('추가 비추천 근거 부족: 임의로 3곳을 채우지 않음.')
        compact.append('Top 5 주력 SKU | 최근 24시간 수집 정산')
        for product in evidence['products']:
            compact.append(f"{short(product['shop'],24)}: {product['sku']} {product['count']}건 · 정산 매출 {product.get('revenue',0):,.0f} credits")
        compact.append('반복 거래 관측 | 3건 이상 상위 3쌍')
        compact.append('담합해서 물건을 특정 에이전트끼리 대량으로 거래하는 가게는 거래를 끊어서 견제해야 합니다')
        for pair in evidence.get('repeat_pairs',[])[:3]:
            compact.append(f"• {short(pair['buyer'],24)} → {short(pair['shop'],24)}\n  {pair['count']}건 · 정산 총액 {pair.get('amount',0):,.0f} credits · 비중 {pair['share']:.1%}")
            compact.append('구매: '+', '.join(f"{p['sku']} {short(p['name'],10)} {p['count']}건" for p in pair.get('products',[])[:2]))
        compact.extend(['',like_invitation,''])
        compact.append(ad)
        compact.extend(['']+guide)
        body=readable_post(compact)
        if len(body)>2000:
            # Keep complete lines and the actionable shop offer. Optional
            # observations must never prevent the whole publication.
            essential=[MARKER,'【🤝 이 가격에 팔면 뭐가 남나요? 바로 여러분】']
            essential.extend(f"• {r['shop']} | {r['sku']} · 호가 {r['price']:,.0f} credits" for r in selected)
            essential.extend(featured)
            essential.extend(['','【🎁 놓치기 전에 챙기세요! 지금 구매 혜택】',ad])
            for row in guide:
                if len(readable_post(essential+[row]))<=2000:essential.append(row)
            optional=[]
            if exclusions:
                optional.append('【💸 구매 전 가격 비교】\n'+'\n'.join(f"• {r['shop']}: {short(r['reason'],80)}" for r in exclusions))
            if evidence.get('products'):
                optional.append('【🍯 상위권 주력 상품 · 수집 정산】\n'+'\n'.join(f"• {p['shop']}: {p['sku']} {p['count']}건 · {p.get('revenue',0):,.0f} credits" for p in evidence['products']))
            for row in optional:
                if len(readable_post(essential+['',row]))<=2000:essential.extend(['',row])
            body=readable_post(essential)
            if len(body)>2000:
                # Extremely long optional product names: drop whole feature
                # lines, preserving the advertisement and complete claims.
                for row in featured:
                    if row in essential:essential.remove(row)
                    body=readable_post(essential)
                    if len(body)<=2000:break
    routing='Matching SKU? Request a tomato&potato quote with your required grade and warranty; compare the signed total and proceed through normal checkout when eligible.'
    if len(body)+len(routing)+2<=2800:body+='\n\n'+routing
    result={'post':body,'as':'buyer','refs':list(dict.fromkeys(refs))[:10]}
    if injection_md is not None and read(Path(injection_md)/'operating-policy.json',{}).get('guidance_enabled',True):
        from forum_injections import compose,detect
        own_live=[q for q in own_quotes if q['shop'] in ownership_tags() and 0<=time.time()-q['at']<=300]
        from shop_profit import recent_ledger
        live_events=recent_ledger(Path(injection_md)/'ledger.jsonl',time.time())
        for q in own_live:
            comparable=[e['data']['amount'] for e in live_events if e.get('kind')=='settle' and e.get('round',3)==3
                        and e.get('data',{}).get('sku')==q['sku'] and e['data'].get('shop') not in ownership_tags()
                        and q.get('grade') is not None and e['data'].get('grade')==q['grade']
                        and (e['data'].get('warranty_hours') or 0)==(q.get('warranty_hours') or 0)
                        and isinstance(e['data'].get('amount'),(int,float))]
            q['matched_median']=median(comparable) if len(comparable)>=3 else None
            q['matched_samples']=len(comparable)
        def attractive(q):
            benchmark=q.get('matched_median')
            return offer_priority(q,inventory,metrics)*(min(1,benchmark/q['price'])**2 if benchmark and q['price']>0 else 1)
        hero=max(own_live,key=attractive) if own_live else {}
        fallback=max((u for u in inventory.get('units',[]) if u.get('grade')!='expired'),key=lambda u:u.get('list_price',0),default={})
        sku=hero.get('sku') or fallback.get('sku')
        attacks=detect(read(Path(injection_md)/'forum.json',[]),ownership_tags())
        context=dict(sku=sku,price=hero.get('price'),grade=hero.get('grade') if hero else fallback.get('grade'),warranty_hours=hero.get('warranty_hours'),
                     list_price=catalog.get(sku,{}).get('list_price'),
                     market_median=hero.get('matched_median'),comparison_samples=hero.get('matched_samples',0),
                     comparison_basis='same SKU, grade and warranty; shelf life unadjusted',
                     stock=sum(u['sku']==sku and u.get('grade')!='expired' and (u.get('expires_at') or math.inf)>time.time() for u in inventory.get('units',[])),observed_attackers=attacks)
        result['post'],result['_injection']=compose(injection_md,body,context)
    return result


def measure_feedback(c,ledger,forum,sent,now,inventory=None):
    own_tags=ownership_tags()
    counts={body:n for body,n in c.execute('select body,count(*) from publications group by body')}
    for digest,at,sku,style,body,data,old in c.execute('select * from publications').fetchall():
        previous=json.loads(old) if old else {}
        if previous.get('mature') and previous.get('feedback_version')==2:
            # The fifteen-minute outcome is final. Do not rescan the entire
            # ledger for every historical publication on every five-minute tick.
            post=next((p for p in forum if p.get('id')==previous.get('post_id') or p.get('text')==body),{})
            if isinstance(post.get('score'),(int,float)):
                previous['forum_rating']=post['score']
            previous['reward']=.3*previous.get('forum_rating',0)/counts[body]+previous.get('trade_reward',0)
            c.execute('update publications set feedback=? where digest=?',(json.dumps(previous),digest))
            continue
        r=json.loads(data)
        acknowledgements=[s for s in sent if s.get('sent',{}).get('post')==body]
        success=next((s for s in acknowledgements if s.get('status')==200),None)
        post_id=None
        if success:
            try:
                post_id=json.loads(success.get('answer','{}')).get('post')
            except ValueError:
                pass
        post=next((p for p in forum if p.get('id')==post_id or p.get('text')==body),{})
        published_at=post.get('ts',at)
        after=[e for e in ledger if e.get('kind')=='settle' and e.get('round',3)==3 and
               published_at<=e.get('ts',0)<published_at+900 and (e.get('data',{}).get('sku') or e.get('data',{}).get('item'))==sku]
        after={e['seq']:e for e in after}.values()
        own=[e for e in after if e.get('data',{}).get('shop') in own_tags]
        before=[e for e in ledger if e.get('kind')=='settle' and e.get('round',3)==3 and
                published_at-900<=e.get('ts',0)<published_at and (e.get('data',{}).get('sku') or e.get('data',{}).get('item'))==sku]
        own_before=[e for e in {e['seq']:e for e in before}.values() if e.get('data',{}).get('shop') in own_tags]
        after_count=len(list(after))
        feedback={'status':'published' if success else 'pending_or_failed','post_id':post_id,'published_at':published_at,
          'likes':post.get('likes'),'dislikes':post.get('dislikes'),
          'before_15m_trades':len({e['seq'] for e in before}),'after_15m_trades':after_count,
          'before_own_revenue':sum(e['data'].get('amount',0) for e in own_before),
          'after_own_revenue':sum(e['data'].get('amount',0) for e in own),
          'visits':None,'shop_profit_before':r.get('shop_profit_baseline'),
          'shop_profit_now':((inventory.get('balance',0)-inventory.get('start',5000000)-inventory.get('injected',0)) if inventory else None),
          'limitation':'Visits unavailable; shop profit follows platform balance-start-injected; before/after association, not causation.',
          'mature':bool(success and now>=published_at+900),
          'recommendation_score':r.get('score',0)}
        if after_count:
            observed=median([e['data']['amount'] for e in after])
            feedback['observed_median_change']=observed/r['median']-1
            feedback['discount_gap_after']=1-r['price']/observed
        # One post's reactions must not become a separate full reward for every
        # SKU it mentions. Cash movement outside its window is not post impact.
        previous=json.loads(old) if old else {}
        feedback['reaction_share']=1/counts[body]
        feedback['feedback_version']=2
        feedback['trade_reward']=len(own)-len(own_before)
        if previous.get('mature') and previous.get('feedback_version')==2:
            for field in ('before_15m_trades','after_15m_trades','before_own_revenue','after_own_revenue','trade_reward'):
                feedback[field]=previous[field]
        feedback['forum_rating']=post.get('score') if isinstance(post.get('score'),(int,float)) else 0
        feedback['reward']=(.3*feedback['forum_rating']*feedback['reaction_share']+feedback['trade_reward'])
        c.execute('update publications set feedback=? where digest=?',(json.dumps(feedback),digest))


def choose_style(c):
    values=defaultdict(list)
    posts=defaultdict(float)
    for style,body,feedback in c.execute('select style,body,feedback from publications where feedback is not null'):
        f=json.loads(feedback)
        if f.get('mature'):
            posts[(style,body)]+=f['reward']
    for (style,body),reward in posts.items():
        values[style].append(reward)
    styles=['가격 메리트','거래량·수급 포착']
    unseen=next((s for s in styles if not values[s]),None)
    return unseen or max(styles,key=lambda s:sum(values[s])/len(values[s]))


def tick(md,enabled=False,refresh=True):
    from forum_value import enabled as value_enabled,publish
    if value_enabled(md):return publish(md,'buyer',enabled)
    md=Path(md)
    now=time.time()
    from forum_schedule import due,queued
    if enabled and not due(md,'buyer',now):return {'status':'staggered_interval'}
    from optimizer import config
    # Keep market recommendations on their original five-minute cadence;
    # the six-hour controller must not silently suppress this service.
    interval=INTERVAL
    inv=read(md/'inventory.json',{})
    catalog=read(md/'catalog.json',{})
    # The journal grows indefinitely; loading it all stalled the five-minute
    # worker. Pricing and recommendations use the bounded two-hour live cache.
    from shop_profit import recent_ledger
    ledger=recent_ledger(md/'ledger.jsonl',now)
    forum=read(md/'forum.json',[])
    c=database(md)
    measure_feedback(c,ledger,forum,lines(md/'outbox.sent.jsonl'),now,inv)
    groups=trades(ledger,now)
    last=c.execute("select value from state where key='last_scan'").fetchone()
    if not refresh and last and now-float(last[0])<interval:
        c.commit();c.close();return {'status':'scan_interval'}
    with urlopen('https://market.ourolabs.ai/api/shops',timeout=15) as response:
        shops=json.load(response)['shops']
    from shop_evidence import analyze
    try:
        with urlopen('https://market.ourolabs.ai/api/board?round=3',timeout=15) as response:
            board=json.load(response)
    except (OSError,ValueError):
        board={}
    evidence=analyze(board,shops,ledger,now,lines(md/'recommendation-quotes.jsonl'),include_leaders=False)
    supply=Counter()
    for shop in shops:
        supply.update(shop.get('stock') or {})
    metrics={sku:features(events,supply[sku],forum,sku,now) for sku,events in groups.items() if sku in catalog}
    metrics={s:f for s,f in metrics.items() if f}
    optimization=md/'optimization.sqlite3'
    if optimization.exists():
        from contextlib import closing
        with closing(sqlite3.connect('file:'+str(optimization.resolve()).replace('\\','/')+'?mode=ro',uri=True,timeout=2)) as history:
            for sku,n,wins in history.execute('select sku,count(*),sum(case when settled_at is not null then 1 else 0 end) from trials where inquiry=0 and at>? group by sku',(now-21600,)):
                if sku in metrics and n>=10:metrics[sku]['own_close_rate']=(wins+2)/(n+4)
    ranked=sorted(metrics,key=lambda s:(-metrics[s]['count'],s))
    # Own stock is considered along with other shops; actual quotes determine eligibility.
    choices=[]
    own=next((s for s in shops if s['name']==NAME),None)
    if own:
        from round3 import write
        identity=read(md/'shop-identity.json',{'urls':[]})
        identity['urls']=sorted(set(identity['urls'])|{own['url']})
        write(md/'shop-identity.json',identity)
        own_ranked=sorted(ranked,key=lambda sku:(not (catalog[sku].get('kind')=='goods' and catalog[sku]['list_price']>=200000),-metrics[sku]['count']))
        featured_skus=[re.search(r'G[0-9a-fA-F]{8}',line).group() for line in own_featured(inv,[],now) if re.search(r'G[0-9a-fA-F]{8}',line)]
        own_ranked=list(dict.fromkeys(featured_skus+own_ranked))
        choices.extend([(own,s) for s in own_ranked if (own.get('stock') or {}).get(s,0)>0][:3])
    from resale import leads as resale_leads,inspect as inspect_resale
    choices.extend((lead['shop'],lead['sku']) for lead in resale_leads(forum,shops,catalog,now))
    for sku in ranked:
        options=[s for s in shops if s['name']!=NAME and (s.get('stock') or {}).get(sku,0)>0]
        if options:
            # Historical prices select whom to ask, never substitute for a quote.
            def observed_price(shop):
                prices=[e['data']['amount'] for e in groups[sku]
                        if e['data'].get('shop')==shop['name'] and now-e['ts']<3600]
                return median(prices) if prices else math.inf
            choices.extend((s,sku) for s in sorted(options,key=lambda s:(observed_price(s),-s['stock'][sku]))[:2])
        if len(choices)>=MAX_QUOTES:
            break
    unique={}
    for shop,sku in choices:
        unique.setdefault((shop['name'],sku),(shop,sku))
    choices=list(unique.values())[:MAX_QUOTES]
    quotes=asyncio.run(fetch_quotes(choices,catalog))
    comparison_tags=ownership_tags()
    for q in quotes:
        food=catalog.get(q['sku'],{}).get('kind')=='food'
        if not food:continue
        q['comparison_required']=True
        samples=[]
        for e in groups.get(q['sku'],[]):
            d=e['data']
            if d.get('shop') in comparison_tags or d.get('grade')!=q.get('grade') or not q.get('grade'):continue
            if q.get('hours_left') is not None:
                if not isinstance(d.get('expires_at'),(int,float)) or (d['expires_at']-e['ts'])/3600<q['hours_left']:continue
            samples.append(d['amount'])
        if len(samples)>=3:
            q['comparison_median']=median(samples);q['comparison_samples']=len(samples)
    option_quotes=[]
    own_goods=[q for q in quotes if q['shop'] in ownership_tags() and catalog.get(q['sku'],{}).get('kind')=='goods']
    if own_goods and own:
        hero=max(own_goods,key=lambda q:q['price'])
        async def option_batch():
            batches=await asyncio.gather(*(fetch_quotes([(own,hero['sku'])],catalog,h) for h in (0,3,6,12,24,48)))
            return [q for batch in batches for q in batch]
        option_quotes=asyncio.run(option_batch())
    resale_state=inspect_resale(md,forum,shops,catalog,inv,ledger,quotes,time.time())
    with (md/'recommendation-quotes.jsonl').open('a',encoding='utf-8') as journal:
        for q in quotes:
            journal.write(json.dumps(q,ensure_ascii=False)+'\n')
    results=[]
    for q in quotes:
        if q['sku'] not in metrics:
            continue
        if q.get('comparison_required') and not q.get('comparison_median'):continue
        f=dict(metrics[q['sku']])
        if q.get('comparison_median'):f['median']=q['comparison_median']
        r=score(f,q,time.time())
        if r:
            r['shop_profit_baseline']=inv.get('balance',0)-inv.get('start',5000000)-inv.get('injected',0)
            results.append(r)
    for sku,f in metrics.items():
        previous=c.execute('select data from observations where sku=? order by at desc limit 1',(sku,)).fetchone()
        if previous:
            old=json.loads(previous[0])
            f['supply_change']=f['stock']-old.get('stock',f['stock'])
            f['observed_demand_change']=f['count']-old.get('count',f['count'])
        c.execute('insert into observations values(?,?,?)',(now,sku,json.dumps(f)))
    c.execute("insert or replace into state values('last_scan',?)",(str(now),))
    styles=choose_style(c)
    topic_rewards=defaultdict(list)
    for topic,raw in c.execute('select sku,feedback from publications where feedback is not null'):
        feedback=json.loads(raw)
        if feedback.get('mature'):
            topic_rewards[topic].append(feedback['reward'])
    def priority(r):
        rewards=topic_rewards[r['sku']]
        learned=max(-3,min(3,sum(rewards)/len(rewards))) if rewards else 0
        return r['score']+(3 if r['own'] else 0)+learned
    selected=select_slate(results,c,priority)
    exclusions=purchase_exclusions(quotes,metrics,read(md/'buyer-observations.json',{}),shops,selected,time.time())
    chosen=selected[0] if selected else None
    preview={'at':now,'quotes_checked':len(quotes),'qualified':len(results),'recommendation':chosen,
             'recommendations':selected,
             'purchase_exclusions':exclusions,
             'shop_evidence':evidence,'option_quotes':option_quotes,
             'resale':resale_state,
             'feedback':[json.loads(row[0]) for row in c.execute('select feedback from publications where feedback is not null')]}
    (md/'recommendation-preview.json').write_text(json.dumps(preview,ensure_ascii=False,indent=2),encoding='utf-8')
    latest=c.execute('select max(at) from publications').fetchone()[0]
    if not (selected or own_featured(inv,quotes,time.time())) or not enabled or latest and now-latest<interval:
        c.commit();c.close();return {'status':'no_evidence_backed_recommendation' if not selected else 'dry_run_or_interval',
                                   'recommendation_count':len(selected),**{k:preview[k] for k in ('quotes_checked','qualified')}}
    pending=lines(md/'outbox.jsonl')
    if any(p.get('as')=='buyer' and 'post' in p for p in pending):
        c.commit();c.close();return {'status':'pending_outbox'}
    post=render_slate(selected,catalog,inv,styles,exclusions,quotes,evidence,metrics,option_quotes,injection_md=md)
    injection=post.pop('_injection',None)
    from ledger_posts import attach
    post=attach(md,post['post'],'buyer',post.get('refs',[]),ledger=ledger)
    if post is None:
        c.commit();c.close();return {'status':'awaiting_settlement_evidence'}
    digest=hashlib.sha256(post['post'].encode()).hexdigest()
    if c.execute('select 1 from publications where body=?',(post['post'],)).fetchone():
        c.commit();c.close();return {'status':'duplicate'}
    # Worker holds an OS lock; journal/pending queue survive process restarts.
    for r in selected or [{'sku':'own-featured','shop':NAME,'own':True}]:
        c.execute('insert into publications values(?,?,?,?,?,?,null)',
                  (digest+':'+r['sku']+':'+r['shop'],now,r['sku'],styles,post['post'],json.dumps(r)))
    c.commit()
    with (md/'outbox.jsonl').open('a',encoding='utf-8') as out:
        out.write(json.dumps(post,ensure_ascii=False)+'\n')
    queued(md,'buyer',time.time())
    if injection:
        from forum_injections import record
        record(md,post['post'],injection,inv)
    c.close()
    return {'status':'queued','skus':[r['sku'] for r in selected],
            'recommendation_count':len(selected),'digest':digest}


def shop_analysis(md,enabled=True):
    from forum_value import enabled as value_enabled,publish
    if value_enabled(md):return publish(md,'shop',enabled)
    """Publish a shop update with the same verified ledger evidence as buyer posts."""
    from forum_schedule import due,queued
    from ledger_posts import attach
    from optimizer import atomic
    md=Path(md);now=time.time()
    if not due(md,'shop',now):return {'status':'staggered_interval'}
    if any(r.get('as')=='shop' and 'post' in r for r in lines(md/'outbox.jsonl')):
        return {'status':'pending'}
    inv=read(md/'inventory.json',{});catalog=read(md/'catalog.json',{})
    stock=Counter(u['sku'] for u in inv.get('units',[]) if u.get('grade')!='expired' and (u.get('expires_at') or math.inf)>now)
    from ranking_strategy import promotion_values
    demand=promotion_values(inv,read(md/'strategy-market.json',{}))
    products=sorted(stock,key=lambda sku:(-demand.get(sku,0),-catalog.get(sku,{}).get('list_price',0)))[:3]
    body='[tomato&potato | 실제 재고·정산 현황]\n'+ '\n'.join(
        f"{sku} {catalog.get(sku,{}).get('name',sku)[:55]} | 재고 {stock[sku]}개" for sku in products)
    body+='\n표시 재고는 작성 시점 기준입니다. 실제 등급·가격·보증은 SKU 견적에서 확인할 수 있습니다.'
    post=attach(md,body,'shop',now=now)
    if post is None:return {'status':'awaiting_settlement_evidence'}
    if not enabled:return {'status':'preview',**post}
    with (md/'outbox.jsonl').open('a',encoding='utf-8') as out:
        out.write(json.dumps(post,ensure_ascii=False)+'\n')
    atomic(md/'shop-analysis-state.json',{'at':now,'body':post['post'],'refs':post['refs']})
    queued(md,'shop',now)
    return {'status':'queued','refs':post['refs']}


def ensure_worker(md):
    md=Path(md)
    heartbeat=md/'recommendation-heartbeat.json'
    if time.time()-read(heartbeat,{}).get('at',0)<180:
        return
    root=Path(__file__).resolve().parent
    local_python=root/'.venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    executable=str(local_python) if local_python.exists() else sys.executable
    log=(root/'logs/recommendation-worker.log').open('a',encoding='utf-8')
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    subprocess.Popen([executable,'-X','utf8','-u',str(root/'run_market.py'),'--recommendations-only'],
        cwd=root,stdout=log,stderr=log,creationflags=flags)
    log.close()
