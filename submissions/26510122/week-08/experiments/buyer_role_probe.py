"""Internal graduate-role samples; all worlds temporary, no market actions."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from buyer_life import BuyerLife,load
from buyer_role import validate_role
from context_compaction import size


def protected():
    files=[ROOT/'agent.json',ROOT/'state/buyer-life.json',ROOT/'state/seller-runtime/agent.json',
           ROOT/'state/seller-runtime/release.json',ROOT/'.market/outbox.jsonl',
           ROOT/'state/seller-runtime/.market/outbox.jsonl']
    return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None for p in files}


def main(output):
    before=protected();catalog=load(ROOT/'state/seller-runtime/.market/catalog.json',{})
    if not catalog:raise RuntimeError('An existing local catalog is required; no network fetch')
    samples=[];start=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='graduate-role-probe-') as directory:
        for i in range(3):
            base=Path(directory)/str(i)
            world=BuyerLife(base/'life.json',base/'events.jsonl',base/'market',
                            role_examples=[s['persona'] for s in samples])
            now=time.time();assert world.ensure(catalog,now),'role design unavailable'
            persona=load(world.state_path,{})['persona'];role=validate_role(persona['student_role'])
            view=world.context(now)
            assert view['owned_total']==0 and size(view)<=7500
            restarted=BuyerLife(world.state_path,world.event_path,world.market_dir,
                                Mock(side_effect=AssertionError('A persisted role must not be regenerated')))
            assert restarted.ensure(catalog,now+1)
            record={'sample':i+1,'fictional':True,'persona':persona,'role':role,
                    'context_bytes':size(view),'owned_total':view['owned_total'],
                    'restart_preserved_role':load(restarted.state_path,{})['persona']['student_role']==role}
            if i==0:
                # Advance fictional time to exercise one role-aware life planning call.
                world.tick(catalog,now+301,forum=[])
                events=[json.loads(l) for l in world.event_path.read_text().splitlines()]
                record['first_life_decision']=next((e['decision'] for e in reversed(events) if e.get('event')=='life_model_choice'),None)
                assert record['first_life_decision'] is not None
                assert world.context(now+301)['owned_total']==0,'The designer/planner cannot invent possessions'
                record['drafts']=[json.loads(l) for l in world.draft_path.read_text().splitlines()] if world.draft_path.exists() else []
            assert not (world.market_dir/'outbox.jsonl').exists()
            samples.append(record)
            print(json.dumps({'sample_result':record},ensure_ascii=False),flush=True)
    result={'samples':samples,'catalog_entries':len(catalog),'elapsed_seconds':round(time.monotonic()-start,2),
            'protected_live_files_unchanged':before==protected(),'market_api_calls':0,
            'buyer_activated':False,'public_posts':0,
            'limitations':['Only three fictional designs; not a diversity benchmark or claim about real students.',
                           'Academic pressures are narrative text, not measured research progress.',
                           'Goal success conditions are stored and shown to the planner; no automatic goal-completion scorer yet.']}
    assert result['protected_live_files_unchanged']
    output=output.resolve();output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'role_probe_summary':{k:result[k] for k in ('elapsed_seconds','protected_live_files_unchanged','market_api_calls','buyer_activated','public_posts')}},ensure_ascii=False),flush=True)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    main(parser.parse_args().output)
