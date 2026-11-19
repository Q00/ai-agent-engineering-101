"""Run the four auth checks against a live market server and write auth_checks.txt.

    python auth_checks.py            # starts its own server on port 8799
"""

import asyncio
import json
from pathlib import Path

import httpx2

from market_client import admin_open, party_client, result_text, running_server

SCENARIO = json.loads((Path(__file__).parent / "scenarios.json").read_text())[0]


async def tool(base, token, name, args):
    async with party_client(base, token) as mcp:
        return result_text(await mcp.call_tool(name, args))


async def checks(base, admin_key) -> list[str]:
    lines = []

    r = httpx2.post(base + "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                    headers={"accept": "application/json, text/event-stream"})
    lines.append(f"(1) no token: POST /mcp tools/list -> HTTP {r.status_code}, "
                 f"WWW-Authenticate: {r.headers.get('www-authenticate')}")

    a = admin_open(base, admin_key, SCENARIO, "prompt")
    b = admin_open(base, admin_key, SCENARIO, "prompt")
    out = await tool(base, a["tokens"]["buyer"], "get_negotiation", {"negotiation_id": b["negotiation_id"]})
    lines.append(f"(2) buyer token of {a['negotiation_id']} on {b['negotiation_id']}: "
                 f"get_negotiation -> {out}")

    out = await tool(base, a["tokens"]["seller"], "propose",
                     {"negotiation_id": a["negotiation_id"], "price": SCENARIO["reserve"] + 20})
    lines.append(f"(3) seller moves first on {a['negotiation_id']} (buyer opens): propose -> {out}")

    s = admin_open(base, admin_key, SCENARIO, "server_inject")
    over = SCENARIO["budget"] + 30
    out = await tool(base, s["tokens"]["buyer"], "propose",
                     {"negotiation_id": s["negotiation_id"], "price": over})
    lines.append(f"(4) server_inject {s['negotiation_id']}, buyer budget {SCENARIO['budget']}: "
                 f"propose {over} -> {out}")
    return lines


def main():
    with running_server(port=8799) as (base, admin_key):
        lines = asyncio.run(checks(base, admin_key))
    text = "\n".join(lines) + "\n"
    (Path(__file__).parent / "auth_checks.txt").write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
