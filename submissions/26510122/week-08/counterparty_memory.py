"""Read-only public-ledger identity links plus bounded willingness evidence.

Confirmed local settlement is mandatory for price evidence. Public verified
results can bind a name/key, but a signed receipt is never counted as paid R3
stock. Unknown/new ledger shapes are ignored rather than guessed.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import statistics
import threading
import time
from collections import Counter, defaultdict

import httpx

from context_compaction import epoch
from market_memory import ATTEMPT_EVENTS, EVENTS, _attempts, events, record
from newsvendor_tool import GRADE_ANCHORS

CACHE=Path('state/counterparty-ledger.json')
POLL_SECONDS=20
RECENT_PURCHASE_SECONDS = 75 * 60
RECENT_PURCHASES_PER_BUYER = 20
RECENT_PURCHASES_LIMIT = 1_200
WINDOW_DAYS=14
PROFILE_EVENTS = frozenset({'buyer_link', 'buyer_price_probe'})
LEDGER_LINK_EVENTS = frozenset({'receipt_signed', 'buyer_link', 'checkout_unpaid'})
_LOCK=threading.Lock()


def recent_low_price_buyers(history, now, threshold):
    """Buyers with at least two verified paid sales, all below the threshold in 24h."""
    identities = defaultdict(set)
    for event in history:
        name = event.get('buyer_name')
        if (event.get('event') == 'buyer_link' and event.get('source') == 'market_verified_result'
                and event.get('task_id') and isinstance(name, str) and name):
            identities[event['task_id']].add(name)
    prices = defaultdict(list)
    seen = set()
    for event in history:
        if event.get('event') != 'settled':
            continue
        ledger = event.get('ledger')
        value = event.get('at')
        at = value if type(value) in (int, float) else epoch(value)
        paid = event.get('price')
        names = identities.get(event.get('task_id'), ())
        if (ledger is None or ledger in seen or at is None or not 0 <= now-at < 86400
                or type(paid) is not int or paid <= 0 or len(names) != 1):
            continue
        seen.add(ledger)
        prices[next(iter(names))].append(paid)
    return {name for name, paid in prices.items() if len(paid) >= 2 and max(paid) < threshold}


def load_cache(path=CACHE):
    try:
        result=json.loads(path.read_text())
        return result if isinstance(result,dict) else {}
    except (OSError,ValueError):return {}


def ingest(rows: list[dict], *, own_name: str,history: list[dict],path: Path=EVENTS) -> list[dict]:
    receipts={e['task_id']:e for e in history if e['event']=='receipt_signed' and e.get('task_id')}
    linked={e['task_id'] for e in history if e['event']=='buyer_link' and e.get('task_id')}
    unpaid_seen={e.get('source_seq') for e in history if e['event']=='checkout_unpaid'}
    proofs=[]
    for entry in rows:
        d=entry.get('data',{})
        if entry.get('kind')!='result' or not isinstance(d,dict):
            continue
        rec=d.get('receipt');name=d.get('buyer')
        local=receipts.get(rec.get('task')) if isinstance(rec,dict) else None
        checks=d.get('checks')
        checks=checks if isinstance(checks,dict) else {}
        required=('order_issued_to_buyer','one_result_per_order','item_matches','price_within_budget',
                  'task_not_seen','sig_seller_ok','sig_buyer_ok')
        if (local and entry.get('round')==3 and type(entry.get('seq')) is int
                and entry['seq'] not in unpaid_seen and checks.get('settled') is False
                and all(checks.get(k) is True for k in required)
                and rec.get('shop')==own_name and rec.get('item')==local.get('sku')
                and rec.get('price')==local.get('price')):
            record('checkout_unpaid',path=path,task_id=rec['task'],sku=local['sku'],
                   unit=local.get('unit'),price=local['price'],source_seq=entry['seq'],
                   source='public_market_result_checks',reason='Signed receipt submitted but not settled; buyer rejection reason unknown.')
            unpaid_seen.add(entry['seq'])
        if d.get('verified') is not True:
            continue
        if not isinstance(rec,dict) or not isinstance(name,str) or not 0<len(name)<=100:
            continue
        if (type(entry.get('seq')) is not int or not all(k in rec for k in ('shop','item','price','task'))
                or not isinstance(d.get('sig_buyer'),str) or not d.get('order')):
            continue
        proof={'seq':entry.get('seq'),'buyer_name':name,'sig_buyer':d['sig_buyer'],
               'signed':{**{k:rec[k] for k in ('shop','item','price','task')},'order':d['order']}}
        proofs.append(proof)
        local=receipts.get(rec['task'])
        if (local and rec['task'] not in linked and rec['shop']==own_name
                and local.get('sku')==rec['item'] and local.get('price')==rec['price']):
            record('buyer_link',path=path,task_id=rec['task'],buyer_name=name,source='market_verified_result',
                   source_seq=entry.get('seq'),order=d['order'])
            linked.add(rec['task'])
    return proofs


def recent_market_purchases(previous, ledger, now):
    """Keep a small, per-buyer balanced sample of actual public paid settlements."""
    by_seq = {r['seq']: r for r in previous if 0 <= now - r['at'] <= RECENT_PURCHASE_SECONDS}
    for row in ledger:
        at, data = row.get('ts'), row.get('data')
        if (row.get('kind') != 'settle' or row.get('round') != 3 or not isinstance(data, dict)
                or type(at) not in (int, float) or not 0 <= now - at <= RECENT_PURCHASE_SECONDS):
            continue
        buyer, sku, price, grade = (data.get(k) for k in ('buyer', 'sku', 'amount', 'grade'))
        if (not isinstance(buyer, str) or not buyer or not isinstance(sku, str) or not sku
                or type(price) is not int or price <= 0 or grade not in ('A', 'B', 'C')):
            continue
        expiry = data.get('expires_at')
        remaining = expiry - at if type(expiry) in (int, float) and math.isfinite(expiry) else None
        warranty = data.get('warranty_hours')
        by_seq[row['seq']] = {'seq': row['seq'], 'at': at, 'buyer': buyer, 'sku': sku, 'price': price,
                             'grade': grade, 'remaining_seconds': remaining,
                             'warranty_hours': warranty if type(warranty) in (int, float) else 0}
    counts = Counter()
    sample = []
    for seq in sorted(by_seq, reverse=True):
        row = by_seq[seq]
        if counts[row['buyer']] < RECENT_PURCHASES_PER_BUYER:
            sample.append(row)
            counts[row['buyer']] += 1
        if len(sample) >= RECENT_PURCHASES_LIMIT:
            break
    return sample


def refresh(own_name: str, *, cache_path=CACHE,event_path=EVENTS,now=None) -> list[dict]:
    now=time.time() if now is None else now
    with _LOCK:
        cache=load_cache(cache_path)
        # Public result and local receipt can arrive in either order.
        cached_rows = cache.get('rows', [])
        if cached_rows:
            ingest(cached_rows, own_name=own_name,
                   history=events(event_path, kinds=LEDGER_LINK_EVENTS), path=event_path)
        if now-cache.get('polled_at',0)<POLL_SECONDS:
            return cache.get('proofs',[])
        cache['polled_at']=now
        history = events(event_path, kinds=LEDGER_LINK_EVENTS)
        try:
            def fetch(after):
                response=httpx.get('https://market.ourolabs.ai/api/ledger',params={'after':after},timeout=5.)
                response.raise_for_status();batch=response.json()
                if not isinstance(batch,list):raise ValueError('ledger is not a list')
                return [e for e in batch if isinstance(e,dict) and type(e.get('seq')) is int]
            # Current results must not wait behind a legacy cursor backlog.
            # Keep advancing that cursor separately, preserving raw evidence.
            latest=fetch(-500);rows=list(latest);cursor=cache.get('cursor')
            if latest:
                cache['latest_seq']=max(e['seq'] for e in latest)
                if type(cursor) is int and cursor>=0 and cursor<min(e['seq'] for e in latest)-1:
                    try:
                        older=fetch(cursor);rows+=older
                        if older:cache['cursor']=max(e['seq'] for e in older)
                    except (httpx.HTTPError,ValueError):pass
                else:cache['cursor']=cache['latest_seq']
            rows=list({e['seq']:e for e in rows}.values())
            proofs=ingest(rows,own_name=own_name,history=history,path=event_path)
            combined={p['seq']:p for p in cache.get('proofs',[])+proofs}
            cache['proofs']=[combined[k] for k in sorted(combined)[-256:]]
            result_rows={e['seq']:e for e in cache.get('rows',[])+rows
                         if e.get('kind')=='result' and isinstance(e.get('data'),dict)}
            cache['rows']=[result_rows[k] for k in sorted(result_rows)[-512:]]
            cache['recent_purchases'] = recent_market_purchases(
                cache.get('recent_purchases', []), latest, now)
        except (httpx.HTTPError,ValueError,KeyError,TypeError):
            pass
        cache_path.parent.mkdir(parents=True,exist_ok=True)
        temporary=cache_path.with_name(f'.{cache_path.name}.{os.getpid()}.tmp')
        temporary.write_text(json.dumps(cache,ensure_ascii=False));os.replace(temporary,cache_path)
        return cache.get('proofs',[])


def profile(identity: dict,unit: dict, *, path=EVENTS,now=None,history=None,attempts=None) -> dict:
    now=time.time() if now is None else now
    result={'identity_status':identity.get('status','anonymous'),'buyer_name':identity.get('buyer_name'),
            'eligible_for_price_probe':False,'status':'unverified_identity','confirmed_comparable':0,
            'calm_high_paid':0,'examples':[],
            'note':'Past prices are evidence under past conditions, not a private budget or a permanent buyer label.'}
    if not identity.get('verified'):
        return result
    if history is None:
        kinds = PROFILE_EVENTS | ATTEMPT_EVENTS if attempts is None else PROFILE_EVENTS
        history = events(path, kinds=kinds)
    rows = history
    names={e['task_id']:e['buyer_name'] for e in rows if e['event']=='buyer_link' and e.get('source')=='market_verified_result'}
    if attempts is None:
        attempts = _attempts(rows)
    comparable=[];others=[]
    def bucket(hours):return 'unknown' if hours is None else 'near_expiry' if hours<=3 else 'not_near_expiry'
    current_hours=max(0,(unit['expires_at']-now)/3600) if unit.get('expires_at') is not None else None
    if unit.get('kind')=='food' and current_hours is None:
        result['status']='current_expiry_unknown';return result
    for a in attempts:
        when=epoch(a.get('at'));grade=a.get('settled_grade')
        if (a['outcome']!='SETTLED' or a['sku']!=unit['sku'] or grade!=unit['grade']
                or a.get('grade')!=grade or when is None or not 0<=now-when<=WINDOW_DAYS*86400
                or type(a.get('list_price')) not in (int,float) or a['list_price']<=0):continue
        if unit.get('kind')=='food' and (a.get('hours_left_at_receipt') is None or bucket(a['hours_left_at_receipt'])!=bucket(current_hours)):continue
        a={**a,'normalized_paid':a['settled_price']/a['list_price']}
        name=names.get(a['task_id'])
        if name==identity['buyer_name']:comparable.append(a)
        elif name:others.append(a)
    baseline=statistics.median(a['normalized_paid'] for a in others) if len(others)>=3 else GRADE_ANCHORS[unit['grade']]
    high=[a for a in comparable if a['normalized_paid']>=baseline*1.05 and a['offer_count']==1
          and a['counter_count']==0 and a['offers'] and a['offers'][0]>=a['opening_ask']*.98]
    result.update({'status':'insufficient_evidence','confirmed_comparable':len(comparable),'calm_high_paid':len(high),
                   'baseline_normalized_price':round(baseline,4),
                   'baseline_source':'other_comparable_settlements' if len(others)>=3 else 'unvalidated_grade_anchor_prior',
                   'other_comparable_count':len(others),'non_high_or_bargained_count':len(comparable)-len(high),
                   'examples':[{'task_id':a['task_id'],'ledger':a['ledger'],'price':a['settled_price'],
                                'grade':a['settled_grade'],'hours_left_at_receipt':a.get('hours_left_at_receipt'),
                                'offer_count':a['offer_count'],'counter_count':a['counter_count'],
                                'calm_high':a in high,
                                'normalized_paid':round(a['normalized_paid'],4)} for a in comparable[-3:]]})
    probes=[e for e in rows if e['event']=='buyer_price_probe' and e.get('buyer_name')==identity['buyer_name']
            and e.get('sku')==unit['sku'] and e.get('grade')==unit['grade'] and e.get('expiry_bucket')==bucket(current_hours)
            and epoch(e.get('at')) is not None and 0<=now-epoch(e['at'])<=WINDOW_DAYS*86400]
    if probes:
        last=probes[-1];attempt=next((a for a in attempts if a['task_id']==last.get('task_id')),None)
        result['last_price_probe']={'price':last['price'],'outcome':attempt['outcome'] if attempt else 'UNKNOWN'}
        later=[a for a in comparable if (epoch(a.get('at')) or 0)>(epoch(last.get('at')) or now)]
        if attempt and attempt['outcome'] in ('REJECTED','CANCELLED'):
            if not later:
                result['status']='last_price_probe_failed';return result
        if attempt and attempt['outcome'] in ('OPEN','RECEIPT_PENDING','EXPIRED_UNKNOWN') and now-(epoch(last.get('at')) or now)<1800:
            result['status']='price_probe_unresolved';return result
        if attempt and not later and attempt['outcome']=='SETTLED' and attempt['settled_price']<last['price']*.98:
            result['status']='last_price_probe_required_discount';return result
    if high:
        anchor=max(unit['list_price']*GRADE_ANCHORS[unit['grade']],
                   statistics.median(a['normalized_paid'] for a in high)*unit['list_price'])
        factors=(1.,1.05) if len(high)==1 else (1.,1.05,1.10)
        result.update({'status':'tentative_price_probe' if len(high)==1 else 'repeated_calm_high_payments',
                       'eligible_for_price_probe':True,
                       'price_candidates':sorted({min(2*unit['list_price'],max(1,round(anchor*f))) for f in factors}),
                       'hypothesis':'Test a higher NEW opening quote; preserve all already-agreed prices. '
                                    'If rejected, reconsider rather than escalating indefinitely.'})
    return result
