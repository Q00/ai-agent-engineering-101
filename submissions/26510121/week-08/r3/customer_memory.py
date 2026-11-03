"""Compact, persistent visitor behavior; no raw chats or guessed identities."""
import base64
import hashlib
import json
import re
import statistics
import time
from cryptography.exceptions import InvalidSignature
from google.protobuf.json_format import MessageToDict
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from .protocol import canon
from .state import identity

SCOPE = 'shopper-memory-v1:Seochanit Shop'
LIMIT = 2000
MAX_VISITS = 1000
MAX_EVENTS = 8000
MAX_PROOFS = 4000


def metadata_dict(value):
    if isinstance(value, dict):
        return value
    try:
        result = MessageToDict(value)
        return result if isinstance(result, dict) else {}
    except (AttributeError, TypeError):
        return {}


def retain(journal, max_visits=MAX_VISITS, max_events=MAX_EVENTS):
    """FIFO only for customer working memory; never touch payment liabilities."""
    with journal.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        old = connection.execute(
            "SELECT data FROM events WHERE kind='customer_visit' ORDER BY ts DESC,rowid DESC LIMIT -1 OFFSET ?",
            (max_visits,)).fetchall()
        for (data,) in old:
            task = json.loads(data)['task']
            connection.execute("DELETE FROM events WHERE kind LIKE 'customer_%' AND json_extract(data,'$.task')=?", (task,))
            connection.execute('DELETE FROM kv WHERE key IN (?,?)', ('customer-visit:' + task, 'customer-payer:' + task))
            connection.execute("DELETE FROM kv WHERE key LIKE 'customer-ledger:%' AND value=?", (json.dumps(task),))
        # Actions/turns can also be numerous within one visit; enforce a total cap.
        connection.execute("DELETE FROM events WHERE rowid IN (SELECT rowid FROM events "
                           "WHERE kind LIKE 'customer_%' AND kind!='customer_visit' "
                           "ORDER BY ts DESC,rowid DESC LIMIT -1 OFFSET ?)",
                           (max(0, max_events - max_visits),))
        # Never evict an unexpired replay guard to make room for a new one.
        connection.execute("DELETE FROM kv WHERE key LIKE 'customer-proof:%' "
                           "AND json_type(value)='object' AND json_extract(value,'$.expires') < ?", (time.time(),))


def signed_visitor(journal, task, message_id, text, metadata, now=None, expected_shop=None):
    """Optional key continuity proof, not proof of a registered market name."""
    now = time.time() if now is None else now
    try:
        proof = metadata.get('buyer_memory_proof', {})
        ts = proof['ts']
        if type(ts) is float and ts.is_integer():
            ts = int(ts)  # A2A protobuf Struct decodes JSON numbers as floats.
        if type(ts) is not int or abs(now - ts) > 300 or not message_id or not expected_shop:
            return None
        raw = base64.b64decode(proof['pubkey'], validate=True)
        payload = {'scope': SCOPE, 'shop_pubkey': expected_shop, 'message_id': message_id, 'text': text, 'ts': ts}
        Ed25519PublicKey.from_public_bytes(raw).verify(base64.b64decode(proof['sig'], validate=True), canon(payload))
        subject = 'key:' + hashlib.sha256(raw).hexdigest()
        # A proof cannot be transplanted to a new task, even inside its time window.
        replay_key = 'customer-proof:' + identity([subject, message_id])
        with journal.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            connection.execute("DELETE FROM kv WHERE key LIKE 'customer-proof:%' "
                               "AND json_type(value)='object' AND json_extract(value,'$.expires') < ?", (now,))
            row = connection.execute('SELECT value FROM kv WHERE key=?', (replay_key,)).fetchone()
            if row:
                previous = json.loads(row[0])
                if (previous.get('task') if isinstance(previous, dict) else previous) != task:
                    return None
            else:
                count = connection.execute("SELECT count(*) FROM kv WHERE key LIKE 'customer-proof:%'").fetchone()[0]
                if count >= MAX_PROOFS:
                    return None
                connection.execute('INSERT INTO kv VALUES(?,?)',
                                   (replay_key, json.dumps({'task': task, 'expires': ts + 300})))
        return subject
    except (InvalidSignature, KeyError, ValueError, TypeError, AttributeError):
        # Invalid signatures and arbitrary peer metadata never authenticate a name.
        return None


