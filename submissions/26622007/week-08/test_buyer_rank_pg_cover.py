import json
import os
import tempfile
import unittest
from unittest import mock

import buyer_rank_pg as rp

NOW = 1794220000.0


def write_lines(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


class LedgerCoverTest(unittest.TestCase):
    """B-R1 (W-20261109-1925-AXPro주기): compute read ledger_rows.jsonl as a fixed 30 MB tail, so a paid order whose
    settle is older than the tail was unknown: on 11/09 19:4x the tail began at seq 4191008 and 680 of 1,896 confirmed
    payments of the orders window were unknown (payment_unknown 605 of the stats; full read 0, ana/br1_tail.py)."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.md = self.td.name
        os.makedirs(os.path.join(self.md, "measure"))
        # 4 orders in the last hour, paid at ledger seqs 10, 20, 900, 950; 1,000 ledger rows of padding
        orders = [{"ts": NOW - 600, "order": f"o-{i}", "shop": "a", "kind": "goods", "quotes": [{"shop": "a", "ask": 1000}],
                   "asks": [1000], "state": "TASK_STATE_COMPLETED", "price": 900, "passed_to_kit": True,
                   "seconds": {"quotes": 5.0, "end": 15.0}} for i in range(4)]
        write_lines(os.path.join(self.md, "measure", "orders.jsonl"), orders)
        seqs = {"o-0": 10, "o-1": 20, "o-2": 900, "o-3": 950}
        write_lines(os.path.join(self.md, "payments.jsonl"),
                    [{"order": o, "paid": True, "price": 900, "ledger": s} for o, s in seqs.items()])
        by_seq = {s: o for o, s in seqs.items()}
        ledger = [{"k": "settle", "seq": s, "order": by_seq[s], "buyer": "nocha12", "amount": 900} if s in by_seq
                  else {"k": "order", "seq": s, "pad": "x" * 200} for s in range(1, 1001)]
        write_lines(os.path.join(self.md, "ledger_rows.jsonl"), ledger)
        self.size = os.path.getsize(os.path.join(self.md, "ledger_rows.jsonl"))

    def tearDown(self):
        self.td.cleanup()

    def switch(self, **kw):
        with open(os.path.join(self.md, rp.SWITCH), "w") as f:
            json.dump({"enabled": True, **kw}, f)

    def compute(self, tail):
        with mock.patch.object(rp, "TAIL_BYTES", tail):
            return rp.compute(self.md, NOW)

    def test_off_keeps_the_fixed_tail(self):
        self.switch()
        st = self.compute(self.size // 4)               # the tail holds seqs 900 and 950, not 10 and 20
        self.assertEqual(st["counts"]["payment_unknown"], 2)
        self.assertFalse(st["observation"]["ledger_cover"])
        self.assertEqual(st["observation"]["ledger_tail_bytes"], self.size // 4)

    def test_cover_reads_back_to_the_oldest_paid_seq(self):
        self.switch(ledger_cover=True)
        st = self.compute(self.size // 4)
        self.assertEqual(st["counts"]["payment_unknown"], 0)
        obs = st["observation"]
        self.assertTrue(obs["ledger_cover"])
        self.assertLessEqual(obs["inputs"]["ledger"]["first_seq"], 10)
        self.assertEqual(obs["ledger_need_seq"], 10)

    def test_cover_stops_at_the_byte_ceiling(self):
        self.switch(ledger_cover=True)
        with mock.patch.object(rp, "LEDGER_MAX_BYTES", self.size // 3):
            st = self.compute(self.size // 4)
        self.assertEqual(st["counts"]["payment_unknown"], 2)       # still unknown, never guessed
        self.assertLessEqual(st["observation"]["ledger_tail_bytes"], self.size // 3)

    def test_cover_with_no_paid_payment_reads_the_tail_once(self):
        write_lines(os.path.join(self.md, "payments.jsonl"), [{"order": "o-0", "paid": False}])
        self.switch(ledger_cover=True)
        st = self.compute(self.size // 4)
        self.assertEqual(st["observation"]["ledger_tail_bytes"], self.size // 4)
        self.assertIsNone(st["observation"]["ledger_need_seq"])

    def test_cover_streams_the_ledger_once(self):
        # review 2 I-a: no list of the ledger is held; the tail size is found from the first row's seq alone
        need = 10
        rows, n = rp._ledger_cover(os.path.join(self.md, "ledger_rows.jsonl"), need, {})
        self.assertFalse(isinstance(rows, list))
        self.assertEqual(next(iter(rows))["seq"], 1)
        self.assertEqual(rp._first_seq_at(os.path.join(self.md, "ledger_rows.jsonl"), self.size)[0], 1)
        self.assertEqual(rp._first_seq_at(os.path.join(self.md, "nothing.jsonl"), 10), (None, 0))

    def test_the_cache_follows_the_switch_and_rows_carry_the_boundary(self):
        self.switch()
        with mock.patch.object(rp, "TAIL_BYTES", self.size // 4):
            st = rp.stats(self.md, NOW)
            self.assertFalse(st["observation"]["ledger_cover"])
            self.switch(ledger_cover=True)          # within REFRESH: recomputed at once, not after 1800 s
            st = rp.stats(self.md, NOW + 60)
            self.assertTrue(st["observation"]["ledger_cover"])
            self.assertEqual(st["counts"]["payment_unknown"], 0)
            rk = rp.state(self.md, {"order": "o-9", "kind": "goods"}, env={}, now=NOW + 120)
            self.assertIs(rp.metadata(rk).get("ledger_cover"), True)
            self.switch()
            st = rp.stats(self.md, NOW + 180)
            self.assertFalse(st["observation"]["ledger_cover"])
            rk = rp.state(self.md, {"order": "o-9", "kind": "goods"}, env={}, now=NOW + 240)
            self.assertNotIn("ledger_cover", rp.metadata(rk))     # off: the row is as before

    def test_a_broken_compute_is_cached_with_the_switch(self):
        self.switch(ledger_cover=True)
        with mock.patch.object(rp, "compute", side_effect=RuntimeError) as c:
            rp.stats(self.md, NOW)
            rp.stats(self.md, NOW + 60)
        self.assertEqual(c.call_count, 1)                     # not computed again at every order
        with mock.patch.object(rp, "compute", side_effect=RuntimeError):
            rk = rp.state(self.md, {"order": "o-9", "kind": "goods"}, env={}, now=NOW + 120)
        self.assertNotIn("ledger_cover", rp.metadata(rk))    # review 3 m-E: empty stats are not cover stats

    def test_a_non_boolean_cover_is_off(self):
        for v in ("true", 1, None, [True]):
            self.switch(ledger_cover=v)
            st = self.compute(self.size // 4)
            self.assertFalse(st["observation"]["ledger_cover"], v)
            self.assertEqual(st["counts"]["payment_unknown"], 2, v)


if __name__ == "__main__":
    unittest.main()
