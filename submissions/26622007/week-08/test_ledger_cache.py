"""market_buyer reads the ledger rows (141 MB, 0.73 s) and the break stats (0.27 s) once per run, not at each use."""
import json
import threading
import unittest
from unittest import mock

import buyer_stats
import market_buyer
import market_model


class LedgerCache(unittest.TestCase):
    def setUp(self):
        market_buyer._LEDGER.clear()

    def test_rows_and_stats_are_read_once_per_run(self):
        with mock.patch.object(market_buyer.market_model, "load_rows", return_value=[{"seq": 1}]) as rows, \
                mock.patch.object(market_buyer.buyer_stats, "break_stats", return_value={"by_shop": {}}) as stats:
            self.assertEqual(market_buyer.ledger_rows("md"), [{"seq": 1}])
            self.assertEqual(market_buyer.ledger_rows("md"), [{"seq": 1}])
            self.assertEqual(market_buyer.ledger_breaks("md", {}), {"by_shop": {}})
            self.assertEqual(market_buyer.ledger_breaks("md", {}), {"by_shop": {}})
        self.assertEqual(rows.call_count, 1)
        self.assertEqual(stats.call_count, 1)

    def test_threads_at_once_read_it_once(self):
        gate = threading.Event()

        def slow(md):
            gate.wait(1)
            return []
        with mock.patch.object(market_buyer.market_model, "load_rows", side_effect=slow) as rows:
            ts = [threading.Thread(target=market_buyer.ledger_rows, args=("md",)) for _ in range(3)]
            for t in ts:
                t.start()
            gate.set()
            for t in ts:
                t.join()
        self.assertEqual(rows.call_count, 1)

    def test_warm_never_raises(self):
        with mock.patch.object(market_buyer.market_model, "load_rows", side_effect=OSError("gone")):
            t = market_buyer.warm_ledger("md")
            t.join()
        with mock.patch.object(market_buyer.market_model, "load_rows", return_value=[]) as rows:
            market_buyer.ledger_rows("md")      # a failed warm-up leaves the read to the first use
        self.assertEqual(rows.call_count, 1)


class CountingRows(list):
    def __iter__(self):
        self.scans = getattr(self, "scans", 0) + 1
        return super().__iter__()


