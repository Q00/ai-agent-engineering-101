"""Week 05 — market MCP server, SKELETON. The five tools work; the checks are yours.

Run:  python market_server.py        (Streamable HTTP on 127.0.0.1:8000/mcp)
Env:  MARKET_HOST, MARKET_PORT

What is here: the five tools from the spec, wired to market.py, one negotiation per
scenario ("n1".."n6") opened at startup so you can poke at it by hand.

What is NOT here, on purpose. Every line marked TODO(you) is a requirement of the
assignment, and the report grades whether you understand why it lives on the server:

  1. Identity          who is calling? Today `_caller` just returns whoever's turn it is.
                       That makes every call look legitimate, which is exactly the hole.
                       Replace it with a role read from a verified Bearer token,
                       and answer HTTP 401 (+ WWW-Authenticate) when there is none.
  2. Binding           the token belongs to ONE negotiation_id. Reject any other.
  3. Turn order        the token's role must equal the negotiation's current turn.
  4. Price limits      (server conditions only) reject propose / accept above the limit
                       carried in the token; log it as `refused`, count the attempt.
  5. Injection         (inject conditions only) append the [market notice] to the
                       BUYER's get_negotiation result. Never to the seller's.
"""
import os
import json

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from market import NEGOTIATIONS, MarketError, open_negotiation

mcp = FastMCP("market",
              host=os.environ.get("MARKET_HOST", "127.0.0.1"),
              port=int(os.environ.get("MARKET_PORT", "8000")))


def _get(negotiation_id: str):
    n = NEGOTIATIONS.get(negotiation_id)
    if n is None:
        raise ToolError(f"unknown negotiation_id '{negotiation_id}'")
    return n


def _caller(n) -> str:
    # TODO(you): 1 Identity, 2 Binding, 3 Turn order, 4 Price limits.
    # Placeholder so the skeleton runs end to end: trust whoever's turn it is.
    return n.turn


def _do(negotiation_id: str, act: str, *args, **kwargs) -> str:
    n = _get(negotiation_id)
    role = _caller(n)
    try:
        getattr(n, act)(role, *args, **kwargs)
    except MarketError as e:
        raise ToolError(str(e))
    return json.dumps({"ok": True, "as": role, "status": n.status,
                       "turn": n.turn, "price": n.price})


@mcp.tool(description="Offer a price for the item. The turn passes to the other side.")
def propose(negotiation_id: str, price: int, note: str = "") -> str:
    return _do(negotiation_id, "propose", price, note)


@mcp.tool(description="Accept the other side's last proposed price. Ends the negotiation "
                      "with a deal at that price.")
def accept_proposal(negotiation_id: str, note: str = "") -> str:
    return _do(negotiation_id, "accept", note)


@mcp.tool(description="Decline the other side's last price and keep negotiating. "
                      "The turn passes to the other side.")
def reject_proposal(negotiation_id: str, note: str = "") -> str:
    return _do(negotiation_id, "reject", note)


@mcp.tool(description="Leave the negotiation for good. Ends it with no deal.")
def refuse(negotiation_id: str, note: str = "") -> str:
    return _do(negotiation_id, "refuse", note)


@mcp.tool(description="Read the current state: item, whose turn it is, status, "
                      "each side's last price, and the message history.")
def get_negotiation(negotiation_id: str) -> str:
    n = _get(negotiation_id)
    text = json.dumps(n.view())
    # TODO(you): 5 Injection. Only when the caller is the buyer and the run is an inject condition.
    return text


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    for sc in json.load(open(os.path.join(here, "scenarios.json"), encoding="utf-8")):
        open_negotiation(sc, f"n{sc['id']}")
    mcp.run(transport="streamable-http")
