"""SKU-aware buyer: Terra chooses counter/accept/leave per turn (bounded-v2 fallback); kit owns checkout/payment."""
import argparse
import asyncio
import base64
import json
import math
import re
import uuid
import statistics
import time
import hashlib
import threading
import buyer_terra
from openai_harness import same_model
from evidence_memory import sync, summary, load, positive
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

HEADERS={'A2A-Version':'1.0'}
DECIDE=buyer_terra.terra_decide  # live default; decide=None is the deterministic bounded-v2-list replay
# Experimental bounds, not tuned optima. Kit allows ~300 s per order; GLOBAL_S leaves checkout reserve.
SHOP_CALLS=3; ORDER_CALLS=6; MAX_SHOPS=3; WANT_CANDIDATES=2
SHOP_S=65; GLOBAL_S=230; MIN_ATTEMPT_S=15; MODEL_CAP_S=20; MIN_MODEL_S=3; SEND_MARGIN_S=4; CANCEL_S=2


def policy(order):
    accept={'cert':True,'grades':['A','B']}
    if order.get('kind')=='food':
        accept['min_hours_left']=max(0,float(order.get('use_within_hours') or 0))
    else:
        # A/B certified goods allowed without warranty; don't demand an unavailable universal warranty.
        accept['warranty_hours']=0
    return accept


def known_ineligible(receipt,accept):
    """'cert'/'claim' if the seller's own unsigned receipt metadata already contradicts the policy, else None.
    Negative screening only: absent metadata stays unknown (the kit's quality guard is authoritative) and a
    claim of A/B is not proof of grade. The receipt is never modified."""
    if 'cert' in receipt and accept.get('cert') is True and receipt['cert'] is not True: return 'cert'
    if 'claim' in receipt and accept.get('grades') and receipt['claim'] not in accept['grades']: return 'claim'
    return None


def evidence_preference(memory,catalog=None):
    """Per-shop paid purchases with observed grade A/B, honest claim and warranty. Local-biased sample."""
    shops={}
    for key,purchase in (memory or {}).get('purchases',{}).items():
        if not isinstance(purchase,dict) or not purchase.get('shop'): continue
        shop=shops.setdefault(purchase['shop'].rstrip('/'),{'paid_ids':[],'quality_evidence_ids':[]})
        shop['paid_ids'].append(str(key))
        # Only observed facts count; missing grade/claim/warranty is unknown, not bad.
        if purchase.get('grade') in ('A','B') and purchase.get('claim_honest') is True and positive(purchase.get('warranty_hours')):
            shop['quality_evidence_ids'].append(str(key))
    return {'sample':'local-biased','shops':shops}


def candidates(shops,order,memory=None,catalog=None):
    acceptable=set(order.get('acceptable',[]))
    out=[]
    for shop in shops:
        if shop.get('name')=='fair-furniture': continue
        for offer in shop.get('offers',[]):
            if offer.get('sku') in acceptable and offer.get('qty',0)>0:
                out.append((shop,offer['sku']))
        if not acceptable and not order.get('kind'):
            out.append((shop,order['item']))  # R2 compatibility: no fake SKU inventory.
    history=summary(memory or {},catalog or {})['shops']
    evidence=evidence_preference(memory,catalog)['shops']
    def score(pair):
        h=history.get(pair[0].get('url','').rstrip('/'),{})
        good=len(evidence.get(pair[0].get('url','').rstrip('/'),{}).get('quality_evidence_ids',[]))
        paid=h.get('paid',0); refused=h.get('refused',0)
        # Bounded heuristics, not calibrated probabilities or a claim of merchant dishonesty.
        reliability=(paid-refused)/(paid+refused+4)
        ratios=h.get('ratios',[])
        price_advantage=(.8-max(.25,min(1.5,statistics.median(ratios))))*len(ratios)/(len(ratios)+4) if ratios else 0
        quality=good/(good+4)  # bounded in [0,1); no evidence scores 0, never negative
        value=.10*reliability+.15*price_advantage+.20*quality
        # Unverified reviews do not influence ranking, regardless of volume or injected content.
        return (-value,pair[0].get('name',''),pair[1])
    ranked=sorted(out,key=score)
    # Round-robin merchants in ranked order: every shop's best SKU before any shop's second SKU.
    groups={}
    for pair in ranked:
        groups.setdefault(pair[0].get('url','').rstrip('/') or pair[0].get('name',''),[]).append(pair)
    lanes=list(groups.values())
    return [lane[i] for i in range(max(map(len,lanes),default=0)) for lane in lanes if i<len(lane)]


