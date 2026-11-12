"""Read-only evidence for the patch-3.6 score discontinuity and stale caps."""
from datetime import datetime,timezone
import difflib
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from r3.state import ROOT,read_json
from autonomous_status import ReadJournal


def main():
    old_path=ROOT/'logs/peer-market-source-20261112T054459689112Z.json'
    new_path=ROOT/'logs/peer-market-source-20261112T085523230195Z.json'
    old=read_json(old_path);new=read_json(new_path)
    old_text=old['responses']['/agent.md']['data'];new_text=new['responses']['/agent.md']['data']
    before=read_json(ROOT/'logs/autonomous-deploy-before-20261112T082837273128Z.json')['board']
    after=new['responses']['/api/board']['data']
    def metrics(board):
        rows=[r for r in board['buyers'] if type(r.get('all')) in (int,float)]
        me=next(r for r in rows if r['name']=='seochanit')
        return {'values':{k:me.get(k) for k in ('saved','all','deals','forum','posts')},
            'ranks':{m:1+sum(r[m]>me[m] for r in rows if type(r.get(m)) in (int,float)) for m in ('saved','all','deals','posts')},
            'cohort':len(rows)}
    a,b=metrics(before),metrics(after)
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN');j=ReadJournal(db,time.time());cat=j.snapshot('catalog.json',{})
        rules=j.get('market_rules',{});observed=j.events('market_rules_observed',1)
        quotes=[]
        for r in j.events('quote_comparison',100):
            state=r.get('assessment',{}).get('saved_state',{})
            if state.get('saved',0)<.6:continue
            product=cat.get(r['sku'],{});L=product.get('list_price',0)
            quotes.append({'order':r['order'],'sku':r['sku'],'kind':product.get('kind'),'list_price':L,
                'ask':r.get('ask'),'cap':r['assessment'].get('cap'),
                'target':r['assessment'].get('target_contribution'),'cap_to_list':r['assessment'].get('cap',0)/max(1,L),
                'at':r['_ts']})
            if len(quotes)>=6:break
        purchase_fields=list((j.events('purchase',1) or [{}])[0])
    n=b['values']['deals']-a['values']['deals']
    max_without_rescore=(a['values']['saved']*(a['values']['deals']+100)+max(0,n))/(b['values']['deals']+100)
    result={'old_source':str(old_path.relative_to(ROOT)),'new_source':str(new_path.relative_to(ROOT)),
        'old_rules_sha256':hashlib.sha256(old_text.encode()).hexdigest(),
        'new_rules_sha256':hashlib.sha256(new_text.encode()).hexdigest(),
        'rule_diff':''.join(difflib.unified_diff(old_text.splitlines(True),new_text.splitlines(True))),
        'before_1728_kst':a,'after_1755_kst':b,'new_deals':n,
        'maximum_saved_from_only_new_deals_under_old_units':max_without_rescore,
        'rules_auditor_still_claims_buyer_compatible':rules.get('buyer_compatible'),
        'latest_rule_observation':observed,'new_rule_quote_caps':quotes,
        'local_purchase_fields':purchase_fields,'buyer_formula_corrected_by_this_script':False,
        'verified_causes':['Historical score units changed; public saved rank falls from 9 to 25.',
            'Price cap still treats new Saved as old list-price discount; observed food cap about 29% of list.',
            'Rule audit recognizes Saved and deals+100 but not the changed cost denominator.'],
        'buyer_patch36_implementation_status':'not implemented in the Shop-only v21 release',
        'next_required_repairs':['explicit scoring formula/version detection',
            'cost-aware contribution and price caps with verified cost or labeled cost uncertainty',
            'formula-scoped outcomes, policy evaluation and refusal-memory invalidation'],
        'model_calls':0,'market_transactions':0}
    path=ROOT/'logs'/('v21-buyer-patch36-diagnosis-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf-8').write(json.dumps(result,ensure_ascii=False,indent=2));print('SOURCE',path)
    print(json.dumps({'before':a,'after':b,'quotes':quotes,'auditor_compatible':rules.get('buyer_compatible')}))


if __name__=='__main__':main()
