"""Opt-in buyer forum report from exact public Round 3 settle evidence.

Writes one outbox line at most; the kit owns submission. No direct POST, credentials, votes or model calls.
maybe_report reports `queued` (local outbox append) only. reconcile (manual CLI, never a hook) reads the kit's
outbox.sent.jsonl for `acknowledged` and an exact public forum post for `verified`; it never resends anything.
"""
import argparse
import hashlib
import http.client
import json
import math
import os
import re
import statistics
import time
import urllib.request
from pathlib import Path

from evidence_memory import atomic_json, load, locked, positive

MARKET = 'https://market.ourolabs.ai'
BUYER = 'fair-furniture'
WINDOW = 12*3600
DAY = 24*3600
SKU = re.compile(r'[A-Za-z0-9_-]{1,64}')
HEX = re.compile(r'[0-9a-f]{64}')
LEDGER_KEY = re.compile(r'[1-9][0-9]*')
GRADES = ('A', 'B', 'C')
STATUSES = ('intent', 'queued', 'acknowledged', 'verified')
OWNER = 'kimchyoungman'
V2 = 'bounded-v2'
MAX_ROWS = 500
MAX_BYTES = 2_000_000
COHORT = 5


def _exact_int(value, low, high):
    return type(value) is int and low <= value <= high  # bool and '60' are not ints here


def _time(value):
    """Finite positive epoch number; bool, str, NaN and inf are not times."""
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _num(value):
    return int(value) if float(value).is_integer() else value


def read_policy(state):
    """Normalized {'version', 'interval', 'max'} from forum-policy.json, or None (disabled).

    Legacy {"enabled": true} (no policy_version, or 'bounded-v1') keeps the 12h window.
    'bounded-v2' needs int minimum_interval_minutes in 60..180 and int max_intents_per_24h in 1..8.
    Anything else, including unknown versions, fails closed.
    """
    try:
        policy = load(Path(state)/'forum-policy.json', {})
    except (OSError, ValueError):
        return None
    if not isinstance(policy, dict) or policy.get('enabled') is not True:
        return None
    version = policy.get('policy_version', 'bounded-v1')
    if version == 'bounded-v1':
        return {'version':version, 'interval':WINDOW, 'max':None}
    if version != V2:
        return None
    minutes, cap = policy.get('minimum_interval_minutes'), policy.get('max_intents_per_24h')
    if not _exact_int(minutes, 60, 180) or not _exact_int(cap, 1, 8):
        return None
    return {'version':version, 'interval':minutes*60, 'max':cap}


COMBINED_MAX_24H = 6  # forum_r3 + terra-forum-v1 posts together per rolling 24 h (forum_terra's own upper bound)


def terra_times(state):
    """Intent times of terra-forum-v1 (forum-terra-report.json, read only): [] if absent, None if malformed.
    forum_r3 runs whenever terra is inactive, so both writers share one interval and one 24 h cap."""
    path = Path(state)/'forum-terra-report.json'
    if not path.exists():
        return []
    try:
        record = load(path, None)
    except (OSError, ValueError, UnicodeError):
        return None
    intents = record.get('intents') if isinstance(record, dict) else None
    if not isinstance(intents, dict) or not all(isinstance(i, dict) and _time(i.get('at')) for i in intents.values()):
        return None
    return [i['at'] for i in intents.values()]


def enabled(state):
    """Disabled unless forum-policy.json is a valid opt-in policy (see read_policy)."""
    return read_policy(state) is not None


def latest_paid(state):
    """Latest local paid ledger; conflicting rows for that ledger fail closed."""
    path = Path(state)/'payments.jsonl'
    if not path.is_file():
        return None
    raw = path.read_bytes()
    paid = {}
    for line in raw[:raw.rfind(b'\n')+1].splitlines():
        try:
            row = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if not isinstance(row, dict) or row.get('paid') is not True:
            continue
        ledger = row.get('ledger')
        if type(ledger) is not int or ledger <= 0 or not positive(row.get('price')):
            continue
        if not all(isinstance(row.get(k), str) and row[k] for k in ('order', 'shop', 'item')):
            continue
        facts = dict(order=row['order'], shop=row['shop'].rstrip('/'), item=row['item'], price=row['price'], ledger=ledger)
        if paid.setdefault(ledger, facts) != facts:
            paid[ledger] = None
    return paid[max(paid)] if paid else None


