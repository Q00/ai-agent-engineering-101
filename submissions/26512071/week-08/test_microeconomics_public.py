"""microecon-v1 public extension: other shops' actual public settlements (real ledger schema) as a conservative
secondary ordering signal among the ORIGINAL eligible candidates, and a cached board comparison in status only.
Offline fixtures only; no network, no seller pricing call."""
import hashlib
import json
import unittest

from test_oneday_pilot import NOW, OWN, ROWS, Base, setup, read, write, inventory
from test_microeconomics import own_sales_ledger

DAY = 86400
P1, P2, P3 = 'https://p1.example', 'https://p2.example', 'https://p3.example'


def pub(seq, sku, amount, shop, ts=NOW-60, grade='A', expires=NOW+30*DAY, warranty=None, drop=()):
    data = {'shop':shop, 'item':sku, 'sku':sku, 'amount':amount, 'grade':grade, 'expires_at':expires,
            'warranty_hours':warranty, 'unit':f'u-pub-{seq}', 'order':f'o-pub-{seq}', 'claim':None}
    for k in drop: data.pop(k)
    return {'seq':seq, 'ts':ts, 'kind':'settle', 'round':3, 'data':data}


def clean(sku, amount, first):
    return [pub(first, sku, amount, P1), pub(first+1, sku, amount, P2), pub(first+2, sku, amount, P3)]


def own_row(seq, uid, sku, grade='A', ts=NOW-3*3600, expires=NOW+30*DAY, amount=8874):
    """Our own shop's public settle row (real schema: the kit writes warranty_hours null for our 0-warranty receipts)."""
    return {'seq':seq, 'ts':ts, 'kind':'settle', 'round':3,
            'data':{'shop':OWN, 'item':sku, 'sku':sku, 'amount':amount, 'grade':grade, 'expires_at':expires,
                    'warranty_hours':None, 'unit':uid, 'order':f'o-own-{seq}', 'claim':None}}


# sale-time evidence for equal_own_ledger(): the own public settle rows of those exact seq/unit/SKU
OWN_SALES = [own_row(900+k, f'u-e{k}', sku) for k, sku in enumerate(['C', 'C', 'O', 'O'])]


def public_cache(p, price_rows, fetched=NOW-10, gaps=None, stale=False, own=OWN_SALES):
    write(p, 'public-settlements.json', {'source_url':'https://market.ourolabs.ai/api/ledger?after=-500',
                                         'fetched_at':fetched, 'own_urls':[OWN+'/'], 'rows':list(ROWS),
                                         'price_rows':list(price_rows)+list(own), 'stale':stale, 'cache_version':3,
                                         'gaps':gaps or []})


def equal_own_ledger(p):
    """C and O: identical own economics (two exact sales each, cost 5000, price 8874, same timing)."""
    units, events = {}, {}
    for k, sku in enumerate(['C', 'C', 'O', 'O']):
        uid = f'u-e{k}'
        units[uid] = {'sku':sku, 'cost':5000, 'kind':'food', 'grade':'A', 'supplied_at':NOW-5*3600,
                      'expires_at':NOW+30*DAY, 'first_seen':NOW-5*3600, 'gone_at':NOW-3600}
        events[f'settle:{900+k}'] = {'kind':'settle', 'seq':900+k, 'ts':NOW-3*3600, 'unit':uid, 'sku':sku, 'price':8874}
    write(p, 'one-day-pilot-ledger.json', {'pilot':'one-day-v1', 'requests':{}, 'units':units, 'events':events,
                                           'conflicts':{}})


def skus(p, now=NOW):
    from test_oneday_pilot import plan
    return [b['sku'] for b in plan(p, now)]