def valid_receipt(receipt,pubkey,sku,task,budget,offer,order=None,shop_name=None,shop_url=None):
    """Normalized copy of the receipt if the kit would accept it (market_kit.py:736-747), else None."""
    try:
        price=receipt['price']
        # A2A carries numbers as doubles; the kit restores integer-valued floats to int BEFORE verifying.
        if type(price) is float and math.isfinite(price) and price.is_integer(): price=int(price)
        if type(price) is not int or not 0<price<=min(budget,offer): return None
        category=(order or {}).get('item')
        if receipt['item']!=sku and not (category and receipt['item']==category): return None
        if receipt['task']!=task: return None
        # Registry URL selects the authoritative key; that key authenticates the signed shop label.
        # Registry nickname and signed display name need not match (no such kit contract).
        if not isinstance(receipt.get('shop'),str) or not receipt['shop']: return None
        rec=dict(receipt,price=price)
        body={k:rec[k] for k in ('shop','item','price','task')}
        canon=json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
        Ed25519PublicKey.from_public_bytes(base64.b64decode(pubkey)).verify(base64.b64decode(receipt['sig_seller']),canon)
        return rec
    except (KeyError,ValueError,TypeError,InvalidSignature): return None


def _origin(url):
    """(scheme,host,effective port) for an absolute http(s) URL without userinfo, else None."""
    try:
        if not isinstance(url,str) or any(c.isspace() or c=='\\' for c in url): return None
        parts=urlsplit(url)
        if parts.scheme not in ('http','https') or not parts.hostname: return None
        if '@' in parts.netloc or parts.username is not None or parts.password is not None: return None
        return (parts.scheme,parts.hostname.lower().rstrip('.'),parts.port or (443 if parts.scheme=='https' else 80))
    except ValueError: return None


async def rpc_endpoint(client,shop_url):
    """Advertised JSON-RPC 1.0 URL from the shop's agent card, same origin only; None fails closed."""
    base=_origin(shop_url)
    if base is None: return None
    try:
        response=await client.get(shop_url.rstrip('/')+'/.well-known/agent-card.json',headers=HEADERS,follow_redirects=False)
        if response.status_code!=200: return None
        card=response.json()
    except (httpx.HTTPError,ValueError): return None
    if not isinstance(card,dict): return None
    urls=[i.get('url') for i in card.get('supportedInterfaces') or [] if isinstance(i,dict)
          and i.get('protocolBinding')=='JSONRPC' and str(i.get('protocolVersion'))=='1.0']
    # Legacy top-level url only when the card clearly declares JSON-RPC 1.0 for it.
    if card.get('url') and card.get('preferredTransport')=='JSONRPC' and str(card.get('protocolVersion'))=='1.0':
        urls.append(card['url'])
    for url in urls:
        if _origin(url)==base: return url
    return None


LIST_OPEN=.70  # first counter as a share of trusted list price; inside A/B seller cost bands (40-75% of list)


def list_price(catalog,sku):
    """Trusted local catalog.json list price for the requested SKU, else None. Never parsed from seller text."""
    item=(catalog or {}).get(sku) if isinstance(catalog,dict) else None
    price=item.get('list_price') if isinstance(item,dict) else None
    return price if type(price) in (int,float) and math.isfinite(price) and price>0 else None


