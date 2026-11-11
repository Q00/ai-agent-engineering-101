"""Bounded forum observations and evidence-backed, expiring public messages.

Peer text is untrusted data. It cannot change policy, authenticate a buyer,
prove a payment, or relax a checkout constraint.
"""
import math
import re
import time
from .state import identity

HORIZON = 21600
MAX_OBSERVATIONS = 1500


def ingest(journal, now=None):
    now = time.time() if now is None else now
    catalog = journal.snapshot('catalog.json', {})
    aliases = {k.casefold(): k for k in catalog}
    categories = {p.get('category', '') for p in catalog.values()} - {''}
    rows = journal.snapshot('forum.json', [])
    if not isinstance(rows, list):
        return
    for row in rows[:100]:
        if not isinstance(row, dict) or row.get('role') not in {'buyer', 'shop'}:
            continue
        pid, ts = row.get('id'), row.get('ts')
        if (type(pid) not in (str, int) or len(str(pid)) > 100 or type(ts) not in (int, float)
                or not math.isfinite(ts) or not now - HORIZON <= ts <= now + 60):
            continue
        text = str(row.get('text', ''))[:2000]
        skus = list(dict.fromkeys(aliases[s.casefold()] for s in re.findall(r'\bG[0-9a-z]+\b', text, re.I)
                                 if s.casefold() in aliases))[:10]
        cats = {catalog[s]['category'] for s in skus}
        if not cats:
            cats = {c for c in categories if len(c.split('>')[-1]) >= 2 and
                    c.split('>')[-1].casefold() in text.casefold()}
        author = row.get('author', '')
        if isinstance(author, dict):
            author = author.get('name', author.get('login', ''))
        if not isinstance(author, str) or not author or author.casefold() in {'seochanit', 'seochanit shop'}:
            continue
        wanted = row['role'] == 'buyer' and bool(re.search(r'\b(want|wanted|need|looking for)\b|구매 희망|구합니다|찾습니다|필요', text, re.I))
        journal.add('forum_observation', {'post': pid, 'author': author[:100], 'role': row['role'],
                    'skus': skus, 'categories': sorted(cats)[:5], 'wanted': wanted,
                    'evidence': 'unverified public request or announcement'},
                    eid='forum-observation:' + str(pid), ts=ts)
    with journal.connect() as connection:
        connection.execute("DELETE FROM events WHERE kind='forum_observation' AND ts<?", (now - HORIZON,))
        connection.execute("DELETE FROM events WHERE rowid IN (SELECT rowid FROM events WHERE kind='forum_observation' "
                           "ORDER BY ts DESC,rowid DESC LIMIT -1 OFFSET ?)", (MAX_OBSERVATIONS,))


def signals(journal, now=None):
    now = time.time() if now is None else now
    categories, skus, seen = {}, {}, set()
    for row in journal.events('forum_observation', MAX_OBSERVATIONS):
        if not row.get('wanted') or not 0 <= now - row['_ts'] <= HORIZON:
            continue
        decay = math.exp(-(now - row['_ts']) / 3600)
        for category in row['categories']:
            key = (row['author'], category)
            if key not in seen:
                categories[category] = categories.get(category, 0) + .35 * decay
                seen.add(key)
        for sku in row['skus']:
            key = (row['author'], sku)
            if key not in seen:
                skus[sku] = skus.get(sku, 0) + .35 * decay
                seen.add(key)
    return {'categories': categories, 'skus': skus, 'note': 'weak, author-deduplicated requests; not settled demand'}


def shop_hint(journal, name, sku, now=None):
    now = time.time() if now is None else now
    from .decision_view import history
    return any(r['role'] == 'shop' and r['author'] == name and sku in r['skus'] and
               0 <= now - r['_ts'] < 1800 for r in history(journal, 'forum_observation', MAX_OBSERVATIONS))


def queue_wanted(journal, order, now=None):
    now = time.time() if now is None else now
    skus = [s for s in (order.get('acceptable') or [])[:3] if isinstance(s, str)]
    if not skus or order['expires'] - now < 60 or now - journal.get('forum_last_wanted', 0) < 1800:
        return
    # Never publish the buyer's private budget or imply that a request is a sale.
    text = 'Wanted: ' + ', '.join(skus) + '; category=' + str(order.get('category', order.get('item', '')))[:100]
    text += '; certificate required; this is an open request, not a reservation.'
    if order.get('kind') == 'food':
        text += f" Minimum shelf life after checkout: {order.get('use_within_hours', 0)} hours."
    journal.queue_post({'post': text, 'as': 'buyer', 'refs': [], 'priority': 2,
                        'expires_at': order['expires'] - 30}, 'wanted:' + str(order['order']))
    journal.put('forum_last_wanted', now)


