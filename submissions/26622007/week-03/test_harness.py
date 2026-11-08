"""Offline tests only. Fake bids never enter the submission's results.csv."""
import contextlib
import copy
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from contract_net import (COMMON, CONDITIONS, GENERALIST, OVERCONFIDENT, SKILLS,
                          Task, bid_response_format, load_tasks, make_team, messages_for, parse_bid, run_round)
from openrouter_client import CallError, ConfigurationError, ENDPOINT, OpenRouterClient, read_key
import run as runner

ROOT = Path(__file__).resolve().parent
FAKE_KEY = "sk-or-" + "test-" + "x" * 40


def bid(yes=True, confidence=90):
    return json.dumps({"bid": yes, "confidence": confidence, "reason": "fixture"})


class ContractTests(unittest.TestCase):
    def test_one_round_counts_ties_refusals_parse_failures_and_wrong_awards(self):
        tasks = [Task(str(i), "public task") for i in range(3)]
        replies = iter([bid(True, 90), bid(True, 90), bid(False),
                        "not JSON", bid(False), bid(False),
                        bid(True, 10), bid(False), bid(True, 99)])
        events = []
        result = run_round(tasks, make_team("baseline"), lambda *_: next(replies),
                           lambda event, **fields: events.append((event, fields)))
        self.assertEqual(result.assignments, {"0": "A", "1": None, "2": "C"})
        self.assertEqual(result.evaluate({"0": "A", "1": "B", "2": "B"}),
                         {"tasks": 3, "correct": 1, "messages": 15, "unassigned": 1, "misawards": 1})
        self.assertEqual(result.parse_fails, 1)
        self.assertEqual(result.refusals, 4)
        self.assertEqual(sum(event == "announcement" for event, _ in events), 9)
        self.assertFalse(any("gold" in fields for _, fields in events))

    def test_conditions_change_only_skill_or_one_overconfidence_suffix(self):
        baseline = make_team("baseline")
        for b, h, o in zip(baseline, make_team("homogeneous"), make_team("overconfident")):
            self.assertEqual(h.system, COMMON.format(name=b.name, skill=GENERALIST))
            self.assertEqual(o.system, b.system + (OVERCONFIDENT if b.name == "C" else ""))
            self.assertEqual(b.skill, SKILLS[b.name])
        with self.assertRaises(ValueError):
            make_team("unknown")

    def test_gold_and_previous_dialogue_are_absent_from_requests(self):
        tasks, golds = load_tasks((ROOT / "tasks.json").read_text())
        messages = messages_for(tasks[0], make_team("baseline")[0])
        original = copy.deepcopy(messages)
        messages.append({"role": "assistant", "content": "old result"})
        golds[tasks[0].id] = "PRIVATE_GOLD_DO_NOT_SEND"
        fresh = messages_for(tasks[0], make_team("baseline")[0])
        self.assertEqual(fresh, original)
        self.assertEqual(len(fresh), 2)
        self.assertNotIn("PRIVATE_GOLD_DO_NOT_SEND", json.dumps(fresh))
        self.assertFalse(hasattr(tasks[0], "gold"))

    def test_bid_parser_rejects_invalid_types_ranges_and_json(self):
        invalid = ["```json\n" + bid() + "\n```", "[]", "null", bid() + " explanation",
                   '{"bid":1,"confidence":90,"reason":"x"}',
                   '{"bid":true,"confidence":true,"reason":"x"}',
                   '{"bid":true,"confidence":"90","reason":"x"}',
                   '{"bid":true,"confidence":NaN,"reason":"x"}',
                   '{"bid":true,"confidence":1e309,"reason":"x"}',
                   '{"bid":true,"confidence":90,"reason":""}',
                   '{"bid":true,"bid":false,"confidence":90,"reason":"x"}',
                   '{"bid":true,"confidence":90,"reason":"x","extra":1}', bid(True, -1), bid(True, 101)]
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_bid(raw)
        self.assertEqual(parse_bid(bid(True, 0)).confidence, 0)
        self.assertEqual(parse_bid(bid(True, 100)).confidence, 100)

    def test_very_large_confidence_is_an_invalid_bid_not_a_crash(self):
        with self.assertRaises(ValueError):
            parse_bid(bid(True, 10 ** 1000))

    def test_bad_gold_and_duplicate_ids_fail_before_any_call(self):
        data = json.loads((ROOT / "tasks.json").read_text())
        data[0]["gold"] = []
        with self.assertRaises(ValueError):
            load_tasks(json.dumps(data))
        data[0]["gold"] = "A"
        data[1]["id"] = data[0]["id"]
        with self.assertRaises(ValueError):
            load_tasks(json.dumps(data))


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.config = runner.load_config(ROOT / "config.json")
        self.events = []
        self.delays = []

    def emit(self, event, **fields):
        self.events.append((event, fields))

    def response(self, content):
        body = {"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                "model": self.config["model"], "provider": "Novita",
                "usage": {"prompt_tokens": 30, "completion_tokens": 20, "cost": 0.00002}}
        return io.BytesIO(json.dumps(body).encode())

    def test_429_retries_once_with_identical_payload_and_keeps_error(self):
        requests = []
        def opener(request, timeout):
            self.assertEqual(request.full_url, ENDPOINT)
            requests.append(request.data)
            if len(requests) == 1:
                raise HTTPError(ENDPOINT, 429, "rate limit", {}, io.BytesIO(b"busy"))
            return self.response(bid())
        client = OpenRouterClient(FAKE_KEY, self.config, opener, self.delays.append)
        raw = client.complete(messages_for(Task("1", "desc"), make_team("baseline")[0]), self.emit, "1", "A", response_format=bid_response_format())
        self.assertTrue(parse_bid(raw).bid)
        self.assertEqual(requests[0], requests[1])
        self.assertEqual(client.request_count, 2)
        self.assertEqual(self.delays, [2])
        self.assertIn("http_error", [event for event, _ in self.events])
        payload = json.loads(requests[0])
        self.assertNotIn("max_tokens", payload)
        self.assertNotIn("max_completion_tokens", payload)
        self.assertEqual(payload["response_format"], bid_response_format())
        self.assertEqual(payload, next(fields["payload"] for event, fields in self.events if event == "request"))
        self.assertEqual(payload["reasoning"], {"enabled": False})
        self.assertEqual(payload["provider"]["only"], ["fireworks"])
        self.assertIs(payload["provider"]["allow_fallbacks"], True)
        self.assertIs(payload["provider"]["require_parameters"], True)
        self.assertEqual(len(payload["messages"]), 2)

    def test_explicit_historical_token_limit_is_forwarded_without_replacement(self):
        requests = []
        self.config["max_tokens"] = 2200
        def opener(request, timeout):
            requests.append(json.loads(request.data))
            return self.response(bid())
        client = OpenRouterClient(FAKE_KEY, self.config, opener)
        client.complete([], self.emit, "1", "A", response_format=bid_response_format())
        self.assertEqual(requests[0]["max_tokens"], 2200)
        self.assertEqual(requests[0]["response_format"], bid_response_format())

    def test_invalid_explicit_token_limit_fails_before_request(self):
        def opener(*_, **__):
            self.fail("invalid max_tokens reached the network")
        for invalid in (None, True, 0, -1, 1.5, "2200"):
            self.config["max_tokens"] = invalid
            client = OpenRouterClient(FAKE_KEY, self.config, opener)
            with self.subTest(invalid=invalid), self.assertRaises(ConfigurationError):
                client.complete([], self.emit, "1", "A", response_format=bid_response_format())
            self.assertEqual(client.request_count, 0)
        self.assertFalse(self.events)

    def test_401_does_not_retry_and_secret_is_redacted(self):
        def opener(*_, **__):
            raise HTTPError(ENDPOINT, 401, "auth", {}, io.BytesIO(FAKE_KEY.encode()))
        client = OpenRouterClient(FAKE_KEY, self.config, opener, self.delays.append)
        with self.assertRaises(CallError):
            client.complete([], self.emit, "1", "A", response_format=bid_response_format())
        self.assertEqual(client.request_count, 1)
        self.assertFalse(self.delays)
        self.assertNotIn(FAKE_KEY, json.dumps(self.events))

    def test_absent_invalid_or_unroutable_format_stops_before_network(self):
        def opener(*_, **__):
            self.fail("invalid response_format reached the network")
        client = OpenRouterClient(FAKE_KEY, self.config, opener)
        for form in (None, {}, {"type": "text"}, {"type": "json_schema"},
                     {"type": "json_schema", "json_schema": {"name": "x", "strict": False, "schema": {}}}):
            with self.subTest(form=form), self.assertRaises(ConfigurationError):
                client.complete([], self.emit, "1", "A", response_format=form)
        self.config["provider"]["require_parameters"] = False
        with self.assertRaises(ConfigurationError):
            client.complete([], self.emit, "1", "A", response_format=bid_response_format())
        self.assertEqual(client.request_count, 0)
        self.assertFalse(self.events)

    def test_unsupported_schema_does_not_fall_back_to_prompt_only(self):
        requests = []
        def opener(request, timeout):
            requests.append(json.loads(request.data))
            raise HTTPError(ENDPOINT, 400, "unsupported schema", {}, io.BytesIO(b"unsupported response_format"))
        client = OpenRouterClient(FAKE_KEY, self.config, opener, self.delays.append)
        with self.assertRaisesRegex(CallError, "HTTP 400"):
            client.complete([], self.emit, "1", "A", response_format=bid_response_format())
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["response_format"], bid_response_format())
        self.assertFalse(self.delays)

    def test_bad_model_json_is_not_repaired_or_retried(self):
        client = OpenRouterClient(FAKE_KEY, self.config, lambda *_, **__: self.response("bad bid"))
        raw = client.complete([], self.emit, "1", "A", response_format=bid_response_format())
        self.assertEqual(raw, "bad bid")
        self.assertEqual(client.request_count, 1)
        with self.assertRaises(ValueError):
            parse_bid(raw)

    def test_transport_timeouts_stop_at_attempt_limit(self):
        def opener(*_, **__):
            raise TimeoutError()
        client = OpenRouterClient(FAKE_KEY, self.config, opener, self.delays.append)
        with self.assertRaises(CallError):
            client.complete([], self.emit, "1", "A", response_format=bid_response_format())
        self.assertEqual(client.request_count, 2)
        self.assertEqual(self.delays, [2])

    def test_global_request_limit_prevents_an_extra_network_attempt(self):
        self.config["max_http_requests"] = 1
        def opener(*_, **__):
            raise TimeoutError()
        client = OpenRouterClient(FAKE_KEY, self.config, opener, self.delays.append)
        with self.assertRaisesRegex(CallError, "budget"):
            client.complete([], self.emit, "1", "A", response_format=bid_response_format())
        self.assertEqual(client.request_count, 1)

    def test_heartbeat_body_cannot_bypass_deadline(self):
        class Heartbeat(io.BytesIO):
            def read1(self, size):
                return b" "
        self.config["response_deadline_seconds"] = 1
        client = OpenRouterClient(FAKE_KEY, self.config, lambda *_, **__: Heartbeat(), self.delays.append)
        with patch("openrouter_client.time.monotonic", side_effect=[0, 2, 3, 5]):
            with self.assertRaisesRegex(CallError, "timeout"):
                client.complete([], self.emit, "1", "A", response_format=bid_response_format())
        self.assertEqual(client.request_count, 2)
        self.assertEqual(self.delays, [2])
        self.assertEqual(sum(event == "transport_error" for event, _ in self.events), 2)

    def test_oversized_response_is_rejected_without_retry(self):
        client = OpenRouterClient(FAKE_KEY, self.config, lambda *_, **__: io.BytesIO(b"x" * 2_000_001))
        with self.assertRaisesRegex(CallError, "size limit"):
            client.complete([], self.emit, "1", "A", response_format=bid_response_format())
        self.assertEqual(client.request_count, 1)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.folder = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_explicit_blank_file_blocks_inherited_credentials(self):
        env_file = self.folder / ".env"
        env_file.write_text("OPENROUTER_API_KEY=\n")
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": FAKE_KEY}, clear=True):
            with self.assertRaises(ConfigurationError):
                read_key(env_file)
        with patch.dict("os.environ", {"OPENAI_API_KEY": FAKE_KEY}, clear=True):
            with self.assertRaises(ConfigurationError):
                read_key()

    def test_local_key_parser_accepts_quotes_and_never_executes_shell(self):
        env_file = self.folder / ".env"
        env_file.write_text(f'export OPENROUTER_API_KEY="{FAKE_KEY}" # local\n')
        self.assertEqual(read_key(env_file), FAKE_KEY)
        env_file.write_text("OPENROUTER_API_KEY=$(echo anything)\n")
        with self.assertRaises(ConfigurationError):
            read_key(env_file)

    def test_crash_is_retained_with_blank_counts_and_new_runs_do_not_overwrite(self):
        class FailingClient:
            key = FAKE_KEY
            request_count = 0
            def complete(self, *args, response_format):
                self.request_count += 1
                raise CallError("fixture timeout")
        with patch.object(runner, "ROOT", self.folder), contextlib.redirect_stdout(io.StringIO()):
            for _ in range(2):
                self.assertFalse(runner.run_one([Task("1", "task")], {"1": "A"}, "baseline",
                                               FailingClient(), {"experiment_id": "offline-test"}))
        with (self.folder / "results.csv").open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["run"], rows[1]["run"])
        self.assertEqual(len(list((self.folder / "logs").glob("*.log"))), 2)
        for row in rows:
            for field in ("tasks", "correct", "messages", "unassigned", "misawards"):
                self.assertEqual(row[field], "")
            self.assertEqual(json.loads(row["note"])["status"], "crashed")

    def test_smoke_output_stays_out_of_scored_results(self):
        class RefusingClient:
            key = FAKE_KEY
            request_count = 0
            def complete(self, *args, response_format):
                self.request_count += 1
                return bid(False)
        with patch.object(runner, "ROOT", self.folder), contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(runner.run_one([Task("1", "task")], {"1": "A"}, "baseline",
                                          RefusingClient(), {"experiment_id": "offline-test"}, smoke=True))
        self.assertFalse((self.folder / "results.csv").exists())
        self.assertFalse((self.folder / "logs").exists())
        self.assertEqual(len(list((self.folder / "diagnostics").glob("*.log"))), 1)

    def test_recorder_keeps_raw_content_and_marks_missing_cost(self):
        stream = io.StringIO()
        recorder = runner.Recorder(stream, "offline-test", FAKE_KEY)
        with contextlib.redirect_stdout(io.StringIO()):
            recorder.emit("response", raw_response="line 1\nline 2")
            recorder.emit("usage", usage={"prompt_tokens": 10, "completion_tokens": 8,
                                         "completion_tokens_details": {"reasoning_tokens": 3}})
        rows = [json.loads(line) for line in stream.getvalue().splitlines()]
        self.assertEqual(rows[0]["raw_response"], "line 1\nline 2")
        self.assertEqual(recorder.usage["reasoning_tokens"], 3)
        self.assertEqual(recorder.usage["cost_missing_responses"], 1)


if __name__ == "__main__":
    unittest.main()
