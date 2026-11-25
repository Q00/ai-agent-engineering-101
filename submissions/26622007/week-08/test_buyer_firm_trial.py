"""Approved pure trial seam; no market IO."""
import unittest
from unittest.mock import patch
import contextlib
import io
import json
import ast
import inspect
from types import SimpleNamespace

import buyer_firm_trial as trial


class TrialTests(unittest.TestCase):
    def call(self, **changes):
        values = dict(baseline=None, meta=None, text="Counteroffer: 120. This is my lowest price.",
            history=[("shop asked", 120), ("you offered", 90)], limit=100,
            order={"order": "o-test", "kind": "goods", "_buyer_started_at": 100},
            shop="fixture", sku="G12345678", policy_ok=True, now=100, log=lambda _: None)
        values.update(changes)
        return trial.turn(**values)

    def test_window_rejects_old_retry_end_and_invalid_values(self):
        with patch.multiple(trial, ENABLED=True, START=100, END=1900):
            for now, started in ((99, 99), (100, 99), (1900, 100), (101, 102), (float("nan"),100), (True,100)):
                with self.subTest(now=now, started=started):
                    order = {"order":"o-test", "kind":"goods", "_buyer_started_at":started}
                    self.assertIsNone(self.call(order=order, now=now))
                    self.assertNotIn("_firm_trial", order)
        with patch.multiple(trial, ENABLED=True, START=0, END=1800):
            self.assertIsNone(self.call())

    def test_eligibility_before_assignment_and_assignment_before_decision(self):
        events = []
        order = {"order":"o-test", "kind":"goods", "_buyer_started_at":100}
        def capture(line):
            if line.startswith("[firm-assignment]"):
                self.assertNotIn("_firm_trial", order)
            events.append(line)
        with patch.multiple(trial, ENABLED=True, START=100, END=1900):
            for change in ({"policy_ok":False}, {"meta":120}, {"history":[]},
                           {"text":"Counteroffer: 120. No warranty."}, {"limit":True}):
                self.assertIsNone(self.call(order=order, log=capture, **change))
                self.assertEqual(events, [])
            self.call(order=order, log=capture)
            self.call(order=order, log=capture, now=101)
        self.assertEqual(sum(e.startswith("[firm-assignment]") for e in events), 1)
        self.assertEqual(order["_firm_trial"]["eligible"], 2)

    def test_stable_arms_and_off_after_assignment(self):
        arms = set()
        with patch.multiple(trial, ENABLED=True, START=100, END=1900):
            for number in range(20):
                identity = f"o-{number}"
                first = {"order":identity, "kind":"goods", "_buyer_started_at":100}
                retry = dict(first)
                result = self.call(order=first)
                self.assertEqual(self.call(order=retry, now=101), result)
                self.assertEqual(first["_firm_trial"]["arm"], retry["_firm_trial"]["arm"])
                selected = first["_firm_trial"]["arm"]
                arms.add(selected)
                self.assertEqual(result, {"shop_price":120,"action":"offer","price":100} if selected == "B" else None)
                with patch.object(trial, "ENABLED", False):
                    self.assertIsNone(self.call(order=first))
                self.assertEqual(first["_firm_trial"]["eligible"], 1)
        self.assertEqual(arms, {"A", "B"})

    @patch.object(trial, "ENABLED", False)
    def test_off_preserves_baseline_and_does_not_assign(self):
        order = {"order": "o-test", "kind": "goods", "_buyer_started_at": 100}
        events = []
        self.assertIsNone(trial.turn(None, None,
            "Counteroffer: 120. This is my lowest price.",
            [("shop asked", 120), ("you offered", 90)], 100,
            order=order, shop="fixture", sku="G12345678", policy_ok=True,
            now=100, log=events.append))
        self.assertEqual(events, [])
        self.assertNotIn("_firm_trial", order)


