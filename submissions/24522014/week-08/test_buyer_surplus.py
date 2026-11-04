"""Surplus counts settled deals only, follows the market's formula and keeps unknown paths apart."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import buyer_surplus as bs

SINCE = "20261104T000000000000Z"
START = bs.to_epoch(SINCE)


class Surplus(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for folder in (".market/buyer-deals", "logs/market-buyer", "logs/market-intel", "logs/buyer"):
            (self.root / folder).mkdir(parents=True)
        self.count = 0

    def deal(self, order, *, price=60, value=100, path=None, trail=None, paid=True, ledger=None, cap=80, budget=200,
             ask=None, name="thing", warranty=0, at=None, legacy=None, record=True):
        self.count += 1
        ledger = ledger if ledger is not None else 1000 + self.count
        at = at if at is not None else START + self.count
        row = {"order": order, "shop": "s", "item": "sku", "price": price, "paid": paid}
        if paid:
            row["ledger"] = ledger
        else:
            row["reason"] = "not paying: grade C"
        with (self.root / ".market/payments.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row) + "\n")
        (self.root / ".market/buyer-deals" / f"{ledger}.json").write_text(json.dumps(
            {"ledger": ledger, "purchased_at": at, "price": price, "value_at_purchase": value, "name": name,
             "warranty_hours": warranty, "order": order}), encoding="utf-8")
        if record:
            stamp = f"20261104T{self.count:06d}000000Z"
            log_name = f"{stamp}-log{self.count}"
            directory = self.root / "logs/buyer" / log_name
            directory.mkdir()
            if ask:
                (directory / "02.response.txt").write_text(json.dumps({"result": {"task": {"status": {
                    "state": "TASK_STATE_INPUT_REQUIRED", "message": {"parts": [{"text": f"asking {ask}."}]}}}}}), encoding="utf-8")
            if legacy:
                (directory / "decision-01.json").write_text(json.dumps(legacy), encoding="utf-8")
            content = {"order": order, "outcome": "purchased", "budget": cap, "order_budget": budget, "agreed_price": price,
                       "buyer_log_dir": "C:\\x\\logs\\buyer\\" + log_name, "receipt": {"price": price}}
            if trail is not None:
                content["decision_trail"] = trail
            (self.root / "logs/market-buyer" / f"{stamp}-a.json").write_text(json.dumps({"record": content}), encoding="utf-8")

    def offer(self, source="deterministic", code="rule_offer"):
        return {"source": source, "reason_code": code, "action": "offer", "price": 60}

    def report(self, **kwargs):
        return bs.report(self.root, SINCE, **kwargs)

    def test_only_paid_with_ledger_counts_and_cap_is_not_budget(self):
        self.deal("a", trail=[self.offer()], ask=75)
        self.deal("refused", paid=False, trail=[self.offer()])
        result = self.report()
        self.assertEqual(result["window"]["deals"], 1)
        row = result["by_path"]["deterministic:rule_offer"]
        self.assertEqual((row["deals"], row["surplus_sum"], row["surplus_median"]), (1, 40, 40))
        self.assertEqual(row["price_per_cap_median"], 0.75)
        self.assertEqual(row["price_per_order_budget_median"], 0.3)
        self.assertEqual(row["price_per_first_ask_median"], 0.8)
        self.assertEqual(row["surplus_per_list_median"], 0.4)

    def test_paths_are_separated_and_old_logs_stay_labeled(self):
        self.deal("rule", trail=[self.offer()])
        self.deal("model", trail=[self.offer("model", "model_offer")])
        self.deal("both", trail=[self.offer(), self.offer("model", "model_offer")])
        self.deal("old_model", legacy={"status": "ok", "attempts": [{"response": {"action": "offer", "price": 60}}]})
        self.deal("old_rule", legacy={"source": "deterministic", "decision": {"action": "offer", "price": 60}})
        self.deal("old_none")
        self.deal("no_record", record=False)
        paths = {k: v["deals"] for k, v in self.report()["by_path"].items()}
        self.assertEqual(paths, {"deterministic:rule_offer": 1, "model:model_offer": 2, "legacy_file:model": 1,
                                 "legacy_file:deterministic": 1, bs.UNKNOWN_PATH: 2})

    def test_counteroffers_are_a_separate_path(self):
        counter = self.offer("deterministic", "rule_counter_offer")
        self.deal("rule", trail=[self.offer()])
        self.deal("counter", trail=[counter])
        self.deal("counter_then_lower_ask", trail=[counter, self.offer()])
        paths = {k: v["deals"] for k, v in self.report()["by_path"].items()}
        self.assertEqual(paths, {"deterministic:rule_offer": 1, "deterministic:rule_counter_offer": 1,
                                 "deterministic:rule_offer_after_counter": 1})

    def test_broken_goods_follow_the_contract_formula(self):
        self.deal("no_cover", price=60, value=100, name="lamp", at=START + 10, trail=[self.offer()])
        self.deal("covered", price=60, value=100, name="desk", warranty=3, at=START + 20, trail=[self.offer()])
        self.deal("late", price=60, value=100, name="sofa", warranty=1, at=START + 30, trail=[self.offer()])
        with (self.root / ".market/broken.jsonl").open("w", encoding="utf-8") as stream:
            for name, at in (("lamp", START + 3600), ("desk", START + 3600), ("sofa", START + 2 * 3600 + 30)):
                stream.write(json.dumps({"name": name, "broke_at": at}) + "\n")
        row = self.report()["by_path"]["deterministic:rule_offer"]
        self.assertEqual(row["broken"], 3)
        self.assertEqual(row["surplus_sum"], -60 + 0 - 60)

    def test_ambiguous_breakage_is_counted_not_guessed(self):
        self.deal("a", name="same", at=START + 1, trail=[self.offer()])
        self.deal("b", name="same", at=START + 2, trail=[self.offer()])
        (self.root / ".market/broken.jsonl").write_text(json.dumps({"name": "same", "broke_at": START + 100}) + "\n", encoding="utf-8")
        deals, unmatched = bs.settled_deals(self.root)
        self.assertEqual([(e["candidates"], e["max_overstatement_if_covered"]) for e in unmatched], [(2, 40)])
        self.assertFalse(any(d["broken"] for d in deals))

    def test_a_fault_tied_by_unit_resolves_a_shared_name(self):
        self.deal("a", name="same", at=START + 1, trail=[self.offer()])
        self.deal("b", name="same", at=START + 2, warranty=3, trail=[self.offer()], ledger=77)
        path = self.root / ".market/buyer-deals/77.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved["fault"] = {"at": START + 100}
        path.write_text(json.dumps(saved), encoding="utf-8")
        (self.root / ".market/broken.jsonl").write_text(json.dumps({"name": "same", "broke_at": START + 100}) + "\n", encoding="utf-8")
        deals, unmatched = bs.settled_deals(self.root)
        self.assertEqual(unmatched, [])
        self.assertEqual({d["ledger"]: d["estimated_surplus"] for d in deals if d["broken"]}, {77: 0})

    def test_recent_goods_are_flagged_as_still_able_to_break(self):
        (self.root / ".market/catalog.json").write_text(json.dumps({"g": {"kind": "goods"}, "f": {"kind": "food"}}), encoding="utf-8")
        for order, ledger, sku, at in (("new", 1, "g", START + 100000), ("old", 2, "g", START + 10), ("food", 3, "f", START + 100000)):
            self.deal(order, ledger=ledger, at=at, trail=[self.offer()])
            path = self.root / f".market/buyer-deals/{ledger}.json"
            saved = json.loads(path.read_text(encoding="utf-8"))
            saved["sku"] = sku
            path.write_text(json.dumps(saved), encoding="utf-8")
        result = bs.report(self.root, SINCE, now=START + 100000 + 3600)
        self.assertEqual(result["goods_still_within_break_window"]["deals"], 1)

    def test_reconciliation_and_official_delta_are_side_by_side(self):
        self.deal("a", at=START + 5, trail=[self.offer()])
        self.deal("b", at=START + 50, trail=[self.offer()])
        for index, (at, surplus, deals) in enumerate(((START, 100, 1), (START + 10, 140, 2))):
            (self.root / "logs/market-intel" / f"{index}.json").write_text(json.dumps({"observed_at": at, "competition": {"own": {"buyers": {
                "surplus": surplus, "deals": deals, "served": deals, "all": 0.5}}}}), encoding="utf-8")
        result = self.report()
        self.assertEqual(result["official_same_window"]["official_surplus_delta"], 40)
        self.assertEqual(result["official_same_window"]["local_estimate_same_interval"], 40)
        self.assertIn("not a causal", result["official_same_window"]["note"])
        self.assertEqual(result["reconciliation"]["estimated_surplus"], 40)
        self.assertEqual(result["reconciliation"]["difference"], 40 - 140)
        self.assertEqual(result["status"].split(";")[0], "estimate")

    def test_acceptance_buckets_use_the_first_labeled_ask(self):
        self.deal("a", price=95, ask=100, trail=[self.offer()])
        self.deal("b", price=100, ask=100, trail=[self.offer()])
        self.deal("c", price=80, ask=100, paid=False, trail=[self.offer()])
        result = self.report()["last_offer_per_first_ask"]
        self.assertEqual(result["0.95-1.00"], {"offers": 1, "receipt": 1, "paid": 1})
        self.assertEqual(result["at_or_above_ask"], {"offers": 1, "receipt": 1, "paid": 1})
        self.assertEqual(result["below_0.85"], {"offers": 1, "receipt": 1, "paid": 0})

    def test_time_window_applies_to_the_settlement(self):
        self.deal("a", at=START + 5, trail=[self.offer()])
        self.deal("b", at=START + 500, trail=[self.offer()])
        self.assertEqual(bs.report(self.root, SINCE, "20261104T000100000000Z")["window"]["deals"], 1)


if __name__ == "__main__":
    unittest.main()
