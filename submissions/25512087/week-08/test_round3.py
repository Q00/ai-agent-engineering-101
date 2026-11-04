"""Round 3 (week 08): seller rules, restock plan and an A2A negotiation against the real server."""
from __future__ import annotations

import json
import random
import socket
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import restock
import seller_r3 as s

UNIT = {"unit": "u1", "sku": "G00001", "name": "포기김치 10kg", "category": "식품>김치", "list_price": 50000,
        "kind": "food", "cost": 30000, "grade": "A", "supplied_at": 1, "expires_at": time.time() + 40 * 3600}


class SellerRuleTests(unittest.TestCase):
    def deal(self, unit=UNIT):
        return {"floor": s.floor_price(unit), "ask": s.opening_ask(unit), "offers": [], "floor_shown": False}

    def test_prices_from_cost_and_list(self):
        self.assertEqual(s.floor_price(UNIT), 31500)                    # cost x 1.05
        self.assertEqual(s.opening_ask(UNIT), 46000)                    # 92 % of list
        cheap_list = dict(UNIT, list_price=30000)
        self.assertEqual(s.opening_ask(cheap_list), 31500, "never under the floor")

    def test_offer_parsing_ignores_sku_and_sizes(self):
        self.assertIsNone(s.parse_offer("I want to buy 포기김치 10kg"))
        self.assertEqual(s.parse_offer("40,000 please"), 40000)
        self.assertEqual(s.parse_offer("ok 38000"), 38000)

    def test_counter_concedes_and_repeat_signs(self):
        d = self.deal()
        self.assertEqual(s.decide(d, 46000, False), ("accept", 46000))
        a, p = s.decide(d, 40000, False)
        self.assertEqual(a, "counter"); self.assertTrue(40000 < p < 46000)
        d["offers"] = [40000]; d["ask"] = p
        self.assertEqual(s.decide(d, 40000, False), ("accept", 40000), "the same offer twice")
        d["offers"] = [35000, 36000]
        self.assertEqual(s.decide(d, 37000, False), ("accept", 37000), "the 3rd offer at or above the floor")

    def test_never_below_floor(self):
        d = self.deal()
        self.assertEqual(s.decide(d, 20000, False), ("counter", 31500))
        d.update(offers=[20000], floor_shown=True, ask=31500)
        self.assertEqual(s.decide(d, 20000, False), ("counter", 31500), "a repeated low offer sees the floor again")
        self.assertEqual(s.decide(dict(d, offers=[20000] * 5), 20000, False), ("end", 31500), "max rounds: end")
        self.assertEqual(s.decide(d, 31500, False), ("accept", 31500))
        self.assertEqual(s.decide(d, None, True), ("accept", 31500), "ok takes the current ask")

    def test_find_unit(self):
        inv = {"round": 3, "units": [dict(UNIT), dict(UNIT, unit="u2", expires_at=UNIT["expires_at"] - 3600),
                                     dict(UNIT, unit="u3", sku="G00002", name="깍두기"), dict(UNIT, unit="u4", grade="expired")]}
        self.assertEqual(s.find_unit("I want to buy 포기김치 10kg (G00001)", inv)["unit"], "u2", "expires first")
        self.assertEqual(s.find_unit("김치 please", inv, {"u2"})["unit"], "u1", "taken units skipped")
        self.assertEqual(s.find_unit("G00002", inv)["unit"], "u3")
        self.assertIsNone(s.find_unit("침대", inv))
        r2 = s.find_unit("I want to buy a chair", {"round": 2})
        self.assertEqual((r2["sku"], s.floor_price(r2), s.opening_ask(r2)), ("chair", 180, 189))


CATALOG = {f"S{i:03d}": {"name": f"item {i}", "category": f"cat{i % 30}", "list_price": 10000 + 1000 * i,
                         "kind": "food" if i % 3 == 0 else "goods"} for i in range(90)}
DEMAND = [{"category": f"cat{c}", "item": f"cat{c}", "kind": "food" if c % 3 == 0 else "goods", "skus": 3,
           "median_price": 10000 + 1000 * c} for c in range(30)]


