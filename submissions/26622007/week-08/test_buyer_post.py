"""buyer_post.py: one code-written buyer post per settled deal, paced, and never in the way of the review hook."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

import buyer_post

NOW = 1800000000.0
HERE = os.path.dirname(os.path.abspath(__file__))


def deal(ledger, sku="G1", price=7000, value=10000, ago=60, **kw):
    return {"time": NOW - ago, "order": f"o-{ledger}", "price": price, "shop": "https://s.example", "sku": sku,
            "name": "생수 500mL", "grade": "B", "claim": "B", "claim_honest": True, "expires_at": NOW + 50 * 3600,
            "warranty_hours": None, "value": value, "ledger": ledger, "kind": "food", **kw}


class BuyerPost(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.md = os.path.join(self.tmp.name, ".market")
        os.makedirs(self.md)
        self.settled = os.path.join(self.tmp.name, "settled.jsonl")
        self.memory = os.path.join(self.tmp.name, "mem.json")
        with open(self.memory, "w") as f:
            json.dump({"trades": [{"order": "o-11", "shop": "yms979"}]}, f)

    def tearDown(self):
        self.tmp.cleanup()

    def write_settled(self, rows):
        with open(self.settled, "w") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def outbox(self):
        path = os.path.join(self.md, "outbox.jsonl")
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return [json.loads(x) for x in f]

    def post(self, now=NOW):
        return buyer_post.maybe_post(self.md, self.settled, self.memory, now=now)

    def test_posts_the_newest_deal_with_its_ledger_ref(self):
        self.write_settled([deal(10), deal(11)])
        line = self.post()
        self.assertEqual(line["refs"], [11])
        self.assertEqual(line["as"], "buyer")
        self.assertIn("7,000원, 정가 10,000원 대비 30% 저렴", line["post"])
        self.assertIn("판매: yms979", line["post"])
        self.assertIn("남은 유통기한 50시간", line["post"])
        self.assertEqual(self.outbox(), [line])

    def test_paced_and_each_deal_once(self):
        self.write_settled([deal(10), deal(11)])
        self.post()
        os.remove(os.path.join(self.md, "outbox.jsonl"))     # the kit sent it
        self.assertIsNone(self.post(now=NOW + 60))           # within GAP_MINUTES
        second = self.post(now=NOW + buyer_post.GAP_MINUTES * 60 + 1)
        self.assertEqual(second["refs"], [10])
        os.remove(os.path.join(self.md, "outbox.jsonl"))
        self.assertIsNone(self.post(now=NOW + 2 * buyer_post.GAP_MINUTES * 60 + 2))   # nothing new left

    def test_waits_while_a_buyer_post_is_queued(self):
        self.write_settled([deal(10)])
        with open(os.path.join(self.md, "outbox.jsonl"), "w") as f:
            f.write(json.dumps({"post": "luna", "refs": [1], "as": "buyer"}) + "\n")
        self.assertIsNone(self.post())
        with open(os.path.join(self.md, "outbox.jsonl"), "w") as f:      # a queued vote or shop post does not block
            f.write(json.dumps({"vote": 3, "value": 1, "as": "buyer"}) + "\n")
            f.write(json.dumps({"post": "shop", "refs": [1], "as": "shop"}) + "\n")
        self.assertEqual(self.post()["refs"], [10])

    def test_skips_my_shop_skus_overpaid_and_stale_deals(self):
        with open(os.path.join(self.md, "inventory.json"), "w") as f:
            json.dump({"units": [{"sku": "MINE"}]}, f)
        self.write_settled([deal(10, sku="MINE"), deal(11, price=12000), deal(12, ago=4 * 3600), deal(13, value=0)])
        self.assertIsNone(self.post())
        self.assertEqual(self.outbox(), [])

    def test_goods_show_the_warranty(self):
        self.write_settled([deal(20, kind="goods", warranty_hours=6.0, expires_at=None, price=900000, value=1800000)])
        line = self.post()
        self.assertIn("보증 6h", line["post"])
        self.assertIn("900,000원, 정가 1,800,000원 대비 50% 저렴", line["post"])

    def test_a_bad_line_or_field_skips_only_that_deal(self):
        self.write_settled([deal(10)])
        with open(self.settled, "a") as f:
            f.write('{"half written\n')
            f.write(json.dumps(deal(11, kind="goods", expires_at=None, warranty_hours="six")) + "\n")
        self.assertEqual(self.post()["refs"], [10])

    def test_restock_list_skus_and_a_post_being_sent_block(self):
        pricing = os.path.join(self.tmp.name, "pricing.json")
        with open(pricing, "w") as f:
            json.dump({"restock": {"priority_skus": ["P1"], "sku_target_units": {"T1": 2}}}, f)
        self.assertEqual(buyer_post.my_shop_skus(self.md, pricing), {"P1", "T1"})
        self.write_settled([deal(10)])
        with open(os.path.join(self.md, "outbox.taking"), "w") as f:
            f.write(json.dumps({"post": "x", "refs": [1], "as": "buyer"}) + "\n")
        self.assertIsNone(self.post())

    def test_bad_files_never_raise(self):
        with open(self.settled, "w") as f:
            f.write("not json\n")
        self.assertIsNone(self.post())
        self.assertIsNone(buyer_post.maybe_post("/nonexistent/dir", self.settled, self.memory, now=NOW))

    def test_review_hook_prints_its_result_last_and_queues_the_post(self):
        deal_path = os.path.join(self.md, "deal.json")
        with open(deal_path, "w") as f:
            json.dump({"order": {"order": "o-11", "kind": "food", "use_within_hours": 5},
                       "receipt": {"price": 7000},
                       "settled": {k: v for k, v in deal(11).items() if k not in ("time", "order", "price", "kind")}}, f)
        env = dict(os.environ, BUYER_SETTLED=self.settled, BUYER_MEMORY=self.memory)
        r = subprocess.run([sys.executable, os.path.join(HERE, "review.py"), "--deal", deal_path], capture_output=True,
                           text=True, env=env, cwd=self.tmp.name, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1])["stars"], 5)
        self.assertEqual(self.outbox()[0]["refs"], [11])


class TrendPost(unittest.TestCase):
    """[가격 동향]: the market's settles of one SKU x grade x warranty, alternating with the purchase records."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.md = os.path.join(self.tmp.name, ".market")
        os.makedirs(self.md)
        with open(os.path.join(self.md, "catalog.json"), "w") as f:
            json.dump({"G9": {"sku": "G9", "name": "소파", "list_price": 400000, "kind": "goods"},
                       "G8": {"sku": "G8", "name": "생수", "list_price": 10000, "kind": "food"}}, f)
        rows, seq = [], 100
        for i in range(12):                           # last 3 hours: 12 x G9 A with a 24h warranty
            seq += 1
            rows.append({"k": "settle", "seq": seq, "ts": NOW - 600 - i * 60, "sku": "G9", "amount": 300000 + i * 1000,
                         "grade": "A", "warranty": 24.0, "shop": "s", "buyer": "b"})
        for i in range(4):                            # the 3 hours before
            seq += 1
            rows.append({"k": "settle", "seq": seq, "ts": NOW - 4 * 3600 - i, "sku": "G9", "amount": 290000,
                         "grade": "A", "warranty": 24.0})
        for i in range(9):                            # same SKU without a warranty: another group, too small
            seq += 1
            rows.append({"k": "settle", "seq": seq, "ts": NOW - 700, "sku": "G9", "amount": 200000, "grade": "A", "warranty": None})
        for i in range(10):
            seq += 1
            rows.append({"k": "settle", "seq": seq, "ts": NOW - 900, "sku": "G8", "amount": 7000, "grade": "B", "warranty": None})
        rows.append({"k": "settle", "seq": seq + 1, "ts": NOW - 60, "sku": "G9", "amount": 1, "grade": "A", "warranty": 24.0,
                     "honest": False})               # a false grade claim is not in the statistics
        with open(os.path.join(self.md, buyer_post.LEDGER), "w") as f:
            f.write("{broken\n")
            for r in rows + rows[:1]:                 # a settle written twice counts once
                f.write(json.dumps(r) + "\n")
        self.settled = os.path.join(self.tmp.name, "settled.jsonl")
        with open(self.settled, "w") as f:
            f.write(json.dumps(deal(11)) + "\n" + json.dumps(deal(12, ago=30)) + "\n")
        self.memory = os.path.join(self.tmp.name, "mem.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_trend_counts_the_biggest_group_against_the_hours_before(self):
        sku, text, refs = buyer_post.trend(self.md, NOW, set(), set())
        self.assertEqual(sku, "G9")
        self.assertIn("[가격 동향] 소파 (G9) 등급 A·보증 6h 이상", text)
        self.assertIn("12건 정산, 결제액 중앙값 305,500원 (사분위 302,750–308,250원, 정가 400,000원의 76%)", text)
        self.assertIn("직전 3시간 4건, 중앙값 290,000원 대비 +5.3%", text)
        self.assertEqual(refs, [101, 112])
        self.assertIn("원장 #101–#112", text)
        # my shop's SKU and the last trend posts are left out; food has no warranty term
        sku, text, _ = buyer_post.trend(self.md, NOW, {"G9"}, set())
        self.assertEqual(sku, "G8")
        self.assertIn("(G8) 등급 B |", text)
        self.assertIn("직전 3시간 정산 없음", text)
        self.assertIsNone(buyer_post.trend(self.md, NOW, set(), {"G9", "G8"}))

    def test_kinds_alternate_and_share_one_slot(self):
        gap = buyer_post.GAP_MINUTES * 60 + 1
        first = buyer_post.maybe_post(self.md, self.settled, self.memory, now=NOW)
        self.assertTrue(first["post"].startswith("[가격 동향]"))
        self.assertEqual(first["refs"], [101, 112])
        os.remove(os.path.join(self.md, "outbox.jsonl"))          # the kit sent it
        self.assertIsNone(buyer_post.maybe_post(self.md, self.settled, self.memory, now=NOW + 60))
        second = buyer_post.maybe_post(self.md, self.settled, self.memory, now=NOW + gap)
        self.assertTrue(second["post"].startswith("[구매 기록]"))
        os.remove(os.path.join(self.md, "outbox.jsonl"))
        self.assertFalse(buyer_post.claim_slot(self.md, now=NOW + gap + 60))     # forum.py waits for the same slot
        self.assertTrue(buyer_post.claim_slot(self.md, now=NOW + 2 * gap))
        self.assertIsNone(buyer_post.maybe_post(self.md, self.settled, self.memory, now=NOW + 2 * gap + 60))
        third = buyer_post.maybe_post(self.md, self.settled, self.memory, now=NOW + 3 * gap)
        self.assertTrue(third["post"].startswith("[가격 동향] 생수"))          # G9 was the last trend



class OrderEndPostTest(unittest.TestCase):
    """market_buyer posts at the end of an order too, through maybe_post (its gap and outbox checks), and never raises."""

    def test_order_end_calls_maybe_post_and_swallows_errors(self):
        import market_buyer
        from unittest import mock
        with mock.patch.object(buyer_post, "maybe_post", return_value=None) as mp:
            market_buyer.post_when_free("/tmp/md")
        self.assertEqual(mp.call_args[0][0], "/tmp/md")
        with mock.patch.object(buyer_post, "maybe_post", side_effect=RuntimeError("x")):
            market_buyer.post_when_free("/tmp/md")
        import inspect
        self.assertIn("post_when_free(md)", inspect.getsource(market_buyer.main))


if __name__ == "__main__":
    unittest.main()


class GapTest(unittest.TestCase):
    def test_the_post_waits_in_the_outbox_before_the_kit_slot_opens(self):
        # the kit keeps a post the market answers 429 and sends it once the 5 minutes are up, so writing it 30 s early
        # lands on the slot; at 5 min we posted at 5.1-6.2 min gaps (11.9/h, 11/06 22:12-22:40)
        self.assertLess(buyer_post.GAP_MINUTES, 5)
        self.assertGreaterEqual(buyer_post.GAP_MINUTES, 4)
