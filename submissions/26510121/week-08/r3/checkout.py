"""One final result per order; retry only proven pre-payment refusals.

No retry follows payment/transport uncertainty. Open mandates stay in memory,
and all accepted receipts still pass the course ACP/AP2 certificate checks.
"""
import base64
import json
import time
from .state import atomic_json


def retryable_pre_payment(reasons):
    if not reasons or any('payment failed:' in r or 'payment not completed:' in r for r in reasons):
        return False
    return all(r.startswith(('not paying:', 'checkout refused:')) for r in reasons)


def handle_order(kit, journal, cfg, order, agent, root, market, proxy, own_shop, keys):
    oid=order['order']
    completed=journal.get('checkout_final:'+oid)
    if completed:
        return completed
    inflight=journal.get('checkout_inflight:'+oid)
    if inflight and inflight.get('phase') in {'started','uncertain','settled'}:
        # A restart must never turn a lost response into another payment.
        response=kit.post_result(market,inflight['body'])
        if response.status_code==200:
            result=response.json();journal.put('checkout_final:'+oid,result)
            return result
        return None
    kit.log(f"[order {oid}] {order['item']}, budget {order['budget']}, round 3")
    order_path=root/'.market/order.json'
    atomic_json(order_path,{k:v for k,v in order.items() if k not in ('mandates','nonce')})
    acceptable=set(order.get('acceptable') or [])
    attempts=max(1,min(3,cfg.get('checkout_max_attempts',2)))
    body={'order':oid,'shop':'','receipt':None,'stars':3,'review':'No settled purchase.'}
    for attempt in range(attempts):
        if time.time()+cfg.get('checkout_buffer_seconds',30)>=order['expires']:
            break
        response=market.call('GET','/api/shops')
        if response.status_code!=200:break
        shops=[s for s in response.json()['shops'] if s['url'].rstrip('/')!=(own_shop or '').rstrip('/')
               and s.get('owner')!='seochanit' and s.get('name') not in {'seochanit','Seochanit Shop'}]
        pubkeys={s['url'].rstrip('/'):s['pubkey'] for s in shops}
        catalog=journal.snapshot('catalog.json',{})
        for s in shops:
            s['offers']=[{'sku':sku,'name':catalog.get(sku,{}).get('name',sku),'qty':qty}
                         for sku,qty in (s.get('stock') or {}).items() if sku in acceptable and qty>0]
        shops_path=root/'.market/shops.json'
        atomic_json(shops_path,[{k:s[k] for k in ('name','url','reviews','offers') if k in s} for s in shops])
        lines=kit.run_buyer(kit.fill(agent['market_buyer']['cmd'],shops=shops_path,item=order['item'],
                                  budget=order['budget'],order=order_path),root)
        output=kit.last_json(lines) or {}
        receipt=output.get('receipt');url=str(output.get('shop','')).rstrip('/')
        body={'order':oid,'shop':url,'receipt':receipt,'stars':output.get('stars',3),'review':output.get('review','')}
        if not isinstance(receipt,dict):break
        if isinstance(receipt.get('price'),float) and receipt['price'].is_integer():
            receipt=dict(receipt,price=int(receipt['price']));body['receipt']=receipt
        valid=(url in pubkeys and kit.verify_seller(pubkeys[url],receipt) and
               type(receipt.get('price')) is int and 0<receipt['price']<=order['budget'] and
               receipt.get('item') in acceptable)
        if not valid:
            journal.add('checkout_guard',{'order':oid,'reason':'invalid_receipt'})
            break
        signed={k:receipt[k] for k in kit.RECEIPT_KEYS}
        body['sig_buyer']=base64.b64encode(keys['buyer'].sign(kit.canon(dict(signed,order=oid)))).decode()
        reasons=[]
        journal.put('checkout_inflight:'+oid,{'phase':'started','at':time.time(),'body':dict(body)})
        settled=kit.acp_pay(order,url,pubkeys[url],receipt,keys['buyer'],output.get('accept'),reasons)
        body['mandate_receipt']=settled
        phase='settled' if settled else 'prepayment_refused' if retryable_pre_payment(reasons) else 'uncertain'
        journal.put('checkout_inflight:'+oid,{'phase':phase,'at':time.time(),'body':dict(body)})
        payment={'order':oid,'shop':url,'item':receipt['item'],'price':receipt['price'],'paid':bool(settled),
                 **({'ledger':settled.get('ledger')} if settled else {'reason':'; '.join(reasons) or 'unknown'})}
        with (journal.md/'payments.jsonl').open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(payment,ensure_ascii=False)+'\n')
        journal.reconcile()
        if settled:
            deal={'order':{k:v for k,v in order.items() if k not in ('mandates','nonce')},
                  'receipt':receipt,'settled':settled}
            atomic_json(journal.md/'deal.json',deal)
            from review import review
            rating=review(deal,journal)
            body['stars'],body['review']=rating['stars'],rating['review']
            break
        safe=retryable_pre_payment(reasons)
        if safe and cfg.get('buyer_patch34_enabled'):
            from .buyer_scoring import remember_refusal
            remember_refusal(journal,order,url,receipt,reasons)
        journal.add('checkout_attempt',{'order':oid,'shop':url,'sku':receipt['item'],
                                        'attempt':attempt+1,'safe_to_retry':safe})
        if not safe:break
        blocked=journal.get('checkout_exclusions:'+oid,[])
        blocked.append({'shop':url,'sku':receipt['item']})
        journal.put('checkout_exclusions:'+oid,blocked[-3:])
        kit.log(f'[r3] confirmed pre-payment refusal; trying another eligible offer ({attempt+1}/{attempts})')
    response=kit.post_result(market,body)
    if response.status_code!=200:
        journal.add('checkout_result_error',{'order':oid,'status':response.status_code})
        return None
    result=response.json()
    journal.put('checkout_final:'+oid,result)
    kit.log(f"[market] ledger #{result.get('ledger')}: verified {result.get('verified')}; result submitted once")
    return result
