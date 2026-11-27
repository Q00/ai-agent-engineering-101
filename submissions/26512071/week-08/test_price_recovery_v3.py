"""Price recovery v3 (public ledger ingestion reliability + anchor tiers). No network, no model calls, no keys.

Slice 1: bounded GET (5 s socket timeout + 5 s total read deadline), failure journal, backoff (no retry hammer),
last verified data kept on a transient failure with explicit error + source age, hard expiry at FRESH_S.
REAL_ROW is copied verbatim from logs/price-recovery/ledger-probe.json (HTTP 200, 2026-11-05 10:27).
"""
import http.server
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from unittest.mock import patch

import public_evidence
from test_market_pricing_v2 import UNIT, settle

REAL_URLOPEN = urllib.request.urlopen  # loopback-only regression test; Isolated still blocks any other fetch

REAL_ROW = {"seq": 2742665, "ts": 1793842023.654, "kind": "settle", "round": 3, "data": {
    "order": "o-e0bab40360d8", "buyer": "buyer-02", "shop": "https://semester-nissan-stores-james.trycloudflare.com",
    "item": "G53e73f8c", "amount": 13666, "checkout_hash": "_h_LcnW61j-SVrvDrv_bzrWFZxNUUga5hbQf0VdMQw0",
    "unit": "u-46a830633eab", "sku": "G53e73f8c",
    "name": "이솔홈 도트 쉬폰 원피스 앞치마", "grade": "A",
    "expires_at": None, "claim": "A", "claim_honest": True, "warranty_hours": None},
    "prev": "9e5c595afcc090b14c7ad070389ef13b36c9ca5daa80b75fc47230df6de32fe8",
    "hash": "252f00f52e21600b33e1381366f1e43d36c85787f4b2872b2c5df4fa18b3fc96"}
NOW = REAL_ROW['ts']+60
REAL_UNIT = dict(unit='u-real', sku='G53e73f8c', name='real', category='test', grade='A', kind='goods',
                 cost=9000, list_price=16000, expires_at=None, supplied_at=1)
BASE = [settle(101, 60, NOW-300), settle(102, 65, NOW-200), settle(103, 70, NOW-100)]


