"""Homework step 1: authenticated, in-memory negotiation market over HTTP."""

import copy
import os
import secrets
import threading
from dataclasses import dataclass, field
from typing import Literal
from uuid import uuid4

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse


Role = Literal["buyer", "seller"]


@dataclass(frozen=True)
class Party:
    negotiation_id: str
    role: Role


@dataclass
class Negotiation:
    item: str
    turn: Role = "buyer"
    status: str = "open"
    price: int | None = None
    offer: dict | None = None
    moves: list[dict] = field(default_factory=list)


class Market:
    """Opaque tokens reference server-owned role and negotiation records."""

    def __init__(self):
        self.negotiations: dict[str, Negotiation] = {}
        self.parties: dict[str, Party] = {}
        self.lock = threading.RLock()

    def create(self, item: str, first_role: Role = "buyer") -> dict:
        with self.lock:
            negotiation_id = str(uuid4())
            self.negotiations[negotiation_id] = Negotiation(item=item, turn=first_role)
            tokens = {}
            for role in ("buyer", "seller"):
                token = secrets.token_urlsafe(32)
                self.parties[token] = Party(negotiation_id, role)
                tokens[role] = token
            return {"negotiation_id": negotiation_id, "tokens": tokens}

    def _authorize(self, token: str, negotiation_id: str):
        party = self.parties.get(token)
        if party is None:
            raise ValueError("Invalid party token")
        if party.negotiation_id != negotiation_id:
            raise ValueError("Token is bound to another negotiation")
        return party, self.negotiations[negotiation_id]

    @staticmethod
    def _view(negotiation_id: str, party: Party, state: Negotiation) -> dict:
        return copy.deepcopy({
            "negotiation_id": negotiation_id,
            "item": state.item,
            "role": party.role,
            "turn": state.turn if state.status == "open" else None,
            "status": state.status,
            "price": state.price,
            "current_proposal": state.offer,
            "moves": state.moves,
        })

    def get(self, token: str, negotiation_id: str) -> dict:
        with self.lock:
            party, state = self._authorize(token, negotiation_id)
            return self._view(negotiation_id, party, state)

    def move(self, token: str, negotiation_id: str, action: str, price=None) -> dict:
        # Check and mutation share a lock: simultaneous requests cannot both move.
        with self.lock:
            party, state = self._authorize(token, negotiation_id)
            if state.status != "open":
                raise ValueError("Negotiation is already closed")
            if state.turn != party.role:
                raise ValueError(f"Out of turn: it is {state.turn}'s turn")
            if action == "propose":
                if type(price) is not int:
                    raise ValueError("Price must be a whole-number integer")
                state.offer = {"role": party.role, "price": price}
            elif action in ("accept_proposal", "reject_proposal"):
                if state.offer is None or state.offer["role"] == party.role:
                    raise ValueError("No outstanding proposal from the other party")
                price = state.offer["price"]
                if action == "accept_proposal":
                    state.status, state.price = "deal", price
                else:
                    state.offer = None
            elif action == "refuse":
                state.status = "no_deal"
                state.offer = None
            else:
                raise ValueError("Unknown action")
            move = {"number": len(state.moves) + 1, "role": party.role, "action": action}
            if action in ("propose", "accept_proposal", "reject_proposal"):
                move["price"] = price
            state.moves.append(move)
            state.turn = "seller" if party.role == "buyer" else "buyer"
            return self._view(negotiation_id, party, state)


class MarketVerifier:
    def __init__(self, market: Market, resource: str):
        self.market, self.resource = market, resource

    async def verify_token(self, token: str) -> AccessToken | None:
        with self.market.lock:
            party = self.market.parties.get(token)
            if party is None:
                return None
            return AccessToken(
                token=token, client_id=party.role, scopes=["negotiate"],
                resource=self.resource,
                claims={"negotiation_id": party.negotiation_id, "role": party.role},
            )


class OpenNegotiation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item: str = Field(min_length=1, max_length=1000)
    first_role: Role = "buyer"


def create_app(admin_token: str, base_url: str = "http://127.0.0.1:8001"):
    if not admin_token:
        raise ValueError("MARKET_ADMIN_TOKEN is required")
    market = Market()
    resource = base_url.rstrip("/") + "/mcp"
    server = MCPServer(
        "negotiation-market",
        token_verifier=MarketVerifier(market, resource),
        auth=AuthSettings(
            issuer_url=base_url, resource_server_url=resource,
            required_scopes=["negotiate"], validate_token_resource=True,
        ),
    )

    def caller_token() -> str:
        access = get_access_token()
        if access is None:
            raise ValueError("Authenticated party token required")
        return access.token

    @server.tool()
    async def get_negotiation(negotiation_id: str) -> dict:
        """Read your negotiation's item, role, turn, status and move history."""
        return market.get(caller_token(), negotiation_id)

    @server.tool()
    async def propose(negotiation_id: str, price: StrictInt) -> dict:
        """Offer a whole-number price on your turn, then pass the turn."""
        return market.move(caller_token(), negotiation_id, "propose", price)

    @server.tool()
    async def accept_proposal(negotiation_id: str) -> dict:
        """Accept the other party's outstanding proposal on your turn; close a deal."""
        return market.move(caller_token(), negotiation_id, "accept_proposal")

    @server.tool()
    async def reject_proposal(negotiation_id: str) -> dict:
        """Reject the outstanding proposal on your turn, then pass the turn."""
        return market.move(caller_token(), negotiation_id, "reject_proposal")

    @server.tool()
    async def refuse(negotiation_id: str) -> dict:
        """Leave on your turn and close the negotiation with no deal."""
        return market.move(caller_token(), negotiation_id, "refuse")

    @server.custom_route("/admin/negotiations", methods=["POST"])
    async def open_negotiation(request: Request):
        supplied = request.headers.get("authorization", "")
        if not secrets.compare_digest(supplied.encode(), f"Bearer {admin_token}".encode()):
            return JSONResponse({"error": "Admin token required"}, status_code=401,
                                headers={"WWW-Authenticate": 'Bearer realm="market-admin"'})
        try:
            data = OpenNegotiation.model_validate(await request.json())
        except (ValidationError, ValueError):
            return JSONResponse({"error": "Expected item and optional first_role"}, status_code=400)
        return JSONResponse(market.create(data.item, data.first_role), status_code=201,
                            headers={"Cache-Control": "no-store"})

    app = server.streamable_http_app(stateless_http=True, json_response=True)
    app.state.market = market
    return app


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("MARKET_PORT", "8001"))
    app = create_app(os.environ.get("MARKET_ADMIN_TOKEN", ""), f"http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port)
