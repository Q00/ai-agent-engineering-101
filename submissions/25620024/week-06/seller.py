"""Week 06 — seller.py: the shop as an A2A v1.0 server (JSON-RPC), prices decided by the harness.

    python seller.py --port 9999

Built on the lab's seller_agent.py. The A2A side (task per negotiation, INPUT_REQUIRED,
receipt artifact, COMPLETED / REJECTED) is the same; the rules are replaced by harness
calls (harness.ask). The code around the harness does three things the model may not:

  guard   an ask below the reserve is raised to the reserve; a sale below the reserve
          is turned into a counteroffer at the reserve; an ask never goes up again
  accept  a buyer offer at or above the current ask is a deal (the buyer met our price)
  format  the reply text is written by code, so the ask is always the LAST number in
          an INPUT_REQUIRED message (that is how the trade test reads it)
"""
import argparse
import asyncio
import json
import re

import uvicorn
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from harness import ask

NAME = "Harness Shop (25620024)"
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}   # lowest prices the shop may accept
MAX_ROUNDS = 6                                             # buyer offers before the shop walks away

OPEN_PROMPT = """You are the seller in a furniture shop, negotiating with a buyer agent.
Item: {item}. Your lowest acceptable price (reserve) is {reserve}. Keep the reserve private.
The buyer just asked to buy the {item}. Choose your opening asking price: high enough to leave
room to negotiate, but realistic.
Reply with one JSON object only: {{"price": <whole number>}}"""

TURN_PROMPT = """You are the seller in a furniture shop, negotiating with a buyer agent.
Item: {item}. Your lowest acceptable price (reserve) is {reserve}. Keep the reserve private.
Never ask for or accept less than the reserve.
Conversation so far (oldest first):
{history}
The buyer's latest offer is {offer}. Your current asking price is {current_ask}.
This is buyer offer {round} of at most {max_rounds}.
Decide your move:
- "accept": sell at the buyer's offer (only if the offer is at or above your reserve)
- "counter": name a new asking price, lower than your current ask but not below the reserve
- "reject": end the negotiation with no deal
Concede step by step instead of jumping to the reserve. If the offer is already at or above
the reserve and the negotiation has gone a few rounds, accepting is usually better than losing the sale.
Reply with one JSON object only: {{"action": "accept" or "counter" or "reject", "price": <whole number>}}"""


def log(msg):
    print(msg, flush=True)   # the trade test saves the seller's output under logs/


class HarnessSeller(AgentExecutor):
    def __init__(self):
        self.open = {}   # task id -> {"item", "reserve", "ask", "rounds", "history"}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)
        tid = task.id[:8]

        if deal is None:   # first message: which item, and the opening ask
            item = next((k for k in RESERVE if k in text.lower()), None)
            if item is None:
                await up.reject(say("We only sell: lamp, chair, bookshelf."))
                return
            reserve = RESERVE[item]
            ask_price = await self.decide_open(item, reserve, tid)
            reply = f"{item}: asking {ask_price}. Send your offer as a number."
            self.open[task.id] = {"item": item, "reserve": reserve, "ask": ask_price, "rounds": 0,
                                  "history": [f"buyer: {text}", f"seller: {reply}"]}
            await up.requires_input(say(reply))
            return

        nums = re.findall(r"\d+", text)
        if not nums:
            await up.requires_input(say(f"Send your offer as a number. Current ask: {deal['ask']}."))
            return
        offer = int(nums[0])
        deal["rounds"] += 1
        deal["history"].append(f"buyer: {text}")
        item, reserve = deal["item"], deal["reserve"]

        if offer >= deal["ask"]:   # the buyer met our current asking price
            action, price = "accept", offer
            log(f"[{tid}] offer {offer} >= current ask {deal['ask']}: accept without asking the harness")
        else:
            action, price = await self.decide_turn(deal, offer, tid)

        if action == "accept":
            receipt = {"seller": NAME, "item": item, "price": offer}
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(say(f"Deal: {item} at {offer}."))
            log(f"[{tid}] DEAL {item} at {offer} (reserve {reserve})")
            del self.open[task.id]
            return
        if action == "reject" or deal["rounds"] >= MAX_ROUNDS:
            await up.reject(say(f"No deal for the {item} at that price."))
            log(f"[{tid}] REJECT after {deal['rounds']} offers (last offer {offer})")
            del self.open[task.id]
            return
        deal["ask"] = price
        reply = f"{offer} is too low. Counteroffer: {price}."
        deal["history"].append(f"seller: {reply}")
        await up.requires_input(say(reply))

    async def decide_open(self, item, reserve, tid):
        try:
            answer = await asyncio.to_thread(ask, OPEN_PROMPT.format(item=item, reserve=reserve))
            price = int(answer["price"])
        except Exception as e:   # harness failed or answered nonsense: open at 1.3 x reserve
            price = round(reserve * 1.3)
            log(f"[{tid}] harness error on opening ask ({e}); fallback {price}")
        log(f"[{tid}] harness opening ask {price} for {item} (reserve {reserve})")
        if price < reserve:
            log(f"[{tid}] GUARD opening ask {price} < reserve {reserve}: raised to {reserve}")
            price = reserve
        return price

    async def decide_turn(self, deal, offer, tid):
        prompt = TURN_PROMPT.format(item=deal["item"], reserve=deal["reserve"],
                                    history="\n".join(deal["history"]), offer=offer,
                                    current_ask=deal["ask"], round=deal["rounds"], max_rounds=MAX_ROUNDS)
        try:
            answer = await asyncio.to_thread(ask, prompt)
            action, price = answer.get("action"), int(answer.get("price", deal["ask"]))
        except Exception as e:   # harness failed: hold the current ask
            action, price = "counter", deal["ask"]
            log(f"[{tid}] harness error ({e}); holding ask {price}")
        log(f"[{tid}] offer {offer} -> harness {json.dumps({'action': action, 'price': price})}")
        reserve = deal["reserve"]
        if action == "accept" and offer < reserve:
            log(f"[{tid}] GUARD harness accepted {offer} < reserve {reserve}: countering at {reserve}")
            action, price = "counter", reserve
        if action not in ("accept", "counter", "reject"):
            log(f"[{tid}] GUARD unknown action {action!r}: countering at current ask")
            action, price = "counter", deal["ask"]
        if action == "counter":
            if price < reserve:
                log(f"[{tid}] GUARD counter {price} < reserve {reserve}: raised to {reserve}")
                price = reserve
            if price > deal["ask"]:
                log(f"[{tid}] GUARD counter {price} above current ask {deal['ask']}: kept at {deal['ask']}")
                price = deal["ask"]
        return action, price

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        self.open.pop(task.id, None)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port):
    card = AgentCard(
        name=NAME,
        description="Sells desk lamps, chairs and bookshelves. Prices are negotiated by a Claude (haiku) harness.",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=f"http://127.0.0.1:{port}/",
                                             protocol_version="1.0")],
        skills=[AgentSkill(
            id="negotiate",
            name="Negotiate a purchase",
            description="Name an item (lamp, chair, bookshelf), then send offers as numbers. "
                        "The shop answers with a counteroffer, a receipt, or no deal.",
            tags=["shopping", "negotiation"],
            examples=["I want to buy a lamp", "90"],
        )],
    )
    handler = DefaultRequestHandler(agent_executor=HarnessSeller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9999)
    port = ap.parse_args().port
    log(f"{NAME} on http://127.0.0.1:{port}  (harness: claude -p --model haiku)")
    uvicorn.run(build(port), host="127.0.0.1", port=port, log_level="warning")