def _v2_step(ask,last_ask,rung,cap,anchor,offer,counter_once=True):
    """bounded-v2-list deterministic next offer: (offer,rung,stop_reason). Unchanged rules from the v2 buyer."""
    if last_ask is None:
        # bounded-v1: exactly one 90% counter, never above budget.
        offer=min(max(1,math.floor(ask*9/10 if counter_once else ask)),cap)
        target=min(max(1,math.floor(anchor*LIST_OPEN)),cap) if anchor else None
        if target is not None and target<offer:
            # bounded-v2-list: open at the list target; the v1 opening stays the next rung.
            rung,offer=offer,target
    elif ask>last_ask:
        # Compared with the latest quote, not the first: 100->95->100 is a raise.
        return offer,rung,'raised_ask'
    elif rung is not None:
        offer=min(rung,math.floor(ask))  # step to the v1 opening, or meet a quote already below it
        rung=None
    elif ask<=cap:
        offer=math.floor(ask)  # meet an unchanged or lowered ask within budget
    # else: lowered but still over budget; repeat our previous offer, never raise it.
    return offer,rung,None


async def _bounded_call(decide,facts,timeout):
    """Run the synchronous backend in a daemon thread and stop waiting at `timeout`. The thread may outlive the wait
    (httpx timeouts are per phase), but a daemon thread never delays process exit past the kit deadline."""
    loop=asyncio.get_running_loop();future=loop.create_future()
    def work():
        try: out=decide(facts,timeout)
        except Exception as exc: out=exc
        def deliver():
            if not future.done(): future.set_result(out)
        try: loop.call_soon_threadsafe(deliver)
        except RuntimeError: pass  # loop already closed: result abandoned
    threading.Thread(target=work,daemon=True).start()
    return await asyncio.wait_for(future,timeout)


async def _model_step(decide,trace,calls,shop_calls,cap,ask,last_offer,remaining_messages,deadline,sku):
    """One validated Terra decision: (action,offer,entry), or (None,None,entry) meaning use the explicit fallback."""
    entry={'turn':len(trace['asks']),'source':'model','reason':None,'requested':buyer_terra.MODEL,'returned':None,
           'usage':None,'latency_s':None,'error':None,'action':None,'effective_offer':None,
           'facts':None,'timeout_s':None,'raw_action':None,'raw_offer':None,'validated':False}
    left=deadline-time.monotonic()-SEND_MARGIN_S
    if left<MIN_MODEL_S:
        entry.update(source='fallback',reason='time');return None,None,entry
    if calls['used']>=calls['max'] or shop_calls>=SHOP_CALLS:
        entry.update(source='fallback',reason='call_cap');return None,None,entry
    calls['used']+=1
    timeout=min(MODEL_CAP_S,left)
    facts=buyer_terra.facts(sku=sku,list_price=trace['list_price'],cap=cap,asks=trace['asks'],offers=trace['offers'],
                            remaining_messages=remaining_messages,remaining_s=deadline-time.monotonic(),
                            remaining_calls=min(SHOP_CALLS-shop_calls-1,calls['max']-calls['used']))
    entry.update(facts=facts,timeout_s=round(timeout,3))  # numeric input as sent; no seller text or signatures
    t0=time.monotonic()
    try: out=await _bounded_call(decide,facts,timeout)
    except asyncio.TimeoutError: out={'error':'timeout'}
    if isinstance(out,Exception): out={'error':type(out).__name__}
    if not isinstance(out,dict): out={'error':'backend_shape'}
    entry.update(returned=out.get('returned'),usage=out.get('usage'),error=out.get('error'),
                 latency_s=out.get('latency_s') if out.get('latency_s') is not None else round(time.monotonic()-t0,3))
    if out.get('error') or out.get('requested',buyer_terra.MODEL)!=buyer_terra.MODEL or not same_model(buyer_terra.MODEL,out.get('returned')):
        # No retry and never another (e.g. nano) model: the explicit v2 fallback decides instead.
        entry.update(source='fallback',reason='model_error:'+str(out.get('error') or 'model_mismatch'))
        return None,None,entry
    entry.update(_raw(out.get('text')))
    try: action,offer=buyer_terra.parse_decision(out.get('text') or '',cap=cap,ask=ask,last_offer=last_offer)
    except ValueError as exc:
        why=str(exc) if str(exc).isidentifier() else 'json'
        entry.update(source='fallback',reason='invalid:'+why)
        return None,None,entry
    entry.update(action=action,effective_offer=offer,validated=True)
    return action,offer,entry


