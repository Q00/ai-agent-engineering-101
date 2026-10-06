"""The four authorization checks against a running market server.

Writes auth_checks.txt, one line per check:
  1. a request without a token        -> HTTP status + WWW-Authenticate header
  2. a party token on another negotiation
  3. a move out of turn
  4. in a server condition, a move outside the token's limit

    python auth_checks.py          # starts its own server, no model needed
"""
import sys

import httpx

import run_market as R
from mcp_client import MCPClient

SC = {"id": 3, "item": "a used electric guitar", "reserve": 300, "budget": 240}


def main(port=8820):
    proc, base, key = R.start_server(port)
    lines = []
    try:
        a = R.open_negotiation(base, key, SC, "server_inject")
        b = R.open_negotiation(base, key, SC, "server_inject")
        nid = a["negotiation_id"]
        buyer, seller = MCPClient(base, a["buyer_token"]), MCPClient(base, a["seller_token"])

        # 1. no token
        r = httpx.post(base + "/mcp", json={"jsonrpc": "2.0", "id": 1,
                                            "method": "tools/list", "params": {}})
        lines.append(f"1. no token: POST /mcp tools/list -> HTTP {r.status_code}, "
                     f"WWW-Authenticate: {r.headers.get('www-authenticate')}")

        # 2. a party token on another negotiation
        text, err = buyer.call_tool("get_negotiation", {"negotiation_id": b["negotiation_id"]})
        lines.append(f"2. buyer token of {nid} on {b['negotiation_id']}: get_negotiation "
                     f"-> isError={err}: {text}")

        # 3. a move out of turn (the buyer opens, so the seller is out of turn)
        text, err = seller.call_tool("propose", {"negotiation_id": nid, "price": 350})
        lines.append(f"3. seller moves on the buyer's turn: propose 350 -> isError={err}: {text}")

        # 4. outside the token's limit (server_inject, buyer budget 240)
        text, err = buyer.call_tool("propose", {"negotiation_id": nid, "price": 260})
        lines.append(f"4. server_inject, buyer budget 240: propose 260 -> isError={err}: {text}")
    finally:
        proc.terminate()
    with open("auth_checks.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