def schedule(journal, config, now=None):
    now = time.time() if now is None else now
    inv = journal.snapshot('inventory.json', {})
    if inv.get('round') != 3 and journal.get('board_round') != 3:
        return
    ingest(journal, now)
    # One current inventory announcement / wanted reply per slot, not one per SKU.
    interval = max(300, config.get('forum_post_interval_seconds', 600))
    bucket = int(now // interval)
    catalog = journal.snapshot('catalog.json', {})
    from .market_memory import market_book
    book = market_book(journal, catalog, now)
    from .economy import remaining, grade, shop_candidates
    with journal.connect() as connection:
        reserved = {r[0] for r in connection.execute('SELECT unit FROM reservations WHERE until>?', (now,))}
    units = [u for u in inv.get('units', []) if u.get('unit') not in reserved and remaining(u, now) > 0]
    wanted = [r for r in journal.events('forum_observation', MAX_OBSERVATIONS)
              if r.get('wanted') and 0 <= now - r['_ts'] < 300]
    unit, thread = None, None
    for row in wanted:
        unit = next((u for u in units if u['sku'] in row['skus']), None)
        if unit:
            thread = row['post']
            break
    if unit is None and units:
        urgent = [u for u in units if u.get('kind') == 'food' and remaining(u, now) < 3]
        # The forum is a demand channel: surface valuable observed-demand stock
        # instead of advertising only the oldest cheap food every time.
        unit = (min(urgent, key=lambda u: remaining(u, now)) if urgent else
                max(units, key=lambda u: book.get(u['sku'], {}).get('median', u.get('list_price', 0)*.8) *
                    book.get(u['sku'], {}).get('per_seller_per_hour', .01)))
    if unit and now - journal.get('forum_last_sent:shop', 0) >= interval:
        candidates = shop_candidates(unit, inv, catalog, journal, config, now=now)
        if candidates:
            choice = max(candidates, key=lambda c: c['score'])
            text = (f"seochanit stock: SKU={unit['sku']}; indicative price={choice['price']}; "
                    f"grade={grade(unit, catalog, now)}; certificate; warranty_hours={choice['warranty_hours']}. "
                    'Check current registered shop offers and negotiate over A2A; stock and price are not reserved.')
            if unit.get('expires_at'):
                text += f" Food expires_at={int(unit['expires_at'])}; check your required use hours."
            journal.queue_post({'post': text, 'refs': [], 'as': 'shop', 'thread': thread,
                                'expires_at': min(now + 120, unit.get('expires_at') or now + 120),
                                'inventory_unit': unit['unit'], 'priority': 3 if thread else 1},
                               'stock-announcement:' + str(bucket))
    if config.get('buyer_patch34_enabled'):
        from .buyer_forum import schedule as buyer_schedule
        buyer_schedule(journal,config,book,catalog,now)
        return
    # Paid observations have immutable ledger references; no fabricated generic filler.
    for item in journal.events('purchase', 100):
        if not 0 <= now - item.get('paid_at', item['_ts']) < HORIZON or type(item.get('ledger')) is not int:
            continue
        text = (f"Settled SKU={item['sku']} price={item['price']}; grade={item.get('grade')}; "
                f"warranty_hours={item.get('warranty_hours', 0)}. "
                'One observed purchase, not a guarantee of future quality or availability.')
        journal.queue_post({'post': text, 'refs': [item['ledger']], 'as': 'buyer', 'priority': 3,
                            'expires_at': item.get('paid_at', item['_ts']) + HORIZON},
                           'paid-observation:' + str(item['ledger']))
    # A recent paid purchase can support one more useful, scoped price comparison.
    # Refs are server ledger sequences; prose in other posts is never price data.
    informative = sorted(journal.events('purchase', 100), key=lambda p:
                         book.get(p.get('sku'), {}).get('median', p.get('price', 0)) - p.get('price', 0), reverse=True)
    for item in informative:
        observed = book.get(item.get('sku'), {})
        if (not 0 <= now - item.get('paid_at', item['_ts']) < 1800 or
                type(item.get('ledger')) is not int or observed.get('samples', 0) < 3 or
                not observed.get('refs')):
            continue
        text = (f"SKU={item['sku']}: I paid {item['price']} (grade={item.get('grade')}, "
                f"warranty_hours={item.get('warranty_hours', 0)}). In the locally observed last 6h ledger "
                f"sample, n={observed['samples']}, median={observed['median']:g}, "
                f"range={observed['low']:g}..{observed['high']:g}, seller URLs={observed['sellers']}. "
                'Observed trades, not live quotes; grade, expiry and warranty differ. '
                'References are examples, not the complete sample.')
        refs = list(dict.fromkeys([item['ledger']] + observed['refs']))[:5]
        matched=next((r for r in observed.get('term_prices',[]) if r['grade']==item.get('grade') and
                      r['warranty_hours']==(item.get('warranty_hours') or 0) and r['n']>=3),None)
        if matched:
            text=(f"조건별 시세: {catalog.get(item['sku'],{}).get('name',item['sku'])} ({item['sku']}). "
                  f"내 결제 {item['price']}; 같은 등급 {matched['grade']}, 보증 {matched['warranty_hours']}h의 "
                  f"최근 관측 n={matched['n']}, median={matched['median']:g}. "
                  '과거 정산 표본이며 현재 호가·재고 또는 미래 품질 보장이 아닙니다.')
            refs=list(dict.fromkeys([item['ledger']]+matched['refs']))[:5]
        if bucket%3==1:
            product=catalog.get(item['sku'],{})
            alternatives=[(sku,row) for sku,row in book.items() if sku!=item['sku'] and
                          catalog.get(sku,{}).get('category')==product.get('category') and
                          .5*product.get('list_price',0)<=catalog.get(sku,{}).get('list_price',0)<=1.5*product.get('list_price',0)
                          and row.get('external_samples',0)>=3 and row.get('refs')]
            if alternatives:
                sku,row=max(alternatives,key=lambda x:catalog[x[0]]['list_price']-x[1]['median'])
                text+=(f" 대체 상품 관측: {catalog[sku].get('name',sku)} ({sku}), n={row['samples']}, "
                       f"median={row['median']:g}. 주문 acceptable·등급·유통기한·보증은 별도 확인해야 합니다.")
                refs=list(dict.fromkeys(refs+row['refs']))[:5]
        journal.queue_post({'post': text, 'refs': refs, 'as': 'buyer', 'priority': 4,
                            'expires_at': now + 600}, 'price-comparison:' + str(bucket))
        break
    if bucket%3==2:
        from .risk import quality_book
        grades=quality_book(journal,now)
        rows={g:r for g,r in grades.items() if r['n']>=20}
        refs=[p['ledger'] for p in journal.events('purchase',1000) if p.get('kind')=='goods' and
              21600<=now-p.get('paid_at',now)<=172800 and type(p.get('ledger')) is int][:5]
        if rows and refs:
            text='내 성숙 구매 표본(최소 6시간 경과): '+ '; '.join(
                f"등급 {g}: {r['n']}건 중 확인 고장 {r['broken']}건, 3h 이내 확인 {r['within3']}건" for g,r in rows.items())
            text+='; 자신의 선택된 표본이며 전체 시장 고장률 또는 미래 품질 보장이 아닙니다. 참조는 예시입니다.'
            journal.queue_post({'post':text,'refs':refs,'as':'buyer','priority':5,'expires_at':now+600},
                               'quality-insight:'+str(bucket))


def deliver(market, journal, config, now=None):
    now = time.time() if now is None else now
    interval = max(300, config.get('forum_post_interval_seconds', 600))
    blocked = set()
    pending = sorted(journal.pending_posts(), key=lambda row: -row[1].get('priority', 0))
    if config.get('buyer_patch34_enabled'):
        retired=[key for key,p in pending if p.get('as','buyer')=='buyer' and
                 not key.startswith(('buyer-insight:','wanted:'))]
        if retired:
            with journal.connect() as connection:
                connection.executemany("UPDATE forum SET status='retired' WHERE id=? AND status='pending'",
                                       [(key,) for key in retired])
            journal.add('forum_queue_retired',{'count':len(retired),'reason':'superseded by patch34 evidence policy'})
            pending=[(key,p) for key,p in pending if key not in set(retired)]
    for key, payload in pending:
        role = payload.get('as', 'buyer')
        role_interval=(max(300,config.get('buyer_forum_post_interval_seconds',300))
                       if role=='buyer' and config.get('buyer_patch34_enabled') else interval)
        if payload.get('expires_at', now + 1) <= now:
            journal.sent_post(key)
            continue
        if role not in market.token or role in blocked or now - journal.get('forum_last_sent:' + role, 0) < role_interval:
            continue
        if payload.get('inventory_unit'):
            units = journal.snapshot('inventory.json', {}).get('units', [])
            if not any(u['unit'] == payload['inventory_unit'] for u in units):
                journal.sent_post(key)
                continue
        body = {'text': payload['post'], 'refs': payload.get('refs', []), 'thread': payload.get('thread')}
        response = market.call('POST', '/api/forum', role, json=body)
        journal.add('forum_delivery', {'key': key, 'status': response.status_code, 'role': role})
        if response.status_code in (200, 201):
            journal.sent_post(key)
            journal.put('forum_last_sent:' + role, now)
            blocked.add(role)
        elif response.status_code == 429 or response.status_code >= 500:
            blocked.add(role)  # A shop throttle must not block an eligible buyer post.
        elif 400 <= response.status_code < 500:
            journal.sent_post(key)
    # Only the operational queue is bounded. Financial/audit events are retained.
    with journal.connect() as connection:
        connection.execute("DELETE FROM forum WHERE rowid IN (SELECT rowid FROM forum ORDER BY rowid DESC LIMIT -1 OFFSET 2000)")
