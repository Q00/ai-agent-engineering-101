"""RED: bounded-v2 seller evolution. Offline fixtures only; plan() must never touch the network."""
import json
import tempfile
import unittest
from pathlib import Path

from test_restock_policy_v1 import OTHER, OWN, START, goods, offline_plan, read, settle, write_state

CAP = 12  # pilot ceiling: units on hand + outstanding reservations + newly planned


def unit(uid, sku, cat, cost=1000):
    return {'unit':uid, 'sku':sku, 'category':cat, 'cost':cost}


def proven(sku, seq):
    # Proven = two distinct exact own ledger settlement IDs.
    return [settle(seq, OWN, sku, amount=9000), settle(seq+1, OWN, sku, amount=9500)]


def row(sku, cat, at, baseline=(), legacy=False):
    # Current restock-pending.json schema (restock_r3.py); legacy rows predate baseline_unit_ids.
    r = {'sku':sku, 'at':at, 'reserve':10000, 'category':cat, 'policy_version':'bounded-v1',
         'matched_unit_id':None, 'evidence_ids':[], 'local_biased_memory_ids':[]}
    if not legacy: r['baseline_unit_ids'] = list(baseline)
    return r


def inv(units):
    return {'round':3, 'balance':START, 'start':START, 'units':units}


def set_units(p, units):
    (p/'inventory.json').write_text(json.dumps(inv(units)))


def outstanding(p):
    path = p/'restock-pending.json'
    return [r for r in (read(p, 'restock-pending.json') if path.exists() else []) if r.get('matched_unit_id') is None]


def rows_for(p, sku):
    return [r for r in read(p, 'restock-pending.json') if r['sku'] == sku]


class ProvenRepeatTests(unittest.TestCase):
    def test_proven_sku_with_one_owned_unit_gets_one_repeat(self):
        # P is proven: two distinct exact own ledger settlement IDs. One P unit is already on hand,
        # so the target of two units total permits exactly one more.
        catalog = {f'F{i}':goods(f'fill{i}') for i in range(6)}
        catalog['P'] = goods('catP', price=10000)
        rows = [settle(301, OWN, 'P', amount=9000), settle(302, OWN, 'P', amount=9500)]
        inv = {'round':3, 'balance':START, 'start':START,
               'units':[{'unit':'u-p1', 'sku':'P', 'category':'catP', 'cost':7500}]}
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            write_state(p, catalog, inv=inv, rows=rows)
            buy = offline_plan(p, now=100)['buy']
            self.assertEqual([x for x in buy if x['sku'] == 'P'], [{'sku':'P', 'qty':1}])


class PilotCapTests(unittest.TestCase):
    """NEW behaviour (expected RED): the old 6-unit ceiling becomes a 12-unit pilot ceiling."""

    def test_max12_pilot_admits_proven_sku_with_six_on_hand(self):
        catalog = {f'F{i}':goods(f'fill{i}') for i in range(6)}
        catalog['P'] = goods('catP', price=10000)
        units = [unit(f'u-f{i}', f'F{i}', f'fill{i}', cost=7500) for i in range(6)]
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            write_state(p, catalog, inv=inv(units), rows=proven('P', 301))
            # Cash and exposure are both available: 250k exposure - 45k held, allowance 100k.
            buy = offline_plan(p, now=100)['buy']
            got = [x for x in buy if x['sku'] == 'P']
            self.assertEqual(len(got), 1, buy)
            self.assertTrue(1 <= got[0]['qty'] <= 2)  # never past the per-SKU target of two held
            self.assertLessEqual(len(units)+len(outstanding(p)), CAP)


class PilotCapGuards(unittest.TestCase):
    """Independent guards: on hand + outstanding + planned never exceeds 12."""

    def catalog(self, n=6):
        c = {f'F{i}':goods(f'fill{i}') for i in range(12)}
        c.update({f'P{i}':goods(f'catP{i}') for i in range(n)})
        return c

    def proofs(self, n=6):
        return [r for i in range(n) for r in proven(f'P{i}', 400+10*i)]

    def test_twelve_on_hand_buys_nothing(self):
        units = [unit(f'u-f{i}', f'F{i}', f'fill{i}') for i in range(12)]
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            write_state(p, self.catalog(), inv=inv(units), rows=self.proofs())
            self.assertEqual(offline_plan(p, now=100)['buy'], [])
            self.assertEqual(outstanding(p), [])

    def test_ten_on_hand_plus_one_outstanding_plans_at_most_one(self):
        units = [unit(f'u-f{i}', f'F{i}', f'fill{i}') for i in range(10)]
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            write_state(p, self.catalog(), inv=inv(units), rows=self.proofs())
            (p/'restock-pending.json').write_text(json.dumps([row('F10', 'fill10', at=90)]))
            buy = offline_plan(p, now=100)['buy']
            self.assertLessEqual(sum(x['qty'] for x in buy), 1)
            self.assertLessEqual(len(units)+len(outstanding(p)), CAP)

    def test_repeated_calls_on_same_snapshot_never_exceed_cap(self):
        units = [unit(f'u-f{i}', f'F{i}', f'fill{i}') for i in range(6)]
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            write_state(p, self.catalog(), inv=inv(units), rows=self.proofs())
            for now in (100, 110, 120, 130):
                buy = offline_plan(p, now=now)['buy']
                self.assertTrue(all(x['qty'] >= 1 for x in buy))
                self.assertLessEqual(len(units)+len(outstanding(p)), CAP, now)


