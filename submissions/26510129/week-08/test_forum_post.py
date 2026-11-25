import json
import tempfile
import unittest
from pathlib import Path

import forum_post
import ledger

CATALOG = {"S1": {"name": "자코모 메이트 4인 소파", "category": "가구>소파/의자/스툴", "list_price": 2_000_000},
           "S2": {"name": "스툴", "category": "가구>소파/의자/스툴", "list_price": 40_000},
           "K1": {"name": "포기김치 10kg", "category": "식품>김치", "list_price": 30_000}}
NOW = 1_000_000


def rows(n_sofa, n_kimchi):
    out = [{"sku": "S1", "price": 1_700_000, "ts": NOW - i, "seq": 100 + i} for i in range(n_sofa)]
    out += [{"sku": "K1", "price": 24_000, "ts": NOW - i, "seq": 500 + i} for i in range(n_kimchi)]
    return out


class ForumPostTest(unittest.TestCase):
    def test_compose_reads_like_the_voted_posts_and_cites_the_ledger(self):
        text, refs = forum_post.compose("가구>소파/의자/스툴", rows(10, 0), CATALOG)
        self.assertTrue(text.startswith("[시세] 소파/의자/스툴: 최근 24h 공개 ledger 정산 10건, 체결가 중앙값 1,700,000원 (정가 대비 85%)"))
        self.assertIn("최다 판매 자코모 메이트 4인 소파 (S1, 10건)", text)
        self.assertEqual([100, 101, 102], refs)

    def test_posts_the_richest_category_first_then_rotates_and_respects_the_shared_five_minutes(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, ledger.CACHE_FILE).write_text(json.dumps({"last_seq": 1, "rows": rows(10, 20)}))
            first = forum_post.maybe_market_post(d, CATALOG, now=NOW)
            self.assertIn("소파/의자/스툴", first)                                   # 17M beats 480k
            self.assertIsNone(forum_post.maybe_market_post(d, CATALOG, now=NOW + 100))   # inside five minutes
            second = forum_post.maybe_market_post(d, CATALOG, now=NOW + 400)
            self.assertIn("김치", second)                                           # sofas posted within three hours
            self.assertIsNone(forum_post.maybe_market_post(d, CATALOG, now=NOW + 800))   # nothing left to say
            lines = [json.loads(l) for l in Path(d, "outbox.jsonl").read_text().splitlines()]
            self.assertEqual(2, len(lines))
            self.assertEqual(("buyer", [100, 101, 102]), (lines[0]["as"], lines[0]["refs"]))
            self.assertEqual(NOW + 400, json.loads(Path(d, forum_post.STATE_FILE).read_text())["last_post"])

    def test_small_samples_are_not_posted(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, ledger.CACHE_FILE).write_text(json.dumps({"last_seq": 1, "rows": rows(3, 0)}))
            self.assertIsNone(forum_post.maybe_market_post(d, CATALOG, now=NOW))


if __name__ == "__main__":
    unittest.main()