class Isolated(unittest.TestCase):
    def setUp(self):
        for p in (patch.object(public_evidence, 'RUNTIME_EVENTS', Path(tempfile.gettempdir(), 'no-such-runtime-events.jsonl')),
                  patch('urllib.request.urlopen', side_effect=AssertionError('tests must not fetch'))):
            p.start()
            self.addCleanup(p.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def refresh(self, now, rows=None, exc=None):
        with patch.object(public_evidence, '_fetch', side_effect=exc, return_value=rows) as fetch:
            cache = public_evidence.refresh(self.tmp.name, now=now)
        return cache, fetch.call_count


class FetchBoundsTests(Isolated):
    def test_get_uses_five_second_timeout_and_bounded_read(self):
        seen = {}

        def opener(req, timeout):
            seen.update(url=req.full_url, timeout=timeout)
            return io.BytesIO(json.dumps([REAL_ROW]).encode())
        with patch('public_evidence.urllib.request.urlopen', side_effect=opener):
            rows = public_evidence._fetch()
        self.assertEqual(seen['timeout'], 5)
        self.assertEqual(public_evidence.TIMEOUT, 5)
        self.assertEqual(seen['url'], public_evidence.SOURCE_URL)
        self.assertEqual(rows, [REAL_ROW])

    def test_slow_drip_body_hits_total_read_deadline(self):
        class Drip(io.BytesIO):
            def read(self, size=-1):
                return super().read(min(size, 4))
        clock = iter([0.0]+[0.0, 1.0, 2.0, 3.0, 4.0, 5.5]+[9.0]*50)
        with patch('public_evidence.urllib.request.urlopen', return_value=Drip(json.dumps([REAL_ROW]).encode())), \
                patch('public_evidence.time.monotonic', side_effect=lambda: next(clock)):
            with self.assertRaises(TimeoutError):
                public_evidence._fetch()


class LastKnownGoodTests(Isolated):
    def test_failure_journal_keeps_last_verified_rows_with_error_and_source_age(self):
        self.refresh(NOW, BASE+[REAL_ROW])
        cache, _ = self.refresh(NOW+60, exc=TimeoutError('The read operation timed out'))
        self.assertIs(cache['stale'], True)
        self.assertEqual(cache['cache_version'], 1)
        self.assertEqual(cache['fetched_at'], NOW)
        err = cache['fetch_error']
        self.assertEqual((err['type'], err['at'], err['last_success_at']), ('TimeoutError', NOW+60, NOW))
        self.assertIn('timed out', err['detail'])
        self.assertEqual(cache['failures'], 1)
        log = cache['fetch_log']
        self.assertEqual([e['ok'] for e in log], [True, False])
        self.assertTrue(all(isinstance(e['elapsed_s'], (int, float)) for e in log))
        self.assertEqual(log[-1]['error_type'], 'TimeoutError')
        self.assertIn(2742665, [r['seq'] for r in cache['price_rows']])

        got = public_evidence.price_anchor(self.tmp.name, UNIT, now=NOW+70)
        self.assertEqual(got['anchor'], 77)
        self.assertEqual(sorted(got['ledger_ids']), [101, 102, 103])
        self.assertIsNone(got['cache_problem'])
        self.assertFalse(got['anchor_cache'].startswith('fallback'))
        self.assertEqual(got['fetch_status'], 'last-known-good')
        self.assertEqual(got['source_age_s'], 70)
        self.assertIn('last verified', got['reason'])
        self.assertIn('TimeoutError', got['reason'])
        real = public_evidence.price_anchor(self.tmp.name, REAL_UNIT, now=NOW+70)
        self.assertEqual((real['comparable_count'], real['ledger_ids']), (1, [2742665]))

    def test_last_known_good_never_outlives_row_freshness_or_hard_expiry(self):
        self.refresh(NOW, BASE)
        self.refresh(NOW+60, exc=OSError('connection reset'))
        aging = public_evidence.price_anchor(self.tmp.name, UNIT, now=NOW+1590)
        self.assertEqual(sorted(aging['ledger_ids']), [102, 103])  # 101 is 1890 s old: no longer a sample
        self.assertEqual(aging['anchor'], 90)
        expired = public_evidence.price_anchor(self.tmp.name, UNIT, now=NOW+1801)
        self.assertEqual(expired['anchor'], 90)
        self.assertEqual(expired['ledger_ids'], [])
        self.assertTrue(expired['anchor_cache'].startswith('fallback'))
        self.assertIn('stale', expired['cache_problem'])
        self.assertIn('OSError', expired['cache_problem'])

    def test_success_clears_error_state(self):
        self.refresh(NOW, BASE)
        self.refresh(NOW+60, exc=OSError('down'))
        cache, _ = self.refresh(NOW+120, BASE)
        self.assertIs(cache['stale'], False)
        self.assertIsNone(cache['fetch_error'])
        self.assertEqual(cache['failures'], 0)
        self.assertEqual(cache['cache_version'], 2)
        self.assertEqual(public_evidence.price_anchor(self.tmp.name, UNIT, now=NOW+130)['fetch_status'], 'fresh')

    def test_reused_anchor_keeps_latest_failure_status_and_seller_snapshot_keeps_historical_flag(self):
        self.refresh(NOW, BASE)
        first = public_evidence.price_anchor(self.tmp.name, UNIT, now=NOW)
        self.refresh(NOW+60, exc=TimeoutError('The read operation timed out'))
        got = public_evidence.price_anchor(self.tmp.name, UNIT, now=NOW+70)
        self.assertEqual((got['anchor_cache'], got['anchor']), ('reused', first['anchor']))
        self.assertEqual(got['fetch_status'], 'last-known-good')
        self.assertIn('latest GET failed: TimeoutError', got['reason'])
        self.assertIs(got['source_historical'], True)
        self.assertEqual(got['provenance'], first['provenance'])
        self.assertEqual(got['source_age_s'], 70)
        import seller_r3
        with patch.object(seller_r3, 'MARKET_DIR', self.tmp.name):
            _, snap = seller_r3.price_policy(UNIT, NOW+70)
        self.assertIs(snap['source_historical'], True)
        self.assertEqual(snap['fetch_status'], 'last-known-good')


class BackoffTests(Isolated):
    def test_consecutive_failures_back_off_exponentially_and_cap(self):
        self.refresh(NOW-100, BASE)
        calls = []
        for t in (0, 30, 60, 120, 179, 180):
            cache, n = self.refresh(NOW+t, exc=OSError('down'))
            calls.append(n)
        # fail@0 -> next 60; skip 30; fail@60 -> next 180; skip 120, 179; fail@180 -> next 420
        self.assertEqual(calls, [1, 0, 1, 0, 0, 1])
        self.assertEqual((cache['failures'], cache['next_attempt_at']), (3, NOW+420))
        for i in range(10):
            cache, _ = self.refresh(cache['next_attempt_at'], exc=OSError('down'))
        self.assertLessEqual(cache['next_attempt_at']-cache['attempted_at'], public_evidence.BACKOFF_MAX_S)
        self.assertEqual(public_evidence.BACKOFF_MAX_S, 900)
        self.assertLessEqual(len(cache['fetch_log']), public_evidence.FETCH_LOG_MAX)

    def test_retry_after_is_honoured_within_bounds(self):
        self.refresh(NOW-100, BASE)
        headers = Message()
        headers['Retry-After'] = '300'
        busy = urllib.error.HTTPError(public_evidence.SOURCE_URL, 503, 'Service Unavailable', headers, None)
        cache, _ = self.refresh(NOW, exc=busy)
        self.assertEqual(cache['next_attempt_at'], NOW+300)
        self.assertEqual(cache['fetch_error']['type'], 'HTTPError')
        self.assertEqual(cache['fetch_error']['status'], 503)


class FakeLedger:
    """Contiguous seqs like the live API (probe: after=2742000 -> 2742001..2742500 ascending). Tail = newest TAIL rows;
    after=N -> rows seq > N, at most public_evidence.PAGE. Every 4th entry is an 'order', the rest settles."""
    TAIL = 10

    def __init__(self, head, ts=NOW, fail_after=False, own=()):
        self.head, self.ts, self.fail_after, self.own, self.urls = head, ts, fail_after, set(own), []

    def row(self, seq):
        if seq % 4 == 0:
            return {'seq':seq, 'ts':self.ts, 'kind':'order', 'round':3, 'data':{}}
        shop = 'https://mine.trycloudflare.com' if seq in self.own else 'https://other-shop.trycloudflare.com'
        return settle(seq, 60, self.ts-(self.head-seq), sku='OTHER', shop=shop)

    def __call__(self, url=public_evidence.SOURCE_URL):
        self.urls.append(url)
        after = int(url.rsplit('after=', 1)[1])
        if after < 0:
            return [self.row(s) for s in range(self.head-self.TAIL+1, self.head+1)]
        if self.fail_after:
            raise OSError('gap page refused')
        return [self.row(s) for s in range(after+1, min(self.head, after+public_evidence.PAGE)+1)]


class CursorCatchupTests(Isolated):
    """Slice 2: newest tail first (freshness), then at most one cursor page into the newest missing range per tick,
    only if the tick deadline allows; remaining ranges are explicit gaps (never reported complete), expire with the
    2 h retention and are capped."""
    def setUp(self):
        super().setUp()
        p = patch.object(public_evidence, 'PAGE', 5)
        p.start()
        self.addCleanup(p.stop)

    def tick(self, ledger, now):
        with patch.object(public_evidence, '_fetch', side_effect=ledger):
            return public_evidence.refresh(self.tmp.name, now=now)

    def test_missed_tail_becomes_explicit_gap_filled_newest_first_two_pages_max(self):
        self.tick(FakeLedger(1010), NOW)
        ledger = FakeLedger(1040, ts=NOW+60)
        cache = self.tick(ledger, NOW+60)
        self.assertEqual(ledger.urls, [public_evidence.SOURCE_URL, public_evidence.SOURCE_URL.replace('-500', '1025')])
        self.assertEqual(cache['gaps'], [{'lo':1011, 'hi':1025, 'detected_at':NOW+60}])
        cov = cache['price_coverage']
        self.assertIs(cov['complete'], False)
        self.assertEqual((cov['gap_seqs'], cov['gaps']), (15, [[1011, 1025]]))
        held = {r['seq'] for r in cache['price_rows']}
        self.assertTrue({1026, 1027, 1029, 1030, 1031, 1039} <= held)
        self.assertFalse({1011, 1025} & held)
        self.assertEqual(cache['cursor_seq'], 1040)

        ledger = FakeLedger(1050, ts=NOW+120)
        cache = self.tick(ledger, NOW+120)
        self.assertEqual(len(ledger.urls), 2)
        self.assertEqual(cache['gaps'], [{'lo':1011, 'hi':1020, 'detected_at':NOW+60}])
        got = public_evidence.price_anchor(self.tmp.name, UNIT, now=NOW+130)
        self.assertEqual(got['provenance']['coverage']['gap_seqs'], 10)
        self.assertIs(got['provenance']['coverage']['complete'], False)
        self.assertIn('coverage incomplete: 10 ledger seq missing in 1 gap', got['reason'])

    def test_second_page_skipped_when_tick_deadline_spent(self):
        self.tick(FakeLedger(1010), NOW)
        ledger = FakeLedger(1040, ts=NOW+60)
        with patch.object(public_evidence, 'PAGE2_START_BY_S', -1):
            cache = self.tick(ledger, NOW+60)
        self.assertEqual(ledger.urls, [public_evidence.SOURCE_URL])
        self.assertEqual(cache['gaps'], [{'lo':1011, 'hi':1030, 'detected_at':NOW+60}])
        self.assertEqual(cache['fetch_log'][-1]['gap_fill'], 'skipped: tick deadline')

    def test_gap_page_failure_keeps_tail_success_and_gap(self):
        self.tick(FakeLedger(1010), NOW)
        cache = self.tick(FakeLedger(1040, ts=NOW+60, fail_after=True), NOW+60)
        self.assertIs(cache['stale'], False)
        self.assertEqual((cache['cache_version'], cache['failures']), (2, 0))
        self.assertEqual(cache['gaps'], [{'lo':1011, 'hi':1030, 'detected_at':NOW+60}])
        self.assertEqual(cache['fetch_log'][-1]['gap_fill'], 'error: OSError')

    def test_gap_page_rows_feed_own_ledger(self):
        self.tick(FakeLedger(1010), NOW)
        with patch.object(public_evidence, 'own_urls', return_value=['https://mine.trycloudflare.com']):
            cache = self.tick(FakeLedger(1040, ts=NOW+60, own={1027}), NOW+60)
        self.assertIn('1027', cache['own_ledger'])

    def test_gaps_expire_with_retention_and_are_capped(self):
        self.tick(FakeLedger(1010), NOW)
        with patch.object(public_evidence, 'PAGE2_START_BY_S', -1):
            self.tick(FakeLedger(1040, ts=NOW+60), NOW+60)
            with patch.object(public_evidence, 'GAP_MAX', 2):
                self.tick(FakeLedger(1070, ts=NOW+120), NOW+120)
                cache = self.tick(FakeLedger(1100, ts=NOW+180), NOW+180)
        self.assertEqual([(g['lo'], g['hi']) for g in cache['gaps']], [(1041, 1060), (1071, 1090)])  # oldest dropped
        self.assertEqual(cache['price_coverage']['gaps_dropped'], 1)
        ledger = FakeLedger(1110, ts=NOW+180+7201)
        cache = self.tick(ledger, NOW+180+7201)  # every gap detected > 2 h ago: rows in it are past retention
        self.assertEqual(cache['gaps'], [])
        self.assertEqual(ledger.urls, [public_evidence.SOURCE_URL])

    def test_gap_page_older_than_retention_closes_rest_of_gap(self):
        self.tick(FakeLedger(1010), NOW)
        ledger = FakeLedger(1040, ts=NOW+60)
        ledger.row = lambda seq, base=ledger.row: dict(base(seq), ts=NOW-7300) if seq <= 1030 else base(seq)
        cache = self.tick(ledger, NOW+60)
        self.assertEqual(cache['gaps'], [])
        self.assertEqual(cache['price_coverage']['gaps_expired'], 1)


CATALOG = {'GPRICE01':{'category':'test', 'list_price':100, 'kind':'goods'},
           'P1':{'category':'test', 'list_price':120, 'kind':'goods'},
           'P2':{'category':'test', 'list_price':80, 'kind':'goods'},
           'P3':{'category':'test', 'list_price':150, 'kind':'goods'},
           'PBIG':{'category':'test', 'list_price':250, 'kind':'goods'},  # 2.5x list: like a real sofa vs G630a202e spray
           'POTHER':{'category':'other', 'list_price':100, 'kind':'goods'}}
SHOP_A, SHOP_B = 'https://shop-a.trycloudflare.com', 'https://shop-b.trycloudflare.com'


class ProxyTierTests(Isolated):
    """Slice 3: exact SKU tier first (unchanged). Only with < 3 fresh exact samples, a category + catalog-list-band
    ratio proxy: >= 5 fresh unique settlements, >= 3 SKUs, >= 2 shops, same kind/grade/warranty/food-life bucket,
    list price within 0.5..2x, ratios clamped 0.2..1, robust median with MAD spread guard, blended 50:50 with the
    90% baseline. Otherwise the unchanged baseline. Proxy is labelled, never called exact."""
    GOOD = [settle(201, 84, NOW-100, sku='P1', shop=SHOP_A), settle(202, 56, NOW-110, sku='P2', shop=SHOP_B),
            settle(203, 105, NOW-120, sku='P3', shop=SHOP_A), settle(204, 86, NOW-130, sku='P1', shop=SHOP_B),
            settle(205, 54, NOW-140, sku='P2', shop=SHOP_A)]  # ratios .70 .70 .70 .717 .675 -> median .70

    def anchor(self, rows, unit=UNIT, catalog=CATALOG, now=NOW, own_urls=()):
        for name in (public_evidence.PRICE_ANCHORS, 'catalog.json'):  # each case starts without a persisted anchor
            Path(self.tmp.name, name).unlink(missing_ok=True)
        Path(self.tmp.name, public_evidence.CACHE).write_text(json.dumps(
            {'source_url':public_evidence.SOURCE_URL, 'fetched_at':now-10, 'attempted_at':now-10, 'stale':False,
             'own_urls':list(own_urls), 'rows':rows, 'own_ledger':{}}))
        if catalog is not None:
            Path(self.tmp.name, 'catalog.json').write_text(json.dumps(catalog))
        return public_evidence.price_anchor(self.tmp.name, unit, now=now)

    def test_proxy_with_enough_independent_evidence_is_labelled_not_exact(self):
        replay = settle(206, 30, NOW-90, sku='P3', shop=SHOP_B)
        replay['data']['order'] = 'o-201'
        noise = [replay, settle(207, 200, NOW-90, sku='PBIG', shop=SHOP_B), settle(208, 30, NOW-90, sku='POTHER', shop=SHOP_B),
                 settle(209, 30, NOW-90, sku='P1', shop=SHOP_B, grade='C'), settle(210, 30, NOW-1900, sku='P2', shop=SHOP_B),
                 settle(211, 30, NOW-90, sku='P3', shop='https://mine.trycloudflare.com'),
                 settle(212, 30, NOW-90, sku='P3', shop=SHOP_B, warranty_hours=24), settle(213, 30, NOW-90, sku='NOCAT', shop=SHOP_B)]
        got = self.anchor(self.GOOD+noise, own_urls=['https://mine.trycloudflare.com'])
        self.assertEqual(got['tier'], 'proxy')
        self.assertEqual(got['anchor'], (70+90)//2)
        self.assertEqual((got['sample_count'], got['comparable_count']), (0, 0))  # exact counts stay honest
        p = got['proxy']
        self.assertEqual((p['count'], p['sku_count'], p['shop_count']), (5, 3, 2))
        self.assertEqual(sorted(p['ledger_ids']), [201, 202, 203, 204, 205])
        self.assertEqual(sorted(got['ledger_ids']), [201, 202, 203, 204, 205])
        self.assertEqual(p['source_skus'], {'P1':120, 'P2':80, 'P3':150})
        # Correction brief fix 2: median of per-shop medians (A: .70, B: (.70+.7167)/2), no longer of all five ratios.
        self.assertAlmostEqual(p['median_ratio'], (0.7+(0.7+86/120)/2)/2)
        self.assertEqual(p['band'], [0.5, 2.0])
        self.assertEqual(len(p['catalog_sha256']), 64)
        self.assertEqual(p['sample_ts_min'], NOW-140)
        self.assertIn('different SKU', p['differences'])
        self.assertIn('proxy', got['reason'])
        self.assertIn('not exact', got['reason'])
        self.assertNotIn('market price is', got['reason'].lower())
        again = public_evidence.price_anchor(self.tmp.name, UNIT, now=NOW+60)
        self.assertEqual((again['anchor_cache'], again['tier'], again['anchor']), ('reused', 'proxy', 80))

    def test_proxy_gates_fall_back_to_unchanged_baseline(self):
        one_shop = [dict(r, data=dict(r['data'], shop=SHOP_A)) for r in self.GOOD]
        two_skus = [dict(r, data=dict(r['data'], sku='P1', item='P1')) if r['data']['sku'] == 'P3' else r for r in self.GOOD]
        cases = {'shops 1 < 2':one_shop, 'SKUs 2 < 3':two_skus, 'settlements 4 < 5':self.GOOD[:4]}
        for note, rows in cases.items():
            with self.subTest(note):
                got = self.anchor(rows)
                self.assertEqual((got['anchor'], got['tier'], got['proxy']), (90, 'baseline', None))
                self.assertIn('proxy not used', got['reason'])
                self.assertIn(note, got['reason'])
        missing = self.anchor(self.GOOD, catalog=None)
        self.assertEqual((missing['anchor'], missing['tier']), (90, 'baseline'))
        self.assertIn('catalog', missing['reason'])
        no_category = self.anchor(self.GOOD, unit=dict(UNIT, category=None))
        self.assertEqual((no_category['anchor'], no_category['tier']), (90, 'baseline'))

    def test_spread_guard_and_ratio_clamp(self):
        wide = [settle(301, 25, NOW-100, sku='P2', shop=SHOP_A), settle(302, 36, NOW-100, sku='P1', shop=SHOP_B),
                settle(303, 90, NOW-100, sku='P3', shop=SHOP_A), settle(304, 114, NOW-100, sku='P1', shop=SHOP_B),
                settle(305, 80, NOW-100, sku='P2', shop=SHOP_A)]  # ratios .31 .30 .60 .95 1.0: MAD/median > .25
        got = self.anchor(wide)
        self.assertEqual((got['anchor'], got['tier']), (90, 'baseline'))
        self.assertIn('spread', got['reason'])
        # Correction brief fix 2 (approved semantic change): 1.5x catalog list is rejected before counting, not clamped.
        over = self.GOOD+[settle(306, 180, NOW-100, sku='P1', shop=SHOP_B)]
        got = self.anchor(over)
        self.assertEqual(got['tier'], 'proxy')
        self.assertEqual((got['proxy']['count'], got['proxy']['ratio_rejected']), (5, 1))
        self.assertNotIn(306, got['proxy']['ledger_ids'])
        self.assertLessEqual(got['proxy']['median_ratio'], 1.0)

    def test_exact_tier_still_wins_with_three_fresh_exact(self):
        got = self.anchor(BASE+self.GOOD)
        self.assertEqual((got['anchor'], got['tier'], got['proxy']), (77, 'exact', None))
        self.assertEqual(sorted(got['ledger_ids']), [101, 102, 103])

    def test_food_proxy_needs_same_life_bucket(self):
        food = dict(UNIT, kind='food', sku='FPRICE01', grade='A', expires_at=NOW+30*3600, category='test')
        cat = {k:dict(v, kind='food') for k, v in CATALOG.items()}
        rows = [dict(r, data=dict(r['data'], grade='A', expires_at=NOW+30*3600)) for r in self.GOOD]
        self.assertEqual(self.anchor(rows, unit=food, catalog=cat)['tier'], 'proxy')
        short = [dict(r, data=dict(r['data'], grade='A', expires_at=NOW+3600)) for r in self.GOOD]
        self.assertEqual(self.anchor(short, unit=food, catalog=cat)['tier'], 'baseline')


class Corrections(Isolated):
    """Correction slice (opus-correction-brief.txt, parent Fable review): cache, catalog and policy file written
    separately so a persisted anchor survives between calls."""
    KEY = 'GPRICE01|B|goods|0'

    def write(self, rows, fetched_at=NOW-10, catalog=CATALOG, **extra):
        Path(self.tmp.name, public_evidence.CACHE).write_text(json.dumps(dict(
            {'source_url':public_evidence.SOURCE_URL, 'fetched_at':fetched_at, 'attempted_at':fetched_at, 'stale':False,
             'own_urls':[], 'rows':rows, 'own_ledger':{}}, **extra)))
        if catalog is not None:
            Path(self.tmp.name, 'catalog.json').write_text(json.dumps(catalog))

    def at(self, now, unit=UNIT):
        return public_evidence.price_anchor(self.tmp.name, unit, now=now)


class AnchorReuseCorrectionTests(Corrections):
    """Fix 1: a persisted anchor is reused <900 s only within the same algorithm version and when the current tier is
    not higher (exact > proxy > baseline > legacy); a proxy entry never outlives its proxy or its catalog."""
    def test_persisted_baseline_upgrades_immediately_to_exact_within_step(self):
        self.write([], catalog=None)
        self.assertEqual((self.at(NOW)['tier'], self.at(NOW)['anchor']), ('baseline', 90))
        self.write(BASE, fetched_at=NOW+50, catalog=None)
        got = self.at(NOW+60)
        self.assertEqual((got['anchor_cache'], got['tier']), ('recalculated', 'exact'))
        self.assertEqual(got['anchor'], 80)  # target 77, one step <= 10 from the previous 90
        self.assertEqual(sorted(got['ledger_ids']), [101, 102, 103])
        self.assertIn('step-limited', got['reason'])

    def test_persisted_baseline_upgrades_immediately_to_proxy(self):
        self.write([])
        self.at(NOW)
        self.write(ProxyTierTests.GOOD, fetched_at=NOW+50)
        got = self.at(NOW+60)
        self.assertEqual((got['anchor_cache'], got['tier'], got['anchor']), ('recalculated', 'proxy', 80))

    def test_entry_from_older_algorithm_version_is_not_reused(self):
        self.assertNotEqual(public_evidence.PRICE_VERSION, 'price-anchor-v2')
        old = {'anchor':70, 'reason':'old', 'ledger_ids':[1], 'sample_count':5, 'comparable_count':5,
               'computed_at':NOW-10, 'version':'price-anchor-v2', 'sample_ts_min':NOW-100, 'cache_version':3,
               'source_cache_version':1, 'tier':'exact'}
        Path(self.tmp.name, public_evidence.PRICE_ANCHORS).write_text(json.dumps({'anchors':{self.KEY:old}}))
        self.write(BASE, catalog=None)
        got = self.at(NOW)
        self.assertEqual((got['anchor_cache'], got['tier'], got['anchor'], got['cache_version']), ('recalculated', 'exact', 77, 4))
        self.assertEqual(json.loads(Path(self.tmp.name, public_evidence.PRICE_ANCHORS).read_text())['anchors'][self.KEY]['version'],
                         public_evidence.PRICE_VERSION)

    def test_proxy_entry_not_reused_once_proxy_rejected_or_catalog_changed(self):
        self.write(ProxyTierTests.GOOD)
        self.assertEqual(self.at(NOW)['tier'], 'proxy')
        self.write(ProxyTierTests.GOOD, fetched_at=NOW+50, gaps=[{'lo':5, 'hi':9, 'detected_at':NOW+40}])
        got = self.at(NOW+60)
        self.assertEqual((got['anchor_cache'], got['tier'], got['anchor']), ('recalculated', 'baseline', 90))

        Path(self.tmp.name, public_evidence.PRICE_ANCHORS).unlink()
        self.write(ProxyTierTests.GOOD)
        self.at(NOW)
        self.write(ProxyTierTests.GOOD, catalog=dict(CATALOG, PZ={'category':'z', 'list_price':1, 'kind':'goods'}))
        got = self.at(NOW+60)
        self.assertEqual((got['anchor_cache'], got['tier'], got['anchor']), ('recalculated', 'proxy', 80))

    def test_same_tier_exact_reuse_keeps_historical_source_labels(self):
        self.write(BASE, catalog=None)
        first = self.at(NOW)
        self.write([settle(201+i, 30, NOW+100+i) for i in range(5)], fetched_at=NOW+500, catalog=None)
        got = self.at(NOW+600)
        self.assertEqual((got['anchor_cache'], got['tier'], got['anchor']), ('reused', 'exact', 77))
        self.assertIs(got['source_historical'], True)
        self.assertEqual(got['source_age_s'], 610)  # age of the reused entry's source, not of the current cache
        self.assertEqual(got['fetch_status'], first['fetch_status'])
        self.assertEqual(got['sample_ts_min'], first['sample_ts_min'])
        self.assertEqual(got['provenance'], first['provenance'])

    def test_reuse_bounded_by_source_hard_age(self):
        self.write([], fetched_at=NOW-1790, catalog=None)
        self.at(NOW)
        self.write([], fetched_at=NOW+90, catalog=None)
        self.assertEqual(self.at(NOW+100)['anchor_cache'], 'recalculated')  # reused source would be 1890 s > 1800 s old


class ProxyCorrectionTests(Corrections):
    """Fix 2: proxy uses OTHER SKUs only, rejects out-of-range ratios before counting, weights shops equally and is
    disallowed while a known ledger gap was detected inside the freshness window."""
    GOOD = ProxyTierTests.GOOD

    def test_unit_sku_never_counts_as_proxy_evidence(self):
        own_sku = [settle(221, 70, NOW-100, shop=SHOP_B), settle(222, 70, NOW-100, shop=SHOP_B)]  # 2 exact: < 3
        self.write(self.GOOD[:3]+own_sku)
        got = self.at(NOW)
        self.assertEqual((got['tier'], got['anchor']), ('baseline', 90))
        self.assertIn('settlements 3 < 5', got['reason'])

    def test_out_of_range_ratios_rejected_before_counts(self):
        for amount in (180, 5):  # 1.5x and 0.04x the P1 catalog list
            with self.subTest(amount):
                Path(self.tmp.name, public_evidence.PRICE_ANCHORS).unlink(missing_ok=True)
                self.write(self.GOOD[:4]+[settle(231, amount, NOW-100, sku='P1', shop=SHOP_B)])
                got = self.at(NOW)
                self.assertEqual((got['tier'], got['anchor']), ('baseline', 90))
                self.assertIn('settlements 4 < 5', got['reason'])
                self.assertIn('ratio outside 0.2..1.0: 1', got['reason'])

    def test_shops_weighted_equally(self):
        heavy = [settle(241+i, 50, NOW-100-i, sku=('P1', 'P3')[i % 2], shop=SHOP_A) for i in range(4)]
        heavy = [dict(r, data=dict(r['data'], amount=round(0.6*CATALOG[r['data']['sku']]['list_price']))) for r in heavy]
        rows = heavy+[settle(245, 64, NOW-100, sku='P2', shop=SHOP_B)]  # shop A 4x ratio .6, shop B 1x ratio .8
        self.write(rows)
        got = self.at(NOW)
        self.assertEqual(got['tier'], 'proxy')
        p = got['proxy']
        self.assertAlmostEqual(p['median_ratio'], 0.7)  # median of shop medians .6 and .8, not of all five (.6)
        self.assertEqual(p['max_shop_share'], 0.8)
        self.assertEqual(p['price'], 70)

    def test_fresh_known_gap_blocks_proxy_but_old_gap_or_incomplete_flag_does_not(self):
        self.write(self.GOOD, gaps=[{'lo':5, 'hi':9, 'detected_at':NOW-100}])
        got = self.at(NOW)
        self.assertEqual((got['tier'], got['anchor']), ('baseline', 90))
        self.assertIn('known ledger gap', got['reason'])
        Path(self.tmp.name, public_evidence.PRICE_ANCHORS).unlink()
        self.write(self.GOOD, gaps=[{'lo':5, 'hi':9, 'detected_at':NOW-1900}], complete=False)
        self.assertEqual(self.at(NOW)['tier'], 'proxy')


class GapFillCorrectionTests(Isolated):
    """Fix 3: a gap page with holes never marks the missing seqs filled; holes stay as precise ranges, capped."""
    def page(self, seqs):
        return [settle(s, 60, NOW-10) for s in seqs]

    def test_page_with_hole_keeps_missing_seq(self):
        gaps, counts = [{'lo':101, 'hi':103, 'detected_at':NOW}], {'gaps_dropped':0, 'gaps_expired':0}
        with patch.object(public_evidence, '_fetch', return_value=self.page([101, 103])) as fetch:
            _, status = public_evidence._fill_gap(gaps, counts, public_evidence.time.monotonic(), NOW)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(gaps, [{'lo':102, 'hi':102, 'detected_at':NOW}])
        self.assertIn('1 missing', status)

    def test_holes_are_capped_and_counted(self):
        gaps, counts = [{'lo':101, 'hi':120, 'detected_at':NOW}], {'gaps_dropped':0, 'gaps_expired':0}
        with patch.object(public_evidence, '_fetch', return_value=self.page(range(101, 121, 2))), \
                patch.object(public_evidence, 'GAP_MAX', 3):
            public_evidence._fill_gap(gaps, counts, public_evidence.time.monotonic(), NOW)
        self.assertEqual([(g['lo'], g['hi']) for g in gaps], [(116, 116), (118, 118), (120, 120)])
        self.assertEqual(counts['gaps_dropped'], 7)

    def test_refresh_with_holey_gap_page_stays_two_pages_and_incomplete(self):
        with patch.object(public_evidence, 'PAGE', 5):
            ledger = FakeLedger(1010)
            with patch.object(public_evidence, '_fetch', side_effect=ledger):
                public_evidence.refresh(self.tmp.name, now=NOW)
            ledger = FakeLedger(1040, ts=NOW+60)
            full = ledger.__call__
            ledger_call = lambda url=public_evidence.SOURCE_URL: [r for r in full(url) if r['seq'] != 1028]
            with patch.object(public_evidence, '_fetch', side_effect=ledger_call):
                cache = public_evidence.refresh(self.tmp.name, now=NOW+60)
        self.assertEqual(len(ledger.urls), 2)
        self.assertEqual([(g['lo'], g['hi']) for g in cache['gaps']], [(1011, 1025), (1028, 1028)])
        self.assertIs(cache['price_coverage']['complete'], False)


class FetchDeadlineCorrectionTests(Isolated):
    """Fix 4: the deadline is rechecked after the last read (EOF included); body reads get the remaining budget as
    their socket timeout when the raw socket is reachable."""
    class Sock:
        def __init__(self):
            self.timeouts = []

        def settimeout(self, value):
            self.timeouts.append(value)

    def response(self, clock, steps):
        sock, body = self.Sock(), json.dumps([REAL_ROW]).encode()

        class Resp(io.BytesIO):
            fp = type('FP', (), {'raw':type('Raw', (), {'_sock':sock})()})()

            def read(self, size=-1):
                clock[0] += steps.pop(0)
                return super().read(size)
        return Resp(body), sock

    def test_slow_eof_read_after_deadline_is_rejected(self):
        clock = [0.0]
        resp, _ = self.response(clock, [1.0, 5.0])  # body by t=1, EOF read returns only at t=6
        with patch('public_evidence.urllib.request.urlopen', return_value=resp), \
                patch('public_evidence.time.monotonic', side_effect=lambda: clock[0]):
            with self.assertRaises(TimeoutError):
                public_evidence._fetch()

    def test_body_reads_use_remaining_budget_as_socket_timeout(self):
        clock = [0.0]
        resp, sock = self.response(clock, [2.0, 1.0])
        with patch('public_evidence.urllib.request.urlopen', return_value=resp), \
                patch('public_evidence.time.monotonic', side_effect=lambda: clock[0]):
            self.assertEqual(public_evidence._fetch(), [REAL_ROW])
        self.assertEqual(sock.timeouts, [5.0, 3.0])


class FetchSocketEofTests(Isolated):
    """Regression (renewed-fetch-diagnostic.log): with Content-Length, HTTPResponse closes its socket on the read that
    exhausts the body, so the EOF read must not settimeout a closed socket; genuine socket errors still propagate."""
    def test_real_content_length_response_closed_at_body_end_is_parsed(self):
        body = json.dumps([REAL_ROW]).encode()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass
        server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        self.addCleanup(server.server_close)
        threading.Thread(target=server.handle_request, daemon=True).start()
        with patch('public_evidence.urllib.request.urlopen', REAL_URLOPEN):
            rows = public_evidence._fetch(f'http://127.0.0.1:{server.server_port}/api/ledger?after=-500')
        self.assertEqual(rows, [REAL_ROW])

    def test_genuine_settimeout_error_on_open_socket_propagates(self):
        class Sock:
            def settimeout(self, value):
                raise OSError(10038, 'genuine socket failure')

        class Resp(io.BytesIO):
            fp = type('FP', (), {'raw':type('Raw', (), {'_sock':Sock()})()})()
        with patch('public_evidence.urllib.request.urlopen', return_value=Resp(json.dumps([REAL_ROW]).encode())):
            with self.assertRaises(OSError):
                public_evidence._fetch()


class SellerProxyLabelTests(unittest.TestCase):
    def test_seller_snapshot_names_proxy_tier_and_keeps_fields(self):
        import seller_r3
        got = {'anchor':80, 'tier':'proxy', 'proxy':{'count':5}, 'sample_count':0, 'stale':False, 'version':'v',
               'reason':'proxy (not exact): ...', 'fetch_status':'fresh', 'source_age_s':10}
        unit = dict(UNIT, list_price=100, cost=40)
        with patch.object(seller_r3, 'price_anchor', return_value=got):
            anchor, snap = seller_r3.price_policy(unit, NOW)
        self.assertEqual(anchor, 80)
        self.assertTrue(snap['label'].startswith('market proxy anchor (not exact)'))
        self.assertEqual((snap['tier'], snap['proxy'], snap['fetch_status'], snap['source_age_s']), ('proxy', {'count':5}, 'fresh', 10))
        with patch.object(seller_r3, 'price_anchor', return_value=dict(got, tier='baseline', proxy=None)):
            _, snap = seller_r3.price_policy(unit, NOW)
        self.assertTrue(snap['label'].startswith('conservative fallback'))


if __name__ == '__main__':
    unittest.main()
