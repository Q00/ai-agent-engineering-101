"""Round 3 basic seller: sells units from the kit's inventory over A2A.

    python seller.py --port 9301

Rules only; the enhanced version replaces them:
  - the buyer names a SKU ("I want to buy <name> (<sku>)"); the shop takes the
    unit of that SKU that expires first and is not promised to another open
    negotiation
  - the first ask is 90% of the list price, and each counter moves halfway to
    the buyer's offer
  - never below the unit's cost: the code holds it, whatever the buyer says
  - the receipt names the SKU and the unit, so the kit ships exactly that unit;
    no grade claim and no warranty, and the kit attaches the certificate
"""
import argparse
import math
import re
import time

import uvicorn
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

import common

NAME = "Kiro729 Shop"
FIRST_ASK = 0.9          # of the list price
MAX_ROUNDS = 4           # offers before the shop closes: take one at or above cost, else no deal
HOLD = 300               # seconds a silent negotiation keeps its unit (the kit's buyer timeout)
SKU = re.compile(r"\bG[0-9a-f]{8}\b")
NUMBER = re.compile(r"(?<!\w)\d[\d,]*(?!\d)")         # 16000, 16,000, 16000원; not the digits inside a SKU


def offer_in(text: str):
    """The last number in the message, or None."""
    nums = [n.replace(",", "") for n in NUMBER.findall(text)]
    nums = [int(n) for n in nums if n.isdigit()]
    return nums[-1] if nums else None


def floor(unit: dict) -> int:
    return math.ceil(unit["cost"])


def find_unit(text: str, held: set):
    """The in-stock unit the buyer asked for (by SKU, else by exact name) that expires first."""
    inv = common.market_file("inventory.json", {}) or {}
    if inv.get("round", 0) < 3:
        return None
    now = time.time()
    units = [u for u in inv.get("units", []) if u.get("grade") != "expired" and u["unit"] not in held
             and (u.get("expires_at") or float("inf")) > now]
    skus = SKU.findall(text)
    if skus:
        fit = [u for u in units if u["sku"] in skus]
    else:
        low = text.lower()
        fit = [u for u in units if u.get("name") and u["name"].lower() in low]
    return min(fit, key=lambda u: (u.get("expires_at") or float("inf"), u.get("supplied_at", 0)), default=None)


class Seller(AgentExecutor):
    def __init__(self, key):
        self.key = key
        self.open = {}   # task id -> {"unit", "asks", "offers", "t"}

    def held(self) -> set:
        """Units promised to open negotiations; a buyer silent for HOLD seconds has left."""
        now = time.time()
        for task_id in [t for t, d in self.open.items() if now - d["t"] > HOLD]:
            self.close(task_id, "walked")
        return {d["unit"]["unit"] for d in self.open.values()}

    def close(self, task_id: str, outcome: str, price=None) -> None:
        d = self.open.pop(task_id)
        u = d["unit"]
        common.record("negotiations", task=task_id, sku=u["sku"], unit=u["unit"], grade=u.get("grade"),
                      cost=u["cost"], list_price=u["list_price"], asks=d["asks"], offers=d["offers"],
                      outcome=outcome, price=price)
        print(f"[seller] {u['sku']} {outcome}" + (f" at {price}" if price else ""), flush=True)

    async def execute(self, context, event_queue):
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text, **kw: up.new_agent_message(parts=[new_text_part(text)], **kw)
        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)

        if deal is None:                                    # first message: what do they want?
            unit = find_unit(text, self.held())
            if unit is None:
                await up.reject(say("Sorry, we have none of that in stock. Ask for a SKU we carry."))
                return
            ask = max(floor(unit), round(unit["list_price"] * FIRST_ASK))
            self.open[task.id] = {"unit": unit, "asks": [ask], "offers": [], "t": time.time()}
            await up.requires_input(say(f"{unit['name']} ({unit['sku']}): asking {ask}. Send your offer as a number.",
                                        metadata={"ask": ask}))
            return

        deal["t"] = time.time()
        offer = offer_in(text)
        ask, low = deal["asks"][-1], floor(deal["unit"])
        if offer is None:
            await up.requires_input(say(f"Send your offer as a number. Asking {ask}.", metadata={"ask": ask}))
            return
        deal["offers"].append(offer)
        last = len(deal["offers"]) >= MAX_ROUNDS
        if offer >= ask or (last and offer >= low):
            await self.sell(up, say, task.id, min(offer, ask))
        elif last:
            self.close(task.id, "rejected")
            await up.reject(say(f"No deal at {offer}."))
        else:
            new = max(low, ask - (ask - max(offer, low)) // 2)
            deal["asks"].append(new)
            await up.requires_input(say(f"Counteroffer: {new}.", metadata={"ask": new}))

    async def sell(self, up, say, task_id: str, price: int) -> None:
        u = self.open[task_id]["unit"]
        receipt = common.sign_receipt(self.key, {"shop": NAME, "item": u["sku"], "price": int(price),
                                                 "task": task_id, "unit": u["unit"]})
        self.close(task_id, "deal", int(price))
        await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
        await up.complete(say(f"Deal: {u['name']} ({u['sku']}) at {price}."))

    async def cancel(self, context, event_queue):
        task = context.current_task
        if task.id in self.open:
            self.close(task.id, "walked")
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port: int) -> Starlette:
    card = AgentCard(
        name=NAME,
        description="A small shop selling food and household goods from its own stock. "
                    "Name a SKU, then send a price; every reply is a counteroffer or a signed receipt.",
        version="3.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=f"http://127.0.0.1:{port}/",
                                             protocol_version="1.0")],
        skills=[AgentSkill(id="negotiate", name="Negotiate a purchase",
                           description="Ask for an item by SKU, then send a price. The ask is the last number "
                                       "in the reply and metadata.ask.",
                           tags=["shopping", "negotiation"],
                           examples=["I want to buy 비비고 포기배추김치 5kg x1개 (G29fc9227)", "42000"])],
    )
    key = common.load_key("SHOP_KEY_FILE", "shop_key.pem")
    handler = DefaultRequestHandler(agent_executor=Seller(key), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9301)
    port = ap.parse_args().port
    print(f"[seller] {NAME} on port {port}, stock from {common.MARKET_DIR}", flush=True)
    uvicorn.run(build(port), host="127.0.0.1", port=port, log_level="warning")
