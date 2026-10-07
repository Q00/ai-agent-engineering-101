"""Record four authorization checks against a running localhost MCP server.

No model or API key is needed. Tokens exist only in memory. Existing output
files are preserved; use --output with a new path for another capture.
"""

import argparse
import asyncio
import json
import secrets

import httpx2

from run_experiment import BASE, local_market, own_path


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


async def rpc(http, url, token=None, name=None, arguments=None):
    method = "tools/call" if name else "tools/list"
    headers = {
        "Accept": "application/json, text/event-stream",
        "Mcp-Protocol-Version": "2026-07-28", "Mcp-Method": method,
    }
    params = {"_meta": {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
    }}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if name:
        headers["Mcp-Name"] = name
        params.update(name=name, arguments=arguments)
    return await http.post(url, headers=headers,
                           json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})


def tool_result(response):
    require(response.status_code == 200, "Expected HTTP 200 for an authenticated tool call")
    body = response.json()
    require("result" in body and "error" not in body, "Expected a JSON-RPC tool result")
    return body["result"]


async def snapshot(http, url, episode):
    result = tool_result(await rpc(http, url, episode["tokens"]["buyer"], "get_negotiation",
                                   {"negotiation_id": episode["negotiation_id"]}))
    require(not result.get("isError"), "State query unexpectedly failed")
    return result.get("structuredContent") or json.loads(result["content"][0]["text"])


async def capture(url):
    admin = secrets.token_urlsafe(32)
    async with local_market(url, admin):
        async with httpx2.AsyncClient(trust_env=False, timeout=30) as http:
            episodes = []
            for item in ("auth-check bicycle A", "auth-check bicycle B"):
                response = await http.post(url.removesuffix("/mcp") + "/admin/negotiations",
                    headers={"Authorization": f"Bearer {admin}"},
                    json={"item": item, "condition": "server_inject", "reserve": 120, "budget": 150})
                require(response.status_code == 201, "Admin negotiation creation failed")
                episodes.append(response.json())
            first, second = episodes
            before = [await snapshot(http, url, episode) for episode in episodes]
            response = await rpc(http, url)
            challenge = response.headers.get("www-authenticate", "")
            require(response.status_code == 401 and "Bearer" in challenge,
                    "Missing-token request must return HTTP 401 and WWW-Authenticate: Bearer")
            lines = [f"(1) missing_token tools/list: HTTP {response.status_code}; WWW-Authenticate: {challenge}"]
            cases = (
                ("cross_negotiation (buyer A token on B)", first["tokens"]["buyer"], "get_negotiation",
                 {"negotiation_id": second["negotiation_id"]}, "Token is bound to another negotiation"),
                ("out_of_turn (seller while buyer turn)", first["tokens"]["seller"], "propose",
                 {"negotiation_id": first["negotiation_id"], "price": 120}, "Out of turn: it is buyer's turn"),
                ("server_limit (buyer proposes 151, budget 150)", first["tokens"]["buyer"], "propose",
                 {"negotiation_id": first["negotiation_id"], "price": 151}, "Price 151 exceeds buyer token budget 150"),
            )
            for index, (label, token, name, args, expected) in enumerate(cases, 2):
                response = await rpc(http, url, token, name, args)
                result = tool_result(response)
                text = " ".join(part["text"] for part in result.get("content", []) if part["type"] == "text")
                require(result.get("isError") is True and expected in text,
                        f"Authorization check {index} did not return the expected tool error")
                after = [await snapshot(http, url, episode) for episode in episodes]
                require(after == before, f"Authorization check {index} changed negotiation state")
                lines.append(f"({index}) {label}: HTTP {response.status_code}; isError=true; {text}; state_unchanged=true")
    return lines


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="http://127.0.0.1:8001/mcp")
    parser.add_argument("--output", default=str(BASE / "auth_checks.txt"))
    args = parser.parse_args()
    output = own_path(args.output)
    if output.exists():
        parser.error("Output already exists; use --output with a new path to preserve the previous capture")
    require(args.server.endswith("/mcp"), "--server must end in /mcp")
    lines = await capture(args.server)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    for line in lines:
        print(line, flush=True)
    print("[validated] four live HTTP checks passed; rejected calls left both negotiations unchanged")


if __name__ == "__main__":
    asyncio.run(main())
