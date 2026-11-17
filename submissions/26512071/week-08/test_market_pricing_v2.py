"""RED: market-informed seller price anchor, first vertical slice. No network, no model calls, no real key.

Assumed contract (initial assumptions, not optimal values):
- public_evidence.price_anchor(state, unit, now=None) -> dict with anchor(int), reason, ledger_ids, sample_count,
  computed_at, version, limitations, comparable_count, stale
- reads only the existing public-settlements.json cache; never fetches
- comparable = round-3 settle of the same SKU+grade+kind+warranty_hours (null observed -> 0), not own shop
- N>=5 median; N 3-4 -> (median + list*9//10)//2; N<3 conservative baseline; never a claimed precise market price
Fixture rows copy the shape of logs/seller-evolution-20261104/ledger-price-contract.json settle rows.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import public_evidence

NOW = 1793801700.0
UNIT = dict(unit='u-v2-goods', sku='GPRICE01', name='Price fixture', category='test', grade='B', kind='goods',
            cost=40, list_price=100, expires_at=None, supplied_at=1)


def settle(seq, amount, ts, sku='GPRICE01', grade='B', shop='https://other-shop.trycloudflare.com',
           warranty_hours=None, expires_at=None):
    return {'seq': seq, 'ts': ts, 'kind': 'settle', 'round': 3,
            'data': {'order': f'o-{seq}', 'buyer': 'buyer-01', 'shop': shop, 'item': sku, 'amount': amount,
                     'checkout_hash': f'h-{seq}', 'unit': f'u-{seq}', 'sku': sku, 'name': 'Price fixture',
                     'grade': grade, 'expires_at': expires_at, 'claim': grade, 'claim_honest': True,
                     'warranty_hours': warranty_hours},
            'prev': 'p', 'hash': f'x-{seq}'}


def write_cache(tmp, rows, fetched_at=NOW-10, own_urls=(), stale=False):
    Path(tmp, public_evidence.CACHE).write_text(json.dumps(
        {'source_url': public_evidence.SOURCE_URL, 'fetched_at': fetched_at, 'attempted_at': fetched_at,
         'stale': stale, 'own_urls': list(own_urls), 'rows': rows, 'own_ledger': {}}))


class PriceAnchorTests(unittest.TestCase):
    def setUp(self):
        # Never let this agent's real runtime-events.jsonl leak own shop URLs into the fixture.
        p = patch.object(public_evidence, 'RUNTIME_EVENTS', Path(tempfile.gettempdir(), 'no-such-runtime-events.jsonl'))
        p.start()
        self.addCleanup(p.stop)

    def test_three_exact_goods_comparables_blend_with_ninety_percent_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_cache(tmp, [settle(101, 60, NOW-300), settle(102, 65, NOW-200), settle(103, 70, NOW-100)])
            with patch('urllib.request.urlopen', side_effect=AssertionError('price_anchor must not fetch')):
                got = public_evidence.price_anchor(tmp, UNIT, now=NOW)
        self.assertIs(type(got['anchor']), int)
        self.assertEqual(got['anchor'], (65+90)//2)  # median 65 blended 50:50 with list*9//10
        self.assertEqual(got['anchor'], 77)
        self.assertEqual(sorted(got['ledger_ids']), [101, 102, 103])
        self.assertEqual(got['sample_count'], 3)
        self.assertEqual(got['comparable_count'], 3)
        self.assertIs(got['stale'], False)
        self.assertEqual(got['computed_at'], NOW)
        self.assertTrue(isinstance(got['version'], str) and got['version'])
        reason = got['reason'].lower()
        self.assertIn('blend', reason)
        self.assertIn('3', reason)
        self.assertNotIn('market price is', reason)  # a bounded sample, never a precise market price
        self.assertTrue(got['limitations'])

    def test_persisted_anchor_reused_for_900s_then_moves_at_most_ten_percent_of_list(self):
        # Assumed contract: anchors persist per comparability key in PRICE_ANCHORS; a stored anchor younger than
        # 900 s is reused (cost guard reapplied); after that the new target moves at most list//10 from it.
        with tempfile.TemporaryDirectory() as tmp:
            write_cache(tmp, [settle(101, 60, NOW-300), settle(102, 65, NOW-200), settle(103, 70, NOW-100)])
            first = public_evidence.price_anchor(tmp, UNIT, now=NOW)
            self.assertEqual(first['anchor'], 77)  # first anchor is direct, no step limit
            self.assertTrue(Path(tmp, public_evidence.PRICE_ANCHORS).exists())
            write_cache(tmp, [settle(201+i, 30, NOW+100+i) for i in range(5)], fetched_at=NOW+500)
            with patch('urllib.request.urlopen', side_effect=AssertionError('price_anchor must not fetch')):
                cached = public_evidence.price_anchor(tmp, UNIT, now=NOW+600)
                guarded = public_evidence.price_anchor(tmp, dict(UNIT, cost=80), now=NOW+600)
                moved = public_evidence.price_anchor(tmp, UNIT, now=NOW+1000)
        self.assertEqual(cached['anchor'], 77)
        self.assertEqual(sorted(cached['ledger_ids']), [101, 102, 103])
        self.assertEqual(cached['computed_at'], NOW)
        self.assertEqual(guarded['anchor'], 80)  # cached 77 is below the current cost floor
        self.assertEqual(moved['anchor'], 67)  # target clamps to cost 40, but one refresh moves <= 10 (10% of list)
        self.assertEqual(sorted(moved['ledger_ids']), [201, 202, 203, 204, 205])
        self.assertEqual(moved['computed_at'], NOW+1000)
        self.assertIn('step', moved['reason'].lower())


FOOD = dict(unit='u-v2-food', sku='FPRICE01', name='Food fixture', category='test', grade='A', kind='food',
            cost=20, list_price=50, expires_at=NOW+30*3600, supplied_at=NOW-3600, warranty_hours=None)


class NextSliceComparabilityTests(unittest.TestCase):
    """NEXT slice. Tests marked RED have no production code yet; the others pin current behaviour so the next
    change cannot regress it. Assumed contract (initial assumptions, not optimal values):
    - food is compared by remaining-life bucket at settle time (expires_at - ts) vs the unit's (expires_at - now):
      <6 h, 6-24 h, >=24 h; the 'food life bucket not compared yet' limitation disappears
    - one settlement per data.order: a replayed settle under a new seq is counted once
    - refresh() keeps earlier comparable settles that fell out of the newest-500 tail while they are still fresh
    """
    def setUp(self):
        p = patch.object(public_evidence, 'RUNTIME_EVENTS', Path(tempfile.gettempdir(), 'no-such-runtime-events.jsonl'))
        p.start()
        self.addCleanup(p.stop)

    def anchor(self, rows, unit=UNIT, now=NOW, **cache):
        with tempfile.TemporaryDirectory() as tmp:
            write_cache(tmp, rows, **cache)
            with patch('urllib.request.urlopen', side_effect=AssertionError('price_anchor must not fetch')):
                return public_evidence.price_anchor(tmp, unit, now=now)

    def test_real_metadata_required_sku_item_shop_and_own_shop(self):
        mismatch = settle(104, 66, NOW-50)
        mismatch['data']['item'] = 'OTHER'
        no_shop = settle(105, 66, NOW-50, shop=None)
        own = settle(106, 66, NOW-50, shop='https://mine.trycloudflare.com/')
        got = self.anchor([settle(101, 60, NOW-300), settle(102, 65, NOW-200), settle(103, 70, NOW-100),
                           mismatch, no_shop, own], own_urls=['https://mine.trycloudflare.com'])
        self.assertEqual(sorted(got['ledger_ids']), [101, 102, 103])
        self.assertEqual(got['comparable_count'], 3)

    def test_filter_kind_round_future_and_invalid_amounts(self):
        wrong_kind = dict(settle(104, 66, NOW-50), kind='result')
        wrong_round = dict(settle(105, 66, NOW-50), round=2)
        rows = [settle(101, 60, NOW-300), settle(102, 65, NOW-200), settle(103, 70, NOW-100), wrong_kind, wrong_round,
                settle(106, 66, NOW+5), settle(107, True, NOW-50), settle(108, '66', NOW-50), settle(109, -66, NOW-50)]
        got = self.anchor(rows)
        self.assertEqual(sorted(got['ledger_ids']), [101, 102, 103])
        self.assertEqual(got['comparable_count'], 3)

    def test_outliers_outside_twenty_percent_to_list_are_not_samples(self):
        got = self.anchor([settle(101, 60, NOW-300), settle(102, 65, NOW-200), settle(103, 70, NOW-100),
                           settle(104, 5, NOW-50), settle(105, 150, NOW-40)])
        self.assertEqual(sorted(got['ledger_ids']), [101, 102, 103])
        self.assertEqual(got['comparable_count'], 3)
        self.assertEqual(got['anchor'], 77)

    def test_dedup_same_seq_once(self):
        got = self.anchor([settle(101, 60, NOW-300), settle(101, 60, NOW-300), settle(102, 65, NOW-200), settle(103, 70, NOW-100)])
        self.assertEqual(got['sample_count'], 3)

    def test_dedup_replayed_order_under_new_seq_counted_once(self):  # RED
        replay = settle(104, 60, NOW-290)
        replay['data']['order'] = 'o-101'
        got = self.anchor([settle(101, 60, NOW-300), replay, settle(102, 65, NOW-200), settle(103, 70, NOW-100)])
        self.assertEqual(got['sample_count'], 3)
        self.assertEqual(got['comparable_count'], 3)
        self.assertEqual(sorted(got['ledger_ids']), [101, 102, 103])

    def test_rounding_even_median_floors_to_int(self):
        blend = self.anchor([settle(101+i, a, NOW-100-i) for i, a in enumerate((60, 62, 63, 70))])
        self.assertIs(type(blend['anchor']), int)
        self.assertEqual(blend['anchor'], 76)  # (62.5 + 90)//2
        median = self.anchor([settle(101+i, a, NOW-100-i) for i, a in enumerate((60, 61, 62, 63, 70, 71))])
        self.assertIs(type(median['anchor']), int)
        self.assertEqual(median['anchor'], 62)  # int(62.5)

    def test_freshness_old_comparable_counted_but_not_sampled(self):
        got = self.anchor([settle(101, 60, NOW-300), settle(102, 65, NOW-200), settle(103, 70, NOW-100),
                           settle(104, 40, NOW-1801)])
        self.assertEqual(sorted(got['ledger_ids']), [101, 102, 103])
        self.assertEqual(got['comparable_count'], 4)
        food = self.anchor([settle(301+i, 40, NOW-901-i, sku='FPRICE01', grade='A', expires_at=NOW+30*3600) for i in range(3)],
                           unit=FOOD)
        self.assertEqual(food['sample_count'], 0)

    def test_unknown_grade_or_warranty_excluded(self):
        no_grade_key = settle(104, 66, NOW-50)
        del no_grade_key['data']['grade']
        no_warranty_key = settle(105, 66, NOW-50)
        del no_warranty_key['data']['warranty_hours']
        rows = [settle(101, 60, NOW-300), settle(102, 65, NOW-200), settle(103, 70, NOW-100, warranty_hours=0),
                settle(106, 66, NOW-50, grade=None), settle(107, 66, NOW-50, warranty_hours='unknown'),
                settle(108, 66, NOW-50, warranty_hours=24), no_grade_key, no_warranty_key]
        got = self.anchor(rows)
        self.assertEqual(sorted(got['ledger_ids']), [101, 102, 103])
        self.assertEqual(got['comparable_count'], 3)

    def test_food_compared_by_remaining_life_bucket(self):  # RED
        same = [settle(301+i, 40, NOW-100-i, sku='FPRICE01', grade='A', expires_at=NOW+30*3600) for i in range(3)]
        short = [settle(304+i, 12, NOW-100-i, sku='FPRICE01', grade='A', expires_at=NOW+2*3600) for i in range(2)]
        got = self.anchor(same+short, unit=FOOD)
        self.assertEqual(sorted(got['ledger_ids']), [301, 302, 303])
        self.assertEqual(got['comparable_count'], 3)
        self.assertNotIn('food life bucket not compared yet', got['limitations'])

    def test_refresh_keeps_fresh_comparables_missing_from_new_tail(self):  # RED
        old = [settle(101, 60, NOW-300), settle(102, 65, NOW-200), settle(103, 70, NOW-100)]
        tail = [settle(501+i, 30, NOW+100+i, sku='OTHER') for i in range(5)]
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(public_evidence, '_fetch', return_value=old):
                public_evidence.refresh(tmp, now=NOW)
            with patch.object(public_evidence, '_fetch', return_value=tail):
                public_evidence.refresh(tmp, now=NOW+120)
            with patch('urllib.request.urlopen', side_effect=AssertionError('price_anchor must not fetch')):
                got = public_evidence.price_anchor(tmp, UNIT, now=NOW+130)
        self.assertIs(got['stale'], False)
        self.assertEqual(sorted(got['ledger_ids']), [101, 102, 103])  # 430 s old: still fresh for goods
        self.assertEqual(got['anchor'], 77)


BASE = [settle(101, 60, NOW-300), settle(102, 65, NOW-200), settle(103, 70, NOW-100)]


class Slice3RegressionTests(unittest.TestCase):
    """Slice 3 contract (user-specified): food buckets <3 h, 3..24 h, >=24 h of remaining life at settle ts; replay
    dedup by seq, data.order and data.unit; refresh carries price_rows (<=2 h by settle ts, <=5000) with observed_at
    and coverage; cache_version bumps per successful refresh; missing/stale/corrupt cache -> labelled baseline only."""
    def setUp(self):
        p = patch.object(public_evidence, 'RUNTIME_EVENTS', Path(tempfile.gettempdir(), 'no-such-runtime-events.jsonl'))
        p.start()
        self.addCleanup(p.stop)
        no_fetch = patch('urllib.request.urlopen', side_effect=AssertionError('tests must not fetch'))
        no_fetch.start()
        self.addCleanup(no_fetch.stop)

    def anchor(self, rows, unit=UNIT, now=NOW, **cache):
        with tempfile.TemporaryDirectory() as tmp:
            write_cache(tmp, rows, **cache)
            return public_evidence.price_anchor(tmp, unit, now=now)

    def assertBaselineFallback(self, got, base=90):
        self.assertEqual(got['anchor'], base)
        self.assertEqual((got['ledger_ids'], got['sample_count'], got['comparable_count']), ([], 0, 0))
        self.assertIs(got['stale'], True)
        self.assertIsNone(got['provenance'])
        self.assertTrue(got['anchor_cache'].startswith('fallback'))
        self.assertIn('baseline', got['reason'])
        self.assertNotIn('market price is', got['reason'].lower())

    def test_no_data_is_baseline_with_provenance(self):
        got = self.anchor([])
        self.assertEqual(got['anchor'], 90)
        self.assertEqual((got['sample_count'], got['comparable_count']), (0, 0))
        self.assertIsNone(got['cache_problem'])
        self.assertEqual(got['provenance']['cache_version'], 0)  # pre-v2 cache has no counter
        self.assertEqual(got['provenance']['coverage']['rows'], 0)

    def test_stale_or_missing_cache_never_reuses_persisted_market_anchor(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_cache(tmp, BASE)
            self.assertEqual(public_evidence.price_anchor(tmp, UNIT, now=NOW)['anchor'], 77)
            policy = Path(tmp, public_evidence.PRICE_ANCHORS).read_text()
            write_cache(tmp, BASE, fetched_at=NOW-10, stale=True)
            marked = public_evidence.price_anchor(tmp, UNIT, now=NOW+100)
            write_cache(tmp, BASE, fetched_at=NOW+100-1801)
            aged = public_evidence.price_anchor(tmp, UNIT, now=NOW+100)
            Path(tmp, public_evidence.CACHE).unlink()
            missing = public_evidence.price_anchor(tmp, UNIT, now=NOW+100)
            self.assertEqual(Path(tmp, public_evidence.PRICE_ANCHORS).read_text(), policy)  # fallback never persisted
        for got in (marked, aged, missing):
            self.assertBaselineFallback(got)
        self.assertIn('stale', marked['cache_problem'])
        self.assertIn('stale', aged['cache_problem'])
        self.assertIn('missing', missing['cache_problem'])

    def test_food_cache_older_than_900s_is_stale(self):
        rows = [settle(301+i, 40, NOW-100-i, sku='FPRICE01', grade='A', expires_at=NOW+30*3600) for i in range(3)]
        self.assertBaselineFallback(self.anchor(rows, unit=FOOD, fetched_at=NOW-901), base=45)
        self.assertEqual(sorted(self.anchor(rows, unit=FOOD, fetched_at=NOW-899)['ledger_ids']), [301, 302, 303])

    def test_corrupt_or_malformed_cache_fails_closed_to_labelled_baseline(self):
        good = {'source_url': public_evidence.SOURCE_URL, 'fetched_at': NOW-10, 'attempted_at': NOW-10, 'stale': False,
                'own_urls': [], 'rows': BASE, 'own_ledger': {}}
        cases = [('corrupt', 'not json{'), ('malformed', '[]'), ('malformed', json.dumps(dict(good, rows={}))),
                 ('malformed', json.dumps(dict(good, price_rows='x'))), ('malformed', json.dumps(dict(good, cache_version='1'))),
                 ('malformed', json.dumps(dict(good, cache_version=True))), ('malformed', json.dumps(dict(good, stale='no'))),
                 ('malformed', json.dumps(dict(good, own_ledger=[]))), ('malformed', json.dumps(dict(good, fetched_at=NOW+60))),
                 ('malformed', json.dumps(dict(good, fetched_at=None)))]
        for label, text in cases:
            with self.subTest(text=text[:60]), tempfile.TemporaryDirectory() as tmp:
                Path(tmp, public_evidence.CACHE).write_text(text)
                got = public_evidence.price_anchor(tmp, UNIT, now=NOW)
                self.assertBaselineFallback(got)
                self.assertIn(label, got['cache_problem'])
                self.assertFalse(Path(tmp, public_evidence.PRICE_ANCHORS).exists())

    def test_anchor_file_failure_and_changed_cost_keep_clamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_cache(tmp, BASE)
            Path(tmp, public_evidence.PRICE_ANCHORS).write_text('{broken')
            broken = public_evidence.price_anchor(tmp, UNIT, now=NOW)
            self.assertEqual(Path(tmp, public_evidence.PRICE_ANCHORS).read_text(), '{broken')
            Path(tmp, public_evidence.PRICE_ANCHORS).unlink()
            self.assertEqual(public_evidence.price_anchor(tmp, UNIT, now=NOW)['anchor'], 77)
            write_cache(tmp, BASE, fetched_at=NOW+990)
            raised = public_evidence.price_anchor(tmp, dict(UNIT, cost=95), now=NOW+1000)
        self.assertEqual(broken['anchor'], 77)
        self.assertTrue(broken['anchor_cache'].startswith('fallback: unreadable'))
        self.assertIsNone(broken['cache_version'])
        self.assertEqual(raised['anchor'], 95)  # step limit never pushes below the current cost

    def test_replay_of_same_unit_under_new_order_counted_once(self):
        replay = settle(104, 60, NOW-50)
        replay['data']['unit'] = 'u-101'
        got = self.anchor(BASE+[replay])
        self.assertEqual(sorted(got['ledger_ids']), [101, 102, 103])
        self.assertEqual(got['comparable_count'], 3)

    def test_food_bucket_boundaries_and_invalid_food_metadata(self):
        food = dict(sku='FPRICE01', grade='A')
        ts = NOW-100
        exact24 = [settle(401+i, 40, ts, expires_at=ts+24*3600, **food) for i in range(3)]
        under24 = settle(404, 40, ts, expires_at=ts+24*3600-1, **food)
        invalid = [settle(405, 40, ts, expires_at='2026', **food), settle(406, 40, ts, expires_at=True, **food),
                   settle(407, 40, ts, expires_at=ts-1, **food), settle(408, 40, ts, expires_at=0, **food)]
        got = self.anchor(exact24+[under24]+invalid, unit=FOOD)
        self.assertEqual(sorted(got['ledger_ids']), [401, 402, 403])
        self.assertEqual(got['comparable_count'], 3)

        short = dict(FOOD, expires_at=NOW+2*3600)
        under3 = [settle(501+i, 40, ts, expires_at=ts+3*3600-1, **food) for i in range(3)]
        exact3 = settle(504, 40, ts, expires_at=ts+3*3600, **food)
        got = self.anchor(under3+[exact3], unit=short)
        self.assertEqual(sorted(got['ledger_ids']), [501, 502, 503])
        with self.assertRaises(ValueError):
            public_evidence.price_anchor(tempfile.gettempdir(), dict(FOOD, expires_at=None), now=NOW)

    def test_food_bucket_change_does_not_reuse_persisted_anchor(self):
        rows = [settle(301+i, 40, NOW-100-i, sku='FPRICE01', grade='A', expires_at=NOW+30*3600) for i in range(3)]
        with tempfile.TemporaryDirectory() as tmp:
            write_cache(tmp, rows)
            long_life = public_evidence.price_anchor(tmp, FOOD, now=NOW)
            short_life = public_evidence.price_anchor(tmp, dict(FOOD, expires_at=NOW+2*3600), now=NOW+10)
        self.assertEqual(long_life['anchor'], (40+45)//2)
        self.assertEqual(short_life['anchor'], 45)
        self.assertEqual(short_life['anchor_cache'], 'recalculated')
        self.assertEqual(short_life['cache_version'], 1)

    def test_repeated_refreshes_dedupe_price_rows_and_bump_cache_version(self):
        replay = settle(104, 60, NOW-50)
        replay['data']['order'] = 'o-101'
        outlier = settle(105, 5, NOW-40)
        tails = [BASE, BASE[1:]+[replay], [replay, outlier]]
        with tempfile.TemporaryDirectory() as tmp:
            for i, tail in enumerate(tails):
                with patch.object(public_evidence, '_fetch', return_value=tail):
                    cache = public_evidence.refresh(tmp, now=NOW+60*i)
                self.assertEqual(cache['cache_version'], i+1)
            got = public_evidence.price_anchor(tmp, UNIT, now=NOW+130)
        self.assertEqual([r['seq'] for r in cache['price_rows']], [101, 102, 103, 104, 105])
        self.assertEqual([r['observed_at'] for r in cache['price_rows']], [NOW, NOW, NOW, NOW+60, NOW+120])
        self.assertEqual(cache['price_rows'][0]['ts'], NOW-300)
        self.assertEqual(cache['price_rows'][0]['data']['grade'], 'B')
        self.assertEqual(cache['price_coverage']['rows'], 5)
        self.assertEqual(sorted(got['ledger_ids']), [101, 102, 103])
        self.assertEqual(got['comparable_count'], 3)
        self.assertEqual(got['anchor'], 77)
        prov = got['provenance']
        self.assertEqual(prov['cache_version'], 3)
        self.assertEqual(got['source_cache_version'], 3)
        self.assertEqual((prov['coverage']['seq_min'], prov['coverage']['seq_max']), (101, 105))
        self.assertEqual(prov['coverage']['observed_from'], NOW)
        self.assertEqual(prov['fetched_at'], NOW+120)

    def test_failed_refresh_marks_stale_and_keeps_cache_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(public_evidence, '_fetch', return_value=BASE):
                public_evidence.refresh(tmp, now=NOW)
            with patch.object(public_evidence, '_fetch', side_effect=OSError('down')):
                cache = public_evidence.refresh(tmp, now=NOW+60)
            got = public_evidence.price_anchor(tmp, UNIT, now=NOW+70)
            hard = public_evidence.price_anchor(tmp, UNIT, now=NOW+1801)
        self.assertEqual(cache['cache_version'], 1)
        self.assertIs(cache['stale'], True)
        # v3 intentional change (logs/price-recovery/OPUS_IMPLEMENTATION.md): one failed GET 60 s after a verified
        # fetch no longer drops to baseline; the last verified rows stay usable, labelled with the error and source
        # age, until the same FRESH_S hard expiry. Legacy stale-marked caches without a v3 fetch_error stay baseline.
        self.assertEqual((got['anchor'], got['fetch_status']), (77, 'last-known-good'))
        self.assertIn('OSError', got['reason'])
        self.assertBaselineFallback(hard)

    def test_price_rows_retained_two_hours_by_settle_ts_and_capped(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(public_evidence, '_fetch', return_value=[settle(90, 60, NOW-7201)]+BASE):
                first = public_evidence.refresh(tmp, now=NOW)
            with patch.object(public_evidence, '_fetch', return_value=[]):
                aged = public_evidence.refresh(tmp, now=NOW+6900)  # 101 is now 7200 s old: kept
                later = public_evidence.refresh(tmp, now=NOW+7000)  # 101 is 7300 s old: dropped
            new = [settle(110, 60, NOW+7050), settle(111, 60, NOW+7055)]
            with patch.object(public_evidence, '_fetch', return_value=new), patch.object(public_evidence, 'PRICE_MAX_ROWS', 2):
                capped = public_evidence.refresh(tmp, now=NOW+7060)  # 103 still within 2 h, cut by the cap
        self.assertEqual([r['seq'] for r in first['price_rows']], [101, 102, 103])
        self.assertEqual([r['seq'] for r in aged['price_rows']], [101, 102, 103])
        self.assertEqual([r['seq'] for r in later['price_rows']], [102, 103])
        self.assertEqual([r['seq'] for r in capped['price_rows']], [110, 111])
        self.assertEqual(capped['cache_version'], 4)

    def test_legacy_cache_observed_at_uses_fetched_at_not_settle_ts(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_cache(tmp, BASE, fetched_at=NOW-10)
            got = public_evidence.price_anchor(tmp, UNIT, now=NOW)
            with patch.object(public_evidence, '_fetch', return_value=[]):
                cache = public_evidence.refresh(tmp, now=NOW+60)
        self.assertEqual(got['provenance']['cache_version'], 0)
        self.assertEqual(got['provenance']['coverage']['observed_from'], NOW-10)
        self.assertEqual([(r['seq'], r['observed_at']) for r in cache['price_rows']],
                         [(101, NOW-10), (102, NOW-10), (103, NOW-10)])
        self.assertEqual(cache['cache_version'], 1)


class MetadataTypeTests(unittest.TestCase):
    def test_missing_item_in_one_row_does_not_poison_valid_comparables(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows=[settle(701+i,60+i,NOW-50-i) for i in range(3)]
            malformed=settle(699,50,NOW-60); del malformed['data']['item']
            write_cache(tmp,[malformed]+rows)
            got=public_evidence.price_anchor(tmp,UNIT,now=NOW)
            self.assertEqual(got['sample_count'],3)
            self.assertEqual(got['ledger_ids'],[701,702,703])

    def test_reused_anchor_keeps_provenance_of_the_settlements_it_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_cache(tmp,[settle(801+i,60+i,NOW-50-i) for i in range(3)])
            p=Path(tmp,public_evidence.CACHE)
            c=json.loads(p.read_text()); c['cache_version']=1; p.write_text(json.dumps(c))
            first=public_evidence.price_anchor(tmp,UNIT,now=NOW)
            write_cache(tmp,[settle(811+i,90,NOW+50+i) for i in range(3)],fetched_at=NOW+100)
            c=json.loads(p.read_text()); c['cache_version']=2; p.write_text(json.dumps(c))
            reused=public_evidence.price_anchor(tmp,UNIT,now=NOW+100)
            self.assertFalse(reused['recalculated'])
            self.assertEqual(reused['ledger_ids'],first['ledger_ids'])
            self.assertEqual(reused['provenance'],first['provenance'])

    def test_boolean_warranty_is_not_an_observed_zero_hour_term(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows=[settle(901+i,60+i,NOW-100-i,warranty_hours=False) for i in range(3)]
            write_cache(tmp,rows)
            got=public_evidence.price_anchor(tmp,UNIT,now=NOW)
            self.assertEqual(got['sample_count'],0)


if __name__ == '__main__':
    unittest.main()