class PublicSelection(Base):
    def test_public_prices_change_selection_for_equal_own_economics(self):
        setup(self.p)
        equal_own_ledger(self.p)
        public_cache(self.p, [])
        self.assertEqual(skus(self.p)[:2], ['O', 'C'])  # no public evidence: baseline tie order

    def test_higher_comparable_price_promotes_c(self):
        setup(self.p)
        equal_own_ledger(self.p)
        public_cache(self.p, clean('C', 12000, 1000)+clean('O', 9000, 1010))
        skus(self.p)
        self.assertEqual(read(self.p, 'restock-observation.json')['decision']['microeconomics']['ranked'][:2], ['C', 'O'])  # ranking, not held-capped buys
        d = read(self.p, 'restock-observation.json')['decision']['microeconomics']
        ev = d['chosen_evidence']['C']
        self.assertEqual(ev['return_on_capital'], (8874-5000)/5000)  # own realized, kept separate
        self.assertEqual(ev['ledger_ids'], [900, 901])
        pc = ev['public']
        self.assertTrue(pc['use'])
        self.assertEqual(pc['ledger_ids'], [1000, 1001, 1002])
        self.assertEqual(pc['shops'], 3)
        self.assertEqual((pc['median'], pc['q25'], pc['q75']), (12000, 12000, 12000))
        self.assertEqual(pc['own_cost_median'], 5000)
        self.assertEqual(pc['market_proxy_return'], (12000-5000)/5000)
        self.assertEqual(pc['stratum'], {'grade':'A', 'kind':'food', 'warranty_hours':0, 'life_bucket':'ge24h'})
        self.assertAlmostEqual(pc['source_age_s'], 10)
        self.assertIn('not public seller margin', pc['label'])
        o = {r['sku']:r for r in read(self.p, 'microeconomics-status.json')['ranking']}['O']  # ranked, not bought
        self.assertTrue(o['public']['use'])
        self.assertEqual(o['public']['market_proxy_return'], (9000-5000)/5000)

    def test_flipped_public_prices_flip_selection(self):
        setup(self.p)
        equal_own_ledger(self.p)
        public_cache(self.p, clean('C', 9000, 1000)+clean('O', 13000, 1010))
        self.assertEqual(skus(self.p)[:2], ['O', 'C'])
        self.tmp.cleanup()  # fresh state: the first plan already used the stage room
        self.setUp()
        setup(self.p)
        equal_own_ledger(self.p)
        public_cache(self.p, clean('C', 13000, 1000)+clean('O', 9000, 1010))
        skus(self.p)
        self.assertEqual(read(self.p, 'restock-observation.json')['decision']['microeconomics']['ranked'][:2], ['C', 'O'])  # ranking, not held-capped buys

    def contaminated(self, c_rows, **kw):
        setup(self.p)
        equal_own_ledger(self.p)
        public_cache(self.p, c_rows+clean('O', 9000, 1010), **kw)
        got = skus(self.p)
        table = {r['sku']:r for r in read(self.p, 'microeconomics-status.json')['ranking']}
        return got, table['C']['public']

    def test_contamination_cannot_qualify(self):
        cases = {
            'wrong_sku':[pub(1000+k, 'K', 12000, s) for k, s in enumerate((P1, P2, P3))],
            'wrong_grade':[pub(1000+k, 'C', 12000, s, grade='B') for k, s in enumerate((P1, P2, P3))],
            'warranty':[pub(1000+k, 'C', 12000, s, warranty=72.0) for k, s in enumerate((P1, P2, P3))],
            'food_life_bucket':[pub(1000+k, 'C', 12000, s, expires=NOW+3600) for k, s in enumerate((P1, P2, P3))],
            'stale_settles':[pub(1000+k, 'C', 12000, s, ts=NOW-2000) for k, s in enumerate((P1, P2, P3))],
            'one_independent_shop':[pub(1000+k, 'C', 12000, P1) for k in range(3)],
            'own_shop':[pub(1000+k, 'C', 12000, s) for k, s in enumerate((OWN, OWN+'/', P1))],
            'missing_grade':[pub(1000+k, 'C', 12000, s, drop=('grade',)) for k, s in enumerate((P1, P2, P3))],
            'two_settles':clean('C', 12000, 1000)[:2],
            'replayed_order':[pub(1000, 'C', 12000, P1), pub(1001, 'C', 12000, P2),
                              dict(pub(1002, 'C', 12000, P3), data=dict(pub(1000, 'C', 12000, P3)['data']))],
            'above_list':[pub(1000+k, 'C', 99999, s) for k, s in enumerate((P1, P2, P3))]}
        for name, rows in cases.items():
            with self.subTest(name):
                self.tmp.cleanup()
                self.setUp()
                got, pc = self.contaminated(rows)
                self.assertEqual(got[:2], ['O', 'C'], name)
                self.assertFalse(pc['use'], name)
                self.assertTrue(pc['reason'], name)

    def test_stale_cache_or_coverage_gap_disables_all_public_use(self):
        for name, kw in {'stale_cache':{'fetched':NOW-2000}, 'marked_stale':{'stale':True},
                         'gap':{'gaps':[{'lo':5, 'hi':9, 'detected_at':NOW-30}]}}.items():
            with self.subTest(name):
                self.tmp.cleanup()
                self.setUp()
                got, pc = self.contaminated(clean('C', 12000, 1000), **kw)
                self.assertEqual(got[:2], ['O', 'C'])
                self.assertFalse(pc['use'])
                st = read(self.p, 'microeconomics-status.json')
                self.assertEqual(st['public_summary']['used'], 0)

    def test_sparse_own_sku_promoted_only_with_observed_own_cost(self):
        setup(self.p)
        public_cache(self.p, clean('C', 12000, 1000))
        skus(self.p)
        self.assertEqual(read(self.p, 'restock-observation.json')['decision']['microeconomics']['ranked'][:2], ['H', 'K'])  # no own C unit: C not promoted
        st = read(self.p, 'microeconomics-status.json')
        self.assertIn('no own acquisition cost', {r['sku']:r for r in st['ranking']}['C']['public']['reason'])
        self.tmp.cleanup()
        self.setUp()
        setup(self.p)
        inventory(self.p, units=[{'unit':'u-h1', 'sku':'C', 'cost':5000, 'kind':'food', 'grade':'A',
                                  'supplied_at':NOW-60, 'expires_at':NOW+30*DAY}])
        public_cache(self.p, clean('C', 12000, 1000))
        # demand-cover-v1: C is promoted to rank 1 but already holds 1 unit with no own rate -> ranked, not re-bought
        self.assertNotIn('C', skus(self.p))
        self.assertEqual(read(self.p, 'restock-observation.json')['decision']['microeconomics']['ranked'][0], 'C')
        st = read(self.p, 'microeconomics-status.json')
        row = {r['sku']:r for r in st['ranking']}['C']
        self.assertEqual(row['tier'], 'baseline_order')
        self.assertEqual(row['basis'], 'no_own_realized_sale')
        self.assertTrue(row['public']['use'])
        self.assertEqual(st['public_summary']['qualified'], 1)

    def test_negative_own_contribution_not_promoted_by_public(self):
        setup(self.p)
        own_sales_ledger(self.p)  # K: two own sales below cost
        public_cache(self.p, clean('K', 22000, 1000))
        skus(self.p)
        self.assertEqual(read(self.p, 'restock-observation.json')['decision']['microeconomics']['ranked'][:3], ['C', 'G', 'H'])
        self.assertEqual(read(self.p, 'restock-observation.json')['decision']['microeconomics']['ranked'][:3].count('K'), 0)
        row = {r['sku']:r for r in read(self.p, 'microeconomics-status.json')['ranking']}['K']
        self.assertEqual(row['basis'], 'net_contribution_not_positive')

    def test_no_new_eligible_sku_from_public(self):
        import microeconomics
        setup(self.p)
        public_cache(self.p, clean('X', 8000, 1000)+clean('S', 9000, 1010))  # X unproven, S short shelf
        got = skus(self.p)
        self.assertFalse({'X', 'S'} & set(got))
        st = read(self.p, microeconomics.STATUS)
        self.assertFalse({'X', 'S'} & set(st['ranked']))

    def test_real_seller_price_policy_is_never_written(self):
        setup(self.p)
        equal_own_ledger(self.p)
        public_cache(self.p, clean('C', 12000, 1000))
        skus(self.p)
        self.assertFalse((self.p/'seller-price-policy.json').exists())


