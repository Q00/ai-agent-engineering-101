"""Own paid-order breakage evidence. No name-only seller attribution or blacklist."""
import json
import math
import time
from datetime import datetime,timezone
from collections import defaultdict
from pathlib import Path

OWN_NAME='outseop-26510122'
WINDOW=7*86400
MAX_PROOFS=2000

def epoch(value):
    if type(value) in (int,float):return float(value)
    try:return datetime.fromisoformat(str(value).replace('Z','+00:00')).timestamp()
    except (ValueError,TypeError):return 0.

def collect(previous,rows,own_name,now):
    by_seq={}
    for e in list(previous)+list(rows):
        d=e.get('data') if isinstance(e,dict) else None
        if (not isinstance(d,dict) or e.get('kind')!='broken' or e.get('round')!=3
                or type(e.get('seq')) is not int or type(e.get('ts')) not in (int,float)
                or not math.isfinite(e['ts']) or not 0<=now-e['ts']<=WINDOW
                or d.get('buyer')!=own_name or not all(isinstance(d.get(k),str) and d[k] for k in ('order','unit','item'))
                or type(d.get('refunded',0)) is not int or d.get('refunded',0)<0):continue
        by_seq[e['seq']]=e
    return [by_seq[k] for k in sorted(by_seq)[-MAX_PROOFS:]]

def paid_orders(history):
    paid={}
    for e in history:
        if (e.get('event')=='settled' or e.get('event')=='payment' and e.get('paid') is True):
            if type(e.get('ledger')) is int and e.get('order') and e.get('sku'):
                if e.get('event')=='settled' or e['ledger'] not in paid:paid[e['ledger']]=e
    return paid

def attach(history,public_rows,*,now=None,own_name=OWN_NAME):
    now=time.time() if now is None else now
    orders=defaultdict(list)
    for e in paid_orders(history).values():orders[(e['order'],e['sku'])].append(e)
    aliases=defaultdict(set)
    for e in history:
        if e.get('shop') and e.get('shop_name'):aliases[(e.get('order'),e['shop'])].add(e['shop_name']);aliases[(None,e['shop'])].add(e['shop_name'])
    result=[];seen=set()
    for proof in collect([],public_rows,own_name,now):
        d=proof['data'];matches=orders.get((d['order'],d['item']),[])
        if len(matches)!=1:continue
        sale=matches[0]
        if sale['ledger'] in seen or proof['ts']<epoch(sale.get('at'))-60:continue
        names=aliases.get((sale['order'],sale.get('shop'))) or aliases.get((None,sale.get('shop')),set())
        name=sale.get('shop_name') or (next(iter(names)) if len(names)==1 else None)
        result.append({'event':'verified_breakage','at':datetime.fromtimestamp(proof['ts'],timezone.utc).isoformat(),
            'broke_at':proof['ts'],'source_seq':proof['seq'],'order':sale['order'],'sku':sale['sku'],
            'shop':sale.get('shop'),'shop_name':name,'unit':d['unit'],'settled_ledger':sale['ledger'],
            'refund_amount':d.get('refunded',0),'paid_price':sale.get('price'),'grade':sale.get('grade'),
            'source':'public_ledger_own_paid_order_sku','note':'Shop identity comes from our paid order; current tunnel URL is not identity.'})
        seen.add(sale['ledger'])
    return result

def load(history,event_path,*,now=None):
    runtime=Path(event_path).resolve().parent.parent
    public=[]
    for path,key in [(runtime.parent/'seller-runtime/state/counterparty-ledger.json','buyer_breakages'),
                     (runtime/'state/buyer-breakage-backfill.json','rows')]:
        try:
            value=json.loads(path.read_text());rows=value.get(key,[])
            if isinstance(rows,list):public.extend(rows)
        except (OSError,ValueError,TypeError):continue
    return attach(history,public,now=now)

def profiles(history,*,now=None):
    now=time.time() if now is None else now
    by_ledger=paid_orders(history);fails={e['settled_ledger']:e for e in history
        if e.get('event')=='verified_breakage' and type(e.get('settled_ledger')) is int}
    result={'shop_sku':{},'product':{}}
    for ledger,sale in by_ledger.items():
        if not 0<=now-epoch(sale.get('at'))<=WINDOW:continue
        name=sale.get('shop_name') or (history.resolved(sale.get('shop'),sale.get('order')) if hasattr(history,'resolved') else None)
        keys=[('product',sale['sku'])]+([('shop_sku',(name,sale['sku']))] if name else [])
        failure=fails.get(ledger)
        if failure and (failure.get('source')!='public_ledger_own_paid_order_sku' or not 0<=now-epoch(failure.get('at'))<=WINDOW):failure=None
        for group,key in keys:
            row=result[group].setdefault(key,{'paid':0,'broken':0,'refunded':0,'mature_paid':0,'early_broken':0})
            row['paid']+=1
            mature=now-epoch(sale.get('at'))>=6*3600
            row['mature_paid']+=int(mature)
            if failure:
                row['early_broken']+=int(not mature)
                row['broken']+=1
                row['refunded']+=int(failure.get('refund_amount',0)>0)
    return result

def for_choice(profiles,shop,sku):
    local=profiles.get('shop_sku',{}).get((shop,sku),{})
    product=profiles.get('product',{}).get(sku,{})
    if not max(local.get('broken',0),product.get('broken',0)):return {}
    return {'same_shop_sku':local,'same_product':product,
            'source':'own_paid_order+public_broken_ledger','censored':True}


def contribution_debit(evidence,saving):
    """Observed adverse-contribution debit, not a calibrated lifetime failure rate."""
    if not evidence:return 0.
    saving=max(0.,min(1.,saving));debits=[]
    for key in ('same_shop_sku','same_product'):
        row=evidence.get(key) or {};paid=row.get('paid',0);broken=row.get('broken',0);refunded=row.get('refunded',0)
        observed=row.get('mature_paid',paid)+row.get('early_broken',0)
        # Pending, still-intact purchases are not evidence of surviving 6h.
        denominator=max(broken,observed)+(5 if key=='same_shop_sku' else 10)
        debits.append(((broken-refunded)*(1+saving)+refunded*saving)/denominator)
    return min(1+saving,max(debits,default=0.)) # overlapping evidence is not charged twice


def purchase_policy(evidence,kind):
    """Known unrefunded failures change terms, not a permanent seller blacklist."""
    if kind!='goods' or not evidence:return {}
    local=evidence.get('same_shop_sku') or {};product=evidence.get('same_product') or {}
    local_loss=max(0,local.get('broken',0)-local.get('refunded',0))
    product_loss=max(0,product.get('broken',0)-product.get('refunded',0))
    required=6 if local_loss>=1 or product_loss>=2 else 0
    return {'required_warranty_hours':required,'same_shop_sku_unrefunded':local_loss,
            'same_product_unrefunded':product_loss,'evidence_window_days':7,
            'reason':'verified_unrefunded_breakage_requires_6h_cover' if required else 'observed_risk_only',
            'note':'A/B and certificate checks still apply. Protection is a condition to negotiate and verify, not proof that this seller currently offers it. Refund avoids minus one, but a broken refunded purchase still contributes zero.'}
