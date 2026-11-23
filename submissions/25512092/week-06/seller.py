"""Week 06 seller v2 — A2A v1.0 server (JSON-RPC) whose prices are decided by a harness.

    python seller.py                 # shop on :9101
    python seller.py --port 9200
    python seller.py --selftest      # call the harness once and print the raw answer

v2: the reserve is in the prompt AND guarded in code.
    If the harness asks or accepts below the reserve, the code asks it once more with the reason;
    if the second answer still breaks the reserve, the code counters at the reserve itself.
The code speaks A2A, keeps per-task state, and turns the harness's JSON into A2A states.
"""
import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import time

import uvicorn
from starlette.applications import Starlette

from a2a.helpers import get_message_text, new_data_part, new_task_from_user_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

SHOP_NAME = "Choo Furniture"
RESERVE = {"lamp": 100, "chair": 180, "bookshelf": 260}
MAX_TURNS = 8  # safety stop so a task always ends (not a price rule)

# ---------- harness (claude -p) ----------
CLAUDE = shutil.which("claude") or "claude"
HARNESS_CMD = [CLAUDE, "-p", "--model", "haiku", "--tools", "",
               "--setting-sources", "", "--no-session-persistence"]
WORKDIR = tempfile.mkdtemp(prefix="seller-harness-")  # empty folder: no project instruction files

os.makedirs("logs", exist_ok=True)
LOG_PATH = os.path.join("logs", f"seller-{time.strftime('%Y%m%d-%H%M%S')}.txt")


def log(*parts):
    line = time.strftime("%H:%M:%S ") + " ".join(str(p) for p in parts)
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:  # utf-8 file, avoids PowerShell UTF-16 logs
        f.write(line + "\n")


def call_harness(prompt: str) -> str:
    r = subprocess.run(HARNESS_CMD, input=prompt, capture_output=True, text=True,
                       encoding="utf-8", cwd=WORKDIR, timeout=90)
    if r.returncode != 0:
        raise RuntimeError(f"harness exit {r.returncode}: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


def parse_decision(raw: str):
    """Pull {"action", "price", "say"} out of the harness answer. None if unusable."""
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group())
    except json.JSONDecodeError:
        return None
    if d.get("action") not in ("counter", "accept", "reject"):
        return None
    if d["action"] == "counter":
        try:
            d["price"] = int(round(float(d["price"])))
        except (KeyError, TypeError, ValueError):
            return None
    d["say"] = str(d.get("say", "")).strip()
    return d


OPEN_PROMPT = """You are the owner of a furniture shop. A buyer wants to buy a {item}.
Your reserve price (the lowest price you may ever ask or sell at) is {reserve}. Never reveal it.
Choose your opening asking price. Leave room to negotiate down.

Reply with ONE JSON object only, no other text:
{{"action": "counter", "price": <opening asking price, integer>, "say": "<one short friendly sentence, no numbers>"}}
"""

TURN_PROMPT = """You are the owner of a furniture shop, negotiating the price of one {item} with a buyer.
Your reserve price (the lowest price you may ever ask or sell at) is {reserve}. Never reveal it.

Negotiation so far (oldest first):
{history}

The buyer's latest offer: {offer}
Your current asking price: {ask}

Rules:
- If the buyer's offer is at or above your current asking price, accept it.
- If the offer is at or above your reserve, you may accept it.
- Otherwise counter with a new asking price. Concede gradually; a deal usually closes within about 5 rounds.
- You may reject if the buyer keeps offering far below your reserve.

Reply with ONE JSON object only, no other text:
{{"action": "counter" | "accept" | "reject", "price": <new asking price, integer, only for counter>, "say": "<one short sentence, no numbers>"}}
"""