def fetch_rows(seq):
    """Public GET of the ledger starting exactly at seq; no auth headers. Oversized or unknown shapes raise."""
    url = f'{MARKET}/api/ledger?after={seq-1}'
    with urllib.request.urlopen(urllib.request.Request(url, headers={'Accept':'application/json'}), timeout=3) as response:
        raw = response.read(MAX_BYTES+1)
    if len(raw) > MAX_BYTES:
        raise ValueError('ledger response exceeds MAX_BYTES')  # never parse a truncated body
    body = json.loads(raw.decode('utf-8'))
    if isinstance(body, dict):
        body = next((body[k] for k in ('entries', 'ledger', 'rows', 'items') if isinstance(body.get(k), list)), None)
    if not isinstance(body, list):
        raise ValueError('unrecognized ledger response shape')
    return body


def snapshot(rows):
    """Bounded observed window: first MAX_ROWS supplied elements, well-formed (dict, exact int seq) rows only.

    Returns (rows sorted by seq, coverage) or None for a non-list, a duplicated seq or unserializable rows.
    Coverage counts the observed rows (own included); it is a sample window, not the whole ledger.
    """
    if not isinstance(rows, list):
        return None
    seen = {}
    for row in rows[:MAX_ROWS]:
        if not isinstance(row, dict) or type(row.get('seq')) is not int or row['seq'] <= 0:
            continue
        if row['seq'] in seen:
            return None  # duplicate or conflicting seq: the source is not trustworthy as a whole
        seen[row['seq']] = row
    ordered = [seen[s] for s in sorted(seen)]
    try:
        blob = json.dumps(ordered, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    except (ValueError, TypeError):
        return None
    times = [r['ts'] for r in ordered if _time(r.get('ts'))]
    coverage = {'seq_min':ordered[0]['seq'] if ordered else None, 'seq_max':ordered[-1]['seq'] if ordered else None,
                'rows':len(ordered), 'ts_min':min(times, default=None), 'ts_max':max(times, default=None),
                'truncated':len(rows) > MAX_ROWS, 'sha256':hashlib.sha256(blob.encode()).hexdigest()}
    return ordered, coverage


def public_match(rows, paid):
    """Public settle row whose seq, round, buyer, shop, order, item and amount all equal the local paid row."""
    found = _public_row(rows, paid)
    return found['data'] if found else None


def _public_row(rows, paid):
    for row in rows or ():
        if not isinstance(row, dict) or type(row.get('seq')) is not int or row['seq'] != paid['ledger']:
            continue
        data = row.get('data')
        if row.get('kind') != 'settle' or not isinstance(data, dict):
            continue
        # Actual ledger schema only: top-level round, data.amount. No price/nested-round fallback.
        amount = data.get('amount')
        shop = data.get('shop')
        if (row.get('round') == 3 and type(row['round']) is int and data.get('buyer') == BUYER
                and isinstance(shop, str) and shop.rstrip('/') == paid['shop']
                and data.get('order') == paid['order'] and data.get('item') == paid['item']
                and positive(amount) and amount == paid['price']):
            return row
    return None


def _warranty(data):
    hours = data.get('warranty_hours')
    if type(hours) in (int, float) and math.isfinite(hours) and 0 <= hours <= 10_000:
        return f'{_num(hours)}h'
    return 'unobserved'  # missing or invalid is unobserved, never presumed zero


def render(paid, data):
    """Fixed factual template: SKU, price, observed grade/warranty, explicit caveats. No names or shop."""
    if not SKU.fullmatch(paid['item']):
        return None
    price = _num(paid['price'])
    grade = data.get('grade') if data.get('grade') in GRADES else 'unobserved'
    warranty = _warranty(data)
    return (f'Buyer report (Round 3): paid {price} credits for SKU {paid["item"]}. '
            f'Observed at settle: grade {grade}, warranty {warranty}. '
            'Durability unknown (unverified so far). '
            "One buyer's local, biased sample; not a market-wide rating.")


def facts_of(paid, data):
    """Material facts compared for novelty: SKU, price, grade, warranty. Never the ledger id or the cohort."""
    return {'sku':paid['item'], 'price':_num(paid['price']),
            'grade':data.get('grade') if data.get('grade') in GRADES else 'unobserved', 'warranty':_warranty(data)}


def facts_digest(facts):
    return hashlib.sha256(json.dumps(dict(facts, policy_version=V2), sort_keys=True).encode()).hexdigest()


def cohort(rows, own, now):
    """First COHORT distinct other buyers' Round 3 settles after own seq, exact same SKU, grade and warranty.

    Each needs a positive amount and a valid ts no later than now. Fewer than COHORT -> [] (comparison omitted).
    Shop owners are not inferred; a distinct buyer name does not prove independence.
    """
    data = own['data']
    grade, hours = data.get('grade'), data.get('warranty_hours')
    if grade not in GRADES or _warranty(data) == 'unobserved':
        return []
    picked, buyers = [], set()
    for row in rows:  # already sorted by seq
        peer = row.get('data')
        if row['seq'] <= own['seq'] or row.get('kind') != 'settle' or row.get('round') != 3 or type(row['round']) is not int:
            continue
        if not isinstance(peer, dict) or not _time(row.get('ts')) or row['ts'] > now:
            continue
        buyer = peer.get('buyer')
        if not isinstance(buyer, str) or not buyer or buyer == BUYER or buyer in buyers:
            continue
        if (peer.get('item') != data['item'] or peer.get('grade') != grade or _warranty(peer) == 'unobserved'
                or peer['warranty_hours'] != hours or not positive(peer.get('amount'))):
            continue
        buyers.add(buyer)
        picked.append(row)
        if len(picked) == COHORT:
            return picked
    return []


def _utc(ts):
    return time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(ts))


