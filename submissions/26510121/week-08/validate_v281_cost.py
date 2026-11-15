"""Stage and audit a no-op critic gate without changing either trading policy."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parent;STAGE=ROOT/'.market/v281-stage-20261115'
ECONOMIC=['market_buyer.py','seller_r3.py','restock.py','r3/economy.py','r3/risk.py',
    'r3/buyer_quality.py','r3/buyer_scoring.py','r3/inventory_portfolio.py','r3/checkout.py','r3/reversal.py']

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def stage():
    assert not STAGE.exists();STAGE.mkdir();(STAGE/'logs').mkdir();(STAGE/'.market').mkdir()
    sources=list(ROOT.glob('*.py'))+[ROOT/'policy.json']
    for folder in ('r3','tests'):sources.extend((ROOT/folder).glob('*.py'))
    for p in sources:
        target=STAGE/p.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target)
    cfg=json.loads((STAGE/'policy.json').read_text(encoding='utf-8-sig'))
    cfg.update(research_noop_gate_enabled=True,execution_revision='v28.1-noop-critic-gate-1')
    (STAGE/'policy.json').write_text(json.dumps(cfg,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('STAGED',len(sources),'source files; credentials and market state not copied')

def replay():
    import sys
    sys.path.insert(0,str(STAGE))
    sys.path.insert(0,str(STAGE/'tests'))
    from r3.research_gate import reviewed,remember
    from r3.state import Journal
    from test_market_cycle import opinion
    old=json.loads((ROOT/'policy.json').read_text(encoding='utf-8-sig'))
    cfg=json.loads((STAGE/'policy.json').read_text(encoding='utf-8-sig'))
    unchanged={n:digest(ROOT/n)==digest(STAGE/n) for n in ECONOMIC};assert all(unchanged.values())
    assert {k:v for k,v in old.items() if k not in ('execution_revision','research_noop_gate_enabled')}=={
        k:v for k,v in cfg.items() if k not in ('execution_revision','research_noop_gate_enabled')}
    j=Journal(STAGE/'.market/gate-replay');j.put('market_rules',{'sha256':'synthetic'})
    j.put('research-reviewed-proposals:v281',{})
    facts={'control':{'status':'observe_and_trade','reasons':[]},'framework':{'issues':['stock_coverage']}}
    proposal=opinion(['one']);proposal['approve_trial']=False;critic=dict(proposal)
    assert reviewed(j,cfg,facts,proposal,1000) is None
    remember(j,facts,proposal,critic,1000)
    assert reviewed(j,cfg,facts,proposal,1100)
    assert reviewed(j,cfg,dict(facts,control={'status':'defensive','reasons':['feed_stale']}),proposal,1100) is None
    changed=dict(proposal,approve_trial=True);assert reviewed(j,cfg,facts,changed,1100) is None
    files=['policy.json','r3/research.py','r3/research_gate.py','tests/test_research_gate.py','verify_market_cycle_live.py']
    row={'at':datetime.now(timezone.utc).isoformat(),'economic_files_byte_identical':unchanged,
        'source_hashes':{n:digest(STAGE/n) for n in files},'no_new_proposal_skips_critic':True,
        'risk_or_trial_change_forces_review':True,'model_calls':0,'market_transactions':0,'live_policy_writes':0,
        'policy_expiry_not_renewed_by_gate':True,'actual_cost_savings_not_yet_measured':True}
    p=ROOT/'logs'/('v281-cost-replay-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    p.open('x',encoding='utf-8').write(json.dumps(row,indent=2));print('SOURCE',p)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--stage',action='store_true');a=p.parse_args()
    stage() if a.stage else replay()
