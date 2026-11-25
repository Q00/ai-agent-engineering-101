"""Real selector-model calls and offline negotiation; no marketplace requests."""
import json
import os
from pathlib import Path
import random
import sys
import tempfile
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import buyer_exploration
import buyer_memory
import market_buyer_r3 as buyer

shops=[{'name':f'synthetic-{n:02d}','url':f'https://synthetic-{n:02d}.test',
        'offers':[{'sku':'F1','qty':1}],'reviews':[]} for n in range(18)]
catalog={'F1':{'kind':'food','list_price':1000,'shelf_hours':24,'name':'Example Food'}}
real_model=buyer.decide_json


class PickLast:
    def random(self):return 0.
    def choice(self,rows):return rows[-1]


def mock_negotiate(url,sku,name,order,**kwargs):
    price=kwargs['opening_offer']
    return {'receipt':{'item':sku,'price':price,'task':'synthetic-task','sig_seller':'placeholder'},
            'offers':[price],'task_id':'synthetic-task','reason':'offline receipt fixture'}


results=[]
for label,epsilon in [('forced_explore',1.),('ordinary_model',0.)]:
    previous=Path.cwd();captured=[]
    with tempfile.TemporaryDirectory() as directory:
        os.chdir(directory)
        try:
            def model(role,prompt,schema):
                captured.append(prompt)
                return real_model(role,prompt,schema)
            order={'order':label,'item':'food','kind':'food','budget':1200,
                   'acceptable':['F1'],'use_within_hours':4,'expires':time.time()+500}
            with patch.object(buyer,'decide_json',side_effect=model), \
                 patch.object(buyer,'negotiate',side_effect=mock_negotiate):
                outcome=buyer.run(order,shops,catalog,'own',epsilon=epsilon,rng=PickLast())
            events=buyer_memory.read_events()
            selected=next(e for e in events if e['event']=='buyer_policy_selection')
            if epsilon==1.:
                assert outcome['shop']==shops[-1]['url']
                assert [r['id'] for r in captured[0]['candidate_shop_skus']]==['17']
            else:assert len(captured[0]['candidate_shop_skus'])==16
            result={'case':label,'mode':selected['mode'],'selected_shop':selected['shop_name'],
                    'selected_sku':selected['sku'],'opening_offer':selected['opening_offer'],
                    'displayed_count':len(captured[0]['candidate_shop_skus']),
                    'receipt_pending':bool(outcome['receipt']),'payment_evidence':[],
                    'note':'Offline placeholder receipt only; no actual payment or quality verification.'}
            results.append(result);print(json.dumps({'probe_result':result},ensure_ascii=False),flush=True)
        finally:os.chdir(previous)

rng=random.Random(20261103);pool=[{'id':str(i)} for i in range(18)]
visits={str(i):0 for i in range(18)};draws=10000
for _ in range(draws):
    policy=buyer_exploration.plan(pool,.1,rng)
    if policy['mode']=='explore':visits[policy['forced_candidate_id']]+=1
print(json.dumps({'sampling_check':{'seed':20261103,'draws':draws,'epsilon':.1,
    'exploration_count':sum(visits.values()),'selected_pair_counts':visits,
    'note':'RNG mechanics check, not buyer performance or real purchase frequencies.'}},ensure_ascii=False))
