"""A 33.5-hour fictional life with nine offline market-issued orders.

One resident, real decision models, synthetic counterparties/payments/ledger.
No live market API, production keys, external messages, deployment or push.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import random
import sys
import tempfile
import time
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from buyer_life import BuyerLife,load
from context_compaction import size
from experiments.offline_market import OfflineMarket
import buyer_memory
import buyer_r3
import harness
import market_buyer_r3 as buyer
import review_r3

SCENES=[
 (0,'발표 순서 변경',['내일 랩미팅이 있다.','발표 순서가 변경되어 내 발표가 첫 번째가 되었다.']),
 (2,'학과 세미나',['오늘 오후 2시에 학과 세미나가 예정되어 있다.']),
 (5,'랩미팅 취소',['내일 예정됐던 랩미팅이 취소됐다.','다음 일정은 아직 정해지지 않았다.']),
 (8,'동기 방문 예정',['동기 두 명이 금요일 저녁 7시에 집에 방문하기로 했다.']),
 (23,'장비 예약 변경',['오늘 공동장비 예약 시간이 오후 1시로 변경됐다.']),
 (27,'단수 공지',['내일 오전 9시부터 정오까지 건물에 단수가 예정되어 있다.']),
 (29,'방문 시간 변경',['동기 두 명의 방문 시간이 오늘 저녁 7시 반으로 변경됐다.']),
 (31,'식당 휴무 공지',['내일 캠퍼스 식당은 휴무다.']),
 (33.5,'동기 도착',['동기 두 명이 집에 도착했다.']),
]


def protected():
    paths=[ROOT/'agent.json',ROOT/'state/buyer-life.json',ROOT/'state/seller-runtime/agent.json',
           ROOT/'state/seller-runtime/release.json',ROOT/'.market/outbox.jsonl',
           ROOT/'state/seller-runtime/.market/outbox.jsonl']
    return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None for p in paths}


def rows(path):return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


def choose_products(persona,catalog):
    def food(query):
        return next(s for s,p in catalog.items() if p.get('kind')=='food' and BuyerLife.matches(p,query))
    def goods(query,exclude=()):
        choices=[(s,p) for s,p in catalog.items() if p.get('kind')=='goods' and '>가구>' in p.get('category','')
                 and query in p.get('name','') and not any(w in p['name'] for w in exclude)]
        return min(choices,key=lambda row:row[1]['list_price'])[0]
    ingredients=persona['recipes'][0]['ingredients']
    milk=food(ingredients[1]);oats=food(ingredients[0]);caramel=food(ingredients[2])
    table=goods('테이블');sofa=goods('소파',('테이블','펫','강아지','고양이','커버','패드'));stool=goods('스툴')
    juice=food(persona['recipes'][2]['ingredients'][0])
    return [milk,oats,oats,caramel,table,sofa,stool,juice,juice],sofa


def main(output):
    guard=protected();wall_start=time.monotonic()
    persona=copy.deepcopy(load(ROOT/'logs/buyer-graduate-role-diversity-probe-2026-11-04.json',{})['samples'][0]['persona'])
    catalog=load(ROOT/'state/seller-runtime/.market/catalog.json',{})
    products,sofa_sku=choose_products(persona,catalog)
    calls=[];real_model=harness.decide_json
    def model(role,prompt,schema):
        start=time.monotonic()
        try:answer=real_model(role,prompt,schema)
        except Exception as e:
            calls.append({'role':role,'seconds':round(time.monotonic()-start,2),'error':type(e).__name__});raise
        calls.append({'role':role,'seconds':round(time.monotonic()-start,2),'prompt_bytes':size(prompt)})
        return answer
    records=[]
    with tempfile.TemporaryDirectory(prefix='buyer-life-rollout-') as directory:
        root=Path(directory);previous=Path.cwd();os.chdir(root)
        try:
            world=BuyerLife(root/'state/life.json',root/'logs/life-events.jsonl',root/'.market',model=model)
            # Fictional calendar: Thursday through Friday. It is not the current real date.
            start=datetime(2026,11,5,10,0,tzinfo=ZoneInfo('Asia/Seoul')).timestamp()
            world.initialize(persona,start)
            market=OfflineMarket(catalog,world.market_dir)
            rng=random.Random(20261104)
            for index,((hours,title,facts),sku) in enumerate(zip(SCENES,products)):
                now=start+hours*3600;market.now=now
                market.fault={1:'short_shelf_life',7:'checkout_price_changed'}.get(index)
                refund=market.break_goods(sofa_sku,now) if index==6 else None
                product=catalog[sku]
                order={'order':f'synthetic-order-{index}','item':product.get('category'),'kind':product['kind'],
                       'acceptable':[sku],'budget':product['list_price'],'use_within_hours':6,
                       'shelf_hours':product.get('shelf_hours'),'expires':time.time()+600,'simulated':True}
                before_events=len(rows(world.event_path));call_offset=len(calls)
                with patch('httpx.Client',side_effect=AssertionError('Market HTTP is prohibited in this simulation')), \
                     patch.object(buyer,'decide_json',side_effect=model), \
                     patch.object(buyer_r3,'decide_json',side_effect=model), \
                     patch.object(buyer,'negotiate',side_effect=market.negotiate):
                    outcome=buyer.run(order,market.shops(sku),catalog,'own-simulated-buyer',epsilon=.1,rng=rng,life=world)
                    deal,payment=market.checkout(order,outcome,now)
                    buyer_memory.import_payments()
                    if deal:
                        acquire=world.acquire
                        with patch.object(world,'acquire',side_effect=lambda d,c:acquire(d,c,now)), \
                             patch.object(review_r3,'hours_left',side_effect=lambda exp,_at:round(max(0,(exp-now)/3600),2) if exp else None):
                            review_r3.run(deal,catalog,life=world)
                    scene={'title':title,'facts':facts,'duration_seconds':1800}
                    world.direct_situation(now,model=lambda *_args,s=scene:copy.deepcopy(s))
                    before=world.context(now)
                    state_before_life=copy.deepcopy(load(world.state_path,{}))
                    after=world.tick(catalog,now+1,forum=[])
                emitted=rows(world.event_path)[before_events:]
                choices=[e for e in emitted if e.get('event')=='life_model_choice']
                decision=choices[-1]['decision'] if choices else None
                state=load(world.state_path,{})
                assert state['persona']['name']==persona['name']
                assert len(state['seen_ledgers'])==len(market.settlements),'Only completed simulated payments grant possessions'
                assert size(after)<=7500
                assert not (world.market_dir/'outbox.jsonl').exists()
                record={'step':index+1,'sim_hour':hours,'sim_clock':datetime.fromtimestamp(now,ZoneInfo('Asia/Seoul')).isoformat(),
                        'scene':scene,'market_order':order,'requested_product':product['name'],'sku':sku,
                        'purchase_driver':'scripted market-issued order; life remains observer',
                        'fault':market.fault,'payment':payment,'refund':refund,
                        'signed_receipt':bool(outcome.get('receipt')),'deal':deal,
                        'decision':decision,'plan_after':state.get('life_plan'),'state_before_life':state_before_life,
                        'hunger_before':before['hunger'],'hunger_after':after['hunger'],
                        'owned_statuses':{u:i['status'] for u,i in state['owned'].items()},
                        'placements':state['placements'],'context_bytes':size(after),
                        'new_events':emitted,'model_calls':calls[call_offset:],
                        'metrics':market.metrics()}
                records.append(record)
                print(json.dumps({'rollout_step':record},ensure_ascii=False),flush=True)
            final=load(world.state_path,{})
            drafts=rows(world.draft_path)
            result={'resident_count':1,'world_count':1,'resident':persona['name'],'persona':persona,
                    'simulated_hours':SCENES[-1][0],'starting_inventory':'empty','steps':records,
                    'metrics':market.metrics(),'payments':market.payments,'refunds':market.refunds,
                    'transcripts':market.transcripts,'final_state':final,'drafts':drafts,'model_calls':calls,
                    'tool_events':rows(world.event_path),'buyer_evidence':buyer_memory.read_events(),
                    'live_market_requests':0,'real_purchases':0,'public_posts':0,'buyer_activated':False,
                    'protected_live_files_unchanged':guard==protected(),'elapsed_seconds':round(time.monotonic()-wall_start,2),
                    'limitations':['One resident trajectory, not a causal test or live-market profit forecast.',
                       'Orders, prices, counterparts and faults are scripted; buyer selection/offer and life decisions use real models.',
                       'Purchases follow issued orders in observer mode; a story event is not evidence that it caused a purchase.',
                       'Fresh ephemeral keys and real local kit checks are used, but there is no A2A/AP2 network or official settlement.',
                       'Hunger, ownership lots and footprints are fictional game mechanics. Exact token billing is not measured.']}
            assert result['protected_live_files_unchanged']
        finally:os.chdir(previous)
    output=output.resolve();output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'rollout_summary':{'resident':result['resident'],'metrics':result['metrics'],
                     'model_calls':len(calls),'elapsed_seconds':result['elapsed_seconds'],
                     'live_unchanged':result['protected_live_files_unchanged']}},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    main(parser.parse_args().output.resolve())