def render_v2(paid, facts, peers):
    """bounded-v2 factual template: own ledger id and facts, then a same-SKU median or an explicit omission."""
    if not SKU.fullmatch(paid['item']):
        return None
    text = (f'Buyer report (Round 3, ledger #{paid["ledger"]}): paid {facts["price"]} credits for SKU {facts["sku"]}. '
            f'Observed at settle: grade {facts["grade"]}, warranty {facts["warranty"]}. '
            'Durability unknown (unverified so far). ')
    if peers:
        median = _num(statistics.median(r['data']['amount'] for r in peers))
        times = [r['ts'] for r in peers]
        text += (f'Same-SKU comparison: median {median} credits over the first {COHORT} distinct other-buyer '
                 f'Round 3 settlements of SKU {facts["sku"]} with grade {facts["grade"]} and warranty {facts["warranty"]}, '
                 f'ledger #{peers[0]["seq"]}-#{peers[-1]["seq"]}, settled {_utc(min(times))} to {_utc(max(times))}. '
                 'Buyer names withheld; buyer owners unknown and not shown to be independent; shop owners also unknown. '
                 'The median describes those rows only and is not a value judgment. ')
    else:
        text += ('Same-SKU comparison omitted: fewer than five qualifying other-buyer settlements '
                 'in the observed ledger window. ')
    return text + "One buyer's local, biased sample; not market-wide."


def maybe_report(state, now=None, rows=None):
    state = Path(state)
    policy = read_policy(state)
    if policy is None:
        return {'queued':False, 'reason':'disabled'}
    now = time.time() if now is None else now
    if not _time(now):
        return {'queued':False, 'reason':'invalid now'}
    try:
        with locked(state/'forum-report.lock'):
            return _report(state, now, rows, policy)
    except TimeoutError:
        return {'queued':False, 'reason':'locked'}


