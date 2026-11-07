"""A negotiation the buyer walked away from releases its unit after OPEN_TTL (3 min), not 15 min."""
import time

import shop_r3


class Mem:
    def __init__(self):
        self.rows = []

    def record_negotiation(self, *a, **k):
        self.rows.append((a, k))


def test_open_ttl_is_a_few_minutes():
    assert 120 <= shop_r3.OPEN_TTL <= 300


def test_gc_releases_a_stale_negotiation_and_keeps_a_live_one():
    shop = shop_r3.Shop(Mem())
    unit = {"sku": "G1", "unit": "u-1"}
    shop.open = {"old": {"t0": time.time() - shop_r3.OPEN_TTL - 1, "unit": unit, "first_ask": 1, "offers": []},
                 "new": {"t0": time.time() - 30, "unit": {"sku": "G2", "unit": "u-2"}, "first_ask": 1, "offers": []}}
    shop._gc()
    assert list(shop.open) == ["new"]
    assert shop.mem.rows[0][0][-1] == "timeout"
