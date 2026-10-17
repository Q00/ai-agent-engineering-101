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
