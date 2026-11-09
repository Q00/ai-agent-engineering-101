"""Food lower-grade sentence A/B (buyer_support.json food_lower_grade_sentence, salt lg1): on food orders that send the conditions
message, the test arm adds one sentence naming exactly the lower grades that order accepts, so it never invites a quote we would
refuse. Off, the control arm, goods and the first-message control arm send today's text."""

import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import first_message_ab as ab
import market_buyer as market
from quote_terms import check_quote

ON = {"enabled": True, "share": 0.5, "salt": "lg1"}
FM_ON = {"enabled": True, "share": 0.9999, "salt": "fm1"}
AB_ONLY = {"cert": True, "grades": ["A", "B"], "min_hours_left": 74.0}
WITH_C = {"cert": True, "grades": ["A", "B", "C"], "min_hours_left": 74.0}
SENTENCE_B = "Grade B is fine if it meets these conditions; price matters more to us than grade."
SENTENCE_BC = "Grade B or C is fine if it meets these conditions; price matters more to us than grade."


class Settings(unittest.TestCase):
    def load(self, section, raw=None):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "buyer_support.json"
            path.write_text(raw if raw is not None else json.dumps({ab.LOWER_SECTION: section}), encoding="utf-8")
            return ab.load_lower(path)

    def test_on_only_when_valid(self):
        self.assertEqual(self.load(ON), ON)
        for bad in ({**ON, "enabled": False}, {**ON, "enabled": "true"}, {**ON, "share": 1.0}, {**ON, "share": 0}, {**ON, "salt": ""}, None, []):
            with self.subTest(bad=bad):
                self.assertFalse(self.load(bad)["enabled"])
        self.assertFalse(self.load(None, raw="{")["enabled"])

    def test_the_shipped_setting(self):
        section = json.loads(Path(ab.CONFIG).read_text(encoding="utf-8"))[ab.LOWER_SECTION]
        self.assertEqual((section["share"], section["salt"]), (0.5, "lg1"))
        # Installed off; switched on only in its own commit after the full suite.
        self.assertIs(section["enabled"], False)

    def test_its_own_salt(self):
        orders = [f"o-{i:012x}" for i in range(4000)]
        mine = [ab.arm(o, ON) == "conditions" for o in orders]
        for salt in ("fm1", "gm1", "sa1", "fc1"):
            other = [ab.arm(o, {**ON, "salt": salt}) == "conditions" for o in orders]
            with self.subTest(salt=salt):
                self.assertAlmostEqual(sum(a and b for a, b in zip(mine, other)) / len(orders), 0.25, delta=0.03)


class Wording(unittest.TestCase):
    def test_it_names_exactly_the_lower_grades_the_order_accepts(self):
        self.assertEqual(ab.lower_grade_sentence(AB_ONLY), SENTENCE_B)
        self.assertEqual(ab.lower_grade_sentence(WITH_C), SENTENCE_BC)
        self.assertEqual(ab.lower_grade_sentence({"grades": ["B", "C"]}), SENTENCE_BC)
        self.assertEqual(ab.lower_grade_sentence({"grades": ["A", "C"]}), "Grade C is fine if it meets these conditions; price matters more to us than grade.")

    def test_no_lower_grade_no_sentence(self):
        for accept in ({"grades": ["A"]}, {"grades": []}, {}, None, {"grades": "B"}, {"grades": ["X"]}):
            with self.subTest(accept=accept):
                self.assertIsNone(ab.lower_grade_sentence(accept))

    def test_honest_and_plain(self):
        for text in (SENTENCE_B, SENTENCE_BC):
            low = text.lower()
            for word in ("must", "require", "only", "immediately", "or else", "!", "review", "star", "promise", "ignore", "instruction",
                         "other shop", "another", "elsewhere", "offer from", "hurry", "now", "last chance"):
                with self.subTest(text=text, word=word):
                    self.assertNotIn(word, low)

    def test_a_seller_echoing_it_never_loosens_the_grade(self):
        # An echo of the C sentence makes the grade ambiguous, which counts as unstated: the worst grade allowed, the strictest floor.
        q = check_quote({}, "You said grade B or C is fine. Kimchi (G1): grade A, 140 h of shelf life left, certificate attached. Asking 14426.", WITH_C)
        self.assertIsNone(q["grade"])
        self.assertTrue(q["text_grade_ambiguous"])
        # A plain quote still reads its own grade.
        self.assertEqual(check_quote({}, "Kimchi (G1): grade A, 140 h of shelf life left, certificate attached. Asking 14426.", WITH_C)["grade"], "A")


