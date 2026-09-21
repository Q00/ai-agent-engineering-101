"""Offline protocol tests. No API key or network is used."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agents import BidAttempt, BidDecision, make_team, parse_bid
from contract_net import (
    BidEnvelope,
    ContractNetManager,
    choose_winner,
    freeze_responses,
    token_efficiencies,
    valid_candidates,
)
from model_client import ModelReply, OpenAIBackend, Usage
from monitor import Monitor, Profile, validate_result


ROOT = Path(__file__).resolve().parent


def envelope(
    contractor_id: str,
    received_at: float,
    *,
    confidence: float = 80,
    bid: bool = True,
    tokens: int | None = None,
    status: str = "valid",
    auction_id: str = "auction",
    request_id: str | None = None,
    reputation: float = 0.5,
) -> BidEnvelope:
    token_status = "measured" if tokens is not None else "no_history"
    profile = Profile(
        contractor_id,
        1,
        "calculation",
        0,
        reputation,
        tokens,
        0,
        token_status,
    )
    decision = BidDecision(bid, confidence, "test") if status == "valid" else None
    attempt = BidAttempt(status, "", Usage(), decision, "test error" if decision is None else "")
    return BidEnvelope(
        auction_id,
        request_id or f"auction-{contractor_id}",
        contractor_id,
        received_at,
        attempt,
        profile,
    )


class FakeBackend:
    model = "fake-model"
    provider = "offline"
    temperature = 0.2
    reasoning_effort = "none"

    def complete(self, messages, tools=None):
        system = messages[0]["content"]
        joined = "\n".join(str(message.get("content") or "") for message in messages)
        if "Return exactly one JSON object" in system:
            contractor = next(name for name in ("A", "B", "C") if f"contractor {name}" in system)
            is_calculation = "137 * 29" in joined
            bid = contractor == "A" if is_calculation else contractor == "B"
            confidence = 90 if bid else 20
            return ModelReply(
                json.dumps({"bid": bid, "confidence": confidence, "reason": "skill fit"}),
                usage=Usage(10, 5),
            )
        if "Check the proposed Answer" in joined:
            return ModelReply("VERIFIED", usage=Usage(5, 1))
        return ModelReply("Answer: 4014", usage=Usage(10, 2))


class ModelConfigurationTests(unittest.TestCase):
    def test_selected_model_and_reasoning_effort_reach_sdk(self):
        captured = {}

        class FakeCompletions:
            def create(self, **kwargs):
                captured.update(kwargs)
                message = SimpleNamespace(content="ok", tool_calls=[])
                usage = SimpleNamespace(prompt_tokens=3, completion_tokens=2)
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=message)], usage=usage
                )

        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions())
        )
        fake_openai = SimpleNamespace(OpenAI=lambda timeout: fake_client)
        backend = OpenAIBackend()
        with patch.dict(sys.modules, {"openai": fake_openai}):
            reply = backend.complete([{"role": "user", "content": "test"}])

        self.assertEqual(captured["model"], "gpt-5.4-mini")
        self.assertEqual(captured["reasoning_effort"], "none")
        self.assertEqual(captured["temperature"], 0.2)
        self.assertEqual(reply.usage.total_tokens, 5)


class BidParsingTests(unittest.TestCase):
    def test_strict_valid_bid(self):
        decision = parse_bid('{"bid": true, "confidence": 87, "reason": "fit"}')
        self.assertTrue(decision.bid)
        self.assertEqual(decision.confidence, 87)

    def test_rejects_extra_or_wrong_type_fields(self):
        invalid = [
            '{"bid": "true", "confidence": 87, "reason": "fit"}',
            '{"bid": true, "confidence": 101, "reason": "fit"}',
            '{"bid": true, "confidence": 87, "reason": "fit", "auction_id": "fake"}',
            "```json\n{\"bid\": true, \"confidence\": 87, \"reason\": \"fit\"}\n```",
        ]
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_bid(text)

    def test_only_overconfident_c_changes_from_baseline(self):
        baseline = make_team("baseline")
        overconfident = make_team("overconfident")
        self.assertEqual([item.skill for item in baseline], [item.skill for item in overconfident])
        self.assertEqual([bool(item.bid_instruction) for item in overconfident], [False, False, True])
        homogeneous = make_team("homogeneous")
        self.assertEqual(len({item.skill for item in homogeneous}), 1)


class TokenAndScoringTests(unittest.TestCase):
    def test_null_token_rules(self):
        cases = [
            ([None, None, None], [0.5, 0.5, 0.5]),
            ([420, None, None], [0.5, 0.5, 0.5]),
            ([420, 420, None], [0.5, 0.5, 0.5]),
        ]
        for tokens, expected in cases:
            rows = [envelope(name, index, tokens=value) for index, (name, value) in enumerate(zip("ABC", tokens))]
            actual = token_efficiencies(rows)
            self.assertEqual([actual[name] for name in "ABC"], expected)

    def test_measured_and_null_normalization(self):
        rows = [
            envelope("A", 0.1, tokens=400),
            envelope("B", 0.2, tokens=800),
            envelope("C", 0.3),
        ]
        actual = token_efficiencies(rows)
        self.assertAlmostEqual(actual["A"], 1.0)
        self.assertAlmostEqual(actual["B"], 0.0)
        self.assertEqual(actual["C"], 0.5)

    def test_declines_and_failures_are_not_normalized(self):
        rows = [
            envelope("A", 0.1, tokens=400),
            envelope("B", 0.2, tokens=800, bid=False),
            envelope("C", 0.3, status="parse_fail"),
        ]
        candidates = valid_candidates(rows)
        self.assertEqual([row.contractor_id for row in candidates], ["A"])
        self.assertEqual(token_efficiencies(candidates), {"A": 0.5})

    def test_higher_score_beats_faster_response(self):
        rows = [envelope("A", 1.0, confidence=60), envelope("C", 8.0, confidence=95)]
        self.assertEqual(choose_winner(rows, "confidence_only").winner, "C")

    def test_tie_uses_arrival_then_fixed_order(self):
        rows = [envelope("A", 2.0), envelope("B", 1.0)]
        decision = choose_winner(rows, "confidence_only")
        self.assertEqual(decision.winner, "B")
        self.assertTrue(decision.tie_break_used)
        same_time = [envelope("C", 1.0), envelope("A", 1.0)]
        self.assertEqual(choose_winner(same_time, "confidence_only").winner, "A")


class VirtualDeadlineTests(unittest.TestCase):
    expected = {"auction-A": "A", "auction-B": "B", "auction-C": "C"}

    def test_boundary_and_late_high_bid(self):
        rows = [
            envelope("A", 9.0, confidence=70),
            envelope("B", 10.0, confidence=80),
            envelope("C", 10.000001, confidence=99),
        ]
        frozen = freeze_responses(rows, "auction", self.expected, 10.0)
        self.assertEqual([row.contractor_id for row in frozen.accepted], ["A", "B"])
        self.assertEqual(frozen.pending_contractors, ["C"])
        self.assertEqual(choose_winner(valid_candidates(frozen.accepted), "confidence_only").winner, "B")
        self.assertEqual([event.event for event in frozen.rejected], ["late_response"])

    def test_declines_and_errors_finish_without_waiting(self):
        rows = [
            envelope("A", 1.0, bid=False),
            envelope("B", 2.0, status="parse_fail"),
            envelope("C", 3.0, status="request_error"),
        ]
        frozen = freeze_responses(rows, "auction", self.expected, 60.0)
        self.assertFalse(frozen.pending_contractors)
        self.assertFalse(valid_candidates(frozen.accepted))

    def test_missing_response_times_out(self):
        frozen = freeze_responses([envelope("A", 1.0)], "auction", self.expected, 5.0)
        self.assertEqual(frozen.pending_contractors, ["B", "C"])

    def test_duplicate_and_stale_responses_cannot_change_award(self):
        rows = [
            envelope("A", 1.0, confidence=80),
            envelope("A", 2.0, confidence=100, request_id="auction-A"),
            envelope("C", 0.5, confidence=100, auction_id="old-auction", request_id="auction-C"),
        ]
        frozen = freeze_responses(rows, "auction", self.expected, 10.0)
        self.assertEqual([row.contractor_id for row in frozen.accepted], ["A"])
        self.assertEqual(
            {event.event for event in frozen.rejected},
            {"duplicate_response", "stale_response"},
        )


class MonitorAndIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tasks = json.loads((ROOT / "tasks.json").read_text(encoding="utf-8"))

    def test_task_set_was_fixed_across_domains(self):
        self.assertGreaterEqual(len(self.tasks), 5)
        self.assertEqual({task["gold"] for task in self.tasks}, {"A", "B", "C"})
        self.assertEqual({task["domain"] for task in self.tasks}, {"calculation", "writing", "code"})

    def test_deterministic_validators(self):
        self.assertTrue(validate_result(self.tasks[0], "Answer: 4014").success)
        writing = "Answer: 응답 지연 문제와 JSON 파싱 문제를 로그에 남겼습니다. 유효한 후보만으로 낙찰해야 재현 가능합니다."
        self.assertTrue(validate_result(self.tasks[2], writing).success)
        code = "Answer:\n```python\ndef clamp(value, low, high):\n    return min(max(value, low), high)\n```"
        self.assertTrue(validate_result(self.tasks[4], code).success)

    def test_profile_rejects_fake_token_values(self):
        with self.assertRaises(ValueError):
            Profile("A", 1, "code", 0, 0.5, "null", 0, "no_history")
        with self.assertRaises(ValueError):
            Profile("A", 1, "code", 0, 0.5, 0, 0, "no_history")

    def test_reputation_is_smoothed_and_domain_specific(self):
        monitor = Monitor()
        self.assertEqual(monitor.snapshot("A", "calculation").reputation, 0.5)
        monitor.evaluate_and_update("A", self.tasks[0], "Answer: 4014", 100)
        self.assertAlmostEqual(monitor.snapshot("A", "calculation").reputation, 2 / 3)
        self.assertEqual(monitor.snapshot("A", "code").reputation, 0.5)

    def test_offline_manager_awards_and_executes_once(self):
        events = []
        manager = ContractNetManager(FakeBackend(), harness="react", log=events.append)
        result = manager.run_round(
            [self.tasks[0]],
            run_id="offline",
            condition="baseline",
            award_policy="confidence_only",
            async_bids=False,
        )
        self.assertEqual(result.correct, 1)
        self.assertEqual(result.misawards, 0)
        self.assertEqual(result.messages, 5)  # 3 announcements + 1 bid + 1 award
        self.assertEqual(result.validated_successes, 1)
        self.assertEqual(sum(event["event"] == "award" for event in events), 1)

    def test_same_auction_cannot_be_awarded_twice(self):
        manager = ContractNetManager(FakeBackend())
        rows = [envelope("A", 1.0)]
        manager._award_once("auction", rows, "confidence_only")
        with self.assertRaises(RuntimeError):
            manager._award_once("auction", rows, "confidence_only")


if __name__ == "__main__":
    unittest.main(verbosity=2)
