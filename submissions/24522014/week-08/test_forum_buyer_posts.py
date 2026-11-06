"""Buyer posts: separate intervals from shop posts, what a note may say and leave out, repeats, and the automatic stop."""

import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import forum_buyer_posts as notes
import forum_policy
from forum_posts import safe

T = 1793930000.0
LIMITS = {"buyer_posts_enabled": True, "buyer_daily_limit": 144, "buyer_report_limit": 72, "buyer_min_interval_seconds": 600, "shop_daily_limit": 24, "shop_reply_interval_seconds": 600, "min_interval_seconds": 3600,
          "brake_dislikes": 3, "brake_dislike_ratio": .25, "minimum_report_surplus_ratio": .25, "buyer_resume_token": "1"}
CATALOG = {"G1": {"name": "Blue chair", "list_price": 100000, "kind": "goods", "category": "goods>home>chairs"}, "G2": {"name": "Red chair", "list_price": 50000, "kind": "goods", "category": "goods>home>chairs"},
           "G3": {"name": "Best floor lamp", "list_price": 50000, "kind": "goods", "category": "goods>home>lamps"}}


def settle(seq, item="G1", amount=70000, shop="https://other", unit=None, grade="A", ago=60):
    return {"round": 3, "kind": "settle", "seq": seq, "ts": T-ago, "data": {"shop": shop, "item": item, "amount": amount, "grade": grade, "unit": unit or f"u{seq}", "claim_honest": True}}


