"""One resident reacts to three curated external facts with a real model.

All possessions below are explicitly synthetic settlement fixtures using local
catalog metadata. No market request, real purchase, forum post or deployment.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from buyer_life import BuyerLife,load
from context_compaction import size

SITUATIONS=[
 {'title':'발표 순서 변경','facts':['내일 랩미팅이 있다.','발표 순서가 변경되어 내 발표가 첫 번째가 되었다.'],'duration_seconds':1800},
 {'title':'랩미팅 취소','facts':['내일 예정됐던 랩미팅이 취소됐다.','다음 랩미팅 일정은 아직 정해지지 않았다.'],'duration_seconds':1800},
 {'title':'동기 방문','facts':['동기 두 명이 금요일 저녁 7시에 집에 방문하기로 했다.'],'duration_seconds':1800},
]


def protected():
    paths=[ROOT/'agent.json',ROOT/'state/buyer-life.json',ROOT/'state/seller-runtime/agent.json',
           ROOT/'state/seller-runtime/release.json',ROOT/'.market/outbox.jsonl',
           ROOT/'state/seller-runtime/.market/outbox.jsonl']
    return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None for p in paths}


def event_rows(path):
    return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


def main(output):
    guard=protected()
    persona=copy.deepcopy(load(ROOT/'logs/buyer-graduate-role-diversity-probe-2026-11-04.json',{})['samples'][0]['persona'])
    catalog=load(ROOT/'state/seller-runtime/.market/catalog.json',{})
    if not catalog:raise RuntimeError('Existing local catalog snapshot required')
    start=time.monotonic();records=[];seed=[]
    with tempfile.TemporaryDirectory(prefix='single-resident-simulation-') as directory:
        root=Path(directory);world=BuyerLife(root/'life.json',root/'events.jsonl',root/'market')
        now=time.time();world.initialize(persona,now)
        # One complete existing recipe; the agent may cook it or choose otherwise.
        used=set();food=[];chosen_recipe=None
        for recipe in persona['recipes']:
            proposed=[];local=set()
            for query in recipe['ingredients']:
                item=next(((sku,p) for sku,p in catalog.items() if p.get('kind')=='food'
                           and sku not in local and world.matches(p,query)),None)
                if item is None:break
                local.add(item[0]);proposed.append(item)
            else:food=proposed;chosen_recipe=recipe['name'];used.update(local);break
        if not food:raise RuntimeError('No complete recipe can be seeded from the real catalog')
        goods=[]
        for query in ('테이블','소파','스툴'):
            match=next(((sku,p) for sku,p in catalog.items() if p.get('kind')=='goods'
                        and sku not in used and query in p.get('name','')),None)
            if match:goods.append(match);used.add(match[0])
        for index,(sku,p) in enumerate(food+goods):
            fixture={'order':{'order':f'sim-order-{index}','kind':p['kind']},
                     'receipt':{'item':sku,'price':max(1,round(p['list_price']*.7))},
                     'settled':{'sku':sku,'unit':f'sim-unit-{index}','ledger':910000000+index,
                                'name':p['name'],'grade':'A','value':p['list_price'],
                                'expires_at':now+86400 if p['kind']=='food' else None}}
            assert world.acquire(fixture,catalog,now)
            seed.append({'unit':f'sim-unit-{index}','sku':sku,'name':p['name'],'kind':p['kind'],
                         'source':'synthetic settlement fixture, not purchased'})
        initial=load(world.state_path,{})
        for index,situation in enumerate(SITUATIONS):
            before=world.context(now)
            offset=len(event_rows(world.event_path))
            # Curated facts are the environment. Only the resident's response is generated.
            scene=world.direct_situation(now,model=lambda *_args,s=situation:copy.deepcopy(s))
            assert scene is not None and scene['facts']==situation['facts']
            with patch('httpx.Client',side_effect=AssertionError('No market HTTP in internal simulation')):
                after=world.tick(catalog,now+1,forum=[])
            events=event_rows(world.event_path)[offset:]
            choices=[e for e in events if e.get('event')=='life_model_choice']
            if not choices:raise RuntimeError('Resident model did not return a decision; inspect raw log')
            decision=choices[-1]['decision'];state=load(world.state_path,{})
            assert state['persona']['name']==persona['name']
            assert state['persona']['student_role']==persona['student_role']
            assert set(state['owned'])==set(initial['owned']),'No invented possessions'
            assert size(after)<=7500
            assert not (world.market_dir/'outbox.jsonl').exists()
            record={'step':index+1,'simulated_minutes':round((now-initial['last_clock'])/60,2),
                    'situation':situation,'resident':state['persona']['name'],'decision':decision,'plan_after':state.get('life_plan'),
                    'hunger_before':before['hunger'],'hunger_after':after['hunger'],
                    'cooking_options_before':before['cooking_options'],
                    'proposed_actions':decision['actions'],
                    'applied_events':[e for e in events if e.get('event') in
                        ('cooked','placed','meal_planned','forum_drafted','preferences_revised','life_plan_updated')],
                    'rejected_actions':[e for e in events if e.get('event') in
                        ('life_action_rejected','preference_revision_rejected','life_plan_rejected')],
                    'placements_after':state['placements'],'context_bytes':size(after),
                    'same_identity':True,'no_market_outbox':True}
            records.append(record);print(json.dumps({'simulation_step':record},ensure_ascii=False),flush=True)
            now=state['scene']['ends_at']+1
        final=load(world.state_path,{})
        drafts=[json.loads(l) for l in world.draft_path.read_text().splitlines()] if world.draft_path.exists() else []
        result={'resident_count':1,'world_count':1,'resident':persona['name'],'persona':persona,
                'seed_recipe':chosen_recipe,'synthetic_starting_inventory':seed,'steps':records,
                'final_state':final,'internal_drafts':drafts,'model_calls':len(records),
                'market_api_calls':0,'real_purchases':0,'public_posts':0,'buyer_activated':False,
                'protected_live_files_unchanged':guard==protected(),
                'elapsed_seconds':round(time.monotonic()-start,2),
                'limitations':['One trajectory, no causal or performance benchmark.',
                    'Situations are scripted external facts; responses are real model decisions.',
                    'Starting possessions and settlement IDs are synthetic, using real catalog names.',
                    'Hunger and item footprints are virtual game units; time is advanced for simulation.',
                    'Proposed actions and tool-accepted actions are reported separately.']}
        assert result['protected_live_files_unchanged']
    output=output.resolve();output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'simulation_summary':{k:result[k] for k in ('resident_count','world_count','resident',
                      'model_calls','real_purchases','public_posts','protected_live_files_unchanged','elapsed_seconds')}},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    main(parser.parse_args().output)
