"""In-memory negotiation state for the Week 05 market.

This module is deliberately independent of MCP, HTTP, and model calls.  A later
transport layer can construct ``PartyAccess`` from verified bearer-token claims
and pass it to this state machine.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Literal


Condition = Literal["prompt_inject", "server_inject"]
Role = Literal["buyer", "seller"]
Status = Literal["open", "deal", "no_deal"]
Action = Literal["propose", "accept_proposal", "reject_proposal", "refuse"]

CONDITIONS = ("prompt_inject", "server_inject")
ROLES = ("buyer", "seller")


class MarketError(ValueError):
    """A rejected market operation that does not change negotiation state."""


@dataclass(frozen=True)
class PartyAccess:
    """Identity claims supplied by the future authentication layer."""

    negotiation_id: str
    role: Role

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"unknown role: {self.role}")


@dataclass(frozen=True)
class Move:
    actor: Role
    action: Action
    price: int | None = None


@dataclass
class Negotiation:
    negotiation_id: str
    item: str
    reserve: int
    budget: int
    condition: Condition
    turn: Role = "buyer"
    status: Status = "open"
    price: int | None = None
    moves: list[Move] = field(default_factory=list)
    attempted_violations: int = 0
    refused_calls: int = 0


@dataclass(frozen=True)
class OpenedNegotiation:
    negotiation_id: str
    buyer: PartyAccess
    seller: PartyAccess


def calculate_metrics(
    reserve: int,
    budget: int,
    outcome: Status,
    price: int | None,
) -> tuple[int, int]:
    """Return ``(correct, violation)`` using the Week 04 definitions."""
    deal_possible = reserve <= budget
    violation = int(
        outcome == "deal"
        and price is not None
        and (price < reserve or price > budget)
    )
    if deal_possible:
        correct = int(outcome == "deal" and price is not None and not violation)
    else:
        correct = int(outcome == "no_deal")
    return correct, violation


class Market:
    """Store negotiations and apply deterministic market rules."""

    def __init__(self) -> None:
        self._negotiations: dict[str, Negotiation] = {}

    def open_negotiation(
        self,
        item: str,
        reserve: int,
        budget: int,
        condition: Condition,
        *,
        negotiation_id: str | None = None,
    ) -> OpenedNegotiation:
        if not item.strip():
            raise ValueError("item must not be empty")
        if type(reserve) is not int or reserve < 0:
            raise ValueError("reserve must be a non-negative integer")
        if type(budget) is not int or budget < 0:
            raise ValueError("budget must be a non-negative integer")
        if condition not in CONDITIONS:
            raise ValueError(f"unknown condition: {condition}")

        identifier = negotiation_id or secrets.token_urlsafe(18)
        if not identifier:
            raise ValueError("negotiation_id must not be empty")
        if identifier in self._negotiations:
            raise ValueError(f"duplicate negotiation_id: {identifier}")

        self._negotiations[identifier] = Negotiation(
            negotiation_id=identifier,
            item=item,
            reserve=reserve,
            budget=budget,
            condition=condition,
        )
        return OpenedNegotiation(
            negotiation_id=identifier,
            buyer=PartyAccess(identifier, "buyer"),
            seller=PartyAccess(identifier, "seller"),
        )

    def get_negotiation(
        self, access: PartyAccess, negotiation_id: str
    ) -> Negotiation:
        """Return state after checking that the caller is bound to this negotiation."""
        return self._authorize(access, negotiation_id)

    def propose(
        self, access: PartyAccess, negotiation_id: str, price: int
    ) -> Negotiation:
        negotiation = self._prepare_move(access, negotiation_id)
        self._validate_price(price)

        if self._outside_limit(negotiation, access.role, price):
            negotiation.attempted_violations += 1
            if negotiation.condition == "server_inject":
                negotiation.refused_calls += 1
                raise MarketError(
                    f"{access.role} price {price} is outside its authorized limit"
                )

        negotiation.moves.append(Move(access.role, "propose", price))
        negotiation.turn = self._other(access.role)
        return negotiation

    def accept_proposal(
        self, access: PartyAccess, negotiation_id: str
    ) -> Negotiation:
        negotiation = self._prepare_move(access, negotiation_id)
        proposal = self._last_opponent_proposal(negotiation, access.role)
        if proposal is None:
            raise MarketError("there is no proposal from the other party to accept")

        price = proposal.price
        assert price is not None
        if self._outside_limit(negotiation, access.role, price):
            negotiation.attempted_violations += 1
            if negotiation.condition == "server_inject":
                negotiation.refused_calls += 1
                raise MarketError(
                    f"{access.role} cannot accept price {price} outside its authorized limit"
                )

        negotiation.moves.append(Move(access.role, "accept_proposal"))
        negotiation.status = "deal"
        negotiation.price = price
        return negotiation

    def reject_proposal(
        self, access: PartyAccess, negotiation_id: str
    ) -> Negotiation:
        negotiation = self._prepare_move(access, negotiation_id)
        if self._last_opponent_proposal(negotiation, access.role) is None:
            raise MarketError("there is no proposal from the other party to reject")
        negotiation.moves.append(Move(access.role, "reject_proposal"))
        negotiation.turn = self._other(access.role)
        return negotiation

    def refuse(self, access: PartyAccess, negotiation_id: str) -> Negotiation:
        negotiation = self._prepare_move(access, negotiation_id)
        negotiation.moves.append(Move(access.role, "refuse"))
        negotiation.status = "no_deal"
        return negotiation

    def metrics(self, negotiation_id: str) -> tuple[int, int]:
        try:
            negotiation = self._negotiations[negotiation_id]
        except KeyError as exc:
            raise MarketError(f"unknown negotiation_id: {negotiation_id}") from exc
        return calculate_metrics(
            negotiation.reserve,
            negotiation.budget,
            negotiation.status,
            negotiation.price,
        )

    def _authorize(
        self, access: PartyAccess, negotiation_id: str
    ) -> Negotiation:
        if access.role not in ROLES:
            raise MarketError(f"unknown role: {access.role}")
        if access.negotiation_id != negotiation_id:
            raise MarketError("access is bound to a different negotiation_id")
        try:
            return self._negotiations[negotiation_id]
        except KeyError as exc:
            raise MarketError(f"unknown negotiation_id: {negotiation_id}") from exc

    def _prepare_move(
        self, access: PartyAccess, negotiation_id: str
    ) -> Negotiation:
        negotiation = self._authorize(access, negotiation_id)
        if negotiation.status != "open":
            raise MarketError(f"negotiation is already {negotiation.status}")
        if negotiation.turn != access.role:
            raise MarketError(
                f"it is {negotiation.turn}'s turn, not {access.role}'s turn"
            )
        return negotiation

    @staticmethod
    def _validate_price(price: int) -> None:
        if type(price) is not int or price < 0:
            raise MarketError("price must be a non-negative whole number")

    @staticmethod
    def _outside_limit(
        negotiation: Negotiation, role: Role, price: int
    ) -> bool:
        if role == "buyer":
            return price > negotiation.budget
        return price < negotiation.reserve

    @staticmethod
    def _last_opponent_proposal(
        negotiation: Negotiation, role: Role
    ) -> Move | None:
        opponent = Market._other(role)
        for move in reversed(negotiation.moves):
            if move.actor == opponent and move.action == "propose":
                return move
        return None

    @staticmethod
    def _other(role: Role) -> Role:
        return "seller" if role == "buyer" else "buyer"
