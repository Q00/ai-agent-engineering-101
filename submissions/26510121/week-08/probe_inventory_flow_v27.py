"""Check whether a row-limited feed actually covers the assumed six hours."""
from datetime import datetime,timezone
import json
import sqlite3
import time
from r3.state import ROOT,read_json

now=time.time();queries={
    'limited':'SELECT count(*),min(ts),max(ts) FROM (SELECT ts FROM events WHERE kind=? AND ts<=? ORDER BY ts DESC,rowid DESC LIMIT 20000)',
    'full':'SELECT count(*),min(ts),max(ts),count(DISTINCT coalesce(json_extract(data,\'$.sku\'),json_extract(data,\'$.item\'))) FROM events WHERE kind=? AND ts>=? AND ts<=?'}
with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
    db.execute('BEGIN')
    limited=db.execute(queries['limited'],('public_settle',now)).fetchone()
    full=db.execute(queries['full'],('public_settle',now-21600,now)).fetchone()
result={'at':now,'queries':queries,'limited':limited,'six_hours':full,'limited_span_hours':(limited[2]-limited[1])/3600}
p=ROOT/'logs'/('v27-flow-coverage-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
p.open('x',encoding='utf-8').write(json.dumps(result,indent=2));print('SOURCE',p);print(json.dumps(result))