class CallbackFakeTests(unittest.TestCase):
    """Real callback adapter + buyer.talk; fake external send/RPC/decision only."""
    @classmethod
    def setUpClass(cls):
        # Static import audit: market_buyer and local dependency top levels only
        # construct constants/regex/locks; all market calls are inside functions.
        import market_buyer
        import buyer
        cls.market, cls.buyer = market_buyer, buyer

    def run_talk(self, selected, *, enabled=True, text=None, initial=None, cap=100, quote=None):
        m, buyer = self.market, self.buyer
        identity = next(f"o-{i}" for i in range(100) if trial.arm(f"o-{i}") == selected)
        order = {"order":identity, "kind":"goods", "_buyer_started_at":100}
        q = quote or {"text":"Asking 120. Warranty: 6 hours."}
        trace, sent = [], []
        def task(state, message="", ask=None):
            msg = {"parts":[{"text":message}]}
            if ask is not None:
                msg["metadata"] = {"ask":ask}
            return {"id":"fixture-task", "status":{"state":state, "message":msg}}
        def send(url, value, task_id):
            sent.append(value)
            return task("TASK_STATE_INPUT_REQUIRED", text or "Counteroffer: 120. This is my lowest price.") if len(sent) == 1 else task("TASK_STATE_CANCELED")
        # Load the actual nested callback, without executing main or duplicating
        # its body. Only the closure's fixture inputs are supplied here.
        callback = next(node for node in ast.walk(ast.parse(inspect.getsource(m.main)))
                        if isinstance(node, ast.FunctionDef) and node.name == "price_ladder")
        namespace = {**vars(m), "discount_arm":None, "probe_arm":None,
            "goods_first_arm":None, "limit":cap, "steps":(.9,), "q":q,
            "resume":True, "order":order, "args":SimpleNamespace(budget=130),
            "shop":{"name":"fixture"}, "key":"G12345678", "r3":True}
        exec(compile(ast.Module(body=[callback],type_ignores=[]), "<fixture callback>","exec"),namespace)
        ladder = namespace["price_ladder"]
        out = {"history":[], "agreed":None, "receipt":None, "holds":0}
        with patch.multiple(trial, ENABLED=enabled, START=100, END=1900), \
             patch.object(m.time, "time", return_value=100), \
             patch.object(buyer, "send", side_effect=send), \
             patch.object(buyer, "rpc", return_value=task("TASK_STATE_CANCELED")), \
             patch.object(buyer, "decide", return_value={"shop_price":120,"action":"offer","price":95}) as decision, \
             patch.object(m, "log", side_effect=lambda line: trace.append(line)), \
             contextlib.redirect_stderr(io.StringIO()):
            buyer.talk("fake", initial or task("TASK_STATE_INPUT_REQUIRED", "Asking 120.",120),
                "fixture",cap,"",out,
                stop_if=lambda t,n: (m.quote_refused(t,order) if n == 1 else None) or m.goods_text_refused(t,order),
                ladder=ladder)
        return order, out, sent, trace, decision.call_count

    def test_both_arms_keep_first_decision_and_record_real_history(self):
        for selected, expected, calls in (("A",["100","95"],1),("B",["100"],0)):
            order,out,sent,trace,count = self.run_talk(selected)
            self.assertEqual(sent,expected)
            self.assertEqual(count,calls)
            measure = trial.measurement(order,out)
            self.assertEqual(measure["arm"],selected)
            self.assertEqual(measure["matches"][0]["baseline"],None)
            self.assertEqual(measure["matches"][0]["candidate"],{"shop_price":120,"action":"walk_away","price":0})
            self.assertEqual(measure["matches"][0]["history_offer"],95 if selected == "A" else None)
            assignment = json.loads(trace[0].split("] ",1)[1])
            self.assertEqual(set(assignment),{"study","order","arm","time","shop","sku"})
            self.assertEqual(assignment["time"],100)

    def test_off_restores_identical_baseline_history(self):
        _,control,sent,_,_ = self.run_talk("A")
        order,off,off_sent,trace,_ = self.run_talk("B",enabled=False)
        self.assertEqual(off,control)
        self.assertEqual(off_sent,sent)
        self.assertEqual(trace,[])
        self.assertNotIn("_firm_trial",order)

    def test_b_offer_is_recorded_separately_from_first_offer(self):
        order,out,sent,_,calls = self.run_talk("B",cap=130)
        self.assertEqual(sent,["108","120"])
        self.assertEqual(calls,0)
        measured = trial.measurement(order,out)
        self.assertEqual(measured["matches"][0]["candidate"]["price"],120)
        self.assertEqual(measured["matches"][0]["history_offer"],120)
        self.assertEqual(measured["actual_decisions"],1)

    def test_current_quote_policy_failure_keeps_harness(self):
        for quote in ({"text":"No warranty."}, {"text":"Warranty: 3 hours."},
                      {"text":"Asking 120.","refused":"existing refusal"}):
            order,_,sent,trace,calls = self.run_talk("B",quote=quote)
            self.assertEqual(sent,["100","95"])
            self.assertEqual(trace,[])
            self.assertEqual(calls,1)
            self.assertNotIn("_firm_trial",order)

    def test_no_warranty_and_receipt_policy_remain_separate(self):
        order,out,sent,trace,count = self.run_talk("B",text="Counteroffer: 120. No warranty.")
        self.assertEqual(sent,["100"])
        self.assertEqual(trace,[])
        self.assertEqual(count,0)
        self.assertIsNotNone(self.market.warranty_short({"warranty_hours":0},order))
        self.assertIsNone(self.market.warranty_short({"warranty_hours":6},order))
        self.assertEqual(self.market.accept_policy(order),{"warranty_hours":6})

    def test_cap_one_and_terminal_do_not_gain_exposure(self):
        order,_,sent,trace,_ = self.run_talk("B",cap=1)
        self.assertEqual(sent,["1"])
        self.assertEqual(order["_firm_trial"]["matches"][0]["decision"]["action"],"walk_away")
        terminal = {"id":"fixture-task","status":{"state":"TASK_STATE_CANCELED"}}
        order,_,sent,trace,calls = self.run_talk("B",initial=terminal)
        self.assertEqual((sent,trace,calls),([],[],0))
        self.assertNotIn("_firm_trial",order)


if __name__ == "__main__":
    unittest.main()
