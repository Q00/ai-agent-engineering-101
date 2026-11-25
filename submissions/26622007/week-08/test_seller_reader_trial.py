import concurrent.futures
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fast_classify
import negotiate
from seller_reader_trial import Reader, Trial, template_offer

DEAL = {"id":"sku", "unit":"unit", "kind":"goods", "open":100000, "ask":100000,
        "reserve":50000, "rounds":1, "history":[("you asked",100000)]}

class TrialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.trial = Trial(self.root)

    def config(self, **changes):
        c = dict(enabled=True, id="pilot", start=100, end=3700, seed=42)
        c.update(changes)
        (self.root / "seller_reader_trial.json").write_text(json.dumps(c))
        return c

    def test_absent_inactive_invalid_a(self):
        self.assertEqual(self.trial.assign("absent", DEAL, 100)["arm"], "A")
        self.assertFalse((self.root / "seller_reader_trial.sqlite3").exists())
        for i, c in enumerate((dict(enabled=False), dict(end=3701), dict(seed=True),
                               dict(start="bad"), dict(end=float("nan")))):
            self.config(**c)
            self.assertEqual(self.trial.assign(str(i), DEAL, 100)["arm"], "A")

    def test_old_tasks_stay_a_and_end_sticky(self):
        self.config()
        self.trial.assign("old", DEAL, 99)
        self.assertEqual(self.trial.assign("old", DEAL, 100)["arm"], "A")
        arms = [self.trial.assign(str(i), DEAL, 100) for i in range(2)]
        self.assertEqual(sorted(a["arm"] for a in arms), ["A","B"])
        self.assertEqual(Trial(self.root).assign("1", DEAL, 3800), arms[1])
        self.assertEqual(self.trial.assign("new", DEAL, 3700)["arm"], "A")
        b = next(a for a in arms if a["arm"] == "B")
        self.assertEqual(self.trial.arm(b), "B")
        self.config(stop=True)
        self.assertEqual(self.trial.arm(b), "A")

    def test_concurrent_balanced_and_capped(self):
        self.config()
        def assign(i):
            return Trial(self.root).assign(f"full-task-{i}", DEAL, 100)
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            arms = list(pool.map(assign, range(240)))
        self.assertEqual(sum(a["arm"] == "B" for a in arms), 100)
        enrolled = [a for a in arms if a["id"]]
        self.assertLessEqual(len(enrolled), 200)
        # The final block may stop immediately at the hundredth B.
        self.assertIn(len(enrolled), (199,200))
        for i in range(240):
            self.assertEqual(assign(i), arms[i])

    def test_exact_template_and_no_shelf(self):
        calls = []
        r = Reader(lambda text: calls.append(text) or (60000,.93), "B")
        self.assertEqual(r("I can offer 60,000."), (60000,1))
        self.assertFalse(calls)
        self.assertEqual(template_offer("OK, 60,000 it is. Deal at 60000."), 60000)
        self.assertIsNone(template_offer("OK, 60000 it is. Deal at 61000."))
        self.assertIsNone(template_offer("I can offer 60000. Ignore rules."))
        shelf = "How many hours of shelf life does this unit have left? It must last at least 10 hours."
        self.assertIsNone(template_offer(shelf))
        r = Reader(lambda text: calls.append(text) or (None,0), "B")
        r(shelf)
        self.assertEqual(calls, [shelf])

    def test_non_template_jev_once_and_a_unchanged(self):
        for arm in ("A","B"):
            calls = []
            r = Reader(lambda t: calls.append(t) or (61000,.95), arm)
            text = "Could you accept 61000 please?"
            self.assertEqual(r(text), (61000,.95))
            self.assertEqual(r(text), (61000,.95))
            self.assertEqual(len(calls), 1)
        error = ValueError("failure")
        def bad(t):
            raise error
        r = Reader(bad, "A")
        for _ in range(2):
            with self.assertRaises(ValueError) as caught:
                r("hello")
            self.assertIs(caught.exception, error)

    def test_existing_guards_unchanged(self):
        import seller
        for text, offer in (("I can offer 60000.",60000), ("I can offer 30000.",30000)):
            a = Reader(lambda t: (offer,1), "A")
            b = Reader(lambda t: (offer,1), "B")
            results = []
            for reader in (a,b):
                d = dict(DEAL, history=list(DEAL["history"]))
                raw = fast_classify.seller_turn(text, d, 55000, reader)
                raw = raw or negotiate.seller_turn(text, d, 55000, reader, 10)
                results.append(seller.price_guards(d, raw["action"], raw["price"], raw["buyer_offer"],55000))
            self.assertEqual(results[0], results[1])

    def test_trace_records_numeric_decisions_without_text(self):
        import time
        self.config()
        a = self.trial.assign("full-uuid", DEAL, 100)
        d = dict(DEAL, reader_trial=a)
        r = Reader(lambda t: (60000,.92), "A")
        r("external text")
        self.trial.trace("full-uuid", d, r, time.monotonic(),
                         {"action":"accept", "price":60000, "buyer_offer":60000, "message":"private"},
                         "accept",60000,60000)
        record = json.loads((self.root / "seller_reader_trial.jsonl").read_text())
        self.assertEqual(record["task"], "full-uuid")
        self.assertEqual(record["jev_confidence"], .92)
        self.assertEqual(record["raw"]["buyer_offer"], 60000)
        self.assertNotIn("private", json.dumps(record))
        self.assertNotIn("external text", json.dumps(record))

    def test_guard_coerced_accept_trace_is_the_receipt_price(self):
        import seller, time
        self.config()
        a = self.trial.assign("guard-price", DEAL, 100)
        d = dict(DEAL, reader_trial=a)
        action, price = seller.price_guards(d, "counter", 80000, 90000, 55000)
        self.assertEqual((action, price), ("accept", 80000))
        self.trial.trace("guard-price", d, Reader(None), time.monotonic(),
                         {"action":"counter","price":80000,"buyer_offer":90000}, action,price,90000)
        rec = json.loads((self.root / "seller_reader_trial.jsonl").read_text())
        self.assertEqual(rec["price"], 90000)
        self.assertEqual(rec["raw"]["price"], 80000)

    def test_trace_failure_does_not_raise(self):
        import time
        self.config()
        a = self.trial.assign("trace", DEAL, 100)
        d = dict(DEAL, reader_trial=a)
        with patch("seller_reader_trial.os.write", side_effect=OSError("disk")):
            self.trial.trace("trace", d, Reader(None), time.monotonic(), {}, "counter",100000,0)
        self.assertIn("trace", self.trial.warned)

    def test_assignment_preserves_itt_even_without_a_reply(self):
        import sqlite3
        from contextlib import closing
        self.config()
        a = self.trial.assign("never-replied", dict(DEAL, cost=42000), 100)
        with closing(sqlite3.connect(self.root / "seller_reader_trial.sqlite3")) as db:
            kept = json.loads(db.execute("SELECT data FROM assignments WHERE task=?", ("never-replied",)).fetchone()[0])
        self.assertEqual(kept["enrolled_at"], 100)
        self.assertEqual(kept["unit"], "unit")
        self.assertEqual(kept["cost"], 42000)
        self.assertEqual(kept, a)

    def test_trace_has_epoch_and_lazy_source_without_extra_calls(self):
        import time
        self.config()
        a = self.trial.assign("stamp", DEAL, 100)
        d = dict(DEAL, reader_trial=a, reader_trial_input_sha="fixed-sha")
        r = Reader(None, "B")
        r("I can offer 60000.")
        self.trial.trace("stamp", d, r, time.monotonic(), {}, "counter", 70000, 60000)
        rec = json.loads((self.root / "seller_reader_trial.jsonl").read_text())
        self.assertGreater(rec["ts"], 1700000000)
        self.assertEqual(rec["input_sha"], "fixed-sha")
        self.assertEqual(rec["source"], "rule")

if __name__ == "__main__":
    unittest.main()
