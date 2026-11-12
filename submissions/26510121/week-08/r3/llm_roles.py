"""Typed background reasoning; no executable code or transaction authority."""
import copy
import json
import math
import time
import httpx
from .model import ModelError, api_key, response_text

EFFORTS = {
    'gpt-6-luna': {'none','low','medium','high','xhigh','max'},
    'gpt-6.1-terra': {'none','low','medium','high','xhigh','max'},
    'gpt-6.1-sol': {'low','medium','high','xhigh','max'},
}


def profile(config, role):
    profiles = config.get('background_llm_roles', {})
    value = profiles.get(role)
    if value is None and role in ('market_research_analyst','market_research_critic'):
        # Backward-compatible historical replay, never an implicit live fallback.
        value = {'model':config.get('research_model','gpt-6-luna'),
                 'reasoning':'none','max_output_tokens':1200,'timeout_seconds':20}
    if not isinstance(value, dict):
        raise ModelError('role_unconfigured:'+role)
    model, effort = value.get('model'), value.get('reasoning')
    output, timeout = value.get('max_output_tokens'), value.get('timeout_seconds')
    rates = config.get('prices_usd_per_million',{}).get(model)
    if (value.get('response_mode','json_schema') not in ('json_schema','function')
            or model not in EFFORTS or effort not in EFFORTS[model]
            or type(output) is not int or not 256<=output<=16384
            or type(timeout) not in (int,float) or not math.isfinite(timeout) or not 5<=timeout<=90
            or not isinstance(rates,(list,tuple)) or len(rates)!=2
            or any(type(x) not in (int,float) or not math.isfinite(x) or x<=0 for x in rates)):
        raise ModelError('role_profile_invalid:'+role)
    return dict(value)


def decision_text(result, mode):
    if mode!='function':return response_text(result)
    calls=[o for o in result.get('output',[]) if o.get('type')=='function_call']
    if (len(calls)!=1 or calls[0].get('name')!='submit_background_decision'
            or calls[0].get('status') not in (None,'completed')
            or type(calls[0].get('arguments')) is not str):
        raise ModelError('decision_function_contract_invalid')
    return calls[0]['arguments']


def call_json(journal, config, role, schema, instructions, payload, client=None, key=None):
    settings=profile(config,role);model=settings['model']
    cap=config.get('daily_api_budget_usd')
    if not (cap is None and config.get('research_budget_mode')=='observe'
            or type(cap) in (int,float) and math.isfinite(cap) and cap>0):
        raise ModelError('research_budget_or_model_unconfigured')
    key=api_key() if key is None else key
    if not key:raise ModelError('api_key_missing')
    # Evidence identifiers are opaque capabilities, not natural-language prose.
    # Constrain decoding to exact IDs; never repair invented IDs after the call.
    schema=copy.deepcopy(schema)
    identifiers=payload.get('valid_evidence_ids')
    if 'evidence_ids' in schema.get('properties',{}):
        if (not isinstance(identifiers,list) or not identifiers
                or any(type(x) is not str for x in identifiers)):
            raise ModelError('valid_evidence_ids_missing')
        schema['properties']['evidence_ids']['items']={'type':'string','enum':list(dict.fromkeys(identifiers))}
        schema['properties']['evidence_ids']['minItems']=1
    body={'model':model,'instructions':instructions,
          'input':json.dumps(payload,ensure_ascii=False,allow_nan=False),
          'reasoning':{'effort':settings['reasoning']},
          'text':{'format':{'type':'json_schema','name':'background_decision','strict':True,'schema':schema}},
          'max_output_tokens':settings['max_output_tokens'],'store':False}
    mode=settings.get('response_mode','json_schema')
    if mode=='function':
        body.pop('text')
        body['instructions']+=' Submit your complete decision by calling submit_background_decision exactly once. Do not write a text answer.'
        body['tools']=[{'type':'function','name':'submit_background_decision',
            'description':'Submit one typed analytic proposal. This function grants no execution authority.',
            'parameters':schema,'strict':True}]
        body['tool_choice']={'type':'function','name':'submit_background_decision'}
        body['parallel_tool_calls']=False
    if len(body['input'].encode())>32000:raise ModelError('research_input_budget')
    rates=config['prices_usd_per_million'][model]
    reserve=((len(json.dumps(body).encode())+4096)*rates[0]*1.25+settings['max_output_tokens']*rates[1])/1e6
    ticket=journal.hold_budget(reserve,cap)
    if ticket is None:raise ModelError('daily_budget_exhausted')
    journal.add('background_model_request',{'role':role,'model':model,'reasoning_effort':settings['reasoning'],
        'response_mode':mode,
        'evidence_ids':identifiers,'observed_at':(payload.get('facts') or {}).get('observed_at'),
        'capability_context_id':((payload.get('facts') or {}).get('capability_evidence') or {}).get('id')})
    started=time.monotonic()
    try:
        response=(client or httpx).post('https://api.openai.com/v1/responses',
            headers={'Authorization':'Bearer '+key},json=body,timeout=settings['timeout_seconds'])
        if response.status_code!=200:
            raise ModelError('api_http_'+str(response.status_code))
        result=response.json();usage=result.get('usage') or {}
        actual=((usage.get('input_tokens',0)*rates[0]*1.25+usage.get('output_tokens',0)*rates[1])/1e6) if usage else reserve
        journal.close_budget(ticket,actual)
        journal.add('model_usage',{'role':role,'model':model,'reasoning_effort':settings['reasoning'],
            'response_mode':mode,
            'usage':usage,'estimated_usd':actual,'seconds':time.monotonic()-started,
            'completion_status':result.get('status'),'accounting':'conservative_input_1.25x',
            'automatic_model_fallback':False})
        if result.get('status') not in (None,'completed'):
            journal.add('background_model_incomplete',{'role':role,'model':model,
                'details':result.get('incomplete_details'),'output_types':[o.get('type') for o in result.get('output',[])]})
            raise ModelError('research_incomplete')
        value=json.loads(decision_text(result,mode))
        if not isinstance(value,dict):raise ModelError('research_contract_invalid')
        journal.add('background_model_output',{'role':role,'model':model,'value':value})
        return value
    except ModelError as exc:
        journal.add('background_model_failed',{'role':role,'model':model,'reason':str(exc),
                    'seconds':time.monotonic()-started})
        # Unknown transport/HTTP costs retain their reservation; never blindly retry.
        raise
    except (httpx.HTTPError,ValueError,TypeError,KeyError):
        journal.add('background_model_failed',{'role':role,'model':model,
                    'reason':'transport_or_schema','seconds':time.monotonic()-started})
        raise ModelError('research_transport_or_schema') from None
