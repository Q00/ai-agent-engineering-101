"""Read-only comparison of published stock with the current numerical policy."""
from collections import Counter
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
from r3.state import ROOT,read_json,policy


def main():
    source=sorted((ROOT/'logs').glob('peer-market-source-*.json'))[-1]
    capture=read_json(source);board=capture['responses']['/api/board']['data']
    shops=capture['responses']['/api/shops']['data']['shops'];catalog=read_json(ROOT/'.market/catalog.json')
    leaders=sorted(board['shops'],key=lambda r:r.get('all',0),reverse=True)[:2]
    names=[r['name'] for r in leaders]+['seochanit'];rows=[]
    for name in names:
        b=next(r for r in board['shops'] if r['name']==name);s=next(r for r in shops if r['name']==name)
        stock={k:v for k,v in s['stock'].items() if type(v) is int and v>0};mix=Counter()
        for sku,n in stock.items():mix[catalog.get(sku,{}).get('kind','unknown')]+=n
        total=sum(stock.values())
        rows.append({'name':name,'all':b['all'],'book':b['book'],'revenue':b['revenue'],
            'stock_units_board':b.get('stock_units'),'stock_units_registry':total,'stock_skus':len(stock),
            'units_per_sku':total/len(stock),'stock_cost':b['stock_cost'],
            'stock_cost_to_book':b['stock_cost']/b['book'],'units_by_kind':dict(mix),
            'ranked_stars':b.get('stars_rank'),'top_stock':[
                {'sku':sku,'qty':n,'name':catalog.get(sku,{}).get('name'),'kind':catalog.get(sku,{}).get('kind')}
                for sku,n in sorted(stock.items(),key=lambda p:p[1],reverse=True)[:6]]})
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN')
        def kv(key):
            row=db.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone();return json.loads(row[0]) if row else {}
        research=kv('research-last-result');context=kv('objective-decision-context')
        feedback=kv('feedback-last-policy');rules=kv('market_rules')
        local={'research_policy':research.get('operating_policy'),'policy_at':research.get('at'),
            'shop_operating_patches':feedback.get('patches',{}).get('shop'),
            'numerical_alternatives':context.get('shop',{}).get('alternatives'),
            'cash_capacity':kv('market_control').get('cash_capacity'),'rules_hash':rules.get('sha256')}
    cfg=policy();inv=read_json(ROOT/'.market/inventory.json')
    result={'observed_at':capture['checked_at'],'source':str(source.relative_to(ROOT)),'shops':rows,
        'own_local_cash':inv.get('balance'),'current_policy':local,
        'optional_inventory_caps_enabled':cfg['shop_inventory_caps_enabled'],
        'optional_capital_caps_enabled':cfg['shop_internal_capital_caps_enabled'],
        'planning_horizon_hours':cfg.get('planning_horizon_hours'),
        'limits':['Stock/cost are independently timed public snapshots; 1-unit discrepancies can be intervening sales.',
            'Stock-cost/Book is an asset composition ratio, not a return or causal effect of inventory.',
            'Public quantities do not reveal unit grade, remaining life, acquisition cost or profitable sell-through.',
            'Published cumulative revenue is not current turnover; numerical alternatives are scenarios, not observed uplift.']}
    dest=ROOT/'logs'/('strategic-stock-comparison-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    dest.open('x',encoding='utf-8').write(json.dumps(result,ensure_ascii=False,indent=2));print('SOURCE',dest)
    print(json.dumps(result,ensure_ascii=True))


if __name__=='__main__':main()
