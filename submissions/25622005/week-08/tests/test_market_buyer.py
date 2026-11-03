"""market_buyer end to end: fixture .market files, a fake A2A shop on 127.0.0.1, the hook in a subprocess.
The hook must print exactly one line on stdout whatever happens."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

W = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_negotiation import FakeShop, WordsShop  # noqa: E402

CATALOG = {"F1": {"sku": "F1", "name": "김치 A", "category": "식품>김치", "list_price": 30000, "kind": "food",
                  "shelf_hours": 720},
           "F2": {"sku": "F2", "name": "김치 B", "category": "식품>김치", "list_price": 40000, "kind": "food",
                  "shelf_hours": 720}}
ORDER = {"order": "o-77", "item": "김치", "category": "식품>김치", "budget": 41234, "acceptable": ["F1", "F2"],
         "kind": "food", "use_within_hours": 10, "expires": 0, "round": 3, "market_pubkey": "x"}


class MarketBuyerHookTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="r3-mb-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / "strategy").mkdir()
        shutil.copy(W / "strategy" / "SKILL.md", self.home / "strategy" / "SKILL.md")
        self.md = self.home / ".market"
        self.md.mkdir()
        (self.md / "catalog.json").write_text(json.dumps(CATALOG, ensure_ascii=False), encoding="utf-8")

    def write(self, order, shops):
        (self.md / "order.json").write_text(json.dumps(order, ensure_ascii=False), encoding="utf-8")
        (self.md / "shops.json").write_text(json.dumps(shops, ensure_ascii=False), encoding="utf-8")

    def run_hook(self, *args, **env):
        args = args or ("--shops", str(self.md / "shops.json"), "--item", "김치", "--budget", "41234",
                        "--order", str(self.md / "order.json"))
        env = dict(os.environ, R3_HOME=str(self.home), **env)
        p = subprocess.run([sys.executable, "-B", str(W / "market_buyer.py"), *args], cwd=self.home, env=env,
                           capture_output=True, text=True, timeout=90)
        lines = p.stdout.splitlines()
        self.assertEqual(len(lines), 1, p.stdout + p.stderr)
        return json.loads(lines[0])

    def rows(self, role="buyer"):
        path = self.home / "logs" / "decisions" / f"{role}.jsonl"
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def shop_entry(self, shop, *skus):
        return {"name": "fake", "url": shop.url, "reviews": [],
                "offers": [{"sku": s, "name": CATALOG[s]["name"], "qty": 1} for s in skus]}

    def test_deal_line_with_accept_and_decision_row(self):
        shop = FakeShop(sku="F2", first_ask=36000, floor=99999, unit="u-9", list_price=40000,
                        data={"hours_left": 300.0})
        self.addCleanup(shop.close)
        self.write(ORDER, [self.shop_entry(shop, "F2")])
        out = self.run_hook()
        self.assertEqual(out["shop"], shop.url)
        self.assertEqual(out["receipt"]["price"], 36000)
        self.assertEqual(out["receipt"]["unit"], "u-9")
        self.assertEqual(out["accept"], {"cert": True, "min_hours_left": 11.0,
                                         "bind": {"max_price_list": 0.95, "sku": "F2", "unit": "u-9"}})
        self.assertIn(out["stars"], range(1, 6))
        self.assertNotIn("41234", json.dumps(shop.messages))
        row = self.rows()[-1]
        self.assertEqual((row["kind"], row["ref"]["order"], row["choice"]["price"]), ("order", "o-77", 36000))
        self.assertIn(f"shop:{shop.url}", row["memory_refs"])
        self.assertTrue(row["p_trace"][shop.url])
        self.assertEqual(row["inputs"]["use_within_hours"], 10)
        self.assertNotIn("+lastgood", row["policy_ver"])

    def test_second_shop_after_the_first_refuses(self):
        stubborn = FakeShop(sku="F2", first_ask=39000, floor=39000, list_price=40000)
        good = FakeShop(sku="F1", first_ask=27000, floor=99999, list_price=30000)
        for s in (stubborn, good):
            self.addCleanup(s.close)
        self.write(ORDER, [self.shop_entry(stubborn, "F2"), self.shop_entry(good, "F1")])
        out = self.run_hook()
        self.assertEqual(out["shop"], good.url)
        self.assertEqual(stubborn.canceled, ["t1"])
        tried = self.rows()[-1]["tried"]
        self.assertEqual([t["shop"] for t in tried], [stubborn.url, good.url])

    def test_model_reads_a_quote_in_words(self):
        """D9 through EXCEPTION_HARNESS_CMD: a fake model command reads the price the rules cannot."""
        fake = self.home / "fake_model.py"
        fake.write_text("import sys; sys.stdin.read(); "
                        "print('Sure: {\"kind\": \"quote\", \"price\": 36000, \"sku\": \"F2\", \"unit\": null}')")
        shop = WordsShop(words="Thirty-six thousand won for this one.", sku="F2", first_ask=36000, floor=99999,
                         list_price=40000)
        self.addCleanup(shop.close)
        self.write(ORDER, [self.shop_entry(shop, "F2")])
        out = self.run_hook(EXCEPTION_HARNESS_CMD=f"{sys.executable} {fake} --model fake-small")
        self.assertEqual(out["receipt"]["price"], 36000)
        llm, order = self.rows()
        self.assertEqual((llm["kind"], llm["why"], llm["model"], llm["choice"]["price"]),
                         ("llm_exception", "ok", "fake-small", 36000))
        self.assertEqual(llm["policy_ver"], order["policy_ver"])
        self.assertEqual(order["tried"][0]["llm_reads"], 1)
        self.assertNotIn("41234", llm["inputs"]["text"] + json.dumps(llm["inputs"]))

    def test_round_2_is_no_deal(self):
        self.write(dict(ORDER, round=2), [])
        self.assertEqual(self.run_hook(), {"shop": "", "receipt": None})
        self.assertEqual(self.rows()[-1]["choice"], "no_deal")

    def test_no_offers_is_no_deal(self):
        self.write(ORDER, [{"name": "x", "url": "http://127.0.0.1:9", "reviews": [], "offers": []}])
        self.assertEqual(self.run_hook(), {"shop": "", "receipt": None})

    def test_unreachable_shop_is_no_deal(self):
        self.write(ORDER, [{"name": "x", "url": "http://127.0.0.1:9", "reviews": [],
                            "offers": [{"sku": "F1", "name": "김치 A", "qty": 1}]}])
        out = self.run_hook()
        self.assertEqual(out, {"shop": "", "receipt": None})
        self.assertEqual(self.rows()[-1]["tried"][0]["state"], "error")

    def test_broken_inputs_still_print_one_line(self):
        self.assertEqual(self.run_hook("--shops", "nope.json", "--item", "x", "--budget", "1",
                                       "--order", "nope.json"), {"shop": "", "receipt": None})
        self.assertEqual(self.run_hook("--budget", "not-a-number"), {"shop": "", "receipt": None})
        (self.md / "order.json").write_text("{half", encoding="utf-8")
        self.assertEqual(self.run_hook(), {"shop": "", "receipt": None})


if __name__ == "__main__":
    unittest.main()
