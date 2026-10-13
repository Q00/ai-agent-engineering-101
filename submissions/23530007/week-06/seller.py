"""Week 06 seller: an A2A v1.0 server (JSON-RPC binding) whose decisions come from the harness.

    python seller.py --port 9999

The A2A side follows the lab's seller_agent.py. The rules are gone: on every buyer message the
harness decides the asking price, a counteroffer, or whether to accept or reject.

What the code still enforces (the harness is told the same thing in the prompt):
  - never ask below the reserve: an ask under the reserve is raised to the reserve
  - never sell below the reserve: an "accept" of an offer under the reserve becomes a counter
  - an ask never goes up once made
  - an offer at or above the seller's own current ask is a deal (the buyer took the seller's price)
The message for TASK_STATE_INPUT_REQUIRED always ends with the asking price, so the last number in
it is the ask.
"""
import argparse
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

from harness import HARNESS_CMD, as_int, decide, log

NAME = "Harness Furniture"
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}

ROLE = ("You are the seller in a price negotiation for a {item}. The lowest price you may accept "
        "(your reserve) is {reserve}. Never ask for or accept less than {reserve}. Get as much as you "
        "can, but a sale at or above {reserve} is better than no sale. The buyer can leave at any time.")

OPEN = ROLE + ("\n\nThe buyer just asked to buy the {item}. Choose your opening asking price.\n"
               'Reply with only one JSON object: {{"ask": <whole number>}}')

REPLY = ROLE + ("\n\nNegotiation so far (oldest first):\n{history}\n\n"
                "Your current asking price is {ask}. The buyer now offers {offer}.\n"
                "Choose one action:\n"
                '- {{"action": "accept"}} to sell at {offer}\n'
                '- {{"action": "counter", "price": <whole number>}} to ask a new price, not above {ask}\n'
                '- {{"action": "reject"}} to end the negotiation without a sale\n'
                "Reply with only one JSON object.")


def history_lines(deal: dict) -> str:
    return "\n".join(f"- {who}: {price}" for who, price in deal["history"]) or "- (nothing yet)"


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}  # task id -> {"item", "reserve", "ask", "history"}

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
        log("seller", f"task {tid} <- {text!r}")

        if deal is None:  # first message: which item, and the opening ask
            item = next((k for k in RESERVE if k in text.lower()), None)
            if item is None:
                await up.reject(say("We only sell: " + ", ".join(RESERVE) + "."))
                return
            reserve = RESERVE[item]
            answer = decide("seller", OPEN.format(item=item, reserve=reserve)) or {}
            ask = as_int(answer.get("ask"))
            if ask is None:
                ask = round(reserve * 1.3)
                log("seller", f"task {tid} harness gave no ask; fallback {ask}")
            if ask < reserve:
                log("seller", f"task {tid} code raised ask {ask} to reserve {reserve}")
                ask = reserve
            self.open[task.id] = {"item": item, "reserve": reserve, "ask": ask,
                                  "history": [("seller asked", ask)]}
            log("seller", f"task {tid} -> input-required, ask {ask}")
            await up.requires_input(say(f"{item}: asking {ask}. Send your offer as a number. Asking {ask}"))
            return

        nums = re.findall(r"\d+", text)
        if not nums:
            await up.requires_input(say(f"Send your offer as a number. Asking {deal['ask']}"))
            return
        offer = int(nums[-1])
        deal["history"].append(("buyer offered", offer))

        if offer >= deal["ask"]:   # the buyer took our own price
            await self.close(up, say, task.id, deal, offer, "offer meets the current ask")
            return

        answer = decide("seller", REPLY.format(item=deal["item"], reserve=deal["reserve"], ask=deal["ask"],
                                               offer=offer, history=history_lines(deal))) or {}
        action = answer.get("action")
        price = as_int(answer.get("price"))

        if action == "accept" and offer >= deal["reserve"]:
            await self.close(up, say, task.id, deal, offer, "harness accepted")
            return
        if action == "accept":
            log("seller", f"task {tid} harness accepted {offer} below reserve {deal['reserve']}; "
                          f"code turns it into a counter")
            action, price = "counter", deal["reserve"]
        if action == "reject":
            del self.open[task.id]
            log("seller", f"task {tid} -> rejected")
            await up.reject(say(f"No deal at {offer}."))
            return

        # counter (also the fallback for an unreadable answer: hold the current ask)
        if action != "counter" or price is None:
            log("seller", f"task {tid} unreadable answer {answer!r}; holding ask {deal['ask']}")
            price = deal["ask"]
        if price < deal["reserve"]:
            log("seller", f"task {tid} code raised counter {price} to reserve {deal['reserve']}")
            price = deal["reserve"]
        price = min(price, deal["ask"])
        if price <= offer:                # countering at or below the offer is accepting it
            await self.close(up, say, task.id, deal, offer, f"counter {price} <= offer")
            return
        deal["ask"] = price
        deal["history"].append(("seller asked", price))
        log("seller", f"task {tid} -> input-required, counter {price}")
        await up.requires_input(say(f"{offer} is too low. Counteroffer: {price}"))

    async def close(self, up, say, task_id, deal, offer, why):
        receipt = {"seller": NAME, "item": deal["item"], "price": offer}
        del self.open[task_id]
        log("seller", f"task {task_id[:8]} -> completed at {offer} ({why})")
        await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
        await up.complete(say(f"Deal: {deal['item']} at {offer}."))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        self.open.pop(task.id, None)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port: int):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=NAME,
        description="Sells lamps, chairs and bookshelves. A model decides the prices.",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(
            id="negotiate",
            name="Negotiate a purchase",
            description="Name an item (lamp, chair, bookshelf), then send offers as numbers. The shop answers "
                        "with an asking price (the last number in its message), a receipt, or a rejection.",
            tags=["shopping", "negotiation"],
            examples=["I want to buy a lamp", "90"],
        )],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9999)
    args = ap.parse_args()
    log("seller", f"harness: {HARNESS_CMD}")
    uvicorn.run(build(args.port), host="127.0.0.1", port=args.port, log_level="warning")
