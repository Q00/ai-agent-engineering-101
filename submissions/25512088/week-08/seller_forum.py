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


def campaign_text(program, inventory, rotation=0):
    from seller_strategy import sale_quote
    p=program.p;lines=[f"[{p['shop_name']} · 실제 재고 / 시장 인증서]"]
    held=program.reserved_units();seen=set();candidates=[]
    for u in inventory.get('units',[]):
        if u.get('unit') in held:continue
        if u.get('sku') in seen:continue
        seen.add(u.get('sku'));candidates.append(u)
    if candidates:
        offset=(rotation*2)%len(candidates);candidates=candidates[offset:]+candidates[:offset]
    for u in candidates:
        try:q=sale_quote(u,{},program)
        except (KeyError,ValueError):continue
        condition=f"보증 {q['warranty_hours']}h" if q.get('warranty_hours') else '식품 남은 기한 문의'
        lines.append(f"{u['sku']} {u.get('name','')[:24]} · {u['grade']} · {q['price']:,}원 · {condition}")
        if len(lines)==3:break
    lines.append("OFFERS로 현재 재고 확인 → SKU 견적 → 숫자로 구매. 시장 인증서·등급·식품 잔여기한·공산품 보증을 견적에서 확인하세요. 재고·가격은 실시간으로 변합니다.")
    return '\n'.join(lines)



def queue_post(program, state, inventory, now=None):
    now = time.time() if now is None else now
    held=program.reserved_units()
    available = [u for u in inventory.get('units', []) if u.get('unit') not in held and u.get('grade') != 'expired'
                 and (not u.get('expires_at') or u['expires_at'] > now)]
    if inventory.get('round', 0) < 3 or not available:
        return False
    day=int(now//86400);count=program.get('post_count_'+str(day),0)
    if count >= program.p['forum_max_posts'] or now-program.get('last_post', 0) < program.p['forum_interval_seconds']:
        return False
    refs=[r[0] for r in program.db.execute(
        'SELECT ledger FROM sales WHERE ledger IS NOT NULL AND refunded=0 ORDER BY ledger DESC LIMIT 2')]
    strategy={}
    try:strategy=json.loads((Path(state)/'strategy.json').read_text())
    except (OSError,ValueError):pass
    order={r.get('sku'):n for n,r in enumerate(strategy.get('top10',[]))}
    quantities={}
    for unit in available:quantities[unit.get('sku')]=quantities.get(unit.get('sku'),0)+1
    available.sort(key=lambda u:(order.get(u.get('sku'),10**6),-quantities.get(u.get('sku'),0),u.get('sku','')))
    text=campaign_text(program,dict(inventory,units=available),rotation=count)
    guard=Control(state)
    try:
        allowed,_,_=guard.allow_publication('shop',text,refs,now)
    finally:guard.close()
    if not allowed:return False
    # This DB transaction is a process lock. Mark before append: a crash may lose
    # one ad, but cannot cause an endless retry/duplicate-post loop.
    with program.db:
        program.db.execute('BEGIN IMMEDIATE')
        last = program.get('last_post', 0)
        count = program.get('post_count_'+str(day), 0)
        if now-last < program.p['forum_interval_seconds'] or count >= program.p['forum_max_posts']:
            return False
        for k, v in [('last_post', now), ('post_count_'+str(day), count+1)]:
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