def _raw(text):
    """Bounded view of the model's raw decision: action (<=16 chars) and offer (number, or its type name)."""
    try: data=json.loads(text or '')
    except ValueError: return {'raw_action':None,'raw_offer':'unparsed'}
    if not isinstance(data,dict): return {'raw_action':None,'raw_offer':type(data).__name__}
    action=data.get('action');offer=data.get('offer')
    ok=type(offer) in (int,float) and math.isfinite(offer)
    return {'raw_action':action[:16] if isinstance(action,str) else None,
            'raw_offer':offer if ok or offer is None else type(offer).__name__}


async def negotiate(client,shop,sku,budget,pubkey,order,rpc_url=None,counter_once=True,catalog=None,
                    decide=None,deadline=None,calls=None,shop_name=None,hard_deadline=None):
    # counter_once=False reproduces the pre-bounded-v1 opening (meet the ask up to budget) for historical
    # replay tests only; receipt guards (price<=last sent offer, budget, task pin, signature) are identical either way.
    # decide=None is the deterministic bounded-v2-list path; a backend makes the model choose each next action.
    url=rpc_url or shop['url'].rstrip('/')+'/'
    result={'shop':shop['url'],'receipt':None,'stars':3,'review':'No verified agreement.','accept':policy(order)}
    anchor=list_price(catalog,sku) if counter_once else None
    deadline=deadline if deadline is not None else time.monotonic()+SHOP_S
    calls=calls if calls is not None else {'used':0,'max':ORDER_CALLS}
    # IDs and numbers only: no seller text or signatures. offers[] holds acknowledged sends only.
    trace=result['negotiation']={'opening_ask':None,'list_price':anchor,'list_source':'catalog.json' if anchor else None,
                                 'offers':[],'asks':[],'calls':[],'ambiguous_offer':None,'reason':None}
    task_id=None; offer=None; finished=False; rung=None; pending=None
    shop_calls=0; mode='model' if decide is not None else 'v2'
    max_offers=SHOP_CALLS+1 if decide is not None else 3  # model mode: 3 model counters + one explicit close
    async def rpc(method,params):
        response=await client.post(url,headers=HEADERS,json={'jsonrpc':'2.0','id':str(uuid.uuid4()),'method':method,'params':params})
        response.raise_for_status()
        data=response.json()
        if 'error' in data: raise ValueError('A2A RPC error')
        return data['result'].get('task',data['result'])
    try:
        if not pubkey or type(budget) not in (int,float) or not math.isfinite(budget) or budget<1:
            trace['reason']='precondition'
            return result
        cap=int(budget); last_ask=None
        text=f'I want to buy {sku}. Please quote a price in credits.'
        while True:
            msg={'role':'ROLE_USER','messageId':str(uuid.uuid4()),'parts':[{'text':text}]}
            if task_id: msg['taskId']=task_id
            try: task=await rpc('SendMessage',{'message':msg})
            except httpx.HTTPError:
                # The seller may or may not have seen it: separate status, never counted as an acknowledged offer.
                trace['ambiguous_offer']=pending
                raise
            if pending is not None:
                offer=pending; trace['offers'].append(offer); pending=None  # immutable: receipt price<=this send
            if task_id and task.get('id')!=task_id:
                # Pinned to the original task: never rebind the receipt guard to a substituted id.
                result['review']='Shop changed the task id mid-negotiation; refused.'
                trace['reason']='task_changed'
                return result
            task_id=task['id']
            state=task.get('status',{}).get('state','')
            if state=='TASK_STATE_COMPLETED':
                finished=True
                for a in task.get('artifacts',[]):
                    for p in a.get('parts',[]):
                        rec=p.get('data')
                        rec=valid_receipt(rec,pubkey,sku,task_id,budget,offer,order,shop_name=shop_name,shop_url=shop['url']) if isinstance(rec,dict) and offer is not None else None
                        if rec:
                            result.update(receipt=rec,stars=3,review='Signed agreement; payment and delivered quality not yet checked.')
                            trace['reason']='signed_receipt'
                            return result
                result['review']='Invalid or unexpected signed receipt; refused.'
                trace['reason']='invalid_receipt'
                return result
            if state!='TASK_STATE_INPUT_REQUIRED':
                finished=True
                trace['reason']='seller_ended'
                return result
            reply=' '.join(p.get('text','') for p in task.get('status',{}).get('message',{}).get('parts',[]))
            # Prefer explicit asking/counteroffer amounts, never a SKU or digits inside the product name.
            match=re.search(r'(?:asking|counteroffer|price|quote)\s*[:=]?\s*([0-9][0-9,]*(?:\.[0-9]+)?)',reply,re.I)
            if not match: match=re.fullmatch(r'\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*[.!]?\s*',reply)
            if not match:
                trace['reason']='no_quote'
                return result
            ask=float(match.group(1).replace(',',''))
            if not math.isfinite(ask) or ask<=0:
                trace['reason']='no_quote'
                return result
            if trace['opening_ask'] is None: trace['opening_ask']=ask
            trace['asks'].append(int(ask) if ask.is_integer() else ask)
            exhausted=len(trace['offers'])>=max_offers
            action=None;entry=None
            if mode=='model' and not exhausted:
                action,nxt,entry=await _model_step(decide,trace,calls,shop_calls,cap,ask,offer,max_offers-len(trace['offers']),deadline,sku)
                trace['calls'].append(entry)
                if entry['source']=='model' or entry['reason'].startswith(('model_error','invalid')): shop_calls+=1
                if action=='leave':
                    trace['reason']='model_leave'
                    return result
                if action is None and shop_calls and entry['reason'] in ('call_cap','time'):
                    # Exhausted after real model turns: explicit close, meet a non-raised ask within budget.
                    entry['source']='close_exhausted'
                    if (last_ask is not None and ask>last_ask) or ask>cap:
                        trace['reason']='exhausted_no_close'
                        return result
                    action,nxt='accept',math.floor(ask)
                elif action is None:
                    mode='fallback'  # explicit bounded-v2 for the rest of this shop; no retry, no other model
                    entry['source']='fallback:v2'
                if action is not None: entry['effective_offer']=nxt
            if action is None:
                nxt,rung,stop=_v2_step(ask,last_ask,rung,cap,anchor,offer,counter_once)
                if stop:
                    result['review']='Seller raised its quote; not chased, no deal.'
                    trace['reason']=stop
                    return result
                if entry is not None: entry['effective_offer']=nxt
            if exhausted:
                trace['reason']='turns_exhausted'
                return result
            last_ask=ask
            pending=nxt
            text=str(nxt)
    except (httpx.HTTPError,ValueError,KeyError,TypeError):
        result['review']='Shop unavailable or incompatible response; no deal.'
        trace['reason']='error'
    finally:
        # Only an owned, still-open negotiation is cancelled; a COMPLETED signed task is never cancelled here.
        # Bounded even when the caller's wait_for cancelled us: <=CANCEL_S and never past the order's hard deadline.
        left=CANCEL_S if hard_deadline is None else min(CANCEL_S,hard_deadline-time.monotonic())
        if task_id and not finished and left>0:
            try: await asyncio.wait_for(rpc('CancelTask',{'id':task_id}),left)
            except Exception: pass
    return result