class ReviewV3(Base):
    """Parent review V3: sale-time food grade only from exact own settle evidence; provenance and raw warranty kept;
    considered sample ids + exclusion reasons; explicit coverage; public_market_used flag."""

    def ranking(self):
        return {r['sku']:r for r in read(self.p, 'microeconomics-status.json')['ranking']}

    def test_aged_food_sold_as_c_never_compares_against_a(self):
        setup(self.p)
        equal_own_ledger(self.p)  # ledger units carry arrival grade A
        aged = [own_row(900+k, f'u-e{k}', sku, grade='C') for k, sku in enumerate(['C', 'C', 'O', 'O'])]
        public_cache(self.p, clean('C', 12000, 1000), own=aged)  # A comparables only
        self.assertEqual(skus(self.p)[:2], ['O', 'C'])
        pc = self.ranking()['C']['public']
        self.assertFalse(pc['use'])
        self.assertEqual([s['stratum']['grade'] for s in pc['strata']], ['C'])
        self.tmp.cleanup()
        self.setUp()
        setup(self.p)
        equal_own_ledger(self.p)
        public_cache(self.p, [pub(1000+k, 'C', 12000, s, grade='C') for k, s in enumerate((P1, P2, P3))], own=aged)
        skus(self.p)
        self.assertEqual(read(self.p, 'restock-observation.json')['decision']['microeconomics']['ranked'][:2], ['C', 'O'])  # ranking, not held-capped buys
        prov = self.ranking()['C']['public']['own_provenance']
        self.assertEqual({x['source'] for x in prov}, {'own_public_settle_row'})
        self.assertEqual(sorted(x['settle_seq'] for x in prov), [900, 901])

    def test_historical_food_without_sale_time_grade_is_omitted(self):
        setup(self.p)
        equal_own_ledger(self.p)
        public_cache(self.p, clean('C', 12000, 1000), own=())
        self.assertEqual(skus(self.p)[:2], ['O', 'C'])
        pc = self.ranking()['C']['public']
        self.assertFalse(pc['use'])
        self.assertEqual(pc['strata'], [])
        self.assertEqual(sorted(x['unit'] for x in pc['omitted_own_units']), ['u-e0', 'u-e1'])
        self.assertIn('no sale-time grade evidence', pc['omitted_own_units'][0]['reason'])

    def test_own_row_must_match_seq_unit_and_sku(self):
        setup(self.p)
        equal_own_ledger(self.p)
        wrong = [own_row(900, 'u-other', 'C'), own_row(901, 'u-e1', 'O')] + OWN_SALES[2:]
        public_cache(self.p, clean('C', 12000, 1000), own=wrong)
        skus(self.p)
        pc = self.ranking()['C']['public']
        self.assertFalse(pc['use'])
        self.assertEqual(len(pc['omitted_own_units']), 2)

    def test_held_snapshot_and_raw_warranty_provenance(self):
        setup(self.p)
        inventory(self.p, units=[{'unit':'u-h1', 'sku':'C', 'cost':5000, 'kind':'food', 'grade':'A',
                                  'supplied_at':NOW-60, 'expires_at':NOW+30*DAY}])
        public_cache(self.p, clean('C', 12000, 1000), own=())
        skus(self.p)
        pc = self.ranking()['C']['public']
        self.assertTrue(pc['use'])
        self.assertEqual(pc['own_provenance'][0]['source'], 'held_current_snapshot')
        s = pc['strata'][0]
        self.assertEqual(s['stratum']['warranty_hours'], 0)
        self.assertEqual(s['warranty_basis'], 'seller_r3.terms() warranty_hours=0 (held unit, not yet sold)')
        setup2 = [own_row(900+k, f'u-e{k}', sku) for k, sku in enumerate(['C', 'C', 'O', 'O'])]
        self.assertIsNone(setup2[0]['data']['warranty_hours'])  # raw own rows: null, compared as 0 by _comparable

    def test_considered_sample_ids_and_exclusion_reasons_persisted(self):
        setup(self.p)
        equal_own_ledger(self.p)
        rows = [pub(1000, 'C', 12000, P1, grade='B'), pub(1001, 'C', 12000, P2, warranty=72.0),
                pub(1002, 'C', 12000, P3, ts=NOW-2000), pub(1003, 'C', 12000, P1, expires=NOW+3600),
                pub(1004, 'C', 99999, P2), pub(1005, 'C', 12000, P3)]
        public_cache(self.p, rows)
        skus(self.p)
        s = self.ranking()['C']['public']['strata'][0]
        why = {x['seq']:x['reason'] for x in s['considered']}
        self.assertEqual(sorted(why), [1000, 1001, 1002, 1003, 1004, 1005])
        self.assertIn('grade', why[1000])
        self.assertIn('warranty', why[1001])
        self.assertIn('stale', why[1002])
        self.assertIn('life bucket', why[1003])
        self.assertIn('list', why[1004])
        self.assertEqual(why[1005], 'comparable')

    def test_summary_coverage_and_public_market_used_flag(self):
        setup(self.p)
        equal_own_ledger(self.p)
        public_cache(self.p, [], gaps=[{'lo':5, 'hi':9, 'detected_at':NOW-5000}])  # old gap: listed, not fresh
        skus(self.p)
        st = read(self.p, 'microeconomics-status.json')
        cov = st['public_summary']['coverage']
        self.assertEqual(cov['gaps'], [[5, 9]])
        self.assertEqual(cov['gap_seqs'], 5)
        self.assertFalse(cov['complete'])
        self.assertIn('seq_min', cov)
        self.assertIs(st['public_summary']['public_market_used'], False)
        d = read(self.p, 'restock-observation.json')['decision']['microeconomics']
        self.assertIs(d['public_market_used'], False)
        self.tmp.cleanup()
        self.setUp()
        setup(self.p)
        equal_own_ledger(self.p)
        public_cache(self.p, clean('C', 12000, 1000))
        skus(self.p)
        self.assertIs(read(self.p, 'restock-observation.json')['decision']['microeconomics']['public_market_used'], True)
        self.assertIs(read(self.p, 'microeconomics-status.json')['public_summary']['public_market_used'], True)


