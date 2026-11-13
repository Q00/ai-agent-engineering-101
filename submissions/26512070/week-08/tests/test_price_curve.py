"""core.price_curve: the acceptance curve and the open it picks (user 11/13: deals count, not the optimum alone)."""
import random
import unittest

from core import price_curve as pc


def rows(open_, n, deals, price, cost=0.58):
    return [("goods", 500000, open_, i < deals, price if i < deals else None, price - cost if i < deals else None)
            for i in range(n)]


class PriceCurve(unittest.TestCase):
    def test_deal_rate_never_rises_with_the_open(self):
        self.assertEqual(pc._pava_down([0.5, 0.6, 0.2], [10, 10, 10]), [0.55, 0.55, 0.2])

    def test_min_p_keeps_deals(self):
        g = pc.fit_group(rows(0.68, 100, 50, 0.64) + rows(0.72, 100, 30, 0.70) + rows(0.80, 100, 10, 0.80))
        # by value alone 0.72 (0.30 x 0.12 + ...) could win; min_p 0.8 of the best rate (0.5) leaves only 0.68
        self.assertEqual(pc.choose(g, {"curve_min_p": 0.8})[0], 0.68)
        self.assertEqual(pc.choose(g, {"curve_min_p": 0.0, "curve_volume_weight": 0})[0], 0.72)

    def test_explore_moves_one_bucket(self):
        class R:
            def random(self): return 0.0
            def choice(self, xs): return xs[1]
        pc._CACHE.clear()
        pc._CACHE.update(mt=None, doc=None)
        g = pc.fit_group(rows(0.68, 100, 50, 0.64))
        s, _ = pc.choose(g)
        self.assertEqual(s, 0.68)
        self.assertEqual(round(s + R().choice((-pc.OPEN_STEP, pc.OPEN_STEP)), 2), 0.70)


if __name__ == "__main__":
    unittest.main()
