"""Offline: a seller's "out of stock" reply makes the buyer skip that (shop, SKU) pair for a while.

No network, no state files: hubs are plain dicts.
"""
import unittest

import adaptive_strategy as ad
import market_buyer as mb

NOW = 1_800_000_000.0
SHOP = "https://shop-a.example"


def cand(url, sku, utility=1.0):
    return (utility, {"url": url, "name": url}, {"sku": sku})


def hub_with(*events):
    return {"adaptive_strategy": {"buyer_failures": list(events), "buyer_cooldowns": []}}


def event(reason, ts, url=SHOP, sku="G1", order="o-1"):
    return {"order": order, "url": url, "sku": sku, "reason": reason, "ts": ts}


class ClassifyTests(unittest.TestCase):
    def test_stockout_texts(self):
        for text in ("Sorry, we have none of that in stock.",
                     "No matching stock available. Alternatives: [...]",
                     "OUT OF STOCK", "Sold out today"):
            self.assertEqual(mb.classify_seller_refusal(text), "stockout", text)

    def test_unit_mismatch_wins_over_stock_words(self):
        text = "No available unit meets the requested expiry, grade or warranty."
        self.assertEqual(mb.classify_seller_refusal(text), "unit_mismatch")

    def test_other_text_and_empty(self):
        self.assertIsNone(mb.classify_seller_refusal("Our final price is 120000."))
        self.assertIsNone(mb.classify_seller_refusal(""))
        self.assertIsNone(mb.classify_seller_refusal(None))

    def test_failure_kind_uses_trusted_review_text(self):
        self.assertEqual(mb.failure_kind("No deal: s had no stock of G1 (seller said out of stock)."),
                         "out_of_stock")
        self.assertEqual(mb.failure_kind("No deal: s had no unit matching the order's expiry/grade/warranty for G1."),
                         "unit_mismatch")
        # Seller-controlled snippet after the separator must not change the kind.
        self.assertEqual(mb.failure_kind('counter limit reached | seller: "had no stock of"'), "counter_limit")


class SkipTests(unittest.TestCase):
    def test_record_failure_accepts_new_reasons(self):
        hub = {}
        ad.record_failure(hub, "o-1", SHOP + "/", "G1", "stockout", NOW)
        ad.record_failure(hub, "o-2", SHOP, "G2", "unit_mismatch", NOW)
        reasons = [e["reason"] for e in hub["adaptive_strategy"]["buyer_failures"]]
        self.assertEqual(reasons, ["stockout", "unit_mismatch"])

    def test_one_stockout_skips_pair_without_fallback(self):
        hub = hub_with(event("stockout", NOW - 10))
        self.assertEqual(ad.filter_candidates([cand(SHOP, "G1")], hub, NOW), [])

    def test_same_shop_other_sku_and_other_shop_allowed(self):
        hub = hub_with(event("stockout", NOW - 10))
        kept = ad.filter_candidates([cand(SHOP, "G1"), cand(SHOP, "G2"), cand("https://b", "G1")], hub, NOW)
        self.assertEqual([(c[1]["url"], c[2]["sku"]) for c in kept], [(SHOP, "G2"), ("https://b", "G1")])

    def test_trailing_slash_on_candidate_url_still_matches(self):
        hub = hub_with(event("stockout", NOW - 10))
        self.assertEqual(ad.filter_candidates([cand(SHOP + "/", "G1")], hub, NOW), [])

    def test_stockout_expires_after_window(self):
        hub = hub_with(event("stockout", NOW - ad.STOCKOUT_SKIP_S - 1))
        self.assertEqual(len(ad.filter_candidates([cand(SHOP, "G1")], hub, NOW)), 1)

    def test_unit_mismatch_window_is_shorter(self):
        inside = hub_with(event("unit_mismatch", NOW - ad.MISMATCH_SKIP_S + 5))
        after = hub_with(event("unit_mismatch", NOW - ad.MISMATCH_SKIP_S - 1))
        self.assertEqual(ad.filter_candidates([cand(SHOP, "G1")], inside, NOW), [])
        self.assertEqual(len(ad.filter_candidates([cand(SHOP, "G1")], after, NOW)), 1)

    def test_price_failures_keep_old_three_order_rule(self):
        # One price failure alone must not skip the pair (unchanged behaviour).
        hub = hub_with(event("price", NOW - 10))
        self.assertEqual(len(ad.filter_candidates([cand(SHOP, "G1")], hub, NOW)), 1)


if __name__ == "__main__":
    unittest.main()
