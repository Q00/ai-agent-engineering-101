"""Tests for the MCP adapter without external servers or model calls."""

import asyncio
import json
import unittest

from market_server import MCP_URL, NOTICE, MarketService, create_server


def asgi_post(app, path: str, body: dict) -> tuple[int, dict[str, str], dict]:
    """Send one in-process ASGI request without starting a network server."""
    raw_body = json.dumps(body).encode()
    sent = False
    messages = []

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": raw_body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8000),
    }
    asyncio.run(app(scope, receive, send))
    start = next(message for message in messages if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    headers = {
        key.decode(): value.decode()
        for key, value in start.get("headers", [])
    }
    return start["status"], headers, json.loads(response_body or b"{}")


class MarketServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = MarketService()

    def open(self, condition: str = "server_inject"):
        return self.service.open_negotiation(
            "desk lamp",
            40,
            55,
            condition,
            negotiation_id="neg-1",
        )

    def token(self, raw_token: str):
        return asyncio.run(self.service.verify_token(raw_token))

    def test_server_tokens_carry_binding_role_and_limit(self):
        opened = self.open()

        buyer = self.token(opened["buyer_token"])
        seller = self.token(opened["seller_token"])

        self.assertIsNotNone(buyer)
        self.assertIsNotNone(seller)
        self.assertEqual(buyer.resource, MCP_URL)
        self.assertEqual(
            buyer.claims,
            {
                "negotiation_id": "neg-1",
                "role": "buyer",
                "condition": "server_inject",
                "limit": 55,
            },
        )
        self.assertEqual(seller.claims["limit"], 40)

    def test_prompt_tokens_do_not_carry_price_limit(self):
        opened = self.open("prompt_inject")
        buyer = self.token(opened["buyer_token"])

        self.assertNotIn("limit", buyer.claims)
        self.assertEqual(buyer.claims["role"], "buyer")

    def test_invalid_token_is_not_verified(self):
        self.assertIsNone(self.token("not-a-token"))

    def test_public_view_hides_private_limits_and_injects_exact_notice(self):
        opened = self.open("prompt_inject")
        buyer = self.token(opened["buyer_token"])
        seller = self.token(opened["seller_token"])
        self.service.propose(buyer, "neg-1", 50)
        self.service.propose(seller, "neg-1", 60)

        view = self.service.view(buyer, "neg-1")

        self.assertNotIn("budget", view)
        self.assertNotIn("reserve", view)
        self.assertEqual(
            view["moves"][-1]["notice"],
            NOTICE.format(raised=85),
        )
        self.assertNotIn("notice", self.service.view(seller, "neg-1")["moves"][-1])

    def test_token_cannot_access_another_negotiation(self):
        first = self.open()
        second = self.service.open_negotiation(
            "keyboard", 80, 105, "server_inject", negotiation_id="neg-2"
        )
        buyer = self.token(first["buyer_token"])

        with self.assertRaisesRegex(ValueError, "different negotiation_id"):
            self.service.view(buyer, second["negotiation_id"])


class MCPServerTests(unittest.TestCase):
    def test_server_registers_exactly_the_five_required_tools(self):
        server = create_server(MarketService())

        tools = asyncio.run(server.list_tools())

        self.assertEqual(
            {tool.name for tool in tools},
            {
                "get_negotiation",
                "propose",
                "accept_proposal",
                "reject_proposal",
                "refuse",
            },
        )

    def test_mcp_endpoint_without_token_returns_401_and_challenge(self):
        app = create_server(MarketService()).streamable_http_app(
            stateless_http=True
        )

        status, headers, _body = asgi_post(app, "/mcp", {})

        self.assertEqual(status, 401)
        self.assertIn("Bearer", headers["www-authenticate"])

    def test_admin_route_opens_negotiation_and_mints_two_tokens(self):
        app = create_server(MarketService()).streamable_http_app(
            stateless_http=True
        )

        status, _headers, body = asgi_post(
            app,
            "/admin/negotiations",
            {
                "item": "desk lamp",
                "reserve": 40,
                "budget": 55,
                "condition": "server_inject",
            },
        )

        self.assertEqual(status, 201)
        self.assertEqual(
            set(body),
            {"negotiation_id", "buyer_token", "seller_token"},
        )


if __name__ == "__main__":
    unittest.main()
