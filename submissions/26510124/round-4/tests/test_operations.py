from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from local_support import ROOT, offline_context
from role_runtime import MultiRoleMind, SharedToolbox, RoleToolbox
from strategy.control import PolicyController
from strategy.runtime import policy, role_context, seller_terms, closing_terms


def evidence(now, version=0, profit=1000):
    return {"round": 4, "observed_at": now, "source": "https://fixture.invalid/verified-observations",
            "cash": 10000, "next_supply_at": now + 300,
            "fees": {"seller_bps": 0, "seller_fixed": 0, "buyer_bps": 0, "buyer_fixed": 0},
            "units": [{"id": "u1", "sku": "a", "grade": "A", "kind": "goods", "cost": 100,
                       "available": True, "base_opening_price": 200},
                      {"id": "u2", "sku": "b", "grade": "B", "kind": "goods", "cost": 100,
                       "available": True, "base_opening_price": 180}],
            "sales": [{"receipt_id": "s1", "sku": "b", "status": "settled", "at": now - 10}],
            "operations_metrics": {"window_start": now - 10800, "window_end": now,
                                   "policy_version": version, "completed_tasks": 20,
                                   "settlements": 5, "refunds": 0, "net_profit": profit}}


def write_evidence(root, data):
    path = Path(root) / "state/shop/evidence.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = 20000
        self.data = evidence(self.now)
        self.controller = PolicyController(self.root, policy())

    def proposal(self, changes=None):
        ready = self.controller.prepare(self.data, self.now)
        self.assertTrue(ready["ready"], ready)
        return {"base_version": ready["version"], "evidence_id": ready["evidence_id"],
                "changes": changes or {"opening_adjust_bps": {"a": -100},
                                        "closing_start_offer": 1, "restock_priority": ["b"]}}

    def activate(self):
        result = self.controller.apply(self.proposal(), self.data, self.now)
        self.assertEqual(result["status"], "applied", result)

    def test_applied_changes_reach_numeric_prices_closing_and_inventory_order(self):
        self.activate()
        write_evidence(self.root, self.data)
        context = json.loads(role_context(self.root, "shop", self.now))
        self.assertEqual(context["operations_policy"]["version"], 1)
        terms = context["calculated_terms"][0]
        self.assertEqual(terms["opening_price"], 198)
        self.assertEqual(terms["price_floor"], 110)
        self.assertEqual(terms["closing"]["start_offer"], 1)
        self.assertEqual(context["inventory_analysis"]["targets"][0]["sku"], "b")
        self.assertEqual(context["policy"]["restock"]["cash_reserve_percent"], 40)
        restarted = PolicyController(self.root, policy())
        self.assertEqual(restarted.current(self.data, self.now)["active"]["changes"]["closing_start_offer"], 1)

    def test_opening_adjustment_cannot_break_floor_or_compound(self):
        self.data["units"][0]["base_opening_price"] = 110
        self.activate()
        write_evidence(self.root, self.data)
        for _ in range(3):
            terms = json.loads(role_context(self.root, "shop", self.now))["calculated_terms"][0]
            self.assertEqual(terms["opening_price"], 110)

    def test_first_offer_closing_does_not_invent_an_improvement_or_crash(self):
        cfg = policy()["shop"]; cfg["closing"]["start_offer"] = 1
        product = {"kind": "goods", "grade": "B", "cost": 100, "supplied_at": 199999}
        terms = seller_terms(product, self.data["fees"], cfg)
        deal = {"terms": terms, "offers": [104]}
        fresh = closing_terms(product, deal, 200000)
        self.assertEqual(fresh["stage"], "close")
        self.assertIsNone(fresh["discount_reason"])
        self.assertEqual(fresh["price_floor"], 105)
        aged = closing_terms(dict(product, supplied_at=0), deal, 200000)
        self.assertEqual(aged["discount_reason"], "aged_goods")
        self.assertEqual(aged["price_floor"], 102)

    def test_stale_insufficient_missing_and_old_version_data_do_not_reserve_calls(self):
        variants = [None, {}, dict(self.data, round=3), dict(self.data, observed_at=self.now - 901)]
        for key, value in (("completed_tasks", 19), ("settlements", 4), ("policy_version", 99), ("net_profit", None)):
            data = deepcopy(self.data); data["operations_metrics"][key] = value
            variants.append(data)
        for data in variants:
            self.assertFalse(self.controller.prepare(data, self.now)["ready"])
        self.assertFalse(self.controller.path.exists())

    def test_reserved_call_cooldown_survives_restart_and_fetch_timestamp_is_not_new_evidence(self):
        self.proposal()
        restarted = PolicyController(self.root, policy())
        self.assertFalse(restarted.prepare(self.data, self.now)["ready"])
        refreshed = deepcopy(self.data); refreshed["observed_at"] += 1
        self.assertEqual(restarted.evidence_id(refreshed), restarted.evidence_id(self.data))

    def test_rejects_out_of_scope_unbounded_and_unknown_targets(self):
        proposal = self.proposal()
        variants = [{"grade_margin_percent": {"A": 0}}, {"buyer": {"budget_cap_percent": 100}},
                    {"opening_adjust_bps": {"a": -201}}, {"opening_adjust_bps": {"a": True}},
                    {"opening_adjust_bps": {"unknown": -100}}, {"closing_start_offer": 0},
                    {"restock_priority": ["a"]}, {"restock_priority": ["b", "b"]},
                    {"opening_adjust_bps": {}}, {}]
        for changes in variants:
            with self.subTest(changes=changes):
                result = self.controller.apply(dict(proposal, changes=changes), self.data, self.now)
                self.assertEqual(result["status"], "rejected")
                self.assertIsNone(self.controller.current(self.data, self.now)["active"])

    def test_compare_version_snapshot_ttl_and_duplicate_application(self):
        proposal = self.proposal()
        self.assertEqual(self.controller.apply(dict(proposal, base_version=9), self.data, self.now)["status"], "rejected")
        modified = deepcopy(self.data); modified["cash"] += 1
        self.assertEqual(self.controller.apply(proposal, modified, self.now)["status"], "rejected")
        self.assertEqual(self.controller.apply(proposal, self.data, self.now + 901)["status"], "rejected")
        self.assertEqual(self.controller.apply(proposal, self.data, self.now)["status"], "applied")
        self.assertEqual(self.controller.apply(proposal, self.data, self.now)["status"], "rejected")

    def test_regression_and_refund_increase_roll_back_without_a_model(self):
        self.activate()
        later = self.now + 10801
        measured = evidence(later, version=1, profit=500)
        state = self.controller.current(measured, later)
        self.assertEqual(state["version"], 2)
        self.assertIsNone(state["active"])
        self.assertIn('"reason": "performance_regressed"', (self.root / "logs/operations.jsonl").read_text())

    def test_good_result_stays_until_expiry_then_restores_baseline_without_evidence(self):
        self.activate()
        later = self.now + 10801
        self.assertIsNotNone(self.controller.current(evidence(later, 1, 1200), later)["active"])
        cfg, status = self.controller.effective(None, self.now + 21600)
        self.assertIsNone(status["active"])
        self.assertEqual(cfg["shop"]["closing"]["start_offer"], 2)
        self.assertNotIn("adjust_bps", cfg["shop"]["opening"])

    def test_higher_refund_share_rolls_back_even_if_profit_improves(self):
        self.activate()
        later = self.now + 10801
        measured = evidence(later, version=1, profit=1500)
        measured["operations_metrics"]["refunds"] = 1
        self.assertIsNone(self.controller.current(measured, later)["active"])

    def test_incomparable_window_is_not_evaluated_and_baseline_edit_rolls_back(self):
        self.activate()
        mixed = evidence(self.now + 10800, 0, -500)
        self.assertIsNotNone(self.controller.current(mixed, self.now + 10800)["active"])
        baseline = policy(); baseline["shop"]["opening"]["aged_markup_percent"] = 4
        self.assertIsNone(PolicyController(self.root, baseline).current(None, self.now + 10)["active"])

    def test_operations_decision_is_applied_and_next_forged_shop_call_consumes_it(self):
        root = self.root
        class DecisionModel:
            def __init__(self, agent, root):
                self.calls = 0
            def complete(self, messages, **kwargs):
                self.calls += 1
                prepared = json.loads(messages[1]["content"])
                return json.dumps({"thought": "fixture experiment", "action": "write_note", "args": {
                    "text": "Test a smaller opening premium", "policy_change": {
                        "base_version": prepared["version"], "evidence_id": prepared["evidence_id"],
                        "changes": {"opening_adjust_bps": {"a": -100}, "closing_start_offer": 1,
                                    "restock_priority": ["b"]}}}})
        ctx = offline_context(); ctx["addresses"] = {"shop": "fixture", "buyer": "fixture"}
        shared = SharedToolbox(root, ctx)
        agent = json.loads((ROOT / "agent.json").read_text())
        mind = MultiRoleMind(root, agent, None, shared, ctx, ["shop", "buyer"], model_factory=DecisionModel)
        write_evidence(root, self.data)
        shop = RoleToolbox(shared, "shop")
        # Real official forge/runner; no network/identity/wallet permission.
        forged = shop.forge({"name": "shop_policy_fixture", "description": "offline policy consumption fixture",
            "permission": {}, "logic": "def run(args, kit):\n p=args['_shop_policy']; return {'version':p['operations_policy']['version'], 'price':p['calculated_terms'][0]['opening_price'], 'start':p['policy']['closing']['start_offer'], 'priority':p['inventory_analysis']['targets'][0]['sku']}\n",
            "test": "def test(run, kit):\n p={'operations_policy':{'version':7}, 'calculated_terms':[{'opening_price':50}], 'policy':{'closing':{'start_offer':1}}, 'inventory_analysis':{'targets':[{'sku':'b'}]}}\n assert run({'_shop_policy':p},kit)['price']==50\n"})
        self.assertEqual(forged["test"], "passed")
        with patch("role_runtime.time.time", return_value=self.now), patch("strategy.runtime.time.time", return_value=self.now):
            before = shop.call("shop_policy_fixture", {})["result"]
            mind.minds["operations"].run_once([])
            after = shop.call("shop_policy_fixture", {"_shop_policy": "spoofed"})["result"]
            mind.minds["operations"].run_once([])
        self.assertEqual(before, {"version": 0, "price": 200, "start": 2, "priority": "a"})
        self.assertEqual(after, {"version": 1, "price": 198, "start": 1, "priority": "b"})
        self.assertEqual(mind.minds["operations"].raw_model.calls, 1)
        self.assertIn('"event": "applied"', (root / "logs/operations.jsonl").read_text())
        later = self.now + 21600
        write_evidence(root, evidence(later, 2))
        with patch("strategy.runtime.time.time", return_value=later):
            restored = shop.call("shop_policy_fixture", {})["result"]
        self.assertEqual(restored, dict(before, version=2))
        print("OFFLINE OPERATIONS CONSUMPTION:", json.dumps({"before": before, "applied": after, "expired": restored}))

    def test_hold_and_model_failure_do_not_change_policy_or_retry_model(self):
        class OutcomeModel:
            def __init__(self, agent, root):
                self.calls, self.fail = 0, False
            def complete(self, messages, **kwargs):
                self.calls += 1
                if self.fail:
                    raise ValueError("fixture model unavailable")
                return json.dumps({"thought": "hold", "action": "write_note", "args": {
                    "text": "No supported improvement", "policy_change": None}})
        for fail in (False, True):
            root = self.root / str(fail); root.mkdir()
            ctx = offline_context(); ctx["addresses"] = {"shop": "fixture"}
            shared = SharedToolbox(root, ctx)
            agent = json.loads((ROOT / "agent.json").read_text())
            mind = MultiRoleMind(root, agent, None, shared, ctx, ["shop"], model_factory=OutcomeModel)
            ops = mind.minds["operations"]; ops.raw_model.fail = fail
            write_evidence(root, self.data)
            with patch("role_runtime.time.time", return_value=self.now):
                ops.run_once([])
                ops.run_once([])
            self.assertEqual(ops.raw_model.calls, 1)
            self.assertIsNone(PolicyController(root, policy()).current(self.data, self.now)["active"])
