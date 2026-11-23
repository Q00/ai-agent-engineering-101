"""Directory search and bounded state for the many-shops buyer challenge."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


@dataclass(frozen=True)
class AgentSummary:
    name: str
    url: str
    description: str
    skills: tuple[str, ...]


class DirectoryIndex:
    """Filter Agent Cards before a small candidate set reaches the harness."""

    def __init__(self, cards: list[AgentSummary]) -> None:
        self.cards = cards

    def search(self, need: str, limit: int = 5) -> list[AgentSummary]:
        wanted = _words(need)
        scored = []
        for card in self.cards:
            skill_words = _words(" ".join(card.skills))
            description_words = _words(card.description)
            score = 3 * len(wanted & skill_words) + len(wanted & description_words)
            if score:
                scored.append((score, card.name, card))
        scored.sort(key=lambda value: (-value[0], value[1]))
        return [value[2] for value in scored[: max(1, limit)]]


@dataclass
class BudgetLedger:
    total_budget: int
    purchases: dict[str, tuple[str, int]] = field(default_factory=dict)

    @property
    def spent(self) -> int:
        return sum(price for _, price in self.purchases.values())

    @property
    def remaining(self) -> int:
        return self.total_budget - self.spent

    def record(self, item: str, shop: str, price: int) -> None:
        if item in self.purchases:
            raise ValueError(f"item already bought: {item}")
        if price < 0 or price > self.remaining:
            raise ValueError(f"purchase exceeds remaining budget: {price} > {self.remaining}")
        self.purchases[item] = (shop, price)


@dataclass
class NegotiationSummary:
    item: str
    shop: str
    events: list[tuple[str, str]] = field(default_factory=list)

    def add(self, role: str, text: str) -> None:
        self.events.append((role, text))

    def render(self, max_events: int = 6) -> str:
        tail = self.events[-max_events:]
        lines = [f"item={self.item}; shop={self.shop}; events={len(self.events)}"]
        for role, text in tail:
            clipped = text if len(text) <= 120 else text[:117] + "..."
            lines.append(f"{role}: {clipped}")
        return "\n".join(lines)
