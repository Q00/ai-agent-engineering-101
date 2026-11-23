"""Audit only application-generated model events, without reading chat prompts."""
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import time


def audit(root):
    root=Path(root)
    calls={}
    blocked=0
    paths=sorted((root/'logs').glob('*.log'))+sorted((root/'logs').glob('*.jsonl'))
    paths+=list((root/'.market/optimization').glob('model-usage.jsonl'))
    seen=set()
    for path in paths:
        with path.open(encoding='utf-8',errors='replace') as stream:
            for line in stream:
                try:
                    row=json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row,dict):
                    continue
                event=row.get('event')
                if event=='call_blocked':
                    blocked+=1
                call_id=row.get('call_id')
                if not call_id or event not in ('harness_start','harness_decision','harness_error','harness_timeout','call_started','call_finished'):
                    continue
                key=(event,call_id)
                if key in seen:
                    continue
                seen.add(key)
                call=calls.setdefault(call_id,{'role':'unknown','events':[],'usage':None})
                call['events'].append(event)
                if row.get('role'):
                    call['role']=row['role']
                if row.get('usage'):
                    call['usage']=row['usage']
                at=row.get('at')
                if isinstance(at,str):
                    try:
                        at=datetime.fromisoformat(at.replace('Z','+00:00')).timestamp()
                    except ValueError:
                        at=None
                if isinstance(at,(int,float)):
                    call['last_at']=max(at,call.get('last_at',0))
                if event=='harness_start':
                    call['prompt_chars']=row.get('prompt_chars')
    totals=Counter()
    for call in calls.values():
        for key,value in (call['usage'] or {}).items():
            if isinstance(value,(int,float)):
                totals[key]+=value
    now=time.time()
    return {'at':now,'scope':'week-08 application model events only; interactive Codex sessions excluded',
            'unique_call_attempts':len(calls),'by_role':dict(Counter(c['role'] for c in calls.values())),
            'attempts_last_hour':sum(c.get('last_at',0)>=now-3600 for c in calls.values()),
            'attempts_without_usage':sum(c['usage'] is None for c in calls.values()),
            'reported_tokens':dict(totals) or None,'actual_credits':None,
            'credit_limitations':'Launch attempts do not prove billable requests. No account billing data available.',
            'blocked_attempts':blocked,'calls':calls}


if __name__=='__main__':
    root=Path(__file__).resolve().parent
    report=audit(root)
    (root/'logs/credit-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({key:value for key,value in report.items() if key!='calls'},ensure_ascii=False))
