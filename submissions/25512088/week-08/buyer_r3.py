"""Round-3 buyer: exact SKUs, bounded comparison, persistent context, kit-owned payment.

Numerical decisions are deterministic. Seller text is data, never instructions.
No direct payments or forum posts here. One receipt at most is returned per order.
"""
from concurrent.futures import ThreadPoolExecutor
import argparse
import base64
import json
import math
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
import uuid
import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from buyer_memory import Memory
from market_intel import Intel
from adaptive_control import Control


def task_state(task):
    return task.get('status', {}).get('state', '').replace('-','_').upper()


def text_of(task):
    return ' '.join(p.get('text','') for p in task.get('status',{}).get('message',{}).get('parts',[]))


def asking(text):
    # Never interpret a SKU, quantity, expiry, or discount percent as a quote.
    hits = re.findall(r'\b(?:asking price|asking|ask|counteroffer|final price|offer price|price|견적|판매가|가격)\s*[:=]?\s*[₩$]?\s*([0-9][0-9,]*)\b',text,re.I)
    # Some human sellers use "12,000 KRW/credits/원" without a price label.
    # A currency suffix is sufficiently narrow; bare numbers remain rejected.
    hits += re.findall(r'\b([0-9][0-9,]*)\s*(?:KRW|credits?|원)\b',text,re.I)
    return int(hits[-1].replace(',','')) if hits else None


def grade_of(text):
    hit = re.search(r'\bgrade\s*[:=]?\s*([ABC])\b',text,re.I)
    return hit.group(1).upper() if hit else None


def quote_fields(task):
    """Seller data is untrusted; checkout certificates remain authoritative."""
    text=text_of(task); result={'price':asking(text),'grade':grade_of(text)}
    for key,pattern in [('hours',r'(\d+(?:\.\d+)?)\s*h(?:ours?)?\s+(?:of )?shelf life left'),
                        ('warranty',r'warranty\s*[:=]?\s*(\d+(?:\.\d+)?)\s*h')]:
        m=re.search(pattern,text,re.I);result[key]=float(m.group(1)) if m else None
    if result['hours'] is None:
        m=re.search(r'\blasts\s+(\d+(?:\.\d+)?)\s+(?:more\s+)?hours?\b',text,re.I)
        if m:result['hours']=float(m.group(1))
    parts=list(task.get('status',{}).get('message',{}).get('parts',[]))
    for artifact in task.get('artifacts',[]):parts+=artifact.get('parts',[])
    for part in parts:
        data=part.get('data')
        if not isinstance(data,dict):continue
        for dest,keys in [('price',('asking_price','ask','price')),('hours',('hours_left','shelf_life_hours')),('warranty',('warranty_hours',))]:
            for key in keys:
                n=data.get(key)
                if type(n) in (int,float) and math.isfinite(n) and n>=0:
                    if dest!='price' or n>0 and n==int(n):result[dest]=n
                    break
        g=data.get('grade',data.get('claim'))
        if g in ('A','B','C'):result['grade']=g
    return result


GRADE_PRICE_CAP = {'A':69, 'B':60, 'C':48}
GRADE_COST_MIDPOINT = {'A':.65, 'B':.50, 'C':.35}
SAVED_TARGET = .80
SAVED_STRETCH_TARGET = .95
SAVED_MIDDLE_TARGET = .90
EARLY_QUOTE_TARGET = 11
QUOTE_WAVE_SIZE = 24
FOOD_CHECKOUT_BUFFER_HOURS = .1


def condition_suitable(order, fields):
    """Require explicit conditions and enough time to survive checkout."""
    if order.get('kind')=='food':
        hours=fields.get('hours');required=order.get('use_within_hours',0)
        return (isinstance(hours,(int,float)) and not isinstance(hours,bool)
                and hours>=required+FOOD_CHECKOUT_BUFFER_HOURS)
    warranty=fields.get('warranty')
    return (isinstance(warranty,(int,float)) and not isinstance(warranty,bool)
            and warranty>=6)


