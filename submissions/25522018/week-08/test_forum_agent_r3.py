#!/usr/bin/env python3
"""Offline tests; no Groq calls, no forum requests, no live credits."""
import json
import tempfile
import unittest
from pathlib import Path
from forum_agent_r3 import run_once, make_observation, normalized_payments


def setup(m):
    m.mkdir(parents=True, exist_ok=True)
    (m / "inventory.json").write_text(json.dumps({"round": 3}), encoding="utf-8")
    (m / "catalog.json").write_text(json.dumps({"G001": {"name":"Test item", "category":"Home", "list_price":1000}}), encoding="utf-8")


def payment(m, ledger, paid=True):
    with (m / "payments.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"shop":"https://example.invalid", "paid":paid, "ledger":ledger,
                            "item":"G001", "price":800}) + "\n")

class ForumTests(unittest.TestCase):
    def test_post_has_verified_refs_and_truthful_data(self):
        with tempfile.TemporaryDirectory() as d:
            m = Path(d); setup(m)
            for i in (101,102,103): payment(m,i)
            msg = run_once(m, now=10000)
            self.assertIn("QUEUED",msg)
            row=json.loads((m / "outbox.jsonl").read_text().splitlines()[0])
            self.assertEqual(row["as"], "buyer")
            self.assertEqual(row["refs"], [103,102,101])
            self.assertIn("800", row["post"])
            self.assertIn("3 paid", row["post"])
    def test_no_fabricated_posts_without_paid_deals(self):
        with tempfile.TemporaryDirectory() as d:
            m=Path(d);setup(m)
            for i in (101,102,103): payment(m,i,paid=False)
            self.assertIn("SKIP",run_once(m,now=10000))
            self.assertFalse((m / "outbox.jsonl").exists())
    def test_duplicate_and_cooldown_prevented(self):
        with tempfile.TemporaryDirectory() as d:
            m=Path(d);setup(m)
            for i in (101,102,103): payment(m,i)
            self.assertIn("QUEUED",run_once(m,now=10000))
            self.assertIn("queued",run_once(m,now=10700))
            (m / "outbox.jsonl").unlink()
            self.assertIn("cooldown",run_once(m,now=10500))
            self.assertIn("need at least",run_once(m,now=11000))
    def test_requires_round3(self):
        with tempfile.TemporaryDirectory() as d:
            m=Path(d);setup(m)
            (m / "inventory.json").write_text('{"round":2}')
            for i in (101,102,103): payment(m,i)
            self.assertIn("not Round 3",run_once(m,now=10000))
    def test_only_known_catalog_and_ledger(self):
        catalog={"G001":{"list_price":1000,"category":"Home"}}
        rows=[{"paid":True,"item":"G001","price":700,"ledger":12},
              {"paid":True,"item":"G001","price":700},
              {"paid":True,"item":"G999","price":100,"ledger":13},
              {"paid":False,"item":"G001","price":700,"ledger":14}]
        valid=normalized_payments(rows,catalog)
        self.assertEqual(len(valid),1)
        self.assertIsNone(make_observation(valid,0,None))

if __name__=="__main__":
    unittest.main(verbosity=2)
