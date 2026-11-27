"""RED: explicit bounded-v2 forum policy (60 min interval) while legacy {"enabled": true} keeps 12h.

v2 intent shape pinned here (forum-report.json -> 'intents' values; key naming is the module's choice):
    {'at': <finite number>, 'status': 'intent'|'queued', 'sha256': <64 hex>,
     'policy_version': 'bounded-v2',
     'source_ids': [own ledger seq, *peer seqs in post order],   # equals the outbox refs
     'coverage': {'seq_min': int, 'seq_max': int, 'rows': <supplied rows observed, own included>,
                  'sha256': <64 hex digest of the observed snapshot>}}
Content dedupe is over policy version + facts (SKU, price, grade, warranty), never the ledger id.
A stored v2 intent must also be self-consistent: source_rows seqs == source_ids, facts and sha256 derive from
source_rows[0], and post_sha256 is the digest of the post re-rendered from them. Anything else fails closed.

reconcile(state, posts=None) (RED: new public function) reads complete lines of outbox.sent.jsonl:
    {'sent': <exact outbox line {'post','refs','as':'buyer'}>, 'status': <int>, 'answer': '{"post": <int id>}'}
A 2xx line whose 'sent' equals a queued intent's post/refs exactly marks it 'acknowledged' with post_id.
It becomes 'verified' only when the public post with that id has author fair-furniture, owner kimchyoungman,
role buyer, the exact text, and refs [{'seq', 'found': True, 'kind': 'settle'}] equal to source_ids in order.
Returns {'verified': [post ids newly verified by this call], ...}; uses forum-report.lock and atomic_json.
"""
import http.client
import json
import math
import re
import statistics
import sys
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_forum_policy_v1 import NAME, SHOP, T0, make_state, no_network, outbox, settle

V2 = {'enabled':True, 'policy_version':'bounded-v2', 'minimum_interval_minutes':60, 'max_intents_per_24h':8}
HEX = re.compile(r'[0-9a-f]{64}')
DAY = 24*3600
CLAIMS = ('cheap', 'safe', 'overpa', 'best', 'recommend', 'guarantee', 'scam')


def set_policy(state, policy):
    (state/'forum-policy.json').write_text(json.dumps(policy), encoding='utf-8')


def add_paid(state, order, sku, price, ledger):
    with open(state/'payments.jsonl', 'a', encoding='utf-8') as f:
        f.write(json.dumps(dict(order=order, shop=SHOP, item=sku, price=price, paid=True, ledger=ledger))+'\n')


def record(state):
    path = state/'forum-report.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None


def intents(state):
    rec = record(state)
    return list(rec['intents'].values()) if rec else []


def peer(seq, buyer, price, sku='SKU123', grade='A', warranty=6, drop=(), **top):
    row = settle(seq, 'p%d' % seq, sku, price, buyer=buyer, shop='https://peer-%d.trycloudflare.com' % seq,
                 grade=grade, warranty=warranty)
    for key in drop:
        row['data'].pop(key)
    row.update(top)
    return row


def try_report(forum_r3, state, **kw):
    """Failure may be a non-queued result or an exception; either is acceptable."""
    try:
        return forum_r3.maybe_report(state, **kw)
    except Exception as error:  # noqa: BLE001 - only the effects on disk matter afterwards
        return {'queued':False, 'reason':repr(error)}


def try_reconcile(forum_r3, state, posts):
    try:
        return forum_r3.reconcile(state, posts=posts)
    except Exception as error:  # noqa: BLE001 - only the effects on disk matter afterwards
        return {'verified':[], 'reason':repr(error)}


def sent(line, status=200, answer='{"post":9001}'):
    return {'sent':dict(line), 'status':status, 'answer':answer}


def write_sent(state, entries, tail=''):
    text = ''.join(json.dumps(e, ensure_ascii=False)+'\n' for e in entries)+tail
    (state/'outbox.sent.jsonl').write_text(text, encoding='utf-8')


def public(line, post_id=9001, **over):
    post = {'id':post_id, 'author':'fair-furniture', 'owner':'kimchyoungman', 'role':'buyer', 'text':line['post'],
            'refs':[{'seq':s, 'found':True, 'kind':'settle'} for s in line['refs']]}
    post.update(over)
    return post


def queued_one(root):
    import forum_r3
    state = make_state(root, payments=[('o2', 'SKU123', 90000, 42)])
    set_policy(state, V2)
    assert forum_r3.maybe_report(state, now=T0, rows=[settle(42, 'o2', 'SKU123', 90000)]).get('queued') is True
    [line] = outbox(state)
    return state, line


def intent_of(state, key='42'):
    return record(state)['intents'][key]


