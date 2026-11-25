"""Recover only uniquely matched historical kit refusals or buy[10:] omissions.

Clock-only kit lines cannot establish a date by themselves. A complete ordered
buy protocol must also match exactly one timestamped emission and its intents.
Ambiguous batches, uncertain HTTP responses and mere silence are left alone.
"""
import ast
import hashlib
import json
import re
import time
from inventory_observation import timestamp
from procurement_transport import DEFINITE_REJECTIONS

PROTOCOL=re.compile(r'^(\d\d:\d\d:\d\d) \[buyer\] (\{.*\})$')
REFUSAL=re.compile(r'^\d\d:\d\d:\d\d \[supply\] (\{.*?\}) refused: (\d+)')


def discover(intents,history,log_text):
    emissions=[e for e in history if e.get('event')=='portfolio_exploration_intent' and e.get('buy')]
    batches=[];active=None;hasher=hashlib.sha256()
    source=log_text.splitlines(keepends=True) if isinstance(log_text,str) else log_text
    for number,raw in enumerate(source,1):
        hasher.update(raw.encode());line=raw.rstrip('\r\n')
        match=PROTOCOL.match(line)
        if match:
            active=None
            try:buy=json.loads(match[2]).get('buy')
            except (ValueError,AttributeError):continue
            if not isinstance(buy,list) or not buy:continue
            candidates=[]
            for e in emissions:
                at=timestamp(e.get('at'))
                if at is None or e['buy']!=buy:continue
                # The emission and printed protocol must agree on the full local clock.
                clocks={time.strftime('%H:%M:%S',time.localtime(at+d)) for d in range(-5,6)}
                if match[1] in clocks:candidates.append(e)
            if len(candidates)!=1:continue
            active={'event':candidates[0],'buy':buy,'line':number,'refusals':[]};batches.append(active)
        elif active:
            match=REFUSAL.match(line)
            if match:
                try:body=ast.literal_eval(match[1])
                except (ValueError,SyntaxError):continue
                active['refusals'].append((body,int(match[2]),number))
    events=[];duplicate={id(b['event']):sum(o['event'] is b['event'] for o in batches) for b in batches}
    digest=hasher.hexdigest()
    for batch in batches:
        if duplicate[id(batch['event'])]!=1:continue
        emission=batch['event'];at=timestamp(emission.get('at'))
        for index,buy in enumerate(batch['buy']):
            if not isinstance(buy,dict):continue
            matches=[i for i in intents if i.get('sku')==buy.get('sku') and i.get('qty',1)==buy.get('qty')
                     and abs(i.get('at',0)-at)<=5
                     and (not emission.get('decision_id') or i.get('decision_id')==emission['decision_id'])]
            if len(matches)!=1:continue
            intent=matches[0]
            failures=[(code,line) for body,code,line in batch['refusals'] if body==buy]
            if index>=10:outcome,code,number='not_sent',None,batch['line']
            elif len(failures)==1 and failures[0][0] in DEFINITE_REJECTIONS:
                outcome='rejected';code,number=failures[0]
            else:continue
            identity=intent.get('intent_id') or 'legacy:'+hashlib.sha256(json.dumps(
                {k:intent.get(k) for k in ('sku','at','window','qty')},sort_keys=True).encode()).hexdigest()[:24]
            intent['intent_id']=identity
            events.append({'event':'procurement_supply_result','intent_id':identity,
                'decision_id':intent.get('decision_id'),'sku':intent['sku'],'qty':intent.get('qty',1),
                'window':intent.get('window'),'outcome':outcome,'http_status':code,
                'evidence_source':'legacy_official_kit_protocol','outcome_link_status':'legacy_protocol_exact',
                'request_id':f'legacy:{identity}:{outcome}', 'log_line':number,'log_sha256':digest})
    return events
