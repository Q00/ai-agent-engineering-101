"""Bounded sampled demand and exact-terms evidence; synthetic HTTP only."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

import market_evidence as market
from feedback import atomic_json


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent / ".market")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = 2000000000

    def row(self, seq, **data):
        return {"seq": seq, "ts": self.now - 30, "round": 3, "kind": "settle",
                "data": {"item": "G8095ddd6", "shop": "shop-" + str(seq % 2),
                         "buyer": "buyer-" + str(seq % 3), "order": "o-" + str(seq),
                         "grade": "A", "warranty_hours": 6.0, "amount": 650000,
                         "claim_honest": True, **data}}

    def state(self, rows):
        return {"schema": 1, "round": 3, "updated_at": self.now,
                "events": market.normalize(rows, 3, self.now)}

    def test_three_fixed_sequence_pages_are_chronological_and_bounded(self):
        calls = []
        pages = {-500: range(1001, 1501), 500: range(501, 1001), 0: range(1, 501)}
        def respond(req):
            if req.url.path == "/api/shops":
                calls.append("stock")
                return httpx.Response(200, json={"round": 3, "shops": []})
            after = int(req.url.params["after"])
            calls.append(after)
            return httpx.Response(200, json=[self.row(seq) for seq in reversed(pages[after])])
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            state = market.refresh(self.root, 3, self.now, client=client)
            again = market.refresh(self.root, 3, self.now + 30, client=client)
        self.assertEqual(calls, [-500, 500, 0, "stock"])
        self.assertEqual(state, again)
        self.assertEqual([e["seq"] for e in state["events"]], list(range(1, 1501)))
        self.assertEqual(state["sample"]["rows"], 1500)
        self.assertIn("sampled", state["coverage"])

    def test_older_page_failure_keeps_valid_head_and_does_not_retry(self):
        calls = []
        def respond(req):
            calls.append(str(req.url))
            if len(calls) == 1:
                return httpx.Response(200, json=[self.row(seq) for seq in range(501, 1001)])
            return httpx.Response(503)
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            state = market.refresh(self.root, 3, self.now, client=client, include_stock=False)
            market.refresh(self.root, 3, self.now + 1, client=client)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(state["events"]), 500)
        self.assertEqual(state["sample"]["partial_error"], "HTTPStatusError")

    def test_deadline_does_not_start_more_pages(self):
        with patch("market_evidence.time.monotonic", side_effect=[0, 20]), \
                patch("market_evidence.fetch_json", return_value=[self.row(i) for i in range(501, 1001)]) as fetch:
            state = market.refresh(self.root, 3, self.now, client=object(), deadline_seconds=8, include_stock=False)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(state["sample"]["partial_error"], "deadline")

    def test_stream_has_short_timeout_and_total_deadline(self):
        deadlines = []
        def respond(req):
            deadlines.append(req.extensions["timeout"])
            return httpx.Response(200, json=[self.row(1)])
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            market.refresh(self.root, 3, self.now, client=client, deadline_seconds=8)
        self.assertTrue(all(0 < value <= 2 for value in deadlines[0].values()))

    def test_empty_page_does_not_claim_reset_and_round_change_bypasses_old_slot(self):
        previous = {**self.state([self.row(900)]), "newest_seq": 900}
        atomic_json(self.root / ".market/market-evidence.json", previous)
        atomic_json(self.root / ".market/market-evidence-control.json", {"round": 2, "last_poll": self.now})
        with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=[]))) as client:
            state = market.refresh(self.root, 3, self.now, client=client, include_stock=False)
        self.assertIsNone(state["reset_at"])
        self.assertEqual(state["newest_seq"], 900)
        self.assertEqual(len(state["events"]), 1)

    def test_newest_records_slightly_after_capture_time_are_kept(self):
        near = {**self.row(1), "ts": self.now + .2}
        distant = {**self.row(2), "ts": self.now + 60}
        state = self.state([near, distant])
        profile = market.demand_profiles(state, self.now)["G8095ddd6"]
        self.assertEqual(profile["sales"], 1)
        self.assertEqual(profile["age_seconds"], 0)

    def test_explicit_null_and_real_numeric_terms_but_not_malformed_or_absent(self):
        rows = [self.row(1, warranty_hours=None), self.row(2, warranty_hours=24.0),
                self.row(3, warranty_hours=True), self.row(4, warranty_hours="6"),
                self.row(5, warranty_hours=169), self.row(6)]
        rows[-1]["data"].pop("warranty_hours")
        facts = market.normalize(rows, 3, self.now)
        self.assertEqual(facts[0]["warranty_hours"], 0)
        self.assertEqual(facts[1]["warranty_hours"], 24)
        for fact in facts[2:]:
            self.assertNotIn("warranty_hours", fact)
        self.assertTrue(facts[0]["claim_honest"])

    def test_profile_counts_observed_orders_deduplicates_and_reports_concentration(self):
        rows = [self.row(1, buyer="repeat"), self.row(2, buyer="repeat"),
                self.row(3, buyer="other"), self.row(4, order="o-3", buyer="other")]
        state = self.state(rows)
        state["events"].append(dict(state["events"][0]))
        profile = market.demand_profiles(state, self.now)["G8095ddd6"]
        self.assertEqual(profile["sales"], 3)
        self.assertEqual(profile["buyers"], 2)
        self.assertAlmostEqual(profile["max_buyer_share"], 2 / 3)
        self.assertEqual(profile["shops"], 2)
        self.assertEqual(profile["first_ts"], self.now - 30)
        self.assertEqual(profile["comparables"][0]["samples"], 3)
        self.assertIn("sampled", profile["coverage"])

    def test_profiles_expire_and_missing_buyer_is_not_diverse_demand(self):
        state = self.state([self.row(i, buyer=None) for i in range(1, 4)])
        profile = market.demand_profiles(state, self.now)["G8095ddd6"]
        self.assertEqual(profile["buyers"], 0)
        self.assertIsNone(profile["max_buyer_share"])
        self.assertEqual(market.demand_profiles(state, self.now + 3601), {})
        state["updated_at"] = self.now + 4000
        self.assertEqual(market.demand_profiles(state, self.now + 4000), {})

    def test_comparables_exact_terms_exclude_false_claims_and_fingerprint_is_stable(self):
        state = self.state([self.row(i) for i in range(1, 4)] +
                           [self.row(4, amount=1, claim_honest=False),
                            self.row(5, amount=1, grade="B"), self.row(6, amount=1, warranty_hours=3)])
        product = {"sku": "G8095ddd6", "grade": "A"}
        summary = market.comparable_price(state, product, 6, self.now)
        self.assertEqual(summary["samples"], 3)
        self.assertEqual(summary["price"], 650000)
        self.assertEqual(summary["evidence_id"],
                         market.comparable_price(state, product, 6, self.now + 500)["evidence_id"])
        state["events"].reverse()
        self.assertEqual(summary["evidence_id"],
                         market.comparable_price(state, product, 6, self.now)["evidence_id"])

    def test_one_stock_lookup_shares_budget_and_profiles_observed_stock_only(self):
        calls = []
        stock = {"round": 3, "shops": [
            {"name": "a", "stock": {"G8095ddd6": 1, "G03d0199f": 0}, "pubkey": "PRIVATE_UNUSED"},
            {"name": "b", "stock": {"G8095ddd6": 2}}]}
        def respond(req):
            calls.append(str(req.url))
            payload = stock if req.url.path == "/api/shops" else [self.row(i) for i in range(1, 4)]
            return httpx.Response(200, json=payload)
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            state = market.refresh(self.root, 3, self.now, client=client)
        self.assertEqual(len(calls), 2)
        profile = market.demand_profiles(state, self.now)["G8095ddd6"]
        self.assertEqual(profile["observed_stock"]["units"], 3)
        self.assertEqual(profile["observed_stock"]["shops"], 2)
        self.assertEqual(profile["sample_to_stock_ratio"], 1)
        self.assertNotIn("G03d0199f", state["market_stock"]["by_sku"])
        self.assertNotIn("PRIVATE_UNUSED", json.dumps(state))

    def test_failed_stock_refresh_preserves_timestamp_until_expiry_not_zero(self):
        rows = [self.row(i) for i in range(1, 4)]
        stock = {"round": 3, "shops": [{"name": "a", "stock": {"G8095ddd6": 2}}]}
        with patch("market_evidence.fetch_json", side_effect=[rows, stock]):
            first = market.refresh(self.root, 3, self.now, client=object())
        with patch("market_evidence.fetch_json", side_effect=[rows, ValueError("unavailable")]):
            second = market.refresh(self.root, 3, self.now + 901, client=object())
        self.assertEqual(second["market_stock"]["observed_at"], self.now)
        profile = market.demand_profiles(second, self.now + 901)["G8095ddd6"]
        self.assertEqual(profile["observed_stock"]["age_seconds"], 901)
        self.assertEqual(profile["observed_stock"]["units"], 2)
        state = self.state(rows)
        state["market_stock"] = {**first["market_stock"], "observed_at": self.now - 3601}
        self.assertIsNone(market.demand_profiles(state, self.now)["G8095ddd6"]["observed_stock"])
        self.assertIsNone(market.demand_profiles(state, self.now)["G8095ddd6"]["sample_to_stock_ratio"])

    def test_stock_invalid_round_values_or_duplicate_shops_never_become_zero(self):
        for stock in ({"round": 2, "shops": []},
                      {"round": 3, "shops": [{"name": "a", "stock": {"G8095ddd6": True}}]},
                      {"round": 3, "shops": [{"name": "a", "stock": {}}, {"name": "a", "stock": {}}]}):
            with self.assertRaises(ValueError):
                market.normalize_stock(stock, 3, self.now)

    def test_failed_prewarm_can_retry_at_opening_once_without_repeated_gets(self):
        calls = []
        def respond(req):
            calls.append(str(req.url))
            if len(calls) == 1:
                return httpx.Response(503)
            return httpx.Response(200, json=[self.row(i) for i in range(1, 4)])
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            self.assertIsNone(market.refresh(self.root, 3, self.now - 120, client=client, include_stock=False))
            # Ordinary calls remain throttled even after failure.
            self.assertIsNone(market.refresh(self.root, 3, self.now, client=client, include_stock=False))
            state = market.refresh(self.root, 3, self.now, client=client, include_stock=False,
                                   opening_retry=self.now)
            again = market.refresh(self.root, 3, self.now + 30, client=client, include_stock=False,
                                   opening_retry=self.now)
        self.assertEqual(len(calls), 2)
        self.assertEqual(state, again)
        self.assertEqual(market.demand_profiles(state, self.now)["G8095ddd6"]["sales"], 3)

    def test_failed_opening_retry_marker_survives_normal_polls_and_restart(self):
        with patch("market_evidence.fetch_json", side_effect=ValueError("offline")) as fetch:
            market.refresh(self.root, 3, self.now, client=object(), include_stock=False, opening_retry=self.now)
            market.refresh(self.root, 3, self.now + 1, client=object(), include_stock=False, opening_retry=self.now)
            self.assertEqual(fetch.call_count, 1)
            market.refresh(self.root, 3, self.now + 901, client=object(), include_stock=False)
            market.refresh(self.root, 3, self.now + 902, client=object(), include_stock=False, opening_retry=self.now)
            self.assertEqual(fetch.call_count, 2)
            market.refresh(self.root, 3, self.now + 1200, client=object(), include_stock=False,
                           opening_retry=self.now + 1200)
            market.refresh(self.root, 3, self.now + 1201, client=object(), include_stock=False,
                           opening_retry=self.now + 1200)
            self.assertEqual(fetch.call_count, 3)

    def test_successful_prewarm_does_not_add_opening_request_when_retry_omitted(self):
        rows = [{**self.row(i), "ts": self.now - 150} for i in range(1, 4)]
        with patch("market_evidence.fetch_json", return_value=rows) as fetch:
            first = market.refresh(self.root, 3, self.now - 120, client=object(), include_stock=False)
            again = market.refresh(self.root, 3, self.now, client=object(), include_stock=False)
        self.assertEqual(first, again)
        self.assertEqual(fetch.call_count, 1)

    def test_opening_retry_rejects_future_or_non_numeric_marker(self):
        for marker in (True, "window", self.now + 60, float("nan")):
            with self.assertRaises(ValueError):
                market.refresh(self.root, 3, self.now, client=object(), opening_retry=marker)


if __name__ == "__main__":
    unittest.main()
