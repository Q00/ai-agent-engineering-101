"""ACP lifecycle guards using existing authoritative receipt reservations.

No price, certificate, mandate or signature check is weakened. Canceled ACP
sessions retain the signed receipt's DB hold until its original deadline.
"""
import asyncio
import json
import time
from pathlib import Path
from starlette.responses import JSONResponse
from market_memory import record


def install(kit,state,*,event_path=Path('logs/shop-events.jsonl'),now=time.time):
    cls=kit.AcpShop
    if getattr(cls,'_reservation_guards_installed',False):return
    create,handle=cls.create,cls.handle

    def emit(event,**fields):
        try:record(event,path=event_path,role='seller',outcome_source='acp_lifecycle',**fields)
        except OSError:pass # Observability cannot reverse an accepted payment.

    def round3(shop):
        try:return json.loads((Path(shop.md)/'inventory.json').read_text()).get('round',0)>=3
        except (OSError,ValueError,TypeError):return None

    def prune(shop):
        for sid,session in list(shop.sessions.items()):
            if session.get('status')!='ready_for_payment' or not session.get('unit'):continue
            reservation=state.receipt_reservation(session.get('task'))
            if not reservation or reservation['unit']!=session['unit']:
                session['status']='expired'
                emit('checkout_session_expired',task_id=session.get('task'),unit=session.get('unit'),session_id=sid,
                     reason='original receipt reservation expired, settled or differs from checkout')
            else:session['_reservation_until']=reservation['until']

    async def guarded_create(shop,body,public):
        if not hasattr(shop,'_receipt_create_lock'):shop._receipt_create_lock=asyncio.Lock()
        async with shop._receipt_create_lock:
            prune(shop)
            rec=body.get('receipt') if isinstance(body.get('receipt'),dict) else {}
            items=body.get('line_items') or [{}]
            if not (rec.get('sig_seller') and kit.verify_seller(shop.pub,rec)):
                return await create(shop,body,public)
            if not isinstance(items,list) or not isinstance(items[0],dict) or items[0].get('id')!=rec.get('item') or type(rec.get('price')) is not int:
                return JSONResponse({'error':'line_items do not match the receipt'},status_code=400)
            reservation=state.receipt_reservation(rec.get('task'))
            if not reservation and round3(shop) is False:return await create(shop,body,public)
            if not reservation:
                emit('checkout_create_refused',task_id=rec.get('task'),reason='no active original signed reservation')
                return JSONResponse({'error':'receipt reservation expired or already settled; request a fresh quote'},status_code=409)
            own=reservation['receipt']
            if any(rec.get(k)!=own.get(k) for k in kit.RECEIPT_KEYS):
                return JSONResponse({'error':'receipt differs from original signed reservation'},status_code=400)
            # Restore the seller's persisted unit/claim/warranty after proxy LRU
            # eviction or kit restart. Unsigned buyer fields are never trusted.
            shop.terms[rec['task']]=dict(own)
            while len(shop.terms)>500:shop.terms.popitem(last=False)
            for sid,session in list(shop.sessions.items()):
                if session.get('task')!=rec['task']:continue
                if session.get('status')=='ready_for_payment' and session.get('unit')==reservation['unit']:
                    emit('checkout_create_reused',task_id=rec['task'],unit=reservation['unit'],session_id=sid)
                    return JSONResponse(session,status_code=200)
                if session.get('status') in ('canceled','expired'):del shop.sessions[sid]
                else:return JSONResponse({'error':'this receipt already has a checkout session'},status_code=409)
            response=await create(shop,body,public)
            if response.status_code in (200,201):
                session=json.loads(response.body);stored=shop.sessions.get(session.get('id'),{})
                stored['_reservation_until']=reservation['until']
                emit('checkout_created',task_id=rec['task'],unit=stored.get('unit'),sku=rec.get('item'),price=rec['price'],
                     session_id=session.get('id'),reservation_until=reservation['until'],trusted_terms_restored=True)
            else:
                emit('checkout_create_refused',task_id=rec['task'],unit=reservation['unit'],http_status=response.status_code,
                     reason='market inventory or checkout rejected original reserved unit')
            return response

    async def guarded_handle(shop,req,path,body,public):
        prune(shop)
        parts=path.split('/')[1:];sid=parts[1] if len(parts)>1 else None
        response=await handle(shop,req,path,body,public)
        if req.method=='POST' and len(parts)>2:
            session=shop.sessions.get(sid,{})
            fields={'task_id':session.get('task'),'unit':session.get('unit'),'session_id':sid,'http_status':response.status_code}
            if parts[2]=='cancel' and response.status_code==200:emit('checkout_cancelled',**fields)
            elif parts[2]=='complete':
                if response.status_code==200:emit('checkout_completed',**fields)
                else:
                    try:checks=(json.loads(response.body).get('market') or {}).get('checks') or {}
                    except (ValueError,AttributeError):checks={}
                    emit('checkout_complete_failed',**fields,failed_checks=[k for k,v in checks.items() if v is False])
        return response

    cls.create=guarded_create;cls.handle=guarded_handle;cls._reservation_guards_installed=True
