import time, unittest

import market_buyer


def trade(shop, item, result, ago):
    return {"shop": shop, "item": item, "result": result, "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW - ago))}


NOW = time.time()
CATALOG = {"G1": {"kind": "goods"}, "G2": {"kind": "goods"}, "F1": {"kind": "food"}}


class ShortWarrantyShopsTest(unittest.TestCase):
    def skips(self, trades):
        return market_buyer.short_warranty_shops({"trades": trades}, CATALOG, now=NOW)

    def test_two_of_the_last_three_goods_receipts_short_skip_the_shop(self):
        # seochanit on 11/05: a goods deal now and then between 0 h receipts
        t = [trade("s", "G1", "no_warranty", 900), trade("s", "G2", "deal", 600), trade("s", "G1", "no_warranty", 300)]
        self.assertEqual(self.skips(t), {"s"})
        self.assertEqual(self.skips(t[:2]), set())                # one short receipt with a deal after it: tried again

    def test_only_the_last_three_count(self):
        t = [trade("s", "G1", "no_warranty", 900), trade("s", "G1", "no_warranty", 800),
             trade("s", "G1", "deal", 300), trade("s", "G2", "deal", 200)]
        self.assertEqual(self.skips(t), set())                    # the shop fixed its warranty

    def test_the_last_goods_receipt_short_skips_the_shop(self):
        # 11/07 02:00-07:10: 19 short receipts, 10 of them the second of a pair from the same shop within the hour
        # (seochanit SS..SS, yms979 SSS, lee-sungsoo SSS): the first one is enough to leave it out for the hours
        t = [trade("s", "G1", "deal", 900), trade("s", "G2", "deal", 600), trade("s", "G1", "no_warranty", 300)]
        self.assertEqual(self.skips(t), {"s"})

    def test_food_and_talks_without_a_receipt_do_not_count(self):
        t = [trade("s", "G1", "no_warranty", 900), trade("s", "F1", "deal", 800), trade("s", "G1", "rejected", 700),
             trade("s", "G2", "no_warranty", 600)]
        self.assertEqual(self.skips(t), {"s"})                    # food deals do not hide two short goods receipts
        self.assertEqual(self.skips([trade("s", "F1", "no_warranty", 10)] * 3), set())

    def test_old_receipts_expire_so_the_shop_is_tried_again(self):
        old = market_buyer.SHORT_WARRANTY_HOURS * 3600 + 60
        t = [trade("s", "G1", "no_warranty", old + 20), trade("s", "G1", "no_warranty", old)]
        self.assertEqual(self.skips(t), set())
        self.assertEqual(self.skips(t + [trade("s", "G1", "no_warranty", 30), trade("s", "G1", "no_warranty", 20)]), {"s"})

    def test_a_trade_without_a_readable_time_is_left_out(self):
        bad = [{"shop": "s", "item": "G1", "result": "no_warranty"}, {"shop": "s", "item": "G1", "result": "no_warranty", "time": "x"}]
        self.assertEqual(self.skips(bad), set())
        self.assertEqual(market_buyer.short_warranty_shops({}, CATALOG, now=NOW), set())
        self.assertEqual(self.skips([{"item": "G1", "result": "no_warranty", "time": trade("s", "G1", "x", 10)["time"]}] * 3), set())


class SkipShortWarrantyTest(unittest.TestCase):
    MEM = {"trades": [trade("s", "G1", "no_warranty", 300), trade("s", "G1", "no_warranty", 200)]}
    SHOPS = [{"name": "s"}, {"name": "t"}]

    def test_goods_orders_leave_the_shop_out(self):
        left, brief = market_buyer.skip_short_warranty(self.SHOPS, self.MEM, CATALOG, {"kind": "goods"}, now=NOW)
        self.assertEqual(([s["name"] for s in left], brief), (["t"], ["s"]))

    def test_food_orders_and_a_list_of_only_skipped_shops_keep_every_shop(self):
        for order, shops in (({"kind": "food"}, self.SHOPS), ({"kind": "goods"}, self.SHOPS[:1]), ({}, self.SHOPS)):
            left, brief = market_buyer.skip_short_warranty(shops, self.MEM, CATALOG, order, now=NOW)
            self.assertEqual((left, brief), (shops, []))


if __name__ == "__main__":
    unittest.main()
