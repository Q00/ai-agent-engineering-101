"""Observe shop-side ACP only; never log mandates, signatures, keys or full request bodies."""
import json
import time
from pathlib import Path

MARKER='checkout_trace_v1'

def install(ns):
    cls=ns['AcpShop'];proxy=ns['Proxy'];log=ns['log']
    if getattr(cls,MARKER,False):return
    def emit(shop,event,**fields):
        try:
            row={'time':time.time(),'event':event,**fields}
            line=json.dumps(row,ensure_ascii=True,default=str)
            log('[checkout-trace] '+line)
            if shop.md:
                with (Path(shop.md)/'seller-checkout.jsonl').open('a',encoding='utf-8') as f:f.write(line+'\n')
        except Exception:
            pass  # Observability must not alter payment behavior.
    original=cls.handle
    async def handle(self,req,path,body,public):
        try:data=json.loads(body or b'{}')
        except (ValueError,TypeError):data={}
        if not isinstance(data,dict):data={}
        parts=path.split('/');sid=parts[2] if len(parts)>2 else None
        session=self.sessions.get(sid,{})
        rec=data.get('receipt') if isinstance(data.get('receipt'),dict) else {}
        task=rec.get('task') or session.get('task')
        action=('create' if path=='acp/checkout_sessions' and req.method=='POST' else
                parts[3] if len(parts)>3 else 'get')
        details={'action':action,'task':task,'session':sid,
                 'unit':session.get('unit'),'sku':rec.get('item'),'price':rec.get('price')}
        if not path.startswith('acp/checkout_sessions'):
            return await original(self,req,path,body,public)
        emit(self,'checkout_request',**details)
        try:
            response=await original(self,req,path,body,public)
        except Exception as exc:
            emit(self,'checkout_exception',**details,error=type(exc).__name__)
            raise
        try:
            payload=json.loads(response.body)
            if not isinstance(payload,dict):payload={}
            details.update(session=payload.get('id',sid),unit=payload.get('unit',session.get('unit')))
            # Error text can be arbitrary remote content: use a fixed classification, not the body.
            error=str(payload.get('error',''))
            known={'the receipt is not signed by this shop':'invalid_signature',
                   'line_items do not match the receipt':'receipt_mismatch',
                   'this receipt already has a checkout session':'duplicate_session',
                   'no such checkout session':'missing_session',
                   'payment refused by the market':'market_rejected'}
            reason=known.get(error)
            if 'is not in stock for this order' in error:reason='unit_missing_or_held'
            if error.startswith('no ') and error.endswith(' in stock'):reason='out_of_stock'
            if error.startswith('session is '):reason='session_state_conflict'
            if error and reason is None:reason='other_error'
            market=payload.get('market') or {}
            checks=market.get('checks') or {} if isinstance(market,dict) else {}
            if not isinstance(checks,dict):checks={}
            status=response.status_code
            event=('checkout_rejected' if status>=400 else
                   'checkout_completed' if payload.get('status')=='completed' else
                   'checkout_canceled' if payload.get('status')=='canceled' else
                   'checkout_created' if action=='create' else 'checkout_response')
            emit(self,event,**details,http_status=status,status=payload.get('status'),reason=reason,
                 failed_checks=[k for k,v in checks.items() if not v],
                 active_holds=sum(s.get('status')=='ready_for_payment' for s in self.sessions.values()))
        except Exception:
            emit(self,'checkout_trace_parse_error',**details,http_status=response.status_code)
        return response
    cls.handle=handle
    observe=proxy.observe
    def observe_receipt(self,content):
        observe(self,content)
        try:
            if not self.acp:return
            result=json.loads(content).get('result') or {}
            task=result.get('task',result)
            for art in task.get('artifacts') or []:
                for part in art.get('parts') or []:
                    rec=part.get('data') or {}
                    if isinstance(rec,dict) and rec.get('sig_seller'):
                        emit(self.acp,'receipt_observed',task=rec.get('task',task.get('id')),
                             sku=rec.get('item'),unit=rec.get('unit'),price=rec.get('price'),
                             grade=rec.get('claim'),warranty_hours=rec.get('warranty_hours'))
        except Exception:pass
    proxy.observe=observe_receipt
    setattr(cls,MARKER,True)
