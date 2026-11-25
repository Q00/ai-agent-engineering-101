"""buyer_post_market.py: arm B purchase records name the market's median paid share of list for the same SKU x grade x
warranty, under a finite switch window, and every record of the study is measured for the per-arm forum verdict."""
import json
import math
import os
import tempfile
import unittest

import buyer_post
import buyer_post_market

NOW = 1800000000.0
# sha256("t-salt:<ledger>") first byte: ledger 100 is odd (arm B), 101 even (arm A)
ARM_B, ARM_A = 100, 101


def deal(ledger, sku="G1", price=7000, value=10000, ago=60, **kw):
    return {"time": NOW - ago, "order": f"o-{ledger}", "price": price, "shop": "https://s.example", "sku": sku,
            "name": "충전기", "grade": "B", "claim": "B", "claim_honest": True, "expires_at": None,
            "warranty_hours": 24, "value": value, "ledger": ledger, "kind": "goods", **kw}


def settle(seq, amount, sku="G1", grade="B", warranty=24, ago=600, honest=True):
    return {"k": "settle", "seq": seq, "ts": NOW - ago, "buyer": "x", "order": f"m-{seq}", "sku": sku, "amount": amount,
            "grade": grade, "honest": honest, "shop": "s", "warranty": warranty, "unit": f"u-{seq}"}


FIVE = [settle(1, 6000), settle(2, 6500), settle(3, 7000), settle(4, 7500), settle(5, 8000)]


