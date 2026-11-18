"""Round 3 buyer: catalog SKUs, bounded offers, checkout certificate constraints."""
import argparse
import asyncio
import json
import math
import re
from pathlib import Path
import time
from protocol import A2AClient, receipt, task_state, task_text
from policy import read_price
from round3 import read
from buyer_negotiation import offer_for,record,low_offer_rejection
from buyer_compare import compare,quoted_warranty,price_ceiling,food_min_hours

_stock_cache={}


def candidates(shops,order,catalog,payments=(),memory=None):
    rows=[]
    acceptable=set(order.get('acceptable',[]))
    from shop_profit import ownership_tags
    own_tags=ownership_tags()
    from strategy_policy import active,ROOT,market_snapshot
    integrated=active(ROOT/'.market')
    market=market_snapshot(ROOT/'.market') if integrated else {}
    own_market=market.get('coverage',{})
    own_tags |= set(own_market.get('own_shop_urls',[])) | set(own_market.get('own_shop_names',[]))
    for shop in shops:
        if shop.get('url') in own_tags or shop.get('name') in own_tags:continue
        own=[p for p in payments if p.get('shop')==shop['url']]
        reliability=(1+sum(p.get('paid') is True for p in own))/(2+len(own))
        for offer in shop.get('offers',[]):
            sku=offer['sku']
            if sku not in acceptable or offer.get('qty',0)<1 or sku not in catalog:
                continue
            value=catalog[sku]['list_price']
            # Positive surplus if non-food survives; actual failure rates are unknown.
            ceiling=math.floor(order['budget']) if integrated else min(order['budget'],math.floor(value*.85))
            if ceiling>0:
                history=[p['price'] for p in own if p.get('paid') and p.get('item')==sku and isinstance(p.get('price'),(int,float))][-20:]
                from statistics import median
                prices=list(market.get('skus',{}).get(sku,{}).get('prices',{}).values())
                n=sum(p.get('n',0) for p in prices)
                market_price=sum(p.get('median',value*.7)*p.get('n',0) for p in prices)/n if n else value*.7
                expected=median(history) if history else market_price
                # A recent actual quote is a better clearance lead than a
                # lifetime average. Public ad claims never supply this price.
                quotes=(memory or {}).get(shop['url'],{}).get('quotes',[])
                quote=next((q for q in reversed(quotes) if q.get('sku')==sku
                            and isinstance(q.get('ask'),(int,float)) and not isinstance(q.get('ask'),bool)
                            and math.isfinite(q['ask']) and q['ask']>=0
                            and 0<=time.time()-q.get('at',0)<=300),None)
                if quote is not None:expected=quote['ask']
                # Beta-smoothed actual checkout success, not public star claims.
                from patch36 import saved_objective,outcome
                q=quote or {}
                share=outcome(value,expected,sku,q.get('grade'),catalog[sku].get('kind','goods'),q.get('warranty_hours',0),shop['url'])
                score=saved_objective(share,reliability)
                if expected>ceiling:score*=min(1,ceiling/max(1,expected))
                rows.append((score,shop,sku,ceiling))
    return sorted(rows,key=lambda r:(-r[0],r[1]['url'],r[2]))


def observed_frontier(rows,order,catalog,payments,memory):
    """A fresh obtainable quote is the fallback frontier, not a fixed score."""
    from patch36 import outcome,saved_objective
    choices=[];now=time.time()
    for _,shop,sku,_ in rows:
        audit=memory.get(shop['url'],{})
        if audit.get('avoid_until',0)>now or audit.get('stock_cooldowns',{}).get(sku,{}).get('until',0)>now:continue
        q=next((q for q in reversed(audit.get('quotes',[])) if q.get('sku')==sku and 0<=now-q.get('at',0)<30
                and isinstance(q.get('ask'),(int,float)) and not isinstance(q.get('ask'),bool)
                and math.isfinite(q['ask']) and 0<q['ask']<=order['budget']),None)
        if q is None:continue
        if order.get('kind')=='food' and food_min_hours(order)>q.get('hours_left',0):continue
        history=[p for p in payments if p.get('shop')==shop['url']]
        reliability=(1+sum(p.get('paid') is True for p in history))/(2+len(history))
        c=catalog[sku];quality=outcome(c['list_price'],q['ask'],sku,q.get('grade'),c.get('kind','goods'),q.get('warranty_hours',0),shop['url'])
        value=saved_objective(quality,reliability)
        if quality>0 and value>0:choices.append(dict(score=quality,value=value,shop=shop['url'],sku=sku))
    return max(choices,key=lambda q:q['value']) if choices else None


