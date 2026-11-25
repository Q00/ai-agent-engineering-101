"""Two sequential situations for the SAME resident; no market activity."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from buyer_life import BuyerLife,load
from context_compaction import size


def protected():
    paths=[ROOT/'agent.json',ROOT/'state/buyer-life.json',ROOT/'state/seller-runtime/agent.json',
           ROOT/'state/seller-runtime/release.json',ROOT/'.market/outbox.jsonl',
           ROOT/'state/seller-runtime/.market/outbox.jsonl']
    return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None for p in paths}


before=protected()
# Reuse ONE existing fictional fixture. No new character generation or cast.
fixture=load(ROOT/'logs/buyer-graduate-role-diversity-probe-2026-11-04.json',{})
persona=fixture['samples'][0]['persona']
catalog=load(ROOT/'state/seller-runtime/.market/catalog.json',{})
records=[]
with tempfile.TemporaryDirectory(prefix='single-buyer-world-') as directory:
    p=Path(directory);world=BuyerLife(p/'life.json',p/'events.jsonl',p/'market')
    now=time.time();world.initialize(persona,now)
    for i in range(2):
        scene=world.direct_situation(now)
        assert scene is not None
        stored=load(world.state_path,{})['scene']
        view=world.tick(catalog,now+1,forum=[])
        state=load(world.state_path,{})
        assert state['persona']==persona,'A situation must not rewrite the resident'
        assert not state['owned'],'No possessions may be invented from a narrative'
        assert size(view)<=7500
        assert not (world.market_dir/'outbox.jsonl').exists()
        events=[json.loads(l) for l in world.event_path.read_text().splitlines()]
        response=next(e['decision'] for e in reversed(events) if e.get('event')=='life_model_choice')
        records.append({'resident':state['persona']['name'],'scene':scene,'resident_response':response,
                        'same_identity':True,'context_bytes':size(view),'owned_count':0})
        print(json.dumps({'situation_step':records[-1]},ensure_ascii=False),flush=True)
        now=stored['ends_at']+1
    state=load(world.state_path,{})
    drafts=[json.loads(l) for l in world.draft_path.read_text().splitlines()] if world.draft_path.exists() else []
    result={'resident_count':1,'world_count':1,'resident':persona['name'],'situations':records,
            'archived_situation_count':len(state.get('scene_history',[])),
            'active_situation_count':1 if state.get('scene') else 0,
            'internal_drafts':drafts,'market_api_calls':0,'public_posts':0,'buyer_activated':False,
            'protected_live_files_unchanged':before==protected(),
            'limitation':'Situations are narrative background; expiry does not assert that a real meeting occurred.'}
    assert result['protected_live_files_unchanged']
(ROOT/'logs/buyer-world-facts-only-probe-2026-11-04.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'one_resident_summary':{k:result[k] for k in ('resident_count','world_count','resident',
      'archived_situation_count','active_situation_count','protected_live_files_unchanged','market_api_calls','public_posts')}},ensure_ascii=False))
