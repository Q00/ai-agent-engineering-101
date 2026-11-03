"""Buyer forum posts: facts from the settled deal, one per ledger, gap and daily cap, outbox line format."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

W = Path(__file__).resolve().parents[1]
os.environ.setdefault("R3_TEST_HOME", tempfile.mkdtemp(prefix="r3-test-"))
os.environ["R3_HOME"] = os.environ["R3_TEST_HOME"]
sys.path.insert(0, str(W))

import policy  # noqa: E402
from tools import forum  # noqa: E402

POL = json.loads(json.dumps(policy.DEFAULTS))
NOW = 1_790_000_000
CATALOG = {"F1": {"name": "김치 A", "category": "식품>김치", "list_price": 30000, "kind": "food", "shelf_hours": 720},
           "G1": {"name": "침대", "category": "가구>침대", "list_price": 100000, "kind": "goods"}}
SHOPS = [{"name": "가게\n이름\x07 " + "광고" * 40, "url": "http://a/", "reviews": [], "offers": []}]
FOOD = {"order": {"order": "o-5", "item": "김치", "budget": 41234, "acceptable": ["F1"], "kind": "food"},
        "receipt": {"item": "F1", "price": 22000, "unit": "u7"},
        "settled": {"shop": "http://a", "unit": "u7", "sku": "F1", "name": "김치 A", "grade": "A", "claim": "A",
                    "claim_honest": True, "expires_at": NOW + 5 * 3600, "warranty_hours": None, "value": 30000,
                    "ledger": 812}}
GOODS = {"order": {"order": "o-6", "item": "침대", "budget": 99999, "acceptable": ["G1"], "kind": "goods"},
         "receipt": {"item": "G1", "price": 70000},
         "settled": {"shop": "http://b", "sku": "G1", "name": "침대", "grade": "B", "claim": "A",
                     "claim_honest": False, "warranty_hours": 6, "value": 100000, "ledger": 900}}


def deal(base, ledger):
    return json.loads(json.dumps(dict(base, settled=dict(base["settled"], ledger=ledger))))


class ComposeTests(unittest.TestCase):
    def test_food_line(self):
        text, refs = forum.compose(FOOD, SHOPS, CATALOG, NOW)
        self.assertEqual(refs, [812])
        self.assertIn("[구매 기록] 김치 A (F1)", text)
        self.assertIn("22,000원, 정가 30,000원 대비 27% 저렴", text)
        self.assertIn("등급 A (주장 A, 정직)", text)
        self.assertIn("남은 유통기한 5시간", text)
        self.assertNotIn("보증", text)
        self.assertIn("ledger #812", text)
        self.assertNotIn("41234", text)
        self.assertNotIn("41,234", text)

    def test_goods_line(self):
        text, _ = forum.compose(GOODS, SHOPS, CATALOG, NOW)
        self.assertIn("등급 B (주장 A, 부정확)", text)
        self.assertIn("보증 6h", text)
        self.assertNotIn("유통기한", text)
        self.assertNotIn("판매:", text)            # shop b is not in shops.json: left out, not invented
        no_w = dict(GOODS, settled=dict(GOODS["settled"], warranty_hours=None))
        self.assertIn("보증 없음", forum.compose(no_w, SHOPS, CATALOG, NOW)[0])

    def test_shop_name_is_one_short_line(self):
        text, _ = forum.compose(FOOD, SHOPS, CATALOG, NOW)
        self.assertNotIn("\n", text)
        shop = text.split("판매: ")[1].split(" | ")[0]
        self.assertLessEqual(len(shop), forum.NAME_MAX)
        self.assertTrue(shop.startswith("가게 이름"))

    def test_missing_facts_left_out(self):
        bare = {"receipt": {"item": "X9", "price": 5000}, "settled": {"ledger": 3}}
        text, _ = forum.compose(bare, [], {}, NOW)
        self.assertEqual(text, "[구매 기록] X9 | 5,000원 | 보증 없음 | ledger #3")
        self.assertIsNone(forum.compose({"receipt": {"price": 5000}, "settled": {}}, [], {}, NOW))
        no_sku = {"receipt": {"price": 5000}, "settled": {"ledger": 4}}
        self.assertEqual(forum.compose(no_sku, [], {}, NOW)[0], "[구매 기록] | 5,000원 | 보증 없음 | ledger #4")

    def test_price_against_list(self):
        cat = {"X9": {"list_price": 10000, "kind": "goods"}}
        line = lambda p: forum.compose({"receipt": {"item": "X9", "price": p}, "settled": {"ledger": 3}},
                                       [], cat, NOW)[0].split(" | ")[1]
        self.assertEqual(line(10000), "10,000원, 정가 10,000원 대비 같은 값")
        self.assertEqual(line(9990), "9,990원, 정가 10,000원 대비 1% 미만 저렴")
        self.assertEqual(line(10040), "10,040원, 정가 10,000원 대비 1% 미만 비쌈")
        self.assertEqual(line(7000), "7,000원, 정가 10,000원 대비 30% 저렴")

    def test_food_under_an_hour(self):
        food = dict(FOOD, settled=dict(FOOD["settled"], expires_at=NOW + 600))
        self.assertIn("남은 유통기한 1시간 미만", forum.compose(food, SHOPS, CATALOG, NOW)[0])


class MaybePostTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="r3-forum-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.md = self.home / ".market"
        self.md.mkdir()
        (self.md / "shops.json").write_text(json.dumps(SHOPS, ensure_ascii=False), encoding="utf-8")
        (self.md / "catalog.json").write_text(json.dumps(CATALOG, ensure_ascii=False), encoding="utf-8")
        for p in (patch.object(forum, "MEM", self.home / "memory"),
                  patch("memory.decisions.LOGS", self.home / "logs")):
            p.start()
            self.addCleanup(p.stop)
        self.pol = json.loads(json.dumps(POL))

    def outbox(self):
        box = self.md / "outbox.jsonl"
        return [json.loads(x) for x in box.read_text(encoding="utf-8").splitlines()] if box.exists() else []

    def whys(self):
        f = self.home / "logs" / "decisions" / "forum.jsonl"
        return [(r["choice"]["posted"], r["why"]) for r in map(json.loads, f.read_text(encoding="utf-8").splitlines())]

    def test_posts_as_buyer_with_ledger_ref(self):
        self.assertTrue(forum.maybe_post(self.md, FOOD, self.pol, NOW, policy_ver="v1"))
        (line,) = self.outbox()
        self.assertEqual((line["as"], line["refs"], line["thread"]), ("buyer", [812], None))
        self.assertIn("판매: 가게 이름", line["post"])
        rows = (self.home / "logs" / "decisions" / "forum.jsonl").read_text(encoding="utf-8").splitlines()
        row = json.loads(rows[0])
        self.assertEqual((row["kind"], row["choice"], row["why"], row["policy_ver"]),
                         ("post", {"posted": True}, "posted", "v1"))
        self.assertEqual(row["inputs"]["text"], line["post"])

    def test_same_ledger_once(self):
        forum.maybe_post(self.md, FOOD, self.pol, NOW)
        self.assertFalse(forum.maybe_post(self.md, FOOD, self.pol, NOW + 3600))
        self.assertEqual(len(self.outbox()), 1)
        self.assertEqual(self.whys()[-1], (False, "skipped: duplicate"))

    def test_gap(self):
        forum.maybe_post(self.md, FOOD, self.pol, NOW)
        self.assertFalse(forum.maybe_post(self.md, deal(FOOD, 813), self.pol, NOW + 299))
        self.assertTrue(forum.maybe_post(self.md, deal(FOOD, 814), self.pol, NOW + 300))
        self.assertEqual([x["refs"] for x in self.outbox()], [[812], [814]])
        self.assertIn((False, "skipped: gap"), self.whys())

    def test_daily_cap_rolls(self):
        self.pol["forum"]["max_per_day"] = 2
        self.assertTrue(forum.maybe_post(self.md, deal(FOOD, 1), self.pol, NOW))
        self.assertTrue(forum.maybe_post(self.md, deal(FOOD, 2), self.pol, NOW + 600))
        self.assertFalse(forum.maybe_post(self.md, deal(FOOD, 3), self.pol, NOW + 1200))
        self.assertEqual(self.whys()[-1], (False, "skipped: daily_cap"))
        self.assertTrue(forum.maybe_post(self.md, deal(FOOD, 4), self.pol, NOW + forum.DAY_S + 1))
        self.assertEqual([x["refs"] for x in self.outbox()], [[1], [2], [4]])

    def test_disabled(self):
        self.pol["forum"]["enabled"] = False
        self.assertFalse(forum.maybe_post(self.md, FOOD, self.pol, NOW))
        self.assertEqual(self.outbox(), [])
        self.assertFalse((self.home / "memory" / "forum.json").exists())
        self.assertEqual(self.whys(), [(False, "skipped: disabled")])

    def test_no_ledger(self):
        self.assertFalse(forum.maybe_post(self.md, deal(FOOD, None), self.pol, NOW))
        self.assertEqual(self.outbox(), [])
        self.assertEqual(self.whys(), [(False, "skipped: no_ledger")])

    def test_unpaid(self):
        unpaid = dict(FOOD, receipt={"item": "F1"})
        self.assertFalse(forum.maybe_post(self.md, unpaid, self.pol, NOW))
        self.assertEqual(self.outbox(), [])
        self.assertEqual(self.whys(), [(False, "skipped: unpaid")])


if __name__ == "__main__":
    unittest.main()
