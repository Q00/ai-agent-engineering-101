"""Local evidence only. No network, inference of durability, or forum side effects."""
import hashlib
import json
import math
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def locked(path, timeout=2):
    """Atomic directory lock shared by hook processes; never steal a possibly live lock."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            path.mkdir()
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError('memory lock unavailable; no write')
            time.sleep(.02)
    try:
        yield
    finally:
        path.rmdir()


def atomic_json(path, data):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=path.name+'.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, allow_nan=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def fingerprint(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return default


def sync(state, order=None):
    """Rescan complete lines; stable evidence IDs survive truncation/rotation/replays."""
    state = Path(state)
    state.mkdir(parents=True, exist_ok=True)
    with locked(state/'evidence-memory.lock'):
        path = state/'evidence-memory.json'
        mem = load(path, {'version':1, 'purchases':{}, 'refusals':{}, 'sources':{}})
        payment = state/'payments.jsonl'
        if payment.exists():
            raw = payment.read_bytes()
            complete = raw[:raw.rfind(b'\n')+1]
            mem['sources']['payments.jsonl'] = {'complete_bytes':len(complete), 'sha256':hashlib.sha256(complete).hexdigest()}
            for line_number, line in enumerate(complete.splitlines(), 1):
                try:
                    row = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(row, dict) or not row.get('order') or not row.get('shop'):
                    continue
                provenance = {'file':'payments.jsonl', 'line':line_number, 'sha256':fingerprint(row)}
                if row.get('paid') is True and type(row.get('ledger')) is int and row['ledger'] > 0 and positive(row.get('price')) and row.get('item'):
                    mem['purchases'].setdefault(str(row['ledger']), dict(order=row['order'], shop=row['shop'], sku=row['item'], price=row['price'], ledger=row['ledger'], sources=[provenance]))
                elif row.get('paid') is False:
                    key = fingerprint([row['order'], row['shop'], row.get('item'), row.get('reason')])
                    mem['refusals'].setdefault(key, dict(row, sources=[provenance]))
        # deal.json is written only after payment; ledger is its authoritative identity.
        try:
            deal = load(state/'deal.json', {})
            settled = deal.get('settled') or {}
            receipt = deal.get('receipt') or {}
            deal_order = deal.get('order')
            order_id = deal_order.get('order') if isinstance(deal_order, dict) else deal_order
            ledger = settled.get('ledger')
            if type(ledger) is int and ledger > 0 and settled.get('sku') and settled.get('shop') and order_id and positive(receipt.get('price')):
                key = str(ledger)
                facts = dict(order=order_id, shop=settled['shop'], sku=settled['sku'], price=receipt['price'], ledger=ledger)
                source = {'file':'deal.json', 'sha256':fingerprint(deal)}
                purchase = mem['purchases'].setdefault(key, dict(facts, sources=[]))
                if all(purchase.get(k) == v for k, v in facts.items()):
                    for k in ('unit', 'name', 'grade', 'claim', 'claim_honest', 'expires_at', 'warranty_hours', 'value'):
                        if k in settled:
                            purchase[k] = settled[k]
                    purchase['receipt_observed'] = {k:receipt[k] for k in ('cert', 'warranty_hours', 'claim') if k in receipt}
                    if source not in purchase['sources']:
                        purchase['sources'].append(source)
        except (ValueError, TypeError, AttributeError):
            pass  # overwritten/truncated transient source is not evidence
        mem.setdefault('broken_unresolved', {})
        broken = state/'broken.jsonl'
        if broken.exists():
            raw = broken.read_bytes()
            for line in raw[:raw.rfind(b'\n')+1].splitlines():
                try:
                    row = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if isinstance(row, dict) and row.get('name') and 'broke_at' in row:
                    mem['broken_unresolved'].setdefault(fingerprint(row), {'observed':row, 'source':'broken.jsonl', 'status':'unresolved; no exact purchase/time binding'})
        mem.setdefault('requested_orders', {})
        if isinstance(order, dict) and order.get('order') and order.get('category'):
            mem['requested_orders'].setdefault(str(order['order']), {'category':order['category'], 'source':'buyer order.json', 'sha256':fingerprint(order)})
        atomic_json(path, mem)
        return mem


def summary(mem, catalog):
    """Local buyer sample, NOT market popularity/rate. Catalog denominators are current."""
    result = {'requested_categories':{}, 'settled_categories':{}, 'settled_denominator':0,
              'unmapped_settlements':0, 'shops':{}, 'source':'local paid payments.jsonl/deal.json joined to current catalog.json'}
    for request in mem.get('requested_orders', {}).values():
        cat = request['category']
        result['requested_categories'][cat] = result['requested_categories'].get(cat, 0)+1
    for purchase in mem.get('purchases', {}).values():
        shop = result['shops'].setdefault(purchase['shop'].rstrip('/'), {'paid':0, 'refused':0, 'reasons':{}, 'ratios':[]})
        shop['paid'] += 1
        item = catalog.get(purchase['sku'], {})
        if positive(item.get('list_price')):
            shop['ratios'].append(purchase['price']/item['list_price'])
        if item.get('category'):
            cat = item['category']
            result['settled_categories'][cat] = result['settled_categories'].get(cat, 0)+1
            result['settled_denominator'] += 1
        else:
            result['unmapped_settlements'] += 1
    for refusal in mem.get('refusals', {}).values():
        shop = result['shops'].setdefault(refusal['shop'].rstrip('/'), {'paid':0, 'refused':0, 'reasons':{}, 'ratios':[]})
        shop['refused'] += 1
        reason = refusal.get('reason', 'unobserved')
        shop['reasons'][reason] = shop['reasons'].get(reason, 0)+1
    return result