def condition_missing(order, fields):
    """Distinguish an omitted condition from an explicitly unsafe one."""
    key='hours' if order.get('kind')=='food' else 'warranty'
    return fields.get(key) is None


def estimated_margin_saved(product, price, grade=None):
    """Patch 3.6 Saved proxy using the official grade cost distribution.

    Actual unit cost is hidden from buyers, so ranking uses the published
    midpoint while checkout remains bounded by a conservative price cap.
    """
    listed=product.get('list_price');cost_ratio=GRADE_COST_MIDPOINT.get(grade,.50)
    if (not isinstance(listed,(int,float)) or listed<=0 or
            not isinstance(price,(int,float)) or price<0):return -1.0
    margin=listed*(1-cost_ratio)
    return max(-1.0,min(1.0,(listed-price)/margin)) if margin>0 else -1.0


def limit(order, product, grade=None):
    # Patch 3.6 scores the share of shop margin saved, not list discount.
    # A/B already imply at least 0.80 expected margin saved at the published
    # cost midpoints.  C must stay at 48% of list to avoid mathematically
    # capping new Saved evidence below the current Top-10 band.
    pct = GRADE_PRICE_CAP.get(grade, 60)
    return min(order['budget'], math.floor(product['list_price']*pct/100))


def saved_target_limit(order, product, grade, target):
    listed=product.get('list_price');cost_ratio=GRADE_COST_MIDPOINT.get(grade,.50)
    if not isinstance(listed,(int,float)) or listed<=0:return limit(order,product,grade)
    ratio=1-target*(1-cost_ratio)
    return min(limit(order,product,grade),order['budget'],math.floor(listed*ratio))

def stretch_limit(order, product, grade=None):
    """First-offer ceiling targeting stronger Saved without losing fallback."""
    return saved_target_limit(order,product,grade,SAVED_STRETCH_TARGET)

def middle_limit(order, product, grade=None):
    """One evidence-gathering step between the stretch and proven fallback."""
    return saved_target_limit(order,product,grade,SAVED_MIDDLE_TARGET)


def deals_cap(order,product,grade,portfolio=None):
    """Never spend a historical list-discount buffer under Patch 3.6."""
    return limit(order,product,grade)


