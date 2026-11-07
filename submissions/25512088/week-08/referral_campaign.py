from procurement_campaign import settings
"""Read public market evidence and queue shop recruitment posts through kit v4.

No standalone HTTP publishing: the kit owns authentication and forum rate limits.
Running this worker explicitly queues public posts; importing it does nothing.
"""
import argparse
import base64
import json
import os
import time
from pathlib import Path
from urllib.request import urlopen
from network_reads import retry_read
from cryptography.hazmat.primitives import serialization
from referral_program import Program


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
            # No warranty is offered by referral_seller. Still stop maturity if
            # an unfamiliar refund event appears instead of guessing its schema.
            if 'refund' in str(row.get('kind', '')).lower():
                d = row.get('data', {})
                if d.get('shop') in urls and d.get('unit'):
                    program.refund_unit(d['unit'])
                else:
                    raise ValueError('Unmapped refund event: reconciliation paused')
            cursor = row['seq']
            program.put('cursor', cursor)
    return False  # Backlog remains: never mature using wall-clock time alone.


def campaign_text(program, inventory):
    from seller_strategy import sale_quote
    p=program.p;lines=[f"[{p['shop_name']} · 실제 재고 / 시장 인증서]"]
    held=program.reserved_units()
    for u in inventory.get('units',[]):
        if u.get('unit') in held:continue
        try:q=sale_quote(u,{},program)
        except (KeyError,ValueError):continue
        condition=f"보증 {q['warranty_hours']}h" if q.get('warranty_hours') else '식품 남은 기한 문의'
        lines.append(f"{u['sku']} {u.get('name','')[:24]} · {u['grade']} · {q['price']:,}원 · {condition}")
        if len(lines)==3:break
    lines.append(f"OFFERS로 확인 → SKU 견적 → 숫자로 구매. 직접 추천: 친구 최대 {p['friend_percent']}% 할인·추천인 최대 {p['referrer_percent']}% 크레딧(각 {p['reward_cap']}원 한도·마진 제한·정산 후 6h). 기존 창립단골 혜택은 PROGRAM. 재고·가격 재확인.")
    return '\n'.join(lines)



def queue_post(program, state, inventory, now=None):
    now = time.time() if now is None else now
    held=program.reserved_units()
    available = [u for u in inventory.get('units', []) if u.get('unit') not in held and u.get('grade') != 'expired'
                 and (not u.get('expires_at') or u['expires_at'] > now)]
    if inventory.get('round', 0) < 3 or not available or not program.live(now):
        return False
    if program.db.execute('SELECT COUNT(*) FROM members WHERE founder=1').fetchone()[0] >= program.p['founder_limit']:
        return False
    day=int(now//86400);count=program.get('post_count_'+str(day),0)
    if count >= program.p['forum_max_posts'] or now-program.get('last_post', 0) < program.p['forum_interval_seconds']:
        return False
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
    refs=[r[0] for r in program.db.execute(
        'SELECT ledger FROM sales WHERE ledger IS NOT NULL AND refunded=0 ORDER BY ledger DESC LIMIT 2')]
    row = {'as': 'shop', 'post': campaign_text(program, dict(inventory,units=available)), 'refs': refs}
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
    args = ap.parse_args()
    while True:
        try:
            tick(args.state, args.key, publish=not args.no_post)
        except Exception as exc:
            print('referral worker:', type(exc).__name__, str(exc), flush=True)
        if args.once:
            break
        time.sleep(15)
