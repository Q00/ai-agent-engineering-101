"""Observe the official kit's unchanged supply calls, without logging credentials.

The context follows one restock thread. Only a protocol containing our exact
reservation IDs can release a reservation; HTTP timeouts/5xx remain unknown.
"""
from contextvars import ContextVar
import uuid
from decision_audit import LINKS
from market_memory import record

_batch=ContextVar('procurement_transport_batch',default=None)
DEFINITE_REJECTIONS={400,401,402,403,404,405,409,410,422}


def lines(protocol):
    metadata=protocol.get('_procurement',{}) if isinstance(protocol,dict) else {}
    decision=metadata.get('decision_id');window=metadata.get('window')
    if not isinstance(decision,str) or not isinstance(window,str):return []
    result=[]
    for b in protocol.get('buy',[])[:10]:
        if not isinstance(b,dict) or not isinstance(b.get('sku'),str) or type(b.get('qty')) is not int:continue
        if not 1<=b['qty']<=20:continue
        result.append({**{k:metadata[k] for k in LINKS if k in metadata},'window':window,
            'sku':b['sku'],'qty':b['qty'],'intent_id':f"{decision}:{b['sku']}"})
    return result if len({r['sku'] for r in result})==len(result) else []


class ObservedMarket:
    def __init__(self,market,batch):self.market=market;self.batch=batch
    def __getattr__(self,name):return getattr(self.market,name)
    def call(self,method,path,*args,**kwargs):
        body=kwargs.get('json') or {}
        matches=[r for r in self.batch['lines'] if r['sku']==body.get('sku') and r['qty']==body.get('qty')]
        if method!='POST' or path!='/api/supply' or len(matches)!=1:
            return self.market.call(method,path,*args,**kwargs)
        row=matches[0];identity=row['intent_id'];request_id=uuid.uuid4().hex
        self.batch['started'].add(identity)
        fields={**row,'request_id':request_id,'evidence_source':'official_kit_http'}
        record('procurement_request_started',**fields)
        try:response=self.market.call(method,path,*args,**kwargs)
        except Exception as error:
            self.batch['last'][identity]='unknown'
            record('procurement_supply_result',**fields,outcome='unknown',error=type(error).__name__)
            raise
        status=response.status_code
        outcome=('accepted' if status==200 else 'retryable' if status==429
                 else 'rejected' if status in DEFINITE_REJECTIONS else 'unknown')
        self.batch['last'][identity]=outcome
        record('procurement_supply_result',**fields,outcome=outcome,http_status=status)
        return response


def instrument(kit):
    """Preserve upstream authentication, retries, throttling and refresh logic."""
    original_run=kit.run_buyer;original_restock=kit.restock
    def observed_run(*args,**kwargs):
        output=original_run(*args,**kwargs);batch=_batch.get()
        if batch is not None:
            protocol=kit.last_json(output) or {};batch['lines']=lines(protocol)
            for row in batch['lines']:record('procurement_protocol_received',**row)
        return output
    def observed_restock(agent,d,market,md,inv,demand):
        batch={'lines':[],'started':set(),'last':{}};token=_batch.set(batch)
        try:return original_restock(agent,d,ObservedMarket(market,batch),md,inv,demand)
        finally:
            try:
                for row in batch['lines']:
                    identity=row['intent_id']
                    if identity not in batch['started']:
                        record('procurement_supply_result',**row,outcome='not_sent',evidence_source='official_kit_batch_finished')
                    elif batch['last'].get(identity)=='retryable':
                        record('procurement_supply_result',**row,outcome='rejected',http_status=429,
                               evidence_source='official_kit_retries_exhausted')
                record('procurement_batch_finished',lines=len(batch['lines']),started=len(batch['started']))
            finally:_batch.reset(token)
    kit.run_buyer=observed_run;kit.restock=observed_restock

