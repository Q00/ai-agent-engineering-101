"""Round 3 official kit-v4 hooks; no registration, HTTP, or publication."""
import argparse
from collections import Counter, defaultdict
import json
import math
import os
from pathlib import Path
import time
DAY = 86400

def read(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return default

def lines(path):
    if not Path(path).exists():
        return []
    return [json.loads(s) for s in Path(path).read_text(encoding='utf-8').splitlines() if s.strip()]

def write(path, value):
    path = Path(path)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)

def forecast(ledger, now):
    bins, seen = defaultdict(lambda: [0] * 7), set()
    for event in ledger:
        if event.get('kind') != 'settle' or event.get('round',3)!=3 or event.get('seq') in seen:
            continue
        seen.add(event['seq'])
        age = (now - event['ts']) / DAY
        sku = event.get('data', {}).get('sku') or event.get('data', {}).get('item')
        if sku and 0 <= age < 7:
            bins[sku][int(age)] += 1
    weights = [math.exp(-i / 3) for i in range(7)]
    result = {}
    for sku, counts in bins.items():
        rate = sum(w * n for w, n in zip(weights, counts)) / sum(weights)
        sigma = math.sqrt(sum(w * (n-rate)**2 for w,n in zip(weights,counts)) / sum(weights))
        result[sku] = {'rate': rate, 'sigma': sigma}
    return result

def pricing(unit, now, rate=0, competitor=None, failure=.1, warranty_hours=0):
    expiry = unit.get('expires_at')
    if unit.get('grade') == 'expired' or expiry is not None and expiry <= now:
        return None
    cost, listed = unit['cost'], unit['list_price']
    holding = 0 if unit['kind'] == 'food' else cost * .01 * min(7,1/max(rate,.1))
    risk = failure if warranty_hours else 0
    floor = math.ceil((cost+holding)/(1-risk))
    ask = max(floor,min(listed,competitor-1 if competitor else listed))
    if expiry is not None:
        hours = (expiry-now)/3600
        if hours < 6:
            ask = max(1,round(ask*max(.1,hours/6)))
            floor = max(1,round(ask*.8))
    return {'ask':ask,'floor':floor,'warranty_hours':warranty_hours,
            'expected_profit':ask*(1-risk)-cost-holding}

def procurement(inv,catalog,ledger,now,md=None):
    from profit_strategy import replenish
    return replenish(inv,catalog,ledger,now,Path(md) if md is not None else Path(__file__).resolve().parent/'.market')

def review(deal):
    from rating_policy import adjusted_stars
    honest=deal['settled'].get('claim_honest')
    return {'stars':adjusted_stars(2 if honest is False else 5),'review':
            'Settled purchase; grade claim was false.' if honest is False else
            'Payment settled; no unobserved quality or failure claims.'}

def refresh_ledger(md):
    """Called only by a future live hook; public read, resumable and bounded."""
    from urllib.request import urlopen
    memory=read(md/'ledger-cursor.json',{'after':-500})
    for _ in range(4):
        with urlopen('https://market.ourolabs.ai/api/ledger?after='+str(memory['after']),timeout=5) as response:
            rows=json.load(response)
        if not rows:
            break
        # Cursor advances after durable append. Forecast deduplicates a replay.
        with (md/'ledger.jsonl').open('a',encoding='utf-8') as out:
            for row in rows:
                out.write(json.dumps(row,ensure_ascii=False)+'\n')
        memory['after']=rows[-1]['seq']
        write(md/'ledger-cursor.json',memory)
        if len(rows)<500:
            break

def wait_supply_window(md,inventory,clock=None,sleeper=None):
    """Catch an imminent window in the kit's existing background restock job."""
    clock=clock or time.time;sleeper=sleeper or time.sleep
    window=inventory.get('restock',{});opens=window.get('opens_at')
    if window.get('open') or not isinstance(opens,(int,float)):return inventory
    delay=opens-clock()
    # Official kit schedules every 60 s and syncs inventory every 10 s.
    if not -10<=delay<=60:return inventory
    deadline=opens+10
    while clock()<deadline:
        try:fresh=read(Path(md)/'inventory.json',inventory)
        except (ValueError,OSError):fresh=inventory
        current=fresh.get('restock',{})
        if current.get('open'):return fresh
        if current.get('opens_at',opens)!=opens:return fresh
        sleeper(min(.5,max(0,deadline-clock())))
    return read(Path(md)/'inventory.json',inventory)


def restock_hook(state,now=None):
    md=Path(state)
    live_clock=now is None
    now=time.time() if now is None else now
    from strategy_policy import active
    coordinated=active(md)
    if not coordinated:
        from patch36 import refresh_scoreboard
        refresh_scoreboard(md,now)
    inv=read(md/'inventory.json',{})
    if coordinated and live_clock:
        inv=wait_supply_window(md,inv)
        now=time.time()
    from optimizer import append
    append(md/'restock-trace.jsonl',{'at':now,'restock':inv.get('restock'),'balance':inv.get('balance')})
    catalog=read(md/'catalog.json',{})
    if coordinated:
        # The persistent worker computes observations outside the supply window.
        # Never spend a three-minute supply slot rebuilding the public market.
        ledger=[]
    else:
        from shop_profit import ownership_tags
        from ledger_window import window
        ledger=window(md/'ledger.jsonl',now,ownership_tags())
        from high_value_growth import update as growth_update
        growth_update(md,inv,catalog,ledger,now)
    result=procurement(inv,catalog,ledger,now,md)
    if live_clock and inv.get('restock',{}).get('closes_at') is not None:
        # Risk/statistics refresh can consume part of the short supply window.
        # Recheck after all computation, before handing orders to the kit.
        slots=min(10,max(0,math.floor((inv['restock']['closes_at']-time.time()-10)/12)))
        result['buy']=result.get('buy',[])[:slots]
        preview=read(md/'procurement-preview.json',{})
        if preview.get('at')==now:
            from optimizer import atomic
            preview['buy']=result['buy']
            preview.setdefault('limits',{})['window_order_slots']=min(slots,preview.get('limits',{}).get('window_order_slots',10))
            atomic(md/'procurement-preview.json',preview)
        if active(md):
            from strategy_policy import database
            kept={b['sku'] for b in result['buy']}
            with database(md,True) as c:
                for row in c.execute('select id,sku from funds where at=?',(now,)).fetchall():
                    if row['sku'] not in kept:c.execute('delete from funds where id=?',(row['id'],))
    if os.environ.get('ROUND3_FORUM_ENABLED')=='1' and (md/'recommendations-ready.json').exists():
        from recommendations import ensure_worker
        ensure_worker(md)
    return result

def main():
    parser=argparse.ArgumentParser()
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--state',type=Path)
    group.add_argument('--deal',type=Path)
    args=parser.parse_args()
    print(json.dumps(restock_hook(args.state) if args.state else review(read(args.deal,{})),ensure_ascii=False))
if __name__=='__main__':
    main()