class BuyerStatsTests(unittest.TestCase):
    def setUp(self):
        self.saved = dict(market_buyer._LEDGER)
        market_buyer._LEDGER.clear()
        self.addCleanup(lambda: (market_buyer._LEDGER.clear(), market_buyer._LEDGER.update(self.saved)))

    def test_break_window_matches_original_contract(self):
        self.assertEqual(buyer_stats.BREAK_WINDOW, 25200)
        self.assertEqual(buyer_stats.BREAK_WINDOW, market_model.BREAK_WINDOW)

    def test_link_and_stats_contract_table(self):
        # These are worked examples, not expectations computed by either implementation.
        # All table entries below are (key, sold, broke), in required insertion order.
        def sale(seq, ts, *, sku="g", buyer="b", order=None, grade="A", shop="s"):
            return dict(k="settle", seq=seq, ts=ts, sku=sku, buyer=buyer,
                        order=order, grade=grade, shop=shop)

        def broken(seq, ts, *, sku="g", buyer="b", order=None):
            return dict(k="broken", seq=seq, ts=ts, sku=sku, buyer=buyer, order=order)

        cases = [
            # An existing order with the wrong SKU must not link or fall back.
            ("03_direct_sku", [sale(1, 100, order="o"),
                               broken(11, 101, sku="h", order="o")],
             [], 1, 0, 0.0, [("g", 1, 0)], [("A", 1, 0)], [("s", 1, 0)], [("g|A", 1, 0)]),
            ("06_future_sale", [sale(1, 101), broken(11, 100)],
             [], 1, 0, 0.0, [("g", 1, 0)], [("A", 1, 0)], [("s", 1, 0)], [("g|A", 1, 0)]),
            ("window_zero_inclusive", [sale(1, 100), broken(11, 100)],
             [(11, 1)], 1, 1, 1.0, [("g", 1, 1)], [("A", 1, 1)], [("s", 1, 1)], [("g|A", 1, 1)]),
            ("window_7h_inclusive", [sale(1, 100), broken(11, 25300)],
             [(11, 1)], 1, 1, 1.0, [("g", 1, 1)], [("A", 1, 1)], [("s", 1, 1)], [("g|A", 1, 1)]),
            ("07_window_7h_plus_one", [sale(1, 100), broken(11, 25301)],
             [], 1, 0, 0.0, [("g", 1, 0)], [("A", 1, 0)], [("s", 1, 0)], [("g|A", 1, 0)]),
            ("10_latest", [sale(1, 100), sale(2, 101, grade="B", shop="t"), broken(11, 102)],
             [(11, 2)], 2, 1, 0.5, [("g", 2, 1)], [("A", 1, 0), ("B", 1, 1)],
             [("s", 1, 0), ("t", 1, 1)], [("g|A", 1, 0), ("g|B", 1, 1)]),
            ("13_used_once", [sale(1, 100), broken(11, 101), broken(12, 102)],
             [(11, 1)], 1, 1, 1.0, [("g", 1, 1)], [("A", 1, 1)], [("s", 1, 1)], [("g|A", 1, 1)]),
            ("16_goods_only", [sale(1, 100, sku="food"), sale(2, 100),
                               broken(11, 101, sku="food"), broken(12, 101)],
             [(11, 1), (12, 2)], 1, 1, 1.0, [("g", 1, 1)], [("A", 1, 1)], [("s", 1, 1)], [("g|A", 1, 1)]),
            ("17_same_buyer", [sale(1, 100), sale(2, 101, buyer="other", grade="B", shop="t"),
                               broken(11, 102)],
             [(11, 1)], 2, 1, 0.5, [("g", 2, 1)], [("A", 1, 1), ("B", 1, 0)],
             [("s", 1, 1), ("t", 1, 0)], [("g|A", 1, 1), ("g|B", 1, 0)]),
            # Direct links are emitted first, even when the fallback row arrived first.
            ("direct_before_fallback", [sale(1, 100, order="one"),
                                        sale(2, 101, order="two", grade="B", shop="t"),
                                        broken(11, 102), broken(12, 103, order="two")],
             [(12, 2), (11, 1)], 2, 2, 1.0, [("g", 2, 2)],
             [("A", 1, 1), ("B", 1, 1)], [("s", 1, 1), ("t", 1, 1)],
             [("g|A", 1, 1), ("g|B", 1, 1)]),
        ]
        catalog = {"g": {"kind": "goods"}, "h": {"kind": "goods"}, "food": {"kind": "food"}}
        for name, rows, pairs, sold, broke, rate, skus, grades, shops, sku_grades in cases:
            with self.subTest(rule=name):
                original_links = market_model.link_broken(rows)
                candidate_links, sales = buyer_stats._scan(rows)
                self.assertEqual([(b["seq"], s["seq"]) for b, s in original_links], pairs)
                self.assertEqual([(b["seq"], s["seq"]) for b, s in candidate_links], pairs)
                self.assertEqual(json.dumps(candidate_links), json.dumps(original_links))
                self.assertEqual(sales, [r for r in rows if r["k"] == "settle"])

                def table(entries):
                    return {key: {"sold": s, "broke": b} for key, s, b in entries}

                expected = dict(sold=sold, broke=broke, rate=rate,
                                by_sku=table(skus), by_grade=table(grades),
                                by_shop=table(shops), by_sku_grade=table(sku_grades))
                original = market_model.break_stats(rows, catalog)
                actual = buyer_stats.break_stats(rows, catalog)
                # JSON comparison protects key insertion order as well as values.
                self.assertEqual(json.dumps(original), json.dumps(expected))
                self.assertEqual(json.dumps(actual), json.dumps(expected))
                self.assertEqual(json.dumps(actual), json.dumps(original))
                with mock.patch.object(market_buyer.market_model, "load_rows", return_value=rows):
                    via_buyer = market_buyer.ledger_breaks("contract-" + name, catalog)
                self.assertEqual(json.dumps(via_buyer), json.dumps(expected))

    def test_all_sales_snapshot_is_classified_once_without_changing_link_priority(self):
        catalog = {"g": {"kind": "goods"}}
        rows = [
            {"k": "settle", "seq": 1, "ts": 10, "buyer": "b", "sku": "g", "order": "one", "grade": "A"},
            {"k": "settle", "seq": 2, "ts": 11, "buyer": "b", "sku": "g", "order": "two", "grade": "B"},
            {"k": "broken", "ts": 12, "buyer": "b", "sku": "g"},
            {"k": "broken", "ts": 13, "buyer": "b", "sku": "g", "order": "two"},
            {"k": "settle", "seq": 3, "ts": 0, "sku": "g", "grade": "C"},
        ]
        expected = market_model.break_stats(rows, catalog)
        counted = CountingRows(rows)
        with mock.patch.object(market_buyer.market_model, "load_rows", return_value=counted):
            actual = market_buyer.ledger_breaks("fixture-market", catalog)
            self.assertEqual(json.dumps(actual), json.dumps(expected))
            self.assertEqual(actual["sold"], 3)
            self.assertEqual(actual["broke"], 2)
            self.assertEqual(counted.scans, 1)
            self.assertIs(actual, market_buyer.ledger_breaks("fixture-market", {}))


if __name__ == "__main__":
    unittest.main()
