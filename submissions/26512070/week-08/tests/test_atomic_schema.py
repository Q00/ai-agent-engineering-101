"""core.atomic and core.schema.

    py -m unittest discover -s tests      # from submissions/26512070/week-08
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core import atomic, schema  # noqa: E402


class AtomicTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="harness-test-"))

    def test_write_then_read(self):
        p = self.dir / "a" / "b.json"
        atomic.write_json(p, {"x": 1, "한글": "값"})
        self.assertEqual(atomic.read_json(p), {"x": 1, "한글": "값"})
        self.assertEqual([f.name for f in p.parent.iterdir()], ["b.json"])  # no temp file left

    def test_missing_file_gives_default(self):
        self.assertEqual(atomic.read_json(self.dir / "none.json", default={"d": 1}), {"d": 1})

    def test_broken_json_raises(self):
        p = self.dir / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            atomic.read_json(p)

    def test_append_rejects_newline(self):
        with self.assertRaises(ValueError):
            atomic.append_line(self.dir / "x.jsonl", "a\nb")

    def test_concurrent_appends_do_not_interleave(self):
        """Four processes append 300 long lines each to one file: every line must stay whole."""
        p = self.dir / "shared.jsonl"
        code = ("import sys, json; sys.path.insert(0, sys.argv[1]); from core import atomic\n"
                "for i in range(300):\n"
                "    atomic.append_line(sys.argv[2], json.dumps({'w': sys.argv[3], 'i': i, 'pad': 'x' * 2000}))\n")
        procs = [subprocess.Popen([sys.executable, "-c", code, str(HERE), str(p), str(w)]) for w in range(4)]
        for pr in procs:
            self.assertEqual(pr.wait(timeout=120), 0)
        lines = p.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1200)
        seen = {(json.loads(l)["w"], json.loads(l)["i"]) for l in lines}
        self.assertEqual(len(seen), 1200)


class SchemaTest(unittest.TestCase):
    def event(self, **over):
        e = {"schema_version": 1, "event_id": "seller-1-1-000001", "ts": 1.0, "writer": "seller",
             "source": "seller", "kind": "deal.closed", "strategy_version": 1, "epoch": "e1",
             "card_version": 1, "keys": {"task": "t1", "sku": "G00001"}, "data": {"price": 100}}
        e.update(over)
        return e

    def test_good_event(self):
        self.assertEqual(schema.validate(self.event(), schema.EVENT), [])

    def test_missing_and_extra_keys(self):
        e = self.event(extra=1)
        del e["kind"]
        errs = schema.validate(e, schema.EVENT)
        self.assertTrue(any("missing 'kind'" in x for x in errs), errs)
        self.assertTrue(any("unexpected key 'extra'" in x for x in errs), errs)

    def test_unknown_join_key(self):
        errs = schema.validate(self.event(keys={"shopname": "x"}), schema.EVENT)
        self.assertTrue(errs)

    def test_bool_is_not_an_integer(self):
        self.assertTrue(schema.validate(self.event(strategy_version=True), schema.EVENT))

    def test_strategy_limits_ranges(self):
        good = {"schema_version": 1, "version": 1, "epoch": "e1", "approved": "user", "phase": "exposure",
                "priorities": ["p"], "experiment": {}, "delegation": {},
                "limits": {"cash_floor": 1500000, "max_units_per_sku": 5, "max_stock_share": 0.4,
                           "food_share_max": 0.25,
                           "below_cost": {"allowed": True, "only_in_phase": "exposure",
                                          "max_loss_share_of_cost": 0.1}}}
        self.assertEqual(schema.validate(good, schema.STRATEGY), [])
        bad = json.loads(json.dumps(good))
        bad["limits"]["max_stock_share"] = 2.4   # 11/4: up to 2 (cash turns over), above is refused
        self.assertTrue(schema.validate(bad, schema.STRATEGY))


if __name__ == "__main__":
    unittest.main()