def _hex(value):
    return isinstance(value, str) and HEX.fullmatch(value) is not None


def _valid_v2_intent(key, intent):
    try:
        return _v2_consistent(key, intent)
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def _v2_consistent(key, intent):
    """Rebuild the post from the stored source_rows; every derived field must match exactly."""
    ids, coverage, facts, rows = (intent.get(k) for k in ('source_ids', 'coverage', 'facts', 'source_rows'))
    if intent.get('policy_version') != V2 or not isinstance(ids, list) or not isinstance(rows, list):
        return False
    if not ids or len(ids) not in (1, 1+COHORT) or not all(type(i) is int and i > 0 for i in ids) or ids[0] != int(key):
        return False
    if ids[1:] != sorted(set(ids[1:])) or any(i <= ids[0] for i in ids[1:]):
        return False  # peers strictly ascending after own seq, as cohort() picks them
    if len(rows) != len(ids) or not all(isinstance(r, dict) for r in rows) or [r.get('seq') for r in rows] != ids:
        return False
    own, data = rows[0], rows[0].get('data')
    if not isinstance(data, dict) or not all(isinstance(data.get(k), str) for k in ('item', 'order', 'shop')):
        return False
    paid = dict(order=data['order'], shop=data['shop'].rstrip('/'), item=data['item'], price=data.get('amount'), ledger=own['seq'])
    if not positive(paid['price']) or _public_row([own], paid) is not own:
        return False  # own paid-like facts: round 3 settle, this buyer, exact seq and amount
    peers = rows[1:]
    if cohort(rows, own, intent['at']) != peers:
        return False
    rebuilt = facts_of(paid, data)
    if facts != rebuilt or intent.get('sha256') != facts_digest(rebuilt):
        return False
    post = render_v2(paid, rebuilt, peers)
    if post is None or not _hex(intent.get('post_sha256')) or intent['post_sha256'] != hashlib.sha256(post.encode()).hexdigest():
        return False
    # Coverage is the whole observed window; the cited rows are only a subset of it, never its hash.
    if not isinstance(coverage, dict) or not all(type(coverage.get(k)) is int for k in ('seq_min', 'seq_max', 'rows')):
        return False
    return (_hex(coverage.get('sha256')) and 0 < coverage['seq_min'] <= min(ids) and max(ids) <= coverage['seq_max']
            and coverage['rows'] >= len(rows) and coverage['rows'] <= MAX_ROWS)


def _valid_progress(intent):
    """intent/queued carry no ack fields; acknowledged needs post_id; verified also needs readback proof."""
    status, acked = intent.get('status'), 'post_id' in intent
    if acked != (status in ('acknowledged', 'verified')):
        return False
    if acked and not _exact_int(intent['post_id'], 1, math.inf):
        return False
    proof = ('readback_sha256' in intent, 'readback_at' in intent)
    if status == 'verified':
        return _hex(intent.get('readback_sha256')) and _time(intent.get('readback_at'))
    return proof == (False, False)


def valid_record(record, now):
    """Strict schema; legacy v1 intents ({'at','sha256','status'}) stay valid. Anything else is corrupt."""
    if not isinstance(record, dict) or type(record.get('version')) is not int or record['version'] != 1:
        return False
    intents, content = record.get('intents'), record.get('content')
    if not isinstance(intents, dict) or not isinstance(content, list):
        return False
    if not all(isinstance(c, str) and HEX.fullmatch(c) for c in content):
        return False
    for key, intent in intents.items():
        if not LEDGER_KEY.fullmatch(key) or not isinstance(intent, dict):
            return False
        if not _time(intent.get('at')) or intent['at'] > now or intent.get('status') not in STATUSES:
            return False
        if not _hex(intent.get('sha256')) or intent['sha256'] not in content or not _valid_progress(intent):
            return False
        if 'policy_version' in intent and not _valid_v2_intent(key, intent):
            return False
    return True


