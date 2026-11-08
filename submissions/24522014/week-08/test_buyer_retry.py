"""One retry at another shop, inside our own run, for an order the first shop lost to a short shelf life or an unacceptable grade
in its quote, or to a refusal at the first message. Approved 11-08. Refusals the kit makes at payment come after our run has
returned its one result, so they are out of reach and never retried. Off unless buyer_support.json says so."""

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import buyer_support as support
import market_buyer
from market_state import read

T = 1793760000
CFG = {**support.config(), "mode": "balanced", "max_attempts_per_utc_day": 100, "min_attempt_interval_seconds": 10, "pair_cooldown_seconds": 300}
ON = {"min_order_seconds_left": 180, "max_elapsed_seconds": 150}


def shop(name):
    return {"name": name, "url": "https://shops/" + name, "reviews": [], "offers": [{"sku": "G1", "qty": 2}]}


class Decision(unittest.TestCase):
    def test_only_the_three_in_run_failures_are_retried(self):
        order = {"expires": T + 600}
        for record, expected in (({"outcome": "no_deal", "failure_reason": "quote_shelf_life_short"}, "quote_shelf_life_short"),
                                 ({"outcome": "no_deal", "failure_reason": "quote_grade_unacceptable"}, "quote_grade_unacceptable"),
                                 ({"outcome": "no_deal", "failure_reason": "seller_rejected", "refused_at_open": True}, "refused_at_open")):
            with self.subTest(record=record):
                self.assertEqual(support.should_retry(record, order, 5, T, ON), (expected, "retry"))
        for record in ({"outcome": "purchased", "receipt": {"price": 1}}, {"outcome": "no_deal", "failure_reason": "model_quota_hour"},
                       {"outcome": "no_deal", "failure_reason": "seller_rejected", "refused_at_open": False},
                       {"outcome": "transport_error", "failure_reason": "transport_error"}, {"outcome": "no_deal", "failure_reason": None}):
            with self.subTest(record=record):
                self.assertEqual(support.should_retry(record, order, 5, T, ON), (None, "not_retryable"))

    def test_order_time_and_run_time_bound_the_retry(self):
        record = {"outcome": "no_deal", "failure_reason": "quote_grade_unacceptable"}
        self.assertEqual(support.should_retry(record, {"expires": T + 180}, 5, T, ON)[1], "retry")
        self.assertEqual(support.should_retry(record, {"expires": T + 179}, 5, T, ON), (None, "order_closing"))
        self.assertEqual(support.should_retry(record, {}, 5, T, ON), (None, "order_closing"))
        self.assertEqual(support.should_retry(record, {"expires": T + 600}, 150, T, ON)[1], "retry")
        self.assertEqual(support.should_retry(record, {"expires": T + 600}, 151, T, ON), (None, "elapsed"))

    def test_off_when_missing_broken_or_out_of_bounds(self):
        self.assertEqual(support.should_retry({"outcome": "no_deal", "failure_reason": "quote_grade_unacceptable"}, {"expires": T + 600}, 5, T, None), (None, "off"))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "b.json"
            self.assertIsNone(support.retry_settings(path))
            for section in (None, [], {"enabled": "true", **ON}, {"enabled": False, **ON}, {"enabled": True}, {"enabled": True, **ON, "min_order_seconds_left": 30},
                            {"enabled": True, **ON, "max_elapsed_seconds": 251}, {"enabled": True, **ON, "max_elapsed_seconds": "150"}):
                path.write_text(json.dumps({"retry_other_shop": section}), encoding="utf-8")
                self.assertIsNone(support.retry_settings(path), section)
            path.write_text(json.dumps({"retry_other_shop": {"enabled": True, **ON}}), encoding="utf-8")
            self.assertEqual(support.retry_settings(path), ON)
            path.write_text("{", encoding="utf-8")
            self.assertIsNone(support.retry_settings(path))

    def test_the_shipped_setting_is_on_with_the_approved_bounds(self):
        # Shipped off at 09:24:40; switched on 09:26:05 KST 11-08 on the orchestrator's decision after the tests.
        self.assertEqual(support.retry_settings(), ON)
        section = json.loads((support.ROOT / "buyer_support.json").read_text(encoding="utf-8"))["retry_other_shop"]
        self.assertIs(section["enabled"], True)