def select(found,catalog):
    """(index,rule) of the one receipt to hand to the kit. Ties keep the earlier candidate."""
    if len(found)==1: return 0,'single_candidate'
    if len({sku for _,sku in found})==1:
        return min(range(len(found)),key=lambda i:(found[i][0]['receipt']['price'],i)),'same_sku_min_price'
    lists=[list_price(catalog,sku) for _,sku in found]
    if None in lists: return 0,'first_valid_no_list'
    # price/list is an explicit proxy for savings; the official Saved metric and seller cost are unknown here.
    return min(range(len(found)),key=lambda i:(found[i][0]['receipt']['price']/lists[i],i)),'list_ratio_proxy'


def policy_digest():
    spec={'SHOP_CALLS':SHOP_CALLS,'ORDER_CALLS':ORDER_CALLS,'MAX_SHOPS':MAX_SHOPS,'WANT_CANDIDATES':WANT_CANDIDATES,
          'SHOP_S':SHOP_S,'GLOBAL_S':GLOBAL_S,'MIN_ATTEMPT_S':MIN_ATTEMPT_S,'MODEL_CAP_S':MODEL_CAP_S,'LIST_OPEN':LIST_OPEN,
          'model':buyer_terra.MODEL,'system':buyer_terra.SYSTEM}
    return hashlib.sha256(json.dumps(spec,sort_keys=True).encode()).hexdigest()