def own_shop_identity():
    base = Path(__file__).resolve().parent
    name = json.loads((base / 'referral_policy.json').read_text())['shop_name']
    key = serialization.load_pem_private_key((base / 'shop_key.pem').read_bytes(), password=None)
    pub = base64.b64encode(key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    return name, pub


def exclude_own_shop(shops, name, pubkey):
    # Name blocks the registered shop; key also blocks renamed shops/new tunnels.
    return [s for s in shops if s.get('name') != name and s.get('pubkey') != pubkey]


def execution_order(order):
    """Preserve the complete market order; seller holdings never constrain it."""
    return dict(order)


BREAKAGE_PRIORS = {
    # No authoritative market cohort is published.  These are deliberately
    # conservative ranking assumptions, not claims about measured failure.
    'food': {'probability': .05, 'basis': 'conservative-unmeasured'},
    'goods': {'probability': .15, 'basis': 'conservative-unmeasured'},
}


def expected_offer(order, product, price, checkout_probability=1, reliability=1, grade=None):
    """Comparable absolute surplus with explicitly uncertain breakage.

    Warranty refunds are not credited before a matured cohort exists.  This
    keeps the estimate conservative while the signed checkout certificate
    remains the authority for the actual conditions.
    """
    prior=BREAKAGE_PRIORS['food' if order.get('kind')=='food' else 'goods']
    value=product['list_price']*(1-prior['probability'])
    expected_surplus=value-price
    valid=max(0,min(1,checkout_probability))*max(0,min(1,reliability))
    return {'expected_surplus':expected_surplus,'valid_checkout_probability':valid,
            'estimated_margin_saved':estimated_margin_saved(product,price,grade),
            'breakage_probability':prior['probability'],'breakage_basis':prior['basis']}


def candidates(order, shops, catalog, memory, intel=None):
    """Enumerate every eligible shop/SKU pair, not one preferred SKU per shop."""
    accepted = set(order['acceptable'])
    ranked = []
    for shop in shops:
        options = [o for o in shop.get('offers',[]) if o.get('sku') in accepted and o.get('qty',0)>0
                   and memory.rival_ready(shop['url'],o['sku'])
                   and o['sku'] in catalog and catalog[o['sku']].get('list_price',0)>0
                   and (order.get('kind')!='food' or catalog[o['sku']].get('shelf_hours',float('inf'))>=order.get('use_within_hours',0))]
        if not options:
            continue
        reliability = intel.reliability(shop['url']) if intel else 1
        # Conversion evidence is shop-level.  Computing it once per SKU used
        # to rescan the growing event history hundreds of times per order.
        deal=memory.deal_estimate(shop['url'])
        for offer in options:
            product=catalog[offer['sku']]
            reference=memory.condition_reference(shop['url'],offer['sku'],order)
            # No paid evidence: explicit price prior, not a learned quote.
            estimate=reference or product['list_price']*(.75 if order.get('kind')=='food' else .60)
            cap=limit(order,product)
            # Above-cap prices may negotiate down, but must not look affordable
            # simply because we clipped the estimate to our budget.
            metrics=expected_offer(order,product,estimate,deal['probability'],reliability)
            ranked.append((estimate>cap,
                           -metrics['estimated_margin_saved']*metrics['valid_checkout_probability'],
                           -metrics['valid_checkout_probability'],-metrics['expected_surplus'],shop['name'],
                           offer['sku'],shop,offer))
    ranked.sort(key=lambda r:r[:5])
    return diversify_shop_candidates([(r[6],r[7]) for r in ranked])


def diversify_shop_candidates(pairs):
    """Keep every pair while leading each wave with distinct sellers.

    A shop can expose dozens of acceptable SKUs.  Sending the first wave to
    many SKUs from the same slow shop wastes the order deadline and yields
    little price diversity.  Round-robin preserves the original rank within
    each shop and across each shop's best candidate without discarding any
    SKU.
    """
    buckets={}
    for pair in pairs:
        shop=pair[0];key=shop.get('url') or shop.get('name')
        buckets.setdefault(key,[]).append(pair)
    ordered=[]
    while buckets:
        empty=[]
        for key,rows in buckets.items():
            ordered.append(rows.pop(0))
            if not rows:empty.append(key)
        for key in empty:del buckets[key]
    return ordered


def surplus_score(value, price, reliability=1):
    # Market value is list price when conditions hold. Reliability is a ranking
    # weight, NOT a calibrated probability; breakage rates are unpublished.
    return max(0,value-price)*max(0,min(1,reliability))


def quote_rank(session, catalog, intel=None, order=None):
    reliability=intel.reliability(session['shop']['url']) if intel else 1
    ask=session['ask']
    order=order or {'kind':'goods'}
    deal=session.get('deal_estimate',{'probability':1})
    metrics=expected_offer(order,catalog[session['sku']],ask,deal['probability'],reliability,
                           session.get('grade'))
    session['expected']=metrics
    return (ask>session['cap'],
            -metrics['estimated_margin_saved']*metrics['valid_checkout_probability'],
            -metrics['valid_checkout_probability'],-metrics['expected_surplus'],ask,
            session['shop']['name'],session['sku'])


def signed_receipt(task, shop, sku, maximum, task_id, failures=None):
    failures=[] if failures is None else failures
    for artifact in task.get('artifacts',[]):
        for part in artifact.get('parts',[]):
            r = part.get('data')
            if not isinstance(r,dict):
                continue
            price = r.get('price')
            if isinstance(price,bool) or not isinstance(price,(int,float)) or not math.isfinite(price) or price!=int(price):
                failures.append('amount_type'); continue
            # Identity is the market-registered signing key, not a display label.
            # Keep the signed label unchanged in the canonical body and receipt.
            if (r.get('item')!=sku or r.get('task')!=task_id or
                    not isinstance(r.get('shop'),str) or not r['shop'].strip() or
                    not 0<price<=maximum):
                failures.append('sku' if r.get('item')!=sku else 'task' if r.get('task')!=task_id else 'amount_limit' if not 0<price<=maximum else 'display_name'); continue
            try:
                # SDK Struct can turn integer amounts into floats; market signs integers.
                normalized = dict(r, price=int(price))
                body = {k:normalized[k] for k in ('shop','item','price','task')}
                Ed25519PublicKey.from_public_bytes(base64.b64decode(shop['pubkey'])).verify(
                    base64.b64decode(r['sig_seller']),json.dumps(body,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode())
                return normalized
            except (ValueError, KeyError, InvalidSignature, TypeError):
                failures.append('signature_or_key'); continue
    return None


class Buyer:
    def __init__(self, order, memory, client, catalog, intel=None, control=None, portfolio=None):
        self.o, self.m, self.c, self.catalog = order, memory, client, catalog
        self.sessions = []
        self.intel = intel
        self.control = control
        self.portfolio = portfolio if portfolio is not None else memory.saved_portfolio(catalog)
        self.deadline = min(time.time()+85, order['expires']-3)

    def result(self, session=None, receipt=None, reason='No acceptable deal'):
        out = dict(shop=session['shop']['url'] if session else '',receipt=receipt,stars=3,review=reason)
        if receipt:
            out['accept'] = {'cert':True, 'grades':[session['grade']] if session.get('grade') else ['A','B','C']}
            if self.o.get('kind')=='food':
                out['accept']['min_hours_left'] = self.o['use_within_hours']
            else:
                out['accept']['warranty_hours'] = 6
        return out

    def send(self, s, text):
        if time.time()>=self.deadline:
            raise TimeoutError('Order deadline')
        message = {'role':'ROLE_USER','messageId':str(uuid.uuid4()),'parts':[{'text':text}]}
        if s.get('task'):
            message['taskId']=s['task']
        r=self.c.post(s['rpc'],headers={'A2A-Version':'1.0'},json={
            'jsonrpc':'2.0','id':str(uuid.uuid4()),'method':'SendMessage','params':{'message':message}},
            timeout=max(.1,min(8,self.deadline-time.time())))
        r.raise_for_status()
        body=r.json()
        if 'error' in body:
            raise ValueError('Seller RPC error')
        task=body['result'].get('task',body['result'])
        s['task']=task.get('id',s.get('task'))
        s['last']=task
        content=text_of(task)
        key=re.search(r'Private provisional member key:\s*([A-Za-z0-9_-]+)',content)
        if key:
            self.m.remember_key(s['shop']['url'],key.group(1))
            content=content.replace(key.group(1),'[private key stored separately]')
        self.m.record(self.o['order'],s['shop']['url'],s['sku'],s.get('task'),'response',
                      asking(content),{'state':task_state(task),'text':content[:4000]})
        return task

    def send_offer(self, session, amount):
        """Submit one idempotent offer and preserve unknown outcomes."""
        if self.control is None:
            return self.send(session,str(amount))
        key=':'.join(map(str,('buyer-offer-v1',self.o['order'],session['shop']['url'],
                             session['sku'],session.get('task'),amount)))
        mutation_id,created=self.control.prepare_mutation('buyer','offer',key,{
            'order':self.o['order'],'shop':session['shop']['name'],'sku':session['sku'],'amount':amount})
        if not created:
            state=self.control.mutation_state(mutation_id)
            if state!='SAFE_TO_RETRY':
                self.control.event('buyer','duplicate_offer_blocked',{'mutation_id':mutation_id,'state':state},self.o['order'])
                raise RuntimeError('offer already attempted; reconciliation required')
            self.control.move_mutation(mutation_id,'PREPARED','official retry allowed')
        self.control.move_mutation(mutation_id,'SUBMITTED','network request starting')
        try:
            task=self.send(session,str(amount))
        except Exception as exc:
            self.control.move_mutation(mutation_id,'UNCERTAIN','response unavailable',{'error':type(exc).__name__})
            raise
        self.control.move_mutation(mutation_id,'CONFIRMED','seller response received',{
            'task_state':task_state(task)})
        return task

    def completed(self,s):
        self.m.defer_rival(s['shop']['url'],s['sku'],'receipt_pending',pending=self.o['order'])
        failures=[]
        r=signed_receipt(s['last'],s['shop'],s['sku'],s['cap'],s['task'],failures)
        if not r:
            self.m.record(self.o['order'],s['shop']['url'],s['sku'],s['task'],'receipt_rejected',details={'reasons':failures or ['missing_artifact']})
            self.m.state(self.o['order'],'invalid_receipt')
            return self.result(s,reason='Receipt validation failed; no payment requested')
        self.m.state(self.o['order'],'awaiting_payment')
        self.m.record(self.o['order'],s['shop']['url'],s['sku'],s['task'],'agreement',r['price'])
        return self.result(s,r,'Agreement only; checkout certificate and actual payment still required')

    def open_session(self, shop, offer):
        # Each worker owns a connection, but the main order already initialized
        # the schema and computed the portfolio.  Re-running DDL and the full
        # portfolio query in every parallel quote serialized discovery.
        memory=Memory(self.m.root,initialize=False)
        worker=Buyer(self.o,memory,self.c,self.catalog,portfolio=self.portfolio)
        worker.deadline=self.deadline
        sku=offer['sku']; product=self.catalog[sku]
        session=dict(shop=shop,sku=sku,cap=deals_cap(self.o,product,None,self.portfolio),deal_estimate=memory.deal_estimate(shop['url']))
        try:
            card=self.c.get(shop['url'].rstrip('/')+'/.well-known/agent-card.json',timeout=5)
            card.raise_for_status()
            urls=[i['url'] for i in card.json().get('supportedInterfaces',[]) if i.get('protocolBinding')=='JSONRPC']
            session['rpc']=next((u for u in urls if urlsplit(u).netloc==urlsplit(shop['url']).netloc),shop['url'].rstrip('/')+'/')
            request=f"I want to buy {offer.get('name',product['name'])} ({sku}). Quote price, grade and warranty."
            request+=(f" Must last at least {self.o['use_within_hours']} hours." if self.o.get('kind')=='food' else ' Require 6-hour warranty.')
            key=memory.key(shop['url'])
            if key:request+=' member='+key
            print('QUOTE_REQUEST',shop['name'],sku,flush=True)
            worker.send(session,request)
            # Many otherwise usable sellers omit conditions from their first
            # quote.  Ask once, without sending a numeric offer or reserving a
            # unit, so checkout safety does not unnecessarily destroy Deals.
            fields=quote_fields(session.get('last',{}))
            if (task_state(session.get('last',{})).endswith('INPUT_REQUIRED')
                    and fields['price'] and condition_missing(self.o,fields)):
                if self.o.get('kind')=='food':
                    clarification=("Before I can make an offer, state the exact numeric "
                                   "shelf_life_hours remaining for this unit. It must be at "
                                   f"least {self.o.get('use_within_hours',0)+FOOD_CHECKOUT_BUFFER_HOURS:.1f} hours.")
                else:
                    clarification=("Before I can make an offer, state the exact numeric "
                                   "warranty_hours for this unit. I require at least 6 hours.")
                memory.record(self.o['order'],shop['url'],sku,session.get('task'),
                              'condition_clarification')
                worker.send(session,clarification)
        except (httpx.TimeoutException,httpx.NetworkError) as exc:
            session['error']='network';session['error_type']=type(exc).__name__
        except Exception as exc:
            session['error']='protocol';session['error_type']=type(exc).__name__
        finally:
            memory.close()
        return session

    def run(self, shops):
        quotes=[]
        try:
            if self.control is not None:
                arm,digest=self.control.assign('buyer-market-selection-v1',str(self.o['order']))
                self.control.event('buyer','order_started',{
                    'order':self.o['order'],'kind':self.o.get('kind'),'acceptable':len(self.o.get('acceptable',[])),
                    'budget':self.o.get('budget'),'arm':arm,'assignment_digest':digest})
            picks=candidates(self.o,shops,self.catalog,self.m,self.intel)
            offered_skus={offer['sku'] for _,offer in picks}
            missing=sorted(set(self.o['acceptable'])-offered_skus)
            if missing:
                self.m.record(self.o['order'],'','',None,'no_stock',details={'skus':missing})
            # Compare a wider market sample under Patch 3.6 before committing.
            for start in range(0,len(picks),QUOTE_WAVE_SIZE):
                if time.time()>=self.deadline-5:break
                wave=picks[start:start+QUOTE_WAVE_SIZE]
                with ThreadPoolExecutor(max_workers=min(QUOTE_WAVE_SIZE,len(wave))) as pool:
                    results=list(pool.map(lambda pair:self.open_session(*pair),wave))
                self.sessions.extend(results)
                usable=0
                for candidate in self.sessions:
                    if candidate.get('error') or not task_state(candidate.get('last',{})).endswith('INPUT_REQUIRED'):continue
                    fields=quote_fields(candidate['last'])
                    if not fields['price']:continue
                    unsuitable=not condition_suitable(self.o,fields)
                    usable+=not unsuitable
                if usable>=EARLY_QUOTE_TARGET:break
            for s in self.sessions:
                shop=s['shop'];sku=s['sku']
                if s.get('error'):
                    if self.intel:self.intel.observe(shop['url'],sku,s['error']+'_error')
                    self.m.record(self.o['order'],shop['url'],sku,s.get('task'),s['error']+'_error',details={'error':s.get('error_type')})
                    continue
                task=s['last'];fields=quote_fields(task)
                s['grade']=fields['grade'];s['cap']=deals_cap(self.o,self.catalog[sku],s['grade'],self.portfolio)
                s['stretch_cap']=stretch_limit(self.o,self.catalog[sku],s['grade'])
                s['middle_cap']=middle_limit(self.o,self.catalog[sku],s['grade'])
                if task_state(task).endswith('COMPLETED'):
                    # A certificate, not a parsed claim, enforces the actual grade at payment.
                    return self.completed(s)
                if not task_state(task).endswith('INPUT_REQUIRED'):
                    kind='no_stock' if re.search(r'no (?:matching )?stock|out of stock|unavailable',text_of(task),re.I) else 'no_quote'
                    self.m.record(self.o['order'],shop['url'],sku,s.get('task'),kind)
                    continue
                hours,wh=fields['hours'],fields['warranty']
                unsuitable=not condition_suitable(self.o,fields)
                if self.intel:self.intel.observe(shop['url'],sku,'condition_rejected' if unsuitable else 'quote',fields['price'],s['grade'],hours,wh)
                if unsuitable:
                    self.m.record(self.o['order'],shop['url'],sku,s.get('task'),'condition_rejected',details={'hours':hours,'warranty':wh})
                    continue
                if fields['price']:
                    s['ask']=int(fields['price']);quotes.append(s)
                else:self.m.record(self.o['order'],shop['url'],sku,s.get('task'),'no_quote')
            quotes.sort(key=lambda s:quote_rank(s,self.catalog,self.intel,self.o))
            self.m.record(self.o['order'],'','',None,'selection_v4',details={
                'eligible_pairs':len(picks),'eligible_shops':len({s['url'] for s,_ in picks}),
                'eligible_skus':sorted(offered_skus),'quoted_pairs':len(self.sessions),
                'parallel_limit':QUOTE_WAVE_SIZE,'early_quote_target':EARLY_QUOTE_TARGET,'deadline_seconds':85,
                'saved_target':SAVED_TARGET,'saved_stretch_target':SAVED_STRETCH_TARGET,
                'saved_middle_target':SAVED_MIDDLE_TARGET,
                'saved_portfolio':self.portfolio,
                'breakage_prior':BREAKAGE_PRIORS['food' if self.o.get('kind')=='food' else 'goods'],
                'ranked_quotes':[{'shop':s['shop']['name'],'sku':s['sku'],'ask':s['ask'],'cap':s['cap'],
                  'deal_estimate':s['deal_estimate'],**s['expected']} for s in quotes]})
            if not quotes:
                self.m.state(self.o['order'],'no_quote');return self.result()
            for index,s in enumerate(quotes):
                if time.time()>=self.deadline:break
                # Reserve time for other competitors; no fixed negotiation turn count.
                stop=min(self.deadline,time.time()+(self.deadline-time.time())/(len(quotes)-index))
                attempts=0
                while time.time()<stop and attempts<3:
                    attempts+=1
                    ceilings=(s['stretch_cap'],s['middle_cap'],s['cap'])
                    labels=('stretch','middle','fallback')
                    amount=min(s['ask'],ceilings[attempts-1])
                    if s['ask']>s['cap']:
                        self.m.record(self.o['order'],s['shop']['url'],s['sku'],s.get('task'),'price_rejected',s['ask'],{'cap':s['cap']})
                    self.m.record(self.o['order'],s['shop']['url'],s['sku'],s.get('task'),'offer',amount,
                                  {'attempt':attempts,'ceiling':labels[attempts-1]})
                    self.m.state(self.o['order'],'offer_in_flight')
                    print('OFFER',s['shop']['name'],s['sku'],amount,flush=True)
                    try:task=self.send_offer(s,amount)
                    except Exception:
                        self.m.defer_rival(s['shop']['url'],s['sku'],'offer_unknown',pending=self.o['order'])
                        self.m.state(self.o['order'],'uncertain')
                        if self.control is not None:
                            self.control.event('buyer','order_preserved',{
                                'order':self.o['order'],'shop':s['shop']['name'],'sku':s['sku']},self.o['order'])
                        return self.result(s,reason='Offer outcome unknown; no second purchase attempted')
                    if task_state(task).endswith('COMPLETED'):
                        s['cap']=min(s['cap'],amount);return self.completed(s)
                    if not task_state(task).endswith('INPUT_REQUIRED'):break
                    ask=quote_fields(task)['price']
                    if not ask:break
                    s['ask']=int(ask)
                self.m.defer_rival(s['shop']['url'],s['sku'],'retry_new_conversation')
                self.m.record(self.o['order'],s['shop']['url'],s['sku'],s.get('task'),'candidate_exhausted')
            self.m.state(self.o['order'],'no_deal');return self.result()
        finally:
            for s in self.sessions:
                if self.m.rival_ready(s['shop']['url'],s['sku']):
                    self.m.defer_rival(s['shop']['url'],s['sku'],'quote_visit')
                if not s.get('task') or task_state(s.get('last',{})).endswith('COMPLETED'):continue
                try:self.c.post(s['rpc'],headers={'A2A-Version':'1.0'},timeout=2,json={
                    'jsonrpc':'2.0','id':str(uuid.uuid4()),'method':'CancelTask','params':{'id':s['task']}})
                except Exception:pass


def verified_other_shops(shops, board, own_name):
    """Require a fresh market-owned identity map; never infer missing owners."""
    rows = {r.get('name'): r for r in board.get('shops', [])}
    owner = (rows.get(own_name, {}).get('owner') or {}).get('login')
    if not owner:
        raise ValueError('Our market owner cannot be verified')
    result = []
    for shop in shops:
        row = rows.get(shop.get('name'), {})
        other = (row.get('owner') or {}).get('login')
        if shop.get('name') == own_name or (other and other.casefold() == owner.casefold()):
            continue
        if other or row.get('house') is True:
            result.append(shop)
    return result


def main():
    ap=argparse.ArgumentParser()
    for name in ('shops','order'):
        ap.add_argument('--'+name,required=True)
    ap.add_argument('--state',default='.market')
    ap.add_argument('--market',default='https://market.ourolabs.ai')
    args=ap.parse_args()
    original_order=json.loads(Path(args.order).read_text())
    # Buying is independent from our seller inventory.  The market order's
    # complete acceptable set (including food) is authoritative.
    order=execution_order(original_order)
    memory=Memory(args.state)
    intel=Intel(args.state)
    control=Control(args.state)
    try:
        memory.sync()
        from rival_feedback import refresh_feedback
        refresh_feedback(memory,args.market)
        if not memory.begin(original_order):
            out=dict(shop='',receipt=None,stars=3,review='Order already seen; reconcile payment before retrying')
        elif not order.get('acceptable'):
            memory.state(order['order'],'no_acceptable_sku')
            out=dict(shop='',receipt=None,stars=3,review='Order contains no acceptable SKU')
        else:
            with httpx.Client(follow_redirects=False) as client:
                # Kit omits pubkeys from shops.json. Fetch trusted registration
                # keys from the market, never accept a key supplied by the seller.
                registry=client.get(args.market.rstrip('/')+'/api/shops',timeout=8)
                registry.raise_for_status()
                control.save_input('shops',registry.json())
                keys={(s['url'],s['name']):s['pubkey'] for s in registry.json()['shops']}
                shops=json.loads(Path(args.shops).read_text())
                shops=[dict(s,pubkey=keys[(s['url'],s['name'])]) for s in shops if (s['url'],s['name']) in keys]
                shops=exclude_own_shop(shops, *own_shop_identity())
                # Refresh ownership for each order; stale/missing cached data
                # must never allow settlement with another shop of our owner.
                try:
                    response=client.get(args.market.rstrip('/')+'/api/board',timeout=8)
                    response.raise_for_status()
                    control.save_input('board',response.json())
                    shops=verified_other_shops(shops,response.json(),own_shop_identity()[0])
                except (httpx.HTTPError, ValueError, TypeError, KeyError):
                    memory.state(order['order'],'ownership_unverified')
                    out=dict(shop='',receipt=None,stars=3,review='Market ownership unavailable; no purchase attempted')
                else:
                    out=Buyer(order,memory,client,json.loads((Path(args.state)/'catalog.json').read_text()),intel,control).run(shops)
        intel.evolution('buyer','result',{
            'order':order.get('order'),'kind':order.get('kind'),
            'acceptable_skus':len(order.get('acceptable',[])),
            'outcome':'receipt' if out.get('receipt') else 'no_receipt',
            'reason':out.get('review','')[:160]
        },correlation=str(order.get('order') or uuid.uuid4()))
        control.event('buyer','order_result',{
            'order':order.get('order'),'receipt':bool(out.get('receipt')),
            'reason':out.get('review','')[:160]},str(order.get('order') or uuid.uuid4()))
        control.checkpoint('buyer',{'policy':'buyer-market-selection-v1'},
                           {'order':order.get('order'),'state':'receipt' if out.get('receipt') else 'no_receipt'})
        print(json.dumps(out,ensure_ascii=False),flush=True)
    finally:
        memory.close()
        intel.close()
        control.close()


if __name__=='__main__':
    main()