class Reservation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.md = Path(self.tmp.name)

    def reserve(self, order, name, at, retry_of=None, sku="G1"):
        return support.reserve(self.md, order, shop(name), sku, 80000, False, CFG, at, retry_of=retry_of)

    def test_one_retry_per_order_at_another_shop_and_no_third(self):
        self.assertEqual(self.reserve("o1", "a", T), "reserved")
        self.assertEqual(self.reserve("o1", "b", T + 1), "duplicate_order")  # not marked as a retry
        self.assertEqual(self.reserve("o1", "b", T + 1, retry_of="a"), "reserved")  # inside the 10 s interval: same order
        self.assertEqual(self.reserve("o1", "c", T + 2, retry_of="b"), "duplicate_order")
        self.assertEqual(self.reserve("o1", "c", T + 2, retry_of="a"), "duplicate_order")
        attempts = read(self.md / "buyer-support-state.json")["attempts"]
        self.assertEqual([(r["shop"], r.get("retry_of")) for r in attempts], [("a", None), ("b", "a")])

    def test_a_retry_needs_the_first_attempt_it_names(self):
        self.assertEqual(self.reserve("o1", "b", T, retry_of="a"), "duplicate_order")
        self.assertEqual(self.reserve("o2", "a", T + 100), "reserved")
        self.assertEqual(self.reserve("o3", "b", T + 200, retry_of="a"), "duplicate_order")

    def test_a_new_order_still_waits_the_interval_and_pairs_still_cool_down(self):
        self.assertEqual(self.reserve("o1", "a", T), "reserved")
        self.assertEqual(self.reserve("o1", "b", T + 1, retry_of="a"), "reserved")
        self.assertEqual(self.reserve("o2", "c", T + 5), "attempt_cooldown")
        self.assertEqual(self.reserve("o2", "b", T + 60), "pair_attempt_cooldown")
        self.assertEqual(self.reserve("o3", "a", T + 400, retry_of=None), "reserved")
        self.assertEqual(self.reserve("o3", "b", T + 401, retry_of="a"), "reserved")  # b/G1 last tried at T+1, 400 s ago

    def test_the_retry_counts_against_the_daily_limit(self):
        settings = dict(CFG, max_attempts_per_utc_day=1)
        self.assertEqual(support.reserve(self.md, "o1", shop("a"), "G1", 80000, False, settings, T), "reserved")
        self.assertEqual(support.reserve(self.md, "o1", shop("b"), "G1", 80000, False, settings, T + 1, retry_of="a"), "daily_attempt_limit")

    def test_the_model_is_reserved_at_most_once_per_order(self):
        with patch.object(support, "model_gate_enabled", return_value=True), patch.object(support, "config", return_value=CFG):
            self.assertEqual(support.model_reservation(self.md, "o1", T), "reserved")
            self.assertEqual(support.model_reservation(self.md, "o1", T + 1), "model_order_already_reserved")


class Prepare(unittest.TestCase):
    def test_the_retry_choice_leaves_out_the_first_shop(self):
        with tempfile.TemporaryDirectory() as folder:
            md = Path(folder)
            seen = []
            def choose(shops, *rest, **kw):
                seen.append([s["name"] for s in shops])
                return None, None, 1, "none", []
            with patch.object(support, "config", return_value=CFG), patch.object(support, "choose", side_effect=choose), \
                    patch("reference_relief.public_rows_without_deals", return_value=[]):
                support.prepare(md, [shop("a"), shop("b"), shop("A")], [], {}, 1000, {}, [], {}, {"order": "o1", "kind": "goods"}, T)
                support.reserve(md, "o1", shop("a"), "G1", 1, False, CFG, T)
                _, _, _, _, _, decision = support.prepare(md, [shop("a"), shop("b"), shop("A")], [], {}, 1000, {}, [], {}, {"order": "o1", "kind": "goods"}, T + 1, retry_of="a")
        self.assertEqual(seen, [["a", "b", "A"], ["b"]])
        self.assertEqual(decision["retry_of"], "a")


