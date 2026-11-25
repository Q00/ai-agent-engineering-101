"""Exact cheap firm-food text must reach the existing first-send/guard boundary."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

import buyer
import market_buyer as mb
from test_buyer_reply import task


LABEL = "fixture food"
SKU = "G0b5efdc7"


def quote_text(price=7021, lp=11900, label=LABEL, sku=SKU):
    return (f"Last 7 days: 2292/2292 claims true, 0 false. {label} ({sku}) "
            f"(grade B, 530.9h shelf life left): list {lp}, firm price {price}, "
            f"{round(100 * (1-price/lp))}% below list. Market certificate attached. "
            f"This price holds for 10 min and does not change at checkout. Reply with {price} to buy.")


class FirmFoodTakeTest(unittest.TestCase):
    def ladder(self, **changes):
        # Compile the whole real callback, not a shortened reimplementation.
        node = next(n for n in ast.walk(ast.parse(Path(mb.__file__).read_text()))
                    if isinstance(n, ast.FunctionDef) and n.name == "price_ladder")
        env = dict(mb.__dict__, r3=True, order={"kind": "food", "use_within_hours": 360},
                   args=SimpleNamespace(budget=15900), limit=7972, steps=(.9,),
                   q={"ask": 7021, "ask_src": "text", "refused": None},
                   offer={"name": LABEL, "sku": SKU, "list_price": 11900},
                   resume=True, shop={"name": "nohaggle-hero", "url": "fixture"}, key=SKU, label=LABEL,
                   md="unused-isolated-state", probe_arm=None, discount_arm=None, goods_first_arm=None,
                   log=lambda *a: None)
        env.update(changes)
        exec(compile(ast.fix_missing_locations(ast.Module([node], type_ignores=[])),
                     "<real-market-price-ladder>", "exec"), env)
        return env["price_ladder"]

    def wire(self, text=None, *, meta=None, guard=None, take_at=None, **changes):
        text = quote_text() if text is None else text
        out = {"history": [], "agreed": None, "holds": 0, "receipt": None}
        sent = []
        def send(url, value, task_id=None):
            sent.append(value)
            return task("", state="TASK_STATE_COMPLETED" if value == "7021" else "TASK_STATE_REJECTED")
        with mock.patch.object(buyer, "send", send), mock.patch.object(buyer, "log"), \
                mock.patch.object(buyer, "rpc", return_value=None), \
                mock.patch.object(buyer, "decide", return_value={
                    "shop_price": 7021, "action": "offer", "price": 5100}):
            buyer.talk("fixture", task(text, meta), LABEL, changes.get("limit", 7972), "", out,
                       ladder=self.ladder(**changes), decision_guard=guard, take_at=take_at)
        return sent, out

    def test_verified_cheap_firm_food_is_sent_at_its_exact_ask_not_rejected_lowball(self):
        # Observed ask 7021 / LP11900 / cap7972; actual old first offer5100 was rejected.
        sent, out = self.wire()
        self.assertEqual(sent, ["7021"])
        self.assertEqual(out["state"], "TASK_STATE_COMPLETED")
        self.assertEqual(out["turns"], {"code": 1})

    def test_existing_65_percent_boundary_is_not_widened(self):
        for price, expected in ((6500, 6500), (6501, None)):
            with self.subTest(price=price):
                turn = self.ladder(limit=6700, offer={"name": LABEL, "sku": SKU, "list_price": 10000},
                                   q={"ask": price, "ask_src": "text"})
                d = turn(None, quote_text(price=price, lp=10000), [])
                self.assertEqual(d["price"] if d else None, expected)
        # The other actual selected quote was 66% of LP; preserve its harness path.
        turn = self.ladder(limit=13332, offer={"name": LABEL, "sku": SKU, "list_price": 19900},
                           q={"ask": 13134, "ask_src": "text"})
        self.assertIsNone(turn(None, quote_text(price=13134, lp=19900), []))

    def test_only_selected_resumed_food_and_matching_quote_get_the_take(self):
        cases = (
            {"order": {"kind": "goods"}}, {"shop": {"name": "other-shop"}},
            {"shop": {"name": "NoHg"}},  # report shorthand is not the production shop identifier
            {"resume": False}, {"r3": False}, {"limit": 7000}, {"limit": True},
            {"q": {"ask": 7022, "ask_src": "text"}},
            {"q": {"ask": True, "ask_src": "text"}},
            {"q": {"ask": 7021, "ask_src": "text_rank"}},
            {"q": {"ask": 7021, "ask_src": "text", "refused": "short shelf"}},
            {"offer": {"name": "different item", "sku": SKU, "list_price": 11900}},
            {"offer": {"name": LABEL, "sku": "G12345678", "list_price": 11900}},
            {"offer": {"name": LABEL, "sku": SKU, "list_price": 12000}},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                self.assertIsNone(self.ladder(**changes)(None, quote_text(), []))

    def test_partial_tampered_or_repeated_message_stays_harness(self):
        for text in (quote_text()[:-1], quote_text()+" Additional conditions.",
                     quote_text().replace("Reply with 7021", "Reply with 7022"),
                     quote_text().replace("41% below", "9% below"),
                     "The price is firm. Reply with 7021 to buy."):
            with self.subTest(text=text):
                self.assertIsNone(self.ladder()(None, text, []))
        self.assertIsNone(self.ladder()(None, quote_text(), [("you offered", 5100)]))
        with mock.patch.object(mb, "FOOD_TAKE_ASK", False):
            self.assertIsNone(self.ladder()(None, quote_text(), []))

    def test_metadata_and_existing_code_decision_keep_precedence(self):
        # Numeric metadata retains the existing TAKE branch, not the new text path.
        sent, out = self.wire(meta=7021, take_at=7735)
        self.assertEqual(sent, ["7021"])
        self.assertEqual(out["turns"], {"take": 1})
        chosen = {"shop_price": 7021, "action": "offer", "price": 6000}
        with mock.patch.object(mb, "opening_probe_turn", return_value=chosen):
            self.assertIs(self.ladder()(None, quote_text(), []), chosen)

    def test_answering_a_question_does_not_make_a_short_shelf_quote_eligible(self):
        hybrid = (f"{LABEL} ({SKU}). Others were asking more; we are asking 7021 now. "
                  "How many hours must it remain usable? Reply e.g. 'use within 72h'")
        order = {"kind": "food", "use_within_hours": 360.0}
        sent = []
        def send(url, value, task_id=None):
            sent.append(value)
            return (task(quote_text().replace("530.9h", "100.0h")) if len(sent) == 1
                    else task("", state="TASK_STATE_COMPLETED"))
        with mock.patch.object(buyer, "send", send), mock.patch.object(buyer, "log"), \
                mock.patch.object(buyer, "rpc", return_value=None), \
                mock.patch.object(buyer, "decide", return_value={
                    "shop_price": 7021, "action": "walk_away", "price": None}):
            buyer.negotiate("fixture", LABEL, 7972, resume=("fixture", task(hybrid)),
                stop_if=lambda text, turn: mb.quote_refused(text, order, item=(LABEL, SKU)) if turn == 1 else None,
                reply_to=lambda text: mb.shop_question_reply(text, order), ladder=self.ladder(order=order))
        self.assertEqual(sent, ["use within 360h"])

    def test_existing_final_guard_can_veto_before_any_send(self):
        sent, out = self.wire(guard=lambda decision, history: {"action": "walk_away"})
        self.assertEqual(sent, [])
        self.assertNotEqual(out["state"], "TASK_STATE_COMPLETED")
        self.assertIsNone(out["receipt"])


if __name__ == "__main__":
    unittest.main()
