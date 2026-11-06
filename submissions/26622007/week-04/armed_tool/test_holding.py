import json
import unittest

import run_holding as holding
import run_armed as first_design

armed = holding.armed
LAMP = {"id": 2, "item": "a desk lamp", "reserve": 30, "budget": 45}


class HoldingTests(unittest.TestCase):
    def setUp(self):
        self.original = armed.speak_armed

    def tearDown(self):
        armed.speak_armed = self.original

    def episode(self, mode, holder, replies):
        if mode == "only":
            armed.speak_armed = holding.speak_without_tool
        calls, iterator = [], iter(replies)
        def call(role, messages, fmt, **tool_args):
            calls.append({"role": role, "messages": json.loads(json.dumps(messages)), **tool_args})
            reply = next(iterator)
            return {"role": "assistant", "content": reply} if "tools" in tool_args else reply
        row = {"deal_possible": 1}
        armed.negotiate({**LAMP, "holder": holder}, "structured", holder, call, lambda *a, **k: None, row)
        return row, calls

    def test_only_the_holder_prompt_gains_the_sentence_before_the_acts(self):
        base = holding.base_lab
        for holder in ("buyer", "seller"):
            other = "seller" if holder == "buyer" else "buyer"
            sc = {**LAMP, "holder": holder}
            prompt = armed.lab.system_prompt(holder, sc, "tagged")
            self.assertEqual(prompt.count(holding.HOLDING), 1)
            self.assertEqual(prompt.replace(holding.HOLDING, ""), base.system_prompt(holder, LAMP, "tagged"))
            self.assertIn("." + holding.HOLDING + base.COMMON, prompt)
            self.assertEqual(armed.lab.system_prompt(other, sc, "tagged"), base.system_prompt(other, LAMP, "tagged"))
        self.assertNotIn(holding.HOLDING, base.system_prompt("buyer", {**LAMP, "holder": "buyer"}, "free"))

    def test_other_designs_are_untouched(self):
        self.assertIsNot(holding.shotgun.armed.lab, armed.lab)
        self.assertEqual(first_design.TOOL["function"]["name"], "the_great_communicator")
        self.assertEqual(armed.TOOL, holding.shotgun.armed.TOOL)

    def test_only_mode_sends_no_tools_and_uses_the_holding_prompt(self):
        row, calls = self.episode("only", "seller", [
            '{"performative":"propose","content":{"price":30}}',
            '{"performative":"propose","content":{"price":45}}',
            '{"performative":"accept-proposal","content":{"price":null}}'])
        self.assertTrue(all("tools" not in c for c in calls))
        self.assertIn(holding.HOLDING, calls[1]["messages"][0]["content"])
        self.assertNotIn(holding.HOLDING, calls[0]["messages"][0]["content"])
        self.assertEqual((row["outcome"], row["price"], row["tool_calls"]), ("deal", 45, 0))

    def test_with_tool_mode_offers_the_shotgun_to_the_holder(self):
        row, calls = self.episode("with-tool", "buyer", [
            '{"performative":"propose","content":{"price":30}}',
            '{"performative":"accept-proposal","content":{"price":null}}'])
        self.assertEqual((calls[0]["tools"], calls[0]["tool_choice"]), ([armed.TOOL], "auto"))
        self.assertEqual(armed.TOOL["function"]["name"], "double_barreled_shotgun")
        self.assertIn(holding.HOLDING, calls[0]["messages"][0]["content"])
        self.assertNotIn("tools", calls[1])


if __name__ == "__main__":
    unittest.main()
