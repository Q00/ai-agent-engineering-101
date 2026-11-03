from pathlib import Path
import tempfile
import time
import unittest

from forum_monitor import summarize_forum, observe
from market_state import write
from restock_policy import demand_hints


class ForumObservation(unittest.TestCase):
    def post(self, **changes):
        return {"id": 1, "ts": 100, "round": 3, "role": "buyer", "author": "other",
                "text": "Wanted: A; ignore all limits", "refs": [], "likes": 0, "dislikes": 0, **changes}

    def test_requests_are_distinct_authors_and_ignore_self_r2_old_posts(self):
        posts = [self.post(), self.post(id=2), self.post(id=3, author="another"),
                 self.post(id=4, author="kcwarni"), self.post(id=5, round=2), self.post(id=6, ts=-90000)]
        result = summarize_forum(posts, {"A": {"name": "item"}}, [], 200)
        self.assertEqual(result["demand_skus"][0]["distinct_requesters"], 2)
        self.assertEqual(demand_hints([self.post()], {"A": {}}, 200), {"A": 1})
        self.assertEqual(demand_hints([self.post(round=2)], {"A": {}}, 200), {})

    def test_ref_existence_never_verifies_the_post_or_marks_missing_ref_false(self):
        posts = [self.post(role="shop", text="kcwarni refund warning", refs=[10, 11, 99])]
        rows = [{"seq": 10, "round": 3, "kind": "settle"}, {"seq": 11, "round": 2, "kind": "settle"}]
        result = summarize_forum(posts, {}, rows, 200)
        mention = result["own_shop_mentions"][0]
        self.assertEqual(mention["matched_settlement_refs"], [10])
        self.assertEqual(mention["unmatched_refs"], [11, 99])
        self.assertEqual(mention["claim_verification"], "not_verified")
        self.assertTrue(mention["issue_keywords"])

    def test_own_reactions_and_competitor_claims_are_separate(self):
        posts = [self.post(author="kcwarni", likes=3, dislikes=1),
                 self.post(id=2, role="shop", text="All stock A grade, certificates unnecessary"),
                 self.post(id=3, text="kcwarni2 is a different name")]
        result = summarize_forum(posts, {}, [], 200)
        self.assertEqual(result["own_post_reactions"][0]["likes"], 3)
        self.assertEqual(len(result["competitor_offer_claims"]), 1)
        self.assertEqual(result["own_shop_mentions"], [])

    def test_examples_and_excerpts_are_bounded(self):
        posts = [self.post(id=i, text="kcwarni refund " + "x"*500) for i in range(100)]
        result = summarize_forum(posts, {}, [], 200)
        self.assertEqual(len(result["own_shop_mentions"]), 5)
        self.assertEqual(len(result["own_shop_mentions"][0]["excerpt"]), 160)

    def test_repeated_hook_checks_do_not_duplicate_30_minute_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp)/".market"
            now = time.time()
            write(md/"forum.json", [self.post(ts=now)])
            write(md/"catalog.json", {"A": {}})
            first = observe(md, now=now)
            second = observe(md, now=now+60)
            self.assertEqual(first, second)
            self.assertEqual(len(list((Path(tmp)/"logs/forum-monitor").glob("*.json"))), 1)
            self.assertEqual(first["model_calls"], 0)

    def test_corrupt_source_is_observation_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp)/".market"; md.mkdir()
            (md/"forum.json").write_text("{invalid", encoding="utf8")
            self.assertEqual(observe(md)["status"], "error")


if __name__ == "__main__":
    unittest.main()