def candidate_passes(rows,order,catalog,payments,memory):
    """Try aggressive targets first; recover one verified positive-ALL lead."""
    for row in rows:
        yield row,False
    if order.get('expires',math.inf)<=time.time()+8:return
    from patch36 import outcome,saved_objective
    recovery=[]
    for _,shop,sku,ceiling in rows:
        audit=memory.get(shop['url'],{})
        if audit.get('avoid_until',0)>time.time():continue
        quotes=audit.get('quotes',[])
        q=next((q for q in reversed(quotes) if q.get('sku')==sku
                and 0<=time.time()-q.get('at',0)<300
                and isinstance(q.get('ask'),(int,float)) and not isinstance(q.get('ask'),bool)
                and math.isfinite(q['ask']) and 0<q['ask']<=order['budget']),None)
        if q is None:continue
        if order.get('kind')=='food' and q.get('hours_left') is not None and q['hours_left']<food_min_hours(order):continue
        history=[p for p in payments if p.get('shop')==shop['url']]
        reliability=(1+sum(p.get('paid') is True for p in history))/(2+len(history))
        c=catalog[sku]
        fallback_ceiling=price_ceiling(c['list_price'],order['budget'],q.get('warranty_hours',0),reliability,shop['url'],sku,q.get('grade'),c.get('kind','goods'),None)
        if fallback_ceiling<1:continue
        score=saved_objective(outcome(c['list_price'],q['ask'],sku,q.get('grade'),c.get('kind','goods'),q.get('warranty_hours',0),shop['url']),reliability)
        if score>0:recovery.append((score,dict(shop,_comparison_quote=q),sku,ceiling))
    if recovery:
        yield max(recovery,key=lambda r:r[0]),True


def terms_failure(rec,order,minimum_warranty=0):
    if rec.get('cert') is False:
        return 'seller explicitly disabled the required certificate'
    if minimum_warranty and (rec.get('warranty_hours') or 0)<minimum_warranty:
        return 'receipt omits the warranty required for the premium offer'
    return None


def continued_quote_terms(text,previous_grade=None,previous_warranty=0):
    match=re.search(r'\bgrade\s*[:=]?\s*([ABC])\b',text,re.I)
    withdrawn=bool(re.search(r'\bgrade\s*[:=]?\s*(?:unknown|unspecified|undisclosed|ungraded)\b',text,re.I))
    grade=match[1].upper() if match else None if withdrawn else previous_grade
    changed=previous_grade is not None and grade!=previous_grade
    none=bool(re.search(r'\b(?:no|without)\s+(?:a\s+)?warranty\b|\bwarranty\s*[:=]?\s*0\b|\b0[- ]?(?:hours?|h)\s+warranty\b|무보증|보증\s*없음',text,re.I))
    stated=quoted_warranty(text)
    warranty=0 if none else stated if stated>0 else 0 if changed else previous_warranty
    return grade,warranty


def stated_lifetime(text):
    m=re.search(r'(\d+(?:\.\d+)?)\s*(?:hours?|h)\s+(?:of\s+)?shelf life left',text,re.I)
    return float(m.group(1)) if m else None