class Main(unittest.TestCase):
    """market_buyer.main: one result line for the kit, two attempts at most, the second at another shop."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.md = root / ".market"
        self.md.mkdir()
        (self.md / "catalog.json").write_text(json.dumps({"G1": {"kind": "goods", "list_price": 100000}}), encoding="utf-8")
        self.order = {"order": "o1", "item": "G1", "budget": 90000, "round": 3, "kind": "goods", "acceptable": ["G1"], "expires": T + 600}
        (self.md / "order.json").write_text(json.dumps(self.order), encoding="utf-8")
        (self.md / "shops.json").write_text(json.dumps([shop("a"), shop("b")]), encoding="utf-8")
        self.root = root
        self.prepares, self.trades = [], []

    def run_main(self, outcomes, settings=ON, picks=("a", "b")):
        picks = list(picks)
        def prepare(md, shops, memory, catalog, budget, policy, portfolio, snapshot, order, retry_of=None):
            self.prepares.append({"shops": [s["name"] for s in shops], "retry_of": retry_of, "memory": len(memory)})
            name = picks.pop(0) if picks else None
            chosen = next((s for s in shops if s["name"] == name), None)
            evidence = [{"shop": name, "sku": "G1", "warranty_required": 0, "cap_without_floor": 80000}] if chosen else []
            return chosen, "G1" if chosen else None, 70000, f"pick {name}", evidence, {"status": "reserved" if chosen else "no_useful_research", "max_turns": 3, "fast_rules": False, "retry_of": retry_of}
        def trade(shop, item, budget, **options):
            self.trades.append({"shop": shop["name"], "accept": dict(options["accept_conditions"])})
            outcome = outcomes.pop(0)
            purchased = outcome == "purchased"
            # A real record carries a timestamp; the memory loader refuses one without it.
            record = {"timestamp": "2026-11-05T00:00:00+00:00", "shop_name": shop["name"], "url_at_time": shop["url"], "item": item, "outcome": "purchased" if purchased else "no_deal",
                      "receipt": {"price": 69000} if purchased else None, "decision_trail": [], "refused_at_open": outcome == "refused_at_open",
                      "failure_reason": None if purchased else ("seller_rejected" if outcome == "refused_at_open" else outcome)}
            return {"shop": shop["url"], "receipt": record["receipt"], "stars": 5 if purchased else 3, "review": "r"}, record
        argv = ["market_buyer.py", "--shops", str(self.md / "shops.json"), "--item", "G1", "--budget", "90000", "--order", str(self.md / "order.json"), "--mode", "codex"]
        out = io.StringIO()
        with patch.object(sys, "argv", argv), patch.object(market_buyer, "ROOT", self.root), patch.object(market_buyer, "prepare_support", side_effect=prepare), \
                patch.object(market_buyer, "trade", side_effect=trade), patch.object(market_buyer, "fresh", return_value={}), \
                patch.object(market_buyer, "retry_settings", return_value=settings), patch.object(market_buyer.time, "time", return_value=T), redirect_stdout(out):
            market_buyer.main()
        lines = [l for l in out.getvalue().splitlines() if l.startswith("{")]
        logs = sorted((self.root / "logs/market-buyer").glob("*.json"))
        return lines, json.loads(logs[-1].read_text(encoding="utf-8"))

    def memory(self):
        path = self.md / "r3-buyer-memory.jsonl"
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def test_a_grade_failure_is_retried_once_at_another_shop_and_the_kit_gets_one_line(self):
        lines, log = self.run_main(["quote_grade_unacceptable", "purchased"])
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["shop"], "https://shops/b")
        self.assertEqual([t["shop"] for t in self.trades], ["a", "b"])
        self.assertEqual(self.prepares[1]["retry_of"], "a")
        self.assertEqual(self.prepares[1]["memory"], self.prepares[0]["memory"] + 1)  # the first try is seen by refusal_skip
        self.assertEqual([m["shop_name"] for m in self.memory()], ["a", "b"])
        self.assertEqual(log["record"]["retry"], {"status": "retry", "reason": "quote_grade_unacceptable", "of": "a"})
        self.assertEqual(log["first_attempt"]["record"]["shop_name"], "a")

    def test_never_more_than_two_trades(self):
        lines, log = self.run_main(["refused_at_open", "refused_at_open"], picks=("a", "b", "a"))
        self.assertEqual([t["shop"] for t in self.trades], ["a", "b"])
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["receipt"], None)

    def test_off_or_not_retryable_or_a_purchase_means_one_trade(self):
        for outcomes, settings, status in ((["quote_grade_unacceptable"], None, "off"), (["model_quota_hour"], ON, "not_retryable"), (["purchased"], ON, "not_retryable")):
            with self.subTest(outcomes=outcomes, settings=settings):
                self.trades.clear()
                lines, log = self.run_main(list(outcomes), settings)
                self.assertEqual(len(self.trades), 1)
                self.assertEqual(len(lines), 1)
                self.assertEqual(log["record"]["retry"]["status"], status)
                self.assertNotIn("first_attempt", log)

    def test_no_second_candidate_keeps_the_first_result(self):
        lines, log = self.run_main(["quote_shelf_life_short"], picks=("a",))
        self.assertEqual(len(self.trades), 1)
        self.assertEqual(json.loads(lines[0])["shop"], "https://shops/a")
        self.assertEqual(log["record"]["retry"]["status"], "retry_no_useful_research")

    def test_each_attempt_gets_its_own_accept_conditions(self):
        def prepare(md, shops, memory, catalog, budget, policy, portfolio, snapshot, order, retry_of=None):
            chosen = shops[0] if retry_of is None else next(s for s in shops if s["name"] == "b")
            warranty = 6 if retry_of is None else 0
            return chosen, "G1", 70000, "p", [{"shop": chosen["name"], "sku": "G1", "warranty_required": warranty, "cap_without_floor": 80000}], {"status": "reserved", "max_turns": 3, "fast_rules": False}
        trades = []
        def trade(shop, item, budget, **options):
            trades.append(options["accept_conditions"].get("warranty_hours"))
            return {"shop": shop["url"], "receipt": None, "stars": 3, "review": "r"}, {"shop_name": shop["name"], "outcome": "no_deal", "failure_reason": "quote_grade_unacceptable", "decision_trail": []}
        argv = ["market_buyer.py", "--shops", str(self.md / "shops.json"), "--item", "G1", "--budget", "90000", "--order", str(self.md / "order.json")]
        with patch.object(sys, "argv", argv), patch.object(market_buyer, "ROOT", self.root), patch.object(market_buyer, "prepare_support", side_effect=prepare), \
                patch.object(market_buyer, "trade", side_effect=trade), patch.object(market_buyer, "fresh", return_value={}), \
                patch.object(market_buyer, "retry_settings", return_value=ON), patch.object(market_buyer.time, "time", return_value=T), redirect_stdout(io.StringIO()):
            market_buyer.main()
        self.assertEqual(trades[0], 6)
        self.assertNotEqual(trades[1], 6)


if __name__ == "__main__":
    unittest.main()
