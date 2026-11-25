"""A restock run inside an open supply window: the seconds before its lines go out are lines lost when the window closes
(GPT Pro 11/07 07:30 S1). The 08:23 window (11/07) ended with G2607d1f6 held back: the run that began at 08:24:4x printed
its lines at 08:24:54 - three reads of the 143 MB ledger rows and one read of the 14 MB seller memory per new unit."""
import json
import os
import tempfile
import unittest
from unittest import mock

import loss_watch
import market_model
import restock
import shop_memory


class SellerMemoryReadOnceTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "seller_memory_r3.json")
        shop_memory._READ.clear()
        self.write([{"sku": "A", "result": "deal", "price": 10}, {"sku": "B", "result": "deal", "price": 20},
                    {"sku": "A", "result": "no_deal", "price": 5}, {"sku": "A", "result": "deal", "price": 12}])

    def tearDown(self):
        shop_memory._READ.clear()
        self.dir.cleanup()

    def write(self, rows):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"negotiations": rows}, f)

    def test_the_prices_are_the_same(self):
        self.assertEqual(shop_memory.paid_prices("A", memory=self.path), [10, 12])
        self.assertEqual(shop_memory.paid_prices("B", memory=self.path), [20])
        self.assertEqual(shop_memory.paid_prices("C", memory=self.path), [])
        self.assertEqual(shop_memory.paid_prices("A", last=1, memory=self.path), [12])

    def test_one_read_for_many_units_of_an_unchanged_file(self):
        with mock.patch.object(shop_memory.json, "load", wraps=json.load) as load:
            for sku in ["A", "B", "A", "C", "B"] * 8:
                shop_memory.paid_prices(sku, memory=self.path)
        self.assertEqual(load.call_count, 1)

    def test_a_rewritten_file_is_read_again(self):
        self.assertEqual(shop_memory.paid_prices("A", memory=self.path), [10, 12])
        self.write([{"sku": "A", "result": "deal", "price": 99, "pad": "x" * 50}])
        st = os.stat(self.path)
        os.utime(self.path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
        self.assertEqual(shop_memory.paid_prices("A", memory=self.path), [99])

    def test_missing_or_broken_file_is_no_prices(self):
        self.assertEqual(shop_memory.paid_prices("A", memory=os.path.join(self.dir.name, "none.json")), [])
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{broken")
        self.assertEqual(shop_memory.paid_prices("A", memory=self.path), [])


class LedgerRowsReadOnceTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.rows = os.path.join(self.dir.name, market_model.ROWS)
        with open(self.rows, "w", encoding="utf-8") as f:
            f.write(json.dumps({"seq": 1, "k": "settle", "sku": "A", "ts": 1}) + "\n")
        restock._ROWS.clear()

    def tearDown(self):
        restock._ROWS.clear()
        self.dir.cleanup()

    def test_one_read_per_run_of_an_unchanged_file(self):
        with mock.patch.object(market_model, "load_stock_rows", wraps=market_model.load_stock_rows) as load:
            a = restock.rows_of(self.dir.name)
            b = restock.rows_of(self.dir.name)
            restock.read_breaks(self.dir.name, {"A": {"kind": "goods"}})
            with open(os.path.join(self.dir.name, "inventory.json"), "w", encoding="utf-8") as f:
                json.dump({"round": market_model.ECON_ROUND, "units": []}, f)
            loss_watch.check(self.dir.name, root=self.dir.name, sold=[], emit=lambda line: None, rows=restock.rows_of(self.dir.name))
        self.assertEqual(load.call_count, 1)
        self.assertEqual(a, [{"seq": 1, "k": "settle", "sku": "A", "ts": 1}, {"k": "last", "ts": 1}])
        self.assertIs(a, b)

    def test_rows_added_by_a_sync_are_read(self):
        self.assertEqual(len(restock.rows_of(self.dir.name)), 1 + 1)          # and the "last" row
        with open(self.rows, "a", encoding="utf-8") as f:
            f.write(json.dumps({"seq": 2, "k": "settle", "sku": "A", "ts": 2}) + "\n")
        self.assertEqual(len(restock.rows_of(self.dir.name)), 2 + 1)

    def test_read_model_reads_the_rows_after_its_sync(self):
        def sync(md, max_pages=None):
            with open(self.rows, "a", encoding="utf-8") as f:
                f.write(json.dumps({"seq": 3, "k": "settle", "sku": "A", "ts": 3}) + "\n")
            return 1
        restock.rows_of(self.dir.name)          # an earlier read in the same run must not hide the synced row
        seen = []
        with mock.patch.object(market_model, "sync", side_effect=sync), \
                mock.patch.object(market_model, "estimate", side_effect=lambda rows, *a, **k: seen.append(len(rows))
                                  or {"skus": {}, "active": False, "n": 0, "captive": []}):
            restock.read_model(self.dir.name, {}, {})
        self.assertEqual(seen, [2 + 1])


if __name__ == "__main__":
    unittest.main()