def _material(intents, facts):
    """Per SKU: price must move >= 5% from the last reported price unless grade or warranty changed; new SKU is material."""
    same = [i['facts'] for i in sorted(intents.values(), key=lambda i: i['at']) if i.get('facts', {}).get('sku') == facts['sku']]
    if not same:
        return True
    last = same[-1]
    if last['grade'] != facts['grade'] or last['warranty'] != facts['warranty']:
        return True
    return abs(facts['price']-last['price'])*20 >= last['price']


def _report(state, now, rows, policy):
    path = state/'forum-report.json'
    try:
        record = load(path, {'version':1, 'intents':{}, 'content':[]})
    except (OSError, ValueError):
        return {'queued':False, 'reason':'record unreadable'}
    if not valid_record(record, now):
        return {'queued':False, 'reason':'record unreadable'}  # left as is on disk; never reset
    intents, content = record['intents'], record['content']
    terra = terra_times(state)
    if terra is None:
        return {'queued':False, 'reason':'forum_terra record unreadable'}
    last = max([i['at'] for i in intents.values()] + terra, default=None)
    if last is not None and now - last < policy['interval']:
        return {'queued':False, 'reason':'rate window'}
    # Every recorded intent counts, including ones whose append failed.
    if policy['max'] is not None and sum(now - i['at'] < DAY for i in intents.values()) >= policy['max']:
        return {'queued':False, 'reason':'rolling 24h cap'}
    if terra and sum(now - t < DAY for t in [i['at'] for i in intents.values()] + terra) >= COMBINED_MAX_24H:
        return {'queued':False, 'reason':'rolling 24h combined cap'}
    try:
        paid = latest_paid(state)
    except OSError:
        return {'queued':False, 'reason':'payments unreadable'}
    if paid is None:
        return {'queued':False, 'reason':'no local paid settlement'}
    key = str(paid['ledger'])
    if key in intents:
        return {'queued':False, 'reason':'ledger already reported'}
    if rows is None:
        try:
            rows = fetch_rows(paid['ledger'])
        except (OSError, ValueError, http.client.HTTPException):
            return {'queued':False, 'reason':'ledger unavailable'}
    observed = snapshot(rows)
    if observed is None:
        return {'queued':False, 'reason':'source rows rejected'}
    rows, coverage = observed
    own = _public_row(rows, paid)
    if own is None:
        return {'queued':False, 'reason':'no exact public settle'}
    if policy['version'] == V2:
        facts = facts_of(paid, own['data'])
        peers = cohort(rows, own, now)
        post = render_v2(paid, facts, peers)
        digest = facts_digest(facts)
        refs = [paid['ledger'], *(r['seq'] for r in peers)]
    else:
        post = render(paid, own['data'])
        digest = hashlib.sha256(post.encode()).hexdigest() if post else None
        refs = [paid['ledger']]
    if post is None:
        return {'queued':False, 'reason':'sku not reportable'}
    if digest in content:
        return {'queued':False, 'reason':'duplicate content'}
    if policy['version'] == V2 and not _material(intents, facts):
        return {'queued':False, 'reason':'not materially new'}
    intent = {'at':now, 'sha256':digest, 'status':'intent'}
    if policy['version'] == V2:
        intent.update(policy_version=V2, source_ids=refs, coverage=coverage, facts=facts,
                      post_sha256=hashlib.sha256(post.encode()).hexdigest(),
                      source_rows=[own, *peers])  # exact rows the post cites
    line = json.dumps({'post':post, 'refs':refs, 'as':'buyer'}, ensure_ascii=False)+'\n'
    # Intent is durable before the append, so a failed or partial append never retries.
    intents[key] = intent
    content.append(digest)
    try:
        atomic_json(path, record)
    except (OSError, ValueError, TypeError):
        return {'queued':False, 'reason':'intent write failed; nothing appended'}
    try:
        with open(state/'outbox.jsonl', 'a', encoding='utf-8') as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        return {'queued':False, 'reason':'append failed; intent kept'}
    intent['status'] = 'queued'
    try:
        atomic_json(path, record)
    except (OSError, ValueError, TypeError):
        return {'queued':False, 'reason':'appended; queued mark failed, intent kept'}
    return {'queued':True, 'ledger':paid['ledger']}  # local outbox only; kit/parent confirms any publish