class ForumPolicyV2(unittest.TestCase):
    def test_v2_allows_new_paid_report_after_60_minutes_legacy_keeps_12h(self):
        import forum_r3
        with tempfile.TemporaryDirectory() as root, no_network():
            state = make_state(root, payments=[('o2', 'SKU123', 90000, 42)])
            set_policy(state, V2)
            rows = [settle(42, 'o2', 'SKU123', 90000), settle(50, 'o3', 'SKU777', 70000, grade='B')]
            self.assertIs(forum_r3.maybe_report(state, now=T0, rows=rows).get('queued'), True)
            add_paid(state, 'o3', 'SKU777', 70000, 50)
            self.assertFalse(forum_r3.maybe_report(state, now=T0+1800, rows=rows).get('queued'))  # 30 min: too soon
            self.assertEqual(len(outbox(state)), 1)
            self.assertIs(forum_r3.maybe_report(state, now=T0+3601, rows=rows).get('queued'), True)
            self.assertEqual([l['refs'] for l in outbox(state)], [[42], [50]])
        with tempfile.TemporaryDirectory() as root, no_network():
            state = make_state(root, payments=[('o2', 'SKU123', 90000, 42)])  # legacy {"enabled": true}
            rows = [settle(42, 'o2', 'SKU123', 90000), settle(50, 'o3', 'SKU777', 70000, grade='B')]
            self.assertIs(forum_r3.maybe_report(state, now=T0, rows=rows).get('queued'), True)
            add_paid(state, 'o3', 'SKU777', 70000, 50)
            self.assertFalse(forum_r3.maybe_report(state, now=T0+3601, rows=rows).get('queued'))
            self.assertEqual(len(outbox(state)), 1)

    def test_invalid_v2_policy_fails_closed(self):
        import forum_r3
        invalid = [dict(V2, policy_version='bounded-v3'),
                   {k:v for k, v in V2.items() if k != 'minimum_interval_minutes'},
                   {k:v for k, v in V2.items() if k != 'max_intents_per_24h'},
                   dict(V2, minimum_interval_minutes='60'),
                   dict(V2, minimum_interval_minutes=True),
                   dict(V2, max_intents_per_24h=0),
                   dict(V2, max_intents_per_24h=-1)]
        with no_network():
            for policy in invalid:
                with self.subTest(policy=policy), tempfile.TemporaryDirectory() as root:
                    state = make_state(root, payments=[('o2', 'SKU123', 90000, 42)])
                    set_policy(state, policy)
                    result = forum_r3.maybe_report(state, now=T0, rows=[settle(42, 'o2', 'SKU123', 90000)])
                    self.assertFalse(result.get('queued'))
                    self.assertEqual(outbox(state), [])

    def test_rolling_cap_counts_failed_append_intents_and_frees_after_24h(self):
        import forum_r3
        with tempfile.TemporaryDirectory() as root, no_network():
            state = make_state(root)
            set_policy(state, V2)
            rows = [settle(100+k, 'o%d' % k, 'SKU%d' % k, 50000+1000*k) for k in range(9)]
            (state/'outbox.jsonl').mkdir()  # first append fails; its durable intent still counts
            add_paid(state, 'o0', 'SKU0', 50000, 100)
            self.assertFalse(try_report(forum_r3, state, now=T0, rows=rows).get('queued'))
            (state/'outbox.jsonl').rmdir()
            self.assertEqual(len(intents(state)), 1)
            for k in range(1, 8):  # distinct SKUs, one per ~60 min
                add_paid(state, 'o%d' % k, 'SKU%d' % k, 50000+1000*k, 100+k)
                self.assertIs(forum_r3.maybe_report(state, now=T0+k*3601, rows=rows).get('queued'), True, k)
            self.assertEqual(len(outbox(state)), 7)
            self.assertEqual(len(intents(state)), 8)
            add_paid(state, 'o8', 'SKU8', 58000, 108)
            self.assertFalse(forum_r3.maybe_report(state, now=T0+8*3601, rows=rows).get('queued'))  # 9th in 24h
            self.assertEqual(len(outbox(state)), 7)
            self.assertEqual(len(intents(state)), 8)
            # the failed-append intent at T0 leaves the rolling window; seven remain, so one more fits
            self.assertIs(forum_r3.maybe_report(state, now=T0+DAY+1, rows=rows).get('queued'), True)
            self.assertEqual([l['refs'][0] for l in outbox(state)], list(range(101, 109)))

    def test_materially_identical_new_ledger_is_duplicate_content(self):
        import forum_r3
        with tempfile.TemporaryDirectory() as root, no_network():
            state = make_state(root, payments=[('o2', 'SKU123', 90000, 42)])
            set_policy(state, V2)
            rows = [settle(42, 'o2', 'SKU123', 90000), settle(43, 'o3', 'SKU123', 90000),
                    settle(44, 'o4', 'SKU123', 94500)]
            self.assertIs(forum_r3.maybe_report(state, now=T0, rows=rows).get('queued'), True)
            add_paid(state, 'o3', 'SKU123', 90000, 43)  # same SKU/price/grade/warranty; only the ledger is new
            self.assertFalse(forum_r3.maybe_report(state, now=T0+3601, rows=rows).get('queued'))
            self.assertEqual(len(outbox(state)), 1)
            self.assertEqual(len(intents(state)), 1)
            add_paid(state, 'o4', 'SKU123', 94500, 44)  # price +5%: materially new
            self.assertIs(forum_r3.maybe_report(state, now=T0+7202, rows=rows).get('queued'), True)
            self.assertEqual([l['refs'] for l in outbox(state)], [[42], [44]])

    def test_corrupt_record_and_invalid_now_fail_closed(self):
        import forum_r3

        def first(rec):
            return next(iter(rec['intents'].values()))

        def setter(key, value):
            return lambda rec: first(rec).__setitem__(key, value)

        mutations = {'status weird': setter('status', 'weird'), 'status None': setter('status', None),
                     'status int': setter('status', 1), 'at bool': setter('at', True),
                     'at str': setter('at', str(T0)), 'at NaN': setter('at', math.nan),
                     'at future': setter('at', T0+48*3600),
                     'content non-hash': lambda rec: rec['content'].append('not-a-hash'),
                     'content upper hex': lambda rec: rec['content'].append('G'*64),
                     'content int': lambda rec: rec['content'].append(123),
                     'intents list': lambda rec: rec.__setitem__('intents', [])}
        rows = [settle(42, 'o2', 'SKU123', 90000), settle(50, 'o3', 'SKU777', 70000, grade='B')]
        with no_network():
            for name, mutate in [('control', None), *mutations.items()]:
                with self.subTest(mutation=name), tempfile.TemporaryDirectory() as root:
                    state = make_state(root, payments=[('o2', 'SKU123', 90000, 42)])
                    set_policy(state, V2)
                    self.assertIs(forum_r3.maybe_report(state, now=T0, rows=rows).get('queued'), True)
                    add_paid(state, 'o3', 'SKU777', 70000, 50)
                    if mutate is None:
                        self.assertIs(forum_r3.maybe_report(state, now=T0+25*3600, rows=rows).get('queued'), True)
                        continue
                    rec = record(state)
                    mutate(rec)
                    (state/'forum-report.json').write_text(json.dumps(rec), encoding='utf-8')
                    before = (state/'forum-report.json').read_bytes()
                    self.assertFalse(try_report(forum_r3, state, now=T0+25*3600, rows=rows).get('queued'))
                    self.assertEqual(len(outbox(state)), 1)
                    self.assertEqual((state/'forum-report.json').read_bytes(), before)  # never reset
            for now in (math.nan, math.inf, -math.inf, True, False, str(T0)):
                with self.subTest(now=now), tempfile.TemporaryDirectory() as root:
                    state = make_state(root, payments=[('o2', 'SKU123', 90000, 42)])
                    set_policy(state, V2)
                    self.assertFalse(try_report(forum_r3, state, now=now, rows=rows).get('queued'))
                    self.assertEqual(outbox(state), [])
                    self.assertEqual(intents(state), [])

    def test_crash_after_append_before_queued_mark_never_duplicates(self):
        import forum_r3
        real, calls = forum_r3.atomic_json, []

        def crash_on_second(path, data):
            calls.append(Path(path).name)
            if len(calls) == 2:
                raise OSError('simulated crash before queued mark')
            return real(path, data)

        rows = [settle(42, 'o2', 'SKU123', 90000)]
        with tempfile.TemporaryDirectory() as root, no_network():
            state = make_state(root, payments=[('o2', 'SKU123', 90000, 42)])
            set_policy(state, V2)
            with mock.patch('forum_r3.atomic_json', side_effect=crash_on_second):
                self.assertFalse(try_report(forum_r3, state, now=T0, rows=rows).get('queued'))
            self.assertGreaterEqual(len(calls), 2)
            self.assertEqual(len(outbox(state)), 1)  # the append itself happened
            [intent] = intents(state)
            self.assertEqual(intent['status'], 'intent')  # preserved, not rolled back
            for later in (T0+3601, T0+25*3600):
                self.assertFalse(forum_r3.maybe_report(state, now=later, rows=rows).get('queued'))
            self.assertEqual(len(outbox(state)), 1)
            self.assertEqual(len(intents(state)), 1)

    def test_concurrent_callers_queue_once_and_held_lock_writes_nothing(self):
        import forum_r3
        rows = [settle(42, 'o2', 'SKU123', 90000)]
        with tempfile.TemporaryDirectory() as root, no_network():
            state = make_state(root, payments=[('o2', 'SKU123', 90000, 42)])
            set_policy(state, V2)
            barrier, results = threading.Barrier(8), []

            def call():
                barrier.wait()
                results.append(try_report(forum_r3, state, now=T0, rows=rows))

            threads = [threading.Thread(target=call) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
            self.assertEqual(len(results), 8)
            self.assertEqual(sum(r.get('queued') is True for r in results), 1)
            self.assertEqual(len(outbox(state)), 1)
            self.assertEqual(len(intents(state)), 1)
        with tempfile.TemporaryDirectory() as root, no_network():
            state = make_state(root, payments=[('o2', 'SKU123', 90000, 42)])
            set_policy(state, V2)
            (state/'forum-report.lock').mkdir()  # possibly live holder: never stolen
            result = forum_r3.maybe_report(state, now=T0, rows=rows)
            self.assertEqual((result.get('queued'), result.get('reason')), (False, 'locked'))
            self.assertEqual(outbox(state), [])
            self.assertIsNone(record(state))
            self.assertTrue((state/'forum-report.lock').is_dir())

    def test_get_failure_and_inexact_public_rows_queue_nothing_under_v2(self):
        import forum_r3
        bad_json = mock.MagicMock()
        bad_json.__enter__.return_value.read.return_value = b'not json'
        failures = [urllib.error.URLError('down'), TimeoutError('slow'),
                    http.client.RemoteDisconnected('closed'), None]  # None -> 200 with an unparseable body
        good = settle(42, 'o2', 'SKU123', 90000)
        with no_network():
            for failure in failures:
                with self.subTest(failure=failure), tempfile.TemporaryDirectory() as root:
                    state = make_state(root, payments=[('o2', 'SKU123', 90000, 42)])
                    set_policy(state, V2)
                    effect = {'return_value':bad_json} if failure is None else {'side_effect':failure}
                    with mock.patch('forum_r3.urllib.request.urlopen', **effect):
                        self.assertFalse(try_report(forum_r3, state, now=T0).get('queued'))
                    self.assertEqual(outbox(state), [])
                    self.assertEqual(intents(state), [])  # no evidence -> no intent burned
                    self.assertIs(forum_r3.maybe_report(state, now=T0+60, rows=[good]).get('queued'), True)
            nested_round = settle(42, 'o2', 'SKU123', 90000)
            del nested_round['round']
            nested_round['data']['round'] = 3
            price_key = settle(42, 'o2', 'SKU123', 90000)
            price_key['data']['price'] = price_key['data'].pop('amount')
            bad = [settle(42, 'o2', 'SKU123', 90000, buyer='other-buyer'),
                   settle(42, 'o2', 'SKU123', 89999),
                   settle(42, 'o9', 'SKU123', 90000),
                   settle(42, 'o2', 'SKU124', 90000),
                   settle(42, 'o2', 'SKU123', 90000, shop='https://other.trycloudflare.com'),
                   settle(42, 'o2', 'SKU123', 90000, kind='order'),
                   settle(41, 'o2', 'SKU123', 90000),
                   dict(settle(42, 'o2', 'SKU123', 90000), round=2),
                   nested_round, price_key]
            for row in bad:
                with self.subTest(row=row), tempfile.TemporaryDirectory() as root:
                    state = make_state(root, payments=[('o2', 'SKU123', 90000, 42)])
                    set_policy(state, V2)
                    self.assertFalse(forum_r3.maybe_report(state, now=T0, rows=[row]).get('queued'))
                    self.assertEqual(outbox(state), [])

    def test_v2_factual_template_without_cohort(self):
        import forum_r3
        rows = [settle(42, 'o2', 'SKU123', 90000), settle(50, 'o3', 'SKU777', 70000, grade='B')]
        with tempfile.TemporaryDirectory() as root, no_network():
            state = make_state(root, payments=[('o2', 'SKU123', 90000, 42)])
            set_policy(state, V2)
            self.assertIs(forum_r3.maybe_report(state, now=T0, rows=rows).get('queued'), True)
            [line] = outbox(state)
            self.assertEqual(set(line), {'post', 'refs', 'as'})
            self.assertEqual((line['refs'], line['as']), ([42], 'buyer'))
            text, low = line['post'], line['post'].lower()
            for fact in ('#42', 'SKU123', '90000'):
                self.assertIn(fact, text)
            self.assertRegex(text, r'(?i)grade\W+A\b')
            self.assertRegex(text, r'(?i)warranty\W+6\s*h')
            self.assertIn('durability unknown', low)
            for absent in ('median', 'first 5', 'peer', *CLAIMS):  # comparison omitted: no cohort
                self.assertNotIn(absent, low)
            for leak in (NAME, 'Secret', SHOP, 'shop-x', 'claim_honest', 'fair-furniture'):
                self.assertNotIn(leak, text)
            [intent] = intents(state)
            self.assertEqual(intent['status'], 'queued')
            self.assertEqual(intent['policy_version'], 'bounded-v2')
            self.assertEqual(intent['source_ids'], [42])
            self.assertRegex(intent['sha256'], HEX)
            coverage = intent['coverage']
            self.assertEqual((coverage['seq_min'], coverage['seq_max'], coverage['rows']), (42, 50, 2))
            self.assertRegex(coverage['sha256'], HEX)

    def test_v2_cohort_is_first_five_distinct_matching_peers_or_suppressed(self):
        import forum_r3
        own = settle(42, 'o2', 'SKU123', 90000)
        eligible = [peer(43, 'peer-buyer-01', 80000)]
        noise = [peer(44, 'fair-furniture', 1),                    # self, excluded
                 peer(45, 'peer-buyer-02', 2, grade='B'),
                 peer(46, 'peer-buyer-01', 3),                     # repeat buyer, excluded
                 peer(48, 'peer-buyer-04', 4, warranty=12),
                 peer(49, 'peer-buyer-05', 5, drop=('grade',)),
                 peer(50, 'peer-buyer-06', 6, drop=('warranty_hours',)),
                 peer(51, 'peer-buyer-07', 7, sku='SKU999'),
                 peer(52, 'peer-buyer-08', 8, kind='order'),
                 peer(53, 'peer-buyer-09', 9, round=2),
                 peer(57, 'peer-buyer-15', 11, grade=None)]
        eligible += [peer(47, 'peer-buyer-03', 85000), peer(54, 'peer-buyer-10', 100000),
                     peer(55, 'peer-buyer-11', 70000), peer(56, 'peer-buyer-12', 95000),
                     peer(58, 'peer-buyer-13', 10), peer(59, 'peer-buyer-14', 20)]  # 6th/7th: beyond first 5
        rows = sorted([own, *noise, *eligible], key=lambda r: r['seq'])
        first5 = sorted(eligible, key=lambda r: r['seq'])[:5]
        expected = statistics.median(r['data']['amount'] for r in first5)
        self.assertEqual(expected, 85000)
        with tempfile.TemporaryDirectory() as root, no_network():
            state = make_state(root, payments=[('o2', 'SKU123', 90000, 42)])
            set_policy(state, V2)
            self.assertIs(forum_r3.maybe_report(state, now=T0, rows=rows).get('queued'), True)
            [line] = outbox(state)
            refs = [42, *(r['seq'] for r in first5)]
            self.assertEqual(line['refs'], refs)
            text, low = line['post'], line['post'].lower()
            self.assertIn('median', low)
            self.assertIn(str(int(expected)), text)
            for disclosure in ('first 5', 'buyer names', 'buyer owners unknown', 'not market-wide'):
                self.assertIn(disclosure, low)
            for claim in CLAIMS:
                self.assertNotIn(claim, low)
            for leak in ('peer-buyer', 'fair-furniture', 'peer-4', SHOP, NAME):
                self.assertNotIn(leak, text)
            [intent] = intents(state)
            self.assertEqual(intent['policy_version'], 'bounded-v2')
            self.assertEqual(intent['source_ids'], refs)
            coverage = intent['coverage']
            self.assertEqual((coverage['seq_min'], coverage['seq_max'], coverage['rows']), (42, 59, len(rows)))
            self.assertRegex(coverage['sha256'], HEX)
        unknown_own = settle(42, 'o2', 'SKU123', 90000)
        del unknown_own['data']['grade']
        suppressed = {'four peers': [own, *noise, *sorted(eligible, key=lambda r: r['seq'])[:4]],
                      'unknown grade matches nothing': [unknown_own, *(peer(60+i, 'peer-buyer-2%d' % i, 1000,
                                                                            drop=('grade',)) for i in range(5))]}
        for name, cohort_rows in suppressed.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as root, no_network():
                state = make_state(root, payments=[('o2', 'SKU123', 90000, 42)])
                set_policy(state, V2)
                self.assertIs(forum_r3.maybe_report(state, now=T0, rows=cohort_rows).get('queued'), True)
                [line] = outbox(state)
                self.assertEqual(line['refs'], [42])
                for absent in ('median', 'first 5', 'peer'):
                    self.assertNotIn(absent, line['post'].lower())
                self.assertEqual(intents(state)[0]['source_ids'], [42])

    def test_new_paid_with_one_percent_price_move_is_not_materially_new(self):
        import forum_r3
        rows = [settle(42, 'o2', 'SKU123', 90000), settle(43, 'o3', 'SKU123', 90900), settle(44, 'o4', 'SKU123', 89100)]
        with tempfile.TemporaryDirectory() as root, no_network():
            state = make_state(root, payments=[('o2', 'SKU123', 90000, 42)])
            set_policy(state, V2)
            self.assertIs(forum_r3.maybe_report(state, now=T0, rows=rows).get('queued'), True)
            add_paid(state, 'o3', 'SKU123', 90900, 43)  # +1%: new ledger, new digest, same SKU/grade/warranty
            self.assertFalse(forum_r3.maybe_report(state, now=T0+3601, rows=rows).get('queued'))
            add_paid(state, 'o4', 'SKU123', 89100, 44)  # -1%
            self.assertFalse(forum_r3.maybe_report(state, now=T0+7202, rows=rows).get('queued'))
            self.assertEqual(len(outbox(state)), 1)
            self.assertEqual(len(intents(state)), 1)

    def test_snapshot_duplicate_seq_or_non_list_rows_are_rejected(self):
        import forum_r3
        own = settle(42, 'o2', 'SKU123', 90000)
        cases = {'identical duplicate own': [own, settle(42, 'o2', 'SKU123', 90000)],
                 'conflicting duplicate own': [own, settle(42, 'o2', 'SKU123', 90000, grade='B')],
                 'conflicting duplicate peer': [own, peer(43, 'peer-buyer-01', 80000), peer(43, 'peer-buyer-02', 1)],
                 'dict': {'entries':[own]}, 'empty dict': {}, 'tuple': (own,), 'str': json.dumps([own]), 'int': 42}
        with no_network():
            for name, rows in cases.items():
                with self.subTest(name), tempfile.TemporaryDirectory() as root:
                    state = make_state(root, payments=[('o2', 'SKU123', 90000, 42)])
                    set_policy(state, V2)
                    self.assertFalse(try_report(forum_r3, state, now=T0, rows=rows).get('queued'))
                    self.assertEqual(outbox(state), [])
                    self.assertEqual(intents(state), [])

    def test_corrupt_v2_post_hash_or_source_rows_fail_closed(self):
        import forum_r3

        def first(rec):
            return next(iter(rec['intents'].values()))

        def setter(key, value):
            return lambda rec: first(rec).__setitem__(key, value)

        def dropper(key):
            return lambda rec: first(rec).pop(key)

        def own_amount(rec):
            first(rec)['source_rows'][0]['data']['amount'] = 1

        def own_seq(rec):
            first(rec)['source_rows'][0]['seq'] = 41

        def extra_row(rec):
            first(rec)['source_rows'].append(peer(43, 'peer-buyer-01', 80000))

        def facts_price(rec):
            first(rec)['facts']['price'] = 1

        def other_content_hash(rec):  # well-formed and listed in content, but not the facts digest
            first(rec)['sha256'] = '1'*64
            rec['content'].append('1'*64)

        mutations = {'post_sha256 missing': dropper('post_sha256'), 'post_sha256 non-hex': setter('post_sha256', 'x'),
                     'post_sha256 other hex': setter('post_sha256', '0'*64), 'post_sha256 int': setter('post_sha256', 1),
                     'source_rows missing': dropper('source_rows'), 'source_rows dict': setter('source_rows', {}),
                     'source_rows empty': setter('source_rows', []), 'source_rows own seq': own_seq,
                     'source_rows own amount': own_amount, 'source_rows extra row': extra_row,
                     'facts price': facts_price, 'sha256 not facts digest': other_content_hash}
        rows = [settle(42, 'o2', 'SKU123', 90000), settle(50, 'o3', 'SKU777', 70000, grade='B')]
        with no_network():
            for name, mutate in [('control', None), *mutations.items()]:
                with self.subTest(mutation=name), tempfile.TemporaryDirectory() as root:
                    state = make_state(root, payments=[('o2', 'SKU123', 90000, 42)])
                    set_policy(state, V2)
                    self.assertIs(forum_r3.maybe_report(state, now=T0, rows=rows).get('queued'), True)
                    add_paid(state, 'o3', 'SKU777', 70000, 50)
                    if mutate is None:
                        self.assertIs(forum_r3.maybe_report(state, now=T0+25*3600, rows=rows).get('queued'), True)
                        continue
                    rec = record(state)
                    mutate(rec)
                    (state/'forum-report.json').write_text(json.dumps(rec), encoding='utf-8')
                    before = (state/'forum-report.json').read_bytes()
                    self.assertFalse(try_report(forum_r3, state, now=T0+25*3600, rows=rows).get('queued'))
                    self.assertEqual(len(outbox(state)), 1)
                    self.assertEqual((state/'forum-report.json').read_bytes(), before)  # never reset


class ForumReconcileV2(unittest.TestCase):
    def test_exact_ack_and_public_post_verifies_then_record_stays_usable(self):
        import forum_r3
        with tempfile.TemporaryDirectory() as root, no_network():
            state, line = queued_one(root)
            write_sent(state, [sent(line)])
            outbox_before = (state/'outbox.jsonl').read_bytes()
            sent_before = (state/'outbox.sent.jsonl').read_bytes()
            result = forum_r3.reconcile(state, posts=[public(line)])
            self.assertEqual(result.get('verified'), [9001])
            intent = intent_of(state)
            self.assertEqual((intent['status'], intent['post_id']), ('verified', 9001))
            self.assertEqual((state/'outbox.jsonl').read_bytes(), outbox_before)  # inputs never rewritten
            self.assertEqual((state/'outbox.sent.jsonl').read_bytes(), sent_before)
            # a verified intent is a valid record: rate window, dedupe and new reports keep working
            add_paid(state, 'o3', 'SKU777', 70000, 50)
            rows = [settle(42, 'o2', 'SKU123', 90000), settle(50, 'o3', 'SKU777', 70000, grade='B')]
            self.assertFalse(forum_r3.maybe_report(state, now=T0+1800, rows=rows).get('queued'))
            self.assertIs(forum_r3.maybe_report(state, now=T0+3601, rows=rows).get('queued'), True)
            self.assertEqual(intent_of(state)['status'], 'verified')

    def test_ack_without_public_post_is_acknowledged_then_verifies_later(self):
        import forum_r3
        with tempfile.TemporaryDirectory() as root, no_network():
            state, line = queued_one(root)
            write_sent(state, [sent(line)])
            self.assertEqual(forum_r3.reconcile(state, posts=[]).get('verified'), [])
            intent = intent_of(state)
            self.assertEqual((intent['status'], intent['post_id']), ('acknowledged', 9001))
            self.assertEqual(forum_r3.reconcile(state, posts=[public(line)]).get('verified'), [9001])
            self.assertEqual(intent_of(state)['status'], 'verified')

    def test_two_xx_statuses_acknowledge_others_never(self):
        import forum_r3
        with no_network():
            for status in (200, 201, 500, 404, 302, '200', True, None, 200.0):
                with self.subTest(status=status), tempfile.TemporaryDirectory() as root:
                    state, line = queued_one(root)
                    write_sent(state, [sent(line, status=status)])
                    result = forum_r3.reconcile(state, posts=[])
                    self.assertEqual(result.get('verified'), [])
                    intent = intent_of(state)
                    if status in (200, 201) and type(status) is int:
                        self.assertEqual((intent['status'], intent['post_id']), ('acknowledged', 9001))
                    else:
                        self.assertEqual(intent['status'], 'queued')
                        self.assertNotIn('post_id', intent)
                    # a failed send never verifies even when a matching public post exists
                    if not (type(status) is int and 200 <= status < 300):
                        self.assertEqual(forum_r3.reconcile(state, posts=[public(line)]).get('verified'), [])
                        self.assertEqual(intent_of(state)['status'], 'queued')

    def test_inexact_sent_line_never_acknowledges(self):
        import forum_r3

        def changed(line, **over):
            return dict(line, **over)

        with no_network():
            with tempfile.TemporaryDirectory() as root:
                _, line = queued_one(root)
            cases = {'post text': changed(line, post=line['post']+' '), 'refs': changed(line, refs=[43]),
                     'refs extra': changed(line, refs=[42, 43]), 'as seller': changed(line, **{'as':'seller'}),
                     'extra key': changed(line, extra=1), 'missing as': {'post':line['post'], 'refs':line['refs']}}
            for name, wrong in cases.items():
                with self.subTest(name), tempfile.TemporaryDirectory() as root:
                    state, line = queued_one(root)
                    write_sent(state, [sent(wrong)])
                    self.assertEqual(forum_r3.reconcile(state, posts=[public(line)]).get('verified'), [])
                    intent = intent_of(state)
                    self.assertEqual(intent['status'], 'queued')
                    self.assertNotIn('post_id', intent)

    def test_wrong_public_post_never_verifies(self):
        import forum_r3
        with no_network():
            with tempfile.TemporaryDirectory() as root:
                _, line = queued_one(root)
            refs = public(line)['refs']
            cases = {'owner': public(line, owner='someone-else'), 'role': public(line, role='seller'),
                     'author': public(line, author='other-buyer'), 'text': public(line, text=line['post']+'!'),
                     'text prefix': public(line, text=line['post'][:-1]), 'id': public(line, post_id=9002),
                     'id str': public(line, post_id='9001'), 'refs found false': public(line, refs=[dict(refs[0], found=False)]),
                     'refs kind': public(line, refs=[dict(refs[0], kind='order')]),
                     'refs seq': public(line, refs=[dict(refs[0], seq=41)]),
                     'refs extra': public(line, refs=[*refs, {'seq':43, 'found':True, 'kind':'settle'}]),
                     'refs empty': public(line, refs=[]), 'refs ints': public(line, refs=[42]),
                     'refs missing': {k:v for k, v in public(line).items() if k != 'refs'}}
            for name, post in cases.items():
                with self.subTest(name), tempfile.TemporaryDirectory() as root:
                    state, line = queued_one(root)
                    write_sent(state, [sent(line)])
                    self.assertEqual(forum_r3.reconcile(state, posts=[post]).get('verified'), [])
                    intent = intent_of(state)
                    self.assertEqual((intent['status'], intent['post_id']), ('acknowledged', 9001))
            with self.subTest('duplicate public id'), tempfile.TemporaryDirectory() as root:
                state, line = queued_one(root)
                write_sent(state, [sent(line)])
                posts = [public(line), public(line, text='other text')]  # same id twice: ambiguous source
                self.assertEqual(forum_r3.reconcile(state, posts=posts).get('verified'), [])
                self.assertNotEqual(intent_of(state)['status'], 'verified')
            for name, posts in {'dict': {'posts':[public(line)]}, 'str': 'posts', 'non-dict item': [42]}.items():
                with self.subTest(name), tempfile.TemporaryDirectory() as root:
                    state, line = queued_one(root)
                    write_sent(state, [sent(line)])
                    self.assertEqual(try_reconcile(forum_r3, state, posts).get('verified'), [])
                    self.assertNotEqual(intent_of(state)['status'], 'verified')

    def test_corrupt_or_truncated_sent_lines_never_crash_or_verify(self):
        import forum_r3
        with no_network():
            for answer in ('not json', '{"post":90', '{"post":"9001"}', '{"post":true}', '{"post":0}',
                           '{"post":-1}', '{"post":9001.0}', '[9001]', '{}', '', None, 9001, {'post':9001}):
                with self.subTest(answer=answer), tempfile.TemporaryDirectory() as root:
                    state, line = queued_one(root)
                    write_sent(state, [sent(line, answer=answer)])
                    self.assertEqual(forum_r3.reconcile(state, posts=[public(line)]).get('verified'), [])
                    intent = intent_of(state)
                    self.assertEqual(intent['status'], 'queued')
                    self.assertNotIn('post_id', intent)
            for name in ('complete entry without newline', 'cut mid-line', 'garbage lines'):
                with self.subTest(name), tempfile.TemporaryDirectory() as root:
                    state, line = queued_one(root)
                    raw = json.dumps(sent(line), ensure_ascii=False)
                    if name == 'complete entry without newline':
                        write_sent(state, [], tail=raw)
                    elif name == 'cut mid-line':
                        write_sent(state, [], tail=raw[:len(raw)//2])
                    else:
                        (state/'outbox.sent.jsonl').write_bytes(b'\xff\xfe\n[]\nnull\n{"sent":1}\n'+raw[:-3].encode()+b'\n')
                    self.assertEqual(forum_r3.reconcile(state, posts=[public(line)]).get('verified'), [])
                    self.assertEqual(intent_of(state)['status'], 'queued')
            with self.subTest('no sent file'), tempfile.TemporaryDirectory() as root:
                state, line = queued_one(root)
                self.assertEqual(forum_r3.reconcile(state, posts=[public(line)]).get('verified'), [])
                self.assertEqual(intent_of(state)['status'], 'queued')

    def test_matches_only_the_intent_whose_line_was_sent(self):
        import forum_r3
        rows = [settle(42, 'o2', 'SKU123', 90000), settle(50, 'o3', 'SKU777', 70000, grade='B')]
        with tempfile.TemporaryDirectory() as root, no_network():
            state = make_state(root, payments=[('o2', 'SKU123', 90000, 42)])
            set_policy(state, V2)
            self.assertIs(forum_r3.maybe_report(state, now=T0, rows=rows).get('queued'), True)
            add_paid(state, 'o3', 'SKU777', 70000, 50)
            self.assertIs(forum_r3.maybe_report(state, now=T0+3601, rows=rows).get('queued'), True)
            first, second = outbox(state)
            write_sent(state, [sent(second, answer='{"post":9002}')])
            result = forum_r3.reconcile(state, posts=[public(first), public(second, post_id=9002)])
            self.assertEqual(result.get('verified'), [9002])
            self.assertEqual((intent_of(state, '50')['status'], intent_of(state, '50')['post_id']), ('verified', 9002))
            self.assertEqual(intent_of(state, '42')['status'], 'queued')
            self.assertNotIn('post_id', intent_of(state, '42'))

    def test_repeat_and_duplicate_sent_lines_dedupe(self):
        import forum_r3
        with tempfile.TemporaryDirectory() as root, no_network():
            state, line = queued_one(root)
            write_sent(state, [sent(line), sent(line)])  # kit retried and logged twice
            self.assertEqual(forum_r3.reconcile(state, posts=[public(line)]).get('verified'), [9001])
            before = (state/'forum-report.json').read_bytes()
            self.assertEqual(forum_r3.reconcile(state, posts=[public(line)]).get('verified'), [])  # already verified
            self.assertEqual((state/'forum-report.json').read_bytes(), before)
        with tempfile.TemporaryDirectory() as root, no_network():
            state, line = queued_one(root)
            write_sent(state, [sent(line), sent(line, answer='{"post":9002}')])  # conflicting ids for one line
            result = forum_r3.reconcile(state, posts=[public(line), public(line, post_id=9002)])
            self.assertEqual(result.get('verified'), [])
            self.assertNotEqual(intent_of(state)['status'], 'verified')

    def test_concurrent_reconcile_verifies_once_and_held_lock_writes_nothing(self):
        import forum_r3
        with tempfile.TemporaryDirectory() as root, no_network():
            state, line = queued_one(root)
            write_sent(state, [sent(line)])
            barrier, results = threading.Barrier(8), []

            def call():
                barrier.wait()
                results.append(try_reconcile(forum_r3, state, [public(line)]))

            threads = [threading.Thread(target=call) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
            self.assertEqual(len(results), 8)
            self.assertEqual(sum(r.get('verified') == [9001] for r in results), 1)
            self.assertEqual(intent_of(state)['status'], 'verified')
            self.assertFalse((state/'forum-report.lock').exists())
        with tempfile.TemporaryDirectory() as root, no_network():
            state, line = queued_one(root)
            write_sent(state, [sent(line)])
            before = (state/'forum-report.json').read_bytes()
            (state/'forum-report.lock').mkdir()  # possibly live holder: never stolen
            result = forum_r3.reconcile(state, posts=[public(line)])
            self.assertEqual(result.get('reason'), 'locked')
            self.assertEqual(result.get('verified', []), [])
            self.assertEqual((state/'forum-report.json').read_bytes(), before)
            self.assertTrue((state/'forum-report.lock').is_dir())

    def test_writes_through_atomic_json_and_failed_write_keeps_record(self):
        import forum_r3
        with tempfile.TemporaryDirectory() as root, no_network():
            state, line = queued_one(root)
            write_sent(state, [sent(line)])
            with mock.patch('forum_r3.atomic_json', wraps=forum_r3.atomic_json) as spy:
                self.assertEqual(forum_r3.reconcile(state, posts=[public(line)]).get('verified'), [9001])
            self.assertIn('forum-report.json', [Path(c.args[0]).name for c in spy.call_args_list])
        with tempfile.TemporaryDirectory() as root, no_network():
            state, line = queued_one(root)
            write_sent(state, [sent(line)])
            before = (state/'forum-report.json').read_bytes()
            with mock.patch('forum_r3.atomic_json', side_effect=OSError('disk full')):
                self.assertEqual(try_reconcile(forum_r3, state, [public(line)]).get('verified'), [])
            self.assertEqual((state/'forum-report.json').read_bytes(), before)
            self.assertFalse((state/'forum-report.lock').exists())

    def test_corrupt_record_is_never_reconciled_or_reset(self):
        import forum_r3
        with tempfile.TemporaryDirectory() as root, no_network():
            state, line = queued_one(root)
            write_sent(state, [sent(line)])
            rec = record(state)
            rec['intents']['42']['status'] = 'weird'
            (state/'forum-report.json').write_text(json.dumps(rec), encoding='utf-8')
            before = (state/'forum-report.json').read_bytes()
            self.assertEqual(try_reconcile(forum_r3, state, [public(line)]).get('verified'), [])
            self.assertEqual((state/'forum-report.json').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
