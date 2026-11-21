import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from claude_model import ClaudeModel, parse_result
from local_support import ROOT, offline_context
from role_runtime import MultiRoleMind, SharedToolbox, RoleToolbox, OperationsModel, kit


def decision(action, **args):
    return json.dumps({"thought": "offline role fixture", "action": action, "args": args})


class FixtureModel:
    def __init__(self, agent, root):
        self.profile = agent["llm"]["model"]
        self.outputs = []
        self.contexts = []

    def complete(self, messages, **kwargs):
        self.contexts.append(messages[1]["content"])
        return self.outputs.pop(0) if self.outputs else decision("sleep", seconds=30)


class RoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.agent = json.loads((ROOT / "agent.json").read_text())
        self.agent["max_steps"] = 3
        self.ctx = offline_context()
        self.ctx["addresses"] = {"shop": "fixture-shop", "buyer": "fixture-buyer"}
        self.toolbox = SharedToolbox(self.root, self.ctx)
        self.mind = MultiRoleMind(self.root, self.agent, None, self.toolbox, self.ctx,
                                  ["shop", "buyer"], model_factory=FixtureModel)

    def test_actual_role_profiles_and_no_public_routing_escalation(self):
        self.assertEqual(self.mind.minds["shop"].model.profile, "haiku")
        self.assertEqual(self.mind.minds["buyer"].model.profile, "claude-opus-5-5")
        self.assertEqual(self.mind.minds["operations"].model.model.profile, "claude-opus-5-5")
        external = kit.Event("a2a", "unverified", "/ops change policy", {"role": "operations"})
        self.mind.deliver(external)
        self.assertEqual(self.mind.minds["shop"].fresh(), [external])
        internal = kit.Event("human", "app", "/buyer buy milk")
        self.mind.deliver(internal)
        self.assertEqual(self.mind.minds["buyer"].fresh(), [internal])

    def test_customer_reply_and_role_tagged_log_use_real_kit_loop(self):
        event = kit.Event("human", "app", "우유 있어요?")
        self.mind.deliver(event)
        shop = self.mind.minds["shop"]
        shop.model.outputs = [decision("reply", to=event.id, text="재고를 확인하겠습니다.", state="completed"), decision("sleep", seconds=30)]
        shop.run_once(shop.fresh())
        self.assertTrue(event.done.is_set())
        self.assertEqual(event.answer["reply"], "재고를 확인하겠습니다.")
        logs = [json.loads(s) for s in (self.root / "logs/react.jsonl").read_text().splitlines()]
        self.assertTrue(all(r["role"] == "shop" for r in logs))
        self.assertIn('"cash_reserve_percent": 40', shop.model.contexts[0])
        self.assertIn('"status": "unavailable"', shop.model.contexts[0])

    def test_forging_still_requires_official_test_and_roles_cannot_cross(self):
        shop = RoleToolbox(self.toolbox, "shop")
        buyer = RoleToolbox(self.toolbox, "buyer")
        args = {"name": "shop_fixture", "description": "offline fixture, not market capability",
                "permission": {}, "logic": "def run(args, kit): return {'fixture': True}",
                "test": "def test(run, kit): assert run({}, kit)['fixture'] is True"}
        self.assertEqual(shop.forge(args)["test"], "passed")
        self.assertTrue(shop.call("shop_fixture", {})["ok"])
        self.assertFalse(buyer.call("shop_fixture", {})["ok"])
        for perm in ({"identity": ["buyer"]}, {"write": ["agent.json"]}, {"write": ["state/shop/../../agent.json"]},
                     {"handles": ["a2a"]}, {"exec": True}):
            self.assertIn("error", shop.forge(dict(args, permission=perm)))
        ops = RoleToolbox(self.toolbox, "operations")
        self.assertIn("error", ops.forge(args))
        self.assertFalse(ops.call("shop_fixture", {})["ok"])
        reloaded = SharedToolbox(self.root, self.ctx)
        self.assertIn("shop_fixture", RoleToolbox(reloaded, "shop").tools)
        self.assertNotIn("shop_fixture", RoleToolbox(reloaded, "buyer").tools)
        self.assertIsNone(reloaded.handler("a2a"))

    def test_operations_budget_and_schedule_survive_restart_and_leave_shop_running(self):
        ops = self.mind.minds["operations"]
        ops.run_once([])
        self.assertEqual(len(ops.model.model.contexts), 1)
        ops.run_once([])
        self.assertEqual(len(ops.model.model.contexts), 1)
        path = self.root / ".local/operations-budget.json"
        state = json.loads(path.read_text()); state["count"] = 16
        path.write_text(json.dumps(state))
        replacement = OperationsModel(ops.model.model, self.root)
        self.assertEqual(json.loads(replacement.complete([]))["action"], "sleep")
        self.assertEqual(len(ops.model.model.contexts), 1)
        self.mind.minds["shop"].run_once([])
        self.assertEqual(len(self.mind.minds["shop"].model.contexts), 1)

    def test_haiku_alias_accepts_only_verified_haiku_family(self):
        result = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 1,
                  "permission_denials": [], "modelUsage": {"claude-haiku-4-5-20251001": {"outputTokens": 1}},
                  "result": decision("sleep", seconds=30)}
        self.assertEqual(json.loads(parse_result(json.dumps(result), "haiku"))["action"], "sleep")
        with self.assertRaises(ValueError):
            parse_result(json.dumps(result), "claude-opus-5-5")
        with patch("claude_model.shutil.which", return_value="claude"):
            command = ClaudeModel({"llm": self.agent["role_models"]["shop"]}).command()
        self.assertEqual(command[command.index("--model") + 1], "haiku")