class MarketNote(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.md = os.path.join(self.tmp.name, ".market")
        os.makedirs(os.path.join(self.md, "measure"))
        self.settled = os.path.join(self.tmp.name, "settled.jsonl")
        self.memory = os.path.join(self.tmp.name, "mem.json")
        with open(self.memory, "w") as f:
            json.dump({"trades": []}, f)

    def tearDown(self):
        self.tmp.cleanup()

    def switch(self, **kw):
        sw = {"enabled": True, "study": "post-market-test", "salt": "t-salt", "start": NOW - 3600, "end": NOW + 3600}
        sw.update(kw)
        with open(os.path.join(self.md, buyer_post_market.SWITCH), "w") as f:
            json.dump(sw, f)

    def ledger(self, rows):
        with open(os.path.join(self.md, buyer_post.LEDGER), "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    def write_settled(self, rows):
        with open(self.settled, "w") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def measured(self):
        path = os.path.join(self.md, "measure", buyer_post_market.MEASURE)
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return [json.loads(x) for x in f]

    def post(self, d, now=NOW, rows=FIVE):
        self.ledger(rows)
        self.write_settled([d])
        return buyer_post.maybe_post(self.md, self.settled, self.memory, now=now)

    def test_off_without_a_switch_leaves_the_post_and_measures_nothing(self):
        line = self.post(deal(ARM_B))
        self.assertNotIn("시장 중앙값", line["post"])
        self.assertTrue(line["post"].endswith(f"ledger #{ARM_B}"))
        self.assertEqual(self.measured(), [])

    def test_arm_b_names_the_median_share_before_the_ledger_ref(self):
        self.switch()
        line = self.post(deal(ARM_B))
        self.assertIn(" | 시장 중앙값 7,000원(정가의 70%, 같은 등급·보증 6h 이상 최근 3시간 5건) | ledger #100", line["post"])
        self.assertEqual(line["refs"], [ARM_B])
        self.assertEqual(set(line), {"post", "refs", "as"})
        [m] = self.measured()
        self.assertEqual((m["study"], m["ledger"], m["arm"], m["eligible"], m["applied"], m["n"], m["median_share"]),
                         ("post-market-test", ARM_B, "B", True, True, 5, 0.7))
        self.assertEqual(m["ts"], NOW)

    def test_arm_a_keeps_the_post_and_is_measured_as_eligible(self):
        self.switch()
        line = self.post(deal(ARM_A))
        self.assertNotIn("시장 중앙값", line["post"])
        [m] = self.measured()
        self.assertEqual((m["arm"], m["eligible"], m["applied"], m["n"], m["median_share"]), ("A", True, False, 5, 0.7))

    def test_food_says_same_grade_only(self):
        self.switch()
        rows = [settle(i, a, warranty=None) for i, a in enumerate([6000, 6500, 7000, 7500, 8000], 1)]
        line = self.post(deal(ARM_B, kind="food", warranty_hours=None, expires_at=NOW + 50 * 3600), rows=rows)
        self.assertIn("시장 중앙값 7,000원(정가의 70%, 같은 등급 최근 3시간 5건)", line["post"])

    def test_fewer_than_five_matching_settles_add_nothing(self):
        self.switch()
        line = self.post(deal(ARM_B), rows=FIVE[:4])
        self.assertNotIn("시장 중앙값", line["post"])
        [m] = self.measured()
        self.assertEqual((m["arm"], m["eligible"], m["applied"], m["n"], m["median_share"]), ("B", False, False, 4, None))

    def test_only_other_deals_of_the_same_condition_fresh_and_honest_count(self):
        self.switch()
        noise = [settle(ARM_B, 100), settle(11, 100, grade="A"), settle(12, 100, warranty=2),
                 settle(13, 100, sku="G2"), settle(14, 100, honest=False), settle(15, 100, ago=3 * 3600 + 60),
                 settle(16, 100, ago=-60)]
        line = self.post(deal(ARM_B), rows=FIVE + noise)
        self.assertIn("시장 중앙값 7,000원(정가의 70%, 같은 등급·보증 6h 이상 최근 3시간 5건)", line["post"])

    def test_short_warranty_and_no_grade_say_so(self):
        self.switch()
        rows = [settle(i, a, grade=None, warranty=2) for i, a in enumerate([6000, 6500, 7000, 7500, 8000], 1)]
        line = self.post(deal(ARM_B, grade=None, claim=None, warranty_hours=2), rows=rows)
        self.assertIn("시장 중앙값 7,000원(정가의 70%, 보증 6h 미만·없음 최근 3시간 5건)", line["post"])

    def test_non_finite_amounts_are_left_out_and_both_arms_are_measured(self):
        self.switch()
        rows = FIVE + [settle(20, math.inf), settle(21, math.inf), settle(22, math.inf)]
        line = self.post(deal(ARM_B), rows=rows)
        self.assertIn("시장 중앙값 7,000원", line["post"])
        [m] = self.measured()
        self.assertEqual((m["arm"], m["n"], m["median_share"]), ("B", 5, 0.7))

    def test_arm_a_without_enough_settles_is_measured_not_eligible(self):
        self.switch()
        self.post(deal(ARM_A), rows=FIVE[:2])
        [m] = self.measured()
        self.assertEqual((m["arm"], m["eligible"], m["applied"], m["n"]), ("A", False, False, 2))

    def test_no_row_while_a_buyer_post_waits(self):
        self.switch()
        with open(os.path.join(self.md, "outbox.jsonl"), "w") as f:
            f.write(json.dumps({"post": "x", "as": "buyer"}) + "\n")
        self.assertIsNone(self.post(deal(ARM_B)))
        self.assertEqual(self.measured(), [])

    def test_the_window_is_finite_and_the_boundary_is_off(self):
        for sw, now in (({}, NOW + 3600), ({}, NOW - 3601), ({"end": math.inf}, NOW), ({"end": None}, NOW), ({"end": math.nan}, NOW),
                        ({"start": True}, NOW), ({"start": -math.inf}, NOW),
                        ({"enabled": "yes"}, NOW), ({"salt": ""}, NOW), ({"study": None}, NOW)):
            with self.subTest(sw=sw, now=now):
                self.switch(**{k: v for k, v in sw.items()})
                self.assertIsNone(buyer_post_market.config(self.md, env={}, now=now))
        self.switch()
        self.assertIsNotNone(buyer_post_market.config(self.md, env={}, now=NOW))
        self.assertIsNotNone(buyer_post_market.config(self.md, env={}, now=NOW - 3600))   # start is on
        self.assertIsNone(buyer_post_market.config(self.md, env={"BUYER_POST_MARKET": "0"}, now=NOW))

    def test_past_the_end_the_post_is_the_old_one(self):
        self.switch(end=NOW)
        line = self.post(deal(ARM_B))
        self.assertNotIn("시장 중앙값", line["post"])
        self.assertEqual(self.measured(), [])

    def test_a_trend_post_is_never_noted_or_measured(self):
        self.switch()
        rows = [settle(i, 7000, sku="G9", ago=60 * i) for i in range(1, 13)]
        with open(os.path.join(self.md, "catalog.json"), "w") as f:
            json.dump({"G9": {"list_price": 10000, "name": "케이블", "kind": "goods"}}, f)
        line = self.post(deal(ARM_B), rows=rows)
        self.assertTrue(line["post"].startswith("[가격 동향]"))
        self.assertEqual(self.measured(), [])

    def test_a_broken_measure_file_never_stops_the_post(self):
        self.switch()
        os.rmdir(os.path.join(self.md, "measure"))
        with open(os.path.join(self.md, "measure"), "w") as f:
            f.write("not a dir")
        line = self.post(deal(ARM_B))
        self.assertIn("시장 중앙값", line["post"])
        self.assertEqual(len(buyer_post.read_lines(os.path.join(self.md, "outbox.jsonl"))), 1)

    def test_a_missing_measure_dir_is_made(self):
        self.switch()
        os.rmdir(os.path.join(self.md, "measure"))
        self.post(deal(ARM_B))
        self.assertEqual(len(self.measured()), 1)

    def test_a_failing_note_leaves_the_plain_post(self):
        self.switch()
        orig = buyer_post_market.median_share
        buyer_post_market.median_share = lambda *a, **k: 1 / 0
        try:
            line = self.post(deal(ARM_B))
        finally:
            buyer_post_market.median_share = orig
        self.assertNotIn("시장 중앙값", line["post"])
        self.assertTrue(line["post"].endswith(f"ledger #{ARM_B}"))
        [m] = self.measured()
        self.assertEqual((m["arm"], m["eligible"], m["applied"], m["n"]), ("B", False, False, 0))


if __name__ == "__main__":
    unittest.main()