async def run(args):
    started=time.monotonic()
    decide=getattr(args,'decide',DECIDE)
    order=json.loads(Path(args.order).read_text(encoding='utf-8')) if args.order else {'item':args.item}
    order.setdefault('item',args.item)
    state=Path(args.order).resolve().parent if args.order else Path('.market')
    try:
        memory=sync(state,order=order)
        catalog=load(state/'catalog.json',{})
    except (OSError,ValueError,TypeError,KeyError):
        memory={}; catalog={}  # Memory unavailable: keep original hard guards; no speculative history.
    shops=json.loads(Path(args.shops).read_text(encoding='utf-8')) if args.shops else [{'url':args.url,'name':'direct','offers':[]}]
    result={'shop':None,'receipt':None,'stars':3,'review':'No matching stocked SKU.','accept':policy(order)}
    # Registry is authoritative for identity/public keys. Never trust a key supplied by shop text.
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            response=await client.get('https://market.ourolabs.ai/api/shops')
            response.raise_for_status(); registry=response.json()
            registry=registry if isinstance(registry,list) else registry.get('shops',[])
        except (httpx.HTTPError,ValueError,TypeError):
            journal(state,order,[],memory,result,None,catalog=catalog,decide=decide)
            return result
        keys={r['url'].rstrip('/'):r.get('pubkey') for r in registry if isinstance(r,dict) and r.get('url')}
        names={r['url'].rstrip('/'):r['name'] for r in registry if isinstance(r,dict) and r.get('url') and isinstance(r.get('name'),str)}
        ranked=candidates(shops,order,memory,catalog)[:MAX_SHOPS]
        chosen_sku=None;attempts=[];found=[];stop=None;calls={'used':0,'max':ORDER_CALLS}
        for shop,sku in ranked:
            if found and decide is None: break  # deterministic replay: first receipt wins, as before
            if len(found)>=WANT_CANDIDATES:
                stop='enough_candidates';break
            remaining=GLOBAL_S-(time.monotonic()-started)
            if remaining<MIN_ATTEMPT_S or remaining<=CANCEL_S:
                stop='checkout_reserve';break  # no new comparison inside the kit's checkout reserve
            # Total runtime bounded to fit per-order kit deadline, including unresponsive shops and the
            # CancelTask cleanup a timed-out attempt still runs (<=CANCEL_S, inside the global deadline).
            budget_s=min(SHOP_S,remaining-CANCEL_S)
            async def attempt(shop=shop,sku=sku,deadline=time.monotonic()+budget_s):
                rpc_url=await rpc_endpoint(client,shop['url'])
                if rpc_url is None:
                    # No hidden root fallback: missing/bad/off-origin card means no deal with this shop.
                    return {'shop':shop['url'],'receipt':None,'stars':3,'review':'No valid same-origin JSON-RPC interface; no deal.','accept':policy(order)}
                return await negotiate(client,shop,sku,args.budget,keys.get(shop['url'].rstrip('/')),order,rpc_url=rpc_url,catalog=catalog,
                                       decide=decide,deadline=deadline,calls=calls,shop_name=names.get(shop['url'].rstrip('/')),
                                       hard_deadline=started+GLOBAL_S)
            try:
                attempt_result=await asyncio.wait_for(attempt(),timeout=budget_s)
            except asyncio.TimeoutError:
                attempts.append({'shop':shop['url'].rstrip('/'),'sku':sku,'reason':'timeout'})
                continue  # an earlier valid receipt is kept
            # Evidence goes to the journal only; kit-facing output keeps its pre-v2 shape.
            trace=attempt_result.pop('negotiation',None) or {}
            attempts.append({'shop':shop['url'].rstrip('/'),'sku':sku,'reason':trace.get('reason') or ('no_rpc' if not trace else None),
                             **{k:trace.get(k) for k in ('opening_ask','list_price','list_source','offers','asks','calls','ambiguous_offer')}})
            bad=decide is not None and attempt_result['receipt'] and known_ineligible(attempt_result['receipt'],attempt_result['accept'])
            if bad:
                # Seller's own metadata already fails policy: never a comparison candidate (task stays COMPLETED, unpaid).
                attempts[-1].update(reason='known_ineligible',ineligible=bad)
                attempt_result=dict(attempt_result,receipt=None,review='Signed terms already fail policy ('+bad+'); refused.')
            if attempt_result['receipt']: found.append((attempt_result,sku))
            elif not found: result=attempt_result
        comparison=None
        if found:
            index,rule=select(found,catalog)
            result,chosen_sku=found[index]
            # Unselected receipts belong to COMPLETED seller tasks; they are left unpaid, never cancelled or checked out.
            comparison={'rule':rule,'selected':index,'candidates':[
                {'shop':r['shop'].rstrip('/'),'sku':s,'task':r['receipt']['task'],'price':r['receipt']['price'],
                 'list_price':list_price(catalog,s)} for r,s in found]}
    journal(state,order,ranked,memory,result,chosen_sku,attempts=attempts,catalog=catalog,decide=decide,
            comparison=comparison,stop=stop,calls=calls)
    return result


