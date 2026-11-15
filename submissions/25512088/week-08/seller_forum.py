from seller_procurement import settings
"""Read public market evidence and queue shop recruitment posts through kit v4.

No standalone HTTP publishing: the kit owns authentication and forum rate limits.
Running this worker explicitly queues public posts; importing it does nothing.
"""
import argparse
import base64
import json
import os
import sqlite3
import time
from pathlib import Path
from urllib.request import urlopen
from network_reads import retry_read
from cryptography.hazmat.primitives import serialization
from seller_ledger import Program
from adaptive_control import Control

SELLER_FORUM_INTERVAL_SECONDS = 4 * 60 * 60
SELLER_FORUM_MAX_POSTS = 6
SELLER_FORUM_POLICY = 'c-liquidation-v2'
SELLER_FORUM_GUARD_ROLE = 'shop:c-liquidation-v2'
MIN_PROMOTED_FOOD_HOURS = 6


def fetch(base, path):
    def read():
        with urlopen(base + path, timeout=20) as response:
            return json.load(response)
    return retry_read(read)


def shop_urls(program, key_path):
    key = serialization.load_pem_private_key(Path(key_path).read_bytes(), password=None)
    pub = base64.b64encode(key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    data = fetch(program.p['market_url'], '/api/shops')
    current = [s['url'] for s in data['shops'] if s.get('pubkey') == pub and s.get('name') == program.p['shop_name']]
    # Keep old tunnel URLs to reconcile sales after a reconnect.
    urls = sorted(set(current + program.get('shop_urls', [])))
    program.put('shop_urls', urls)
    return urls


def sync(program, urls):
    cursor = program.get('cursor')
    if cursor is None:
        if program.db.execute('SELECT COUNT(*) FROM sales').fetchone()[0]:
            raise RuntimeError('Missing ledger cursor with sales present: explicit historical recovery required')
        rows = fetch(program.p['market_url'], '/api/ledger?after=-1')
        program.put('cursor', max((r['seq'] for r in rows), default=0))
        return False
    for _ in range(30):
        rows = fetch(program.p['market_url'], '/api/ledger?after=' + str(cursor))
        if not rows:
            program.put('synced_at', time.time())
            program.reconcile(time.time())
            return True
        for row in rows:
            if type(row.get('seq')) is not int or row['seq'] <= cursor:
                raise ValueError('Market ledger is not ordered')
            program.settle(row, urls)
            program.review(row, urls)
            program.release_failed(row, urls)
            # Stop maturity on unfamiliar refund events instead of guessing.
            if 'refund' in str(row.get('kind', '')).lower():
                d = row.get('data', {})
                if d.get('shop') in urls and d.get('unit'):
                    program.refund_unit(d['unit'])
                else:
                    raise ValueError('Unmapped refund event: reconciliation paused')
            cursor = row['seq']
        # All row handlers are idempotent. Persisting once per fetched page
        # avoids hundreds of SQLite commits for unrelated market events while
        # retaining crash safety: an interrupted page is simply replayed.
        program.put('cursor', cursor)
    return False  # Backlog remains: never mature using wall-clock time alone.


def recover_failed_sales(program, urls, state):
    """Replay narrow public-ledger windows around unresolved signed receipts."""
    pending=program.db.execute('''SELECT s.task,g.ts FROM sales s JOIN growth_events g
        ON g.task=s.task AND g.event='quote' WHERE s.ledger IS NULL ORDER BY g.ts''').fetchall()
    intel_path=Path(state)/'market_intel.sqlite3'
    if not pending or not intel_path.exists():return {'pending':len(pending),'rows':0,'released':0}
    groups={}
    for task,ts in pending:groups.setdefault(int(ts//120),[]).append((task,ts))
    seen=set();scanned=0;released=0
    with sqlite3.connect('file:'+str(intel_path.resolve())+'?mode=ro',uri=True) as intel:
        for group in groups.values():
            lo=min(ts for _,ts in group)-60;hi=max(ts for _,ts in group)+600
            anchor=intel.execute('SELECT MAX(seq) FROM trades WHERE ts<=?',(lo,)).fetchone()[0]
            cursor=max(-1,(anchor or 0)-25)
            for _ in range(5):
                rows=fetch(program.p['market_url'],'/api/ledger?after='+str(cursor))
                if not rows:break
                for row in rows:
                    seq=row.get('seq')
                    if type(seq) is int and seq not in seen:
                        seen.add(seq);scanned+=1
                        released+=program.release_failed(row,urls)
                cursor=max(r['seq'] for r in rows if type(r.get('seq')) is int)
                if len(rows)<500 or max((r.get('ts',0) for r in rows),default=0)>hi:break
    return {'pending':len(pending),'rows':scanned,'released':released}


def campaign_text(program, inventory, rotation=0, now=None):
    from seller_strategy import sale_quote
    now=time.time() if now is None else now
    p=program.p;units=inventory.get('units',[])
    promoting_c=any(u.get('grade')=='C' for u in units)
    title='C등급 실재고 할인' if promoting_c else '실재고 / 시장 인증서'
    lines=[f"[{p['shop_name']} · {title}]"]
    held=program.reserved_units();seen=set();candidates=[]
    quantities={}
    for u in units:quantities[u.get('sku')]=quantities.get(u.get('sku'),0)+1
    for u in units:
        if u.get('unit') in held:continue
        if u.get('sku') in seen:continue
        seen.add(u.get('sku'));candidates.append(u)
    if candidates:
        c=[u for u in candidates if u.get('grade')=='C'];other=[u for u in candidates if u.get('grade')!='C']
        if c:
            offset=(rotation*2)%len(c);c=c[offset:]+c[:offset]
        candidates=c+other
    for u in candidates:
        try:q=sale_quote(u,{},program)
        except (KeyError,ValueError):continue
        listed=u.get('list_price') or q.get('base') or 0
        discount=round((1-q['price']/listed)*100) if listed else None
        if u.get('expires_at'):
            condition=f"남은 기한 {max(0,int((u['expires_at']-now)/3600))}h"
        else:condition=f"보증 {q['warranty_hours']}h" if q.get('warranty_hours') else '보증 없음'
        saving=f" · 정가 대비 {discount}% 할인" if discount is not None else ''
        lines.append(f"{u['sku']} {u.get('name','')[:24]} · {u['grade']} · {q['price']:,}원{saving} · {condition} · 재고 {quantities.get(u.get('sku'),1)}")
        if len(lines)==3:break
    lines.append("첨부 원장은 실제 정산 근거입니다. OFFERS → SKU 견적 → 숫자로 구매. 시장 인증서 제공, 재고·가격은 실시간 변동됩니다.")
    return '\n'.join(lines)



def queue_post(program, state, inventory, now=None):
    now = time.time() if now is None else now
    held=program.reserved_units()
    available = [u for u in inventory.get('units', []) if u.get('unit') not in held and u.get('grade') != 'expired'
                 and (not u.get('expires_at') or u['expires_at'] > now)]
    available = [u for u in available if not u.get('expires_at')
                 or u['expires_at']-now >= MIN_PROMOTED_FOOD_HOURS*3600]
    if inventory.get('round', 0) < 3 or not available:
        return False
    day=int((now+9*3600)//86400)
    count_key=SELLER_FORUM_POLICY+'_post_count_'+str(day)
    last_key=SELLER_FORUM_POLICY+'_last_post'
    count=program.get(count_key,0);last_post=program.get(last_key)
    if count >= SELLER_FORUM_MAX_POSTS or (last_post is not None and now-last_post < SELLER_FORUM_INTERVAL_SECONDS):
        return False
    refs=[r[0] for r in program.db.execute(
        'SELECT ledger FROM sales WHERE ledger IS NOT NULL AND refunded=0 ORDER BY ledger DESC LIMIT 2')]
    strategy={}
    try:strategy=json.loads((Path(state)/'strategy.json').read_text())
    except (OSError,ValueError):pass
    order={r.get('sku'):n for n,r in enumerate(strategy.get('top10',[]))}
    quantities={}
    for unit in available:quantities[unit.get('sku')]=quantities.get(unit.get('sku'),0)+1
    # Seller forum is an acquisition channel, not a score-posting channel.
    # Prefer C liquidation with observed demand; do not burn six posts in the morning.
    available.sort(key=lambda u:(u.get('grade')!='C',order.get(u.get('sku'),10**6),
                                 u.get('expires_at') or float('inf'),-quantities.get(u.get('sku'),0),u.get('sku','')))
    text=campaign_text(program,dict(inventory,units=available),rotation=count,now=now)
    guard=Control(state)
    try:
        allowed,_,_=guard.allow_publication(SELLER_FORUM_GUARD_ROLE,text,refs,now)
    finally:guard.close()
    if not allowed:return False
    # This DB transaction is a process lock. Mark before append: a crash may lose
    # one ad, but cannot cause an endless retry/duplicate-post loop.
    with program.db:
        program.db.execute('BEGIN IMMEDIATE')
        last = program.get(last_key)
        count = program.get(count_key, 0)
        if (last is not None and now-last < SELLER_FORUM_INTERVAL_SECONDS) or count >= SELLER_FORUM_MAX_POSTS:
            return False
        for k, v in [(last_key, now), (count_key, count+1)]:
            program.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (k, json.dumps(v)))
    row = {'as': 'shop', 'post': text, 'refs': refs}
    fd = os.open(Path(state) / 'outbox.jsonl', os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    try:
        os.write(fd, (json.dumps(row, ensure_ascii=False)+'\n').encode())
    finally:
        os.close(fd)
    return True


def tick(state, key_path, publish=True):
    program = Program(state)
    try:
        urls = shop_urls(program, key_path)
        if not urls:
            return  # Registration not confirmed: no campaign claims yet.
        sync(program, urls)
        path = Path(state) / 'forum.json'
        if path.exists():
            posts = json.loads(path.read_text())
            program.forum(posts if isinstance(posts, list) else posts.get('posts', []))
        # Refresh the structured preference artifact at most once per minute.
        if time.time()-program.get('preferences_updated_at',0)>=60:
            from market_intel import Intel
            from seller_market import write_buyer_preferences
            intel=Intel(state)
            try:
                catalog_path=Path(state)/'catalog.json'
                if catalog_path.exists():write_buyer_preferences(state,intel,json.loads(catalog_path.read_text()))
                program.put('preferences_updated_at',time.time())
            finally:intel.close()
        if publish and program.get('synced_at', 0) > time.time()-60:
            inv = Path(state) / 'inventory.json'
            if inv.exists():
                queue_post(program, state, json.loads(inv.read_text()))
    finally:
        program.close()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--state', default='.market')
    ap.add_argument('--key', default='shop_key.pem')
    ap.add_argument('--once', action='store_true')
    ap.add_argument('--no-post', action='store_true')
    ap.add_argument('--recover-failed', action='store_true')
    args = ap.parse_args()
    if args.recover_failed:
        p=Program(args.state)
        try:print(json.dumps(recover_failed_sales(p,shop_urls(p,args.key),args.state),sort_keys=True))
        finally:p.close()
        raise SystemExit(0)
    while True:
        try:
            tick(args.state, args.key, publish=not args.no_post)
        except Exception as exc:
            print('referral worker:', type(exc).__name__, str(exc), flush=True)
        if args.once:
            break
        time.sleep(15)
