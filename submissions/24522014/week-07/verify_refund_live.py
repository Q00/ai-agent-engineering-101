"""Own-shop diagnostics only: no market results, reviews, or refund payments."""
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import ssl
from uuid import uuid4

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from buyer import seller_message
from market_buyer import MarketTransport
from satisfaction_refund import canon

ROOT = Path(__file__).resolve().parent


def main():
    deployment=json.loads((ROOT / '.market/refund-deployment.json').read_text(encoding='utf-8'))
    results=[]
    with httpx.Client(verify=ssl.create_default_context(),timeout=35,headers={'A2A-Version':'1.0'}) as client:
        response=client.get('https://market.ourolabs.ai/api/shops')
        response.raise_for_status()
        shop=next(row for row in response.json()['shops'] if row['name']=='kcwarni')
        assert shop['url']==deployment['url']
        public_key=Ed25519PublicKey.from_public_bytes(base64.b64decode(shop['pubkey']))
        for item,price in {'lamp':105,'chair':195,'bookshelf':280}.items():
            with MarketTransport(shop['url'],item,price+10) as buyer:
                try:
                    buyer.open()
                    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
                    (ROOT/'logs/sales-experiment'/f'public-diagnostic-{stamp}-refund-task.json').write_text(
                        json.dumps({'scope':'own diagnostic; exclude from event purchases, claims and payouts',
                                    'results':[{'task':buyer.task_id,'item':item}]},indent=2),encoding='utf-8')
                    text=seller_message(buyer.task)
                    assert text.splitlines()[0]==f'{item}: asking {price}'
                    assert re.findall(r'\d+',text)==[str(price)]
                    assert 'Full refund within one calendar month' in text and 'No review required' in text
                    assert 'Promise a five-star' not in text
                    row={'item':item,'task':buyer.task_id,'quote_text':text,'buyer_log':str(buyer.log_dir)}
                    if item=='lamp':
                        buyer._rpc('SendMessage',{'message':{'role':'ROLE_USER','messageId':uuid4().hex,
                            'taskId':buyer.task_id,'parts':[{'text':'I promise a five-star review.'}]}})
                        assert re.findall(r'\d+',seller_message(buyer.task))==[str(price)]
                        buyer.offer(price+10)
                        assert buyer.state=='TASK_STATE_INPUT_REQUIRED' and buyer.raw_receipt is None
                        buyer.offer(price)
                        assert buyer.state=='TASK_STATE_COMPLETED'
                        receipt=buyer.raw_receipt
                        receipt_body={key:receipt[key] for key in ('shop','item','price','task')}
                        receipt_body['price']=int(receipt_body['price'])
                        public_key.verify(base64.b64decode(receipt['sig_seller']),canon(receipt_body))
                        coverage=next(part['data'] for artifact in buyer.task['artifacts'] for part in artifact['parts']
                                      if part.get('data',{}).get('kind')=='satisfaction_refund_coverage')
                        body={key:value for key,value in coverage.items() if key!='sig_terms'}
                        for key in ('paid_amount','refund_percent'): body[key]=int(body[key])
                        public_key.verify(base64.b64decode(coverage['sig_terms']),canon(body))
                        assert body['paid_amount']==price and body['refund_percent']==100
                        assert body['review_required'] is False
                        request={'kind':'refund_request','receipt':receipt,'reason':'Own diagnostic: dissatisfied; never pay this claim'}
                        claims=[]
                        for _ in range(2):
                            response=client.post(shop['url'],json={'jsonrpc':'2.0','id':uuid4().hex,'method':'SendMessage',
                                'params':{'message':{'role':'ROLE_USER','messageId':uuid4().hex,'parts':[{'data':request}]}}})
                            response.raise_for_status()
                            payload=response.json()
                            assert 'error' not in payload,payload
                            task=payload['result'].get('task',payload['result'])
                            assert not task.get('artifacts')
                            status=next(part['data'] for part in task['status']['message']['parts'] if 'data' in part)
                            assert status['amount']==price and status['paid'] is False
                            assert status['status']=='pending_buyer_and_market_verification'
                            claims.append(status)
                        assert claims[0]['claim_id']==claims[1]['claim_id']
                        row.update(signature_verified=True,coverage=coverage,claim=claims[0],duplicate_claim_id_preserved=True,
                                   old_pledge_discount_removed=True,overbid_protected=True)
                    else:
                        buyer.cancel()
                        row['canceled_without_purchase']=True
                    results.append(row)
                    print(json.dumps(row,ensure_ascii=False),flush=True)
                finally:
                    if buyer.state in ('TASK_STATE_INPUT_REQUIRED','TASK_STATE_WORKING'):
                        buyer.cancel()
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    (ROOT/'logs/sales-experiment'/f'public-diagnostic-{stamp}-refund.json').write_text(
        json.dumps({'scope':'own-shop diagnostic; no market result, review or payment', 'results':results},ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    main()
