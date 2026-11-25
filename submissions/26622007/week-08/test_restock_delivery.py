"""Delivery evidence survives sale; stock and cash reservations are distinct."""
import json
import os
import tempfile
import unittest
from unittest import mock

import market_model
import restock
from test_profit_rules import CAT, CFG


class DeliveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = self.tmp.name

    def sent(self, qty=2, at=1000):
        restock.note_sent(self.md, [{"sku": "H1", "qty": qty}], CAT, now=at)

    def supply(self, units, ts=1010, shop="nocha12", seq=1, sold=True):
        row = market_model.compact({"kind": "supply", "seq": seq, "ts": ts,
                                   "data": {"shop": shop, "sku": "H1", "units": units}})
        if row:
            with open(os.path.join(self.md, market_model.ROWS), "a") as f:
                f.write(json.dumps(row) + "\n")
                if sold:
                    for i, uid in enumerate(units):
                        sale = market_model.compact({"kind": "settle", "seq": seq + 100 + i, "ts": ts + 1,
                                                     "data": {"shop": shop, "item": "H1", "unit": uid}})
                        f.write(json.dumps(sale) + "\n")

    def test_supplied_then_sold_never_returns_as_pending_stock(self):
        self.sent()
        self.supply(["u1", "u2"])
        waiting = restock.pending(self.md, {"units": []}, now=1080)
        self.assertEqual(sum(w["qty"] for w in waiting), 0)
        # Inventory/wallet could predate the supply: do not free its cash.
        self.assertEqual(sum(w["worst"] * w.get("cash_qty", w["qty"]) for w in waiting), 30000)

    def test_actual_closed_window_releases_five_sold_units_but_keeps_full_cash(self):
        from pathlib import Path
        fixture = json.loads((Path(__file__).parent / "testdata" /
                              "restock_closed_supply_sales.json").read_text())
        Path(self.md, restock.SENT).write_text(json.dumps(fixture["sent"]))
        Path(self.md, market_model.ROWS).write_text(
            "".join(json.dumps(row) + "\n" for row in fixture["cache"]))
        waiting = restock.pending(self.md, fixture["inventory"], now=fixture["observed_at"])
        self.assertEqual(sum(w["qty"] for w in waiting), 0)
        self.assertEqual(sum(w["cash_qty"] for w in waiting), 85)
        self.assertAlmostEqual(sum(w["worst"] * w["cash_qty"] for w in waiting), 24953766.315)

    def sale(self, uid, ts=1011, sku="H1", shop="https://shop.example", seq=200):
        row = market_model.compact({"kind": "settle", "seq": seq, "ts": ts,
                                   "data": {"shop": shop, "item": sku, "unit": uid}})
        with open(os.path.join(self.md, market_model.ROWS), "a") as f:
            f.write(json.dumps(row) + "\n")

    def test_sale_shop_url_or_display_name_joins_only_our_supply(self):
        self.sent()
        self.supply(["u1", "u2"], sold=False)
        self.sale("u1")
        self.sale("u2", shop="Store display name", seq=201)
        waiting = restock.pending(self.md, {"units": []}, now=1080)
        self.assertEqual(sum(w["qty"] for w in waiting), 0)
        self.assertEqual(sum(w["worst"] * w["cash_qty"] for w in waiting), 30000)
        self.assertEqual(restock.pending(self.md, {"units": []}, now=1240), [])

    def test_sale_cannot_create_delivery_or_clear_unsold_stock(self):
        self.sent()
        self.supply(["u1", "u2"], sold=False)
        # Valid-looking sales cannot clear missing ownership, another SKU, an
        # unsold unit, or sales before delivery / after this observation.
        self.sale("foreign", shop="nocha12")
        self.sale("u1", sku="OTHER", seq=201)
        self.sale("u1", ts=1009, seq=202)
        self.sale("u2", ts=1081, seq=203)
        self.assertEqual(restock.pending(self.md, {"units": []}, now=1080)[0]["qty"], 2)

    def test_other_supply_owner_cannot_be_cleared_by_matching_sale_name(self):
        self.sent()
        self.supply(["foreign"], shop="other", sold=False)
        self.sale("foreign", shop="nocha12")
        self.assertEqual(restock.pending(self.md, {"units": []}, now=1080)[0]["qty"], 2)

    def test_sale_rows_are_order_independent_and_units_are_consumed_once(self):
        self.sent(1)
        self.sent(1, at=1020)
        self.sale("u1", ts=1030)
        self.supply(["u1"], ts=1022, sold=False)
        self.sale("u1", ts=1021, seq=201)  # older duplicate must not hide valid sale
        waiting = restock.pending(self.md, {"units": []}, now=1080)
        self.assertEqual(sum(w["qty"] for w in waiting), 1)
        self.assertEqual(sum(w["cash_qty"] for w in waiting), 2)

    def test_old_url_settlement_and_supply_do_not_clear_new_request(self):
        self.sent(1, at=1020)
        self.supply(["u1"], ts=1010, sold=False)
        self.sale("u1")
        self.assertEqual(restock.pending(self.md, {"units": []}, now=1080)[0]["qty"], 1)

    def test_partial_supply_broken_or_sold_units_still_delivered(self):
        self.sent()
        self.supply(["u1"])
        self.assertEqual(sum(w["qty"] for w in restock.pending(self.md, {"units": []}, now=1080)), 1)

    def test_other_shop_and_unconfirmed_requests_stay_reserved(self):
        self.sent()
        self.supply(["u1", "u2"], shop="other")
        self.assertEqual(restock.pending(self.md, {"units": []}, now=1080)[0]["qty"], 2)

    def test_same_units_do_not_confirm_two_requests_or_count_twice(self):
        self.sent(1)
        self.sent(1, at=1020)
        self.supply(["u1"], ts=1022)
        self.supply(["u1"], ts=1022, seq=2)
        waiting = restock.pending(self.md, {"units": [{"sku": "H1", "unit": "u1", "supplied_at": 1022}]}, now=1080)
        self.assertEqual(sum(w["qty"] for w in waiting), 1)

    def test_cash_reservation_still_blocks_a_purchase_with_stale_wallet(self):
        inv = {"balance": 5000000, "units": []}
        cfg = {**CFG, "keep_cash": 4970000}
        self.assertEqual(restock.guard([{"sku": "H1", "qty": 1}], inv, CAT, cfg,
                                      waiting=[{"sku": "H1", "qty": 0, "cash_qty": 2, "worst": 15000}]), [])

    def test_delivery_without_sale_in_stale_snapshot_keeps_stock_reserved(self):
        self.sent()
        self.supply(["u1", "u2"], sold=False)
        waiting = restock.pending(self.md, {"units": []}, now=1080)
        self.assertEqual(sum(w["qty"] for w in waiting), 2)
        cfg = {**CFG, "max_units_per_sku": 2, "sku_target_units": {"H1": 2}}
        self.assertEqual(restock.guard([{"sku": "H1", "qty": 2}],
                                      {"balance": 5000000, "units": []}, CAT, cfg, waiting=waiting), [])

    def test_old_delivery_cannot_confirm_a_later_request_after_ttl_or_prune(self):
        self.sent(1)
        self.supply(["u1"], ts=1060)
        self.sent(1, at=1062)
        self.assertEqual(sum(w["qty"] for w in restock.pending(self.md, {"units": []}, now=1100)), 1)
        self.assertEqual(sum(w["qty"] for w in restock.pending(self.md, {"units": []}, now=1241)), 1)
        self.sent(1, at=1242)  # note_sent prunes the expired request too
        self.assertEqual(sum(w["qty"] for w in restock.pending(self.md, {"units": []}, now=1243)), 2)

    def test_expired_request_releases_reservation(self):
        self.sent()
        self.assertEqual(restock.pending(self.md, {"units": []}, now=1241), [])


class ForumSchedulingTest(unittest.TestCase):
    def test_only_explicitly_closed_far_window_runs_the_same_forum_callback(self):
        for window, safe in [
            ({"open": True, "closes_at": 1200}, False),
            ({"open": False, "opens_at": 1150}, False),
            ({"open": False, "opens_at": 1151}, True),
            ({"open": False, "opens_at": 999}, False),
            ({}, False),
        ]:
            callback = mock.Mock()
            with self.subTest(window=window), mock.patch.object(restock.forum, "maybe_run", callback):
                restock.forum_off_window("state", {"restock": window}, now=1000)
                self.assertEqual(callback.call_count, int(safe))


if __name__ == "__main__":
    unittest.main()
