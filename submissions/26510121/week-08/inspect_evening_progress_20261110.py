"""Compact inspection and independent arithmetic for the evening review."""
import json
import sys
from r3.state import ROOT, read_json

r=read_json(ROOT/sys.argv[1])
for comp in r['comparisons']:
    print('COMPARISON',comp['start_kst'],comp['hours'])
    for role in ['shops','buyers','players']:
        own=comp['before'][role]
        print('BEFORE',role,json.dumps(own,ensure_ascii=False))
        print('PEERS',role,json.dumps(comp['peers'][role],ensure_ascii=False))
print('RULES',json.dumps(r['rules']))
print('CONTROL',json.dumps({k:v for k,v in r['control'].items() if k!='cash_capacity'}))
print('CAPACITY',json.dumps({k:v for k,v in (r['control'].get('cash_capacity') or {}).items() if k!='reserve_release_schedule'}))
for key in ['framework','memory','reviews','freshness','last_restock_decisions']:
    value=r[key]
    if key=='memory':
        value={k:v for k,v in value.items() if k!='rating_shadow'}|{'rating_shadow_keys':list(value['rating_shadow']),
                'rating_shadow_summary':{k:v for k,v in value['rating_shadow'].items() if k not in ['accounts','reviewers','groups','observations']}}
    if key=='last_restock_decisions':
        value=[{k:v for k,v in row.items() if k not in ['candidates','lines','plan','options']} for row in value]
    print(key.upper(),json.dumps(value,ensure_ascii=False))
# Recompute endpoint deltas independently from input frozen boards.
current=read_json(ROOT/r['source'])['responses']['/api/board']['data']
morning=read_json(ROOT/r['comparisons'][0]['source'])['responses']['/api/board']['data']
for role,fields in [('shops',['book','revenue','deals']),('buyers',['saved','deals','forum','posts'])]:
    before=next(x for x in morning[role] if x['name']=='seochanit')
    after=next(x for x in current[role] if x['name']=='seochanit')
    print('INDEPENDENT_DELTA',role,json.dumps({f:after[f]-before[f] for f in fields}))
    if role=='shops':
        w=r['shop_windows']['since_morning']
        print('BOOK_RESIDUAL',w['margin_minus_observed_refunds']-(after['book']-before['book']))