def visit(journal, task, text, unit=None, strategy_version=0, signer=None):
    key = 'customer-visit:' + task
    current = journal.get(key)
    if current is not None:
        return current
    catalog = journal.snapshot('catalog.json', {})
    requested = re.findall(r'\bG[0-9a-z]+\b', text, re.I)
    product = unit or next((dict(catalog[sku], sku=sku) for sku in requested if sku in catalog), {})
    if not product:
        categories = {(p.get('category'), p.get('kind')) for p in catalog.values()
                      if p.get('category') and p['category'].split('>')[-1].casefold() in text.casefold()}
        if len(categories) == 1:
            category, kind = categories.pop()
            product = {'category': category, 'kind': kind}
    record = {'task': task, 'sku': product.get('sku'), 'category': product.get('category'),
              'list_price': product.get('list_price'), 'kind': product.get('kind'),
              'strategy_version': strategy_version, 'signer': signer}
    journal.add('customer_visit', record, eid='customer-visit:' + task, role='shop')
    journal.put(key, record)
    retain(journal)
    return record


def incoming(journal, task, message_id, text, last_ask=None, turn=0):
    match = re.fullmatch(r'\s*(?:I accept(?: at)?\s*)?([\d,]+)\s*', text, re.I)
    offer = int(match.group(1).replace(',', '')) if match else None
    if offer is not None and not 0 < offer <= 10**12:
        offer = None
    record = {'task': task, 'message_id': message_id, 'turn': turn, 'offer': offer,
              'last_ask': last_ask,
              'mentions_certificate': bool(re.search(r'cert|인증|증명', text, re.I)),
              'mentions_warranty': bool(re.search(r'warranty|보증', text, re.I)),
              'mentions_freshness': bool(re.search(r'fresh|expire|유통기한|신선', text, re.I))}
    dedup = identity([task, message_id]) if message_id else identity([task, turn, text])
    journal.add('customer_turn', record, eid='customer-turn:' + dedup, role='shop')
    retain(journal)


def action(journal, task, message_id, turn, chosen, version):
    journal.add('customer_action', {'task': task, 'turn': turn, 'action': chosen['action'],
                'price': chosen['price'], 'strategy_version': version},
                eid='customer-action:' + identity([task, message_id or turn]), role='shop')


def finish(journal, task, outcome):
    journal.add('customer_outcome', {'task': task, 'outcome': outcome},
                eid='customer-outcome:' + identity([task, outcome]), role='shop')


def bind_market_payer(journal, ledger, trusted_data):
    """Called only on an accepted settlement or the market's own ledger feed."""
    task = journal.get('customer-ledger:' + str(ledger))
    label = trusted_data.get('buyer')
    if not task or not isinstance(label, str) or not label.strip() or len(label) > 200:
        return False
    subject = 'market-round3:' + label.strip()
    old = journal.get('customer-payer:' + task)
    if old and old != subject:
        journal.add('customer_identity_conflict', {'task': task, 'ledger': ledger}, role='shop')
        return False
    journal.add('customer_identity', {'task': task, 'payer': subject, 'source': 'accepted_market_record'},
                eid='customer-identity:' + task, role='shop')
    journal.put('customer-payer:' + task, subject)
    return True


def paid(journal, task, price, sku, ledger, trusted_receipt=None):
    details = journal.get('customer-visit:' + task, {})
    product = journal.snapshot('catalog.json', {}).get(sku, {})
    record = {'task': task, 'sku': sku, 'category': product.get('category', details.get('category')),
              'list_price': product.get('list_price', details.get('list_price')),
              'price': int(price), 'ledger': ledger, 'signer': details.get('signer')}
    journal.add('customer_paid', record, eid='customer-paid:' + (str(ledger) if ledger is not None else task), role='shop')
    journal.put('customer-ledger:' + str(ledger), task)
    bind_market_payer(journal, ledger, trusted_receipt or {})
    # Feed and settlement callbacks may arrive in either order.
    with journal.connect() as connection:
        row = connection.execute('SELECT data FROM events WHERE id=? AND kind=?',
                                 ('public:' + str(ledger), 'public_settle')).fetchone()
    if row:
        bind_market_payer(journal, ledger, json.loads(row[0]))