def _expected(key, intent):
    """(refs, post sha256) the outbox line carried: v2 source_ids/post_sha256, legacy [ledger]/sha256."""
    if intent.get('policy_version') == V2:
        return intent['source_ids'], intent['post_sha256']
    return [int(key)], intent['sha256']


def _text_sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest() if isinstance(text, str) else None


def _sent_matches(line, refs, post_sha):
    return (isinstance(line, dict) and set(line) == {'post', 'refs', 'as'} and line['as'] == 'buyer'
            and isinstance(line['refs'], list) and all(type(r) is int for r in line['refs']) and line['refs'] == refs
            and _text_sha(line['post']) == post_sha)


def _answer_id(answer):
    if not isinstance(answer, str):
        return None
    try:
        body = json.loads(answer)
    except ValueError:
        return None
    post_id = body.get('post') if isinstance(body, dict) else None
    return post_id if _exact_int(post_id, 1, math.inf) else None


def sent_acks(state):
    """[(sent line, post id)] from complete 2xx lines of outbox.sent.jsonl; anything malformed is skipped."""
    path = Path(state)/'outbox.sent.jsonl'
    if not path.is_file():
        return []
    raw = path.read_bytes()
    acks = []
    for line in raw[:raw.rfind(b'\n')+1].splitlines():
        try:
            entry = json.loads(line.decode('utf-8'))
        except (ValueError, UnicodeError):
            continue
        if not isinstance(entry, dict) or type(entry.get('status')) is not int or not 200 <= entry['status'] < 300:
            continue
        post_id = _answer_id(entry.get('answer'))
        if post_id is not None and isinstance(entry.get('sent'), dict):
            acks.append((entry['sent'], post_id))
    return acks


def _ack_id(key, intent, acks):
    """The single post id the kit acknowledged for this intent's exact line; None if absent or conflicting."""
    refs, post_sha = _expected(key, intent)
    ids = {post_id for line, post_id in acks if _sent_matches(line, refs, post_sha)}
    return ids.pop() if len(ids) == 1 else None


def index_posts(posts):
    """{id: post} from a list of dicts; any other shape -> None. A repeated id maps to None (ambiguous)."""
    if not isinstance(posts, list) or not all(isinstance(p, dict) for p in posts):
        return None
    index = {}
    for post in posts:
        post_id = post.get('id')
        if _exact_int(post_id, 1, math.inf):
            index[post_id] = None if post_id in index else post
    return index


def _public_ok(post, refs, post_sha):
    """Exact author/owner/role, full text hash, and refs found as settles at exactly source_ids in order."""
    if not isinstance(post, dict) or post.get('author') != BUYER or post.get('owner') != OWNER or post.get('role') != 'buyer':
        return False
    if _text_sha(post.get('text')) != post_sha:
        return False
    public_refs = post.get('refs')
    return (isinstance(public_refs, list) and len(public_refs) == len(refs)
            and all(isinstance(r, dict) and type(r.get('seq')) is int and r['seq'] == seq
                    and r.get('found') is True and r.get('kind') == 'settle' for r, seq in zip(public_refs, refs)))


