import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from codex_backend import ModelSession, RateLimited, inspect_events, payload
from negotiation import (ACTS, CONDITIONS, MAX_TURNS, advance, evaluate, initial_state,
                         load_json, negotiate, parse_object, read_message, role_system)
from run_experiment import read_rows, run_group


def structured(act, price=None):
    return json.dumps(dict(performative=act, content=dict(price=price)))


def label(act, price=None):
    return json.dumps(dict(performative=act, price=price))


class NegotiationTests(unittest.TestCase):
    def setUp(self):
        self.prompts = load_json("prompts.json")
        self.scenario = dict(id="TEST", item="a fictional slot", reserve=60, budget=100)
        self.events = []

    def emit(self, event, **data):
        self.events.append(dict(event=event, **data))

    def session(self, replies):
        replies = iter(replies)
        def transport(system, history, emit):
            emit("model_input", payload=payload(system, history))
            raw = next(replies)
            if isinstance(raw, BaseException):
                raise raw
            return raw, dict(input_tokens=10, output_tokens=2)
        return ModelSession(self.emit, transport=transport, sleep=lambda _: None)

    def test_parse_all_acts(self):
        for act in ACTS:
            price = 70 if act == "propose" else None
            self.assertEqual(parse_object(structured(act, price), structured=True), (act, price, None))

    def test_bad_json_not_repaired(self):
        for raw in ("not JSON", "```json\n{}\n```", '{} trailing', '[]', 'null'):
            self.assertIsNotNone(parse_object(raw)[2])

    def test_bad_fields_and_prices(self):
        for price in (True, -1, 1.5, "70", float("nan"), float("inf")):
            self.assertIsNotNone(parse_object(label("propose", price))[2])
        for raw in ('{"performative":"propose","price":1,"extra":0}',
                    '{"performative":"propose","price":1,"price":2}',
                    '{"performative":"query-ref","price":null}',
                    '{"performative":"propose","content":{"price":1,"extra":0}}'):
            self.assertIsNotNone(parse_object(raw, structured="content" in raw)[2])

    def test_propose_requires_price(self):
        self.assertIsNotNone(parse_object(label("propose"))[2])

    def test_zero_price_allowed(self):
        self.assertEqual(parse_object(label("propose", 0)), ("propose", 0, None))

    def test_tag_uses_reader_only_for_propose(self):
        model = self.session([label("propose", 77)])
        for act in ACTS:
            result = read_message("tagged", "({}) message".format(act), [], self.prompts, model, self.emit)
            self.assertIsNone(result[2])
        self.assertEqual(model.stats["reader_calls"], 1)

    def test_tag_reader_act_is_ignored(self):
        model = self.session([label("accept-proposal", 80)])
        result = read_message("tagged", "(propose) I offer 80.", [], self.prompts, model, self.emit)
        self.assertEqual(result, ("propose", 80, None))

    def test_tag_reader_missing_price_fails(self):
        model = self.session([label("accept-proposal")])
        self.assertIsNotNone(read_message("tagged", "(propose) Offer.", [], self.prompts, model, self.emit)[2])

    def test_invalid_tags_do_not_call_reader(self):
        model = self.session([])
        for raw in ("propose 10", "(query-ref) Price?", "(propose)", "(propose) 10 (refuse)",
                    "hello (propose) 10"):
            self.assertIsNotNone(read_message("tagged", raw, [], self.prompts, model, self.emit)[2])
        self.assertEqual(model.stats["reader_calls"], 0)

    def test_free_always_uses_reader(self):
        model = self.session([label("refuse")])
        self.assertEqual(read_message("free", "I leave.", [], self.prompts, model, self.emit), ("refuse", None, None))
        self.assertEqual(model.stats["reader_calls"], 1)

    def test_structured_no_reader(self):
        model = self.session([])
        self.assertEqual(read_message("structured", structured("propose", 80), [], self.prompts, model, self.emit), ("propose", 80, None))
        self.assertEqual(model.stats["reader_calls"], 0)

    def test_format_is_only_prompt_difference(self):
        for role in ("buyer", "seller"):
            prefixes = [role_system(role, self.scenario, c, self.prompts)[:-len(self.prompts["formats"][c])]
                        for c in CONDITIONS]
            self.assertEqual(len(set(prefixes)), 1)

    def test_private_limits_are_separated(self):
        scenario = dict(self.scenario, reserve=9127, budget=4813)
        buyer = role_system("buyer", scenario, "free", self.prompts)
        seller = role_system("seller", scenario, "free", self.prompts)
        self.assertIn("4813", buyer)
        self.assertNotIn("9127", buyer)
        self.assertIn("9127", seller)
        self.assertNotIn("4813", seller)

    def test_accept_uses_opponent_offer_not_acceptance_price(self):
        model = self.session([structured("propose", 80), structured("accept-proposal", 999)])
        result = negotiate(self.scenario, "structured", self.prompts, model, self.emit)
        self.assertEqual((result["outcome"], result["price"], result["correct"]), ("deal", 80, 1))

    def test_invalid_message_still_reaches_other_history(self):
        model = self.session(["MALFORMED BUT PUBLIC", structured("refuse")])
        result = negotiate(self.scenario, "structured", self.prompts, model, self.emit)
        requests = [e["payload"] for e in self.events if e["event"] == "model_input"]
        self.assertEqual(requests[1]["history"], [dict(role="user", content="MALFORMED BUT PUBLIC")])
        self.assertEqual((result["turns"], result["format_errors"]), (2, 1))

    def test_accept_without_offer_is_error_not_deal(self):
        model = self.session([structured("accept-proposal"), structured("refuse")])
        result = negotiate(self.scenario, "structured", self.prompts, model, self.emit)
        self.assertEqual((result["outcome"], result["format_errors"], result["price"]), ("no_deal", 1, ""))

    def test_out_of_limit_deal_is_not_blocked(self):
        for price in (40, 120):
            model = self.session([structured("propose", price), structured("accept-proposal")])
            result = negotiate(self.scenario, "structured", self.prompts, model, self.emit)
            self.assertEqual((result["outcome"], result["correct"], result["violation"]), ("deal", 0, 1))

    def test_boundary_equal_limits(self):
        scenario = dict(self.scenario, reserve=60, budget=60)
        state = dict(initial_state(), outcome="deal", price=60)
        self.assertEqual(evaluate(scenario, state)["correct"], 1)

    def test_refusal_correct_only_if_infeasible(self):
        state = dict(initial_state(), outcome="no_deal")
        self.assertEqual(evaluate(self.scenario, state)["correct"], 0)
        self.assertEqual(evaluate(dict(self.scenario, reserve=120), state)["correct"], 1)

    def test_turn_limit_is_open_and_incorrect(self):
        model = self.session([structured("reject-proposal")] * MAX_TURNS)
        result = negotiate(dict(self.scenario, reserve=120), "structured", self.prompts, model, self.emit)
        self.assertEqual((result["outcome"], result["correct"], result["turns"]), ("open", 0, 8))

    def test_latest_opponent_offer_persists_after_rejection(self):
        state = initial_state()
        advance(state, "buyer", "propose", 70)
        advance(state, "seller", "reject-proposal", None)
        advance(state, "buyer", "propose", 80)
        advance(state, "seller", "accept-proposal", None)
        self.assertEqual(state["price"], 80)

    def test_histories_and_reader_only_contain_public_messages(self):
        replies = ["I offer 80.", label("propose", 80), "Accepted.", label("accept-proposal")]
        model = self.session(replies)
        result = negotiate(self.scenario, "free", self.prompts, model, self.emit)
        requests = [e["payload"] for e in self.events if e["event"] == "model_input"]
        self.assertEqual(requests[2]["history"], [dict(role="user", content="I offer 80.")])
        self.assertEqual(json.loads(requests[3]["history"][0]["content"]),
                         dict(transcript=[dict(speaker="buyer", text="I offer 80."),
                                          dict(speaker="seller", text="Accepted.")]))
        self.assertEqual((result["turns"], result["reader_calls"]), (2, 2))

    def test_payload_history_is_snapshot(self):
        history = [dict(role="user", content="first")]
        request = payload("system", history)
        history.append(dict(role="assistant", content="second"))
        self.assertEqual(len(request["history"]), 1)

    def test_rate_limit_retries_are_counted(self):
        model = self.session([RateLimited("HTTP 429"), label("refuse")])
        model("test", [], "reader")
        self.assertEqual((model.stats["reader_calls"], model.stats["retries"]), (2, 1))
        self.assertEqual([e["delay_seconds"] for e in self.events if e["event"] == "retry"], [2])

    def test_retry_limit_and_backoff(self):
        model = self.session([RateLimited("HTTP 429")] * 4)
        with self.assertRaises(RateLimited):
            model("test", [], "buyer")
        self.assertEqual([e["delay_seconds"] for e in self.events if e["event"] == "retry"], [2, 4, 8])
        self.assertEqual(model.stats["actor_calls"], 4)

    def test_other_errors_not_retried(self):
        model = self.session([RuntimeError("synthetic failure")])
        with self.assertRaises(RuntimeError):
            model("test", [], "buyer")
        self.assertEqual(model.stats["retries"], 0)

    def test_native_action_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "native tool"):
            inspect_events(json.dumps(dict(type="item.started", item=dict(type="command_execution"))))

    def test_raw_transport_requires_usage(self):
        events = [dict(type="item.completed", item=dict(type="agent_message", text="hello")),
                  dict(type="turn.completed", usage=dict(input_tokens=10, output_tokens=2))]
        self.assertEqual(inspect_events("\n".join(map(json.dumps, events)))[0], "hello")
        events[-1]["usage"]["input_tokens"] = True
        with self.assertRaises(RuntimeError):
            inspect_events("\n".join(map(json.dumps, events)))


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scenarios = load_json("scenarios.json")
        self.prompts = load_json("prompts.json")
        self.calls = 0

    def factory(self, emit):
        replies = iter([structured("propose", 80), structured("accept-proposal")])
        def transport(*args):
            self.calls += 1
            return next(replies), dict(input_tokens=1, output_tokens=1)
        return ModelSession(emit, transport=transport)

    def run_group(self, factory=None):
        with contextlib.redirect_stdout(io.StringIO()):
            run_group(self.root, "structured-01", "structured", self.scenarios,
                      self.prompts, {}, session_factory=factory or self.factory)

    def test_resume_skips_recorded_pairs(self):
        self.run_group()
        self.run_group()
        self.assertEqual((len(read_rows(self.root)), self.calls), (4, 8))

    def test_missing_csv_restored_from_original_log(self):
        self.run_group()
        expected = read_rows(self.root)
        (self.root / "results.csv").unlink()
        self.run_group()
        self.assertEqual(read_rows(self.root), expected)
        self.assertEqual(self.calls, 8)

    def test_crash_preserved_and_resume_skips_it(self):
        def factory(emit):
            def transport(*args):
                raise RuntimeError("offline crash")
            return ModelSession(emit, transport=transport)
        with self.assertRaises(RuntimeError):
            self.run_group(factory)
        row = read_rows(self.root)[0]
        self.assertTrue(all(row[k] == "" for k in ("outcome", "price", "turns", "reader_calls", "correct")))
        self.run_group()
        self.assertEqual(len(read_rows(self.root)), 4)
        self.assertEqual(read_rows(self.root)[0], row)
        self.assertEqual(self.calls, 6)

    def test_interrupted_episode_is_marked_crashed_on_resume(self):
        directory = self.root / "logs"
        directory.mkdir()
        events = [dict(event="config", run="structured-01", condition="structured"),
                  dict(event="episode_start", scenario="S1")]
        (directory / "structured-01.log").write_text("\n".join(map(json.dumps, events)) + "\n")
        self.run_group()
        rows = read_rows(self.root)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["outcome"], "")
        self.assertIn("Interrupted episode", rows[0]["note"])
        self.assertEqual(self.calls, 6)


if __name__ == "__main__":
    unittest.main()
