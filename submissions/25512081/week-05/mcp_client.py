"""A minimal MCP client over Streamable HTTP (JSON-RPC 2.0), with a bearer token.

The market server runs stateless with JSON responses, so a tool call is one
POST to /mcp; there is no session to open. This is the week-01 loop's HTTP
client plus an Authorization header, which is all a host needs here.
"""
import itertools

import httpx

PROTOCOL_VERSION = "2026-07-28"
_ids = itertools.count(1)


class MCPClient:
    def __init__(self, base_url, token=None, timeout=60):
        self.url = base_url.rstrip("/") + "/mcp"
        self.headers = {"content-type": "application/json",
                        "accept": "application/json, text/event-stream",
                        "mcp-protocol-version": PROTOCOL_VERSION}
        if token:
            self.headers["authorization"] = f"Bearer {token}"
        self.http = httpx.Client(timeout=timeout)

    def _rpc(self, method, params):
        # 2026-07-28 has no session/initialize: every request carries the
        # protocol version and the client capabilities in params._meta.
        params = dict(params)
        params["_meta"] = {"io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
                           "io.modelcontextprotocol/clientCapabilities": {}}
        body = {"jsonrpc": "2.0", "id": next(_ids), "method": method, "params": params}
        # routing headers of the 2026-07-28 transport: they must match the body
        headers = dict(self.headers, **{"mcp-method": method})
        if "name" in params:
            headers["mcp-name"] = params["name"]
        return self.http.post(self.url, json=body, headers=headers)

    def list_tools(self):
        r = self._rpc("tools/list", {})
        r.raise_for_status()
        return r.json()["result"]["tools"]

    def call_tool(self, name, arguments):
        """-> (text, is_error). An HTTP-level failure is raised."""
        r = self._rpc("tools/call", {"name": name, "arguments": arguments})
        r.raise_for_status()
        data = r.json()
        if "error" in data:                       # JSON-RPC protocol error
            return f"protocol error: {data['error'].get('message')}", True
        res = data["result"]
        text = "\n".join(c.get("text", "") for c in res.get("content", [])
                         if c.get("type") == "text")
        return text, bool(res.get("isError"))
