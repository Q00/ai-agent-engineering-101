"""Server-owned negotiation state and opaque token grants.

The handle selects a record; the token authorizes access to that record.
No agent-supplied role is accepted. Action transitions are added in stage 3.
"""

from copy import deepcopy
from dataclasses import dataclass, field
from secrets import token_urlsafe
from threading import RLock
from typing import Literal
from uuid import uuid4


Role = Literal["buyer", "seller"]
Condition = Literal["prompt", "server", "prompt_inject", "server_inject"]
CONDITIONS = ("prompt", "server", "prompt_inject", "server_inject")


class MarketError(ValueError):
    """Expected refusal to be returned to an agent as a tool error."""


@dataclass(frozen=True, slots=True)
class PartyGrant:
    negotiation_id: str
    role: Role
    limit: int | None


@dataclass(slots=True)
class Negotiation:
    negotiation_id: str
    scenario_id: str
    item: str
    reserve: int
    budget: int
    condition: Condition
    turn: Role = "buyer"
    status: Literal["open", "deal", "no_deal"] = "open"
    moves: list[dict] = field(default_factory=list)


class Market:
    """In-memory market. A restart invalidates all negotiations and tokens."""

    def __init__(self):
        self.lock = RLock()
        self._negotiations: dict[str, Negotiation] = {}
        self._grants: dict[str, PartyGrant] = {}

    def create(self, *, scenario_id: str, item: str, reserve: int,
               budget: int, condition: Condition) -> dict:
        if condition not in CONDITIONS:
            raise ValueError("Unknown condition")
        if not isinstance(scenario_id, str) or not scenario_id.strip():
            raise ValueError("Scenario id must be a nonempty string")
        if not isinstance(item, str) or not item.strip():
            raise ValueError("Item must be a nonempty string")
        if any(type(price) is not int or price < 0 for price in (reserve, budget)):
            raise ValueError("Reserve and budget must be nonnegative integers")
        with self.lock:
            negotiation_id = uuid4().hex
            negotiation = Negotiation(negotiation_id, scenario_id, item, reserve, budget, condition)
            self._negotiations[negotiation_id] = negotiation
            tokens = {}
            for role, own_limit in (("buyer", budget), ("seller", reserve)):
                token = token_urlsafe(32)
                grant = PartyGrant(negotiation_id, role, own_limit if condition.startswith("server") else None)
                self._grants[token] = grant
                tokens[role] = token
            # Only the administrator receives tokens. Never log this response.
            return {"negotiation_id": negotiation_id, "tokens": tokens}

    def grant_for(self, token: str) -> PartyGrant | None:
        with self.lock:
            return self._grants.get(token)

    def _party(self, token: str, negotiation_id: str) -> tuple[Negotiation, PartyGrant]:
        grant = self._grants.get(token)
        if grant is None:
            raise MarketError("Invalid party token")
        if grant.negotiation_id != negotiation_id:
            raise MarketError("Token is not authorized for this negotiation")
        negotiation = self._negotiations.get(negotiation_id)
        if negotiation is None:
            raise MarketError("Negotiation unavailable")
        return negotiation, grant

    def view(self, token: str, negotiation_id: str) -> dict:
        with self.lock:
            negotiation, grant = self._party(token, negotiation_id)
            return {
                "negotiation_id": negotiation.negotiation_id,
                "item": negotiation.item,
                "role": grant.role,
                "turn": negotiation.turn,
                "status": negotiation.status,
                "moves": deepcopy(negotiation.moves),
            }

    def require_turn(self, token: str, negotiation_id: str) -> PartyGrant:
        """Stage-3 actions must call this inside the same lock as their mutation."""
        with self.lock:
            negotiation, grant = self._party(token, negotiation_id)
            if negotiation.status != "open":
                raise MarketError("Negotiation is already closed")
            if negotiation.turn != grant.role:
                raise MarketError("It is not your turn")
            return grant
