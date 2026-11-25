"""Read-only actual-review selection and contract replay, no live model or trade calls."""
from contextlib import contextmanager
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT=Path(__file__).resolve().parent;STAGE=ROOT/'.market/v24-stage-20261112'
sys.path.insert(0,str(STAGE))
from r3.state import read_json
from r3.reputation import update,augment
from r3.economy import shop_candidates,cash_capacity
from r3.model import Chooser
from autonomous_status import ReadJournal


class View(ReadJournal):
    def __init__(self,db,now):
        super().__init__(db,now);self.md=ROOT/'.market/v24-replay';self.records=[]
        self.files={name:read_json(ROOT/'.market'/name,{}) for name in
            ('catalog.json','inventory.json','board.json','market_stock_counts.json','operating-policy-v16.json')}
    @contextmanager
    def connect(self):yield self.c
    def snapshot(self,name,default=None):return self.files.get(name,default)
    def add(self,kind,row,**kwargs):self.records.append((kind,row))


def main():
    now=time.time();cfg=read_json(STAGE/'policy.json')
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN');j=View(db,now);inv=j.snapshot('inventory.json');catalog=j.snapshot('catalog.json')
        packet=update(j,cfg,now);chooser=Chooser(j,cfg)
        chooser._call=lambda *a,**k:(_ for _ in ()).throw(AssertionError('No LLM on the replay path'))
        capacity=cash_capacity(inv,j,cfg,now)
        assert capacity['cash_floor']==0 and capacity['spendable']<=inv['balance']
        examples=[];timings=[]
        units=sorted(inv['units'],key=lambda u:(u.get('grade')!='C',u.get('kind')!='goods',-u.get('list_price',0)))
        selected=[];seen=set()
        for unit in units:
            key=(unit.get('kind'),unit.get('grade'))
            if key in seen:continue
            seen.add(key);selected.append(unit)
        for unit in selected:
            started=time.perf_counter()
            rows=shop_candidates(unit,inv,catalog,j,cfg,now=now)
            choice=chooser.choose('shop',rows,{'shop_execution':True,'operation':'quote','unit':unit})
            timings.append(time.perf_counter()-started)
            sales=[c for c in rows if c['action']!='hold']
            if unit['kind']=='goods':assert all(c['warranty_hours']>=cfg['shop_new_goods_warranty_hours'] for c in sales)
            assert all(c['price']>=c.get('recovery_floor',0) for c in sales)
            examples.append({'sku':unit['sku'],'kind':unit['kind'],'grade':unit.get('grade'),
                'choices':len(rows),'selected':{k:choice.get(k) for k in ('id','price','warranty_hours','expected_delta','objective_utility')},
                'reputation_candidates':sum(bool(c.get('evidence',{}).get('account_reputation')) for c in rows),
                'warranty_choices':sorted({c['warranty_hours'] for c in sales}),
                'downside_examples':[c['evidence']['account_reputation'] for c in sales if c.get('evidence',{}).get('account_reputation')][:2]})
        result={'at':datetime.fromtimestamp(now,timezone.utc).isoformat(),'model_summary':packet['summary'],
            'reviews':packet['evidence_reviews'],'observed_accounts':packet['observed_accounts'],
            'downside_contract_cells':packet['downside'],'capacity':capacity,'examples':examples,
            'elapsed_seconds':timings,'guards_verified':True,
            'source_hashes':{p.relative_to(STAGE).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                for p in list((STAGE/'r3').glob('*.py'))+[STAGE/'seller_r3.py',STAGE/'policy.json']},
            'market_transactions':0,'model_calls':0,'shared_state_writes':0,'economic_superiority_proven':False,
            'future_ratings_or_rank_guaranteed':False}
    path=ROOT/'logs'/('v24-reputation-replay-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(result,ensure_ascii=False,indent=2))
    print('SOURCE',path)
    print(json.dumps({'summary':packet['summary'],'reviews':packet['evidence_reviews'],'accounts':packet['observed_accounts'],
        'cash_floor':capacity['cash_floor'],'examples':examples,'elapsed_seconds':timings},ensure_ascii=True))


if __name__=='__main__':main()
