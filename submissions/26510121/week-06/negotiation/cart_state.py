"""Authoritative cart ledger; model summaries never authorize spending.

Reserve before concurrent work. Release only after a verified terminal outcome.
Unknown outcomes keep their full reservation and are never automatically retried.
"""
import asyncio
from dataclasses import asdict, dataclass, field

from negotiation.a2a_client import ProtocolError, integer_money


@dataclass
class CartLine:
    line_id: str
    item_id: str
    category: str
    description: str
    state: str = "pending"
    cap: int = 0
    shop_id: str | None = None
    task_id: str | None = None
    attempted_shops: list = field(default_factory=list)
    summary: str = ""
    recent_turns: list = field(default_factory=list)
    receipt: dict | None = None


class CartLedger:
    def __init__(self, lines, total_budget, log):
        if type(total_budget) is not int or total_budget <= 0:
            raise ValueError("Positive total budget required")
        self.lines = {}
        items = set()
        for source in lines:
            if set(source) != {"line_id", "item_id", "category", "description"}:
                raise ValueError("Shopping line fields must be explicit")
            if any(not isinstance(value, str) or not value.strip() for value in source.values()):
                raise ValueError("Nonempty shopping line fields required")
            line = CartLine(**source)
            if line.line_id in self.lines or line.item_id in items:
                raise ValueError("Duplicate shopping line or item")
            self.lines[line.line_id] = line
            items.add(line.item_id)
        if not self.lines:
            raise ValueError("Nonempty shopping list required")
        self.total_budget = total_budget
        self.spent = 0
        self.log = log
        self.lock = asyncio.Lock()

    @property
    def held(self):
        return sum(line.cap for line in self.lines.values() if line.state in {"reserved", "active", "uncertain"})

    @property
    def available(self):
        return self.total_budget - self.spent - self.held

    def snapshot(self):
        assert self.available >= 0
        return {"total_budget": self.total_budget, "spent": self.spent, "held": self.held,
                "available": self.available, "lines": [asdict(line) for line in self.lines.values()]}

    def record(self, event, **fields):
        self.log.record(event, **fields, ledger=self.snapshot())

    async def reserve(self, decision):
        async with self.lock:
            rows = decision.assignments
            ids = [row.line_id for row in rows]
            if decision.action != "plan" or not rows or len(ids) != len(set(ids)):
                raise ProtocolError("invalid_reservation_plan")
            if any(row.line_id not in self.lines or self.lines[row.line_id].state not in {"pending", "stopped"}
                   or row.shop_id in self.lines[row.line_id].attempted_shops
                   or type(row.budget) is not int or row.budget <= 0 for row in rows):
                raise ProtocolError("line_already_reserved_or_attempted")
            if sum(row.budget for row in rows) > self.available:
                raise ProtocolError("cart_budget_overreserved")
            for row in rows:
                line = self.lines[row.line_id]
                line.cap, line.shop_id, line.state = row.budget, row.shop_id, "reserved"
                line.task_id = None
                line.summary, line.recent_turns = "", []
            self.record("cart_reserved")

    async def begin(self, line_id):
        async with self.lock:
            line = self.lines[line_id]
            if line.state != "reserved":
                raise ProtocolError("line_already_started")
            line.state = "active"
            line.attempted_shops.append(line.shop_id)
            self.record("cart_negotiation_started", line_id=line_id, shop_id=line.shop_id)

    async def remember(self, line_id, memory):
        async with self.lock:
            line = self.lines[line_id]
            if line.state != "active" or (line.task_id is not None and line.task_id != memory.task_id):
                raise ProtocolError("cart_task_changed")
            line.task_id = memory.task_id
            line.summary = memory.summary
            line.recent_turns = list(memory.recent)
            self.record("cart_memory", line_id=line_id, task_id=line.task_id,
                        summary_chars=len(line.summary), recent_count=len(line.recent_turns))

    async def finish(self, line_id, result):
        async with self.lock:
            line = self.lines[line_id]
            if line.state != "active" or line.task_id != result.get("task_id"):
                raise ProtocolError("cart_result_task_mismatch")
            if result["outcome"] == "purchased":
                data = result["receipt"]
                price = integer_money(data["price"])
                if data.get("item") != line.item_id or price > line.cap:
                    raise ProtocolError("cart_receipt_item_or_budget_mismatch")
                if any(other.receipt and other.receipt["item"] == line.item_id for other in self.lines.values()):
                    raise ProtocolError("duplicate_cart_purchase")
                self.spent += price
                line.receipt = {**data, "price": price}
                line.state = "purchased"
            elif result["outcome"] == "stopped" and result.get("state") in {"TASK_STATE_REJECTED", "TASK_STATE_CANCELED"}:
                line.state = "stopped"
            else:
                raise ProtocolError("unverified_cart_outcome")
            self.record("cart_settled", line_id=line_id, outcome=result["outcome"])

    async def uncertain(self, line_id, code):
        async with self.lock:
            line = self.lines[line_id]
            if line.state == "active":
                line.state = "uncertain"
                self.record("cart_uncertain", line_id=line_id, code=code)

    def result(self):
        return {"outcome": "purchased" if all(line.state == "purchased" for line in self.lines.values()) else "incomplete",
                **self.snapshot()}
