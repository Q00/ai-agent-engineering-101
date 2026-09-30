"""Offline behavioral checks; all model replies are scripted, never live API calls."""

import copy
import csv
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from analyze_results import AuditError, audit, render_report
from model_client import ModelCallError, ModelReply, OpenAIBackend, Usage
from negotiation import run_episode
from prompts import FORMAT, system_prompt
from protocol import read_message
from run_experiment import HEADER, JsonlLogger, load_scenarios, read_events, read_results, run_batch


SCENARIO = {"id": 1, "item": "a test bicycle", "reserve": 711, "budget": 923}


def structured(act, price=None):
    return json.dumps({"performative": act, "content": {"price": price}})


class ScriptedBackend:
    """Record requests as sent, independent of subsequent history mutations."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, messages):
        self.calls.append(copy.deepcopy(messages))
        if not self.replies:
            raise AssertionError("Unexpected extra model call")
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply if isinstance(reply, ModelReply) else ModelReply(reply)


class ProtocolTests(unittest.TestCase):
    def parse(self, condition, message, reader_reply=None):
        backend = ScriptedBackend(*([] if reader_reply is None else [reader_reply]))
        events = []
        result = read_message(
            condition,
            message,
            [{"role": "buyer", "content": message}],
            backend.complete,
            events.append,
        )
        return result, backend, events

    def test_structured_does_not_call_reader(self):
        parsed, backend, _ = self.parse("structured", structured("propose", 800))
        self.assertTrue(parsed.ok)
        self.assertEqual((parsed.performative, parsed.price), ("propose", 800))
        self.assertEqual(backend.calls, [])

    def test_structured_leading_object_ignores_trailing_counterprice(self):
        message = '  {"performative":"reject-proposal","content":{"price":null}} I need 900.'
        parsed, backend, _ = self.parse("structured", message)
        self.assertTrue(parsed.ok)
        self.assertEqual(parsed.performative, "reject-proposal")
        self.assertIsNone(parsed.price)
        self.assertIn("I need 900.", parsed.trailing)
        self.assertEqual(backend.calls, [])

    def test_structured_rejects_invalid_types_and_missing_fields(self):
        invalid = [
            "not JSON", "[]", "null", "{}",
            '{"performative":"query-ref","content":{"price":800}}',
            '{"performative":"propose"}',
            '{"performative":"propose","content":800}',
            '{"performative":"propose","content":{}}',
            '```json\n' + structured("propose", 800) + '\n```',
            '{"performative":"refuse","content":{"price":true}}',
        ]
        invalid.extend(structured("propose", price) for price in (None, True, False, 800.0, "800", -1))
        for message in invalid:
            with self.subTest(message=message):
                parsed, backend, _ = self.parse("structured", message)
                self.assertFalse(parsed.ok)
                self.assertTrue(parsed.error)
                self.assertEqual(backend.calls, [])

    def test_structured_duplicate_keys_are_ambiguous_and_rejected(self):
        for message in (
            '{"performative":"refuse","performative":"propose","content":{"price":800}}',
            '{"performative":"propose","content":{"price":800,"price":900}}',
        ):
            with self.subTest(message=message):
                parsed, _, _ = self.parse("structured", message)
                self.assertFalse(parsed.ok)

    def test_zero_is_valid_price(self):
        parsed, _, _ = self.parse("structured", structured("propose", 0))
        self.assertTrue(parsed.ok)
        self.assertEqual(parsed.price, 0)

    def test_tagged_uses_tag_even_when_body_disagrees(self):
        for act in ("accept-proposal", "reject-proposal", "refuse"):
            with self.subTest(act=act):
                parsed, backend, _ = self.parse("tagged", f"({act}) I offer 800 instead.")
                self.assertTrue(parsed.ok)
                self.assertEqual(parsed.performative, act)
                self.assertIsNone(parsed.price)
                self.assertEqual(backend.calls, [])

    def test_tagged_price_reader_cannot_override_tag(self):
        for reply in ('{"performative":"refuse","price":800}', '{"price":800}'):
            with self.subTest(reply=reply):
                parsed, backend, _ = self.parse("tagged", "(propose) I offer 800.", reply)
                self.assertTrue(parsed.ok)
                self.assertEqual((parsed.performative, parsed.price), ("propose", 800))
                self.assertEqual(len(backend.calls), 1)

    def test_tagged_rejects_missing_unanchored_unknown_tags_without_reader(self):
        for message in ("I propose 800.", "Hello (propose) 800", "(query-ref) What price?", "(propose)800"):
            with self.subTest(message=message):
                parsed, backend, _ = self.parse("tagged", message)
                self.assertFalse(parsed.ok)
                self.assertEqual(backend.calls, [])

    def test_tagged_invalid_price_is_format_error(self):
        for price in (None, True, 800.5, "800", -1):
            with self.subTest(price=price):
                parsed, backend, _ = self.parse(
                    "tagged", "(propose) I offer 800.", json.dumps({"price": price})
                )
                self.assertFalse(parsed.ok)
                self.assertEqual(len(backend.calls), 1)

    def test_free_uses_reader_even_for_question(self):
        parsed, backend, _ = self.parse(
            "free", "What is your asking price?", '{"performative":"refuse","price":null}'
        )
        self.assertTrue(parsed.ok)
        self.assertEqual(parsed.performative, "refuse")
        self.assertEqual(len(backend.calls), 1)
        self.assertIn("What is your asking price?", json.dumps(backend.calls[0]))

    def test_free_requires_complete_valid_reader_json(self):
        for reply in (
            "I think they propose 800.",
            '{"performative":"propose","price":800} Explanation',
            '{"performative":"propose","price":true}',
            '{"performative":"propose","price":800.0}',
            '{"performative":"propose","price":null}',
            '{"performative":"query-ref","price":null}',
            '{"performative":"propose","price":800,"price":900}',
        ):
            with self.subTest(reply=reply):
                parsed, backend, _ = self.parse("free", "I offer 800.", reply)
                self.assertFalse(parsed.ok)
                self.assertEqual(len(backend.calls), 1)


class EpisodeTests(unittest.TestCase):
    def run_script(self, *replies, condition="structured", scenario=None, max_turns=8):
        backend = ScriptedBackend(*replies)
        events = []
        result = run_episode(
            scenario or SCENARIO, condition, backend, max_turns=max_turns, log=events.append
        )
        return result, backend, events

    def test_accept_uses_other_partys_latest_offer_not_accept_price(self):
        result, backend, _ = self.run_script(
            structured("propose", 800), structured("propose", 860),
            structured("propose", 840), structured("accept-proposal", 999),
        )
        self.assertEqual((result["outcome"], result["price"]), ("deal", 840))
        self.assertEqual((result["correct"], result["violation"]), (1, 0))
        self.assertEqual((result["turns"], result["reader_calls"]), (4, 0))
        self.assertEqual(len(backend.calls), 4)

    def test_history_roles_and_private_limits_are_isolated(self):
        buyer_offer = structured("propose", 800)
        seller_offer = structured("propose", 850)
        _, backend, _ = self.run_script(buyer_offer, seller_offer, structured("accept-proposal"))
        buyer_system = backend.calls[0][0]["content"]
        seller_system = backend.calls[1][0]["content"]
        self.assertIn("923", buyer_system)
        self.assertNotIn("711", buyer_system)
        self.assertIn("711", seller_system)
        self.assertNotIn("923", seller_system)
        self.assertIn({"role": "user", "content": buyer_offer}, backend.calls[1])
        self.assertIn({"role": "assistant", "content": buyer_offer}, backend.calls[2])
        self.assertIn({"role": "user", "content": seller_offer}, backend.calls[2])
        self.assertEqual(sum(m["role"] == "system" for m in backend.calls[2]), 1)

    def test_reject_counterprice_does_not_create_offer(self):
        result, _, _ = self.run_script(
            "(propose) I offer 800.", '{"price":800}',
            "(reject-proposal) No, I need at least 900.",
            "(accept-proposal) Agreed.", "(refuse) Goodbye.",
            condition="tagged",
        )
        self.assertEqual((result["outcome"], result["turns"]), ("no_deal", 4))
        self.assertEqual(result["reader_calls"], 1)
        self.assertEqual(result["format_errors"], 0)

    def test_premature_accept_continues_without_inventing_price(self):
        result, _, events = self.run_script(
            structured("accept-proposal"), structured("propose", 800), structured("accept-proposal")
        )
        self.assertEqual((result["outcome"], result["price"], result["turns"]), ("deal", 800, 3))
        self.assertEqual(result["format_errors"], 0)
        self.assertTrue(events)

    def test_limit_violation_is_observed_not_blocked(self):
        for price in (600, 1000):
            with self.subTest(price=price):
                result, _, _ = self.run_script(structured("propose", price), structured("accept-proposal"))
                self.assertEqual((result["outcome"], result["price"]), ("deal", price))
                self.assertEqual((result["correct"], result["violation"]), (0, 1))

    def test_invalid_message_is_delivered_and_counted_then_conversation_continues(self):
        result, backend, _ = self.run_script("not JSON", structured("refuse"))
        self.assertEqual((result["outcome"], result["turns"], result["format_errors"]), ("no_deal", 2, 1))
        self.assertIn({"role": "user", "content": "not JSON"}, backend.calls[1])

    def test_turn_limit_is_open_not_correct_even_when_deal_impossible(self):
        impossible = dict(SCENARIO, reserve=1000)
        result, backend, _ = self.run_script(
            *[structured("reject-proposal")] * 8, scenario=impossible
        )
        self.assertEqual(result["outcome"], "open")
        self.assertEqual((result["correct"], result["violation"], result["turns"]), (0, 0, 8))
        self.assertIn(result["price"], (None, ""))
        self.assertEqual(len(backend.calls), 8)

    def test_refusal_is_correct_only_when_deal_impossible(self):
        for reserve, correct in ((711, 0), (1000, 1)):
            with self.subTest(reserve=reserve):
                result, _, _ = self.run_script(structured("refuse"), scenario=dict(SCENARIO, reserve=reserve))
                self.assertEqual((result["outcome"], result["correct"], result["violation"]), ("no_deal", correct, 0))

    def test_free_question_classified_as_refuse_is_preserved(self):
        result, backend, _ = self.run_script(
            "What is your asking price?", '{"performative":"refuse","price":null}', condition="free"
        )
        self.assertEqual((result["outcome"], result["turns"], result["reader_calls"]), ("no_deal", 1, 1))
        self.assertEqual(len(backend.calls), 2)

    def test_malformed_reader_response_still_counts_call(self):
        result, _, _ = self.run_script(
            "I offer 800.", "not JSON", "Goodbye.", '{"performative":"refuse","price":null}',
            condition="free",
        )
        self.assertEqual((result["turns"], result["reader_calls"], result["format_errors"]), (2, 2, 1))

    def test_reader_has_transcript_but_no_private_system_prompts(self):
        _, backend, _ = self.run_script(
            "I offer 800.", '{"performative":"propose","price":800}',
            "Agreed.", '{"performative":"accept-proposal","price":null}', condition="free",
        )
        reader_request = json.dumps(backend.calls[3])
        self.assertIn("I offer 800.", reader_request)
        self.assertIn("Agreed.", reader_request)
        self.assertNotIn("711", reader_request)
        self.assertNotIn("923", reader_request)

    def test_generation_failure_retains_partial_counts_and_redacts_error_payload(self):
        result, _, events = self.run_script(
            structured("propose", 800), RuntimeError("sensitive-provider-payload")
        )
        self.assertEqual(result["outcome"], "")
        self.assertEqual(result["turns"], 1)
        self.assertEqual(result["metrics"]["agent_calls"], 2)
        self.assertEqual(result["reader_calls"], 0)
        self.assertIn("RuntimeError", result["note"])
        self.assertNotIn("sensitive-provider-payload", json.dumps([result, events]))

    def test_failed_reader_call_is_counted_without_fabricating_format_error(self):
        result, _, _ = self.run_script("I offer 800.", RuntimeError("failed"), condition="free")
        self.assertEqual(result["outcome"], "")
        self.assertEqual((result["turns"], result["reader_calls"], result["format_errors"]), (1, 1, 0))

    def test_keyboard_interrupt_retains_episode_and_interrupt_flag(self):
        result, _, _ = self.run_script(structured("propose", 800), KeyboardInterrupt())
        self.assertTrue(result["interrupted"])
        self.assertEqual(result["outcome"], "")
        self.assertEqual(result["turns"], 1)

    def test_reader_retries_count_actual_attempts_and_unknown_usage(self):
        result, _, _ = self.run_script(
            ModelReply("Goodbye.", Usage(10, 2)),
            ModelReply('{"performative":"refuse","price":null}', Usage(None, None), retries=2),
            condition="free",
        )
        self.assertEqual(result["reader_calls"], 3)
        self.assertEqual(result["metrics"]["agent_calls"], 1)
        self.assertEqual(result["metrics"]["retries"], 2)
        self.assertIsNone(result["metrics"]["total_tokens"])

    def test_exhausted_reader_retries_remain_counted(self):
        result, _, _ = self.run_script(
            "I offer 800.", ModelCallError("RateLimitError", 429, retries=2), condition="free"
        )
        self.assertEqual(result["reader_calls"], 3)
        self.assertEqual(result["metrics"]["retries"], 2)
        self.assertIsNone(result["metrics"]["total_tokens"])

    def test_successful_retry_usage_cannot_fill_missing_failed_attempt_usage(self):
        result, _, _ = self.run_script(
            ModelReply(structured("refuse"), Usage(10, 2), retries=1)
        )
        self.assertEqual(result["metrics"]["agent_calls"], 2)
        self.assertEqual(result["metrics"]["retries"], 1)
        self.assertIsNone(result["metrics"]["total_tokens"])


class ModelClientTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"OPENAI_BASE_URL": "https://api.openai.com/v1"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    @staticmethod
    def response(usage=True):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="hello"))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2) if usage else None,
            model="test-model",
        )

    @staticmethod
    def provider_error(status):
        error = RuntimeError("sensitive-provider-payload")
        error.status_code = status
        return error

    def test_rate_limit_retries_have_observable_exponential_backoff(self):
        create = Mock(side_effect=[self.provider_error(429), self.provider_error(429), self.response()])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        delays, events = [], []
        backend = OpenAIBackend(client=client, max_retries=3, sleeper=delays.append, on_retry=events.append)
        reply = backend.complete([{"role": "user", "content": "hello"}])
        self.assertEqual(create.call_count, 3)
        self.assertEqual(delays, [1.0, 2.0])
        self.assertEqual(reply.retries, 2)
        self.assertEqual(reply.usage.total_tokens, 12)
        self.assertEqual(len(events), 2)
        self.assertNotIn("sensitive-provider-payload", json.dumps(events))
        self.assertEqual(create.call_args.kwargs["reasoning_effort"], "none")
        self.assertEqual(create.call_args.kwargs["temperature"], 0.2)

    def test_bad_request_fails_once_without_sleep_or_provider_payload(self):
        create = Mock(side_effect=self.provider_error(400))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        sleeper = Mock()
        backend = OpenAIBackend(client=client, sleeper=sleeper)
        with self.assertRaises(ModelCallError) as caught:
            backend.complete([{"role": "user", "content": "hello"}])
        self.assertEqual(create.call_count, 1)
        sleeper.assert_not_called()
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(caught.exception.attempts, 1)
        self.assertNotIn("sensitive-provider-payload", str(caught.exception))

    def test_sdk_automatic_retries_disabled_to_avoid_unobserved_calls(self):
        create = Mock(return_value=self.response())
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        constructor = Mock(return_value=client)
        with patch.dict(sys.modules, {"openai": SimpleNamespace(OpenAI=constructor)}):
            OpenAIBackend().complete([{"role": "user", "content": "hello"}])
        self.assertEqual(constructor.call_args.kwargs["max_retries"], 0)

    def test_missing_usage_is_unknown_and_propagates_through_addition(self):
        create = Mock(return_value=self.response(usage=False))
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        reply = OpenAIBackend(client=client).complete([{"role": "user", "content": "hello"}])
        self.assertIsNone(reply.usage.total_tokens)
        self.assertIsNone((Usage(10, 2) + reply.usage).total_tokens)
        self.assertEqual((Usage(10, 2) + Usage(5, 3)).total_tokens, 20)


class PromptTests(unittest.TestCase):
    def test_only_format_paragraph_changes_across_conditions(self):
        for role in ("buyer", "seller"):
            prefixes = []
            for condition, paragraph in FORMAT.items():
                prompt = system_prompt(role, "a test bicycle", 800, condition)
                self.assertTrue(prompt.endswith(paragraph))
                prefixes.append(prompt[:-len(paragraph)])
            self.assertEqual(len(set(prefixes)), 1)


class RefusingBackend:
    """End each synthetic episode immediately, preserving all three protocols."""

    def __init__(self, fail_once=None):
        self.calls = []
        self.model = "offline-test-model"
        self.fail_once = fail_once

    def configuration(self):
        return {"model": self.model, "temperature": 0.2}

    def complete(self, messages):
        self.calls.append(copy.deepcopy(messages))
        if self.fail_once is not None:
            error, self.fail_once = self.fail_once, None
            raise error
        prompt = messages[0]["content"]
        if "You are an observer" in prompt:
            return ModelReply('{"performative":"refuse","price":null}')
        if "Start your message with exactly one performative tag" in prompt:
            return ModelReply("(refuse) Goodbye.")
        if "Reply with exactly one JSON object" in prompt:
            return ModelReply(structured("refuse"))
        return ModelReply("Goodbye.")


class RunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="week04-offline-tests-")
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name) / "experiment"
        self.scenarios = load_scenarios(Path(__file__).parent / "scenarios.json")
        self.backend = RefusingBackend()
        quiet = redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def batch(self, backend=None, **kwargs):
        return run_batch(backend or self.backend, self.scenarios, self.output, repeats=1, **kwargs)

    def remove_fixture_first_csv_row(self):
        """Simulate a lost CSV write using only synthetic temporary test data."""
        path = self.output / "results.csv"
        rows = read_results(path)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, HEADER)
            writer.writeheader()
            writer.writerows(rows[1:])
        return rows[0]

    def test_three_conditions_run_once_then_resume_without_new_calls(self):
        rows = self.batch()
        self.assertEqual(len(rows), 12)
        self.assertEqual(len(read_results(self.output / "results.csv")), 12)
        self.assertEqual(len(list((self.output / "logs").glob("*.log"))), 3)
        self.assertEqual(len(self.backend.calls), 16)
        self.assertEqual({row["condition"] for row in rows}, {"free", "tagged", "structured"})
        self.assertEqual(self.batch(), [])
        self.assertEqual(len(self.backend.calls), 16)
        self.assertEqual(len(read_results(self.output / "results.csv")), 12)

    def test_changed_model_turn_limit_scenario_or_config_rejects_resume(self):
        self.batch()
        before = len(self.backend.calls)
        self.backend.model = "different-model"
        with self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
            self.batch()
        self.backend.model = "offline-test-model"
        with self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
            self.batch(max_turns=7)
        with self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
            self.batch(config={"changed_setting": True})
        changed_scenarios = copy.deepcopy(self.scenarios)
        changed_scenarios[0]["budget"] += 1
        with self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
            run_batch(self.backend, changed_scenarios, self.output, repeats=1)
        self.assertEqual(len(self.backend.calls), before)

    def test_crashed_result_is_retained_and_skipped_on_resume(self):
        backend = RefusingBackend(fail_once=RuntimeError("synthetic-failure"))
        rows = self.batch(backend)
        self.assertEqual(len(rows), 12)
        self.assertEqual(rows[0]["outcome"], "")
        self.assertIn("RuntimeError", rows[0]["note"])
        calls = len(backend.calls)
        self.assertEqual(self.batch(backend), [])
        self.assertEqual(len(backend.calls), calls)
        self.assertEqual(len(read_results(self.output / "results.csv")), 12)

    def test_recorded_episode_recovers_lost_csv_write_without_rerun(self):
        self.batch()
        missing_row = self.remove_fixture_first_csv_row()
        calls = len(self.backend.calls)
        recovered = self.batch()
        self.assertEqual(len(recovered), 1)
        self.assertEqual(len(self.backend.calls), calls)
        self.assertEqual(str(recovered[0]["scenario"]), missing_row["scenario"])
        self.assertEqual(recovered[0]["outcome"], missing_row["outcome"])
        self.assertEqual(len(read_results(self.output / "results.csv")), 12)
        events = read_events(self.output / "logs" / "free-run-01.log")
        self.assertTrue(any(event["event"] == "csv_recovered" for event in events))

    def test_abrupt_exit_before_result_recovers_as_crash_and_preserves_log(self):
        killed = RefusingBackend(fail_once=SystemExit(9))
        with self.assertRaises(SystemExit):
            self.batch(killed)
        path = self.output / "logs" / "free-run-01.log"
        original = path.read_bytes()
        rows = self.batch()
        self.assertEqual(len(rows), 12)
        self.assertEqual(rows[0]["outcome"], "")
        self.assertIn("interrupted_before_result", rows[0]["note"])
        self.assertEqual(len(self.backend.calls), 14)
        self.assertTrue(path.read_bytes().startswith(original))

    def test_persisted_result_before_record_is_recovered_without_losing_outcome(self):
        original_call = JsonlLogger.__call__

        def exit_before_record(logger, event):
            if event["event"] == "episode_record":
                raise SystemExit(9)
            original_call(logger, event)

        with patch.object(JsonlLogger, "__call__", exit_before_record):
            with self.assertRaises(SystemExit):
                self.batch()
        self.assertEqual(len(self.backend.calls), 2)
        rows = self.batch()
        self.assertEqual(len(rows), 12)
        self.assertEqual(rows[0]["outcome"], "no_deal")
        self.assertEqual(rows[0]["turns"], 1)
        self.assertEqual(rows[0]["reader_calls"], 1)
        self.assertEqual(len(self.backend.calls), 16)

    def test_provider_configuration_failure_stops_batch_after_one_preserved_row(self):
        backend = RefusingBackend(fail_once=ModelCallError("AuthenticationError", 401, 0))
        rows = self.batch(backend)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(rows[0]["outcome"], "")
        self.assertIn("status_code=401", rows[0]["note"])

    def test_cli_missing_key_fails_before_creating_experiment_outputs(self):
        environment = dict(os.environ)
        environment.pop("OPENAI_API_KEY", None)
        process = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "run_experiment.py"),
             "--output-dir", str(self.output)],
            env=environment, text=True, capture_output=True, check=False,
        )
        self.assertEqual(process.returncode, 2)
        self.assertIn("OPENAI_API_KEY is missing", process.stderr)
        self.assertFalse(self.output.exists())

    def test_scenario_loader_rejects_boolean_limits_and_duplicate_ids(self):
        path = self.output.parent / "invalid-scenarios.json"
        for mutate in (
            lambda values: values[0].update(budget=True),
            lambda values: values[1].update(id=values[0]["id"]),
            lambda values: values[0].update(reserve=-1),
        ):
            with self.subTest(mutate=mutate):
                values = copy.deepcopy(self.scenarios)
                mutate(values)
                path.write_text(json.dumps(values), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_scenarios(path)


class AnalyzerTests(unittest.TestCase):
    """Audit synthetic records only; the saved live experiment is never touched."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="week04-audit-tests-")
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name) / "experiment"
        self.scenarios = load_scenarios(Path(__file__).parent / "scenarios.json")
        self.backend = RefusingBackend()
        quiet = redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def batch(self, backend=None):
        return run_batch(backend or self.backend, self.scenarios, self.output, repeats=3)

    def test_complete_scripted_experiment_audits_and_renders_all_rows(self):
        self.batch()
        report = audit(self.output)
        self.assertEqual(len(report["rows"]), 36)
        self.assertEqual(report["logs"], 9)
        self.assertEqual(report["warnings"], [])
        self.assertEqual(report["diagnostics"]["first_turn_no_deal"], 36)
        rendered = render_report(report)
        self.assertIn("Audit passed: 36 episode rows, 9 run logs", rendered)
        self.assertEqual(sum(line.startswith("| free |") for line in rendered.splitlines()), 1)
        self.assertEqual(sum(line.startswith("| tagged |") for line in rendered.splitlines()), 1)
        self.assertEqual(sum(line.startswith("| structured |") for line in rendered.splitlines()), 1)
        self.assertEqual(sum(line.startswith(("| 1 |", "| 2 |", "| 3 |"))
                             for line in rendered.splitlines()), 36)

    def test_replay_rejects_consistently_tampered_csv_and_summary_events(self):
        self.batch()
        csv_path = self.output / "results.csv"
        rows = read_results(csv_path)
        target_scenario = rows[0]["scenario"]
        rows[0]["outcome"] = "open"
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, HEADER)
            writer.writeheader()
            writer.writerows(rows)
        log_path = self.output / "logs" / "free-run-01.log"
        events = read_events(log_path)
        for event in events:
            if str(event.get("scenario")) != target_scenario:
                continue
            if event["event"] == "episode_result":
                event["outcome"] = "open"
            elif event["event"] == "episode_record":
                event["row"]["outcome"] = "open"
        # Leave messages, parse results, and transitions intact. Merely comparing
        # CSV to the two summary events would now miss this fabricated outcome.
        log_path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
        with self.assertRaisesRegex(AuditError, "outcome=.*replay expects"):
            audit(self.output)

    def test_recovered_crash_unknown_metrics_stay_unknown_in_summary(self):
        with self.assertRaises(SystemExit):
            self.batch(RefusingBackend(fail_once=SystemExit(9)))
        self.batch()
        report = audit(self.output)
        crashed = [row for row in report["rows"] if not row["outcome"]]
        self.assertEqual(len(crashed), 1)
        for field in ("price", "correct", "violation", "turns", "format_errors", "reader_calls"):
            self.assertEqual(crashed[0][field], "")
        summary = next(line for line in render_report(report).splitlines()
                       if line.startswith("| free |"))
        cells = [cell.strip() for cell in summary.split("|")[1:-1]]
        self.assertIn("1 unknown", cells[1])
        self.assertEqual(cells[2], "0 / 11 / 0 / 1")
        self.assertEqual(cells[3], "0 (+1 unknown)")
        self.assertEqual(cells[4], "1.00 (+1 unknown)")
        self.assertEqual(cells[5], "0 (+1 unknown)")
        self.assertEqual(cells[6], "11 (+1 unknown)")


if __name__ == "__main__":
    unittest.main()
