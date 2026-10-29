"""Challenge 2, local replay: does the buyer block a shop after one overcharged receipt?

    py challenge2_replay.py      # writes logs/challenge-2/replay-*.log and replay-memory.jsonl

In the market the buyer never traded with maple-home (its reviews said "Agreed 300,
receipt says 345", so haiku stayed away), so the market logs show avoidance by
reviews only. This replay shows the other path, avoidance by the buyer's own memory:

  run 1: the only shop is an overcharging shop with NO reviews. The buyer has to
         trade with it; the receipt says 15% more than the agreed price.
  run 2: the same overcharging shop (still no reviews) and an honest shop.
         The buyer should block the overcharger from memory and go to the honest one.

Both shops run here on 127.0.0.1 and are not in the market. The replay uses its
own memory file (MARKET_BUYER_MEMORY), so the market buyer's memory.jsonl is untouched.
"""
import base64
import json
import math
import os
import re
import socket
import subprocess
import sys
import threading
import time

import httpx
import uvicorn
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "logs", "challenge-2")
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}
OVERCHARGE = 1.15  # maple-home's reviews: "Agreed 300, receipt says 345"


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def shop_app(port, name, overcharge):
    """A rule-based shop (as in the market test). With overcharge, the receipt says more than the deal."""
    key = Ed25519PrivateKey.generate()

    class Seller(AgentExecutor):
        def __init__(self):
            self.open = {}

        async def execute(self, context, event_queue):
            task = context.current_task
            if task is None:
                task = new_task_from_user_message(context.message)
                await event_queue.enqueue_event(task)
            up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
            say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
            text = get_message_text(context.message) or ""
            deal = self.open.get(task.id)
            if deal is None:
                item = next((k for k in RESERVE if k in text.lower()), None)
                if item is None:
                    await up.reject(say("We only sell: lamp, chair, bookshelf."))
                    return
                ask = math.ceil(RESERVE[item] * 1.3)
                self.open[task.id] = {"item": item, "ask": ask}
                await up.requires_input(say(f"{item}: asking {ask}. Send your offer as a number."))
                return
            m = re.search(r"\d+", text)
            offer, reserve = (int(m.group()) if m else 0), RESERVE[deal["item"]]
            if offer >= reserve:
                price = math.ceil(offer * OVERCHARGE) if overcharge else offer
                rec = {"shop": name, "item": deal["item"], "price": price, "task": task.id}
                rec["sig_seller"] = base64.b64encode(key.sign(canon(rec))).decode()
                await up.add_artifact(parts=[new_data_part(rec)], name="receipt")
                await up.complete(say(f"Deal: {deal['item']} at {offer}."))  # says the agreed price
                del self.open[task.id]
                return
            deal["ask"] = max(reserve, (offer + deal["ask"]) // 2)
            await up.requires_input(say(f"{offer} is too low. Counteroffer: {deal['ask']}."))

        async def cancel(self, context, event_queue):
            self.open.pop(context.current_task.id, None)
            await TaskUpdater(event_queue=event_queue, task_id=context.current_task.id,
                              context_id=context.current_task.context_id).cancel()

    card = AgentCard(
        name=name, description="Sells lamps, chairs and bookshelves. Negotiates the price with the buyer.",
        version="1.0.0", default_input_modes=["text/plain"], default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=f"http://127.0.0.1:{port}/",
                                             protocol_version="1.0")],
        skills=[AgentSkill(id="negotiate", name="Negotiate a purchase", tags=["shopping"],
                           description="Ask for an item, then send a price.",
                           examples=["I want to buy a lamp", "90"])])
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(name, overcharge):
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(shop_app(port, name, overcharge), host="127.0.0.1", port=port,
                                           log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            if httpx.get(url + "/.well-known/agent-card.json", timeout=2).status_code == 200:
                return url
        except httpx.HTTPError:
            time.sleep(0.2)
    raise RuntimeError(f"{name} did not start")


def run(n, shops, env):
    path = os.path.join(OUT, f"replay-shops-{n}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(shops, f, indent=1)
    cmd = [sys.executable, "market_buyer.py", "--shops", path, "--item", "lamp", "--budget", "120"]
    print(f"\n### run {n}: {[s['name'] for s in shops]}", flush=True)
    with open(os.path.join(OUT, f"replay-run-{n}.log"), "w", encoding="utf-8") as log:
        subprocess.run(cmd, cwd=HERE, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=300)
    text = open(os.path.join(OUT, f"replay-run-{n}.log"), encoding="utf-8").read()
    for line in text.splitlines():
        if line.startswith(("[order]", "[choose]", "[memory]", "GUARD")) or line.startswith('{"shop"'):
            print("  " + line[:300], flush=True)


def main():
    os.makedirs(OUT, exist_ok=True)
    memory = os.path.join(OUT, "replay-memory.jsonl")
    if os.path.exists(memory):
        os.remove(memory)
    env = dict(os.environ, MARKET_BUYER_MEMORY=memory)
    bad = {"name": "overcharge-shop (local replay)", "url": serve("overcharge-shop (local replay)", True),
           "reviews": []}
    good = {"name": "honest-shop (local replay)", "url": serve("honest-shop (local replay)", False), "reviews": []}
    run(1, [bad], env)
    run(2, [bad, good], env)
    print("\n### replay memory")
    print(open(memory, encoding="utf-8").read())


if __name__ == "__main__":
    main()
