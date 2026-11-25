"""The seller's memory write is off the event loop (11/06 16:2x: 0.28 s a rewrite of the 10.5 MB file, on the loop)."""
import os
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import seller          # noqa: E402
import shop_memory     # noqa: E402


class MemoryWriter(unittest.TestCase):
    def test_remember_returns_at_once_and_the_writes_keep_their_order(self):
        written, gate = [], threading.Event()

        def slow_record(entry):
            gate.wait(2)
            written.append((entry["task"], entry["result"]))
        shop = seller.Seller.__new__(seller.Seller)
        deal = {"id": "G1", "name": "김치", "reserve": 1000, "history": [("you asked", 1200)]}
        with mock.patch.object(shop_memory, "record", slow_record), mock.patch.object(seller, "log"):
            t0 = time.time()
            shop.remember("t1", deal, "agreed", 1200)
            deal["history"].append(("buyer offered", 1100))     # the talk goes on: the queued entry keeps what it had
            shop.remember("t1", deal, "deal", 1200)
            shop.remember("t2", deal, "rejected")
            self.assertLess(time.time() - t0, 0.2)
            self.assertEqual(written, [])
            gate.set()
            seller.flush_memory()
        self.assertEqual(written, [("t1", "agreed"), ("t1", "deal"), ("t2", "rejected")])

    def test_a_failing_write_is_logged_not_raised(self):
        logged = []
        shop = seller.Seller.__new__(seller.Seller)

        def boom(entry):
            raise ValueError("bad json")
        with mock.patch.object(seller, "MEMORY_FAILURES", []) as failures, \
                mock.patch.object(shop_memory, "record", boom), mock.patch.object(seller, "log", logged.append):
            shop.remember("t3", {"id": "G1", "name": "x", "reserve": 1, "history": []}, "canceled")
            seller.flush_memory()
            self.assertEqual(failures, [True])
            with self.assertRaisesRegex(RuntimeError, 'drain is not clean'):
                seller.flush_memory(strict=True)
        self.assertTrue(any("memory write failed: ValueError" in x for x in logged))


if __name__ == "__main__":
    unittest.main()