BOARD = {'round':3, 'current':3, 'totals':{'github_players':6},
         'players':[{'player':'a', 'student':True, 'student_total':1.7, 'shop':{'name':'sa', 'all':.9}},
                    {'player':'b', 'student':True, 'student_total':0.9, 'shop':{'name':'sb', 'all':.5}},
                    {'player':'me', 'student':True, 'student_total':0.711, 'shop':{'name':'fair-furniture', 'all':.355},
                     'buyer':{'name':'fair-furniture', 'all':.489}},
                    {'player':'d', 'student':True, 'student_total':0.711, 'shop':{'name':'sd', 'all':.3}},
                    {'player':'e', 'student':True, 'student_total':0.5, 'shop':None},
                    {'player':'house', 'student':False, 'student_total':None, 'total':2.0}],
         'shops':[{'name':'sa', 'deals':900, 'book':9000000, 'revenue':20000000, 'profit':4000000},
                  {'name':'sb', 'deals':300, 'book':2000000, 'revenue':5000000, 'profit':900000},
                  {'name':'fair-furniture', 'deals':279, 'book':837768, 'revenue':3935095, 'profit':611574,
                   'stock_cost':226194},
                  {'name':'sd', 'deals':100, 'book':700000, 'revenue':2000000, 'profit':-50000},
                  {'name':'idle', 'deals':0, 'book':5000000, 'revenue':0, 'profit':0}]}


