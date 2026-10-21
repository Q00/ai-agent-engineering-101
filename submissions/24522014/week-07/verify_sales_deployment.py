"""Diagnostic trades with our own public seller; never submit market results/reviews."""
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import ssl

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
import httpx

from buyer import seller_message
from market_buyer import MarketTransport
from seller import canon

ROOT=Path(__file__).resolve().parent


def main():
    deployment=json.loads((ROOT/'.market/experiment-deployment.json').read_text(encoding='utf-8'))
    prices=json.loads((ROOT/'shop_policy.json').read_text(encoding='utf-8'))['opening_reference']
    with httpx.Client(verify=ssl.create_default_context(),timeout=30) as http:
        r=http.get('https://market.ourolabs.ai/api/shops');r.raise_for_status()
        shop=next(s for s in r.json()['shops'] if s['name']=='kcwarni')
    assert shop['url']==deployment['url']
    results=[]
    for item,price in prices.items():
        with MarketTransport(shop['url'],item,price) as buyer:
            try:
                task=buyer.open();text=seller_message(task).strip()
                assert text==f'{item}: asking {price}',text
                assert re.findall(r'\d+',text)==[str(price)]
                parts=task['status']['message']['parts']
                data=next(p['data'] for p in parts if 'data' in p)
                assert data['item']==item and data['price']==price and data['kind']=='quote'
                assert 'in bulk on 10-29 KST' in data['event_terms']
                result={'item':item,'opening':price,'task':buyer.task_id,'quote_text':text,'buyer_log':str(buyer.log_dir)}
                if item=='lamp':
                    buyer.offer(100)
                    assert buyer.state=='TASK_STATE_COMPLETED'
                    receipt=buyer.raw_receipt
                    assert set(receipt)=={'shop','item','price','task','sig_seller'}
                    body={k:receipt[k] for k in ('shop','item','price','task')};body['price']=int(body['price'])
                    assert body['price']==100
                    Ed25519PublicKey.from_public_bytes(base64.b64decode(shop['pubkey'])).verify(
                        base64.b64decode(receipt['sig_seller']),canon(body))
                    result.update({'one_offer_completion':True,'receipt':receipt,'signature_verified':True})
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
