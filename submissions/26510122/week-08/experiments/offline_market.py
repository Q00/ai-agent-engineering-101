"""Isolated counterparties and checkout for internal simulation only.

Uses fresh in-memory keys and the local official kit's signature/quality checks.
No HTTP, A2A transport, AP2 network or official settlement is performed.
"""
from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
import time
import uuid
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import buyer_r3
from buyer_memory import record


def official_kit():
    path=Path(__file__).resolve().parents[4]/'scripts/market_kit.py'
    spec=importlib.util.spec_from_file_location('internal_offline_official_kit',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class OfflineMarket:
    def __init__(self,catalog,market_dir,kit=None):
        self.catalog=catalog;self.market_dir=Path(market_dir);self.market_dir.mkdir(parents=True,exist_ok=True)
        self.kit=kit or official_kit()
        self.market_key=Ed25519PrivateKey.generate()
        self.keys={f'https://offline-shop-{i}.invalid':Ed25519PrivateKey.generate() for i in range(2)}
        self.products={};self.now=0;self.fault=None;self.next_ledger=920000000
        self.settlements=[];self.payments=[];self.refunds=[];self.transcripts=[]

    def shops(self,sku):
        return [{'name':f'offline-shop-{i}','url':url,'offers':[{'sku':sku,'qty':1}],
                 'reviews':[{'stars':4 if i==0 else 5,'review':'Synthetic local shop; no live reputation evidence.'}]}
                for i,url in enumerate(self.keys)]

    def negotiate(self,url,sku,name,order,**kw):
        p=self.catalog[sku];price=p['list_price'];task='offline-'+uuid.uuid4().hex
        idx=list(self.keys).index(url)
        ask=round(price*(.78 if idx==0 else .82));reserve=round(price*(.62 if idx==0 else .66))
        offered=[];transcript=[];receipt=None
        def message(speaker,text):
            row={'speaker':speaker,'text':text};transcript.append(row)
            if kw.get('on_message'):kw['on_message'](row)
        message('seller',f'Asking {ask}; certified unit, '+('6 h warranty.' if p['kind']=='goods' else 'shelf life shown at checkout.'))
        for turn in range(3):
            if not turn:offer=kw['opening_offer']
            else:
                offer=buyer_r3.followup_offer(message=f'Counteroffer: {ask}.',last_offer=offered[-1],
                    cap=kw['cap'],sku=sku,list_price=price,memory=kw['memory'],deadline=kw['deadline'],
                    history=transcript,offers_remaining=3-turn)
            if offer is None:break
            offered.append(offer);message('buyer',str(offer))
            if offer>=reserve:
                terms={'shop':f'offline-shop-{idx}','item':sku,'price':offer,'task':task,
                       'unit':'sim-'+uuid.uuid4().hex,'claim':'A','warranty_hours':6 if p['kind']=='goods' else None}
                terms['sig_seller']=base64.b64encode(self.keys[url].sign(self.kit.canon({k:terms[k] for k in self.kit.RECEIPT_KEYS}))).decode()
                receipt=terms;self.products[task]={'receipt':dict(terms),'url':url,'sku':sku,'fault':self.fault}
                message('seller',f'Deal at {offer}.');break
            ask=max(reserve,round((ask+offer)/2));message('seller',f'Counteroffer: {ask}.')
        self.transcripts.append({'order':order['order'],'shop':url,'sku':sku,'offers':offered,'messages':transcript,
                                 'signed_receipt':receipt is not None})
        return {'receipt':receipt,'offers':offered,'task_id':task,'transcript':transcript,
                'reason':'offline signed receipt; payment pending' if receipt else 'offline negotiation ended'}

    def checkout(self,order,outcome,now):
        receipt=outcome.get('receipt');url=outcome.get('shop');paid=False;reason=None;deal=None
        if not receipt:reason='no_signed_receipt'
        elif url not in self.keys or not self.kit.verify_seller(self.kit.pub_b64(self.keys[url]),receipt):reason='invalid_seller_signature'
        else:
            stored=self.products.get(receipt['task']);p=self.catalog.get(receipt['item'],{})
            if not stored or stored['receipt']!=receipt:reason='receipt_does_not_match_shop_record'
            elif receipt['item'] not in order['acceptable'] or receipt['price']>order['budget']:reason='order_mandate_mismatch'
            else:
                expires=(now+2*3600 if self.fault=='short_shelf_life' else now+p.get('shelf_hours',96)*.90*3600) if p['kind']=='food' else None
                cert_body={'unit':receipt['unit'],'sku':receipt['item'],'shop':receipt['shop'],
                           'kind':p['kind'],'supplied_at':now,'expires_at':expires,
                           'grade':None if p['kind']=='food' else 'A'}
                cert={'cert':cert_body,'sig':base64.b64encode(self.market_key.sign(self.kit.canon(cert_body))).decode()}
                co={'unit':receipt['unit'],'item':receipt['item'],'price':receipt['price'],
                    'cert':cert,'warranty_hours':receipt.get('warranty_hours')}
                if self.fault=='checkout_price_changed':co['price']+=1
                if co['price']!=receipt['price']:reason='checkout_price_differs_from_signed_receipt'
                else:
                    # Patch only the kit module's clock; story time never changes global host time.
                    class Clock:
                        @staticmethod
                        def time():return now
                    with patch.object(self.kit,'time',Clock):
                        reason=self.kit.check_accept({**order,'market_pubkey':self.kit.pub_b64(self.market_key)},co,outcome.get('accept'))
                if reason is None:
                    paid=True;self.next_ledger+=1
                    grade='A' if p['kind']=='goods' or (expires-now)/(p['shelf_hours']*3600)>=.8 else 'B'
                    settled={'ledger':self.next_ledger,'unit':receipt['unit'],'sku':receipt['item'],'name':p['name'],
                             'shop':url,'grade':grade,'claim':'A','claim_honest':grade=='A','expires_at':expires,
                             'warranty_hours':receipt.get('warranty_hours'),'value':p['list_price'],
                             'simulated':True,'settled_at':now,'price':receipt['price']}
                    deal={'order':order,'receipt':receipt,'settled':settled}
                    self.settlements.append(deal)
        row={'order':order['order'],'shop':url,'item':receipt.get('item') if receipt else None,
             'price':receipt.get('price') if receipt else None,'paid':paid,'reason':reason,
             'ledger':deal['settled']['ledger'] if deal else None,'simulated':True}
        self.payments.append(row)
        with (self.market_dir/'payments.jsonl').open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
        return deal,row

    def break_goods(self,sku,now):
        purchase=next((d for d in self.settlements if d['settled']['sku']==sku and not d.get('broken')),None)
        if not purchase:return None
        purchase['broken']=True;settled=purchase['settled']
        news={'name':settled['name'],'broke_at':now}
        with (self.market_dir/'broken.jsonl').open('a') as f:f.write(json.dumps(news,ensure_ascii=False)+'\n')
        covered=now-settled['settled_at']<=3600*(settled.get('warranty_hours') or 0)
        refund={'ledger':settled['ledger'],'unit':settled['unit'],'sku':sku,'amount':purchase['receipt']['price'] if covered else 0,
                'covered':covered,'simulated':True,'broke_at':now}
        self.refunds.append(refund);record('refund_received',**refund)
        return refund

    def metrics(self):
        spent=sum(d['receipt']['price'] for d in self.settlements)
        refunded=sum(r['amount'] for r in self.refunds)
        value=sum(0 if d.get('broken') else d['settled']['value'] for d in self.settlements)
        return {'orders':len(self.payments),'signed_receipts':sum(p['price'] is not None for p in self.payments),
                'paid':len(self.settlements),'refused':sum(not p['paid'] for p in self.payments),
                'gross_spend':spent,'refunds':refunded,'net_spend':spent-refunded,
                'simulated_surplus':value-spent+refunded}
