"""core.card, core.strategy and memory.card_section on the real card in card/.

    py -m unittest discover -s tests      # from submissions/26512070/week-08
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core import atomic, card, paths, strategy  # noqa: E402
from tools import query  # noqa: E402,F401
from tools.registry import Ctx, call  # noqa: E402

STRATEGY_V1 = atomic.read_json(HERE / "card" / "strategy_v1.json")


class Base(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.get("HARNESS_ROOT")
        root = Path(tempfile.mkdtemp(prefix="harness-test-"))
        shutil.copytree(HERE / "card", root / "card")
        os.environ["HARNESS_ROOT"] = str(root)

    def tearDown(self):
        if self.old is None:
            os.environ.pop("HARNESS_ROOT", None)
        else:
            os.environ["HARNESS_ROOT"] = self.old


class CardTest(Base):
    def test_sections_and_version(self):
        v, s = card.load()
        self.assertEqual(v, 2)                      # 11/5: core: market rules that cost money
        for k in ("core: the world", "core: the goal", "core: market rules that cost money", "intent", "role: seller", "role: audit", "appendix: rules"):
            self.assertIn(k, s)

    def test_assemble_order_and_frames(self):
        v, text = card.assemble("seller", "executor", priorities=["exposure first"])
        self.assertLess(text.index("# core: the world"), text.index("# your role: seller"))
        self.assertLess(text.index("# your role: seller"), text.index("# intent"))
        self.assertTrue(text.rstrip().endswith("- exposure first"))           # the changing part comes last
        self.assertNotIn("appendix", text)
        _, with_limits = card.assemble("seller", priorities=["exposure first"],
                                       limits=STRATEGY_V1["limits"])
        self.assertIn("cash floor (KRW): 1500000", with_limits)
        self.assertIn("below cost: allowed in phase exposure", with_limits)
        self.assertLess(with_limits.index("# risk limits"), with_limits.index("# current priorities"))
        _, audit = card.assemble("audit", "auditor")
        self.assertIn("claims to verify", audit)
        with self.assertRaises(KeyError):
            card.assemble("cashier")

    def test_card_section_tool(self):
        ctx = Ctx(caller="test")
        self.assertIn("bankrupt", call(ctx, "memory.card_section", {"name": "appendix: rules"})["result"]["text"])
        self.assertFalse(call(ctx, "memory.card_section", {"name": "appendix: nope"})["ok"])

    def test_executor_reads_only_its_own_role(self):
        seller = Ctx(caller="seller")
        self.assertTrue(call(seller, "memory.card_section", {"name": "role: seller"})["ok"])
        self.assertTrue(call(seller, "memory.card_section", {"name": "appendix: rules"})["ok"])
        r = call(seller, "memory.card_section", {"name": "role: buyer"})
        self.assertFalse(r["ok"])
        self.assertIn("own role", r["reason"])
        self.assertTrue(call(Ctx(caller="strategy"), "memory.card_section", {"name": "role: buyer"})["ok"])

    def test_assemble_with_strategy(self):
        _, text = card.assemble("seller", strategy=STRATEGY_V1)
        self.assertNotIn("# your role: buyer", text)
        self.assertIn("phase exposure", text)
        self.assertIn("What the analysts and the strategy agent saw:", text)
        self.assertIn("Hypothesis under test: H1", text)
        order = [text.index(h) for h in ("# your role: seller", "# intent", "# strategy in force",
                                         "# risk limits", "# current priorities")]
        self.assertEqual(order, sorted(order))


class StrategyTest(Base):
    def test_draft_is_refused_then_installed_when_approved(self):
        src = paths.root() / "card" / "strategy_v1.json"
        with self.assertRaises(ValueError):
            strategy.install(src)                                             # still a draft
        s = strategy.install(src, approved="user 2026-11-03 test")
        self.assertEqual(atomic.read_json(paths.memory() / "strategy" / "active.json")["version"], 1)
        self.assertTrue((paths.memory() / "strategy" / "history" / "v1.json").exists())
        with self.assertRaises(ValueError):
            strategy.install(src, approved="again")                           # not newer than v1


if __name__ == "__main__":
    unittest.main()


class ShopCardAndBankruptTest(unittest.TestCase):
    def test_strategy_v3_shop_card_fits_the_schema(self):
        from core import schema
        s = atomic.read_json(HERE / "card" / "strategy_v3.json")
        self.assertEqual(schema.validate(s, schema.STRATEGY), [])
        bad = dict(s, shop_card={"description": "x", "name": "Another Name"})
        self.assertTrue(schema.validate(bad, schema.STRATEGY))       # the name is not the strategy's to change

    def test_restock_files_only_when_stuck(self):
        import restock
        unit = {"unit": "u", "grade": "A"}
        self.assertTrue(restock.stuck({"balance": 400000, "bankrupt_line": 500000, "bankruptcies_left": 2, "units": []}))
        self.assertTrue(restock.stuck({"balance": 400000, "bankrupt_line": 500000, "bankruptcies_left": 1,
                                       "units": [{"unit": "x", "grade": "expired"}]}))
        self.assertFalse(restock.stuck({"balance": 400000, "bankrupt_line": 500000, "bankruptcies_left": 2,
                                        "units": [unit]}))
        self.assertFalse(restock.stuck({"balance": 400000, "bankrupt_line": 500000, "bankruptcies_left": 0, "units": []}))
        self.assertFalse(restock.stuck({"balance": 600000, "bankrupt_line": 500000, "bankruptcies_left": 2, "units": []}))
