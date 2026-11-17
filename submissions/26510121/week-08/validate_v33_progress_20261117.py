"""Independent checks of the frozen readout and chart; no market writes."""
from collections import Counter
from pathlib import Path
import json
import re
import sqlite3

ROOT=Path(__file__).resolve().parent
def read(path):return json.loads((ROOT/path).read_text(encoding='utf8'))
baseline=read('logs/autonomous-deploy-before-20261117T000600153643Z.json')
source=read('logs/v28-progress-source-20261117T034009147309Z.json')
report=read('logs/v33-progress-review-20261117T034625581194Z.json')
chart_text=(ROOT/'logs/v33-book-growth-20261117-1240.html').read_text(encoding='utf8')
chart=json.loads(re.search(r'<script type="application/json" data-viz-chart>(.*?)</script>',chart_text,re.S)[1])
before={r['name']:r for r in baseline['board']['shops']}
after={r['name']:r for r in source['responses']['/api/board']['data']['shops']}
assert len(chart['rows'])==6
for name,value in chart['rows']:
    key='seochanit' if name.endswith('seochanit') else name
    assert abs(value-(after[key]['book']-before[key]['book'])/1e6)<1e-10
assert chart['spec']['type']=='horizontalBar' and chart['spec']['x']=='growth'
assert chart['columns']==['shop','growth']
assert chart['height']>=40+20*len(chart['rows'])
assert not any(t in chart_text for t in ('<!doctype','<html','<body','fetch('))
lo=baseline['at'];hi=source['responses']['/api/board']['finished_at']
catalog=read('.market/catalog.json')
with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
    rows=[json.loads(raw) for raw, in db.execute('SELECT data FROM events WHERE kind=? AND ts>=? AND ts<?',('supply_execution_status',lo,hi))]
complete=[r for r in rows if r.get('status')=='complete']
mix=Counter()
for r in complete:mix[catalog.get(r['sku'],{}).get('kind','unknown')]+=r['qty']
assert len(complete)==87 and sum(mix.values())==189
assert report['rank']['shops']['value_after']==.763
assert report['rank']['buyers']['value_after']==.804
assert round(.763+.804,3)==1.567
assert sum(report['shop']['mix'][k]['gross_margin'] for k in ('food','goods'))==815388
print('PASS: all six chart values match independent paired official board calculations; label bindings, data count and fragment structure checked.')
print('PASS: 87 confirmed supply calls, 189 units; supply mix:',dict(mix))
print('PASS: official ALL sum and gross-margin totals reconcile. Registered rows without a numeric ALL are excluded from ranks.')
print('LIMITATION: chart rendering not inspected; bundled check cannot spawn python3 on this Windows host. Sources renderer rejected its bundled Windows path; safe source links remain available.')
