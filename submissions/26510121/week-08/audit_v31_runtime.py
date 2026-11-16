"""Read-only post-deployment contention and worker evidence."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import sqlite3
import statistics
import time
from r3.state import ROOT, read_json


def main(release_path='logs/v31-release-20261116.json'):
    runtime = read_json(ROOT/'.market/runtime.json', {})
    start = runtime.get('started_at', time.time())
    stop = time.time()
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro', uri=True) as db:
        def events(kind):
            return [json.loads(r[0]) for r in db.execute(
                'SELECT data FROM events WHERE kind=? AND ts>=? AND ts<?', (kind,start,stop))]
        def kv(key):
            row = db.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else {}
        counts = {kind:len(events(kind)) for kind in ('shop_settled','purchase','model_usage',
            'cycle_supervisor_error','analytical_role_error','buyer_worker_error','market_control_error')}
        lengths = [r[0] for r in db.execute('SELECT length(data) FROM events WHERE kind=? AND ts>=? AND ts<?',
            ('prediction',start,stop))]
        times = {kind:[r['seconds'] for r in events(kind) if type(r.get('seconds')) in (int,float)]
            for kind in ('shop_fast_execution','buyer_saved_execution')}
        summaries = {kind:{'samples':len(v),'median_seconds':statistics.median(v) if v else None,
                          'max_seconds':max(v) if v else None} for kind,v in times.items()}
        errors = {kind:events(kind)[-5:] for kind in ('buyer_worker_error','cycle_supervisor_error','analytical_role_error')}
        epoch = {key:kv(key) for key in ('shop_revision_epoch','buyer_revision_epoch')}
    release = read_json(ROOT/release_path)
    out = {'at':stop,'runtime':runtime,'window_seconds':stop-start,'counts':counts,
        'pending_observation_files':len(list((ROOT/'.market/telemetry-pending').glob('*.json'))),
        'prediction':{'samples':len(lengths),'mean_characters':sum(lengths)/len(lengths) if lengths else None},
        'numeric_chooser_times':summaries,'error_samples':errors,'financial_epochs':epoch,
        'source_hashes_match':all(hashlib.sha256((ROOT/f).read_bytes()).hexdigest()==h
            for f,h in release['source_hashes'].items()),
        'limits':['Chooser times exclude prior routing/stock preparation and network/payment time.',
                  'Short initial run cannot establish score uplift or long-term error absence.']}
    path = ROOT/'logs'/('v31-runtime-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')
    path.open('x',encoding='utf8').write(json.dumps(out,ensure_ascii=False,indent=2))
    print('SOURCE',path)
    print(json.dumps(out,ensure_ascii=True))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--release',default='logs/v31-release-20261116.json')
    main(parser.parse_args().release)