class ExplorationTests(unittest.TestCase):
    """At most three unproven SKUs explored at once, counting ones already held."""

    def catalog(self):
        c = {f'X{i}':goods(f'catX{i}') for i in range(10)}
        c['P'] = goods('catP')
        return c

    def unproven(self, buy, proven_skus=()):
        return [x['sku'] for x in buy if x['sku'] not in proven_skus]

    def test_exploration_at_most_three_from_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            write_state(p, self.catalog(), rows=[])
            buy = offline_plan(p, now=100)['buy']
            self.assertLessEqual(len(self.unproven(buy)), 3)
            self.assertTrue(all(x['qty'] == 1 for x in buy))

    def test_two_unproven_held_leaves_one_exploration_slot(self):
        # NEW behaviour (expected RED): existing unproven SKUs count toward the three.
        units = [unit(f'u-e{i}', f'E{i}', f'catE{i}') for i in range(2)]
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            write_state(p, self.catalog(), inv=inv(units), rows=[])
            self.assertLessEqual(len(self.unproven(offline_plan(p, now=100)['buy'])), 1)

    def test_four_unproven_held_blocks_new_exploration_but_not_proven(self):
        units = [unit(f'u-e{i}', f'E{i}', f'catE{i}') for i in range(4)]
        with self.subTest('no new exploration'), tempfile.TemporaryDirectory() as tmp:
            # NEW behaviour (expected RED): the old code explores into the two remaining slots.
            p = Path(tmp)
            write_state(p, self.catalog(), inv=inv(units), rows=[])
            self.assertEqual(self.unproven(offline_plan(p, now=100)['buy']), [])
        with self.subTest('proven still allowed'), tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            write_state(p, self.catalog(), inv=inv(units), rows=proven('P', 301))
            buy = offline_plan(p, now=100)['buy']
            self.assertEqual(self.unproven(buy, {'P'}), [])
            self.assertIn('P', [x['sku'] for x in buy])


class ReservationRegressionTests(unittest.TestCase):
    """Regression coverage against the current baseline_unit_ids / matched_unit_id schema; may already pass."""

    def test_reservation_lifecycle_for_proven_target_two(self):
        catalog = {'P':goods('catP')}
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            write_state(p, catalog, rows=proven('P', 301))

            # Empty inventory: proven P is ordered up to target two, one row per unit.
            self.assertEqual(offline_plan(p, now=100)['buy'], [{'sku':'P', 'qty':2}])
            rows = rows_for(p, 'P')
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(r['baseline_unit_ids'] == [] and r['matched_unit_id'] is None for r in rows))

            # Same snapshot next call: two outstanding already cover the target; no duplicates.
            self.assertEqual(offline_plan(p, now=110)['buy'], [])
            self.assertEqual(len(rows_for(p, 'P')), 2)
            self.assertEqual(len(outstanding(p)), 2)

            # slot-recovery-v1 (exact evidence only): the kit logs no unit id, so a new same-SKU arrival is NOT
            # attributed to either row (no oldest-first mapping). Both rows stay outstanding at full reserve.
            reserves = sorted(r['reserve'] for r in rows_for(p, 'P'))
            set_units(p, [unit('u-new1', 'P', 'catP', cost=7500)])
            self.assertEqual(offline_plan(p, now=120)['buy'], [])
            self.assertEqual([r['matched_unit_id'] for r in rows_for(p, 'P')], [None, None])
            self.assertEqual(len(outstanding(p)), 2)

            # Repeated calls never assign it later either, and never release by age.
            self.assertEqual(offline_plan(p, now=130)['buy'], [])
            self.assertEqual([r['matched_unit_id'] for r in rows_for(p, 'P')], [None, None])

            # Both arrive: still no inferred mapping, full reserve kept, and no extra buy beyond target.
            set_units(p, [unit('u-new1', 'P', 'catP', cost=7500), unit('u-new2', 'P', 'catP', cost=7500)])
            self.assertEqual(offline_plan(p, now=140)['buy'], [])
            self.assertEqual(len(rows_for(p, 'P')), 2)
            self.assertEqual(len(outstanding(p)), 2)
            self.assertEqual(sorted(r['reserve'] for r in rows_for(p, 'P')), reserves)

    def test_legacy_row_without_baseline_stays_outstanding(self):
        catalog = {'P':goods('catP')}
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            write_state(p, catalog, inv=inv([unit('u-p1', 'P', 'catP', cost=7500)]), rows=proven('P', 301))
            (p/'restock-pending.json').write_text(json.dumps([row('P', 'catP', at=90, legacy=True)]))
            # An own same-SKU unit on hand must not be assumed to be the legacy row's arrival.
            self.assertEqual(offline_plan(p, now=100)['buy'], [])
            legacy = [r for r in rows_for(p, 'P') if 'baseline_unit_ids' not in r]
            self.assertEqual(len(legacy), 1)
            self.assertIsNone(legacy[0]['matched_unit_id'])
            self.assertEqual(legacy[0]['reserve'], 10000)