class Wiring(unittest.TestCase):
    """trade_attempt: only food orders in the conditions arm can get the sentence, and only in the test arm."""

    def attempt(self, order, lower, accept=AB_ONLY, fm=FM_ON):
        folder = Path(tempfile.mkdtemp())
        (folder / "order.json").write_text("{}", encoding="utf-8")
        args = types.SimpleNamespace(mode="claude", order=folder / "order.json", budget=1000, memory=folder / "memory.jsonl", item="x")
        pick = {"shop": {"name": "s", "url": "http://s.test", "offers": []}, "item": "G1", "budget": 900,
                "accept": dict(accept) if order["kind"] == "food" else {"cert": True, "grades": ["A", "B"]},
                "selected": {}, "decision": {"max_turns": 3}, "reason": "fixture", "food_c_arm": "grade_c" if "C" in accept["grades"] else None}
        seen = {}
        def fake_trade(shop_, item, budget, **options):
            seen.update(options)
            return {"shop": shop_["url"], "receipt": None, "stars": 3, "review": ""}, {"outcome": "no_deal"}
        trial = {"enabled": True, "share": .5, "salt": "fc1", "penalty": .05}
        import food_c_trial
        with patch.object(market, "trade", fake_trade), patch.object(ab, "load", lambda path=None: fm), patch.object(ab, "load_lower", lambda path=None: lower), \
                patch.object(food_c_trial, "load", lambda path=None: trial), patch.object(market, "remember", lambda *a, **k: None):
            _, record = market.trade_attempt(args, order, {}, pick)
        return seen, record

    def order_in(self, lower_arm, fm_arm="conditions"):
        return next(o for o in (f"o-{i:012x}" for i in range(100000))
                    if ab.arm(o, ON) == lower_arm and ab.arm(o, FM_ON) == fm_arm)

    def conditions(self, accept):
        return ab.text("G1", accept)

    def test_test_arm_adds_the_sentence_for_d_control(self):
        seen, record = self.attempt({"order": self.order_in("conditions"), "kind": "food"}, ON)
        self.assertEqual(seen["first_message"], self.conditions(AB_ONLY) + " " + SENTENCE_B)
        self.assertEqual(record["lower_grade_arm"], "lower_grade")

    def test_test_arm_adds_the_c_sentence_for_d_test(self):
        seen, record = self.attempt({"order": self.order_in("conditions"), "kind": "food"}, ON, accept=WITH_C)
        self.assertEqual(seen["first_message"], self.conditions(WITH_C) + " " + SENTENCE_BC)
        self.assertEqual(record["lower_grade_arm"], "lower_grade")

    def test_control_arm_is_today(self):
        for accept in (AB_ONLY, WITH_C):
            with self.subTest(accept=accept["grades"]):
                seen, record = self.attempt({"order": self.order_in("control"), "kind": "food"}, ON, accept=accept)
                self.assertEqual(seen["first_message"], self.conditions(accept))
                self.assertEqual(record["lower_grade_arm"], "control")

    def test_off_is_today(self):
        seen, record = self.attempt({"order": self.order_in("conditions"), "kind": "food"}, dict(ab.DISABLED))
        self.assertEqual(seen["first_message"], self.conditions(AB_ONLY))
        self.assertIsNone(record["lower_grade_arm"])

    def test_first_message_control_and_goods_never_get_it(self):
        seen, record = self.attempt({"order": self.order_in("conditions", fm_arm="control"), "kind": "food"}, ON)
        self.assertNotIn("first_message", seen)
        self.assertIsNone(record["lower_grade_arm"])
        seen, record = self.attempt({"order": self.order_in("conditions"), "kind": "goods"}, ON)
        self.assertNotIn("first_message", seen)
        self.assertIsNone(record["lower_grade_arm"])

    def test_c_dropped_after_selection_drops_c_from_the_sentence(self):
        import food_c_trial
        folder = Path(tempfile.mkdtemp())
        (folder / "order.json").write_text("{}", encoding="utf-8")
        args = types.SimpleNamespace(mode="claude", order=folder / "order.json", budget=1000, memory=folder / "memory.jsonl", item="x")
        pick = {"shop": {"name": "s", "url": "http://s.test", "offers": []}, "item": "G1", "budget": 900, "accept": dict(WITH_C, grades=["A", "B", "C"]),
                "selected": {}, "decision": {"max_turns": 3}, "reason": "fixture", "food_c_arm": "grade_c"}
        seen = {}
        def fake_trade(shop_, item, budget, **options):
            seen.update(options)
            return {"shop": shop_["url"], "receipt": None, "stars": 3, "review": ""}, {"outcome": "no_deal"}
        with patch.object(market, "trade", fake_trade), patch.object(ab, "load", lambda path=None: FM_ON), patch.object(ab, "load_lower", lambda path=None: ON), \
                patch.object(food_c_trial, "load", lambda path=None: dict(food_c_trial.OFF)), patch.object(market, "remember", lambda *a, **k: None):
            market.trade_attempt(args, {"order": self.order_in("conditions"), "kind": "food"}, {}, pick)
        self.assertTrue(seen["first_message"].endswith(SENTENCE_B))


if __name__ == "__main__":
    unittest.main()
