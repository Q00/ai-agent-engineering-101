"""Market-research extraction contract: synthetic snapshots and in-memory cache only.

The same assertions run before and after extraction, including full metadata and
hub contents rather than just the numerical ratio. No live data or API calls.
"""
import contextlib
import copy
import inspect
import io
import unittest
from unittest.mock import patch

import restock


NOW = 1_800_000_000
PUBLIC_SOURCE = '/api/shops+/api/board: top revenue median'
FALLBACK = (.4, {'source':'default: public API unavailable/insufficient, no fresh cache',
                 'default':True, 'cached':False, 'shops':0})


class MarketResearchTests(unittest.TestCase):
    def snapshots(self, quantities=(1, 1, 1)):
        catalog = {'synthetic':{'list_price':4_000_000}}
        shops = [{'name':str(i), 'stock':{'synthetic':q}} for i,q in enumerate(quantities)]
        board = [{'name':str(i), 'revenue':10-i, 'profit':0} for i in range(len(shops))]
        return shops, board, catalog

    def resolve(self, hub, observation=None, now=NOW):
        with patch.object(restock.time, 'time', return_value=now) as clock:
            result = restock.resolve_market_ratio(hub, observation)
        clock.assert_called_once_with()
        return result

    def cache(self, ratio=.5, ts=NOW):
        return {'ratio':ratio, 'ts':ts, 'shops':3, 'source':PUBLIC_SOURCE}

    def test_public_signatures_and_module_stay_compatible(self):
        for name, signature in [('market_ratio','(shops, board, catalog)'),
                                ('resolve_market_ratio','(hub, observation=None)')]:
            fn = getattr(restock, name)
            self.assertEqual(str(inspect.signature(fn)), signature)
            self.assertEqual(fn.__module__, 'restock')
        self.assertEqual(restock.popularity.__module__, 'restock')

    def test_sufficient_snapshots_and_dict_envelopes_are_pure(self):
        shops, board, catalog = self.snapshots()
        expected = (.375, {'source':PUBLIC_SOURCE, 'default':False, 'shops':3})
        for wrapped in (False, True):
            args = ({'shops':shops}, {'shops':board}, catalog) if wrapped else (shops, board, catalog)
            before = copy.deepcopy(args)
            self.assertEqual(restock.market_ratio(*args), expected)
            self.assertEqual(args, before)

    def test_insufficient_and_invalid_public_rows(self):
        shops, board, catalog = self.snapshots()
        invalid = [None, {'name':'missing', 'stock':{}},
                   {'name':'unknown', 'stock':{'absent':1}, 'revenue':1, 'profit':0}]
        for count in (0, 1, 2):
            self.assertEqual(restock.market_ratio(shops[:count]+invalid, board, catalog),
                             (.4, {'source':'default: insufficient public shops/financials',
                                   'default':True, 'shops':count}))
        for field, value in [('revenue',float('nan')), ('profit',float('inf')),
                             ('profit',-6_000_000), ('stock',{'synthetic':-1}),
                             ('stock',{'synthetic':'1'})]:
            broken = copy.deepcopy(shops)
            metrics = copy.deepcopy(board)
            (broken if field == 'stock' else metrics)[0][field] = value
            self.assertEqual(restock.market_ratio(broken, metrics, catalog)[1]['shops'], 2)

    def test_top_five_revenue_tie_break_and_clamps(self):
        shops, board, catalog = self.snapshots((0, 0, 0, 100, 100, 100))
        for row in board:
            row['revenue'] = 10
        self.assertEqual(restock.market_ratio(shops[::-1], board, catalog),
                         (.30, {'source':PUBLIC_SOURCE, 'default':False, 'shops':5}))
        self.assertEqual(restock.market_ratio(*self.snapshots((100,)*3))[0], .75)

    def test_fresh_observation_replaces_only_cache_and_preserves_source(self):
        source = {'source':PUBLIC_SOURCE, 'default':False, 'shops':5}
        observation = (.6, source)
        hub = {'unrelated':{'keep':[1]}, 'market_ratio_cache':self.cache(ts=NOW-5000)}
        before = copy.deepcopy(observation)
        self.assertEqual(self.resolve(hub, observation),
                         (.6, {**source, 'cached':False, 'ts':NOW}))
        self.assertEqual(hub, {'unrelated':{'keep':[1]},
                              'market_ratio_cache':dict(self.cache(.6), shops=5)})
        self.assertEqual(observation, before)

    def test_cache_reuse_at_inclusive_boundaries_does_not_renew(self):
        for age in (0, 1799, 1800):
            for observation in (None, (.4, {'default':True, 'shops':0})):
                hub = {'market_ratio_cache':self.cache(ts=NOW-age), 'other':[1]}
                before = copy.deepcopy(hub)
                self.assertEqual(self.resolve(hub, observation),
                                 (.5, {'source':PUBLIC_SOURCE, 'shops':3, 'ts':NOW-age,
                                       'default':False, 'cached':True, 'refresh_failed':True}))
                self.assertEqual(hub, before)

    def test_missing_expired_future_and_invalid_cache_fallback_without_mutation(self):
        cases = [{}, {'market_ratio_cache':{}},
                 *({'market_ratio_cache':self.cache(ts=ts)} for ts in (NOW-1801, NOW+1, None, 'bad')),
                 *({'market_ratio_cache':self.cache(ratio=r)}
                   for r in (.29, .76, None, 'bad', float('inf')))]
        for hub in cases:
            with self.subTest(hub=hub):
                before = copy.deepcopy(hub)
                self.assertEqual(self.resolve(hub), FALLBACK)
                self.assertEqual(hub, before)

    def test_constants_and_replaced_time_object_are_live_dependencies(self):
        with patch.object(restock, 'DEFAULT_RATIO', .22), \
                patch.object(restock, 'RATIO_MIN', .10), \
                patch.object(restock, 'RATIO_MAX', .20), \
                patch.object(restock, 'RATIO_CACHE_S', 9), \
                patch.object(restock, 'time') as clock:
            clock.time.return_value = NOW
            self.assertEqual(restock.market_ratio([], [], {})[0], .22)
            self.assertEqual(restock.market_ratio(*self.snapshots())[0], .20)
            self.assertEqual(restock.market_ratio(*self.snapshots((0,)*3))[0], .10)
            hub = {'market_ratio_cache':self.cache(.15, NOW-9)}
            self.assertEqual(restock.resolve_market_ratio(hub)[0], .15)
            clock.time.return_value = NOW+1
            self.assertEqual(restock.resolve_market_ratio(hub)[0], .22)
            self.assertEqual(hub['market_ratio_cache'], self.cache(.15, NOW-9))

    def test_research_emits_no_stdout_or_stderr(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            observation = restock.market_ratio(*self.snapshots())
            self.resolve({}, observation)
            self.resolve({})
        self.assertEqual((stdout.getvalue(), stderr.getvalue()), ('', ''))


if __name__ == '__main__':
    unittest.main()
