"""Cooperatively drain existing identity-bound runtime and watcher for redeploy.

Does not kill unrelated processes, modify keys, clear state or start trading.
"""
from datetime import datetime,timezone
import json
import time
from r3.state import ROOT,read_json
from r3.lifecycle import market_active,request_stop,lease


def watch_active():
    try:
        with lease(ROOT/'.market/watch.lock'):return False
    except RuntimeError:return True


if __name__=='__main__':
    runtime=read_json(ROOT/'.market/runtime.json',{})
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    before={'at':time.time(),'runtime':runtime,'health':read_json(ROOT/'.market/shop-health.json',{}),
            'board':read_json(ROOT/'.market/board.json',{}),'inventory':read_json(ROOT/'.market/inventory.json',{})}
    path=ROOT/'logs'/('autonomous-deploy-before-'+stamp+'.json')
    path.write_text(json.dumps(before,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'.market/watch-stop.request').touch()
    if runtime.get('pid'):request_stop(runtime['pid'])
    deadline=time.monotonic()+55
    while time.monotonic()<deadline:
        if not market_active() and not watch_active():
            print('Existing runtime and watcher released leases. Identity and journal preserved. Source:',path)
            break
        time.sleep(1)
    else:
        raise SystemExit('Old process still holds a lease; no duplicate runtime was launched.')
