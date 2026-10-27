"""Rebuild numeric experience from untouched own logs and preserved Git history."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
AGENT = ROOT.parent/'week-07'
sys.path.insert(0,str(AGENT))
from negotiation.experience import Experience


def rows(text):
    for number,line in enumerate(text.splitlines(),1):
        if line.strip():
            value=json.loads(line)
            if not isinstance(value,dict):
                raise ValueError(f'non_object_record_at_line_{number}')
            yield value


def source_time(value):
    return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp() if value else None


def import_seller(memory, records):
    pending, turns, items, added = {}, {}, {}, 0
    for row in records:
        if row.get('event')=='startup' and row.get('live_market') is False:
            return added
        task=row.get('task_id')
        if row.get('event')=='seller_decision':
            pending[task]=row
        elif row.get('event')=='decision_applied' and task in pending:
            decision=pending.pop(task)
            value=decision['decision']
            turns[task]=turns.get(task,0)+1
            items[task]=value['item_id']
            added+=memory.seller_decision(task=task,item=value['item_id'],reserve=decision['reserve'],
                    turn=turns[task],action=value['action'],price=value['price'],
                    buyer_offer=decision.get('buyer_offer'),round_number=decision.get('market_round') or 2,
                    policy=decision.get('policy','historical-unspecified'),at=source_time(row.get('at')))
            if value['action']=='reject':
                memory.seller_end(task,value['item_id'],'reject',at=source_time(row.get('at')))
        elif row.get('event')=='canceled' and task in items:
            memory.seller_end(task,items[task],'cancel',at=source_time(row.get('at')))
    return added


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history-ref',default='stash@{0}')
    args=parser.parse_args()
    memory=Experience()
    sources=[]
    base='submissions/26510121/week-07/'

    def archived(relative):
        result=subprocess.run(['git','show',args.history_ref+':'+base+relative],capture_output=True)
        return result.stdout.decode('utf-8') if result.returncode==0 else None

    for name in ['market-trades.jsonl','market-verification.jsonl']:
        texts=[]
        old=archived('memory/'+name)
        if old is not None:
            texts.append((args.history_ref+':memory/'+name,old))
        local=AGENT/'memory'/name
        if local.is_file():
            texts.append((str(local.relative_to(AGENT.parent)),local.read_text(encoding='utf-8')))
        for label,text in texts:
            count=added=0
            for row in rows(text):
                count+=1
                if name=='market-trades.jsonl':
                    added+=memory.buyer_observation(row,round_number=row.get('round',2),policy='historical-unspecified')
                else:
                    added+=memory.buyer_feedback(task=row.get('task_id'),order=row.get('order'),
                        verified=row.get('verified'),settled=row.get('settled',False),
                        round_number=row.get('round',2),ledger=row.get('ledger'))
            sources.append({'source':label,'rows_read':count,'events_added':added})
    # The latest three archived server sessions plus all surviving local sessions.
    tree=subprocess.run(['git','ls-tree','-r','--name-only',args.history_ref,'--',base+'logs/seller'],capture_output=True,text=True)
    archived_sessions=sorted(p for p in tree.stdout.splitlines() if p.endswith('/server-events.jsonl'))[-3:]
    for path in archived_sessions:
        text=archived(path[len(base):])
        if text is not None:
            sources.append({'source':args.history_ref+':'+path,
                            'seller_decisions_added':import_seller(memory,rows(text))})
    for path in sorted((AGENT/'logs'/'seller').glob('*/server-events.jsonl')):
        sources.append({'source':str(path.relative_to(AGENT.parent)),
                        'seller_decisions_added':import_seller(memory,rows(path.read_text(encoding='utf-8')))})
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    result={'at_utc':stamp,'sources':sources,'database_counts':memory.status(),
            'limitations':['Archived rows without a round are week-07 historical Round 2 observations.',
                           'Historical rows without a policy version are not a controlled pricing baseline.',
                           'Only last three archived seller sessions are imported; all raw originals remain in Git.',
                           'Source files are read only; import identity makes repeated runs idempotent.']}
    output=ROOT/'logs'/('memory-bootstrap-'+stamp+'.json')
    with output.open('x',encoding='utf-8') as f:f.write(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'database_counts':result['database_counts'],'source_count':len(sources),'record':str(output.relative_to(ROOT))},ensure_ascii=True,indent=2))


if __name__=='__main__':
    main()
