"""Stage-2 tests against a real loopback HTTP server; no model requests.

Tokens are generated in memory. Assertions never include secret values.
Action tools do not exist yet: turn guard tests target the shared state guard.
"""

import asyncio
import json
from pathlib import Path
from secrets import token_urlsafe
import socket
import sys
from threading import Thread
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from market_server import PartyTokens, create_app
from market_state import CONDITIONS, Market, MarketError


class Stage2HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin_token = token_urlsafe(32)
        cls.market = Market()
        cls.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cls.socket.bind(("127.0.0.1", 0))
        cls.base = f"http://127.0.0.1:{cls.socket.getsockname()[1]}"
        cls.app = create_app(base_url=cls.base, admin_token=cls.admin_token, market=cls.market)
        cls.server = uvicorn.Server(uvicorn.Config(cls.app, log_level="error", access_log=False))
        cls.thread = Thread(target=cls.server.run, kwargs={"sockets": [cls.socket]}, daemon=True)
        cls.thread.start()
        deadline = time.monotonic() + 10
        while not cls.server.started and cls.thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        if not cls.server.started:
            cls.server.should_exit = True
            cls.thread.join(timeout=5)
            cls.socket.close()
            raise RuntimeError("Test HTTP server did not start")

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(timeout=5)
        cls.socket.close()
        if cls.thread.is_alive():
            raise RuntimeError("Test HTTP server did not stop")

    def request(self, path, payload=None, token=None, extra_headers=None):
        headers = {"Accept": "application/json, text/event-stream"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        headers.update(extra_headers or {})
        request = Request(self.base + path, headers=headers,
                          data=json.dumps(payload).encode() if payload is not None else None)
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read().decode("utf-8")
            # HTTP header names are case-insensitive. A plain dict loses that rule.
            return response.status, response.headers, json.loads(raw) if raw else None

    def create(self, condition="server_inject", scenario=None):
        payload = {"condition": condition, "scenario": scenario or {
            "id": "test", "item": "test item", "reserve": 60, "budget": 55}}
        status, headers, body = self.request("/admin/negotiations", payload, self.admin_token)
        self.assertEqual(status, 201, "Admin creation failed")
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertEqual(set(body), {"negotiation_id", "tokens"})
        self.assertEqual(set(body["tokens"]), {"buyer", "seller"})
        self.assertTrue(body["tokens"]["buyer"] != body["tokens"]["seller"], "Party tokens must differ")
        return body

    def rpc(self, method, token=None, name=None, arguments=None, extra_headers=None):
        params = {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                             "io.modelcontextprotocol/clientCapabilities": {}}}
        headers = {"Mcp-Method": method, "MCP-Protocol-Version": "2026-07-28"}
        if name is not None:
            params.update({"name": name, "arguments": arguments or {}})
            headers["Mcp-Name"] = name
        headers.update(extra_headers or {})
        return self.request("/mcp", {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, token, headers)

    @staticmethod
    def tool_value(body):
        result = body["result"]
        if "structuredContent" in result:
            return result["structuredContent"]
        text = "\n".join(c["text"] for c in result["content"] if c["type"] == "text")
        return json.loads(text)

    def test_no_token_returns_401_and_challenge(self):
        status, headers, _ = self.rpc("tools/list")
        self.assertEqual(status, 401)
        challenge = headers.get("WWW-Authenticate", "")
        self.assertIn("Bearer", challenge)
        self.assertIn("resource_metadata", challenge)

    def test_unrecognized_party_token_returns_401(self):
        status, headers, _ = self.rpc("tools/list", token_urlsafe(32))
        self.assertEqual(status, 401)
        self.assertIn("Bearer", headers.get("WWW-Authenticate", ""))

    def test_admin_token_cannot_call_mcp(self):
        status, _, _ = self.rpc("tools/list", self.admin_token)
        self.assertEqual(status, 401)

    def test_admin_route_requires_admin_not_party(self):
        payload = {"condition": "prompt_inject", "scenario": {"id": "test", "item": "item", "reserve": 1, "budget": 2}}
        status, _, _ = self.request("/admin/negotiations", payload)
        self.assertEqual(status, 401)
        grant = self.create()
        status, _, _ = self.request("/admin/negotiations", payload, grant["tokens"]["buyer"])
        self.assertEqual(status, 401)

    def test_admin_rejects_boolean_float_and_unknown_fields(self):
        invalid = [
            {"id": "test", "item": "item", "reserve": True, "budget": 5},
            {"id": "test", "item": "item", "reserve": 1.5, "budget": 5},
            {"id": "test", "item": " ", "reserve": 1, "budget": 5},
            {"id": "test", "item": "item", "reserve": -1, "budget": 5},
            {"id": "test", "item": "item", "reserve": 1, "budget": 5, "role": "buyer"},
        ]
        for scenario in invalid:
            with self.subTest(case=invalid.index(scenario)):
                status, _, _ = self.request("/admin/negotiations", {"condition": "server", "scenario": scenario}, self.admin_token)
                self.assertEqual(status, 400)
        status, _, _ = self.request("/admin/negotiations", {"condition": "unknown", "scenario": invalid[0]}, self.admin_token)
        self.assertEqual(status, 400)

    def test_role_comes_from_token_and_private_data_is_hidden(self):
        grant = self.create()
        for role in ("buyer", "seller"):
            status, headers, body = self.rpc("tools/call", grant["tokens"][role], "get_negotiation",
                                           {"negotiation_id": grant["negotiation_id"]})
            self.assertEqual(status, 200)
            self.assertFalse(body["result"].get("isError", False))
            view = self.tool_value(body)
            self.assertEqual(view, {"negotiation_id": grant["negotiation_id"], "item": "test item",
                                    "role": role, "turn": "buyer", "status": "open", "moves": []})
            self.assertNotIn("Mcp-Session-Id", headers)
            serialized = json.dumps(body)
            self.assertTrue(all(token not in serialized for token in grant["tokens"].values()), "Token exposed in view")

    def test_tools_schema_does_not_accept_caller_role(self):
        grant = self.create()
        status, _, body = self.rpc("tools/list", grant["tokens"]["buyer"])
        self.assertEqual(status, 200)
        tools = body["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], ["get_negotiation"])
        self.assertEqual(set(tools[0]["inputSchema"]["properties"]), {"negotiation_id"})

    def test_other_negotiation_is_tool_error_without_state_change(self):
        first, second = self.create(), self.create()
        before = self.market.view(second["tokens"]["buyer"], second["negotiation_id"])
        status, _, body = self.rpc("tools/call", first["tokens"]["buyer"], "get_negotiation",
                                   {"negotiation_id": second["negotiation_id"]})
        self.assertEqual(status, 200)
        self.assertTrue(body["result"]["isError"])
        self.assertIn("not authorized", json.dumps(body["result"]))
        self.assertEqual(before, self.market.view(second["tokens"]["buyer"], second["negotiation_id"]))

    def test_fabricated_handle_is_also_tool_error(self):
        grant = self.create()
        status, _, body = self.rpc("tools/call", grant["tokens"]["buyer"], "get_negotiation",
                                   {"negotiation_id": "not-a-real-negotiation"})
        self.assertEqual(status, 200)
        self.assertTrue(body["result"]["isError"])

    def test_turn_guard_allows_buyer_refuses_seller_preserves_state(self):
        grant = self.create()
        before = self.market.view(grant["tokens"]["buyer"], grant["negotiation_id"])
        self.assertEqual(self.market.require_turn(grant["tokens"]["buyer"], grant["negotiation_id"]).role, "buyer")
        with self.assertRaisesRegex(MarketError, "not your turn"):
            self.market.require_turn(grant["tokens"]["seller"], grant["negotiation_id"])
        self.assertEqual(before, self.market.view(grant["tokens"]["buyer"], grant["negotiation_id"]))

    def test_view_cannot_mutate_server_history(self):
        grant = self.create()
        view = self.market.view(grant["tokens"]["buyer"], grant["negotiation_id"])
        view["moves"].append({"act": "refuse"})
        self.assertEqual(self.market.view(grant["tokens"]["buyer"], grant["negotiation_id"])["moves"], [])

    def test_token_claims_have_own_limit_only_in_server_conditions(self):
        verifier = PartyTokens(self.market, self.base + "/mcp")
        for condition in CONDITIONS:
            grant = self.create(condition)
            for role, limit in (("buyer", 55), ("seller", 60)):
                access = asyncio.run(verifier.verify_token(grant["tokens"][role]))
                self.assertEqual(access.resource, self.base + "/mcp")
                self.assertEqual(access.scopes, ["negotiate"])
                self.assertEqual(access.claims["role"], role)
                self.assertEqual(access.claims["negotiation_id"], grant["negotiation_id"])
                expected = {"role", "negotiation_id", "limit"} if condition.startswith("server") else {"role", "negotiation_id"}
                self.assertEqual(set(access.claims), expected)
                if condition.startswith("server"):
                    self.assertEqual(access.claims["limit"], limit)

    def test_restart_invalidates_party_tokens(self):
        grant = self.create()
        other_market = Market()
        self.assertTrue(other_market.grant_for(grant["tokens"]["buyer"]) is None, "Token incorrectly survived restart")

    def test_header_name_mismatch_is_rejected(self):
        grant = self.create()
        status, _, _ = self.rpc("tools/call", grant["tokens"]["buyer"], "get_negotiation",
                                {"negotiation_id": grant["negotiation_id"]}, {"Mcp-Name": "refuse"})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
