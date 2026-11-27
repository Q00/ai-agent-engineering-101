"""Exact-evidence supply result correlation for restock-pending rows. Offline fixtures shaped like the live kit log
(scripts/market_kit.py restock(): hook stdout as '[buyer] <line>', one '[supply]' line per buy entry)."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

UTC = lambda t: int(t) % 86400  # seconds of day; fixtures use UTC so stamps are deterministic
AT = 36217.743  # 10:03:37.743 UTC on day 0
NAME = {'A':'Alpha item', 'B':'Bravo item'}
CATALOG = {k:{'name':v, 'category':'c'+k, 'kind':'goods', 'list_price':10000} for k,v in NAME.items()}


def row(sku, at=AT, **kw):
    r = {'sku':sku, 'at':at, 'reserve':10000, 'category':'c'+sku, 'policy_version':'bounded-v2',
         'baseline_unit_ids':[], 'matched_unit_id':None}
    r.update(kw)
    return r


def write_log(p, lines):
    path = Path(p)/'market-kit.log'
    path.write_bytes(''.join(l+'\n' for l in lines).encode('utf-8'))
    return path


class LegacyCorrelationTests(unittest.TestCase):
    def test_429_closed_refusal_resolves_rows_with_byte_evidence(self):
        import supply_journal
        refusal = ("10:04:47 [supply] {'sku': 'A', 'qty': 2} refused: 429 {\"error\":\"supply is closed; the next "
                   "restock window opens in 7 s\",\"wait\":7.3,\"opens_at\":36287,\"closes_at\":36467}")
        lines = ['10:03:20 [kit] 50 shops in .market\\shops.json, running market_buyer',
                 '10:03:37 [buyer] {"buy": [{"sku": "A", "qty": 2}, {"sku": "B", "qty": 1}]}',
                 '10:04:01 [order o-1] c, budget 1, round 3', refusal,
                 '10:04:47 [supply] 1 x Bravo item for 6000, balance 100']
        with tempfile.TemporaryDirectory() as d:
            path = write_log(d, lines)
            got = supply_journal.correlate_legacy([row('A'), row('A')], supply_journal.scan(path), CATALOG, sod=UTC)
            raw = path.read_bytes()
        self.assertEqual([g['status'] for g in got], ['refused', 'refused'])
        ev = got[0]['result']
        self.assertEqual(ev['line'], 4)
        self.assertEqual(raw[ev['offset']:ev['offset']+ev['length']].decode('utf-8'), refusal)
        self.assertEqual(ev['sha256'], hashlib.sha256(refusal.encode('utf-8')).hexdigest())
        self.assertEqual(got[0]['anchor']['line'], 2)
        self.assertEqual(got[0]['http_status'], 429)

    def test_success_line_needs_exact_qty_and_catalog_name(self):
        import supply_journal
        lines = ['10:03:37 [buyer] {"buy": [{"sku": "A", "qty": 2}, {"sku": "B", "qty": 1}]}',
                 '10:03:40 [supply] 2 x Alpha item for 13121, balance 5569199',
                 '10:03:50 [supply] 1 x Alpha item for 6000, balance 5563199']  # B's line names another SKU
        with tempfile.TemporaryDirectory() as d:
            got = supply_journal.correlate_legacy([row('A'), row('A'), row('B')],
                                                  supply_journal.scan(write_log(d, lines)), CATALOG, sod=UTC)
        self.assertEqual([g['status'] for g in got], ['supplied', 'supplied', 'unknown'])
        self.assertEqual(got[0]['total'], 13121)
        self.assertEqual(got[0]['result']['line'], 2)

    def test_429_opens_at_must_agree_with_plan_time(self):
        import supply_journal
        lines = ['10:03:37 [buyer] {"buy": [{"sku": "A", "qty": 1}]}',
                 "10:04:47 [supply] {'sku': 'A', 'qty': 1} refused: 429 {\"error\":\"supply is closed\",\"wait\":7,"
                 "\"opens_at\":%d,\"closes_at\":1}" % (AT+86400)]  # a different day's window: not this request
        with tempfile.TemporaryDirectory() as d:
            got = supply_journal.correlate_legacy([row('A')], supply_journal.scan(write_log(d, lines)), CATALOG, sod=UTC)
        self.assertEqual(got[0]['status'], 'unknown')

    def test_unprovable_results_stay_unknown(self):
        import supply_journal
        cases = {
            '502': ['10:03:37 [buyer] {"buy": [{"sku": "A", "qty": 1}]}', "10:03:59 [supply] {'sku': 'A', 'qty': 1} refused: 502 "],
            'missing': ['10:03:37 [buyer] {"buy": [{"sku": "A", "qty": 1}]}', '10:04:37 [buyer] {"buy": []}',
                        '10:04:40 [supply] 1 x Alpha item for 1, balance 1'],
            'restart': ['10:03:37 [buyer] {"buy": [{"sku": "A", "qty": 1}]}', '10:03:50 [kit] market_kit 3.1, C:\\x',
                        '10:04:40 [supply] 1 x Alpha item for 1, balance 1'],
            'ambiguous': ['10:03:37 [buyer] {"buy": [{"sku": "A", "qty": 1}]}', '10:03:40 [supply] 1 x Alpha item for 1, balance 1',
                          '10:03:38 [buyer] {"buy": [{"sku": "A", "qty": 1}]}', "10:03:41 [supply] {'sku': 'A', 'qty': 1} refused: 429 {\"error\":\"x\"}"],
            'one_arrival_two_rows': ['10:03:37 [buyer] {"buy": [{"sku": "A", "qty": 1}]}', '10:03:40 [supply] 1 x Alpha item for 1, balance 1'],
        }
        for name, lines in cases.items():
            rows = [row('A'), row('A')] if name == 'one_arrival_two_rows' else [row('A')]
            with tempfile.TemporaryDirectory() as d:
                got = supply_journal.correlate_legacy(rows, supply_journal.scan(write_log(d, lines)), CATALOG, sod=UTC)
            self.assertEqual({g['status'] for g in got}, {'unknown'}, name)


class FutureJournalTests(unittest.TestCase):
    """Future rows: unique hook invocation marker + request index, resolved only from that invocation's own segment."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = self.root/'.market'
        self.state.mkdir()
        (self.root/'logs').mkdir()
        self.kit = self.root/'logs'/'market-kit.log'
        self.kit.write_bytes(b'09:00:00 [kit] market_kit 3.1, C:\\x\n')

    def tearDown(self):
        self.tmp.cleanup()

    def append(self, *lines, path=None):
        with open(path or self.kit, 'ab') as f:
            f.write(''.join(l+'\n' for l in lines).encode('utf-8'))

    def invoke(self, buy, now=AT):
        import supply_journal
        inv = supply_journal.begin(self.state, now)
        rows = [row(e['sku'], at=now, request=supply_journal.request(inv, i, k, e))
                for i, e in enumerate(buy) for k in range(e['qty'])]
        self.append('10:00:00 [buyer] '+supply_journal.marker(inv), '10:00:01 [buyer] '+supply_journal.buy_line(inv, buy))
        return inv, rows

    def test_marker_and_buy_line_carry_unique_invocation(self):
        import supply_journal
        a, b = supply_journal.begin(self.state, AT), supply_journal.begin(self.state, AT)
        self.assertNotEqual(a['invocation'], b['invocation'])
        self.assertEqual(a['log_offset'], self.kit.stat().st_size)
        j = json.loads(supply_journal.buy_line(a, [{'sku':'A', 'qty':1}]))
        self.assertEqual(j, {'buy':[{'sku':'A', 'qty':1}], 'invocation':a['invocation']})
        self.assertEqual(supply_journal.marker(a), '[restock] invocation '+a['invocation'])

    def test_refused_releases_and_supplied_stays_counted_with_byte_evidence(self):
        import supply_journal
        _, rows = self.invoke([{'sku':'A', 'qty':2}, {'sku':'B', 'qty':1}])
        refusal = "10:00:40 [supply] {'sku': 'A', 'qty': 2} refused: 429 {\"error\":\"supply is closed\",\"wait\":7}"
        self.append('10:00:02 [buyer] {"shop": "x"}', refusal, '10:00:41 [supply] 1 x Bravo item for 6000, balance 1')
        audit = supply_journal.resolve(self.state, rows, CATALOG)
        self.assertEqual([r.get('supply_result') for r in rows], ['refused', 'refused', 'supplied'])
        self.assertIsNone(rows[2]['matched_unit_id'])  # supplied, but sold identity unknown: still counted
        self.assertEqual(len(audit), 3)
        ev = rows[0]['supply_evidence']['result']
        raw = self.kit.read_bytes()
        self.assertEqual(raw[ev['offset']:ev['offset']+ev['length']].decode('utf-8'), refusal)
        self.assertEqual(ev['sha256'], hashlib.sha256(refusal.encode('utf-8')).hexdigest())
        self.assertEqual(supply_journal.resolve(self.state, rows, CATALOG), [])  # resolved once

    def test_402_marks_later_entries_not_attempted(self):
        import supply_journal
        _, rows = self.invoke([{'sku':'A', 'qty':1}, {'sku':'B', 'qty':1}])
        self.append("10:00:40 [supply] {'sku': 'A', 'qty': 1} refused: 402 {\"error\":\"insufficient balance\"}",
                    '10:01:00 [buyer] [restock] invocation next')
        supply_journal.resolve(self.state, rows, CATALOG)
        self.assertEqual([r.get('supply_result') for r in rows], ['refused', 'not_attempted'])

    def test_incomplete_restart_interleaved_or_mismatched_segments_stay_unknown(self):
        import supply_journal
        A = "10:00:40 [supply] {'sku': 'A', 'qty': 1} refused: 429 {\"error\":\"x\"}"
        cases = {'no_result_yet':[], 'restart':['10:00:30 [kit] market_kit 3.1, C:\\x', A],
                 'next_invocation_first':['10:01:00 [buyer] [restock] invocation other', A],
                 'thread_died':['10:01:00 [buyer] [restock] invocation other'],
                 'wrong_sku':["10:00:40 [supply] {'sku': 'B', 'qty': 1} refused: 429 {\"error\":\"x\"}"],
                 'proxy_502':["10:00:40 [supply] {'sku': 'A', 'qty': 1} refused: 502 <html>"]}
        for name, extra in cases.items():
            self.kit.write_bytes(b'')
            _, rows = self.invoke([{'sku':'A', 'qty':1}])
            self.append(*extra)
            self.assertEqual(supply_journal.resolve(self.state, rows, CATALOG), [], name)
            self.assertNotIn('supply_result', rows[0], name)
        self.kit.write_bytes(b'')
        inv, rows = self.invoke([{'sku':'A', 'qty':1}])
        self.append('10:00:01 [buyer] '+supply_journal.buy_line(inv, [{'sku':'A', 'qty':1}]), A)  # duplicate anchor
        self.assertEqual(supply_journal.resolve(self.state, rows, CATALOG), [])
        self.kit.write_bytes(b'')
        inv, rows = self.invoke([{'sku':'A', 'qty':1}])
        self.append(A)
        rows[0]['request']['qty'] = 2  # row's entry does not match the logged buy entry
        self.assertEqual(supply_journal.resolve(self.state, rows, CATALOG), [])

    def test_rotated_log_is_read_from_recorded_offset(self):
        import supply_journal
        _, rows = self.invoke([{'sku':'A', 'qty':1}])
        self.append("10:00:40 [supply] {'sku': 'A', 'qty': 1} refused: 429 {\"error\":\"x\"}")
        self.kit.replace(self.kit.with_suffix('.log.1'))
        self.kit.write_bytes(b'10:02:00 [buyer] [restock] invocation later\n')
        supply_journal.resolve(self.state, rows, CATALOG)
        self.assertEqual(rows[0].get('supply_result'), 'refused')
        self.assertTrue(rows[0]['supply_evidence']['result']['file'].endswith('market-kit.log.1'))


class HookIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = self.root/'.market'
        self.state.mkdir()
        (self.root/'logs').mkdir()
        (self.root/'logs'/'market-kit.log').write_bytes(b'')

    def tearDown(self):
        self.tmp.cleanup()

    def test_unapproved_run_journals_invocation_and_holds_without_touching_pending(self):
        import restock_r3
        pending = [row('A'), row('B', matched_unit_id='u-1', reserve=0)]
        (self.state/'restock-pending.json').write_text(json.dumps(pending))
        before = (self.state/'restock-pending.json').read_bytes()
        out = restock_r3.run(self.state, approved=False, now=AT)
        self.assertEqual((self.state/'restock-pending.json').read_bytes(), before)
        rec = [json.loads(l) for l in (self.state/'restock-invocations.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(len(rec), 1)
        self.assertEqual(out[0], '[restock] invocation '+rec[0]['invocation'])
        self.assertEqual(json.loads(out[-1]), {'buy':[], 'invocation':rec[0]['invocation']})
        self.assertEqual(rec[0]['holds']['count'], 1)
        self.assertEqual(rec[0]['holds']['reserve'], 10000)
        self.assertFalse(rec[0]['approved'])

    def test_gate_binds_supply_journal_source(self):
        import restock_r3
        src = Path(restock_r3.__file__).resolve().parent
        h = {n:hashlib.sha256((src/n).read_bytes()).hexdigest() for n in restock_r3.SOURCES}
        self.assertIn('supply_journal.py', restock_r3.SOURCES)
        gate = self.state/'seller-v2-approved.json'
        gate.write_text(json.dumps({'policy_version':'bounded-v2', 'approved':True, 'source_sha256':h}))
        self.assertTrue(restock_r3.approved(self.state))
        h.pop('supply_journal.py')
        gate.write_text(json.dumps({'policy_version':'bounded-v2', 'approved':True, 'source_sha256':h}))
        self.assertFalse(restock_r3.approved(self.state))

    def test_plan_rows_carry_request_and_prior_refusal_is_audited_before_release(self):
        import restock_r3, supply_journal
        kit = self.root/'logs'/'market-kit.log'
        inv0 = supply_journal.begin(self.state, AT-60)
        old = row('A', at=AT-60, request=supply_journal.request(inv0, 0, 0, {'sku':'A', 'qty':1}))
        kit.write_bytes(('10:00:00 [buyer] '+supply_journal.marker(inv0)+'\n10:00:01 [buyer] '
                         + supply_journal.buy_line(inv0, [{'sku':'A', 'qty':1}])+'\n'
                         "10:00:40 [supply] {'sku': 'A', 'qty': 1} refused: 429 {\"error\":\"x\"}\n").encode())
        (self.state/'restock-pending.json').write_text(json.dumps([old]))
        (self.state/'inventory.json').write_text(json.dumps({'round':3, 'balance':5000000, 'start':5000000, 'units':[]}))
        (self.state/'catalog.json').write_text(json.dumps(CATALOG))
        inv1 = supply_journal.begin(self.state, AT)
        got = restock_r3.plan(self.state, now=AT, invocation=inv1)
        pend = json.loads((self.state/'restock-pending.json').read_text())
        audit = [json.loads(l) for l in (self.state/'restock-supply-evidence.jsonl').read_text().splitlines()]
        self.assertEqual([a['supply_result'] for a in audit], ['refused'])
        self.assertEqual(audit[0]['row']['request']['invocation'], inv0['invocation'])
        self.assertTrue(got['buy'])
        self.assertEqual({r['request']['invocation'] for r in pend}, {inv1['invocation']})
        self.assertEqual([(r['request']['index'], r['request']['unit']) for r in pend],
                         [(i, k) for i, e in enumerate(got['buy']) for k in range(e['qty'])])


if __name__ == '__main__':
    unittest.main()