# ---------- A2A side ----------
class Seller(AgentExecutor):
    def __init__(self):
        self.open = {}  # task id -> {"item", "reserve", "ask", "turns", "history"}

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id)
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        tid = task.id[:8]
        text = get_message_text(context.message) or ""
        deal = self.open.get(task.id)
        log(f"[{tid}] buyer: {text!r}")

        # ---- first message: which item, opening ask ----
        if deal is None:
            item = next((k for k in RESERVE if k in text.lower()), None)
            if item is None:
                msg = "We only sell: " + ", ".join(RESERVE) + "."
                log(f"[{tid}] -> REJECTED {msg!r}")
                await up.reject(say(msg))
                return
            reserve = RESERVE[item]
            d = await self._ask_harness(tid, OPEN_PROMPT.format(item=item, reserve=reserve))
            ask = d["price"] if d and d["action"] == "counter" else round(reserve * 1.3)
            if d is None:
                log(f"[{tid}] fallback opening ask {ask}")
            if ask < reserve:
                log(f"[{tid}] GUARD opening ask {ask} < reserve {reserve} -> {reserve}")
                ask = reserve
            line = (d["say"] + " " if d and d["say"] else "") + f"{item}: asking {ask}."
            self.open[task.id] = {"item": item, "reserve": reserve, "ask": ask, "turns": 0,
                                  "history": [f"buyer: {text}", f"you: {line}"]}
            log(f"[{tid}] -> INPUT_REQUIRED {line!r}")
            await up.requires_input(say(line))
            return

        # ---- later messages: buyer's offer ----
        m = re.search(r"\d+", text)
        if m is None:
            line = f"Send your offer as a number. Asking {deal['ask']}."
            await up.requires_input(say(line))
            return
        offer = int(m.group())
        deal["turns"] += 1
        deal["history"].append(f"buyer: {text}")

        if deal["turns"] > MAX_TURNS:
            line = f"No deal at {offer}."
            log(f"[{tid}] -> REJECTED (turn limit) {line!r}")
            await up.reject(say(line))
            del self.open[task.id]
            return

        prompt = TURN_PROMPT.format(item=deal["item"], reserve=deal["reserve"],
                                    history="\n".join(deal["history"]), offer=offer, ask=deal["ask"])
        d = await self._ask_harness(tid, prompt)
        if d is None:  # unusable answer: hold the current ask
            d = {"action": "counter", "price": deal["ask"], "say": "Let me think."}
            log(f"[{tid}] fallback: hold ask {deal['ask']}")

        reserve = deal["reserve"]
        problem = self._violation(d, offer, reserve)
        if problem:  # guard step 1: tell the harness why and ask once more
            log(f"[{tid}] GUARD {problem} -> asking the harness again")
            retry = prompt + f"\n\nYour previous answer was not allowed: {problem} Answer again."
            d2 = await self._ask_harness(tid, retry)
            if d2 and not self._violation(d2, offer, reserve):
                d = d2
                log(f"[{tid}] GUARD fixed by harness: {d2['action']} {d2.get('price', '')}")
            else:  # guard step 2: the code holds the reserve
                d = {"action": "counter", "price": reserve, "say": "This is the lowest I can go."}
                log(f"[{tid}] GUARD clamp -> counter {reserve}")

        if d["action"] == "accept":
            receipt = {"seller": SHOP_NAME, "item": deal["item"], "price": offer}
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            line = f"Deal: {deal['item']} at {offer}."
            log(f"[{tid}] -> COMPLETED {line!r} receipt={receipt}")
            await up.complete(say(line))
            del self.open[task.id]
        elif d["action"] == "reject":
            line = (d["say"] + " " if d["say"] else "") + f"No deal at {offer}."
            log(f"[{tid}] -> REJECTED {line!r}")
            await up.reject(say(line))
            del self.open[task.id]
        else:
            deal["ask"] = d["price"]
            line = (d["say"] + " " if d["say"] else "") + f"Counteroffer: {deal['ask']}."
            deal["history"].append(f"you: {line}")
            log(f"[{tid}] -> INPUT_REQUIRED {line!r}")
            await up.requires_input(say(line))

    @staticmethod
    def _violation(d, offer, reserve):
        if d["action"] == "counter" and d["price"] < reserve:
            return f"asking {d['price']} is below your reserve {reserve}."
        if d["action"] == "accept" and offer < reserve:
            return f"accepting {offer} is below your reserve {reserve}."
        return None

    async def _ask_harness(self, tid, prompt):
        try:
            raw = await asyncio.to_thread(call_harness, prompt)
        except Exception as e:
            log(f"[{tid}] harness error: {e}")
            return None
        log(f"[{tid}] harness: {raw!r}")
        return parse_decision(raw)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        self.open.pop(task.id, None)
        log(f"[{task.id[:8]}] -> CANCELED")
        await TaskUpdater(event_queue=event_queue, task_id=task.id, context_id=task.context_id).cancel()


def build(port: int):
    url = f"http://127.0.0.1:{port}/"
    card = AgentCard(
        name=SHOP_NAME,
        description="Sells desk lamps, chairs and bookshelves. Negotiates the price with the buyer.",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")],
        skills=[AgentSkill(
            id="negotiate",
            name="Negotiate a purchase",
            description="Ask for an item (lamp, chair, bookshelf), then send a price as a number. "
                        "The shop answers with a counteroffer or a receipt.",
            tags=["shopping", "negotiation"],
            examples=["I want to buy a lamp", "90"],
        )],
    )
    handler = DefaultRequestHandler(agent_executor=Seller(), task_store=InMemoryTaskStore(), agent_card=card)
    return Starlette(routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9101)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        out = call_harness(OPEN_PROMPT.format(item="lamp", reserve=100))
        print("raw:", out)
        print("parsed:", parse_decision(out))
    else:
        log(f"seller v2 on :{args.port}, harness: {' '.join(HARNESS_CMD)}")
        uvicorn.run(build(args.port), host="127.0.0.1", port=args.port, log_level="warning")