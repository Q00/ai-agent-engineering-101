"""Replay one resident snapshot with original vs neutral current facts."""
import argparse
import copy
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from buyer_life import BuyerLife,load
from experiments.buyer_life_rollout import protected


def main(source,output):
    result=json.loads(source.read_text());sample=result['steps'][4]
    catalog=load(ROOT/'state/seller-runtime/.market/catalog.json',{})
    guard=protected();records=[]
    for label in ('original_fact','neutral_fact'):
        state=copy.deepcopy(sample['state_before_life'])
        if label=='neutral_fact':
            state['scene']['title']='학과 홈페이지 공지'
            state['scene']['facts']=['학과 홈페이지 공지 목록의 정렬 방식이 바뀌었다.']
            for e in reversed(state['recent']):
                if e['kind']=='scene_started':e['summary']=state['scene']['facts'][0];break
        with tempfile.TemporaryDirectory(prefix='same-resident-control-') as directory:
            p=Path(directory);world=BuyerLife(p/'life.json',p/'events.jsonl',p/'market')
            world.state_path.write_text(json.dumps(state,ensure_ascii=False))
            now=datetime.fromisoformat(sample['sim_clock']).timestamp()+1
            with patch('httpx.Client',side_effect=AssertionError('No market network')):
                view=world.tick(catalog,now,forum=[])
            events=[json.loads(l) for l in world.event_path.read_text().splitlines()]
            decision=next(e['decision'] for e in reversed(events) if e['event']=='life_model_choice')
            after=load(world.state_path,{})
            assert after['persona']['name']==result['resident']
            assert set(after['owned'])==set(state['owned'])
            assert not (world.market_dir/'outbox.jsonl').exists()
            record={'condition':label,'resident':result['resident'],'facts':state['scene']['facts'],
                    'identical_starting_inventory':True,'previous_plan':state.get('life_plan'),
                    'decision':decision,'applied_events':[e for e in events if e['event'] in ('cooked','placed','forum_drafted')],
                    'model_calls':1}
            records.append(record);print(json.dumps({'control_case':record},ensure_ascii=False),flush=True)
    output.write_text(json.dumps({'cases':records,'protected_live_files_unchanged':guard==protected(),
         'limitation':'One stochastic pair, holding the previous history and new table ownership fixed. It cannot prove a causal scene effect.'},ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();main(args.input.resolve(),args.output.resolve())