def profile(journal, subject, category=None):
    """Compact history for a verified signing key or market-confirmed payer label."""
    visits = journal.events('customer_visit', LIMIT)
    payers = {e['task']: e['payer'] for e in journal.events('customer_identity', LIMIT)}
    tasks = {e['task'] for e in visits if e.get('signer') == subject or payers.get(e['task']) == subject}
    purchases = [e for e in journal.events('customer_paid', LIMIT) if e['task'] in tasks]
    categories = {}
    for event in purchases:
        if event.get('category'):
            categories[event['category']] = categories.get(event['category'], 0) + 1
    relevant = [e for e in purchases if category is None or e.get('category') == category]
    ratios = [e['price'] / e['list_price'] for e in relevant if e.get('list_price', 0)]
    turns = journal.events('customer_turn', LIMIT)
    paid_tasks = {e['task'] for e in relevant}
    lengths = [sum(e['task'] == t for e in turns) for t in paid_tasks]
    lengths = [n for n in lengths if n]
    return {'subject': subject, 'observed_visits': len(tasks), 'paid_visits': len(purchases),
            'purchased_categories': dict(sorted(categories.items(), key=lambda p: p[1], reverse=True)[:5]),
            'category_paid_visits': len(relevant),
            'median_paid_to_list_ratio': statistics.median(ratios) if ratios else None,
            'median_turns_before_payment': statistics.median(lengths) if lengths else None,
            'identity_scope': 'signing-key continuity' if subject.startswith('key:') else 'market payer label within Round 3',
            'note': 'observed requests and purchases, not inferred private preferences'}


def summary(journal, task, unit):
    visits = {e['task']: e for e in journal.events('customer_visit', LIMIT)}
    current = journal.get('customer-visit:' + task, {})
    turns = journal.events('customer_turn', LIMIT)
    recent = sorted((e for e in turns if e['task'] == task), key=lambda e: e['_ts'])
    comparable = {t for t, e in visits.items() if t != task and e.get('category') == unit.get('category')
                  and e.get('kind') == unit.get('kind')}
    purchases = [e for e in journal.events('customer_paid', LIMIT)
                 if e['task'] in comparable and e.get('category') == unit.get('category')]
    paid_tasks = {e['task'] for e in purchases}
    ratios = [e['price'] / e['list_price'] for e in purchases if e.get('list_price', 0) and
              0 < e['price'] <= 2 * e['list_price']]
    first_offers = {}
    for event in sorted(turns, key=lambda e: e['_ts']):
        if event['task'] in paid_tasks and event.get('offer') and event.get('last_ask'):
            first_offers.setdefault(event['task'], event['offer'] / event['last_ask'])
    behavior = {'observed_visits': len(comparable), 'paid_visits': len(paid_tasks),
                'unpaid_or_unknown_visits': len(comparable - paid_tasks),
                'median_paid_to_list_ratio': statistics.median(ratios) if ratios else None,
                'median_first_offer_to_ask_ratio': statistics.median(first_offers.values()) if first_offers else None,
                'certificate_mentions': sum(e['mentions_certificate'] for e in turns if e['task'] in paid_tasks),
                'warranty_mentions': sum(e['mentions_warranty'] for e in turns if e['task'] in paid_tasks),
                'note': 'recent observed category behavior; unpaid does not mean rejected; mentions are not requirements'}
    result = {'task': task, 'identity_status': 'anonymous', 'category_behavior': behavior,
              'current_offers': [e['offer'] for e in recent if e.get('offer')][-4:],
              'current_mentions': {key: any(e[key] for e in recent) for key in
                                   ('mentions_certificate', 'mentions_warranty', 'mentions_freshness')},
              'remembered_turns': len(recent)}
    subject = current.get('signer') or journal.get('customer-payer:' + task)
    if subject:
        result.update(identity_status='verified_key' if subject.startswith('key:') else 'market_confirmed_payer',
                      customer_profile=profile(journal, subject, unit.get('category')))
    return result