def _source_sha256():
    try: return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    except OSError: return None


def _terra_sha256():
    try: return hashlib.sha256(Path(buyer_terra.__file__).read_bytes()).hexdigest()
    except OSError: return None


def journal(state,order,ranked,memory,result,sku,attempts=(),catalog=None,decide=None,comparison=None,stop=None,calls=None):
    """Append one decision row under the market state dir. IDs and outcome only: no reviews, model text or signatures."""
    source_order=list(dict.fromkeys(shop.get('url','').rstrip('/') for shop,_ in ranked))
    evidence=evidence_preference(memory)['shops']
    receipt=result.get('receipt') or {}
    try: ratios=summary(memory or {},catalog or {})['shops']
    except (TypeError,ValueError,KeyError,ZeroDivisionError): ratios={}
    shop_ratios={}
    for url in source_order:
        seen=ratios.get(url,{}).get('ratios',[])
        shop_ratios[url]={'n':len(seen),'median':round(statistics.median(seen),4) if seen else None}
    row={'ts':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'policy_version':'bounded-v2-list','sample':'local-biased',
         'source_sha256':_source_sha256(),'attempts':list(attempts),'shop_ratios':shop_ratios,
         'order':order.get('order'),'source_order':source_order,
         'memory_ids':sorted({i for url in source_order for i in evidence.get(url,{}).get('paid_ids',[])}),
         'quality_evidence_ids':sorted({i for url in source_order for i in evidence.get(url,{}).get('quality_evidence_ids',[])}),
         'decision':'accepted' if receipt else 'no_deal','shop':(result.get('shop') or '').rstrip('/') or None,
         'sku':sku,'task':receipt.get('task'),'price':receipt.get('price')}
    if decide is not None:
        row.update(policy_version='terra-multiquote-v1',policy_digest=policy_digest(),terra_sha256=_terra_sha256(),
                   model=buyer_terra.MODEL,model_calls=(calls or {}).get('used'),comparison=comparison,stop=stop,
                   source='model' if any(c.get('source')=='model' for a in attempts for c in a.get('calls') or []) else 'fallback')
    try:
        Path(state).mkdir(parents=True,exist_ok=True)
        with open(Path(state)/'buyer-decisions.jsonl','a',encoding='utf-8') as stream:
            stream.write(json.dumps(row,ensure_ascii=False,sort_keys=True)+'\n')
    except OSError: pass  # journal is evidence, never a reason to fail the purchase

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--shops');ap.add_argument('--url');ap.add_argument('--order');ap.add_argument('--item',required=True);ap.add_argument('--budget',required=True,type=float)
    try: result=asyncio.run(run(ap.parse_args()))
    except Exception: result={'shop':None,'receipt':None,'stars':3,'review':'Buyer failed closed.'}
    print(json.dumps(result,ensure_ascii=False))