class Board(Base):
    def wrap(self, board=BOARD, fetched=NOW-60, **extra):
        import microeconomics
        w = microeconomics.board_wrapper(board, fetched, 'https://market.ourolabs.ai/api/board', own_shop='fair-furniture')
        w.update(extra)
        write(self.p, 'microeconomics-board.json', w)
        return w

    def test_board_comparison_ties_denominator_and_context(self):
        import microeconomics
        self.wrap()
        b = microeconomics.board_comparison(self.p, {'units':[]}, NOW)
        self.assertEqual(b['status'], 'ok')
        self.assertFalse(b['stale'])
        own = b['own']
        self.assertEqual((own['player'], own['student_total'], own['rank'], own['denominator']), ('me', 0.711, 3, 5))
        self.assertEqual(own['tied_with'], ['d'])
        self.assertEqual(b['rank_rule'], 'competition rank: 1 + students with strictly higher student_total')
        self.assertEqual([p['player'] for p in b['near_peers']], ['a', 'b', 'd', 'e'])
        self.assertEqual(b['own_shop']['book'], 837768)
        pop = b['shop_population']
        self.assertEqual(pop['population'], 'board shops with deals > 0')
        self.assertEqual(pop['n'], 4)
        self.assertEqual(pop['median_book'], (837768+2000000)/2)
        self.assertEqual(pop['category_composition'], 'unobserved')
        self.assertIn('not a unit margin', b['note'])
        self.assertEqual(b['source_sha256'], hashlib.sha256(json.dumps(BOARD, sort_keys=True, separators=(',', ':'),
                                                                        ensure_ascii=False).encode()).hexdigest())

    def test_own_identity_from_inventory_certificates(self):
        import microeconomics
        w = self.wrap()
        w.pop('own_shop')
        write(self.p, 'microeconomics-board.json', w)
        inv = {'units':[{'unit':'u1', 'cert':{'cert':{'shop':'fair-furniture'}}}]}
        self.assertEqual(microeconomics.board_comparison(self.p, inv, NOW)['own']['player'], 'me')
        self.assertEqual(microeconomics.board_comparison(self.p, {'units':[]}, NOW)['status'], 'own_identity_unknown')

    def test_missing_stale_and_tampered_board_are_labelled(self):
        import microeconomics
        self.assertEqual(microeconomics.board_comparison(self.p, {'units':[]}, NOW)['status'], 'missing')
        self.wrap(fetched=NOW-2*3600)
        b = microeconomics.board_comparison(self.p, {'units':[]}, NOW)
        self.assertTrue(b['stale'])
        self.assertEqual(b['status'], 'ok')
        self.wrap(source_sha256='0'*64)
        b = microeconomics.board_comparison(self.p, {'units':[]}, NOW)
        self.assertEqual(b['status'], 'hash_mismatch')
        self.assertNotIn('own', b)
        (self.p/'microeconomics-board.json').write_text('{bad', encoding='utf-8')
        self.assertEqual(microeconomics.board_comparison(self.p, {'units':[]}, NOW)['status'], 'invalid')

    def test_house_players_never_in_class_denominator(self):
        import microeconomics
        board = json.loads(json.dumps(BOARD))
        board['players'].append({'player':'house2', 'house':True, 'student':True, 'student_total':5.0,
                                 'shop':{'name':'h2', 'all':1}})
        self.wrap(board=board)
        own = microeconomics.board_comparison(self.p, {'units':[]}, NOW)['own']
        self.assertEqual((own['rank'], own['denominator']), (3, 5))
        self.assertIn('house', microeconomics.board_comparison(self.p, {'units':[]}, NOW)['denominator_rule'])

    def test_future_fetched_at_is_labelled_not_fresh(self):
        import microeconomics
        self.wrap(fetched=NOW+79)
        b = microeconomics.board_comparison(self.p, {'units':[]}, NOW)
        self.assertTrue(b['stale'])
        self.assertTrue(b['fetched_after_evaluation'])
        self.assertIsNone(b['age_s'])

    def test_board_is_in_status_but_never_changes_selection(self):
        setup(self.p)
        equal_own_ledger(self.p)
        public_cache(self.p, clean('C', 12000, 1000))
        without = skus(self.p)
        self.tmp.cleanup()
        self.setUp()
        setup(self.p)
        equal_own_ledger(self.p)
        public_cache(self.p, clean('C', 12000, 1000))
        self.wrap()
        self.assertEqual(skus(self.p), without)
        st = read(self.p, 'microeconomics-status.json')
        self.assertEqual(st['board']['own']['rank'], 3)
        self.assertEqual(st['board']['used_in_selection'], False)


if __name__ == '__main__':
    unittest.main()
