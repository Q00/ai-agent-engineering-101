"""Week 05 — negotiation state machine (no MCP, no auth). Plain Python, easy to test.

One Negotiation is one buyer + one seller, buyer speaks first. The four acts mean
what they meant in week 04:
  propose  offer a price, the turn passes to the other side
  accept   agree to the other side's last price -> deal, ends the negotiation
  reject   decline the last price and keep going, the turn passes
  refuse   leave for good -> no_deal, ends the negotiation

This file only moves the state. It does NOT decide who is allowed to act or whether a
price is allowed. Those checks are the assignment; they belong in the server layer
(market_server.py), before a method here is called.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class MarketError(ValueError):
    """The move makes no sense in the current state (e.g. accept with nothing on the table)."""


@dataclass
class Negotiation:
    id: str
    item: str
    reserve: int                  # seller's private limit. Never put it in a view().
    budget: int                   # buyer's private limit. Never put it in a view().
    turn: str = "buyer"
    status: str = "open"          # open | deal | no_deal
    price: int | None = None      # agreed price once status == "deal"
    last_price: dict = field(default_factory=lambda: {"buyer": None, "seller": None})
    history: list = field(default_factory=list)

    def _log(self, role: str, act: str, price: int | None, note: str) -> None:
        self.history.append({"n": len(self.history) + 1, "role": role, "act": act,
                             "price": price, "note": note})

    def _require_open(self) -> None:
        if self.status != "open":
            raise MarketError(f"negotiation is already {self.status}")

    def _other(self, role: str) -> str:
        return "seller" if role == "buyer" else "buyer"

    def propose(self, role: str, price: int, note: str = "") -> None:
        self._require_open()
        self.last_price[role] = price
        self._log(role, "propose", price, note)
        self.turn = self._other(role)

    def accept(self, role: str, note: str = "") -> None:
        self._require_open()
        offered = self.last_price[self._other(role)]
        if offered is None:
            raise MarketError("nothing to accept: the other side has not proposed a price")
        self.status, self.price = "deal", offered
        self._log(role, "accept", offered, note)

    def reject(self, role: str, note: str = "") -> None:
        self._require_open()
        self._log(role, "reject", None, note)
        self.turn = self._other(role)

    def refuse(self, role: str, note: str = "") -> None:
        self._require_open()
        self.status = "no_deal"
        self._log(role, "refuse", None, note)

    def view(self) -> dict:
        """What a caller may see. Leaves out reserve and budget on purpose."""
        return {"negotiation_id": self.id, "item": self.item, "turn": self.turn,
                "status": self.status, "price": self.price,
                "last_price": dict(self.last_price), "history": list(self.history)}


NEGOTIATIONS: dict[str, Negotiation] = {}


def open_negotiation(sc: dict, negotiation_id: str) -> Negotiation:
    n = Negotiation(id=negotiation_id, item=sc["item"],
                    reserve=sc["reserve"], budget=sc["budget"])
    NEGOTIATIONS[negotiation_id] = n
    return n
