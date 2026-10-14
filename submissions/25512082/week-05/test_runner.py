"""Tests for scheduling, resumption, parsing, and episode accounting."""

import argparse
import asyncio
import csv
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path

from host import GatewayResult
from market_server import MarketService
from model_client import ToolCall, ToolDecision
from run_experiment import (
    HEADER,
    exception_summary,
    load_completed,
    load_scenarios,
    parse_state,
    run_episode,
    selected_episodes,
    system_prompt,
)


class RunnerDataTests(unittest.TestCase):
    def test_repository_scenarios_satisfy_contract(self):
        base = Path(__file__).resolve().parent
        scenarios = load_scenarios(base / "scenarios.json")
        self.assertEqual(len(scenarios), 4)

    def test_completed_rows_are_loaded_for_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.csv"
            with path.open("w", encoding="utf-8", newline="") as result_file:
                writer = csv.writer(result_file)
                writer.writerow(HEADER)
                writer.writerow([1, "prompt_inject", "S1"] + [""] * 10)

            self.assertEqual(
                load_completed(path), {("prompt_inject", 1, "S1")}
            )

    def test_default_schedule_is_exactly_24_required_episodes(self):
        scenarios = [
            {"id": f"S{i}", "item": "x", "reserve": 1, "budget": 1}
            for i in range(1, 5)
        ]
        args = argparse.Namespace(
            condition=None, run=None, scenario=None, max_episodes=None
        )

        episodes = selected_episodes(scenarios, args)

        self.assertEqual(len(episodes), 24)
        self.assertEqual({item[0] for item in episodes}, {"prompt_inject", "server_inject"})

    def test_prompt_is_identical_between_conditions_by_construction(self):
        scenario = {"item": "lamp", "reserve": 40, "budget": 55}
        self.assertEqual(system_prompt("buyer", scenario), system_prompt("buyer", scenario))
        self.assertNotIn("inject", system_prompt("buyer", scenario))

    def test_parses_direct_and_mcp_structured_state(self):
        state = {"status": "open", "turn": "buyer"}
        self.assertEqual(parse_state(json.dumps(state)), state)
        wrapped = {"structuredContent": state}
        self.assertEqual(parse_state(json.dumps(wrapped)), state)

    def test_nested_exception_summary_keeps_root_cause(self):
        error = ExceptionGroup("task failed", [ValueError("root cause")])
        summary = exception_summary(error)
        self.assertIn("ExceptionGroup: task failed", summary)
        self.assertIn("ValueError: root cause", summary)


class FakeModel:
    def __init__(self, decisions):
        self.decisions = iter(decisions)

    def complete_with_tools(self, _messages, _tools):
        return next(self.decisions)


def decision(name, arguments, call_id):
    return ToolDecision(None, (ToolCall(call_id, name, arguments),))


class DirectGateway:
    def __init__(self, service, token):
        self.service = service
        self.token = token

    async def list_tools(self):
        return []

    async def call_tool(self, name, arguments):
        methods = {
            "get_negotiation": self.service.view,
            "propose": self.service.propose,
            "accept_proposal": self.service.accept,
            "reject_proposal": self.service.reject,
            "refuse": self.service.refuse,
        }
        try:
            state = methods[name](self.token, **arguments)
        except ValueError as exc:
            return GatewayResult(str(exc), is_error=True)
        return GatewayResult(json.dumps(state))


class EpisodeTests(unittest.TestCase):
    def test_episode_records_deal_turns_and_tool_calls(self):
        service = MarketService()
        opened = service.open_negotiation(
            "desk lamp", 40, 55, "server_inject", negotiation_id="neg-1"
        )
        buyer = asyncio.run(service.verify_token(opened["buyer_token"]))
        seller = asyncio.run(service.verify_token(opened["seller_token"]))
        token_map = {opened["buyer_token"]: buyer, opened["seller_token"]: seller}
        model = FakeModel(
            [
                decision("get_negotiation", {"negotiation_id": "neg-1"}, "c1"),
                decision("propose", {"negotiation_id": "neg-1", "price": 45}, "c2"),
                decision("get_negotiation", {"negotiation_id": "neg-1"}, "c3"),
                decision("accept_proposal", {"negotiation_id": "neg-1"}, "c4"),
            ]
        )

        @asynccontextmanager
        async def gateway_factory(raw_token):
            yield DirectGateway(service, token_map[raw_token])

        result = asyncio.run(
            run_episode(
                {"item": "desk lamp", "reserve": 40, "budget": 55},
                "server_inject",
                model,
                opened,
                gateway_factory,
                lambda _message: None,
            )
        )

        self.assertEqual((result.outcome, result.price), ("deal", 45))
        self.assertEqual((result.correct, result.violation), (1, 0))
        self.assertEqual((result.turns, result.tool_calls, result.model_calls), (2, 4, 4))


if __name__ == "__main__":
    unittest.main()