def fetch_posts():
    """Public GET of the latest forum posts; bounded, timeout 3, unknown shapes raise."""
    url = f'{MARKET}/api/forum?limit=100'
    with urllib.request.urlopen(urllib.request.Request(url, headers={'Accept':'application/json'}), timeout=3) as response:
        raw = response.read(MAX_BYTES+1)
    if len(raw) > MAX_BYTES:
        raise ValueError('forum response exceeds MAX_BYTES')
    body = json.loads(raw.decode('utf-8'))
    if isinstance(body, dict) and isinstance(body.get('posts'), list):
        body = body['posts']
    if not isinstance(body, list):
        raise ValueError('unrecognized forum response shape')
    return body


def _load_valid(path):
    try:
        record = load(path, None)
    except (OSError, ValueError):
        return None
    # The clock is maybe_report's concern; here only the shape and self-consistency matter.
    return record if record is not None and valid_record(record, math.inf) else None


def _needs_readback(state):
    """Read-only precheck so a CLI run with nothing to verify makes no network call."""
    record = _load_valid(state/'forum-report.json')
    if record is None:
        return False
    try:
        acks = sent_acks(state)
    except OSError:
        acks = []
    return any(i['status'] == 'acknowledged' or (i['status'] in ('intent', 'queued') and _ack_id(k, i, acks) is not None)
               for k, i in record['intents'].items())


def reconcile(state, posts=None):
    """Advance intents to acknowledged (exact 2xx sent line) and verified (exact public post). Never resends.

    posts=None fetches one public readback only when something can be verified; injected posts never touch
    the network. A failed readback leaves acknowledgements in place and verifies nothing.
    """
    state = Path(state)
    note = None
    if posts is None:
        posts = []
        if _needs_readback(state):
            try:
                posts = fetch_posts()
            except (OSError, ValueError, http.client.HTTPException):
                posts, note = None, 'readback unavailable'
    try:
        with locked(state/'forum-report.lock'):
            result = _reconcile(state, posts)
    except TimeoutError:
        return {'verified':[], 'reason':'locked'}
    if note and result.get('reason') in (None, 'posts rejected'):
        result['reason'] = note
    return result


def _reconcile(state, posts):
    path = state/'forum-report.json'
    if not path.is_file():
        return {'verified':[], 'reason':'no record'}
    record = _load_valid(path)
    if record is None:
        return {'verified':[], 'reason':'record unreadable'}  # left as is on disk; never reset
    try:
        acks = sent_acks(state)
    except OSError:
        acks = []
    index = index_posts(posts)
    acknowledged, verified = [], []
    for key, intent in sorted(record['intents'].items(), key=lambda kv: int(kv[0])):
        if intent['status'] == 'verified':
            continue
        ack = _ack_id(key, intent, acks)
        if intent['status'] in ('intent', 'queued'):
            if ack is None:
                continue  # unsent, failed or conflicting: stays as is; never retried
            intent.update(status='acknowledged', post_id=ack)
            acknowledged.append(ack)
        elif ack is not None and ack != intent['post_id']:
            continue  # a later sent line names another id: ambiguous, do not verify
        post = index.get(intent['post_id']) if index else None
        refs, post_sha = _expected(key, intent)
        if post is None or not _public_ok(post, refs, post_sha):
            continue
        try:
            proof = json.dumps(post, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        except (ValueError, TypeError):
            continue
        intent.update(status='verified', readback_sha256=hashlib.sha256(proof.encode()).hexdigest(), readback_at=time.time())
        verified.append(intent['post_id'])
    if not acknowledged and not verified:
        return {'verified':[], 'acknowledged':[]} if index is not None else {'verified':[], 'reason':'posts rejected'}
    try:
        atomic_json(path, record)
    except (OSError, ValueError, TypeError):
        return {'verified':[], 'reason':'record write failed; nothing changed'}
    result = {'verified':verified, 'acknowledged':acknowledged}
    if index is None:
        result['reason'] = 'posts rejected'
    return result


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('maybe-report').add_argument('--state', required=True)
    sub.add_parser('reconcile').add_argument('--state', required=True)
    args = ap.parse_args()
    run = maybe_report if args.cmd == 'maybe-report' else reconcile
    print(json.dumps(run(args.state), ensure_ascii=False))
