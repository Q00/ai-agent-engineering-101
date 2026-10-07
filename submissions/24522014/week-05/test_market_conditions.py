"""Injection and token-limit checks through the MCP HTTP client."""

import unittest

import httpx2

from market_server import NOTICE
from negotiation_host import connect_party, read_view
from test_negotiation_host import MarketFixture


class ConditionTests(MarketFixture, unittest.IsolatedAsyncioTestCase):
    async def open(self, condition, reserve=90, budget=70, first_role="buyer"):
        async with httpx2.AsyncClient(transport=self.transport) as http:
            response = await http.post("http://127.0.0.1:8001/admin/negotiations",
                headers={"Authorization": f"Bearer {self.admin}"},
                json={"item": "keyboard", "condition": condition, "reserve": reserve,
                      "budget": budget, "first_role": first_role})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    async def test_same_exact_notice_on_each_seller_proposal_in_buyer_view(self):
        for condition in ("prompt_inject", "server_inject"):
            episode = await self.open(condition, reserve=200, budget=160, first_role="seller")
            nid = episode["negotiation_id"]
            async with connect_party("http://127.0.0.1:8001/mcp", episode["tokens"]["seller"], self.transport) as seller:
                async with connect_party("http://127.0.0.1:8001/mcp", episode["tokens"]["buyer"], self.transport) as buyer:
                    await seller.call_tool("propose", {"negotiation_id": nid, "price": 200})
                    await buyer.call_tool("propose", {"negotiation_id": nid, "price": 160})
                    await seller.call_tool("propose", {"negotiation_id": nid, "price": 210})
                    view = read_view(await buyer.call_tool("get_negotiation", {"negotiation_id": nid}))
                    other = read_view(await seller.call_tool("get_negotiation", {"negotiation_id": nid}))
            self.assertEqual([m.get("notice") for m in view["moves"]],
                             [NOTICE.format(raised=230), None, NOTICE.format(raised=230)])
            self.assertTrue(all("notice" not in m for m in other["moves"]))
            self.assertTrue(all("notice" not in m for m in self.app.state.market.negotiations[nid].moves))
            self.assertFalse({"condition", "reserve", "budget", "price_limit"} & view.keys())

    async def test_server_limits_refuse_propose_and_accept_without_consuming_turn(self):
        episode = await self.open("server_inject")
        nid = episode["negotiation_id"]
        args = {"negotiation_id": nid}
        async with connect_party("http://127.0.0.1:8001/mcp", episode["tokens"]["buyer"], self.transport) as buyer:
            async with connect_party("http://127.0.0.1:8001/mcp", episode["tokens"]["seller"], self.transport) as seller:
                before = read_view(await buyer.call_tool("get_negotiation", args))
                result = await buyer.call_tool("propose", {**args, "price": 71})
                self.assertTrue(result.is_error)
                self.assertIn("buyer token budget 70", result.content[0].text)
                self.assertEqual(read_view(await buyer.call_tool("get_negotiation", args)), before)
                self.assertFalse((await buyer.call_tool("propose", {**args, "price": 70})).is_error)
                before = read_view(await seller.call_tool("get_negotiation", args))
                for name, extra in (("propose", {"price": 89}), ("accept_proposal", {})):
                    result = await seller.call_tool(name, {**args, **extra})
                    self.assertTrue(result.is_error)
                    self.assertIn("seller token reserve 90", result.content[0].text)
                    self.assertEqual(read_view(await seller.call_tool("get_negotiation", args)), before)
                self.assertFalse((await seller.call_tool("propose", {**args, "price": 90})).is_error)
                before = read_view(await buyer.call_tool("get_negotiation", args))
                result = await buyer.call_tool("accept_proposal", args)
                self.assertTrue(result.is_error)
                self.assertEqual(read_view(await buyer.call_tool("get_negotiation", args)), before)
                final = read_view(await buyer.call_tool("refuse", args))
                self.assertEqual((final["status"], len(final["moves"])), ("no_deal", 3))

    async def test_prompt_allows_violation_server_refuses_same_accept(self):
        for condition in ("prompt_inject", "server_inject"):
            episode = await self.open(condition, first_role="seller")
            args = {"negotiation_id": episode["negotiation_id"]}
            async with connect_party("http://127.0.0.1:8001/mcp", episode["tokens"]["seller"], self.transport) as seller:
                await seller.call_tool("propose", {**args, "price": 90})
            async with connect_party("http://127.0.0.1:8001/mcp", episode["tokens"]["buyer"], self.transport) as buyer:
                result = await buyer.call_tool("accept_proposal", args)
            self.assertEqual(result.is_error, condition == "server_inject")
            party = self.app.state.market.parties[episode["tokens"]["buyer"]]
            self.assertEqual(party.price_limit, 70 if condition == "server_inject" else None)

    async def test_boundary_deal_is_valid(self):
        episode = await self.open("server_inject", reserve=40, budget=40)
        args = {"negotiation_id": episode["negotiation_id"]}
        async with connect_party("http://127.0.0.1:8001/mcp", episode["tokens"]["buyer"], self.transport) as buyer:
            await buyer.call_tool("propose", {**args, "price": 40})
        async with connect_party("http://127.0.0.1:8001/mcp", episode["tokens"]["seller"], self.transport) as seller:
            final = read_view(await seller.call_tool("accept_proposal", args))
        self.assertEqual((final["status"], final["price"]), ("deal", 40))

    async def test_admin_rejects_missing_or_non_integer_limits(self):
        for extra in ({}, {"reserve": 90, "budget": True}, {"reserve": 90, "budget": "70"}):
            async with httpx2.AsyncClient(transport=self.transport) as http:
                response = await http.post("http://127.0.0.1:8001/admin/negotiations",
                    headers={"Authorization": f"Bearer {self.admin}"},
                    json={"item": "keyboard", "condition": "server_inject", **extra})
            self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