class RestockTests(unittest.TestCase):
    def inv(self, units=(), balance=5_000_000, rnd=3):
        return {"round": rnd, "balance": balance, "start": 5_000_000, "units": list(units)}

    def test_no_stock_before_round3_or_near_the_end(self):
        self.assertEqual(restock.plan(self.inv(rnd=2), DEMAND, CATALOG, {})[0], [])
        self.assertEqual(restock.plan(self.inv(), DEMAND, CATALOG, {}, now=restock.ROUND_END - 3600)[0], [])

    def test_coverage_first_goods_first_with_limits(self):
        buy, _ = restock.plan(self.inv(), DEMAND, CATALOG, {}, now=1_794_000_000, rng=random.Random(1))
        self.assertEqual(len(buy), restock.LINES_PER_RUN)
        cats = [CATALOG[b["sku"]]["category"] for b in buy]
        self.assertEqual(len(set(cats)), len(cats), "one line per category")
        self.assertTrue(all(CATALOG[b["sku"]]["kind"] == "goods" for b in buy), "cheap goods before food")
        self.assertTrue(all(b["qty"] * CATALOG[b["sku"]]["list_price"] * restock.EST_COST <= 0.05 * 5_000_000 for b in buy))

    def test_cash_floor(self):
        self.assertEqual(restock.plan(self.inv(balance=1_400_000), DEMAND, CATALOG, {}, now=1_794_000_000)[0], [])

    def test_sold_units_are_reordered(self):
        units = [{"unit": "a", "sku": "S001", "category": "cat1", "cost": 7000}]
        _, mem = restock.plan(self.inv(units), DEMAND, CATALOG, {}, now=1_794_000_000)
        buy, mem = restock.plan(self.inv([]), DEMAND, CATALOG, mem, now=1_794_000_060)
        self.assertEqual(mem["sold"], {"S001": 1})
        self.assertEqual(buy[0], {"sku": "S001", "qty": 2}, "sold-out SKU first (11/04: ceil(1.5 x 1 sale) = 2)")

    def test_expired_units_are_not_counted_as_sold(self):
        units = [{"unit": "f", "sku": "S003", "category": "cat3", "cost": 8000, "expires_at": 1_794_000_100}]
        _, mem = restock.plan(self.inv(units), DEMAND, CATALOG, {}, now=1_794_000_000)
        _, mem = restock.plan(self.inv([]), DEMAND, CATALOG, mem, now=1_794_000_200)
        self.assertEqual(mem["sold"], {})

    def test_main_prints_one_json_line(self):
        with TemporaryDirectory() as d:
            md = Path(d)
            (md / "inventory.json").write_text(json.dumps(self.inv()))
            (md / "demand.json").write_text(json.dumps(DEMAND))
            (md / "catalog.json").write_text(json.dumps(CATALOG))
            import io, contextlib, sys
            out = io.StringIO()
            with mock.patch.object(sys, "argv", ["restock.py", "--state", d]), contextlib.redirect_stdout(out):
                restock.main()
            self.assertIn("buy", json.loads(out.getvalue().strip().splitlines()[-1]))
            self.assertTrue((md / "memory" / "restock.json").exists())


class A2ATests(unittest.TestCase):
    """The real seller over A2A v1.0 with our own client (protocol.py), stock from a temporary MARKET_DIR."""

    @classmethod
    def setUpClass(cls):
        import uvicorn
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        cls.tmp = TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        (cls.dir / "inventory.json").write_text(json.dumps({"round": 3, "units": [UNIT]}))
        cls.key = Ed25519PrivateKey.generate()
        cls.patch_dir = mock.patch.object(s, "MARKET_DIR", cls.dir)
        cls.patch_dir.start()
        cls.cwd = Path.cwd()
        import os
        os.chdir(cls.dir)
        Path("shop_key.pem").write_bytes(cls.key.private_bytes(serialization.Encoding.PEM,
                                         serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        sock = socket.socket(); sock.bind(("127.0.0.1", 0)); cls.port = sock.getsockname()[1]; sock.close()
        cls.server = uvicorn.Server(uvicorn.Config(s.build(cls.port, "ggari-shop"), host="127.0.0.1", port=cls.port,
                                                   log_level="warning"))
        threading.Thread(target=cls.server.run, daemon=True).start()
        for _ in range(100):
            if cls.server.started:
                break
            time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        import os
        cls.server.should_exit = True
        os.chdir(cls.cwd)
        cls.patch_dir.stop()
        cls.tmp.cleanup()

    def test_negotiation_to_signed_receipt_with_unit(self):
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from protocol import A2AClient
        import io, contextlib
        c = A2AClient(f"http://127.0.0.1:{self.port}", timeout=10)
        with contextlib.redirect_stdout(io.StringIO()):
            t = c.send("I want to buy 포기김치 10kg (G00001)")
            text = json.dumps(t, ensure_ascii=False)
            self.assertIn("grade A", text); self.assertIn("certificate", text); self.assertIn("46000", text)
            t = c.send("40000", t["id"])
            t = c.send("40000", t["id"])
        self.assertEqual(t["status"]["state"], "TASK_STATE_COMPLETED")
        rec = t["artifacts"][0]["parts"][0]["data"]
        rec["price"] = int(rec["price"])          # A2A data parts carry numbers as doubles (the kit does the same)
        self.assertEqual((rec["item"], rec["price"], rec["unit"], rec["shop"]), ("G00001", 40000, "u1", "ggari-shop"))
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        import base64
        self.key.public_key().verify(base64.b64decode(rec["sig_seller"]), s.canon({k: rec[k] for k in ("shop", "item", "price", "task")}))
        c.close()

    def test_value_ladder_buyer_against_our_seller(self):
        """Fix 1 end to end: our Round 3 buyer (SKU by name, value ladder) against our Round 3 seller."""
        import sys, io, contextlib
        sys.path.insert(0, str(Path(__file__).parent))
        from protocol import A2AClient
        from buyer import BuyerRunner
        from r3_buyer import ValueLadderPolicy, ask_text
        c = A2AClient(f"http://127.0.0.1:{self.port}", timeout=10)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            r = BuyerRunner(c, ValueLadderPolicy(50000)).run(
                "김치", 60000, opening=ask_text({"name": "포기김치 10kg", "sku": "G00001"}), allowed={"G00001"})
        c.close()
        self.assertEqual((r["outcome"], r["price"]), ("deal", 31500), r)       # the floor, not 75 % of the budget
        self.assertEqual(r["offers"], [25000, 31500])


if __name__ == "__main__":
    unittest.main()
