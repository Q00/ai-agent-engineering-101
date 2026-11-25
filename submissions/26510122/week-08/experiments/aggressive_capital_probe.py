"""Live evidence, synthetic supply window, model approval only: no market writes."""
import argparse
import json
import math
import os
from pathlib import Path
import random
import shutil
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import inventory_portfolio as portfolio
from capital_policy import operating_policy,target_inventory_cost
from harness import decide_json
from market_memory import events
from replenishment_plan import inventory_risk


def main():
    runtime=ROOT/'state/seller-runtime'
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=ROOT/'logs/aggressive-capital-model-probe-2026-11-04')
    output=parser.parse_args().output.resolve();output.mkdir(exist_ok=False)
    inventory=json.loads((runtime/'.market/inventory.json').read_text())
    catalog=json.loads((runtime/'.market/catalog.json').read_text())
    history=events(runtime/'logs/shop-events.jsonl')
    (output/'inventory-observed.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2))
    with tempfile.TemporaryDirectory(prefix='capital-probe-',dir=ROOT/'state') as directory:
        temporary=Path(directory);(temporary/'state').mkdir();(temporary/'logs').mkdir()
        source=runtime/'state/inventory-portfolio.json'
        if source.exists():
            state=json.loads(source.read_text());state['windows']={}
            (temporary/'state/inventory-portfolio.json').write_text(json.dumps(state))
        original=Path.cwd();os.chdir(temporary)
        try:
            now=time.time()
            inventory['restock']={'open':True,'opens_at':now-10,'closes_at':now+170}
            risk=inventory_risk(inventory,now)
            result=portfolio.run(inventory,catalog,history,now,model=decide_json,
                                 refresh=lambda:(inventory,time.time(),True),rng=random.Random(20261104))
            approved=sum(math.ceil(catalog[b['sku']]['list_price']*.75)*b['qty'] for b in result.get('buy',[])) if result else 0
            report={'at':now,'market_writes':False,'synthetic_open_window':True,'risk_capacity':risk,
                    'operating_policy':operating_policy(now),'target_inventory_cost':target_inventory_cost(inventory,now),
                    'model_approved_intent':result,'approved_worst_case_cost':approved}
            (output/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
            for name in ('logs/shop-events.jsonl','state/inventory-portfolio.json'):
                if (temporary/name).exists():shutil.copy2(temporary/name,output/Path(name).name)
            print(json.dumps(report,ensure_ascii=False),flush=True)
        finally:os.chdir(original)


if __name__=='__main__':main()
