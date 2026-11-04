import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from forum_capture import capture
from forum_monitor import observe
from forum_demand import match_request, requests
from forum_engagement import run, effects, available, followup
from forum_policy import queue_report, enqueue, message_key
from market_state import read, write, task_file

T = 1793760000
CONFIG = {"collection_interval_seconds": 1800, "analysis_interval_seconds": 1800,
          "max_posts": 100, "retention_seconds": 86400}


class ForumEngagementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = Path(self.tmp.name) / ".market"
        self.md.mkdir()
        self.unit = dict(unit="u1", sku="G1", kind="goods", grade="A", cost=100, list_price=150,
                         supplied_at=T-60, name="침구", category="가구/침구>침구/홈데코>침구",
                         cert={"sig": "fixture", "cert": dict(unit="u1", sku="G1", kind="goods", grade="A")})
        self.catalog = {"G1": dict(name="침구", category="가구/침구>침구/홈데코>침구", kind="goods"),
                        "G2": dict(name="소파", category="가구/침구>가구>소파/의자/스툴", kind="goods")}
        self.posts = [dict(id=1, ts=T-10, round=3, role="buyer", author="other", text="침구 구합니다")]
        self.save("forum.json", self.posts)
        self.save("inventory.json", dict(round=3, units=[self.unit]))
        self.save("catalog.json", self.catalog)

    def save(self, name, data):
        path = self.md / name
        write(path, data)
        os.utime(path, (T, T))

    def test_category_request_and_explicit_sku(self):
        self.assertEqual(match_request("침구 구합니다", self.catalog), (["G1"], "category"))
        self.assertEqual(match_request("소파/의자/스툴 구합니다", self.catalog), (["G2"], "category"))
        self.assertEqual(match_request("G2 구합니다. 침구", self.catalog), (["G2"], "sku"))
        self.assertEqual(match_request("식품 구합니다", self.catalog), ([], "none"))

    def test_no_self_old_or_r2_requests(self):
        self.assertEqual(requests([{**self.posts[0], "owner": "kcwarni"}], self.catalog, T), [])
        self.assertEqual(requests([{**self.posts[0], "round": 2}], self.catalog, T), [])
        self.assertEqual(requests(self.posts, self.catalog, T+7201), [])
        self.assertEqual(requests([{k: v for k, v in self.posts[0].items() if k != "round"}], self.catalog, T), [])

    def test_reply_uses_thread_disclosure_and_deduplication(self):
        self.assertEqual(run(self.md, T)["status"], "queued")
        message = json.loads((self.md / "outbox.jsonl").read_text(encoding="utf8"))
        self.assertEqual(message["thread"], 1)
        self.assertEqual(message["as"], "shop")
        self.assertIn("보증 0시간", message["post"])
        self.assertIn("150 credits", message["post"])
        self.assertEqual(run(self.md, T+60)["status"], "interval_limited")
        os.utime(self.md / "forum.json", (T+1800, T+1800))
        os.utime(self.md / "inventory.json", (T+1800, T+1800))
        run(self.md, T+1800)
        self.assertEqual(len((self.md / "outbox.jsonl").read_text(encoding="utf8").splitlines()), 1)

    def test_stale_stock_and_missing_certificate_do_not_advertise(self):
        self.assertEqual(run(self.md, T+121)["status"], "stale_or_non_r3")
        self.unit["cert"] = {}
        self.save("inventory.json", dict(round=3, units=[self.unit]))
        self.assertNotEqual(run(self.md, T)["status"], "queued")

    def test_held_uncertain_and_completed_units_not_advertised(self):
        offer = dict(task="task1", unit="u1", expires_at=T-1000)
        write(self.md / "signed-offers/offer.json", offer)
        for status in ("uncertain", "processing", "completed"):
            write(task_file(self.md, "checkout-events", "task1"), {"status": status})
            self.assertEqual(available(self.md, {"units": [self.unit]}, T), [])
        write(task_file(self.md, "checkout-events", "task1"), {"status": "refused"})
        self.assertEqual(len(available(self.md, {"units": [self.unit]}, T)), 1)

    def test_buyer_comparison_separates_conditions_and_deduplicates(self):
        rows = [dict(seq=i, ts=T-30, round=3, kind="settle", data=dict(item="G1", grade="A", amount=100))
                for i in (2, 3, 4)]
        rows += [rows[0], dict(seq=5, ts=T-30, round=3, kind="settle", data=dict(item="G1", grade="B", amount=1))]
        self.save("market-intel.json", dict(round=3, observed_at=T, rows=rows))
        deal = {"receipt": {"price": 80}, "settled": {"ledger": 1, "sku": "G1", "grade": "A", "value": 150}}
        self.assertEqual(queue_report(self.md, deal, {"stars": 5}, T), "queued")
        message = json.loads((self.md / "outbox.jsonl").read_text(encoding="utf8"))
        self.assertIn("n=3, median=100", message["post"])
        self.assertIn("20.0% below", message["post"])
        self.assertEqual(queue_report(self.md, deal, {"stars": 5}, T+3600), "already_queued")

    def test_collection_interval_changes_no_analysis_or_llm(self):
        with patch("forum_capture.settings", return_value={**CONFIG, "collection_interval_seconds": 60}):
            first = capture(self.md, T)
            self.save("forum.json", self.posts + [{**self.posts[0], "id": 2}])
            self.assertEqual(capture(self.md, T+30), first)
            second = capture(self.md, T+60)
            self.assertEqual(len(second["posts"]), 2)
            self.assertEqual(second["model_calls"], 0)

    def test_thirty_minute_option_does_not_collect_every_minute(self):
        with patch("forum_capture.settings", return_value=CONFIG):
            first = capture(self.md, T)
            self.assertEqual(first["collection_interval_seconds"], 1800)
            self.assertEqual(capture(self.md, T+60), first)

    def test_five_minute_collection_keeps_analysis_at_thirty_minutes(self):
        with patch("forum_capture.settings", return_value={**CONFIG, "collection_interval_seconds": 300}):
            first = observe(self.md, now=T)
            self.save("forum.json", self.posts + [{**self.posts[0], "id": 2}])
            os.utime(self.md / "forum.json", (T+300, T+300))
            second = observe(self.md, now=T+300)
            self.assertEqual(first, second)
            self.assertEqual(len(read(self.md / "forum-buffer.json")["posts"]), 2)
            self.assertEqual(len(list((self.md.parent / "logs/forum-monitor").glob("*.json"))), 1)

    def test_buffer_retention_and_count_bound(self):
        with patch("forum_capture.settings", return_value={**CONFIG, "collection_interval_seconds": 300}):
            self.save("forum.json", [{**self.posts[0], "id": i} for i in range(100)])
            capture(self.md, T)
            self.save("forum.json", [{**self.posts[0], "id": i} for i in range(100, 200)])
            os.utime(self.md / "forum.json", (T+300, T+300))
            second = capture(self.md, T+300)
            self.assertEqual(len(second["posts"]), 100)
            self.assertEqual({p["id"] for p in second["posts"]}, set(range(100, 200)))

    def test_reactions_missing_are_unknown_and_api_error_not_success(self):
        run(self.md, T)
        action = next(iter(read(self.md / "forum-actions.json").values()))
        self.save("outbox.sent.jsonl", {})
        (self.md / "outbox.sent.jsonl").write_text(json.dumps({"sent": action["message"], "status": 400, "answer": "{}"})+"\n", encoding="utf8")
        result = effects(self.md, [], T)[0]
        self.assertEqual(result["status"], 400)
        self.assertIsNone(result["likes"])
        self.assertIsNone(result["advertised_unit_settlements"])

    def test_effects_use_local_acp_sales_and_preserve_noncausal_label(self):
        run(self.md, T)
        action = next(iter(read(self.md / "forum-actions.json").values()))
        (self.md / "outbox.sent.jsonl").write_text(json.dumps({"sent": action["message"], "status": 200, "answer": '{"post":10}'})+"\n", encoding="utf8")
        self.save("local-settlements.json", dict(r3_anchor_found=True, active_round=3, conflicting_ledgers=[], collected_at=T+60,
                  records={"1": dict(source="local_acp_settled", round=3, unit="u1", ts=T+40)}))
        result = effects(self.md, [{"id": 10, "ts": T+10, "likes": 2, "dislikes": 0}], T+60)[0]
        self.assertEqual(result["advertised_unit_settlements"], 1)
        self.assertEqual(result["window_basis"], "post_timestamp")
        self.assertEqual(result["attribution"], "concurrent_observation_only")

    def test_shop_hourly_daily_cap_and_buyer_independent_budget(self):
        message = dict(post="fixture", refs=[], **{"as": "shop"})
        self.assertEqual(enqueue(self.md, message, "a", "stock_reply", now=T), "queued")
        self.assertEqual(enqueue(self.md, message, "b", "stock_reply", now=T+1), "rate_limited")
        self.assertEqual(enqueue(self.md, {**message, "as": "buyer"}, "c", "buyer_followup", now=T+1), "queued")

    def test_followup_requires_successful_original_and_matching_purchase(self):
        deal = dict(receipt={"price": 80}, settled=dict(ledger=1, sku="G1", unit="u1", value=100, grade="A"))
        queue_report(self.md, deal, {"stars": 5}, T)
        actions = read(self.md / "forum-actions.json")
        self.assertEqual(followup(self.md, actions, T+21600), "none")
        action = next(iter(actions.values()))
        (self.md / "outbox.sent.jsonl").write_text(json.dumps({"sent": action["message"], "status": 200})+"\n", encoding="utf8")
        self.save("buyer-deals/1.json", dict(ledger=1, unit="u1", refund_confirmed=True))
        self.assertEqual(followup(self.md, actions, T+21600), "queued")
        lines = (self.md / "outbox.jsonl").read_text(encoding="utf8").splitlines()
        self.assertIn("Refund settlement confirmed", json.loads(lines[-1])["post"])

    def test_buyer_three_slots_reserved_for_followups(self):
        for i in range(9):
            deal = dict(receipt={"price": 80}, settled=dict(ledger=i+1, sku="G1", value=100, grade="A"))
            self.assertEqual(queue_report(self.md, deal, {"stars": 5}, T+i*3600), "queued")
        deal["settled"]["ledger"] = 10
        self.assertEqual(queue_report(self.md, deal, {"stars": 5}, T+32400), "rate_limited")
        self.assertEqual(enqueue(self.md, {"post": "follow-up", "refs": [1], "as": "buyer"},
                                 "followup:1", "buyer_followup", now=T+32400), "queued")


if __name__ == "__main__":
    unittest.main()
