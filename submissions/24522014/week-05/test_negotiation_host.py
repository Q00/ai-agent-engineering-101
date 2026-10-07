"""MCP HTTP integration with scripted model responses, never real model calls."""

import asyncio
import copy
import json
import secrets
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx2

from market_server import create_app
from negotiation_host import ChatModel, NegotiationHost, connect_party, system_prompt


class ScriptedModel:
    def __init__(self, actions):
        self.actions = iter(actions)
        self.requests = []

    async def complete(self, messages, tools, tool_choice="auto"):
        self.requests.append(copy.deepcopy(messages))
        nid = messages[1]["content"].split("negotiation_id=", 1)[1]
        if isinstance(tool_choice, dict):
            name, args = "get_negotiation", {}
        else:
            name, args = next(self.actions)
        return {"role": "assistant", "tool_calls": [{
            "id": f"call-{len(self.requests)}", "type": "function",
            "function": {"name": name, "arguments": json.dumps({"negotiation_id": nid, **args})},
        }]}


class MarketFixture:
    async def asyncSetUp(self):
        self.admin = secrets.token_urlsafe(32)
        self.app = create_app(self.admin)
        self.transport = httpx2.ASGITransport(app=self.app)
        ready, self.stop = asyncio.Event(), asyncio.Event()

        async def lifespan():
            async with self.app.router.lifespan_context(self.app):
                ready.set()
                await self.stop.wait()

        self.task = asyncio.create_task(lifespan())
        await ready.wait()
        async with httpx2.AsyncClient(transport=self.transport) as http:
            response = await http.post(
                "http://127.0.0.1:8001/admin/negotiations",
                headers={"Authorization": f"Bearer {self.admin}"}, json={"item": "bicycle"},
            )
        self.episode = response.json()
        self.logs = []

    async def asyncTearDown(self):
        self.stop.set()
        await self.task

    def host(self, mcp, role, model):
        return NegotiationHost(
            mcp, role, self.episode["negotiation_id"], "bicycle",
            150 if role == "buyer" else 120, model, self.logs.append,
        )


class HostHTTPTests(MarketFixture, unittest.IsolatedAsyncioTestCase):
    async def test_distinct_party_tokens_read_and_make_one_move(self):
        model = ScriptedModel([("propose", {"price": 130}), ("accept_proposal", {})])
        async with connect_party("http://127.0.0.1:8001/mcp", self.episode["tokens"]["buyer"], self.transport) as buyer:
            async with connect_party("http://127.0.0.1:8001/mcp", self.episode["tokens"]["seller"], self.transport) as seller:
                first = await self.host(buyer, "buyer", model).run_turn()
                second = await self.host(seller, "seller", model).run_turn()
        self.assertEqual((first.state["role"], first.state["turn"]), ("buyer", "seller"))
        self.assertEqual((second.state["role"], second.state["status"], second.state["price"]), ("seller", "deal", 130))
        self.assertEqual((first.tool_calls, second.tool_calls), (2, 2))
        for token in (self.admin, *self.episode["tokens"].values()):
            self.assertNotIn(token, "\n".join(self.logs))
            self.assertNotIn(token, json.dumps(model.requests))

    async def test_refusal_retries_on_same_turn(self):
        model = ScriptedModel([("accept_proposal", {}), ("propose", {"price": 130})])
        async with connect_party("http://127.0.0.1:8001/mcp", self.episode["tokens"]["buyer"], self.transport) as buyer:
            result = await self.host(buyer, "buyer", model).run_turn()
        self.assertEqual((result.refused_calls, result.tool_calls, len(result.state["moves"])), (1, 3, 1))
        self.assertIn("No outstanding proposal", model.requests[-1][-1]["content"])

    async def test_host_measures_but_does_not_enforce_prompt_limit(self):
        model = ScriptedModel([("propose", {"price": 170})])
        async with connect_party("http://127.0.0.1:8001/mcp", self.episode["tokens"]["buyer"], self.transport) as buyer:
            result = await self.host(buyer, "buyer", model).run_turn()
        self.assertEqual(result.attempted_violations, 1)
        self.assertEqual(result.state["current_proposal"]["price"], 170)

    async def test_wrong_token_does_not_make_a_move(self):
        model = ScriptedModel([])
        async with connect_party("http://127.0.0.1:8001/mcp", self.episode["tokens"]["seller"], self.transport) as mcp:
            with self.assertRaisesRegex(RuntimeError, "token role"):
                await self.host(mcp, "buyer", model).run_turn()
        state = self.app.state.market.get(self.episode["tokens"]["buyer"], self.episode["negotiation_id"])
        self.assertEqual(state["moves"], [])


class ModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_choices_retries_with_same_settings(self):
        message = SimpleNamespace(model_dump=lambda **_: {"role": "assistant", "content": "ok"})
        create = AsyncMock(side_effect=[SimpleNamespace(choices=None), SimpleNamespace(choices=[SimpleNamespace(message=message)])])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        sleep, logs = AsyncMock(), []
        model = ChatModel(client=client, pace_seconds=0, sleep=sleep, log=logs.append)
        result = await model.complete([], [])
        self.assertEqual(result["content"], "ok")
        self.assertEqual((model.calls, model.retries), (2, 1))
        self.assertIn(5, [call.args[0] for call in sleep.await_args_list])
        self.assertEqual(create.await_args_list[0].kwargs, create.await_args_list[1].kwargs)
        self.assertEqual(create.await_args.kwargs["extra_body"], {"reasoning": {"enabled": False}})

    async def test_private_limits_stay_in_each_partys_prompt(self):
        self.assertIn("150", system_prompt("buyer", "bicycle", 150))
        self.assertNotIn("120", system_prompt("buyer", "bicycle", 150))
        self.assertIn("120", system_prompt("seller", "bicycle", 120))
        self.assertNotIn("150", system_prompt("seller", "bicycle", 120))


if __name__ == "__main__":
    unittest.main(verbosity=2)