def checkout_feedback(payments,memory,now):
    """Brief SKU-level cooldown after repeated, confirmed stale-stock failures.

    The persisted order ID prevents old failures from restarting the cooldown
    forever. Other products and other shops remain eligible.
    """
    groups={}
    for p in payments[-100:]:
        groups.setdefault((p.get('shop'),p.get('item')),[]).append(p)
    for (shop,sku),rows in groups.items():
        if not shop or not sku:continue
        audit=memory.setdefault(shop,{})
        cooldowns=audit.setdefault('stock_cooldowns',{})
        if rows[-1].get('paid') is True:
            cooldowns.pop(sku,None)
        elif rows[-1].get('paid') is False and any(w in rows[-1].get('reason','').lower() for w in ('not in stock','out of stock','currently unavailable')):
            last=rows[-1].get('order')
            if last and cooldowns.get(sku,{}).get('order')!=last:
                repeated=len(rows)>=2 and rows[-2].get('paid') is False and 'stock' in rows[-2].get('reason','').lower()
                cooldowns[sku]={'order':last,'until':now+(300 if repeated else 60),'reason':'confirmed checkout stock failure'}
                audit['quotes']=[q for q in audit.get('quotes',[]) if q.get('sku')!=sku]


async def refresh_stock(rows):
    """One bounded public refresh; keep existing candidates if the API fails."""
    if not rows:return rows
    from urllib.request import urlopen
    def fetch():
        with urlopen('https://market.ourolabs.ai/api/shops',timeout=2) as r:return json.load(r)['shops']
    if 0<=time.monotonic()-_stock_cache.get('at',-math.inf)<2:
        current=_stock_cache['current']
    else:
        try:shops=await asyncio.wait_for(asyncio.to_thread(fetch),timeout=2.5)
        except Exception:return rows
        current={s['url']:s for s in shops}
        _stock_cache.update(at=time.monotonic(),current=current)
    return [r for r in rows if r[1]['url'] not in current or current[r[1]['url']].get('stock',{}).get(r[2],0)>0]


def prioritize_clearance(rows,forum,now):
    leads=set()
    for p in forum:
        if p.get('role')!='shop' or not 0<=now-p.get('ts',0)<900:continue
        text=p.get('text','')
        if not any(w in text.lower() for w in ('clearance','discount','임박','마감','떨이','할인')):continue
        for sku in re.findall(r'\bG[0-9a-fA-F]{8}\b',text):leads.add((p.get('author'),sku))
    first=next((r for r in rows if (r[1].get('name'),r[2]) in leads),None)
    return [first]+[r for r in rows if r is not first] if first else rows


