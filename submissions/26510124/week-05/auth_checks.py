"""Print the four required checks against a real, temporary HTTP market."""
import argparse
import asyncio
from pathlib import Path

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from run_experiment import admin_json, managed_server


async def check(output_dir):
    async with managed_server(output_dir, 0, []) as (url, admin):
        scenario = {"id": "auth-fixture", "item": "a test keyboard", "reserve": 90, "budget": 70}
        first = await admin_json(admin, "POST", "/admin/negotiations",
                                 {"scenario": scenario, "condition": "server_inject", "max_turns": 8})
        second = await admin_json(admin, "POST", "/admin/negotiations",
                                  {"scenario": scenario, "condition": "server_inject", "max_turns": 8})
        async with httpx2.AsyncClient(trust_env=False) as http:
            response = await http.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            challenge = response.headers.get("WWW-Authenticate", "")
            assert response.status_code == 401 and challenge.startswith("Bearer")
            print(f"1. no token: HTTP {response.status_code}; WWW-Authenticate: {challenge}", flush=True)

        for label, role, tool, arguments, expected in [
            ("2. wrong negotiation", "buyer", "get_negotiation",
             {"negotiation_id": second["negotiation_id"]}, "not authorized"),
            ("3. out of turn", "seller", "propose",
             {"negotiation_id": first["negotiation_id"], "price": 90}, "not your turn"),
            ("4. outside token limit", "buyer", "propose",
             {"negotiation_id": first["negotiation_id"], "price": 100}, "price limit refused"),
        ]:
            async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {first[role + '_token']}"},
                                          trust_env=False) as http:
                async with Client(streamable_http_client(url, http_client=http)) as mcp:
                    result = await mcp.call_tool(tool, arguments)
                    text = " ".join(c.text for c in result.content if c.type == "text").replace("\n", " ")
                    assert result.is_error and expected in text
                    print(f"{label}: isError=true; {text}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    asyncio.run(check(args.output_dir))
