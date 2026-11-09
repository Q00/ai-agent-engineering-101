import asyncio
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from mcp import Client
from mcp.client.streamable_http import (
    create_mcp_http_client,
    streamable_http_client,
)

BASE = "http://127.0.0.1:8000"
FOLDER = Path(__file__).resolve().parent


def admin_request(path, data=None):
    headers = {
        "Authorization": f"Bearer {os.environ['MARKET_ADMIN_TOKEN']}",
        "Content-Type": "application/json",
    }
    body = json.dumps(data).encode() if data is not None else None
    request = Request(BASE + path, data=body, headers=headers)
    with urlopen(request, timeout=20) as response:
        return json.load(response)


async def tool_call(token, name, arguments):
    async with create_mcp_http_client(
        headers={"Authorization": f"Bearer {token}"}
    ) as http:
        transport = streamable_http_client(
            BASE + "/mcp", http_client=http
        )
        async with Client(transport, cache=None) as client:
            return await client.call_tool(name, arguments)


def error_text(result):
    return " ".join(
        block.text for block in result.content
        if block.type == "text"
    ).replace("\n", " ")


async def main():
    lines = []

    # 1. Missing token must fail at the HTTP layer.
    try:
        with urlopen(BASE + "/mcp", timeout=10):
            raise AssertionError("A request without a token was allowed.")
    except HTTPError as error:
        challenge = error.headers.get("WWW-Authenticate", "")
        assert error.code == 401, error.code
        assert challenge.lower().startswith("bearer"), challenge
        lines.append(
            f"1. Missing token: HTTP {error.code}; "
            f"WWW-Authenticate: {challenge}"
        )

    scenario = {
        "id": 1, "item": "desk lamp", "reserve": 30, "budget": 45
    }
    data = {"scenario": scenario, "condition": "server_inject"}
    first = admin_request("/admin/open", data)
    second = admin_request("/admin/open", data)
    negotiation_id = first["negotiation_id"]

    # 2. A valid token must not access another negotiation.
    result = await tool_call(
        first["tokens"]["buyer"],
        "get_negotiation",
        {"negotiation_id": second["negotiation_id"]},
    )
    assert result.is_error, "Cross-negotiation access was allowed."
    lines.append(f"2. Other negotiation: tool error; {error_text(result)}")

    # 3. Buyer goes first, so the seller cannot move yet.
    result = await tool_call(
        first["tokens"]["seller"],
        "propose",
        {"negotiation_id": negotiation_id, "price": 40},
    )
    assert result.is_error, "An out-of-turn move was allowed."
    lines.append(f"3. Wrong turn: tool error; {error_text(result)}")

    # 4. Buyer budget is 45: proposing 46 must be blocked.
    result = await tool_call(
        first["tokens"]["buyer"],
        "propose",
        {"negotiation_id": negotiation_id, "price": 46},
    )
    assert result.is_error, "An over-budget move was allowed."
    lines.append(f"4. Outside limit: tool error; {error_text(result)}")

    # Refused moves must not change the turn or add a move.
    state = admin_request(f"/admin/state/{negotiation_id}")
    assert state["moves"] == []
    assert state["turn"] == "buyer"
    assert state["attempted_violations"] == 1
    assert state["refused_calls"] == 2

    output = FOLDER / "auth_checks.txt"
    with output.open("x", encoding="utf-8") as file:
        file.write("\n".join(lines) + "\n")

    for line in lines:
        print(line)
    print("All four authorization checks passed. No model calls.")


if __name__ == "__main__":
    asyncio.run(main())