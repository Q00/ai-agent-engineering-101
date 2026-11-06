"""buyer_r3 ranks candidates by expected share of list saved (the market's buyer score), not by won."""
from unittest.mock import patch

import buyer_r3


class Mem:
    def break_rate(self, *a, **k):
        return 0.0

    def shop_reputation(self, url):
        return {"break_rate": 0.0, "lie_rate": 0.0, "our_buys": 0, "our_paid": 0, "our_bad": 0,
                "talks_24h": 0, "deals_24h": 0}

    def price_ratio(self, sku=None, category=None, **k):
        return {"BIG": 0.75, "SMALL": 0.60}[sku], 10

    def shop_price_ratio(self, url, category=None, **k):
        return None

    def shop_profile(self, url):
        return None


def test_bigger_discount_beats_bigger_won():
    cat = {"BIG": {"list_price": 1_500_000, "kind": "food", "category": "c"},
           "SMALL": {"list_price": 500_000, "kind": "food", "category": "c"}}
    shops = [{"url": "https://a", "name": "a", "offers": [{"sku": "BIG", "qty": 1}, {"sku": "SMALL", "qty": 1}]}]
    order = {"acceptable": ["BIG", "SMALL"], "budget": 2_000_000}
    with patch.object(buyer_r3.market, "catalog", return_value=cat):
        out = buyer_r3.candidates(order, shops, Mem(), set())
    # BIG saves 375k (25%), SMALL saves 200k (40%): the market scores SMALL higher
    assert [c["sku"] for c in out] == ["SMALL", "BIG"]
    assert out[0]["saved"] == 0.4
