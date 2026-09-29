"""Authenticated Streamable HTTP negotiation market.

Run with MARKET_ADMIN_TOKEN in the environment. Tokens and state are in memory.
No model is called here. Identity, turn, and price checks run on the server.
"""

import argparse
import json
import os
from secrets import compare_digest

import uvicorn
from mcp.server import MCPServer
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from market_state import Condition, Market, MarketError


class ScenarioInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(strict=True, min_length=1, max_length=200)
    item: str = Field(strict=True, min_length=1, max_length=500)
    reserve: StrictInt = Field(ge=0)
    budget: StrictInt = Field(ge=0)


class CreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario: ScenarioInput
    condition: Condition


class PartyTokens:
    def __init__(self, market: Market, resource: str):
        self.market = market
        self.resource = resource

    async def verify_token(self, token: str) -> AccessToken | None:
        grant = self.market.grant_for(token)
        if grant is None:
            return None
        claims = {"role": grant.role, "negotiation_id": grant.negotiation_id}
        if grant.limit is not None:
            claims["limit"] = grant.limit
        return AccessToken(token=token, client_id=grant.role,
                           subject=f"{grant.negotiation_id}:{grant.role}",
                           scopes=["negotiate"], resource=self.resource, claims=claims)


def create_app(*, base_url: str, admin_token: str, market: Market | None = None):
    if not isinstance(admin_token, str) or len(admin_token) < 32 or not admin_token.isascii():
        raise ValueError("MARKET_ADMIN_TOKEN must contain at least 32 ASCII characters")
    market = market if market is not None else Market()
    base_url = base_url.rstrip("/")
    resource = f"{base_url}/mcp"
    mcp = MCPServer("market", token_verifier=PartyTokens(market, resource),
                    auth=AuthSettings(issuer_url=base_url, resource_server_url=resource,
                                      required_scopes=["negotiate"], validate_token_resource=True))

    def party_call(operation, negotiation_id: str, *args) -> dict:
        access = get_access_token()
        if access is None:
            raise ToolError("Party authentication required")
        try:
            return operation(access.token, negotiation_id, *args)
        except MarketError as error:
            raise ToolError(str(error)) from error

    @mcp.tool()
    def get_negotiation(negotiation_id: str) -> dict:
        """Read the item, your role, the current turn, status, and moves so far."""
        return party_call(market.view, negotiation_id)

    @mcp.tool()
    def propose(negotiation_id: str, price: StrictInt) -> dict:
        """Offer a nonnegative whole-number price for the item. Ends your turn."""
        return party_call(market.propose, negotiation_id, price)

    @mcp.tool()
    def accept_proposal(negotiation_id: str) -> dict:
        """Accept the other party's active proposal at its price. Closes with a deal."""
        return party_call(market.accept_proposal, negotiation_id)

    @mcp.tool()
    def reject_proposal(negotiation_id: str) -> dict:
        """Decline the other party's active proposal and continue. Ends your turn."""
        return party_call(market.reject_proposal, negotiation_id)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request):
        return JSONResponse({"status": "ok", "stage": 2})

    @mcp.custom_route("/admin/negotiations", methods=["POST"])
    async def create_negotiation(request: Request):
        # SDK custom routes are public by default: explicitly protect this one.
        authorization = request.headers.get("authorization", "")
        parts = authorization.split()
        supplied = parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else ""
        if not compare_digest(supplied.encode("utf-8"), admin_token.encode("ascii")):
            return JSONResponse({"error": "Administrator authentication required"}, status_code=401,
                                headers={"WWW-Authenticate": 'Bearer realm="market-admin"',
                                         "Cache-Control": "no-store"})
        try:
            payload = CreateInput.model_validate(await request.json())
        except (ValidationError, json.JSONDecodeError, UnicodeDecodeError):
            # Do not echo a body that might contain credentials.
            return JSONResponse({"error": "Expected scenario {id,item,reserve,budget} and a valid condition"},
                                status_code=400, headers={"Cache-Control": "no-store"})
        result = market.create(scenario_id=payload.scenario.id, item=payload.scenario.item,
                               reserve=payload.scenario.reserve, budget=payload.scenario.budget,
                               condition=payload.condition)
        return JSONResponse(result, status_code=201, headers={"Cache-Control": "no-store"})

    app = mcp.streamable_http_app(stateless_http=True, json_response=True, host="127.0.0.1")
    app.state.market = market
    app.state.mcp = mcp
    return app


def main():
    parser = argparse.ArgumentParser(description="Run the local authenticated market server")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    admin_token = os.environ.get("MARKET_ADMIN_TOKEN", "")
    if len(admin_token) < 32 or not admin_token.isascii():
        parser.error("set MARKET_ADMIN_TOKEN to a random secret of at least 32 ASCII characters")
    app = create_app(base_url=f"http://127.0.0.1:{args.port}", admin_token=admin_token)
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