class OwnEvidenceTests(unittest.TestCase):
    def write_cache(self, p, catalog, rows, own_ledger, units=()):
        write_state(p, catalog, inv=inv(list(units)), rows=rows)
        cache = read(p, 'public-settlements.json')
        cache['own_ledger'] = own_ledger
        (p/'public-settlements.json').write_text(json.dumps(cache))

    def test_public_tail_absence_does_not_erase_own_evidence(self):
        catalog = {'P':goods('catP'), 'A':goods('catA')}
        # The newest-500 tail no longer contains our settles; persisted own_ledger still does.
        tail = [settle(900+i, OTHER, 'A') for i in range(3)]
        own = {'301':{'sku':'P', 'price':9000}, '302':{'sku':'P', 'price':9500}}
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            self.write_cache(p, catalog, tail, own)
            buy = offline_plan(p, now=100)['buy']
            self.assertIn({'sku':'P', 'qty':2}, buy)  # still proven: target two
            self.assertEqual(read(p, 'restock-observation.json')['public_coverage']['own_ledger_ids'], [301, 302])
            self.assertTrue(all({301, 302} <= set(r['evidence_ids']) for r in rows_for(p, 'P')))

    def test_inferred_sale_does_not_count_as_own_evidence(self):
        catalog = {'P':goods('catP')}
        own = {'301':{'sku':'P', 'price':9000}}
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            self.write_cache(p, catalog, [], own, units=[unit('u-p1', 'P', 'catP', cost=7500)])
            self.assertEqual(offline_plan(p, now=100)['buy'], [])
            # u-p1 vanished: a sale may be inferred, but there is no exact ledger ID for it.
            set_units(p, [])
            buy = offline_plan(p, now=110)['buy']
            self.assertEqual(buy, [{'sku':'P', 'qty':1}])  # still one exact ID: unproven, no target-two repeat
            self.assertEqual(read(p, 'restock-observation.json')['public_coverage']['own_sku_counts'], {'P':1})
            self.assertEqual([r['evidence_ids'] for r in rows_for(p, 'P')], [[301]])


