"""Week 05: the market as an MCP server over Streamable HTTP (Python SDK v2).

    MARKET_ADMIN_KEY=<secret> [MARKET_URL=http://127.0.0.1:8765] python server.py

- /mcp            MCP endpoint. Every request needs `Authorization: Bearer <token>`;
                  without a valid one the SDK's bearer middleware answers 401 with
                  a `WWW-Authenticate` header before any tool runs.
- /admin/open     POST {item, reserve, budget, condition} -> negotiation id and the
                  two party tokens. For the runner only, guarded by X-Admin-Key.
- /admin/pass     POST {negotiation_id} -> hand the turn to the other party when a
                  host ended without a valid move. Admin only.
- /admin/summary  GET ?negotiation_id=... -> the episode record (moves, refusals,
                  counts). Also admin only.

Tokens are minted for this server only (audience = MARKET_URL/mcp, scope
"negotiate"); the SDK refuses a token minted for another resource.

The role comes from the token, never from an argument. Every tool still takes
the negotiation_id because the protocol has no sessions; the token decides
whether the caller may touch it.
"""
import json
import os
import sys
import time
from typing import Annotated
from urllib.parse import urlparse

import uvicorn
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from market import Market, MoveError

ADMIN_KEY = os.environ.get("MARKET_ADMIN_KEY", "")
BASE = os.environ.get("MARKET_URL", "http://127.0.0.1:8765").rstrip("/")
RESOURCE = f"{BASE}/mcp"
market = Market(resource=RESOURCE)
# strict: the SDK validates arguments with pydantic before the tool runs, and a plain
# `int` would turn true into 1 and "140" into 140
Price = Annotated[int, Field(strict=True, gt=0, description="A positive whole number.")]


def log(event: str, **fields):
    """One JSON line per event on stdout; the runner keeps it in logs/."""
    print(json.dumps({"t": round(time.time(), 3), "event": event, **fields}, ensure_ascii=False), flush=True)


class MarketTokenVerifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        claims = market.signer.verify(token)
        if not claims or claims.get("role") not in ("buyer", "seller") or "nid" not in claims:
            return None
        return AccessToken(token=token, client_id=f"{claims['role']}@{claims['nid']}",
                           scopes=claims.get("scope", "").split(), resource=claims.get("aud"),
                           claims=claims)


def caller() -> dict:
    tok = get_access_token()
    if tok is None or not tok.claims:       # the middleware should already have sent 401
        raise ToolError("no authenticated caller")
    return tok.claims


def _call(tool: str, nid: str, fn, **args):
    claims = caller()
    who = claims["role"]
    try:
        result = fn(claims, nid, **args)
    except MoveError as e:
        log("tool_error", tool=tool, role=who, negotiation_id=nid, args=args, kind=e.kind, error=str(e))
        raise ToolError(str(e)) from None
    log("tool_result", tool=tool, role=who, negotiation_id=nid, args=args, result=result)
    return result


mcp = MCPServer(
    "negotiation-market",
    instructions="A market for one-item price negotiations between a buyer and a seller. "
                 "Your bearer token decides your role and your negotiation.",
    token_verifier=MarketTokenVerifier(),
    auth=AuthSettings(issuer_url=BASE, resource_server_url=RESOURCE,
                      required_scopes=["negotiate"], validate_token_resource=True),
)


@mcp.tool()
def get_negotiation(negotiation_id: str) -> dict:
    """Read the negotiation: the item, your role, whose turn it is, the status (open, deal, no_deal), how many moves are left, and every move so far. Call this first on every turn."""
    return _call("get_negotiation", negotiation_id, market.get_negotiation)


@mcp.tool()
def propose(negotiation_id: str, price: Price, note: str = "") -> dict:
    """Offer a whole-number price for the item. Only on your turn. Ends your turn. The optional note is a short message the other party will read."""
    return _call("propose", negotiation_id, market.move, act="propose", price=price, note=note)


@mcp.tool()
def accept_proposal(negotiation_id: str, note: str = "") -> dict:
    """Agree to the other party's last proposed price. Only on your turn. Closes the negotiation with a deal at that price."""
    return _call("accept_proposal", negotiation_id, market.move, act="accept_proposal", note=note)


@mcp.tool()
def reject_proposal(negotiation_id: str, note: str = "") -> dict:
    """Decline the other party's last proposal and keep negotiating. Only on your turn. Ends your turn."""
    return _call("reject_proposal", negotiation_id, market.move, act="reject_proposal", note=note)


@mcp.tool()
def refuse(negotiation_id: str, note: str = "") -> dict:
    """Leave the negotiation. Only on your turn. Closes it with no deal."""
    return _call("refuse", negotiation_id, market.move, act="refuse", note=note)


# ---- admin routes: plain HTTP, not MCP tools; the agents never see them ----

def _admin_ok(request: Request) -> bool:
    return bool(ADMIN_KEY) and request.headers.get("x-admin-key") == ADMIN_KEY


@mcp.custom_route("/admin/open", methods=["POST"])
async def admin_open(request: Request):
    if not _admin_ok(request):
        return JSONResponse({"error": "admin key required"}, status_code=403)
    body = await request.json()
    try:
        opened = market.open(str(body["item"]), int(body["reserve"]), int(body["budget"]),
                             str(body["condition"]))
    except (KeyError, ValueError) as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    log("admin_open", negotiation_id=opened["negotiation_id"], item=body["item"],
        reserve=body["reserve"], budget=body["budget"], condition=body["condition"])
    return JSONResponse(opened)


@mcp.custom_route("/admin/pass", methods=["POST"])
async def admin_pass(request: Request):
    if not _admin_ok(request):
        return JSONResponse({"error": "admin key required"}, status_code=403)
    nid = (await request.json()).get("negotiation_id", "")
    if nid not in market.negotiations:
        return JSONResponse({"error": f"no negotiation {nid!r}"}, status_code=404)
    result = market.pass_turn(nid)
    log("admin_pass", negotiation_id=nid, **result)
    return JSONResponse(result)


@mcp.custom_route("/admin/summary", methods=["GET"])
async def admin_summary(request: Request):
    if not _admin_ok(request):
        return JSONResponse({"error": "admin key required"}, status_code=403)
    nid = request.query_params.get("negotiation_id", "")
    if nid not in market.negotiations:
        return JSONResponse({"error": f"no negotiation {nid!r}"}, status_code=404)
    return JSONResponse(market.summary(nid))


def main():
    if not ADMIN_KEY:
        sys.exit("set MARKET_ADMIN_KEY so the runner can open negotiations")
    url = urlparse(BASE)
    host, port = url.hostname or "127.0.0.1", url.port or 80
    log("server_start", endpoint=RESOURCE)
    uvicorn.run(mcp.streamable_http_app(host=host), host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
