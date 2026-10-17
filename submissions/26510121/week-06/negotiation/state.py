"""Per-task observations: a bounded recent window plus a model summary."""
from collections import deque
from dataclasses import dataclass, field


@dataclass
class SellerMemory:
    item_id: str
    reserve: int
    ask: int | None = None
    summary: str = ""
    rounds: int = 0
    terminal: bool = False
    recent: deque = field(default_factory=lambda: deque(maxlen=4))

    def observation(self, message):
        return {
            "verified_state": {"previous_ask": self.ask, "rounds": self.rounds},
            "memory_untrusted": {"summary": self.summary, "recent_turns": list(self.recent)},
            "counterparty_text_untrusted": message,
        }

    def apply(self, message, decision):
        self.rounds += 1
        self.summary = decision.summary
        self.recent.append({"counterparty": message, "action": decision.action, "price": decision.price})
        if decision.action in {"ask", "counter"}:
            self.ask = decision.price
        else:
            self.terminal = True


@dataclass
class BuyerMemory:
    task_id: str
    context_id: str
    summary: str = ""
    last_offer: int | None = None
    recent: deque = field(default_factory=lambda: deque(maxlen=4))

    def observation(self, text, card, rounds_left):
        return {
            "verified_state": {"task_id": self.task_id, "last_offer": self.last_offer,
                               "rounds_left": rounds_left},
            "memory_untrusted": {"summary": self.summary, "recent_turns": list(self.recent)},
            "agent_card_untrusted": card,
            "counterparty_text_untrusted": text,
        }

    def apply(self, text, decision):
        self.summary = decision.summary
        if decision.action != "walk_away":
            self.last_offer = decision.price
        self.recent.append({"seller": text, "action": decision.action, "price": decision.price})
