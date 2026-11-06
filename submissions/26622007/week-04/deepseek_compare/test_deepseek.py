import json
import unittest

import run_deepseek as ds

armed = ds.armed
LAMP = {"id": 2, "item": "a desk lamp", "reserve": 30, "budget": 45}
REFUSE = '{"performative":"refuse","content":{"price":null}}'
PROPOSE = '{"performative":"propose","content":{"price":40}}'


def tool_message(action):
    return {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function",
            "function": {"name": "double_barreled_shotgun", "arguments": json.dumps({"action": action})}}]}


class DeepSeekTests(unittest.TestCase):
    def tearDown(self):
        ds.configure("control")

    def episode(self, design, side, replies):
        ds.configure(design)
        spec = ds.DESIGNS[design]
        holder = None if side == "none" else side
        scenario = {**LAMP, "holder": holder} if spec["holding"] else LAMP
        calls, iterator = [], iter(replies)
        def call(role, messages, fmt, **tool_args):
            calls.append({"role": role, "system": messages[0]["content"], **tool_args})
            reply = next(iterator)
            return {"role": "assistant", "content": reply} if "tools" in tool_args and isinstance(reply, str) else reply
        row = {"deal_possible": 1}
        armed.negotiate(scenario, "structured", holder, call, lambda *a, **k: None, row)
        return row, calls

    def test_control_has_no_tool_and_no_holding_sentence(self):
        row, calls = self.episode("control", "none", [PROPOSE, REFUSE])
        self.assertTrue(all("tools" not in c and ds.holding.HOLDING not in c["system"] for c in calls))
        self.assertEqual(calls[0]["system"], ds.base_lab.system_prompt("buyer", LAMP, "structured"))
        self.assertEqual(row["outcome"], "no_deal")

    def test_holding_only_marks_the_holder_prompt_without_tools(self):
        _, calls = self.episode("holding-only", "buyer", [PROPOSE, REFUSE])
        self.assertIn(ds.holding.HOLDING, calls[0]["system"])
        self.assertNotIn(ds.holding.HOLDING, calls[1]["system"])
        self.assertTrue(all("tools" not in c for c in calls))

    def test_holding_with_tool_and_shotgun_designs_offer_the_same_tool(self):
        for design in ("holding-with-tool", "shotgun-auto"):
            _, calls = self.episode(design, "seller", [PROPOSE, REFUSE])
            self.assertEqual(calls[1]["tools"], [ds.shotgun.armed.TOOL])
            self.assertEqual((ds.holding.HOLDING in calls[1]["system"]), design == "holding-with-tool")

    def test_forced_design_forces_the_first_armed_request(self):
        _, calls = self.episode("shotgun-forced", "buyer", [tool_message("Holds it."), PROPOSE, REFUSE])
        self.assertEqual(calls[0]["tool_choice"], ds.shotgun.FORCED_CHOICE)
        self.assertEqual(calls[1]["tool_choice"], "auto")

    def test_wire_payload_uses_the_deepseek_settings(self):
        payloads = []
        class Response:
            done = False
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read1(self, size):
                if self.done: return b""
                self.done = True
                return json.dumps({"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]}).encode()
        def opener(request, **kwargs):
            payloads.append(json.loads(request.data))
            return Response()
        config = json.loads((ds.HERE / "config.json").read_text())
        client = armed.OpenRouterClient("fixture", config, opener=opener)
        client.complete([], lambda *a, **k: None, 2, "buyer", response_format=ds.base_lab.TEXT_FORMAT, tools=[armed.TOOL])
        client.complete([], lambda *a, **k: None, 2, "seller", response_format=ds.base_lab.schema_format(True))
        for p in payloads:
            self.assertEqual((p["model"], p["temperature"], p["top_p"], p["reasoning"]),
                             ("deepseek/deepseek-v4.1-flash", 1.0, 0.95, {"effort": "low"}))
            self.assertEqual(p["provider"]["only"], ["deepinfra/fp8"])
        self.assertIn("tools", payloads[0])
        self.assertNotIn("tools", payloads[1])

    def test_other_design_modules_are_untouched(self):
        self.assertIsNot(armed, ds.holding.armed)
        self.assertIsNot(armed.lab, ds.holding.armed.lab)
        self.assertEqual(ds.holding.armed.speak_armed.__name__, "speak_armed")


if __name__ == "__main__":
    unittest.main()
