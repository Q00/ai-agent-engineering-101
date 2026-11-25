"""Diagnostic trades with our own public seller; never submit market results/reviews."""
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import ssl
import time
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
import httpx

from buyer import seller_message
from market_buyer import MarketTransport
from seller import canon

ROOT=Path(__file__).resolve().parent


def main():
    deployment_path = ROOT / '.market/purchase-deployment.json'
    if not deployment_path.exists():
        deployment_path = ROOT / '.market/review-promise-deployment.json'
    deployment=json.loads(deployment_path.read_text(encoding='utf-8'))
    prices=json.loads((ROOT/'shop_policy.json').read_text(encoding='utf-8'))['opening_reference']
    with httpx.Client(verify=ssl.create_default_context(),timeout=30) as http:
        r=http.get('https://market.ourolabs.ai/api/shops');r.raise_for_status()
        shop=next(s for s in r.json()['shops'] if s['name']=='kcwarni')
    assert shop['url']==deployment['url']
    results=[]
    for item,price in prices.items():
        with MarketTransport(shop['url'],item,price+10) as buyer:
            try:
                started=time.monotonic()
                task=buyer.open();text=seller_message(task).strip()
                # Register diagnostic tasks before assertions or follow-up trades,
                # so failed diagnostics also remain excluded from measurements.
                diagnostic_stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
                (ROOT/'logs/sales-experiment'/f'public-diagnostic-{diagnostic_stamp}-task.json').write_text(
                    json.dumps({'scope':'own-shop diagnostic only; never a market result or event participant',
                                'url':shop['url'], 'results':[{'task':buyer.task_id,'item':item}]}, indent=2), encoding='utf-8')
                opening_seconds=time.monotonic()-started
                assert text.splitlines()[0]==f'{item}: asking {price}',text
                assert 'Promise a five-star review to unlock our lowest price.' in text
                assert re.findall(r'\d+',text)==[str(price)]
                parts=task['status']['message']['parts']
                data=next(p['data'] for p in parts if 'data' in p)
                assert data['item']==item and data['price']==price and data['kind']=='quote'
                assert 'promise a 5-star review' in data['event_terms']
                assert 'No refund' in data['event_terms']
                result={'item':item,'opening':price,'opening_seconds':opening_seconds,
                        'task':buyer.task_id,'quote_text':text,'buyer_log':str(buyer.log_dir)}
                if item=='lamp':
                    buyer.offer(price+10)
                    assert buyer.state=='TASK_STATE_INPUT_REQUIRED'
                    assert buyer.raw_receipt is None
                    assert re.findall(r'\d+',seller_message(buyer.task))==[str(price)]
                    result['overbid_reconfirmed_without_receipt']=True
                    # Diagnostic pledge to our own shop; this is not submitted
                    # as a market review or counted event participation.
                    started=time.monotonic()
                    buyer._rpc('SendMessage', {'message': {'role':'ROLE_USER',
                        'messageId':uuid4().hex, 'taskId':buyer.task_id,
                        'parts':[{'text':'I promise a five-star review.'}]}})
                    assert buyer.state=='TASK_STATE_INPUT_REQUIRED' and buyer.raw_receipt is None
                    assert re.findall(r'\d+',seller_message(buyer.task))==['100']
                    result['promise_response_seconds']=time.monotonic()-started
                    result['promise_unlocked_price']=100
                    buyer.offer(100)
                    assert buyer.state=='TASK_STATE_COMPLETED'
                    receipt=buyer.raw_receipt
                    assert set(receipt)=={'shop','item','price','task','sig_seller'}
                    body={k:receipt[k] for k in ('shop','item','price','task')};body['price']=int(body['price'])
                    assert body['price']==100
                    Ed25519PublicKey.from_public_bytes(base64.b64decode(shop['pubkey'])).verify(
                        base64.b64decode(receipt['sig_seller']),canon(body))
                    result.update({'confirmed_event_price':True,'receipt':receipt,'signature_verified':True})
                elif item == 'chair':
                    buyer._rpc('SendMessage', {'message': {'role':'ROLE_USER',
                        'messageId':uuid4().hex, 'taskId':buyer.task_id,
                        'parts':[{'text':'I offer 195 if you provide a warranty.'}]}})
                    assert buyer.state == 'TASK_STATE_INPUT_REQUIRED' and buyer.raw_receipt is None
                    assert 'not configured' in seller_message(buyer.task)
                    result['conditional_service_offer_not_accepted'] = True
                    started = time.monotonic()
                    # This diagnostic explicitly consents to the current quote.
                    # MarketTransport normally records this in offer(price).
                    buyer.last_offer = price
                    buyer._rpc('SendMessage', {'message': {'role':'ROLE_USER',
                        'messageId':uuid4().hex, 'taskId':buyer.task_id,
                        'parts':[{'text':'I accept the quoted price.'}]}})
                    assert buyer.state == 'TASK_STATE_COMPLETED'
                    receipt = buyer.raw_receipt
                    body = {k:receipt[k] for k in ('shop','item','price','task')}
                    body['price'] = int(body['price'])
                    assert body['price'] == price and body['task'] == buyer.task_id
                    Ed25519PublicKey.from_public_bytes(base64.b64decode(shop['pubkey'])).verify(
                        base64.b64decode(receipt['sig_seller']),canon(body))
                    result.update(natural_confirmation_verified=True, signature_verified=True,
                                  confirmation_seconds=time.monotonic()-started, receipt=receipt)
                else:
                    buyer.cancel();result['canceled_without_purchase']=True
                results.append(result)
                print(json.dumps(result,ensure_ascii=False),flush=True)
            finally:
                if buyer.state in ('TASK_STATE_INPUT_REQUIRED','TASK_STATE_WORKING'):
                    buyer.cancel()
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out=ROOT/'logs/sales-experiment'/f'public-diagnostic-{stamp}.json'
    out.write_text(json.dumps({'scope':'own-shop diagnostic only; no market order, result submission, rating or rebate eligibility',
                               'url':shop['url'],'results':results},ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    main()
