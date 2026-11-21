"""Compare procurement scenarios on one frozen market/inventory cutoff.

SQLite is read-only, journal cache writes are in memory, no models or orders.
"""
import argparse
from datetime import datetime,timezone
import json
import sqlite3
import time
from autonomous_status import ReadJournal
from r3.state import ROOT,read_json
from r3.economy import restock_candidates


def replay(path):
    source=read_json(path,{})
    cutoff=source['journal_cutoff'];files=source['local_files'];cfg=source['policy']
    inv=dict(files['inventory.json'],restock={'open':True,'opens_at':cutoff-1,'closes_at':cutoff+180})
    variants={}
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as conn:
        conn.execute('BEGIN')
        class Frozen(ReadJournal):
            def snapshot(self,name,default=None):return files.get(name,default)
        for name,patch in [('v6',{'shop_velocity_enabled':False}),
            ('v7_terms_legacy_allocation',{'shop_velocity_enabled':True,'shop_velocity_allocation_enabled':False,'restock_max_qty':12}),
            ('v7_velocity',{'shop_velocity_enabled':True,'restock_max_qty':12})]:
            journal=Frozen(conn,cutoff)
            journal.put('merchant_context_v5',{})
            journal.put('market_control',{'pause_restock':False,'capital_fraction':1})
            started=time.monotonic()
            rows=restock_candidates(inv,files['catalog.json'],[],journal,dict(cfg,**patch),cutoff)
            variants[name]={'lines':len(rows),'units':sum(c['qty'] for c in rows),
                'capital_commitment':sum(c['capital_commitment'] for c in rows),
                'book_scenario':sum(c['expected_delta']['book'] for c in rows),
                'seconds':time.monotonic()-started,
                'items':[{'sku':c['sku'],'name':files['catalog.json'][c['sku']]['name'],'qty':c['qty'],
                    'capital':c['capital_commitment'],'book_scenario':c['expected_delta']['book'],
                    'holding_hours':c['evidence']['expected_holding_hours'],
                    'repeat_paid':c['evidence']['growth'].get('positive_paid_while_available',0),
                    'terms':c['evidence'].get('resale_terms_scenario')} for c in rows]}
    result={'source':str(path.relative_to(ROOT)),'NOT_EXECUTED':True,'variants':variants,
        'limitations':['Control is assumed normal for the shadow scenarios.',
            'New grade/warranty price assumptions differ from v6; forecast changes alone are not measured gains.',
            'The two v7 variants share corrected unit economics; allocation choices can be compared on that basis.',
            'No capital is assumed to be reinvested before a real subsequent supply window.']}
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source');args=p.parse_args()
    result=replay(ROOT/args.source)
    path=ROOT/'logs'/('shop-velocity-replay-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('SOURCE',path)
    print(json.dumps({name:{k:v for k,v in row.items() if k!='items'} for name,row in result['variants'].items()}))