class Base(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.md = self.root / ".market"
        self.md.mkdir()
        self.limits = dict(LIMITS)
        patcher = patch.object(forum_policy, "read", side_effect=self.read)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.write("catalog.json", CATALOG)
        self.intel([settle(i) for i in range(1, 7)])

    def read(self, path, default=None):
        if Path(path).name == "forum_posting.json":
            return self.limits
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default

    def write(self, name, value):
        (self.md / name).write_text(json.dumps(value), encoding="utf-8")

    def intel(self, rows, forum=.8, observed=T, url="https://mine"):
        self.write("market-intel.json", {"round": 3, "observed_at": observed, "rows": rows, "competition": {"own": {"buyers": {"forum": forum}, "shops": {"card": {"url": url}}}}})

    def outbox(self):
        path = self.md / "outbox.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def enqueue(self, key, at, kind="buyer_followup", role="buyer"):
        return forum_policy.enqueue(self.md, {"post": key, "as": role}, key, kind, {}, at)


class Limits(Base):
    def test_shipped_settings_are_valid(self):
        self.assertTrue(forum_policy.valid_limits(json.loads((forum_policy.ROOT / "forum_posting.json").read_text(encoding="utf-8"))))

    def test_both_steps_are_valid_and_faster_than_the_market_is_not(self):
        self.assertTrue(forum_policy.valid_limits(LIMITS))
        self.assertTrue(forum_policy.valid_limits({**LIMITS, "buyer_min_interval_seconds": 300, "buyer_daily_limit": 288, "buyer_report_limit": 144}))
        for bad in (299, 3601, "600", None):
            self.assertFalse(forum_policy.valid_limits({**LIMITS, "buyer_min_interval_seconds": bad}), bad)
        self.assertFalse(forum_policy.valid_limits({**LIMITS, "buyer_daily_limit": 289}))

    def test_a_settings_file_without_the_new_key_keeps_the_hour(self):
        del self.limits["buyer_min_interval_seconds"]
        self.assertEqual(self.enqueue("a", T), "queued")
        self.assertEqual(self.enqueue("b", T+3599), "rate_limited")
        self.assertEqual(self.enqueue("c", T+3600), "queued")

    def test_buyer_interval_applies_at_six_an_hour(self):
        self.assertEqual(self.enqueue("a", T), "queued")
        self.assertEqual(self.enqueue("b", T+599), "rate_limited")
        self.assertEqual(self.enqueue("c", T+600), "queued")

    def test_shop_general_interval_does_not_move(self):
        self.assertEqual(self.enqueue("a", T, "stock_notice", "shop"), "queued")
        self.assertEqual(self.enqueue("b", T+3599, "stock_notice", "shop"), "rate_limited")
        self.assertEqual(self.enqueue("c", T+3600, "stock_notice", "shop"), "queued")

    def test_buyer_posts_do_not_use_up_shop_slots(self):
        self.assertEqual(self.enqueue("a", T), "queued")
        self.assertEqual(self.enqueue("s", T+1, "stock_notice", "shop"), "queued")

    def test_daily_limit_holds(self):
        self.limits["buyer_daily_limit"] = 4
        self.limits["buyer_report_limit"] = 3
        start = T - T % 86400
        for i in range(4):
            self.assertEqual(self.enqueue(f"a{i}", start+i*600), "queued")
        self.assertEqual(self.enqueue("a5", start+4*600), "rate_limited")


class Content(Base):
    def posted(self):
        return notes.run(self.md, T), self.outbox()

    def test_a_note_has_samples_refs_and_no_shop_name(self):
        status, sent = self.posted()
        self.assertEqual(status, "queued")
        self.assertEqual(sent[0]["as"], "buyer")
        self.assertEqual(sent[0]["refs"], [6, 5, 4, 3, 2])
        text = sent[0]["post"]
        self.assertIn("n=6", text)
        self.assertIn("median paid 70000 (30.0% off list)", text)
        self.assertNotIn("https://other", text)
        self.assertNotIn("kcwarni", text)
        self.assertTrue(safe(text))

    def test_nothing_goes_out_while_the_setting_is_off(self):
        self.limits["buyer_posts_enabled"] = False
        self.assertEqual(notes.run(self.md, T), "off")
        self.assertEqual(self.outbox(), [])

    def test_too_few_samples_say_nothing(self):
        self.intel([settle(i) for i in range(1, 5)])
        self.assertEqual(notes.run(self.md, T), "no_candidate")

    def test_old_rows_do_not_count(self):
        self.intel([settle(i, ago=6*3600+1) for i in range(1, 7)])
        self.assertEqual(notes.run(self.md, T), "no_candidate")

    def test_stale_market_data_says_nothing_and_asks_for_none(self):
        self.intel([settle(i) for i in range(1, 7)], observed=T-3601)
        self.assertEqual(notes.run(self.md, T), "stale_market_intel")

    def test_our_shop_sales_are_left_out_by_url_and_by_unit(self):
        self.intel([settle(i, shop="https://mine") for i in range(1, 7)])
        self.assertEqual(notes.run(self.md, T), "no_candidate")
        self.write("local-settlements.json", {"records": {str(i): {"unit": f"u{i}"} for i in range(1, 7)}})
        self.intel([settle(i) for i in range(1, 7)])
        self.assertEqual(notes.run(self.md, T), "no_candidate")

    def test_same_sku_does_not_repeat_within_a_day(self):
        self.intel([settle(i) for i in range(1, 7)])
        self.assertEqual(notes.run(self.md, T), "queued")
        self.assertEqual(notes.run(self.md, T+600), "no_candidate")
        self.assertEqual(len(self.outbox()), 1)

    def test_a_purchase_report_of_the_sku_also_blocks_a_note_and_the_other_way(self):
        deal = {"receipt": {"price": 70000}, "settled": {"ledger": 900, "sku": "G1", "unit": "u", "value": 100000, "grade": "A"}}
        self.assertEqual(forum_policy.queue_report(self.md, deal, {"stars": 5}, T), "queued")
        self.assertEqual(notes.run(self.md, T+600), "no_candidate")
        other = {"receipt": {"price": 70000}, "settled": {"ledger": 901, "sku": "G1", "unit": "v", "value": 100000, "grade": "A"}}
        self.assertEqual(forum_policy.queue_report(self.md, other, {"stars": 5}, T+1200), "sku_recently_posted")

    def test_turns_between_sku_and_category(self):
        rows = [settle(i) for i in range(1, 12)] + [settle(i, "G2", 30000) for i in range(12, 23)]
        self.intel(rows)
        self.assertEqual(notes.run(self.md, T), "queued")
        self.assertEqual(notes.run(self.md, T+600), "queued")
        posts = [m["post"] for m in self.outbox()]
        self.assertIn("(SKU G", posts[0])
        self.assertIn("category chairs", posts[1])
        self.assertIn("across 2 SKUs", posts[1])

    def test_a_word_safe_refuses_skips_that_note(self):
        self.intel([settle(i, "G3", 30000) for i in range(1, 7)])
        self.assertFalse(safe(notes.sku_note("G3", [(1, "G3", 30000, .4, "A", "")], CATALOG["G3"], T)))
        self.assertEqual(notes.run(self.md, T), "no_candidate")
        self.assertEqual(self.outbox(), [])

    def test_a_report_under_the_set_share_is_not_posted(self):
        deal = {"receipt": {"price": 76000}, "settled": {"ledger": 902, "sku": "G2", "unit": "w", "value": 100000, "grade": "A"}}
        self.assertEqual(forum_policy.queue_report(self.md, deal, {"stars": 5}, T), "insufficient_new_value")

    def test_never_writes_a_vote(self):
        notes.run(self.md, T)
        self.assertFalse(any("vote" in m for m in self.outbox()))


class Numbers(Base):
    def test_large_prices_are_written_in_full(self):
        text = notes.sku_note("G9", [(1, "G9", 1784970, .2, "A", ""), (2, "G9", 2250000, .1, "A", "")], {"name": "x", "list_price": 2320000}, T)
        self.assertNotIn("e+", text)
        self.assertIn("list price 2320000", text)
        self.assertIn("range 1784970 to 2250000", text)


class Stop(Base):
    def post(self, ago, likes, dislikes, author="kcwarni", role="buyer"):
        return {"author": author, "role": role, "ts": T-ago, "likes": likes, "dislikes": dislikes}

    def begin(self):
        self.intel([], forum=.8, observed=T-7200)
        self.assertEqual(self.enqueue("start", T-7200), "queued")
        self.intel([], forum=.8)

    def posts(self, rows):
        self.write("forum-buffer.json", {"posts": rows})

    def test_high_dislike_share_stops_and_says_so_once(self):
        self.begin()
        self.posts([self.post(3600, 0, 1) for _ in range(5)])
        with patch("sys.stderr") as stream:
            self.assertEqual(self.enqueue("b", T), "buyer_posts_stopped")
            self.assertEqual(self.enqueue("c", T+600), "buyer_posts_stopped")
        self.assertEqual(sum("자동 중지" in str(call) for call in stream.write.call_args_list), 1)
        lines = (self.root / "logs/forum-buyer-stop.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["reason"], "dislike_share")

    def test_exactly_a_quarter_does_not_stop(self):
        self.begin()
        self.posts([self.post(3600, 3, 1) for _ in range(5)])
        self.assertEqual(self.enqueue("b", T), "queued")

    def test_young_posts_are_not_judged_since_one_dislike_comes_first(self):
        self.begin()
        self.posts([self.post(600, 0, 1) for _ in range(5)])
        self.assertEqual(self.enqueue("b", T), "queued")

    def test_few_posts_are_not_judged(self):
        self.begin()
        self.posts([self.post(3600, 0, 3) for _ in range(4)])
        self.assertEqual(self.enqueue("b", T), "queued")

    def test_others_posts_and_our_shop_posts_and_earlier_posts_do_not_count(self):
        self.begin()
        self.posts([self.post(3600, 0, 5, author="someone") for _ in range(5)] + [self.post(3600, 0, 5, role="shop") for _ in range(5)] + [self.post(9000, 0, 5) for _ in range(5)])
        self.assertEqual(self.enqueue("b", T), "queued")

    def test_only_the_last_twenty_count(self):
        self.begin()
        self.posts([self.post(7000, 0, 9) for _ in range(5)] + [self.post(3600, 9, 1) for _ in range(20)])
        self.assertEqual(self.enqueue("b", T), "queued")

    def test_board_forum_fall_stops_at_the_limit_not_before(self):
        self.begin()
        self.intel([], forum=.79)
        self.assertEqual(self.enqueue("b", T), "queued")
        self.intel([], forum=.78)
        self.assertEqual(self.enqueue("c", T+600), "buyer_posts_stopped")

    def test_a_board_forum_rise_is_never_a_stop(self):
        self.begin()
        self.intel([], forum=.9)
        self.assertEqual(self.enqueue("b", T), "queued")

    def test_a_stale_board_is_not_a_stop(self):
        self.begin()
        self.intel([], forum=.1, observed=T-3601)
        self.assertEqual(self.enqueue("b", T), "queued")

    def test_a_correction_still_goes_out_after_a_stop(self):
        self.begin()
        self.posts([self.post(3600, 0, 1) for _ in range(5)])
        self.assertEqual(self.enqueue("b", T), "buyer_posts_stopped")
        self.assertEqual(self.enqueue("fix", T+1, forum_policy.BUYER_CORRECTION), "queued")

    def test_a_new_token_starts_a_new_count(self):
        self.begin()
        self.posts([self.post(3600, 0, 1) for _ in range(5)])
        self.assertEqual(self.enqueue("b", T), "buyer_posts_stopped")
        self.limits["buyer_resume_token"] = "2"
        self.assertEqual(self.enqueue("c", T+600), "queued")
        state = json.loads((self.md / "forum-policy.json").read_text(encoding="utf-8"))
        self.assertEqual(state["buyer_resume"]["token"], "2")

    def test_note_run_stops_too(self):
        self.begin()
        self.posts([self.post(3600, 0, 1) for _ in range(5)])
        self.intel([settle(i) for i in range(1, 7)])
        sent = len(self.outbox())
        self.assertEqual(notes.run(self.md, T), "buyer_posts_stopped")
        self.assertEqual(len(self.outbox()), sent)


class Engagement(Base):
    def test_a_note_failure_does_not_stop_the_shop_pass(self):
        import forum_engagement
        with patch.object(forum_buyer_run := notes, "run", side_effect=ValueError("x")), patch.object(forum_engagement, "_run", return_value={"status": "ok"}) as shop, patch("builtins.print"):
            self.assertEqual(forum_engagement.run(self.md, T), {"status": "ok"})
        shop.assert_called_once()
        self.assertTrue(callable(forum_buyer_run.run))

    def test_hook_no_longer_than_it_needs_when_a_post_is_not_due(self):
        self.assertEqual(self.enqueue("a", T), "queued")
        start = time.perf_counter()
        self.assertEqual(notes.run(self.md, T+1), "rate_limited")
        self.assertLess(time.perf_counter()-start, 1)


if __name__ == "__main__":
    unittest.main()
