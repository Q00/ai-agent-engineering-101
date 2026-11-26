"""Week 05 -- the four checks on the market's own guards, with no model involved.

The point of writing these as a script rather than pasting curl output is that
the four lines in auth_checks.txt can be reproduced by anyone with the server
running, and that they are produced the same way every time.

    python market_server.py            # in one terminal
    python auth_checks.py              # in another; writes auth_checks.txt

Each check is a request the market is supposed to turn down:

    1  no bearer token at all        -> HTTP 401 and a WWW-Authenticate header
    2  a party token on someone      -> tool error (the token is bound to one
       else's negotiation               negotiation, and a tool argument cannot
                                        move it)
    3  a move when it is not         -> tool error (the server holds the turn)
       your turn
    4  a price outside the token's   -> tool error, but only where enforce is
       limit, in a server condition      on; the attempt is counted either way
"""
import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
OUT = "auth_checks.txt"

RPC_META = {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                      "io.modelcontextprotocol/clientCapabilities": {}}}


def admin_open(condition: str, scenario: dict) -> dict:
    req = urllib.request.Request(
        f"{BASE}/admin/negotiations", method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"condition": condition, "scenario": scenario}).encode())
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def rpc(token, name, arguments):
    """One tools/call. Returns (http_status, headers, parsed body or None)."""
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": arguments, **RPC_META}}
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{BASE}/mcp", method="POST",
                                 headers=headers, data=json.dumps(body).encode())
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, dict(r.headers), _parse(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), None


def _parse(raw: str):
    """Streamable HTTP answers either as JSON or as one SSE data: frame."""
    if raw.lstrip().startswith("event:"):
        raw = next(l[6:] for l in raw.splitlines() if l.startswith("data: "))
    return json.loads(raw)


def tool_text(body) -> str:
    result = (body or {}).get("result", {})
    return " ".join(c.get("text", "") for c in result.get("content", []))


def main() -> int:
    coat = {"id": 4, "item": "a winter coat", "reserve": 110, "budget": 100}
    bike = {"id": 1, "item": "a used bicycle", "reserve": 40, "budget": 120}

    a = admin_open("server_inject", coat)       # buyer limit 100, enforced
    b = admin_open("prompt", bike)              # a different negotiation

    lines = []

    # 1 -- no token. Decided above MCP, so it is an HTTP status, not a tool error.
    status, headers, _ = rpc(None, "get_negotiation",
                             {"negotiation_id": a["negotiation_id"]})
    lines.append(
        f'1. no token, tools/call get_negotiation -> HTTP {status} '
        f'{headers.get("WWW-Authenticate") or headers.get("www-authenticate")}')

    # 2 -- a token from negotiation a, pointed at negotiation b.
    _, _, body = rpc(a["buyer_token"], "propose",
                     {"negotiation_id": b["negotiation_id"], "price": 90})
    lines.append(f'2. buyer token of {a["negotiation_id"]} on '
                 f'{b["negotiation_id"]} -> tool error: {tool_text(body)}')

    # 3 -- the item is listed when the negotiation opens and the buyer answers
    # it, so the seller moving first is out of turn.
    _, _, body = rpc(a["seller_token"], "propose",
                     {"negotiation_id": a["negotiation_id"], "price": 125})
    lines.append(f'3. seller moves before the buyer has -> tool error: {tool_text(body)}')

    # 4 -- the buyer's limit is 100 and this is a server condition. The buyer
    # is on turn from the start, so this refusal is about the limit and
    # nothing else.
    _, _, body = rpc(a["buyer_token"], "propose",
                     {"negotiation_id": a["negotiation_id"], "price": 130})
    lines.append(f'4. server_inject, buyer (limit 100) proposes 130 -> '
                 f'tool error: {tool_text(body)}')

    text = "\n".join(lines) + "\n"
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
