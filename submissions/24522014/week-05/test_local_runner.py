"""One real localhost HTTP check; scripted model, no API calls or graded rows."""

import asyncio
import csv
import json
import secrets
import socket
import tempfile
import unittest
from pathlib import Path

import httpx2

from run_experiment import BASE, local_market, run_batch
from test_negotiation_host import ScriptedModel


class LocalRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def diagnose_framing(self, app, port):
        token = next(iter(app.state.market.parties))
        body = json.dumps({"jsonrpc": "2.0", "id": 99, "method": "tools/list", "params": {
            "_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                      "io.modelcontextprotocol/clientCapabilities": {}}
        }}).encode()
        headers = {
            "Authorization": f"Bearer {token}", "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream", "Mcp-Method": "tools/list",
            "Mcp-Protocol-Version": "2026-07-28",
        }
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app)) as http:
            response = await http.post(f"http://127.0.0.1:{port}/mcp", headers=headers, content=body)
            print(f"[diagnostic ASGI] status={response.status_code} headers={dict(response.headers)} body_bytes={len(response.content)}")
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            request = f"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
            request += "".join(f"{key}: {value}\r\n" for key, value in headers.items())
            request += f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
            writer.write(request.encode() + body)
            await writer.drain()
            async with asyncio.timeout(10):
                response_headers = await reader.readuntil(b"\r\n\r\n")
                prefix = await reader.read(24)
            # Response headers and a tools/list body prefix contain no issued tokens.
            print("[diagnostic TCP headers] " + response_headers.decode("latin-1").replace("\r\n", " | "))
            print(f"[diagnostic TCP body prefix] {prefix!r}")
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_local_market_and_sdk_clients_over_tcp(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        server_url = f"http://127.0.0.1:{port}/mcp"
        admin = secrets.token_urlsafe(32)
        model = ScriptedModel([("propose", {"price": 130}), ("accept_proposal", {})])
        scenario = {"id": 1, "item": "bicycle", "reserve": 120, "budget": 150}
        with tempfile.TemporaryDirectory(dir=BASE) as directory:
            root = Path(directory)
            async with local_market(server_url, admin) as app:
                failed = await run_batch([scenario], "prompt", 1, server_url, admin,
                                         root / "results.csv", root / "logs", lambda _: model)
                if failed:
                    await self.diagnose_framing(app, port)
                async with httpx2.AsyncClient(trust_env=False) as http:
                    for token in (None, "invalid-token", admin):
                        headers = {"Authorization": f"Bearer {token}"} if token else {}
                        response = await http.post(server_url, headers=headers,
                                                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
                        self.assertEqual(response.status_code, 401)
                        self.assertIn("Bearer", response.headers["www-authenticate"])
                        self.assertEqual(response.headers.get("transfer-encoding"), "chunked")
                        self.assertEqual(response.json()["error"], "invalid_token")
                    print("[TCP auth] missing, invalid and admin tokens: HTTP 401 + WWW-Authenticate; valid chunked JSON")
            self.assertEqual(failed, 0)
            with (root / "results.csv").open(encoding="utf-8", newline="") as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual((row["outcome"], row["price"], row["tool_calls"]), ("deal", "130", "4"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
