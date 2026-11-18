"""Bounded quotes ranked by expected saved share, with checkout reliability."""
import asyncio
import math
import re
import time
from pathlib import Path
from protocol import A2AClient,task_state,task_text
from policy import read_price


def food_min_hours(order):
    # Only the order's explicit use deadline; no agent-added safety margin.
    return max(0,float(order.get('use_within_hours') or 0))


def price_ceiling(value,budget,warranty,reliability,url,sku=None,grade=None,kind='goods',minimum_score=None):
    from patch36 import ceiling
    return ceiling(value,budget,warranty,reliability,url,sku,grade,kind,minimum_score=minimum_score)


def quoted_warranty(text):
    days=re.search(r'(\d+(?:\.\d+)?)\s*[- ]?days?\s+(?:full.refund\s+)?warranty|warranty\s*(?:of|:|=)?\s*(\d+(?:\.\d+)?)\s*days?',text,re.I)
    if days:return min(72,24*float(days[1] or days[2]))
    for word,days_count in [('one',1),('two',2),('three',3)]:
        if re.search(r'\b'+word+r'[- ]day\s+(?:full.refund\s+)?warranty',text,re.I):return days_count*24
    patterns=[r'(\d+(?:\.\d+)?)\s*[- ]?\s*(?:hours?|h)\s+(?:full.refund\s+)?warranty',
              r'warranty(?:\s+of)?\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(?:hours?|h)',r'보증\s*[:=]?\s*(\d+(?:\.\d+)?)\s*시간',r'(\d+(?:\.\d+)?)\s*시간\s*보증']
    for pattern in patterns:
        match=re.search(pattern,text,re.I)
        if match:
            return min(72,float(match[1]))
    for word,hours in [('three',3),('six',6),('twelve',12),('twenty-four',24),('forty-eight',48),('seventy-two',72)]:
        if re.search(word+r'[- ]hour\s+warranty',text,re.I):
            return hours
    return 0


