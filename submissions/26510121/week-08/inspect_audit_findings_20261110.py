"""Validate small findings against the read-only decision and outcome traces."""
import json,sqlite3,time
from collections import Counter
from datetime import datetime,timezone
from r3.state import ROOT,read_json,atomic_json

AUDIT='logs/agent-system-audit-20261110T043032470360Z.json'


def main():
    audit=read_json(ROOT/AUDIT);end=audit['cutoff'];result={'audit':AUDIT}
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as c:
        predictions=[json.loads(r[0]) for r in c.execute("SELECT data FROM events WHERE kind='prediction' AND ts<=? ORDER BY ts DESC LIMIT 2000",(end,))]
        rows=[];identities=Counter();route_samples=Counter()
        for p in predictions:
            context=p.get('context',{})
            if p.get('role')=='shop' and context.get('customer_memory'):
                memory=context['customer_memory'];identities[memory.get('identity_status')]+=1
            if p.get('role')!='buyer' or context.get('operation')!='offer_selection':continue
            for candidate in p.get('candidates',[]):
                evidence=candidate.get('evidence',{});execution=evidence.get('execution_model',{});live=evidence.get('live_quote',{})
                if live:route_samples[execution.get('route_samples','missing')]+=1
                if execution.get('route_samples',0)>=1 and execution.get('paid')==0 and live:
                    rows.append({'shop':candidate['shop'],'sku':candidate['sku'],
                        'prior_n':execution['route_samples'],'prior_completion':execution['completion_scenario'],
                        'live_ask':live['ask'],'live_cap':live['cap'],'conditional':bool(live.get('conditional_terms')),
                        'post_quote_expected_deals':candidate.get('expected_delta',{}).get('deals')})
        result['route_feedback_reset']={'sampled_predictions':len(predictions),'candidate_occurrences':len(rows),
            'live_quote_route_sample_counts':dict(route_samples),
            'above_cap':sum(r['live_ask']>r['live_cap'] for r in rows),
            'above_cap_reset_to_quarter':sum(r['live_ask']>r['live_cap'] and r['post_quote_expected_deals']==.25 for r in rows),
            'examples':rows[:8],'note':'Candidate occurrences, not unique orders. A fresh quote may update beliefs; fixed .25 does not calibrate observed failure history.'}
        result['customer_identity_in_quote_contexts']=dict(identities)
        result['evaluation_latest']={}
        for role,summary in audit['adaptive_evaluations_24h'].items():
            result['evaluation_latest'][role]={'actions':summary['actions'],'reasons':summary['reasons'],
                'latest':[{'action':r['action'],'reason':r['reason'],'evidence':r.get('evidence')} for r in summary['latest']]}
        result['states']={role:{k:state.get(k) for k in ('version','status','since','cooldown_until','patch')}
                          for role,state in audit['adaptive_states'].items()}
        result['event_sizes']=[{'kind':r['kind'],'n':r['n'],
            'json_characters':r.get('json_characters',r.get('json_bytes'))} for r in audit['event_sizes']]
        result['event_size_note']='Corrected label: source SQL length(TEXT) counts characters, not bytes. Source log remains unchanged.'
        p=c.execute("SELECT min(ts),max(ts),count(*) FROM events WHERE kind='customer_visit'").fetchone()
        result['customer_retained_now']={'oldest':p[0],'newest':p[1],'n':p[2],'span_hours':(p[1]-p[0])/3600}
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    path=ROOT/'logs'/f'agent-system-findings-{stamp}.json';atomic_json(path,result)
    print('SOURCE',path);print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