async def buy(shops,order,catalog,payments=(),memory=None):
    from strategy_policy import active as strategy_active,load as strategy_load,ROOT as strategy_root
    integrated=strategy_active(strategy_root/'.market')
    strategy=strategy_load() if integrated else {}
    if integrated and strategy.get('buyer_time_policy_enabled'):
        from buyer_time_policy import execute
        import sys
        from demand_strategy import record_order
        record_order(strategy_root/'.market',order)
        return await execute(sys.modules[__name__],shops,order,catalog,payments,{} if memory is None else memory,strategy,strategy_root/'.market')
    if integrated:strategy=dict(strategy,**strategy.get('legacy_buyer_policy',{}))
    if integrated:
        from demand_strategy import record_order
        record_order(strategy_root/'.market',order)
    memory={} if memory is None else memory
    checkout_feedback(list(payments),memory,time.time())
    from rating_policy import adjusted_stars
    result={'shop':None,'receipt':None,'stars':adjusted_stars(3),'review':'No eligible stock for acceptable SKUs.',
            'strategy_revision':'patch36-margin-share'}
    if integrated:result['strategy_revision']=strategy['version']
    accept={'cert':True}
    shelf=order.get('shelf_hours') or 24
    required=(food_min_hours(order) or None) if order.get('kind')=='food' else None
    if required is not None:
        accept['min_hours_left']=required
    result['accept']=accept
    rows=candidates(shops,order,catalog,payments,memory)
    rows=[r for r in rows if memory.get(r[1]['url'],{}).get('stock_cooldowns',{}).get(r[2],{}).get('until',0)<=time.time()]
    rows=await refresh_stock(rows)
    if not integrated:
        rows=prioritize_clearance(rows,read(Path(__file__).resolve().parent/'.market/forum.json',[]),time.time())
    from resale import purchase_intent
    resale_dir=Path(__file__).resolve().parent/'.market'
    from profit_strategy import settings
    rows=await compare(rows,order,catalog,memory,None if integrated else int(settings()['buyer_quote_comparisons']),payments=payments)
    frontier=observed_frontier(rows,order,catalog,payments,memory) if integrated else None
    seen=set()
    failures=[]
    passes=candidate_passes(rows,order,catalog,payments,memory) if integrated else ((row,False) for row in rows)
    for (_,shop,sku,ceiling),recovery in passes:
        if memory.get(shop['url'],{}).get('stock_cooldowns',{}).get(sku,{}).get('until',0)>time.time():
            continue
        candidate=(shop['url'],sku,recovery)
        if candidate in seen:
            continue
        if memory.get(shop['url'],{}).get('avoid_until',0)>time.time():
            continue
        if not await refresh_stock([(0,shop,sku,ceiling)]):
            failures.append(shop['name']+': fresh preflight out of stock')
            continue
        seen.add(candidate)
        if order.get('expires',math.inf)<=time.time()+5:
            break
        result['shop']=shop['url']
        task=None
        last=None
        initial_ask=None
        minimum_warranty=0
        bargain=purchase_intent(resale_dir,shop['url'],sku,time.time())
        accept={'cert':True}
        selected_grade=shop.get('_comparison_quote',{}).get('grade')
        known_grade=selected_grade
        known_warranty=(shop.get('_comparison_quote',{}).get('warranty_hours',0) or 0) if catalog[sku].get('kind','goods')=='goods' else 0
        minimum_warranty=min(6,known_warranty) if known_warranty>=3 else 0
        if selected_grade in ('A','B','C'):accept['grades']=[selected_grade]
        if required is not None:accept['min_hours_left']=required
        if bargain:
            accept['grades']=[bargain['observed_grade']]
        audit=memory.setdefault(shop['url'],{})
        recovery_trace=None
        if recovery:
            recovery_trace={'at':time.time(),'sku':sku}
            audit.setdefault('recovery_attempts',[]).append(recovery_trace)
            audit['recovery_attempts']=audit['recovery_attempts'][-20:]
        async with A2AClient(shop['url'],timeout=strategy.get('buyer_request_timeout',20)) as client:
            try:
                await client.discover()
                terms=' Require a market certificate. Confirm an available unreserved unit before agreeing; select another eligible unit if the first has sold.'
                if catalog[sku].get('kind')=='goods':
                    terms+=(f' Require grade {selected_grade} only, as quoted in the comparison.' if selected_grade in ('A','B','C') else ' Prefer grade A near acquisition cost. B/C must offer a better risk-adjusted share of list-minus-cost.')+' Include a six-hour warranty when offered. State true grade, total price and warranty; no optional fee without agreement.'
                if required is not None:
                    terms+=f' Clearance/near-expiry food is welcome. Select the cheapest unit lasting at least {required:g} hours at checkout.'
                elif order.get('kind')=='food':
                    terms+=' Clearance and near-expiry food are welcome. Quote the cheapest unit eligible for checkout; no additional minimum shelf life is requested.'
                task=await client.send(f'I want to buy {catalog[sku]["name"]} ({sku}). Quote the total price.'+terms)
                for turn in range(6):
                    if order.get('expires',math.inf)<=time.time()+3:
                        break
                    state=task_state(task)
                    if state=='TASK_STATE_COMPLETED':
                        try:
                            rec=receipt(task,sku,order['budget'],last)
                        except ValueError as exc:
                            audit.update(avoid_until=time.time()+3600,reason='receipt violation: '+str(exc))
                            raise
                        why=terms_failure(rec,order,minimum_warranty)
                        if why:
                            audit.update(avoid_until=time.time()+600,reason=why)
                            raise ValueError(why)
                        _stock_cache.clear()  # Keep the receipt-stage check fresh.
                        if order.get('expires',math.inf)-time.time()>4 and not await refresh_stock([(0,shop,sku,ceiling)]):
                            raise ValueError('stock disappeared before checkout; trying another eligible seller')
                        record(audit,'receipt_validated',initial_ask,last)
                        if recovery_trace is not None:recovery_trace.update(receipt_task=rec.get('task'),receipt_price=rec.get('price'))
                        result['accept']=dict(accept,**({'warranty_hours':minimum_warranty} if minimum_warranty else {}))
                        result.update(receipt=rec,review='Receipt fits authorized offer; kit verifies certificate, signature and checkout.')
                        return result
                    if state!='TASK_STATE_INPUT_REQUIRED':
                        failures.append(shop['name']+': '+state)
                        break
                    text=task_text(task)
                    lifetime=stated_lifetime(text)
                    if required is not None and lifetime is not None and lifetime<required:
                        failures.append(shop['name']+': stated shelf life below order requirement')
                        break
                    metadata=task.get('status',{}).get('message',{}).get('metadata',{})
                    ask=await read_price(text,role='ask',metadata=metadata,allow_model=False)
                    quoted_grade,warranty=continued_quote_terms(text,known_grade,known_warranty)
                    if catalog[sku].get('kind','goods')!='goods':warranty=0
                    if minimum_warranty and warranty<minimum_warranty:
                        audit['quotes']=[q for q in audit.get('quotes',[]) if q.get('sku')!=sku]
                        failures.append(shop['name']+': previously required warranty was withdrawn')
                        break
                    known_grade,known_warranty=quoted_grade,warranty
                    history=[p for p in payments if p.get('shop')==shop['url']]
                    reliability=(1+sum(p.get('paid') is True for p in history))/(2+len(history))
                    if selected_grade and quoted_grade and selected_grade!=quoted_grade:
                        failures.append(shop['name']+': comparison grade is no longer available')
                        break
                    if quoted_grade is not None:accept['grades']=[quoted_grade]
                    remaining=order.get('expires',math.inf)-time.time()
                    target_score=(strategy.get('buyer_minimum_share',.82) if len(seen)<=3 else strategy.get('buyer_recovery_share',.72)) if remaining>20 and not recovery else None
                    if frontier is not None and len(seen)>1 and target_score is not None:
                        target_score=min(target_score,max(0,frontier['score']-1e-6))
                        audit['observed_frontier_target']={'at':time.time(),'score':target_score,'sku':frontier['sku']}
                    if quoted_grade is None and target_score is not None:
                        from patch36 import outcome
                        attainable=outcome(catalog[sku]['list_price'],0,sku,None,catalog[sku].get('kind','goods'),warranty,shop['url'])
                        target_score=min(target_score,max(0,attainable-1e-6))
                    ceiling=price_ceiling(catalog[sku]['list_price'],order['budget'],warranty,reliability,shop['url'],sku,quoted_grade,catalog[sku].get('kind','goods'),target_score)
                    audit=memory.setdefault(shop['url'],{})
                    audit.setdefault('quotes',[]).append({'at':time.time(),'sku':sku,'ask':ask,'warranty_hours':warranty,'grade':quoted_grade})
                    if lifetime is not None:audit['quotes'][-1]['hours_left']=lifetime
                    audit['quotes']=audit['quotes'][-30:]
                    if ceiling<1:
                        failures.append(shop['name']+': no positive authorized price meets the score target')
                        audit['unattainable_score_target']=target_score
                        break
                    if ask is None or isinstance(ask,bool) or not math.isfinite(ask) or ask<0:
                        failures.append(shop['name']+': no usable quote')
                        break
                    if initial_ask is None:
                        initial_ask=ask
                        audit.setdefault('negotiation',{}).pop('paid_opening_ratio',None)
                        paid_prices=sorted([p['price'] for p in payments if p.get('shop')==shop['url']
                                           and p.get('item')==sku and p.get('grade')==quoted_grade and p.get('paid') is True
                                           and isinstance(p.get('price'),(int,float)) and p['price']>0][-20:])
                        from patch36 import settled_price
                        paid=settled_price(shop['url'],sku,quoted_grade)
                        if not paid_prices and paid is not None:paid_prices=[paid,paid]
                        # Actual checkout history is evidence; a receipt or a
                        # seller's claimed floor alone is not paid evidence.
                        if len(paid_prices)>=2 and ask>0:
                            audit.setdefault('negotiation',{})['paid_opening_ratio']=min(.95,max(.05,paid_prices[len(paid_prices)//4]/ask*.90))
                        elif ask>0:
                            from patch36 import comparison_price
                            benchmark=comparison_price(shop['url'],sku,quoted_grade,ask,warranty)
                            if benchmark<ask:
                                audit.setdefault('negotiation',{})['paid_opening_ratio']=min(.95,max(.05,benchmark/ask*.9))
                                audit['opening_reference_source']='public_same_grade_warranty_paid_prices_shrunk_to_quote'
                    # Anchor narrow concessions to the first quote, not a later
                    # seller increase. Even clearance stock follows bargaining.
                    from patch36 import target_price
                    audit['margin_target']=min(ceiling,target_price(catalog[sku]['list_price'],sku,quoted_grade,share=strategy.get('buyer_target_share',.95)))
                    offer=offer_for(audit,initial_ask,ceiling,turn,last)
                    if ask<=ceiling:
                        from patch36 import outcome
                        ready=outcome(catalog[sku]['list_price'],ask,sku,quoted_grade,catalog[sku].get('kind','goods'),warranty,shop['url'])
                        ready_target=min(strategy.get('buyer_ready_score',.85),target_score) if target_score is not None else strategy.get('buyer_ready_score',.85)
                        from patch36 import saved_objective
                        if ready>=ready_target and saved_objective(ready,reliability)>0:
                            # Bid the real quoted price; receipt, certificate,
                            # budget and one-payment guards still run below.
                            offer=math.floor(ask)
                            audit['fast_settle_expected_score']=ready
                    if offer==last:
                        failures.append(shop['name']+': seller refused price ceiling')
                        break
                    audit['last_offer']=offer
                    minimum_warranty=max(minimum_warranty,min(6,warranty) if warranty>=3 else 0)
                    last=offer
                    task=await client.send(str(offer),task['id'])
                # Inspect a completed final response before proceeding elsewhere.
                if task_state(task)=='TASK_STATE_COMPLETED':
                    rec=receipt(task,sku,order['budget'],last)
                    why=terms_failure(rec,order,minimum_warranty)
                    if not why:
                        _stock_cache.clear()
                        if order.get('expires',math.inf)-time.time()>4 and not await refresh_stock([(0,shop,sku,ceiling)]):
                            raise ValueError('stock disappeared before checkout; trying another eligible seller')
                        record(audit,'receipt_validated',initial_ask,last)
                        if recovery_trace is not None:recovery_trace.update(receipt_task=rec.get('task'),receipt_price=rec.get('price'))
                        result['accept']=dict(accept,**({'warranty_hours':minimum_warranty} if minimum_warranty else {}))
                        result.update(receipt=rec,review='Receipt fits authorized offer; awaiting kit checkout checks.')
                        return result
                    failures.append(shop['name']+': '+why)
                elif low_offer_rejection(task_state(task),task_text(task),initial_ask,last):
                    # The final bid's response has no next loop iteration.
                    record(audit,'low_offer_rejected',initial_ask,last,'Final bid explicitly rejected.')
            except Exception as exc:
                failures.append(shop['name']+': '+type(exc).__name__+': '+str(exc)[:180])
            finally:
                if task and task_state(task)=='TASK_STATE_INPUT_REQUIRED':
                    try:
                        await client.cancel(task['id'])
                    except Exception:
                        pass
            # Negotiation receipts do not move money: only the single returned
            # receipt reaches the kit's signed mandate and ACP payment path.
    result['review']='; '.join(failures[-4:]) if failures else result['review']
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--shops',type=Path,required=True)
    p.add_argument('--order',type=Path,required=True)
    p.add_argument('--item',required=True)
    p.add_argument('--budget',type=float,required=True)
    a=p.parse_args()
    order=read(a.order,{})
    order['budget']=min(order['budget'],a.budget)
    from round3 import lines,write
    memory_path=a.order.parent/'buyer-observations.json'
    memory=read(memory_path,{})
    out=asyncio.run(buy(read(a.shops,[]),order,read(a.order.parent/'catalog.json',{}),
                       lines(a.order.parent/'payments.jsonl'),memory))
    write(memory_path,memory)
    print(json.dumps(out,ensure_ascii=False))
