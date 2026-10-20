"""Week 07 seller: an A2A v1.0 server (JSON-RPC binding) that sells at the floor price, fast.

    python seller.py --port 9999

Round 2 ranks shops by counted deals: signed receipts behind a market order. Round 1 showed
that share follows visits, and visits follow memory: buyers go back to the shop where they
paid least (corner-store 466 visits, outseop 237, both near the reserve). Nobody may sell
below the reserve, so a shop that always sells at the reserve is a place no memory-driven
buyer has a reason to leave. So the rules are fixed, no harness (week 06 used claude -p,
about 3 s per turn, and lost a few deals to harness errors):
  - open at the reserve (the asking price is always the last number in the message),
  - accept any offer at or above the reserve, at the offered price,
  - counter anything below with the reserve, and never end the negotiation ourselves,
  - accept words without a number ("accept", "deal", "ok") close at the current ask.
Week 06 history: the first trade test ran with the reserve in the prompt only and failed
(haiku countered 250 and sold at 230 on a bookshelf with reserve 260); the code guard fixed it.

LAB 2: the receipt is {shop, item, price, task} with price a whole number and task the A2A
task id, signed with shop_key.pem over canon(...) as sig_seller (sign_receipt from the notes).
"""
import argparse
import base64
import json
import re

import uvicorn
from cryptography.hazmat.primitives import serialization
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

SHOP = "Inextro Furniture"
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}
ACCEPT = re.compile(r"\b(accept|accepted|agree|agreed|deal|ok|okay|yes)\b")
REFUSE = re.compile(r"\b(no|not|don't|dont|won't|cannot|can't|never|reject|walk)\b")


def accepts(text):
    """A plain yes with no number: 'I accept', 'deal', 'ok'. Never 'no deal' or 'not ok'."""
    t = text.lower()
    return bool(ACCEPT.search(t)) and not REFUSE.search(t)


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign_receipt(receipt, key_path="shop_key.pem"):
    key = serialization.load_pem_private_key(open(key_path, "rb").read(), password=None)
    body = {k: receipt[k] for k in ("shop", "item", "price", "task")}
    receipt["sig_seller"] = base64.b64encode(key.sign(canon(body))).decode()
    return receipt


class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}  # task id -> {"item", "ask"}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)
        print(f"[task {task.id[:8]}] buyer: {text!r}", flush=True)
        if deal is None:
            await self.opening(task.id, text, up, say)
        else:
            await self.respond(task.id, deal, text, up, say)

    async def opening(self, task_id, text, up, say):
        item = next((k for k in RESERVE if k in text.lower()), None)
        if item is None:
            await up.reject(say("We only sell: " + ", ".join(RESERVE) + "."))
            return
        ask = RESERVE[item]
        self.open[task_id] = {"item": item, "ask": ask}
        print(f"[task {task_id[:8]}] -> INPUT_REQUIRED ask {ask}", flush=True)
        await up.requires_input(say(f"{item}: our lowest price, no haggling needed. Asking {ask}. "
                                    f"Send your offer as a number."))

    async def respond(self, task_id, deal, text, up, say):
        m = re.search(r"\d+(?:\.\d+)?", text)
        if m is None:
            if accepts(text):
                await self.close(task_id, deal, deal["ask"], up, say)
            else:
                await up.requires_input(say(f"Send your offer as a number. Current price: {deal['ask']}."))
            return
        offer = round(float(m.group()))
        if offer >= RESERVE[deal["item"]]:
            await self.close(task_id, deal, offer, up, say)
        else:
            print(f"[task {task_id[:8]}] -> INPUT_REQUIRED ask {deal['ask']} (offer {offer})", flush=True)
            await up.requires_input(say(f"{offer} is below our floor price. Counteroffer: {deal['ask']}."))

    async def close(self, task_id, deal, price, up, say):
        receipt = sign_receipt({"shop": SHOP, "item": deal["item"], "price": int(price), "task": task_id})
        await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
        print(f"[task {task_id[:8]}] -> COMPLETED at {price}", flush=True)
        await up.complete(say(f"Deal: {deal['item']} at {price}."))
        del self.open[task_id]

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        self.open.pop(task.id, None)
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build_app(port):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=SHOP,
        description="Sells lamps, chairs and bookshelves at the lowest price in the market "
                    "(lamp 100, chair 180, bookshelf 260). Instant replies, signed receipts.",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(
            id="negotiate", name="Negotiate a purchase",
            description="Name an item (lamp, chair, bookshelf) to get an asking price, then reply with your "
                        "offer as a number on the same task. Any offer at the asking price or above closes "
                        "at once with a signed receipt artifact {shop, item, price, task, sig_seller}.",
            tags=["shopping", "negotiation"], examples=["I want to buy a lamp", "100"])],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9999)
    args = ap.parse_args()
    print(f"{SHOP} on http://127.0.0.1:{args.port}/ (rule-based: floor price, no harness)", flush=True)
    uvicorn.run(build_app(args.port), host="127.0.0.1", port=args.port, log_level="warning")
