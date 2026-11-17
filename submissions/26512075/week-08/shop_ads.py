"""Local seller-owned ads. No network publishing or market credentials here."""
from contextlib import contextmanager, closing
from datetime import datetime, timezone, timedelta
import hashlib
import forum_delivery
import json
import socket
import sqlite3
import time
import math
from pathlib import Path

SOCKET = 'seller-ads.sock'


def digest(entry):
    return hashlib.sha256(json.dumps({k:v for k,v in entry.items() if k != 'id'},
        ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def legacy_post_text(ad):
    stamp = lambda t: datetime.fromtimestamp(t, timezone.utc).astimezone().isoformat(timespec='seconds')
    return (f"오늘의 생활템\n{ad['name']} · {ad['price']:,} 크레딧 · 재고 1개\n"
            f"등급 {ad['grade']} · 보증 없음(0시간) · SKU {ad['sku']}\n"
            f"확인 {stamp(ad['checked_at'])} · 가격 적용 종료 {stamp(ad['expires_at'])}\n"
            "고정 광고가 · 판매 완료 시 품절")


def ledger_reference(state, market, sku, grade, seq=None, now=None):
    """Read an observed official settlement with matching terms, never a quote."""
    if seq is not None and (type(seq) is not int or seq <= 0):
        raise ValueError("positive ledger sequence required")
    now = time.time() if now is None else now
    path = Path(state) / 'forum-analysis.sqlite3'
    if not path.exists():
        if seq is not None:
            raise ValueError("ledger database unavailable")
        return None
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        source = db.execute("SELECT value FROM meta WHERE key='market'").fetchone()
        if not source or source[0] != market:
            if seq is not None:
                raise ValueError("ledger market mismatch")
            return None
        query = """SELECT seq, amount, ts FROM settlements
                   WHERE sku=? AND grade=? AND warranty=0 AND amount>0 AND ts<=?"""
        params = [sku, grade, now]
        if seq is not None:
            query += ' AND seq=?'
            params.append(seq)
        row = db.execute(query + ' ORDER BY ts DESC, seq DESC LIMIT 1', params).fetchone()
    if row is None:
        if seq is not None:
            raise ValueError("ledger must match SKU, grade and explicit zero warranty")
        return None
    ledger, price, stamp = row
    if (type(ledger) is not int or ledger <= 0 or type(price) is not int or price <= 0
            or type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp <= 0):
        raise ValueError("invalid settlement evidence")
    return {'seq': ledger, 'sku': sku, 'grade': grade, 'warranty_hours': 0,
            'price': price, 'settled_at': stamp}


def validate_reference(state, ad, now=None):
    version = ad.get('format_version', 1)
    if version == 1:
        if ad.get('refs') != [] or ad.get('reference') is not None:
            raise ValueError("legacy advertisement cannot contain ledger references")
        return
    if version not in (2, 3):
        raise ValueError("unsupported advertisement format")
    if version == 3:
        for key, value in limited_offer_fields(ad).items():
            if ad.get(key) != value:
                raise ValueError(f"limited offer field changed: {key}")
    reference = ad.get('reference')
    if reference is None:
        if ad.get('refs') != []:
            raise ValueError("unverified ledger references")
        return
    if not isinstance(reference, dict):
        raise ValueError("invalid ledger reference")
    seq = reference.get('seq')
    if type(seq) is not int or seq <= 0 or ad.get('refs') != [seq]:
        raise ValueError("ledger references disagree")
    actual = ledger_reference(state, ad['market'], ad['sku'], ad['grade'], seq, now)
    if actual != reference:
        raise ValueError("settlement evidence changed")


def limited_offer_fields(ad):
    return {
        'type': 'limited_offer', 'currency': 'credits', 'stock': ad['qty'],
        'valid_until': datetime.fromtimestamp(ad['expires_at'], timezone(timedelta(hours=9))).isoformat(timespec='seconds'),
        'condition': f"{ad['grade']}등급 + 보증 없음 조건에만 적용",
        'order_with': f"I want to buy SKU {ad['sku']} at {ad['price']}",
    }


def offer_json_block(ad):
    fields = limited_offer_fields(ad)
    offer = {"type": "limited_offer", "sku": ad['sku'], "name": ad['name'],
             "grade": ad['grade'], "price": ad['price'], "currency": "credits",
             "stock": ad['qty'], "warranty_hours": 0,
             "valid_until": fields['valid_until'], "order_with": fields['order_with']}
    return "\n\n구매 에이전트용 상품 정보\n```json\n" + json.dumps(offer, ensure_ascii=False, indent=2) + "\n```"


def limited_post_text(ad):
    fields = limited_offer_fields(ad)
    deadline = datetime.fromisoformat(fields['valid_until']).strftime('%Y-%m-%d %H:%M:%S KST')
    if ad.get('copy_style') in ('friendly_bc_v1', 'friendly_bc_v2') and ad['grade'] in ('B', 'C'):
        text = (f"[조건 한정] 알뜰템 구경하고 가세용 🛍️\n"
                f"{ad['name']}\n"
                f"{ad['grade']}등급 · 보증 0h · {ad['price']:,} 크레딧 · 재고 {ad['qty']}개\n\n"
                "장바구니에 쏙, 필요한 분께 딱! 👀\n"
                f"실제 {ad['grade']}등급 상품이며, 보증은 없어요(0시간).\n"
                f"가격은 {deadline}까지, 결제 완료 시 판매 종료예요.\n\n"
                f"마음에 드시면 이렇게 말해주세용 💬\n{fields['order_with']}\n"
                f"판매자의 가격 확인 후 {ad['price']}을 보내 수락해주세요.\n"
                f"SKU {ad['sku']}")
        if ad['copy_style'] == 'friendly_bc_v2':
            text += offer_json_block(ad)
        return text
    text = (f"[조건 한정] SKU {ad['sku']} | {ad['grade']}등급 · 보증 0h | "
            f"{ad['price']:,} 크레딧 | ~{deadline}\n"
            f"상품: {ad['name']} · 재고 {ad['qty']}개\n\n"
            f"{fields['condition']}됩니다.\n"
            f"구매 요청: {fields['order_with']}\n"
            f"판매자의 가격 확인 후 {ad['price']}을 보내 수락해주세요.\n"
            "결제 완료 시 판매 종료합니다.")
    ref = ad.get('reference')
    if ref:
        stamp = datetime.fromtimestamp(ref['settled_at'], timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S KST')
        text += (f"\n\n거래 참고 (과거 정산): 같은 SKU·{ref['grade']}등급·보증 0h\n"
                 f"{ref['price']:,} 크레딧 — 원장 #{ref['seq']} · {stamp}")
    if ad.get('copy_style') == 'offer_json_v1':
        text += offer_json_block(ad)
    return text


def post_text(ad):
    if ad.get('format_version', 1) == 1:
        return legacy_post_text(ad)
    if ad.get('format_version') == 3:
        return limited_post_text(ad)
    if ad.get('format_version') != 2:
        raise ValueError("unsupported advertisement format")
    # Explicit timezone keeps approval text stable across host timezone changes.
    from datetime import timedelta
    stamp = lambda t: datetime.fromtimestamp(t, timezone(timedelta(hours=9))).strftime('%Y-%m-%d %H:%M:%S KST')
    text = (f"[재고·가격 공개]\n상품: {ad['name']}\nSKU: {ad['sku']}\n"
            f"현재 판매가: {ad['price']:,} 크레딧\n"
            f"현재 재고: 1개 · 등급 {ad['grade']} · 보증 없음(0시간)\n"
            f"재고 확인: {stamp(ad['checked_at'])}\n가격 유효: {stamp(ad['expires_at'])}\n")
    ref = ad.get('reference')
    if ref:
        text += (f"\n거래 참고 (과거 정산):\n같은 SKU·{ref['grade']}등급·보증 없음 거래\n"
                 f"{ref['price']:,} 크레딧에 정산 — 원장 #{ref['seq']}\n"
                 f"정산 시각: {stamp(ref['settled_at'])}\n")
    return text + "\n구매하려면 SKU와 위 판매가격을 제시해주세요.\n판매 완료 시 품절입니다."


@contextmanager
def database(state):
    db = sqlite3.connect(Path(state) / 'shop-ads.sqlite3', timeout=3)
    db.row_factory = sqlite3.Row
    db.executescript('''
      CREATE TABLE IF NOT EXISTS ads (id TEXT PRIMARY KEY, entry TEXT NOT NULL, active_at REAL NOT NULL);
      CREATE TABLE IF NOT EXISTS receipts (unit TEXT PRIMARY KEY, ad_id TEXT NOT NULL,
        task TEXT NOT NULL, sku TEXT NOT NULL, price INTEGER NOT NULL, receipt TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS conversions (ledger INTEGER PRIMARY KEY, ad_id TEXT NOT NULL,
        unit TEXT NOT NULL, sku TEXT NOT NULL, price REAL, cost REAL, storage_estimate REAL,
        refund REAL, settled_at REAL);
    ''')
    try:
        yield db
        db.commit()
    finally:
        db.close()


def active_ads(state):
    with database(state) as db:
        return [json.loads(r['entry']) for r in db.execute('SELECT entry FROM ads ORDER BY active_at DESC')]


def issued_units(state):
    with database(state) as db:
        return {r['unit'] for r in db.execute('SELECT unit FROM receipts')}


def issued(state, ad, task, receipt):
    with database(state) as db:
        db.execute('INSERT INTO receipts VALUES (?,?,?,?,?,?)',
            (ad['unit'], ad['id'], task, ad['sku'], ad['price'], json.dumps(receipt)))


def record_settlement(state, completed, unit, sku, price, cost, storage, refund, now):
    if not (Path(state) / 'shop-ads.sqlite3').exists():
        return
    with database(state) as db:
        row = db.execute('SELECT * FROM receipts WHERE unit=?', (unit,)).fetchone()
        if row is None:
            return
        if (row['sku'], row['price']) != (sku, price):
            raise ValueError('advertised receipt and settlement disagree')
        ledger = completed['order']['mandate_receipt']['ledger']
        db.execute('INSERT OR IGNORE INTO conversions VALUES (?,?,?,?,?,?,?,?,?)',
            (ledger, row['ad_id'], unit, sku, price, cost, storage, refund, now))


def effects(state):
    if not (Path(state) / 'shop-ads.sqlite3').exists():
        return []
    path = Path(state) / 'forum-delivery.jsonl'
    history = [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []
    history = forum_delivery.resolved(state, history)
    with database(state) as db:
        result = []
        for row in db.execute('SELECT * FROM conversions'):
            item = dict(row)
            deliveries = [p for p in history if p.get('as') == 'shop' and p.get('id') == item['ad_id']]
            last = deliveries[-1] if deliveries else {}
            item.update(post_id=last.get('post_id'), sent_at=last.get('sent_at'),
                        delivery_state=last.get('state', 'unconfirmed'), advertised_price_applied=True,
                        gross_spread=item['price']-item['cost'],
                        estimated_net_margin=(item['price']-item['cost']-item['storage_estimate']-item['refund']
                            if all(item[k] is not None for k in ('cost','storage_estimate','refund')) else None),
                        confirmed_net_margin=None)
            result.append(item)
        return result


def request(state, action, **payload):
    """Private Unix socket; never expose an admin route through the kit tunnel."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(5)
        client.connect(str((Path(state) / SOCKET).resolve()))
        client.sendall(json.dumps({'action':action, **payload}, allow_nan=False).encode()+b'\n')
        with client.makefile('rb') as stream:
            raw = stream.readline(65537)
        if len(raw) > 65536:
            raise ValueError('seller response too large')
        reply = json.loads(raw)
        if 'error' in reply:
            raise ValueError(reply['error'])
        return reply['result']
