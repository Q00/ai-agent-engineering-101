"""Step 1 regression checks using real MCP HTTP handlers in an ASGI transport.

No model, API key, network listener, or printed bearer tokens are needed.
"""

import secrets
import unittest

import httpx2

from market_server import create_app


class MarketHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.admin = secrets.token_urlsafe(32)
        self.app = create_app(self.admin)
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.http = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=self.app), base_url="http://127.0.0.1:8001"
        )
        self.episode = await self.open_negotiation()
        self.nid = self.episode["negotiation_id"]
        self.buyer = self.episode["tokens"]["buyer"]
        self.seller = self.episode["tokens"]["seller"]

    async def asyncTearDown(self):
        await self.http.aclose()
        await self.lifespan.__aexit__(None, None, None)

    async def open_negotiation(self):
        response = await self.http.post(
            "/admin/negotiations", headers={"Authorization": f"Bearer {self.admin}"},
            json={"item": "used bicycle"},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.headers["cache-control"], "no-store")
        return response.json()

    async def rpc(self, token=None, name=None, arguments=None):
        method = "tools/call" if name else "tools/list"
        headers = {
            "Accept": "application/json, text/event-stream",
            "Mcp-Protocol-Version": "2026-07-28", "Mcp-Method": method,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        params = {"_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {},
        }}
        if name:
            headers["Mcp-Name"] = name
            params.update(name=name, arguments=arguments or {"negotiation_id": self.nid})
        return await self.http.post("/mcp", headers=headers,
                                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})

    async def call(self, token, name, **arguments):
        response = await self.rpc(token, name, {"negotiation_id": self.nid, **arguments})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertNotIn("error", body, body)
        return body["result"]

    def view(self, result):
        self.assertFalse(result.get("isError", False), result)
        return result["structuredContent"]

    async def test_missing_invalid_and_admin_tokens_are_http_401(self):
        for token in (None, "invalid-token", self.admin):
            response = await self.rpc(token)
            self.assertEqual(response.status_code, 401)
            self.assertIn("Bearer", response.headers["www-authenticate"])

    async def test_admin_route_requires_separate_token(self):
        for token in (None, self.buyer):
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            response = await self.http.post("/admin/negotiations", headers=headers, json={"item": "x"})
            self.assertEqual(response.status_code, 401)
        response = await self.http.post(
            "/admin/negotiations", headers={"Authorization": f"Bearer {self.admin}"},
            json={"item": "x", "role": "seller"},
        )
        self.assertEqual(response.status_code, 400)

    async def test_five_tools_and_server_owned_role(self):
        response = await self.rpc(self.buyer)
        self.assertEqual(response.status_code, 200, response.text)
        tools = response.json()["result"]["tools"]
        self.assertEqual({tool["name"] for tool in tools}, {
            "get_negotiation", "propose", "accept_proposal", "reject_proposal", "refuse"})
        for tool in tools:
            self.assertNotIn("role", tool["inputSchema"]["properties"])
        self.assertEqual(self.view(await self.call(self.seller, "get_negotiation"))["role"], "seller")

    async def test_cross_negotiation_and_wrong_turn_leave_state_unchanged(self):
        other = await self.open_negotiation()
        before = self.view(await self.call(self.buyer, "get_negotiation"))
        result = await self.call(self.buyer, "get_negotiation", negotiation_id=other["negotiation_id"])
        self.assertTrue(result["isError"])
        for action in ("propose", "accept_proposal", "reject_proposal", "refuse"):
            args = {"price": 120} if action == "propose" else {}
            self.assertTrue((await self.call(self.seller, action, **args))["isError"])
        self.assertEqual(self.view(await self.call(self.buyer, "get_negotiation")), before)

    async def test_counterproposal_and_accept_close_at_opponents_price(self):
        self.view(await self.call(self.buyer, "propose", price=120))
        self.view(await self.call(self.seller, "propose", price=140))
        final = self.view(await self.call(self.buyer, "accept_proposal"))
        self.assertEqual((final["status"], final["price"], final["turn"]), ("deal", 140, None))
        self.assertEqual(len(final["moves"]), 3)
        self.assertTrue((await self.call(self.seller, "refuse"))["isError"])
        self.assertEqual(self.view(await self.call(self.seller, "get_negotiation"))["moves"], final["moves"])

    async def test_rejection_removes_offer_and_refuse_closes(self):
        for action in ("accept_proposal", "reject_proposal"):
            self.assertTrue((await self.call(self.buyer, action))["isError"])
        self.view(await self.call(self.buyer, "propose", price=100))
        rejected = self.view(await self.call(self.seller, "reject_proposal"))
        self.assertIsNone(rejected["current_proposal"])
        self.assertEqual(rejected["turn"], "buyer")
        self.assertTrue((await self.call(self.buyer, "accept_proposal"))["isError"])
        final = self.view(await self.call(self.buyer, "refuse"))
        self.assertEqual((final["status"], final["price"], len(final["moves"])), ("no_deal", None, 3))

    async def test_non_integer_prices_do_not_consume_turn(self):
        for price in (True, 1.5, "120", None):
            self.assertTrue((await self.call(self.buyer, "propose", price=price))["isError"])
        state = self.view(await self.call(self.buyer, "get_negotiation"))
        self.assertEqual((state["turn"], state["moves"]), ("buyer", []))


if __name__ == "__main__":
    unittest.main(verbosity=2)