async def compare(rows,order,catalog,memory,limit=3,payments=()):
    from strategy_policy import load
    policy=load();time_mode=bool(policy.get('buyer_time_policy_enabled'))
    if time_mode and limit is None:rows=rows[:int(policy['buyer_quote_parallelism'])*2]
    if len(rows)<2 or order.get('expires',math.inf)-time.time()<12:
        return rows
    choices=[]
    for row in rows:
        if (row[1]['url'],row[2]) not in {(r[1]['url'],r[2]) for r in choices}:
            audit=memory.get(row[1]['url'],{})
            if audit.get('avoid_until',0)>time.time():
                continue
            choices.append(row)
        if limit is not None and len(choices)>=limit:
            break
    async def quote(row):
        started=time.monotonic()
        _,shop,sku,_=row
        audit=memory.setdefault(shop['url'],{})
        cached=next((q for q in reversed(audit.get('quotes',[])) if q.get('sku')==sku
                     and isinstance(q.get('ask'),(int,float)) and q['ask']>0
                     and 0<=time.time()-q.get('at',0)<(policy.get('buyer_quote_ttl_seconds',6) if time_mode else 30)),None)
        if cached:
            return shop['url'],sku,cached
        task=None
        try:
            async with A2AClient(shop['url'],timeout=4) as client:
                try:
                    await client.discover()
                    terms=' Prefer grade A close to acquisition cost; include a six-hour warranty if offered. Compare B/C only at a materially better margin-share price. Disclose true grade and total fees.' if catalog[sku].get('kind')=='goods' else ''
                    if order.get('kind')=='food':
                        terms+=' Clearance and near-expiry stock are welcome; quote the cheapest unit eligible for checkout.'
                        if food_min_hours(order)>0:terms+=f' The order requires {food_min_hours(order):g} hours of usable life.'
                    task=await client.send(f"Quote only, no purchase authorization: {catalog[sku]['name']} ({sku}). State price, grade and warranty hours."+terms)
                    if task_state(task)!='TASK_STATE_INPUT_REQUIRED':
                        return None
                    text=task_text(task)
                    ask=await read_price(text,role='ask',metadata=task.get('status',{}).get('message',{}).get('metadata',{}),allow_model=False)
                    if ask is None or isinstance(ask,bool) or not math.isfinite(ask) or ask<=0:
                        return None
                    grade=re.search(r'\bgrade\s*[:=]?\s*([ABC])\b',text,re.I)
                    result={'sku':sku,'ask':ask,'at':time.time(),'warranty_hours':quoted_warranty(text),'preflight':True,
                            'grade':grade[1].upper() if grade else None,'response_seconds':max(.05,time.monotonic()-started)}
                    life=re.search(r'(\d+(?:\.\d+)?)\s*(?:hours?|h)\s+(?:of\s+)?shelf life left',text,re.I)
                    if life:result['hours_left']=float(life[1])
                    if catalog[sku].get('kind')=='goods' and result['grade'] in ('B','C'):
                        from patch36 import outcome,comparison_price
                        def expected_score(q):
                            price=comparison_price(shop['url'],sku,q.get('grade'),q['ask'],q.get('warranty_hours',0))
                            return outcome(catalog[sku]['list_price'],price,sku,q.get('grade'),'goods',q.get('warranty_hours',0),shop['url'])
                        if expected_score(result)<.85 and time.monotonic()-started<4:
                            # "Prefer A" is ignored by some deterministic
                            # sellers. Try an explicit A quote before settling
                            # for B/C; keep the original quote as fallback.
                            try:
                                await asyncio.wait_for(client.cancel(task['id']),timeout=.5)
                                task=None
                                remaining=max(.1,min(2,5.5-time.monotonic()+started))
                                task=await asyncio.wait_for(client.send(f"Quote only, no purchase authorization: {catalog[sku]['name']} ({sku}). Only grade A; do not substitute B/C. Decline if unavailable. State total price, grade and warranty hours."),timeout=remaining)
                                if task_state(task)=='TASK_STATE_INPUT_REQUIRED':
                                    alternative=task_text(task)
                                    ag=set(g.upper() for g in re.findall(r'\bgrade\s*[:=]?\s*([ABC])\b',alternative,re.I))
                                    ap=await read_price(alternative,role='ask',allow_model=False)
                                    if ag=={'A'} and isinstance(ap,(int,float)) and not isinstance(ap,bool) and math.isfinite(ap) and ap>0:
                                        q=dict(sku=sku,ask=ap,at=time.time(),grade='A',warranty_hours=quoted_warranty(alternative),preflight=True,response_seconds=max(.05,time.monotonic()-started))
                                        if expected_score(q)>expected_score(result):result=q
                                result['strict_A_checked']=True
                            except Exception:
                                result['strict_A_checked']=True
                    audit.setdefault('quotes',[]).append(result)
                    audit['quotes']=audit['quotes'][-30:]
                    return shop['url'],sku,result
                finally:
                    if task and task_state(task)=='TASK_STATE_INPUT_REQUIRED':
                        try:
                            await asyncio.wait_for(client.cancel(task['id']),timeout=1)
                        except Exception:
                            pass
        except Exception:
            return None
    async def bounded(row):
        try:
            return await asyncio.wait_for(quote(row),timeout=7)
        except asyncio.TimeoutError:
            return None
    if limit is None:
        from strategy_policy import load
        policy=load();semaphore=asyncio.Semaphore(int(policy['buyer_quote_parallelism']))
        async def queued(row):
            async with semaphore:return await bounded(row)
        search_started=time.time()
        tasks=[asyncio.create_task(queued(r)) for r in choices]
        pending=set(tasks);done=set()
        initial_best=max((r[0] for r in rows),default=-math.inf)
        deadline=time.monotonic()+min(policy.get('buyer_search_seconds',20),max(0,order.get('expires',math.inf)-time.time()-8))
        while pending and time.monotonic()<deadline:
            finished,pending=await asyncio.wait(pending,timeout=max(0,deadline-time.monotonic()),return_when=asyncio.FIRST_COMPLETED)
            done.update(finished);ready=False
            for task in finished:
                if task.cancelled() or task.exception() is not None:continue
                v=task.result()
                if not v:continue
                url,sku,q=v
                if q.get('at',0)<search_started or q['ask']>order['budget']:continue
                if order.get('kind')=='food' and food_min_hours(order)>q.get('hours_left',0):continue
                from patch36 import outcome,saved_objective
                actual=outcome(catalog[sku]['list_price'],q['ask'],sku,q.get('grade'),catalog[sku].get('kind','goods'),q.get('warranty_hours',0),url)
                history=[p for p in payments if p.get('shop')==url]
                reliability=(1+sum(p.get('paid') is True for p in history))/(2+len(history))
                if time_mode:
                    from buyer_time_policy import search_ready,opportunity
                    row=next(r for r in choices if r[1]['url']==url and r[2]==sku)
                    ready=search_ready(row,q,order,catalog,payments,opportunity(Path(__file__).resolve().parent/'.market'),time.time()-search_started,deadline-time.monotonic())
                    if ready:break
                elif actual>=policy.get('buyer_search_ready_score',1) and saved_objective(actual,reliability)>=initial_best:
                    ready=True;break
            if ready:break
        for task in pending:task.cancel()
        await asyncio.gather(*pending,return_exceptions=True)
        results=[t.result() for t in done if not t.cancelled() and t.exception() is None]
    else:results=await asyncio.gather(*(bounded(r) for r in choices))
    quotes={ (v[0],v[1]):v[2] for v in results if v}
    ranked=[]
    for score,shop,sku,ceiling in rows:
        quote=quotes.get((shop['url'],sku))
        if quote:
            if order.get('kind')=='food' and quote.get('hours_left',math.inf)<food_min_hours(order):
                continue
            value=catalog[sku]['list_price']
            history=[p for p in payments if p.get('shop')==shop['url']]
            reliability=(1+sum(p.get('paid') is True for p in history))/(2+len(history))
            from statistics import median
            paid_prices=[p['price'] for p in history if p.get('paid') is True and p.get('item')==sku
                         and p.get('grade')==quote.get('grade') and p.get('grade') is not None
                         and isinstance(p.get('price'),(int,float)) and p['price']>0][-20:]
            expected=min(quote['ask'],median(paid_prices)) if len(paid_prices)>=2 else quote['ask']
            from patch36 import comparison_price
            expected=min(expected,comparison_price(shop['url'],sku,quote.get('grade'),quote['ask'],quote.get('warranty_hours',0)))
            ceiling=order['budget'] if time_mode else price_ceiling(value,order['budget'],quote.get('warranty_hours',0),reliability,shop['url'],sku,quote.get('grade'),catalog[sku].get('kind','goods'))
            # A very cheap quote that cannot settle or exceeds our ceiling is
            # not equivalent to obtainable surplus.
            attainable=expected<=ceiling
            from patch36 import outcome,saved_objective
            expected_score=outcome(value,expected,sku,quote.get('grade'),catalog[sku].get('kind','goods'),
                                   quote.get('warranty_hours',0),shop['url'])
            settlement_probability=min(1,max(.1,reliability))
            if not attainable:settlement_probability*=min(1,ceiling/max(1,expected))
            score=saved_objective(expected_score,settlement_probability)
        # An unquoted candidate retains its estimated ALL contribution. Never
        # subtract raw 0.02/0.10 points from micro-scale modeled ALL gains.
        if quote:shop=dict(shop,_comparison_quote=quote)
        ranked.append((score,shop,sku,ceiling))
    return sorted(ranked,key=lambda r:(-r[0],r[1]['url'],r[2]))
