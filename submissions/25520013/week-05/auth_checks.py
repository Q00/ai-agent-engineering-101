"""Week-05: the four auth checks against the running market server, by hand over HTTP.

Each check is a real HTTP request with the 2026-07-28 headers from the lab
(MCP-Protocol-Version, Mcp-Method, Mcp-Name for tools/call, and the _meta
envelope). Writes one line per check to auth_checks.txt:
  1. no token                     -> HTTP status and WWW-Authenticate header
  2. a party token, other negotiation -> the tool error
  3. a move out of turn           -> the tool error
  4. server_inject, outside the token's limit -> the refusal

  MARKET_ADMIN_TOKEN=... uv run python auth_checks.py   # server already running
"""

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

URL = os.environ.get("MARKET_URL", "http://127.0.0.1:8001")
ADMIN = os.environ["MARKET_ADMIN_TOKEN"]
V = "2026-07-28"
OUT = Path(__file__).resolve().parent / "auth_checks.txt"


def _http(path: str, body: dict, headers: dict) -> tuple[int, dict, str]:
    req = urllib.request.Request(
        URL + path,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode()


def admin_open(scenario: int, condition: str) -> dict:
    return json.loads(
        _http(
            "/admin/open",
            {"scenario": scenario, "condition": condition},
            {"X-Admin-Token": ADMIN},
        )[2]
    )


def call_tool(token: str | None, name: str, args: dict) -> tuple[int, dict, str]:
    """tools/call with the lab's headers; returns status, headers, and the result text."""
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": name,
            "arguments": args,
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": V,
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        },
    }
    headers = {
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": V,
        "Mcp-Method": "tools/call",
        "Mcp-Name": name,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    status, hdrs, text = _http("/mcp", body, headers)
    if status == 200:
        # The reply may be one SSE event; take the JSON from its data line.
        data = next((l[5:] for l in text.splitlines() if l.startswith("data:")), text)
        res = json.loads(data)["result"]
        text = f"isError={res['isError']} {res['content'][0]['text']}"
    return status, hdrs, text


def main() -> None:
    a = admin_open(1, "prompt_inject")
    b = admin_open(2, "prompt_inject")
    s = admin_open(4, "server_inject")
    lines = []

    status, hdrs, _ = call_tool(
        None, "get_negotiation", {"negotiation_id": a["negotiation_id"]}
    )
    www = {k.lower(): v for k, v in hdrs.items()}.get("www-authenticate")
    lines.append(
        f"1. no token: tools/call get_negotiation -> HTTP {status}, WWW-Authenticate: {www}"
    )

    status, _, text = call_tool(
        a["buyer_token"], "get_negotiation", {"negotiation_id": b["negotiation_id"]}
    )
    lines.append(
        f"2. buyer token of {a['negotiation_id']} on {b['negotiation_id']}: "
        f"get_negotiation -> HTTP {status}, {text}"
    )

    status, _, text = call_tool(
        a["seller_token"],
        "propose",
        {"negotiation_id": a["negotiation_id"], "price": 140},
    )
    lines.append(
        f"3. seller moves first in {a['negotiation_id']} (buyer's turn): "
        f"propose 140 -> HTTP {status}, {text}"
    )

    status, _, text = call_tool(
        s["buyer_token"],
        "propose",
        {"negotiation_id": s["negotiation_id"], "price": 100},
    )
    lines.append(
        f"4. server_inject {s['negotiation_id']} (scenario 4, budget 70): "
        f"buyer propose 100 -> HTTP {status}, {text}"
    )

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