class HighPriceTests(unittest.TestCase):
    """NEXT SLICE (expected RED, not implemented): goods with 60k < list <= 100k need two distinct exact own
    settle IDs with median realized >= 75% list; target 1; high-price exposure <= 40% of the 5% budget;
    at most one such SKU across stock and pending. The unproven explorer stays at <= 60k."""

    def plan(self, catalog, rows, units=(), pending=(), start=START, nows=(100,)):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            write_state(p, catalog, inv={'round':3, 'balance':start, 'start':start, 'units':list(units)}, rows=rows)
            if pending: (p/'restock-pending.json').write_text(json.dumps(list(pending)))
            buys = [offline_plan(p, now=now)['buy'] for now in nows]
            return buys[0] if len(buys) == 1 else buys

    def paid(self, sku, *amounts, seq=301):
        return [settle(seq+i, OWN, sku, amount=a) for i, a in enumerate(amounts)]

    def test_proven_100k_with_two_own_80k_settles_buys_one(self):
        # RED under the current 60k per-SKU cap.
        catalog = {'H':goods('catH', price=100000)}
        self.assertEqual(self.plan(catalog, self.paid('H', 80000, 80000)), [{'sku':'H', 'qty':1}])

    def test_target_is_one_across_calls(self):
        # 10M start: 40% of the 5% budget is 200k, so only the target-one rule stops a second 61k unit.
        catalog = {'H':goods('catH', price=61000)}
        first, second = self.plan(catalog, self.paid('H', 50000, 50000), start=10000000, nows=(100, 110))
        self.assertEqual(first, [{'sku':'H', 'qty':1}])
        self.assertEqual(second, [])

    def test_insufficient_evidence_never_buys_high_price(self):
        catalog = {'H':goods('catH', price=100000)}
        cases = {'public category frequency':[settle(900+i, OTHER, 'H', amount=95000) for i in range(10)],
                 'one own settle':self.paid('H', 90000),
                 'duplicate own ID':[settle(301, OWN, 'H', amount=90000)]*2,
                 'low realized median':self.paid('H', 60000, 70000)}
        for name, rows in cases.items():
            with self.subTest(name):
                self.assertEqual(self.plan(catalog, rows), [])

    def test_100001_never(self):
        catalog = {'H':goods('catH', price=100001)}
        self.assertEqual(self.plan(catalog, self.paid('H', 100001, 100001), start=10000000), [])

    def test_default_explorer_stays_at_60k(self):
        catalog = {'L':goods('catL', price=60000), 'M':goods('catM', price=60001)}
        self.assertEqual(self.plan(catalog, []), [{'sku':'L', 'qty':1}])

    def test_concentration_is_forty_percent_of_five_percent_budget(self):
        # 4M start: 5% budget 200k, 40% of it 80k.
        with self.subTest('100k over 80k'):
            catalog = {'H':goods('catH', price=100000)}
            self.assertEqual(self.plan(catalog, self.paid('H', 80000, 80000), start=4000000), [])
        with self.subTest('80k fits'):  # RED
            catalog = {'H':goods('catH', price=80000)}
            self.assertEqual(self.plan(catalog, self.paid('H', 64000, 64000), start=4000000), [{'sku':'H', 'qty':1}])

    def test_at_most_one_high_price_sku_counting_stock_and_pending(self):
        # 10M start: concentration 200k would fit two 90k units, so only the one-SKU rule refuses H2.
        catalog = {'H1':goods('catH1', price=90000), 'H2':goods('catH2', price=90000)}
        rows = self.paid('H1', 80000, 80000) + self.paid('H2', 80000, 80000, seq=311)
        with self.subTest('empty'):  # RED
            buy = self.plan(catalog, rows, start=10000000)
            self.assertEqual([x['qty'] for x in buy], [1])
        with self.subTest('one on hand'):
            units = [unit('u-h1', 'H1', 'catH1', cost=67500)]
            self.assertEqual(self.plan(catalog, rows, units=units, start=10000000), [])
        with self.subTest('one pending'):
            pending = [dict(row('H1', 'catH1', at=90), reserve=90000)]
            self.assertEqual(self.plan(catalog, rows, pending=pending, start=10000000), [])


class ReservationExpiryTests(unittest.TestCase):
    """RED: age alone must not drop an outstanding reservation. An unobserved arrival is an unknown
    outcome, not a failure, so the row keeps holding its slot, exposure and per-SKU target."""

    def test_unmatched_rows_past_300s_do_not_trigger_reorder(self):
        catalog = {'P':goods('catP')}
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            write_state(p, catalog, rows=proven('P', 301))
            self.assertEqual(offline_plan(p, now=100)['buy'], [{'sku':'P', 'qty':2}])
            snapshot = (p/'inventory.json').read_text()
            # Same unmodified inventory 301 s later: no arrival observed, so nothing is proven failed.
            buy = offline_plan(p, now=401)['buy']
            self.assertEqual((p/'inventory.json').read_text(), snapshot)
            self.assertEqual([x for x in buy if x['sku'] == 'P'], [])  # else four P units possibly ordered
            self.assertGreaterEqual(len([r for r in outstanding(p) if r['sku'] == 'P']), 2)

    def test_legacy_row_past_300s_with_same_sku_on_hand_is_kept(self):
        catalog = {'P':goods('catP')}
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            write_state(p, catalog, inv=inv([unit('u-p1', 'P', 'catP', cost=7500)]), rows=proven('P', 301))
            (p/'restock-pending.json').write_text(json.dumps([row('P', 'catP', at=90, legacy=True)]))
            # Age 311 s; the on-hand P unit does not prove the legacy row was fulfilled.
            buy = offline_plan(p, now=401)['buy']
            self.assertEqual([x for x in buy if x['sku'] == 'P'], [])  # 1 on hand + 1 outstanding = target
            legacy = [r for r in rows_for(p, 'P') if 'baseline_unit_ids' not in r]
            self.assertEqual(len(legacy), 1)
            self.assertIsNone(legacy[0]['matched_unit_id'])
            self.assertEqual(legacy[0]['reserve'], 10000)


if __name__ == '__main__': unittest.main()
